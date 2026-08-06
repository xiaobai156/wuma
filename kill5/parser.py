from __future__ import annotations

import base64
import html
import re
import unicodedata
from typing import Iterable
from urllib.parse import urlparse

from .errors import CrawlError, ErrorCode


CANDIDATE_STRICT_THRESHOLD = 30
CANDIDATE_REGION_WINDOW = 3
USER_RECORD_BOUNDARY = "[[USER_RECORD_BOUNDARY]]"
DOCUMENT_BOUNDARY = "[[DOCUMENT_BOUNDARY]]"
OTHER_DATA_COLUMN_PATTERN = re.compile(
    r"(?:(?:稳杀|绝杀|精杀|砍杀|杀码|必杀|杀号|综合杀)"
    r"(?:五码|5码|六码|6码)?|五码|5码|六码|6码|三肖|一肖|六肖|波色|尾数)"
)


def issue_boundary_pattern() -> str:
    return "|".join(
        re.escape(boundary)
        for boundary in (USER_RECORD_BOUNDARY, DOCUMENT_BOUNDARY)
    )

def remove_fragment(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def origin(url: str) -> str:
    parsed = urlparse(remove_fragment(url))
    return f"{parsed.scheme}://{parsed.netloc}"


def normalize_issue(issue: str) -> str:
    return str(int(str(issue).strip().replace("期", "")))


def parse_issues(raw: str) -> list[str]:
    issues = []
    for part in re.split(r"[,，\s]+", raw.strip()):
        if part:
            issues.append(normalize_issue(part))
    return issues


def fullwidth_to_halfwidth(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    table = str.maketrans(
        "０１２３４５６７８９（）【】［］，．：　",
        "0123456789()[][],.: ",
    )
    return text.translate(table)


def html_to_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|tr|td|h[1-6])\s*>", "\n", value)
    value = re.sub(r"(?is)<script.*?</script>", "\n", value)
    value = re.sub(r"(?is)<style.*?</style>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)
    value = fullwidth_to_halfwidth(value)
    value = value.replace("\r", "\n")
    value = re.sub(r"[ \t\xa0]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{2,}", "\n", value)
    return value.strip()


def unique_keep_order(items: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def valid_number(token: str) -> bool:
    try:
        number = int(token)
    except ValueError:
        return False
    return 1 <= number <= 49


def find_number_groups(segment: str) -> list[list[str]]:
    before_open = re.split(r"[=＝]?\s*开\s*[:：]?", segment, maxsplit=1)[0]
    before_open = before_open.replace("杀", " ")

    def collect(source: str) -> list[list[str]]:
        found = []
        # 每个 match 都是一组独立号码。禁止把多组、开奖号、日期或其他栏目拼接来凑 count。
        # 支持“16-20”这种 5 连号范围，只在刚好展开为 5 个合法号码时采用。
        # 如果它是“11-15-19-34-12”这种横线 5 码组的一部分，不能把局部误当范围。
        for match in re.finditer(r"(?<!\d)(0?[1-9]|[1-4]\d)\s*-\s*(0?[1-9]|[1-4]\d)(?!\d)", source):
            if re.match(r"\s*-\s*\d{2}", source[match.end():]):
                continue
            if re.search(r"\d{2}\s*-\s*$", source[:match.start()]):
                continue
            start = int(match.group(1))
            end = int(match.group(2))
            if end - start == 4 and valid_number(str(start)) and valid_number(str(end)):
                found.append([f"{number:02d}" for number in range(start, end + 1)])

        # 支持“绝杀5码0129491942”这种紧凑 10 位格式，只拆成 5 个两位数。
        for match in re.finditer(r"(?:5\s*码|五码)\D*([0-4]\d(?:[0-4]\d){4})(?!\d)", source):
            raw = match.group(1)
            nums = [raw[index:index + 2] for index in range(0, 10, 2)]
            if all(valid_number(num) for num in nums):
                found.append(nums)

        # 支持“20-28-15-18-14”这种横线分隔的 5 码组。
        for match in re.finditer(r"(?<!\d)\d{2}(?:\s*-\s*\d{2}){4,}(?!\d)", source):
            nums = re.findall(r"\d{2}", match.group(0))
            if nums and all(valid_number(num) for num in nums) and len(nums) >= 5:
                found.append(nums)

        # 优先抓一串 2 位数，分隔符可以是点、空格、逗号、顿号等。
        # 分隔符必须存在，避免把日期 2020-10-02 19:41:06 当成号码。
        for match in re.finditer(
            r"(?<!\d)\d{2}(?:[\s.,，。、;；|/\\]+\d{2}){2,}",
            source,
        ):
            nums = re.findall(r"\d{2}", match.group(0))
            if nums and all(valid_number(num) for num in nums) and len(nums) >= 3:
                found.append(nums)
        return found

    groups = collect(before_open)
    if not groups:
        # 有些页面把“开:00准”放在第一行，号码放到下一行。
        groups = collect(segment.replace("杀", " "))

    groups.sort(key=len, reverse=True)
    return groups


def keyword_scoped_number_groups(
    segment: str,
    keywords: list[str] | None,
) -> list[list[str]]:
    keyword_list = [str(keyword) for keyword in (keywords or []) if str(keyword).strip()]
    if not keyword_list:
        return find_number_groups(segment)

    lines = segment.splitlines(keepends=True) or [segment]
    keyword_lines = [
        index
        for index, line in enumerate(lines)
        if any_keyword_matches(line, keyword_list)
    ]
    if not keyword_lines:
        return []

    groups: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    keyword_line_set = set(keyword_lines)

    def belongs_to_keyword_scope(scope: str, group: list[str]) -> bool:
        compact_scope = normalize_keyword(scope)
        group_token = "".join(group)
        group_start = compact_scope.find(group_token)
        if group_start < 0:
            return True

        normalized_keywords = [
            normalize_keyword(keyword)
            for keyword in keyword_list
            if normalize_keyword(keyword)
        ]
        keyword_ends = []
        for keyword in normalized_keywords:
            start = compact_scope.find(keyword)
            while 0 <= start < group_start:
                keyword_ends.append(start + len(keyword))
                start = compact_scope.find(keyword, start + 1)
        if keyword_ends:
            keyword_end = max(keyword_ends)
            between = compact_scope[keyword_end:group_start]
            between = re.sub(r"^(?:五码|5码|六码|6码)", "", between, count=1)
            return OTHER_DATA_COLUMN_PATTERN.search(between) is None

        literal_positions = [
            scope.rfind(keyword, 0, max(0, scope.find(group[0])))
            for keyword in keyword_list
            if not normalize_keyword(keyword)
        ]
        literal_positions = [position for position in literal_positions if position >= 0]
        if not literal_positions:
            return False
        literal_start = max(literal_positions)
        tail = normalize_keyword(scope[literal_start:])
        tail_group_start = tail.find(group_token)
        if tail_group_start < 0:
            return True
        return OTHER_DATA_COLUMN_PATTERN.search(tail[:tail_group_start]) is None

    for line_index in keyword_lines:
        scoped_lines = [lines[line_index]]
        for next_index in range(line_index + 1, len(lines)):
            line = lines[next_index]
            compact = normalize_keyword(line)
            if next_index in keyword_line_set:
                break
            if DOCUMENT_BOUNDARY in line or USER_RECORD_BOUNDARY in line:
                break
            if OTHER_DATA_COLUMN_PATTERN.search(compact):
                break
            scoped_lines.append(line)

        scope = "".join(scoped_lines)
        for group in find_number_groups(scope):
            if not belongs_to_keyword_scope(scope, group):
                continue
            key = tuple(group)
            if key not in seen:
                seen.add(key)
                groups.append(group)
    return groups


def has_duplicate_numbers(numbers: list[str]) -> bool:
    return len(numbers) != len(set(numbers))


def has_pending_open_marker(segment: str) -> bool:
    return bool(re.search(r"开\s*[:：]?\s*(?:[?？]{1,2}|0{2,4})", segment))


def select_candidate(
    candidates: list[tuple[list[str], str]],
    position: str = "first",
    strict_ambiguous: bool = False,
    issue: str = "",
    allow_duplicate_numbers: bool = False,
) -> list[str] | None:
    if allow_duplicate_numbers:
        valid_candidates = candidates
    else:
        valid_candidates = [
            (numbers, segment)
            for numbers, segment in candidates
            if not has_duplicate_numbers(numbers)
        ]
    if not valid_candidates:
        return None

    distinct: list[tuple[list[str], str]] = []
    seen = set()
    for numbers, segment in valid_candidates:
        key = tuple(numbers)
        if key not in seen:
            seen.add(key)
            distinct.append((numbers, segment))

    if len(distinct) == 1:
        return distinct[0][0]

    if strict_ambiguous:
        preview = " | ".join(",".join(numbers) for numbers, _ in distinct[:5])
        issue_text = f"{issue}期" if issue else "该期"
        raise CrawlError(
            ErrorCode.CANDIDATE_CONFLICT,
            f"{issue_text} 候选不唯一，已停止输出避免抓错：{preview}",
            stage="candidate_selection",
        )

    pending = [
        (numbers, segment)
        for numbers, segment in distinct
        if has_pending_open_marker(segment)
    ]
    if len(pending) == 1:
        return pending[0][0]

    return distinct[-1][0] if position == "last" else distinct[0][0]


def issue_segment_matches(text: str, issue: str) -> list[re.Match]:
    issue = normalize_issue(issue)
    pattern = re.compile(
        rf"(?<!\d)0?{re.escape(issue)}\s*期(?P<body>.*?)(?=(?<!\d)\d{{3}}\s*期|{issue_boundary_pattern()}|$)",
        re.S,
    )
    return list(pattern.finditer(text))


def issue_segments(text: str, issue: str) -> list[str]:
    return [m.group(0) for m in issue_segment_matches(text, issue)]


def all_issue_segment_matches(text: str) -> list[re.Match]:
    pattern = re.compile(
        rf"(?<!\d)0?(\d{{3}})\s*期(?P<body>.*?)(?=(?<!\d)\d{{3}}\s*期|{issue_boundary_pattern()}|$)",
        re.S,
    )
    return list(pattern.finditer(text))


def normalize_keyword(keyword: str) -> str:
    keyword = fullwidth_to_halfwidth(keyword or "")
    keyword = keyword.translate(
        str.maketrans(
            {
                "碼": "码",
                "殺": "杀",
                "絕": "绝",
                "穩": "稳",
                "準": "准",
                "開": "开",
                "零": "0",
                "〇": "0",
                "一": "1",
                "二": "2",
                "两": "2",
                "三": "3",
                "四": "4",
                "五": "5",
                "六": "6",
                "七": "7",
                "八": "8",
                "九": "9",
                "十": "10",
                "①": "1",
                "②": "2",
                "③": "3",
                "④": "4",
                "⑤": "5",
                "⑥": "6",
                "⑦": "7",
                "⑧": "8",
                "⑨": "9",
                "⑩": "10",
            }
        )
    )
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", keyword)


def keyword_matches(text: str, keyword: str) -> bool:
    normalized_keyword = normalize_keyword(keyword)
    if normalized_keyword:
        return normalized_keyword in normalize_keyword(text)
    literal = str(keyword or "").strip()
    return bool(literal and literal in text)


def any_keyword_matches(text: str, keywords: list[str] | None) -> bool:
    values = [str(keyword) for keyword in (keywords or []) if str(keyword).strip()]
    return not values or any(keyword_matches(text, keyword) for keyword in values)


def as_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if item]


def find_anchor_index(text: str, anchor: str, start: int = 0) -> int:
    direct = text.find(anchor, start)
    if direct >= 0:
        return direct

    normalized_anchor = normalize_keyword(anchor)
    if not normalized_anchor:
        return -1

    offset = 0
    for line in text.splitlines(keepends=True):
        if offset >= start and normalized_anchor in normalize_keyword(line):
            return offset
        offset += len(line)
    return -1


def scope_text_by_anchor(
    text: str,
    anchors=None,
    stop_anchors=None,
) -> str:
    anchor_list = as_list(anchors)
    if not anchor_list:
        return text

    start_index = -1
    for anchor in anchor_list:
        start_index = find_anchor_index(text, anchor)
        if start_index >= 0:
            break
    if start_index < 0:
        raise CrawlError(
            ErrorCode.ANCHOR_MISSING,
            f"没有找到正文锚点：{anchor_list}",
            stage="document_scope",
        )

    end_index = len(text)
    for stop_anchor in as_list(stop_anchors):
        candidate = find_anchor_index(text, stop_anchor, start=start_index + 1)
        if candidate >= 0:
            end_index = min(end_index, candidate)

    return text[start_index:end_index]


def normalize_region(region: str | None) -> str:
    value = normalize_keyword(region or "").lower()
    if value in {"top", "upper", "head", "first", "上", "顶部"}:
        return "top"
    if value in {"bottom", "lower", "tail", "last", "下", "尾部", "底部"}:
        return "bottom"
    return ""


def candidate_region(position: int, text_length: int) -> str:
    if text_length <= 0:
        return ""
    return "top" if position < text_length / 2 else "bottom"


def filter_candidates_by_region(
    candidates: list[tuple[list[str], str, int]],
    region: str | None,
    text_length: int | None = None,
    strict_window: bool = False,
    require_region: bool = False,
):
    region = normalize_region(region)
    if require_region and not region:
        return []
    if not region:
        return candidates

    ordered = sorted(candidates, key=lambda item: item[2])
    if text_length is not None:
        ordered = [
            item for item in ordered
            if candidate_region(item[2], text_length) == region
        ]
    if len(ordered) <= 1:
        return ordered

    if strict_window:
        if region == "top":
            return ordered[:CANDIDATE_REGION_WINDOW]
        return ordered[-CANDIDATE_REGION_WINDOW:]

    middle = (len(ordered) + 1) // 2
    if region == "top":
        selected = ordered[:middle]
    else:
        selected = ordered[middle:]

    return selected


def needs_strict_region_window(candidates: list[tuple[list[str], str, int]]) -> bool:
    # 同一期候选过多或出现多条同期资料时，只允许在配置方向的最新 3 组内取数。
    return len(candidates) > CANDIDATE_STRICT_THRESHOLD or len(candidates) > 1


def scope_first_issue_chain(
    text: str,
    issues: list[str],
    keywords: list[str] | None,
    expected_count: int | None,
    region: str | None = None,
) -> str:
    wanted = {normalize_issue(issue) for issue in issues}
    normalized_region = normalize_region(region)
    current_start = None
    current_end = None
    current_issues: set[str] = set()
    seen_issues: set[str] = set()
    first_match_start = None
    first_match_end = None
    last_match_start = None
    last_match_end = None

    def complete_group() -> str | None:
        nonlocal first_match_start, first_match_end, last_match_start, last_match_end
        if current_start is None or current_end is None:
            return None
        if wanted.issubset(current_issues):
            if first_match_start is None or current_start < first_match_start:
                first_match_start = current_start
                first_match_end = current_end
            last_match_start = current_start
            last_match_end = current_end
            if normalized_region != "bottom":
                return text[current_start:current_end]
        return None

    for match in all_issue_segment_matches(text):
        issue = normalize_issue(match.group(1))
        segment = match.group(0)
        if not any_keyword_matches(segment, keywords):
            continue
        if expected_count:
            groups = [
                group
                for group in keyword_scoped_number_groups(segment, keywords)
                if len(group) == expected_count
            ]
            if not groups:
                continue

        if current_start is None:
            current_start = match.start()
        elif issue in seen_issues:
            scoped = complete_group()
            if scoped is not None:
                return scoped
            current_start = match.start()
            current_issues = set()
            seen_issues = set()

        current_end = match.end()
        current_issues.add(issue)
        seen_issues.add(issue)

    scoped = complete_group()
    if scoped is not None:
        return scoped
    if normalized_region == "bottom" and last_match_start is not None and last_match_end is not None:
        return text[last_match_start:last_match_end]
    if first_match_start is not None and first_match_end is not None:
        return text[first_match_start:first_match_end]
    return text


def _keyword_prefix_before_issue(
    text: str,
    issue_start: int,
    keywords: list[str] | None,
    lookback: int,
) -> str:
    keyword_list = [str(keyword) for keyword in (keywords or []) if str(keyword).strip()]
    if not keyword_list or lookback <= 0:
        return ""

    prefix_start = max(0, issue_start - lookback)
    line_start = text.rfind("\n", prefix_start, issue_start)
    same_line_start = line_start + 1 if line_start >= 0 else prefix_start
    same_line_prefix = text[same_line_start:issue_start]
    if any_keyword_matches(same_line_prefix, keyword_list):
        return same_line_prefix
    if same_line_prefix.strip() or line_start < 0:
        return ""

    previous_lines = text[prefix_start:line_start].splitlines()
    if not previous_lines:
        return ""
    previous_line = previous_lines[-1].strip()
    normalized_keywords = {
        normalize_keyword(keyword)
        for keyword in keyword_list
        if normalize_keyword(keyword)
    }
    if normalize_keyword(previous_line) not in normalized_keywords:
        return ""
    return previous_line + "\n"


def issue_position_window_starts(
    text: str,
    keywords: list[str] | None,
    expected_count: int | None,
    region: str | None,
    issue_position_window: int | None,
    keyword_before_issue: bool = False,
    keyword_before_issue_window: int | None = None,
) -> set[int] | None:
    if not issue_position_window:
        return None

    candidates: list[tuple[str, int]] = []
    for match in all_issue_segment_matches(text):
        segment = match.group(0)
        groups = (
            keyword_scoped_number_groups(segment, keywords)
            if any_keyword_matches(segment, keywords)
            else []
        )
        if keyword_before_issue and not any(
            not expected_count or len(group) == expected_count
            for group in groups
        ):
            prefix = _keyword_prefix_before_issue(
                text,
                match.start(),
                keywords,
                int(keyword_before_issue_window or 160),
            )
            if prefix:
                groups = find_number_groups(prefix + segment)

        for group in groups:
            if expected_count and len(group) != expected_count:
                continue
            candidates.append((segment, match.start()))
            break

    region = normalize_region(region)
    ordered = sorted(candidates, key=lambda item: item[1])
    if region == "bottom":
        selected = ordered[-issue_position_window:]
    else:
        selected = ordered[:issue_position_window]
    return {start for _segment, start in selected}


def keyword_before_issue_candidates(
    text: str,
    issue: str,
    keywords: list[str] | None,
    expected_count: int | None,
    lookback: int,
    allowed_window_starts: set[int] | None = None,
) -> list[tuple[list[str], str, int]]:
    candidates: list[tuple[list[str], str, int]] = []
    for match in issue_segment_matches(text, issue):
        if allowed_window_starts is not None and match.start() not in allowed_window_starts:
            continue
        prefix = _keyword_prefix_before_issue(
            text,
            match.start(),
            keywords,
            lookback,
        )
        if not prefix:
            continue

        segment = prefix + match.group(0)
        for group in find_number_groups(segment):
            if expected_count and len(group) != expected_count:
                continue
            candidates.append((group, segment, match.start()))
    return candidates


def clean_name(name: str) -> str:
    name = html_to_text(name)
    name = re.sub(r"^(作者|楼主|昵称)\s*[:：]\s*", "", name)
    name = re.sub(r"\s+", "", name)
    name = re.sub(r"(已更新|公开)$", "", name)
    return name[:20] or "未命名"


def preserve_configured_name(name: str) -> str:
    return str(name or "").strip() or "未命名"


def extract_name_from_text(text: str, fallback: str = "未命名") -> str:
    clean = html_to_text(text)

    author_patterns = [
        r"作者\s*[:：]\s*([^\n<]+)",
        r"楼主\s*[:：]\s*([^\n<]+)",
        r"发帖人\s*[:：]\s*([^\n<]+)",
    ]
    for pattern in author_patterns:
        match = re.search(pattern, clean)
        if match:
            return clean_name(match.group(1))

    bracket_patterns = [
        r"\d{3}\s*期\s*[:：]?\s*[【\[]([^】\]]{2,20})[】\]]",
        r"\d{3}\s*期\s*[:：]?\s*[《〈]([^》〉]{2,20})[》〉]",
        r"\d{3}\s*期\s*[:：]?\s*[『「]([^』」]{2,20})[』」]",
    ]
    bad_words = ("杀", "码", "稳", "绝", "期", "开", "公开")
    for pattern in bracket_patterns:
        for match in re.finditer(pattern, clean):
            name = clean_name(match.group(1))
            if name and not any(word in name for word in bad_words):
                return name

    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if title:
        title_name = clean_name(title.group(1))
        if title_name and title_name not in {"17图库", "未命名"}:
            return title_name

    return clean_name(fallback)


def decode_strdecode_payloads(value: str) -> list[str]:
    decoded = []
    patterns = [
        r"strdecode\([\"']([^\"']+)[\"']\)",
        r"decodeB64\([\"']([^\"']+)[\"']\)",
        r"__PAGE_DATA__\s*=\s*[\"']([^\"']+)[\"']",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, value):
            payload = match.group(1)
            try:
                decoded.append(base64.b64decode(payload).decode("utf-8", errors="ignore"))
            except Exception:
                continue
    return decoded


def select_decoded_anchor_parts(
    decoded: list[str],
    anchor: str,
    chunk_count: int,
    stop_anchor: str | None = None,
    to_end: bool = False,
) -> list[str]:
    selected: list[str] = []
    for index, part in enumerate(decoded):
        if find_anchor_index(part, anchor) < 0:
            continue
        if to_end:
            selected.extend(decoded[index:])
            continue
        if not stop_anchor:
            selected.extend(decoded[index:index + chunk_count])
            continue

        end_index = next(
            (
                candidate
                for candidate in range(index, len(decoded))
                if find_anchor_index(decoded[candidate], stop_anchor) >= 0
            ),
            None,
        )
        if end_index is None:
            raise CrawlError(
                ErrorCode.DOCUMENT_BOUNDARY_ERROR,
                f"找到专属解码正文锚点 {anchor!r}，但没有找到解码正文结束锚点：{stop_anchor!r}",
                stage="decoded_document_scope",
            )
        selected.extend(decoded[index:end_index + 1])
    return unique_keep_order(selected)


__all__ = [
    'CANDIDATE_STRICT_THRESHOLD',
    'CANDIDATE_REGION_WINDOW',
    'USER_RECORD_BOUNDARY',
    'DOCUMENT_BOUNDARY',
    'remove_fragment',
    'origin',
    'normalize_issue',
    'parse_issues',
    'fullwidth_to_halfwidth',
    'html_to_text',
    'unique_keep_order',
    'valid_number',
    'find_number_groups',
    'keyword_scoped_number_groups',
    'has_duplicate_numbers',
    'has_pending_open_marker',
    'select_candidate',
    'issue_segment_matches',
    'issue_segments',
    'all_issue_segment_matches',
    'normalize_keyword',
    'keyword_matches',
    'any_keyword_matches',
    'as_list',
    'find_anchor_index',
    'scope_text_by_anchor',
    'normalize_region',
    'candidate_region',
    'filter_candidates_by_region',
    'needs_strict_region_window',
    'scope_first_issue_chain',
    'issue_position_window_starts',
    'keyword_before_issue_candidates',
    'clean_name',
    'preserve_configured_name',
    'extract_name_from_text',
    'decode_strdecode_payloads',
    'select_decoded_anchor_parts',
]
