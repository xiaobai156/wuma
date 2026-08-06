from __future__ import annotations

import base64
import json
import re
from urllib.error import HTTPError
from urllib.parse import urlparse

from .errors import CrawlError, ErrorCode
from .network import fetch_text, origin_key
from .parser import clean_name, html_to_text


ARTICLE_ID_FIELDS = (
    "id",
    "_id",
    "articleId",
    "article_id",
    "articleID",
    "recordId",
    "record_id",
)

def parse_user_id(url: str) -> str | None:
    match = re.search(r"/users/(\d+)", url)
    return match.group(1) if match else None


def fetch_json(url: str):
    text = fetch_text(url)
    return json.loads(text)


def is_http_404(exc: Exception) -> bool:
    return isinstance(exc, HTTPError) and exc.code == 404


def parse_admin_article_id(url: str) -> str | None:
    path = urlparse(url).path.rstrip("/")
    match = re.search(r"/article/admin/([^/]+)$", path)
    return match.group(1) if match else None


def parse_manager_article_id(url: str) -> str | None:
    path = urlparse(url).path.rstrip("/")
    match = re.search(r"/article/manager/([^/]+)$", path)
    return match.group(1) if match else None


def parse_lottery_article_id(url: str) -> str | None:
    path = urlparse(url).path.rstrip("/")
    match = re.search(r"/article/lottery/([^/]+)$", path)
    return match.group(1) if match else None


def decode_possible_base64(value: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) < 8:
        return text
    candidate = text.replace("-", "+").replace("_", "/")
    if not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", candidate):
        return text
    candidate = candidate + ("=" * ((4 - len(candidate) % 4) % 4))
    try:
        decoded = base64.b64decode(candidate, validate=True).decode("utf-8", errors="replace")
    except Exception:
        return text
    if "\ufffd" in decoded or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", decoded):
        return text
    return decoded


def walk_json_nodes(value, path: str = "$"):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk_json_nodes(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_json_nodes(child, f"{path}[{index}]")


def article_record_id_values(record: dict) -> list[str]:
    values = []
    for field in ARTICLE_ID_FIELDS:
        value = record.get(field)
        if value is not None and str(value).strip():
            values.append(str(value).strip())
    return values


def locate_article_record(payload, expected_article_id: str) -> tuple[dict, str]:
    expected = str(expected_article_id or "").strip()
    if not expected:
        raise CrawlError(
            ErrorCode.ARTICLE_ID_MISMATCH,
            "没有找到文章 ID",
            stage="article_identity",
        )

    matches: list[tuple[dict, str]] = []
    for path, value in walk_json_nodes(payload):
        if not isinstance(value, dict):
            continue
        if expected in article_record_id_values(value):
            matches.append((value, path))

    if not matches:
        raise CrawlError(
            ErrorCode.ARTICLE_ID_MISMATCH,
            f"文章接口没有找到 URL 文章 ID {expected}",
            stage="article_identity",
            evidence={"article_id": expected},
        )
    if len(matches) != 1:
        paths = ",".join(path for _record, path in matches[:3])
        raise CrawlError(
            ErrorCode.ARTICLE_ID_MISMATCH,
            f"文章接口找到多个相同文章 ID {expected}：{paths}，已停止避免跨记录取数",
            stage="article_identity",
            evidence={"article_id": expected, "record_paths": paths},
        )
    return matches[0]


def article_record_content(
    payload,
    expected_article_id: str,
    expected_name: str,
) -> tuple[str, str]:
    record, _path = locate_article_record(payload, expected_article_id)
    raw_author = record.get("authorNickname") or record.get("author")
    if not isinstance(raw_author, str) or not raw_author.strip():
        raise CrawlError(
            ErrorCode.DOCUMENT_BOUNDARY_ERROR,
            f"目标文章 ID {expected_article_id} 缺少作者字段",
            stage="article_record",
        )
    author = clean_name(raw_author)
    configured_author = clean_name(expected_name)
    if expected_name != "未命名" and author != configured_author:
        raise CrawlError(
            ErrorCode.DOCUMENT_BOUNDARY_ERROR,
            f"目标文章 ID {expected_article_id} 作者不匹配：期望 {expected_name}，实际 {author}",
            stage="article_record",
        )

    raw_title = record.get("title")
    if not isinstance(raw_title, str) or not raw_title.strip():
        raise CrawlError(
            ErrorCode.DOCUMENT_BOUNDARY_ERROR,
            f"目标文章 ID {expected_article_id} 缺少标题",
            stage="article_record",
        )
    raw_body = record.get("html")
    if not isinstance(raw_body, str) or not raw_body.strip():
        raw_body = record.get("content")
    if not isinstance(raw_body, str) or not raw_body.strip():
        raise CrawlError(
            ErrorCode.DOCUMENT_BOUNDARY_ERROR,
            f"目标文章 ID {expected_article_id} 缺少正文",
            stage="article_record",
        )

    title = decode_possible_base64(raw_title)
    body = decode_possible_base64(raw_body)
    if not html_to_text(title).strip():
        raise CrawlError(
            ErrorCode.DOCUMENT_BOUNDARY_ERROR,
            f"目标文章 ID {expected_article_id} 标题无有效内容",
            stage="article_record",
        )
    if not html_to_text(body).strip():
        raise CrawlError(
            ErrorCode.DOCUMENT_BOUNDARY_ERROR,
            f"目标文章 ID {expected_article_id} 正文无有效内容",
            stage="article_record",
        )
    # 作者单独校验；解析范围只含同一记录的标题和正文，避免锚点落到作者字段后放宽正文边界。
    return author, "\n".join([title, body])


def article_api_response_matches_id(
    response_url: str,
    expected_article_id: str,
    page_url: str,
    allowed_types: set[str],
) -> bool:
    if origin_key(response_url) != origin_key(page_url):
        return False
    path = urlparse(response_url).path.rstrip("/")
    match = re.search(
        rf"/api/(?:proxy/)?(?P<kind>admin|manager|lottery)-articles/"
        rf"{re.escape(expected_article_id)}$",
        path,
    )
    return bool(match and match.group("kind") in allowed_types)


__all__ = [
    'parse_user_id',
    'fetch_json',
    'is_http_404',
    'parse_admin_article_id',
    'parse_manager_article_id',
    'parse_lottery_article_id',
    'decode_possible_base64',
    'walk_json_nodes',
    'article_record_id_values',
    'locate_article_record',
    'article_record_content',
    'article_api_response_matches_id',
    'ARTICLE_ID_FIELDS',
]
