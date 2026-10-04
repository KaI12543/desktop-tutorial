"""策略庫。

每個策略只負責「產生進場 / 出場訊號」, 停損、部位大小、成本一律交給回測引擎,
這樣策略之間才能在同一個標準下公平比較。

訊號時序: 第 i 根 K 棒「收盤後」用當日(含)以前的資料判斷, 第 i+1 根「開盤」成交。
絕不用當日最高/最低價去決定當日進場, 避免未來函數把回測績效灌水。
"""

from __future__ import annotations

from typing import Dict, List

from .datafeed import Bar
from .indicators import atr, bollinger, ema, highest, lowest, macd, rsi, sma, slope


class Strategy:
    name = "base"
    description = ""
    default_params: Dict[str, float] = {}
    param_grid: Dict[str, List] = {}
    force_sizing: str = ""      # 非空時覆蓋設定檔的部位規模模式
    use_risk_exits: bool = True # False 表示不套用停損/停利/時間停損

    def __init__(self, **params):
        self.params = dict(self.default_params)
        unknown = set(params) - set(self.default_params)
        if unknown:
            raise ValueError("%s 不支援參數: %s" % (self.name, ", ".join(sorted(unknown))))
        self.params.update(params)

    def __repr__(self):
        args = ", ".join("%s=%s" % kv for kv in sorted(self.params.items()))
        return "%s(%s)" % (self.name, args)

    # 子類別實作: 回傳 (entries, exits)
    def signals(self, bars: List[Bar]):
        raise NotImplementedError

    # 共用小工具
    @staticmethod
    def _series(bars: List[Bar]):
        return (
            [b.open for b in bars], [b.high for b in bars],
            [b.low for b in bars], [b.close for b in bars],
            [b.volume for b in bars],
        )

    def atr_series(self, bars: List[Bar], period: int = 14):
        _o, h, l, c, _v = self._series(bars)
        return atr(h, l, c, period)


class BuyHold(Strategy):
    name = "buyhold"
    description = "買進並抱著 (基準線)。任何策略贏不過它, 就不該花時間盯盤。"
    force_sizing = "full"       # 基準線就是把資金一次投入
    use_risk_exits = False      # 也不套停損, 否則就不叫買進持有了

    def signals(self, bars):
        n = len(bars)
        entries = [False] * n
        if n > 1:
            entries[0] = True
        return entries, [False] * n


class MaCross(Strategy):
    name = "ma_cross"
    description = "均線黃金交叉: 快線上穿慢線且慢線翻揚進場, 死亡交叉出場。抓中長波段。"
    default_params = {"fast": 20, "slow": 60, "slope_len": 10}
    param_grid = {"fast": [5, 10, 20, 30], "slow": [40, 60, 120], "slope_len": [5, 10]}

    def signals(self, bars):
        _o, _h, _l, c, _v = self._series(bars)
        fast = sma(c, int(self.params["fast"]))
        slow = sma(c, int(self.params["slow"]))
        sl = slope(slow, int(self.params["slope_len"]))
        n = len(bars)
        entries, exits = [False] * n, [False] * n
        for i in range(1, n):
            if None in (fast[i], slow[i], fast[i - 1], slow[i - 1]):
                continue
            cross_up = fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]
            cross_dn = fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]
            entries[i] = bool(cross_up and (sl[i] is not None and sl[i] > 0))
            exits[i] = bool(cross_dn)
        return entries, exits


class Donchian(Strategy):
    name = "donchian"
    description = ("N 日高點突破 + 量能放大 + 長多濾網 (海龜式)。"
                   "小型股主升段最有效率的進場方式, 也是本專案的主力策略。")
    default_params = {"entry_len": 20, "exit_len": 10, "vol_mult": 1.5,
                      "vol_len": 20, "trend_len": 120}
    param_grid = {"entry_len": [10, 20, 40, 55], "exit_len": [5, 10, 20],
                  "vol_mult": [1.0, 1.5, 2.0], "trend_len": [0, 60, 120]}

    def signals(self, bars):
        _o, h, l, c, v = self._series(bars)
        up = highest(h, int(self.params["entry_len"]))
        dn = lowest(l, int(self.params["exit_len"]))
        vma = sma(v, int(self.params["vol_len"]))
        trend_len = int(self.params["trend_len"])
        trend = sma(c, trend_len) if trend_len else [0.0] * len(c)
        n = len(bars)
        entries, exits = [False] * n, [False] * n
        for i in range(1, n):
            if up[i - 1] is None or vma[i - 1] is None:
                continue
            vol_ok = v[i] >= vma[i - 1] * self.params["vol_mult"]
            trend_ok = True if not trend_len else (trend[i] is not None and c[i] > trend[i])
            entries[i] = bool(c[i] > up[i - 1] and vol_ok and trend_ok)
            if dn[i - 1] is not None:
                exits[i] = bool(c[i] < dn[i - 1])
        return entries, exits


class RsiReversion(Strategy):
    name = "rsi_rev"
    description = ("長多格局中的短線超賣反彈 (RSI2 式)。持有天數短、交易次數多, "
                   "小資金要特別注意最低手續費會吃掉多少。")
    default_params = {"rsi_len": 3, "oversold": 15, "exit_rsi": 60, "trend_len": 120}
    param_grid = {"rsi_len": [2, 3, 5, 14], "oversold": [10, 15, 25, 30],
                  "exit_rsi": [50, 60, 70], "trend_len": [60, 120, 200]}

    def signals(self, bars):
        _o, _h, _l, c, _v = self._series(bars)
        r = rsi(c, int(self.params["rsi_len"]))
        trend = sma(c, int(self.params["trend_len"]))
        n = len(bars)
        entries, exits = [False] * n, [False] * n
        for i in range(n):
            if r[i] is None or trend[i] is None:
                continue
            entries[i] = bool(c[i] > trend[i] and r[i] < self.params["oversold"])
            exits[i] = bool(r[i] > self.params["exit_rsi"])
        return entries, exits


class BollingerSqueeze(Strategy):
    name = "bb_squeeze"
    description = "布林通道收斂後向上突破: 波動壓縮 -> 爆發, 適合盤整已久的個股。"
    default_params = {"period": 20, "mult": 2.0, "squeeze_len": 60, "exit_period": 10}
    param_grid = {"period": [20, 30], "mult": [1.8, 2.0, 2.5],
                  "squeeze_len": [40, 60, 120], "exit_period": [5, 10, 20]}

    def signals(self, bars):
        _o, _h, l, c, _v = self._series(bars)
        upper, mid, _lower, width = bollinger(c, int(self.params["period"]), self.params["mult"])
        sq_len = int(self.params["squeeze_len"])
        exit_low = lowest(l, int(self.params["exit_period"]))
        n = len(bars)
        entries, exits = [False] * n, [False] * n
        for i in range(1, n):
            if None in (upper[i - 1], width[i]) or i < sq_len:
                continue
            window = [w for w in width[i - sq_len:i] if w is not None]
            if not window:
                continue
            squeezed = width[i - 1] is not None and width[i - 1] <= min(window) * 1.15
            entries[i] = bool(squeezed and c[i] > upper[i - 1])
            if exit_low[i - 1] is not None and mid[i] is not None:
                exits[i] = bool(c[i] < exit_low[i - 1] or c[i] < mid[i])
        return entries, exits


class MacdTrend(Strategy):
    name = "macd"
    description = "MACD 柱狀體由負轉正 + 長天期均線之上, 標準順勢策略。"
    default_params = {"fast": 12, "slow": 26, "signal": 9, "trend_len": 120}
    param_grid = {"fast": [8, 12], "slow": [21, 26, 34], "signal": [6, 9],
                  "trend_len": [0, 60, 120]}

    def signals(self, bars):
        _o, _h, _l, c, _v = self._series(bars)
        _diff, _sig, hist = macd(c, int(self.params["fast"]), int(self.params["slow"]),
                                 int(self.params["signal"]))
        trend_len = int(self.params["trend_len"])
        trend = ema(c, trend_len) if trend_len else [0.0] * len(c)
        n = len(bars)
        entries, exits = [False] * n, [False] * n
        for i in range(1, n):
            if hist[i] is None or hist[i - 1] is None:
                continue
            trend_ok = True if not trend_len else (trend[i] is not None and c[i] > trend[i])
            entries[i] = bool(hist[i - 1] <= 0 < hist[i] and trend_ok)
            exits[i] = bool(hist[i - 1] >= 0 > hist[i])
        return entries, exits


REGISTRY = {cls.name: cls for cls in
            (BuyHold, MaCross, Donchian, RsiReversion, BollingerSqueeze, MacdTrend)}


def build(name: str, **params) -> Strategy:
    if name not in REGISTRY:
        raise KeyError("未知策略 '%s'。可用: %s" % (name, ", ".join(sorted(REGISTRY))))
    return REGISTRY[name](**params)


def parse_params(pairs: List[str]) -> Dict[str, float]:
    """把 CLI 的 ['fast=10','vol_mult=1.5'] 轉成 dict。"""
    out: Dict[str, float] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError("參數格式應為 key=value, 收到: %s" % pair)
        key, val = pair.split("=", 1)
        out[key.strip()] = float(val)
    return out
