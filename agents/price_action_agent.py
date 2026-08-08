import json
import os
from typing import Literal, Optional

from openai import OpenAI
from dotenv import load_dotenv
from pydantic import BaseModel

from tools.binance_tools import get_price_action_15m

load_dotenv()

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key=os.getenv("OPENAI_API_KEY")
)

SYSTEM_PROMPT = """
You are a technical price action analyst.

You will be given a JSON snapshot of a token's OHLC candles plus derived
indicators: SMA 20/50, EMA 12/26, RSI 14, MACD (line/signal/histogram), and
recent support/resistance levels.

Analyze:
- Trend: is price above or below the SMAs, and are the shorter SMA/EMA above
  the longer ones (bullish alignment) or below (bearish alignment)?
- Momentum: is RSI overbought (>70), oversold (<30), or neutral? Is the MACD
  histogram positive/growing (bullish momentum) or negative/shrinking
  (bearish momentum)?
- Position relative to support/resistance: is price near a breakout above
  resistance, a breakdown below support, or ranging between them?

Any indicator field that is null means there weren't enough candles to
compute it (e.g. SMA 50 needs 50 candles) — say the data isn't available for
that one instead of guessing. Never invent numbers not in the provided data.
Do not give investment advice — only report and interpret price action.

Respond with a JSON object matching this schema:
{"trend": "bullish" | "bearish" | "neutral", "reasoning": "<2-4 sentences>"}
"""


class PriceActionAnalysis(BaseModel):
    trend: Literal["bullish", "bearish", "neutral"]
    reasoning: str


def analyze_price_action(
    name: Optional[str] = None,
    symbol: Optional[str] = None,
) -> Optional[PriceActionAnalysis]:
    snapshot = get_price_action_15m(name=name, symbol=symbol)
    if snapshot is None:
        return None

    payload = snapshot.model_dump(exclude={"candles"})
    payload["last_close"] = snapshot.candles[-1].close if snapshot.candles else None
    print(payload)

    response = client.chat.completions.create(
        model=os.getenv("MODEL"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    return PriceActionAnalysis.model_validate(json.loads(content))


if __name__ == "__main__":
    print(analyze_price_action(symbol="BTC"))
