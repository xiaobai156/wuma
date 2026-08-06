from __future__ import annotations

from urllib.parse import urlparse

from ..documents import (
    article_api_response_matches_id,
    article_record_content,
    fetch_json,
    is_http_404,
    parse_admin_article_id,
    parse_manager_article_id,
)
from ..errors import CrawlError, ErrorCode
from ..network import HEADERS
from ..parser import (
    html_to_text,
    normalize_keyword,
    origin,
    remove_fragment,
    unique_keep_order,
)

def crawl_admin_article_page(url: str, expected_name: str) -> tuple[str, str]:
    article_id = parse_admin_article_id(url)
    if not article_id:
        raise CrawlError(
            ErrorCode.ARTICLE_ID_MISMATCH,
            "没有找到文章 ID",
            stage="article_identity",
        )
    api_url = f"{origin(url)}/api/proxy/admin-articles/{article_id}"
    return crawl_article_api_payload(api_url, article_id, expected_name)


def crawl_manager_article_page(url: str, expected_name: str) -> tuple[str, str]:
    article_id = parse_manager_article_id(url) or parse_admin_article_id(url)
    if not article_id:
        raise CrawlError(
            ErrorCode.ARTICLE_ID_MISMATCH,
            "没有找到文章 ID",
            stage="article_identity",
        )
    api_url = f"{origin(url)}/api/proxy/manager-articles/{article_id}"
    return crawl_article_api_payload(api_url, article_id, expected_name)


def crawl_article_api_payload(
    api_url: str,
    expected_article_id: str,
    expected_name: str,
) -> tuple[str, str]:
    return article_record_content(fetch_json(api_url), expected_article_id, expected_name)


def render_article_admin_page(
    url: str,
    expected_article_id: str,
    expected_name: str,
) -> tuple[str, str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise CrawlError(
            ErrorCode.BROWSER_RENDER_FAILED,
            f"浏览器渲染不可用：{exc}",
            stage="browser_render",
        ) from exc

    page_url = remove_fragment(url)
    captured_records: list[tuple[str, str]] = []
    rejected_responses: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=HEADERS["User-Agent"], locale="zh-CN")

                def capture_article_response(response) -> None:
                    if response.status != 200 or not article_api_response_matches_id(
                        response.url,
                        expected_article_id,
                        page_url,
                        {"admin", "manager"},
                    ):
                        return
                    try:
                        captured_records.append(
                            article_record_content(
                                response.json(), expected_article_id, expected_name
                            )
                        )
                    except Exception as exc:
                        rejected_responses.append(str(exc))

                page.on("response", capture_article_response)
                page.goto(page_url, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(1500)
            finally:
                browser.close()
    except CrawlError:
        raise
    except Exception as exc:
        raise CrawlError(
            ErrorCode.BROWSER_RENDER_FAILED,
            f"浏览器渲染失败：{exc}",
            stage="browser_render",
            retryable=True,
        ) from exc

    unique_records = unique_keep_order(captured_records)
    if not unique_records:
        detail = f"；候选响应被拒绝：{rejected_responses[0]}" if rejected_responses else ""
        raise CrawlError(
            ErrorCode.ARTICLE_ID_MISMATCH,
            f"浏览器渲染没有捕获 URL 文章 ID {expected_article_id} 的有效单记录响应{detail}",
            stage="browser_article_identity",
        )
    if len(unique_records) != 1:
        raise CrawlError(
            ErrorCode.ARTICLE_ID_MISMATCH,
            f"浏览器渲染捕获多个 URL 文章 ID {expected_article_id} 的不同正文，已停止避免跨记录取数",
            stage="browser_article_identity",
        )
    return unique_records[0]


def validate_rendered_article_identity(content: str, configured_name: str, anchor=None) -> None:
    normalized_name = normalize_keyword(configured_name)
    normalized_content = normalize_keyword(html_to_text(content))
    if not normalized_name or normalized_name not in normalized_content:
        raise CrawlError(
            ErrorCode.DOCUMENT_BOUNDARY_ERROR,
            f"浏览器渲染正文没有找到配置目录身份：{configured_name}，已停止避免抓到同页其他文章",
            stage="browser_article_identity",
        )


def crawl_admin_article_page_with_fallback(
    url: str,
    expected_name: str,
) -> tuple[str, str, bool]:
    article_id = parse_admin_article_id(url)
    if not article_id:
        raise CrawlError(
            ErrorCode.ARTICLE_ID_MISMATCH,
            "没有找到文章 ID",
            stage="article_identity",
        )
    try:
        auto_name, content = crawl_admin_article_page(url, expected_name)
        return auto_name, content, False
    except Exception as exc:
        if is_http_404(exc):
            try:
                auto_name, content = crawl_manager_article_page(url, expected_name)
                return auto_name, content, False
            except Exception as manager_exc:
                if not is_http_404(manager_exc):
                    raise
                auto_name, content = render_article_admin_page(
                    url, article_id, expected_name
                )
                return auto_name, content, True
        raise


__all__ = [
    'crawl_admin_article_page',
    'crawl_manager_article_page',
    'crawl_article_api_payload',
    'render_article_admin_page',
    'validate_rendered_article_identity',
    'crawl_admin_article_page_with_fallback',
]
