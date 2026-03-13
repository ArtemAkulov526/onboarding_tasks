from playwright.sync_api import sync_playwright
from curl_cffi import requests as requests
from curl_cffi.requests import AsyncSession
import psutil
import json
import re
import time
import random
import logging
import asyncio

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MAX_RETRIES       = 3
BASE_BACKOFF      = 2.0
MAX_JITTER        = 1.5
LISTING_CARDS_URL = "https://www.etsy.com/api/v3/ajax/bespoke/member/neu/specs/listingCards"


def get_nst_debug_port():
    for proc in psutil.process_iter(['name', 'cmdline']):
        try:
            if proc.info['name'] and 'nstchrome.exe' in proc.info['name'].lower():
                cmdline = " ".join(proc.info['cmdline'])
                match = re.search(r'--remote-debugging-port=(\d+)', cmdline)
                if match:
                    return int(match.group(1))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def get_browser_data() -> tuple[str, str]:
    port = get_nst_debug_port()
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0]
        page = context.new_page()
        page.goto("https://www.etsy.com/search?q=wooden+toys", wait_until="load")
        cookies = page.context.cookies()
        user_agent = page.evaluate("() => navigator.userAgent")
        page.close()

    cookie_str = "; ".join(
        f"{c['name']}={c['value']}" for c in cookies
        if "etsy.com" in c.get("domain", "")
    )
    return cookie_str, user_agent


def extract_page_data(html: str) -> dict:
    def find_list(pattern):
        m = re.search(pattern, html)
        return json.loads(m.group(1)) if m else []

    def find_str(pattern):
        m = re.search(pattern, html)
        return m.group(1) if m else ""

    def find_int(pattern):
        m = re.search(pattern, html)
        return int(m.group(1)) if m else 0

    return {
        "listing_ids":            find_list(r'"lazy_loaded_listing_ids"\s*:\s*(\[\d+(?:,\d+)*\])'),
        "ad_ids":                 find_list(r'"ad_ids"\s*:\s*(\[\d+(?:,\d+)*\])'),
        "organic_logging_keys":   find_list(r'"organic_logging_keys"\s*:\s*(\["[^\]]+"\])'),
        "ads_logging_keys":       find_list(r'"ads_logging_keys"\s*:\s*(\["[^\]]+"\])'),
        "req_id":                 find_str(r'"req_id"\s*:\s*"([^"]+)"'),
        "organic_listings_count": find_int(r'"organic_listings_count"\s*:\s*(\d+)'),
        "csrf_nonce":             find_str(r'"csrf_nonce"\s*:\s*"([^"]+)"'),
        "page_guid":              find_str(r'"page_guid"\s*:\s*"([^"]+)"'),
        "bucket_id":              find_str(r'"bucket_id"\s*:\s*"([^"]+)"'),
    }


def build_payload(d: dict) -> dict:
    return {
        "log_performance_metrics": True,
        "runtime_analysis": False,
        "specs": {
            "listingCards": ["Search2_ApiSpecs_LazyListingCards", {
                "ad_ids": d["ad_ids"],
                "ads_logging_keys": d["ads_logging_keys"],
                "ads_organic_positions": [],
                "customizable_ads_listing_ids": [],
                "customizable_ads_logging_keys": [],
                "customizable_organic_listing_ids": [],
                "is_mobile": False,
                "lazy_loaded_dynamic_aspect_ratio": None,
                "listing_ids": d["listing_ids"],
                "organic_listings_count": d["organic_listings_count"],
                "organic_logging_keys": d["organic_logging_keys"],
                "req_id": d["req_id"],
                "search_request_params": {
                    "detected_locale": {"language": "en", "currency_code": "USD", "region": "US"},
                    "locale":          {"language": "en", "currency_code": "USD", "region": "US"},
                    "name_map": {"query": "q", "query_type": "qt", "results_per_page": "result_count",
                                 "min_price": "min", "max_price": "max"},
                    "parameters": {
                        "q": "wooden toys", "instant_download": "0", "utm_medium": None,
                        "placement": "wsg", "page_type": "search", "referrer": None,
                        "request_type": "initial", "result_count": 48,
                        "should_pass_user_location_to_thrift": True,
                        "filter_distracting_content": True,
                        "bucket_id": d["bucket_id"], "user_id": None,
                    },
                    "user_id": None,
                },
                "user_favorite_shop_ids": [],
            }]
        },
        "view_data_event_name": "search_lazy_loaded_cards_specview_rendered",
    }


def build_get_headers(user_agent: str, cookie_str: str, page_num: int) -> dict:
    return {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "accept-encoding": "gzip, deflate, br, zstd",
        "user-agent": user_agent,
        "upgrade-insecure-requests": "1",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "referer": f"https://www.etsy.com/search?q=wooden+toys&page={page_num - 1}" if page_num > 1 else "https://www.etsy.com/",
        "cookie": cookie_str,
    }

def build_post_headers(user_agent: str, cookie_str: str, d: dict, url: str) -> dict:
    return {
        "accept": "*/*",
        "content-type": "application/json",
        "user-agent": user_agent,
        "referer": "https://www.etsy.com/search?q=wooden+toys&instant_download=0",
        "origin": "https://www.etsy.com",
        "x-csrf-token": d["csrf_nonce"],
        "x-detected-locale": "USD|en-US|US",
        "x-page-guid": d["page_guid"],
        "x-recs-primary-location": url,
        "x-recs-primary-referrer": "",
        "x-recs-should-show-more": "false",
        "x-requested-with": "XMLHttpRequest",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "cookie": cookie_str,
    }

def parse_ids(raw: str) -> set:
    marker = '"atc_buttons_shown":'
    start = raw.find(marker)
    if start == -1:
        return set()

    bracket = raw.find("[", start)
    if bracket == -1:
        return set()

    try:
        entries, _ = json.JSONDecoder().raw_decode(raw, bracket)
        return {e[0] for e in entries if isinstance(e, list) and len(e) >= 2 and e[1] == "add_to_cart"}
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(f"Could not parse atc_buttons_shown: {exc}")
        return set()

def sync_run(cookie_str: str, user_agent: str) -> set:
    session = requests.Session()
    all_ids = set()

    for page_num in range(1, 21):
        url = f"https://www.etsy.com/search?q=wooden+toys&page={page_num}"

        t0 = time.perf_counter()
        try:
            r = session.get(url, headers=build_get_headers(user_agent, cookie_str, page_num),
                            impersonate="chrome116")
            if r.status_code in (403, 429, 503):
                raise ValueError(f"GET {r.status_code}")

            d = extract_page_data(r.text)
            if not d["listing_ids"]:
                logger.warning(f"[Sync] Page {page_num:>2} | no listing_ids in HTML")
                break

            api_r = session.post(LISTING_CARDS_URL, json=build_payload(d),
                                 headers=build_post_headers(user_agent, cookie_str, d, url),
                                 impersonate="chrome116")

            if api_r.status_code in (403, 429, 503):
                raise ValueError(f"POST {api_r.status_code}")
            if api_r.status_code != 200:
                logger.warning(f"[Sync] Page {page_num:>2} | POST {api_r.status_code}: {api_r.text[:150]}")
                break

            page_ids = parse_ids(api_r.text)
            all_ids.update(page_ids)
            elapsed = time.perf_counter() - t0
            logger.info(f"[Sync] Page {page_num:>2} | items {len(page_ids):>2} | {elapsed:.2f}s")
            break

        except ValueError as e:
            logger.warning(f"[Sync] Page {page_num:>2}")

    logger.info(f"[Sync] Done | total unique IDs: {len(all_ids)}")
    return all_ids

async def _fetch_page(session, semaphore: asyncio.Semaphore,
                      page_num: int, cookie_str: str, user_agent: str) -> set:
    url = f"https://www.etsy.com/search?q=wooden+toys&page={page_num}"

    async with semaphore:
        await asyncio.sleep(random.uniform(0.2, MAX_JITTER))

        for attempt in range(1, MAX_RETRIES + 1):
            t0 = asyncio.get_event_loop().time()
            try:
                r = await session.get(url, headers=build_get_headers(user_agent, cookie_str, page_num),
                                      impersonate="chrome116")
                if r.status_code in (403, 429, 503):
                    raise ValueError(f"GET {r.status_code}")

                d = extract_page_data(r.text)
                if not d["listing_ids"]:
                    logger.warning(f"[Async] Page {page_num:>2} | no listing_ids in HTML")
                    return set()

                api_r = await session.post(LISTING_CARDS_URL, json=build_payload(d),
                                           headers=build_post_headers(user_agent, cookie_str, d, url),
                                           impersonate="chrome116")

                if api_r.status_code in (403, 429, 503):
                    raise ValueError(f"POST {api_r.status_code}")
                if api_r.status_code != 200:
                    logger.warning(f"[Async] Page {page_num:>2} | POST {api_r.status_code}: {api_r.text[:150]}")
                    return set()

                page_ids = parse_ids(api_r.text)
                elapsed = asyncio.get_event_loop().time() - t0
                logger.info(f"[Async] Page {page_num:>2} | attempt {attempt}/{MAX_RETRIES} "
                            f"| items {len(page_ids):>2} | {elapsed:.2f}s")
                return page_ids

            except ValueError as e:
                backoff = BASE_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0, MAX_JITTER)
                logger.warning(f"[Async] Page {page_num:>2} | attempt {attempt}/{MAX_RETRIES} "
                               f"| {e} | retry in {backoff:.1f}s")
                await asyncio.sleep(backoff)

        logger.error(f"[Async] Page {page_num:>2} | all {MAX_RETRIES} retries failed")
        return set()


async def async_run(cookie_str: str, user_agent: str) -> set:
    semaphore = asyncio.Semaphore(5)
    all_ids = set()

    async with AsyncSession() as session:
        tasks = [_fetch_page(session, semaphore, pn, cookie_str, user_agent)
                 for pn in range(1, 21)]
        t0 = time.perf_counter()
        results = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - t0

    for page_ids in results:
        all_ids.update(page_ids)

    logger.info(f"[Async] Done | total unique IDs: {len(all_ids)} | total time: {elapsed:.2f}s")
    return all_ids


if __name__ == "__main__":
    cookie_str, user_agent = get_browser_data()

    ids = asyncio.run(async_run(cookie_str, user_agent))
    #ids = sync_run(cookie_str, user_agent)
