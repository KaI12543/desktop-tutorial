"""跨資料來源共用的報價結構。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Quote:
    """單一檔股票的一筆快照。

    價格相關欄位一律用 float，取不到就是 None（例如試撮時段還沒有成交價），
    呼叫端要自己處理 None，不要假設一定有值。
    """

    code: str
    name: str = ""
    market: str = ""  # "tse" 或 "otc"
    price: Optional[float] = None  # 最近成交價
    prev_close: Optional[float] = None  # 昨收
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    limit_up: Optional[float] = None
    limit_down: Optional[float] = None
    volume: Optional[int] = None  # 累積成交量（張）
    bid: Optional[float] = None  # 最佳買價
    ask: Optional[float] = None  # 最佳賣價
    time: str = ""  # 撮合時間 HH:MM:SS
    date: str = ""  # YYYYMMDD
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def change(self) -> Optional[float]:
        if self.price is None or self.prev_close is None:
            return None
        return self.price - self.prev_close

    @property
    def change_pct(self) -> Optional[float]:
        change = self.change
        if change is None or not self.prev_close:
            return None
        return change / self.prev_close * 100

    @property
    def is_limit_up(self) -> bool:
        return self.price is not None and self.price == self.limit_up

    @property
    def is_limit_down(self) -> bool:
        return self.price is not None and self.price == self.limit_down

    def to_dict(self) -> dict:
        """輸出成適合 --json 的純量 dict。"""
        return {
            "code": self.code,
            "name": self.name,
            "market": self.market,
            "price": self.price,
            "prev_close": self.prev_close,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "bid": self.bid,
            "ask": self.ask,
            "volume": self.volume,
            "change": self.change,
            "change_pct": self.change_pct,
            "time": self.time,
            "date": self.date,
        }
