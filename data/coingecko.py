import os
import logging
import pandas as pd
from dotenv import load_dotenv

from coingecko_sdk import Coingecko
from pydantic import BaseModel
from typing import List, Optional, Literal

from data.sheets import GoogleSheets

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
_data_instance = None

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
    whitepaper: Optional[str] = None
    github_repos: List[str] = []
    genesis_date: Optional[str] = None
    reddit_subscribers: Optional[int] = None
    github_stars: Optional[int] = None
    github_forks: Optional[int] = None
    github_commit_count_4_weeks: Optional[int] = None
    current_price_usd: Optional[float] = None
    market_cap_usd: Optional[float] = None
    market_cap_rank: Optional[int] = None
    fdv_usd: Optional[float] = None
    total_volume_usd: Optional[float] = None
    circulating_supply: Optional[float] = None
    total_supply: Optional[float] = None
    max_supply: Optional[float] = None
    price_change_percentage_24h: Optional[float] = None
    price_change_percentage_7d: Optional[float] = None
    price_change_percentage_30d: Optional[float] = None
    ath_usd: Optional[float] = None
    ath_change_percentage_usd: Optional[float] = None

class CoingeckoData:
    def __init__(self):
        try:
            self.client = Coingecko(
                demo_api_key=os.getenv("COINGECKO_API_KEY"),
                environment='demo',
            )
            logger.info("Connected Coingecko successfully.")
        except Exception as e:
            self.client = None
            logger.error("Connected Coingecko failed.")
            logger.exception(e)

        self.gg = GoogleSheets()
        self._coin_id_df = None

    def get_coin_by_id(self, name: str = None, symbol: str = None) -> Optional[CoinDetail]:
        if self.client is None:
            raise RuntimeError("Coingecko client not initialized")
        if self._coin_id_df is None:
            self._coin_id_df = self.gg.get_coin_id()

        df = self._coin_id_df

        if name:
            id = df.loc[df['Name'].str.lower() == name.lower()]['ID']
        elif symbol:
            id = df.loc[df['Symbol'].str.lower() == symbol.lower()]['ID']
        else:
            raise ValueError("Name or symbol is None. Please provided.")

        if id.empty:
            logger.error(f"Coin not found: name={name}, symbol={symbol}")
            return None

        response = self.client.coins.get_id(id.values[0])
        market = response.market_data
        links = response.links
        community = response.community_data
        developer = response.developer_data

        coin_detail = CoinDetail(
            id=response.id,
            symbol=response.symbol,
            name=response.name,
            description=(response.description or {}).get("en"),
            categories=[c for c in (response.categories or []) if c],
            homepage=next((h for h in (links.homepage or []) if h), None) if links else None,
            whitepaper=links.whitepaper if links and links.whitepaper else None,
            github_repos=list(links.repos_url.github or []) if links and links.repos_url else [],
            genesis_date=response.genesis_date,
            reddit_subscribers=community.reddit_subscribers if community else None,
            github_stars=developer.stars if developer else None,
            github_forks=developer.forks if developer else None,
            github_commit_count_4_weeks=developer.commit_count_4_weeks if developer else None,
            current_price_usd=(market.current_price or {}).get("usd") if market else None,
            market_cap_usd=(market.market_cap or {}).get("usd") if market else None,
            market_cap_rank=market.market_cap_rank if market else None,
            fdv_usd=(market.fully_diluted_valuation or {}).get("usd") if market else None,
            total_volume_usd=(market.total_volume or {}).get("usd") if market else None,
            circulating_supply=market.circulating_supply if market else None,
            total_supply=market.total_supply if market else None,
            max_supply=market.max_supply if market else None,
            price_change_percentage_24h=market.price_change_percentage_24h if market else None,
            price_change_percentage_7d=market.price_change_percentage_7d if market else None,
            price_change_percentage_30d=market.price_change_percentage_30d if market else None,
            ath_usd=(market.ath or {}).get("usd") if market else None,
            ath_change_percentage_usd=(market.ath_change_percentage or {}).get("usd") if market else None,
        )

        logger.info(f"Get coin by id successfully: {coin_detail.id}")
        return coin_detail

def get_coin_data(name: str = None, symbol: str = None):
    global _data_instance
    if _data_instance is None:
        _data_instance = CoingeckoData()
    return _data_instance.get_coin_by_id(name=name, symbol=symbol)

if __name__ == "__main__":
    coingecko_data = CoingeckoData()
    print(coingecko_data.get_coin_by_id(name="worldcoin"))