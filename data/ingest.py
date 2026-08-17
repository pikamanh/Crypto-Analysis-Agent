"""Raw-data ingest loop: writes to the 4 raw_* tables in TimescaleDB.

Three cadences, run together:
  - OHLCV + futures snapshot poll every OHLCV_FUTURES_INTERVAL_SECONDS
  - options chain poll every OPTIONS_CHAIN_INTERVAL_SECONDS (much larger
    payload per snapshot — kept slower to stay within storage limits)
  - liquidation websocket listener, subscribed once and kept open for the
    process lifetime (event-driven — there's no REST equivalent to poll)

Run: python -m data.ingest
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

from data.db import init_db, insert_rows  # noqa: E402
from data.sources import binance, deribit  # noqa: E402
from data.sources.binance import BinanceBannedError, BinanceStreamNotReadyError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

OHLCV_FUTURES_INTERVAL_SECONDS = 60
OPTIONS_CHAIN_INTERVAL_SECONDS = 5 * 60

OHLCV_COLUMNS = ["ts", "symbol", "exchange", "open", "high", "low", "close", "volume"]
FUTURES_COLUMNS = ["ts", "symbol", "exchange", "open_interest", "funding_rate", "mark_price", "index_price"]
OPTIONS_COLUMNS = [
    "ts", "symbol", "exchange", "expiry", "strike", "option_type",
    "open_interest", "volume", "mark_iv", "mark_price", "underlying_price",
]
LIQUIDATION_COLUMNS = ["ts", "symbol", "exchange", "side", "price", "size"]


def _row_values(row: dict, columns: list[str]) -> tuple:
    return tuple(row[c] for c in columns)


def poll_ohlcv_futures() -> None:
    try:
        candle = binance.fetch_last_closed_1m_candle()
        insert_rows("raw_ohlcv", OHLCV_COLUMNS, [_row_values(candle, OHLCV_COLUMNS)])
    except BinanceStreamNotReadyError as exc:
        logger.info("OHLCV poll skipped: %s", exc)
    except Exception:
        logger.exception("OHLCV poll failed")

    try:
        futures = binance.fetch_futures_snapshot()
        insert_rows("raw_futures_snapshot", FUTURES_COLUMNS, [_row_values(futures, FUTURES_COLUMNS)])
    except (BinanceBannedError, BinanceStreamNotReadyError) as exc:
        logger.info("futures snapshot poll skipped: %s", exc)
    except Exception:
        logger.exception("futures snapshot poll failed")


def poll_options_chain() -> None:
    try:
        chain_rows = deribit.fetch_raw_chain_rows()
        n = insert_rows("raw_options_chain", OPTIONS_COLUMNS, [_row_values(r, OPTIONS_COLUMNS) for r in chain_rows])
        logger.info("ingested %d options chain rows", n)
    except Exception:
        logger.exception("options chain poll failed")


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
        _run_on_interval(poll_ohlcv_futures, OHLCV_FUTURES_INTERVAL_SECONDS),
        _run_on_interval(poll_options_chain, OPTIONS_CHAIN_INTERVAL_SECONDS),
    )


async def main() -> None:
    logger.info("applying schema")
    init_db()

    logger.info("starting liquidation listener")
    listener = binance.LiquidationListener(on_event=on_liquidation)
    await listener.start()

    logger.info("starting market data listener (markPrice + kline_1m streams)")
    market_data = binance.MarketDataListener()
    await market_data.start()

    try:
        logger.info(
            "starting poll loop (ohlcv/futures every %ds, options chain every %ds)",
            OHLCV_FUTURES_INTERVAL_SECONDS, OPTIONS_CHAIN_INTERVAL_SECONDS,
        )
        await poll_loop()
    finally:
        await listener.stop()
        await market_data.stop()


if __name__ == "__main__":
    asyncio.run(main())
