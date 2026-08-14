import os
from typing import List

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

    response = client.chat.completions.create(
        model=os.getenv("MODEL"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": option_data.model_dump_json()},
        ]
    )

    return response.choices[0].message.content

if __name__ == "__main__":
    print(analyze_option_data())