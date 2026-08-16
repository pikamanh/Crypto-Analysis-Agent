"""Raw data fetchers for Binance USDS-M Futures (BTCUSDT), via the official
`binance-sdk-derivatives-trading-usds-futures` SDK — REST client is sync,
WebSocket Streams client is async (asyncio-native).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Callable, Optional

from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    ConfigurationRestAPI,
    ConfigurationWebSocketStreams,
    DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
    DerivativesTradingUsdsFutures,
)
from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    KlineCandlestickDataIntervalEnum,
)

logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
EXCHANGE = "binance"

_rest_client: Optional[DerivativesTradingUsdsFutures] = None
_ws_client: Optional[DerivativesTradingUsdsFutures] = None


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
    """Most recent *closed* 1m kline (limit=2, take the second-to-last —
    the last entry is the still-forming current candle)."""
    response = _get_rest_client().rest_api.kline_candlestick_data(
        symbol=symbol,
        interval=KlineCandlestickDataIntervalEnum["INTERVAL_1m"].value,
        limit=2,
    )
    klines = response.data()
    k = klines[-2] if len(klines) >= 2 else klines[-1]
    return {
        "ts": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
        "symbol": symbol,
        "exchange": EXCHANGE,
        "open": float(k[1]),
        "high": float(k[2]),
        "low": float(k[3]),
        "close": float(k[4]),
        "volume": float(k[5]),
    }


def fetch_futures_snapshot(symbol: str = SYMBOL) -> dict:
    """Open interest + funding/mark/index, merged into one snapshot row."""
    client = _get_rest_client().rest_api
    oi = client.open_interest(symbol=symbol).data()
    mark = client.mark_price(symbol=symbol).data().actual_instance

    return {
        "ts": datetime.now(tz=timezone.utc),
        "symbol": symbol,
        "exchange": EXCHANGE,
        "open_interest": float(oi.open_interest),
        "funding_rate": float(mark.last_funding_rate),
        "mark_price": float(mark.mark_price),
        "index_price": float(mark.index_price),
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
