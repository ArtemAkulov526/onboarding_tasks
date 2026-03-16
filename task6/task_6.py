from curl_cffi import requests, AsyncSession
from parsel import Selector
import logging
import time
import random
import asyncio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("ebay_scraper.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

BLOCK_MARKERS = [
    "are you a robot",
    "captcha",
    "access denied",
    "unusual traffic",
    "security check",
    "verify you are human",
    "pardon our interruption"
]

def is_blocked(html: str) -> list[str]:
    html_lower = html.lower()
    triggered = [marker for marker in BLOCK_MARKERS if marker in html_lower]
    if triggered:
        logger.debug(f"triggered_markers: {triggered}")
    return triggered

def count_items(html: str) -> int:
    sel = Selector(text=html)
    items = sel.css("ul.srp-results li.s-card").getall()
    return len(items)

class RPS:
    proxies = ["ip:port:username:password",
               "ip:port:username:password",
               "ip:port:username:password",
               "ip:port:username:password",
            ]

    def get_random_proxy(self) -> str:
        proxy = random.choice(self.proxies)
        ip, port, user, password = proxy.split(":")
        proxy_url = f"http://{user}:{password}@{ip}:{port}"
        return proxy_url

    def sync_requests(self):
        ses = requests.Session()
        for page in range(1,25):
            try:
                start_time = time.time()
                resp = ses.get(
                    f"https://www.ebay.com/sch/i.html?_nkw=laptop&_pgn={page}",
                    impersonate="chrome120",
                )
                elapsed = round(time.time() - start_time, 3)
                if resp.status_code != 200:
                    logger.warning(f"Page {page}: non-200 status {resp.status_code}")
                    break

                status_code = resp.status_code
                size_resp = len(resp.content)
                triggered_markers = [m for m in BLOCK_MARKERS if m in resp.text.lower()]

                logger.info(
                    f"Page: {page} "
                    f" Status code: {status_code} | "
                    f"Response size: {size_resp:,} KB | "
                    f"Blocked: {'Yes' + str(triggered_markers) if triggered_markers else 'No'} | "
                    f"Time of request: {elapsed}s"
                )
                if triggered_markers:
                    logger.warning(f"Blocked on page {page}: {triggered_markers}")
                    break

            except Exception as e:
                elapsed = round(time.time() - start_time, 3)
                logger.error(
                    f" Error on {page} (time: {elapsed}s): {e}"
                )

    async def single_request(self, session: AsyncSession, page: int, rps: float) -> dict:
        url = f"https://www.ebay.com/sch/i.html?_nkw=laptop&_pgn={page}"
        start_time = time.time()
        result = {
            "page": page, "rps": rps, "status": None,
            "size_bytes": 0, "blocked": [], "elapsed": 0, "error": None,
        }
        try:
            resp = await session.get(
                url,
                impersonate="chrome120",
                proxy=self.get_random_proxy(),
                timeout=15,
            )
            html = resp.text
            if page == 1:
                with open("debug_page.html", "w", encoding="utf-8") as f:
                    f.write(resp.text)

            result["elapsed"] = round(time.time() - start_time, 3)
            result["status"] = resp.status_code
            result["size_bytes"] = len(resp.content)
            result["blocked"] = is_blocked(html)
            result["items_count"] = count_items(html)

            blocker = None
            if resp.status_code == 503:
                blocker = "503 Service Unavailable"
            elif resp.status_code in (301, 302, 303, 307, 308):
                blocker = f"Redirect → {resp.headers.get('location', '?')}"
            elif result["size_bytes"] < 50_000:
                blocker = f"Small response ({result['size_bytes']} bytes)"
            elif result["blocked"]:
                blocker = f"Blocked by markers: {result['blocked']}"
            elif result["items_count"] == 0:
                blocker = f"Soft ban: 200 OK but 0 items on page"

            log_fn = logger.warning if blocker else logger.info
            log_fn(
                f"[{rps} req/s | page{page:>3}] "
                f"Status_code: {result['status']} | "
                f"Response_size: {result['size_bytes']} | "
                f"Items: {result['items_count']} | "
                f"Time_of_request: {result['elapsed']}s"
                + (f" |  {blocker}" if blocker else "")
            )

        except Exception as e:
            result["elapsed"] = round(time.time() - start_time, 3)
            result["error"] = str(e)
            logger.error(f"[{rps} req/s | page{page:>3}]  {e}")

        return result

    async def run_phase(self, rps: float, duration_seconds: int = 120):
        interval = 1.0 / rps
        logger.info(f"RPS: {rps} req/s | Duration: {duration_seconds}s")

        results = []
        results_lock = asyncio.Lock()
        page_counter = 0
        page_lock = asyncio.Lock()

        async def worker(session: AsyncSession, request_time: float):
            nonlocal page_counter
            delay = request_time - time.time()
            if delay > 0:
                await asyncio.sleep(delay)

            async with page_lock:
                page_counter = (page_counter % 50) + 1
                page = page_counter

            result = await self.single_request(session, page, rps)

            async with results_lock:
                results.append(result)

        async with AsyncSession() as session:
            start_phase = time.time()
            tasks = []

            t = start_phase
            while t < start_phase + duration_seconds:
                fire_at = t
                task = asyncio.create_task(worker(session, fire_at))
                tasks.append(task)
                t += interval

            await asyncio.gather(*tasks, return_exceptions=True)

        # Агрегация результатов
        total = len(results)
        errors = sum(1 for r in results if r["error"] is not None)
        ok_with_items = sum(
            1 for r in results
            if r["error"] is None and r["status"] == 200 and r["items_count"] > 0
        )
        soft_ban = sum(
            1 for r in results
            if r["error"] is None and r["status"] == 200
            and (r["items_count"] == 0 or r["blocked"])
        )
        other = total - errors - ok_with_items - soft_ban

        logger.info(
            f"\n--- Phase summary [{rps} req/s] ---\n"
            f"  Total requests : {total}\n"
            f"  200 + items    : {ok_with_items}  (clean)\n"
            f"  200 + soft ban : {soft_ban}  (0 items or blocked markers)\n"
            f"  Errors         : {errors}\n"
            f"  Other          : {other}\n"
            f"----------------------------------"
        )

        return results

if __name__ == "__main__":
    # options for rps = [1.0, 2.0, 5.0]
    rps = 2.0
    session = RPS()
    session.sync_requests()
    asyncio.run(session.run_phase(rps, duration_seconds=120))
