"""終端機表格輸出。"""

from __future__ import annotations

import os
import sys
import unicodedata
from typing import List, Optional, Sequence

from twquote.models import Quote

RESET = "\033[0m"
RED = "\033[31m"  # 台股慣例：紅漲綠跌
GREEN = "\033[32m"
DIM = "\033[2m"
BOLD = "\033[1m"
CLEAR = "\033[H\033[J"

COLUMNS: Sequence[tuple] = (
    # (標題, 寬度, 是否靠右)
    ("代號", 6, False),
    ("名稱", 10, False),
    ("成交", 9, True),
    ("漲跌", 8, True),
    ("幅度%", 8, True),
    ("買進", 9, True),
    ("賣出", 9, True),
    ("開", 9, True),
    ("高", 9, True),
    ("低", 9, True),
    ("量(張)", 10, True),
    ("時間", 9, True),
)


def display_width(text: str) -> int:
    """全形字算兩格，不然中文股名一定會把欄位推歪。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1 for ch in text)


def pad(text: str, width: int, right: bool = False) -> str:
    """依顯示寬度補空白；太長就截斷。"""
    while display_width(text) > width:
        text = text[:-1]
    padding = " " * (width - display_width(text))
    return padding + text if right else text + padding


def supports_color(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(stream, "isatty") and stream.isatty()


def _num(value: Optional[float], digits: int = 2) -> str:
    return "-" if value is None else f"{value:,.{digits}f}"


def _quote_color(quote: Quote, color: bool) -> str:
    if not color:
        return ""
    change = quote.change
    if change is None or change == 0:
        return ""
    return RED if change > 0 else GREEN


def format_row(quote: Quote, color: bool = False) -> str:
    change = quote.change
    change_pct = quote.change_pct
    arrow = ""
    if change is not None and change != 0:
        arrow = "▲" if change > 0 else "▼"
    if quote.is_limit_up:
        arrow = "▲✦"
    elif quote.is_limit_down:
        arrow = "▼✦"

    cells = [
        quote.code,
        quote.name,
        _num(quote.price),
        "-" if change is None else f"{arrow}{abs(change):,.2f}",
        "-" if change_pct is None else f"{change_pct:+.2f}",
        _num(quote.bid),
        _num(quote.ask),
        _num(quote.open),
        _num(quote.high),
        _num(quote.low),
        "-" if quote.volume is None else f"{quote.volume:,}",
        quote.time or "-",
    ]
    rendered = [pad(cell, width, right) for cell, (_, width, right) in zip(cells, COLUMNS)]

    tint = _quote_color(quote, color)
    if tint:
        # 只把價格相關的欄位上色，代號名稱維持原色比較好讀
        for index in range(2, 5):
            rendered[index] = f"{tint}{rendered[index]}{RESET}"
    return " ".join(rendered)


def format_header(color: bool = False) -> str:
    header = " ".join(pad(title, width, right) for title, width, right in COLUMNS)
    return f"{BOLD}{header}{RESET}" if color else header


def render_table(quotes: List[Quote], color: bool = False, footer: str = "") -> str:
    lines = [format_header(color)]
    lines.append("-" * display_width(format_header(False)))
    lines.extend(format_row(quote, color) for quote in quotes)
    if footer:
        lines.append(f"{DIM}{footer}{RESET}" if color else footer)
    return "\n".join(lines)
