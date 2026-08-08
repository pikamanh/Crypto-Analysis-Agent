import os
import logging
import pandas as pd
from dotenv import load_dotenv

from coingecko_sdk import Coingecko
from pydantic import BaseModel
from typing import List, Optional, Literal

from sheets import GoogleSheets

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

class CoinInfo(BaseModel):
    id: str
    symbol: str
    name: str
    curr_price: float
    market_cap: Optional[float] = None
    fdv: Optional[float] = None
    total_volume: Optional[float] = None
    total_supply: Optional[float] = None
    circulating_supply: Optional[float] = None
    max_supply: Optional[float] = None

class AllCoin(BaseModel):
    list_coin: List[CoinInfo]

class CoinDetail(BaseModel):
    id: str
    symbol: str
    name: str
    description: Optional[str] = None
    categories: List[str] = []
    homepage: Optional[str] = None
    current_price_usd: Optional[float] = None
    market_cap_usd: Optional[float] = None
    total_volume_usd: Optional[float] = None
    ath_usd: Optional[float] = None
    ath_change_percentage_usd: Optional[float] = None

class CoingeckoData:
    def __init__(self):
        try:
            self.client = Coingecko(
                pro_api_key=os.getenv("COINGECKO_API_KEY"),
                environment='demo'
            )
            logger.info("Connected Coingecko successfully.")
        except Exception as e:
            logger.error("Connected Coingecko failed.")
            logger.exception(e)

        self.gg = GoogleSheets()

    def get_coin_market(self):
        list_coin = list()

        responses = self.client.coins.markets.get(
            vs_currency="usd"
        )

        if len(responses) == 0:
            logger.error("Please try again after 60 seconds.")
            return

        for res in responses:
            coin_info = CoinInfo(
                id = res.id,
                symbol = res.symbol,
                name = res.name,
                curr_price = res.current_price,
                market_cap = res.market_cap,
                fdv = res.fully_diluted_valuation,
                total_volume = res.total_volume,
                total_supply = res.total_supply,
                circulating_supply = res.circulating_supply,
                max_supply = res.max_supply,
            )
            list_coin.append(coin_info)

        logger.info("Get coin in market successfully.")
        return AllCoin(list_coin=list_coin)

    def get_coin_by_id(self, name: str = None, symbol: str = None) -> Optional[CoinDetail]:
        df = self.gg.get_coin_id()

        if name:
            id = df.loc[df['Name'].str.lower() == name.lower()]['ID']
        elif symbol:
            id = df.loc[df['Symbol'].str.lower() == symbol.lower()]['ID']

        if id.empty:
            logger.error(f"Coin not found: name={name}, symbol={symbol}")
            return None

        response = self.client.coins.get_id(id.values[0])
        market = response.market_data

        coin_detail = CoinDetail(
            id=response.id,
            symbol=response.symbol,
            name=response.name,
            description=(response.description or {}).get("en"),
            categories=[c for c in (response.categories or []) if c],
            homepage=next((h for h in (response.links.homepage or []) if h), None) if response.links else None,
            current_price_usd=(market.current_price or {}).get("usd") if market else None,
            market_cap_usd=(market.market_cap or {}).get("usd") if market else None,
            total_volume_usd=(market.total_volume or {}).get("usd") if market else None,
            ath_usd=(market.ath or {}).get("usd") if market else None,
            ath_change_percentage_usd=(market.ath_change_percentage or {}).get("usd") if market else None,
        )

        logger.info(f"Get coin by id successfully: {coin_detail.id}")
        return coin_detail

if __name__ == "__main__":
    coingecko_data = CoingeckoData()
    print(coingecko_data.get_coin_by_id(name="bitcoin"))