"""
Backtest engine for the Stage-3 signal logic.

Deribit only exposes the *current* option chain (no historical GEX/DEX/OI
series on the free tier), so a like-for-like replay of the live Signal Agent
(price action + option flow + sentiment, judged by an LLM) isn't possible
over historical data. Instead this engine replays a deterministic, rule-based
proxy of the price-action half of that logic (trend/momentum alignment +
support/resistance entry/stop/target, same fields `agents/price_action_agent.py`
and `agents/signal_agent.py` use) across historical Binance candles. This is
the part of the signal that historical data can actually validate; option
flow and sentiment remain live-only inputs.
"""
import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Literal, Optional

from data.binance import get_historical_candles
from data.indicators import Candle, PriceActionSnapshot, build_price_action_snapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000,
}


@dataclass
class RuleSignal:
    direction: Literal["long", "short"]
    stop_loss: float
    take_profit: float


@dataclass
class Trade:
    direction: Literal["long", "short"]
    entry_index: int
    entry_time: int
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_index: Optional[int] = None
    exit_time: Optional[int] = None
    exit_price: Optional[float] = None
    outcome: Optional[Literal["win", "loss", "timeout"]] = None
    r_multiple: Optional[float] = None


@dataclass
class BacktestReport:
    symbol: str
    interval: str
    candle_count: int
    trades: List[Trade] = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> Optional[float]:
        if not self.trades:
            return None
        wins = sum(1 for t in self.trades if t.r_multiple and t.r_multiple > 0)
        return wins / len(self.trades)

    @property
    def avg_r_multiple(self) -> Optional[float]:
        if not self.trades:
            return None
        return sum(t.r_multiple for t in self.trades) / len(self.trades)

    @property
    def profit_factor(self) -> Optional[float]:
        gains = sum(t.r_multiple for t in self.trades if t.r_multiple > 0)
        losses = -sum(t.r_multiple for t in self.trades if t.r_multiple < 0)
        if losses == 0:
            return None
        return gains / losses

    @property
    def max_drawdown_r(self) -> float:
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in self.trades:
            equity += t.r_multiple or 0.0
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        return max_dd

    def summary(self) -> dict:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "candle_count": self.candle_count,
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "avg_r_multiple": self.avg_r_multiple,
            "total_r_multiple": sum(t.r_multiple for t in self.trades) if self.trades else 0.0,
            "profit_factor": self.profit_factor,
            "max_drawdown_r": self.max_drawdown_r,
        }


def rule_based_signal(snapshot: PriceActionSnapshot) -> Optional[RuleSignal]:
    """
    Deterministic proxy for the direction/entry/stop/target logic that
    `agents/price_action_agent.py` (trend/momentum read) and
    `agents/signal_agent.py` (support/resistance -> stop/target) ask an LLM
    to produce. Returns None when indicators aren't warmed up yet or price
    action is ambiguous/ranging (mirrors the "neutral -> no trade" behavior
    of the live signal).
    """
    if None in (snapshot.sma_20, snapshot.ema_12, snapshot.ema_26, snapshot.rsi_14, snapshot.macd_histogram):
        return None
    if snapshot.support is None or snapshot.resistance is None or snapshot.support >= snapshot.resistance:
        return None

    close = snapshot.candles[-1].close
    bullish = snapshot.ema_12 > snapshot.ema_26 and close > snapshot.sma_20 and snapshot.macd_histogram > 0 and snapshot.rsi_14 < 70
    bearish = snapshot.ema_12 < snapshot.ema_26 and close < snapshot.sma_20 and snapshot.macd_histogram < 0 and snapshot.rsi_14 > 30

    if bullish and not bearish:
        stop_loss, take_profit = snapshot.support, snapshot.resistance
        if not (stop_loss < close < take_profit):
            return None
        return RuleSignal(direction="long", stop_loss=stop_loss, take_profit=take_profit)

    if bearish and not bullish:
        stop_loss, take_profit = snapshot.resistance, snapshot.support
        if not (take_profit < close < stop_loss):
            return None
        return RuleSignal(direction="short", stop_loss=stop_loss, take_profit=take_profit)

    return None


def run_backtest(
    symbol: str,
    interval: str = "15m",
    days: int = 90,
    window: int = 200,
    max_hold_bars: int = 96,
) -> BacktestReport:
    """
    Walk forward one candle at a time. With no open trade, build an
    indicator snapshot from the trailing `window` candles (not including the
    current one) and evaluate `rule_based_signal`; if it fires, enter at the
    *next* candle's open (avoids lookahead bias). While a trade is open,
    check each subsequent candle's high/low against stop/target; if both are
    touched in the same candle, assume the stop was hit first (conservative).
    Force-close at `max_hold_bars` at that candle's close if neither level
    is hit.
    """
    if interval not in _INTERVAL_MS:
        raise ValueError(f"Unsupported interval: {interval}")

    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - days * 86_400_000
    lookback_ms = window * _INTERVAL_MS[interval]

    candles = get_historical_candles(
        symbol=symbol, interval=interval, start_time_ms=start_ms - lookback_ms, end_time_ms=end_ms,
    )
    logger.info(f"Backtesting {symbol} {interval}: {len(candles)} candles over ~{days}d.")

    report = BacktestReport(symbol=symbol, interval=interval, candle_count=len(candles))
    open_trade: Optional[Trade] = None
    pending_signal: Optional[RuleSignal] = None

    i = window
    while i < len(candles):
        candle = candles[i]

        if open_trade is not None:
            risk = abs(open_trade.entry_price - open_trade.stop_loss)
            hit_sl = candle.low <= open_trade.stop_loss if open_trade.direction == "long" else candle.high >= open_trade.stop_loss
            hit_tp = candle.high >= open_trade.take_profit if open_trade.direction == "long" else candle.low <= open_trade.take_profit
            held_bars = i - open_trade.entry_index

            if hit_sl:
                open_trade.exit_price, open_trade.outcome = open_trade.stop_loss, "loss"
            elif hit_tp:
                open_trade.exit_price, open_trade.outcome = open_trade.take_profit, "win"
            elif held_bars >= max_hold_bars:
                open_trade.exit_price, open_trade.outcome = candle.close, "timeout"

            if open_trade.exit_price is not None:
                open_trade.exit_index, open_trade.exit_time = i, candle.timestamp
                direction_sign = 1 if open_trade.direction == "long" else -1
                pnl = direction_sign * (open_trade.exit_price - open_trade.entry_price)
                open_trade.r_multiple = pnl / risk if risk > 0 else 0.0
                report.trades.append(open_trade)
                open_trade = None

        elif pending_signal is not None:
            entry_price = candle.open
            valid = (
                pending_signal.stop_loss < entry_price < pending_signal.take_profit
                if pending_signal.direction == "long"
                else pending_signal.take_profit < entry_price < pending_signal.stop_loss
            )
            if valid:
                open_trade = Trade(
                    direction=pending_signal.direction, entry_index=i, entry_time=candle.timestamp,
                    entry_price=entry_price, stop_loss=pending_signal.stop_loss, take_profit=pending_signal.take_profit,
                )
            pending_signal = None

        else:
            window_candles = candles[i - window:i]
            snapshot = build_price_action_snapshot(source="binance", id=symbol, interval=interval, candles=window_candles)
            pending_signal = rule_based_signal(snapshot)

        i += 1

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest the price-action rule proxy over historical Binance data.")
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--window", type=int, default=200, help="Indicator lookback window (candles)")
    parser.add_argument("--max-hold-bars", type=int, default=96, help="Force-close open trades after N candles")
    parser.add_argument("--out", default=None, help="Optional path to write full JSON report")
    args = parser.parse_args()

    result = run_backtest(
        symbol=args.symbol, interval=args.interval, days=args.days,
        window=args.window, max_hold_bars=args.max_hold_bars,
    )
    print(json.dumps(result.summary(), indent=2))

    if args.out:
        with open(args.out, "w") as f:
            json.dump(
                {
                    "summary": result.summary(),
                    "trades": [
                        {
                            **{k: v for k, v in t.__dict__.items()},
                        }
                        for t in result.trades
                    ],
                },
                f,
                indent=2,
                default=str,
            )
        print(f"Full report written to {args.out}")
