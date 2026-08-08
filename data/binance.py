import logging
from typing import Optional

from binance_sdk_spot.spot import Spot
from binance_common.configuration import ConfigurationRestAPI

from data.sheets import GoogleSheets
from data.indicators import Candle, PriceActionSnapshot, build_price_action_snapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
_data_instance = None


class BinanceData:
    def __init__(self):
        self.client = Spot()
        logger.info("Connected Binance successfully.")

        self.gg = GoogleSheets()
        self._coin_id_df = None

    def _resolve_pair(self, name: Optional[str], symbol: Optional[str], quote: str) -> Optional[str]:
        if symbol:
            return f"{symbol.upper()}{quote}"

        if self._coin_id_df is None:
            self._coin_id_df = self.gg.get_coin_id()

        if not name:
            raise ValueError("Name or symbol is None. Please provided.")

        match = self._coin_id_df.loc[self._coin_id_df["Name"].str.lower() == name.lower()]["Symbol"]
        if match.empty:
            return None

        return f"{match.values[0].upper()}{quote}"

    def get_klines_snapshot(
        self,
        name: Optional[str] = None,
        symbol: Optional[str] = None,
        interval: str = "15m",
        limit: int = 200,
        quote: str = "USDT",
    ) -> Optional[PriceActionSnapshot]:
        pair = self._resolve_pair(name, symbol, quote)
        if pair is None:
            logger.error(f"Trading pair not found: name={name}, symbol={symbol}, quote={quote}")
            return None

        response = self.client.rest_api.klines(symbol=pair, interval=interval, limit=limit)
        raw = response.data()
        if not raw:
            logger.error(f"No klines data for {pair}")
            return None

        candles = [
            Candle(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
            )
            for row in raw
        ]
        snapshot = build_price_action_snapshot(
            source="binance", id=pair, interval=interval, candles=candles
        )

        logger.info(f"Get klines snapshot successfully: {pair} ({interval})")
        return snapshot


def get_klines_data(
    name: Optional[str] = None,
    symbol: Optional[str] = None,
    interval: str = "15m",
    limit: int = 200,
):
    global _data_instance
    if _data_instance is None:
        _data_instance = BinanceData()
    return _data_instance.get_klines_snapshot(name=name, symbol=symbol, interval=interval, limit=limit)


if __name__ == "__main__":
    binance_data = BinanceData()
    print(binance_data.get_klines_snapshot(symbol="BTC"))
