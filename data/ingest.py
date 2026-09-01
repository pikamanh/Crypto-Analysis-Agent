"""Ingest loop: writes to raw_ohlcv, raw_futures_snapshot, raw_liquidations,
feature_gex_snapshot (derived GEX/key levels, top-10 by |GEX|), and
feature_gex_profile_snapshot (full-chain GEX profile, JSONB, 24h retention)
in TimescaleDB.

Several cadences, run together:
  - OHLCV + futures snapshot poll every OHLCV_FUTURES_INTERVAL_SECONDS (BTC)
    — gated behind BTC_ENABLE (see below)
  - QQQ OHLCV poll on the same interval, from Yahoo Finance's chart API
    (yahoo.fetch_ohlcv_1m) — BTC's candles instead arrive via the Binance
    kline WebSocket (on_candle_closed), which Yahoo has no free equivalent of.
    Always runs, independent of BTC_ENABLE.
  - options snapshot poll every OPTIONS_CHAIN_INTERVAL_SECONDS — one Deribit
    fetch (deribit.fetch_snapshot_rows) feeds both feature_gex_snapshot and
    feature_gex_profile_snapshot per tick; gated behind BTC_ENABLE. QQQ
    mirrors this from Nasdaq (nasdaq.fetch_snapshot_rows) on the same
    interval, always running.
  - liquidation websocket listener, subscribed once and kept open for the
    process lifetime (event-driven — there's no REST equivalent to poll) —
    BTC-only (Binance perpetual liquidations), gated behind BTC_ENABLE

BTC_ENABLE: env var, "true"/"1"/"yes"/"on" (case-insensitive) to enable BTC
data collection; anything else (including unset) leaves BTC untouched — no
Deribit/Binance polling or listening happens, and no BTC rows get written.
QQQ collection is unconditional and unaffected by this flag. See BTC_ENABLED
below and api.app's matching gate on the BTC-only endpoints.

Run: python -m data.ingest
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from psycopg2.extras import Json

load_dotenv()

from data.db import init_db, insert_rows, prune_gex_profile_history  # noqa: E402
from data.sources import nasdaq, yahoo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BTC_ENABLED = os.environ.get("BTC_ENABLE", "false").strip().lower() in ("1", "true", "yes", "on")

# `binance`/`deribit` pull in the Binance SDK (aiohttp, websockets,
# pycryptodome, binance-common) — real memory weight on a 512MB Render
# instance. Only imported when BTC_ENABLED, and only inside the functions
# below that actually touch them (all of which are only ever scheduled when
# BTC_ENABLED is true — see poll_loop/main), so the whole dependency chain
# stays out of the process entirely while BTC is off.

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
    from data.sources import binance

    try:
        candle = dict(candle, symbol=binance.SYMBOL, exchange=binance.EXCHANGE)
        insert_rows("raw_ohlcv", OHLCV_COLUMNS, [_row_values(candle, OHLCV_COLUMNS)])
    except Exception:
        logger.exception("failed to insert OHLCV candle")


def poll_futures_snapshot() -> None:
    from data.sources import binance
    from data.sources.binance import BinanceBannedError, BinanceStreamNotReadyError

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
    from data.sources import deribit

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


def poll_qqq_options_snapshot() -> None:
    """QQQ equivalent of poll_options_snapshot — one Nasdaq fetch
    (nasdaq.fetch_snapshot_rows) feeds the same two feature tables, tagged
    symbol='QQQ', exchange='nasdaq' instead of BTC/deribit. Kept on the same
    60s interval as BTC for simplicity even outside US market hours, when
    Nasdaq's API just keeps returning the last quote (see plan, out of scope
    for v1 to special-case RTH). QQQ *price* history is a separate poll (see
    poll_qqq_price_snapshot) since Nasdaq's quote API has no candle series."""
    try:
        feature_rows, profile_row = nasdaq.fetch_snapshot_rows()
        n1 = insert_rows(
            "feature_gex_snapshot", OPTIONS_COLUMNS,
            [_row_values(r, OPTIONS_COLUMNS) for r in feature_rows],
        )
        profile_row = dict(profile_row, profile=Json(profile_row["profile"]))
        n2 = insert_rows(
            "feature_gex_profile_snapshot", GEX_PROFILE_COLUMNS,
            [_row_values(profile_row, GEX_PROFILE_COLUMNS)],
        )
        logger.info("ingested %d QQQ GEX feature row(s), %d QQQ GEX profile row(s)", n1, n2)
    except Exception:
        logger.exception("QQQ options snapshot poll failed")


def poll_qqq_price_snapshot() -> None:
    """QQQ equivalent of on_candle_closed — Yahoo Finance has no free
    WebSocket, so this polls its chart API (yahoo.fetch_ohlcv_1m) instead of
    listening for closed candles. Each poll re-fetches up to 5 days of 1m
    candles; ON CONFLICT DO NOTHING on insert means only genuinely new
    candles actually get written, so this stays cheap after the first
    backfilling call."""
    try:
        rows = yahoo.fetch_ohlcv_1m()
        n = insert_rows("raw_ohlcv", OHLCV_COLUMNS, [_row_values(r, OHLCV_COLUMNS) for r in rows])
        logger.info("ingested %d QQQ OHLCV row(s) from Yahoo Finance", n)
    except Exception:
        logger.exception("QQQ price snapshot poll failed")


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
    """Runs both polling cadences concurrently for the life of the process.
    The BTC pollers (futures snapshot, Deribit options) are only scheduled
    when BTC_ENABLED — QQQ's pollers always run regardless."""
    tasks = [
        _run_on_interval(poll_qqq_price_snapshot, OHLCV_FUTURES_INTERVAL_SECONDS),
        _run_on_interval(poll_qqq_options_snapshot, OPTIONS_CHAIN_INTERVAL_SECONDS),
        _run_on_interval(prune_options_gex_profile, GEX_PROFILE_PRUNE_INTERVAL_SECONDS),
    ]
    if BTC_ENABLED:
        tasks.append(_run_on_interval(poll_futures_snapshot, OHLCV_FUTURES_INTERVAL_SECONDS))
        tasks.append(_run_on_interval(poll_options_snapshot, OPTIONS_CHAIN_INTERVAL_SECONDS))
    await asyncio.gather(*tasks)


async def main() -> None:
    logger.info("applying schema")
    init_db()

    listener = None
    market_data = None
    if BTC_ENABLED:
        from data.sources import binance

        logger.info("BTC_ENABLE is on — starting liquidation + market data listeners")
        listener = binance.LiquidationListener(on_event=on_liquidation)
        await listener.start()

        market_data = binance.MarketDataListener(on_candle_closed=on_candle_closed)
        await market_data.start()
    else:
        logger.info("BTC_ENABLE is off — skipping all Binance/Deribit BTC data collection")

    try:
        logger.info(
            "starting poll loop (futures snapshot every %ds, options chain every %ds)",
            OHLCV_FUTURES_INTERVAL_SECONDS, OPTIONS_CHAIN_INTERVAL_SECONDS,
        )
        await poll_loop()
    finally:
        if listener:
            await listener.stop()
        if market_data:
            await market_data.stop()


if __name__ == "__main__":
    asyncio.run(main())
