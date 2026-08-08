import json
import os

from openai import OpenAI
from dotenv import load_dotenv

from tools.registry import TOOL_FUNCTIONS, TOOL_SCHEMAS

load_dotenv()

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key=os.getenv("OPENAI_API_KEY")
)

SYSTEM_PROMPT = """
You are a cryptocurrency research assistant.

You have access to four tools, each covering a different part of the picture:
- get_coin_info: market data (price, market cap, FDV, supply) and project info from CoinGecko.
- get_protocol_data: on-chain health from DefiLlama (TVL, TVL change, chains, funding raises, past hacks).
- get_price_action: daily OHLC candles and technical indicators (SMA/EMA/RSI/MACD,
  support/resistance) from CoinGecko. Use this for general / longer-term technical
  questions ("how has price been trending", "is it above its 50-day average").
- get_price_action_15m: 15-minute OHLC candles and the same indicators, sourced from
  Binance. Use this specifically for short-term / intraday technical analysis
  ("what does it look like right now", scalping/day-trading style questions) —
  never get_price_action for that. Only reach for it when the user's question is
  actually about near-term price action; it's a live Binance call, not for casual
  price lookups (use get_coin_info for those).

No single tool tells the full story. When a user asks a research or "how is this
project doing" style question (not a single fact like "what's the price of X"), call
get_coin_info AND get_protocol_data for the token before answering, then connect the
results instead of reporting them side by side:
- Compare market cap / FDV against TVL to gauge whether the token looks over- or
  under-valued relative to the value it secures.
- Note funding raises and total capital raised as context for valuation and runway.
- Flag any past hacks as a risk factor, especially if TVL dropped around that date.
- If one tool has no data for the token (e.g. it's not a DeFi protocol), say so
  explicitly rather than silently dropping that angle.

When the user asks a technical-analysis question, add get_price_action (or
get_price_action_15m for intraday) to the mix and connect it the same way — e.g.
note if a bullish technical trend contradicts a stretched market cap/TVL ratio.

Rules:
- Always use a tool when the user asks for current market, protocol, or price
  action data.
- Never invent prices, TVL, indicator values, or other market statistics.
- Clearly distinguish between factual data and your own analysis.
- Do not provide financial advice.
"""

def run_tool_loop(messages: list, max_turns: int = 5):
    for _ in range(max_turns):
        response = client.chat.completions.create(
            model=os.getenv("MODEL"),
            messages=messages,
            tools=TOOL_SCHEMAS
        )
        message = response.choices[0].message

        messages.append(message.model_dump(exclude_none=True))
        if not message.tool_calls:
            return message.content

        for tool_call in message.tool_calls:
            function_name = tool_call.function.name
            function_args = json.loads(tool_call.function.arguments or "{}")

            function = TOOL_FUNCTIONS.get(function_name)
            if function is None:
                result = {"error": f"Unknown tool: {function_name}"}
            else:
                result = function(**function_args)
                if result is None:
                    result = {"error": f"No data found for arguments: {function_args}"}

            if hasattr(result, "model_dump_json"):
                content = result.model_dump_json()
            else:
                content = json.dumps(result)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": content,
            })

    return "Reached max tool-call turns without a final answer."

def ask_agent(messages: list):
    return run_tool_loop(messages)