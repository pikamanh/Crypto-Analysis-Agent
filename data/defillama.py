import logging

from defillama_sdk import DefiLlama
from pydantic import BaseModel
from typing import List, Optional

from data.sheets import GoogleSheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
_data_instance = None


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
        self.client = DefiLlama()
        self._protocols = None
        self._gecko_id_by_protocol = None
        try:
            self._load_protocols()
            logger.info("Connected DeFiLlama successfully.")
        except Exception as e:
            logger.error("Connected DeFiLlama failed.")
            logger.exception(e)

        self.gg = GoogleSheets()
        self._coin_id_df = None

    def _load_protocols(self) -> List[dict]:
        if self._protocols is None:
            self._protocols = self.client.tvl.getProtocols()
            self._gecko_id_by_protocol = {
                p["gecko_id"]: p for p in self._protocols if p.get("gecko_id")
            }
        return self._protocols

    def _resolve_gecko_id(self, name: Optional[str], symbol: Optional[str]) -> Optional[str]:
        if self._coin_id_df is None:
            self._coin_id_df = self.gg.get_coin_id()

        df = self._coin_id_df
        if name:
            match = df.loc[df["Name"].str.lower() == name.lower()]["ID"]
        elif symbol:
            match = df.loc[df["Symbol"].str.lower() == symbol.lower()]["ID"]
        else:
            raise ValueError("Name or symbol is None. Please provided.")

        return match.values[0] if not match.empty else None

    def _resolve_protocol(self, name: Optional[str], symbol: Optional[str]) -> Optional[dict]:
        self._load_protocols()

        gecko_id = self._resolve_gecko_id(name, symbol)
        if gecko_id is None:
            return None

        return self._gecko_id_by_protocol.get(gecko_id)

    def get_protocol_tvl(self, name: Optional[str] = None, symbol: Optional[str] = None) -> Optional[ProtocolTVL]:
        summary = self._resolve_protocol(name, symbol)
        if summary is None:
            logger.error(f"Protocol not found: name={name}, symbol={symbol}")
            return None

        slug = summary["slug"]
        detail = self.client.tvl.getProtocol(slug)

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
            tvl_change_1d=summary.get("change_1d"),
            tvl_change_7d=summary.get("change_7d"),
            raises=raises,
            total_raised_musd=total_raised_musd,
            hacks=hacks,
        )

        logger.info(f"Get protocol TVL successfully: {protocol_tvl.slug}")
        return protocol_tvl


def get_protocol_tvl_data(name: Optional[str] = None, symbol: Optional[str] = None):
    global _data_instance
    if _data_instance is None:
        _data_instance = DefiLlamaData()
    return _data_instance.get_protocol_tvl(name=name, symbol=symbol)


if __name__ == "__main__":
    defillama_data = DefiLlamaData()
    print(defillama_data.get_protocol_tvl(name="Aave"))
