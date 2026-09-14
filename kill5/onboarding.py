from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path

from .adapters.registry import adapter_kind
from .cache import ordered_issues, read_cache_document as read_cache_file
from .engine import crawl_one
from .errors import ErrorCode
from .identity import canonical_url, target_cache_identity
from .parser import (
    has_duplicate_numbers,
    normalize_issue,
    preserve_configured_name,
    valid_number,
)


@dataclass
class CandidateCheck:
    name: str
    url: str
    status: str
    stage: str
    reason: str
    issues: list[str] = field(default_factory=list)
    duplicate_matches: list[dict] = field(default_factory=list)


OPTIONAL_MISSING_CODES = {
    ErrorCode.ISSUE_NOT_FOUND.value,
    ErrorCode.POSITION_MISMATCH.value,
    ErrorCode.KEYWORD_MISMATCH.value,
}


def load_cache_document(path: Path) -> tuple[list[dict], dict[str, dict]]:
    records, _failures, identities = read_cache_file(path)
    if not isinstance(records, list) or not records:
        raise ValueError("recent_10_cache.json 没有有效基准记录")
    if not isinstance(identities, dict) or not identities:
        raise ValueError("recent_10_cache.json sites 缺少正式目录身份，判重未完成")
    return records, identities


def cache_issue_window(records: list[dict], count: int = 10) -> list[str]:
    issues = ordered_issues(
        record.get("issue", "")
        for record in records
    )
    if len(issues) < 2:
        raise ValueError("recent_10_cache.json 不足两个有效基准期")
    return issues[-count:]


def longest_consecutive(values) -> int:
    if not values:
        return 0
    ordered = [int(issue) for issue in ordered_issues(values)]
    longest = 1
    current = 1
    for previous, value in zip(ordered, ordered[1:]):
        if value == previous + 1 or (previous in {365, 366} and value == 1):
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return longest


def formal_duplicate_matches(
    candidate_numbers: dict[str, str],
    records: list[dict],
    production_targets: list[dict],
    issues: list[str],
    cache_identities: dict[str, dict] | None = None,
) -> list[dict]:
    """Compare a candidate with the identity-bound active cache baseline.

    The cache stores stable target IDs, while the current configuration owns the
    target URL and name.  Joining those two sources here prevents a same-name or
    same-URL guess from silently deciding the duplicate result.
    """
    wanted_issues = {
        normalize_issue(issue)
        for issue in issues
        if normalize_issue(issue)
    }
    candidate_map = {
        normalize_issue(issue): str(numbers)
        for issue, numbers in candidate_numbers.items()
        if normalize_issue(issue)
    }
    candidate_issues = set(candidate_map)
    if not candidate_issues:
        raise ValueError("候选没有有效期数，判重未完成")
    if not candidate_issues.issubset(wanted_issues):
        extra = sorted(candidate_issues - wanted_issues, key=int)
        detail = []
        if extra:
            detail.append(f"多出 {','.join(extra)}期")
        raise ValueError(f"候选历史期数不完整，判重未完成：{'；'.join(detail)}")

    target_by_id = {
        str(target.get("id") or "").strip(): target
        for target in production_targets
        if str(target.get("id") or "").strip()
    }
    if len(target_by_id) != len(production_targets):
        raise ValueError("正式配置缺少稳定 target_id，判重未完成")
    if cache_identities is not None:
        expected_project = Path(__file__).resolve().parents[1].name
        cached_target_ids = {
            str(record.get("target_id") or "").strip()
            for record in records
            if str(record.get("target_id") or "").strip()
        }
        for target_id in cached_target_ids:
            target = target_by_id.get(target_id)
            if target is None:
                raise ValueError(
                    f"缓存记录 {target_id} 缺少当前配置身份，判重未完成"
                )
            actual_identity = cache_identities.get(target_id)
            expected_identity = target_cache_identity(
                target,
                project_name=expected_project,
            )
            if actual_identity is None or actual_identity != expected_identity:
                raise ValueError(
                    f"缓存记录 {target.get('name') or target_id} 的目录、栏目、字段、方向或配置指纹不一致，判重未完成"
                )

    by_site: dict[str, dict[str, str]] = {}
    site_names: dict[str, tuple[str, str]] = {}
    for record in records:
        target_id = str(record.get("target_id") or "").strip()
        target = target_by_id.get(target_id)
        if target is None:
            raise ValueError(
                f"缓存记录 {record.get('name') or '未命名'} 缺少当前配置身份，判重未完成"
            )
        configured_name = preserve_configured_name(target.get("name") or "")
        configured_url = str(target.get("url") or "")
        if (
            preserve_configured_name(record.get("name") or "") != configured_name
            or str(record.get("url") or "") != configured_url
        ):
            raise ValueError(
                f"缓存记录 {target_id} 名称或 URL 与当前配置不一致，判重未完成"
            )
        issue = normalize_issue(record.get("issue", ""))
        numbers_text = str(record.get("numbers") or "")
        if not issue or not numbers_text:
            raise ValueError(f"缓存记录 {target_id} 存在空期数或空号码，判重未完成")
        if issue not in wanted_issues:
            continue
        numbers = [number.strip() for number in numbers_text.split(",")]
        expected_count = target.get("count")
        if (
            not numbers
            or not all(valid_number(number) for number in numbers)
            or (expected_count is not None and len(numbers) != int(expected_count))
            or has_duplicate_numbers(numbers)
        ):
            raise ValueError(
                f"缓存记录 {configured_name} {issue}期号码格式无效，判重未完成"
            )
        site_map = by_site.setdefault(target_id, {})
        previous = site_map.get(issue)
        if previous is not None and previous != numbers_text:
            raise ValueError(
                f"缓存记录 {configured_name} {issue}期存在冲突，判重未完成"
            )
        site_map[issue] = numbers_text
        site_names[target_id] = (configured_name, configured_url)

    matches: list[dict] = []
    compared_sites = 0
    for target_id, issue_map in by_site.items():
        common_issues = ordered_issues(candidate_issues & set(issue_map))
        if not common_issues:
            continue
        compared_sites += 1
        same_issues = [
            issue
            for issue in common_issues
            if issue_map[issue] == candidate_map[issue]
        ]
        if same_issues:
            name, url = site_names[target_id]
            matches.append(
                {
                    "target_id": target_id,
                    "name": name,
                    "url": url,
                    "same_issues": same_issues,
                    "longest_consecutive": longest_consecutive(same_issues),
                }
            )
    if not compared_sites:
        raise ValueError("缓存没有与候选共同的有效期号，判重未完成")
    return sorted(
        matches,
        key=lambda item: (
            item["longest_consecutive"],
            len(item["same_issues"]),
        ),
        reverse=True,
    )


def crawl_history_for_onboarding(
    candidate: dict,
    issues: list[str],
    *,
    debug_dir: Path,
) -> tuple[list, object | None]:
    """Collect historical evidence after direction-gating the current issues.

    Historical collection stays in memory and uses the same URL, adapter,
    keywords, and document boundaries.  Only the direction window is removed
    for this evidence pass; the current and previous issue gate is performed by
    ``check_candidate`` before this function is called.
    """
    historical_target = dict(candidate)
    historical_target["region"] = None
    historical_target["issue_position_window"] = None
    return crawl_available_issues(historical_target, issues, debug_dir=debug_dir)


def crawl_available_issues(
    target: dict,
    issues: list[str],
    *,
    debug_dir: Path,
) -> tuple[list, object | None]:
    """Collect valid issue results while allowing genuine page gaps.

    Each issue is crawled independently so a missing issue cannot make valid
    neighboring evidence disappear. Network, anchor, conflict, and malformed
    number failures remain fatal; only issue/position/keyword absence is
    treated as a missing-period gap.
    """
    results = []
    optional_failures = []
    seen_issues: set[str] = set()
    for issue in issues:
        normalized = normalize_issue(issue)
        if not normalized or normalized in seen_issues:
            continue
        seen_issues.add(normalized)
        issue_results, failure = crawl_one(
            target,
            [normalized],
            debug_dir=debug_dir,
        )
        if failure is not None:
            if failure.error_code in OPTIONAL_MISSING_CODES:
                optional_failures.append(failure)
                continue
            return [], failure
        for result in issue_results:
            result_issue = normalize_issue(result.issue)
            if result_issue in {item.issue for item in results}:
                return [], ValueError(f"{target.get('name') or '候选站'} {result_issue}期重复成功结论")
            results.append(result)
    if results:
        return results, None
    return [], optional_failures[0] if optional_failures else ValueError("没有可用期数")


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

    if not has_dedicated_scope(candidate):
        return CandidateCheck(
            name,
            url,
            "rejected",
            "dedicated_scope",
            "候选站没有专属正文范围",
        )

    try:
        records, cache_identities = load_cache_document(cache_path)
        issues = cache_issue_window(records, 10)
    except Exception as exc:
        return CandidateCheck(name, url, "rejected", "cache_baseline", str(exc))

    baseline_issues = issues[-2:]
    direction_results, direction_failure = crawl_available_issues(
        candidate,
        baseline_issues,
        debug_dir=debug_dir,
    )
    if direction_failure is not None:
        return CandidateCheck(
            name,
            url,
            "rejected",
            "real_fetch",
            getattr(direction_failure, "reason", str(direction_failure)),
            issues=baseline_issues,
        )
    direction_map = {
        normalize_issue(result.issue): ",".join(result.numbers)
        for result in direction_results
    }
    if not any(issue in direction_map for issue in baseline_issues):
        return CandidateCheck(
            name,
            url,
            "rejected",
            "latest_two_baseline",
            f"没有抓到基准期中的任意一期：{', '.join(baseline_issues)}",
            issues=baseline_issues,
        )

    history_issues = ordered_issues(set(issues) | set(baseline_issues))
    results, failure = crawl_history_for_onboarding(
        candidate,
        history_issues,
        debug_dir=debug_dir,
    )
    if failure is not None:
        return CandidateCheck(
            name,
            url,
            "rejected",
            "history_fetch",
            getattr(failure, "reason", str(failure)),
            issues=history_issues,
        )
    issue_map = {
        normalize_issue(result.issue): ",".join(result.numbers)
        for result in results
    }
    available_issues = ordered_issues(issue_map)
    if not available_issues:
        return CandidateCheck(
            name,
            url,
            "rejected",
            "history_fetch",
            "页面没有可用于判重的有效期数",
            issues=history_issues,
        )
    try:
        matches = formal_duplicate_matches(
            issue_map,
            records,
            production_targets,
            available_issues,
            cache_identities,
        )
    except Exception as exc:
        return CandidateCheck(
            name,
            url,
            "rejected",
            "duplicate_validation",
            str(exc),
            issues=available_issues,
        )
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
            issues=available_issues,
            duplicate_matches=matches,
        )
    if maximum >= 3:
        return CandidateCheck(
            name,
            url,
            "rejected",
            "suspected_duplicate",
            f"与已有目录连续 {maximum} 期数据相同，属于疑似重复，停止添加",
            issues=available_issues,
            duplicate_matches=matches,
        )
    return CandidateCheck(
        name,
        url,
        "accepted",
        "complete",
        "同名、URL、专属解析、真实可用历史期和重复检测全部通过",
        issues=available_issues,
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
    "crawl_history_for_onboarding",
    "crawl_available_issues",
    "formal_duplicate_matches",
    "load_cache_document",
    "write_onboarding_report",
]
