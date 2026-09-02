"""
FastAPI app serving the BTC options dashboard + its LLM interpretation.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from starlette.middleware.gzip import GZipMiddleware

from api.options_engine import get_options_dashboard
from api.qqq_options_engine import get_qqq_options_dashboard

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Crypto Options Dashboard API", version="1.0.0")
# The dashboard HTML + its JSON payloads (GEX profile history, OHLCV) are
# mostly repetitive text/JSON, so gzip cuts transfer size a lot — smaller
# responses mean a visibly faster first paint, especially over a slow/mobile
# connection or right after a Render free-tier cold start.
app.add_middleware(GZipMiddleware, minimum_size=500)

_liquidation_listener = None
_market_data_listener = None
_ingest_task: asyncio.Task | None = None


def _btc_enabled() -> bool:
    """BTC_ENABLE env var gate — "true"/"1"/"yes"/"on" (case-insensitive)
    turns BTC data collection AND every BTC-only endpoint on; anything else
    (including unset) keeps BTC fully dark: no Binance/Deribit listeners or
    polling (see data.ingest.BTC_ENABLED), and every BTC endpoint below
    404s instead of touching Binance/Deribit. QQQ is unaffected either way."""
    return os.environ.get("BTC_ENABLE", "false").strip().lower() in ("1", "true", "yes", "on")


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

    init_db()
    if _btc_enabled():
        # Deferred until here: pulls in the Binance SDK (aiohttp, websockets,
        # pycryptodome, binance-common), real memory weight on a 512MB
        # instance, so it never loads at all while BTC is off.
        from data.sources.binance import LiquidationListener, MarketDataListener

        _liquidation_listener = LiquidationListener(on_event=on_liquidation)
        await _liquidation_listener.start()
        _market_data_listener = MarketDataListener(on_candle_closed=on_candle_closed)
        await _market_data_listener.start()
        logger.info("data ingest started (poll loop + BTC liquidation listener + BTC market data listener)")
    else:
        logger.info("BTC_ENABLE is off — data ingest started with QQQ pollers only, no BTC listeners")
    _ingest_task = asyncio.create_task(poll_loop())


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


@app.get("/health", include_in_schema=False)
def health() -> dict:
    """Tiny keep-alive target — no DB/file I/O, so it stays fast and small
    even as the dashboard page grows. Point external uptime/cron pings here,
    not at "/" (which returns the full dashboard HTML)."""
    return {"status": "ok"}


# How stale feature_gex_snapshot's latest row can get before /api/ingest/
# freshness calls a stream dead. Ticks land every OPTIONS_CHAIN_INTERVAL_
# SECONDS (60s, see data/ingest.py) regardless of market hours, so 5 minutes
# is several missed ticks' worth of slack for a slow upstream poll or a
# transient retry — enough to not false-alarm on a blip, tight enough to
# catch a genuinely dead poll loop within a few minutes of it happening.
_INGEST_STALE_AFTER_SECONDS = 5 * 60


def _stream_freshness(symbol: str, exchange: str) -> dict:
    from data.db import fetch_latest_ingest_ts

    last_ts = fetch_latest_ingest_ts(symbol, exchange)
    if last_ts is None:
        return {"symbol": symbol, "exchange": exchange, "last_ts": None, "age_seconds": None, "stale": True}
    age_seconds = (datetime.now(timezone.utc) - last_ts).total_seconds()
    return {
        "symbol": symbol,
        "exchange": exchange,
        "last_ts": last_ts.isoformat(),
        "age_seconds": round(age_seconds),
        "stale": age_seconds > _INGEST_STALE_AFTER_SECONDS,
    }


@app.get("/api/ingest/freshness", include_in_schema=False)
def ingest_freshness(response: Response) -> dict:
    """Canary for the in-process poll loop actually still ticking.

    Unlike /health (deliberately DB-free, for lightweight uptime pings),
    this hits Postgres to check feature_gex_snapshot's latest row per
    stream. That table is written on every tick unconditionally — unlike
    raw_ohlcv, which only grows during market hours when Yahoo/Nasdaq
    actually hand back a new candle — so its age is the one signal that
    can't be explained away by "market's closed".

    This matters most on a serverless deploy (Vercel): the ingest loop is
    started from a FastAPI startup event and rides on the serverless
    instance happening to stay warm between requests — there's no
    guarantee of that the way there is on an always-on process, and when
    the instance is recycled the loop dies with no error surfaced
    anywhere else. Point a separate external monitor here (not /health) to
    get alerted when that happens instead of noticing from a gap in the
    charts."""
    if not os.environ.get("DATABASE_URL"):
        return {"ingest_enabled": False}

    from data.sources.nasdaq import EXCHANGE as QQQ_EXCHANGE, SYMBOL as QQQ_SYMBOL

    streams = {"qqq": _stream_freshness(QQQ_SYMBOL, QQQ_EXCHANGE)}
    if _btc_enabled():
        from data.sources.deribit import EXCHANGE as BTC_EXCHANGE, SYMBOL as BTC_SYMBOL

        streams["btc"] = _stream_freshness(BTC_SYMBOL, BTC_EXCHANGE)

    ok = all(not s["stale"] for s in streams.values())
    if not ok:
        response.status_code = 503
    return {"ingest_enabled": True, "ok": ok, "streams": streams}


@app.get("/api/config", include_in_schema=False)
def config() -> dict:
    """Lets the frontend know which symbols it's allowed to fetch/show
    before it makes any BTC calls — see SYMBOL_CONFIG / btcEnabled in
    index.html. QQQ is always enabled."""
    return {"btc_enabled": _btc_enabled()}


def _require_btc_enabled() -> None:
    if not _btc_enabled():
        raise HTTPException(status_code=404, detail="BTC is disabled (set BTC_ENABLE=true to enable).")


@app.get("/api/options/dashboard", include_in_schema=False)
def options_dashboard() -> dict:
    _require_btc_enabled()
    try:
        return get_options_dashboard()
    except Exception as exc:
        logger.exception("Failed to build BTC options dashboard.")
        raise HTTPException(status_code=502, detail=f"Upstream options data unavailable: {exc}")


@app.get("/api/options/qqq-dashboard", include_in_schema=False)
def qqq_options_dashboard() -> dict:
    try:
        return get_qqq_options_dashboard()
    except Exception as exc:
        logger.exception("Failed to build QQQ options dashboard.")
        raise HTTPException(status_code=502, detail=f"Upstream options data unavailable: {exc}")


@app.get("/api/price/history", include_in_schema=False)
def price_history(hours: int = 24, symbol: str | None = None, exchange: str | None = None) -> dict:
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=502, detail="Price history unavailable: DATABASE_URL not set.")

    from data.db import fetch_ohlcv
    from data.sources.binance import EXCHANGE as DEFAULT_EXCHANGE, SYMBOL as DEFAULT_SYMBOL

    symbol = symbol or DEFAULT_SYMBOL
    exchange = exchange or DEFAULT_EXCHANGE
    if symbol == DEFAULT_SYMBOL and exchange == DEFAULT_EXCHANGE:
        _require_btc_enabled()

    try:
        candles = fetch_ohlcv(symbol, exchange, hours=min(max(hours, 1), 168))
        return {"symbol": symbol, "exchange": exchange, "candles": candles}
    except Exception as exc:
        logger.exception("Failed to fetch OHLCV price history.")
        raise HTTPException(status_code=502, detail=f"Price history unavailable: {exc}")


@app.get("/api/options/gex-profile-history", include_in_schema=False)
def gex_profile_history(hours: int = 24, symbol: str | None = None, exchange: str | None = None) -> dict:
    """Backs the GEX Interval Map's one-time backfill on page load — fills
    in whatever the server ingested while no browser tab was open, since the
    map's ongoing live updates are still accumulated client-side (see
    accumulateGexSnapshot in index.html) to avoid re-fetching this every
    poll tick."""
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=502, detail="GEX profile history unavailable: DATABASE_URL not set.")

    from data.db import fetch_gex_profile_history
    from data.sources.deribit import EXCHANGE as DEFAULT_EXCHANGE, SYMBOL as DEFAULT_SYMBOL

    symbol = symbol or DEFAULT_SYMBOL
    exchange = exchange or DEFAULT_EXCHANGE
    if symbol == DEFAULT_SYMBOL and exchange == DEFAULT_EXCHANGE:
        _require_btc_enabled()

    try:
        snapshots = fetch_gex_profile_history(symbol, exchange, hours=min(max(hours, 1), 24))
        return {"symbol": symbol, "exchange": exchange, "snapshots": snapshots}
    except Exception as exc:
        logger.exception("Failed to fetch GEX profile history.")
        raise HTTPException(status_code=502, detail=f"GEX profile history unavailable: {exc}")


@app.get("/api/options/interpretation", include_in_schema=False)
def options_interpretation() -> dict:
    # BTC-only (agents.option_agent.analyze_option_data hardcodes
    # get_options_dashboard, no QQQ equivalent) — gate like the other
    # BTC endpoints rather than let it hit Deribit while BTC is disabled.
    _require_btc_enabled()

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
