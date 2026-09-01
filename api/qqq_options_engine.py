"""QQQ options analytics engine — mirrors options_engine.py's structure but
sources raw data from Nasdaq's own free, no-auth JSON endpoints instead of
Deribit:

  - Nasdaq's option-chain API (unofficial/internal, not documented, but
    verified reachable via plain server-side HTTP with a browser User-Agent
    and no rate-limiting observed): Strike/Bid/Ask/Volume/Open Interest for
    calls+puts across all expiries in a date range.
  - Nasdaq's own quote-info API (same host) for spot (last sale price) —
    used instead of a third-party source so the underlying price and the
    option chain it's priced against always come from the same feed.

Neither source publishes IV/greeks the way Deribit's book-summary does, so
this module derives them itself: Newton-Raphson inversion of Black-Scholes
against each contract's bid/ask mid-price to get implied vol, then the same
bs_gamma/bs_delta used for BTC to get gamma/delta/GEX/DEX. See
api/greeks_engine.py for the shared math and aggregation helpers this module
reuses (unchanged from the BTC engine).

Being unofficial endpoints, both are wrapped in the same defensive
try/except -> 502 pattern the BTC engine uses for Deribit (see api/app.py) —
they can change shape or start blocking without notice.
"""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from api.http_cache import cached_get as _cached_get_raw
from api.greeks_engine import (
    aggregate_by_strike as _aggregate_by_strike,
    bs_delta,
    bs_gamma,
    bs_price,
    bs_vega,
    expiry_block as _expiry_block,
    fmt_expiry as _fmt_expiry,
    gamma_regime as _gamma_regime,
    key_levels as _key_levels,
    positioning_block as _positioning_block,
    profile_series as _profile_series,
    top_n_abs_gex as _top_n_abs_gex,
    top_n_by as _top_n_by,
    week_bounds as _week_bounds,
)

logger = logging.getLogger(__name__)

NASDAQ_CHAIN_URL = "https://api.nasdaq.com/api/quote/QQQ/option-chain"
NASDAQ_QUOTE_URL = "https://api.nasdaq.com/api/quote/QQQ/info"
# A generic browser UA — both endpoints are unofficial/internal APIs that
# were only verified reachable in testing when sent one; no cookies/session
# needed beyond that.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

CONTRACT_SIZE = 100.0  # standard US equity/ETF option multiplier (vs. Deribit's 1.0 BTC)
# Approximate short-term risk-free rate (~3-month T-bill yield). Unlike
# Deribit's r=0 forward-pricing simplification, QQQ's Black-Scholes IV solve
# is sensitive to r — this is a hand-set approximation, not a live feed;
# update it periodically or wire up a free source (e.g. Yahoo's ^IRX quote)
# as a follow-up.
RISK_FREE_RATE = 0.04

CHAIN_FETCH_MONTHS_AHEAD = 9  # matches what was verified to return the full multi-expiry chain

_CACHE: Dict[str, Tuple[float, Any]] = {}

# Nasdaq's per-row drillDownURL encodes the expiry date + strike reliably,
# e.g. ".../qqq---260925c00450000" = 2026-09-25, strike 450.000 (the c/p
# letter always reflects the call leg's own page, even though the row it's
# attached to carries both call and put data at that strike/expiry).
_DRILL_RE = re.compile(r"qqq---(\d{2})(\d{2})(\d{2})[cp](\d{8})")


def _num(v: Any) -> Optional[float]:
    if v in (None, "--", ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Raw data fetch
# ---------------------------------------------------------------------------

def _fetch_spot() -> float:
    def fetch() -> float:
        resp = requests.get(
            NASDAQ_QUOTE_URL, params={"assetclass": "etf"},
            headers=REQUEST_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data")
        if not data:
            raise RuntimeError(f"Nasdaq quote API returned no data: {payload.get('message')}")
        raw_price = data.get("primaryData", {}).get("lastSalePrice")
        price = _num(str(raw_price).replace("$", "").replace(",", "")) if raw_price else None
        if price is None:
            raise RuntimeError(f"Nasdaq quote API returned no lastSalePrice: {data}")
        return price

    return _cached_get_raw(_CACHE, "spot", ttl=15, fetch_fn=fetch)


def _fetch_chain_rows(from_date: str, to_date: str) -> List[dict]:
    def fetch() -> List[dict]:
        resp = requests.get(
            NASDAQ_CHAIN_URL,
            params={
                "assetclass": "etf", "limit": 10000, "offset": 0, "type": "all",
                "excode": "oprac", "money": "all", "fromdate": from_date, "todate": to_date,
            },
            headers=REQUEST_HEADERS, timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data")
        if not data:
            raise RuntimeError(f"Nasdaq option-chain API returned no data: {payload.get('message')}")
        return data["table"]["rows"]

    key = f"chain:{from_date}:{to_date}"
    return _cached_get_raw(_CACHE, key, ttl=45, fetch_fn=fetch)


def _parse_expiry_strike(row: dict) -> Optional[Tuple[int, float]]:
    url = row.get("drillDownURL")
    if not url:
        return None
    m = _DRILL_RE.search(url)
    if not m:
        return None
    yy, mm, dd, strike_raw = m.groups()
    # US equity options expire at market close (4pm ET); approximate with
    # 20:00 UTC year-round (EDT) — good enough for the day-granularity T
    # (time-to-expiry) that feeds GEX here.
    expiry_dt = datetime(2000 + int(yy), int(mm), int(dd), 20, 0, tzinfo=timezone.utc)
    return int(expiry_dt.timestamp() * 1000), int(strike_raw) / 1000.0


# ---------------------------------------------------------------------------
# Implied vol solve (Nasdaq publishes bid/ask, not IV)
# ---------------------------------------------------------------------------

def _implied_vol(price: float, spot: float, strike: float, t_years: float, is_call: bool, r: float) -> Optional[float]:
    """Newton-Raphson inversion of Black-Scholes against a quoted mid price,
    falling back to bisection if Newton doesn't converge (can happen near
    expiry or deep in/out of the money, where vega is tiny)."""
    if t_years <= 0 or price <= 0:
        return None

    sigma = 0.3
    for _ in range(50):
        model_price = bs_price(spot, strike, t_years, sigma, is_call, r)
        diff = model_price - price
        if abs(diff) < 1e-4:
            return sigma
        vega = bs_vega(spot, strike, t_years, sigma, r)
        if vega < 1e-8:
            break
        sigma -= diff / vega
        if sigma <= 0.001 or sigma > 5.0:
            break

    lo, hi = 0.001, 5.0
    price_lo, price_hi = bs_price(spot, strike, t_years, lo, is_call, r), bs_price(spot, strike, t_years, hi, is_call, r)
    if not (price_lo <= price <= price_hi):
        return None  # quoted price outside the no-arbitrage band this model can represent
    for _ in range(100):
        mid = (lo + hi) / 2.0
        price_mid = bs_price(spot, strike, t_years, mid, is_call, r)
        if abs(price_mid - price) < 1e-4:
            return mid
        if price_mid < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# Chain assembly
# ---------------------------------------------------------------------------

def _build_chain(spot: float) -> List[dict]:
    """Fetch the Nasdaq chain, solve IV per contract from bid/ask, and
    compute per-strike greeks/GEX/DEX — the QQQ equivalent of
    options_engine._build_chain."""
    today = date.today()
    from_date = today.isoformat()
    to_date = (today + timedelta(days=30 * CHAIN_FETCH_MONTHS_AHEAD)).isoformat()
    raw_rows = _fetch_chain_rows(from_date, to_date)
    now = time.time()

    rows = []
    for raw in raw_rows:
        parsed = _parse_expiry_strike(raw)
        if parsed is None:
            continue
        expiry_ms, strike = parsed
        t_years = max((expiry_ms / 1000.0 - now), 0.0) / (365.0 * 86400.0)
        if t_years <= 0:
            continue

        for is_call, bid_key, ask_key, oi_key, vol_key in (
            (True, "c_Bid", "c_Ask", "c_Openinterest", "c_Volume"),
            (False, "p_Bid", "p_Ask", "p_Openinterest", "p_Volume"),
        ):
            oi = _num(raw.get(oi_key)) or 0.0
            if oi <= 0:
                continue
            bid, ask = _num(raw.get(bid_key)), _num(raw.get(ask_key))
            if bid is None or ask is None or ask <= 0 or bid > ask:
                continue
            mid_price = (bid + ask) / 2.0

            sigma = _implied_vol(mid_price, spot, strike, t_years, is_call, RISK_FREE_RATE)
            if sigma is None or sigma <= 0:
                continue

            volume = _num(raw.get(vol_key)) or 0.0
            gamma = bs_gamma(spot, strike, t_years, sigma, r=RISK_FREE_RATE)
            delta = bs_delta(spot, strike, t_years, sigma, is_call, r=RISK_FREE_RATE)

            dollar_gamma = gamma * oi * CONTRACT_SIZE * spot * spot * 0.01
            gex = dollar_gamma if is_call else -dollar_gamma
            dex = delta * oi * CONTRACT_SIZE * spot

            rows.append(
                {
                    "instrument": f"QQQ-{strike:g}-{'C' if is_call else 'P'}-{expiry_ms}",
                    "strike": strike,
                    "expiry_ms": expiry_ms,
                    "t_years": t_years,
                    "is_call": is_call,
                    "oi": oi,
                    "volume": volume,
                    "iv": sigma,
                    "contract_size": CONTRACT_SIZE,
                    "gamma": gamma,
                    "delta": delta,
                    "gex": gex,
                    "dex": dex,
                }
            )
    return rows


def get_raw_chain_snapshot() -> Tuple[float, List[dict]]:
    """Raw per-instrument options data for the ingest pipeline, mirroring
    options_engine.get_raw_chain_snapshot() — no derived greeks/GEX, just
    OI/volume/bid/ask/solved-IV as sourced. Solving IV here too (rather than
    only in _build_chain) means both this and the dashboard path use the
    same numbers; solves are cheap relative to the fetch itself."""
    spot = _fetch_spot()
    today = date.today()
    raw_rows = _fetch_chain_rows(today.isoformat(), (today + timedelta(days=30 * CHAIN_FETCH_MONTHS_AHEAD)).isoformat())
    now = time.time()

    rows = []
    for raw in raw_rows:
        parsed = _parse_expiry_strike(raw)
        if parsed is None:
            continue
        expiry_ms, strike = parsed
        t_years = max((expiry_ms / 1000.0 - now), 0.0) / (365.0 * 86400.0)
        for is_call, bid_key, ask_key, oi_key, vol_key in (
            (True, "c_Bid", "c_Ask", "c_Openinterest", "c_Volume"),
            (False, "p_Bid", "p_Ask", "p_Openinterest", "p_Volume"),
        ):
            oi = _num(raw.get(oi_key)) or 0.0
            if oi <= 0:
                continue
            bid, ask = _num(raw.get(bid_key)), _num(raw.get(ask_key))
            mid_price = (bid + ask) / 2.0 if (bid is not None and ask is not None and ask > 0 and bid <= ask) else None
            sigma = (
                _implied_vol(mid_price, spot, strike, t_years, is_call, RISK_FREE_RATE)
                if (mid_price is not None and t_years > 0) else None
            )
            rows.append(
                {
                    "expiry": datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc).date(),
                    "strike": strike,
                    "option_type": "call" if is_call else "put",
                    "open_interest": oi,
                    "volume": _num(raw.get(vol_key)) or 0.0,
                    "mark_iv": sigma * 100.0 if sigma else None,
                    "mark_price": mid_price,
                }
            )
    return spot, rows


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_qqq_options_dashboard() -> dict:
    """QQQ equivalent of options_engine.get_options_dashboard() — same
    output shape (so the frontend can reuse its chart-rendering code
    unchanged), built from Nasdaq alone instead of Deribit.

    Two things Deribit gives for free that Nasdaq doesn't have a free
    equivalent for, so they're left None here rather than faked:
      - `historical_volatility_pct` / `iv_rank_pct` — Deribit's DVOL history
        endpoint has no free Nasdaq/OPRA counterpart in this pipeline.
      - a daily settlement time — QQQ options settle at market close (4pm
        ET / ~20:00 UTC), used below for the 0DTE countdown, but there's no
        Deribit-style "next_settlement" concept distinct from expiry itself.
    """
    now = datetime.now(timezone.utc)
    spot = _fetch_spot()
    chain_ts = time.time()
    rows = _build_chain(spot)

    if not rows:
        raise RuntimeError("Nasdaq returned no active QQQ option contracts with open interest and a valid bid/ask.")

    strike_rows_all = _aggregate_by_strike(rows)
    total_gex = sum(v["net_gex"] for v in strike_rows_all.values())
    total_abs_gex = sum(abs(v["net_gex"]) for v in strike_rows_all.values())
    total_dex = sum(row["dex"] for row in rows)
    total_oi = sum(row["oi"] for row in rows)
    call_oi = sum(row["oi"] for row in rows if row["is_call"])
    put_oi = sum(row["oi"] for row in rows if not row["is_call"])
    call_gex = sum(v["call_gex"] for v in strike_rows_all.values())
    put_gex = sum(v["put_gex"] for v in strike_rows_all.values())
    call_dex = sum(row["dex"] for row in rows if row["is_call"])
    put_dex = sum(row["dex"] for row in rows if not row["is_call"])
    total_abs_dex = sum(abs(row["dex"]) for row in rows)
    total_call_volume = sum(row["volume"] for row in rows if row["is_call"])
    total_put_volume = sum(row["volume"] for row in rows if not row["is_call"])
    top10_gex = _top_n_abs_gex(strike_rows_all, 10)

    levels = _key_levels(strike_rows_all, spot, rows, r=RISK_FREE_RATE)
    gamma_regime = _gamma_regime(total_gex, total_abs_gex)

    expiry_set = sorted({row["expiry_ms"] for row in rows})
    rows_by_expiry: Dict[int, List[dict]] = defaultdict(list)
    for row in rows:
        rows_by_expiry[row["expiry_ms"]].append(row)

    first_expiry = expiry_set[0] if len(expiry_set) > 0 else None
    next_expiry = expiry_set[1] if len(expiry_set) > 1 else None

    zero_dte_expiry = next(
        (e for e in expiry_set if datetime.fromtimestamp(e / 1000, tz=timezone.utc).date() == now.date()),
        first_expiry,
    )
    zero_dte_rows = rows_by_expiry.get(zero_dte_expiry, []) if zero_dte_expiry is not None else []
    zero_dte_strike_rows = _aggregate_by_strike(zero_dte_rows)
    zero_dte_levels = _key_levels(zero_dte_strike_rows, spot, zero_dte_rows, r=RISK_FREE_RATE)
    expiring_gex = sum(v["net_gex"] for v in zero_dte_strike_rows.values())

    # QQQ/OPRA options settle at market close, ~20:00 UTC (4pm ET, EDT
    # approximation — see _parse_expiry_strike). Once it passes, 0DTE rolls
    # to the next listed expiry via the fallback above.
    settlement_today = now.replace(hour=20, minute=0, second=0, microsecond=0)
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

    current_week_rows = [row for row in rows if in_week(row["expiry_ms"], cur_week_start, cur_week_end)]
    next_week_rows = [row for row in rows if in_week(row["expiry_ms"], next_week_start, next_week_end)]

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

    # No free Nasdaq/OPRA equivalent of Deribit's DVOL history in this
    # pipeline (see docstring) — approximate "current IV" as the OI-weighted
    # average solved IV of the nearest-expiry chain instead of leaving it
    # blank; iv_rank/hv genuinely have no data source here, so stay None.
    iv_rank, hv = None, None
    near_rows = rows_by_expiry.get(first_expiry, []) if first_expiry else rows
    iv = (
        sum(row["iv"] * row["oi"] for row in near_rows) / sum(row["oi"] for row in near_rows) * 100.0
        if near_rows else None
    )
    atm_sigma = (iv / 100.0) if iv else (sum(row["iv"] for row in rows) / len(rows) if rows else 0.2)
    expected_move_1d_pct = atm_sigma * (1 / 365.0) ** 0.5 * 100.0
    expected_move_1d_usd = spot * expected_move_1d_pct / 100.0
    day_max = spot + expected_move_1d_usd
    day_min = spot - expected_move_1d_usd
    distance_to_hvl_pct = (abs(spot - levels["hvl"]) / spot * 100.0) if levels["hvl"] else None

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
