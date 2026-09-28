import unittest

from twquote.models import Quote
from twquote.render import display_width, format_row, pad, render_table

UP = Quote(code="2330", name="台積電", price=1085.0, prev_close=1070.0,
           open=1075.0, high=1090.0, low=1072.0, volume=38215,
           bid=1085.0, ask=1090.0, time="13:30:00")
FLAT = Quote(code="6488", name="環球晶", prev_close=505.0, time="09:00:00")


class WidthTest(unittest.TestCase):
    def test_full_width_counts_two(self):
        self.assertEqual(display_width("台積電"), 6)
        self.assertEqual(display_width("2330"), 4)

    def test_pad_uses_display_width(self):
        self.assertEqual(display_width(pad("台積電", 10)), 10)
        self.assertEqual(display_width(pad("2330", 10, right=True)), 10)

    def test_pad_truncates_overlong(self):
        self.assertLessEqual(display_width(pad("一二三四五六", 6)), 6)

    def test_columns_line_up(self):
        widths = {display_width(format_row(q)) for q in (UP, FLAT)}
        self.assertEqual(len(widths), 1)


class RowTest(unittest.TestCase):
    def test_up_row_marks_direction(self):
        self.assertIn("▲15.00", format_row(UP))
        self.assertIn("+1.40", format_row(UP))

    def test_missing_price_renders_dash_not_crash(self):
        row = format_row(FLAT)
        self.assertIn("6488", row)
        self.assertIn("-", row)

    def test_color_only_when_asked(self):
        self.assertNotIn("\033[", format_row(UP, color=False))
        self.assertIn("\033[31m", format_row(UP, color=True))  # 紅漲

    def test_down_is_green(self):
        down = Quote(code="2317", name="鴻海", price=200.0, prev_close=210.0)
        self.assertIn("\033[32m", format_row(down, color=True))

    def test_table_has_header_and_footer(self):
        table = render_table([UP], footer="更新於 13:30:05")
        self.assertIn("代號", table)
        self.assertIn("更新於 13:30:05", table)


if __name__ == "__main__":
    unittest.main()
