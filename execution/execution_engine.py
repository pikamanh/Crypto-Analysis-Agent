import logging
import math
import os
from typing import Literal, Optional

from pydantic import BaseModel

from binance_common.configuration import ConfigurationRestAPI
from binance_common.constants import (
    DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL,
    DERIVATIVES_TRADING_USDS_FUTURES_REST_API_TESTNET_URL,
)
from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    DerivativesTradingUsdsFutures,
)
from binance_sdk_derivatives_trading_usds_futures.rest_api.models.enums import (
    NewAlgoOrderAlgoTypeEnum,
    NewAlgoOrderClosePositionEnum,
    NewAlgoOrderSideEnum,
    NewAlgoOrderTypeEnum,
    NewOrderReduceOnlyEnum,
    NewOrderSideEnum,
    NewOrderTypeEnum,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

LIVE_TRADING = os.getenv("BINANCE_LIVE_TRADING", "false").lower() == "true"


class OrderResult(BaseModel):
    entry_order_id: Optional[str] = None
    quantity: float
    entry_price: float
    stop_loss_order_id: Optional[str] = None
    take_profit_order_id: Optional[str] = None


class ExecutionEngine:
    def __init__(self):
        self.live = LIVE_TRADING
        if self.live:
            api_key = os.getenv("BINANCE_FUTURES_LIVE_API_KEY")
            api_secret = os.getenv("BINANCE_FUTURES_LIVE_SECRET_KEY")
            base_path = DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL
        else:
            api_key = os.getenv("BINANCE_FUTURES_TESTNET_API_KEY")
            api_secret = os.getenv("BINANCE_FUTURES_TESTNET_SECRET_KEY")
            base_path = DERIVATIVES_TRADING_USDS_FUTURES_REST_API_TESTNET_URL

        configuration = ConfigurationRestAPI(api_key=api_key, api_secret=api_secret, base_path=base_path)
        self.client = DerivativesTradingUsdsFutures(config_rest_api=configuration)
        self._symbol_info_cache = {}
        logger.info(f"Execution engine connected ({'LIVE' if self.live else 'TESTNET'}).")

    def _pair(self, symbol: str) -> str:
        return f"{symbol.upper()}USDT"

    def _symbol_info(self, symbol: str) -> dict:
        pair = self._pair(symbol)
        if pair not in self._symbol_info_cache:
            response = self.client.rest_api.exchange_information()
            data = response.data()
            for s in data.symbols:
                self._symbol_info_cache[s.symbol] = {
                    "quantity_precision": s.quantity_precision,
                    "price_precision": s.price_precision,
                }
        return self._symbol_info_cache.get(pair, {"quantity_precision": 3, "price_precision": 2})

    def _round_qty(self, symbol: str, qty: float) -> float:
        precision = self._symbol_info(symbol)["quantity_precision"]
        factor = 10 ** precision
        return math.floor(qty * factor) / factor

    def _round_price(self, symbol: str, price: float) -> float:
        precision = self._symbol_info(symbol)["price_precision"]
        return round(price, precision)

    def get_account_equity(self) -> Optional[float]:
        response = self.client.rest_api.futures_account_balance_v3()
        for balance in response.data():
            if balance.asset == "USDT":
                return float(balance.available_balance)
        return None

    def get_position_amt(self, symbol: str) -> float:
        pair = self._pair(symbol)
        response = self.client.rest_api.position_information_v3(symbol=pair)
        positions = response.data()
        if not isinstance(positions, list):
            positions = [positions]
        return sum(float(p.position_amt) for p in positions if p.symbol == pair)

    def get_mark_price(self, symbol: str) -> Optional[float]:
        pair = self._pair(symbol)
        response = self.client.rest_api.mark_price(symbol=pair)
        instance = response.data().actual_instance
        return float(instance.mark_price)

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self.client.rest_api.change_initial_leverage(symbol=self._pair(symbol), leverage=leverage)

    def open_position(
        self,
        symbol: str,
        direction: Literal["long", "short"],
        notional_usdt: float,
        stop_loss: float,
        take_profit: float,
    ) -> Optional[OrderResult]:
        pair = self._pair(symbol)
        price = self.get_mark_price(symbol)
        if price is None or price <= 0:
            logger.error(f"No mark price available for {pair}, aborting entry.")
            return None

        quantity = self._round_qty(symbol, notional_usdt / price)
        if quantity <= 0:
            logger.error(f"Rounded quantity is zero for {pair} (notional={notional_usdt}), aborting entry.")
            return None

        entry_side = NewOrderSideEnum.BUY if direction == "long" else NewOrderSideEnum.SELL
        exit_side = NewAlgoOrderSideEnum.SELL if direction == "long" else NewAlgoOrderSideEnum.BUY

        entry = self.client.rest_api.new_order(
            symbol=pair,
            side=entry_side,
            type=NewOrderTypeEnum.MARKET,
            quantity=quantity,
        ).data()
        logger.info(f"Opened {direction} {quantity} {pair} @ market (order_id={entry.order_id}).")

        stop_loss_order_id = None
        take_profit_order_id = None
        try:
            sl = self.client.rest_api.new_algo_order(
                algo_type=NewAlgoOrderAlgoTypeEnum.CONDITIONAL,
                symbol=pair,
                side=exit_side,
                type=NewAlgoOrderTypeEnum.STOP_MARKET,
                trigger_price=self._round_price(symbol, stop_loss),
                close_position=NewAlgoOrderClosePositionEnum.TRUE,
            ).data()
            stop_loss_order_id = str(sl.algo_id)
        except Exception:
            logger.exception(f"Failed to place stop-loss algo order for {pair}.")

        try:
            tp = self.client.rest_api.new_algo_order(
                algo_type=NewAlgoOrderAlgoTypeEnum.CONDITIONAL,
                symbol=pair,
                side=exit_side,
                type=NewAlgoOrderTypeEnum.TAKE_PROFIT_MARKET,
                trigger_price=self._round_price(symbol, take_profit),
                close_position=NewAlgoOrderClosePositionEnum.TRUE,
            ).data()
            take_profit_order_id = str(tp.algo_id)
        except Exception:
            logger.exception(f"Failed to place take-profit algo order for {pair}.")

        return OrderResult(
            entry_order_id=str(entry.order_id),
            quantity=quantity,
            entry_price=float(entry.price) if entry.price else price,
            stop_loss_order_id=stop_loss_order_id,
            take_profit_order_id=take_profit_order_id,
        )

    def close_position(self, symbol: str, direction: Literal["long", "short"], quantity: float) -> Optional[float]:
        pair = self._pair(symbol)
        close_side = NewOrderSideEnum.SELL if direction == "long" else NewOrderSideEnum.BUY

        try:
            self.client.rest_api.cancel_all_algo_open_orders(symbol=pair)
        except Exception:
            logger.exception(f"Failed to cancel algo (SL/TP) orders for {pair} before close.")

        order = self.client.rest_api.new_order(
            symbol=pair,
            side=close_side,
            type=NewOrderTypeEnum.MARKET,
            quantity=self._round_qty(symbol, quantity),
            reduce_only=NewOrderReduceOnlyEnum.TRUE,
        ).data()
        logger.info(f"Closed {direction} {quantity} {pair} @ market (order_id={order.order_id}).")
        return float(order.price) if order.price else self.get_mark_price(symbol)


_engine: Optional[ExecutionEngine] = None


def get_engine() -> ExecutionEngine:
    global _engine
    if _engine is None:
        _engine = ExecutionEngine()
    return _engine


if __name__ == "__main__":
    engine = get_engine()
    print("equity:", engine.get_account_equity())
    print("BTC mark price:", engine.get_mark_price("BTC"))
