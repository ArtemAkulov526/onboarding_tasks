import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

from playwright.async_api import async_playwright, BrowserContext
from playwright_stealth import Stealth

from utils import (
    get_logger, parse_proxy_url, human_delay, random_mouse_move,
    handle_captcha_if_present, extract_hotels_from_page,
    scroll_until_more_cards, add_hotels,
)

load_dotenv()

TWOCAPTCHA_API_KEY = os.getenv("TWOCAPTCHA_API_KEY")

PROXIES = {
    "london": "ip:port:username:password",
    "paris":  "ip:port:username:password",
    "berlin": "ip:port:username:password",
}

TARGET_HOTELS_PER_CITY = 30
OUTPUT_FILE = "hotels.json"

CITIES = {
    "london": {
        "label": "London",
        "url": (
            "https://www.booking.com/searchresults.uk.html"
            "?ss=%D0%9B%D0%BE%D0%BD%D0%B4%D0%BE%D0%BD"
            "&dest_id=-2601889&dest_type=city"
            "&checkin=2026-03-20&checkout=2026-04-22"
            "&group_adults=1&no_rooms=1&group_children=0"
            "&lang=uk&sb=1&src=searchresults&offset=0"
        ),
    },
    "paris": {
        "label": "Paris",
        "url": (
            "https://www.booking.com/searchresults.uk.html"
            "?ss=Paris&dest_id=-1456928&dest_type=city"
            "&checkin=2026-03-20&checkout=2026-04-22"
            "&group_adults=1&no_rooms=1&group_children=0"
            "&lang=uk&sb=1&src=searchresults&offset=0"
        ),
    },
    "berlin": {
        "label": "Berlin",
        "url": (
            "https://www.booking.com/searchresults.uk.html"
            "?ss=%D0%91%D0%B5%D1%80%D0%BB%D1%96%D0%BD"
            "&dest_id=-1746443&dest_type=city"
            "&checkin=2026-03-20&checkout=2026-04-22"
            "&group_adults=1&no_rooms=1&group_children=0"
            "&lang=uk&sb=1&src=searchresults&offset=0"
        ),
    },
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

async def scrape_city(city_key: str, city_info: dict, pw, semaphore: asyncio.Semaphore, proxy: str | None = None) -> list[dict]:
    label = city_info["label"]
    logger = get_logger(f"scraper.{city_key}")
    hotels = []

    async with semaphore:
        browser = await pw.chromium.launch(
            headless=False,
            proxy=parse_proxy_url(proxy),
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        context: BrowserContext = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="Europe/London",
            geolocation={"latitude": 51.5074, "longitude": -0.1278},
            permissions=["geolocation"],
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9", "DNT": "1"},
        )
        page = await context.new_page()

        try:
            await page.goto(city_info["url"], wait_until="domcontentloaded", timeout=45000)
            await human_delay(2000, 4000)
            await handle_captcha_if_present(page, TWOCAPTCHA_API_KEY, logger)
            await random_mouse_move(page, steps=4)
            await human_delay(500, 1200)

            for scroll_num in range(1, 50):
                visible = await extract_hotels_from_page(page)
                add_hotels(visible, hotels, label, TARGET_HOTELS_PER_CITY, logger)

                if len(hotels) >= TARGET_HOTELS_PER_CITY:
                    break

                more = await scroll_until_more_cards(page, logger, label)
                if not more:
                    add_hotels(await extract_hotels_from_page(page), hotels, label, TARGET_HOTELS_PER_CITY, logger)
                    break

                await human_delay(800, 1500)

        except Exception as e:
            logger.error(f"[{label}] Error: {e}", exc_info=True)
        finally:
            await browser.close()

        return hotels[:TARGET_HOTELS_PER_CITY]


async def main() -> None:
    logger = get_logger("scraper.main")

    async with Stealth().use_async(async_playwright()) as pw:
        semaphore = asyncio.Semaphore(3)
        results = await asyncio.gather(*[
            scrape_city(key, info, pw, semaphore, proxy=PROXIES.get(key))
            for key, info in CITIES.items()
        ], return_exceptions=True)

    all_hotels, summary = [], {}
    for city_key, result in zip(CITIES.keys(), results):
        label = CITIES[city_key]["label"]
        if isinstance(result, Exception):
            logger.error(f"[{label}] Error: {result}")
            summary[label] = {"count": 0, "error": str(result)}
        else:
            all_hotels.extend(result)
            summary[label] = {"count": len(result)}

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "total_hotels": len(all_hotels),
            "summary": summary,
            "hotels": all_hotels,
        }, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    asyncio.run(main())