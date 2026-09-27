from __future__ import annotations

import unittest
from unittest.mock import patch

from kill5.adapters.list_detail import MAX_LIST_PAGES, crawl_list_detail_page

LIST_URL = "https://example.test/index.php?fid=3"


def fake_page(page_number: int, extra_links: list[tuple[str, str]] = ()) -> str:
    links = "".join(f'<a href="{href}">{text}</a>' for href, text in extra_links)
    return (
        "<html><body>"
        f"{links}"
        f'<a href="/index.php?fid=3&page={page_number + 1}">下一页</a>'
        "</body></html>"
    )


class ListDetailPageLimitTests(unittest.TestCase):
    def test_missing_issue_stops_at_page_limit(self) -> None:
        fetched: list[str] = []

        def fake_fetch(url: str) -> str:
            fetched.append(url)
            page_number = int(url.rsplit("page=", 1)[1]) if "page=" in url else 1
            # 侧栏每页都挂着上期链接，但整站永远没有 270 期
            return fake_page(page_number, [("/read.php?tid=1", "269期上错统计")])

        with patch("kill5.adapters.list_detail.fetch_text", side_effect=fake_fetch):
            _name, content = crawl_list_detail_page(
                LIST_URL, ["270"], title_keywords=["上错统计"]
            )

        self.assertEqual(len(fetched), MAX_LIST_PAGES)
        self.assertFalse([url for url in fetched if "page=6" in url])
        self.assertEqual(content.strip(), "")

    def test_issue_found_within_limit_stops_early(self) -> None:
        fetched: list[str] = []

        def fake_fetch(url: str) -> str:
            fetched.append(url)
            if "page=" not in url:
                return fake_page(1, [("/read.php?tid=1", "269期上错统计")])
            return fake_page(2, [("/read.php?tid=9", "270期上错统计")])

        with (
            patch("kill5.adapters.list_detail.fetch_text", side_effect=fake_fetch),
            patch(
                "kill5.adapters.list_detail.crawl_static_page",
                return_value=("上错统计", "270期 上错杀五码统计 详情"),
            ),
        ):
            _name, content = crawl_list_detail_page(
                LIST_URL, ["270"], title_keywords=["上错统计"]
            )

        self.assertEqual(len(fetched), 2)
        self.assertIn("详情", content)


if __name__ == "__main__":
    unittest.main()
