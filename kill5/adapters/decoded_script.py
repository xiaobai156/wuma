from __future__ import annotations

from ..config import target_allows_insecure_tls
from .static_topic import crawl_static_page


def crawl_decoded_script_page(target: dict) -> tuple[str, str]:
    return crawl_static_page(
        target["url"],
        encoding=target.get("encoding"),
        decoded_anchor_only=target.get("decoded_anchor_only"),
        decoded_anchor_chunks=target.get("decoded_anchor_chunks", 1),
        decoded_stop_anchor=target.get("decoded_stop_anchor"),
        decoded_anchor_to_end=target.get("decoded_anchor_to_end", False),
        allow_insecure_tls=target_allows_insecure_tls(target),
    )


__all__ = ["crawl_decoded_script_page"]
