from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kill5 import services
from kill5.domain import CrawlFailure, CrawlResult, RunOutcome, RunStats


ROOT = Path(__file__).resolve().parents[1]


def target(name: str = "测试站", stable_id: str = "kill5-test") -> dict:
    return {
        "id": stable_id,
        "name": name,
        "url": f"https://example.test/{stable_id}",
        "keywords": ["五码"],
        "count": 5,
        "region": "top",
        "anchor": name,
        "source_kind": "static_topic",
    }


def result_for(item: dict, issue: str = "211") -> CrawlResult:
    return CrawlResult(
        item["url"],
        item["name"],
        issue,
        ["01", "02", "03", "04", "05"],
        target_id=item["id"],
    )


def failure_for(item: dict, issue: str = "211") -> CrawlFailure:
    return CrawlFailure(
        item["url"],
        item["name"],
        "没有找到指定期数",
        error_code="ISSUE_NOT_FOUND",
        stage="issue_validation",
        evidence={"target_id": item["id"], "issues": [issue]},
    )


class CacheIsolationTests(unittest.TestCase):
    def test_execute_run_success_and_retry_are_cache_free(self):
        item = target()
        result = result_for(item)
        with patch.object(
            services,
            "crawl_targets",
            return_value=({0: [result]}, {}),
        ) as crawl, patch.object(services, "update_recent_duplicate_cache") as update:
            outcome = services.execute_run(
                [item],
                ["211"],
                workers=1,
                retry_passes=0,
                debug_dir=ROOT / "staging" / "test_debug",
                retry_wait=0,
            )

        self.assertEqual(outcome.results, [result])
        self.assertFalse(outcome.failures)
        crawl.assert_called_once()
        update.assert_not_called()

        failure = CrawlFailure(
            item["url"],
            item["name"],
            "网络临时失败",
            error_code="NETWORK_TIMEOUT",
            retryable=True,
        )
        with patch.object(
            services,
            "crawl_targets",
            side_effect=[({}, {0: failure}), ({0: [result]}, {})],
        ) as crawl, patch.object(
            services,
            "is_transient_failure",
            side_effect=lambda value: value is not None,
        ), patch.object(services.time, "sleep") as sleep:
            outcome = services.execute_run(
                [item],
                ["211"],
                workers=1,
                retry_passes=1,
                debug_dir=ROOT / "staging" / "test_debug",
                retry_wait=0,
            )

        self.assertEqual(outcome.results, [result])
        self.assertFalse(outcome.failures)
        self.assertEqual(crawl.call_count, 2)
        sleep.assert_not_called()

    def test_execute_run_rejects_invalid_engine_outcomes(self):
        item = target()
        valid = result_for(item)
        cases = [
            ([valid], {0: CrawlFailure(item["url"], item["name"], "同时返回")}),
            ([result_for({**item, "id": "wrong-id"})], {}),
            ([result_for(item, "210")], {}),
            ([], {}),
        ]
        for results, failures in cases:
            with self.subTest(results=results, failures=failures):
                with patch.object(
                    services,
                    "crawl_targets",
                    return_value=({0: results} if results else {}, failures),
                ):
                    outcome = services.execute_run(
                        [item],
                        ["211"],
                        workers=1,
                        retry_passes=0,
                        debug_dir=ROOT / "staging" / "test_debug",
                        retry_wait=0,
                    )
                self.assertFalse(outcome.results)
                self.assertEqual(len(outcome.failures), 1)

    def test_production_success_updates_cache_after_outputs(self):
        item = target()
        outcome = RunOutcome(
            results=[result_for(item)],
            failures=[],
            stats=RunStats(1, 1, 0, 0, 1),
            issues=["211"],
            targets=[item],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = services.ProductionRunService(
                results_dir=root / "results",
                report_dir=root / "reports",
                debug_dir=root / "debug",
                cache_path=root / "recent_10_cache.json",
            )
            with patch.object(services, "execute_run", return_value=outcome), patch.object(
                services, "update_recent_duplicate_cache"
            ) as update:
                execution = service.run([item], ["211"], workers=1, retry_passes=0)

            self.assertTrue(execution.cache_updated)
            self.assertIsNone(execution.cache_error)
            update.assert_called_once()
            self.assertEqual(
                Path(execution.result_file).read_text(encoding="utf-8"),
                "01,02,03,04,05 测试站\n",
            )

    def test_production_updates_cache_above_85_percent_and_marks_failures(self):
        items = [target(f"测试站{i}", f"kill5-test-{i}") for i in range(20)]
        outcome = RunOutcome(
            results=[result_for(item) for item in items[:18]],
            failures=[failure_for(item) for item in items[18:]],
            stats=RunStats(20, 18, 0, 0, 1),
            issues=["211"],
            targets=items,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = services.ProductionRunService(
                results_dir=root / "results",
                report_dir=root / "reports",
                debug_dir=root / "debug",
                cache_path=root / "recent_10_cache.json",
            )
            (root / "recent_10_cache.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "generated_at": "",
                        "recent_count": 10,
                        "records": [
                            {
                                "name": item["name"],
                                "url": item["url"],
                                "issue": "211",
                                "numbers": "06,07,08,09,10",
                                "target_id": item["id"],
                            }
                            for item in items[18:]
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch.object(services, "execute_run", return_value=outcome):
                execution = service.run(items, ["211"], workers=1, retry_passes=0)

            self.assertTrue(execution.cache_updated)
            data = json.loads((root / "recent_10_cache.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data["records"]), 18)
            self.assertEqual(
                {record["target_id"] for record in data["records"]},
                {item["id"] for item in items[:18]},
            )
            self.assertEqual(len(data["failures"]), 2)
            self.assertTrue(all(item["status"] == "failed" for item in data["failures"]))
            self.assertEqual(
                {item["target_id"] for item in data["failures"]},
                {item["id"] for item in items[18:]},
            )

    def test_production_does_not_update_cache_at_or_below_85_percent(self):
        items = [target(f"测试站{i}", f"kill5-test-{i}") for i in range(20)]
        outcome = RunOutcome(
            results=[result_for(item) for item in items[:17]],
            failures=[failure_for(item) for item in items[17:]],
            stats=RunStats(20, 17, 0, 0, 1),
            issues=["211"],
            targets=items,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = services.ProductionRunService(
                results_dir=root / "results",
                report_dir=root / "reports",
                debug_dir=root / "debug",
                cache_path=root / "recent_10_cache.json",
            )
            original_cache = json.dumps(
                {
                    "version": 1,
                    "generated_at": "before",
                    "recent_count": 10,
                    "records": [],
                },
                ensure_ascii=False,
            ).encode("utf-8")
            (root / "recent_10_cache.json").write_bytes(original_cache)
            with patch.object(services, "execute_run", return_value=outcome):
                execution = service.run(items, ["211"], workers=1, retry_passes=0)

            self.assertFalse(execution.cache_updated)
            self.assertEqual((root / "recent_10_cache.json").read_bytes(), original_cache)

    def test_multi_issue_production_run_never_updates_cache(self):
        item = target()
        outcome = RunOutcome(
            results=[result_for(item, "210"), result_for(item, "211")],
            failures=[],
            stats=RunStats(1, 1, 0, 0, 1),
            issues=["210", "211"],
            targets=[item],
        )
        self.assertFalse(services.cache_update_allowed(outcome))

    def test_staging_cache_failure_keeps_outputs_and_reports_error(self):
        item = target()
        outcome = RunOutcome(
            results=[result_for(item)],
            failures=[],
            stats=RunStats(1, 1, 0, 0, 1),
            issues=["211"],
            targets=[item],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            production_cache = root / "production.json"
            production_cache.write_text(
                json.dumps(
                    {"version": 1, "generated_at": "", "recent_count": 10, "records": []}
                ),
                encoding="utf-8",
            )
            service = services.StagingRunService(
                results_dir=root / "results",
                debug_dir=root / "debug",
                cache_path=root / "staging_cache.json",
                production_cache_path=production_cache,
            )
            with patch.object(services, "execute_run", return_value=outcome), patch.object(
                services,
                "update_recent_duplicate_cache",
                side_effect=ValueError("隔离缓存失败"),
            ):
                execution = service.run([item], ["211"], workers=1, retry_passes=0)

            self.assertFalse(execution.cache_updated)
            self.assertEqual(execution.cache_error, "缓存更新未完成：隔离缓存失败")
            self.assertEqual(
                Path(execution.result_file).read_text(encoding="utf-8"),
                "01,02,03,04,05 测试站\n",
            )

    def test_staging_helpers_keep_cache_out_of_candidate_selection(self):
        item = target()
        self.assertEqual(
            services.parse_target_names(["站一,站二", "站一，站三"]),
            ["站一", "站二", "站三"],
        )
        self.assertEqual(services.select_existing_targets([item], [item["name"]]), [item])
        candidate = {
            **target("候选站", "kill5-candidate"),
            "url": "https://example.test/candidate",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(json.dumps([candidate], ensure_ascii=False), encoding="utf-8")
            loaded = services.load_staging_candidates(path, [item])
        self.assertEqual(loaded, [candidate])


if __name__ == "__main__":
    unittest.main()
