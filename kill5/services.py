from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from .cache import update_recent_duplicate_cache
from .domain import CrawlFailure, CrawlResult, RunExecution, RunOutcome, RunStats
from .errors import ErrorCode
from .engine import (
    blocked_by_local_socket_policy,
    crawl_targets,
    is_transient_failure,
)
from .identity import canonical_url, dedupe_results
from .output import (
    append_repaired_outputs,
    output_files_for_issues,
    output_transaction_journal,
    write_outputs,
)
from .parser import normalize_issue, preserve_configured_name
from .storage import file_transaction, recover_pending_transaction


DEFAULT_RETRY_WAIT = 12
MIN_CACHE_UPDATE_SUCCESS_PERCENT = 85


def cache_update_allowed(outcome: RunOutcome) -> bool:
    """Return whether this single-run outcome may advance the cache."""
    normalized_issues = {
        normalize_issue(issue) for issue in outcome.issues if normalize_issue(issue)
    }
    if len(normalized_issues) != 1:
        return False
    total_targets = len(outcome.targets)
    if total_targets <= 0:
        return False
    successful_targets = {
        str(result.target_id).strip() or canonical_url(result.url)
        for result in outcome.results
    }
    return len(successful_targets) * 100 > total_targets * MIN_CACHE_UPDATE_SUCCESS_PERCENT


def failure_cache_markers(
    failures: list[CrawlFailure],
    issues: list[str],
) -> list[dict]:
    markers: list[dict] = []
    for failure in failures:
        evidence = failure.evidence if isinstance(failure.evidence, dict) else {}
        raw_issues = evidence.get("issues")
        marker_issues = (
            raw_issues
            if isinstance(raw_issues, (list, tuple))
            else issues
        )
        for issue in marker_issues:
            normalized_issue = normalize_issue(issue)
            if not normalized_issue:
                continue
            marker = {
                "status": "failed",
                "name": failure.name,
                "url": failure.url,
                "issue": normalized_issue,
                "reason": failure.reason,
            }
            if failure.stage:
                marker["stage"] = failure.stage
            if failure.error_code:
                marker["error_code"] = failure.error_code
            target_id = str(evidence.get("target_id") or "").strip()
            if target_id:
                marker["target_id"] = target_id
            markers.append(marker)
    return markers


def execute_run(
    targets: list[dict],
    issues: list[str],
    *,
    workers: int,
    retry_passes: int,
    debug_dir: Path,
    retry_wait: float = DEFAULT_RETRY_WAIT,
) -> RunOutcome:
    ordered_results: list[list[CrawlResult]] = [[] for _ in targets]
    ordered_failures: list[CrawlFailure | None] = [None for _ in targets]
    max_workers = max(1, min(workers, len(targets)))

    initial_results, initial_failures = crawl_targets(
        list(enumerate(targets)),
        issues,
        max_workers,
        debug_dir=debug_dir,
    )
    for index in range(len(targets)):
        ordered_results[index] = initial_results.get(index, [])
        ordered_failures[index] = initial_failures.get(index)

    initial_success = sum(1 for group in ordered_results if group)
    retry_rescued = 0
    retry_passes_used = 0
    for retry_pass in range(1, max(0, retry_passes) + 1):
        retry_items = [
            (index, targets[index])
            for index, failure in enumerate(ordered_failures)
            if not ordered_results[index] and is_transient_failure(failure)
        ]
        if not retry_items:
            break

        print(f"\n网络失败补抓第 {retry_pass}/{retry_passes} 轮：{len(retry_items)} 条")
        retry_passes_used = retry_pass
        if retry_wait > 0:
            time.sleep(retry_wait)
        retry_results, retry_failures = crawl_targets(
            retry_items,
            issues,
            1,
            label="慢速单线程补抓：",
            debug_dir=debug_dir,
        )
        for index, _target in retry_items:
            found = retry_results.get(index, [])
            failure = retry_failures.get(index)
            if found and not failure:
                ordered_results[index] = found
                ordered_failures[index] = None
                retry_rescued += 1
            elif failure:
                ordered_failures[index] = failure

    wanted_issues = {normalize_issue(issue) for issue in issues}
    for index, target in enumerate(targets):
        found_issues = {
            normalize_issue(result.issue)
            for result in ordered_results[index]
        }
        invalid_outcome = bool(ordered_results[index] and ordered_failures[index])
        expected_target_id = str(target.get("id") or "").strip()
        invalid_identity = bool(
            expected_target_id
            and any(
                str(result.target_id or "").strip() != expected_target_id
                for result in ordered_results[index]
            )
        )
        incomplete_outcome = bool(ordered_results[index]) and found_issues != wanted_issues
        missing_outcome = not ordered_results[index] and ordered_failures[index] is None
        if not (
            invalid_outcome
            or invalid_identity
            or incomplete_outcome
            or missing_outcome
        ):
            continue

        if invalid_outcome:
            reason = "抓取引擎同时返回成功和失败，已停止写入"
        elif invalid_identity:
            reason = "抓取引擎结果缺少或混入错误稳定ID，已停止写入"
        elif incomplete_outcome:
            missing = sorted(wanted_issues - found_issues, key=int)
            reason = f"抓取引擎成功结果不完整，缺少：{','.join(issue + '期' for issue in missing)}"
        else:
            reason = "抓取引擎没有返回成功结果或失败原因，已停止写入"
        ordered_results[index] = []
        ordered_failures[index] = CrawlFailure(
            url=target["url"],
            name=preserve_configured_name(target.get("name") or ""),
            reason=reason,
            error_code=ErrorCode.OUTPUT_VALIDATION_FAILED.value,
            stage="engine_outcome",
            evidence={
                "target_id": target.get("id"),
                "issues": list(issues),
            },
        )

    results = dedupe_results([item for group in ordered_results for item in group])
    failures = [item for item in ordered_failures if item]
    stats = RunStats(
        total_targets=len(targets),
        initial_success=initial_success,
        retry_rescued=retry_rescued,
        retry_passes_used=retry_passes_used,
        workers=max_workers,
    )
    return RunOutcome(
        results=results,
        failures=failures,
        stats=stats,
        issues=list(issues),
        targets=list(targets),
    )


@dataclass(frozen=True)
class ProductionRunService:
    results_dir: Path
    report_dir: Path
    debug_dir: Path
    cache_path: Path

    def run(
        self,
        targets: list[dict],
        issues: list[str],
        *,
        workers: int,
        retry_passes: int,
    ) -> RunExecution:
        transaction_journal = output_transaction_journal(self.results_dir)
        recover_pending_transaction(transaction_journal)
        outcome = execute_run(
            targets,
            issues,
            workers=workers,
            retry_passes=retry_passes,
            debug_dir=self.debug_dir,
        )
        result_file, failed_file, report_file = output_files_for_issues(
            issues,
            results_dir=self.results_dir,
            report_dir=self.report_dir,
        )
        if not outcome.results and blocked_by_local_socket_policy(outcome.failures):
            return RunExecution(
                outcome,
                result_file,
                failed_file,
                report_file,
                cache_updated=False,
                preserved_outputs=True,
            )

        cache_updated = False
        cache_error = None
        transaction_paths = [
            Path(result_file),
            Path(failed_file),
            Path(report_file),
        ]
        with file_transaction(transaction_paths, transaction_journal):
            write_outputs(
                outcome.results,
                outcome.failures,
                result_file,
                failed_file,
                report_file,
                outcome.stats,
                issues,
                targets,
            )

        if cache_update_allowed(outcome):
            try:
                update_recent_duplicate_cache(
                    self.cache_path,
                    outcome.results,
                    issues,
                    recent_count=10,
                    active_targets=targets,
                    failure_markers=failure_cache_markers(outcome.failures, issues),
                    require_complete=not outcome.failures,
                )
            except Exception as exc:
                cache_error = f"缓存更新未完成：{exc}"
            else:
                cache_updated = True
        return RunExecution(
            outcome,
            result_file,
            failed_file,
            report_file,
            cache_updated=cache_updated,
            cache_error=cache_error,
        )

    def run_repairs(
        self,
        targets: list[dict],
        active_targets: list[dict],
        issues: list[str],
        *,
        workers: int,
        retry_passes: int,
    ) -> RunExecution:
        outcome = execute_run(
            targets,
            issues,
            workers=workers,
            retry_passes=retry_passes,
            debug_dir=self.debug_dir,
        )
        result_file, failed_file, report_file = output_files_for_issues(
            issues,
            results_dir=self.results_dir,
            report_dir=self.report_dir,
        )
        if outcome.failures or not outcome.results:
            return RunExecution(
                outcome,
                result_file,
                failed_file,
                report_file,
                cache_updated=False,
                preserved_outputs=True,
            )

        transaction_journal = output_transaction_journal(self.results_dir)
        recover_pending_transaction(transaction_journal)
        with file_transaction(
            [Path(result_file), Path(failed_file)],
            transaction_journal,
        ):
            append_repaired_outputs(
                outcome.results,
                result_file,
                failed_file,
                issues,
                targets,
            )

        cache_error = None
        try:
            update_recent_duplicate_cache(
                self.cache_path,
                outcome.results,
                issues,
                recent_count=10,
                active_targets=active_targets,
                failure_markers=[],
                require_complete=False,
            )
        except Exception as exc:
            cache_error = f"缓存更新未完成：{exc}"
        return RunExecution(
            outcome,
            result_file,
            failed_file,
            report_file,
            cache_updated=cache_error is None,
            cache_error=cache_error,
        )

    def retry_failed(
        self,
        targets: list[dict],
        active_targets: list[dict],
        issues: list[str],
        *,
        workers: int,
        retry_passes: int,
    ) -> RunExecution:
        outcome = execute_run(
            targets,
            issues,
            workers=workers,
            retry_passes=retry_passes,
            debug_dir=self.debug_dir,
        )
        result_file, failed_file, report_file = output_files_for_issues(
            issues,
            results_dir=self.results_dir,
            report_dir=self.report_dir,
        )
        cache_error = None
        cache_updated = False
        if outcome.results:
            transaction_journal = output_transaction_journal(self.results_dir)
            recover_pending_transaction(transaction_journal)
            with file_transaction(
                [Path(result_file), Path(failed_file)],
                transaction_journal,
            ):
                append_repaired_outputs(
                    outcome.results,
                    result_file,
                    failed_file,
                    issues,
                    active_targets,
                )
            try:
                update_recent_duplicate_cache(
                    self.cache_path,
                    outcome.results,
                    issues,
                    recent_count=10,
                    active_targets=active_targets,
                    failure_markers=[],
                    require_complete=False,
                )
            except Exception as exc:
                cache_error = f"缓存更新未完成：{exc}"
            else:
                cache_updated = True
        return RunExecution(
            outcome,
            result_file,
            failed_file,
            report_file,
            cache_updated=cache_updated,
            cache_error=cache_error,
        )


__all__ = [
    "ProductionRunService",
    "execute_run",
]
