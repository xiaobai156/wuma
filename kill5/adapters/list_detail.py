from __future__ import annotations

import html
import re
from urllib.parse import urljoin, urlparse

from ..network import ensure_same_origin, fetch_text
from ..parser import (
    DOCUMENT_BOUNDARY,
    extract_name_from_text,
    html_to_text,
    normalize_issue,
    normalize_keyword,
    remove_fragment,
)
from .static_topic import crawl_static_page

# 列表版块最多翻这么多页。当期帖子未发布时，版块可能有上百页，翻完会白跑很久并撞上
# 120 秒单站时限，把「本期未发布」误报成网络超时；到上限仍未命中就按未找到处理，
# 由上层报 ISSUE_NOT_FOUND。
MAX_LIST_PAGES = 5


def crawl_list_detail_page(
    url: str,
    issues: list[str],
    title_keywords: list[str],
    decoded_anchor_only: str | None = None,
    decoded_anchor_chunks: int = 1,
) -> tuple[str, str]:
    list_url = remove_fragment(url)
    entries = []
    normalized_title_keywords = [
        normalize_keyword(keyword) for keyword in title_keywords if normalize_keyword(keyword)
    ]
    wanted_issues = [normalize_issue(issue) for issue in issues]
    seen_pages = set()
    page_url = list_url
    pages_fetched = 0
    while page_url and page_url not in seen_pages and pages_fetched < MAX_LIST_PAGES:
        pages_fetched += 1
        seen_pages.add(page_url)
        list_page = fetch_text(page_url)
        next_page = None
        for match in re.finditer(
            r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            list_page,
            re.I | re.S,
        ):
            href = html.unescape(match.group(1))
            body = html_to_text(match.group(2))
            compact = normalize_keyword(body)
            absolute = urljoin(page_url, href)
            entries.append((absolute, body, compact, match.start()))
            if compact == normalize_keyword("下一页"):
                ensure_same_origin(absolute, list_url)
                next_page = absolute
        if all(
            any(
                f"{issue}期" in body
                and all(keyword in compact for keyword in normalized_title_keywords)
                for _href, body, compact, _start in entries
            )
            for issue in wanted_issues
        ):
            break
        page_url = next_page

    detail_parts = []
    for issue in issues:
        issue = normalize_issue(issue)
        issue_text = f"{issue}期"
        candidates = [
            item
            for item in entries
            if issue_text in item[1]
            and all(keyword in item[2] for keyword in normalized_title_keywords)
        ]
        if not candidates:
            continue
        ordered = sorted(candidates, key=lambda item: item[3])
        for candidate in ordered:
            _detail_name, detail_content = crawl_static_page(
                candidate[0],
                decoded_anchor_only=decoded_anchor_only,
                decoded_anchor_chunks=decoded_anchor_chunks,
                include_script_documents=bool(decoded_anchor_only),
            )
            detail_parts.extend([detail_content, DOCUMENT_BOUNDARY])

    combined = "\n".join(detail_parts)
    name = extract_name_from_text(combined, fallback=urlparse(url).netloc)
    return name, combined


__all__ = [
    'crawl_list_detail_page',
]
