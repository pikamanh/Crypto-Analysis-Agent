from tools.coingecko_tools import get_coin_info, get_price_action
from tools.defillama_tools import get_protocol_data
from tools.binance_tools import get_price_action_15m
from tools.deribit_tools import get_option_flow
from tools.calendar_tools import get_high_impact_calendar

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
    {
        "type": "function",
        "function": {
            "name": "get_option_flow",
            "description": """
                Get an options GEX/DEX (gamma/delta exposure) profile for a
                crypto currency, sourced from Deribit's option chain.

                `currency` is the underlying, e.g. "BTC" or "ETH" (Deribit
                only lists options for a handful of majors).

                Returns per-strike open interest, GEX, and DEX, plus
                chain-wide totals, put/call OI ratio, max pain, and the
                zero-gamma level. Returns nothing if no option data could be
                fetched (e.g. unsupported currency).
                """,
            "parameters": {
                "type": "object",
                "properties": {
                    "currency": {"type": "string", "description": "Underlying currency, e.g. BTC or ETH"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_high_impact_calendar",
            "description": """
                Get high-impact ("3-star") macro economic events (FOMC, CPI,
                NFP, PCE, ...) from the ForexFactory weekly calendar,
                filtered to a window around now.

                `hours_ahead`/`hours_behind` define the watch window
                (default: last 2h to next 24h). Use this to check if the
                market is currently near a high-impact macro event — these
                tend to drive outsized crypto futures volatility.

                Returns the events inside the window, whether the window
                currently contains any high-impact event, and the next
                upcoming one beyond the window.
                """,
            "parameters": {
                "type": "object",
                "properties": {
                    "hours_ahead": {"type": "integer", "description": "Hours ahead of now to watch"},
                    "hours_behind": {"type": "integer", "description": "Hours behind now to watch"},
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
    "get_option_flow": get_option_flow,
    "get_high_impact_calendar": get_high_impact_calendar,
}
