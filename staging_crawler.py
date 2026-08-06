from __future__ import annotations

import argparse
from pathlib import Path
import sys

from kill5.config import load_targets
from kill5.parser import parse_issues, preserve_configured_name
from kill5.services import (
    StagingRunService,
    cache_update_allowed,
    load_staging_candidates,
    parse_target_names,
    select_existing_targets,
)


DEFAULT_WORKERS = 8
DEFAULT_RETRY_PASSES = 2

SCRIPT_DIR = Path(__file__).resolve().parent
TARGETS_FILE = SCRIPT_DIR / "targets.json"
STAGING_DIR = SCRIPT_DIR / "staging"
DEBUG_DIR = STAGING_DIR / "debug_pages"
RESULTS_DIR = STAGING_DIR / "results"
CACHE_FILE = STAGING_DIR / "recent_10_cache.json"
PRODUCTION_CACHE_FILE = SCRIPT_DIR / "recent_10_cache.json"
TARGETS = load_targets(TARGETS_FILE)


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    configure_output_encoding()
    parser = argparse.ArgumentParser(
        description="第二层隔离测试：单独抓取一个或多个杀五码目录"
    )
    parser.add_argument(
        "--issues",
        required=True,
        help="指定期数，多个用逗号分隔，例如：119 或 119,120",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--names",
        nargs="+",
        help="targets.json 中的目录名；多个可用空格或逗号分隔",
    )
    source.add_argument(
        "--candidate-json",
        help="第一层检测通过但尚未加入 targets.json 的候选配置 JSON",
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--retry-passes", type=int, default=DEFAULT_RETRY_PASSES)
    args = parser.parse_args()

    issues = parse_issues(args.issues)
    if not issues:
        print("请指定期数，例如：python staging_crawler.py --issues 119 --names 牛奶咖啡")
        return 2

    try:
        if args.names:
            targets = select_existing_targets(
                TARGETS,
                parse_target_names(args.names),
            )
            source_text = "正式配置目录"
        else:
            candidate_path = Path(args.candidate_json)
            if not candidate_path.is_absolute():
                candidate_path = SCRIPT_DIR / candidate_path
            targets = load_staging_candidates(candidate_path, TARGETS)
            source_text = "第一层候选 JSON"
    except Exception as exc:
        print(f"目录筛选失败：{exc}")
        return 2

    names = [preserve_configured_name(item.get("name") or "") for item in targets]
    print("第二层隔离测试模式：不会写正式输出和正式 recent_10_cache.json")
    print(f"目标来源：{source_text}")
    print(f"测试目录（{len(targets)}）：{', '.join(names)}")
    max_workers = max(1, min(args.workers, len(targets)))
    print(f"并发线程数：{max_workers}")

    execution = StagingRunService(
        results_dir=RESULTS_DIR,
        debug_dir=DEBUG_DIR,
        cache_path=CACHE_FILE,
        production_cache_path=PRODUCTION_CACHE_FILE,
    ).run(
        targets,
        issues,
        workers=max_workers,
        retry_passes=max(0, args.retry_passes),
    )
    outcome = execution.outcome
    if outcome.failures and not cache_update_allowed(outcome):
        successful_targets = {
            str(result.target_id).strip() or result.url
            for result in outcome.results
        }
        total_targets = len(outcome.targets)
        print(
            f"\n成功目录 {len(successful_targets)}/{total_targets}，"
            "成功率不超过 85%，隔离缓存未更新。"
        )
    elif outcome.failures and not execution.cache_updated:
        print("\n成功率已超过 85%，但隔离缓存更新未完成；失败目录状态未影响本轮 TXT。")
    elif outcome.failures:
        print("\n成功率已超过 85%，隔离缓存已更新，失败目录已写入 failures 标记。")
    if execution.cache_error:
        print(f"\n{execution.cache_error}")

    print(f"\n完成：成功 {len(outcome.results)} 条，失败 {len(outcome.failures)} 条")
    print(f"成功结果：{execution.result_file}")
    if outcome.failures:
        print(f"失败记录：{execution.failed_file}")
    else:
        print("失败记录：无失败，不生成失败文件")
    print(f"抓取报告：{execution.report_file}")
    print(f"隔离缓存：{CACHE_FILE}")
    print("正式 crawler.py、正式输出和正式 recent_10_cache.json 均未修改。")
    return 1 if outcome.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
