import base64
import json
import ssl
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kill5 import cache, config, documents, engine, network, parser, validator
from kill5.adapters import dynamic_article, static_topic, user_page
from kill5.domain import CrawlResult, ResolvedContent


ROOT = Path(__file__).resolve().parent
TARGETS = config.load_targets(ROOT / "targets.json")
CORE_API = SimpleNamespace(
    __name__="kill5.shared_core",
    all_issue_segment_matches=parser.all_issue_segment_matches,
    article_api_response_matches_id=documents.article_api_response_matches_id,
    crawl_one=engine.crawl_one,
    crawl_static_page=static_topic.crawl_static_page,
    ensure_same_origin=network.ensure_same_origin,
    extract_issue_numbers=validator.extract_issue_numbers,
    fetch_bytes=network.fetch_bytes,
    filter_candidates_by_region=parser.filter_candidates_by_region,
    html_to_text=parser.html_to_text,
    issue_position_window_starts=parser.issue_position_window_starts,
    load_targets=config.load_targets,
    select_decoded_anchor_parts=parser.select_decoded_anchor_parts,
    target_allows_insecure_tls=config.target_allows_insecure_tls,
    validate_rendered_article_identity=(
        dynamic_article.validate_rendered_article_identity
    ),
)
PARSER_MODULES = (CORE_API,)


class SafetyRegressionTests(unittest.TestCase):
    def test_script_documents_keep_dom_order(self):
        page = '<script src="/upload/script/a.js"></script><script src="/upload/script/b.js"></script>'

        def fake_fetch(url, encoding=None, timeout=25):
            if url.endswith("/root"):
                return page
            if url.endswith("a.js"):
                time.sleep(0.05)
                return "A-DOCUMENT"
            if url.endswith("b.js"):
                return "B-DOCUMENT"
            raise AssertionError(url)

        with patch.object(static_topic, "fetch_text", side_effect=fake_fetch):
            _name, combined = static_topic.crawl_static_page("https://unit.test/root")

        self.assertLess(combined.index("A-DOCUMENT"), combined.index("B-DOCUMENT"))
        self.assertIs(CORE_API.crawl_static_page, static_topic.crawl_static_page)

    def test_decoded_anchor_reads_until_required_stop_boundary(self):
        root = "https://unit.test/root"
        script_url = "https://unit.test/upload/script/data.js"
        page = '<script src="/upload/script/data.js"></script>'
        chunks = [
            "目标作者 发表于",
            "208期:[稳杀五码](01.02.03.04.05)",
            "209期:[稳杀五码](06.07.08.09.10)",
            ".down9898 正文结束",
            "210期:[稳杀五码](11.12.13.14.15) 其他资料",
        ]
        script = "\n".join(
            f'strdecode("{base64.b64encode(chunk.encode()).decode()}")'
            for chunk in chunks
        )

        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                def fake_fetch(url, encoding=None, timeout=25):
                    if url == root:
                        return page
                    if url == script_url:
                        return script
                    raise AssertionError(url)

                with patch.object(static_topic, "fetch_text", side_effect=fake_fetch):
                    _name, combined = module.crawl_static_page(
                        root,
                        decoded_anchor_only="目标作者 发表于",
                        decoded_stop_anchor=".down9898",
                    )

                self.assertIn("209期", combined)
                self.assertNotIn("210期", combined)

    def test_decoded_anchor_missing_required_stop_fails_closed(self):
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                with self.assertRaisesRegex(ValueError, "没有找到解码正文结束锚点"):
                    module.select_decoded_anchor_parts(
                        ["目标作者 发表于", "209期 五码 01,02,03,04,05"],
                        "目标作者 发表于",
                        1,
                        stop_anchor=".down9898",
                    )

    def test_decoded_anchor_can_read_to_end_for_dedicated_script(self):
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                selected = module.select_decoded_anchor_parts(
                    ["noise", "作者锚点", "210期 数据", "211期 数据"],
                    "作者锚点",
                    1,
                    to_end=True,
                )
                self.assertEqual(
                    selected,
                    ["作者锚点", "210期 数据", "211期 数据"],
                )

    def test_jiuchengyanran_includes_chunk_41_and_keeps_current_bottom_window(self):
        target = {target["name"]: target for target in TARGETS}["旧城俨然"]
        decoded_parts = [
            "提高速度,减少浏览流量,不保留大量往期记录!",
            "217期:[绝杀5码] 24.18.46.22.30",
            *(["无关解码分片"] * 35),
            "001期:[绝杀5码] 01.02.03.04.05\n214期:[绝杀5码] 03.15.10.27.46",
            "215期:[绝杀5码] 15.38.12.29.42",
            "216期:[绝杀5码] 18.01.13.26.44",
            "217期:[绝杀5码] 28.06.11.31.33",
        ]
        selected = parser.select_decoded_anchor_parts(
            decoded_parts,
            target["decoded_anchor_only"],
            target["decoded_anchor_chunks"],
        )
        content = "\n".join(selected)
        common = {
            "keywords": target["keywords"],
            "expected_count": target["count"],
            "strict_ambiguous": True,
            "allow_duplicate_numbers": False,
            "anchor": target["anchor"],
            "region": target["region"],
            "issue_position_window": target["issue_position_window"],
        }

        self.assertEqual(
            validator.extract_issue_numbers(content, ["216"], **common),
            {"216": ["18", "01", "13", "26", "44"]},
        )
        self.assertEqual(
            validator.extract_issue_numbers(content, ["217"], **common),
            {"217": ["28", "06", "11", "31", "33"]},
        )
        self.assertEqual(
            validator.extract_issue_numbers(content, ["214"], **common),
            {},
        )
        self.assertEqual(
            validator.extract_issue_numbers(content, ["218"], **common),
            {},
        )

    def test_multi_issue_run_requires_every_requested_issue(self):
        target = {
            "url": "https://unit.test/topic/1",
            "name": "测试站",
            "keywords": ["五码"],
            "count": 5,
            "region": "top",
            "anchor": "测试站",
        }
        content = "测试站\n200期 五码 01,02,03,04,05 开:00准"
        with patch.object(
            engine,
            "fetch_target_content",
            return_value=ResolvedContent("测试站", content, "static_topic"),
        ):
            results, failure = engine.crawl_one(target, ["199", "200"])

        self.assertEqual(results, [])
        self.assertIsNotNone(failure)
        self.assertIn("199期", failure.reason)

    def test_old_manual_issue_does_not_evict_newer_cache_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            records = [
                {"name": "站", "url": "u", "issue": str(issue), "numbers": "01,02,03,04,05"}
                for issue in range(191, 201)
            ]
            path.write_text(
                json.dumps(
                    {"version": 1, "generated_at": "", "recent_count": 10, "records": records}
                ),
                encoding="utf-8",
            )
            cache.update_recent_duplicate_cache(
                path,
                [CrawlResult("u", "站", "195", ["06", "07", "08", "09", "10"])],
                ["195"],
            )
            issues = {
                int(record["issue"])
                for record in json.loads(path.read_text(encoding="utf-8"))["records"]
            }

        self.assertEqual(issues, set(range(191, 201)))

    def test_malformed_cache_is_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            original = json.dumps({"version": 1, "records": "BROKEN"})
            path.write_text(original, encoding="utf-8")
            with self.assertRaises(ValueError):
                cache.update_recent_duplicate_cache(
                    path,
                    [CrawlResult("u", "站", "200", ["01", "02", "03", "04", "05"])],
                    ["200"],
                )
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_config_requires_name_keywords_and_region(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.json"
            path.write_text(json.dumps([{"url": "https://unit.test", "count": 5}]), encoding="utf-8")
            with self.assertRaises(ValueError):
                config.load_targets(path, allow_legacy=True)

    def test_insecure_tls_is_limited_to_the_configured_exception(self):
        allowed = {
            "name": "倦鸟归林",
            "url": "https://nlafoq9v.dh5565656.xyz/bbs/topic.php?id=918",
            "insecure_tls": True,
        }
        rejected = {**allowed, "name": "非例外站"}
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                self.assertTrue(module.target_allows_insecure_tls(allowed))
                self.assertFalse(module.target_allows_insecure_tls(rejected))
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "targets.json"
                    path.write_text(
                        json.dumps(
                            [
                                {
                                    "url": "https://unit.test/topic/1",
                                    "name": "非例外站",
                                    "keywords": ["五码"],
                                    "count": 5,
                                    "region": "bottom",
                                    "insecure_tls": True,
                                }
                            ],
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, "不允许关闭 TLS"):
                        module.load_targets(path, allow_legacy=True)

    def test_fetch_bytes_uses_unverified_context_only_when_explicit(self):
        from kill5 import network

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return "https://unit.test/page"

            def read(self):
                return b"ok"

        for module in PARSER_MODULES:
            self.assertIs(module.fetch_bytes, network.fetch_bytes)

        with patch.object(network, "wait_for_host_slot"), patch.object(
            network, "urlopen", return_value=FakeResponse()
        ) as mocked_urlopen:
            self.assertEqual(network.fetch_bytes("https://unit.test/page"), b"ok")
            normal_context = mocked_urlopen.call_args.kwargs["context"]

            self.assertEqual(
                network.fetch_bytes(
                    "https://unit.test/page", allow_insecure_tls=True
                ),
                b"ok",
            )
            exception_context = mocked_urlopen.call_args.kwargs["context"]

        self.assertIs(normal_context, network.SSL_CONTEXT)
        self.assertIsNot(exception_context, network.SSL_CONTEXT)
        self.assertFalse(exception_context.check_hostname)
        self.assertEqual(exception_context.verify_mode, ssl.CERT_NONE)

    def test_insecure_tls_does_not_extend_to_external_scripts(self):
        root = "https://unit.test/root"
        same_origin_script = "https://unit.test/upload/script/same.js"
        external_script = "https://cdn.unit.test/upload/script/external.js"
        page = (
            '<script src="/upload/script/same.js"></script>'
            '<script src="https://cdn.unit.test/upload/script/external.js"></script>'
        )
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                flags = {}

                def fake_fetch(url, encoding=None, timeout=25, allow_insecure_tls=False):
                    flags[url] = allow_insecure_tls
                    if url == root:
                        return page
                    if url in {same_origin_script, external_script}:
                        return "SCRIPT"
                    raise AssertionError(url)

                with patch.object(static_topic, "fetch_text", side_effect=fake_fetch):
                    module.crawl_static_page(root, allow_insecure_tls=True)

                self.assertTrue(flags[root])
                self.assertTrue(flags[same_origin_script])
                self.assertFalse(flags[external_script])

    def test_clean_name_does_not_remove_real_suffix(self):
        self.assertEqual(parser.clean_name("越来越准"), "越来越准")

    def test_dynamic_article_rejects_wrong_url_record_id(self):
        target = {
            "url": "https://unit.test/article/manager/target-record-id?url=x",
            "name": "目标目录",
            "keywords": ["绝杀五码"],
            "count": 5,
            "region": "bottom",
            "anchor": "目标目录",
            "first_issue_chain": True,
        }
        wrong_record = {
            "id": "other-record-id",
            "authorNickname": "目标目录",
            "title": "目标目录",
            "html": "目标目录\n200期 绝杀五码 01,02,03,04,05",
        }
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                with patch.object(dynamic_article, "fetch_json", return_value=wrong_record), patch.object(
                    engine, "save_debug_page", return_value=None
                ):
                    results, failure = module.crawl_one(target, ["200"])

                self.assertEqual(results, [])
                self.assertIsNotNone(failure)
                self.assertIn("文章 ID", failure.reason)

    def test_dynamic_article_selects_nested_exact_id_not_same_issue_decoy(self):
        target = {
            "url": "https://unit.test/article/manager/target-record-id?url=x",
            "name": "目标目录",
            "keywords": ["绝杀五码"],
            "count": 5,
            "region": "bottom",
            "anchor": "目标目录",
            "first_issue_chain": True,
        }
        payload = {
            "records": [
                {
                    "id": "decoy-record-id",
                    "authorNickname": "目标目录",
                    "title": "目标目录",
                    "html": "目标目录\n200期 绝杀五码 11,12,13,14,15",
                },
                {
                    "id": "target-record-id",
                    "authorNickname": "目标目录",
                    "title": "目标目录",
                    "html": "目标目录\n200期 绝杀五码\n01,\n02,\n03,\n04,\n05",
                },
            ]
        }
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                with patch.object(dynamic_article, "fetch_json", return_value=payload), patch.object(
                    engine, "save_debug_page", return_value=None
                ):
                    results, failure = module.crawl_one(target, ["200"])

                self.assertIsNone(failure)
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].numbers, ["01", "02", "03", "04", "05"])

    def test_dynamic_article_rejects_duplicate_id_and_cross_record_body(self):
        target = {
            "url": "https://unit.test/article/manager/target-record-id?url=x",
            "name": "目标目录",
            "keywords": ["绝杀五码"],
            "count": 5,
            "region": "bottom",
            "anchor": "目标目录",
            "first_issue_chain": True,
        }
        duplicate_id = {
            "records": [
                {
                    "id": "target-record-id",
                    "authorNickname": "目标目录",
                    "title": "目标目录",
                    "html": "目标目录\n200期 绝杀五码 01,02,03,04,05",
                },
                {
                    "id": "target-record-id",
                    "authorNickname": "目标目录",
                    "title": "目标目录",
                    "html": "目标目录\n200期 绝杀五码 11,12,13,14,15",
                },
            ]
        }
        cross_record_body = {
            "records": [
                {
                    "id": "target-record-id",
                    "authorNickname": "目标目录",
                    "title": "目标目录 200期 绝杀五码",
                    "html": "",
                },
                {
                    "id": "decoy-record-id",
                    "authorNickname": "目标目录",
                    "title": "目标目录",
                    "html": "01,02,03,04,05",
                },
            ]
        }
        cases = ((duplicate_id, "多个相同文章 ID"), (cross_record_body, "缺少正文"))
        for module in PARSER_MODULES:
            for payload, expected_reason in cases:
                with self.subTest(module=module.__name__, expected_reason=expected_reason):
                    with patch.object(dynamic_article, "fetch_json", return_value=payload), patch.object(
                        engine, "save_debug_page", return_value=None
                    ):
                        results, failure = module.crawl_one(target, ["200"])

                    self.assertEqual(results, [])
                    self.assertIsNotNone(failure)
                    self.assertIn(expected_reason, failure.reason)

    def test_dynamic_article_rejects_author_mismatch(self):
        target = {
            "url": "https://unit.test/article/manager/target-record-id?url=x",
            "name": "目标目录",
            "keywords": ["绝杀五码"],
            "count": 5,
            "region": "bottom",
            "anchor": "目标目录",
            "first_issue_chain": True,
        }
        payload = {
            "id": "target-record-id",
            "authorNickname": "其他作者",
            "title": "目标目录",
            "html": "目标目录\n200期 绝杀五码 01,02,03,04,05",
        }
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                with patch.object(dynamic_article, "fetch_json", return_value=payload), patch.object(
                    engine, "save_debug_page", return_value=None
                ):
                    results, failure = module.crawl_one(target, ["200"])

                self.assertEqual(results, [])
                self.assertIsNotNone(failure)
                self.assertIn("作者不匹配", failure.reason)

    def test_browser_article_response_requires_same_origin_and_allowed_type(self):
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                page_url = "https://unit.test/article/admin/target-record-id"
                self.assertTrue(
                    module.article_api_response_matches_id(
                        "https://unit.test/api/proxy/manager-articles/target-record-id",
                        "target-record-id",
                        page_url,
                        {"admin", "manager"},
                    )
                )
                self.assertFalse(
                    module.article_api_response_matches_id(
                        "https://other.test/api/proxy/manager-articles/target-record-id",
                        "target-record-id",
                        page_url,
                        {"admin", "manager"},
                    )
                )
                self.assertFalse(
                    module.article_api_response_matches_id(
                        "https://unit.test/api/proxy/lottery-articles/target-record-id",
                        "target-record-id",
                        page_url,
                        {"admin", "manager"},
                    )
                )
                with self.assertRaises(ValueError):
                    module.ensure_same_origin(
                        "https://other.test/api/proxy/manager-articles/target-record-id",
                        page_url,
                    )
                with self.assertRaises(ValueError):
                    module.ensure_same_origin("not-a-url", "also-not-a-url")

    def test_user_profile_decoy_numbers_are_not_parseable_documents(self):
        target = {
            "url": "https://unit.test/#/users/999",
            "name": "目标用户",
            "keywords": ["绝杀五码"],
            "count": 5,
            "region": "bottom",
        }
        profile = {
            "id": "999",
            "nickname": "目标用户",
            "bio": "200期 绝杀五码 01,02,03,04,05",
        }
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                with patch.object(user_page, "fetch_json", side_effect=[profile, []]), patch.object(
                    engine, "save_debug_page", return_value=None
                ):
                    results, failure = module.crawl_one(target, ["200"])

                self.assertEqual(results, [])
                self.assertIsNotNone(failure)
                self.assertIn("没有找到指定期数", failure.reason)

    def test_user_aggregate_rejects_foreign_user_and_cross_document_numbers(self):
        target = {
            "url": "https://unit.test/#/users/999",
            "name": "目标用户",
            "keywords": ["绝杀五码"],
            "count": 5,
            "region": "bottom",
        }
        profile = {"id": "999", "nickname": "目标用户"}
        foreign_forum = [
            {
                "id": "100",
                "user_id": "888",
                "topic": "200期 绝杀五码",
                "content": "01,02,03,04,05",
            }
        ]
        split_documents = [
            {
                "id": "100",
                "user_id": "999",
                "topic": "200期 绝杀五码",
                "content": "",
            },
            {
                "id": "99",
                "user_id": "999",
                "topic": "其他资料",
                "content": "01,02,03,04,05",
            },
        ]
        cases = (
            ([profile, foreign_forum], "用户 ID 不匹配"),
            ([profile, split_documents, []], "没有找到符合配置"),
        )
        for module in PARSER_MODULES:
            for responses, expected_reason in cases:
                with self.subTest(module=module.__name__, expected_reason=expected_reason):
                    with patch.object(user_page, "fetch_json", side_effect=responses), patch.object(
                        engine, "save_debug_page", return_value=None
                    ):
                        results, failure = module.crawl_one(target, ["200"])

                    self.assertEqual(results, [])
                    self.assertIsNotNone(failure)
                    self.assertIn(expected_reason, failure.reason)

    def test_illegal_number_invalidates_whole_group(self):
        groups = parser.find_number_groups("五码 01,02,03,04,99,05 开:00准")
        self.assertNotIn(["01", "02", "03", "04", "05"], groups)

    def test_chain_scope_uses_configured_region_before_conflict(self):
        content = "\n".join(
            [
                "200期 五码 01,02,03,04,05 开:00准",
                "199期 五码 11,12,13,14,15 开:47准",
                "200期 五码 06,07,08,09,10 开:47准",
                "199期 五码 16,17,18,19,20 开:47准",
            ]
        )
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__, region="top"):
                found = module.extract_issue_numbers(
                    content,
                    ["200"],
                    keywords=["五码"],
                    expected_count=5,
                    strict_ambiguous=True,
                    region="top",
                    first_issue_chain=True,
                )
                self.assertEqual(found["200"], ["01", "02", "03", "04", "05"])
            with self.subTest(module=module.__name__, region="bottom"):
                found = module.extract_issue_numbers(
                    content,
                    ["200"],
                    keywords=["五码"],
                    expected_count=5,
                    strict_ambiguous=True,
                    region="bottom",
                    first_issue_chain=True,
                )
                self.assertEqual(found["200"], ["06", "07", "08", "09", "10"])

    def test_selected_chain_still_rejects_conflicting_groups(self):
        content = "\n".join(
            [
                "200期 五码 01,02,03,04,05",
                "五码 06,07,08,09,10 开:00准",
                "199期 五码 11,12,13,14,15 开:47准",
            ]
        )
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                with self.assertRaisesRegex(ValueError, "候选不唯一"):
                    module.extract_issue_numbers(
                        content,
                        ["200"],
                        keywords=["五码"],
                        expected_count=5,
                        strict_ambiguous=True,
                        region="top",
                        first_issue_chain=True,
                    )

    def test_position_window_is_applied_before_conflict_check(self):
        content = "\n".join(
            [
                "200期 五码 01,02,03,04,05 开:00准",
                "199期 五码 11,12,13,14,15 开:47准",
                "198期 五码 21,22,23,24,25 开:47准",
                "200期 五码 06,07,08,09,10 开:47准",
            ]
        )
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                found = module.extract_issue_numbers(
                    content,
                    ["200"],
                    keywords=["五码"],
                    expected_count=5,
                    strict_ambiguous=True,
                    region="top",
                    issue_position_window=2,
                )
                self.assertEqual(found["200"], ["01", "02", "03", "04", "05"])

    def test_position_window_counts_issue_segments(self):
        content = "\n".join(
            [
                "200期 五码 01,02,03,04,05 五码 06,07,08,09,10 开:00准",
                "199期 五码 11,12,13,14,15 开:47准",
                "198期 五码 21,22,23,24,25 开:47准",
            ]
        )
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                starts = module.issue_position_window_starts(
                    module.html_to_text(content), ["五码"], 5, "top", 3
                )
                expected = {
                    match.start() for match in module.all_issue_segment_matches(content)
                }
                self.assertEqual(starts, expected)

    def test_keyword_marker_on_previous_line_counts_as_top_window_candidate(self):
        content = "\n".join(
            [
                "绝杀五码",
                "215期 [03 18 25 38 43]开 ?? 准",
                "绝杀五码 214期 [02 17 32 41 47]开 04 准",
                "绝杀五码 213期 [29 38 42 46 48]开 35 准",
                "绝杀五码 212期 [01 16 19 32 46]开 06 准",
                "杀三尾",
            ]
        )
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                common = {
                    "keywords": ["绝杀五码", "绝杀5码"],
                    "expected_count": 5,
                    "strict_ambiguous": True,
                    "anchor": "绝杀五码",
                    "stop_anchor": "杀三尾",
                    "region": "top",
                    "issue_position_window": 3,
                    "keyword_before_issue": True,
                    "keyword_before_issue_window": 30,
                }
                self.assertEqual(
                    module.extract_issue_numbers(content, ["215"], **common),
                    {"215": ["03", "18", "25", "38", "43"]},
                )
                self.assertEqual(
                    module.extract_issue_numbers(content, ["212"], **common),
                    {},
                )

    def test_previous_line_keyword_requires_an_exact_marker_line(self):
        content = "\n".join(
            [
                "绝杀五码 推荐资料",
                "215期 [03 18 25 38 43]开 ?? 准",
                "杀三尾",
            ]
        )
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                found = module.extract_issue_numbers(
                    content,
                    ["215"],
                    keywords=["绝杀五码", "绝杀5码"],
                    expected_count=5,
                    strict_ambiguous=True,
                    anchor="绝杀五码",
                    stop_anchor="杀三尾",
                    region="top",
                    issue_position_window=3,
                    keyword_before_issue=True,
                    keyword_before_issue_window=30,
                )
                self.assertEqual(found, {})

    def test_single_candidate_must_match_configured_region(self):
        candidate = (["01", "02", "03", "04", "05"], "200期 五码", 90)
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                selected = module.filter_candidates_by_region(
                    [candidate],
                    "top",
                    text_length=100,
                    strict_window=True,
                    require_region=True,
                )
                self.assertEqual(selected, [])

    def test_exact_column_scope_cannot_leak_into_next_column(self):
        content = "\n".join(
            [
                "目标杀码栏目",
                "201期 杀5码 01,02,03,04,05 开:00准",
                "199期 杀5码 11,12,13,14,15 开:47准",
                "下一个五不中栏目",
                "200期 杀5码 21,22,23,24,25 开:47准",
            ]
        )
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                found = module.extract_issue_numbers(
                    content,
                    ["200"],
                    keywords=["杀5码"],
                    expected_count=5,
                    strict_ambiguous=True,
                    anchor="目标杀码栏目",
                    stop_anchor="下一个五不中栏目",
                    region="top",
                    first_issue_chain=True,
                )
                self.assertEqual(found, {})

    def test_known_multi_column_sites_use_exact_column_scopes(self):
        targets = {target["name"]: target for target in TARGETS}
        self.assertEqual(targets["六合皇"].get("anchor"), "『绝杀系列』")
        self.assertEqual(targets["六合皇"].get("stop_anchor"), "必杀三尾")
        self.assertTrue(targets["六合皇"].get("first_issue_chain"))
        self.assertEqual(targets["黄金宝坛"].get("anchor"), "今日头版资料杀五码")
        self.assertEqual(targets["黄金宝坛"].get("stop_anchor"), "高手专研定制心水五不中")
        self.assertTrue(targets["黄金宝坛"].get("first_issue_chain"))

    def test_dynamic_author_name_is_not_used_as_body_anchor(self):
        target = {target["name"]: target for target in TARGETS}["热忱一腔"]
        self.assertEqual(target.get("anchor"), "稳杀五码")
        self.assertEqual(target.get("region"), "bottom")
        self.assertTrue(target.get("first_issue_chain"))

    def test_failed_201_targets_use_current_body_anchors(self):
        targets = {target["name"]: target for target in TARGETS}
        expected = {
            "开奖": "杀料贴",
            "雷锋第一版": "杀料榜",
            "雷锋第二版": "绝杀五码",
            "砍杀五码": "砍杀五码",
            "优秀约翰": "稳杀5码",
        }
        for name, anchor in expected.items():
            with self.subTest(name=name):
                self.assertEqual(targets[name].get("anchor"), anchor)
                self.assertNotIn("decoded_anchor_only", targets[name])

    def test_current_decoded_blocks_include_201_body(self):
        targets = {target["name"]: target for target in TARGETS}
        expected = {
            "随风流浪": ("作者:随风流浪", "作者:随风流浪", 1),
            "大显身手": ("大显身手 发表于", "大显身手 发表于", 15),
        }
        for name, (anchor, decoded_anchor, chunks) in expected.items():
            with self.subTest(name=name):
                self.assertEqual(targets[name].get("anchor"), anchor)
                self.assertEqual(targets[name].get("decoded_anchor_only"), decoded_anchor)
                self.assertEqual(targets[name].get("decoded_anchor_chunks"), chunks)

        to_end_expected = {
            "天朗气清": ("天朗气清 发表于", "天朗气清 发表于"),
            "金毛狮王": ("作者:金毛狮王", "作者:金毛狮王"),
        }
        for name, (anchor, decoded_anchor) in to_end_expected.items():
            with self.subTest(name=name):
                target = targets[name]
                self.assertEqual(target.get("anchor"), anchor)
                self.assertEqual(target.get("decoded_anchor_only"), decoded_anchor)
                self.assertTrue(target.get("decoded_anchor_to_end"))
                self.assertNotIn("decoded_anchor_chunks", target)

    def test_suifengliulang_reads_decoded_content_to_end_and_keeps_bottom_window(self):
        target = {target["name"]: target for target in TARGETS}["随风流浪"]
        self.assertTrue(target.get("decoded_anchor_to_end"))
        self.assertNotIn("rendered_fallback_selectors", target)

        decoded_content = (
            "作者:随风流浪\n"
            "216期:【绝杀五码】《01.07.34.04.40》\n"
            "214期:【绝杀五码】《10.08.03.15.06》\n"
            "215期:【绝杀五码】《05.01.37.31.26》\n"
            "216期:【绝杀五码】《08.02.45.40.44》"
        )

        with patch.object(
            engine,
            "fetch_target_content",
            return_value=ResolvedContent(
                "随风流浪",
                decoded_content,
                "decoded_script",
            ),
        ), patch.object(
            engine,
            "render_static_page",
        ) as rendered:
            results, failure = engine.crawl_one(target, ["216"])

        self.assertIsNone(failure)
        self.assertEqual(
            [(item.issue, item.numbers) for item in results],
            [("216", ["08", "02", "45", "40", "44"])],
        )
        rendered.assert_not_called()

    def test_suifengliulang_rejects_out_of_window_periods(self):
        target = {target["name"]: target for target in TARGETS}["随风流浪"]
        decoded_content = (
            "作者:随风流浪\n"
            "213期:【绝杀五码】《09.14.35.07.16》\n"
            "216期:【绝杀五码】《01.07.34.04.40》\n"
            "217期:【绝杀五码】《47.33.44.12.10》\n"
            "214期:【绝杀五码】《10.08.03.15.06》\n"
            "215期:【绝杀五码】《05.01.37.31.26》\n"
            "216期:【绝杀五码】《08.02.45.40.44》"
        )

        with patch.object(
            engine,
            "fetch_target_content",
            return_value=ResolvedContent(
                "随风流浪",
                decoded_content,
                "decoded_script",
            ),
        ):
            for issue in ("213", "217"):
                with self.subTest(issue=issue):
                    results, failure = engine.crawl_one(target, [issue])

                self.assertEqual(results, [])
                self.assertIsNotNone(failure)
                self.assertEqual(failure.error_code, "POSITION_MISMATCH")
                self.assertIn("bottom 最新 3 条同栏目内", failure.reason)

    def test_all_static_targets_have_dedicated_scope(self):
        missing = []
        for target in TARGETS:
            url = target["url"]
            is_special = any(
                (
                    documents.parse_admin_article_id(url),
                    documents.parse_manager_article_id(url),
                    documents.parse_lottery_article_id(url),
                    documents.parse_user_id(url),
                    target.get("list_detail"),
                )
            )
            has_scope = any(
                target.get(field)
                for field in (
                    "anchor",
                    "decoded_anchor_only",
                    "issue_position_window",
                    "first_issue_chain",
                )
            )
            if not is_special and not has_scope:
                missing.append(target["name"])
        self.assertEqual(missing, [])

    def test_rendered_admin_requires_configured_identity_without_anchor(self):
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                with self.assertRaisesRegex(ValueError, "配置目录身份"):
                    module.validate_rendered_article_identity(
                        "200期 五码 01,02,03,04,05",
                        "目标目录",
                        anchor=None,
                    )
                module.validate_rendered_article_identity(
                    "目标目录 200期 五码 01,02,03,04,05",
                    "目标目录",
                    anchor=None,
                )
                with self.assertRaisesRegex(ValueError, "配置目录身份"):
                    module.validate_rendered_article_identity(
                        "正文不含目录名",
                        "目标目录",
                        anchor="专属锚点",
                    )

    def test_unscoped_static_target_fails_closed(self):
        target = {
            "url": "https://unit.test/topic/1",
            "name": "无范围站",
            "keywords": ["五码"],
            "count": 5,
            "region": "top",
        }
        results, failure = engine.crawl_one(target, ["200"])
        self.assertEqual(results, [])
        self.assertIsNotNone(failure)
        self.assertIn("未配置专属正文范围", failure.reason)

    def test_static_rendered_fallback_reparses_only_configured_content(self):
        target = {
            "url": "https://unit.test/topic/1",
            "name": "目标站",
            "keywords": ["绝杀五码"],
            "count": 5,
            "region": "bottom",
            "anchor": "目标站",
            "first_issue_chain": True,
            "rendered_fallback_selectors": [".title", ".content"],
        }
        raw_content = "目标站\n202期:绝杀五码 06,07,08,09,10"
        rendered_content = "目标站\n203期:绝杀五码 01,02,03,04,05"
        for module in PARSER_MODULES:
            with self.subTest(module=module.__name__):
                with patch.object(
                    engine,
                    "fetch_target_content",
                    return_value=ResolvedContent(
                        "目标站",
                        raw_content,
                        "static_topic",
                    ),
                ), patch.object(
                    engine,
                    "render_static_page",
                    return_value=("目标站", rendered_content),
                ) as rendered:
                    results, failure = module.crawl_one(target, ["203"])

                self.assertIsNone(failure)
                self.assertEqual(
                    [(item.issue, item.numbers) for item in results],
                    [("203", ["01", "02", "03", "04", "05"])],
                )
                rendered.assert_called_once_with(
                    target["url"], target["rendered_fallback_selectors"], encoding=None
                )


if __name__ == "__main__":
    unittest.main()
