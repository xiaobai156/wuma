from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import crawler
from kill5.cache import update_recent_duplicate_cache, update_repaired_cache
from kill5.domain import CrawlFailure, CrawlResult, RunOutcome, RunStats
from kill5.output import append_repaired_outputs, format_failure_line
from kill5.services import ProductionRunService
from kill5.storage import atomic_write_bytes, file_transaction, recover_pending_transaction


ISSUE = "321"


def target(name):
    return dict(id=name, name=name, url=f"https://example.invalid/{name}",
                region="top", count=5, keywords=["绝杀五码"],
                anchor="专栏", source_kind="static_topic")


def result(site, issue=ISSUE, numbers=None):
    return CrawlResult(site["url"], site["name"], issue,
                       numbers or ["01", "12", "23", "34", "45"], site["id"])


def failure(site, issue=ISSUE):
    return CrawlFailure(site["url"], site["name"], "页面无当期",
                        evidence={"target_id": site["id"], "issues": [issue]})


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wuma-retry-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.a, self.b, self.c = [target(name) for name in ("甲", "乙", "丙")]
        self.targets = [self.a, self.b, self.c]
        self.success = self.root / f"{ISSUE}期-杀五码-成功.txt"
        self.failed = self.root / f"{ISSUE}期-杀五码-失败.txt"
        self.cache = self.root / "cache.json"
        self.success.write_bytes(b"09,18,27,36,49 existing\r\n")
        self.failed.write_bytes(self.line(self.a) + self.line(self.b))
        self.service = ProductionRunService(self.root, self.root, self.root / "debug", self.cache)
        self.addCleanup(patch.stopall)
        patch.object(socket.socket, "connect", side_effect=AssertionError("测试禁止联网")).start()
        patch("subprocess.Popen", side_effect=AssertionError("测试禁止启动外部抓取进程")).start()

    def line(self, site, issue=ISSUE):
        return (format_failure_line(failure(site, issue), [issue], self.targets) + "\r\n").encode()

    def outcome(self, successes, failures, targets=None):
        chosen = targets if targets is not None else [self.a, self.b]
        return RunOutcome(successes, failures, RunStats(len(chosen), len(successes), 0, 0, 2),
                          [ISSUE], chosen)

    def run_retry(self, successes, failures, targets=None):
        chosen = targets if targets is not None else [self.a, self.b]
        with patch("kill5.services.execute_run", return_value=self.outcome(successes, failures, chosen)) as run:
            execution = self.service.retry_failed(chosen, self.targets, [ISSUE], workers=2, retry_passes=0)
        if chosen:
            self.assertEqual(run.call_args.args[0], chosen)
        return execution

    def snapshot(self):
        return {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in (self.success, self.failed, self.cache) if path.exists()}

    def seed_cache(self):
        update_recent_duplicate_cache(self.cache, [result(t, "320") for t in self.targets],
                                      ["320"], active_targets=self.targets)
        data = json.loads(self.cache.read_bytes())
        data["custom"] = {"keep": True}
        data["sites"][1]["custom"] = "untouched"
        data["sites"][1]["identity"]["config_fingerprint"] = "previous-config"
        data["sites"].append(dict(id="disabled", name="停用", url="https://example.invalid/disabled",
                                  fingerprint={"320": "01,12,23,34,45"}, custom="retain"))
        data["failures"] = [dict(status="failed", name=t["name"], url=t["url"], target_id=t["id"],
                                 issue=ISSUE, reason="旧失败", custom="retain") for t in self.targets]
        self.cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def cli(self, *args):
        with patch.multiple(crawler, RESULTS_DIR=self.root, SCRIPT_DIR=self.root,
                            DEBUG_DIR=self.root / "debug", CACHE_FILE=self.cache, TARGETS=self.targets), \
                patch("sys.argv", ["crawler.py", *args]), \
                patch.object(crawler, "configure_output_encoding"), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = crawler.main()
        return code, output.getvalue()

    def test_partial_success_appends_and_preserves_failure(self):
        original = self.success.read_bytes()
        execution = self.run_retry([result(self.a)], [failure(self.b)])
        self.assertTrue(execution.cache_updated)
        self.assertEqual(self.success.read_bytes(), original + "01,12,23,34,45 甲\r\n".encode())
        self.assertEqual(self.failed.read_bytes(), self.line(self.b))

    def test_cache_only_changes_successful_site(self):
        before = self.seed_cache()
        self.run_retry([result(self.a)], [failure(self.b)])
        after = json.loads(self.cache.read_bytes())
        self.assertEqual(after["sites"][1:], before["sites"][1:])
        self.assertEqual(after["failures"], before["failures"][1:])
        self.assertEqual(after["custom"], before["custom"])
        self.assertEqual(after["sites"][0]["fingerprint"][ISSUE], "01,12,23,34,45")

    def test_zero_success_preserves_bytes_and_mtimes(self):
        self.seed_cache()
        before = self.snapshot()
        execution = self.run_retry([], [failure(self.a), failure(self.b)])
        self.assertFalse(execution.cache_updated)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         sorted([self.success.name, self.failed.name, self.cache.name, ".cache.json.lock"]))

    def test_empty_failure_file_is_successful_noop(self):
        self.failed.write_bytes(b"\xef\xbb\xbf\r\n \r\n")
        before = self.snapshot()
        with patch("kill5.services.execute_run") as run:
            code, _ = self.cli("--retry-failed", "--issues", ISSUE)
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_bom_failure_file_parses(self):
        self.failed.write_bytes(b"\xef\xbb\xbf" + self.failed.read_bytes())
        targets, issues = crawler.failed_targets_from_file(self.failed, self.targets)
        self.assertEqual(targets, [self.a, self.b])
        self.assertEqual(issues, [ISSUE])

    def test_whitespace_and_bom_are_preserved_during_append(self):
        original = b"\xef\xbb\xbf09,18,27,36,49 existing\n\n"
        self.success.write_bytes(original)
        other = self.line(self.b).replace(b"\r\n", b"\n")
        self.failed.write_bytes(b"\xef\xbb\xbf" + self.line(self.a).replace(b" ", b"\t") + other)
        self.run_retry([result(self.a)], [failure(self.b)])
        self.assertEqual(self.success.read_bytes(), original + "01,12,23,34,45 甲\n".encode())
        self.assertEqual(self.failed.read_bytes(), b"\xef\xbb\xbf" + other)

    def test_append_does_not_remove_other_issue(self):
        self.failed.write_bytes(self.line(self.a) + self.line(self.a, "320") + self.line(self.b))
        append_repaired_outputs([result(self.a)], str(self.success), str(self.failed), [ISSUE], [self.a])
        self.assertEqual(self.failed.read_bytes(), self.line(self.a, "320") + self.line(self.b))

    def test_unchanged_success_line_is_not_rewritten(self):
        self.success.write_bytes("01,12,23,34,45 甲".encode())
        before = (self.success.read_bytes(), self.success.stat().st_mtime_ns)
        append_repaired_outputs([result(self.a)], str(self.success), str(self.failed), [ISSUE], [self.a])
        self.assertEqual((self.success.read_bytes(), self.success.stat().st_mtime_ns), before)

    def test_success_conflict_stops_all_writes(self):
        self.success.write_bytes("02,12,23,34,45 甲\r\n".encode())
        before = {key: value[0] for key, value in self.snapshot().items()}
        with self.assertRaises(ValueError):
            self.run_retry([result(self.a)], [failure(self.b)])
        self.assertEqual({key: value[0] for key, value in self.snapshot().items()}, before)

    def test_multiple_failure_files_require_explicit_issue(self):
        (self.root / "320期-杀五码-失败.txt").write_bytes(self.line(self.b, "320"))
        with patch.object(crawler, "RESULTS_DIR", self.root):
            with self.assertRaises(ValueError):
                crawler.find_failed_file(None)

    def test_auto_selection_ignores_multi_issue_file(self):
        (self.root / "320-321期-杀五码-失败.txt").write_bytes(b"multi")
        with patch.object(crawler, "RESULTS_DIR", self.root):
            self.assertEqual(crawler.find_failed_file(None), self.failed)

    def test_invalid_files_stop_before_crawl(self):
        cases = [b"invalid", self.line(target("unknown")), self.line(self.a, "320"),
                 self.line(self.a).replace(b"top", b"bottom"), b"\xff"]
        for content in cases:
            with self.subTest(content=content):
                self.failed.write_bytes(content)
                before = self.snapshot()
                with patch("kill5.services.execute_run") as run:
                    code, _ = self.cli("--retry-failed", "--issues", ISSUE)
                self.assertEqual(code, 2)
                run.assert_not_called()
                self.assertEqual(self.snapshot(), before)

    def test_duplicate_failure_lines_crawl_once(self):
        self.failed.write_bytes(self.line(self.a) * 2)
        chosen, _ = crawler.failed_targets_from_file(self.failed, self.targets)
        self.assertEqual(chosen, [self.a])

    def test_service_rejects_targets_outside_failure_file(self):
        with patch("kill5.services.execute_run") as run:
            with self.assertRaises(ValueError):
                self.service.retry_failed(self.targets, self.targets, [ISSUE], workers=2, retry_passes=0)
        run.assert_not_called()

    def test_cli_zero_success_returns_failure(self):
        with patch("kill5.services.execute_run", return_value=self.outcome([], [failure(self.a), failure(self.b)])):
            code, _ = self.cli("--retry-failed", "--issues", ISSUE)
        self.assertEqual(code, 1)

    def test_cli_full_pipeline_only_fetches_failed_sites(self):
        calls = []

        def fetch(url, **kwargs):
            calls.append(url)
            issue = ISSUE if url == self.a["url"] else "320"
            return f"<div>专栏</div><div>{issue}期 绝杀五码【01,12,23,34,45】</div>"

        before = self.seed_cache()
        with patch("kill5.adapters.static_topic.fetch_text", side_effect=fetch):
            code, output = self.cli("--retry-failed", "--issues", ISSUE, "--retry-passes", "0")
        self.assertEqual(code, 1, output)
        self.assertCountEqual(calls, [self.a["url"], self.b["url"]])
        self.assertIn("成功 1 条，仍失败 1 条", output)
        self.assertIn("01,12,23,34,45 甲", self.success.read_text())
        self.assertEqual(self.failed.read_bytes(), self.line(self.b))
        self.assertEqual(json.loads(self.cache.read_bytes())["sites"][1:], before["sites"][1:])

    def test_cli_all_success_and_second_retry_does_not_crawl(self):
        with patch("kill5.adapters.static_topic.fetch_text", return_value=
                   f"<p>专栏</p><p>{ISSUE}期 绝杀五码【01,12,23,34,45】</p>") as fetch:
            code, output = self.cli("--retry-failed", "--issues", ISSUE, "--retry-passes", "0")
        self.assertEqual(code, 0, output)
        self.assertEqual(fetch.call_count, 2)
        self.assertFalse(self.failed.exists())
        before = self.snapshot()
        with patch("kill5.services.execute_run") as run:
            code, _ = self.cli("--retry-failed", "--issues", ISSUE)
        self.assertEqual(code, 2)
        run.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_cli_invalid_arguments_do_not_crawl(self):
        for args in [("--issues", ""), ("--issues", "0"), ("--issues", "1000"),
                     ("--issues", "320,321"), ("--repair-names", "甲")]:
            with self.subTest(args=args), patch("kill5.services.execute_run") as run:
                code, _ = self.cli("--retry-failed", *args)
                self.assertEqual(code, 2)
                run.assert_not_called()

    def test_service_rejects_multi_issue_before_crawl(self):
        with patch("kill5.services.execute_run") as run:
            with self.assertRaises(ValueError):
                self.service.retry_failed([self.a, self.b], self.targets, ["320", ISSUE], workers=1, retry_passes=0)
        run.assert_not_called()

    def test_service_empty_file_does_nothing(self):
        self.failed.write_bytes(b"")
        before = self.snapshot()
        with patch("kill5.services.execute_run") as run:
            execution = self.service.retry_failed([], self.targets, [ISSUE], workers=1, retry_passes=0)
        run.assert_not_called()
        self.assertFalse(execution.cache_updated)
        self.assertEqual(self.snapshot(), before)

    def test_disabled_target_is_rejected(self):
        self.a["disabled"] = True
        with patch("kill5.services.execute_run") as run:
            code, _ = self.cli("--retry-failed", "--issues", ISSUE)
        self.assertEqual(code, 2)
        run.assert_not_called()

    def test_concurrent_failure_change_is_preserved(self):
        original = self.success.read_bytes()
        modified = self.line(self.b)

        def execute(*args, **kwargs):
            self.failed.write_bytes(modified)
            return self.outcome([result(self.a)], [failure(self.b)])

        with patch("kill5.services.execute_run", side_effect=execute):
            with self.assertRaisesRegex(ValueError, "已变化"):
                self.service.retry_failed([self.a, self.b], self.targets, [ISSUE], workers=2, retry_passes=0)
        self.assertEqual(self.success.read_bytes(), original)
        self.assertEqual(self.failed.read_bytes(), modified)
        self.assertFalse(self.cache.exists())

    def test_network_retry_stays_within_failed_subset(self):
        first_failure = failure(self.b)
        first_failure.retryable = True
        first_failure.error_code = "NETWORK_TIMEOUT"
        with patch("kill5.services.crawl_targets", side_effect=[
                ({0: [result(self.a)]}, {1: first_failure}), ({1: [result(self.b)]}, {})]) as crawl, \
                patch("kill5.services.time.sleep"):
            self.service.retry_failed([self.a, self.b], self.targets, [ISSUE], workers=2, retry_passes=2)
        self.assertEqual(crawl.call_args_list[0].args[0], [(0, self.a), (1, self.b)])
        self.assertEqual(crawl.call_args_list[1].args[0], [(1, self.b)])
        self.assertFalse(self.failed.exists())

    def test_output_write_failure_or_interrupt_rolls_back(self):
        for error in [OSError("disk failure"), KeyboardInterrupt()]:
            with self.subTest(error=type(error).__name__):
                before = {key: value[0] for key, value in self.snapshot().items()}

                def write(path, content):
                    if path == self.failed:
                        raise error
                    atomic_write_bytes(path, content)

                with patch("kill5.output.atomic_write_bytes", side_effect=write):
                    with self.assertRaises(type(error)):
                        self.run_retry([result(self.a)], [failure(self.b)])
                self.assertEqual({key: value[0] for key, value in self.snapshot().items()}, before)
                self.assertFalse((self.root / ".kill5-output-transaction.json").exists())

    def test_output_reread_corruption_rolls_back(self):
        before = {key: value[0] for key, value in self.snapshot().items()}

        def corrupt(path, content):
            atomic_write_bytes(path, content + b"corrupt" if path == self.failed else content)

        with patch("kill5.output.atomic_write_bytes", side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, "复读校验"):
                self.run_retry([result(self.a)], [failure(self.b)])
        self.assertEqual({key: value[0] for key, value in self.snapshot().items()}, before)

    def test_cache_conflict_preserves_cache_and_committed_outputs(self):
        self.seed_cache()
        data = json.loads(self.cache.read_bytes())
        data["sites"][0]["fingerprint"][ISSUE] = "02,12,23,34,45"
        self.cache.write_text(json.dumps(data), encoding="utf-8")
        cache_before = self.cache.read_bytes()
        execution = self.run_retry([result(self.a)], [failure(self.b)])
        self.assertFalse(execution.cache_updated)
        self.assertIn("缓存更新未完成", execution.cache_error)
        self.assertEqual(self.cache.read_bytes(), cache_before)
        self.assertIn("01,12,23,34,45 甲", self.success.read_text())
        self.assertEqual(self.failed.read_bytes(), self.line(self.b))

    def test_cache_replace_failure_preserves_old_cache(self):
        self.seed_cache()
        cache_before = self.cache.read_bytes()
        replace = os.replace

        def fail_once(src, dst):
            if Path(dst) == self.cache and not getattr(fail_once, "failed", False):
                fail_once.failed = True
                raise OSError("cache disk full")
            return replace(src, dst)

        with patch("kill5.storage.os.replace", side_effect=fail_once):
            execution = self.run_retry([result(self.a)], [failure(self.b)])
        self.assertIn("缓存更新未完成", execution.cache_error)
        self.assertEqual(self.cache.read_bytes(), cache_before)
        self.assertEqual(self.failed.read_bytes(), self.line(self.b))

    def test_cache_corruption_and_legacy_are_not_overwritten(self):
        cases = [b"not json", b'{"version": 1, "records": []}', b'{"sites": {}}']
        for content in cases:
            with self.subTest(content=content):
                self.cache.write_bytes(content)
                with self.assertRaises(ValueError):
                    update_repaired_cache(self.cache, [result(self.a)], [ISSUE], [self.a])
                self.assertEqual(self.cache.read_bytes(), content)

    def test_cache_duplicate_run_is_noop(self):
        update_repaired_cache(self.cache, [result(self.a)], [ISSUE], [self.a])
        before = (self.cache.read_bytes(), self.cache.stat().st_mtime_ns)
        update_repaired_cache(self.cache, [result(self.a)], [ISSUE], [self.a])
        self.assertEqual((self.cache.read_bytes(), self.cache.stat().st_mtime_ns), before)

    def test_cache_rejects_one_digit_new_result(self):
        with self.assertRaisesRegex(ValueError, "两位数"):
            update_repaired_cache(self.cache, [result(self.a, numbers=["1", "12", "23", "34", "45"])],
                                  [ISSUE], [self.a])
        self.assertFalse(self.cache.exists())

    def test_cache_zero_success_does_not_read_or_write(self):
        self.cache.write_bytes(b"broken cache remains untouched")
        before = self.snapshot()
        update_repaired_cache(self.cache, [], [ISSUE], [])
        self.assertEqual(self.snapshot(), before)

    def test_cache_identity_conflict_is_not_overwritten(self):
        self.seed_cache()
        before = self.cache.read_bytes()
        changed = {**self.a, "anchor": "另一个专栏"}
        with self.assertRaisesRegex(ValueError, "身份"):
            update_repaired_cache(self.cache, [result(changed)], [ISSUE], [changed])
        self.assertEqual(self.cache.read_bytes(), before)

    def test_cache_failure_without_id_is_removed_only_by_exact_identity(self):
        data = self.seed_cache()
        del data["failures"][0]["target_id"]
        unmatched = {**data["failures"][0], "url": self.a["url"] + "/other"}
        data["failures"].append(unmatched)
        self.cache.write_text(json.dumps(data), encoding="utf-8")
        update_repaired_cache(self.cache, [result(self.a)], [ISSUE], [self.a])
        self.assertEqual(json.loads(self.cache.read_bytes())["failures"], data["failures"][1:])

    def test_cache_only_rolls_successful_site_and_keeps_other_issue_failures(self):
        before = self.seed_cache()
        before["sites"][0]["fingerprint"] = {str(i): "01,12,23,34,45" for i in range(310, 321)}
        before["sites"][1]["fingerprint"] = dict(before["sites"][0]["fingerprint"])
        old_failure = {**before["failures"][0], "issue": "320"}
        before["failures"].insert(0, old_failure)
        self.cache.write_text(json.dumps(before), encoding="utf-8")
        update_repaired_cache(self.cache, [result(self.a)], [ISSUE], [self.a])
        after = json.loads(self.cache.read_bytes())
        self.assertEqual(len(after["sites"][0]["fingerprint"]), 10)
        self.assertEqual(after["sites"][1:], before["sites"][1:])
        self.assertEqual(after["failures"], [old_failure, *before["failures"][2:]])

    def test_named_repair_uses_same_isolated_cache_update(self):
        before = self.seed_cache()
        with patch("kill5.services.execute_run", return_value=self.outcome([result(self.a)], [], [self.a])):
            execution = self.service.run_repairs([self.a], self.targets, [ISSUE], workers=1, retry_passes=0)
        self.assertTrue(execution.cache_updated)
        self.assertEqual(json.loads(self.cache.read_bytes())["sites"][1:], before["sites"][1:])

    def test_daily_full_run_still_uses_full_output_and_threshold(self):
        self.seed_cache()
        before = self.cache.read_bytes()
        outcome = self.outcome([result(self.a)], [failure(self.b), failure(self.c)], self.targets)
        with patch("kill5.services.execute_run", return_value=outcome):
            execution = self.service.run(self.targets, [ISSUE], workers=2, retry_passes=0)
        self.assertFalse(execution.cache_updated)
        self.assertEqual(self.cache.read_bytes(), before)
        self.assertEqual(self.success.read_text(), "01,12,23,34,45 甲\n")
        self.assertIn("丙", self.failed.read_text())

    def test_wrong_or_invalid_engine_results_cannot_be_written(self):
        cases = [result(self.c), result(self.a, "320"), result(self.a, numbers=["01"] * 5),
                 result(self.a, numbers=["1", "12", "23", "34", "45"]),
                 result(self.a, numbers=["50", "12", "23", "34", "45"]),
                 result(self.a, numbers=["01", "12", "23", "34"])]
        for invalid in cases:
            with self.subTest(invalid=invalid):
                before = {key: value[0] for key, value in self.snapshot().items()}
                with self.assertRaises(ValueError):
                    self.run_retry([invalid], [failure(self.b)])
                self.assertEqual({key: value[0] for key, value in self.snapshot().items()}, before)

    def test_missing_success_file_is_not_recreated(self):
        self.success.unlink()
        before = self.failed.read_bytes()
        with self.assertRaises(FileNotFoundError):
            self.run_retry([result(self.a)], [failure(self.b)])
        self.assertFalse(self.success.exists())
        self.assertFalse(self.cache.exists())
        self.assertEqual(self.failed.read_bytes(), before)

    def test_append_to_file_without_final_newline_keeps_prefix(self):
        original = b"09,18,27,36,49 existing"
        self.success.write_bytes(original)
        self.run_retry([result(self.a)], [failure(self.b)])
        self.assertEqual(self.success.read_bytes(), original + "\r\n01,12,23,34,45 甲\r\n".encode())

    def test_zero_success_does_not_recover_unrelated_pending_transaction(self):
        journal = self.root / ".kill5-output-transaction.json"
        journal.write_text("pending transaction sentinel")
        before = self.snapshot()
        self.run_retry([], [failure(self.a), failure(self.b)])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(journal.read_text(), "pending transaction sentinel")

    def test_pending_output_transaction_can_be_recovered(self):
        journal = self.root / ".kill5-output-transaction.json"
        original = self.success.read_bytes()
        with patch("kill5.storage._restore_transaction", side_effect=OSError("rollback unavailable")):
            with self.assertRaises(RuntimeError):
                with file_transaction([self.success], journal):
                    self.success.write_bytes(b"interrupted partial write")
                    raise KeyboardInterrupt()
        self.assertTrue(journal.exists())
        self.assertTrue(recover_pending_transaction(journal))
        self.assertEqual(self.success.read_bytes(), original)
        self.assertFalse(journal.exists())


if __name__ == "__main__":
    unittest.main()
