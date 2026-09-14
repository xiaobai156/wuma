from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import time

from .config import BASHU_URL, PROJECT_ROOT
from .domain import CrawlResult
from .identity import (
    canonical_url,
    dedupe_results,
    target_cache_identity,
    target_for_result,
    target_identity,
)
from .parser import (
    has_duplicate_numbers,
    normalize_issue,
    normalize_region,
    preserve_configured_name,
    valid_number,
)
from .storage import atomic_write_verified_bytes, file_lock

def issue_key(value: str) -> str:
    digits = re.sub(r"\D+", "", str(value))
    if not digits:
        return ""
    issue = int(digits)
    return str(issue) if 1 <= issue <= 999 else ""


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


def _parse_cache_data(data: dict) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """Read both the legacy flat cache and the human-readable sites cache."""
    if not isinstance(data, dict):
        raise ValueError("缓存根对象必须是对象")

    if "sites" in data:
        sites = data.get("sites")
        if not isinstance(sites, list):
            raise ValueError("sites 必须是列表")
        failures = data.get("failures", [])
        if not isinstance(failures, list):
            raise ValueError("failures 必须是列表")
        records: list[dict] = []
        identities: dict[str, dict] = {}
        seen_site_ids: set[str] = set()
        for index, site in enumerate(sites, start=1):
            if not isinstance(site, dict):
                raise ValueError(f"sites 第 {index} 条不是对象")
            target_id = str(site.get("id") or "").strip()
            name = str(site.get("name") or "").strip()
            url = str(site.get("url") or "").strip()
            fingerprint = site.get("fingerprint", {})
            if not target_id or not name or not url:
                raise ValueError(f"sites 第 {index} 条缺少 id/name/url")
            if target_id in seen_site_ids:
                raise ValueError(f"sites 存在重复稳定 ID：{target_id}")
            seen_site_ids.add(target_id)
            if not isinstance(fingerprint, dict):
                raise ValueError(f"sites 第 {index} 条 fingerprint 必须是对象")
            identity = site.get("identity")
            if identity is not None:
                if not isinstance(identity, dict):
                    raise ValueError(f"sites 第 {index} 条 identity 必须是对象")
                identities[target_id] = identity
            for issue, numbers in fingerprint.items():
                normalized_issue = issue_key(issue)
                numbers_text = str(numbers or "").strip()
                if not normalized_issue or not numbers_text:
                    raise ValueError(
                        f"sites 第 {index} 条 fingerprint 存在空期数或空号码"
                    )
                records.append(
                    {
                        "name": name,
                        "url": url,
                        "issue": normalized_issue,
                        "numbers": numbers_text,
                        "target_id": target_id,
                    }
                )
        return records, failures, identities

    if data.get("version") != 1:
        raise ValueError("缓存根对象或 version 无效")
    records = data.get("records")
    if not isinstance(records, list):
        raise ValueError("records 必须是列表")
    failures = data.get("failures", [])
    if not isinstance(failures, list):
        raise ValueError("failures 必须是列表")
    identities = data.get("target_identities", {})
    if not isinstance(identities, dict):
        raise ValueError("target_identities 必须是对象")
    return records, failures, identities


def read_cache_document(path: Path) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """Load cache data into the internal flat representation."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _parse_cache_data(data)
    except Exception as exc:
        raise ValueError(f"{path.name} 读取失败：{exc}") from exc


def ordered_issues(issue_values) -> list[str]:
    normalized = {issue_key(issue) for issue in issue_values if issue_key(issue)}
    issue_numbers = [int(issue) for issue in normalized]
    crosses_year = (
        any(issue >= 300 for issue in issue_numbers)
        and any(issue <= 60 for issue in issue_numbers)
    )

    def recency_key(issue: str) -> int:
        value = int(issue)
        if crosses_year and value <= 60:
            return value + 1000
        return value

    return sorted(normalized, key=recency_key)


def _recent_issue_set(issue_values: set[str], recent_count: int) -> set[str]:
    return set(ordered_issues(issue_values)[-recent_count:])


def update_repaired_cache(
    cache_path: Path,
    results: list[CrawlResult],
    issues: list[str],
    targets: list[dict],
    recent_count: int = 10,
) -> None:
    """Merge successful repairs without rebuilding any other site's data."""
    from .output import validate_results_before_write

    if not results:
        return
    if recent_count <= 0:
        raise ValueError("recent_count 必须大于 0")
    validate_results_before_write(results, issues, targets)
    if any(not re.fullmatch(r"[0-9]{2}", number) for item in results for number in item.numbers):
        raise ValueError("定向修复缓存的成功号码必须是两位数")
    with cache_file_lock(cache_path):
        if cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8-sig"))
            _parse_cache_data(data)
            if "sites" not in data:
                raise ValueError("定向修复需要 sites 缓存；旧缓存必须先独立迁移")
        else:
            data = {"sites": [], "failures": [], "periods": recent_count, "base_period": 0}
        sites = list(data["sites"])
        by_id = {str(site["id"]).strip(): index for index, site in enumerate(sites)}
        repaired: dict[str, tuple[dict, set[str]]] = {}
        for item in results:
            target = target_for_result(item, targets)
            stable_id = str(target.get("id") or "").strip()
            if not stable_id:
                raise ValueError("定向修复缓存缺少稳定 ID")
            identity = target_cache_identity(target, project_name=PROJECT_ROOT.name)
            index = by_id.get(stable_id)
            if index is None:
                if any(canonical_url(site["url"]) == canonical_url(item.url)
                       or site["name"] == item.name for site in sites):
                    raise ValueError(f"{item.name} 的缓存名称或 URL 已绑定其他稳定 ID")
                site = dict(id=stable_id, name=item.name, url=item.url,
                            pick=normalize_region(target.get("region")),
                            browser=bool(target.get("rendered_fallback_selectors")),
                            click_first=bool(target.get("click_first")),
                            fingerprint={}, identity=identity)
                index = len(sites)
                sites.append(site)
                by_id[stable_id] = index
            else:
                site = dict(sites[index])
                if site["name"] != item.name or site["url"] != item.url:
                    raise ValueError(f"{item.name} 的缓存名称或 URL 与本次成功结果冲突")
                if site.get("fingerprint") and site.get("identity") != identity:
                    raise ValueError(f"{item.name} 的缓存身份与当前配置不一致，已停止同步")
                site["identity"] = identity
            fingerprint: dict[str, str] = {}
            for raw_issue, numbers in site.get("fingerprint", {}).items():
                cached_issue = issue_key(raw_issue)
                if not isinstance(numbers, str):
                    raise ValueError(f"{item.name} 的缓存号码格式无效")
                values = numbers.split(",")
                expected_count = target.get("count")
                if (not cached_issue
                        or (expected_count is not None and len(values) != int(expected_count))
                        or any(not re.fullmatch(r"[0-9]{2}", value) or not valid_number(value) for value in values)
                        or has_duplicate_numbers(values) or cached_issue in fingerprint):
                    raise ValueError(f"{item.name} 的缓存期数或号码无效/重复")
                fingerprint[cached_issue] = numbers
            issue = normalize_issue(item.issue)
            numbers = ",".join(item.numbers)
            if issue in fingerprint and fingerprint[issue] != numbers:
                raise ValueError(f"{item.name} {issue}期 新结果与正式缓存冲突，已停止同步")
            fingerprint[issue] = numbers
            site["fingerprint"] = {key: fingerprint[key] for key in
                                   reversed(ordered_issues(fingerprint)[-recent_count:])}
            sites[index] = site
            repaired.setdefault(stable_id, (target, set()))[1].add(issue)

        remaining = []
        for marker in data.get("failures", []):
            if not isinstance(marker, dict):
                raise ValueError("缓存失败标记不是对象")
            stable_id = str(marker.get("target_id") or "").strip()
            match = repaired.get(stable_id) if stable_id else next(
                (value for value in repaired.values()
                 if (marker.get("name"), marker.get("url")) ==
                    (value[0]["name"], value[0]["url"])), None
            )
            if match and issue_key(marker.get("issue", "")) in match[1]:
                target = match[0]
                if (marker.get("name"), marker.get("url"), marker.get("status")) != (
                        target["name"], target["url"], "failed"):
                    raise ValueError("待清除缓存失败标记的身份或状态冲突")
                continue
            remaining.append(marker)
        updated = {**data, "sites": sites, "failures": remaining}
        if updated == data:
            return
        periods = ordered_issues(issue for site in sites for issue in site.get("fingerprint", {}))
        updated["base_period"] = int(periods[-1]) if periods else 0
        updated["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        atomic_write_json(cache_path, updated)


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
    existing_target_identities: dict[str, dict] = {}
    if cache_path.exists():
        try:
            (
                existing_records,
                existing_failure_markers,
                existing_target_identities,
            ) = read_cache_document(cache_path)
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
        if target is not None:
            expected_count = target.get("count")
        elif record["name"] == "拔树寻根" and record["url"] == BASHU_URL:
            expected_count = 6
        else:
            expected_count = 5
        number_list = [number.strip() for number in record["numbers"].split(",")]
        if not number_list or not all(
            valid_number(number) for number in number_list
        ):
            raise ValueError(
                f"缓存记录 {record['name']} {record['issue']}期 号码格式或数量无效"
            )
        if expected_count is not None and len(number_list) != int(expected_count):
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

    for key, record in new_by_key.items():
        previous = existing_by_key.get(key)
        if previous and previous["numbers"] != record["numbers"]:
            raise ValueError(
                f"{record['name']} {record['issue']}期 新结果与正式缓存冲突，已停止同步"
            )

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
    wanted_by_site = {
        site: _recent_issue_set(issue_values, recent_count)
        for site, issue_values in issue_values_by_site.items()
    }
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
    wanted_failure_by_site = {
        site: _recent_issue_set(issue_values, recent_count)
        for site, issue_values in failure_issue_values_by_site.items()
    }
    combined_failure_list = [
        marker
        for marker in combined_failure_markers.values()
        if marker["issue"] in wanted_failure_by_site.get(site_key(marker), set())
    ]

    site_views: dict[str, dict] = {}

    def ensure_site(
        target_id: str,
        *,
        name: str,
        url: str,
        target: dict | None = None,
        identity: dict | None = None,
    ) -> dict:
        site = site_views.get(target_id)
        if site is None:
            site = {
                "id": target_id,
                "name": name,
                "url": url,
                "pick": normalize_region(
                    target.get("region") if target is not None else ""
                ),
                "browser": bool(
                    target.get("rendered_fallback_selectors")
                    if target is not None
                    else False
                ),
                "click_first": bool(
                    target.get("click_first") if target is not None else False
                ),
                "fingerprint": {},
            }
            site_views[target_id] = site
        if identity is not None:
            site["identity"] = identity
        return site

    for target in active_targets or []:
        stable_id = str(target.get("id") or "").strip()
        if not stable_id:
            continue
        ensure_site(
            stable_id,
            name=preserve_configured_name(target.get("name") or ""),
            url=str(target.get("url") or ""),
            target=target,
            identity=target_cache_identity(
                target,
                project_name=PROJECT_ROOT.name,
            ),
        )

    for target_id, identity in existing_target_identities.items():
        stable_id = str(target_id or "").strip()
        if (
            not stable_id
            or stable_id in site_views
            or not isinstance(identity, dict)
        ):
            continue
        ensure_site(
            stable_id,
            name=str(identity.get("site_name") or ""),
            url=str(identity.get("url") or ""),
            identity=identity,
        )

    for record in combined_records:
        stable_id = str(record.get("target_id") or "").strip()
        if not stable_id:
            raise ValueError(
                f"缓存记录 {record.get('name') or '未命名'} 缺少稳定目录身份，已停止同步"
            )
        site = ensure_site(
            stable_id,
            name=str(record.get("name") or ""),
            url=str(record.get("url") or ""),
        )
        fingerprint = site["fingerprint"]
        issue = issue_key(record.get("issue", ""))
        numbers = str(record.get("numbers") or "")
        previous = fingerprint.get(issue)
        if previous is not None and previous != numbers:
            raise ValueError(
                f"缓存站点 {site['name']} {issue}期 fingerprint 存在冲突，已停止同步"
            )
        fingerprint[issue] = numbers

    for marker in combined_failure_list:
        stable_id = str(marker.get("target_id") or "").strip()
        if stable_id and stable_id not in site_views:
            target = active_by_id.get(stable_id)
            ensure_site(
                stable_id,
                name=str(marker.get("name") or ""),
                url=str(marker.get("url") or ""),
                target=target,
                identity=(
                    target_cache_identity(target, project_name=PROJECT_ROOT.name)
                    if target is not None
                    else existing_target_identities.get(stable_id)
                ),
            )

    for site in site_views.values():
        site["fingerprint"] = dict(
            (issue, site["fingerprint"][issue])
            for issue in reversed(ordered_issues(site["fingerprint"]))
        )

    ordered_periods = ordered_issues(record["issue"] for record in combined_records)
    base_period = int(ordered_periods[-1]) if ordered_periods else 0
    data = {
        "base_period": base_period,
        "periods": recent_count,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sites": list(site_views.values()),
        "failures": combined_failure_list,
    }
    atomic_write_json(cache_path, data)


__all__ = [
    'issue_key',
    'ordered_issues',
    'cache_file_lock',
    'atomic_write_json',
    'read_cache_document',
    'update_recent_duplicate_cache',
    'update_repaired_cache',
    '_update_recent_duplicate_cache_locked',
]
