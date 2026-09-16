"""命令列介面。macOS 上直接用內建 python3 執行, 不需安裝任何套件:

    cd tw7924-strategy
    python3 -m twalpha demo                 # 不用網路, 先看看它長怎樣
    python3 -m twalpha fetch                # 抓 7924 近五年日線
    python3 -m twalpha compare              # 所有策略同場較量
    python3 -m twalpha backtest -s donchian --html
    python3 -m twalpha walkforward -s donchian
    python3 -m twalpha signal -s donchian    # 今天該做什麼
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from . import optimize, report
from .config import DEFAULT_STOCK, BacktestConfig, CostModel, RiskConfig
from .datafeed import fetch, load, synthetic
from .engine import run
from .live import format_snapshot, snapshot
from .strategies import REGISTRY, build, parse_params

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")


# --------------------------------------------------------------------------

def _common(parser: argparse.ArgumentParser, with_strategy: bool = True) -> None:
    parser.add_argument("--stock", default=DEFAULT_STOCK, help="股票代號 (預設 %s)" % DEFAULT_STOCK)
    parser.add_argument("--csv", default="", help="改用自己的日線 CSV 檔")
    parser.add_argument("--start", default="", help="起始日 YYYY-MM-DD")
    parser.add_argument("--end", default="", help="結束日 YYYY-MM-DD")
    parser.add_argument("--demo", action="store_true", help="用合成資料跑 (無需網路)")
    if with_strategy:
        parser.add_argument("-s", "--strategy", default="donchian",
                            choices=sorted(REGISTRY), help="策略名稱")
        parser.add_argument("-p", "--param", action="append", default=[],
                            metavar="KEY=VALUE", help="覆寫策略參數, 可重複")
    # 資金與風控
    parser.add_argument("--capital", type=float, default=100_000.0, help="期初資金 (元)")
    parser.add_argument("--risk-pct", type=float, default=2.0, help="單筆風險 %% (預設 2)")
    parser.add_argument("--sizing", default="risk", choices=["risk", "full", "fraction"])
    parser.add_argument("--lot", default="odd", choices=["odd", "round"],
                        help="odd=零股(小資金建議), round=整股 1000 股")
    parser.add_argument("--stop-atr", type=float, default=2.0, help="初始停損 = N x ATR")
    parser.add_argument("--trail-atr", type=float, default=3.0, help="移動停利 = N x ATR, 0 關閉")
    parser.add_argument("--take-profit-r", type=float, default=0.0, help="N 倍風險停利, 0 關閉")
    parser.add_argument("--max-hold", type=int, default=0, help="最長持有天數, 0 關閉")
    # 成本
    parser.add_argument("--discount", type=float, default=0.6, help="手續費折扣 (0.6 = 六折)")
    parser.add_argument("--min-fee", type=float, default=20.0, help="單筆最低手續費")
    parser.add_argument("--slippage-bps", type=float, default=10.0, help="滑價 (萬分之)")


def _cfg(args) -> BacktestConfig:
    label = args.stock + "(合成資料)" if getattr(args, "demo", False) else args.stock
    return BacktestConfig(
        stock=label,
        costs=CostModel(fee_discount=args.discount, min_fee=args.min_fee,
                        slippage_bps=args.slippage_bps),
        risk=RiskConfig(initial_capital=args.capital, sizing=args.sizing,
                        risk_pct=args.risk_pct / 100.0, lot_mode=args.lot,
                        stop_atr_mult=args.stop_atr, trail_atr_mult=args.trail_atr,
                        take_profit_r=args.take_profit_r, max_hold_days=args.max_hold),
        start=args.start, end=args.end)


def _bars(args):
    if args.demo:
        from .datafeed import slice_bars
        return slice_bars(synthetic(stock=args.stock, days=1500), args.start, args.end)
    return load(args.stock, args.start, args.end, args.csv)


def _strategy(args):
    return build(args.strategy, **parse_params(args.param))


# --------------------------------------------------------------------------

def cmd_fetch(args) -> int:
    print("抓取 %s 近 %.1f 年日線 ..." % (args.stock, args.years))
    bars = fetch(args.stock, years=args.years, market=args.market)
    print("完成: %d 根日線, 最後一筆 %s 收盤 %.2f" % (len(bars), bars[-1].date, bars[-1].close))
    return 0


def cmd_costcheck(args) -> int:
    cfg = _cfg(args)
    print("\n【小資金第一課】每筆交易的成本結構")
    print(report.cost_table(cfg.costs))
    print("\n重點: 手續費有「單筆最低 %.0f 元」的門檻。" % cfg.costs.min_fee)
    print("      單筆 5,000 元的交易, 來回成本相當於 %.2f%%;" % cfg.costs.breakeven_pct(5_000))
    print("      單筆 50,000 元則只要 %.2f%%。" % cfg.costs.breakeven_pct(50_000))
    print("      這就是小資金做極短線必輸的結構性原因 -- 成本是固定的, 你的獲利卻是等比例縮小的。")
    print("      對策: (1) 單筆金額盡量 >= 2 萬元 (2) 降低進出頻率 (3) 找最低手續費 1 元的券商。\n")
    return 0


def cmd_backtest(args) -> int:
    cfg, bars, strat = _cfg(args), _bars(args), _strategy(args)
    result = run(bars, strat, cfg)
    print(report.text_report(result, cfg))
    if args.chart:
        print("\n權益曲線:")
        print(report.ascii_equity(result.dates, result.equity))
    tag = re.sub(r"[^0-9A-Za-z_\-]+", "_", "%s_%s" % (result.stock, result.strategy)).strip("_").replace("__", "_")
    if args.csv_out:
        print("交易明細: " + report.write_trades_csv(result, os.path.join(OUT_DIR, tag + "_trades.csv")))
    if args.md:
        print("Markdown: " + report.write_markdown(result, cfg, os.path.join(OUT_DIR, tag + ".md")))
    if args.html:
        path = report.write_html(result, cfg, os.path.join(OUT_DIR, tag + ".html"))
        print("HTML 報告: " + path + "   (macOS: open '%s')" % path)
    return 0


def cmd_compare(args) -> int:
    cfg, bars = _cfg(args), _bars(args)
    results = []
    for name in sorted(REGISTRY):
        try:
            results.append(run(bars, build(name), cfg))
        except ValueError as exc:
            print("跳過 %s: %s" % (name, exc))
    print("\n標的 %s   期間 %s ~ %s   資金 %s 元   %s\n"
          % (cfg.stock, bars[0].date, bars[-1].date, "{:,.0f}".format(cfg.risk.initial_capital),
             "零股" if cfg.risk.lot_mode == "odd" else "整股"))
    print(report.compare_table(results))
    best = max(results, key=lambda r: r.metrics["Calmar"])
    print("\n以 Calmar (年化報酬 / 最大回檔) 排序最佳: %s" % best.strategy)
    print(report._verdict(best.metrics))
    print("\n注意: 這只是同一段歷史的「事後最佳」, 請務必再跑 walkforward。")
    return 0


def cmd_optimize(args) -> int:
    cfg, bars = _cfg(args), _bars(args)
    rows = optimize.grid_search(bars, args.strategy, cfg, args.objective, args.min_trades)
    if not rows:
        print("沒有任何參數組合達到最低交易次數 %d, 試著放寬 --min-trades。" % args.min_trades)
        return 1
    print("\n策略 %s  目標函數 %s  (共 %d 組有效參數)\n" % (args.strategy, args.objective, len(rows)))
    print("%-58s %8s %9s %8s %7s %8s" % ("參數", "分數", "年化%", "MDD%", "交易", "期望值R"))
    print("-" * 102)
    for row in rows[:args.top]:
        params = ", ".join("%s=%s" % kv for kv in sorted(row.params.items()))
        print("%-58s %8.2f %9.2f %8.2f %7d %8.2f" % (
            params, row.score, row.metrics["年化報酬率%"], row.metrics["最大回檔%"],
            row.metrics["交易次數"], row.metrics["每筆期望值R"]))
    print("\n警告: 排行榜第一名通常是過度配適的結果。請看整體分佈是否穩定, "
          "並用 walkforward 驗證。")
    return 0


def cmd_walkforward(args) -> int:
    cfg, bars = _cfg(args), _bars(args)
    print("\nWalk-Forward: 訓練 %d 日 -> 測試 %d 日, 目標 %s\n"
          % (args.train, args.test, args.objective))
    wf = optimize.walk_forward(bars, args.strategy, cfg, args.train, args.test, args.objective)
    m = wf.metrics
    print("\n【OOS (未看過的資料) 總成績】")
    for key in ["期間", "視窗數", "總報酬率%", "年化報酬率%", "最大回檔%", "Sharpe", "Calmar"]:
        if key in m:
            print("  %-12s %s" % (key, report._fmt(key, m[key])))
    print("\n判讀: OOS 年化報酬若明顯低於單段回測, 代表那些漂亮數字多半是參數硬湊出來的。")
    return 0


def cmd_signal(args) -> int:
    cfg, bars, strat = _cfg(args), _bars(args), _strategy(args)
    print(format_snapshot(snapshot(bars, strat, cfg)))
    return 0


def cmd_demo(args) -> int:
    args.demo = True
    print("\n[Demo] 使用合成資料驗證整套流程 (不需網路)。真實分析請先執行 fetch。\n")
    cmd_costcheck(args)
    cmd_compare(args)
    args.strategy, args.param = "donchian", []
    args.chart, args.csv_out, args.md, args.html = True, False, False, True
    cmd_backtest(args)
    cmd_signal(args)
    return 0


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="twalpha", description="台股小資金策略回測工具 (預設標的 %s)" % DEFAULT_STOCK,
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="抓取官方日線資料並存成 CSV")
    p.add_argument("--stock", default=DEFAULT_STOCK)
    p.add_argument("--years", type=float, default=5.0)
    p.add_argument("--market", default="auto", choices=["auto", "twse", "tpex"])
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("costcheck", help="檢視交易成本結構 (小資金必看)")
    _common(p, with_strategy=False)
    p.set_defaults(func=cmd_costcheck)

    p = sub.add_parser("backtest", help="回測單一策略")
    _common(p)
    p.add_argument("--html", action="store_true", help="輸出 HTML 報告")
    p.add_argument("--md", action="store_true", help="輸出 Markdown 報告")
    p.add_argument("--csv-out", action="store_true", help="輸出交易明細 CSV")
    p.add_argument("--chart", action="store_true", help="終端機顯示權益曲線")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("compare", help="所有策略同條件比較")
    _common(p, with_strategy=False)
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("optimize", help="參數網格搜尋")
    _common(p)
    p.add_argument("--objective", default="calmar", choices=sorted(optimize.OBJECTIVES))
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--min-trades", type=int, default=5)
    p.set_defaults(func=cmd_optimize)

    p = sub.add_parser("walkforward", help="滾動最佳化 + 樣本外驗證 (最重要的一步)")
    _common(p)
    p.add_argument("--train", type=int, default=500, help="訓練期交易日數")
    p.add_argument("--test", type=int, default=125, help="測試期交易日數")
    p.add_argument("--objective", default="calmar", choices=sorted(optimize.OBJECTIVES))
    p.set_defaults(func=cmd_walkforward)

    p = sub.add_parser("signal", help="今天該做什麼 (含股數與停損價)")
    _common(p)
    p.set_defaults(func=cmd_signal)

    p = sub.add_parser("demo", help="用合成資料跑一次完整流程")
    _common(p)
    p.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, KeyError, RuntimeError) as exc:
        print("錯誤: %s" % exc, file=sys.stderr)
        return 1
