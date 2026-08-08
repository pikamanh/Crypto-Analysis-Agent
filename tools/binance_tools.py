from typing import Optional

from data.binance import get_klines_data

def get_price_action_15m(name: Optional[str] = None, symbol: Optional[str] = None, limit: int = 200):
    """
    Get 15-minute OHLC candles and technical indicators (SMA 20/50, EMA 12/26,
    RSI 14, MACD, recent support/resistance) for a token, sourced from Binance
    spot market data.

    Use `symbol` when the user gives a ticker (e.g. "BTC", "ETH") — this is
    preferred since it maps directly to the Binance trading pair (SYMBOL+USDT).
    Use `name` when only the full project name is known (e.g. "Bitcoin").
    Exactly one of `name` or `symbol` should be provided.
    `limit` is the number of 15-minute candles to fetch (max 1000). Defaults
    to 200 (~50 hours), enough for all indicators.
    Returns None if the token has no USDT trading pair on Binance.
    """
    return get_klines_data(name=name, symbol=symbol, interval="15m", limit=limit)
