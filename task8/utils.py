import asyncio
import logging
import random
import re
from datetime import datetime, timezone
from typing import Optional

from playwright.async_api import Page

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

def parse_proxy_url(proxy: str | None) -> dict | None:
    if not proxy:
        return None
    if proxy.startswith(("http://", "https://", "socks5://")):
        from urllib.parse import urlparse
        p = urlparse(proxy)
        r: dict = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
        if p.username: r["username"] = p.username
        if p.password: r["password"] = p.password
        return r
    parts = proxy.split(":")
    if len(parts) == 4:
        host, port, user, pwd = parts
        return {"server": f"http://{host}:{port}", "username": user, "password": pwd}
    if len(parts) == 2:
        return {"server": f"http://{proxy}"}
    if "@" in proxy:
        from urllib.parse import urlparse
        p = urlparse("http://" + proxy)
        r = {"server": f"http://{p.hostname}:{p.port}"}
        if p.username: r["username"] = p.username
        if p.password: r["password"] = p.password
        return r
    raise ValueError(f"Unsupported proxy format: '{proxy}'")


async def human_delay(min_ms: int = 800, max_ms: int = 2500) -> None:
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def random_mouse_move(page: Page, steps: int = 5) -> None:
    vp = page.viewport_size or {"width": 1280, "height": 900}
    for _ in range(steps):
        await page.mouse.move(random.randint(100, vp["width"] - 100), random.randint(100, vp["height"] - 100))
        await asyncio.sleep(random.uniform(0.05, 0.20))


async def solve_recaptcha_v2(page: Page, api_key: str, logger: logging.Logger) -> bool:
    try:
        from twocaptcha import TwoCaptcha
    except ImportError:
        logger.error("pip install twocaptcha-solver")
        return False
    sitekey = await page.evaluate("() => document.querySelector('[data-sitekey]')?.getAttribute('data-sitekey')")
    if not sitekey:
        return False
    try:
        token = TwoCaptcha(api_key).recaptcha(sitekey=sitekey, url=page.url)["code"]
        await page.evaluate("""(token) => {
            const ta = document.getElementById('g-recaptcha-response');
            if (ta) { ta.innerHTML = token; ta.style.display = 'block'; }
            const keys = Object.keys(___grecaptcha_cfg?.clients || {});
            if (keys.length) {
                const cb = ___grecaptcha_cfg.clients[keys[0]]?.O?.b?.callback
                        || ___grecaptcha_cfg.clients[keys[0]]?.l?.b?.callback;
                if (typeof cb === 'function') cb(token);
            }
        }""", token)
        await human_delay(1500, 3000)
        return True
    except Exception as e:
        logger.error(f"2captcha error: {e}")
        return False


async def handle_captcha_if_present(page: Page, api_key: str, logger: logging.Logger) -> None:
    for sel in ["iframe[src*='recaptcha']", ".g-recaptcha", "[data-sitekey]"]:
        if await page.query_selector(sel):
            logger.warning(f"Captcha: {sel}")
            if await solve_recaptcha_v2(page, api_key, logger):
                await page.wait_for_load_state("networkidle", timeout=15000)
            break


def parse_hotel_card(card_data: dict) -> Optional[dict]:
    name = card_data.get("name", "").strip()
    if not name:
        return None
    rating = card_data.get("rating")
    reviews = card_data.get("reviews")
    price = card_data.get("price")
    if rating:
        try: rating = float(str(rating).replace(",", "."))
        except ValueError: rating = None
    if reviews:
        d = re.sub(r"[^\d]", "", str(reviews))
        reviews = int(d) if d else None
    price_per_night = None
    if price:
        d = re.sub(r"[^\d]", "", str(price))
        if d: price_per_night = round(int(d) / 33)
    return {"name": name, "rating": rating, "price_per_night": price_per_night, "reviews": reviews}


EXTRACT_HOTELS_JS = """
() => {
    const results = [];
    document.querySelectorAll('[data-testid="property-card"]').forEach(card => {
        const name = card.querySelector('[data-testid="title"]')?.innerText.trim() || '';
        const price = card.querySelector('[data-testid="price-and-discounted-price"]')?.innerText.trim() || null;
        let rating = null, reviews = null;
        const score = card.querySelector('[data-testid="review-score"]');
        if (score) {
            const texts = [];
            const walk = el => el.childNodes.forEach(n =>
                n.nodeType === 3 && n.textContent.trim() ? texts.push(n.textContent.trim()) : walk(n)
            );
            walk(score);
            for (const t of texts) {
                if (!rating && /^\\d[.,]\\d$/.test(t)) rating = t;
                if (!reviews && /\\d/.test(t) && /відгук|review|bewertung|avis/i.test(t)) reviews = t;
            }
            if (!reviews) {
                for (const t of texts) {
                    if (t !== rating && /^\\d{2,}$/.test(t.replace(/[\\s\\u00a0]/g, ''))) { reviews = t; break; }
                }
            }
        }
        if (name) results.push({ name, rating, price, reviews });
    });
    return results;
}
"""


async def extract_hotels_from_page(page: Page) -> list[dict]:
    raw = await page.evaluate(EXTRACT_HOTELS_JS)
    return [h for item in raw if (h := parse_hotel_card(item))]


async def scroll_until_more_cards(page: Page, logger: logging.Logger, label: str) -> bool:
    prev = len(await page.query_selector_all('[data-testid="property-card"]'))
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await human_delay(2000, 3500)
    for _ in range(8):
        cur = len(await page.query_selector_all('[data-testid="property-card"]'))
        if cur > prev:
            return True
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await human_delay(1000, 2000)
    logger.warning(f"[{label}] No new hotels after a scroll")
    return False


def add_hotels(all_visible: list[dict], hotels: list[dict],
               label: str, target: int, logger: logging.Logger) -> None:
    seen = {h["name"] for h in hotels}
    for hotel in all_visible:
        if len(hotels) >= target:
            break
        if hotel["name"] not in seen:
            seen.add(hotel["name"])
            hotel["city"] = label
            hotel["scraped_at"] = datetime.now(timezone.utc).isoformat()
            hotels.append(hotel)
            if logger:
                logger.info(
                    f"[{label}] #{len(hotels):>2} {hotel['name']} | "
                    f"★ {hotel.get('rating') or '—':>4} | "
                    f"{hotel.get('reviews') or '—':>6} відгуків | "
                    f"€{hotel.get('price_per_night') or '—'}/ніч"
                )
