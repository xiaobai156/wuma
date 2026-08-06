from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def target(name: str = "测试站", stable_id: str = "kill5-test") -> dict:
    return {
        "id": stable_id,
        "name": name,
        "url": f"https://example.test/{stable_id}",
        "keywords": ["绝杀五码"],
        "count": 5,
        "region": "top",
        "anchor": name,
        "source_kind": "static_topic",
    }


class ReleaseBlockerTests(unittest.TestCase):
    def test_region_window_is_fixed_at_three(self):
        from kill5 import config, parser

        self.assertEqual(parser.CANDIDATE_REGION_WINDOW, 3)
        self.assertEqual(config.ISSUE_POSITION_WINDOW, 3)

    def test_configuration_rejects_non_three_region_window(self):
        from kill5.config import load_targets

        configured = target()
        configured["issue_position_window"] = 5
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(
                json.dumps([configured], ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "issue_position_window.*3"):
                load_targets(path, allow_legacy=True)

    def test_active_targets_all_use_three_region_window(self):
        from kill5.config import load_targets

        targets = load_targets(ROOT / "targets.json")
        self.assertEqual(len(targets), 190)
        self.assertTrue(
            all(target.get("issue_position_window") == 3 for target in targets)
        )

    def test_static_documents_cannot_supply_cross_document_numbers(self):
        from kill5.adapters import static_topic
        from kill5.validator import extract_issue_numbers

        root = "https://example.test/root"
        page = '<script src="/a.js"></script><script src="/b.js"></script>'
        responses = {
            root: page,
            "https://example.test/a.js": "211期 绝杀五码",
            "https://example.test/b.js": "01,02,03,04,05 开:00准",
        }
        with patch.object(
            static_topic,
            "fetch_text",
            side_effect=lambda url, **_kwargs: responses[url],
        ):
            _name, content = static_topic.crawl_static_page(root)

        self.assertEqual(
            extract_issue_numbers(
                content,
                ["211"],
                keywords=["绝杀五码"],
                expected_count=5,
                strict_ambiguous=True,
            ),
            {},
        )

    def test_same_document_cross_line_numbers_still_parse(self):
        from kill5.validator import extract_issue_numbers

        content = "211期 绝杀五码\n01,02,03,04,05 开:00准"
        self.assertEqual(
            extract_issue_numbers(
                content,
                ["211"],
                keywords=["绝杀五码"],
                expected_count=5,
                strict_ambiguous=True,
            ),
            {"211": ["01", "02", "03", "04", "05"]},
        )

    def test_region_filters_candidates_before_conflict_detection(self):
        from kill5.validator import extract_issue_numbers

        content = (
            "211期 绝杀五码 01,02,03,04,05 开:00准\n"
            + ("页面说明 " * 80)
            + "\n211期 绝杀五码 06,07,08,09,10 开:00准"
        )
        self.assertEqual(
            extract_issue_numbers(
                content,
                ["211"],
                keywords=["绝杀五码"],
                expected_count=5,
                strict_ambiguous=True,
                region="top",
            ),
            {"211": ["01", "02", "03", "04", "05"]},
        )
        self.assertEqual(
            extract_issue_numbers(
                content,
                ["211"],
                keywords=["绝杀五码"],
                expected_count=5,
                strict_ambiguous=True,
                region="bottom",
            ),
            {"211": ["06", "07", "08", "09", "10"]},
        )

    def test_keyword_without_own_numbers_cannot_borrow_next_column(self):
        from kill5.validator import extract_issue_numbers

        content = "211期\n绝杀五码\n稳杀五码 01,02,03,04,05 开:00准"
        self.assertEqual(
            extract_issue_numbers(
                content,
                ["211"],
                keywords=["绝杀五码"],
                expected_count=5,
                strict_ambiguous=True,
            ),
            {},
        )
        self.assertEqual(
            extract_issue_numbers(
                "211期 绝杀五码 稳杀五码 01,02,03,04,05 开:00准",
                ["211"],
                keywords=["绝杀五码"],
                expected_count=5,
                strict_ambiguous=True,
            ),
            {},
        )

    def test_symbol_keyword_is_matched_literally_not_as_wildcard(self):
        from kill5.validator import extract_issue_numbers

        content = (
            "综合杀碼\n"
            "211期☠ 35 11 47 23 25 ☠开0000\n"
            "210期普通栏目 01 02 03 04 05"
        )
        self.assertEqual(
            extract_issue_numbers(
                content,
                ["211"],
                keywords=["☠"],
                expected_count=5,
                strict_ambiguous=True,
            ),
            {"211": ["35", "11", "47", "23", "25"]},
        )
        self.assertEqual(
            extract_issue_numbers(
                content,
                ["210"],
                keywords=["☠"],
                expected_count=5,
                strict_ambiguous=True,
            ),
            {},
        )

    def test_v2_nested_sections_cannot_override_reserved_identity_fields(self):
        from kill5.config import load_targets

        document = {
            "schema_version": 2,
            "targets": [
                {
                    "id": "kill5-test",
                    "name": "测试站",
                    "url": "https://example.test/right",
                    "region": "top",
                    "count": 5,
                    "source": {"kind": "static_topic"},
                    "parse": {
                        "keywords": ["绝杀五码"],
                        "anchor": "测试站",
                        "url": "https://example.test/wrong",
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.json"
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "保留字段|不允许字段"):
                load_targets(path)

    def test_formal_loader_requires_v2_but_candidate_loader_can_accept_v1(self):
        from kill5.config import load_targets

        legacy = [
            {
                "name": "候选站",
                "url": "https://example.test/candidate",
                "keywords": ["绝杀五码"],
                "count": 5,
                "region": "top",
                "anchor": "候选站",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema_version=2"):
                load_targets(path)
            self.assertEqual(
                load_targets(path, allow_legacy=True)[0]["name"],
                "候选站",
            )

    def test_configuration_cannot_disable_conflict_or_duplicate_number_rules(self):
        from kill5.config import load_targets

        configured = target()
        configured["allow_ambiguous"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(
                json.dumps([configured], ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "allow_ambiguous|歧义"):
                load_targets(path, allow_legacy=True)

        configured = target()
        configured["allow_duplicate_numbers"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(
                json.dumps([configured], ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "allow_duplicate_numbers|重复号码"):
                load_targets(path, allow_legacy=True)

    def test_missing_engine_outcome_becomes_explicit_failure(self):
        from kill5 import services
        from kill5.errors import ErrorCode

        configured = [target()]
        with patch.object(services, "crawl_targets", return_value=({}, {})):
            outcome = services.execute_run(
                configured,
                ["211"],
                workers=1,
                retry_passes=0,
                debug_dir=ROOT / "tests" / "tmp_debug",
                retry_wait=0,
            )

        self.assertEqual(outcome.results, [])
        self.assertEqual(len(outcome.failures), 1)
        self.assertEqual(
            outcome.failures[0].error_code,
            ErrorCode.OUTPUT_VALIDATION_FAILED.value,
        )

    def test_output_validation_rejects_any_missing_target(self):
        from kill5.output import validate_results_before_write

        with self.assertRaisesRegex(ValueError, "缺少|没有成功结果"):
            validate_results_before_write([], ["211"], [target()])

    def test_output_validation_rejects_incomplete_success_failure_vector(self):
        from kill5.domain import CrawlFailure, CrawlResult
        from kill5.output import validate_results_before_write

        first = target("站一", "kill5-one")
        second = target("站二", "kill5-two")
        result = CrawlResult(
            first["url"],
            first["name"],
            "211",
            ["01", "02", "03", "04", "05"],
            target_id=first["id"],
        )
        failure = CrawlFailure(
            first["url"],
            first["name"],
            "网络临时失败",
            error_code="NETWORK_TIMEOUT",
            evidence={"target_id": first["id"], "issues": ["211"]},
        )
        with self.assertRaisesRegex(ValueError, "成功和失败|缺少|完整"):
            validate_results_before_write(
                [result],
                ["211"],
                [first, second],
                failures=[failure],
            )

    def test_cache_rejects_partial_active_target_results(self):
        from kill5.cache import update_recent_duplicate_cache
        from kill5.domain import CrawlResult

        first = target("站一", "kill5-one")
        second = target("站二", "kill5-two")
        result = CrawlResult(
            first["url"],
            first["name"],
            "211",
            ["01", "02", "03", "04", "05"],
            target_id=first["id"],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recent_10_cache.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "generated_at": "",
                        "recent_count": 10,
                        "records": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "结果不完整|缺少"):
                update_recent_duplicate_cache(
                    path,
                    [result],
                    ["211"],
                    active_targets=[first, second],
                )

    def test_formal_output_rejects_result_without_stable_id(self):
        from kill5.domain import CrawlResult
        from kill5.output import validate_results_before_write

        configured = target()
        result = CrawlResult(
            configured["url"],
            configured["name"],
            "211",
            ["01", "02", "03", "04", "05"],
        )
        with self.assertRaisesRegex(ValueError, "target_id"):
            validate_results_before_write([result], ["211"], [configured])

    def test_url_normalization_rejects_equivalent_urls_but_keeps_hash_routes(self):
        from kill5.config import load_targets
        from kill5.identity import canonical_url

        self.assertEqual(
            canonical_url("HTTPS://EXAMPLE.TEST:443/topic/1/?b=2&a=1#section"),
            "https://example.test/topic/1?a=1&b=2",
        )
        self.assertNotEqual(
            canonical_url("https://example.test/#/users/1"),
            canonical_url("https://example.test/#/users/2"),
        )

        first = target("站一", "one")
        second = target("站二", "two")
        first["url"] = "https://example.test:443/topic/1/?b=2&a=1"
        second["url"] = "https://EXAMPLE.test/topic/1?a=1&b=2#section"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.json"
            path.write_text(
                json.dumps([first, second], ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "重复 URL"):
                load_targets(path, allow_legacy=True)

    def test_stable_id_preserves_cache_history_after_url_and_name_change(self):
        from kill5.cache import update_recent_duplicate_cache
        from kill5.domain import CrawlResult

        stable_id = "kill5-stable"
        old_record = {
            "target_id": stable_id,
            "name": "旧名称",
            "url": "https://old.example/topic/1",
            "issue": "210",
            "numbers": "01,02,03,04,05",
        }
        active = {
            **target("新名称", stable_id),
            "url": "https://new.example/topic/1",
        }
        result = CrawlResult(
            active["url"],
            active["name"],
            "211",
            ["06", "07", "08", "09", "10"],
            target_id=stable_id,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recent_10_cache.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "generated_at": "",
                        "recent_count": 10,
                        "records": [old_record],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            update_recent_duplicate_cache(
                path,
                [result],
                ["211"],
                active_targets=[active],
            )
            records = json.loads(path.read_text(encoding="utf-8"))["records"]

        self.assertEqual([item["issue"] for item in records], ["210", "211"])
        self.assertTrue(all(item["target_id"] == stable_id for item in records))
        self.assertTrue(all(item["name"] == "新名称" for item in records))
        self.assertTrue(all(item["url"] == active["url"] for item in records))

    def test_unmapped_legacy_cache_record_fails_closed_after_url_change(self):
        from kill5.cache import update_recent_duplicate_cache
        from kill5.domain import CrawlResult

        stable_id = "kill5-stable"
        active = {
            **target("新名称", stable_id),
            "url": "https://new.example/topic/1",
        }
        result = CrawlResult(
            active["url"],
            active["name"],
            "211",
            ["06", "07", "08", "09", "10"],
            target_id=stable_id,
        )
        original = {
            "version": 1,
            "generated_at": "",
            "recent_count": 10,
            "records": [
                {
                    "name": "旧名称",
                    "url": "https://old.example/topic/1",
                    "issue": "210",
                    "numbers": "01,02,03,04,05",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recent_10_cache.json"
            path.write_text(
                json.dumps(original, ensure_ascii=False),
                encoding="utf-8",
            )
            original_bytes = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "无法映射|稳定 ID"):
                update_recent_duplicate_cache(
                    path,
                    [result],
                    ["211"],
                    active_targets=[active],
                )
            self.assertEqual(path.read_bytes(), original_bytes)

    def test_cache_rollover_keeps_new_low_issue_as_latest(self):
        from kill5.cache import update_recent_duplicate_cache
        from kill5.domain import CrawlResult

        configured = target()
        records = [
            {
                "target_id": configured["id"],
                "name": configured["name"],
                "url": configured["url"],
                "issue": str(issue),
                "numbers": "01,02,03,04,05",
            }
            for issue in range(356, 366)
        ]
        result = CrawlResult(
            configured["url"],
            configured["name"],
            "1",
            ["06", "07", "08", "09", "10"],
            target_id=configured["id"],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recent_10_cache.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "generated_at": "",
                        "recent_count": 10,
                        "records": records,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            update_recent_duplicate_cache(
                path,
                [result],
                ["1"],
                active_targets=[configured],
            )
            updated = json.loads(path.read_text(encoding="utf-8"))["records"]

        self.assertEqual(
            [record["issue"] for record in updated],
            [str(issue) for issue in range(357, 366)] + ["1"],
        )

    def test_current_cache_id_migration_preserves_every_row_in_place(self):
        from kill5.cache import update_recent_duplicate_cache
        from kill5.config import load_targets
        from kill5.domain import CrawlResult

        configured = load_targets(ROOT / "targets.json")
        latest_issue = "211"
        original_records = []
        results = []
        for active in configured:
            numbers = [f"{index:02d}" for index in range(1, active["count"] + 1)]
            original_records.append(
                {
                    "name": active["name"],
                    "url": active["url"],
                    "issue": latest_issue,
                    "numbers": ",".join(numbers),
                }
            )
            results.append(
                CrawlResult(
                    active["url"],
                    active["name"],
                    latest_issue,
                    numbers,
                    target_id=active["id"],
                )
            )

        original = {
            "version": 1,
            "generated_at": "2026-01-01T00:00:00",
            "recent_count": 10,
            "records": original_records,
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recent_10_cache.json"
            path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
            update_recent_duplicate_cache(
                path,
                results,
                [latest_issue],
                active_targets=configured,
            )
            migrated = json.loads(path.read_text(encoding="utf-8"))

        original_rows = [
            (record["name"], record["url"], str(record["issue"]), record["numbers"])
            for record in original["records"]
        ]
        migrated_rows = [
            (record["name"], record["url"], str(record["issue"]), record["numbers"])
            for record in migrated["records"]
        ]
        self.assertEqual(migrated_rows, original_rows)
        self.assertTrue(all(record.get("target_id") for record in migrated["records"]))

    def test_cache_commit_failure_preserves_finalized_formal_outputs(self):
        from kill5 import services
        from kill5.domain import CrawlResult, RunOutcome, RunStats
        from kill5.services import ProductionRunService

        configured = [target()]
        outcome = RunOutcome(
            results=[
                CrawlResult(
                    configured[0]["url"],
                    configured[0]["name"],
                    "211",
                    ["01", "02", "03", "04", "05"],
                    target_id=configured[0]["id"],
                )
            ],
            failures=[],
            stats=RunStats(1, 1, 0, 0, 1),
            issues=["211"],
            targets=configured,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results_dir = root / "results"
            reports_dir = root / "reports"
            results_dir.mkdir()
            reports_dir.mkdir()
            result_file = results_dir / "211期-杀五码-成功.txt"
            failure_file = results_dir / "211期-杀五码-失败.txt"
            report_file = reports_dir / "211期报告.txt"
            cache_file = root / "recent_10_cache.json"
            originals = {
                result_file: b"OLD RESULT\n",
                failure_file: b"OLD FAILURE\n",
                report_file: b"OLD REPORT\n",
                cache_file: b'{"version":1,"records":[]}\n',
            }
            for path, value in originals.items():
                path.write_bytes(value)

            service = ProductionRunService(
                results_dir=results_dir,
                report_dir=reports_dir,
                debug_dir=root / "debug",
                cache_path=cache_file,
            )
            with patch.object(services, "execute_run", return_value=outcome), patch.object(
                services,
                "update_recent_duplicate_cache",
                side_effect=ValueError("模拟缓存提交失败"),
            ):
                execution = service.run(
                    configured,
                    ["211"],
                    workers=1,
                    retry_passes=0,
                )

            self.assertTrue(execution.outcome.results)
            self.assertFalse(execution.outcome.failures)
            self.assertFalse(execution.cache_updated)
            self.assertEqual(execution.cache_error, "缓存更新未完成：模拟缓存提交失败")
            self.assertEqual(
                result_file.read_text(encoding="utf-8"),
                "01,02,03,04,05 测试站\n",
            )
            self.assertFalse(failure_file.exists())
            self.assertNotEqual(report_file.read_bytes(), originals[report_file])
            self.assertEqual(cache_file.read_bytes(), originals[cache_file])

    def test_pending_transaction_is_rolled_back_on_next_start(self):
        from kill5 import storage

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "result.txt"
            journal = root / ".transaction.json"
            output.write_bytes(b"ORIGINAL\n")

            backup_dir = journal.with_name(f"{journal.name}.data")
            storage._snapshot_transaction([output], journal)
            storage.atomic_write_bytes(output, b"PARTIAL NEW\n")
            self.assertEqual(output.read_bytes(), b"PARTIAL NEW\n")
            self.assertTrue(storage.recover_pending_transaction(journal))
            self.assertEqual(output.read_bytes(), b"ORIGINAL\n")
            self.assertFalse(journal.exists())
            self.assertFalse(backup_dir.exists())

    def test_committed_transaction_is_not_rolled_back_when_cleanup_was_interrupted(self):
        from kill5 import storage

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "result.txt"
            journal = root / ".transaction.json"
            output.write_bytes(b"ORIGINAL\n")

            with patch.object(
                storage,
                "_cleanup_transaction_files",
                side_effect=OSError("模拟清理日志时中断"),
            ):
                with self.assertRaisesRegex(OSError, "模拟清理日志时中断"):
                    with storage.file_transaction([output], journal):
                        storage.atomic_write_bytes(output, b"COMMITTED\n")

            self.assertEqual(output.read_bytes(), b"COMMITTED\n")
            self.assertTrue(storage.recover_pending_transaction(journal))
            self.assertEqual(output.read_bytes(), b"COMMITTED\n")
            self.assertFalse(journal.exists())

    def test_output_modes_share_one_transaction_lock(self):
        from kill5.output import output_transaction_journal
        from kill5.storage import atomic_write_bytes, file_transaction

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "211期-杀五码-成功.txt"
            output.write_bytes(b"ORIGINAL\n")
            journal = output_transaction_journal(root)
            first_has_written = threading.Event()
            release_first = threading.Event()
            errors: list[BaseException] = []

            def first_writer() -> None:
                try:
                    with file_transaction([output], journal):
                        atomic_write_bytes(output, b"MULTI-COMMITTED\n")
                        first_has_written.set()
                        release_first.wait(timeout=3)
                except BaseException as exc:
                    errors.append(exc)

            def second_writer() -> None:
                try:
                    first_has_written.wait(timeout=3)
                    with self.assertRaisesRegex(RuntimeError, "FORMAL-FAIL"):
                        with file_transaction([output], journal):
                            atomic_write_bytes(output, b"FORMAL-PARTIAL\n")
                            raise RuntimeError("FORMAL-FAIL")
                except BaseException as exc:
                    errors.append(exc)

            first = threading.Thread(target=first_writer)
            second = threading.Thread(target=second_writer)
            first.start()
            second.start()
            self.assertTrue(first_has_written.wait(timeout=3))
            release_first.set()
            first.join(timeout=5)
            second.join(timeout=5)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(output.read_bytes(), b"MULTI-COMMITTED\n")


if __name__ == "__main__":
    unittest.main()
