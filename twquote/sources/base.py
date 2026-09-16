"""資料來源的共同介面。"""

from __future__ import annotations

from typing import Iterable, List, Protocol, runtime_checkable

from twquote.models import Quote


class SourceError(RuntimeError):
    """資料來源回了錯誤或格式不符預期。"""


@runtime_checkable
class QuoteSource(Protocol):
    """任何報價來源只要實作這兩個東西就能接進 CLI。"""

    #: 建議的最短輪詢間隔（秒），CLI 會用它當 --interval 的下限
    min_interval: float

    def fetch(self, symbols: Iterable[str]) -> List[Quote]:
        """取得這批代號的最新快照，順序不保證與輸入相同。"""
        ...
