"""跨期借值防护回归测试：段落截断、号码前置、开奖号误取。"""
from __future__ import annotations

import unittest

from kill5 import parser


class SegmentBoundaryTests(unittest.TestCase):
    """相邻期标签不规范时，本期段落必须截断。"""

    def test_normal_next_issue(self):
        text = "298期\n绝杀五码 05.12.33.44.49\n297期\n绝杀五码 07.08.09.10.11\n"
        seg = parser.issue_segments(text, "298")[0]
        self.assertNotIn("07.08", seg)
        self.assertEqual(parser.keyword_scoped_number_groups(seg, ["五码"]),
                         [["05", "12", "33", "44", "49"]])

    def test_next_issue_missing_qi_char(self):
        """下一期写成 299: （缺「期」字）也必须截断。"""
        text = "298期\n绝杀五码 05.12.33.44.49\n299: 绝杀五码 31.32.33.34.35\n"
        seg = parser.issue_segments(text, "298")[0]
        self.assertNotIn("31.32", seg)
        self.assertEqual(parser.keyword_scoped_number_groups(seg, ["五码"]),
                         [["05", "12", "33", "44", "49"]])

    def test_next_issue_four_digits(self):
        """下一期四位笔误 1299期 也必须截断。"""
        text = "298期\n绝杀五码 05.12.33.44.49\n1299期 绝杀五码 31.32.33.34.35\n"
        seg = parser.issue_segments(text, "298")[0]
        self.assertNotIn("31.32", seg)

    def test_next_issue_with_di_prefix(self):
        """「第299期」写法也必须截断。"""
        text = "298期\n绝杀五码 05.12.33.44.49\n第299期 绝杀五码 31.32.33.34.35\n"
        seg = parser.issue_segments(text, "298")[0]
        self.assertNotIn("31.32", seg)


class NumberBeforeIssueTests(unittest.TestCase):
    """号码写在期号之前时，不能把它当本期号码。"""

    def test_numbers_above_issue_are_not_taken(self):
        text = (
            "297期\n绝杀五码 07.08.09.10.11\n"
            "298期\n"
            "299期\n绝杀五码 31.32.33.34.35\n"
        )
        seg = parser.issue_segments(text, "298")[0]
        self.assertEqual(parser.keyword_scoped_number_groups(seg, ["五码"]), [])


class OpenNumberTests(unittest.TestCase):
    """开奖号不能被当成本期杀码。"""

    def test_open_numbers_not_used_as_kill_numbers(self):
        text = "298期 绝杀五码 开:13.24.35.46.07"
        self.assertEqual(parser.find_number_groups(text), [])

    def test_kill_numbers_before_open_still_parsed(self):
        text = "298期 绝杀五码 05.12.33.44.49 开:13.24.35.46.07"
        self.assertEqual(parser.find_number_groups(text),
                         [["05", "12", "33", "44", "49"]])

    def test_regular_layout_unchanged(self):
        cases = {
            "298期:绝杀五码【05.12.33.44.49】开:0000准": ["05", "12", "33", "44", "49"],
            "298期:≮绝杀五码≯【13.29.28.25.41】开：猴23准": ["13", "29", "28", "25", "41"],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parser.find_number_groups(text)[0], expected)

    def test_comma_separated_numbers(self):
        """⑥合彩开奖资料写法：逗号分隔 + 「开 ?? 准」在后。"""
        text = "298期 ⑥合彩开奖资料【最新杀码】 : 【04,35,01,06,27】 开 ?? 准"
        self.assertEqual(parser.find_number_groups(text), [["04", "35", "01", "06", "27"]])

    def test_numbers_after_open_marker(self):
        """席不暇暖写法：「开0000准」在前，本期号码在后。"""
        text = "298期【绝杀五码】开0000准 09.06.07.26.24"
        self.assertEqual(parser.find_number_groups(text), [["09", "06", "07", "26", "24"]])

    def test_draw_result_only_returns_nothing(self):
        """整段只有开奖结果时不能返回任何号码。"""
        for text in (
            "298期 绝杀五码 开:龙02准",
            "298期 绝杀五码 开:0000准",
            "298期 绝杀五码 开:?? 准",
        ):
            with self.subTest(text=text):
                self.assertEqual(parser.find_number_groups(text), [])


if __name__ == "__main__":
    unittest.main()
