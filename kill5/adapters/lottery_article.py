from __future__ import annotations

from urllib.parse import urlparse

from ..documents import parse_lottery_article_id
from ..errors import CrawlError, ErrorCode
from ..parser import origin
from .dynamic_article import crawl_article_api_payload

def crawl_lottery_article_page(url: str, expected_name: str) -> tuple[str, str]:
    article_id = parse_lottery_article_id(url)
    if not article_id:
        raise CrawlError(
            ErrorCode.ARTICLE_ID_MISMATCH,
            "没有找到文章 ID",
            stage="article_identity",
        )
    api_url = f"{origin(url)}/api/proxy/lottery-articles/{article_id}"
    query = urlparse(url).query
    if query:
        api_url = f"{api_url}?{query}"
    return crawl_article_api_payload(api_url, article_id, expected_name)


__all__ = [
    'crawl_lottery_article_page',
]
