from __future__ import annotations

from dataclasses import dataclass
import re

from .errors import CrawlError, ErrorCode
from .parser import (
    CANDIDATE_REGION_WINDOW,
    remove_fragment,
    origin,
    normalize_issue,
    parse_issues,
    fullwidth_to_halfwidth,
    html_to_text,
    unique_keep_order,
    valid_number,
    keyword_scoped_number_groups,
    has_duplicate_numbers,
    has_pending_open_marker,
    select_candidate,
    issue_segment_matches,
    issue_segments,
    all_issue_segment_matches,
    any_keyword_matches,
    as_list,
    find_anchor_index,
    scope_text_by_anchor,
    normalize_region,
    candidate_region,
    filter_candidates_by_region,
    needs_strict_region_window,
    scope_first_issue_chain,
    issue_position_window_starts,
    keyword_before_issue_candidates,
    clean_name,
    preserve_configured_name,
    extract_name_from_text,
    decode_strdecode_payloads,
    select_decoded_anchor_parts,
)


@dataclass(frozen=True)
class IssueMismatch:
    message: str
    error_code: ErrorCode

def issue_candidates_for_validation(
    text: str,
    issue: str,
    keywords: list[str] | None,
    expected_count: int | None,
    allow_duplicate_numbers: bool = False,
    keyword_before_issue: bool = False,
    keyword_before_issue_window: int | None = None,
    allowed_window_starts: set[int] | None = None,
) -> list[tuple[list[str], str, int]]:
    candidates: list[tuple[list[str], str, int]] = []
    for match in issue_segment_matches(text, issue):
        if allowed_window_starts is not None and match.start() not in allowed_window_starts:
            continue
        segment = match.group(0)
        if not any_keyword_matches(segment, keywords):
            continue
        for group in keyword_scoped_number_groups(segment, keywords):
            if expected_count and len(group) != expected_count:
                continue
            if not allow_duplicate_numbers and has_duplicate_numbers(group):
                continue
            candidates.append((group, segment, match.start()))

    if keyword_before_issue:
        candidates.extend(
            keyword_before_issue_candidates(
                text,
                issue,
                keywords,
                expected_count,
                int(keyword_before_issue_window or 160),
                allowed_window_starts=allowed_window_starts,
            )
        )
    return candidates


def reject_conflicting_issue_candidates(
    text: str,
    issues: list[str],
    keywords: list[str] | None,
    expected_count: int | None,
    allow_duplicate_numbers: bool = False,
    keyword_before_issue: bool = False,
    keyword_before_issue_window: int | None = None,
    allowed_window_starts: set[int] | None = None,
    region: str | None = None,
    text_length: int | None = None,
    first_issue_chain: bool = False,
    issue_position_window: int | None = None,
) -> None:
    for issue in issues:
        candidates = issue_candidates_for_validation(
            text,
            issue,
            keywords,
            expected_count,
            allow_duplicate_numbers=allow_duplicate_numbers,
            keyword_before_issue=keyword_before_issue,
            keyword_before_issue_window=keyword_before_issue_window,
            allowed_window_starts=allowed_window_starts,
        )
        strict_region = needs_strict_region_window(candidates)
        candidates = filter_candidates_by_region(
            candidates,
            region,
            None if (first_issue_chain or issue_position_window) else text_length,
            strict_window=strict_region,
            require_region=strict_region,
        )
        distinct: list[list[str]] = []
        seen = set()
        for numbers, _segment, _start in candidates:
            key = tuple(numbers)
            if key not in seen:
                seen.add(key)
                distinct.append(numbers)
        if len(distinct) > 1:
            preview = " | ".join(",".join(numbers) for numbers in distinct[:5])
            raise CrawlError(
                ErrorCode.CANDIDATE_CONFLICT,
                f"{normalize_issue(issue)}期 候选不唯一，已停止输出避免抓错：{preview}",
                stage="candidate_validation",
            )


def extract_issue_numbers(
    text: str,
    issues: list[str],
    keywords: list[str] | None = None,
    expected_count: int | None = None,
    position: str = "first",
    strict_ambiguous: bool = False,
    allow_duplicate_numbers: bool = False,
    anchor=None,
    stop_anchor=None,
    region: str | None = None,
    issue_position_window: int | None = None,
    first_issue_chain: bool = False,
    keyword_before_issue: bool = False,
    keyword_before_issue_window: int | None = None,
) -> dict[str, list[str]]:
    found = {}
    text = html_to_text(text)
    text = scope_text_by_anchor(text, anchor, stop_anchor)
    if first_issue_chain:
        text = scope_first_issue_chain(text, issues, keywords, expected_count, region=region)
    allowed_window_starts = issue_position_window_starts(
        text,
        keywords,
        expected_count,
        region,
        issue_position_window,
        keyword_before_issue,
        keyword_before_issue_window,
    )
    if strict_ambiguous:
        # 只在配置确认的链和位置窗口内判冲突；范围外候选不是高可信候选。
        reject_conflicting_issue_candidates(
            text,
            issues,
            keywords,
            expected_count,
            allow_duplicate_numbers=allow_duplicate_numbers,
            keyword_before_issue=keyword_before_issue,
            keyword_before_issue_window=keyword_before_issue_window,
            allowed_window_starts=allowed_window_starts,
            region=region,
            text_length=len(text),
            first_issue_chain=first_issue_chain,
            issue_position_window=issue_position_window,
        )
    for issue in issues:
        candidates = []
        for match in issue_segment_matches(text, issue):
            if allowed_window_starts is not None and match.start() not in allowed_window_starts:
                continue
            segment = match.group(0)
            if not any_keyword_matches(segment, keywords):
                continue
            groups = keyword_scoped_number_groups(segment, keywords)
            for group in groups:
                if expected_count and len(group) != expected_count:
                    # 数量不符直接拒绝，不能从其他组或其他位置补数字来凑够 count。
                    continue
                candidates.append((group, segment, match.start()))
        if keyword_before_issue:
            candidates.extend(
                keyword_before_issue_candidates(
                    text,
                    issue,
                    keywords,
                    expected_count,
                    int(keyword_before_issue_window or 160),
                    allowed_window_starts=allowed_window_starts,
                )
            )
        if candidates:
            strict_region = needs_strict_region_window(candidates)
            normalized_region = normalize_region(region)
            candidates = filter_candidates_by_region(
                candidates,
                region,
                None if (first_issue_chain or issue_position_window) else len(text),
                strict_window=strict_region,
                require_region=strict_region,
            )
            candidate_position = position
            candidate_strict_ambiguous = strict_ambiguous
            if strict_region and normalized_region:
                candidate_position = "last" if normalized_region == "bottom" else "first"
            selected = select_candidate(
                [(group, segment) for group, segment, _ in candidates],
                position=candidate_position,
                strict_ambiguous=candidate_strict_ambiguous,
                issue=issue,
                allow_duplicate_numbers=allow_duplicate_numbers,
            )
            if selected:
                found[issue] = selected
    return found


def diagnose_issue_mismatch(
    text: str,
    issues: list[str],
    keywords: list[str] | None = None,
    expected_count: int | None = None,
    allow_duplicate_numbers: bool = False,
    region: str | None = None,
    anchor=None,
    stop_anchor=None,
    issue_position_window: int | None = None,
    first_issue_chain: bool = False,
    keyword_before_issue: bool = False,
    keyword_before_issue_window: int | None = None,
) -> dict[str, IssueMismatch]:
    diagnostics = {}
    text = html_to_text(text)
    text = scope_text_by_anchor(text, anchor, stop_anchor)
    if first_issue_chain:
        text = scope_first_issue_chain(text, issues, keywords, expected_count, region=region)
    normalized_region = normalize_region(region)
    allowed_window_starts = issue_position_window_starts(
        text,
        keywords,
        expected_count,
        region,
        issue_position_window,
        keyword_before_issue,
        keyword_before_issue_window,
    )

    for issue in issues:
        issue = normalize_issue(issue)
        messages = []
        error_codes: set[ErrorCode] = set()
        region_candidates: list[tuple[list[str], str, int]] = []
        skipped_by_window = False
        matched_keyword = False
        saw_number_group = False
        for match in issue_segment_matches(text, issue):
            if allowed_window_starts is not None and match.start() not in allowed_window_starts:
                skipped_by_window = True
                continue
            segment = match.group(0)
            if not any_keyword_matches(segment, keywords):
                messages.append("找到该期，但关键词不匹配")
                error_codes.add(ErrorCode.KEYWORD_MISMATCH)
                continue

            matched_keyword = True
            groups = keyword_scoped_number_groups(segment, keywords)
            for group in groups:
                saw_number_group = True
                numbers = ",".join(group)
                if expected_count and len(group) != expected_count:
                    messages.append(
                        f"找到该期号码 {numbers}，实际 {len(group)} 个，配置要求 {expected_count} 个，已拒绝硬凑"
                    )
                    error_codes.add(ErrorCode.NUMBER_INVALID)
                    continue

                if not allow_duplicate_numbers and has_duplicate_numbers(group):
                    messages.append(f"找到该期号码 {numbers}，但号码有重复")
                    error_codes.add(ErrorCode.NUMBER_INVALID)
                    continue

                region_candidates.append((group, segment, match.start()))

        if keyword_before_issue:
            extra_candidates = keyword_before_issue_candidates(
                text,
                issue,
                keywords,
                expected_count,
                int(keyword_before_issue_window or 160),
                allowed_window_starts=allowed_window_starts,
            )
            if extra_candidates:
                matched_keyword = True
                saw_number_group = True
                region_candidates.extend(extra_candidates)

        if matched_keyword and not saw_number_group:
            messages.append("找到该期和关键词，但没有识别到号码组")
            error_codes.add(ErrorCode.NUMBER_INVALID)
        elif skipped_by_window and not region_candidates:
            messages.append(f"找到该期候选，但不在配置位置 {normalized_region or '未配置'} 最新 {issue_position_window} 条同栏目内")
            error_codes.add(ErrorCode.POSITION_MISMATCH)
        elif region_candidates:
            strict_region = needs_strict_region_window(region_candidates)
            if strict_region and not normalized_region:
                messages.append(
                    f"找到该期 {len(region_candidates)} 组候选，但配置缺少 top/bottom/顶部/尾部，已按严格规则拒绝"
                )
                error_codes.add(ErrorCode.POSITION_MISMATCH)
            selected = filter_candidates_by_region(
                region_candidates,
                normalized_region,
                None if (first_issue_chain or issue_position_window) else len(text),
                strict_window=strict_region,
                require_region=strict_region,
            )
            if not selected:
                preview = " | ".join(",".join(group) for group, _segment, _start in region_candidates[:5])
                messages.append(
                    f"找到该期符合数量的候选，但不在配置位置 {normalized_region or '未配置'} 最新 {CANDIDATE_REGION_WINDOW} 组内：{preview}"
                )
                error_codes.add(ErrorCode.POSITION_MISMATCH)
        if messages:
            if ErrorCode.POSITION_MISMATCH in error_codes:
                error_code = ErrorCode.POSITION_MISMATCH
            elif ErrorCode.NUMBER_INVALID in error_codes:
                error_code = ErrorCode.NUMBER_INVALID
            elif ErrorCode.KEYWORD_MISMATCH in error_codes:
                error_code = ErrorCode.KEYWORD_MISMATCH
            else:
                error_code = ErrorCode.OUTPUT_VALIDATION_FAILED
            diagnostics[issue] = IssueMismatch(
                "；".join(unique_keep_order(messages)),
                error_code,
            )
    return diagnostics


def detect_available_issues(
    text: str,
    keywords: list[str] | None = None,
    expected_count: int | None = None,
    position: str = "first",
    allow_duplicate_numbers: bool = False,
    anchor=None,
    stop_anchor=None,
    region: str | None = None,
    issue_position_window: int | None = None,
    first_issue_chain: bool = False,
    keyword_before_issue: bool = False,
    keyword_before_issue_window: int | None = None,
) -> list[str]:
    text = html_to_text(text)
    text = scope_text_by_anchor(text, anchor, stop_anchor)
    candidates = unique_keep_order(
        normalize_issue(match.group(1))
        for match in re.finditer(r"(?<!\d)0?(\d{3})\s*期", text)
    )
    available = []
    for issue in candidates:
        found = extract_issue_numbers(
            text,
            [issue],
            keywords=keywords,
            expected_count=expected_count,
            position=position,
            allow_duplicate_numbers=allow_duplicate_numbers,
            anchor=None,
            stop_anchor=None,
            region=region,
            issue_position_window=issue_position_window,
            first_issue_chain=first_issue_chain,
            keyword_before_issue=keyword_before_issue,
            keyword_before_issue_window=keyword_before_issue_window,
        )
        if issue in found:
            available.append(issue)
    return available


def nearest_issues(available: list[str], wanted: list[str], limit: int = 20) -> list[str]:
    wanted_nums = [int(issue) for issue in wanted]
    return sorted(
        unique_keep_order(available),
        key=lambda issue: (
            min(abs(int(issue) - wanted_num) for wanted_num in wanted_nums),
            -int(issue),
        ),
    )[:limit]


__all__ = [
    'IssueMismatch',
    'issue_candidates_for_validation',
    'reject_conflicting_issue_candidates',
    'extract_issue_numbers',
    'diagnose_issue_mismatch',
    'detect_available_issues',
    'nearest_issues',
]
