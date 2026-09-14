from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from .identity import canonical_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGETS_FILE = PROJECT_ROOT / "targets.json"

CONFIG_REGIONS = {
    "top",
    "upper",
    "head",
    "first",
    "上",
    "顶部",
    "bottom",
    "lower",
    "tail",
    "last",
    "下",
    "尾部",
    "底部",
}
BASHU_URL = "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/116164"
INSECURE_TLS_TARGET = (
    "倦鸟归林",
    "https://nlafoq9v.dh5565656.xyz/bbs/topic.php?id=918",
)
SOURCE_KINDS = {
    "static_topic",
    "decoded_script",
    "dynamic_article",
    "user_page",
    "list_detail",
    "lottery_article",
}
V2_TARGET_FIELDS = {
    "id",
    "name",
    "url",
    "region",
    "count",
    "source",
    "parse",
    "network",
    "disabled",
}
SOURCE_FIELDS = {"kind"}
PARSE_FIELDS = {
    "keywords",
    "anchor",
    "stop_anchor",
    "decoded_anchor_only",
    "decoded_anchor_chunks",
    "decoded_stop_anchor",
    "decoded_anchor_to_end",
    "first_issue_chain",
    "issue_position_window",
    "keyword_before_issue",
    "keyword_before_issue_window",
    "list_title_keywords",
    "position",
    "stats_max_row",
    "stats_block_keywords",
}
ISSUE_POSITION_WINDOW = 3
NETWORK_FIELDS = {
    "encoding",
    "insecure_tls",
    "rendered_fallback_selectors",
}


def target_allows_insecure_tls(target: dict) -> bool:
    return target.get("insecure_tls") is True and (
        str(target.get("name") or "").strip(),
        str(target.get("url") or "").strip(),
    ) == INSECURE_TLS_TARGET


def flatten_v2_target(item: dict, index: int) -> dict:
    unknown_target_fields = set(item) - V2_TARGET_FIELDS
    if unknown_target_fields:
        raise ValueError(
            f"targets.json 第 {index} 条包含不允许字段：{sorted(unknown_target_fields)}"
        )
    if "disabled" in item and not isinstance(item["disabled"], bool):
        raise ValueError(f"targets.json 第 {index} 条 disabled 必须是 true/false")
    source = item.get("source")
    parse = item.get("parse")
    network = item.get("network", {})
    if not isinstance(source, dict) or source.get("kind") not in SOURCE_KINDS:
        raise ValueError(f"targets.json 第 {index} 条缺少有效 source.kind")
    if not isinstance(parse, dict):
        raise ValueError(f"targets.json 第 {index} 条 parse 必须是对象")
    if not isinstance(network, dict):
        raise ValueError(f"targets.json 第 {index} 条 network 必须是对象")
    issue_position_window = parse.get("issue_position_window")
    if issue_position_window is not None and issue_position_window != ISSUE_POSITION_WINDOW:
        raise ValueError(
            f"targets.json 第 {index} 条 {item.get('name') or item.get('id')} 的 issue_position_window 必须固定为 3"
        )
    for section_name, section, allowed_fields in (
        ("source", source, SOURCE_FIELDS),
        ("parse", parse, PARSE_FIELDS),
        ("network", network, NETWORK_FIELDS),
    ):
        unknown_fields = set(section) - allowed_fields
        if unknown_fields:
            raise ValueError(
                f"targets.json 第 {index} 条 {section_name} 包含不允许字段或保留字段：{sorted(unknown_fields)}"
            )

    target = {
        **parse,
        **network,
        "id": item.get("id"),
        "name": item.get("name"),
        "url": item.get("url"),
        "region": item.get("region"),
        "count": item.get("count"),
        "source_kind": source["kind"],
    }
    target.setdefault("issue_position_window", ISSUE_POSITION_WINDOW)
    if source["kind"] == "list_detail":
        target["list_detail"] = True
    if item.get("disabled"):
        target["disabled"] = True
    return target


def load_target_document(
    path: Path = TARGETS_FILE,
    *,
    allow_legacy: bool = False,
) -> tuple[list[dict], int]:
    if not path.exists():
        raise FileNotFoundError(f"目标配置文件不存在：{path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        if not allow_legacy:
            raise ValueError(
                "正式 targets.json 必须使用 schema_version=2；旧列表只允许候选站隔离测试"
            )
        return raw, 1
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
        raise ValueError("targets.json 根对象必须是旧目标列表或 schema_version=2 对象")
    targets = raw.get("targets")
    if not isinstance(targets, list):
        raise ValueError("targets.json schema_version=2 的 targets 必须是列表")
    return [
        flatten_v2_target(item, index)
        if isinstance(item, dict)
        else item
        for index, item in enumerate(targets, start=1)
    ], 2


def load_targets(
    path: Path = TARGETS_FILE,
    *,
    allow_legacy: bool = False,
) -> list[dict]:
    data, schema_version = load_target_document(path, allow_legacy=allow_legacy)

    names: set[str] = set()
    urls: set[str] = set()
    ids: set[str] = set()
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"targets.json 第 {index} 条不是对象")
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        keywords = item.get("keywords")
        region = str(item.get("region") or "").strip().lower()
        stable_id = str(item.get("id") or "").strip()
        source_kind = item.get("source_kind")
        if "allow_ambiguous" in item:
            raise ValueError(
                f"targets.json 第 {index} 条 {name or stable_id} 不允许配置 allow_ambiguous；候选冲突必须失败"
            )
        if "allow_duplicate_numbers" in item:
            raise ValueError(
                f"targets.json 第 {index} 条 {name or stable_id} 不允许配置 allow_duplicate_numbers；重复号码必须失败"
            )
        if "disabled" in item and not isinstance(item["disabled"], bool):
            raise ValueError(f"targets.json 第 {index} 条 {name or stable_id} 的 disabled 必须是 true/false")
        if schema_version == 2:
            if not stable_id:
                raise ValueError(f"targets.json 第 {index} 条缺少稳定 id")
            if stable_id in ids:
                raise ValueError(f"targets.json 存在重复稳定 ID：{stable_id}")
            if source_kind not in SOURCE_KINDS:
                raise ValueError(
                    f"targets.json 第 {index} 条 {name or stable_id} source_kind 无效"
                )
        if not name:
            raise ValueError(f"targets.json 第 {index} 条缺少 name")
        parsed_url = urlparse(url)
        if not url or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(f"targets.json 第 {index} 条缺少有效 http(s) url")
        if not isinstance(keywords, list) or not keywords or not all(
            str(keyword).strip() for keyword in keywords
        ):
            raise ValueError(f"targets.json 第 {index} 条 {name} 缺少有效 keywords")
        if region not in CONFIG_REGIONS:
            raise ValueError(
                f"targets.json 第 {index} 条 {name} 缺少有效 top/bottom region"
            )
        issue_position_window = item.get("issue_position_window")
        if issue_position_window is not None and issue_position_window != ISSUE_POSITION_WINDOW:
            raise ValueError(
                f"targets.json 第 {index} 条 {name} 的 issue_position_window 必须固定为 3"
            )
        if name in names:
            raise ValueError(f"targets.json 存在同名目录：{name}")
        normalized_url = canonical_url(url)
        if normalized_url in urls:
            raise ValueError(f"targets.json 存在重复 URL：{url}")
        if "insecure_tls" in item and not isinstance(item["insecure_tls"], bool):
            raise ValueError(
                f"targets.json 第 {index} 条 {name} 的 insecure_tls 必须是 true/false"
            )
        if item.get("insecure_tls") and (name, url) != INSECURE_TLS_TARGET:
            raise ValueError(
                f"targets.json 第 {index} 条 {name} 不允许关闭 TLS 证书校验"
            )
        rendered_selectors = item.get("rendered_fallback_selectors")
        if rendered_selectors is not None and (
            not isinstance(rendered_selectors, list)
            or not rendered_selectors
            or not all(
                isinstance(selector, str) and selector.strip()
                for selector in rendered_selectors
            )
        ):
            raise ValueError(
                f"targets.json 第 {index} 条 {name} 的 rendered_fallback_selectors 必须是非空选择器列表"
            )
        decoded_stop_anchor = item.get("decoded_stop_anchor")
        if decoded_stop_anchor is not None and (
            not isinstance(decoded_stop_anchor, str)
            or not decoded_stop_anchor.strip()
            or not item.get("decoded_anchor_only")
        ):
            raise ValueError(
                f"targets.json 第 {index} 条 {name} 的 decoded_stop_anchor 必须是非空字符串，且必须同时配置 decoded_anchor_only"
            )
        decoded_anchor_to_end = item.get("decoded_anchor_to_end")
        if decoded_anchor_to_end is not None and not isinstance(
            decoded_anchor_to_end, bool
        ):
            raise ValueError(
                f"targets.json 第 {index} 条 {name} 的 decoded_anchor_to_end 必须是布尔值"
            )
        if decoded_anchor_to_end and not item.get("decoded_anchor_only"):
            raise ValueError(
                f"targets.json 第 {index} 条 {name} 的 decoded_anchor_to_end=true 时必须同时配置 decoded_anchor_only"
            )
        if decoded_anchor_to_end and decoded_stop_anchor:
            raise ValueError(
                f"targets.json 第 {index} 条 {name} 的 decoded_anchor_to_end 与 decoded_stop_anchor 不能同时配置"
            )

        stats_max_row = item.get("stats_max_row")
        if stats_max_row is not None and not isinstance(stats_max_row, bool):
            raise ValueError(
                f"targets.json 第 {index} 条 {name} 的 stats_max_row 必须是 true/false"
            )
        stats_block_keywords = item.get("stats_block_keywords")
        if stats_max_row:
            if (
                not isinstance(stats_block_keywords, list)
                or not stats_block_keywords
                or not all(
                    isinstance(keyword, str) and keyword.strip()
                    for keyword in stats_block_keywords
                )
            ):
                raise ValueError(
                    f"targets.json 第 {index} 条 {name} 的 stats_max_row=true 时必须配置非空 stats_block_keywords 列表"
                )
        elif stats_block_keywords is not None:
            raise ValueError(
                f"targets.json 第 {index} 条 {name} 未启用 stats_max_row，不允许配置 stats_block_keywords"
            )

        count = item.get("count")
        is_bashu_exception = (
            name == "拔树寻根"
            and url == BASHU_URL
            and count == 6
            and any(
                "期杀6码" in str(keyword).replace("碼", "码")
                for keyword in keywords
            )
            and region in {"top", "上", "顶部"}
        )
        if stats_max_row:
            if count is not None:
                raise ValueError(
                    f"targets.json 第 {index} 条 {name} 的 stats_max_row=true 时 count 必须为 null（号码个数可变）"
                )
        elif count != 5 and not is_bashu_exception:
            raise ValueError(
                f"targets.json 第 {index} 条 {name} 的 count 必须固定为 5；拔树寻根允许 count=6 例外"
            )
        names.add(name)
        urls.add(normalized_url)
        if stable_id:
            ids.add(stable_id)

    return [item for item in data if not item.get("disabled")]
