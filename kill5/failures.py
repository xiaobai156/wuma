from __future__ import annotations

from pathlib import Path
import re

from .domain import CrawlFailure
from .errors import ErrorCode
from .parser import normalize_region, parse_issues


def parse_failure_line(line: str) -> tuple[str, str, str, list[str]]:
    match = re.fullmatch(
        r"失败\s+(.+?)\s+(https?://\S+)\s+方向[:：]\s*(\S+)\s+"
        r"期数[:：]\s*(.+?)\s+阶段[:：]\s*.+?\s+原因[:：]\s*.+",
        line.strip(),
    )
    if not match:
        raise ValueError("失败 TXT 存在无法识别的记录")
    name, url, direction, raw_issues = match.groups()
    if not re.fullmatch(r"[0-9]+期?(?:\s*[,，]\s*[0-9]+期?)*", raw_issues):
        raise ValueError("失败 TXT 期数字段无效")
    issues = parse_issues(raw_issues)
    return name.strip(), url, normalize_region(direction), issues


def failed_targets_from_text(text: str, targets: list[dict]) -> tuple[list[dict], list[str]]:
    target_by_identity = {
        (str(target.get("name") or "").strip(), str(target.get("url") or "")): target
        for target in targets if not target.get("disabled")
    }
    matched: dict[tuple[str, str], dict] = {}
    issues: set[str] = set()
    for index, line in enumerate(text.removeprefix("\ufeff").splitlines(), 1):
        if not line.strip():
            continue
        try:
            name, url, direction, line_issues = parse_failure_line(line)
            target = target_by_identity.get((name, url))
            if target is None:
                raise ValueError(f"记录不在当前启用 targets.json：{name} {url}")
            if direction != normalize_region(target.get("region")):
                raise ValueError(f"{name} 的失败记录方向与当前配置不一致")
        except ValueError as exc:
            raise ValueError(f"失败 TXT 第 {index} 行：{exc}") from exc
        matched[(name, url)] = target
        issues.update(line_issues)
    return list(matched.values()), sorted(issues, key=int)


def failed_targets_from_file(path: Path, targets: list[dict]) -> tuple[list[dict], list[str]]:
    return failed_targets_from_text(path.read_text(encoding="utf-8-sig"), targets)


def failure_category(failure: CrawlFailure) -> str:
    error_code = failure.error_code
    if error_code == ErrorCode.LOCAL_NETWORK_POLICY.value:
        return "本地网络权限"
    if failure.retryable and error_code in {
        ErrorCode.NETWORK_TIMEOUT.value,
        ErrorCode.SSL_FAILURE.value,
        ErrorCode.HTTP_FAILURE.value,
        ErrorCode.BROWSER_RENDER_FAILED.value,
    }:
        return "网络临时失败"
    if error_code in {
        ErrorCode.CONTENT_NOT_PUBLISHED.value,
        ErrorCode.ISSUE_NOT_FOUND.value,
    }:
        return "页面无当期"
    if error_code == ErrorCode.POSITION_MISMATCH.value:
        return "位置不匹配"
    if error_code == ErrorCode.CANDIDATE_CONFLICT.value:
        return "解析歧义"
    if error_code == ErrorCode.ANCHOR_MISSING.value:
        return "锚点失效"
    return "其他失败"


def summarize_failures(failures: list[CrawlFailure]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for failure in failures:
        category = failure_category(failure)
        summary[category] = summary.get(category, 0) + 1
    return summary


__all__ = ["failure_category", "summarize_failures", "parse_failure_line",
           "failed_targets_from_text", "failed_targets_from_file"]
