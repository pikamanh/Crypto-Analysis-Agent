from typing import List, Optional

import pandas as pd
from pydantic import BaseModel


class Candle(BaseModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float


class PriceActionSnapshot(BaseModel):
    source: str
    id: str
    interval: str
    candles: List[Candle]
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    ema_12: Optional[float] = None
    ema_26: Optional[float] = None
    rsi_14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    support: Optional[float] = None
    resistance: Optional[float] = None


def build_price_action_snapshot(
    source: str, id: str, interval: str, candles: List[Candle]
) -> PriceActionSnapshot:
    close = pd.Series([c.close for c in candles])

    sma_20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None
    sma_50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None

    ema_12_series = close.ewm(span=12, adjust=False).mean()
    ema_26_series = close.ewm(span=26, adjust=False).mean()
    ema_12 = ema_12_series.iloc[-1] if len(close) >= 12 else None
    ema_26 = ema_26_series.iloc[-1] if len(close) >= 26 else None

    macd_line = ema_12_series - ema_26_series
    macd_signal_series = macd_line.ewm(span=9, adjust=False).mean()
    macd = macd_line.iloc[-1] if len(close) >= 26 else None
    macd_signal = macd_signal_series.iloc[-1] if len(close) >= 26 + 9 else None
    macd_histogram = (macd - macd_signal) if macd is not None and macd_signal is not None else None

    rsi_14 = None
    if len(close) >= 15:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        last_gain, last_loss = gain.iloc[-1], loss.iloc[-1]
        if last_loss == 0:
            rsi_14 = 100.0
        else:
            rs = last_gain / last_loss
            rsi_14 = 100 - (100 / (1 + rs))

    lookback = candles[-20:] if len(candles) >= 20 else candles
    support = min(c.low for c in lookback) if lookback else None
    resistance = max(c.high for c in lookback) if lookback else None

    return PriceActionSnapshot(
        source=source,
        id=id,
        interval=interval,
        candles=candles,
        sma_20=sma_20,
        sma_50=sma_50,
        ema_12=ema_12,
        ema_26=ema_26,
        rsi_14=rsi_14,
        macd=macd,
        macd_signal=macd_signal,
        macd_histogram=macd_histogram,
        support=support,
        resistance=resistance,
    )
