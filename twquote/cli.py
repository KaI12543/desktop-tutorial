"""命令列進入點：twquote 2330 2317 ^TAIEX"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from typing import List, Sequence

from twquote.models import Quote
from twquote.render import CLEAR, render_table, supports_color
from twquote.sources import MisSource, SourceError

DEFAULT_SYMBOLS = ["^TAIEX", "2330"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="twquote",
        description="台股盤中報價（資料來源：證交所 MIS，約 5 秒一次快照）",
        epilog=(
            "代號寫法：2330（自動判斷上市/上櫃）、6488.two（指定上櫃）、"
            "tse_2330.tw（原始頻道）、^TAIEX / ^OTC（大盤與櫃買指數）"
        ),
    )
    parser.add_argument("symbols", nargs="*", default=[], help="股票代號，可給多個")
    parser.add_argument(
        "-n", "--interval", type=float, default=5.0, help="更新間隔秒數（預設 5）"
    )
    parser.add_argument("-1", "--once", action="store_true", help="只抓一次就結束")
    parser.add_argument("--json", action="store_true", help="輸出 JSON Lines 而非表格")
    parser.add_argument("--no-color", action="store_true", help="關閉顏色")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP 逾時秒數")
    return parser


def order_quotes(symbols: Sequence[str], quotes: Sequence[Quote]) -> List[Quote]:
    """照使用者輸入的順序排，讓畫面不會每次刷新就跳動。"""
    remaining = list(quotes)
    ordered: List[Quote] = []
    for symbol in symbols:
        wanted = symbol.strip().lower().lstrip("^")
        for quote in remaining:
            if quote.code.lower() == wanted or quote.name.lower() == wanted:
                ordered.append(quote)
                remaining.remove(quote)
                break
    return ordered + remaining


def main(argv: Sequence[str] | None = None, source=None) -> int:
    """source 可注入其他 QuoteSource（測試或日後換成券商 API 用）。"""
    args = build_parser().parse_args(argv)
    symbols = args.symbols or DEFAULT_SYMBOLS

    source = source or MisSource(timeout=args.timeout)
    interval = args.interval
    if interval < source.min_interval and not args.once:
        print(
            f"提醒：來源本來就只有 {source.min_interval:.0f} 秒一次快照，"
            f"間隔已調整為 {source.min_interval:.0f} 秒（打太快會被鎖 IP）",
            file=sys.stderr,
        )
        interval = source.min_interval

    color = not args.no_color and supports_color()
    live = not args.once and not args.json
    last_quotes: List[Quote] = []

    while True:
        error = ""
        try:
            quotes = order_quotes(symbols, source.fetch(symbols))
            last_quotes = quotes
        except SourceError as exc:
            if args.once:
                print(f"錯誤：{exc}", file=sys.stderr)
                return 1
            # 盤中偶爾會逾時或被擋，不要讓畫面整個消失，沿用上一筆
            error = str(exc)
            quotes = last_quotes
        except ValueError as exc:  # 代號格式不對，重試也沒用
            print(f"錯誤：{exc}", file=sys.stderr)
            return 2

        if args.json:
            for quote in quotes:
                print(json.dumps(quote.to_dict(), ensure_ascii=False), flush=True)
        else:
            stamp = datetime.now().strftime("%H:%M:%S")
            if error:
                footer = f"{stamp} 取得失敗（{error}），畫面為上一筆快照"
            else:
                footer = f"更新於 {stamp}．每 {interval:.0f} 秒．Ctrl-C 離開"
            output = render_table(quotes, color=color, footer=footer)
            print(f"{CLEAR}{output}" if live else output, flush=True)

        if args.once:
            return 0 if quotes else 1

        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print()
            return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        raise SystemExit(0)
