from tools.coingecko_tools import get_coin_info, get_price_action
from tools.defillama_tools import get_protocol_data
from tools.binance_tools import get_price_action_15m

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_coin_info",
            "description": """
                Get cryptocurrency information (price, market cap, description, etc).

                Use `symbol` when the user gives a ticker (e.g. "BTC", "ETH").
                Use `name` when the user gives a full project name (e.g. "Bitcoin", "Ethereum").
                Exactly one of `name` or `symbol` should be provided.
                """,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Full token name"},
                    "symbol": {"type": "string", "description": "Token symbol"},
                },
            },
        },
    },
    {
            "type": "function",
            "function": {
                "name": "get_protocol_data",
                "description": """
                    Get DeFi protocol information (TVL, category, funding raises, hacks) for a token.

                    Use `symbol` when the user gives a ticker (e.g. "AAVE", "UNI").
                    Use `name` when the user gives a full project name (e.g. "Aave", "Uniswap").
                    Exactly one of `name` or `symbol` should be provided.
                    Returns nothing if the token has no associated DeFi protocol.
                    """,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Full token name"},
                        "symbol": {"type": "string", "description": "Token symbol"},
                    },
                },
            },
        },
    {
        "type": "function",
        "function": {
            "name": "get_price_action",
            "description": """
                Get OHLC candles and technical indicators (SMA 20/50, EMA 12/26,
                RSI 14, MACD, recent support/resistance) for a token.

                Use `symbol` when the user gives a ticker (e.g. "BTC", "ETH").
                Use `name` when the user gives a full project name (e.g. "Bitcoin", "Ethereum").
                Exactly one of `name` or `symbol` should be provided.
                `days` is the OHLC lookback window: one of "1", "7", "14", "30",
                "90", "180", "365". Defaults to "365" so indicators have enough
                candles to compute.
                """,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Full token name"},
                    "symbol": {"type": "string", "description": "Token symbol"},
                    "days": {"type": "string", "description": "OHLC lookback window in days"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_price_action_15m",
            "description": """
                Get 15-minute OHLC candles and technical indicators (SMA 20/50,
                EMA 12/26, RSI 14, MACD, recent support/resistance) for a token,
                sourced from Binance spot market data. Use this for short-term /
                intraday price action instead of get_price_action.

                Use `symbol` when the user gives a ticker (e.g. "BTC", "ETH") —
                preferred, maps directly to the Binance trading pair.
                Use `name` only when the full project name is known.
                Exactly one of `name` or `symbol` should be provided.
                Returns nothing if the token has no USDT pair on Binance.
                """,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Full token name"},
                    "symbol": {"type": "string", "description": "Token symbol"},
                },
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "get_coin_info": get_coin_info,
    "get_protocol_data": get_protocol_data,
    "get_price_action": get_price_action,
    "get_price_action_15m": get_price_action_15m,
}
