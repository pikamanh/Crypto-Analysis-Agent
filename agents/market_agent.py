import json
import os
from typing import Literal, Optional

from openai import OpenAI
from dotenv import load_dotenv
from pydantic import BaseModel

from tools.coingecko_tools import get_coin_info

load_dotenv()

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key=os.getenv("OPENAI_API_KEY")
)

SYSTEM_PROMPT = """
You are a cryptocurrency market trend analyst.

You will be given a JSON snapshot of a token's market data (price change over
24h/7d/30d, volume, market cap, FDV). Your job is purely quantitative trend
reading, not project research.

Analyze:
- Direction and consistency of price change across the 24h/7d/30d windows
  (e.g. a short-term bounce inside a longer downtrend vs. a sustained trend).
- Whether trading volume supports the price move (rising volume with rising
  price is a stronger signal than rising price on falling volume).
- Whether market cap looks stretched or cheap relative to FDV (large gap
  implies future dilution risk).
- Distance from all-time high as context, not as a standalone signal.

Never invent numbers that are not in the provided data. If a field is null,
say the data isn't available instead of guessing. Do not give investment
advice — only report and interpret the trend.

Respond with a JSON object matching this schema:
{"trend": "bullish" | "bearish" | "neutral", "reasoning": "<2-4 sentences>"}
"""


class MarketAnalysis(BaseModel):
    trend: Literal["bullish", "bearish", "neutral"]
    reasoning: str


MARKET_FIELDS = [
    "symbol",
    "name",
    "current_price_usd",
    "market_cap_usd",
    "market_cap_rank",
    "fdv_usd",
    "total_volume_usd",
    "price_change_percentage_24h",
    "price_change_percentage_7d",
    "price_change_percentage_30d",
    "ath_usd",
    "ath_change_percentage_usd",
]


def analyze_market(name: Optional[str] = None, symbol: Optional[str] = None) -> Optional[MarketAnalysis]:
    coin = get_coin_info(name=name, symbol=symbol)
    if coin is None:
        return None

    snapshot = coin.model_dump(include=set(MARKET_FIELDS))
    print(snapshot)

    response = client.chat.completions.create(
        model=os.getenv("MODEL"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(snapshot)},
        ],
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    return MarketAnalysis.model_validate(json.loads(content))


if __name__ == "__main__":
    print(analyze_market(name="bitcoin"))
