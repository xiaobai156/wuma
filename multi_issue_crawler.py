from __future__ import annotations

import argparse
from pathlib import Path
import sys

from kill5.config import load_targets
from kill5.multi import run_multi_issues
from kill5.parser import parse_issues


SCRIPT_DIR = Path(__file__).resolve().parent
TARGETS = load_targets(SCRIPT_DIR / "targets.json")
RESULTS_DIR = Path(
    r"C:\Users\Administrator\Desktop\每天工具\爬虫合集\大围杀号生肖数据统一归纳"
)


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    configure_output_encoding()
    parser = argparse.ArgumentParser(
        description="多期逐期抓取杀五码，不更新 recent_10_cache.json"
    )
    parser.add_argument("--issues", nargs="+")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--retry-passes", type=int, default=2)
    args = parser.parse_args()

    raw = " ".join(args.issues or [])
    if not raw:
        print("请输入要爬取的多个期数，例如：187 188 189 190")
        raw = input("期数：").strip()
    issues = parse_issues(raw)
    if not issues:
        print("没有可用期数，已停止。")
        return 2

    print(f"多期模式：{', '.join(issue + '期' for issue in issues)}")
    print("本模式不会更新 recent_10_cache.json。")
    execution = run_multi_issues(
        TARGETS,
        issues,
        workers=max(1, min(args.workers, len(TARGETS))),
        retry_passes=max(0, args.retry_passes),
        results_dir=RESULTS_DIR,
        report_dir=RESULTS_DIR,
        debug_dir=SCRIPT_DIR / "debug_pages",
    )
    print("\n===== 多期模式完成 =====")
    print(f"目标总数：{len(TARGETS)}")
    print(f"任意一期成功目录：{execution.passed_targets}")
    print(f"全部失败目录：{execution.failed_targets}")
    print(f"多期汇总失败报告：{execution.report_path}")
    print("recent_10_cache.json 未更新。")
    return 1 if execution.failed_targets else 0


if __name__ == "__main__":
    raise SystemExit(main())
