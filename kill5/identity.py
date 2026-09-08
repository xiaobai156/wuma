from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .domain import CrawlFailure, CrawlResult
from .parser import normalize_region, preserve_configured_name


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


def _record_id_from_url(url: str) -> str:
    parsed = urlsplit(canonical_url(url))
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    lowered = [part.lower() for part in parts]

    def clean(value: str) -> str:
        return re.sub(r"\.(?:s?html?|php)$", "", value, flags=re.IGNORECASE)

    for index in range(len(parts) - 2):
        article_type = lowered[index + 1]
        if lowered[index] == "article" and article_type in {
            "admin",
            "manager",
            "lottery",
        }:
            return f"{article_type}:{clean(parts[index + 2])}"

    for marker in ("topic", "manager", "admin", "lottery", "id"):
        if marker in lowered:
            index = lowered.index(marker)
            if index + 1 < len(parts):
                return f"{marker}:{clean(parts[index + 1])}"

    query = {
        key.lower(): value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    }
    for key in ("id", "tid", "topic", "article_id", "articleid"):
        if query.get(key):
            return f"{key}:{query[key]}"

    fragment_parts = [part for part in parsed.fragment.lstrip("!/").split("/") if part]
    if len(fragment_parts) >= 2 and fragment_parts[0].lower() in {"user", "users"}:
        return f"user:{clean(fragment_parts[1])}"
    return ""


def target_cache_identity(target: dict, *, project_name: str) -> dict[str, object]:
    """Return the configuration identity persisted with the formal cache."""
    parse_fields = (
        "keywords",
        "anchor",
        "stop_anchor",
        "decoded_anchor_only",
        "decoded_anchor_chunks",
        "decoded_stop_anchor",
        "decoded_anchor_to_end",
        "first_issue_chain",
        "issue_position_window",
        "keyword_before_issue",
        "keyword_before_issue_window",
        "list_title_keywords",
        "position",
    )
    network_fields = ("encoding", "insecure_tls", "rendered_fallback_selectors")
    config = {
        "source_kind": target.get("source_kind"),
        "count": target.get("count"),
        "region": normalize_region(target.get("region")),
        "parse": {key: target.get(key) for key in parse_fields if key in target},
        "network": {key: target.get(key) for key in network_fields if key in target},
    }
    fingerprint = hashlib.sha256(
        json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "directory": project_name,
        "site_name": preserve_configured_name(target.get("name") or ""),
        "url": canonical_url(target.get("url") or ""),
        "record_id": _record_id_from_url(target.get("url") or ""),
        "column": {
            "keywords": target.get("keywords"),
            "anchor": target.get("anchor")
            or target.get("decoded_anchor_only"),
            "stop_anchor": target.get("stop_anchor")
            or target.get("decoded_stop_anchor"),
        },
        "field": "杀五码",
        "direction": normalize_region(target.get("region")),
        "config_fingerprint": fingerprint,
    }


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
    "target_cache_identity",
    "target_for_result",
    "target_identity",
]
