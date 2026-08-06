from __future__ import annotations

import html
import re
from urllib.parse import urljoin, urlparse

from ..network import fetch_text
from ..parser import (
    DOCUMENT_BOUNDARY,
    extract_name_from_text,
    html_to_text,
    normalize_issue,
    normalize_keyword,
    remove_fragment,
)
from .static_topic import crawl_static_page

def crawl_list_detail_page(
    url: str,
    issues: list[str],
    title_keywords: list[str],
) -> tuple[str, str]:
    page_url = remove_fragment(url)
    list_page = fetch_text(page_url)
    entries = []
    for match in re.finditer(
        r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        list_page,
        re.I | re.S,
    ):
        href = html.unescape(match.group(1))
        body = html_to_text(match.group(2))
        compact = normalize_keyword(body)
        entries.append((urljoin(page_url, href), body, compact, match.start()))

    detail_parts = []
    normalized_title_keywords = [
        normalize_keyword(keyword) for keyword in title_keywords if normalize_keyword(keyword)
    ]
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
            _detail_name, detail_content = crawl_static_page(candidate[0])
            detail_parts.extend([detail_content, DOCUMENT_BOUNDARY])

    combined = "\n".join(detail_parts)
    name = extract_name_from_text(combined, fallback=urlparse(url).netloc)
    return name, combined


__all__ = [
    'crawl_list_detail_page',
]
