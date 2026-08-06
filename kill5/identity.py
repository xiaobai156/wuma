from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .domain import CrawlFailure, CrawlResult
from .parser import preserve_configured_name


def canonical_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    netloc = parsed.netloc.lower()
    if (parsed.scheme.lower() == "https" and netloc.endswith(":443")) or (
        parsed.scheme.lower() == "http" and netloc.endswith(":80")
    ):
        netloc = netloc.rsplit(":", 1)[0]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    fragment = parsed.fragment
    if not (fragment.startswith("/") or fragment.startswith("!/")):
        fragment = ""
    return urlunsplit(
        (
            parsed.scheme.lower(),
            netloc,
            path,
            query,
            fragment,
        )
    )


def target_identity(target: dict) -> tuple[str, ...]:
    stable_id = str(target.get("id") or "").strip()
    if stable_id:
        return ("id", stable_id)
    return (
        "legacy",
        preserve_configured_name(target.get("name") or ""),
        canonical_url(target.get("url") or ""),
    )


def result_identity(result: CrawlResult) -> tuple[str, ...]:
    stable_id = str(result.target_id or "").strip()
    if stable_id:
        return ("id", stable_id)
    return (
        "legacy",
        preserve_configured_name(result.name),
        canonical_url(result.url),
    )


def failure_identity(failure: CrawlFailure) -> tuple[str, ...]:
    evidence = failure.evidence if isinstance(failure.evidence, dict) else {}
    stable_id = str(evidence.get("target_id") or "").strip()
    if stable_id:
        return ("id", stable_id)
    return (
        "legacy",
        preserve_configured_name(failure.name),
        canonical_url(failure.url),
    )


def target_for_result(result: CrawlResult, targets: list[dict]) -> dict | None:
    wanted = result_identity(result)
    matches = [target for target in targets if target_identity(target) == wanted]
    if len(matches) == 1:
        return matches[0]
    return None


def dedupe_results(results: list[CrawlResult]) -> list[CrawlResult]:
    seen = set()
    deduped = []
    for item in results:
        key = (
            result_identity(item),
            item.issue,
            tuple(item.numbers),
            item.name,
            item.url,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


__all__ = [
    "canonical_url",
    "dedupe_results",
    "failure_identity",
    "result_identity",
    "target_for_result",
    "target_identity",
]
