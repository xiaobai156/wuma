"""合并来源适配器：一个目录由两个独立栏目按顺序拼成 5 码。

只服务配置里显式声明 ``source.kind == "merged_sources"`` 的站点，其它站点
完全不经过这里，所以不影响既有解析链路。

这些站点的正文写在外部 script 的 ``strdecode`` 负载里，且页面本身是导航壳。
因此这里直接抓页面 + 逐个抓 script 并解码，把所有解码块按顺序拼起来，
不经过 ``crawl_static_page`` 的文档边界切分（那会把正文切掉）。
"""
from __future__ import annotations

import base64
import re

from ..network import fetch_text

MERGED_KIND = "merged_sources"

_SCRIPT_SRC_RE = re.compile(r'src="(https?://[^"]+\.js)"')
_STRDECODE_RE = re.compile(r'strdecode\("([^"]+)"\)')


def _decode_payloads(text: str) -> list[str]:
    out = []
    for payload in _STRDECODE_RE.findall(text):
        try:
            out.append(base64.b64decode(payload).decode('utf-8', 'replace'))
        except Exception:  # noqa: BLE001
            continue
    return out


def _source_specs(target: dict) -> list[dict]:
    specs = target.get("merged") or []
    if not isinstance(specs, list) or len(specs) < 2:
        raise ValueError("merged_sources 站点必须在 merged 中声明至少两个来源")
    return specs


def crawl_merged_source(spec: dict) -> tuple[str, str]:
    """抓取单个来源，返回（站名, 全部解码后的正文）。"""
    url = str(spec.get("url") or "").strip()
    if not url:
        raise ValueError("merged 来源缺少 url")
    name = str(spec.get("name") or "").strip()

    page = fetch_text(url, encoding=spec.get("encoding"))
    parts = [page, *_decode_payloads(page)]
    for src in _SCRIPT_SRC_RE.findall(page):
        try:
            script = fetch_text(src, encoding=spec.get("encoding"))
        except Exception:  # noqa: BLE001
            continue
        parts.extend(_decode_payloads(script))
    return name, "\n".join(parts)


__all__ = ["MERGED_KIND", "crawl_merged_source", "_source_specs"]
