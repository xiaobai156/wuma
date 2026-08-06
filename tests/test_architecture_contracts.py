from __future__ import annotations

import json
import ast
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class DomainAndConfigContractTests(unittest.TestCase):
    def test_domain_models_preserve_current_result_contract(self):
        from kill5.domain import CrawlFailure, CrawlResult, RunStats

        result = CrawlResult(
            url="https://example.test/topic/1",
            name="测试站",
            issue="211",
            numbers=["01", "02", "03", "04", "05"],
        )
        failure = CrawlFailure(
            url=result.url,
            name=result.name,
            reason="指定期数不存在",
        )
        stats = RunStats(
            total_targets=1,
            initial_success=0,
            retry_rescued=1,
            retry_passes_used=1,
            workers=1,
        )

        self.assertEqual(result.issue, "211")
        self.assertEqual(failure.reason, "指定期数不存在")
        self.assertEqual(stats.retry_rescued, 1)

    def test_structured_error_codes_cover_precision_and_storage_failures(self):
        from kill5.errors import ErrorCode

        required = {
            "NETWORK_TIMEOUT",
            "SSL_FAILURE",
            "HTTP_FAILURE",
            "CONTENT_NOT_PUBLISHED",
            "ARTICLE_ID_MISMATCH",
            "ANCHOR_MISSING",
            "ISSUE_NOT_FOUND",
            "KEYWORD_MISMATCH",
            "NUMBER_INVALID",
            "CANDIDATE_CONFLICT",
            "DOCUMENT_BOUNDARY_ERROR",
            "OUTPUT_VALIDATION_FAILED",
            "CACHE_COMMIT_FAILED",
            "LOCAL_NETWORK_POLICY",
        }
        self.assertTrue(required.issubset({item.value for item in ErrorCode}))

    def test_retry_and_failure_category_use_codes_not_reason_text(self):
        from kill5.domain import CrawlFailure
        from kill5.engine import blocked_by_local_socket_policy, is_transient_failure
        from kill5.errors import ErrorCode
        from kill5.failures import failure_category

        timeout = CrawlFailure(
            url="https://example.test",
            name="测试站",
            reason="文案可以完全变化",
            error_code=ErrorCode.NETWORK_TIMEOUT.value,
            retryable=True,
        )
        misleading = CrawlFailure(
            url="https://example.test",
            name="测试站",
            reason="timed out 只是一段普通正文",
            error_code=ErrorCode.NUMBER_INVALID.value,
            retryable=False,
        )
        blocked = CrawlFailure(
            url="https://example.test",
            name="测试站",
            reason="不含任何系统错误文本",
            error_code=ErrorCode.LOCAL_NETWORK_POLICY.value,
        )

        self.assertTrue(is_transient_failure(timeout))
        self.assertEqual(failure_category(timeout), "网络临时失败")
        self.assertFalse(is_transient_failure(misleading))
        self.assertEqual(failure_category(misleading), "其他失败")
        self.assertTrue(blocked_by_local_socket_policy([blocked]))

    def test_missing_issue_and_candidate_conflict_have_stable_codes(self):
        from kill5 import engine
        from kill5.domain import ResolvedContent
        from kill5.errors import CrawlError, ErrorCode
        from kill5.parser import select_candidate

        target = {
            "id": "kill5-test",
            "name": "测试站",
            "url": "https://example.test/topic/1",
            "keywords": ["绝杀五码"],
            "count": 5,
            "region": "top",
            "anchor": "测试站",
            "source_kind": "static_topic",
        }
        content = "测试站\n210期 绝杀五码 01,02,03,04,05"
        with tempfile.TemporaryDirectory() as directory, patch.object(
            engine,
            "fetch_target_content",
            return_value=ResolvedContent(
                name="测试站",
                content=content,
                source_kind="static_topic",
            ),
        ):
            results, failure = engine.crawl_one(
                target,
                ["211"],
                debug_dir=Path(directory),
            )

        self.assertEqual(results, [])
        self.assertIsNotNone(failure)
        self.assertEqual(failure.error_code, ErrorCode.ISSUE_NOT_FOUND.value)

        with self.assertRaises(CrawlError) as raised:
            select_candidate(
                [
                    (["01", "02", "03", "04", "05"], "候选一"),
                    (["06", "07", "08", "09", "10"], "候选二"),
                ],
                strict_ambiguous=True,
                issue="211",
            )
        self.assertEqual(raised.exception.code, ErrorCode.CANDIDATE_CONFLICT)

    def test_issue_mismatch_diagnostics_keep_keyword_and_number_codes_separate(self):
        from kill5.errors import ErrorCode
        from kill5.validator import diagnose_issue_mismatch

        keyword = diagnose_issue_mismatch(
            "211期 其他栏目 01,02,03,04,05",
            ["211"],
            keywords=["绝杀五码"],
            expected_count=5,
        )
        duplicate = diagnose_issue_mismatch(
            "211期 绝杀五码 01,02,03,04,04",
            ["211"],
            keywords=["绝杀五码"],
            expected_count=5,
        )

        self.assertEqual(keyword["211"].error_code, ErrorCode.KEYWORD_MISMATCH)
        self.assertEqual(duplicate["211"].error_code, ErrorCode.NUMBER_INVALID)

    def test_crawl_one_converts_typed_network_exception_without_reading_message(self):
        from http.client import IncompleteRead

        from kill5 import engine
        from kill5.errors import ErrorCode, classify_exception

        target = {
            "id": "kill5-network-test",
            "name": "网络站",
            "url": "https://example.test/topic/1",
            "keywords": ["绝杀五码"],
            "count": 5,
            "region": "top",
            "anchor": "网络站",
            "source_kind": "static_topic",
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            engine,
            "fetch_target_content",
            side_effect=TimeoutError("这段文案不用于分类"),
        ):
            results, failure = engine.crawl_one(
                target,
                ["211"],
                debug_dir=Path(directory),
            )

        self.assertEqual(results, [])
        self.assertEqual(failure.error_code, ErrorCode.NETWORK_TIMEOUT.value)
        self.assertTrue(failure.retryable)

        code, retryable = classify_exception(IncompleteRead(b"partial", 100))
        self.assertEqual(code, ErrorCode.HTTP_FAILURE)
        self.assertTrue(retryable)

    def test_atomic_cache_write_restores_original_on_post_read_failure(self):
        from kill5 import cache

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recent_10_cache.json"
            original = b'{"version": 1, "records": []}\n'
            path.write_bytes(original)
            replacement = {
                "version": 1,
                "generated_at": "2026-07-30T00:00:00",
                "recent_count": 10,
                "records": [],
            }
            real_loads = json.loads
            calls = 0

            def fail_second_read(value):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise ValueError("模拟写后复读失败")
                return real_loads(value)

            with patch.object(cache.json, "loads", side_effect=fail_second_read):
                with self.assertRaisesRegex(ValueError, "模拟写后复读失败"):
                    cache.atomic_write_json(path, replacement)

            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(any(path.parent.glob("*.tmp")))

    def test_shared_config_loader_matches_current_target_order(self):
        import crawler
        import staging_crawler
        from kill5.config import load_targets

        shared = load_targets(ROOT / "targets.json")
        expected = [(item["name"], item["url"]) for item in crawler.TARGETS]

        self.assertEqual([(item["name"], item["url"]) for item in shared], expected)
        self.assertEqual(
            [(item["name"], item["url"]) for item in staging_crawler.TARGETS],
            expected,
        )

    def test_shared_config_loader_rejects_duplicate_names_before_use(self):
        from kill5.config import load_targets

        duplicate = [
            {
                "name": "同名",
                "url": "https://example.test/topic/1",
                "keywords": ["五码"],
                "count": 5,
                "region": "top",
                "anchor": "同名",
            },
            {
                "name": "同名",
                "url": "https://example.test/topic/2",
                "keywords": ["五码"],
                "count": 5,
                "region": "bottom",
                "anchor": "同名",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.json"
            path.write_text(
                json.dumps(duplicate, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "同名目录"):
                load_targets(path, allow_legacy=True)

    def test_parser_and_validator_have_single_shared_implementations(self):
        from kill5 import parser, validator

        migrated_names = set(parser.__all__) | set(validator.__all__)
        for path in (ROOT / "crawler.py", ROOT / "staging_crawler.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            local_functions = {
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            self.assertFalse(
                migrated_names & local_functions,
                f"{path.name} 仍保留共享解析实现",
            )

    def test_request_scope_downloads_same_url_once_across_workers(self):
        from kill5 import network

        calls = 0
        calls_lock = threading.Lock()

        def fake_fetch_bytes(
            url,
            timeout=25,
            encoding=None,
            allow_insecure_tls=False,
        ):
            nonlocal calls
            with calls_lock:
                calls += 1
            return "同一响应".encode("utf-8")

        with patch.object(network, "fetch_bytes", side_effect=fake_fetch_bytes):
            with network.request_scope():
                with ThreadPoolExecutor(max_workers=8) as executor:
                    values = list(
                        executor.map(
                            lambda _index: network.fetch_text(
                                "https://example.test/shared",
                                encoding="utf-8",
                            ),
                            range(8),
                        )
                    )

        self.assertEqual(values, ["同一响应"] * 8)
        self.assertEqual(calls, 1)

    def test_adapter_registry_classifies_all_supported_page_families(self):
        from kill5.adapters.registry import adapter_kind
        from kill5.config import load_targets

        targets = {
            target["name"]: target
            for target in load_targets(ROOT / "targets.json")
        }
        expected = {
            "八方聚宝": "static_topic",
            "天朗气清": "decoded_script",
            "赢遍天下": "dynamic_article",
            "有恃无恐": "dynamic_article",
            "独特王国": "user_page",
            "心若向阳": "list_detail",
            "什么都好": "lottery_article",
        }
        for name, kind in expected.items():
            with self.subTest(name=name):
                self.assertEqual(adapter_kind(targets[name]), kind)

    def test_network_documents_and_adapters_are_shared_once(self):
        from kill5 import documents, network
        from kill5.adapters import (
            dynamic_article,
            list_detail,
            static_topic,
            user_page,
        )

        shared_functions = {
            "fetch_text": network.fetch_text,
            "locate_article_record": documents.locate_article_record,
            "crawl_static_page": static_topic.crawl_static_page,
            "crawl_admin_article_page_with_fallback": (
                dynamic_article.crawl_admin_article_page_with_fallback
            ),
            "crawl_list_detail_page": list_detail.crawl_list_detail_page,
            "crawl_user_page": user_page.crawl_user_page,
        }
        self.assertIs(documents.fetch_text, network.fetch_text)
        self.assertIs(static_topic.fetch_text, network.fetch_text)

        migrated_names = set(shared_functions)
        for path in (ROOT / "crawler.py", ROOT / "staging_crawler.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            local_functions = {
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            self.assertFalse(migrated_names & local_functions)

    def test_crawl_engine_is_shared_and_reuses_response_scope(self):
        from kill5 import engine, network
        from kill5.domain import CrawlResult

        calls = 0
        calls_lock = threading.Lock()

        def fake_fetch_bytes(
            url,
            timeout=25,
            allow_insecure_tls=False,
        ):
            nonlocal calls
            with calls_lock:
                calls += 1
            return b"shared"

        def fake_crawl_one(target, issues, *, debug_dir=None):
            network.fetch_text(target["url"], encoding="utf-8")
            return [
                CrawlResult(
                    target["url"],
                    target["name"],
                    issues[0],
                    ["01", "02", "03", "04", "05"],
                )
            ], None

        targets = [
            (0, {"name": "站一", "url": "https://example.test/shared"}),
            (1, {"name": "站二", "url": "https://example.test/shared"}),
        ]
        with patch.object(network, "fetch_bytes", side_effect=fake_fetch_bytes), patch.object(
            engine,
            "crawl_one",
            side_effect=fake_crawl_one,
        ):
            results, failures = engine.crawl_targets(
                targets,
                ["211"],
                workers=2,
            )

        self.assertEqual(calls, 1)
        self.assertEqual(len(results), 2)
        self.assertEqual(failures, {0: None, 1: None})

    def test_crawl_engine_uses_adapter_registry_for_initial_document(self):
        from kill5 import engine
        from kill5.domain import ResolvedContent

        target = {
            "url": "https://example.test/topic/1",
            "name": "目标站",
            "keywords": ["五码"],
            "count": 5,
            "region": "top",
            "anchor": "目标站",
        }
        resolved = ResolvedContent(
            name="目标站",
            content="目标站\n211期 五码 01,02,03,04,05 开:00准",
            source_kind="static_topic",
        )
        with patch.object(
            engine,
            "fetch_target_content",
            return_value=resolved,
        ) as fetch:
            results, failure = engine.crawl_one(target, ["211"])

        self.assertIsNone(failure)
        self.assertEqual(results[0].numbers, ["01", "02", "03", "04", "05"])
        fetch.assert_called_once_with(target, ["211"], "目标站")

    def test_output_and_cache_repositories_are_shared_and_path_explicit(self):
        from kill5 import cache, output, services

        self.assertIs(
            services.update_recent_duplicate_cache,
            cache.update_recent_duplicate_cache,
        )
        self.assertIs(services.write_outputs, output.write_outputs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results_dir = root / "results"
            report_dir = root / "reports"
            success, failure, report = output.output_files_for_issues(
                ["211"],
                results_dir=results_dir,
                report_dir=report_dir,
            )

            self.assertEqual(Path(success).parent, results_dir)
            self.assertEqual(Path(failure).parent, results_dir)
            self.assertEqual(Path(report).parent, report_dir)
            self.assertFalse(any(root.rglob("*.bak")))

    def test_production_service_does_not_update_cache_when_success_rate_is_zero(self):
        from kill5.domain import CrawlFailure, RunOutcome, RunStats
        from kill5.services import ProductionRunService
        from kill5 import services

        target = {
            "name": "失败站",
            "url": "https://example.test/fail",
            "keywords": ["五码"],
            "count": 5,
            "region": "top",
            "anchor": "失败站",
        }
        outcome = RunOutcome(
            results=[],
            failures=[
                CrawlFailure(
                    target["url"],
                    target["name"],
                    "没有找到指定期数 211期",
                )
            ],
            stats=RunStats(1, 0, 0, 0, 1),
            issues=["211"],
            targets=[target],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = ProductionRunService(
                results_dir=root / "results",
                report_dir=root / "reports",
                debug_dir=root / "debug",
                cache_path=root / "recent_10_cache.json",
            )
            with patch.object(
                services,
                "execute_run",
                return_value=outcome,
            ), patch.object(
                services,
                "update_recent_duplicate_cache",
            ) as update_cache:
                execution = service.run(
                    [target],
                    ["211"],
                    workers=1,
                    retry_passes=0,
                )

            update_cache.assert_not_called()
            self.assertFalse(execution.cache_updated)
            self.assertTrue(Path(execution.failed_file).exists())

    def test_onboarding_same_name_rejects_before_cache_or_network(self):
        from kill5 import onboarding

        existing = [
            {
                "name": "同名站",
                "url": "https://example.test/existing",
                "keywords": ["五码"],
                "count": 5,
                "region": "top",
                "anchor": "同名站",
            }
        ]
        candidate = {
            "name": "同名站",
            "url": "https://example.test/candidate",
            "keywords": ["五码"],
            "count": 5,
            "region": "top",
            "anchor": "同名站",
        }
        with patch.object(onboarding, "crawl_one") as crawl:
            result = onboarding.check_candidate(
                candidate,
                existing,
                cache_path=ROOT / "不存在的缓存.json",
                debug_dir=ROOT / "tests" / "tmp_debug",
            )

        crawl.assert_not_called()
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.stage, "same_name")

    def test_four_entrypoints_are_thin_and_storage_permissions_are_separate(self):
        entries = {
            "crawler.py": 150,
            "staging_crawler.py": 150,
            "onboarding_crawler.py": 100,
            "multi_issue_crawler.py": 100,
        }
        forbidden_definitions = {
            "crawl_one",
            "crawl_targets",
            "extract_issue_numbers",
            "fetch_text",
            "write_outputs",
            "update_recent_duplicate_cache",
        }
        for filename, max_lines in entries.items():
            path = ROOT / filename
            source = path.read_text(encoding="utf-8")
            self.assertLessEqual(
                len(source.splitlines()),
                max_lines,
                f"{filename} 不是薄入口",
            )
            tree = ast.parse(source)
            definitions = {
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            self.assertFalse(forbidden_definitions & definitions)
            self.assertNotIn("kill5.compat", source)
            self.assertNotIn("import *", source)

        multi_source = (ROOT / "kill5" / "multi.py").read_text(encoding="utf-8")
        onboarding_source = (ROOT / "kill5" / "onboarding.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("update_recent_duplicate_cache", multi_source)
        self.assertNotIn("update_recent_duplicate_cache", onboarding_source)
        self.assertFalse((ROOT / "kill5" / "compat.py").exists())

        list_detail_source = (
            ROOT / "kill5" / "adapters" / "list_detail.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("validator", list_detail_source)
        self.assertNotIn("extract_issue_numbers", list_detail_source)

    def test_formal_targets_use_versioned_stable_ids_without_order_change(self):
        from kill5.config import load_targets

        raw = json.loads((ROOT / "targets.json").read_text(encoding="utf-8"))
        self.assertIsInstance(raw, dict)
        self.assertEqual(raw.get("schema_version"), 2)
        self.assertIsInstance(raw.get("targets"), list)

        ids = [target.get("id") for target in raw["targets"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(isinstance(value, str) and value for value in ids))

        runtime = load_targets(ROOT / "targets.json")
        baseline = json.loads(
            (ROOT / "tests" / "fixtures" / "baseline" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(runtime), baseline["target_count"])
        self.assertEqual(
            [(item["name"], item["url"]) for item in runtime],
            [
                (item["name"], item["url"])
                for item in json.loads(
                    (ROOT / "migration" / "targets_v1.json").read_text(
                        encoding="utf-8"
                    )
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
