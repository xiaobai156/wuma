from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kill5.config import load_targets  # noqa: E402
from kill5.engine import crawl_one  # noqa: E402
from kill5.errors import CrawlError  # noqa: E402
from kill5 import validator  # noqa: E402
from kill5.parser import (  # noqa: E402
    DOCUMENT_BOUNDARY,
    all_issue_segment_matches,
    any_keyword_matches,
    find_number_groups,
    has_duplicate_numbers,
    html_to_text,
    keyword_scoped_number_groups,
    normalize_issue,
    normalize_keyword,
    normalize_region,
    scope_text_by_anchor,
)


def _keyword_marker_on_previous_line(
    text: str,
    issue_start: int,
    keywords: list[str] | None,
    lookback: int,
) -> str:
    prefix_start = max(0, issue_start - lookback)
    prefix = text[prefix_start:issue_start]
    last_newline = prefix.rfind("\n")
    if last_newline < 0 or prefix[last_newline + 1 :].strip():
        return ""

    previous = prefix[:last_newline].splitlines()[-1].strip()
    normalized_previous = normalize_keyword(previous)
    normalized_keywords = {
        normalize_keyword(keyword)
        for keyword in keywords or []
        if normalize_keyword(keyword)
    }
    if normalized_previous not in normalized_keywords:
        return ""
    return previous + "\n"


def experimental_keyword_before_issue_candidates(
    text: str,
    issue: str,
    keywords: list[str] | None,
    expected_count: int | None,
    lookback: int,
    allowed_window_starts: set[int] | None = None,
) -> list[tuple[list[str], str, int]]:
    candidates: list[tuple[list[str], str, int]] = []
    for match in validator.issue_segment_matches(text, issue):
        if allowed_window_starts is not None and match.start() not in allowed_window_starts:
            continue

        prefix_start = max(0, match.start() - lookback)
        line_start = text.rfind("\n", prefix_start, match.start())
        if line_start >= 0:
            prefix_start = line_start + 1
        prefix = text[prefix_start : match.start()]
        if not any_keyword_matches(prefix, keywords):
            prefix = _keyword_marker_on_previous_line(
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
            if has_duplicate_numbers(group):
                continue
            candidates.append((group, segment, match.start()))
    return candidates


def experimental_issue_position_window_starts(
    text: str,
    keywords: list[str] | None,
    expected_count: int | None,
    region: str | None,
    issue_position_window: int | None,
) -> set[int] | None:
    if not issue_position_window:
        return None

    candidates: dict[int, str] = {}
    issue_matches = all_issue_segment_matches(text)
    for match in issue_matches:
        segment = match.group(0)
        groups = keyword_scoped_number_groups(segment, keywords)
        if any(
            (not expected_count or len(group) == expected_count)
            and not has_duplicate_numbers(group)
            for group in groups
        ):
            candidates[match.start()] = normalize_issue(match.group(1))

    for issue in {normalize_issue(match.group(1)) for match in issue_matches}:
        for _group, _segment, start in experimental_keyword_before_issue_candidates(
            text,
            issue,
            keywords,
            expected_count,
            30,
        ):
            candidates[start] = issue

    ordered = sorted(candidates.items())
    if normalize_region(region) == "bottom":
        selected = ordered[-issue_position_window:]
    else:
        selected = ordered[:issue_position_window]
    return {start for start, _issue in selected}


def target_arguments(target: dict) -> dict:
    return {
        "keywords": target["keywords"],
        "expected_count": target["count"],
        "strict_ambiguous": True,
        "anchor": target["anchor"],
        "stop_anchor": target["stop_anchor"],
        "region": target["region"],
        "issue_position_window": target["issue_position_window"],
        "keyword_before_issue": target["keyword_before_issue"],
        "keyword_before_issue_window": target["keyword_before_issue_window"],
    }


def run() -> None:
    target = next(
        item for item in load_targets(ROOT / "targets.json")
        if item["name"] == "凤凰天机"
    )
    debug_file = max(
        (ROOT / "staging" / "debug_pages").glob("215_凤凰天机_*.txt"),
        key=lambda path: path.stat().st_mtime,
    )
    raw = debug_file.read_text(encoding="utf-8", errors="replace")
    raw_html = raw[raw.find("<!DOCTYPE html>") :]
    source_text = html_to_text(raw_html)
    scoped = scope_text_by_anchor(
        source_text,
        target["anchor"],
        target["stop_anchor"],
    )

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                validator,
                "keyword_before_issue_candidates",
                experimental_keyword_before_issue_candidates,
            )
        )
        stack.enter_context(
            patch.object(
                validator,
                "issue_position_window_starts",
                experimental_issue_position_window_starts,
            )
        )

        starts = experimental_issue_position_window_starts(
            scoped,
            target["keywords"],
            target["count"],
            target["region"],
            target["issue_position_window"],
        )
        top_window = [
            normalize_issue(match.group(1))
            for match in all_issue_segment_matches(scoped)
            if starts is not None and match.start() in starts
        ]
        assert top_window == ["215", "214", "213"], top_window

        expected = {
            "215": ["03", "18", "25", "38", "43"],
            "214": ["02", "17", "32", "41", "47"],
            "213": ["29", "38", "42", "46", "48"],
        }
        for issue, numbers in expected.items():
            found = validator.extract_issue_numbers(
                raw_html,
                [issue],
                **target_arguments(target),
            )
            assert found == {issue: numbers}, (issue, found)

        for rejected_issue in ("212", "216", "999"):
            found = validator.extract_issue_numbers(
                raw_html,
                [rejected_issue],
                **target_arguments(target),
            )
            assert found == {}, (rejected_issue, found)

        wrong_column = (
            "绝杀五码\n215期 [03 18 25 38 43]开 ?? 准\n"
            "杀三尾\n215期 [01 02 03 04 05]开 ?? 准"
        )
        assert validator.extract_issue_numbers(
            wrong_column,
            ["215"],
            **target_arguments(target),
        ) == {"215": expected["215"]}

        wrong_field = (
            "绝杀五码\n215期 [03 18 25 38]开 ?? 准\n"
            "214期 [02 17 32 41 47]开 04 准\n杀三尾"
        )
        assert validator.extract_issue_numbers(
            wrong_field,
            ["215"],
            **target_arguments(target),
        ) == {}

        script_noise = (
            "<script>绝杀五码 215期 01 02 03 04 05</script>"
            "绝杀五码\n215期 [03 18 25 38 43]开 ?? 准\n杀三尾"
        )
        assert validator.extract_issue_numbers(
            script_noise,
            ["215"],
            **target_arguments(target),
        ) == {"215": expected["215"]}

        cross_document = (
            "绝杀五码\n215期\n"
            f"{DOCUMENT_BOUNDARY}\n03 18 25 38 43 开 ?? 准\n杀三尾"
        )
        assert validator.extract_issue_numbers(
            cross_document,
            ["215"],
            **target_arguments(target),
        ) == {}

        conflicting = (
            "绝杀五码\n"
            "215期 [03 18 25 38 43]开 ?? 准\n"
            "绝杀五码 215期 [01 02 03 04 05]开 ?? 准\n"
            "绝杀五码 214期 [02 17 32 41 47]开 04 准\n"
            "杀三尾"
        )
        try:
            validator.extract_issue_numbers(
                conflicting,
                ["215"],
                **target_arguments(target),
            )
        except CrawlError as exc:
            assert "候选不唯一" in str(exc), exc
        else:
            raise AssertionError("同一期冲突没有被拒绝")

        live_results, live_failure = crawl_one(
            target,
            ["215"],
            debug_dir=ROOT / "staging" / "debug_pages",
        )
        assert live_failure is None, live_failure
        assert len(live_results) == 1, live_results
        assert live_results[0].issue == "215", live_results[0]
        assert live_results[0].numbers == expected["215"], live_results[0]

    print(f"debug_file={debug_file}")
    print("top_window=215,214,213")
    print("215=03,18,25,38,43")
    print("214=02,17,32,41,47")
    print("213=29,38,42,46,48")
    print("rejected=212(out_of_window),216(absent),999(absent)")
    print("safety=wrong_column,wrong_field,script_noise,cross_document,conflict:PASS")
    print("live_full_flow=凤凰天机,215,top:PASS")


if __name__ == "__main__":
    run()
