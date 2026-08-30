"""Ingest loop: writes to raw_ohlcv, raw_futures_snapshot, raw_liquidations,
feature_gex_snapshot (derived GEX/key levels, top-10 by |GEX|), and
feature_gex_profile_snapshot (full-chain GEX profile, JSONB, 24h retention)
in TimescaleDB.

Two cadences, run together:
  - OHLCV + futures snapshot poll every OHLCV_FUTURES_INTERVAL_SECONDS
  - options snapshot poll every OPTIONS_CHAIN_INTERVAL_SECONDS — one Deribit
    fetch (deribit.fetch_snapshot_rows) feeds both feature_gex_snapshot and
    feature_gex_profile_snapshot per tick
  - liquidation websocket listener, subscribed once and kept open for the
    process lifetime (event-driven — there's no REST equivalent to poll)

Run: python -m data.ingest
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from psycopg2.extras import Json

load_dotenv()

from data.db import init_db, insert_rows, prune_gex_profile_history  # noqa: E402
from data.sources import binance, deribit  # noqa: E402
from data.sources.binance import BinanceBannedError, BinanceStreamNotReadyError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

OHLCV_FUTURES_INTERVAL_SECONDS = 60
OPTIONS_CHAIN_INTERVAL_SECONDS = 60
GEX_PROFILE_RETENTION_HOURS = 24
GEX_PROFILE_PRUNE_INTERVAL_SECONDS = 3600  # hourly is plenty for a 24h window

OHLCV_COLUMNS = ["ts", "symbol", "exchange", "open", "high", "low", "close", "volume"]
FUTURES_COLUMNS = ["ts", "symbol", "exchange", "open_interest", "funding_rate", "mark_price", "index_price"]
OPTIONS_COLUMNS = [
    "ts", "symbol", "exchange", "spot_price", "call_resistance", "put_support",
    "hvl", "day_max", "day_min", "iv", "hv", "iv_rank",
    *[f"gex_strike_{i}" for i in range(1, 11)],
    *[f"gex_net_{i}" for i in range(1, 11)],
]
GEX_PROFILE_COLUMNS = ["ts", "symbol", "exchange", "spot_price", "profile"]
LIQUIDATION_COLUMNS = ["ts", "symbol", "exchange", "side", "price", "size"]


def _row_values(row: dict, columns: list[str]) -> tuple:
    return tuple(row[c] for c in columns)


def on_candle_closed(candle: dict) -> None:
    """Inserted straight from the kline_1m WebSocket callback (see
    MarketDataListener) instead of the poll loop, so a closed candle lands
    in the DB within milliseconds rather than waiting up to a minute for
    the next futures-snapshot poll tick."""
    try:
        candle = dict(candle, symbol=binance.SYMBOL, exchange=binance.EXCHANGE)
        insert_rows("raw_ohlcv", OHLCV_COLUMNS, [_row_values(candle, OHLCV_COLUMNS)])
    except Exception:
        logger.exception("failed to insert OHLCV candle")


def poll_futures_snapshot() -> None:
    try:
        futures = binance.fetch_futures_snapshot()
        insert_rows("raw_futures_snapshot", FUTURES_COLUMNS, [_row_values(futures, FUTURES_COLUMNS)])
    except (BinanceBannedError, BinanceStreamNotReadyError) as exc:
        logger.info("futures snapshot poll skipped: %s", exc)
    except Exception:
        logger.exception("futures snapshot poll failed")


def poll_options_snapshot() -> None:
    """One Deribit REST call (deribit.fetch_snapshot_rows) feeds both
    feature_gex_snapshot (top-10 by |GEX|, kept indefinitely) and
    feature_gex_profile_snapshot (full chain, JSONB, 24h retention — backs
    the GEX Interval Map) — combined so this costs one Deribit poll per
    tick instead of two."""
    try:
        feature_rows, profile_row = deribit.fetch_snapshot_rows()
        n1 = insert_rows(
            "feature_gex_snapshot", OPTIONS_COLUMNS,
            [_row_values(r, OPTIONS_COLUMNS) for r in feature_rows],
        )
        profile_row = dict(profile_row, profile=Json(profile_row["profile"]))
        n2 = insert_rows(
            "feature_gex_profile_snapshot", GEX_PROFILE_COLUMNS,
            [_row_values(profile_row, GEX_PROFILE_COLUMNS)],
        )
        logger.info("ingested %d GEX feature row(s), %d GEX profile row(s)", n1, n2)
    except Exception:
        logger.exception("options snapshot poll failed")


def prune_options_gex_profile() -> None:
    """Runs on its own hourly timer, separate from the 60s snapshot poll —
    drop_chunks() is cheap but there's no need to call it every tick for a
    24h retention window. See db.prune_gex_profile_history."""
    try:
        prune_gex_profile_history(GEX_PROFILE_RETENTION_HOURS)
        logger.info("pruned feature_gex_profile_snapshot chunks older than %dh", GEX_PROFILE_RETENTION_HOURS)
    except Exception:
        logger.exception("gex profile prune failed")


def on_liquidation(row: dict) -> None:
    try:
        insert_rows(
            "raw_liquidations", LIQUIDATION_COLUMNS,
            [_row_values(row, LIQUIDATION_COLUMNS)], on_conflict=False,
        )
    except Exception:
        logger.exception("failed to insert liquidation row")


async def _run_on_interval(fn, interval_seconds: float) -> None:
    while True:
        start = asyncio.get_event_loop().time()
        await asyncio.to_thread(fn)
        elapsed = asyncio.get_event_loop().time() - start
        await asyncio.sleep(max(0.0, interval_seconds - elapsed))


async def poll_loop() -> None:
    """Runs both polling cadences concurrently for the life of the process."""
    await asyncio.gather(
        _run_on_interval(poll_futures_snapshot, OHLCV_FUTURES_INTERVAL_SECONDS),
        _run_on_interval(poll_options_snapshot, OPTIONS_CHAIN_INTERVAL_SECONDS),
        _run_on_interval(prune_options_gex_profile, GEX_PROFILE_PRUNE_INTERVAL_SECONDS),
    )


async def main() -> None:
    logger.info("applying schema")
    init_db()

    logger.info("starting liquidation listener")
    listener = binance.LiquidationListener(on_event=on_liquidation)
    await listener.start()

    logger.info("starting market data listener (markPrice + kline_1m streams)")
    market_data = binance.MarketDataListener(on_candle_closed=on_candle_closed)
    await market_data.start()

    try:
        logger.info(
            "starting poll loop (futures snapshot every %ds, options chain every %ds)",
            OHLCV_FUTURES_INTERVAL_SECONDS, OPTIONS_CHAIN_INTERVAL_SECONDS,
        )
        await poll_loop()
    finally:
        await listener.stop()
        await market_data.stop()


if __name__ == "__main__":
    asyncio.run(main())
