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

    正文**只取解码块**。这类站点的期号与号码都在 strdecode 负载里，页面本身
    是导航壳；退回整页原文会把导航栏内容当正文解析（fail-open），按项目规则
    应当失败而不是猜测，所以解码为空时直接报错。
    """
    url = str(spec.get("url") or "").strip()
    if not url:
        raise ValueError("merged 来源缺少 url")
    name = str(spec.get("name") or "").strip()
    encoding = spec.get("encoding")

    page = fetch_text(url, encoding=encoding)
    decoded: list[str] = decode_strdecode_payloads(page)

    failed_scripts = 0
    for src in _SCRIPT_SRC_RE.findall(page):
        try:
            script = fetch_text(_absolute(src, url), encoding=encoding)
        except Exception:  # noqa: BLE001
            failed_scripts += 1
            continue
        decoded.extend(decode_strdecode_payloads(script))

    if not decoded:
        raise ValueError(
            f"merged 来源解码为空（页面 {len(page)} 字节，"
            f"脚本抓取失败 {failed_scripts} 个）：{url}"
        )
    return name, "\n".join(decoded)


__all__ = ["MERGED_KIND", "crawl_merged_source", "_source_specs"]
