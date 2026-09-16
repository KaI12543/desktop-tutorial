"""績效指標。

小資金使用者請優先看這三個數字:
  1. 最大回檔 (MDD): 你的心臟與本金撐不撐得住。
  2. 每筆期望值 (R): 平均一筆交易賺幾倍風險, 小於 0 就不要做。
  3. 成本佔毛利比例: 小額交易最常見的死法 -- 賺的都拿去繳手續費了。
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List

TRADING_DAYS = 252


def _daily_returns(equity: List[float]) -> List[float]:
    out = []
    for i in range(1, len(equity)):
        prev = equity[i - 1]
        out.append((equity[i] - prev) / prev if prev > 0 else 0.0)
    return out


def _stdev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def max_drawdown(equity: List[float]):
    """回傳 (最大回檔%, 最長回檔天數(交易日), 谷底索引)。"""
    peak = equity[0] if equity else 0.0
    peak_idx = 0
    worst = 0.0
    worst_idx = 0
    longest = 0
    for i, val in enumerate(equity):
        if val > peak:
            peak, peak_idx = val, i
        dd = (peak - val) / peak * 100.0 if peak > 0 else 0.0
        if dd > worst:
            worst, worst_idx = dd, i
        longest = max(longest, i - peak_idx)
    return worst, longest, worst_idx


def cagr(equity: List[float], dates: List[str]) -> float:
    if len(equity) < 2 or equity[0] <= 0:
        return 0.0
    d0 = datetime.strptime(dates[0], "%Y-%m-%d")
    d1 = datetime.strptime(dates[-1], "%Y-%m-%d")
    years = max((d1 - d0).days / 365.25, 1e-9)
    ratio = equity[-1] / equity[0]
    if ratio <= 0:
        return -100.0
    return (ratio ** (1 / years) - 1) * 100.0


def monthly_returns(dates: List[str], equity: List[float]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    first: Dict[str, float] = {}
    last: Dict[str, float] = {}
    prev_month_end = None
    for d, e in zip(dates, equity):
        ym = d[:7]
        if ym not in first:
            first[ym] = prev_month_end if prev_month_end is not None else e
        last[ym] = e
        prev_month_end = e
    for ym in first:
        base = first[ym]
        out[ym] = (last[ym] - base) / base * 100.0 if base > 0 else 0.0
    return dict(sorted(out.items()))


def summarize(result, cfg) -> dict:
    equity, dates = result.equity, result.dates
    trades = result.trades
    start_equity = result.initial_capital
    rets = _daily_returns([start_equity] + equity)
    sd = _stdev(rets)
    downside = _stdev([r for r in rets if r < 0]) if any(r < 0 for r in rets) else 0.0
    mean = sum(rets) / len(rets) if rets else 0.0

    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    gross_win = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))
    total_costs = sum(t.costs for t in trades)
    total_gross = sum(t.gross_pnl for t in trades)

    mdd, dd_days, _ = max_drawdown([start_equity] + equity)
    ann = cagr([start_equity] + equity, [dates[0]] + dates) if dates else 0.0
    # 基準線也要扣掉最後賣出的手續費與證交稅, 否則等於偷偷少付成本
    bh_final = result.buy_hold_equity[-1] if result.buy_hold_equity else start_equity
    if result.buy_hold_equity and result.bh_shares:
        bh_position = result.bh_shares * result.bh_last_close
        bh_final -= cfg.costs.sell_fee(bh_position) + cfg.costs.sell_tax(bh_position)

    return {
        "期間": "%s ~ %s" % (dates[0], dates[-1]) if dates else "-",
        "交易日數": len(dates),
        "期初資金": start_equity,
        "期末資金": result.final_equity,
        "總報酬率%": (result.final_equity - start_equity) / start_equity * 100.0,
        "年化報酬率%": ann,
        "最大回檔%": mdd,
        "最長回檔(交易日)": dd_days,
        "Sharpe": (mean / sd * (TRADING_DAYS ** 0.5)) if sd > 0 else 0.0,
        "Sortino": (mean / downside * (TRADING_DAYS ** 0.5)) if downside > 0 else 0.0,
        "Calmar": (ann / mdd) if mdd > 0 else 0.0,
        "交易次數": len(trades),
        "勝率%": len(wins) / len(trades) * 100.0 if trades else 0.0,
        "平均獲利": gross_win / len(wins) if wins else 0.0,
        "平均虧損": -gross_loss / len(losses) if losses else 0.0,
        "賺賠比": (gross_win / len(wins)) / (gross_loss / len(losses)) if wins and losses and gross_loss else 0.0,
        "獲利因子": gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0,
        "每筆期望值R": sum(t.r_multiple for t in trades) / len(trades) if trades else 0.0,
        "每筆期望值元": sum(t.net_pnl for t in trades) / len(trades) if trades else 0.0,
        "平均持有天數": sum(t.hold_days for t in trades) / len(trades) if trades else 0.0,
        "總交易成本": total_costs,
        "成本佔毛利%": (total_costs / abs(total_gross) * 100.0) if total_gross else 0.0,
        "成本佔本金%": total_costs / start_equity * 100.0,
        "資金在場比例%": result.days_in_market / len(dates) * 100.0 if dates else 0.0,
        "資金不足錯過訊號": result.skipped_signals,
        "同期買進持有%": (bh_final - start_equity) / start_equity * 100.0,
        "超額報酬%": (result.final_equity - bh_final) / start_equity * 100.0,
    }
