import argparse
import logging
import os
import time
from typing import List

from dotenv import load_dotenv

from agents.signal_agent import generate_signal
from alert import notifier
from execution.execution_engine import get_engine
from risk import risk_manager
from storage import state

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

KILL_SWITCH_PATH = os.getenv(
    "KILL_SWITCH_PATH", os.path.join(os.path.dirname(__file__), "KILL_SWITCH")
)
DEFAULT_INTERVAL_SECONDS = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "300"))


def is_kill_switch_active() -> bool:
    return os.path.exists(KILL_SWITCH_PATH)


def engage_kill_switch(reason: str = "manual") -> None:
    os.makedirs(os.path.dirname(KILL_SWITCH_PATH) or ".", exist_ok=True)
    with open(KILL_SWITCH_PATH, "w") as f:
        f.write(reason)
    notifier.notify_kill_switch(True, reason)


def release_kill_switch() -> None:
    if os.path.exists(KILL_SWITCH_PATH):
        os.remove(KILL_SWITCH_PATH)
    notifier.notify_kill_switch(False, "manual release")


def reconcile_positions(engine) -> None:
    """
    SL/TP orders execute directly on the exchange, so a locally 'open' position
    can already be flat there. Detect that and close it locally using the current
    mark price as an approximation of the actual fill price (exact fill price
    would require pulling trade history, out of scope for this loop).
    """
    for position in state.get_open_positions():
        try:
            amt = engine.get_position_amt(position.symbol)
        except Exception:
            logger.exception(f"Failed to fetch exchange position for {position.symbol}, skipping reconcile.")
            continue

        if abs(amt) > 1e-9:
            continue

        exit_price = engine.get_mark_price(position.symbol) or position.entry_price
        direction_sign = 1 if position.side == "long" else -1
        realized_pnl = direction_sign * (exit_price - position.entry_price) * position.quantity
        state.close_position(position.id, exit_price, realized_pnl)
        notifier.notify_execution(
            position.symbol,
            "position_closed_on_exchange",
            {"position_id": position.id, "exit_price": exit_price, "realized_pnl": realized_pnl},
        )


def run_cycle(symbols: List[str]) -> None:
    engine = get_engine()
    reconcile_positions(engine)

    equity = engine.get_account_equity()
    if equity is None:
        logger.error("Could not fetch account equity, skipping cycle.")
        return

    for symbol in symbols:
        try:
            signal = generate_signal(symbol=symbol)
        except Exception:
            logger.exception(f"Signal generation failed for {symbol}.")
            continue
        notifier.notify_signal(symbol, signal)

        decision = risk_manager.evaluate(signal, symbol, account_equity=equity)
        notifier.notify_risk_decision(decision)

        if decision.action != "approve":
            continue

        notional_usdt = equity * (decision.notional_pct / 100)
        result = engine.open_position(
            symbol=symbol,
            direction=decision.direction,
            notional_usdt=notional_usdt,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
        )
        if result is None:
            notifier.notify_execution(symbol, "position_open_failed", {"notional_usdt": notional_usdt})
            continue

        position_id = state.open_position(
            symbol=symbol,
            side=decision.direction,
            quantity=result.quantity,
            entry_price=result.entry_price,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            entry_order_id=result.entry_order_id,
        )
        state.record_order(
            symbol=symbol, side=decision.direction, order_type="MARKET", purpose="entry",
            position_id=position_id, quantity=result.quantity, price=result.entry_price,
            status="FILLED", exchange_order_id=result.entry_order_id,
        )
        if result.stop_loss_order_id:
            state.record_order(
                symbol=symbol, side=decision.direction, order_type="STOP_MARKET", purpose="stop_loss",
                position_id=position_id, price=decision.stop_loss, status="NEW",
                exchange_order_id=result.stop_loss_order_id,
            )
        if result.take_profit_order_id:
            state.record_order(
                symbol=symbol, side=decision.direction, order_type="TAKE_PROFIT_MARKET", purpose="take_profit",
                position_id=position_id, price=decision.take_profit, status="NEW",
                exchange_order_id=result.take_profit_order_id,
            )
        notifier.notify_execution(
            symbol, "position_opened",
            {"position_id": position_id, "quantity": result.quantity, "entry_price": result.entry_price},
        )


def main(symbols: List[str], interval_seconds: int = DEFAULT_INTERVAL_SECONDS, once: bool = False) -> None:
    logger.info(f"Starting scheduler loop: symbols={symbols}, interval={interval_seconds}s.")
    while True:
        if is_kill_switch_active():
            logger.warning(f"Kill switch active ({KILL_SWITCH_PATH}) — no new orders this cycle.")
        else:
            try:
                run_cycle(symbols)
            except Exception:
                logger.exception("Unhandled error during trading cycle.")

        if once:
            return
        time.sleep(interval_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Signal -> risk -> execution scheduler loop.")
    parser.add_argument("--symbols", default="BTC", help="Comma-separated symbols, e.g. BTC,ETH")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="Seconds between cycles")
    parser.add_argument("--once", action="store_true", help="Run a single cycle then exit")
    parser.add_argument("--kill", nargs="?", const="manual", metavar="REASON", help="Engage the kill switch and exit")
    parser.add_argument("--release", action="store_true", help="Release the kill switch and exit")
    args = parser.parse_args()

    if args.kill is not None:
        engage_kill_switch(args.kill)
        print(f"Kill switch engaged: {args.kill}")
    elif args.release:
        release_kill_switch()
        print("Kill switch released.")
    else:
        main(symbols=[s.strip().upper() for s in args.symbols.split(",")], interval_seconds=args.interval, once=args.once)
