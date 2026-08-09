import json
import os
from typing import Literal, Optional

from openai import OpenAI
from dotenv import load_dotenv
from pydantic import BaseModel

from tools.calendar_tools import get_high_impact_calendar

load_dotenv()

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key=os.getenv("OPENAI_API_KEY")
)

SYSTEM_PROMPT = """
You are a macro risk analyst for crypto futures trading. You will be given
a JSON snapshot of high-impact ("3-star") economic calendar events (FOMC,
CPI, NFP, PCE, ...) around the current time: events inside a watch window
(is_high_impact_window, high_impact_events), and the next upcoming
high-impact event beyond that window (next_high_impact_event,
hours_to_next_high_impact).

This agent's job is risk warning, not price direction — a high-impact
event does not tell you which way price will move, only that volatility
risk is elevated. Never claim a directional bias from calendar data alone.

Analyze:
- If is_high_impact_window is true: there is 3-star macro news in progress
  or very recent/imminent — flag elevated volatility risk, and recommend
  reducing position size or avoiding new entries until it passes.
- If false but hours_to_next_high_impact is small (e.g. under a few hours):
  note the upcoming event and that risk will rise as it approaches.
- If false and nothing upcoming soon: state conditions are calendar-normal.

Never invent events not in the data. Do not give investment advice beyond
risk-sizing context — only report calendar risk.

Respond with a JSON object matching this schema:
{"risk_level": "high" | "elevated" | "normal", "reasoning": "<2-4 sentences>"}
"risk_level" means: "high" = inside the high-impact window right now,
"elevated" = no event in window but one is coming up soon, "normal" =
nothing high-impact nearby.
"""


class SentimentAnalysis(BaseModel):
    risk_level: Literal["high", "elevated", "normal"]
    reasoning: str


def analyze_sentiment(
    hours_ahead: int = 24, hours_behind: int = 2
) -> Optional[SentimentAnalysis]:
    snapshot = get_high_impact_calendar(hours_ahead=hours_ahead, hours_behind=hours_behind)
    if snapshot is None:
        return None

    payload = snapshot.model_dump(mode="json")

    response = client.chat.completions.create(
        model=os.getenv("MODEL"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    return SentimentAnalysis.model_validate(json.loads(content))


if __name__ == "__main__":
    print(analyze_sentiment())
