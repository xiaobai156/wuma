"""合并来源站点（merged_sources）的独立抓取与校验。

与主解析链路完全隔离：只有配置显式声明 ``source.kind == "merged_sources"``
的站点才走这里，其余站点不受任何影响。

取数规则一律复用主链路已加固过的实现，不另写一套正则：
- 段边界用 ``neighbor_issue_pattern``（认 3 位期号、四位笔误、缺「期」字冒号写法）
- 开奖号识别用 ``DRAW_MARKER_RE`` + ``_tail_has_kill_numbers``（认 开: / 开龙02 / 开 13 24）
- 栏目归属用 ``keyword_scoped_number_groups``（号码必须落在关键词所在行）

fail-closed：任一段数量不符、号码非法、跨来源重复、同期多值冲突，一律判失败。
"""
from __future__ import annotations

import re

from ..domain import CrawlResult
from ..errors import CrawlError, ErrorCode
from ..parser import (
    DRAW_MARKER_RE,
    find_number_groups,
    keyword_scoped_number_groups,
    neighbor_issue_pattern,
    normalize_issue,
    valid_number,
)
from .merged_sources import _source_specs, crawl_merged_source

MERGED_KIND = "merged_sources"

# 日期/时间形态：2026-10-27、10-27、19:41、2026/10/27
_DATE_LIKE_RE = re.compile(
    r"\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}"
    r"|\b\d{1,2}\s*[-/]\s*\d{1,2}\b"
    r"|\b\d{1,2}\s*[:：]\s*\d{2}\b"
)


def _strip_date_like(segment: str) -> str:
    """把日期/时间形态整段剔除，避免 10-27、19:41 被当成号码。"""
    return _DATE_LIKE_RE.sub(" ", segment)


def _issue_segments(content: str, issue: str) -> list[str]:
    """取本期所有段落：本期期号 → 下一个期号标签。边界复用主链路规则。

    页面上同一期常出现多次（标题行 + 正文行），全部返回，由上层逐个尝试。
    """
    normalized = normalize_issue(issue)
    pattern = re.compile(
        rf"(?<!\d)0?{re.escape(normalized)}\s*期(?P<body>.*?)"
        rf"(?={neighbor_issue_pattern(normalized)}|$)",
        re.S,
    )
    segments = [m.group(0) for m in pattern.finditer(content)]
    if not segments:
        raise CrawlError(
            ErrorCode.ISSUE_NOT_FOUND,
            f"来源中没有 {normalized} 期",
            stage="merged_issue",
        )
    return segments


def _groups_for_issue(segment: str, keywords: list[str], expected: int) -> list[list[str]]:
    """取本期段内属于该栏目的候选号码组。

    优先用主链路的 ``keyword_scoped_number_groups``（号码必须落在关键词所在行）；
    该函数要求至少 3 码，2 码来源会取不到，此时才用短组正则兜底。
    兜底同样只在开奖标记之前取，并先剔除日期时间形态。
    """
    cleaned = _strip_date_like(segment)
    groups = keyword_scoped_number_groups(cleaned, keywords)

    # 同一段里若本期行还带了第二个同栏目号码组，也要收进来判冲突。
    # 但别栏目的号码（没有紧跟本栏目关键词）必须排除。
    before_draw = DRAW_MARKER_RE.split(cleaned, maxsplit=1)[0]
    keyword_list = [str(k) for k in keywords if str(k).strip()]
    for match in re.finditer(r"[^\n]+", before_draw):
        line = match.group(0)
        if not any(k in line for k in keyword_list):
            continue
        # 关键词必须出现在号码之前，才算本栏目的号码
        first_kw = min(line.find(k) for k in keyword_list if k in line)
        tail = line[first_kw:]
        for group in find_number_groups(tail):
            if group not in groups:
                groups.append(group)

    if groups:
        return groups

    # 2 码这类短组：只取关键词同一行内的号码
    short: list[list[str]] = []
    for line in before_draw.splitlines() or [before_draw]:
        if not any(str(k) in line for k in keywords):
            continue
        for match in re.finditer(r"(?<!\d)(\d{1,2})(?:[.．,，、\s\-/]+(\d{1,2}))+", line):
            tokens = re.findall(r"\d{1,2}", match.group(0))
            if len(tokens) >= 2:
                short.append(tokens)
    return short


def _pick_group(
    groups: list[list[str]],
    expected: int,
    issue: str,
    source_label: str,
) -> list[str]:
    """从候选组里挑出数量正好等于 expected 的合法组；多个不同值即判冲突。"""
    picked: list[list[str]] = []
    for group in groups:
        if len(group) != expected:
            continue
        if any(not valid_number(token) for token in group):
            continue
        if len(set(group)) != len(group):
            continue
        normalized = [f"{int(token):02d}" for token in group]
        if normalized not in picked:
            picked.append(normalized)

    if not picked:
        raise CrawlError(
            ErrorCode.POSITION_MISMATCH,
            f"{normalize_issue(issue)} 期在来源「{source_label}」中未取到 "
            f"{expected} 个合法号码",
            stage="merged_numbers",
        )
    if len(picked) > 1:
        preview = " | ".join(",".join(g) for g in picked[:5])
        raise CrawlError(
            ErrorCode.CANDIDATE_CONFLICT,
            f"{normalize_issue(issue)} 期在来源「{source_label}」出现多个不同结果，"
            f"已停止避免取错：{preview}",
            stage="merged_conflict",
        )
    return picked[0]


def _numbers_from_source(
    content: str,
    issue: str,
    keywords: list[str],
    expected: int,
    source_label: str,
) -> list[str]:
    """在单个来源里取本期号码。

    页面上同一期可能出现多次（标题行、正文行、以及上一圈的历史记录）。
    按出现顺序逐个段落尝试，取第一个能给出合法组的段落即停；
    同一段落内出现多个不同结果仍判冲突（说明该段落本身有歧义）。
    """
    segments = _issue_segments(content, issue)
    for segment in segments:
        groups = _groups_for_issue(segment, keywords, expected)
        if not groups:
            continue
        try:
            return _pick_group(groups, expected, issue, source_label)
        except CrawlError as exc:
            # 本段无合法组（数量不符）时继续看下一段；冲突则直接上抛
            if exc.code == ErrorCode.CANDIDATE_CONFLICT:
                raise
            continue

    raise CrawlError(
        ErrorCode.POSITION_MISMATCH,
        f"{normalize_issue(issue)} 期在来源「{source_label}」中未取到 "
        f"{expected} 个合法号码",
        stage="merged_numbers",
    )


def crawl_merged_target(target: dict, issues: list[str]) -> list[CrawlResult]:
    """抓取合并来源站点，返回本期结果。任一段失败即整站失败。"""
    specs = _source_specs(target)
    expected_total = int(target.get("count") or 0)
    declared = sum(int(spec.get("count") or 0) for spec in specs)
    if expected_total and declared != expected_total:
        raise CrawlError(
            ErrorCode.DOCUMENT_BOUNDARY_ERROR,
            f"merged 各段数量之和 {declared} 与 count {expected_total} 不一致",
            stage="merged_config",
        )

    # 每期只抓一次每个来源，避免多期重复请求
    fetched: list[tuple[dict, str]] = []
    for spec in specs:
        part_count = int(spec.get("count") or 0)
        keywords = [str(k) for k in (spec.get("keywords") or []) if str(k).strip()]
        if not keywords:
            raise CrawlError(
                ErrorCode.KEYWORD_MISMATCH,
                "merged 来源缺少 keywords",
                stage="merged_config",
            )
        if part_count <= 0:
            raise CrawlError(
                ErrorCode.DOCUMENT_BOUNDARY_ERROR,
                "merged 来源缺少有效 count",
                stage="merged_config",
            )
        _, content = crawl_merged_source(spec)
        fetched.append((spec, content))

    results: list[CrawlResult] = []
    name = str(target.get("name") or "").strip()
    for issue in issues:
        collected: list[str] = []
        for spec, content in fetched:
            part_count = int(spec.get("count") or 0)
            keywords = [str(k) for k in (spec.get("keywords") or []) if str(k).strip()]
            label = str(spec.get("name") or spec.get("url") or "").strip() or "来源"
            collected.extend(
                _numbers_from_source(content, issue, keywords, part_count, label)
            )

        if expected_total and len(collected) != expected_total:
            raise CrawlError(
                ErrorCode.POSITION_MISMATCH,
                f"{normalize_issue(issue)} 期拼接后共 {len(collected)} 个号码，"
                f"应为 {expected_total}",
                stage="merged_total",
            )
        if len(set(collected)) != len(collected):
            raise CrawlError(
                ErrorCode.CANDIDATE_CONFLICT,
                f"{normalize_issue(issue)} 期拼接后存在重复号码",
                stage="merged_total",
            )

        results.append(
            CrawlResult(
                url=str(target.get("url") or specs[0].get("url") or ""),
                name=name,
                issue=normalize_issue(issue),
                numbers=collected,
                target_id=str(target.get("id") or ""),
            )
        )
    return results


__all__ = ["MERGED_KIND", "crawl_merged_target"]
