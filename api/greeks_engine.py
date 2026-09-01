"""Shared, symbol-agnostic GEX/Greeks math and aggregation helpers.

Split out of options_engine.py (originally BTC/Deribit-only) so a second
underlying (QQQ/Nasdaq — see data/sources/nasdaq.py and
api/qqq_options_engine.py) can reuse the same Black-Scholes greeks and
per-strike/per-expiry aggregation logic instead of duplicating it. Nothing
in this module is Deribit- or BTC-specific — the risk-free rate that used
to be a BTC-only module constant (`RISK_FREE_RATE = 0.0`, a Deribit
forward-pricing simplification) is now an explicit `r` parameter so QQQ can
pass a real short rate instead.

The aggregation helpers below operate on generic per-instrument row-dicts
with at least these keys: `strike`, `expiry_ms`, `t_years`, `is_call`, `oi`,
`volume`, `iv`, `gamma`, `delta`, `gex`, `dex`. Building that row list (i.e.
fetching a chain and computing gamma/delta/gex/dex per row) is left to each
source-specific engine (options_engine.py, qqq_options_engine.py).
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Black-Scholes greeks
# ---------------------------------------------------------------------------

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _bs_d1(spot: float, strike: float, t_years: float, sigma: float, r: float = 0.0) -> float:
    return (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t_years) / (
        sigma * math.sqrt(t_years)
    )


def bs_gamma(spot: float, strike: float, t_years: float, sigma: float, r: float = 0.0) -> float:
    if t_years <= 0 or sigma <= 0:
        return 0.0
    d1 = _bs_d1(spot, strike, t_years, sigma, r)
    return _norm_pdf(d1) / (spot * sigma * math.sqrt(t_years))


def bs_delta(spot: float, strike: float, t_years: float, sigma: float, is_call: bool, r: float = 0.0) -> float:
    if t_years <= 0 or sigma <= 0:
        return 1.0 if (is_call and spot > strike) else (0.0 if is_call else (-1.0 if spot < strike else 0.0))
    d1 = _bs_d1(spot, strike, t_years, sigma, r)
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0


def bs_vega(spot: float, strike: float, t_years: float, sigma: float, r: float = 0.0) -> float:
    """dPrice/dSigma — used by the Newton-Raphson implied-vol solver (see
    qqq_options_engine._implied_vol) for sources that publish bid/ask
    instead of IV directly, e.g. Nasdaq's option chain."""
    if t_years <= 0 or sigma <= 0:
        return 0.0
    d1 = _bs_d1(spot, strike, t_years, sigma, r)
    return spot * _norm_pdf(d1) * math.sqrt(t_years)


def bs_price(spot: float, strike: float, t_years: float, sigma: float, is_call: bool, r: float = 0.0) -> float:
    """Black-Scholes theoretical price — used by the IV solver (Newton-
    Raphson inversion against a quoted mid price) for sources that don't
    publish IV directly, e.g. Nasdaq's option chain."""
    if t_years <= 0 or sigma <= 0:
        intrinsic = (spot - strike) if is_call else (strike - spot)
        return max(intrinsic, 0.0)
    d1 = _bs_d1(spot, strike, t_years, sigma, r)
    d2 = d1 - sigma * math.sqrt(t_years)
    disc = math.exp(-r * t_years)
    if is_call:
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def fmt_expiry(expiry_ms: int) -> str:
    return datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc).strftime("%d %b %Y")


def week_bounds(ref: datetime) -> Tuple[datetime, datetime]:
    """Monday 00:00 UTC .. next Monday 00:00 UTC containing `ref`."""
    start = (ref - timedelta(days=ref.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


def gamma_flip(rows: List[dict], spot: float, r: float = 0.0) -> Optional[float]:
    """True zero-gamma / HVL level: the underlying price at which *total*
    dealer gamma exposure crosses zero.

    Each option's gamma depends on the underlying price via moneyness, so the
    flip point cannot be read off GEX computed at today's spot — it requires
    re-pricing gamma for every strike across a grid of hypothetical spot
    levels and finding where the aggregate switches sign. This is the same
    method public GEX trackers (e.g. SpotGamma) use for their "Gamma Flip" /
    "HVL" line.
    """
    if not rows:
        return None

    strikes = [row["strike"] for row in rows]
    lo, hi = min(spot * 0.5, min(strikes)), max(spot * 1.5, max(strikes))
    steps = 300
    grid = [lo + (hi - lo) * i / steps for i in range(steps + 1)]

    totals = []
    for s in grid:
        total = 0.0
        for row in rows:
            g = bs_gamma(s, row["strike"], row["t_years"], row["iv"], r)
            dollar_gamma = g * row["oi"] * row["contract_size"] * s * s * 0.01
            total += dollar_gamma if row["is_call"] else -dollar_gamma
        totals.append(total)

    crossings = []
    for i in range(len(grid) - 1):
        t0, t1 = totals[i], totals[i + 1]
        if t0 == 0:
            crossings.append(grid[i])
        elif (t0 < 0) != (t1 < 0):
            frac = t0 / (t0 - t1)
            crossings.append(grid[i] + frac * (grid[i + 1] - grid[i]))

    if not crossings:
        # gamma never flips sign across the scanned range (e.g. one-sided
        # book) — fall back to the price with smallest |total gamma|.
        return min(zip(grid, totals), key=lambda gt: abs(gt[1]))[0]

    # multiple crossings can occur with lumpy OI; the tradable one is the
    # one nearest today's spot.
    return min(crossings, key=lambda c: abs(c - spot))


def key_levels(strike_rows: Dict[float, dict], spot: float, rows: Optional[List[dict]] = None, r: float = 0.0) -> dict:
    """Derive call resistance / put support / HVL / max-GEX / max-OI strikes
    from a per-strike aggregation. `strike_rows` maps strike -> aggregated dict
    with keys: net_gex, call_gex, put_gex, call_oi, put_oi. `rows` (raw,
    per-instrument) is used for the HVL/gamma-flip scan when supplied.
    """
    if not strike_rows:
        return {
            "call_resistance": None, "put_support": None, "hvl": None,
            "max_gex_strike": None, "max_call_oi_strike": None, "max_put_oi_strike": None,
        }

    strikes_sorted = sorted(strike_rows.keys())

    # Call resistance: strike at/above spot with the largest *net* GEX bar —
    # matches the net_gex bars actually drawn on the chart. Using call-side
    # GEX alone (ignoring that strike's put contribution) can pick a strike
    # that isn't the tallest bar on screen whenever call/put OI overlap at
    # the same strike.
    above = [k for k in strikes_sorted if k >= spot]
    call_resistance = max(above, key=lambda k: strike_rows[k]["net_gex"]) if above else None

    # Put support: strike at/below spot with the most negative *net* GEX bar
    # — same reasoning, kept consistent with the plotted net_gex column
    # rather than the isolated put_gex figure.
    below = [k for k in strikes_sorted if k <= spot]
    put_support = min(below, key=lambda k: strike_rows[k]["net_gex"]) if below else None

    # HVL ("hedge vol level" / zero-gamma flip): the underlying price where
    # *total* dealer gamma exposure crosses zero, found by re-pricing gamma
    # across a grid of hypothetical spot levels (see `gamma_flip`). Snapped
    # to the nearest strike so it lines up with the strike-bucketed chart.
    if rows:
        flip_price = gamma_flip(rows, spot, r)
        hvl = min(strikes_sorted, key=lambda k: abs(k - flip_price)) if flip_price is not None else None
    else:
        hvl = None
    if hvl is None:
        hvl = min(strikes_sorted, key=lambda k: abs(strike_rows[k]["net_gex"]))

    max_gex_strike = max(strikes_sorted, key=lambda k: abs(strike_rows[k]["net_gex"]))
    max_call_oi_strike = max(strikes_sorted, key=lambda k: strike_rows[k]["call_oi"])
    max_put_oi_strike = max(strikes_sorted, key=lambda k: strike_rows[k]["put_oi"])

    return {
        "call_resistance": call_resistance,
        "put_support": put_support,
        "hvl": hvl,
        "max_gex_strike": max_gex_strike,
        "max_call_oi_strike": max_call_oi_strike,
        "max_put_oi_strike": max_put_oi_strike,
    }


def aggregate_by_strike(rows: List[dict]) -> Dict[float, dict]:
    agg: Dict[float, dict] = defaultdict(lambda: {
        "net_gex": 0.0, "call_oi": 0.0, "put_oi": 0.0, "call_gex": 0.0, "put_gex": 0.0,
        "call_oi_iv": 0.0, "put_oi_iv": 0.0,
    })
    for row in rows:
        a = agg[row["strike"]]
        a["net_gex"] += row["gex"]
        iv = row.get("iv") or 0.0
        if row["is_call"]:
            a["call_oi"] += row["oi"]
            a["call_gex"] += row["gex"]
            a["call_oi_iv"] += row["oi"] * iv
        else:
            a["put_oi"] += row["oi"]
            a["put_gex"] += row["gex"]
            a["put_oi_iv"] += row["oi"] * iv
    return dict(agg)


def profile_series(strike_rows: Dict[float, dict]) -> List[dict]:
    return [
        {
            "strike": k,
            "net_gex": v["net_gex"],
            "call_gex": v["call_gex"],
            "put_gex": v["put_gex"],
            "call_oi": v["call_oi"],
            "put_oi": v["put_oi"],
            # OI-weighted average IV per strike (call/put), for the "OI x IV
            # by Strike" panel — None when that side has no OI at that strike.
            "call_iv": (v["call_oi_iv"] / v["call_oi"]) if v["call_oi"] else None,
            "put_iv": (v["put_oi_iv"] / v["put_oi"]) if v["put_oi"] else None,
        }
        for k, v in sorted(strike_rows.items())
    ]


def positioning_block(rows_for_expiry: List[dict], strike_rows: Dict[float, dict]) -> dict:
    total_oi = sum(row["oi"] for row in rows_for_expiry)
    total_gex = sum(v["net_gex"] for v in strike_rows.values())
    total_dex = sum(row["dex"] for row in rows_for_expiry)
    call_oi = sum(row["oi"] for row in rows_for_expiry if row["is_call"])
    put_oi = sum(row["oi"] for row in rows_for_expiry if not row["is_call"])
    call_gex = sum(v["call_gex"] for v in strike_rows.values())
    put_gex = sum(v["put_gex"] for v in strike_rows.values())
    call_dex = sum(row["dex"] for row in rows_for_expiry if row["is_call"])
    put_dex = sum(row["dex"] for row in rows_for_expiry if not row["is_call"])
    return {
        "total_oi": total_oi,
        "total_gex": total_gex,
        "total_dex": total_dex,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "put_call_oi_ratio": (put_oi / call_oi) if call_oi else None,
        "put_call_gex_ratio": (put_gex / call_gex) if call_gex else None,
        "put_call_dex_ratio": (put_dex / call_dex) if call_dex else None,
    }


def expiry_block(rows_for_expiry: List[dict], spot: float, total_gex_all: float, label: str, expiry_ms: Optional[int] = None,
                  period_label: Optional[str] = None, r: float = 0.0) -> dict:
    strike_rows = aggregate_by_strike(rows_for_expiry)
    levels = key_levels(strike_rows, spot, rows_for_expiry, r)
    total_gex = sum(v["net_gex"] for v in strike_rows.values())
    gex_expiring_pct = (abs(total_gex) / abs(total_gex_all) * 100.0) if total_gex_all else 0.0

    out = {
        "label": label,
        "gex": total_gex,
        "gex_expiring_pct": gex_expiring_pct,
        "call_resistance": levels["call_resistance"],
        "put_support": levels["put_support"],
        "hvl": levels["hvl"],
        "spot": spot,
        "profile": profile_series(strike_rows),
    }
    if expiry_ms is not None:
        out["expiration_date"] = fmt_expiry(expiry_ms)
        remaining = expiry_ms / 1000.0 - time.time()
        out["time_to_expiration_hours"] = max(remaining, 0.0) / 3600.0
    if period_label is not None:
        out["period"] = period_label
    return out


def gamma_regime(total_gex: float, total_abs_gex: float) -> str:
    if total_abs_gex == 0:
        return "neutral"
    ratio = total_gex / total_abs_gex
    if ratio > 0.08:
        return "positive"
    if ratio < -0.08:
        return "negative"
    return "neutral"


def top_n_by(strike_rows: Dict[float, dict], key: str, n: int = 3) -> List[dict]:
    ranked = sorted(strike_rows.items(), key=lambda kv: kv[1][key], reverse=True)[:n]
    return [{"strike": k, "value": v[key]} for k, v in ranked]


def top_n_abs_gex(strike_rows: Dict[float, dict], n: int = 10) -> List[dict]:
    """Top strikes ranked by |net GEX| — the public "GEX Level" leaderboard."""
    ranked = sorted(strike_rows.items(), key=lambda kv: abs(kv[1]["net_gex"]), reverse=True)[:n]
    return [
        {
            "rank": i + 1,
            "strike": k,
            "net_gex": v["net_gex"],
            "call_gex": v["call_gex"],
            "put_gex": v["put_gex"],
            "call_oi": v["call_oi"],
            "put_oi": v["put_oi"],
        }
        for i, (k, v) in enumerate(ranked)
    ]
