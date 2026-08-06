from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import time

from .config import BASHU_URL
from .domain import CrawlResult
from .identity import canonical_url, dedupe_results, target_for_result, target_identity
from .parser import (
    has_duplicate_numbers,
    normalize_issue,
    preserve_configured_name,
    valid_number,
)
from .storage import atomic_write_verified_bytes, file_lock

def issue_key(value: str) -> str:
    digits = re.sub(r"\D+", "", str(value))
    return str(int(digits)) if digits else ""


def recent_issues_from_latest(latest_issue: str, count: int) -> list[str]:
    last_issue = int(issue_key(latest_issue))
    first_issue = max(1, last_issue - count + 1)
    return [str(issue) for issue in range(first_issue, last_issue + 1)]


@contextmanager
def cache_file_lock(cache_path: Path, timeout: float = 30.0):
    lock_path = cache_path.with_name(f".{cache_path.name}.lock")
    with file_lock(lock_path, timeout=timeout):
        yield


def atomic_write_json(path: Path, data: dict) -> None:
    content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    if json.loads(content.decode("utf-8")) != data:
        raise ValueError("缓存序列化复读校验不一致，已停止替换")

    def verify(actual: bytes) -> None:
        if json.loads(actual.decode("utf-8")) != data:
            raise ValueError("缓存写入后复读校验不一致")

    atomic_write_verified_bytes(path, content, verify)


def update_recent_duplicate_cache(
    cache_path: Path,
    results: list[CrawlResult],
    issues: list[str],
    recent_count: int = 10,
    active_targets: list[dict] | None = None,
    failure_markers: list[dict] | None = None,
    require_complete: bool = True,
) -> None:
    if not issues:
        return
    if recent_count <= 0:
        raise ValueError("recent_count 必须大于 0")
    wanted_issues = {
        normalize_issue(issue)
        for issue in issues
        if normalize_issue(issue)
    }
    if not results:
        raise ValueError("没有成功结果，已停止缓存同步")
    result_issues = [normalize_issue(result.issue) for result in results]
    if any(not issue for issue in result_issues):
        raise ValueError("缓存结果包含无效期数")
    unexpected_issues = sorted(
        set(result_issues) - wanted_issues,
        key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value),
    )
    if unexpected_issues:
        raise ValueError(
            f"缓存结果包含非指定期数：{','.join(unexpected_issues)}"
        )
    if active_targets is not None and any(
        str(target.get("id") or "").strip() for target in active_targets
    ):
        missing_ids = [result.name for result in results if not str(result.target_id or "").strip()]
        if missing_ids:
            raise ValueError(
                f"正式缓存结果缺少稳定 target_id：{','.join(missing_ids)}"
            )
    if active_targets is not None:
        wanted_keys = {
            (target_identity(target), issue)
            for target in active_targets
            for issue in wanted_issues
        }
        result_keys: set[tuple[tuple[str, ...], str]] = set()
        for result in results:
            target = target_for_result(result, active_targets)
            if not target:
                raise ValueError(
                    f"缓存结果 {result.name} {result.url} 无法精确匹配当前活动目标"
                )
            key = (target_identity(target), normalize_issue(result.issue))
            if key in result_keys:
                raise ValueError(
                    f"缓存结果 {result.name} {result.issue}期存在重复成功结论"
                )
            result_keys.add(key)
        missing_keys = wanted_keys - result_keys
        if require_complete and missing_keys:
            missing_text = ", ".join(
                f"{target.get('name') or identity} {issue}期"
                for identity, issue in sorted(missing_keys, key=lambda value: (value[0], value[1]))
                for target in active_targets
                if target_identity(target) == identity
            )
            raise ValueError(f"正式缓存结果不完整，缺少：{missing_text}")
    with cache_file_lock(cache_path):
        _update_recent_duplicate_cache_locked(
            cache_path,
            results,
            recent_count,
            active_targets=active_targets,
            failure_markers=failure_markers,
        )


def _update_recent_duplicate_cache_locked(
    cache_path: Path,
    results: list[CrawlResult],
    recent_count: int,
    active_targets: list[dict] | None = None,
    failure_markers: list[dict] | None = None,
) -> None:
    active_by_id: dict[str, dict] = {}
    active_by_url: dict[str, dict] = {}
    for target in active_targets or []:
        stable_id = str(target.get("id") or "").strip()
        name = preserve_configured_name(target.get("name") or "")
        url = str(target.get("url") or "")
        url_key = canonical_url(url)
        if stable_id:
            if stable_id in active_by_id:
                raise ValueError(f"启用目标存在重复稳定 ID：{stable_id}")
            active_by_id[stable_id] = target
        if url_key in active_by_url:
            raise ValueError(f"启用目标存在重复规范化 URL：{url}")
        if not name or not url_key:
            raise ValueError("启用目标缺少 name 或 url")
        active_by_url[url_key] = target

    existing_records = []
    existing_failure_markers = []
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1:
                raise ValueError("根对象或 version 无效")
            records = data.get("records")
            if not isinstance(records, list):
                raise ValueError("records 必须是列表")
            existing_records = records
            existing_failure_markers = data.get("failures", [])
            if not isinstance(existing_failure_markers, list):
                raise ValueError("failures 必须是列表")
        except Exception as exc:
            raise ValueError(f"{cache_path.name} 读取失败，已停止同步，避免覆盖旧缓存：{exc}") from exc

    def result_record(item: CrawlResult) -> dict:
        record = {
            "name": item.name,
            "url": item.url,
            "issue": normalize_issue(item.issue),
            "numbers": ",".join(item.numbers),
        }
        stable_id = str(item.target_id or "").strip()
        if stable_id:
            record["target_id"] = stable_id
        return record

    new_records = [
        result_record(item)
        for item in dedupe_results(results)
        if normalize_issue(item.issue)
    ]

    def clean_record(record: dict) -> dict | None:
        if not isinstance(record, dict):
            raise ValueError("缓存记录不是对象")
        cleaned = {
            "name": str(record.get("name") or ""),
            "url": str(record.get("url") or ""),
            "issue": issue_key(record.get("issue", "")),
            "numbers": str(record.get("numbers") or ""),
        }
        stable_id = str(record.get("target_id") or "").strip()
        if stable_id:
            cleaned["target_id"] = stable_id
        if not all(cleaned.values()):
            raise ValueError("缓存记录缺少 name/url/issue/numbers")
        return cleaned

    def apply_active_target(record: dict, *, required: bool) -> dict | None:
        stable_id = str(record.get("target_id") or "").strip()
        if stable_id:
            target = active_by_id.get(stable_id)
        else:
            target = active_by_url.get(canonical_url(record["url"]))
        if target is None:
            if required or not stable_id:
                raise ValueError(
                    f"{record['name']} {record['url']} 无法映射到当前稳定 ID，已停止缓存同步"
                )
            return None

        configured_name = preserve_configured_name(target.get("name") or "")
        configured_url = str(target.get("url") or "")
        if required and (
            preserve_configured_name(record["name"]) != configured_name
            or record["url"] != configured_url
        ):
            raise ValueError(
                f"稳定ID {target.get('id') or configured_url} 的新结果名称或 URL 与配置不一致"
            )
        updated = {
            **record,
            "name": configured_name,
            "url": configured_url,
        }
        target_id = str(target.get("id") or "").strip()
        if target_id:
            updated["target_id"] = target_id
        return updated

    def clean_failure_marker(record: dict) -> dict:
        if not isinstance(record, dict):
            raise ValueError("缓存失败标记不是对象")
        status = str(record.get("status") or "").strip().lower()
        cleaned = {
            "status": status,
            "name": str(record.get("name") or ""),
            "url": str(record.get("url") or ""),
            "issue": issue_key(record.get("issue", "")),
            "reason": str(record.get("reason") or "未提供原因"),
        }
        if status != "failed":
            raise ValueError("缓存失败标记 status 必须为 failed")
        if not all(
            [
                cleaned["name"],
                cleaned["url"],
                cleaned["issue"],
                cleaned["reason"],
            ]
        ):
            raise ValueError("缓存失败标记缺少 name/url/issue/reason")
        for key in ("stage", "error_code", "target_id"):
            value = str(record.get(key) or "").strip()
            if value:
                cleaned[key] = value
        return cleaned

    def apply_failure_target(record: dict, *, required: bool) -> dict | None:
        stable_id = str(record.get("target_id") or "").strip()
        target = active_by_id.get(stable_id) if stable_id else None
        if target is None:
            target = active_by_url.get(canonical_url(record["url"]))
        if target is None:
            if required:
                raise ValueError(
                    f"缓存失败标记 {record['name']} {record['url']} 无法映射到当前稳定 ID"
                )
            return None
        configured_name = preserve_configured_name(target.get("name") or "")
        configured_url = str(target.get("url") or "")
        if required and (
            preserve_configured_name(record["name"]) != configured_name
            or record["url"] != configured_url
        ):
            raise ValueError(
                f"缓存失败标记目标 {target.get('id') or configured_url} 的名称或 URL 与配置不一致"
            )
        updated = {
            **record,
            "name": configured_name,
            "url": configured_url,
        }
        target_id = str(target.get("id") or "").strip()
        if target_id:
            updated["target_id"] = target_id
        return updated

    def validate_record(record: dict) -> dict:
        target = None
        stable_id = str(record.get("target_id") or "").strip()
        if stable_id:
            target = active_by_id.get(stable_id)
        if target is None:
            target = active_by_url.get(canonical_url(record["url"]))
        expected_count = int(target.get("count") or 0) if target else (
            6
            if record["name"] == "拔树寻根" and record["url"] == BASHU_URL
            else 5
        )
        number_list = [number.strip() for number in record["numbers"].split(",")]
        if len(number_list) != expected_count or not all(
            valid_number(number) for number in number_list
        ):
            raise ValueError(
                f"缓存记录 {record['name']} {record['issue']}期 号码格式或数量无效"
            )
        if has_duplicate_numbers(number_list):
            raise ValueError(f"缓存记录 {record['name']} {record['issue']}期 号码重复")
        return record

    try:
        normalized_existing = [clean_record(record) for record in existing_records]
        new_records = [clean_record(record) for record in new_records]
        normalized_failure_markers = [
            clean_failure_marker(record) for record in existing_failure_markers
        ]
        new_failure_markers = [
            clean_failure_marker(record) for record in (failure_markers or [])
        ]
        if active_targets is not None:
            normalized_existing = [
                mapped
                for record in normalized_existing
                if (mapped := apply_active_target(record, required=False)) is not None
            ]
            new_records = [
                apply_active_target(record, required=True)
                for record in new_records
            ]
            normalized_failure_markers = [
                mapped
                for record in normalized_failure_markers
                if (mapped := apply_failure_target(record, required=False)) is not None
            ]
            new_failure_markers = [
                apply_failure_target(record, required=True)
                for record in new_failure_markers
            ]

        new_ids_by_url: dict[str, set[str]] = {}
        for record in new_records:
            stable_id = str(record.get("target_id") or "").strip()
            if stable_id:
                new_ids_by_url.setdefault(canonical_url(record["url"]), set()).add(stable_id)
        for record in normalized_existing:
            if record.get("target_id"):
                continue
            matching_ids = new_ids_by_url.get(canonical_url(record["url"]), set())
            if len(matching_ids) == 1:
                record["target_id"] = next(iter(matching_ids))

        normalized_existing = [validate_record(record) for record in normalized_existing]
        new_records = [validate_record(record) for record in new_records]
    except Exception as exc:
        raise ValueError(f"{cache_path.name} 记录校验失败，已停止同步，避免覆盖旧缓存：{exc}") from exc

    def site_key(record: dict) -> tuple[str, str]:
        stable_id = str(record.get("target_id") or "").strip()
        if stable_id:
            return ("id", stable_id)
        return ("url", canonical_url(record["url"]))

    def record_key(record: dict) -> tuple[tuple[str, str], str]:
        return (site_key(record), record["issue"])

    def failure_marker_key(record: dict) -> tuple[tuple[str, str], str]:
        return record_key(record)

    existing_by_key: dict[tuple[tuple[str, str], str], dict] = {}
    deduped_existing = []
    for record in normalized_existing:
        key = record_key(record)
        previous = existing_by_key.get(key)
        if previous:
            if previous["numbers"] != record["numbers"]:
                raise ValueError(
                    f"{record['name']} {record['issue']}期 缓存存在冲突记录，已停止同步"
                )
            continue
        existing_by_key[key] = record
        deduped_existing.append(record)
    normalized_existing = deduped_existing

    new_by_key: dict[tuple[tuple[str, str], str], dict] = {}
    for record in new_records:
        key = record_key(record)
        previous = new_by_key.get(key)
        if previous and previous["numbers"] != record["numbers"]:
            raise ValueError(
                f"{record['name']} {record['issue']}期 缓存候选冲突，已停止同步"
            )
        new_by_key[key] = record

    existing_failures_by_key: dict[
        tuple[tuple[str, str], str], dict
    ] = {}
    for marker in normalized_failure_markers:
        key = failure_marker_key(marker)
        previous = existing_failures_by_key.get(key)
        if previous and previous != marker:
            raise ValueError(
                f"{marker['name']} {marker['issue']}期 缓存存在冲突失败标记，已停止同步"
            )
        existing_failures_by_key[key] = marker

    new_failures_by_key: dict[tuple[tuple[str, str], str], dict] = {}
    for marker in new_failure_markers:
        key = failure_marker_key(marker)
        previous = new_failures_by_key.get(key)
        if previous and previous != marker:
            raise ValueError(
                f"{marker['name']} {marker['issue']}期 缓存候选失败标记冲突，已停止同步"
            )
        new_failures_by_key[key] = marker

    issue_values_by_site: dict[tuple[str, str], set[str]] = {}
    for record in [*normalized_existing, *new_records]:
        issue_values_by_site.setdefault(site_key(record), set()).add(record["issue"])
    wanted_by_site = {}
    for site, issue_values in issue_values_by_site.items():
        issue_numbers = [int(issue) for issue in issue_values]
        crosses_year = (
            any(issue >= 300 for issue in issue_numbers)
            and any(issue <= 60 for issue in issue_numbers)
        )

        def recency_key(issue: str) -> int:
            value = int(issue)
            if crosses_year and value <= 60:
                return value + 1000
            return value

        wanted_by_site[site] = set(
            sorted(issue_values, key=recency_key, reverse=True)[:recent_count]
        )
    replacements = new_by_key
    metadata_by_site = {
        site_key(record): {
            key: record[key]
            for key in ("name", "url", "target_id")
            if key in record
        }
        for record in new_records
    }
    for target in active_targets or []:
        stable_id = str(target.get("id") or "").strip()
        target_record = {
            "name": preserve_configured_name(target.get("name") or ""),
            "url": str(target.get("url") or ""),
        }
        if stable_id:
            target_record["target_id"] = stable_id
        metadata_by_site[site_key(target_record)] = target_record
    used_replacements: set[tuple[tuple[str, str], str]] = set()

    combined_records: list[dict] = []
    for record in normalized_existing:
        key = record_key(record)
        site = site_key(record)
        if key in new_failures_by_key:
            continue
        if record["issue"] not in wanted_by_site.get(site, set()):
            continue
        replacement = replacements.get(key)
        if replacement:
            combined_records.append(replacement)
            used_replacements.add(key)
        else:
            combined_records.append({**record, **metadata_by_site.get(site, {})})

    for record in new_records:
        key = record_key(record)
        if key in used_replacements:
            continue
        if record["issue"] in wanted_by_site.get(site_key(record), set()):
            combined_records.append(record)

    combined_failure_markers = {
        **existing_failures_by_key,
        **new_failures_by_key,
    }
    for key in new_by_key:
        combined_failure_markers.pop(key, None)

    failure_issue_values_by_site: dict[tuple[str, str], set[str]] = {}
    for marker in combined_failure_markers.values():
        failure_issue_values_by_site.setdefault(site_key(marker), set()).add(
            marker["issue"]
        )
    wanted_failure_by_site: dict[tuple[str, str], set[str]] = {}
    for site, issue_values in failure_issue_values_by_site.items():
        issue_numbers = [int(issue) for issue in issue_values]
        crosses_year = (
            any(issue >= 300 for issue in issue_numbers)
            and any(issue <= 60 for issue in issue_numbers)
        )

        def failure_recency_key(issue: str) -> int:
            value = int(issue)
            if crosses_year and value <= 60:
                return value + 1000
            return value

        wanted_failure_by_site[site] = set(
            sorted(issue_values, key=failure_recency_key, reverse=True)[:recent_count]
        )
    combined_failure_list = [
        marker
        for marker in combined_failure_markers.values()
        if marker["issue"] in wanted_failure_by_site.get(site_key(marker), set())
    ]

    data = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "recent_count": recent_count,
        "records": combined_records,
        "failures": combined_failure_list,
    }
    atomic_write_json(cache_path, data)


__all__ = [
    'issue_key',
    'recent_issues_from_latest',
    'cache_file_lock',
    'atomic_write_json',
    'update_recent_duplicate_cache',
    '_update_recent_duplicate_cache_locked',
]
