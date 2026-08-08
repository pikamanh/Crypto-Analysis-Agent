from tools.crypto_tools import get_coin_info

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
]

TOOL_FUNCTIONS = {
    "get_coin_info": get_coin_info,
}
