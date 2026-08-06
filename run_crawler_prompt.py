import subprocess
import sys
from pathlib import Path

from crawler import DEFAULT_ISSUES
from kill5.output import output_files_for_issues
from kill5.parser import parse_issues


BASE_DIR = Path(__file__).resolve().parent
CRAWLER_FILE = BASE_DIR / "crawler.py"
CACHE_FILE = "recent_10_cache.json"
CRAWLER_WORKERS = 16


def latest_issue_from_input(raw_issues: str, default_issues: str) -> str:
    issues = parse_issues(raw_issues or default_issues)
    if not issues:
        raise ValueError("没有可用期数")
    return issues[-1]


def crawler_command_for_input(raw_issues: str) -> list[str]:
    cmd = [sys.executable, str(CRAWLER_FILE), "--workers", str(CRAWLER_WORKERS)]
    if raw_issues:
        cmd.extend(["--issues", raw_issues])
    return cmd


def validate_result_file_exists(result_file: Path) -> None:
    if not result_file.exists():
        raise ValueError(f"成功文件不存在，已停止：{result_file}")
    if not result_file.read_text(encoding="utf-8", errors="ignore").strip():
        raise ValueError(f"成功文件为空，已停止：{result_file}")


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    configure_output_encoding()
    print("请输入要爬取的期数，多个期数用逗号分隔，例如：124 或 123,124")
    print("直接回车则使用 crawler.py 默认期数。")
    issues = input("期数：").strip()
    crawl_issues = parse_issues(issues or DEFAULT_ISSUES)
    latest_issue = latest_issue_from_input(issues, DEFAULT_ISSUES)

    cmd = crawler_command_for_input(issues)

    print()
    print("正在启动...")
    result = subprocess.run(cmd, cwd=BASE_DIR)
    if result.returncode != 0:
        print()
        print("抓取存在失败目录；缓存是否更新由 crawler.py 按单期成功率阈值决定。")
        return result.returncode

    result_file = Path(output_files_for_issues(crawl_issues)[0])
    try:
        validate_result_file_exists(result_file)
    except Exception as exc:
        print()
        print(str(exc))
        print("已跳过 recent_10_cache.json 同步。")
        return 1

    print()
    print(
        f"{CACHE_FILE} 是否同步由 crawler.py 按单期成功目录是否严格超过85%决定；"
        f"目标期数：{latest_issue}。"
    )

    print()
    print("运行结束，请查看 N期-杀五码-成功.txt；如有失败，再查看 N期-杀五码-失败.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
