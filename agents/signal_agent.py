import json
import os
from typing import List, Literal, Optional

from openai import OpenAI
from dotenv import load_dotenv
from pydantic import BaseModel

from tools.binance_tools import get_price_action_15m
from tools.deribit_tools import get_option_flow
from agents.sentiment_agent import analyze_sentiment

load_dotenv()

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key=os.getenv("OPENAI_API_KEY")
)

SYSTEM_PROMPT = """
You are a crypto futures signal synthesizer. You will be given a JSON
payload with three sections:
- "price_action": OHLC-derived support/resistance, trend indicators
  (SMA/EMA/RSI/MACD), and the latest close price. This is the PRIMARY input
  for direction and entry/stop/target levels.
- "option_flow": per-strike open interest/GEX/DEX, chain totals (total_gex,
  total_dex, put_call_oi_ratio, max_pain, zero_gamma_level, gex_regime,
  spot_vs_zero_gamma), and spot price. This is the SECOND primary input —
  high-OI strikes act as support/resistance. `gex_regime` is already
  computed in code ("dampening" or "amplifying") — always use it directly
  instead of re-deriving it from the sign of total_gex, since that sign is
  easy to misread. "dampening" informs tighter stops / higher confidence in
  levels holding; "amplifying" (especially combined with spot_vs_zero_gamma
  showing spot just crossed zero_gamma_level) means wider risk of a sharp
  move and should lower confidence.
- "sentiment": a macro calendar risk_level ("high"/"elevated"/"normal") and
  reasoning. This is SECONDARY — it must NEVER change direction. It only
  scales size_pct and confidence down (risk_level "high" should cut size
  sharply, e.g. toward 0, and say to wait until the event passes;
  "elevated" should reduce size moderately; "normal" doesn't change
  anything).

Task:
- Derive `direction` ("long"/"short"/"neutral") from price_action trend and
  option_flow positioning (agreement between the two raises confidence,
  disagreement should push toward "neutral" or lower confidence).
- Derive `entry_zone` (a [low, high] price range), `stop_loss`, and
  `take_profit` strictly from the numeric levels actually present in the
  data (support/resistance from price_action, high-OI strikes / max_pain /
  zero_gamma_level from option_flow) — never invent price levels not
  derivable from the given numbers.
- `confidence` (0.0-1.0): how much price_action and option_flow agree.
- `size_pct` (0-100): suggested position size as % of a normal-size
  position, after applying the sentiment risk adjustment described above.
- `reason`: 2-5 sentences explaining the direction, key levels used, and
  any sentiment-driven size adjustment.

If price_action or option_flow is missing/null, say so in `reason` and
lower confidence accordingly instead of guessing. This is not investment
advice — only a structured summary of the available signals.

Respond with a JSON object matching this schema:
{"direction": "long" | "short" | "neutral",
 "entry_zone": [<low>, <high>] | null,
 "stop_loss": <number> | null,
 "take_profit": <number> | null,
 "confidence": <0.0-1.0>,
 "size_pct": <0-100>,
 "reason": "<2-5 sentences>"}
"""


class TradeSignal(BaseModel):
    direction: Literal["long", "short", "neutral"]
    entry_zone: Optional[List[float]] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    confidence: float
    size_pct: float
    reason: str


def generate_signal(
    symbol: str,
    currency: Optional[str] = None,
) -> Optional[TradeSignal]:
    """
    `symbol` is the token ticker for Binance price action (e.g. "BTC").
    `currency` is the Deribit option underlying (e.g. "BTC"); defaults to
    `symbol` since majors share the same ticker on both venues.
    """
    currency = currency or symbol

    price_action = get_price_action_15m(symbol=symbol)
    option_flow = get_option_flow(currency=currency)
    sentiment = analyze_sentiment()

    if price_action is None and option_flow is None:
        return None

    payload = {
        "price_action": (
            {
                **price_action.model_dump(exclude={"candles"}),
                "last_close": price_action.candles[-1].close if price_action.candles else None,
            }
            if price_action else None
        ),
        "option_flow": option_flow.model_dump() if option_flow else None,
        "sentiment": sentiment.model_dump() if sentiment else None,
    }

    response = client.chat.completions.create(
        model=os.getenv("MODEL"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    return TradeSignal.model_validate(json.loads(content))


if __name__ == "__main__":
    print(generate_signal(symbol="BTC"))
