"""
FastAPI app serving the BTC options dashboard + its LLM interpretation.
"""
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from api.options_engine import get_options_dashboard

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Crypto Options Dashboard API", version="1.0.0")

_liquidation_listener = None
_market_data_listener = None
_ingest_task: asyncio.Task | None = None


@app.on_event("startup")
async def start_ingest() -> None:
    """Runs the raw-data ingest loop inside this same web process so it
    doesn't need a separate (paid) Render Background Worker — it rides on
    this service's own uptime instead. Only starts if DATABASE_URL is set,
    so local/dashboard-only runs aren't forced to have a database.

    Backups run on a schedule that doesn't depend on this process's uptime —
    see .github/workflows/weekly-db-backup.yml, not an in-process task,
    since a free-tier Render web service sleeps when idle."""
    global _liquidation_listener, _market_data_listener, _ingest_task
    if not os.environ.get("DATABASE_URL"):
        logger.info("DATABASE_URL not set — skipping data ingest, dashboard only.")
        return

    from data.db import init_db
    from data.ingest import poll_loop, on_liquidation, on_candle_closed
    from data.sources.binance import LiquidationListener, MarketDataListener

    init_db()
    _liquidation_listener = LiquidationListener(on_event=on_liquidation)
    await _liquidation_listener.start()
    _market_data_listener = MarketDataListener(on_candle_closed=on_candle_closed)
    await _market_data_listener.start()
    _ingest_task = asyncio.create_task(poll_loop())
    logger.info("data ingest started (poll loop + liquidation listener + market data listener)")


@app.on_event("shutdown")
async def stop_ingest() -> None:
    if _ingest_task:
        _ingest_task.cancel()
    if _liquidation_listener:
        await _liquidation_listener.stop()
    if _market_data_listener:
        await _market_data_listener.stop()


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/options/dashboard", include_in_schema=False)
def options_dashboard() -> dict:
    try:
        return get_options_dashboard()
    except Exception as exc:
        logger.exception("Failed to build BTC options dashboard.")
        raise HTTPException(status_code=502, detail=f"Upstream options data unavailable: {exc}")


@app.get("/api/price/history", include_in_schema=False)
def price_history(hours: int = 24) -> dict:
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=502, detail="Price history unavailable: DATABASE_URL not set.")

    from data.db import fetch_ohlcv
    from data.sources.binance import EXCHANGE, SYMBOL

    try:
        candles = fetch_ohlcv(SYMBOL, EXCHANGE, hours=min(max(hours, 1), 168))
        return {"symbol": SYMBOL, "exchange": EXCHANGE, "candles": candles}
    except Exception as exc:
        logger.exception("Failed to fetch OHLCV price history.")
        raise HTTPException(status_code=502, detail=f"Price history unavailable: {exc}")


@app.get("/api/options/gex-profile-history", include_in_schema=False)
def gex_profile_history(hours: int = 24) -> dict:
    """Backs the GEX Interval Map's one-time backfill on page load — fills
    in whatever the server ingested while no browser tab was open, since the
    map's ongoing live updates are still accumulated client-side (see
    accumulateGexSnapshot in index.html) to avoid re-fetching this every
    poll tick."""
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=502, detail="GEX profile history unavailable: DATABASE_URL not set.")

    from data.db import fetch_gex_profile_history
    from data.sources.deribit import EXCHANGE, SYMBOL

    try:
        snapshots = fetch_gex_profile_history(SYMBOL, EXCHANGE, hours=min(max(hours, 1), 24))
        return {"symbol": SYMBOL, "exchange": EXCHANGE, "snapshots": snapshots}
    except Exception as exc:
        logger.exception("Failed to fetch GEX profile history.")
        raise HTTPException(status_code=502, detail=f"GEX profile history unavailable: {exc}")


@app.get("/api/options/interpretation", include_in_schema=False)
def options_interpretation() -> dict:
    # Lazy import: builds an OpenAI client at import time, which would crash
    # startup if OPENAI_API_KEY isn't set.
    from agents.option_agent import analyze_option_data

    try:
        return {"analysis": analyze_option_data()}
    except Exception as exc:
        logger.exception("Failed to generate market structure interpretation.")
        raise HTTPException(status_code=502, detail=f"Interpretation unavailable: {exc}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.app:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
    )
