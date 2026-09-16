"""輸出報表: 終端機文字、Markdown、交易明細 CSV、以及可用瀏覽器開啟的 HTML。"""

from __future__ import annotations

import csv
import html
import json
import os
from typing import Dict, List

from .config import BacktestConfig, CostModel
from .engine import BacktestResult
from .metrics import max_drawdown, monthly_returns

KEY_ORDER = [
    "期間", "交易日數", "期初資金", "期末資金", "總報酬率%", "年化報酬率%",
    "同期買進持有%", "超額報酬%", "最大回檔%", "最長回檔(交易日)", "Calmar",
    "Sharpe", "Sortino", "交易次數", "勝率%", "賺賠比", "獲利因子",
    "每筆期望值R", "每筆期望值元", "平均獲利", "平均虧損", "平均持有天數",
    "總交易成本", "成本佔毛利%", "成本佔本金%", "資金在場比例%", "資金不足錯過訊號",
]
MONEY_KEYS = {"期初資金", "期末資金", "平均獲利", "平均虧損", "總交易成本", "每筆期望值元"}


def _fmt(key: str, val) -> str:
    if isinstance(val, str):
        return val
    if key in MONEY_KEYS:
        return "{:,.0f}".format(val)
    if isinstance(val, float):
        if val in (float("inf"), float("-inf")):
            return "inf"
        return "{:,.2f}".format(val)
    return str(val)


def text_report(result: BacktestResult, cfg: BacktestConfig) -> str:
    m = result.metrics
    lines = []
    lines.append("=" * 62)
    lines.append(" 標的 %s  策略 %s" % (result.stock, result.strategy))
    lines.append(" 參數 %s" % (", ".join("%s=%s" % kv for kv in sorted(result.params.items())) or "(無)"))
    lines.append(" 部位 %s / 單筆風險 %.1f%% / 停損 %.1fATR / 移動停利 %.1fATR"
                 % ("零股" if cfg.risk.lot_mode == "odd" else "整股",
                    cfg.risk.risk_pct * 100, cfg.risk.stop_atr_mult, cfg.risk.trail_atr_mult))
    lines.append("=" * 62)
    for key in KEY_ORDER:
        if key in m:
            lines.append("  %-18s %s" % (key, _fmt(key, m[key])))
    lines.append("-" * 62)
    lines.append(_verdict(m))
    return "\n".join(lines)


def _verdict(m: Dict) -> str:
    """把數字翻成白話, 避免只看到「總報酬率」就衝進場。"""
    notes = []
    if m["交易次數"] < 20:
        notes.append("! 交易樣本只有 %d 筆, 統計上沒有意義, 別把它當成穩定的結論。" % m["交易次數"])
    if m["每筆期望值R"] <= 0:
        notes.append("! 每筆期望值為負 -- 這個策略在這段期間是在慢性失血, 不要上線。")
    if m["成本佔毛利%"] > 30:
        notes.append("! 交易成本吃掉 %.0f%% 的毛利, 對小資金而言太重, 請降低交易頻率或提高單筆金額。"
                     % m["成本佔毛利%"])
    if m["最大回檔%"] > 30:
        notes.append("! 最大回檔 %.0f%%: 100 萬會看到剩 %.0f 萬, 先問自己受不受得了。"
                     % (m["最大回檔%"], 100 * (1 - m["最大回檔%"] / 100)))
    if m["超額報酬%"] < 0:
        notes.append("! 輸給單純買進持有 %.1f%%, 那所有的盯盤與手續費都白費了。" % abs(m["超額報酬%"]))
    if m["資金不足錯過訊號"] > 0:
        notes.append("  (資金不足錯過 %d 次訊號, 表示本金相對股價偏小, 零股模式可緩解。)"
                     % m["資金不足錯過訊號"])
    if not notes:
        notes.append("  數字沒有明顯紅旗, 但仍請務必跑 walkforward 確認不是參數硬湊出來的。")
    return "\n".join(notes)


def compare_table(results: List[BacktestResult]) -> str:
    header = "%-12s %10s %9s %8s %7s %7s %9s %10s" % (
        "策略", "總報酬%", "年化%", "MDD%", "交易", "勝率%", "期望值R", "成本佔毛利%")
    rows = [header, "-" * len(header)]
    for r in sorted(results, key=lambda x: x.metrics["年化報酬率%"], reverse=True):
        m = r.metrics
        rows.append("%-12s %10.2f %9.2f %8.2f %7d %7.1f %9.2f %10.1f" % (
            r.strategy, m["總報酬率%"], m["年化報酬率%"], m["最大回檔%"],
            m["交易次數"], m["勝率%"], m["每筆期望值R"], m["成本佔毛利%"]))
    return "\n".join(rows)


def cost_table(costs: CostModel) -> str:
    """小資金最該先看的一張表: 這筆金額做一趟來回, 要漲幾 % 才打平。"""
    sizes = [3_000, 5_000, 10_000, 20_000, 30_000, 50_000, 100_000, 200_000, 500_000]
    lines = ["單筆金額      手續費(買)  手續費(賣)   證交稅    來回成本   需漲幅才打平",
             "-" * 68]
    for amount in sizes:
        buy, sell = costs.buy_fee(amount), costs.sell_fee(amount)
        tax = costs.sell_tax(amount)
        lines.append("%10s %11.0f %11.0f %9.0f %11.0f %12.2f%%"
                     % ("{:,}".format(amount), buy, sell, tax, buy + sell + tax,
                        costs.breakeven_pct(amount)))
    lines.append("-" * 68)
    lines.append("手續費 %.4f%% x %.2f 折, 單筆最低 %.0f 元; 證交稅 %.2f%%; 滑價假設 %.2f%%(單邊)"
                 % (costs.fee_rate * 100, costs.fee_discount, costs.min_fee,
                    costs.tax_rate * 100, costs.slippage_bps / 100))
    return "\n".join(lines)


def ascii_equity(dates: List[str], equity: List[float], width: int = 70, height: int = 14) -> str:
    if len(equity) < 2:
        return ""
    step = max(1, len(equity) // width)
    sampled = equity[::step]
    lo, hi = min(sampled), max(sampled)
    span = (hi - lo) or 1.0
    grid = [[" "] * len(sampled) for _ in range(height)]
    for x, val in enumerate(sampled):
        y = height - 1 - int((val - lo) / span * (height - 1))
        grid[y][x] = "*"
    out = ["  %s" % "".join(row) for row in grid]
    out.insert(0, "  高 %s" % "{:,.0f}".format(hi))
    out.append("  低 %s   (%s ~ %s)" % ("{:,.0f}".format(lo), dates[0], dates[-1]))
    return "\n".join(out)


def write_trades_csv(result: BacktestResult, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["進場日", "進場價", "股數", "出場日", "出場價", "出場原因",
                    "停損價", "毛損益", "成本", "淨損益", "報酬率%", "R倍數", "持有天數"])
        for t in result.trades:
            w.writerow([t.entry_date, round(t.entry_price, 2), t.shares, t.exit_date,
                        round(t.exit_price, 2), t.exit_reason, round(t.stop_price, 2),
                        round(t.gross_pnl, 0), round(t.costs, 0), round(t.net_pnl, 0),
                        round(t.return_pct, 2), round(t.r_multiple, 2), t.hold_days])
    return path


def write_markdown(result: BacktestResult, cfg: BacktestConfig, path: str) -> str:
    m = result.metrics
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines = ["# %s 回測報告 - %s" % (result.stock, result.strategy), "",
             "參數: `%s`" % (", ".join("%s=%s" % kv for kv in sorted(result.params.items())) or "無"),
             "", "| 指標 | 數值 |", "| --- | ---: |"]
    for key in KEY_ORDER:
        if key in m:
            lines.append("| %s | %s |" % (key, _fmt(key, m[key])))
    lines += ["", "## 解讀", "", "```", _verdict(m), "```", "",
              "## 交易明細 (前 30 筆)", "",
              "| 進場 | 出場 | 股數 | 淨損益 | R | 原因 |", "| --- | --- | ---: | ---: | ---: | --- |"]
    for t in result.trades[:30]:
        lines.append("| %s @%.2f | %s @%.2f | %d | %s | %+.2f | %s |"
                     % (t.entry_date, t.entry_price, t.exit_date, t.exit_price,
                        t.shares, "{:+,.0f}".format(t.net_pnl), t.r_multiple, t.exit_reason))
    lines += ["", "> 本報告僅為歷史資料統計, 不構成投資建議。"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


# --------------------------------------------------------------------------
# HTML 報告 (可直接用瀏覽器開啟; macOS 直接 `open output/xxx.html`)
# --------------------------------------------------------------------------

_HTML_CSS = """
:root { color-scheme: light; --surface: #fcfcfb; --card: #ffffff; --line: #e4e3df;
  --ink: #0b0b0b; --ink-2: #52514e; --ink-3: #8a8880;
  --series-1: #2a78d6; --series-2: #eb6834; --neg: #e34948; --pos: #1baf7a; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  color-scheme: dark; --surface: #1a1a19; --card: #232322; --line: #3a3a37;
  --ink: #ffffff; --ink-2: #c3c2b7; --ink-3: #8f8e85;
  --series-1: #3987e5; --series-2: #d95926; --neg: #e66767; --pos: #199e70; } }
:root[data-theme="dark"] { color-scheme: dark; --surface: #1a1a19; --card: #232322;
  --line: #3a3a37; --ink: #ffffff; --ink-2: #c3c2b7; --ink-3: #8f8e85;
  --series-1: #3987e5; --series-2: #d95926; --neg: #e66767; --pos: #199e70; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface); color: var(--ink);
  font: 15px/1.6 -apple-system, "PingFang TC", "Noto Sans TC", "Helvetica Neue", sans-serif; }
.wrap { max-width: 1040px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 24px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 17px; margin: 40px 0 12px; }
.sub { color: var(--ink-2); font-size: 14px; margin-bottom: 24px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.tile { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }
.tile .k { color: var(--ink-3); font-size: 12px; letter-spacing: .02em; }
.tile .v { font-size: 26px; font-weight: 600; margin-top: 4px; font-variant-numeric: tabular-nums; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 16px; margin-top: 12px; position: relative; }
svg { width: 100%; height: auto; display: block; overflow: visible; }
.legend { display: flex; gap: 18px; flex-wrap: wrap; font-size: 13px; color: var(--ink-2);
  margin-bottom: 8px; }
.legend i { display: inline-block; width: 18px; height: 3px; border-radius: 2px;
  vertical-align: middle; margin-right: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 13px;
  font-variant-numeric: tabular-nums; }
th, td { padding: 7px 8px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { color: var(--ink-3); font-weight: 500; font-size: 12px; }
.scroll { max-height: 420px; overflow: auto; }
.pos { color: var(--pos); } .neg { color: var(--neg); }
.note { background: var(--card); border: 1px solid var(--line); border-left: 3px solid var(--series-2);
  border-radius: 8px; padding: 14px 16px; white-space: pre-wrap; color: var(--ink-2); font-size: 14px; }
#tip { position: absolute; pointer-events: none; opacity: 0; transition: opacity .1s;
  background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px;
  font-size: 12px; box-shadow: 0 6px 20px rgba(0,0,0,.14); font-variant-numeric: tabular-nums; }
footer { margin-top: 48px; color: var(--ink-3); font-size: 12px; }
"""

_HTML_JS = """
const D = window.__CHART__;
const svg = document.getElementById('chart');
const tip = document.getElementById('tip');
const cross = document.getElementById('cross');
const dot1 = document.getElementById('dot1');
const dot2 = document.getElementById('dot2');
function move(evt) {
  const box = svg.getBoundingClientRect();
  const px = (evt.touches ? evt.touches[0].clientX : evt.clientX) - box.left;
  const vx = px / box.width * D.vw;
  let idx = Math.round((vx - D.x0) / D.dx);
  idx = Math.max(0, Math.min(D.n - 1, idx));
  const x = D.x0 + idx * D.dx;
  cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.style.opacity = 1;
  dot1.setAttribute('cx', x); dot1.setAttribute('cy', D.s1[idx]); dot1.style.opacity = 1;
  dot2.setAttribute('cx', x); dot2.setAttribute('cy', D.s2[idx]); dot2.style.opacity = 1;
  tip.innerHTML = '<b>' + D.dates[idx] + '</b><br>' +
    '<span style="color:var(--series-1)">&#9632;</span> 策略 ' + D.v1[idx].toLocaleString() + '<br>' +
    '<span style="color:var(--series-2)">&#9632;</span> 買進持有 ' + D.v2[idx].toLocaleString();
  tip.style.opacity = 1;
  const left = Math.min(box.width - 160, Math.max(8, px + 14));
  tip.style.left = left + 'px';
  tip.style.top = '24px';
}
function hide() { tip.style.opacity = 0; cross.style.opacity = 0; dot1.style.opacity = 0; dot2.style.opacity = 0; }
svg.addEventListener('mousemove', move);
svg.addEventListener('touchmove', move, {passive: true});
svg.addEventListener('mouseleave', hide);
"""


def _downsample(dates, *series, limit: int = 700):
    n = len(dates)
    if n <= limit:
        return [dates] + [list(s) for s in series]
    step = n // limit + 1
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return [[dates[i] for i in idx]] + [[s[i] for i in idx] for s in series]


def _tile(label: str, value: str, cls: str = "") -> str:
    return ('<div class="tile"><div class="k">%s</div><div class="v %s">%s</div></div>'
            % (html.escape(label), cls, html.escape(value)))


def write_html(result: BacktestResult, cfg: BacktestConfig, path: str) -> str:
    m = result.metrics
    dates, eq, bh = _downsample(result.dates, result.equity,
                                result.buy_hold_equity or result.equity)

    vw, vh = 960.0, 340.0
    x0, y0, w, h = 60.0, 14.0, 880.0, 280.0
    lo = min(min(eq), min(bh))
    hi = max(max(eq), max(bh))
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad
    dx = w / max(len(dates) - 1, 1)

    def sy(v):
        return y0 + h - (v - lo) / (hi - lo) * h

    def path_of(vals):
        return "M " + " L ".join("%.1f %.1f" % (x0 + i * dx, sy(v)) for i, v in enumerate(vals))

    ticks = [lo + (hi - lo) * i / 4 for i in range(5)]
    grid = "".join(
        '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="var(--line)" stroke-width="1"/>'
        '<text x="%.1f" y="%.1f" fill="var(--ink-3)" font-size="11" text-anchor="end">%s</text>'
        % (x0, sy(t), x0 + w, sy(t), x0 - 8, sy(t) + 4, "{:,.0f}".format(t)) for t in ticks)
    xlabels = "".join(
        '<text x="%.1f" y="%.1f" fill="var(--ink-3)" font-size="11" text-anchor="middle">%s</text>'
        % (x0 + i * dx, y0 + h + 20, dates[i])
        for i in range(0, len(dates), max(1, len(dates) // 5)))

    end_labels = (
        '<text x="%.1f" y="%.1f" fill="var(--series-1)" font-size="12" font-weight="600">策略</text>'
        '<text x="%.1f" y="%.1f" fill="var(--series-2)" font-size="12" font-weight="600">買進持有</text>'
        % (x0 + w + 6, sy(eq[-1]) + 4, x0 + w + 6, sy(bh[-1]) + 4))

    chart_data = {
        "vw": vw, "x0": x0, "dx": dx, "n": len(dates), "dates": dates,
        "v1": [round(v) for v in eq], "v2": [round(v) for v in bh],
        "s1": [round(sy(v), 1) for v in eq], "s2": [round(sy(v), 1) for v in bh],
    }

    sign = lambda v: "pos" if v > 0 else ("neg" if v < 0 else "")
    tiles = "".join([
        _tile("年化報酬率", "%.1f%%" % m["年化報酬率%"], sign(m["年化報酬率%"])),
        _tile("最大回檔", "%.1f%%" % m["最大回檔%"], "neg"),
        _tile("每筆期望值", "%.2f R" % m["每筆期望值R"], sign(m["每筆期望值R"])),
        _tile("交易次數", "%d 筆" % m["交易次數"]),
        _tile("勝率", "%.0f%%" % m["勝率%"]),
        _tile("超額報酬 (對比買進持有)", "%+.1f%%" % m["超額報酬%"], sign(m["超額報酬%"])),
    ])

    metric_rows = "".join("<tr><td>%s</td><td>%s</td></tr>" % (html.escape(k), html.escape(_fmt(k, m[k])))
                          for k in KEY_ORDER if k in m)
    trade_rows = "".join(
        '<tr><td>%s</td><td>%.2f</td><td>%s</td><td>%.2f</td><td>%s</td><td>%d</td>'
        '<td class="%s">%s</td><td class="%s">%+.2f</td><td>%d</td></tr>'
        % (t.entry_date, t.entry_price, t.exit_date, t.exit_price, html.escape(t.exit_reason),
           t.shares, sign(t.net_pnl), "{:+,.0f}".format(t.net_pnl),
           sign(t.r_multiple), t.r_multiple, t.hold_days)
        for t in result.trades)
    monthly = monthly_returns(result.dates, result.equity)
    month_rows = "".join('<tr><td>%s</td><td class="%s">%+.2f%%</td></tr>' % (ym, sign(v), v)
                         for ym, v in monthly.items())

    params = ", ".join("%s=%s" % kv for kv in sorted(result.params.items())) or "無"
    doc = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(stock)s %(strategy)s 回測報告</title><style>%(css)s</style></head><body><div class="wrap">
<h1>%(stock)s · %(strategy)s 回測報告</h1>
<div class="sub">%(period)s ｜ 參數 %(params)s ｜ 期初資金 %(cap)s 元 ｜ %(lot)s ｜ 單筆風險 %(risk).1f%%</div>
<div class="tiles">%(tiles)s</div>
<h2>權益曲線</h2>
<div class="legend"><span><i style="background:var(--series-1)"></i>策略</span>
<span><i style="background:var(--series-2)"></i>買進持有 (同期同成本)</span></div>
<div class="card"><div id="tip"></div>
<svg id="chart" viewBox="0 0 %(vw)s %(vh)s" role="img" aria-label="策略與買進持有的權益曲線比較">
%(grid)s%(xlabels)s
<line id="cross" x1="0" y1="%(y0)s" x2="0" y2="%(ybot)s" stroke="var(--ink-3)" stroke-width="1" stroke-dasharray="3 3" style="opacity:0"/>
<path d="%(p2)s" fill="none" stroke="var(--series-2)" stroke-width="2" stroke-linejoin="round"/>
<path d="%(p1)s" fill="none" stroke="var(--series-1)" stroke-width="2" stroke-linejoin="round"/>
<circle id="dot1" r="4.5" fill="var(--series-1)" stroke="var(--card)" stroke-width="2" style="opacity:0"/>
<circle id="dot2" r="4.5" fill="var(--series-2)" stroke="var(--card)" stroke-width="2" style="opacity:0"/>
%(endlabels)s</svg></div>
<h2>解讀</h2><div class="note">%(verdict)s</div>
<h2>完整指標</h2><div class="card scroll"><table><tbody>%(metric_rows)s</tbody></table></div>
<h2>交易明細 (%(ntrades)d 筆)</h2><div class="card scroll"><table>
<thead><tr><th>進場日</th><th>進場價</th><th>出場日</th><th>出場價</th><th>原因</th><th>股數</th><th>淨損益</th><th>R</th><th>天數</th></tr></thead>
<tbody>%(trade_rows)s</tbody></table></div>
<h2>月報酬</h2><div class="card scroll"><table><thead><tr><th>月份</th><th>報酬</th></tr></thead><tbody>%(month_rows)s</tbody></table></div>
<footer>本報告由 twalpha 依歷史日線資料產生, 僅供研究與教學使用, 不構成任何投資建議。過去績效不代表未來報酬。</footer>
</div><script>window.__CHART__=%(data)s;</script><script>%(js)s</script></body></html>""" % {
        "stock": html.escape(result.stock), "strategy": html.escape(result.strategy),
        "css": _HTML_CSS, "js": _HTML_JS, "period": html.escape(str(m["期間"])),
        "params": html.escape(params), "cap": "{:,.0f}".format(m["期初資金"]),
        "lot": "零股" if cfg.risk.lot_mode == "odd" else "整股(1000股)",
        "risk": cfg.risk.risk_pct * 100, "tiles": tiles,
        "vw": vw, "vh": vh, "y0": y0, "ybot": y0 + h,
        "grid": grid, "xlabels": xlabels, "endlabels": end_labels,
        "p1": path_of(eq), "p2": path_of(bh),
        "verdict": html.escape(_verdict(m)), "metric_rows": metric_rows,
        "ntrades": len(result.trades), "trade_rows": trade_rows, "month_rows": month_rows,
        "data": json.dumps(chart_data, ensure_ascii=False),
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return path
