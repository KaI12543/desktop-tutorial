"""把回測策略接到「今天該做什麼」。

回測是研究, 這裡才是每天真的會用到的東西:
最後一根 K 棒收盤後, 策略有沒有發出訊號? 如果有, 明天開盤該買幾股? 停損擺哪裡?
這筆的成本要多少、要漲幾 % 才打平?
"""

from __future__ import annotations

from typing import Dict, List

from .config import BacktestConfig
from .datafeed import Bar
from .engine import _atr_or_fallback, size_position
from .indicators import atr, highest, lowest, rsi, sma
from .strategies import Strategy


def snapshot(bars: List[Bar], strategy: Strategy, cfg: BacktestConfig) -> Dict:
    entries, exits = strategy.signals(bars)
    atr_s = strategy.atr_series(bars)
    last = bars[-1]
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]

    atr_now = _atr_or_fallback(atr_s[-1], last.close)
    # 明日開盤價未知, 用今日收盤 + 滑價當估計
    est_price = last.close + cfg.costs.slippage(last.close)
    stop_price = est_price - cfg.risk.stop_atr_mult * atr_now
    per_share_risk = max(est_price - stop_price, 0.01)
    qty = size_position(est_price, per_share_risk, cfg.risk.initial_capital, cfg,
                        use_risk_sizing=strategy.use_risk_exits)
    amount = qty * est_price
    ma20, ma60, ma120 = sma(closes, 20), sma(closes, 60), sma(closes, 120)
    hi20, lo10 = highest(highs, 20), lowest(lows, 10)
    r14 = rsi(closes, 14)

    return {
        "stock": cfg.stock,
        "strategy": strategy.name,
        "params": dict(strategy.params),
        "資料日期": last.date,
        "收盤": last.close,
        "今日訊號": "進場" if entries[-1] else ("出場" if exits[-1] else "無"),
        "執行時點": "下一個交易日開盤",
        "估計進場價": est_price,
        "ATR(14)": atr_now,
        "建議停損價": stop_price,
        "每股風險": per_share_risk,
        "建議股數": qty,
        "投入金額": amount,
        "單筆最大虧損": qty * per_share_risk,
        "佔資金比例%": amount / cfg.risk.initial_capital * 100 if cfg.risk.initial_capital else 0.0,
        "來回成本": cfg.costs.round_trip_cost(amount) if amount else 0.0,
        "打平需漲幅%": cfg.costs.breakeven_pct(amount) if amount else float("inf"),
        "MA20": ma20[-1], "MA60": ma60[-1], "MA120": ma120[-1],
        "20日高": hi20[-1], "10日低": lo10[-1], "RSI14": r14[-1],
    }


def format_snapshot(snap: Dict) -> str:
    def fnum(v, digits=2):
        if v is None:
            return "資料不足"
        if v in (float("inf"), float("-inf")):
            return "inf"
        return ("{:,.%df}" % digits).format(v)

    lines = [
        "=" * 56,
        " %s  策略 %s  (資料到 %s)" % (snap["stock"], snap["strategy"], snap["資料日期"]),
        "=" * 56,
        "  今日訊號        %s   -> %s執行" % (snap["今日訊號"], snap["執行時點"]),
        "  收盤價          %s" % fnum(snap["收盤"]),
        "  MA20/60/120     %s / %s / %s" % (fnum(snap["MA20"]), fnum(snap["MA60"]), fnum(snap["MA120"])),
        "  20日高 / 10日低  %s / %s" % (fnum(snap["20日高"]), fnum(snap["10日低"])),
        "  RSI14 / ATR14   %s / %s" % (fnum(snap["RSI14"], 1), fnum(snap["ATR(14)"])),
        "-" * 56,
        "  若要進場 (依目前設定):",
        "    估計進場價    %s" % fnum(snap["估計進場價"]),
        "    建議股數      %d 股" % snap["建議股數"],
        "    投入金額      %s 元 (佔資金 %.1f%%)" % (fnum(snap["投入金額"], 0), snap["佔資金比例%"]),
        "    停損價        %s  (每股風險 %s)" % (fnum(snap["建議停損價"]), fnum(snap["每股風險"])),
        "    最大虧損      %s 元" % fnum(snap["單筆最大虧損"], 0),
        "    來回成本      %s 元, 需漲 %s%% 才打平" % (fnum(snap["來回成本"], 0), fnum(snap["打平需漲幅%"])),
        "=" * 56,
        "  提醒: 訊號是「收盤後」才確立的, 請在下一個交易日開盤執行;",
        "        停損價請在成交後就掛好, 不要臨場才決定。",
    ]
    return "\n".join(lines)
