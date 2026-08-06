from __future__ import annotations

import argparse
from pathlib import Path
import time

from kill5.config import load_targets
from kill5.onboarding import check_candidate, write_onboarding_report


SCRIPT_DIR = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="第一层新站准入检查；不写正式配置、正式输出或正式缓存"
    )
    parser.add_argument("--candidate-json", required=True)
    args = parser.parse_args()

    candidate_path = Path(args.candidate_json)
    if not candidate_path.is_absolute():
        candidate_path = SCRIPT_DIR / candidate_path
    try:
        candidates = load_targets(candidate_path, allow_legacy=True)
        production_targets = load_targets(SCRIPT_DIR / "targets.json")
    except Exception as exc:
        print(f"候选配置加载失败：{exc}")
        return 2

    results = [
        check_candidate(
            candidate,
            production_targets,
            cache_path=SCRIPT_DIR / "recent_10_cache.json",
            debug_dir=SCRIPT_DIR / "staging" / "onboarding_debug",
        )
        for candidate in candidates
    ]
    report_path = (
        SCRIPT_DIR
        / "staging"
        / "onboarding"
        / f"准入报告_{time.strftime('%Y%m%d-%H%M%S')}.json"
    )
    write_onboarding_report(results, report_path)
    for result in results:
        print(f"[{result.status}] {result.name}：{result.reason}")
    print(f"准入报告：{report_path}")
    print("本入口未写正式 targets.json、正式输出或正式缓存。")
    return 0 if all(result.status == "accepted" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
