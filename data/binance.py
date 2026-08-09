import logging
import time
from typing import List, Optional
import os

from binance_sdk_spot.spot import Spot
from binance_common.configuration import ConfigurationRestAPI
from binance_common.constants import SPOT_REST_API_PROD_URL

from data.sheets import GoogleSheets
from data.indicators import Candle, PriceActionSnapshot, build_price_action_snapshot

_INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
_data_instance = None

configuration = ConfigurationRestAPI(api_key=os.getenv("BINANCE_API_KEY"), api_secret=os.getenv("BINANCE_SECRET_KEY"), base_path=SPOT_REST_API_PROD_URL)

class BinanceData:
    def __init__(self):
        self.client = Spot(config_rest_api=configuration)
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

    def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: Optional[int] = None,
        quote: str = "USDT",
    ) -> List[Candle]:
        """
        Paginate Binance klines (max 1000/call) across [start_time_ms, end_time_ms]
        to build a long historical candle series for backtesting.
        """
        pair = self._resolve_pair(None, symbol, quote)
        if pair is None:
            logger.error(f"Trading pair not found for symbol={symbol}")
            return []

        step_ms = _INTERVAL_MS.get(interval)
        if step_ms is None:
            raise ValueError(f"Unsupported interval for historical fetch: {interval}")

        end_time_ms = end_time_ms or int(time.time() * 1000)
        candles: List[Candle] = []
        cursor = start_time_ms
        while cursor < end_time_ms:
            response = self.client.rest_api.klines(
                symbol=pair, interval=interval, start_time=cursor, end_time=end_time_ms, limit=1000,
            )
            raw = response.data()
            if not raw:
                break

            for row in raw:
                candles.append(Candle(
                    timestamp=int(row[0]), open=float(row[1]), high=float(row[2]),
                    low=float(row[3]), close=float(row[4]),
                ))

            last_open_time = int(raw[-1][0])
            if last_open_time <= cursor:
                break
            cursor = last_open_time + step_ms

            if len(raw) < 1000:
                break

        logger.info(f"Fetched {len(candles)} historical candles for {pair} ({interval}).")
        return candles


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


def get_historical_candles(
    symbol: str,
    interval: str,
    start_time_ms: int,
    end_time_ms: Optional[int] = None,
) -> List[Candle]:
    global _data_instance
    if _data_instance is None:
        _data_instance = BinanceData()
    return _data_instance.get_historical_candles(
        symbol=symbol, interval=interval, start_time_ms=start_time_ms, end_time_ms=end_time_ms,
    )


if __name__ == "__main__":
    binance_data = BinanceData()
    print(binance_data.get_klines_snapshot(symbol="BTC"))
