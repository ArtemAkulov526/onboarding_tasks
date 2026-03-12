import json
import logging
from curl_cffi import requests
from parsel import Selector

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s")

class Task12:

    def send_request(self):
        session = requests.Session()
        login_url = "https://quotes.toscrape.com/login"

        resp = session.get(login_url)
        selector = Selector(resp.text)

        csrf_token = selector.css('input[name="csrf_token"]::attr(value)').get()
        logger.info("Extracted CSRF token: %s", csrf_token)

        resp_login = session.post(login_url, data={
            "username": "user",
            "password": "user",
            "csrf_token": csrf_token
        })

        if resp_login.status_code == 200:
            logger.info("Login successful")
        else:
            logger.error("Login failed")

        quotes = resp_login.text
        session.get("https://quotes.toscrape.com/logout")
        logger.info("Logout successful")

        return quotes

    def get_quotes(self, quotes):
        sel = Selector(text=quotes)
        result = []

        for quote in sel.css("div.quote"):
            text = quote.css("span.text::text").get()
            author = quote.css("small.author::text").get()
            full_quote = text.strip() if text else None
            author_name = author.strip() if author else None
            goodreads_href = quote.css("a[href*='goodreads.com']::attr(href)").get()

            result.append({
                "text": full_quote,
                "author": author_name,
                "goodreads_url": goodreads_href,
            })
            logger.info(
                "Quote: %s | Author: %s | Goodreads URL: %s",
                full_quote,
                author_name,
                goodreads_href,
            )
            with open("quotes.json", "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

        return result


if __name__ == "__main__":
    request = Task12()
    quotes = request.send_request()
    request.get_quotes(quotes)
