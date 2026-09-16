"""日線資料來源: 證交所 / 櫃買中心官方 API, 本機 CSV, 以及離線用的合成資料。

設計原則:
  1. 只用標準庫 (urllib), 不需要安裝任何套件。
  2. 抓下來的資料一律存成 CSV 快取, 重複回測不再打 API (官方有流量限制,
     連續抓取請保持 >3 秒間隔, 本模組預設 3.5 秒)。
  3. 7924 到底掛牌在上市還是上櫃, 程式自己試 -- 先問證交所, 沒有就問櫃買。
"""

from __future__ import annotations

import csv
import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
USER_AGENT = "Mozilla/5.0 (compatible; twalpha/0.1; research use)"
REQUEST_GAP_SEC = 3.5


@dataclass
class Bar:
    date: str      # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float  # 成交股數

    @property
    def dt(self) -> date:
        return datetime.strptime(self.date, "%Y-%m-%d").date()


# --------------------------------------------------------------------------
# 共用工具
# --------------------------------------------------------------------------

def _num(text) -> float:
    """把 '1,234.50' / '--' / '' 轉成 float, 無法解析回傳 nan(以 -1 表示無效)。"""
    if text is None:
        return float("nan")
    s = str(text).replace(",", "").replace("+", "").strip()
    if s in ("", "--", "---", "X", "N/A", "null"):
        return float("nan")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _roc_to_iso(roc: str) -> str:
    """民國日期 '114/09/01' 或 '1140901' -> '2025-09-01'。"""
    s = str(roc).strip().replace("年", "/").replace("月", "/").replace("日", "")
    if "/" in s:
        parts = [p for p in s.split("/") if p != ""]
        y, m, d = parts[0], parts[1], parts[2]
    else:
        y, m, d = s[:-4], s[-4:-2], s[-2:]
    return "%04d-%02d-%02d" % (int(y) + 1911, int(m), int(d))


def _valid(bar: Bar) -> bool:
    vals = [bar.open, bar.high, bar.low, bar.close]
    if any(v != v for v in vals):        # nan
        return False
    return all(v > 0 for v in vals) and bar.high >= bar.low


def _http_json(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8-sig"))


def _month_starts(start: date, end: date):
    cur = date(start.year, start.month, 1)
    while cur <= end:
        yield cur
        cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)


# --------------------------------------------------------------------------
# 證交所 (上市)
# --------------------------------------------------------------------------

def fetch_twse_month(stock: str, month: date) -> list[Bar]:
    url = ("https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
           "?date=%s&stockNo=%s&response=json" % (month.strftime("%Y%m01"), stock))
    payload = _http_json(url)
    if payload.get("stat") != "OK":
        return []
    bars = []
    for row in payload.get("data", []) or []:
        # 日期, 成交股數, 成交金額, 開盤價, 最高價, 最低價, 收盤價, 漲跌價差, 成交筆數
        bar = Bar(_roc_to_iso(row[0]), _num(row[3]), _num(row[4]),
                  _num(row[5]), _num(row[6]), _num(row[1]))
        if _valid(bar):
            bars.append(bar)
    return bars


# --------------------------------------------------------------------------
# 櫃買中心 (上櫃)。官網改版過, 兩個端點都試。
# --------------------------------------------------------------------------

def fetch_tpex_month(stock: str, month: date) -> list[Bar]:
    roc_ym = "%d/%02d" % (month.year - 1911, month.month)
    endpoints = [
        ("new", "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
                "?code=%s&date=%s&id=&response=json" % (stock, month.strftime("%Y/%m/%d"))),
        ("old", "https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/"
                "st43_result.php?l=zh-tw&d=%s&stkno=%s" % (roc_ym, stock)),
    ]
    for _tag, url in endpoints:
        try:
            payload = _http_json(url)
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            continue
        rows = payload.get("tables", [{}])[0].get("data") if "tables" in payload else payload.get("aaData")
        if not rows:
            continue
        bars = []
        for row in rows:
            # 日期, 成交仟股, 成交仟元, 開盤, 最高, 最低, 收盤, 漲跌, 筆數
            bar = Bar(_roc_to_iso(row[0]), _num(row[3]), _num(row[4]),
                      _num(row[5]), _num(row[6]), _num(row[1]) * 1000)
            if _valid(bar):
                bars.append(bar)
        if bars:
            return bars
    return []


# --------------------------------------------------------------------------
# 對外主要介面
# --------------------------------------------------------------------------

def cache_path(stock: str) -> str:
    return os.path.join(CACHE_DIR, "%s_daily.csv" % stock)


def save_csv(bars: list[Bar], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for b in sorted(bars, key=lambda x: x.date):
            writer.writerow([b.date, b.open, b.high, b.low, b.close, b.volume])


def load_csv(path: str) -> list[Bar]:
    """讀取 CSV。欄位名稱容錯 (date/Date/日期, open/Open/開盤價 ...)。"""
    alias = {
        "date": "date", "日期": "date", "時間": "date",
        "open": "open", "開盤價": "open", "開盤": "open",
        "high": "high", "最高價": "high", "最高": "high",
        "low": "low", "最低價": "low", "最低": "low",
        "close": "close", "收盤價": "close", "收盤": "close", "adj close": "close",
        "volume": "volume", "成交股數": "volume", "成交量": "volume",
    }
    bars = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh):
            row = {}
            for key, val in raw.items():
                if key is None:
                    continue
                norm = alias.get(key.strip().lower()) or alias.get(key.strip())
                if norm and norm not in row:
                    row[norm] = val
            if "date" not in row:
                continue
            ds = str(row["date"]).strip()[:10].replace("/", "-")
            if ds and ds[0] in "0123456789" and len(ds.split("-")[0]) == 3:
                ds = _roc_to_iso(ds)
            bar = Bar(ds, _num(row.get("open")), _num(row.get("high")),
                      _num(row.get("low")), _num(row.get("close")), _num(row.get("volume") or 0))
            if _valid(bar):
                bars.append(bar)
    return sorted(bars, key=lambda b: b.date)


def fetch(stock: str, years: float = 5.0, market: str = "auto",
          use_cache: bool = True, verbose: bool = True) -> list[Bar]:
    """抓日線。market: auto | twse | tpex。資料會與既有快取合併。"""
    path = cache_path(stock)
    existing = {b.date: b for b in load_csv(path)} if (use_cache and os.path.exists(path)) else {}

    end = date.today()
    start = end - timedelta(days=int(365.25 * years))
    months = list(_month_starts(start, end))

    order = {"auto": ["twse", "tpex"], "twse": ["twse"], "tpex": ["tpex"]}[market]
    chosen = None
    fetched: dict[str, Bar] = {}

    for idx, month in enumerate(months):
        if idx:
            time.sleep(REQUEST_GAP_SEC)
        got: list[Bar] = []
        for src in ([chosen] if chosen else order):
            try:
                got = fetch_twse_month(stock, month) if src == "twse" else fetch_tpex_month(stock, month)
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                if verbose:
                    print("  ! %s %s 抓取失敗: %s" % (src, month.strftime("%Y-%m"), exc))
                got = []
            if got:
                if chosen is None:
                    chosen = src
                    if verbose:
                        print("  > 判定 %s 掛牌市場: %s" % (stock, "上市(TWSE)" if src == "twse" else "上櫃(TPEx)"))
                break
        for b in got:
            fetched[b.date] = b
        if verbose:
            print("  %s: %d 筆" % (month.strftime("%Y-%m"), len(got)))

    if not fetched and not existing:
        raise RuntimeError(
            "抓不到 %s 的資料。可能是: (1) 網路/防火牆擋住官方網站 "
            "(2) 代號錯誤 (3) 官方 API 改版。請改用 `--csv 你的檔案.csv` 匯入。" % stock)

    existing.update(fetched)
    bars = sorted(existing.values(), key=lambda b: b.date)
    save_csv(bars, path)
    if verbose:
        print("已寫入 %s (共 %d 根日線, %s ~ %s)" % (path, len(bars), bars[0].date, bars[-1].date))
    return bars


def load(stock: str, start: str = "", end: str = "", csv_path: str = "") -> list[Bar]:
    """回測用讀取: 優先讀指定 CSV, 否則讀快取。"""
    path = csv_path or cache_path(stock)
    if not os.path.exists(path):
        raise FileNotFoundError(
            "找不到資料檔 %s。請先執行:  python3 -m twalpha fetch --stock %s\n"
            "若你的網路無法連到證交所/櫃買, 可用 --csv 匯入自行下載的日線檔。" % (path, stock))
    bars = load_csv(path)
    return slice_bars(bars, start, end)


def slice_bars(bars: list[Bar], start: str = "", end: str = "") -> list[Bar]:
    out = bars
    if start:
        out = [b for b in out if b.date >= start]
    if end:
        out = [b for b in out if b.date <= end]
    return out


# --------------------------------------------------------------------------
# 合成資料: 沒有網路時也能驗證回測引擎是否正確
# --------------------------------------------------------------------------

def synthetic(stock: str = "SYN", days: int = 1500, seed: int = 20240101,
              start_price: float = 60.0, annual_drift: float = 0.08,
              annual_vol: float = 0.45) -> list[Bar]:
    """產生有趨勢 + 波段特性的假日線 (小型股等級的高波動)。僅供程式驗證。"""
    rng = random.Random(seed)
    mu = annual_drift / 252.0
    sigma = annual_vol / (252 ** 0.5)
    price = start_price
    regime = 1.0
    bars: list[Bar] = []
    day = date.today() - timedelta(days=int(days * 1.45))
    while len(bars) < days:
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        if rng.random() < 0.012:            # 多空格局切換
            regime = rng.choice([1.6, 1.0, 0.4, -0.8])
        ret = mu * regime + rng.gauss(0, sigma)
        open_ = price * (1 + rng.gauss(0, sigma * 0.3))
        close = max(0.5, open_ * (1 + ret))
        high = max(open_, close) * (1 + abs(rng.gauss(0, sigma * 0.5)))
        low = min(open_, close) * (1 - abs(rng.gauss(0, sigma * 0.5)))
        vol = max(1000, rng.lognormvariate(13.0, 0.6))
        bars.append(Bar(day.isoformat(), round(open_, 2), round(high, 2),
                        round(low, 2), round(close, 2), round(vol)))
        price = close
    return bars
