from typing import Optional

from data.coingecko import get_coin_data

def get_coin_info(name: Optional[str] = None, symbol: Optional[str] = None):
    """
    Get cryptocurrency information (price, market cap, description, etc).

    Use `symbol` when the user gives a ticker (e.g. "BTC", "ETH").
    Use `name` when the user gives a full project name (e.g. "Bitcoin", "Ethereum").
    Exactly one of `name` or `symbol` should be provided.
    """
    return get_coin_data(name=name, symbol=symbol)