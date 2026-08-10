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

logger = logging.getLogger(__name__)

DERIBIT_BASE = "https://www.deribit.com/api/v2"
CONTRACT_SIZE = 1.0  # BTC options on Deribit are sized in 1 BTC per contract
RISK_FREE_RATE = 0.0  # Deribit prices options on a BTC-denominated forward; r≈0 is the standard simplification

_CACHE: Dict[str, Tuple[float, Any]] = {}


def _cached_get(url: str, params: dict, ttl: float) -> dict:
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()["result"]
    _CACHE[key] = (now, data)
    return data


# ---------------------------------------------------------------------------
# Black-Scholes greeks (r=0, forward-style pricing consistent with Deribit)
# ---------------------------------------------------------------------------

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _bs_d1(spot: float, strike: float, t_years: float, sigma: float) -> float:
    return (math.log(spot / strike) + (RISK_FREE_RATE + 0.5 * sigma * sigma) * t_years) / (
        sigma * math.sqrt(t_years)
    )


def bs_gamma(spot: float, strike: float, t_years: float, sigma: float) -> float:
    if t_years <= 0 or sigma <= 0:
        return 0.0
    d1 = _bs_d1(spot, strike, t_years, sigma)
    return _norm_pdf(d1) / (spot * sigma * math.sqrt(t_years))


def bs_delta(spot: float, strike: float, t_years: float, sigma: float, is_call: bool) -> float:
    if t_years <= 0 or sigma <= 0:
        return 1.0 if (is_call and spot > strike) else (0.0 if is_call else (-1.0 if spot < strike else 0.0))
    d1 = _bs_d1(spot, strike, t_years, sigma)
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0


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
        mark_iv = b.get("mark_iv")
        if mark_iv is None or mark_iv <= 0 or oi <= 0:
            continue
        sigma = float(mark_iv) / 100.0
        strike = float(inst["strike"])
        expiry_ms = int(inst["expiration_timestamp"])
        t_years = max((expiry_ms / 1000.0 - now), 0.0) / (365.0 * 86400.0)
        is_call = inst["option_type"] == "call"
        contract_size = float(inst.get("contract_size") or CONTRACT_SIZE)

        gamma = bs_gamma(spot, strike, t_years, sigma)
        delta = bs_delta(spot, strike, t_years, sigma, is_call)

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
                "is_call": is_call,
                "oi": oi,
                "iv": sigma,
                "gamma": gamma,
                "delta": delta,
                "gex": gex,
                "dex": dex,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _fmt_expiry(expiry_ms: int) -> str:
    return datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc).strftime("%d %b %Y")


def _week_bounds(ref: datetime) -> Tuple[datetime, datetime]:
    """Monday 00:00 UTC .. next Monday 00:00 UTC containing `ref`."""
    start = (ref - timedelta(days=ref.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


def _key_levels(strike_rows: Dict[float, dict], spot: float) -> dict:
    """Derive call resistance / put support / HVL / max-GEX / max-OI strikes
    from a per-strike aggregation. `strike_rows` maps strike -> aggregated dict
    with keys: net_gex, call_gex, put_gex, call_oi, put_oi.
    """
    if not strike_rows:
        return {
            "call_resistance": None, "put_support": None, "hvl": None,
            "max_gex_strike": None, "max_call_oi_strike": None, "max_put_oi_strike": None,
        }

    strikes_sorted = sorted(strike_rows.keys())

    # Call resistance: strike at/above spot with the strongest call-side gamma
    # concentration — where dealer hedging of short calls tends to cap upside.
    above = [k for k in strikes_sorted if k >= spot]
    call_resistance = max(above, key=lambda k: strike_rows[k]["call_gex"]) if above else None

    # Put support: strike at/below spot with the strongest put-side gamma
    # concentration (magnitude, not net sign) — where hedging of short puts
    # tends to cushion downside.
    below = [k for k in strikes_sorted if k <= spot]
    put_support = max(below, key=lambda k: abs(strike_rows[k]["put_gex"])) if below else None

    # HVL ("hedge vol level" / zero-gamma flip): the strike where cumulative
    # net GEX actually changes sign scanning low-to-high strikes — not just
    # wherever the running total happens to be smallest, which is skewed
    # toward the low end where cumulative OI (and thus GEX) is still thin.
    cum = 0.0
    cum_by_strike = []
    for k in strikes_sorted:
        cum += strike_rows[k]["net_gex"]
        cum_by_strike.append((k, cum))

    hvl = None
    for (k_prev, cum_prev), (k_curr, cum_curr) in zip(cum_by_strike, cum_by_strike[1:]):
        if cum_prev == 0 or (cum_prev < 0) != (cum_curr < 0):
            # pick whichever side of the flip is numerically closer to zero
            hvl = k_curr if abs(cum_curr) < abs(cum_prev) else k_prev
            break
    if hvl is None:
        # book never flips sign — fall back to the smallest-magnitude point
        hvl = min(cum_by_strike, key=lambda kc: abs(kc[1]))[0]

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


def _aggregate_by_strike(rows: List[dict]) -> Dict[float, dict]:
    agg: Dict[float, dict] = defaultdict(lambda: {"net_gex": 0.0, "call_oi": 0.0, "put_oi": 0.0, "call_gex": 0.0, "put_gex": 0.0})
    for r in rows:
        a = agg[r["strike"]]
        a["net_gex"] += r["gex"]
        if r["is_call"]:
            a["call_oi"] += r["oi"]
            a["call_gex"] += r["gex"]
        else:
            a["put_oi"] += r["oi"]
            a["put_gex"] += r["gex"]
    return dict(agg)


def _profile_series(strike_rows: Dict[float, dict]) -> List[dict]:
    return [
        {"strike": k, "net_gex": v["net_gex"], "call_oi": v["call_oi"], "put_oi": v["put_oi"]}
        for k, v in sorted(strike_rows.items())
    ]


def _positioning_block(rows_for_expiry: List[dict], strike_rows: Dict[float, dict]) -> dict:
    total_oi = sum(r["oi"] for r in rows_for_expiry)
    total_gex = sum(v["net_gex"] for v in strike_rows.values())
    total_dex = sum(r["dex"] for r in rows_for_expiry)
    call_oi = sum(r["oi"] for r in rows_for_expiry if r["is_call"])
    put_oi = sum(r["oi"] for r in rows_for_expiry if not r["is_call"])
    call_gex = sum(v["call_gex"] for v in strike_rows.values())
    put_gex = sum(v["put_gex"] for v in strike_rows.values())
    call_dex = sum(r["dex"] for r in rows_for_expiry if r["is_call"])
    put_dex = sum(r["dex"] for r in rows_for_expiry if not r["is_call"])
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


def _expiry_block(rows_for_expiry: List[dict], spot: float, total_gex_all: float, label: str, expiry_ms: Optional[int] = None,
                   period_label: Optional[str] = None) -> dict:
    strike_rows = _aggregate_by_strike(rows_for_expiry)
    levels = _key_levels(strike_rows, spot)
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
        "profile": _profile_series(strike_rows),
    }
    if expiry_ms is not None:
        out["expiration_date"] = _fmt_expiry(expiry_ms)
        remaining = expiry_ms / 1000.0 - time.time()
        out["time_to_expiration_hours"] = max(remaining, 0.0) / 3600.0
    if period_label is not None:
        out["period"] = period_label
    return out


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


def _gamma_regime(total_gex: float, total_abs_gex: float) -> str:
    if total_abs_gex == 0:
        return "neutral"
    ratio = total_gex / total_abs_gex
    if ratio > 0.08:
        return "positive"
    if ratio < -0.08:
        return "negative"
    return "neutral"


def _top_n_by(strike_rows: Dict[float, dict], key: str, n: int = 3) -> List[dict]:
    ranked = sorted(strike_rows.items(), key=lambda kv: kv[1][key], reverse=True)[:n]
    return [{"strike": k, "value": v[key]} for k, v in ranked]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_options_dashboard() -> dict:
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

    levels = _key_levels(strike_rows_all, spot)
    gamma_regime = _gamma_regime(total_gex, total_abs_gex)

    # Expirations, sorted by time.
    expiry_set = sorted({r["expiry_ms"] for r in rows})
    rows_by_expiry: Dict[int, List[dict]] = defaultdict(list)
    for r in rows:
        rows_by_expiry[r["expiry_ms"]].append(r)

    first_expiry = expiry_set[0] if len(expiry_set) > 0 else None
    next_expiry = expiry_set[1] if len(expiry_set) > 1 else None

    first_expiration = (
        _expiry_block(rows_by_expiry[first_expiry], spot, total_gex, "First Expiration", first_expiry)
        if first_expiry else None
    )
    next_expiration = (
        _expiry_block(rows_by_expiry[next_expiry], spot, total_gex, "Next Expiration", next_expiry)
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
    )
    next_week = _expiry_block(
        next_week_rows, spot, total_gex, "Next Week",
        period_label=f"{next_week_start.strftime('%d %b')} – {(next_week_end - timedelta(days=1)).strftime('%d %b %Y')}",
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

    # Multi-expiration GEX view: per-expiration totals plus each expiration's
    # own strike profile, so the frontend can let the user pick which to plot.
    multi_expiration = []
    for exp_ms in expiry_set:
        exp_rows = rows_by_expiry[exp_ms]
        exp_strike_rows = _aggregate_by_strike(exp_rows)
        exp_levels = _key_levels(exp_strike_rows, spot)
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
            "put_support": levels["put_support"],
            "hvl": levels["hvl"],
            "max_gex_strike": levels["max_gex_strike"],
            "max_call_oi_strike": levels["max_call_oi_strike"],
            "max_put_oi_strike": levels["max_put_oi_strike"],
        },
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
