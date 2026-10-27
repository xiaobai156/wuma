"""合并来源站点（merged_sources）的独立抓取与校验。

与主解析链路完全隔离：只有配置显式声明 ``source.kind == "merged_sources"``
的站点才走这里，其余站点不受任何影响。

规则：
- 每个来源各自抓取、各自按自己的 keywords 取该期号码；
- 各段号码数量必须与声明数量完全一致；
- 拼接后总数必须等于 count，且号码合法、不重复；
- 任一段取不到或数量不符 → 整站失败，不猜值、不跨期借值。
"""
from __future__ import annotations

import re

from ..domain import CrawlResult
from ..errors import CrawlError, ErrorCode
from ..parser import (
    find_number_groups,
    keyword_scoped_number_groups,
    normalize_issue,
    valid_number,
)
from .merged_sources import crawl_merged_source, _source_specs

MERGED_KIND = "merged_sources"


def _issue_numbers_from_segment(
    content: str,
    issue: str,
    keywords: list[str],
    expected: int,
) -> list[str]:
    """在单个来源的正文里取指定期的号码。

    合并来源允许 2 码、3 码这类短组，所以不能直接用主链路的
    ``find_number_groups``（它要求至少 3 个号码）。这里按「本期期号 → 下一个
    期号」切段，取段内第一组数量正好等于 expected 的合法号码。
    """
    normalized = normalize_issue(issue)
    pattern = re.compile(
        rf"(?<!\d)0?{re.escape(normalized)}\s*期(?P<body>.*?)"
        rf"(?=(?<!\d)\d{{3}}\s*期|$)",
        re.S,
    )
    matches = list(pattern.finditer(content))
    if not matches:
        raise CrawlError(
            ErrorCode.ISSUE_NOT_FOUND,
            f"来源中没有 {normalized} 期",
            stage="merged_issue",
        )

    for match in matches:
        segment = match.group(0)
        if not _segment_has_keyword(segment, keywords):
            continue
        for group in _short_number_groups(segment, expected):
            if len(group) != expected:
                continue
            if any(not valid_number(token) for token in group):
                continue
            if len(set(group)) != len(group):
                continue
            return [f"{int(token):02d}" for token in group]

    raise CrawlError(
        ErrorCode.POSITION_MISMATCH,
        f"{normalized} 期在来源中未取到 {expected} 个合法号码",
        stage="merged_numbers",
    )


def _segment_has_keyword(segment: str, keywords: list[str]) -> bool:
    plain = re.sub(r"<[^>]+>", " ", segment)
    return any(str(k) in plain for k in keywords)


def _short_number_groups(segment: str, expected: int) -> list[list[str]]:
    """取段内所有「点/逗号/横线/空格分隔」的号码组，允许 2 码起。

    只认开奖标记之前的部分，避免把开奖号当本期号码。
    """
    plain = re.sub(r"<[^>]+>", " ", segment)
    before_draw = re.split(r"[=＝]?\s*开\s*[:：]", plain, maxsplit=1)[0]
    groups: list[list[str]] = []
    for match in re.finditer(
        r"(?<!\d)(\d{1,2})(?:[.．,，、\s\-/]+(\d{1,2}))+",
        before_draw,
    ):
        tokens = re.findall(r"\d{1,2}", match.group(0))
        if len(tokens) >= 2:
            groups.append(tokens)
    # 也要能取到「13.11」这种紧贴关键词后的短组
    groups.sort(key=lambda g: (len(g) != expected, len(g)))
    return groups


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

    results: list[CrawlResult] = []
    name = str(target.get("name") or "").strip()
    for issue in issues:
        collected: list[str] = []
        for spec in specs:
            part_count = int(spec.get("count") or 0)
            keywords = [str(k) for k in (spec.get("keywords") or []) if str(k).strip()]
            if not keywords:
                raise CrawlError(
                    ErrorCode.KEYWORD_MISMATCH,
                    "merged 来源缺少 keywords",
                    stage="merged_config",
                )
            _, content = crawl_merged_source(spec)
            part = _issue_numbers_from_segment(content, issue, keywords, part_count)
            collected.extend(part)

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
