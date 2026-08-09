import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Literal, Optional

import requests
from pydantic import BaseModel
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
_data_instance = None

DERIBIT_REST_API_URL = "https://www.deribit.com/api/v2"


class StrikeExposure(BaseModel):
    strike: float
    call_oi: float
    put_oi: float
    call_gex: float
    put_gex: float
    net_gex: float
    net_dex: float


class OptionFlowSnapshot(BaseModel):
    source: str = "deribit"
    currency: str
    spot_price: float
    strikes: List[StrikeExposure]
    total_gex: float
    total_dex: float
    put_call_oi_ratio: Optional[float] = None
    max_pain: Optional[float] = None
    zero_gamma_level: Optional[float] = None
    # Computed in code (not left for the LLM to infer) so a small model can't
    # misread the sign of total_gex: positive total_gex means dealers are net
    # long gamma and their hedging dampens volatility; negative means it
    # amplifies moves.
    gex_regime: Literal["dampening", "amplifying", "neutral"] = "neutral"
    spot_vs_zero_gamma: Optional[Literal["above", "below"]] = None


class DeribitData:
    """
    Public Deribit market data client (no API key required). Computes GEX
    (gamma exposure) and DEX (delta exposure) per strike from the option
    chain's open interest + greeks.

    Sign convention (standard GEX/DEX approximation used across public
    trackers, since dealers' actual positioning is not public data): market
    makers are assumed net long calls / net short puts. So call open
    interest contributes positive gamma exposure and put open interest
    contributes negative gamma exposure; delta exposure is summed using the
    signed delta Deribit already returns (positive for calls, negative for
    puts).
    """

    def __init__(self, max_workers: int = 8):
        self.max_workers = max_workers
        self.session = requests.Session()
        retry = Retry(
            total=5,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(
            pool_connections=max_workers, pool_maxsize=max_workers, max_retries=retry
        )
        self.session.mount("https://", adapter)
        logger.info("Connected Deribit successfully.")

    def _get(self, endpoint: str, params: dict) -> Optional[dict]:
        try:
            response = self.session.get(
                f"{DERIBIT_REST_API_URL}/{endpoint}", params=params, timeout=10
            )
            response.raise_for_status()
            payload = response.json()
            if "error" in payload:
                logger.error(f"Deribit API error on {endpoint}: {payload['error']}")
                return None
            return payload.get("result")
        except requests.RequestException as e:
            logger.error(f"Deribit request failed on {endpoint}: {e}")
            return None

    def _get_index_price(self, currency: str) -> Optional[float]:
        result = self._get(
            "public/get_index_price", {"index_name": f"{currency.lower()}_usd"}
        )
        return result.get("index_price") if result else None

    def _get_active_options(self, currency: str) -> List[dict]:
        result = self._get(
            "public/get_instruments",
            {"currency": currency.upper(), "kind": "option", "expired": "false"},
        )
        return result or []

    def _get_ticker(self, instrument_name: str) -> Optional[dict]:
        return self._get("public/ticker", {"instrument_name": instrument_name})

    def get_option_flow_snapshot(
        self,
        currency: str = "BTC",
        max_days_to_expiry: int = 45,
        strike_range_pct: float = 0.3,
    ) -> Optional[OptionFlowSnapshot]:
        """
        Build a GEX/DEX profile for the given currency's option chain.

        `max_days_to_expiry` bounds the instruments to near-term expiries
        (dealer hedging pressure concentrates there). `strike_range_pct`
        bounds strikes to +/- this fraction around spot. Both exist to keep
        the number of per-instrument ticker calls (and Deribit rate-limit
        exposure) small enough to run on a 5-minute schedule.
        """
        spot_price = self._get_index_price(currency)
        if spot_price is None:
            logger.error(f"Could not fetch spot price for {currency}")
            return None

        instruments = self._get_active_options(currency)
        if not instruments:
            logger.error(f"No active option instruments for {currency}")
            return None

        now_ms = time.time() * 1000
        max_expiry_ms = now_ms + max_days_to_expiry * 24 * 60 * 60 * 1000
        low_strike = spot_price * (1 - strike_range_pct)
        high_strike = spot_price * (1 + strike_range_pct)

        filtered = [
            inst
            for inst in instruments
            if inst.get("expiration_timestamp", 0) <= max_expiry_ms
            and low_strike <= inst.get("strike", 0) <= high_strike
        ]
        if not filtered:
            logger.error(
                f"No instruments in range for {currency} "
                f"(spot={spot_price}, strike_range_pct={strike_range_pct}, "
                f"max_days_to_expiry={max_days_to_expiry})"
            )
            return None

        by_strike: dict[float, dict] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_inst = {
                executor.submit(self._get_ticker, inst["instrument_name"]): inst
                for inst in filtered
            }
            for future in as_completed(future_to_inst):
                inst = future_to_inst[future]
                ticker = future.result()
                if ticker is None:
                    continue

                strike = inst["strike"]
                is_call = inst["option_type"] == "call"
                open_interest = ticker.get("open_interest") or 0.0
                greeks = ticker.get("greeks") or {}
                delta = greeks.get("delta") or 0.0
                gamma = greeks.get("gamma") or 0.0

                bucket = by_strike.setdefault(
                    strike,
                    {"call_oi": 0.0, "put_oi": 0.0, "call_gamma_oi": 0.0,
                     "put_gamma_oi": 0.0, "delta_oi": 0.0},
                )
                bucket["delta_oi"] += delta * open_interest
                if is_call:
                    bucket["call_oi"] += open_interest
                    bucket["call_gamma_oi"] += gamma * open_interest
                else:
                    bucket["put_oi"] += open_interest
                    bucket["put_gamma_oi"] += gamma * open_interest

        if not by_strike:
            logger.error(f"No ticker data collected for {currency}")
            return None

        gamma_multiplier = spot_price ** 2 * 0.01
        strikes: List[StrikeExposure] = []
        for strike in sorted(by_strike):
            bucket = by_strike[strike]
            call_gex = bucket["call_gamma_oi"] * gamma_multiplier
            put_gex = bucket["put_gamma_oi"] * gamma_multiplier
            net_gex = call_gex - put_gex
            net_dex = bucket["delta_oi"] * spot_price
            strikes.append(
                StrikeExposure(
                    strike=strike,
                    call_oi=bucket["call_oi"],
                    put_oi=bucket["put_oi"],
                    call_gex=call_gex,
                    put_gex=put_gex,
                    net_gex=net_gex,
                    net_dex=net_dex,
                )
            )

        total_gex = sum(s.net_gex for s in strikes)
        total_dex = sum(s.net_dex for s in strikes)
        total_call_oi = sum(s.call_oi for s in strikes)
        total_put_oi = sum(s.put_oi for s in strikes)
        put_call_oi_ratio = (
            total_put_oi / total_call_oi if total_call_oi else None
        )
        max_pain = self._compute_max_pain(strikes)
        zero_gamma_level = self._compute_zero_gamma_level(strikes)

        gex_regime = "dampening" if total_gex > 0 else "amplifying" if total_gex < 0 else "neutral"
        spot_vs_zero_gamma = None
        if zero_gamma_level is not None:
            spot_vs_zero_gamma = "above" if spot_price >= zero_gamma_level else "below"

        snapshot = OptionFlowSnapshot(
            currency=currency.upper(),
            spot_price=spot_price,
            strikes=strikes,
            total_gex=total_gex,
            total_dex=total_dex,
            put_call_oi_ratio=put_call_oi_ratio,
            max_pain=max_pain,
            zero_gamma_level=zero_gamma_level,
            gex_regime=gex_regime,
            spot_vs_zero_gamma=spot_vs_zero_gamma,
        )

        logger.info(
            f"Get option flow snapshot successfully: {currency} "
            f"({len(strikes)} strikes)"
        )
        return snapshot

    @staticmethod
    def _compute_max_pain(strikes: List[StrikeExposure]) -> Optional[float]:
        if not strikes:
            return None

        candidates = [s.strike for s in strikes]
        best_strike, best_payout = None, None
        for candidate in candidates:
            payout = sum(
                max(0.0, candidate - s.strike) * s.call_oi
                + max(0.0, s.strike - candidate) * s.put_oi
                for s in strikes
            )
            if best_payout is None or payout < best_payout:
                best_strike, best_payout = candidate, payout

        return best_strike

    @staticmethod
    def _compute_zero_gamma_level(strikes: List[StrikeExposure]) -> Optional[float]:
        if len(strikes) < 2:
            return None

        cumulative = 0.0
        prev_strike, prev_cumulative = strikes[0].strike, None
        for s in strikes:
            cumulative += s.net_gex
            if prev_cumulative is not None and (
                (prev_cumulative < 0 <= cumulative) or (prev_cumulative >= 0 > cumulative)
            ):
                span = cumulative - prev_cumulative
                if span != 0:
                    ratio = -prev_cumulative / span
                    return prev_strike + ratio * (s.strike - prev_strike)
                return s.strike
            prev_strike, prev_cumulative = s.strike, cumulative

        return None


def get_option_flow_data(
    currency: str = "BTC",
    max_days_to_expiry: int = 45,
    strike_range_pct: float = 0.3,
):
    global _data_instance
    if _data_instance is None:
        _data_instance = DeribitData()
    return _data_instance.get_option_flow_snapshot(
        currency=currency,
        max_days_to_expiry=max_days_to_expiry,
        strike_range_pct=strike_range_pct,
    )


if __name__ == "__main__":
    deribit_data = DeribitData()
    print(deribit_data.get_option_flow_snapshot(currency="BTC"))
