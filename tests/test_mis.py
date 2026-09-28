import json
import unittest
from io import BytesIO

from twquote.sources import SourceError
from twquote.sources.mis import MisSource, normalize, parse_payload

SAMPLE = {
    "msgArray": [
        {
            "c": "2330", "n": "台積電", "ex": "tse", "ch": "2330.tw",
            "z": "1085.0000", "y": "1070.0000", "o": "1075.0000",
            "h": "1090.0000", "l": "1072.0000", "u": "1177.0000", "w": "963.0000",
            "v": "38215", "b": "1085.0000_1084.0000_", "a": "1090.0000_1095.0000_",
            "t": "13:30:00", "d": "20260916",
        },
        {
            "c": "6488", "n": "環球晶", "ex": "otc", "ch": "6488.tw",
            "z": "-", "y": "505.0000", "o": "-", "h": "-", "l": "-",
            "v": "0", "b": "498.0000_497.0000_", "a": "499.0000_500.0000_",
            "t": "09:00:00", "d": "20260916",
        },
    ],
    "rtcode": "0000",
    "rtmessage": "OK",
}


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeOpener:
    """記下被要求的 URL，回固定 payload，測試完全不碰網路。"""

    def __init__(self, payload=SAMPLE):
        self.payload = payload
        self.urls = []

    def open(self, url, timeout=None):
        self.urls.append(url)
        return FakeResponse(json.dumps(self.payload).encode("utf-8"))


class NormalizeTest(unittest.TestCase):
    def test_bare_code_tries_both_markets(self):
        self.assertEqual(normalize("2330"), ["tse_2330.tw", "otc_2330.tw"])

    def test_suffixes_pick_market(self):
        self.assertEqual(normalize("2330.tw"), ["tse_2330.tw"])
        self.assertEqual(normalize("6488.two"), ["otc_6488.tw"])
        self.assertEqual(normalize("otc:6488"), ["otc_6488.tw"])

    def test_raw_channel_passes_through(self):
        self.assertEqual(normalize("tse_2330.tw"), ["tse_2330.tw"])
        self.assertEqual(normalize("TSE_2330"), ["tse_2330.tw"])

    def test_index_aliases(self):
        self.assertEqual(normalize("^TAIEX"), ["tse_t00.tw"])
        self.assertEqual(normalize("^otc"), ["otc_o00.tw"])

    def test_empty_symbol_rejected(self):
        with self.assertRaises(ValueError):
            normalize("  ")


class ParseTest(unittest.TestCase):
    def test_parses_numbers_and_best_bid_ask(self):
        first, second = parse_payload(SAMPLE)
        self.assertEqual(first.name, "台積電")
        self.assertEqual(first.price, 1085.0)
        self.assertEqual(first.bid, 1085.0)
        self.assertEqual(first.ask, 1090.0)
        self.assertEqual(first.volume, 38215)
        self.assertAlmostEqual(first.change, 15.0)
        self.assertAlmostEqual(first.change_pct, 15.0 / 1070 * 100)
        self.assertEqual(second.market, "otc")

    def test_dash_becomes_none(self):
        _, second = parse_payload(SAMPLE)
        self.assertIsNone(second.price)
        self.assertIsNone(second.change)
        self.assertIsNone(second.change_pct)

    def test_limit_flags(self):
        quote, _ = parse_payload(SAMPLE)
        self.assertFalse(quote.is_limit_up)
        quote.price = quote.limit_up
        self.assertTrue(quote.is_limit_up)

    def test_error_rtcode_raises(self):
        with self.assertRaises(SourceError):
            parse_payload({"rtcode": "5001", "rtmessage": "查詢過於頻繁"})


class FetchTest(unittest.TestCase):
    def test_first_fetch_asks_both_markets_then_narrows(self):
        opener = FakeOpener()
        source = MisSource(opener=opener)

        source.fetch(["2330", "6488"])
        self.assertIn("tse_2330.tw", opener.urls[0])
        self.assertIn("otc_2330.tw", opener.urls[0])

        source.fetch(["2330", "6488"])
        self.assertIn("tse_2330.tw", opener.urls[1])
        self.assertNotIn("otc_2330.tw", opener.urls[1])
        self.assertIn("otc_6488.tw", opener.urls[1])

    def test_blank_body_raises(self):
        class Blank(FakeOpener):
            def open(self, url, timeout=None):
                return FakeResponse(b"")

        with self.assertRaises(SourceError):
            MisSource(opener=Blank()).fetch(["2330"])


if __name__ == "__main__":
    unittest.main()
