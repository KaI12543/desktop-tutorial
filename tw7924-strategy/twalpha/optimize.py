"""參數最佳化與 Walk-Forward 驗證。

為什麼一定要 walk-forward:
  在同一段歷史資料上找出最賺的參數, 幾乎一定是過度配適 (overfitting)。
  真正該問的是「用過去 N 年找出來的參數, 套到接下來沒看過的那一年, 還有用嗎?」
  這裡的做法是滾動切窗: 訓練期找參數 -> 測試期實測 -> 視窗往前推, 把所有測試期
  的結果串起來。這條 OOS 權益曲線才是比較接近實戰的估計。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Dict, List, Optional

from .config import BacktestConfig
from .datafeed import Bar
from .engine import BacktestResult, run
from .strategies import REGISTRY, Strategy, build

OBJECTIVES = {
    "calmar": lambda m: m["Calmar"],
    "sharpe": lambda m: m["Sharpe"],
    "cagr": lambda m: m["年化報酬率%"],
    "expectancy": lambda m: m["每筆期望值R"],
    "profit": lambda m: m["總報酬率%"],
}


@dataclass
class OptRow:
    params: Dict[str, float]
    score: float
    metrics: dict


def _grid(strategy_name: str, overrides: Optional[Dict[str, List]] = None):
    grid = dict(REGISTRY[strategy_name].param_grid)
    grid.update(overrides or {})
    if not grid:
        return [{}]
    keys = sorted(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*[grid[k] for k in keys])]


def grid_search(bars: List[Bar], strategy_name: str, cfg: BacktestConfig,
                objective: str = "calmar", min_trades: int = 5,
                overrides: Optional[Dict[str, List]] = None) -> List[OptRow]:
    score_fn = OBJECTIVES[objective]
    rows: List[OptRow] = []
    for params in _grid(strategy_name, overrides):
        try:
            result = run(bars, build(strategy_name, **params), cfg)
        except ValueError:
            continue
        m = result.metrics
        if m["交易次數"] < min_trades:
            continue
        score = score_fn(m)
        if score != score or score in (float("inf"), float("-inf")):
            continue
        rows.append(OptRow(params, score, m))
    rows.sort(key=lambda r: r.score, reverse=True)
    return rows


@dataclass
class WalkForwardResult:
    strategy: str
    windows: List[dict]
    oos_dates: List[str]
    oos_equity: List[float]
    metrics: dict


def walk_forward(bars: List[Bar], strategy_name: str, cfg: BacktestConfig,
                 train_days: int = 500, test_days: int = 125,
                 objective: str = "calmar", min_trades: int = 3,
                 verbose: bool = True) -> WalkForwardResult:
    """滾動最佳化。train_days / test_days 以交易日計 (約 250 天 = 1 年)。"""
    if len(bars) < train_days + test_days + 30:
        raise ValueError("資料長度 %d 不足以做 %d/%d 的 walk-forward, 請縮短視窗或抓更多年資料"
                         % (len(bars), train_days, test_days))

    windows: List[dict] = []
    oos_dates: List[str] = []
    oos_equity: List[float] = []
    capital = cfg.risk.initial_capital
    start = 0

    while start + train_days + test_days <= len(bars):
        train = bars[start:start + train_days]
        # 測試期往前多帶 200 根, 讓長天期指標(如 MA120)有足夠暖機資料, 但績效只計測試段
        warmup = 200
        test_start = start + train_days
        test = bars[max(0, test_start - warmup):test_start + test_days]
        cut = test_start - max(0, test_start - warmup)

        train_cfg = _clone_cfg(cfg, capital)
        rows = grid_search(train, strategy_name, train_cfg, objective, min_trades)
        if not rows:
            start += test_days
            continue
        best = rows[0]

        test_cfg = _clone_cfg(cfg, capital)
        res = run(test, build(strategy_name, **best.params), test_cfg)
        # 只取測試段的權益 (扣掉暖機段), 並重新基準化到目前資金
        seg_dates, seg_equity = [], []
        base = None
        for d, e in zip(res.dates, res.equity):
            if d >= test[cut].date:
                if base is None:
                    base = e
                seg_dates.append(d)
                seg_equity.append(capital * e / base)
        if seg_dates:
            oos_dates += seg_dates
            oos_equity += seg_equity
            capital = seg_equity[-1]

        windows.append({
            "訓練期": "%s ~ %s" % (train[0].date, train[-1].date),
            "測試期": "%s ~ %s" % (seg_dates[0], seg_dates[-1]) if seg_dates else "-",
            "最佳參數": best.params,
            "訓練期年化%": best.metrics["年化報酬率%"],
            "測試期報酬%": (seg_equity[-1] / seg_equity[0] - 1) * 100.0 if len(seg_equity) > 1 else 0.0,
            "測試期交易次數": res.metrics["交易次數"],
            "期末資金": capital,
        })
        if verbose:
            w = windows[-1]
            print("  視窗 %d: 訓練 %s -> 測試 %s | 參數 %s | OOS %+.1f%%"
                  % (len(windows), w["訓練期"], w["測試期"], w["最佳參數"], w["測試期報酬%"]))
        start += test_days

    if not oos_equity:
        raise RuntimeError("walk-forward 沒有產生任何 OOS 結果 (可能是訊號太少)")

    stub = BacktestResult(strategy_name, {}, cfg.stock, dates=oos_dates, equity=oos_equity,
                          buy_hold_equity=[], initial_capital=cfg.risk.initial_capital)
    from .metrics import summarize
    metrics = summarize(stub, cfg)
    metrics["視窗數"] = len(windows)
    return WalkForwardResult(strategy_name, windows, oos_dates, oos_equity, metrics)


def _clone_cfg(cfg: BacktestConfig, capital: float) -> BacktestConfig:
    import copy
    new = copy.deepcopy(cfg)
    new.risk.initial_capital = capital
    return new
