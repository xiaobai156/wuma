"""锁定「网络失败自动补抓」默认关闭：不传 --retry-passes 时 CLI 只抓一遍。"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

import crawler
import multi_issue_crawler


class RetryPassesDefaultTests(unittest.TestCase):
    def test_single_entry_defaults_to_zero(self):
        """crawler.py 默认不补抓；--retry-passes 仍可手动覆盖。"""
        self.assertEqual(crawler.DEFAULT_RETRY_PASSES, 0)

    def test_single_entry_passes_zero_to_service(self):
        """不传参数跑单期入口时，service 收到 retry_passes=0。"""
        with patch.object(sys, "argv", ["crawler.py", "--issues", "321"]), \
             patch("crawler.ProductionRunService") as service_cls, \
             patch("crawler.configure_output_encoding"), \
             patch("crawler.parse_issues", return_value=["321"]):
            service = service_cls.return_value
            service.run.return_value = self._fake_execution()
            with patch.object(crawler, "load_targets", return_value=[]):
                try:
                    crawler.main()
                except SystemExit:
                    pass
            if service.run.called:
                kwargs = service.run.call_args.kwargs
                self.assertEqual(kwargs.get("retry_passes"), 0)

    def test_multi_entry_defaults_to_zero(self):
        """多期入口的默认值同样是 0，避免历史期跑批空等补抓。"""
        fake = type("Exec", (), {
            "passed_targets": [], "failed_targets": [], "report_path": "x",
        })()
        with patch.object(sys, "argv", ["multi_issue_crawler.py", "--issues", "321"]), \
             patch("multi_issue_crawler.run_multi_issues", return_value=fake) as runner, \
             patch("multi_issue_crawler.parse_issues", return_value=["321"]), \
             patch("multi_issue_crawler.configure_output_encoding"), \
             patch("multi_issue_crawler.TARGETS", [{"id": "x", "name": "x"}]):
            try:
                multi_issue_crawler.main()
            except SystemExit:
                pass
            self.assertTrue(runner.called)
            self.assertEqual(runner.call_args.kwargs.get("retry_passes"), 0)

    def _fake_execution(self):
        from kill5.domain import RunOutcome, RunStats

        return type("Execution", (), {
            "outcome": RunOutcome([], [], RunStats(0, 0, 0, 0, 1), ["321"], []),
            "result_file": "a",
            "failed_file": "b",
            "report_file": "c",
            "cache_updated": False,
            "cache_error": None,
            "preserved_outputs": True,
        })()


if __name__ == "__main__":
    unittest.main()
