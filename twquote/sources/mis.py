"""證交所 MIS（基本市況報導）快照來源。

端點是 https://mis.twse.com.tw/stock/api/getStockInfo.jsp ，它就是
mis.twse.com.tw 網頁自己在用的那支，沒有對外文件也沒有 SLA，隨時可能改。
它大約每 5 秒更新一次撮合快照，所以再怎麼打也拿不到更細的東西，
打太快只會被鎖 IP —— 真的需要逐筆 tick 請改用券商 API。

注意授權：即時行情自用沒問題，要轉發或商用得先向證交所申請資訊使用授權。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Dict, Iterable, List, Optional, Sequence

from twquote.models import Quote
from twquote.sources.base import SourceError

API_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
INDEX_URL = "https://mis.twse.com.tw/stock/index.jsp"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

# 常見指數的別名，讓使用者不用背 t00 / o00
ALIASES = {
    "taiex": "tse_t00.tw",
    "^taiex": "tse_t00.tw",
    "加權": "tse_t00.tw",
    "加權指數": "tse_t00.tw",
    "otc": "otc_o00.tw",
    "^otc": "otc_o00.tw",
    "櫃買": "otc_o00.tw",
    "櫃買指數": "otc_o00.tw",
}


def _to_float(value: Optional[str]) -> Optional[float]:
    """MIS 用 "-" 表示沒有值（例如試撮時段的成交價）。"""
    if value is None:
        return None
    value = value.strip()
    if not value or value == "-":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _to_int(value: Optional[str]) -> Optional[int]:
    number = _to_float(value)
    return None if number is None else int(number)


def _first_of(value: Optional[str]) -> Optional[float]:
    """五檔欄位長這樣："580.0000_579.0000_..."，取第一檔。"""
    if not value:
        return None
    return _to_float(value.split("_")[0])


def normalize(symbol: str) -> List[str]:
    """把使用者輸入的代號轉成 MIS 的 ex_ch 頻道。

    分不出上市/上櫃時兩個都回傳，MIS 只會回存在的那個，等於順便幫我們查市場別。

        "2330"        -> ["tse_2330.tw", "otc_2330.tw"]
        "2330.tw"     -> ["tse_2330.tw"]
        "6488.two"    -> ["otc_6488.tw"]
        "otc_6488.tw" -> ["otc_6488.tw"]
    """
    raw = symbol.strip()
    if not raw:
        raise ValueError("股票代號不可為空")

    lowered = raw.lower()
    if lowered in ALIASES:
        return [ALIASES[lowered]]

    # 已經是完整頻道就直接用
    if lowered.startswith(("tse_", "otc_")):
        return [lowered if lowered.endswith(".tw") else f"{lowered}.tw"]

    # tse:2330 / otc:6488
    if ":" in lowered:
        market, _, code = lowered.partition(":")
        if market in ("tse", "otc"):
            return [f"{market}_{code}.tw"]

    # yfinance 風格的後綴：.tw = 上市、.two = 上櫃
    if lowered.endswith(".two"):
        return [f"otc_{lowered[:-4]}.tw"]
    if lowered.endswith(".tw"):
        return [f"tse_{lowered[:-3]}.tw"]

    return [f"tse_{lowered}.tw", f"otc_{lowered}.tw"]


def parse_payload(payload: dict) -> List[Quote]:
    """把 getStockInfo.jsp 的 JSON 轉成 Quote 清單。"""
    rtcode = str(payload.get("rtcode", ""))
    if rtcode and rtcode != "0000":
        raise SourceError(
            f"MIS 回傳錯誤 rtcode={rtcode} rtmessage={payload.get('rtmessage', '')}"
        )

    quotes: List[Quote] = []
    for item in payload.get("msgArray", []) or []:
        quotes.append(
            Quote(
                code=item.get("c", ""),
                name=(item.get("n") or "").strip(),
                market=item.get("ex", ""),
                price=_to_float(item.get("z")),
                prev_close=_to_float(item.get("y")),
                open=_to_float(item.get("o")),
                high=_to_float(item.get("h")),
                low=_to_float(item.get("l")),
                limit_up=_to_float(item.get("u")),
                limit_down=_to_float(item.get("w")),
                volume=_to_int(item.get("v")),
                bid=_first_of(item.get("b")),
                ask=_first_of(item.get("a")),
                time=item.get("t", ""),
                date=item.get("d", ""),
                raw=item,
            )
        )
    return quotes


class MisSource:
    """輪詢 MIS 的報價來源。

    會記住每檔代號實際命中的市場別，第二次之後就只問正確的那個頻道，
    少一半的查詢量。
    """

    min_interval = 5.0

    def __init__(self, timeout: float = 10.0, opener=None) -> None:
        self.timeout = timeout
        self._resolved: Dict[str, str] = {}  # 使用者輸入 -> 命中的頻道
        self._opener = opener
        self._primed = opener is not None

    # -- HTTP ---------------------------------------------------------
    def _get_opener(self):
        if self._opener is None:
            self._opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(CookieJar())
            )
            self._opener.addheaders = [
                ("User-Agent", USER_AGENT),
                ("Accept", "application/json, text/plain, */*"),
                ("Referer", INDEX_URL),
            ]
        return self._opener

    def _prime(self) -> None:
        """先打一次首頁拿 cookie，不然 API 常常直接回空的。"""
        if self._primed:
            return
        try:
            self._get_opener().open(INDEX_URL, timeout=self.timeout).read()
        except urllib.error.URLError:
            pass  # 拿不到 cookie 也還是試著問 API
        self._primed = True

    def _request(self, channels: Sequence[str]) -> dict:
        self._prime()
        query = urllib.parse.urlencode(
            {"ex_ch": "|".join(channels), "json": "1", "delay": "0"}
        )
        url = f"{API_URL}?{query}"
        try:
            with self._get_opener().open(url, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise SourceError(f"MIS HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise SourceError(f"連線 MIS 失敗：{exc.reason}") from exc

        if not body.strip():
            raise SourceError("MIS 回了空白內容（通常是被擋，稍後再試或放慢輪詢）")
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise SourceError(f"MIS 回傳非 JSON：{body[:120]!r}") from exc

    # -- QuoteSource --------------------------------------------------
    def fetch(self, symbols: Iterable[str]) -> List[Quote]:
        symbols = list(symbols)
        channels: List[str] = []
        for symbol in symbols:
            known = self._resolved.get(symbol)
            for channel in [known] if known else normalize(symbol):
                if channel not in channels:
                    channels.append(channel)
        if not channels:
            return []

        quotes = parse_payload(self._request(channels))

        # 記住命中的市場別，下一輪就不用兩邊都問
        by_channel = {f"{q.market}_{q.code}.tw": q for q in quotes if q.market and q.code}
        for symbol in symbols:
            if symbol in self._resolved:
                continue
            for channel in normalize(symbol):
                if channel in by_channel:
                    self._resolved[symbol] = channel
                    break
        return quotes
