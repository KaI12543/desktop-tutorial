"""事件式回測引擎 (逐根 K 棒)。

刻意做成「保守派」:
  * 訊號在收盤後產生, 隔日開盤成交, 並加上滑價。
  * 同一根 K 棒同時觸及停損與停利時, 一律算停損 (不給自己好處)。
  * 買賣都扣真實台股成本 (手續費有最低收費、賣出有證交稅)。
  * 現金不足就買不到, 不允許隱形融資。

這些設定會讓回測數字變醜, 但比較接近你真的下單會拿到的結果。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from .config import BacktestConfig
from .datafeed import Bar
from .strategies import Strategy


@dataclass
class Trade:
    entry_date: str
    entry_price: float
    shares: int
    exit_date: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    entry_cost: float = 0.0
    exit_cost: float = 0.0
    stop_price: float = 0.0
    risk_per_share: float = 0.0
    hold_days: int = 0

    @property
    def gross_pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.shares

    @property
    def costs(self) -> float:
        return self.entry_cost + self.exit_cost

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.costs

    @property
    def invested(self) -> float:
        return self.entry_price * self.shares

    @property
    def return_pct(self) -> float:
        return self.net_pnl / self.invested * 100.0 if self.invested else 0.0

    @property
    def r_multiple(self) -> float:
        """賺賠是「單筆風險」的幾倍 -- 比報酬率更能看出策略體質。"""
        risk = self.risk_per_share * self.shares
        return self.net_pnl / risk if risk > 0 else 0.0


@dataclass
class BacktestResult:
    strategy: str
    params: dict
    stock: str
    dates: List[str] = field(default_factory=list)
    equity: List[float] = field(default_factory=list)
    buy_hold_equity: List[float] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)
    skipped_signals: int = 0
    initial_capital: float = 0.0
    days_in_market: int = 0
    metrics: dict = field(default_factory=dict)
    bh_shares: int = 0          # 基準線持股, 讓 metrics 能算出賣出時該扣的稅費
    bh_last_close: float = 0.0

    @property
    def final_equity(self) -> float:
        return self.equity[-1] if self.equity else self.initial_capital


def _atr_or_fallback(value: Optional[float], price: float) -> float:
    if value is None or value != value or value <= 0:
        return price * 0.05      # 沒有 ATR 時, 用 5% 當作波動估計
    return value


def size_position(price: float, per_share_risk: float, cash: float,
                  cfg: BacktestConfig, use_risk_sizing: bool = True) -> int:
    """算出這筆該買幾股 -- 回測與實單共用同一套邏輯, 才不會「回測一套、下單一套」。

    risk 模式: 股數 = (資金 x 單筆風險%) / 每股風險(進場價 - 停損價)
    再受限於: 現金買得起 (含手續費)、單一部位上限、零股/整股單位。
    """
    risk, costs = cfg.risk, cfg.costs
    if price <= 0:
        return 0
    if use_risk_sizing and risk.sizing == "risk":
        target = (cash * risk.risk_pct) / max(per_share_risk, 0.01)
    elif risk.sizing == "fraction":
        target = (cash * risk.fraction) / price
    else:
        target = cash / price

    eff_fee = costs.fee_rate * costs.fee_discount
    budget = min(cash - costs.min_fee, cash / (1.0 + eff_fee), cash * risk.max_position_pct)
    target = min(target, budget / price)
    qty = int(math.floor(max(target, 0) / risk.lot_size) * risk.lot_size)
    if qty < risk.lot_size or qty * price + costs.buy_fee(qty * price) > cash:
        return 0
    return qty


def run(bars: List[Bar], strategy: Strategy, cfg: BacktestConfig) -> BacktestResult:
    if len(bars) < 30:
        raise ValueError("資料太少 (%d 根), 至少需要 30 根日線" % len(bars))

    entries, exits = strategy.signals(bars)
    atr_series = strategy.atr_series(bars)
    costs, risk = cfg.costs, cfg.risk
    sizing_mode = strategy.force_sizing or risk.sizing
    risk_exits_on = strategy.use_risk_exits

    cash = risk.initial_capital
    shares = 0
    trade: Optional[Trade] = None
    stop = tp = 0.0
    entry_index = 0
    pending_exit = ""
    result = BacktestResult(strategy.name, dict(strategy.params), cfg.stock,
                            initial_capital=risk.initial_capital)

    # 基準線: 與策略同一天(第 2 根 K 棒開盤)進場, 同樣吃滑價與手續費, 才是公平比較
    bh_price = bars[1].open + costs.slippage(bars[1].open)
    bh_shares = int(math.floor(min(risk.initial_capital - costs.min_fee,
                                   risk.initial_capital / (1.0 + costs.fee_rate * costs.fee_discount))
                               / bh_price / risk.lot_size) * risk.lot_size)
    bh_cash = risk.initial_capital - bh_shares * bh_price - costs.buy_fee(bh_shares * bh_price)
    result.bh_shares = bh_shares
    result.bh_last_close = bars[-1].close

    def close_position(bar: Bar, price: float, reason: str) -> None:
        nonlocal cash, shares, trade, pending_exit, stop, tp
        amount = price * shares
        fee = costs.sell_fee(amount) + costs.sell_tax(amount)
        cash += amount - fee
        trade.exit_date, trade.exit_price, trade.exit_reason = bar.date, price, reason
        trade.exit_cost = fee
        trade.hold_days = (bar.dt - bars[entry_index].dt).days
        result.trades.append(trade)
        trade, shares, stop, tp, pending_exit = None, 0, 0.0, 0.0, ""

    for j in range(1, len(bars)):
        bar = bars[j]
        prev = j - 1
        exited_today = False

        # ---------- 1. 出場 ----------
        if shares > 0:
            if pending_exit:
                close_position(bar, bar.open - costs.slippage(bar.open), pending_exit)
                exited_today = True
            elif risk_exits_on and bar.low <= stop:
                fill = min(bar.open, stop)          # 跳空低開時只能用開盤價出場
                close_position(bar, fill - costs.slippage(fill), "停損")
                exited_today = True
            elif risk_exits_on and tp > 0 and bar.high >= tp:
                fill = max(bar.open, tp)
                close_position(bar, fill - costs.slippage(fill), "停利")
                exited_today = True

        # ---------- 2. 進場 ----------
        if shares == 0 and not exited_today and entries[prev] and j < len(bars) - 1:
            price = bar.open + costs.slippage(bar.open)
            atr_val = _atr_or_fallback(atr_series[prev], price)
            stop_price = price - risk.stop_atr_mult * atr_val
            per_share_risk = max(price - stop_price, 0.01)
            equity_now = cash

            qty = size_position(price, per_share_risk, equity_now, cfg,
                                use_risk_sizing=risk_exits_on and sizing_mode == "risk")
            if qty > 0:
                amount = qty * price
                fee = costs.buy_fee(amount)
                cash -= amount + fee
                shares = qty
                entry_index = j
                stop = stop_price
                tp = (price + risk.take_profit_r * per_share_risk
                      if risk.take_profit_r > 0 and risk_exits_on else 0.0)
                trade = Trade(entry_date=bar.date, entry_price=price, shares=qty,
                              entry_cost=fee, stop_price=stop_price,
                              risk_per_share=per_share_risk)
            else:
                result.skipped_signals += 1   # 資金不足或零股都買不起

        # ---------- 3. 收盤後: 更新移動停損 / 排定明日出場 ----------
        if shares > 0:
            if risk_exits_on and risk.trail_atr_mult > 0:
                atr_now = _atr_or_fallback(atr_series[j], bar.close)
                stop = max(stop, bar.close - risk.trail_atr_mult * atr_now)
            if exits[j]:
                pending_exit = "訊號出場"
            elif risk_exits_on and risk.max_hold_days > 0 and (bar.dt - bars[entry_index].dt).days >= risk.max_hold_days:
                pending_exit = "時間停損"
            result.days_in_market += 1

        equity = cash + shares * bar.close
        result.dates.append(bar.date)
        result.equity.append(equity)
        result.buy_hold_equity.append(bh_cash + bh_shares * bar.close)

    # 回測結束: 以最後收盤價結清, 讓績效數字包含未實現損益
    if shares > 0:
        close_position(bars[-1], bars[-1].close, "回測結束平倉")
        result.equity[-1] = cash

    from .metrics import summarize
    result.metrics = summarize(result, cfg)
    return result
