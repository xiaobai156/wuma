"""merged_sources 合并来源站点的回归测试（不联网）。

覆盖审查指出的缺陷：失败证据带 issues、配置校验不读残留 count、
日期不误取、无冒号开奖号不误取、缺期字/四位笔误不借值、
段内别栏目不误取、同期多值冲突、跨来源重复、数量不足。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kill5.adapters.merged_target import crawl_merged_target
from kill5.errors import CrawlError

TARGET = {
    'id': 'kill5-0249',
    'name': '朱唇玉面老鼠报',
    'url': 'https://a.example/topic/1.html',
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
    '300期: 彩霸王【绝杀三码】 朱唇玉面\n'
    '300期:绝杀三码【01.35.32】开00准\n'
    '299期:绝杀三码【01.23.24】开猴23错\n'
)
CONTENT_B = (
    '300期 【绝杀②码】 已更新\n'
    '300期【绝杀②码】13.11 开0000准\n'
    '299期【绝杀②码】45.34 开猴23准\n'
)


def make_fake(content_a=CONTENT_A, content_b=CONTENT_B):
    def fake(spec):
        return spec.get('name', ''), (content_a if 'a.example' in spec['url'] else content_b)
    return fake


class MergedSourcesTests(unittest.TestCase):
    def run_with(self, content_a=CONTENT_A, content_b=CONTENT_B, issues=('300',)):
        with patch('kill5.adapters.merged_target.crawl_merged_source',
                   side_effect=make_fake(content_a, content_b)):
            return crawl_merged_target(TARGET, list(issues))

    def test_two_sources_merge_into_five(self):
        results = self.run_with()
        self.assertEqual(results[0].numbers, ['01', '35', '32', '13', '11'])
        self.assertEqual(results[0].name, '朱唇玉面老鼠报')

    def test_multi_issue_run(self):
        results = self.run_with(issues=('300', '299'))
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].numbers, ['01', '35', '32', '13', '11'])
        self.assertEqual(results[1].numbers, ['01', '23', '24', '45', '34'])

    def test_three_plus_one_fails(self):
        with self.assertRaises(CrawlError) as ctx:
            self.run_with(content_b='300期【绝杀②码】13 开0000准\n')
        self.assertIn('2 个合法号码', str(ctx.exception))

    def test_missing_issue_fails(self):
        with self.assertRaises(CrawlError) as ctx:
            self.run_with(content_a='299期:绝杀三码【01.23.24】开猴23错\n')
        self.assertIn('没有 300 期', str(ctx.exception))

    def test_duplicate_numbers_across_sources_fail(self):
        with self.assertRaises(CrawlError) as ctx:
            self.run_with(content_b='300期【绝杀②码】01.35 开0000准\n')
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

    def test_draw_number_with_colon_not_taken(self):
        with self.assertRaises(CrawlError):
            self.run_with(content_a='300期:绝杀三码 开:龙02准\n')

    def test_draw_number_without_colon_not_taken(self):
        """缺陷 4：无冒号开奖号（开 13 24 / 开龙02）不能被当杀码。"""
        with self.assertRaises(CrawlError):
            self.run_with(content_a='300期:绝杀三码 开 05 12 24\n')

    def test_date_not_taken_as_numbers(self):
        """缺陷 5：日期/时间不能被当成号码。"""
        with self.assertRaises(CrawlError):
            self.run_with(
                content_b='300期【绝杀②码】 更新时间 2026-10-27 已更新 开0000准\n'
            )

    def test_date_ignored_real_numbers_still_taken(self):
        """带日期时仍要取到真实号码。"""
        results = self.run_with(
            content_b='300期【绝杀②码】 更新 2026-10-27 13.11 开0000准\n'
        )
        self.assertEqual(results[0].numbers[-2:], ['13', '11'])

    def test_missing_qi_char_does_not_borrow(self):
        """缺陷 3：299: 这种缺「期」字写法要能截断，不能借下一期。"""
        with self.assertRaises(CrawlError):
            self.run_with(
                content_a='300期:绝杀三码\n299:绝杀三码【01.23.24】\n'
            )

    def test_four_digit_issue_does_not_borrow(self):
        """缺陷 3：1300期 四位笔误要能截断。"""
        with self.assertRaises(CrawlError):
            self.run_with(
                content_a='300期:绝杀三码\n1300期:绝杀三码【05.06.07】\n'
            )

    def test_other_column_in_same_segment_not_taken(self):
        """缺陷 6：段内别栏目（杀三肖）的号码不能被取走。"""
        results = self.run_with(
            content_a='300期:绝杀三肖 41.42.43 绝杀三码【01.35.32】开00准\n'
        )
        self.assertEqual(results[0].numbers[:3], ['01', '35', '32'])

    def test_conflicting_values_in_one_segment_fails(self):
        """同一段内出现两个不同结果必须判冲突（不许静默取第一个）。"""
        with self.assertRaises(CrawlError) as ctx:
            self.run_with(
                content_a=(
                    '300期:绝杀三码【01.35.32】开00准\n'
                    '绝杀三码【04.05.06】开00准\n'
                )
            )
        self.assertIn('多个不同结果', str(ctx.exception))

    def test_later_wrapped_period_does_not_override(self):
        """页面上一圈的同期记录（期号循环）不能覆盖本圈的最新值。"""
        results = self.run_with(
            content_a=(
                '300期:绝杀三码【01.35.32】开00准\n'
                '299期:绝杀三码【01.23.24】开猴23错\n'
                '300期:绝杀三码【04.05.06】开00准\n'
            )
        )
        self.assertEqual(results[0].numbers[:3], ['01', '35', '32'])


class MergedConfigTests(unittest.TestCase):
    """缺陷 2/8：配置校验不能读残留 count，merged 顶层 url 必须合法。"""

    def _load(self, targets):
        from kill5.config import load_targets
        tmp = tempfile.TemporaryDirectory(prefix='wuma-merged-cfg-')
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'targets.json'
        path.write_text(json.dumps({'schema_version': 2, 'targets': targets},
                                   ensure_ascii=False), encoding='utf-8')
        return load_targets(path, allow_legacy=True)

    def _merged_entry(self, **over):
        entry = {
            'id': 'kill5-9001',
            'name': '合并测试站',
            'url': 'https://a.example/topic/1.html',
            'region': 'top',
            'count': 5,
            'source': {'kind': 'merged_sources'},
            'parse': {'keywords': ['绝杀三码']},
            'merged': [
                {'url': 'https://a.example/1', 'keywords': ['绝杀三码'], 'count': 3},
                {'url': 'https://b.example/2', 'keywords': ['绝杀②码'], 'count': 2},
            ],
        }
        entry.update(over)
        return entry

    def test_merged_as_first_target_loads(self):
        """缺陷 2：merged 站排第一条不能抛 UnboundLocalError。"""
        loaded = self._load([self._merged_entry()])
        self.assertEqual(len(loaded), 1)
        self.assertEqual(len(loaded[0]['merged']), 2)

    def test_merged_after_six_count_target_still_loads(self):
        """缺陷 2：前一条 count=6 时不能拿它去比对 merged 的 3+2。"""
        other = {
            'id': 'kill5-9002',
            'name': '占位站',
            'url': 'https://c.example/topic/3.html',
            'region': 'top',
            'count': 5,
            'source': {'kind': 'static_topic'},
            'parse': {'keywords': ['绝杀五码'], 'anchor': '占位'},
        }
        loaded = self._load([other, self._merged_entry()])
        self.assertEqual(len(loaded), 2)

    def test_merged_requires_two_sources(self):
        with self.assertRaises(ValueError) as ctx:
            self._load([self._merged_entry(merged=[
                {'url': 'https://a.example/1', 'keywords': ['绝杀三码'], 'count': 5},
            ])])
        self.assertIn('至少需要两个来源', str(ctx.exception))

    def test_merged_rejects_empty_url(self):
        """缺陷 8：merged 顶层 url 为空必须拒绝。"""
        with self.assertRaises(ValueError) as ctx:
            self._load([self._merged_entry(url='')])
        self.assertIn('缺少有效 http(s) url', str(ctx.exception))

    def test_merged_rejects_bottom_region(self):
        """建议 10：merged 只支持 top。"""
        with self.assertRaises(ValueError) as ctx:
            self._load([self._merged_entry(region='bottom')])
        self.assertIn('只支持 top', str(ctx.exception))


class MergedFailureEvidenceTests(unittest.TestCase):
    """缺陷 1：合并站失败时证据必须带 issues，否则整批输出被阻断。"""

    def test_failure_evidence_contains_issues(self):
        from kill5.engine import crawl_one
        target = dict(TARGET)
        target['merged'] = [
            {'url': 'https://a.example/1', 'keywords': ['绝杀三码'], 'count': 3},
            {'url': 'https://b.example/2', 'keywords': ['绝杀②码'], 'count': 2},
        ]
        with patch('kill5.adapters.merged_target.crawl_merged_source',
                   side_effect=make_fake(content_a='299期:绝杀三码【01.23.24】\n')):
            with tempfile.TemporaryDirectory(prefix='wuma-merged-dbg-') as tmp:
                _, failure = crawl_one(target, ['300'], debug_dir=Path(tmp))
        self.assertIsNotNone(failure)
        self.assertIn('issues', failure.evidence)
        self.assertEqual(list(failure.evidence['issues']), ['300'])


if __name__ == '__main__':
    unittest.main()
