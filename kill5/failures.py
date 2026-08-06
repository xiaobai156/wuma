from __future__ import annotations

from .domain import CrawlFailure
from .errors import ErrorCode


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


__all__ = ["failure_category", "summarize_failures"]
