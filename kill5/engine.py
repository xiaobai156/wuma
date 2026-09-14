from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re
import time
from urllib.parse import urlparse

from .domain import CrawlFailure, CrawlResult
from .errors import CrawlError, ErrorCode, classify_exception
from .documents import (
    parse_admin_article_id,
    parse_lottery_article_id,
    parse_manager_article_id,
    parse_user_id,
)
from .network import request_scope
from .parser import normalize_issue, preserve_configured_name, stats_max_row_numbers
from .validator import (
    detect_available_issues,
    diagnose_issue_mismatch,
    extract_issue_numbers,
    nearest_issues,
)
from .adapters.dynamic_article import (
    render_article_admin_page,
    validate_rendered_article_identity,
)
from .adapters.static_topic import render_static_page
from .adapters.registry import adapter_kind, fetch_target_content


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEBUG_DIR = PROJECT_ROOT / "debug_pages"


def build_failure(
    target: dict,
    name: str,
    reason: str,
    error_code: ErrorCode,
    *,
    stage: str | None = None,
    retryable: bool = False,
    evidence: dict | None = None,
) -> CrawlFailure:
    base_evidence = {
        "target_id": target.get("id"),
        "source_kind": target.get("source_kind"),
    }
    base_evidence.update(evidence or {})
    return CrawlFailure(
        url=target["url"],
        name=name,
        reason=reason,
        error_code=error_code.value,
        stage=stage,
        evidence=base_evidence,
        retryable=retryable,
    )


def failure_from_exception(
    target: dict,
    name: str,
    exc: BaseException,
    *,
    reason: str | None = None,
    issues: list[str] | None = None,
) -> CrawlFailure:
    error_code, retryable = classify_exception(exc)
    stage = exc.stage if isinstance(exc, CrawlError) else "crawl"
    evidence = dict(exc.evidence) if isinstance(exc, CrawlError) else {}
    evidence["issues"] = list(issues or [])
    return build_failure(
        target,
        name,
        reason if reason is not None else str(exc),
        error_code,
        stage=stage,
        retryable=retryable,
        evidence=evidence,
    )

def safe_filename(value: str, limit: int = 80) -> str:
    value = re.sub(r"[\\/:*?\"<>|\s]+", "_", value or "")
    value = value.strip("._")
    return (value[:limit] or "unknown")


def debug_file_for(
    target: dict,
    issues: list[str],
    name: str,
    *,
    debug_dir: Path = DEFAULT_DEBUG_DIR,
) -> Path:
    issue_text = "-".join(normalize_issue(issue) for issue in issues)
    url_part = safe_filename(urlparse(target["url"]).netloc)
    name_part = safe_filename(name or target.get("name") or "unknown")
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return debug_dir / f"{issue_text}_{name_part}_{url_part}_{timestamp}.txt"


def save_debug_page(
    target: dict,
    issues: list[str],
    name: str,
    content: str,
    reason: str,
    *,
    debug_dir: Path = DEFAULT_DEBUG_DIR,
) -> Path | None:
    if not content:
        return None
    debug_dir.mkdir(parents=True, exist_ok=True)
    path = debug_file_for(target, issues, name, debug_dir=debug_dir)
    lines = [
        f"name: {name}",
        f"url: {target['url']}",
        f"issues: {','.join(issues)}",
        f"reason: {reason}",
        "",
        content,
    ]
    path.write_text("\n".join(lines), encoding="utf-8", errors="replace")
    return path


def crawl_one(
    target: dict,
    issues: list[str],
    *,
    debug_dir: Path = DEFAULT_DEBUG_DIR,
) -> tuple[list[CrawlResult], CrawlFailure | None]:
    url = target["url"]
    configured_name = preserve_configured_name(target.get("name") or "")
    content = ""
    name = configured_name
    parse_keywords = target.get("keywords")
    is_admin_article = bool(parse_admin_article_id(url))
    rendered_admin_article = False

    try:
        kind = adapter_kind(target)
        has_native_record_boundary = bool(
            is_admin_article
            or parse_manager_article_id(url)
            or parse_lottery_article_id(url)
            or parse_user_id(url)
            or target.get("list_detail")
        )
        if not has_native_record_boundary:
            if not any(
                target.get(field)
                for field in (
                    "anchor",
                    "stop_anchor",
                    "decoded_anchor_only",
                    "first_issue_chain",
                )
            ):
                raise CrawlError(
                    ErrorCode.DOCUMENT_BOUNDARY_ERROR,
                    "普通静态站未配置专属正文范围，已停止避免宽关键词误匹配",
                    stage="target_boundary",
                )

        resolved = fetch_target_content(target, issues, configured_name)
        if resolved.source_kind != kind:
            raise CrawlError(
                ErrorCode.ADAPTER_MISMATCH,
                f"页面适配器类型不一致：期望 {kind}，实际 {resolved.source_kind}",
                stage="adapter_dispatch",
            )
        auto_name = resolved.name
        content = resolved.content
        rendered_admin_article = resolved.rendered

        name = configured_name if configured_name != "未命名" else auto_name
        if rendered_admin_article:
            validate_rendered_article_identity(
                f"{auto_name}\n{content}", configured_name, target.get("anchor")
            )
        if "此帖子ID已被下架" in content:
            reason = "页面明确提示：此帖子ID已被下架，无法抓取指定期数"
            debug_path = save_debug_page(
                target,
                issues,
                name,
                content,
                reason,
                debug_dir=debug_dir,
            )
            if debug_path:
                reason = f"{reason}；调试页面：{debug_path.name}"
            return [], build_failure(
                target,
                name,
                reason,
                ErrorCode.CONTENT_NOT_PUBLISHED,
                stage="content_status",
                evidence={"issues": list(issues)},
            )

        if target.get("stats_max_row"):
            issue_map = stats_max_row_numbers(
                content,
                issues,
                target.get("stats_block_keywords"),
            )
            missing_issues = [issue for issue in issues if issue not in issue_map]
            if missing_issues:
                wanted = ",".join(f"{issue}期" for issue in missing_issues)
                keywords_text = "、".join(
                    str(keyword)
                    for keyword in (target.get("stats_block_keywords") or [])
                )
                reason = (
                    f"没有找到 {wanted} 的专属统计区块（{keywords_text}）"
                    "或该区块没有统计结果行"
                )
                debug_path = save_debug_page(
                    target,
                    issues,
                    name,
                    content,
                    reason,
                    debug_dir=debug_dir,
                )
                if debug_path:
                    reason = f"{reason}；调试页面：{debug_path.name}"
                return [], build_failure(
                    target,
                    name,
                    reason,
                    ErrorCode.ISSUE_NOT_FOUND,
                    stage="issue_validation",
                    evidence={
                        "issues": list(issues),
                        "missing_issues": list(missing_issues),
                    },
                )
            results = [
                CrawlResult(
                    url=url,
                    name=name,
                    issue=issue,
                    numbers=numbers,
                    target_id=str(target.get("id") or ""),
                )
                for issue, numbers in issue_map.items()
            ]
            return results, None

        try:
            issue_map = extract_issue_numbers(
                content,
                issues,
                keywords=parse_keywords,
                expected_count=target.get("count"),
                position=target.get("position", "first"),
                strict_ambiguous=True,
                allow_duplicate_numbers=False,
                anchor=target.get("anchor"),
                stop_anchor=target.get("stop_anchor"),
                region=target.get("region"),
                issue_position_window=target.get("issue_position_window"),
                first_issue_chain=target.get("first_issue_chain", False),
                keyword_before_issue=target.get("keyword_before_issue", False),
                keyword_before_issue_window=target.get("keyword_before_issue_window"),
            )
        except CrawlError as exc:
            if not (
                target.get("rendered_fallback_selectors")
                and not is_admin_article
                and exc.code in {ErrorCode.ANCHOR_MISSING, ErrorCode.DOCUMENT_BOUNDARY_ERROR}
            ):
                raise
            rendered_name, rendered_content = render_static_page(
                url,
                target["rendered_fallback_selectors"],
                encoding=target.get("encoding"),
            )
            issue_map = extract_issue_numbers(
                rendered_content,
                issues,
                keywords=parse_keywords,
                expected_count=target.get("count"),
                position=target.get("position", "first"),
                strict_ambiguous=True,
                allow_duplicate_numbers=False,
                anchor=target.get("anchor"),
                stop_anchor=target.get("stop_anchor"),
                region=target.get("region"),
                issue_position_window=target.get("issue_position_window"),
                first_issue_chain=target.get("first_issue_chain", False),
                keyword_before_issue=target.get("keyword_before_issue", False),
                keyword_before_issue_window=target.get("keyword_before_issue_window"),
            )
            auto_name = rendered_name
            content = rendered_content
        missing_issues = [issue for issue in issues if issue not in issue_map]
        if missing_issues and target.get("rendered_fallback_selectors") and not is_admin_article:
            rendered_name, rendered_content = render_static_page(
                url,
                target["rendered_fallback_selectors"],
                encoding=target.get("encoding"),
            )
            rendered_issue_map = extract_issue_numbers(
                rendered_content,
                issues,
                keywords=parse_keywords,
                expected_count=target.get("count"),
                position=target.get("position", "first"),
                strict_ambiguous=True,
                allow_duplicate_numbers=False,
                anchor=target.get("anchor"),
                stop_anchor=target.get("stop_anchor"),
                region=target.get("region"),
                issue_position_window=target.get("issue_position_window"),
                first_issue_chain=target.get("first_issue_chain", False),
                keyword_before_issue=target.get("keyword_before_issue", False),
                keyword_before_issue_window=target.get("keyword_before_issue_window"),
            )
            auto_name = rendered_name
            content = rendered_content
            issue_map = rendered_issue_map
            missing_issues = [issue for issue in issues if issue not in issue_map]
        if missing_issues and is_admin_article and not rendered_admin_article:
            article_id = parse_admin_article_id(url)
            if not article_id:
                raise CrawlError(
                    ErrorCode.ARTICLE_ID_MISMATCH,
                    "没有找到文章 ID",
                    stage="article_identity",
                )
            rendered_name, rendered_content = render_article_admin_page(
                url, article_id, configured_name
            )
            validate_rendered_article_identity(
                f"{rendered_name}\n{rendered_content}",
                configured_name,
                target.get("anchor"),
            )
            rendered_issue_map = extract_issue_numbers(
                rendered_content,
                issues,
                keywords=parse_keywords,
                expected_count=target.get("count"),
                position=target.get("position", "first"),
                strict_ambiguous=True,
                allow_duplicate_numbers=False,
                anchor=target.get("anchor"),
                stop_anchor=target.get("stop_anchor"),
                region=target.get("region"),
                issue_position_window=target.get("issue_position_window"),
                first_issue_chain=target.get("first_issue_chain", False),
                keyword_before_issue=target.get("keyword_before_issue", False),
                keyword_before_issue_window=target.get("keyword_before_issue_window"),
            )
            auto_name = rendered_name
            content = rendered_content
            issue_map = rendered_issue_map
            missing_issues = [issue for issue in issues if issue not in issue_map]

        if missing_issues:
            mismatch = diagnose_issue_mismatch(
                content,
                missing_issues,
                keywords=parse_keywords,
                expected_count=target.get("count"),
                allow_duplicate_numbers=False,
                region=target.get("region"),
                anchor=target.get("anchor"),
                stop_anchor=target.get("stop_anchor"),
                issue_position_window=target.get("issue_position_window"),
                first_issue_chain=target.get("first_issue_chain", False),
                keyword_before_issue=target.get("keyword_before_issue", False),
                keyword_before_issue_window=target.get("keyword_before_issue_window"),
            )
            available = detect_available_issues(
                content,
                keywords=parse_keywords,
                expected_count=target.get("count"),
                position=target.get("position", "first"),
                allow_duplicate_numbers=False,
                anchor=target.get("anchor"),
                stop_anchor=target.get("stop_anchor"),
                region=target.get("region"),
                issue_position_window=target.get("issue_position_window"),
                first_issue_chain=target.get("first_issue_chain", False),
                keyword_before_issue=target.get("keyword_before_issue", False),
                keyword_before_issue_window=target.get("keyword_before_issue_window"),
            )
            wanted = ",".join(f"{issue}期" for issue in missing_issues)
            found_issues = [issue for issue in issues if issue in issue_map]
            partial = ""
            if found_issues:
                partial = (
                    f"已找到 {','.join(issue + '期' for issue in found_issues)}，"
                    f"但缺少 {wanted}，该站本次全部结果均未写入；"
                )
            if mismatch:
                detail = "；".join(
                    f"{issue}期：{diagnostic.message}"
                    for issue, diagnostic in mismatch.items()
                )
                reason = f"{partial}没有找到符合配置的 {wanted} 号码；{detail}"
            elif available:
                found_text = ",".join(
                    f"{issue}期" for issue in nearest_issues(available, missing_issues)
                )
                reason = f"{partial}没有找到指定期数 {wanted} 的号码；本页同栏目找到：{found_text}"
            else:
                reason = f"{partial}没有找到指定期数 {wanted} 的号码；本页同栏目没有识别到可用期数"
            debug_path = save_debug_page(
                target,
                issues,
                name,
                content,
                reason,
                debug_dir=debug_dir,
            )
            if debug_path:
                reason = f"{reason}；调试页面：{debug_path.name}"
            failure_code = (
                next(iter(mismatch.values())).error_code
                if mismatch
                else ErrorCode.ISSUE_NOT_FOUND
            )
            return [], build_failure(
                target,
                name,
                reason,
                failure_code,
                stage="issue_validation",
                evidence={
                    "issues": list(issues),
                    "missing_issues": list(missing_issues),
                    "available_issues": list(available),
                    "diagnostics": {
                        issue: {
                            "message": diagnostic.message,
                            "error_code": diagnostic.error_code.value,
                        }
                        for issue, diagnostic in mismatch.items()
                    },
                },
            )

        results = [
            CrawlResult(
                url=url,
                name=name,
                issue=issue,
                numbers=numbers,
                target_id=str(target.get("id") or ""),
            )
            for issue, numbers in issue_map.items()
        ]
        return results, None

    except Exception as exc:
        name = configured_name if configured_name != "未命名" else urlparse(url).netloc
        reason = str(exc)
        debug_path = save_debug_page(
            target,
            issues,
            name,
            content,
            reason,
            debug_dir=debug_dir,
        )
        if debug_path:
            reason = f"{reason}；调试页面：{debug_path.name}"
        return [], failure_from_exception(
            target,
            name,
            exc,
            reason=reason,
            issues=issues,
        )


def blocked_by_local_socket_policy(failures: list[CrawlFailure]) -> bool:
    return bool(failures) and all(
        item.error_code == ErrorCode.LOCAL_NETWORK_POLICY.value
        for item in failures
    )


def is_transient_failure(failure: CrawlFailure | None) -> bool:
    return bool(failure and failure.retryable)


def _crawl_targets_in_scope(
    target_items: list[tuple[int, dict]],
    issues: list[str],
    max_workers: int,
    label: str = "",
    debug_dir: Path = DEFAULT_DEBUG_DIR,
) -> tuple[dict[int, list[CrawlResult]], dict[int, CrawlFailure | None]]:
    results_by_index: dict[int, list[CrawlResult]] = {}
    failures_by_index: dict[int, CrawlFailure | None] = {}
    total = len(target_items)
    if not total:
        return results_by_index, failures_by_index

    if label:
        print(label)
    started_at = time.monotonic()
    success_count = 0
    failure_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(crawl_one, target, issues, debug_dir=debug_dir): (
                index,
                target,
            )
            for index, target in target_items
        }
        for done_count, future in enumerate(as_completed(future_map), start=1):
            index, target = future_map[future]
            try:
                found, failure = future.result()
            except Exception as exc:
                found = []
                failure = failure_from_exception(
                    target,
                    target.get("name") or urlparse(target["url"]).netloc,
                    exc,
                    issues=issues,
                )

            results_by_index[index] = found
            failures_by_index[index] = failure
            if found and not failure:
                success_count += 1
            else:
                failure_count += 1

            percent = done_count * 100 // total
            current_name = target.get("name") or urlparse(target["url"]).netloc
            elapsed = time.monotonic() - started_at
            print(
                f"\r[进度 {done_count}/{total} {percent}% 成功 {success_count} 失败 {failure_count} "
                f"用时 {elapsed:.1f}s] 当前：{current_name}",
                end="",
                flush=True,
            )

    print()

    return results_by_index, failures_by_index


def crawl_targets(
    target_items: list[tuple[int, dict]],
    issues: list[str],
    workers: int,
    label: str = "",
    *,
    debug_dir: Path = DEFAULT_DEBUG_DIR,
) -> tuple[dict[int, list[CrawlResult]], dict[int, CrawlFailure | None]]:
    with request_scope():
        return _crawl_targets_in_scope(
            target_items,
            issues,
            workers,
            label=label,
            debug_dir=debug_dir,
        )


__all__ = [
    'safe_filename',
    'debug_file_for',
    'save_debug_page',
    'build_failure',
    'failure_from_exception',
    'crawl_one',
    'blocked_by_local_socket_policy',
    'is_transient_failure',
    'crawl_targets',
]
