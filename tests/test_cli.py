import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from twquote.cli import main, order_quotes
from twquote.models import Quote
from twquote.sources import SourceError

TSMC = Quote(code="2330", name="台積電", market="tse", price=1085.0, prev_close=1070.0, time="13:30:00")
HON_HAI = Quote(code="2317", name="鴻海", market="tse", price=200.0, prev_close=210.0, time="13:30:00")


class StubSource:
    min_interval = 5.0

    def __init__(self, quotes=(TSMC, HON_HAI), error=None):
        self.quotes = list(quotes)
        self.error = error
        self.calls = 0

    def fetch(self, symbols):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.quotes)


class OrderTest(unittest.TestCase):
    def test_follows_user_order(self):
        ordered = order_quotes(["2317", "2330"], [TSMC, HON_HAI])
        self.assertEqual([q.code for q in ordered], ["2317", "2330"])

    def test_unrequested_extras_kept_at_end(self):
        ordered = order_quotes(["2330"], [HON_HAI, TSMC])
        self.assertEqual([q.code for q in ordered], ["2330", "2317"])


class MainTest(unittest.TestCase):
    def _run(self, argv, source):
        buffer = io.StringIO()
        with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
            code = main(argv, source=source)
        return code, buffer.getvalue()

    def test_once_prints_table(self):
        code, out = self._run(["--once", "2330"], StubSource())
        self.assertEqual(code, 0)
        self.assertIn("台積電", out)
        self.assertNotIn("\033[H", out)  # 單次輸出不該清畫面

    def test_json_lines(self):
        code, out = self._run(["--once", "--json", "2330", "2317"], StubSource())
        self.assertEqual(code, 0)
        rows = [json.loads(line) for line in out.strip().splitlines()]
        self.assertEqual([r["code"] for r in rows], ["2330", "2317"])
        self.assertAlmostEqual(rows[0]["change"], 15.0)

    def test_source_error_once_exits_nonzero(self):
        code, _ = self._run(["--once", "2330"], StubSource(error=SourceError("boom")))
        self.assertEqual(code, 1)

    def test_bad_symbol_exits_two(self):
        code, _ = self._run(["--once", "2330"], StubSource(error=ValueError("bad")))
        self.assertEqual(code, 2)

    def test_unknown_symbol_returns_one(self):
        code, _ = self._run(["--once", "9999"], StubSource(quotes=[]))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
