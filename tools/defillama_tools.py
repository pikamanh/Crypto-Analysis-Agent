from typing import Optional

from data.defillama import get_protocol_tvl_data

def get_protocol_data(name: Optional[str] = None, symbol: Optional[str] = None):
    """
    Get DeFi protocol information (TVL, category, funding raises, hacks) for a token.

    Use `symbol` when the user gives a ticker (e.g. "AAVE", "UNI").
    Use `name` when the user gives a full project name (e.g. "Aave", "Uniswap").
    Exactly one of `name` or `symbol` should be provided.
    Returns None if the token has no associated DeFi protocol (e.g. it isn't a
    DeFi governance token).
    """
    return get_protocol_tvl_data(name=name, symbol=symbol)
