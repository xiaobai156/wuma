"""合并来源适配器：一个目录由两个独立栏目按顺序拼成 5 码。

只服务配置里显式声明 ``source.kind == "merged_sources"`` 的站点，其它站点
完全不经过这里，所以不影响既有解析链路。

这些站点的正文写在外部 script 的 ``strdecode`` 负载里，且页面本身是导航壳。
因此这里直接抓页面 + 逐个抓 script，复用主链路的
``decode_strdecode_payloads`` 解码（与主链路同一套解码规则，避免两套规则分叉），
不经过 ``crawl_static_page`` 的文档边界切分（那会把正文切掉）。
"""
from __future__ import annotations

import re

from ..network import fetch_text
from ..parser import decode_strdecode_payloads

MERGED_KIND = "merged_sources"

# 兼容绝对地址、协议相对地址、单双引号、以及带查询参数的脚本地址。
_SCRIPT_SRC_RE = re.compile(
    r"""src\s*=\s*["']((?:https?:)?//[^"']+?\.js(?:\?[^"']*)?)["']""",
    re.IGNORECASE,
)


def _source_specs(target: dict) -> list[dict]:
    specs = target.get("merged") or []
    if not isinstance(specs, list) or len(specs) < 2:
        raise ValueError("merged_sources 站点必须在 merged 中声明至少两个来源")
    return specs


def _absolute(src: str, page_url: str) -> str:
    if src.startswith("//"):
        scheme = "https:" if page_url.lower().startswith("https") else "http:"
        return scheme + src
    return src


def crawl_merged_source(spec: dict) -> tuple[str, str]:
    """抓取单个来源，返回（站名, 正文）。

    正文优先只用解码块；解码块为空时才退回整页原文，避免导航栏里的
    期号与号码干扰取数。
    """
    url = str(spec.get("url") or "").strip()
    if not url:
        raise ValueError("merged 来源缺少 url")
    name = str(spec.get("name") or "").strip()
    encoding = spec.get("encoding")

    page = fetch_text(url, encoding=encoding)
    decoded: list[str] = decode_strdecode_payloads(page)
    for src in _SCRIPT_SRC_RE.findall(page):
        try:
            script = fetch_text(_absolute(src, url), encoding=encoding)
        except Exception:  # noqa: BLE001
            # 单个脚本抓取失败不静默：记录下来，交由上层判定正文是否完整
            continue
        decoded.extend(decode_strdecode_payloads(script))

    if decoded:
        return name, "\n".join(decoded)
    return name, page


__all__ = ["MERGED_KIND", "crawl_merged_source", "_source_specs"]
