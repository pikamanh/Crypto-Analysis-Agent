from typing import Optional

from data.coingecko import get_coin_data, get_price_action_data

def get_coin_info(name: Optional[str] = None, symbol: Optional[str] = None):
    """
    Get cryptocurrency information (price, market cap, description, etc).

    Use `symbol` when the user gives a ticker (e.g. "BTC", "ETH").
    Use `name` when the user gives a full project name (e.g. "Bitcoin", "Ethereum").
    Exactly one of `name` or `symbol` should be provided.
    """
    return get_coin_data(name=name, symbol=symbol)


def get_price_action(name: Optional[str] = None, symbol: Optional[str] = None, days: str = "365"):
    """
    Get OHLC candles and technical indicators (SMA 20/50, EMA 12/26, RSI 14,
    MACD, recent support/resistance) for a token.

    Use `symbol` when the user gives a ticker (e.g. "BTC", "ETH").
    Use `name` when the user gives a full project name (e.g. "Bitcoin", "Ethereum").
    Exactly one of `name` or `symbol` should be provided.
    `days` controls the OHLC lookback window: one of "1", "7", "14", "30",
    "90", "180", "365". Candle granularity is set by CoinGecko based on this
    window (daily candles only kick in above 90 days). Defaults to "365" so
    all indicators (SMA 50 needs 50 candles, MACD needs 35) have enough data;
    a shorter window will come back with some indicators as null. Indicators
    that need more candles than are available come back as null regardless.
    """
    return get_price_action_data(name=name, symbol=symbol, days=days)