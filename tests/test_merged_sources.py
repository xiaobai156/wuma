"""merged_sources 合并来源站点的回归测试（不联网）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from kill5.adapters.merged_target import crawl_merged_target
from kill5.errors import CrawlError

TARGET = {
    'id': 'kill5-0249',
    'name': '朱唇玉面老鼠报',
    'url': '',
    'region': 'top',
    'count': 5,
    'source_kind': 'merged_sources',
    'keywords': ['绝杀三码'],
    'merged': [
        {'url': 'https://a.example/topic/1.html', 'keywords': ['绝杀三码'], 'count': 3},
        {'url': 'https://b.example/topic/2.html', 'keywords': ['绝杀②码'], 'count': 2},
    ],
}

CONTENT_A = (
    '300期: 彩霸王【绝杀三码】 朱唇玉面 ！ 作者:朱唇玉面 '
    '300期:绝杀三码【01.35.32】开00准 '
    '299期:绝杀三码【01.23.24】开猴23错 '
)
CONTENT_B = (
    '300期 【绝杀②码】 已更新 温馨提示 '
    '300期【绝杀②码】13.11 开0000准 '
    '299期【绝杀②码】45.34 开猴23准 '
)


def fake_crawl(spec):
    url = spec['url']
    return spec.get('name', ''), CONTENT_A if 'a.example' in url else CONTENT_B


class MergedSourcesTests(unittest.TestCase):
    def test_two_sources_merge_into_five(self):
        with patch('kill5.adapters.merged_target.crawl_merged_source', side_effect=fake_crawl):
            results = crawl_merged_target(TARGET, ['300'])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].numbers, ['01', '35', '32', '13', '11'])
        self.assertEqual(results[0].name, '朱唇玉面老鼠报')

    def test_three_plus_one_fails(self):
        """3+1 不足 5 码必须失败，不能凑数。"""
        content_b_short = '300期【绝杀②码】13 开0000准 '
        def fake(spec):
            if 'a.example' in spec['url']:
                return '', CONTENT_A
            return '', content_b_short
        with patch('kill5.adapters.merged_target.crawl_merged_source', side_effect=fake):
            with self.assertRaises(CrawlError) as ctx:
                crawl_merged_target(TARGET, ['300'])
        self.assertIn('2 个合法号码', str(ctx.exception))

    def test_missing_issue_fails(self):
        content_no_300 = '299期:绝杀三码【01.23.24】开猴23错 '
        def fake(spec):
            if 'a.example' in spec['url']:
                return '', content_no_300
            return '', CONTENT_B
        with patch('kill5.adapters.merged_target.crawl_merged_source', side_effect=fake):
            with self.assertRaises(CrawlError) as ctx:
                crawl_merged_target(TARGET, ['300'])
        self.assertIn('没有 300 期', str(ctx.exception))

    def test_duplicate_numbers_across_sources_fail(self):
        """两段拼起来出现重复号码必须失败。"""
        content_b_dup = '300期【绝杀②码】01.35 开0000准 '
        def fake(spec):
            if 'a.example' in spec['url']:
                return '', CONTENT_A
            return '', content_b_dup
        with patch('kill5.adapters.merged_target.crawl_merged_source', side_effect=fake):
            with self.assertRaises(CrawlError) as ctx:
                crawl_merged_target(TARGET, ['300'])
        self.assertIn('重复号码', str(ctx.exception))

    def test_config_count_mismatch_fails(self):
        bad = dict(TARGET)
        bad['merged'] = [
            {'url': 'https://a.example/1', 'keywords': ['绝杀三码'], 'count': 3},
            {'url': 'https://b.example/2', 'keywords': ['绝杀②码'], 'count': 1},
        ]
        with self.assertRaises(CrawlError) as ctx:
            crawl_merged_target(bad, ['300'])
        self.assertIn('不一致', str(ctx.exception))

    def test_draw_number_not_taken_as_kill(self):
        """只有开奖号没有本期号码时必须失败。"""
        content_a_draw_only = '300期:绝杀三码 开:龙02准 '
        def fake(spec):
            if 'a.example' in spec['url']:
                return '', content_a_draw_only
            return '', CONTENT_B
        with patch('kill5.adapters.merged_target.crawl_merged_source', side_effect=fake):
            with self.assertRaises(CrawlError):
                crawl_merged_target(TARGET, ['300'])


if __name__ == '__main__':
    unittest.main()
