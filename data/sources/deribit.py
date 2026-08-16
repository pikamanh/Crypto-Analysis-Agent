"""Raw options chain snapshot — thin wrapper around options_engine's fetcher
so the ingest pipeline and the dashboard share one Deribit REST client."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Tuple

from api.options_engine import get_raw_chain_snapshot

SYMBOL = "BTC"
EXCHANGE = "deribit"


def fetch_raw_chain_rows() -> List[dict]:
    spot, rows = get_raw_chain_snapshot()
    ts = datetime.now(tz=timezone.utc)
    return [
        {
            "ts": ts,
            "symbol": SYMBOL,
            "exchange": EXCHANGE,
            "expiry": r["expiry"],
            "strike": r["strike"],
            "option_type": r["option_type"],
            "open_interest": r["open_interest"],
            "volume": r["volume"],
            "mark_iv": r["mark_iv"],
            "mark_price": r["mark_price"],
            "underlying_price": spot,
        }
        for r in rows
    ]
