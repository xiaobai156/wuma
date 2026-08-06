from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FailureOutputFormatTests(unittest.TestCase):
    def test_failure_file_uses_single_line_fields_from_screenshot_contract(self):
        from kill5.domain import CrawlFailure, RunStats
        from kill5.output import write_outputs

        target = {
            "id": "format-test",
            "name": "燚燚奔月",
            "url": "https://example.test/topic/544148.html",
            "region": "bottom",
            "count": 5,
        }
        failure = CrawlFailure(
            url=target["url"],
            name=target["name"],
            reason="未找到目录关键字",
            error_code="ANCHOR_MISSING",
            stage="target_boundary",
            evidence={"target_id": target["id"], "issues": ["216"]},
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed_file = root / "216期-杀五码-失败.txt"
            write_outputs(
                results=[],
                failures=[failure],
                result_file=str(root / "216期-杀五码-成功.txt"),
                failed_file=str(failed_file),
                report_file=str(root / "216期报告.txt"),
                stats=RunStats(1, 0, 0, 0, 1),
                issues=["216"],
                targets=[target],
            )

            self.assertEqual(
                failed_file.read_text(encoding="utf-8"),
                "失败 燚燚奔月 https://example.test/topic/544148.html "
                "方向: bottom 期数: 216 阶段: 栏目锚点校验 原因: 未找到目录关键字\n",
            )

    def test_multiple_failures_are_separate_lines_without_category_brackets(self):
        from kill5.domain import CrawlFailure
        from kill5.output import format_failure_line

        targets = [
            {
                "id": "first",
                "name": "甲站",
                "url": "https://example.test/first",
                "region": "top",
            },
            {
                "id": "second",
                "name": "乙站",
                "url": "https://example.test/second",
                "region": "bottom",
            },
        ]
        failures = [
            CrawlFailure(
                targets[0]["url"],
                targets[0]["name"],
                "期数不存在",
                error_code="ISSUE_NOT_FOUND",
                stage="issue_validation",
                evidence={"target_id": "first", "issues": ["215", "216"]},
            ),
            CrawlFailure(
                targets[1]["url"],
                targets[1]["name"],
                "方向越界",
                error_code="POSITION_MISMATCH",
                stage="issue_validation",
                evidence={"target_id": "second", "issues": ["216"]},
            ),
        ]

        lines = [format_failure_line(failure, ["215", "216"], targets) for failure in failures]

        self.assertEqual(
            lines,
            [
                "失败 甲站 https://example.test/first 方向: top 期数: 215,216 "
                "阶段: 期数校验 原因: 期数不存在",
                "失败 乙站 https://example.test/second 方向: bottom 期数: 216 "
                "阶段: 方向位置校验 原因: 方向越界",
            ],
        )
        self.assertTrue(all("\n" not in line for line in lines))
        self.assertTrue(all(not line.startswith("[") for line in lines))

    def test_failure_line_has_safe_fallbacks_when_metadata_is_missing(self):
        from kill5.domain import CrawlFailure
        from kill5.output import format_failure_line

        failure = CrawlFailure(
            url="",
            name="",
            reason="",
            evidence={"direction": "bottom", "issues": []},
        )

        self.assertEqual(
            format_failure_line(failure, [], []),
            "失败 未命名 未知URL 方向: bottom 期数: 未指定 阶段: 未标注 原因: 未提供原因",
        )

    def test_output_file_names_keep_empty_and_multi_issue_contracts(self):
        from kill5.output import output_files_for_issues

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty = output_files_for_issues(
                [], results_dir=root / "empty-results", report_dir=root / "empty-reports"
            )
            multiple = output_files_for_issues(
                ["215", "216"],
                results_dir=root / "multi-results",
                report_dir=root / "multi-reports",
            )

        self.assertTrue(empty[0].endswith("当期-杀五码-成功.txt"))
        self.assertTrue(empty[1].endswith("当期-杀五码-失败.txt"))
        self.assertTrue(empty[2].endswith("当期报告.txt"))
        self.assertTrue(multiple[0].endswith("215-216期-杀五码-成功.txt"))
        self.assertTrue(multiple[1].endswith("215-216期-杀五码-失败.txt"))
        self.assertTrue(multiple[2].endswith("215-216期报告.txt"))


if __name__ == "__main__":
    unittest.main()
