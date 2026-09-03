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
        if t % 60 != 0:
            # The last element is usually the still-forming current-minute
            # bar, stamped with the actual (sub-minute) fetch time instead of
            # a minute boundary — every closed candle lands exactly on :00.
            # Keeping it would insert a near-duplicate, volume=0 row each
            # poll (distinct ts under the (ts, symbol, exchange) PK, so
            # ON CONFLICT DO NOTHING doesn't dedupe it) until the bar
            # finally closes on the next full minute.
            continue
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


def _fetch_chart(params: Dict[str, Any], cache_key: str) -> List[dict]:
    def fetch() -> List[dict]:
        resp = requests.get(CHART_URL, params=params, headers=REQUEST_HEADERS, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        chart = payload.get("chart") or {}
        result = chart.get("result")
        if not result:
            raise RuntimeError(f"Yahoo chart API returned no result: {chart.get('error')}")
        return _rows_from_chart_result(result[0])

    return _cached_get_raw(_CACHE, cache_key, ttl=45, fetch_fn=fetch)


def fetch_ohlcv(interval: str = "1m", range_: str = "1d") -> List[dict]:
    """Candles for QQQ from Yahoo's chart API at any interval, oldest first.

    General form behind fetch_ohlcv_1m (see its docstring for why the poll
    loop pins interval="1m", range_="1d"). Also used standalone by
    scripts/backfill_qqq_ohlcv.py for one-off history pulls at coarser
    intervals (1h/1d), which aren't subject to that same "don't repopulate
    truncated history" constraint since they write to CSV, not raw_ohlcv.

    Note: for interval="1m" specifically, Yahoo rejects any `range_` beyond
    "5d" with a 422 ("Only 8 days worth of 1m granularity data are allowed
    to be fetched per request") even though ~30 days of 1m history exists
    overall — see fetch_ohlcv_1m_history for paging around that per-request cap.
    """
    params = {"range": range_, "interval": interval, "includePrePost": "false"}
    return _fetch_chart(params, cache_key=f"ohlcv:{interval}:{range_}")


def fetch_ohlcv_1m_history(period1: int, period2: int) -> List[dict]:
    """1-minute candles for an explicit [period1, period2) unix-second
    window, oldest first — the `range_` shorthand fetch_ohlcv() uses can't
    express this because Yahoo caps any single 1m request at 8 days
    regardless of the range value requested (see fetch_ohlcv's docstring).
    scripts/backfill_qqq_ohlcv.py calls this in <=7-day chunks to page back
    through the ~30 days of 1m history Yahoo actually retains.
    """
    params = {
        "period1": period1, "period2": period2,
        "interval": "1m", "includePrePost": "false",
    }
    return _fetch_chart(params, cache_key=f"ohlcv:1m:{period1}:{period2}")


def fetch_ohlcv_1m(range_: str = "1d") -> List[dict]:
    """1-minute candles for QQQ from Yahoo's chart API, oldest first.

    `range_="1d"` (today's session only) rather than a wider span: raw_ohlcv
    is truncated back to empty by the daily backup job (data/backup.py), and
    with ON CONFLICT DO NOTHING every poll re-sends its whole range as a
    cheap no-op — so a wider range (e.g. "5d") would silently repopulate
    several days of history within one poll cycle right after each
    truncate, defeating the "today only" retention that truncate is meant
    to enforce. The cost: a cold ingest start (or a gap from the process
    being asleep) only backfills the current session, not prior days.
    """
    return fetch_ohlcv(interval="1m", range_=range_)
