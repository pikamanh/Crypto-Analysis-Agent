"""QQQ price history from Yahoo Finance's free, no-auth chart API — feeds
the QQQ Price chart's raw_ohlcv rows (same table/shape the Binance kline
listener writes for BTC — see data/ingest.py's OHLCV_COLUMNS), independent
of the Nasdaq quote/option-chain APIs used elsewhere for QQQ (see
data/sources/nasdaq.py): Nasdaq's quote endpoint only exposes the latest
last-sale price, not an intraday candle series, so it can't back a price
chart on its own.

Endpoint: https://query1.finance.yahoo.com/v8/finance/chart/QQQ — unofficial
but widely relied on and reachable via plain server-side HTTP with a browser
User-Agent, no key required. Like the Nasdaq module, wrapped in the same
defensive try/except -> 502/skip pattern used elsewhere for unofficial
endpoints (see api/app.py, data/ingest.py) since it can change shape or
start blocking without notice.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

from api.http_cache import cached_get as _cached_get_raw

SYMBOL = "QQQ"
EXCHANGE = "yahoo"

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/QQQ"
# Same generic browser UA used for the Nasdaq endpoints (see
# data/sources/nasdaq.py) — Yahoo's chart API has been observed to reject
# requests with no User-Agent at all.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

_CACHE: Dict[str, Any] = {}


def _rows_from_chart_result(result: dict) -> List[dict]:
    timestamps = result.get("timestamp") or []
    indicators = (result.get("indicators") or {}).get("quote") or [{}]
    quote = indicators[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    rows = []
    for i, t in enumerate(timestamps):
        o = opens[i] if i < len(opens) else None
        h = highs[i] if i < len(highs) else None
        l = lows[i] if i < len(lows) else None
        c = closes[i] if i < len(closes) else None
        if o is None or h is None or l is None or c is None:
            continue  # Yahoo leaves pre/post-market or halted minutes as nulls
        v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0.0
        rows.append(
            {
                "ts": datetime.fromtimestamp(t, tz=timezone.utc),
                "symbol": SYMBOL,
                "exchange": EXCHANGE,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v),
            }
        )
    return rows


def fetch_ohlcv_1m(range_: str = "5d") -> List[dict]:
    """1-minute candles for QQQ from Yahoo's chart API, oldest first.

    `range_="5d"` (Yahoo's max span at 1m granularity) rather than "1d" so a
    cold ingest start (or a gap from the process being asleep) backfills
    several trading days in one call instead of trickling in one candle at a
    time — insert_rows()'s ON CONFLICT DO NOTHING makes re-sending already-
    ingested candles on every poll a no-op.
    """
    def fetch() -> List[dict]:
        resp = requests.get(
            CHART_URL,
            params={"range": range_, "interval": "1m", "includePrePost": "false"},
            headers=REQUEST_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()
        chart = payload.get("chart") or {}
        result = chart.get("result")
        if not result:
            raise RuntimeError(f"Yahoo chart API returned no result: {chart.get('error')}")
        return _rows_from_chart_result(result[0])

    return _cached_get_raw(_CACHE, f"ohlcv:{range_}", ttl=45, fetch_fn=fetch)
