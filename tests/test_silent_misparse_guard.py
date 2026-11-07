# -*- coding: utf-8 -*-
"""静默取错的三种写法必须变成“取不到”，不能把别行号码当本期杀码。

覆盖：
- 关键词行只有 3 个号码时，不许把下一行（下期预测/开奖号码）凑成 5 个；
- 「开奖号码 …」必须当成开奖内容，不能当杀码；
- 「更新时间 10-14」必须当成日期，不能展开成 5 连号；
- 正常的同 5 码、下一行纯号码、号码跨行换行、开奖标记同 行仍要照常取到。
"""
from __future__ import annotations

import unittest

from kill5.parser import (
    DRAW_MARKER_RE,
    find_number_groups,
    is_number_only_line,
)
from kill5.validator import extract_issue_numbers


def extract(text: str) -> dict[str, list[str]]:
    return extract_issue_numbers(
        text,
        ["310"],
        keywords=["杀五码", "杀5码"],
        expected_count=5,
        position="first",
        strict_ambiguous=True,
        region="top",
    )


class SilentMisparseGuardTests(unittest.TestCase):
    def test_partial_keyword_line_does_not_borrow_next_line(self):
        text = "310期 杀五码 05 12 18\n下期预测 06 07 08 09 10"
        self.assertEqual(extract(text), {})

    def test_draw_number_column_is_not_used_as_kill_numbers(self):
        text = "310期 杀五码：见图\n开奖号码 05 12 20 33 41"
        self.assertEqual(extract(text), {})

    def test_draw_number_column_on_keyword_line_is_split(self):
        text = "310期 杀五码 05 12 18 26 33 开奖号码 05 12 20 33 41"
        self.assertEqual(extract(text)["310"], ["05", "12", "18", "26", "33"])

    def test_date_range_is_not_expanded_into_numbers(self):
        text = "310期 杀五码\n更新时间 10-14"
        self.assertEqual(extract(text), {})

    def test_numbers_on_next_plain_line_still_work(self):
        text = "310期 杀五码\n05 12 18 26 33"
        self.assertEqual(extract(text)["310"], ["05", "12", "18", "26", "33"])

    def test_numbers_wrapped_across_two_lines_still_work(self):
        text = "310期 杀五码 05 12\n18 26 33"
        self.assertEqual(extract(text)["310"], ["05", "12", "18", "26", "33"])

    def test_same_line_five_numbers_still_work(self):
        text = "310期 杀五码 05 12 18 26 33"
        self.assertEqual(extract(text)["310"], ["05", "12", "18", "26", "33"])

    def test_draw_marker_on_same_line_still_works(self):
        text = "310期 杀五码 05 12 18 26 33 开:05 12 20 33 41"
        self.assertEqual(extract(text)["310"], ["05", "12", "18", "26", "33"])

    def test_bracketed_numbers_on_next_line_still_work(self):
        # 卢九：号码写在关键词下一行的方括号里。
        text = "310期:绝杀五码\n[05.15.27.29.41]"
        self.assertEqual(extract(text)["310"], ["05", "15", "27", "29", "41"])

    def test_decorated_numbers_on_next_line_still_work(self):
        # 甜屿晚风 / 鹊巢鸠佔：装饰符包着号码，同 行带开奖标记。
        self.assertEqual(
            extract("310期:绝杀五码\n[18.21.23.46.38]开:鸡46错")["310"],
            ["18", "21", "23", "46", "38"],
        )
        self.assertEqual(
            extract("310期:绝杀五碼\n〓37.34.41.47.39〓开:00准")["310"],
            ["37", "34", "41", "47", "39"],
        )


class NumberOnlyLineTests(unittest.TestCase):
    def test_plain_number_line_is_allowed(self):
        self.assertTrue(is_number_only_line("05 12 18 26 33"))
        self.assertTrue(is_number_only_line("05,12,18.26.33"))
        self.assertTrue(is_number_only_line("05-12-18-26-33"))

    def test_text_line_is_rejected(self):
        self.assertFalse(is_number_only_line("下期预测 06 07 08 09 10"))
        self.assertFalse(is_number_only_line("更新时间 10-14"))

    def test_draw_number_line_contributes_no_numbers(self):
        # 开奖号码行允许保留在扫描范围内，但开奖标记之后的号码不算本期杀码。
        self.assertEqual(find_number_groups("开奖号码 05 12 20 33 41"), [])

    def test_line_with_draw_marker_is_allowed(self):
        self.assertTrue(is_number_only_line("05 12 18 26 33 开:05 12 20 33 41"))

    def test_bracketed_and_decorated_number_lines_are_allowed(self):
        # 卢九 / 甜屿晚风 / 鹊巢鸠佔 的真实排版，方括号和 〓 不算文字。
        self.assertTrue(is_number_only_line("[05.15.27.29.41]"))
        self.assertTrue(is_number_only_line("[18.21.23.46.38]开:鸡46错"))
        self.assertTrue(is_number_only_line("〓37.34.41.47.39〓开:00准"))

    def test_draw_number_marker_is_recognised(self):
        self.assertIsNotNone(DRAW_MARKER_RE.search("开奖号码 05 12 20 33 41"))
        self.assertIsNotNone(DRAW_MARKER_RE.search("开奖结果：05 12 20 33 41"))


class DateAndRangeTests(unittest.TestCase):
    def test_datetime_forms_are_stripped(self):
        self.assertEqual(find_number_groups("更新时间 10-14"), [])
        self.assertEqual(find_number_groups("发布 2026-10-27 19:41"), [])

    def test_bare_range_is_still_a_five_number_group(self):
        self.assertEqual(
            find_number_groups("310期 杀五码 16-20"),
            [["16", "17", "18", "19", "20"]],
        )


class DetectAvailableBudgetTests(unittest.TestCase):
    def test_detect_stops_when_budget_is_exhausted(self):
        from kill5.validator import detect_available_issues

        text = (
            "311期 杀五码 05 12 18 26 33\n"
            "310期 杀五码 06 13 19 27 34\n"
            "309期 杀五码 07 14 20 28 35\n"
        )
        self.assertEqual(len(detect_available_issues(text, keywords=["杀五码"])), 3)
        # 预算为 0 时立即停，不逐期重扫（超大用户页的失败诊断退化保护）。
        self.assertEqual(
            detect_available_issues(text, keywords=["杀五码"], time_budget=0.0),
            [],
        )


if __name__ == "__main__":
    unittest.main()
