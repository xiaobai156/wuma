"""合并来源站点（merged_sources）的独立抓取与校验。

与主解析链路完全隔离：只有配置显式声明 ``source.kind == "merged_sources"``
的站点才走这里，其余站点不受任何影响。

取数规则一律复用主链路已加固过的实现，不另写一套正则：
- 段边界用 ``neighbor_issue_pattern``（认 3 位期号、四位笔误、缺「期」字冒号写法）
- 开奖号识别用 ``DRAW_MARKER_RE``（认 开: / 开龙02 / 开 13 24）
- 栏目归属用 ``keyword_scoped_number_groups`` + 归一化关键词比较

fail-closed 原则：
- 任一段数量不符、号码非法、跨来源重复、同期多值冲突，一律判失败；
- 本圈段落里一旦出现号码，就必须给出唯一合法组，取不到即失败，绝不跳到
  后续段落（那是上一圈的历史记录，会跨期借值）。
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
    normalize_keyword,
    valid_number,
)
from .merged_sources import _source_specs, crawl_merged_source

MERGED_KIND = "merged_sources"

# 四位数年份：2026-10-27 / 2026/10/27 / 2026年10月27日。年份不可能是号码，永远剔。
_YEAR_DATE_RE = re.compile(
    r"(?<!\d)\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?"
)
# 时:分：19:41。冒号分隔，不可能是号码组，永远剔。
_CLOCK_RE = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3])\s*[:：]\s*[0-5]\d(?!\d)")
# 裸「月-日」：只在**紧跟日期词**时才剔。没有日期词的 12-13、11-13-15、16-20
# 是号码，必须保留，否则 2 码来源会被整组剔掉。
_MONTH_DAY_RE = re.compile(
    r"(?P<word>更新于|更新时间|更新日期|更新|日期|时间|发布于|发布时间|发布|"
    r"开奖时间|开奖|最后更新|最后)\s*[:：]?\s*"
    r"(?:0?[1-9]|1[0-2])\s*[-/]\s*(?:0?[1-9]|[12]\d|3[01])"
    r"(?!\s*[-/]\s*\d)(?!\d)"
)


def _strip_date_like(segment: str) -> str:
    """剔除日期/时间形态，避免 10-27、19:41 被当成号码。

    - 四位数年份、时:分 永远剔除（不可能是号码）；
    - 裸「月-日」只在紧跟日期词时剔除，否则视为号码。
    """
    text = _YEAR_DATE_RE.sub(" ", segment)
    text = _CLOCK_RE.sub(" ", text)
    text = _MONTH_DAY_RE.sub(lambda m: m.group("word"), text)
    return text


# 期号循环一周是 365。文档里期号从大到小排列，跨圈时会出现一次大幅向上跳
# （如 292 → 365、1 → 365）。该位置之后出现的同期号属于上一圈，必须排除，
# 否则本圈缺数据时会借上一圈的值。
_WRAP_JUMP = 50


def _wrap_boundary(content: str) -> int:
    """返回跨圈分界位置；找不到跨圈时返回文本长度（即整篇都算本圈）。"""
    labels = re.finditer(r"(?<!\d)(\d{1,3})\s*期", content)
    previous = None
    for match in labels:
        value = int(match.group(1))
        if not 1 <= value <= 365:
            continue
        if previous is not None and value - previous > _WRAP_JUMP:
            return match.start()
        previous = value
    return len(content)


def _issue_segments(content: str, issue: str) -> list[str]:
    """取本期所有段落：本期期号 → 下一个期号标签。边界复用主链路规则。

    页面上同一期常出现多次（标题行 + 正文行 + 上一圈历史记录），全部返回，
    由上层按顺序判断。
    """
    normalized = normalize_issue(issue)
    boundary = _wrap_boundary(content)
    current_cycle = content[:boundary]
    pattern = re.compile(
        rf"(?<!\d)0?{re.escape(normalized)}\s*期(?P<body>.*?)"
        rf"(?={neighbor_issue_pattern(normalized)}|$)",
        re.S,
    )
    segments = [m.group(0) for m in pattern.finditer(current_cycle)]
    if not segments:
        raise CrawlError(
            ErrorCode.ISSUE_NOT_FOUND,
            f"来源中没有 {normalized} 期",
            stage="merged_issue",
        )
    return segments


def _normalized_positions(line: str, keywords: list[str]) -> list[int]:
    """关键词在归一化文本里的位置。兼容 绝杀2码/绝杀②码/绝杀二码 写法差异。"""
    normalized_line = normalize_keyword(line)
    positions = []
    for keyword in keywords:
        normalized_keyword = normalize_keyword(keyword)
        if not normalized_keyword:
            continue
        index = normalized_line.find(normalized_keyword)
        if index >= 0:
            positions.append(index)
    return positions


def _line_has_keyword(line: str, keywords: list[str]) -> bool:
    return bool(_normalized_positions(line, keywords))


def _tail_after_keyword(line: str, keywords: list[str]) -> str:
    """切出「最后一个本栏目关键词之后」的文本，并截掉开奖标记及其后内容。"""
    positions = _normalized_positions(line, keywords)
    if not positions:
        return ""
    tail = line[max(positions):]
    return DRAW_MARKER_RE.split(tail, maxsplit=1)[0]


# 别栏目标记：出现这些字样说明前缀里的号码属于另一栏，不是本栏目的号码。
_OTHER_COLUMN_RE = re.compile(
    r"杀\s*[0-9一二三四五六七八九十]+\s*[肖码头尾波]"
    r"|绝杀\s*[0-9一二三四五六七八九十]*\s*[肖码头尾波]"
    r"|中特|平特|波色|生肖|杀料|资料"
)


def _prefix_has_other_column(prefix: str) -> bool:
    """前缀里是否有别的栏目标记。有则前缀号码归属别栏目，可忽略。"""
    return bool(_OTHER_COLUMN_RE.search(normalize_keyword(prefix)))


def _numbers_after_keyword(line: str, keywords: list[str]) -> list[list[str]]:
    """只取「关键词之后」的号码组，避免把别栏目、期号前的号码算进来。

    关键词之前若出现号码：只有当这些号码前面另有别栏目标记时才忽略（它们属于
    别栏目）；否则说明本行号码写在本栏目关键词之前，顺序异常，整行不认。
    """
    positions = _normalized_positions(line, keywords)
    if not positions:
        return []
    start = max(positions)
    prefix = line[:start]
    has_prefix_numbers = bool(
        find_number_groups(prefix) or re.search(r"(?<!\d)\d{1,2}[.．,，、]", prefix)
    )
    if has_prefix_numbers and not _prefix_has_other_column(prefix):
        return []
    tail = _tail_after_keyword(line, keywords)
    if not tail:
        return []
    return find_number_groups(tail)


def _short_groups_after_keyword(line: str, keywords: list[str]) -> list[list[str]]:
    """2 码这类短组：只取关键词之后的、点/逗号/空格/横线分隔的号码。"""
    positions = _normalized_positions(line, keywords)
    if not positions:
        return []
    start = max(positions)
    prefix = line[:start]
    has_prefix_numbers = bool(re.search(r"(?<!\d)\d{1,2}[.．,，、]", prefix))
    if has_prefix_numbers and not _prefix_has_other_column(prefix):
        return []
    tail = _tail_after_keyword(line, keywords)
    if not tail:
        return []
    groups: list[list[str]] = []
    for match in re.finditer(r"(?<!\d)(\d{1,2})(?:[.．,，、\s\-]+(\d{1,2}))+", tail):
        tokens = re.findall(r"\d{1,2}", match.group(0))
        if len(tokens) >= 2:
            groups.append(tokens)
    return groups


def _groups_for_issue(
    segment: str,
    keywords: list[str],
    expected: int,
) -> list[list[str]]:
    """取本期段内属于该栏目的候选号码组。

    只认「本栏目关键词之后」的号码：期号前的号码、别栏目的号码都不算。
    主链路 ``keyword_scoped_number_groups`` 只判断关键词在同一行，不判断
    号码在关键词之前还是之后，所以这里不直接用它，而是按行切出关键词之后
    的文本再取数。
    """
    cleaned = _strip_date_like(segment)
    groups: list[list[str]] = []

    for line in re.split(r"[\r\n]+", cleaned):
        if not line.strip() or not _line_has_keyword(line, keywords):
            continue
        for group in _numbers_after_keyword(line, keywords):
            if group not in groups:
                groups.append(group)

    if groups:
        return groups

    # 2 码这类短组兜底：同样只认关键词之后的号码
    short: list[list[str]] = []
    for line in re.split(r"[\r\n]+", cleaned):
        if not line.strip() or not _line_has_keyword(line, keywords):
            continue
        for group in _short_groups_after_keyword(line, keywords):
            if group not in short:
                short.append(group)
    return short


def _pick_group(
    groups: list[list[str]],
    expected: int,
    issue: str,
    source_label: str,
) -> list[str]:
    """挑出数量正好等于 expected 的合法组；多个不同值即判冲突。"""
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


# 站方明确表示「本期还没发」的标记。出现这种标记说明本圈还没数据，
# 后面段落里的号码必然是上一圈的历史记录，必须判失败而不是采用。
_PENDING_RE = re.compile(
    r"待更新|未更新|待发布|未发布|暂无|敬请期待|尚未更新|暂未|待定|未开奖"
)


def _numbers_from_source(
    content: str,
    issue: str,
    keywords: list[str],
    expected: int,
    source_label: str,
) -> list[str]:
    """在单个来源里取本期号码，全程 fail-closed。

    规则：
    - 段落里完全没有号码（标题行）→ 跳过；
    - 段落里出现「待更新/未发布」标记 → 本圈无数据，若之后又出现号码，
      说明那是上一圈的，直接失败（跨圈借值）；
    - 段落里有号码但取不到合法组 → 失败，不跳下一段；
    - 多个段落给出不同结果 → 判冲突失败。
    """
    segments = _issue_segments(content, issue)
    pending_at = -1
    picked: list[list[str]] = []

    for index, segment in enumerate(segments):
        if _PENDING_RE.search(segment):
            if pending_at < 0:
                pending_at = index
            continue
        if not _segment_has_any_number(segment):
            continue
        if pending_at >= 0:
            raise CrawlError(
                ErrorCode.POSITION_MISMATCH,
                f"{normalize_issue(issue)} 期在来源「{source_label}」标记为未更新，"
                f"后面的号码属上一圈，已停止避免跨期借值",
                stage="merged_stale_cycle",
            )
        groups = _groups_for_issue(segment, keywords, expected)
        if not groups:
            raise CrawlError(
                ErrorCode.POSITION_MISMATCH,
                f"{normalize_issue(issue)} 期在来源「{source_label}」的段落里"
                f"找不到本栏目（{'/'.join(keywords)}）的号码",
                stage="merged_numbers",
            )
        value = _pick_group(groups, expected, issue, source_label)
        if value not in picked:
            picked.append(value)

    if not picked:
        raise CrawlError(
            ErrorCode.POSITION_MISMATCH,
            f"{normalize_issue(issue)} 期在来源「{source_label}」中未取到 "
            f"{expected} 个合法号码",
            stage="merged_numbers",
        )
    if len(picked) > 1:
        preview = " | ".join(",".join(v) for v in picked[:5])
        raise CrawlError(
            ErrorCode.CANDIDATE_CONFLICT,
            f"{normalize_issue(issue)} 期在来源「{source_label}」出现多个不同结果，"
            f"已停止避免取错：{preview}",
            stage="merged_conflict",
        )
    return picked[0]


def _segment_has_any_number(segment: str) -> bool:
    """段落里是否出现过任何号码形态（含单独 1 码、2 码这类短组）。

    判定范围只到开奖标记之前，避免开奖号让「无号码的标题段」被误判为有号码。
    """
    cleaned = _strip_date_like(segment)
    cleaned = DRAW_MARKER_RE.split(cleaned, maxsplit=1)[0]
    if find_number_groups(cleaned):
        return True
    # 去掉期号本身（如 300期 的 300），剩下的数字才算号码
    without_issue = re.sub(r"(?<!\d)0?\d{2,4}\s*期", " ", cleaned)
    without_issue = re.sub(r"<[^>]+>", " ", without_issue)
    # 关键词里的数字（绝杀5码 的 5）不是号码，先剔掉再判断
    without_issue = re.sub(r"[0-9一二三四五六七八九十]+\s*[肖码头尾波段]", " ", without_issue)
    return bool(re.search(r"(?<!\d)\d{1,2}(?!\d)", without_issue))


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

    # 先校验全部配置，再统一抓取，避免配置有误时白抓
    prepared: list[tuple[dict, list[str], int, str]] = []
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
        label = str(spec.get("name") or spec.get("url") or "").strip() or "来源"
        prepared.append((spec, keywords, part_count, label))

    # 每个来源只抓一次，多期共用
    fetched: list[tuple[list[str], int, str, str]] = []
    for spec, keywords, part_count, label in prepared:
        _, content = crawl_merged_source(spec)
        fetched.append((keywords, part_count, label, content))

    results: list[CrawlResult] = []
    name = str(target.get("name") or "").strip()
    for issue in issues:
        collected: list[str] = []
        for keywords, part_count, label, content in fetched:
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
