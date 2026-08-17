"""Raw data fetchers for Binance USDS-M Futures (BTCUSDT), via the official
`binance-sdk-derivatives-trading-usds-futures` SDK — REST client is sync,
WebSocket Streams client is async (asyncio-native).
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from binance_common.errors import RateLimitBanError

from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    ConfigurationRestAPI,
    ConfigurationWebSocketStreams,
    DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
    DerivativesTradingUsdsFutures,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_streams.models import (
    KlineCandlestickStreamsIntervalEnum,
)

logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
EXCHANGE = "binance"

_rest_client: Optional[DerivativesTradingUsdsFutures] = None
_ws_client: Optional[DerivativesTradingUsdsFutures] = None

# Shared IP-ban state — set whenever Binance returns -1003 (RateLimitBanError).
# Once banned, further REST calls are skipped entirely (not even attempted)
# until this timestamp passes, since retrying while banned can extend it.
_banned_until: Optional[datetime] = None
_BAN_TS_RE = re.compile(r"banned until (\d+)")


class BinanceBannedError(Exception):
    """Raised in place of an actual API call while an IP ban is still active."""


class BinanceStreamNotReadyError(Exception):
    """Raised when a WebSocket-fed cache hasn't received its first message yet."""


# Latest values pushed by MarketDataListener's markPrice/kline_1m streams —
# read by fetch_futures_snapshot/fetch_last_closed_1m_candle instead of REST,
# since REST polling of these was what triggered Binance's IP ban (-1003).
_latest_mark_price: Optional[dict] = None
_latest_closed_candle: Optional[dict] = None
_latest_open_interest: Optional[float] = None


def _is_banned() -> bool:
    return _banned_until is not None and datetime.now(tz=timezone.utc) < _banned_until


def _record_ban(exc: RateLimitBanError) -> None:
    global _banned_until
    match = _BAN_TS_RE.search(exc.error_message or "")
    _banned_until = (
        datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc)
        if match
        else datetime.now(tz=timezone.utc) + timedelta(minutes=10)
    )
    logger.error("Binance IP ban detected — pausing REST calls until %s", _banned_until)


def _guarded(fn: Callable, *args, **kwargs):
    """Runs `fn` unless we're already known to be banned, and records any new
    ban so subsequent calls skip instead of hammering the API further."""
    if _is_banned():
        raise BinanceBannedError(f"skipping call — banned until {_banned_until}")
    try:
        return fn(*args, **kwargs)
    except RateLimitBanError as exc:
        _record_ban(exc)
        raise


def _get_rest_client() -> DerivativesTradingUsdsFutures:
    global _rest_client
    if _rest_client is None:
        config = ConfigurationRestAPI(
            api_key=os.getenv("BINANCE_API_KEY", ""),
            api_secret=os.getenv("BINANCE_SECRET_KEY", ""),
            base_path=DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL,
        )
        _rest_client = DerivativesTradingUsdsFutures(config_rest_api=config)
    return _rest_client


def _get_ws_client() -> DerivativesTradingUsdsFutures:
    global _ws_client
    if _ws_client is None:
        config = ConfigurationWebSocketStreams(
            stream_url=DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
            reconnect_delay=5000,
            reconnect_attempts=10,
        )
        _ws_client = DerivativesTradingUsdsFutures(config_ws_streams=config)
    return _ws_client


def fetch_last_closed_1m_candle(symbol: str = SYMBOL) -> dict:
    """Most recent *closed* 1m kline, from the cached kline_1m WebSocket
    stream (see MarketDataListener) — no REST call involved."""
    if _latest_closed_candle is None:
        raise BinanceStreamNotReadyError("kline stream not ready yet")
    candle = dict(_latest_closed_candle)
    candle["symbol"] = symbol
    candle["exchange"] = EXCHANGE
    return candle


def fetch_futures_snapshot(symbol: str = SYMBOL) -> dict:
    """Open interest (REST — no public push stream exists for it) merged
    with funding/mark/index price from the cached markPrice WebSocket
    stream (see MarketDataListener).

    Open interest changes slowly, so if the REST call is banned/fails we
    fall back to the last known value instead of dropping the whole row —
    mark/funding/index still come fresh off the WS stream either way."""
    global _latest_open_interest
    if _latest_mark_price is None:
        raise BinanceStreamNotReadyError("mark price stream not ready yet")

    try:
        oi = _guarded(_get_rest_client().rest_api.open_interest, symbol=symbol).data()
        _latest_open_interest = float(oi.open_interest)
    except (BinanceBannedError, RateLimitBanError):
        if _latest_open_interest is None:
            raise BinanceStreamNotReadyError("open interest not fetched yet") from None
        logger.info("open interest REST call unavailable — reusing last known value")

    return {
        "ts": datetime.now(tz=timezone.utc),
        "symbol": symbol,
        "exchange": EXCHANGE,
        "open_interest": _latest_open_interest,
        "funding_rate": _latest_mark_price["funding_rate"],
        "mark_price": _latest_mark_price["mark_price"],
        "index_price": _latest_mark_price["index_price"],
    }


class LiquidationListener:
    """Subscribes to the `<symbol>@forceOrder` stream and calls `on_event`
    for every liquidation print. Reconnection is handled internally by the
    SDK's websocket-streams client (reconnect_delay/reconnect_attempts) —
    the connection just needs to be kept open for the app's lifetime."""

    def __init__(self, on_event: Callable[[dict], None], symbol: str = SYMBOL):
        self._on_event = on_event
        self._symbol = symbol.lower()
        self._connection = None

    def _handle_message(self, data) -> None:
        try:
            o = data.o
            row = {
                "ts": datetime.fromtimestamp(data.E / 1000, tz=timezone.utc),
                "symbol": o.s,
                "exchange": EXCHANGE,
                "side": "long" if o.S == "SELL" else "short",
                "price": float(o.ap),
                "size": float(o.q),
            }
            self._on_event(row)
        except Exception:
            logger.exception("failed to parse liquidation message: %s", data)

    async def start(self) -> None:
        self._connection = await _get_ws_client().websocket_streams.create_connection()
        stream = await self._connection.liquidation_order_streams(symbol=self._symbol)
        stream.on("message", self._handle_message)

    async def stop(self) -> None:
        if self._connection:
            await self._connection.close_connection(close_session=True)


class MarketDataListener:
    """Subscribes to the markPrice and kline_1m WebSocket streams and caches
    the latest values in module state (_latest_mark_price / _latest_closed_
    candle), so fetch_futures_snapshot/fetch_last_closed_1m_candle can read
    them without REST calls — REST polling of these is what triggered
    Binance's IP ban (-1003) in the first place."""

    def __init__(self, symbol: str = SYMBOL):
        self._symbol = symbol.lower()
        self._connection = None

    def _handle_mark_price(self, data) -> None:
        global _latest_mark_price
        try:
            _latest_mark_price = {
                "mark_price": float(data.p),
                "index_price": float(data.i),
                "funding_rate": float(data.r),
            }
        except Exception:
            logger.exception("failed to parse mark price message: %s", data)

    def _handle_kline(self, data) -> None:
        global _latest_closed_candle
        try:
            k = data.k
            if not k.x:  # still-forming candle — only cache once closed
                return
            _latest_closed_candle = {
                "ts": datetime.fromtimestamp(k.t / 1000, tz=timezone.utc),
                "open": float(k.o),
                "high": float(k.h),
                "low": float(k.l),
                "close": float(k.c),
                "volume": float(k.v),
            }
        except Exception:
            logger.exception("failed to parse kline message: %s", data)

    async def start(self) -> None:
        self._connection = await _get_ws_client().websocket_streams.create_connection()

        mark_price_stream = await self._connection.mark_price_stream(symbol=self._symbol)
        mark_price_stream.on("message", self._handle_mark_price)

        kline_stream = await self._connection.kline_candlestick_streams(
            symbol=self._symbol,
            interval=KlineCandlestickStreamsIntervalEnum["INTERVAL_1m"].value,
        )
        kline_stream.on("message", self._handle_kline)

    async def stop(self) -> None:
        if self._connection:
            await self._connection.close_connection(close_session=True)
