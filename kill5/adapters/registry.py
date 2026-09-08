from __future__ import annotations

import re

from ..config import target_allows_insecure_tls
from ..documents import (
    parse_admin_article_id,
    parse_lottery_article_id,
    parse_manager_article_id,
    parse_user_id,
)
from ..domain import ResolvedContent
from .decoded_script import crawl_decoded_script_page
from .dynamic_article import (
    crawl_admin_article_page_with_fallback,
    crawl_manager_article_page,
)
from .list_detail import crawl_list_detail_page
from .lottery_article import crawl_lottery_article_page
from .static_topic import crawl_static_page
from .user_page import crawl_user_page


def is_classic_lottery_article(url: str) -> bool:
    path = url.split("?", 1)[0].rstrip("/")
    return bool(
        re.search(r"/(?:article|gsb)\.aspx$", path, re.IGNORECASE)
        or re.search(r"/Article\.Aspx$", path)
    )


def adapter_kind(target: dict) -> str:
    configured_kind = target.get("source_kind")
    if configured_kind:
        return str(configured_kind)
    url = str(target.get("url") or "")
    if parse_admin_article_id(url) or parse_manager_article_id(url):
        return "dynamic_article"
    if parse_user_id(url):
        return "user_page"
    if target.get("list_detail"):
        return "list_detail"
    if parse_lottery_article_id(url) or is_classic_lottery_article(url):
        return "lottery_article"
    if target.get("decoded_anchor_only"):
        return "decoded_script"
    return "static_topic"


def crawl_regular_static_target(target: dict) -> tuple[str, str]:
    return crawl_static_page(
        target["url"],
        encoding=target.get("encoding"),
        allow_insecure_tls=target_allows_insecure_tls(target),
    )


def fetch_target_content(
    target: dict,
    issues: list[str],
    configured_name: str,
) -> ResolvedContent:
    url = target["url"]
    kind = adapter_kind(target)

    if kind == "dynamic_article":
        article_id = parse_admin_article_id(url)
        if article_id:
            name, content, rendered = crawl_admin_article_page_with_fallback(
                url,
                configured_name,
            )
            return ResolvedContent(
                name=name,
                content=content,
                source_kind=kind,
                rendered=rendered,
                record_id=article_id,
            )
        record_id = parse_manager_article_id(url)
        name, content = crawl_manager_article_page(url, configured_name)
        return ResolvedContent(
            name=name,
            content=content,
            source_kind=kind,
            record_id=record_id,
        )

    if kind == "user_page":
        name, content = crawl_user_page(
            url,
        )
        return ResolvedContent(name=name, content=content, source_kind=kind)

    if kind == "list_detail":
        name, content = crawl_list_detail_page(
            url,
            issues,
            title_keywords=target.get("list_title_keywords") or [configured_name],
            decoded_anchor_only=target.get("decoded_anchor_only"),
            decoded_anchor_chunks=target.get("decoded_anchor_chunks", 1),
        )
        return ResolvedContent(name=name, content=content, source_kind=kind)

    if kind == "lottery_article" and parse_lottery_article_id(url):
        name, content = crawl_lottery_article_page(url, configured_name)
        return ResolvedContent(
            name=name,
            content=content,
            source_kind=kind,
            record_id=parse_lottery_article_id(url),
        )

    if kind == "decoded_script":
        name, content = crawl_decoded_script_page(target)
        return ResolvedContent(name=name, content=content, source_kind=kind)

    name, content = crawl_regular_static_target(target)
    return ResolvedContent(name=name, content=content, source_kind=kind)


__all__ = [
    "adapter_kind",
    "fetch_target_content",
    "is_classic_lottery_article",
]
