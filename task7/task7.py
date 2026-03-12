from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright.async_api import async_playwright, TimeoutError as AsyncPWTimeout
import psutil
import json
import time
import random
import logging
import asyncio
import re

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MAX_RETRIES  = 3
BASE_BACKOFF = 2.0
MAX_JITTER   = 1.5
PAGE_TIMEOUT = 30_000
XHR_TIMEOUT  = 15_000

def get_nst_debug_port() -> int| None:
    for proc in psutil.process_iter(['name', 'cmdline']):
        try:
            if proc.info['name'] and 'nstchrome.exe' in proc.info['name'].lower():
                cmdline = " ".join(proc.info['cmdline'])
                match = re.search(r'--remote-debugging-port=(\d+)', cmdline)
                if match:
                    return int(match.group(1))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def parse_listing_cards(raw: str) -> set:
    ids = set()

    marker = '"atc_buttons_shown":'
    idx = raw.find(marker)
    if idx == -1:
        logger.warning("atc_buttons_shown not found in response")
        return ids

    array_start = raw.find('[', idx + len(marker))
    if array_start == -1:
        return ids

    try:
        buttons, _ = json.JSONDecoder().raw_decode(raw, array_start)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"atc_buttons_shown parse error: {e}")
        return ids

    for entry in buttons:
        if isinstance(entry, list) and len(entry) >= 2 and entry[1] == "add_to_cart":
            ids.add(entry[0])

    return ids

def sync_requests() -> set:
    start = time.perf_counter()
    all_ids = set()
    port = get_nst_debug_port()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0]
        page = context.new_page()

        for page_num in range(1, 21):
            url = f"https://www.etsy.com/search?q=wooden+toys&instant_download=0&page={page_num}"

            t0 = time.perf_counter()
            try:
                with page.expect_response(
                    lambda r: "specs/listingCards" in r.url,
                    timeout=XHR_TIMEOUT
                ) as resp_info:
                    page.goto(url, wait_until="load", timeout=PAGE_TIMEOUT)

                raw = resp_info.value.text()
                elapsed = time.perf_counter() - t0

                page_ids = parse_listing_cards(raw)
                all_ids.update(page_ids)

                logger.info(
                    f"[Sync] Page {page_num:>2} | items {len(page_ids):>2} | {elapsed:.2f}s")
            except PWTimeout:
                logger.error(PWTimeout)
        page.close()
    end = time.perf_counter() - start
    logger.info(f"[Sync] Done | total unique IDs: {len(all_ids)}| total time: {end:.2f}s")
    return all_ids

async def _fetch_page_async(context, semaphore: asyncio.Semaphore, page_num: int) -> set:
    url = f"https://www.etsy.com/search?q=wooden+toys&page={page_num}"

    async with semaphore:
        await asyncio.sleep(random.uniform(0.2, MAX_JITTER))
        page = await context.new_page()

        for attempt in range(1, MAX_RETRIES + 1):
            t0 = asyncio.get_event_loop().time()
            try:
                async with page.expect_response(
                    lambda r: "specs/listingCards" in r.url,
                    timeout=XHR_TIMEOUT
                ) as resp_info:
                    await page.goto(url, wait_until="load", timeout=PAGE_TIMEOUT)

                resp = await resp_info.value
                raw  = await resp.text()
                elapsed = asyncio.get_event_loop().time() - t0

                page_ids = parse_listing_cards(raw)

                logger.info(
                    f"[Async] Page {page_num:>2} | items {len(page_ids):>2} | {elapsed:.2f}s"
                )
                await page.close()
                return page_ids

            except AsyncPWTimeout:
                backoff = BASE_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0, MAX_JITTER)
                logger.warning(
                    f"[Async] Page {page_num:>2} | attempt {attempt}/{MAX_RETRIES} "
                    f"| timeout | retry in {backoff:.1f}s"
                )
                await asyncio.sleep(backoff)

        logger.error(f"[Async] Page {page_num:>2} | all {MAX_RETRIES} retries failed, skipping")
        await page.close()
        return set()

async def async_requests():
    port = get_nst_debug_port()
    semaphore = asyncio.Semaphore(5)
    all_ids = set()

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0]

        tasks = [
            _fetch_page_async(context, semaphore, page_num)
            for page_num in range(1, 21)
        ]

        t0 = time.perf_counter()
        results = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - t0

        for page_ids in results:
            all_ids.update(page_ids)

    logger.info(f"[Async] Done | total unique IDs: {len(all_ids)} | total time: {elapsed:.2f}s")
    return all_ids

if __name__ == "__main__":

    asyncio.run(async_requests())
    #sync_requests()