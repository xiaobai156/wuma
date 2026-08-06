from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from .domain import CrawlFailure
from .failures import failure_category
from .identity import failure_identity, result_identity, target_identity
from .output import (
    output_files_for_issues,
    output_transaction_journal,
    write_outputs,
)
from .services import execute_run
from .storage import atomic_write_text, file_transaction, recover_pending_transaction


@dataclass(frozen=True)
class MultiIssueExecution:
    passed_targets: int
    failed_targets: int
    report_path: Path


def target_key(target: dict) -> tuple[str, ...]:
    return target_identity(target)


def failure_key(failure: CrawlFailure) -> tuple[str, ...]:
    return failure_identity(failure)


def write_multi_failure_report(
    targets: list[dict],
    issues: list[str],
    successes_by_key: set[tuple[str, ...]],
    failures_by_issue: dict[str, dict[tuple[str, ...], CrawlFailure]],
    report_dir: Path,
) -> Path:
    first_issue = issues[0]
    last_issue = issues[-1]
    if len(issues) == 1:
        name = f"{first_issue}期-杀五码-多期全部失败报告.txt"
    else:
        name = f"{first_issue}-{last_issue}期-杀五码-多期全部失败报告.txt"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / name

    lines = [
        "多期汇总失败报告",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"指定期数：{', '.join(issue + '期' for issue in issues)}",
        "判定规则：任意一期成功即通过；本报告只列所有指定期数全部失败的目录。",
        "",
    ]
    failed_count = 0
    for target in targets:
        key = target_key(target)
        if key in successes_by_key:
            continue
        failed_count += 1
        name = str(target.get("name") or "")
        url = str(target.get("url") or "")
        lines.append(f"[全部失败] {name} {url}")
        for issue in issues:
            failure = failures_by_issue.get(issue, {}).get(key)
            if failure is None:
                lines.append(f"- {issue}期：没有返回成功结果")
            else:
                lines.append(
                    f"- {issue}期：[{failure_category(failure)}] {failure.reason}"
                )
        lines.append("")
    lines.insert(4, f"全部失败目录数：{failed_count}")
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")
    return path


def run_multi_issues(
    targets: list[dict],
    issues: list[str],
    *,
    workers: int,
    retry_passes: int,
    results_dir: Path,
    report_dir: Path,
    debug_dir: Path,
) -> MultiIssueExecution:
    successes_by_key: set[tuple[str, ...]] = set()
    failures_by_issue: dict[str, dict[tuple[str, ...], CrawlFailure]] = {}

    for issue in issues:
        print(f"\n===== 开始抓取 {issue}期 =====")
        outcome = execute_run(
            targets,
            [issue],
            workers=workers,
            retry_passes=retry_passes,
            debug_dir=debug_dir,
        )
        result_file, failed_file, report_file = output_files_for_issues(
            [issue],
            results_dir=results_dir,
            report_dir=report_dir,
        )
        transaction_journal = output_transaction_journal(results_dir)
        recover_pending_transaction(transaction_journal)
        with file_transaction(
            [Path(result_file), Path(failed_file), Path(report_file)],
            transaction_journal,
        ):
            write_outputs(
                outcome.results,
                outcome.failures,
                result_file,
                failed_file,
                report_file,
                outcome.stats,
                [issue],
                targets,
            )
        for item in outcome.results:
            successes_by_key.add(result_identity(item))
        failures_by_issue[issue] = {
            failure_key(failure): failure for failure in outcome.failures
        }
        print(
            f"{issue}期完成：成功 {len(outcome.results)} 条，"
            f"失败 {len(outcome.failures)} 条"
        )

    report_path = write_multi_failure_report(
        targets,
        issues,
        successes_by_key,
        failures_by_issue,
        report_dir,
    )
    passed = len(successes_by_key)
    return MultiIssueExecution(
        passed_targets=passed,
        failed_targets=len(targets) - passed,
        report_path=report_path,
    )


__all__ = ["MultiIssueExecution", "run_multi_issues", "write_multi_failure_report"]
