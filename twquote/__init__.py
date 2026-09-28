"""twquote - 台股盤中報價小工具。

資料來源可抽換：預設使用證交所 MIS（基本市況報導）的 5 秒快照，
之後要換成券商的 tick 推播（例如永豐 Shioaji）只要實作
``twquote.sources.base.QuoteSource`` 再換掉 CLI 的 source 即可。
"""

from twquote.models import Quote

__all__ = ["Quote"]
__version__ = "0.1.0"
