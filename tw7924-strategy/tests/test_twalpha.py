# -*- coding: utf-8 -*-
"""單元測試: 重點在「回測沒有偷看未來」與「成本、部位大小算得對」。

    python3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from twalpha import indicators as ind
from twalpha import strategies as st
from twalpha.config import BacktestConfig, CostModel, RiskConfig
from twalpha.datafeed import Bar, _roc_to_iso, load_csv, save_csv, synthetic
from twalpha.engine import run, size_position
from twalpha.optimize import grid_search, walk_forward


class TestIndicators(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(ind.sma([1, 2, 3, 4, 5], 3)[:3], [None, None, 2.0])
        self.assertEqual(ind.sma([1, 2, 3, 4, 5], 3)[-1], 4.0)

    def test_ema_seed_is_sma(self):
        vals = [float(i) for i in range(1, 11)]
        self.assertAlmostEqual(ind.ema(vals, 5)[4], 3.0)

    def test_rsi_bounds_and_extremes(self):
        up = [float(i) for i in range(1, 40)]
        self.assertAlmostEqual(ind.rsi(up, 14)[-1], 100.0)
        mixed = ind.rsi([float(i % 7) + 1 for i in range(60)], 14)
        for v in mixed[14:]:
            self.assertTrue(0.0 <= v <= 100.0)

    def test_atr_positive(self):
        bars = synthetic(days=200, seed=7)
        vals = ind.atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars], 14)
        self.assertIsNone(vals[5])
        self.assertTrue(all(v > 0 for v in vals[20:]))

    def test_indicator_has_no_lookahead(self):
        """把未來的資料改掉, 過去的指標值不能跟著變。"""
        vals = [float(i % 13) + 10 for i in range(120)]
        base = ind.sma(vals, 20)[:60] + ind.rsi(vals, 14)[:60]
        tampered = list(vals)
        for i in range(60, 120):
            tampered[i] = 999.0
        after = ind.sma(tampered, 20)[:60] + ind.rsi(tampered, 14)[:60]
        self.assertEqual(base, after)


class TestCosts(unittest.TestCase):
    def setUp(self):
        self.c = CostModel()

    def test_min_fee_applies_to_small_trades(self):
        self.assertEqual(self.c.buy_fee(1000), 20.0)          # 比例算出來只有 0.9 元
        self.assertAlmostEqual(self.c.buy_fee(100_000), 100_000 * 0.001425 * 0.6)

    def test_breakeven_shrinks_as_size_grows(self):
        small = self.c.breakeven_pct(5_000)
        big = self.c.breakeven_pct(500_000)
        self.assertGreater(small, big)
        self.assertGreater(small, 1.0)      # 小額交易來回成本 > 1%

    def test_sell_tax_day_trade_is_half(self):
        self.assertAlmostEqual(self.c.sell_tax(100_000, True), self.c.sell_tax(100_000) / 2)


class TestSizing(unittest.TestCase):
    def test_risk_sizing_caps_loss_at_risk_pct(self):
        cfg = BacktestConfig(risk=RiskConfig(initial_capital=100_000, risk_pct=0.02))
        qty = size_position(price=50.0, per_share_risk=5.0, cash=100_000, cfg=cfg)
        self.assertEqual(qty, 400)                      # 100000*2% / 5 = 400 股
        self.assertLessEqual(qty * 5.0, 100_000 * 0.02 + 1e-9)

    def test_round_lot_mode_rounds_down_to_1000(self):
        cfg = BacktestConfig(risk=RiskConfig(initial_capital=100_000, sizing="full",
                                             lot_mode="round"))
        qty = size_position(price=50.0, per_share_risk=5.0, cash=100_000, cfg=cfg,
                            use_risk_sizing=False)
        self.assertEqual(qty % 1000, 0)
        self.assertEqual(qty, 1000)

    def test_never_buys_more_than_cash(self):
        cfg = BacktestConfig(risk=RiskConfig(initial_capital=10_000, sizing="full"))
        qty = size_position(price=100.0, per_share_risk=5.0, cash=10_000, cfg=cfg,
                            use_risk_sizing=False)
        cost = qty * 100.0 + cfg.costs.buy_fee(qty * 100.0)
        self.assertLessEqual(cost, 10_000)

    def test_too_expensive_returns_zero(self):
        cfg = BacktestConfig(risk=RiskConfig(initial_capital=500, sizing="full"))
        self.assertEqual(size_position(1000.0, 50.0, 500, cfg, False), 0)


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.bars = synthetic(days=900, seed=42)
        self.cfg = BacktestConfig(stock="TEST")

    def test_entry_fills_at_next_open_plus_slippage(self):
        """訊號在第 i 根收盤產生, 成交價必須是第 i+1 根的開盤價 (加滑價)。"""
        strat = st.build("donchian")
        entries, _exits = strat.signals(self.bars)
        result = run(self.bars, strat, self.cfg)
        by_date = {b.date: b for b in self.bars}
        index = {b.date: i for i, b in enumerate(self.bars)}
        self.assertTrue(result.trades)
        for trade in result.trades:
            bar = by_date[trade.entry_date]
            expected = bar.open + self.cfg.costs.slippage(bar.open)
            self.assertAlmostEqual(trade.entry_price, expected, places=6)
            self.assertTrue(entries[index[trade.entry_date] - 1])

    def test_exit_never_before_entry(self):
        result = run(self.bars, st.build("rsi_rev"), self.cfg)
        for trade in result.trades:
            self.assertGreaterEqual(trade.exit_date, trade.entry_date)
            self.assertGreater(trade.shares, 0)

    def test_equity_never_negative_and_matches_trades(self):
        result = run(self.bars, st.build("macd"), self.cfg)
        self.assertTrue(all(e > 0 for e in result.equity))
        realised = sum(t.net_pnl for t in result.trades)
        self.assertAlmostEqual(result.final_equity - result.initial_capital, realised, places=4)

    def test_buyhold_matches_benchmark(self):
        result = run(self.bars, st.build("buyhold"), self.cfg)
        self.assertAlmostEqual(result.metrics["總報酬率%"], result.metrics["同期買進持有%"], places=6)

    def test_costs_reduce_returns(self):
        cheap = BacktestConfig(stock="TEST", costs=CostModel(fee_discount=0.28, min_fee=1.0,
                                                            slippage_bps=0.0))
        pricey = BacktestConfig(stock="TEST", costs=CostModel(fee_discount=1.0, min_fee=50.0,
                                                             slippage_bps=30.0))
        a = run(self.bars, st.build("rsi_rev"), cheap).metrics["總報酬率%"]
        b = run(self.bars, st.build("rsi_rev"), pricey).metrics["總報酬率%"]
        self.assertGreater(a, b)

    def test_stop_loss_caps_single_trade_loss(self):
        cfg = BacktestConfig(stock="TEST", risk=RiskConfig(initial_capital=200_000,
                                                           risk_pct=0.02, trail_atr_mult=0))
        result = run(self.bars, st.build("donchian"), cfg)
        for trade in result.trades:
            if trade.exit_reason == "停損":
                # 允許跳空造成的超額虧損, 但不該離譜 (3 倍風險以內)
                self.assertGreater(trade.r_multiple, -3.0)

    def test_short_data_raises(self):
        with self.assertRaises(ValueError):
            run(self.bars[:10], st.build("macd"), self.cfg)


class TestStrategies(unittest.TestCase):
    def test_all_strategies_return_aligned_boolean_lists(self):
        bars = synthetic(days=400, seed=11)
        for name in st.REGISTRY:
            entries, exits = st.build(name).signals(bars)
            self.assertEqual(len(entries), len(bars), name)
            self.assertEqual(len(exits), len(bars), name)
            self.assertTrue(all(isinstance(v, bool) for v in entries), name)

    def test_unknown_param_is_rejected(self):
        with self.assertRaises(ValueError):
            st.build("donchian", not_a_param=3)

    def test_parse_params(self):
        self.assertEqual(st.parse_params(["fast=10", "vol_mult=1.5"]),
                         {"fast": 10.0, "vol_mult": 1.5})
        with self.assertRaises(ValueError):
            st.parse_params(["fast"])


class TestDataFeed(unittest.TestCase):
    def test_roc_date_conversion(self):
        self.assertEqual(_roc_to_iso("114/09/01"), "2025-09-01")
        self.assertEqual(_roc_to_iso("1140901"), "2025-09-01")
        self.assertEqual(_roc_to_iso("99/12/31"), "2010-12-31")

    def test_csv_round_trip(self):
        bars = synthetic(days=50, seed=3)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.csv")
            save_csv(bars, path)
            back = load_csv(path)
        self.assertEqual(len(back), len(bars))
        self.assertEqual(back[0].date, bars[0].date)
        self.assertAlmostEqual(back[-1].close, bars[-1].close)

    def test_csv_skips_invalid_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "y.csv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("date,open,high,low,close,volume\n")
                fh.write("2025-01-02,10,11,9,10.5,1000\n")
                fh.write("2025-01-03,--,--,--,--,0\n")       # 停牌
            self.assertEqual(len(load_csv(path)), 1)


class TestOptimize(unittest.TestCase):
    def test_grid_search_sorted_and_filtered(self):
        bars = synthetic(days=900, seed=5)
        cfg = BacktestConfig(stock="TEST")
        rows = grid_search(bars, "rsi_rev", cfg, "sharpe", min_trades=3)
        self.assertTrue(rows)
        self.assertTrue(all(r.metrics["交易次數"] >= 3 for r in rows))
        self.assertGreaterEqual(rows[0].score, rows[-1].score)

    def test_walk_forward_produces_oos_curve(self):
        bars = synthetic(days=1200, seed=9)
        cfg = BacktestConfig(stock="TEST")
        wf = walk_forward(bars, "rsi_rev", cfg, train_days=400, test_days=125, verbose=False)
        self.assertGreater(wf.metrics["視窗數"], 1)
        self.assertEqual(len(wf.oos_dates), len(wf.oos_equity))
        self.assertEqual(sorted(wf.oos_dates), wf.oos_dates)   # 時間必須遞增且不重疊


if __name__ == "__main__":
    unittest.main()
