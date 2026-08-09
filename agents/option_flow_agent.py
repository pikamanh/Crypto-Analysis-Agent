import json
import os
from typing import Literal, Optional

from openai import OpenAI
from dotenv import load_dotenv
from pydantic import BaseModel

from tools.deribit_tools import get_option_flow

load_dotenv()

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key=os.getenv("OPENAI_API_KEY")
)

SYSTEM_PROMPT = """
You are an options flow analyst for crypto futures trading. You will be
given a JSON snapshot of a currency's option chain: spot price, per-strike
open interest/GEX/DEX, and chain-wide totals (total_gex, total_dex,
put_call_oi_ratio, max_pain, zero_gamma_level, gex_regime,
spot_vs_zero_gamma).

`gex_regime` and `spot_vs_zero_gamma` are already computed for you in code
— always use them directly as-is instead of re-deriving the regime
yourself from the sign of total_gex (small models tend to misread that
sign, so don't second-guess these two fields).

Background on the metrics (this is the standard dealer-positioning
convention used across public GEX trackers, not a guarantee of actual
dealer positioning):
- gex_regime "dampening" (positive total_gex): dealers are net long gamma,
  so their hedging (buying dips / selling rallies) tends to dampen
  volatility and pin price near high-OI strikes or max_pain.
- gex_regime "amplifying" (negative total_gex): dealers are net short
  gamma, so their hedging (selling into drops / buying into rallies)
  amplifies moves — higher risk of sharp continuation.
- zero_gamma_level: the price level where dealer hedging flips between
  dampening and amplifying. spot_vs_zero_gamma ("above"/"below") tells you
  which side spot is currently on; price crossing this level is a
  regime-change signal.
- Strikes with large open interest / |net_gex| act as potential magnet or
  support/resistance levels from dealer hedging flow.
- put_call_oi_ratio above 1 skews put-heavy (hedging or bearish
  positioning); below 1 skews call-heavy.
- max_pain is a weak magnet toward expiry, not a reliable standalone signal.

Analyze:
- The chain's gex_regime and where spot sits relative to zero_gamma_level
  (spot_vs_zero_gamma).
- Which nearby strikes have outsized OI/GEX and could act as support or
  resistance.
- What put_call_oi_ratio and max_pain add to the picture.

Never invent numbers not in the provided data. If a field is null, say the
data isn't available instead of guessing. Do not give investment advice —
only report and interpret option positioning.

Respond with a JSON object matching this schema:
{"trend": "bullish" | "bearish" | "neutral", "reasoning": "<2-4 sentences>"}
"trend" here means the directional/volatility bias implied by option
positioning (e.g. negative GEX below zero_gamma_level with heavy put OI
skews bearish/high-risk; positive GEX pinning near max_pain skews neutral).
"""


class OptionFlowAnalysis(BaseModel):
    trend: Literal["bullish", "bearish", "neutral"]
    reasoning: str


def analyze_option_flow(
    currency: str = "BTC",
    max_days_to_expiry: int = 45,
    strike_range_pct: float = 0.3,
) -> Optional[OptionFlowAnalysis]:
    snapshot = get_option_flow(
        currency=currency,
        max_days_to_expiry=max_days_to_expiry,
        strike_range_pct=strike_range_pct,
    )
    if snapshot is None:
        return None

    payload = snapshot.model_dump()

    response = client.chat.completions.create(
        model=os.getenv("MODEL"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    return OptionFlowAnalysis.model_validate(json.loads(content))


if __name__ == "__main__":
    print(analyze_option_flow(currency="BTC"))
