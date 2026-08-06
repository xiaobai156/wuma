from __future__ import annotations

import time

from ..documents import fetch_json, parse_user_id
from ..errors import CrawlError, ErrorCode
from ..parser import USER_RECORD_BOUNDARY, clean_name, origin

def crawl_user_page(
    url: str,
) -> tuple[str, str]:
    user_id = parse_user_id(url)
    if not user_id:
        raise CrawlError(
            ErrorCode.ARTICLE_ID_MISMATCH,
            "没有找到用户 ID",
            stage="user_identity",
        )

    base = origin(url)
    user = fetch_json(f"{base}/api/v1/users/{user_id}")
    if not isinstance(user, dict) or str(user.get("id") or "").strip() != user_id:
        raise CrawlError(
            ErrorCode.ARTICLE_ID_MISMATCH,
            f"用户接口用户 ID 不匹配：期望 {user_id}",
            stage="user_identity",
        )
    name = clean_name(user.get("nickname") or f"user_{user_id}")

    # 用户 profile 不是论坛正文，不能让签名、简介等字段参与期数和号码解析。
    all_parts: list[str] = []
    lt = None
    for _ in range(5):
        api_url = f"{base}/api/v1/users/{user_id}/forums"
        if lt:
            api_url += f"?lt={lt}"
        forums = fetch_json(api_url)
        if not isinstance(forums, list) or not forums:
            break

        for item in forums:
            if not isinstance(item, dict):
                raise CrawlError(
                    ErrorCode.DOCUMENT_BOUNDARY_ERROR,
                    "用户论坛接口记录格式异常",
                    stage="user_record",
                )
            record_user_ids = []
            for field in ("user_id", "userId"):
                value = item.get(field)
                if value is not None and str(value).strip():
                    record_user_ids.append(str(value).strip())
            nested_user = item.get("user")
            if isinstance(nested_user, dict):
                value = nested_user.get("id")
                if value is not None and str(value).strip():
                    record_user_ids.append(str(value).strip())
            if not record_user_ids or any(value != user_id for value in record_user_ids):
                raise CrawlError(
                    ErrorCode.DOCUMENT_BOUNDARY_ERROR,
                    f"用户论坛记录用户 ID 不匹配：期望 {user_id}，实际 {','.join(record_user_ids) or '缺失'}",
                    stage="user_record",
                )

            topic = item.get("topic")
            content = item.get("content")
            if not isinstance(topic, str) or not isinstance(content, str):
                raise CrawlError(
                    ErrorCode.DOCUMENT_BOUNDARY_ERROR,
                    "用户论坛记录标题或正文格式异常",
                    stage="user_record",
                )
            # 每条论坛记录单独封边，指定期数的标题和号码不能跨到下一条记录。
            all_parts.extend([topic, content, USER_RECORD_BOUNDARY])

        lt = forums[-1].get("id")
        if not lt:
            break
        time.sleep(0.1)

    return name, "\n".join(all_parts)


__all__ = [
    'crawl_user_page',
]
