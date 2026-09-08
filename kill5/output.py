from __future__ import annotations

from pathlib import Path
import re
import time

from .domain import CrawlFailure, CrawlResult, RunStats
from .failures import failure_category, parse_failure_line, summarize_failures
from .identity import (
    dedupe_results,
    failure_identity,
    target_for_result,
    target_identity,
)
from .parser import normalize_issue, normalize_region, preserve_configured_name, valid_number
from .parser import has_duplicate_numbers
from .storage import atomic_write_bytes, atomic_write_text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_DIR = Path(
    r"C:\Users\Administrator\Desktop\每天工具\爬虫合集\大围杀号生肖数据统一归纳"
)
DEFAULT_REPORT_DIR = PROJECT_ROOT
RESULT_FILE = "当期-杀五码-成功.txt"
FAILED_FILE = "当期-杀五码-失败.txt"


_FAILURE_STAGE_BY_ERROR_CODE = {
    "ANCHOR_MISSING": "栏目锚点校验",
    "DOCUMENT_BOUNDARY_ERROR": "栏目锚点校验",
    "KEYWORD_MISMATCH": "栏目锚点校验",
    "POSITION_MISMATCH": "方向位置校验",
    "ISSUE_NOT_FOUND": "期数校验",
    "CONTENT_NOT_PUBLISHED": "页面状态校验",
    "NUMBER_INVALID": "号码校验",
    "CANDIDATE_CONFLICT": "候选冲突校验",
    "ARTICLE_ID_MISMATCH": "文章身份校验",
    "ADAPTER_MISMATCH": "适配器校验",
    "BROWSER_RENDER_FAILED": "浏览器渲染",
    "HTTP_FAILURE": "网络请求",
    "NETWORK_TIMEOUT": "网络请求",
    "SSL_FAILURE": "网络请求",
    "LOCAL_NETWORK_POLICY": "网络请求",
}

_FAILURE_STAGE_BY_STAGE = {
    "target_boundary": "栏目锚点校验",
    "decoded_document_scope": "栏目锚点校验",
    "browser_document_scope": "栏目锚点校验",
    "issue_validation": "期数校验",
    "candidate_validation": "候选校验",
    "content_status": "页面状态校验",
    "article_identity": "文章身份校验",
    "browser_article_identity": "文章身份校验",
    "user_identity": "用户身份校验",
    "user_record": "用户记录校验",
    "adapter_dispatch": "适配器校验",
    "browser_render": "浏览器渲染",
    "script_documents": "脚本文档校验",
    "engine_outcome": "引擎结果校验",
    "crawl": "抓取",
}

def output_transaction_journal(results_dir: Path) -> Path:
    return Path(results_dir) / ".kill5-output-transaction.json"


def output_files_for_issues(
    issues: list[str],
    *,
    results_dir: Path = DEFAULT_RESULTS_DIR,
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> tuple[str, str, str]:
    normalized = [normalize_issue(issue) for issue in issues if normalize_issue(issue)]
    results_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    if not normalized:
        return (
            str(results_dir / RESULT_FILE),
            str(results_dir / FAILED_FILE),
            str(report_dir / "当期报告.txt"),
        )

    if len(normalized) == 1:
        prefix = f"{normalized[0]}期"
    else:
        prefix = f"{normalized[0]}-{normalized[-1]}期"
    return (
        str(results_dir / f"{prefix}-杀五码-成功.txt"),
        str(results_dir / f"{prefix}-杀五码-失败.txt"),
        str(report_dir / f"{prefix}报告.txt"),
    )


def validate_outcome_coverage(
    results: list[CrawlResult],
    failures: list[CrawlFailure],
    issues: list[str],
    targets: list[dict],
) -> None:
    """Require exactly one success or failure decision per target and issue."""
    wanted_issues = tuple(
        dict.fromkeys(normalize_issue(issue) for issue in issues if normalize_issue(issue))
    )
    target_map: dict[tuple[str, ...], dict] = {}
    for target in targets:
        identity = target_identity(target)
        if identity in target_map:
            raise ValueError(f"targets.json 存在重复目标身份：{identity}")
        target_map[identity] = target

    decisions: dict[tuple[tuple[str, ...], str], str] = {}
    errors: list[str] = []

    def record_decision(
        identity: tuple[str, ...],
        issue: str,
        kind: str,
        label: str,
    ) -> None:
        if issue not in wanted_issues:
            return
        key = (identity, issue)
        previous = decisions.get(key)
        if previous:
            errors.append(
                f"{label}与已有{previous}结果重复覆盖 {issue}期，已停止写入"
            )
            return
        decisions[key] = kind

    for item in results:
        target = target_for_result(item, targets)
        if not target:
            if any(str(target.get("id") or "").strip() for target in targets) and not str(
                item.target_id or ""
            ).strip():
                errors.append(
                    f"{item.name} {item.url} {item.issue}期 缺少稳定 target_id，"
                    "已拒绝按名称或 URL 猜测目标"
                )
            else:
                errors.append(
                    f"{item.name} {item.url} {item.issue}期 无法精确匹配当前 targets.json 目标"
                )
            continue
        record_decision(
            target_identity(target),
            normalize_issue(item.issue),
            "成功",
            f"{item.name} {item.url}",
        )

    for failure in failures:
        identity = failure_identity(failure)
        target = target_map.get(identity)
        if not target:
            errors.append(
                f"失败结果 {failure.name} {failure.url} 无法精确匹配当前 targets.json 目标"
            )
            continue

        configured_name = preserve_configured_name(target.get("name") or "")
        configured_url = str(target.get("url") or "")
        if preserve_configured_name(failure.name) != configured_name:
            errors.append(
                f"稳定ID {target.get('id') or identity} 的失败目录名不匹配："
                f"{failure.name} != {configured_name}"
            )
        if failure.url != configured_url:
            errors.append(
                f"稳定ID {target.get('id') or identity} 的失败 URL 不匹配："
                f"{failure.url} != {configured_url}"
            )

        evidence = failure.evidence if isinstance(failure.evidence, dict) else {}
        stable_id = str(target.get("id") or "").strip()
        if stable_id and str(evidence.get("target_id") or "").strip() != stable_id:
            errors.append(
                f"{failure.name} {failure.url} 缺少与目标一致的稳定 target_id，已拒绝失败旁路"
            )

        raw_failure_issues = evidence.get("issues")
        if raw_failure_issues is None:
            if stable_id:
                errors.append(
                    f"{failure.name} {failure.url} 失败证据缺少指定期数，已拒绝不完整失败结论"
                )
                continue
            failure_issues = list(wanted_issues)
        elif isinstance(raw_failure_issues, (list, tuple)):
            failure_issues = list(
                dict.fromkeys(
                    normalize_issue(issue)
                    for issue in raw_failure_issues
                    if normalize_issue(issue)
                )
            )
        else:
            errors.append(f"{failure.name} {failure.url} 失败证据期数格式无效")
            continue

        if not failure_issues:
            errors.append(f"{failure.name} {failure.url} 失败证据没有覆盖任何指定期数")
            continue
        unknown_issues = [issue for issue in failure_issues if issue not in wanted_issues]
        if unknown_issues:
            errors.append(
                f"{failure.name} {failure.url} 失败证据包含未指定期数：{','.join(unknown_issues)}"
            )
            continue
        for issue in failure_issues:
            record_decision(identity, issue, "失败", f"{failure.name} {failure.url}")

    for identity, target in target_map.items():
        for issue in wanted_issues:
            if (identity, issue) not in decisions:
                errors.append(
                    f"{preserve_configured_name(target.get('name') or '')} "
                    f"{target.get('url') or ''} 缺少 {issue}期 的唯一成功或失败结论"
                )

    if errors:
        preview = "\n".join(f"- {error}" for error in errors[:20])
        if len(errors) > 20:
            preview += f"\n- 其余 {len(errors) - 20} 条错误省略"
        raise ValueError(f"统一结果覆盖校验失败，已停止输出避免漏站或旁路：\n{preview}")


def validate_results_before_write(
    results: list[CrawlResult],
    issues: list[str],
    targets: list[dict],
    *,
    require_all_targets: bool = True,
    failures: list[CrawlFailure] | None = None,
) -> None:
    validate_outcome_coverage(results, failures or [], issues, targets)
    wanted_issues = {normalize_issue(issue) for issue in issues if normalize_issue(issue)}
    target_map: dict[tuple[str, ...], dict] = {}
    for target in targets:
        identity = target_identity(target)
        if identity in target_map:
            raise ValueError(f"targets.json 存在重复目标身份：{identity}")
        target_map[identity] = target

    seen_site_issue: dict[tuple[tuple[str, ...], str], CrawlResult] = {}
    issues_by_site: dict[tuple[str, ...], set[str]] = {}
    errors: list[str] = []

    for item in results:
        issue = normalize_issue(item.issue)
        target = target_for_result(item, targets)
        if not target:
            if any(str(target.get("id") or "").strip() for target in targets) and not str(
                item.target_id or ""
            ).strip():
                errors.append(
                    f"{item.name} {item.url} {item.issue}期 缺少稳定 target_id，已拒绝按名称或 URL 猜测目标"
                )
            else:
                errors.append(
                    f"{item.name} {item.url} {item.issue}期 不在 targets.json 当前启用目标内"
                )
            continue

        target_key = target_identity(target)
        configured_name = preserve_configured_name(target.get("name") or "")
        configured_url = str(target.get("url") or "")
        if preserve_configured_name(item.name) != configured_name:
            errors.append(
                f"稳定ID {target.get('id') or target_key} 的结果目录名不匹配："
                f"{item.name} != {configured_name}"
            )
        if item.url != configured_url:
            errors.append(
                f"稳定ID {target.get('id') or target_key} 的结果 URL 不匹配："
                f"{item.url} != {configured_url}"
            )

        site_issue_key = (target_key, issue)
        if site_issue_key in seen_site_issue:
            errors.append(f"{item.name} {item.url} {issue}期 出现多条成功结果，已停止写入避免覆盖冲突")
            continue
        seen_site_issue[site_issue_key] = item

        if issue not in wanted_issues:
            errors.append(f"{item.name} {item.url} {item.issue}期 不在本次指定期数内")
        issues_by_site.setdefault(target_key, set()).add(issue)

        expected_count = int(target.get("count") or 0)
        if len(item.numbers) != expected_count:
            errors.append(
                f"{item.name} {item.url} {issue}期 号码数量 {len(item.numbers)} 不等于配置 count={expected_count}"
            )

        for number in item.numbers:
            if not valid_number(number):
                errors.append(f"{item.name} {item.url} {issue}期 存在非法号码 {number}")

        if has_duplicate_numbers(item.numbers):
            errors.append(f"{item.name} {item.url} {issue}期 号码重复：{','.join(item.numbers)}")

    for identity, found_issues in issues_by_site.items():
        if found_issues != wanted_issues:
            missing = sorted(wanted_issues - found_issues, key=int)
            target = target_map[identity]
            name = preserve_configured_name(target.get("name") or "")
            url = str(target.get("url") or "")
            errors.append(
                f"{name} {url} 缺少指定期数：{','.join(issue + '期' for issue in missing)}"
            )

    if errors:
        preview = "\n".join(f"- {error}" for error in errors[:20])
        if len(errors) > 20:
            preview += f"\n- 其余 {len(errors) - 20} 条错误省略"
        raise ValueError(f"写入前安全校验失败，已停止输出避免抓错：\n{preview}")


def verify_result_file(path: str, results: list[CrawlResult], issues: list[str]) -> None:
    result_path = Path(path)
    expected_content = [
        f"{','.join(item.numbers)} {item.name}"
        for item in dedupe_results(results)
    ]
    actual_lines = [
        line.strip()
        for line in result_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    errors: list[str] = []
    if actual_lines != expected_content:
        errors.append("成功文件内容、顺序或目录名与内存校验结果不一致")

    for line in actual_lines:
        match = re.match(r"^([0-9,]+)\s+(.+)$", line)
        if not match:
            errors.append(f"成功文件格式异常：{line}")
            continue
        numbers_text, name = match.groups()
        numbers = [number for number in numbers_text.split(",") if number]
        for number in numbers:
            if not valid_number(number):
                errors.append(f"{name} 成功文件存在非法号码 {number}")

    if errors:
        preview = "\n".join(f"- {error}" for error in errors[:20])
        if len(errors) > 20:
            preview += f"\n- 其余 {len(errors) - 20} 条错误省略"
        raise ValueError(f"输出文件安全校验失败：\n{preview}")


def _single_line(value: object, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or fallback


def _target_for_failure(failure: CrawlFailure, targets: list[dict]) -> dict | None:
    wanted = failure_identity(failure)
    matches = [target for target in targets if target_identity(target) == wanted]
    return matches[0] if len(matches) == 1 else None


def _failure_issue_text(failure: CrawlFailure, issues: list[str]) -> str:
    evidence = failure.evidence if isinstance(failure.evidence, dict) else {}
    raw_issues = evidence.get("issues")
    if not isinstance(raw_issues, (list, tuple)):
        raw_issues = issues
    normalized = list(
        dict.fromkeys(
            normalize_issue(issue)
            for issue in raw_issues
            if normalize_issue(issue)
        )
    )
    return ",".join(normalized) or "未指定"


def _failure_direction(failure: CrawlFailure, target: dict | None) -> str:
    evidence = failure.evidence if isinstance(failure.evidence, dict) else {}
    raw_direction = target.get("region") if target is not None else None
    if not raw_direction:
        raw_direction = evidence.get("region") or evidence.get("direction")
    direction = normalize_region(str(raw_direction or ""))
    return direction or _single_line(raw_direction, "未配置")


def _failure_stage(failure: CrawlFailure) -> str:
    error_code = str(failure.error_code or "").strip()
    if error_code in _FAILURE_STAGE_BY_ERROR_CODE:
        return _FAILURE_STAGE_BY_ERROR_CODE[error_code]
    stage = str(failure.stage or "").strip()
    return _FAILURE_STAGE_BY_STAGE.get(stage, stage) or "未标注"


def format_failure_line(
    failure: CrawlFailure,
    issues: list[str],
    targets: list[dict],
) -> str:
    target = _target_for_failure(failure, targets)
    name = _single_line(failure.name, "未命名")
    url = _single_line(failure.url, "未知URL")
    reason = _single_line(failure.reason, "未提供原因")
    return (
        f"失败 {name} {url} 方向: {_failure_direction(failure, target)} "
        f"期数: {_failure_issue_text(failure, issues)} "
        f"阶段: {_failure_stage(failure)} 原因: {reason}"
    )


def write_outputs(
    results: list[CrawlResult],
    failures: list[CrawlFailure],
    result_file: str,
    failed_file: str,
    report_file: str,
    stats: RunStats,
    issues: list[str],
    targets: list[dict],
) -> None:
    validate_results_before_write(
        results,
        issues,
        targets,
        require_all_targets=True,
        failures=failures,
    )

    result_text = "".join(
        f"{','.join(item.numbers)} {item.name}\n"
        for item in dedupe_results(results)
    )
    atomic_write_text(Path(result_file), result_text)
    verify_result_file(result_file, results, issues)

    if failures:
        failure_text = "\n".join(
            format_failure_line(item, issues, targets)
            for item in failures
        ) + "\n"
        atomic_write_text(Path(failed_file), failure_text)
    else:
        remove_stale_failure_file(failed_file)

    write_report(results, failures, report_file, stats)


def append_repaired_outputs(
    results: list[CrawlResult],
    result_file: str,
    failed_file: str,
    issues: list[str],
    targets: list[dict],
) -> None:
    normalized_issues = {
        normalize_issue(issue) for issue in issues if normalize_issue(issue)
    }
    if len(normalized_issues) != 1:
        raise ValueError("失败修复输出只允许单期")
    validate_results_before_write(results, issues, targets, failures=[])
    if not results:
        return

    result_path = Path(result_file)
    if not result_path.exists():
        raise FileNotFoundError("当期成功 TXT 不存在，无法执行修复追加")
    original_success = result_path.read_bytes()
    existing_lines = original_success.decode("utf-8-sig").splitlines()
    existing_by_name: dict[str, str] = {}
    for line in existing_lines:
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9,]+)\s+(.+)", line.strip())
        if not match:
            raise ValueError(f"原成功 TXT 存在异常行，已停止追加：{line}")
        name = match.group(2)
        if name in existing_by_name:
            raise ValueError(f"原成功 TXT 存在重复目录，已停止追加：{name}")
        existing_by_name[name] = f"{match.group(1)} {name}"

    additions: list[str] = []
    added_names: set[str] = set()
    repaired_keys: set[tuple[str, str]] = set()
    for item in dedupe_results(results):
        line = f"{','.join(item.numbers)} {item.name}"
        if any(not re.fullmatch(r"[0-9]{2}", number) for number in item.numbers):
            raise ValueError(f"{item.name} 的成功号码必须是两位数")
        existing = existing_by_name.get(item.name)
        if existing and existing != line:
            raise ValueError(f"成功 TXT 已存在 {item.name} 的不同结果，禁止覆盖")
        if not existing:
            additions.append(line)
            added_names.add(item.name)
        repaired_keys.add((item.name, item.url))

    failed_path = Path(failed_file)
    original_failure = failed_path.read_bytes() if failed_path.exists() else b""
    failure_lines = original_failure.decode("utf-8-sig").splitlines(keepends=True)
    matched_failures: set[tuple[str, str]] = set()
    remaining_failures: list[str] = []
    for line in failure_lines:
        matched = None
        try:
            name, url, _direction, line_issues = parse_failure_line(line)
            if (name, url) in repaired_keys and set(line_issues) == normalized_issues:
                matched = (name, url)
        except ValueError:
            pass  # Unrelated or unrecognised lines are never removed by a repair.
        if matched is None:
            remaining_failures.append(line)
        else:
            matched_failures.add(matched)

    missing_failures = sorted(
        name
        for name, url in repaired_keys - matched_failures
        if name in added_names
    )
    if missing_failures:
        raise ValueError(
            f"失败 TXT 没有对应待修复记录，已停止追加：{','.join(missing_failures)}"
        )

    merged_success = original_success
    if additions:
        eol = re.search(rb"\r\n|\n|\r", original_success)
        newline = eol.group() if eol else b"\r\n"
        if original_success.removeprefix(b"\xef\xbb\xbf") and not original_success.endswith((b"\r", b"\n")):
            merged_success += newline
        merged_success += newline.join(line.encode("utf-8") for line in additions) + newline
        atomic_write_bytes(result_path, merged_success)
    if matched_failures:
        if any(line.strip() for line in remaining_failures):
            bom = b"\xef\xbb\xbf" if original_failure.startswith(b"\xef\xbb\xbf") else b""
            merged_failure = bom + "".join(remaining_failures).encode("utf-8")
            atomic_write_bytes(failed_path, merged_failure)
            if failed_path.read_bytes() != merged_failure:
                raise ValueError("修复后的失败 TXT 复读校验不一致")
        else:
            remove_stale_failure_file(failed_file)

    if result_path.read_bytes() != merged_success:
        raise ValueError("修复追加后的成功 TXT 复读校验不一致")


def remove_stale_failure_file(failed_file: str) -> None:
    path = Path(failed_file)
    if not path.exists():
        return
    path.unlink()


def write_report(
    results: list[CrawlResult],
    failures: list[CrawlFailure],
    report_file: str,
    stats: RunStats,
) -> None:
    unique_results = dedupe_results(results)
    failure_summary = summarize_failures(failures)
    lines = [
        "抓取报告",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"目标总数：{stats.total_targets}",
        f"初跑成功：{stats.initial_success}",
        f"补抓救回：{stats.retry_rescued}",
        f"最终成功：{len(unique_results)}",
        f"最终失败：{len(failures)}",
        f"并发线程：{stats.workers}",
        f"补抓轮数：{stats.retry_passes_used}",
        "",
        "失败分类：",
    ]
    if failure_summary:
        for category, count in sorted(failure_summary.items()):
            lines.append(f"- {category}：{count}")
    else:
        lines.append("- 无")

    if failures:
        lines.extend(["", "失败明细："])
        for failure in failures:
            lines.append(f"- [{failure_category(failure)}] {failure.name} {failure.url}")
            lines.append(f"  原因：{failure.reason}")

    atomic_write_text(Path(report_file), "\n".join(lines) + "\n")


__all__ = [
    'output_files_for_issues',
    'output_transaction_journal',
    'validate_outcome_coverage',
    'validate_results_before_write',
    'verify_result_file',
    'format_failure_line',
    'write_outputs',
    'append_repaired_outputs',
    'remove_stale_failure_file',
    'write_report',
]
