"""GEX feature snapshot — thin wrapper around options_engine's dashboard
computation so the ingest pipeline and the live dashboard share one Deribit
REST client."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Tuple

from api.options_engine import get_options_dashboard

SYMBOL = "BTC"
EXCHANGE = "deribit"


def _feature_row_from_dashboard(dashboard: dict) -> dict:
    kl = dashboard["key_levels"]
    ts = datetime.now(tz=timezone.utc)

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


def _gex_profile_row_from_dashboard(dashboard: dict, band_pct: float = 0.20) -> dict:
    spot = dashboard["key_levels"]["spot_price"]
    lo, hi = spot * (1 - band_pct), spot * (1 + band_pct)
    profile = [
        {"strike": r["strike"], "net_gex": r["net_gex"], "call_gex": r["call_gex"], "put_gex": r["put_gex"]}
        for r in dashboard["net_gex_profile"]
        if lo <= r["strike"] <= hi
    ]
    return {
        "ts": datetime.now(tz=timezone.utc),
        "symbol": SYMBOL,
        "exchange": EXCHANGE,
        "spot_price": spot,
        "profile": profile,
    }


def fetch_feature_snapshot_rows() -> List[dict]:
    return [_feature_row_from_dashboard(get_options_dashboard())]


def fetch_gex_profile_snapshot_row(band_pct: float = 0.20) -> dict:
    """Full per-strike net/call/put GEX profile (aggregated across all
    expiries), filtered to strikes within `band_pct` of spot — one row for
    feature_gex_profile_snapshot. Powers the GEX Interval Map's continuous
    per-strike history."""
    return _gex_profile_row_from_dashboard(get_options_dashboard(), band_pct)


def fetch_snapshot_rows(band_pct: float = 0.20) -> Tuple[List[dict], dict]:
    """Combined fetch for the ingest loop's poller: one Deribit REST call
    (get_options_dashboard) feeds both feature_gex_snapshot (top-10 by
    |GEX|) and feature_gex_profile_snapshot (full chain, JSONB) instead of
    each table doing its own separate poll."""
    dashboard = get_options_dashboard()
    return [_feature_row_from_dashboard(dashboard)], _gex_profile_row_from_dashboard(dashboard, band_pct)
