from __future__ import annotations

import argparse
from pathlib import Path
import sys

from kill5.config import load_targets
from kill5.parser import parse_issues
from kill5.services import ProductionRunService, cache_update_allowed


DEFAULT_ISSUES = "128"
DEFAULT_WORKERS = 8
DEFAULT_RETRY_PASSES = 2

SCRIPT_DIR = Path(__file__).resolve().parent
TARGETS_FILE = SCRIPT_DIR / "targets.json"
DEBUG_DIR = SCRIPT_DIR / "debug_pages"
RESULTS_DIR = Path(
    r"C:\Users\Administrator\Desktop\每天工具\爬虫合集\大围杀号生肖数据统一归纳"
)
CACHE_FILE = SCRIPT_DIR / "recent_10_cache.json"
TARGETS = load_targets(TARGETS_FILE)


def select_repair_targets(raw_names: str) -> list[dict]:
    names = [name.strip() for name in raw_names.replace("，", ",").split(",") if name.strip()]
    if not names:
        raise ValueError("--repair-names 没有指定修复目录")
    if len(names) != len(set(names)):
        raise ValueError("--repair-names 存在重复目录名")
    matches = [target for target in TARGETS if str(target.get("name") or "").strip() in names]
    matched_names = {str(target.get("name") or "").strip() for target in matches}
    missing = [name for name in names if name not in matched_names]
    if missing:
        raise ValueError(f"targets.json 没有启用修复目录：{','.join(missing)}")
    return matches


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    configure_output_encoding()
    parser = argparse.ArgumentParser(description="按指定期数爬取杀五码并输出 txt")
    parser.add_argument(
        "--issues",
        default=None,
        help="指定期数，多个用逗号分隔，例如：119 或 119,120",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="并发线程数，默认 8；这些站点不稳定，失败多时可手动调低",
    )
    parser.add_argument(
        "--retry-passes",
        type=int,
        default=DEFAULT_RETRY_PASSES,
        help="网络失败后的慢速补抓轮数，默认 2；设为 0 可关闭",
    )
    parser.add_argument(
        "--repair-names",
        default=None,
        help="正式修复目录名，多个用逗号分隔；成功后只追加当期成功 TXT",
    )
    args = parser.parse_args()
    raw_issues = args.issues
    if raw_issues is None:
        print("请输入要爬取的期数，多个期数用逗号分隔，例如：124 或 123,124")
        raw_issues = input(f"期数（直接回车使用 {DEFAULT_ISSUES}）：").strip()
        raw_issues = raw_issues or DEFAULT_ISSUES
    try:
        issues = parse_issues(raw_issues)
    except ValueError as exc:
        print(exc)
        return 2
    if not issues:
        print("请指定期数，例如：python crawler.py --issues 119")
        return 2

    run_targets = TARGETS
    if args.repair_names is not None:
        if len(issues) != 1:
            print("正式修复追加只允许指定一个期数")
            return 2
        try:
            run_targets = select_repair_targets(args.repair_names)
        except ValueError as exc:
            print(exc)
            return 2

    max_workers = max(1, min(args.workers, len(run_targets)))
    print(f"并发线程数：{max_workers}")
    service = ProductionRunService(
        results_dir=RESULTS_DIR,
        report_dir=SCRIPT_DIR,
        debug_dir=DEBUG_DIR,
        cache_path=CACHE_FILE,
    )
    if args.repair_names is None:
        execution = service.run(
            run_targets,
            issues,
            workers=max_workers,
            retry_passes=max(0, args.retry_passes),
        )
    else:
        execution = service.run_repairs(
            run_targets,
            TARGETS,
            issues,
            workers=max_workers,
            retry_passes=max(0, args.retry_passes),
        )
    outcome = execution.outcome

    if execution.preserved_outputs:
        if args.repair_names is None:
            print("\n本地网络权限阻止了全部访问，已保留原输出文件不覆盖。")
        else:
            print("\n修复抓取未全部成功，已保留原成功 TXT、失败 TXT 和缓存不变。")
        if outcome.failures:
            print(f"失败原因：{outcome.failures[0].reason}")
        return 1
    if args.repair_names is not None:
        if execution.cache_error:
            print(f"\n{execution.cache_error}")
        else:
            print("\n修复结果已追加到当期成功 TXT，并已同步当期缓存。")
        print(f"成功结果：{execution.result_file}")
        return 0 if execution.cache_updated else 1
    if outcome.failures and not cache_update_allowed(outcome):
        successful_targets = {
            str(result.target_id).strip() or result.url
            for result in outcome.results
        }
        total_targets = len(outcome.targets)
        print(
            f"\n成功目录 {len(successful_targets)}/{total_targets}，"
            "成功率不超过 85%，recent_10_cache.json 未更新。"
        )
    elif outcome.failures and not execution.cache_updated:
        print("\n成功率已超过 85%，但缓存更新未完成；失败目录状态未影响本轮 TXT。")
    elif outcome.failures:
        print("\n成功率已超过 85%，recent_10_cache.json 已更新，失败目录已写入 failures 标记。")
    if execution.cache_error:
        print(f"\n{execution.cache_error}")

    print(f"\n完成：成功 {len(outcome.results)} 条，失败 {len(outcome.failures)} 条")
    print(f"成功结果：{execution.result_file}")
    if outcome.failures:
        print(f"失败记录：{execution.failed_file}")
    else:
        print("失败记录：无失败，不生成失败文件")
    print(f"抓取报告：{execution.report_file}")
    return 1 if outcome.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
