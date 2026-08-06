from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path

from .adapters.registry import adapter_kind
from .engine import crawl_one
from .identity import canonical_url
from .parser import normalize_issue, preserve_configured_name


@dataclass
class CandidateCheck:
    name: str
    url: str
    status: str
    stage: str
    reason: str
    issues: list[str] = field(default_factory=list)
    duplicate_matches: list[dict] = field(default_factory=list)


def parser_signature(target: dict) -> str:
    fields = {
        "kind": adapter_kind(target),
        "region": target.get("region"),
        "count": target.get("count"),
        "keywords": target.get("keywords"),
        "anchor": target.get("anchor"),
        "stop_anchor": target.get("stop_anchor"),
        "decoded_anchor_only": target.get("decoded_anchor_only"),
        "decoded_stop_anchor": target.get("decoded_stop_anchor"),
        "decoded_anchor_to_end": target.get("decoded_anchor_to_end"),
        "first_issue_chain": target.get("first_issue_chain"),
        "issue_position_window": target.get("issue_position_window"),
        "list_title_keywords": target.get("list_title_keywords"),
    }
    return json.dumps(fields, ensure_ascii=False, sort_keys=True)


def load_cache_records(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("recent_10_cache.json 根对象或版本无效")
    records = data.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("recent_10_cache.json 没有有效基准记录")
    return records


def cache_issue_window(records: list[dict], count: int = 10) -> list[str]:
    issues = sorted(
        {
            int(normalize_issue(record.get("issue", "")))
            for record in records
            if normalize_issue(record.get("issue", "")).isdigit()
        }
    )
    if len(issues) < 2:
        raise ValueError("recent_10_cache.json 不足两个有效基准期")
    latest = issues[-1]
    first = max(1, latest - count + 1)
    return [str(issue) for issue in range(first, latest + 1)]


def longest_consecutive(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(set(values))
    longest = 1
    current = 1
    for previous, value in zip(ordered, ordered[1:]):
        if value == previous + 1:
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return longest


def duplicate_matches(
    candidate_numbers: dict[str, str],
    records: list[dict],
) -> list[dict]:
    by_site: dict[tuple[str, str], dict[str, str]] = {}
    for record in records:
        key = (
            preserve_configured_name(record.get("name") or ""),
            str(record.get("url") or ""),
        )
        issue = normalize_issue(record.get("issue", ""))
        numbers = str(record.get("numbers") or "")
        if issue and numbers:
            by_site.setdefault(key, {})[issue] = numbers

    matches: list[dict] = []
    for (name, url), issue_map in by_site.items():
        same_issues = [
            int(issue)
            for issue, numbers in candidate_numbers.items()
            if issue_map.get(issue) == numbers
        ]
        consecutive = longest_consecutive(same_issues)
        if same_issues:
            matches.append(
                {
                    "name": name,
                    "url": url,
                    "same_issues": [str(issue) for issue in sorted(same_issues)],
                    "longest_consecutive": consecutive,
                }
            )
    return sorted(
        matches,
        key=lambda item: (
            item["longest_consecutive"],
            len(item["same_issues"]),
        ),
        reverse=True,
    )


def has_dedicated_scope(target: dict) -> bool:
    if adapter_kind(target) in {
        "dynamic_article",
        "user_page",
        "list_detail",
    }:
        return True
    return any(
        target.get(field)
        for field in (
            "anchor",
            "stop_anchor",
            "decoded_anchor_only",
            "issue_position_window",
            "first_issue_chain",
        )
    )


def check_candidate(
    candidate: dict,
    production_targets: list[dict],
    *,
    cache_path: Path,
    debug_dir: Path,
) -> CandidateCheck:
    name = preserve_configured_name(candidate.get("name") or "")
    url = str(candidate.get("url") or "")
    existing_names = {
        preserve_configured_name(target.get("name") or "")
        for target in production_targets
    }
    if name in existing_names:
        return CandidateCheck(
            name,
            url,
            "rejected",
            "same_name",
            f"目录名已存在：{name}；已在第一道关卡停止，未执行后续检测",
        )

    normalized_url = canonical_url(url)
    existing_urls = {
        canonical_url(str(target.get("url") or ""))
        for target in production_targets
    }
    if normalized_url in existing_urls:
        return CandidateCheck(
            name,
            url,
            "rejected",
            "same_url",
            "URL/topic 已存在于正式配置",
        )

    signature = parser_signature(candidate)
    same_fields = [
        target["name"]
        for target in production_targets
        if parser_signature(target) == signature
    ]
    if same_fields:
        return CandidateCheck(
            name,
            url,
            "rejected",
            "same_fields",
            f"解析字段与已有目录完全相同：{', '.join(same_fields)}",
        )
    if not has_dedicated_scope(candidate):
        return CandidateCheck(
            name,
            url,
            "rejected",
            "dedicated_scope",
            "候选站没有专属正文范围",
        )

    try:
        records = load_cache_records(cache_path)
        issues = cache_issue_window(records, 10)
    except Exception as exc:
        return CandidateCheck(name, url, "rejected", "cache_baseline", str(exc))

    results, failure = crawl_one(candidate, issues, debug_dir=debug_dir)
    if failure is not None:
        return CandidateCheck(
            name,
            url,
            "rejected",
            "real_fetch",
            failure.reason,
            issues=issues,
        )
    issue_map = {
        normalize_issue(result.issue): ",".join(result.numbers)
        for result in results
    }
    missing = [issue for issue in issues if issue not in issue_map]
    if missing:
        return CandidateCheck(
            name,
            url,
            "rejected",
            "ten_issue_completeness",
            f"真实近10期缺少：{', '.join(issue + '期' for issue in missing)}",
            issues=issues,
        )
    latest_two = issues[-2:]
    if not any(issue in issue_map for issue in latest_two):
        return CandidateCheck(
            name,
            url,
            "rejected",
            "latest_two_baseline",
            f"没有抓到缓存最新两期中的任意一期：{', '.join(latest_two)}",
            issues=issues,
        )

    matches = duplicate_matches(issue_map, records)
    maximum = max(
        (item["longest_consecutive"] for item in matches),
        default=0,
    )
    if maximum >= 6:
        return CandidateCheck(
            name,
            url,
            "rejected",
            "duplicate_data",
            f"与已有目录连续 {maximum} 期数据相同，按重复资料拒收",
            issues=issues,
            duplicate_matches=matches,
        )
    if maximum >= 3:
        return CandidateCheck(
            name,
            url,
            "rejected",
            "suspected_duplicate",
            f"与已有目录连续 {maximum} 期数据相同，属于疑似重复，停止添加",
            issues=issues,
            duplicate_matches=matches,
        )
    return CandidateCheck(
        name,
        url,
        "accepted",
        "complete",
        "同名、URL、专属解析、真实近10期和重复检测全部通过",
        issues=issues,
        duplicate_matches=matches,
    )


def write_onboarding_report(
    results: list[CandidateCheck],
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "results": [asdict(result) for result in results],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


__all__ = [
    "CandidateCheck",
    "check_candidate",
    "duplicate_matches",
    "write_onboarding_report",
]
