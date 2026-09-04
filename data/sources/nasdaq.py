"""GEX feature snapshot for QQQ — thin wrapper around qqq_options_engine's
dashboard computation, exactly mirroring data/sources/deribit.py's shape so
the ingest pipeline treats both symbols identically (same feature tables,
keyed on ts/symbol/exchange)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Tuple

from api.qqq_options_engine import get_qqq_options_dashboard

SYMBOL = "QQQ"
EXCHANGE = "nasdaq"


def _minute_bucket_ts() -> datetime:
    """Now, floored to the minute — see deribit._minute_bucket_ts for why:
    lets ON CONFLICT DO NOTHING collapse a redundant poll (in-process loop
    and external cron both firing within the same minute) onto the same
    primary key instead of adding a second, barely-offset row."""
    return datetime.now(tz=timezone.utc).replace(second=0, microsecond=0)


def _feature_row_from_dashboard(dashboard: dict, ts: datetime | None = None) -> dict:
    kl = dashboard["key_levels"]
    ts = ts or _minute_bucket_ts()

    row = {
        "ts": ts,
        "symbol": SYMBOL,
        "exchange": EXCHANGE,
        "spot_price": kl["spot_price"],
        "call_resistance": kl["call_resistance"],
        "put_support": kl["put_support"],
        "hvl": kl["high_vol_level"],
        "day_max": kl["day_max"],
        "day_min": kl["day_min"],
        "iv": kl["implied_volatility_30d_pct"],
        "hv": kl["historical_volatility_30d_pct"],
        "iv_rank": kl["iv_rank_pct"],
    }

    top_gex = dashboard["gex_levels"][:10]
    for i in range(1, 11):
        level = top_gex[i - 1] if i <= len(top_gex) else None
        row[f"gex_strike_{i}"] = level["strike"] if level else None
        row[f"gex_net_{i}"] = level["net_gex"] if level else None

    return row


def _gex_profile_row_from_dashboard(dashboard: dict, band_pct: float = 0.20, ts: datetime | None = None) -> dict:
    spot = dashboard["key_levels"]["spot_price"]
    lo, hi = spot * (1 - band_pct), spot * (1 + band_pct)
    profile = [
        {"strike": r["strike"], "net_gex": r["net_gex"], "call_gex": r["call_gex"], "put_gex": r["put_gex"]}
        for r in dashboard["net_gex_profile"]
        if lo <= r["strike"] <= hi
    ]
    return {
        "ts": ts or _minute_bucket_ts(),
        "symbol": SYMBOL,
        "exchange": EXCHANGE,
        "spot_price": spot,
        "profile": profile,
    }


def fetch_feature_snapshot_rows() -> List[dict]:
    return [_feature_row_from_dashboard(get_qqq_options_dashboard())]


def fetch_gex_profile_snapshot_row(band_pct: float = 0.20) -> dict:
    return _gex_profile_row_from_dashboard(get_qqq_options_dashboard(), band_pct)


def fetch_snapshot_rows(band_pct: float = 0.20) -> Tuple[List[dict], dict]:
    """Combined fetch for the ingest loop's poller: one Nasdaq fetch
    (get_qqq_options_dashboard) feeds both feature_gex_snapshot (top-10 by
    |GEX|) and feature_gex_profile_snapshot (full chain, JSONB). Both rows
    share the same minute-bucketed `ts` (see _minute_bucket_ts) so a
    redundant poll landing in the same minute — the in-process loop and the
    external cron both firing, say — collides on the same primary key and
    is dropped by ON CONFLICT DO NOTHING instead of adding a second,
    barely-offset row."""
    ts = _minute_bucket_ts()
    dashboard = get_qqq_options_dashboard()
    return [_feature_row_from_dashboard(dashboard, ts)], _gex_profile_row_from_dashboard(dashboard, band_pct, ts)
