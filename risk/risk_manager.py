import os
from typing import List, Literal, Optional

from pydantic import BaseModel

from agents.signal_agent import TradeSignal
from storage import state

MAX_POSITION_PCT = float(os.getenv("RISK_MAX_POSITION_PCT", "5"))
MAX_CONCURRENT_POSITIONS = int(os.getenv("RISK_MAX_CONCURRENT_POSITIONS", "3"))
MAX_DAILY_LOSS_PCT = float(os.getenv("RISK_MAX_DAILY_LOSS_PCT", "3"))
MIN_CONFIDENCE = float(os.getenv("RISK_MIN_CONFIDENCE", "0.5"))


class RiskDecision(BaseModel):
    action: Literal["approve", "reject"]
    symbol: str
    direction: Literal["long", "short", "neutral"]
    notional_pct: float = 0.0
    entry_zone: Optional[List[float]] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: str


def _reject(symbol: str, direction: str, reason: str) -> RiskDecision:
    return RiskDecision(action="reject", symbol=symbol, direction=direction, reason=reason)


def evaluate(
    signal: Optional[TradeSignal],
    symbol: str,
    account_equity: float,
) -> RiskDecision:
    """
    Approve/reject a Stage-3 TradeSignal before it reaches the execution engine.
    Never widens direction/levels the signal didn't produce — only caps size or
    blocks the trade outright.
    """
    if signal is None:
        return _reject(symbol, "neutral", "no signal produced")

    if signal.direction == "neutral":
        return _reject(symbol, "neutral", "signal direction is neutral")

    if signal.confidence < MIN_CONFIDENCE:
        return _reject(
            symbol, signal.direction,
            f"confidence {signal.confidence:.2f} below minimum {MIN_CONFIDENCE:.2f}",
        )

    if not signal.entry_zone or signal.stop_loss is None or signal.take_profit is None:
        return _reject(symbol, signal.direction, "signal missing entry_zone/stop_loss/take_profit levels")

    if account_equity <= 0:
        return _reject(symbol, signal.direction, "account equity is zero or unavailable")

    daily_pnl = state.get_daily_realized_pnl()
    max_daily_loss = -(MAX_DAILY_LOSS_PCT / 100) * account_equity
    if daily_pnl <= max_daily_loss:
        return _reject(
            symbol, signal.direction,
            f"daily loss limit hit (realized {daily_pnl:.2f} <= -{MAX_DAILY_LOSS_PCT}% of equity)",
        )

    if state.has_open_position(symbol):
        return _reject(symbol, signal.direction, f"position already open for {symbol}")

    open_count = state.get_open_position_count()
    if open_count >= MAX_CONCURRENT_POSITIONS:
        return _reject(
            symbol, signal.direction,
            f"max concurrent positions reached ({open_count}/{MAX_CONCURRENT_POSITIONS})",
        )

    size_pct = max(0.0, min(signal.size_pct, 100.0))
    notional_pct = MAX_POSITION_PCT * (size_pct / 100)
    if notional_pct <= 0:
        return _reject(symbol, signal.direction, "signal size_pct resolves to zero position size")

    return RiskDecision(
        action="approve",
        symbol=symbol,
        direction=signal.direction,
        notional_pct=notional_pct,
        entry_zone=signal.entry_zone,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        reason=f"approved: confidence={signal.confidence:.2f}, notional={notional_pct:.2f}% of equity",
    )
