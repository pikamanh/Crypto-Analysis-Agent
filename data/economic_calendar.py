import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests
from pydantic import BaseModel
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
_data_instance = None

FOREXFACTORY_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


class EconomicEvent(BaseModel):
    title: str
    country: str
    date: datetime
    impact: str
    forecast: Optional[str] = None
    previous: Optional[str] = None


class EconomicCalendarSnapshot(BaseModel):
    source: str = "forexfactory"
    window_start: datetime
    window_end: datetime
    high_impact_events: List[EconomicEvent]
    is_high_impact_window: bool
    next_high_impact_event: Optional[EconomicEvent] = None
    hours_to_next_high_impact: Optional[float] = None


class EconomicCalendarData:
    """
    Public ForexFactory weekly calendar client (no API key required).
    "High" impact events (FOMC, CPI, NFP, PCE, ...) are the "3-star" news
    the sentiment agent watches for — they're the macro events that tend to
    drive outsized crypto futures volatility.

    Note: only covers the current calendar week (the feed's own scope) —
    an event landing in the next few hours right at week boundary could be
    missed. Acceptable for a 5-minute-polling risk-warning use case.
    """

    def __init__(self):
        self.session = requests.Session()
        retry = Retry(
            total=5,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        logger.info("Connected ForexFactory calendar successfully.")

    def _fetch_calendar(self) -> List[dict]:
        try:
            response = self.session.get(FOREXFACTORY_CALENDAR_URL, timeout=10)
            response.raise_for_status()
            return response.json() or []
        except requests.RequestException as e:
            logger.error(f"ForexFactory calendar request failed: {e}")
            return []

    def get_high_impact_snapshot(
        self, hours_ahead: int = 24, hours_behind: int = 2
    ) -> EconomicCalendarSnapshot:
        """
        `hours_ahead`/`hours_behind` define the watch window around now.
        Events with impact="High" (3 sao) inside that window mean elevated
        volatility risk right now; the closest upcoming one beyond the
        window is also surfaced so the caller can see what's coming next.
        """
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=hours_behind)
        window_end = now + timedelta(hours=hours_ahead)

        raw_events = self._fetch_calendar()
        events = []
        for e in raw_events:
            if e.get("impact") != "High":
                continue
            try:
                event_date = datetime.fromisoformat(e["date"])
            except (KeyError, ValueError):
                continue
            events.append(
                EconomicEvent(
                    title=e.get("title", ""),
                    country=e.get("country", ""),
                    date=event_date,
                    impact=e.get("impact", ""),
                    forecast=e.get("forecast"),
                    previous=e.get("previous"),
                )
            )
        events.sort(key=lambda e: e.date)

        in_window = [e for e in events if window_start <= e.date <= window_end]
        upcoming = [e for e in events if e.date > window_end]
        next_event = upcoming[0] if upcoming else None
        hours_to_next = (
            (next_event.date - now).total_seconds() / 3600 if next_event else None
        )

        snapshot = EconomicCalendarSnapshot(
            window_start=window_start,
            window_end=window_end,
            high_impact_events=in_window,
            is_high_impact_window=len(in_window) > 0,
            next_high_impact_event=next_event,
            hours_to_next_high_impact=hours_to_next,
        )

        logger.info(
            f"Get economic calendar snapshot successfully: "
            f"{len(in_window)} high-impact event(s) in window"
        )
        return snapshot


def get_economic_calendar_data(hours_ahead: int = 24, hours_behind: int = 2):
    global _data_instance
    if _data_instance is None:
        _data_instance = EconomicCalendarData()
    return _data_instance.get_high_impact_snapshot(
        hours_ahead=hours_ahead, hours_behind=hours_behind
    )


if __name__ == "__main__":
    calendar_data = EconomicCalendarData()
    print(calendar_data.get_high_impact_snapshot())
