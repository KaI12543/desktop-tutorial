"""技術指標 (純標準庫)。所有函式回傳與輸入等長的 list, 前置不足期間填 None。

重要: 每個指標在第 i 格的值, 只會用到第 i 格(含)以前的資料 -- 不可有未來函數。
"""

from __future__ import annotations

from typing import List, Optional

Series = List[Optional[float]]


def sma(values: List[float], period: int) -> Series:
    out: Series = [None] * len(values)
    if period <= 0:
        return out
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: List[float], period: int) -> Series:
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def stdev(values: List[float], period: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        out[i] = var ** 0.5
    return out


def highest(values: List[float], period: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = max(values[i - period + 1:i + 1])
    return out


def lowest(values: List[float], period: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = min(values[i - period + 1:i + 1])
    return out


def true_range(highs: List[float], lows: List[float], closes: List[float]) -> List[float]:
    out = [highs[0] - lows[0]] if highs else []
    for i in range(1, len(highs)):
        prev = closes[i - 1]
        out.append(max(highs[i] - lows[i], abs(highs[i] - prev), abs(lows[i] - prev)))
    return out


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Series:
    tr = true_range(highs, lows, closes)
    out: Series = [None] * len(tr)
    if len(tr) < period:
        return out
    prev = sum(tr[:period]) / period       # Wilder 平滑
    out[period - 1] = prev
    for i in range(period, len(tr)):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def rsi(values: List[float], period: int = 14) -> Series:
    out: Series = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        gains += max(diff, 0.0)
        losses += max(-diff, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(diff, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def macd(values: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    fast_line, slow_line = ema(values, fast), ema(values, slow)
    diff: Series = [None] * len(values)
    for i in range(len(values)):
        if fast_line[i] is not None and slow_line[i] is not None:
            diff[i] = fast_line[i] - slow_line[i]
    valid = [(i, v) for i, v in enumerate(diff) if v is not None]
    sig: Series = [None] * len(values)
    hist: Series = [None] * len(values)
    if len(valid) >= signal:
        sig_vals = ema([v for _, v in valid], signal)
        for (idx, _), s in zip(valid, sig_vals):
            sig[idx] = s
    for i in range(len(values)):
        if diff[i] is not None and sig[i] is not None:
            hist[i] = diff[i] - sig[i]
    return diff, sig, hist


def bollinger(values: List[float], period: int = 20, mult: float = 2.0):
    mid = sma(values, period)
    sd = stdev(values, period)
    upper: Series = [None] * len(values)
    lower: Series = [None] * len(values)
    width: Series = [None] * len(values)
    for i in range(len(values)):
        if mid[i] is not None and sd[i] is not None:
            upper[i] = mid[i] + mult * sd[i]
            lower[i] = mid[i] - mult * sd[i]
            width[i] = (upper[i] - lower[i]) / mid[i] if mid[i] else None
    return upper, mid, lower, width


def slope(series: Series, lookback: int = 5) -> Series:
    """簡單斜率: (今日 - N 日前) / N, 用來判斷均線是否翻揚。"""
    out: Series = [None] * len(series)
    for i in range(lookback, len(series)):
        a, b = series[i], series[i - lookback]
        if a is not None and b is not None:
            out[i] = (a - b) / lookback
    return out
