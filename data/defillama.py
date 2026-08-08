import logging

import requests
from pydantic import BaseModel
from typing import List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
_data_instance = None

BASE_URL = "https://api.llama.fi"


class FundingRaise(BaseModel):
    date: Optional[int] = None
    round: Optional[str] = None
    amount_musd: Optional[float] = None
    lead_investors: List[str] = []
    other_investors: List[str] = []
    valuation_musd: Optional[float] = None


class Hack(BaseModel):
    date: Optional[int] = None
    name: Optional[str] = None
    technique: Optional[str] = None
    amount_usd: Optional[float] = None
    returned_funds_usd: Optional[float] = None


class ProtocolTVL(BaseModel):
    slug: str
    name: str
    symbol: Optional[str] = None
    category: Optional[str] = None
    chains: List[str] = []
    url: Optional[str] = None
    twitter: Optional[str] = None
    github: List[str] = []
    tvl_usd: Optional[float] = None
    mcap_usd: Optional[float] = None
    tvl_change_1d: Optional[float] = None
    tvl_change_7d: Optional[float] = None
    raises: List[FundingRaise] = []
    total_raised_musd: Optional[float] = None
    hacks: List[Hack] = []


class DefiLlamaData:
    def __init__(self):
        self.session = requests.Session()
        self._protocols = None
        try:
            self.session.get(f"{BASE_URL}/protocols", timeout=15).raise_for_status()
            logger.info("Connected DeFiLlama successfully.")
        except Exception as e:
            logger.error("Connected DeFiLlama failed.")
            logger.exception(e)

    def _get(self, path: str) -> dict:
        resp = self.session.get(f"{BASE_URL}{path}", timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _load_protocols(self) -> List[dict]:
        if self._protocols is None:
            self._protocols = self._get("/protocols")
        return self._protocols

    def _resolve_slug(self, name: str) -> Optional[str]:
        protocols = self._load_protocols()
        slug_guess = name.lower().strip().replace(" ", "-")

        exact_slug = next((p for p in protocols if p["slug"] == slug_guess), None)
        if exact_slug:
            return exact_slug["slug"]

        exact_name = next((p for p in protocols if p["name"].lower() == name.lower()), None)
        if exact_name:
            return exact_name["slug"]

        candidates = [p for p in protocols if name.lower() in p["name"].lower()]
        if candidates:
            candidates.sort(key=lambda p: p.get("tvl") or 0, reverse=True)
            return candidates[0]["slug"]

        return None

    def get_protocol_tvl(self, name: str) -> Optional[ProtocolTVL]:
        slug = self._resolve_slug(name)
        if slug is None:
            logger.error(f"Protocol not found: name={name}")
            return None

        detail = self._get(f"/protocol/{slug}")

        tvl_history = detail.get("tvl") or []
        current_tvl = tvl_history[-1]["totalLiquidityUSD"] if tvl_history else None

        raises = [
            FundingRaise(
                date=r.get("date"),
                round=r.get("round"),
                amount_musd=r.get("amount"),
                lead_investors=r.get("leadInvestors") or [],
                other_investors=r.get("otherInvestors") or [],
                valuation_musd=r.get("valuation"),
            )
            for r in (detail.get("raises") or [])
        ]
        total_raised_musd = sum(r.amount_musd for r in raises if r.amount_musd) or None

        hacks = [
            Hack(
                date=h.get("date"),
                name=h.get("name"),
                technique=h.get("technique"),
                amount_usd=h.get("amount"),
                returned_funds_usd=h.get("returnedFunds"),
            )
            for h in (detail.get("hacks") or [])
        ]

        protocol_tvl = ProtocolTVL(
            slug=slug,
            name=detail.get("name"),
            symbol=detail.get("symbol"),
            category=detail.get("category"),
            chains=detail.get("chains") or [],
            url=detail.get("url"),
            twitter=detail.get("twitter"),
            github=detail.get("github") or [],
            tvl_usd=current_tvl,
            mcap_usd=detail.get("mcap"),
            raises=raises,
            total_raised_musd=total_raised_musd,
            hacks=hacks,
        )

        logger.info(f"Get protocol TVL successfully: {protocol_tvl.slug}")
        return protocol_tvl


def get_protocol_tvl_data(name: str):
    global _data_instance
    if _data_instance is None:
        _data_instance = DefiLlamaData()
    return _data_instance.get_protocol_tvl(name)


if __name__ == "__main__":
    defillama_data = DefiLlamaData()
    print(defillama_data.get_protocol_tvl("Aave"))
