"""GEX feature snapshot — thin wrapper around options_engine's dashboard
computation so the ingest pipeline and the live dashboard share one Deribit
REST client."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from api.options_engine import get_options_dashboard

SYMBOL = "BTC"
EXCHANGE = "deribit"


def fetch_feature_snapshot_rows() -> List[dict]:
    dashboard = get_options_dashboard()
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

    return [row]
