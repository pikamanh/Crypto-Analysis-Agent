"""
BTC options analytics engine — pulls the live options chain from Deribit's
public REST API (no auth required) and derives the metrics the options
dashboard needs: open interest, gamma/delta exposure, key levels, weekly
GEX aggregation, expected move, realized/implied vol, and gamma regime.

Deribit's book-summary endpoint does not publish per-instrument greeks, so
gamma/delta are computed in-house with Black-Scholes using each instrument's
mark IV. This is the same approximation most public GEX trackers use and is
documented inline at each step that relies on it.

All Deribit calls are wrapped in a short TTL cache (`_CACHE`) so a burst of
dashboard refreshes from multiple browser tabs doesn't hammer the upstream
API or blow past its rate limits.
"""
from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from api.http_cache import cached_get as _cached_get_raw
from api.greeks_engine import (
    aggregate_by_strike as _aggregate_by_strike,
    bs_delta,
    bs_gamma,
    expiry_block as _expiry_block,
    fmt_expiry as _fmt_expiry,
    gamma_flip as _gamma_flip,
    gamma_regime as _gamma_regime,
    key_levels as _key_levels,
    positioning_block as _positioning_block,
    profile_series as _profile_series,
    top_n_abs_gex as _top_n_abs_gex,
    top_n_by as _top_n_by,
    week_bounds as _week_bounds,
)

logger = logging.getLogger(__name__)

DERIBIT_BASE = "https://www.deribit.com/api/v2"
CONTRACT_SIZE = 1.0  # BTC options on Deribit are sized in 1 BTC per contract
RISK_FREE_RATE = 0.0  # Deribit prices options on a BTC-denominated forward; r≈0 is the standard simplification

_CACHE: Dict[str, Tuple[float, Any]] = {}


def _cached_get(url: str, params: dict, ttl: float) -> dict:
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))

    def fetch() -> dict:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()["result"]

    return _cached_get_raw(_CACHE, key, ttl, fetch)


# ---------------------------------------------------------------------------
# Black-Scholes greeks (r=0, forward-style pricing consistent with Deribit)
# ---------------------------------------------------------------------------
# bs_gamma/bs_delta and the aggregation helpers imported above now live in
# api/greeks_engine.py, shared with the QQQ/Nasdaq engine — this module just
# supplies its own r=RISK_FREE_RATE (0.0) at each call site below instead of
# relying on a module-global rate baked into the math functions.


# ---------------------------------------------------------------------------
# Raw data fetch
# ---------------------------------------------------------------------------

def _fetch_spot() -> float:
    data = _cached_get(f"{DERIBIT_BASE}/public/get_index_price", {"index_name": "btc_usd"}, ttl=15)
    return float(data["index_price"])


def _fetch_instruments() -> List[dict]:
    return _cached_get(
        f"{DERIBIT_BASE}/public/get_instruments",
        {"currency": "BTC", "kind": "option", "expired": "false"},
        ttl=300,
    )


def _fetch_book_summary() -> List[dict]:
    return _cached_get(
        f"{DERIBIT_BASE}/public/get_book_summary_by_currency",
        {"currency": "BTC", "kind": "option"},
        ttl=30,
    )


def _fetch_dvol_history(days: int = 90) -> List[list]:
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86400_000
    data = _cached_get(
        f"{DERIBIT_BASE}/public/get_volatility_index_data",
        {"currency": "BTC", "start_timestamp": start_ms, "end_timestamp": now_ms, "resolution": "3600"},
        ttl=900,
    )
    return data.get("data", [])


def _fetch_perp_daily_closes(days: int = 31) -> List[float]:
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86400_000
    data = _cached_get(
        f"{DERIBIT_BASE}/public/get_tradingview_chart_data",
        {"instrument_name": "BTC-PERPETUAL", "resolution": "1D", "start_timestamp": start_ms, "end_timestamp": now_ms},
        ttl=900,
    )
    closes = data.get("close") or []
    return [float(c) for c in closes if c is not None]


# ---------------------------------------------------------------------------
# Chain assembly
# ---------------------------------------------------------------------------

def _build_chain(spot: float) -> List[dict]:
    """Join instrument metadata with live OI/IV and compute per-strike greeks."""
    instruments = {i["instrument_name"]: i for i in _fetch_instruments()}
    book = _fetch_book_summary()
    now = time.time()

    rows = []
    for b in book:
        name = b["instrument_name"]
        inst = instruments.get(name)
        if not inst:
            continue
        oi = float(b.get("open_interest") or 0.0)
        volume = float(b.get("volume") or 0.0)
        mark_iv = b.get("mark_iv")
        if mark_iv is None or mark_iv <= 0 or oi <= 0:
            continue
        sigma = float(mark_iv) / 100.0
        strike = float(inst["strike"])
        expiry_ms = int(inst["expiration_timestamp"])
        t_years = max((expiry_ms / 1000.0 - now), 0.0) / (365.0 * 86400.0)
        is_call = inst["option_type"] == "call"
        contract_size = float(inst.get("contract_size") or CONTRACT_SIZE)

        gamma = bs_gamma(spot, strike, t_years, sigma, r=RISK_FREE_RATE)
        delta = bs_delta(spot, strike, t_years, sigma, is_call, r=RISK_FREE_RATE)

        # Dollar gamma exposure per 1% spot move; calls contribute positive
        # dealer gamma, puts negative — the standard public-GEX convention.
        dollar_gamma = gamma * oi * contract_size * spot * spot * 0.01
        gex = dollar_gamma if is_call else -dollar_gamma
        dex = delta * oi * contract_size * spot

        rows.append(
            {
                "instrument": name,
                "strike": strike,
                "expiry_ms": expiry_ms,
                "t_years": t_years,
                "is_call": is_call,
                "oi": oi,
                "volume": volume,
                "iv": sigma,
                "contract_size": contract_size,
                "gamma": gamma,
                "delta": delta,
                "gex": gex,
                "dex": dex,
            }
        )
    return rows


def get_raw_chain_snapshot() -> Tuple[float, List[dict]]:
    """Raw per-instrument options data for the ingest pipeline — OI/IV/price
    as published by Deribit, no derived greeks/GEX. Those are computed from
    this raw snapshot in the feature layer, where the formula can change
    without needing to re-fetch history."""
    spot = _fetch_spot()
    instruments = {i["instrument_name"]: i for i in _fetch_instruments()}
    book = _fetch_book_summary()

    rows = []
    for b in book:
        name = b["instrument_name"]
        inst = instruments.get(name)
        if not inst:
            continue
        oi = float(b.get("open_interest") or 0.0)
        if oi <= 0:
            continue
        mark_iv = b.get("mark_iv")
        expiry_ms = int(inst["expiration_timestamp"])
        rows.append(
            {
                "expiry": datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc).date(),
                "strike": float(inst["strike"]),
                "option_type": inst["option_type"],
                "open_interest": oi,
                "volume": float(b.get("volume") or 0.0),
                "mark_iv": float(mark_iv) if mark_iv is not None else None,
                "mark_price": float(b["mark_price"]) if b.get("mark_price") is not None else None,
            }
        )
    return spot, rows


# ---------------------------------------------------------------------------
# Vol metrics
# ---------------------------------------------------------------------------

def _current_iv_and_rank() -> Tuple[Optional[float], Optional[float]]:
    hist = _fetch_dvol_history(90)
    if not hist:
        return None, None
    closes = [row[4] for row in hist if row[4] is not None]
    if not closes:
        return None, None
    current = closes[-1]
    lo, hi = min(closes), max(closes)
    rank = ((current - lo) / (hi - lo) * 100.0) if hi > lo else 50.0
    return current, rank


def _historical_volatility_30d() -> Optional[float]:
    closes = _fetch_perp_daily_closes(31)
    if len(closes) < 5:
        return None
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    n = len(log_returns)
    mean = sum(log_returns) / n
    var = sum((x - mean) ** 2 for x in log_returns) / max(n - 1, 1)
    daily_stdev = math.sqrt(var)
    return daily_stdev * math.sqrt(365) * 100.0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_options_dashboard() -> dict:
    """Cached wrapper — see the note on qqq_options_engine.get_qqq_options_dashboard
    for why: without this, every call re-solves IV + GEX/DEX aggregation for
    the whole chain from scratch, which is real CPU work a burst of
    concurrent requests (or a request racing the ingest poll) can pile up on
    Render's throttled free-tier CPU."""
    return _cached_get_raw(_CACHE, "dashboard", ttl=25, fetch_fn=_compute_options_dashboard)


def _compute_options_dashboard() -> dict:
    now = datetime.now(timezone.utc)
    spot = _fetch_spot()
    chain_ts = time.time()
    rows = _build_chain(spot)

    if not rows:
        raise RuntimeError("Deribit returned no active BTC option instruments with open interest.")

    strike_rows_all = _aggregate_by_strike(rows)
    total_gex = sum(v["net_gex"] for v in strike_rows_all.values())
    total_abs_gex = sum(abs(v["net_gex"]) for v in strike_rows_all.values())
    total_dex = sum(r["dex"] for r in rows)
    total_oi = sum(r["oi"] for r in rows)
    call_oi = sum(r["oi"] for r in rows if r["is_call"])
    put_oi = sum(r["oi"] for r in rows if not r["is_call"])
    call_gex = sum(v["call_gex"] for v in strike_rows_all.values())
    put_gex = sum(v["put_gex"] for v in strike_rows_all.values())
    call_dex = sum(r["dex"] for r in rows if r["is_call"])
    put_dex = sum(r["dex"] for r in rows if not r["is_call"])
    total_abs_dex = sum(abs(r["dex"]) for r in rows)
    total_call_volume = sum(r["volume"] for r in rows if r["is_call"])
    total_put_volume = sum(r["volume"] for r in rows if not r["is_call"])
    top10_gex = _top_n_abs_gex(strike_rows_all, 10)

    levels = _key_levels(strike_rows_all, spot, rows, r=RISK_FREE_RATE)

    # Expirations, sorted by time.
    expiry_set = sorted({r["expiry_ms"] for r in rows})
    rows_by_expiry: Dict[int, List[dict]] = defaultdict(list)
    for r in rows:
        rows_by_expiry[r["expiry_ms"]].append(r)

    first_expiry = expiry_set[0] if len(expiry_set) > 0 else None
    next_expiry = expiry_set[1] if len(expiry_set) > 1 else None

    # 0DTE = expiry landing today (UTC); fall back to the nearest expiry.
    zero_dte_expiry = next(
        (e for e in expiry_set if datetime.fromtimestamp(e / 1000, tz=timezone.utc).date() == now.date()),
        first_expiry,
    )
    zero_dte_rows = rows_by_expiry.get(zero_dte_expiry, []) if zero_dte_expiry is not None else []
    zero_dte_strike_rows = _aggregate_by_strike(zero_dte_rows)
    zero_dte_levels = _key_levels(zero_dte_strike_rows, spot, zero_dte_rows, r=RISK_FREE_RATE)
    expiring_gex = sum(v["net_gex"] for v in zero_dte_strike_rows.values())
    expiring_abs_gex = sum(abs(v["net_gex"]) for v in zero_dte_strike_rows.values())
    # Regime badge reads the 0DTE book specifically (dealers' same-day hedging
    # pressure), not total_gex across every listed expiration — see the QQQ
    # engine's identical note.
    gamma_regime = _gamma_regime(expiring_gex, expiring_abs_gex)

    # Deribit settles daily options at 08:00 UTC — the closest crypto analogue to
    # equities' EOD/RTH close. Once it passes, 0DTE naturally rolls to the next
    # calendar day via the fallback above; expose the countdown for the UI.
    settlement_today = now.replace(hour=8, minute=0, second=0, microsecond=0)
    next_settlement = settlement_today if now < settlement_today else settlement_today + timedelta(days=1)
    next_settlement_ms = int(next_settlement.timestamp() * 1000)

    first_expiration = (
        _expiry_block(rows_by_expiry[first_expiry], spot, total_gex, "First Expiration", first_expiry, r=RISK_FREE_RATE)
        if first_expiry else None
    )
    next_expiration = (
        _expiry_block(rows_by_expiry[next_expiry], spot, total_gex, "Next Expiration", next_expiry, r=RISK_FREE_RATE)
        if next_expiry else None
    )

    cur_week_start, cur_week_end = _week_bounds(now)
    next_week_start, next_week_end = cur_week_start + timedelta(days=7), cur_week_end + timedelta(days=7)

    def in_week(expiry_ms: int, start: datetime, end: datetime) -> bool:
        dt = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc)
        return start <= dt < end

    current_week_rows = [r for r in rows if in_week(r["expiry_ms"], cur_week_start, cur_week_end)]
    next_week_rows = [r for r in rows if in_week(r["expiry_ms"], next_week_start, next_week_end)]

    current_week = _expiry_block(
        current_week_rows, spot, total_gex, "Current Week",
        period_label=f"{cur_week_start.strftime('%d %b')} – {(cur_week_end - timedelta(days=1)).strftime('%d %b %Y')}",
        r=RISK_FREE_RATE,
    )
    next_week = _expiry_block(
        next_week_rows, spot, total_gex, "Next Week",
        period_label=f"{next_week_start.strftime('%d %b')} – {(next_week_end - timedelta(days=1)).strftime('%d %b %Y')}",
        r=RISK_FREE_RATE,
    )

    call_oi_top = _top_n_by(strike_rows_all, "call_oi", 3)
    put_oi_top = _top_n_by(strike_rows_all, "put_oi", 3)
    pos_gex_strikes = {k: v for k, v in strike_rows_all.items() if v["net_gex"] > 0}
    neg_gex_strikes = {k: v for k, v in strike_rows_all.items() if v["net_gex"] < 0}
    largest_pos_gex = max(pos_gex_strikes.items(), key=lambda kv: kv[1]["net_gex"])[0] if pos_gex_strikes else None
    largest_neg_gex = min(neg_gex_strikes.items(), key=lambda kv: kv[1]["net_gex"])[0] if neg_gex_strikes else None
    largest_abs_gex = max(strike_rows_all.items(), key=lambda kv: abs(kv[1]["net_gex"]))[0] if strike_rows_all else None

    iv, iv_rank = _current_iv_and_rank()
    hv = _historical_volatility_30d()
    atm_sigma = iv / 100.0 if iv else (sum(r["iv"] for r in rows) / len(rows) if rows else 0.5)
    expected_move_1d_pct = atm_sigma * math.sqrt(1 / 365.0) * 100.0
    expected_move_1d_usd = spot * expected_move_1d_pct / 100.0
    day_max = spot + expected_move_1d_usd
    day_min = spot - expected_move_1d_usd
    distance_to_hvl_pct = (abs(spot - levels["hvl"]) / spot * 100.0) if levels["hvl"] else None

    # Multi-expiration GEX view: per-expiration totals plus each expiration's
    # own strike profile, so the frontend can let the user pick which to plot.
    multi_expiration = []
    for exp_ms in expiry_set:
        exp_rows = rows_by_expiry[exp_ms]
        exp_strike_rows = _aggregate_by_strike(exp_rows)
        exp_levels = _key_levels(exp_strike_rows, spot, exp_rows, r=RISK_FREE_RATE)
        multi_expiration.append(
            {
                "expiration_date": _fmt_expiry(exp_ms),
                "expiry_ms": exp_ms,
                "total_gex": sum(v["net_gex"] for v in exp_strike_rows.values()),
                "profile": _profile_series(exp_strike_rows),
                "positioning": _positioning_block(exp_rows, exp_strike_rows),
                "key_levels": {
                    "spot_price": spot,
                    "call_resistance": exp_levels["call_resistance"],
                    "put_support": exp_levels["put_support"],
                    "hvl": exp_levels["hvl"],
                    "max_gex_strike": exp_levels["max_gex_strike"],
                    "max_call_oi_strike": exp_levels["max_call_oi_strike"],
                    "max_put_oi_strike": exp_levels["max_put_oi_strike"],
                },
            }
        )

    return {
        "meta": {
            "market_data_ts": chain_ts,
            "gex_data_ts": chain_ts,
            "oi_data_ts": chain_ts,
            "generated_at": now.isoformat(),
            "next_settlement_utc_ms": next_settlement_ms,
        },
        "market_summary": {
            "spot_price": spot,
            "put_call_oi_ratio": (put_oi / call_oi) if call_oi else None,
            "expected_move_1d_pct": expected_move_1d_pct,
            "expected_move_1d_usd": expected_move_1d_usd,
            "gamma_regime": gamma_regime,
            "implied_volatility_pct": iv,
            "historical_volatility_pct": hv,
            "iv_rank_pct": iv_rank,
        },
        "positioning": {
            "total_oi": total_oi,
            "total_gex": total_gex,
            "total_dex": total_dex,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "put_call_oi_ratio": (put_oi / call_oi) if call_oi else None,
            "put_call_gex_ratio": (put_gex / call_gex) if call_gex else None,
            "put_call_dex_ratio": (put_dex / call_dex) if call_dex else None,
        },
        "key_levels": {
            "spot_price": spot,
            "call_resistance": levels["call_resistance"],
            "call_resistance_0dte": zero_dte_levels["call_resistance"],
            "put_support": levels["put_support"],
            "put_support_0dte": zero_dte_levels["put_support"],
            "hvl": levels["hvl"],
            "high_vol_level": levels["hvl"],
            "high_vol_level_0dte": zero_dte_levels["hvl"],
            "gamma_wall_0dte": zero_dte_levels["max_gex_strike"],
            "day_max": day_max,
            "day_min": day_min,
            "distance_to_hvl_pct": distance_to_hvl_pct,
            "implied_volatility_30d_pct": iv,
            "historical_volatility_30d_pct": hv,
            "iv_rank_pct": iv_rank,
            "total_call_volume": total_call_volume,
            "total_put_volume": total_put_volume,
            "total_oi": total_oi,
            "total_call_oi": call_oi,
            "total_put_oi": put_oi,
            "total_gex": total_abs_gex,
            "net_gex": total_gex,
            "expiring_gex": expiring_gex,
            "put_call_gex_ratio": (put_gex / call_gex) if call_gex else None,
            "total_dex": total_abs_dex,
            "net_dex": total_dex,
            "put_call_dex_ratio": (put_dex / call_dex) if call_dex else None,
            "max_gex_strike": levels["max_gex_strike"],
            "max_call_oi_strike": levels["max_call_oi_strike"],
            "max_put_oi_strike": levels["max_put_oi_strike"],
        },
        "gex_levels": top10_gex,
        "gex_concentration": {
            "largest_positive_gex_strike": largest_pos_gex,
            "largest_negative_gex_strike": largest_neg_gex,
            "largest_absolute_gex_strike": largest_abs_gex,
            "positive_gamma_zone": [min(pos_gex_strikes), max(pos_gex_strikes)] if pos_gex_strikes else None,
            "negative_gamma_zone": [min(neg_gex_strikes), max(neg_gex_strikes)] if neg_gex_strikes else None,
        },
        "oi_concentration": {
            "call_oi_top": call_oi_top,
            "put_oi_top": put_oi_top,
        },
        "net_gex_profile": _profile_series(strike_rows_all),
        "oi_profile": _profile_series(strike_rows_all),
        "multi_expiration": multi_expiration,
        "expiration_structure": {
            "first_expiration": first_expiration,
            "next_expiration": next_expiration,
            "current_week": current_week,
            "next_week": next_week,
        },
    }
