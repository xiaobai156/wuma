from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import html
import re
from urllib.parse import urljoin, urlparse

from ..errors import CrawlError, ErrorCode, classify_exception
from ..network import HEADERS, ensure_same_origin, fetch_text, origin_key
from ..parser import (
    DOCUMENT_BOUNDARY,
    decode_strdecode_payloads,
    extract_name_from_text,
    html_to_text,
    remove_fragment,
    select_decoded_anchor_parts,
    unique_keep_order,
)


SCRIPT_WORKERS = 2

def is_fetchable_script(src: str, page_url: str) -> bool:
    parsed_src = urlparse(src)
    parsed_page = urlparse(page_url)
    if "hm.baidu.com" in parsed_src.netloc:
        return False
    if "/upload/script/" in src:
        return True
    if parsed_src.netloc == parsed_page.netloc:
        return True
    if not parsed_src.netloc:
        return True
    return False


def script_urls(html_value: str, page_url: str) -> list[str]:
    urls = []
    for match in re.finditer(r"<script[^>]+src=[\"']([^\"']+)[\"']", html_value, re.I):
        src = html.unescape(match.group(1))
        absolute = urljoin(page_url, src)
        if is_fetchable_script(absolute, page_url):
            urls.append(absolute)
    return unique_keep_order(urls)


def crawl_static_page(
    url: str,
    encoding: str | None = None,
    decoded_anchor_only: str | None = None,
    decoded_anchor_chunks: int = 1,
    decoded_stop_anchor: str | None = None,
    decoded_anchor_to_end: bool = False,
    allow_insecure_tls: bool = False,
) -> tuple[str, str]:
    page_url = remove_fragment(url)
    if allow_insecure_tls:
        page = fetch_text(page_url, encoding=encoding, allow_insecure_tls=True)
    else:
        page = fetch_text(page_url, encoding=encoding)
    chunk_count = max(1, int(decoded_anchor_chunks or 1))

    def source_documents(source: str) -> list[str]:
        decoded = decode_strdecode_payloads(source)
        if not decoded_anchor_only:
            return [source, *decoded]
        selected = select_decoded_anchor_parts(
            decoded,
            decoded_anchor_only,
            chunk_count,
            stop_anchor=decoded_stop_anchor,
            to_end=decoded_anchor_to_end,
        )
        return ["\n".join(selected)] if selected else []

    parts = source_documents(page)
    urls = script_urls(page, page_url)

    def fetch_script_parts(src: str) -> list[str]:
        # TLS 例外仅限该页面自身来源，第三方脚本仍使用正常证书校验。
        if allow_insecure_tls and origin_key(src) == origin_key(page_url):
            script = fetch_text(src, encoding=encoding, allow_insecure_tls=True)
        else:
            script = fetch_text(src, encoding=encoding)
        return source_documents(script)

    if urls:
        script_parts: list[list[str] | None] = [None] * len(urls)
        required_errors: list[tuple[str, BaseException]] = []
        with ThreadPoolExecutor(max_workers=min(SCRIPT_WORKERS, len(urls))) as executor:
            future_map = {
                executor.submit(fetch_script_parts, src): (index, src)
                for index, src in enumerate(urls)
            }
            for future in as_completed(future_map):
                index, src = future_map[future]
                try:
                    script_parts[index] = future.result()
                except Exception as exc:
                    if "/upload/script/" in src:
                        required_errors.append((src, exc))
        if required_errors:
            first_error = required_errors[0][1]
            code, retryable = classify_exception(first_error)
            detail = "；".join(f"{src}: {exc}" for src, exc in required_errors)
            raise CrawlError(
                code if retryable else ErrorCode.DOCUMENT_BOUNDARY_ERROR,
                f"页面数据脚本抓取失败，已停止避免使用不完整文档：{detail}",
                stage="script_documents",
                retryable=retryable,
            )
        for fetched_parts in script_parts:
            if fetched_parts:
                parts.extend(fetched_parts)

    combined = f"\n{DOCUMENT_BOUNDARY}\n".join(parts)
    if decoded_anchor_only and not combined:
        raise CrawlError(
            ErrorCode.ANCHOR_MISSING,
            f"没有找到专属解码正文锚点：{decoded_anchor_only}",
            stage="decoded_document_scope",
        )
    name = extract_name_from_text(combined, fallback=urlparse(url).netloc)
    return name, combined


def render_static_page(
    url: str,
    selectors: list[str],
    encoding: str | None = None,
) -> tuple[str, str]:
    if not selectors:
        raise CrawlError(
            ErrorCode.DOCUMENT_BOUNDARY_ERROR,
            "未配置浏览器渲染正文选择器",
            stage="browser_render",
        )
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise CrawlError(
            ErrorCode.BROWSER_RENDER_FAILED,
            f"浏览器渲染不可用：{exc}",
            stage="browser_render",
        ) from exc

    page_url = remove_fragment(url)
    parts: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=HEADERS["User-Agent"], locale="zh-CN")
                response = page.goto(page_url, wait_until="networkidle", timeout=45000)
                if response is not None and response.status != 200:
                    raise CrawlError(
                        ErrorCode.HTTP_FAILURE,
                        f"浏览器渲染页面 HTTP 状态异常：{response.status}",
                        stage="browser_render",
                        retryable=response.status in {408, 425, 429, 500, 502, 503, 504},
                        evidence={"http_status": response.status},
                    )
                ensure_same_origin(page.url, page_url)
                page.wait_for_timeout(1500)

                for selector in selectors:
                    locator = page.locator(selector)
                    count = locator.count()
                    if count != 1:
                        raise CrawlError(
                            ErrorCode.DOCUMENT_BOUNDARY_ERROR,
                            f"浏览器渲染选择器 {selector!r} 匹配 {count} 个元素，已停止避免跨栏目取数",
                            stage="browser_document_scope",
                        )
                    parts.append(locator.evaluate("(element) => element.outerHTML"))
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

    combined = "\n".join(parts)
    if not html_to_text(combined).strip():
        raise CrawlError(
            ErrorCode.DOCUMENT_BOUNDARY_ERROR,
            "浏览器渲染选择器内容为空",
            stage="browser_document_scope",
        )
    name = extract_name_from_text(combined, fallback=urlparse(url).netloc)
    return name, combined


__all__ = [
    'is_fetchable_script',
    'script_urls',
    'crawl_static_page',
    'render_static_page',
    'SCRIPT_WORKERS',
]
