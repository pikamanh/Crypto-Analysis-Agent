import json
import os
from typing import List, Optional

from dotenv import load_dotenv
from api.options_engine import get_options_dashboard

from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

with open("agents/system prompt/option.md", "r") as f:
    system_prompt = f.read()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("BASE_URL")
)

# Only re-run the LLM when the data has moved enough to change the scenarios
# it would generate. Small noise between refreshes reuses the last analysis.
CACHE_PATH = os.path.join(os.path.dirname(__file__), ".option_cache.json")

PRICE_LEVEL_PCT_THRESHOLD = 0.003   # spot, CR, PS, HVL, strong GEX strikes: 0.3%
VOL_ABS_THRESHOLD = 1.0             # iv, hv: 1 vol point
IV_RANK_ABS_THRESHOLD = 5.0         # iv rank: 5 points
GEX_NET_PCT_THRESHOLD = 0.15        # strong GEX net gamma magnitude: 15%
GEX_EXPIRING_ABS_THRESHOLD = 5.0    # gex expiring %: 5 points

class KeyLevel(BaseModel):
    spotPrice: float
    cr: float
    ps: float
    hvl: float
    dayMax: float
    dayMin: float
    iv: float
    hv: float
    ivRank: float

class GEXItem(BaseModel):
    rank: int
    strike: float
    netGex: float

class GEXLevel(BaseModel):
    gexLevels: List[GEXItem]
    strongLevels: List[GEXItem]
    mediumLevels: List[GEXItem]
    weakLevels: List[GEXItem]

class Expiration(BaseModel):
    gexVolume: float
    gexExpirating: float
    cr: float
    ps: float
    hvl: float

class ExpirationStructure(BaseModel):
    firstExpiration: Expiration
    nextExpiration: Expiration
    currentWeekExpiration: Expiration

class OptionData(BaseModel):
    keyLevels: KeyLevel
    GEXLevels: GEXLevel
    expirationStructure: ExpirationStructure

def _relevant_snapshot(option_data: "OptionData") -> dict:
    """Fields that actually drive the scenarios in the system prompt."""
    kl = option_data.keyLevels
    strong = option_data.GEXLevels.strongLevels
    exp = option_data.expirationStructure.firstExpiration

    return {
        "spotPrice": kl.spotPrice,
        "cr": kl.cr,
        "ps": kl.ps,
        "hvl": kl.hvl,
        "iv": kl.iv,
        "hv": kl.hv,
        "ivRank": kl.ivRank,
        "strongLevels": [{"strike": g.strike, "netGex": g.netGex} for g in strong],
        "firstExpirationGexExpiring": exp.gexExpirating,
    }


def _pct_move(old: float, new: float) -> float:
    if old == 0:
        return 0.0 if new == 0 else float("inf")
    return abs(new - old) / abs(old)


def _is_significant_change(old: dict, new: dict) -> bool:
    for field in ("spotPrice", "cr", "ps", "hvl"):
        if _pct_move(old[field], new[field]) >= PRICE_LEVEL_PCT_THRESHOLD:
            return True

    if abs(new["iv"] - old["iv"]) >= VOL_ABS_THRESHOLD:
        return True
    if abs(new["hv"] - old["hv"]) >= VOL_ABS_THRESHOLD:
        return True
    if abs(new["ivRank"] - old["ivRank"]) >= IV_RANK_ABS_THRESHOLD:
        return True

    if abs(new["firstExpirationGexExpiring"] - old["firstExpirationGexExpiring"]) >= GEX_EXPIRING_ABS_THRESHOLD:
        return True

    old_strong = old["strongLevels"]
    new_strong = new["strongLevels"]
    if len(old_strong) != len(new_strong):
        return True
    for old_g, new_g in zip(old_strong, new_strong):
        if _pct_move(old_g["strike"], new_g["strike"]) >= PRICE_LEVEL_PCT_THRESHOLD:
            return True
        if _pct_move(old_g["netGex"], new_g["netGex"]) >= GEX_NET_PCT_THRESHOLD:
            return True

    return False


def _load_cache() -> Optional[dict]:
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(snapshot: dict, analysis: str) -> None:
    with open(CACHE_PATH, "w") as f:
        json.dump({"snapshot": snapshot, "analysis": analysis}, f)


def analyze_option_data():
    options = get_options_dashboard()

    #Key levels
    spot_price = options["key_levels"]["spot_price"]
    cr = options["key_levels"]["call_resistance"]
    ps = options["key_levels"]["put_support"]
    hvl = options["key_levels"]["high_vol_level"]
    day_max = options["key_levels"]["day_max"]
    day_min = options["key_levels"]["day_min"]
    iv = options["key_levels"]["implied_volatility_30d_pct"]
    hv = options["key_levels"]["historical_volatility_30d_pct"]
    iv_rank = options["key_levels"]["iv_rank_pct"]

    #GEX Levels
    gex_levels = [
        GEXItem(
            rank=gex["rank"],
            strike=gex["strike"],
            netGex=gex["net_gex"]
        )
        for gex in options["gex_levels"]
    ]

    strong_levels = gex_levels[:2]
    medium_levels = gex_levels[2:5]
    weak_levels = gex_levels[5:]

    #Expiration
    structure_0dte = options["expiration_structure"]["first_expiration"]
    structure_1dte = options["expiration_structure"]["next_expiration"]
    structure_curr_week = options["expiration_structure"]["current_week"]

    #Schema BaseModel
    key_level_schema = KeyLevel(
        spotPrice=spot_price,
        cr=cr,
        ps=ps,
        hvl=hvl,
        dayMax=day_max,
        dayMin=day_min,
        iv=iv,
        hv=hv,
        ivRank=iv_rank
    )

    gex_level_schema = GEXLevel(
        gexLevels=gex_levels,
        strongLevels=strong_levels,
        mediumLevels=medium_levels,
        weakLevels=weak_levels
    )

    first_expiration_schema = Expiration(
        gexVolume=structure_0dte["gex"],
        gexExpirating=structure_0dte["gex_expiring_pct"],
        cr=structure_0dte["call_resistance"],
        ps=structure_0dte["put_support"],
        hvl=structure_0dte["hvl"]
    )

    next_expiration_schema = Expiration(
        gexVolume=structure_1dte["gex"],
        gexExpirating=structure_1dte["gex_expiring_pct"],
        cr=structure_1dte["call_resistance"],
        ps=structure_1dte["put_support"],
        hvl=structure_1dte["hvl"]
    )

    curr_week_expiration_schema = Expiration(
        gexVolume=structure_curr_week["gex"],
        gexExpirating=structure_curr_week["gex_expiring_pct"],
        cr=structure_curr_week["call_resistance"],
        ps=structure_curr_week["put_support"],
        hvl=structure_curr_week["hvl"]
    )

    expiration_structure_schema = ExpirationStructure(
        firstExpiration=first_expiration_schema,
        nextExpiration=next_expiration_schema,
        currentWeekExpiration=curr_week_expiration_schema
    )

    #Final data
    option_data = OptionData(
        keyLevels=key_level_schema,
        GEXLevels=gex_level_schema,
        expirationStructure=expiration_structure_schema
    )

    snapshot = _relevant_snapshot(option_data)
    cached = _load_cache()
    if cached and not _is_significant_change(cached["snapshot"], snapshot):
        return cached["analysis"]

    response = client.chat.completions.create(
        model=os.getenv("MODEL"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": option_data.model_dump_json()},
        ]
    )

    analysis = response.choices[0].message.content
    _save_cache(snapshot, analysis)
    return analysis

if __name__ == "__main__":
    print(analyze_option_data())