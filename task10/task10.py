import requests
import logging
from decimal import Decimal, ROUND_DOWN

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CoinMarketScraper:

    def round_nums(self, price, change24h, market_cap, supply):

        price = Decimal(str(price)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        change24h = Decimal(str(change24h)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        market_cap = Decimal(str(market_cap)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        supply = Decimal(str(supply)).quantize(Decimal("1"), rounding=ROUND_DOWN)

        return price, change24h, market_cap, supply

    def api_scrape_coin_market(self):
        url = "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing?start=1&limit=100"
        r = requests.get(url)
        data = r.json()

        return data

    def parse_coin_market_data(self, data):
        market = []
        coins = data['data']['cryptoCurrencyList']
        for coin in coins[1:]:
            name = coin['name']
            symbol = coin['symbol']
            price = coin['quotes'][0]['price']
            change24h = coin['quotes'][0]['percentChange24h']
            market_cap = coin['quotes'][0]['marketCap']
            supply = coin['circulatingSupply']

            price, change24h, market_cap, supply = self.round_nums(price, change24h, market_cap, supply)

            market.append({
                "name": name,
                "symbol": symbol,
                "price": price,
                "change_24h": change24h,
                "market_cap": market_cap,
                "supply": supply,
            })
            logger.info(
                "Coin: %s (%s) | Price: %s | 24h: %s | Market Cap: %s",
                name,
                symbol,
                price,
                change24h,
                market_cap
            )
        return market

if __name__ == "__main__":
    scraper = CoinMarketScraper()
    data = scraper.api_scrape_coin_market()
    scraper.parse_coin_market_data(data)