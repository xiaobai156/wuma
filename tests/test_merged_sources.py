"""merged_sources 合并来源站点的回归测试（不联网）。

覆盖两轮审查指出的缺陷：
- 失败证据必须带 issues（否则整批输出被阻断）
- 配置校验不能读残留 count（前一条 count=3/6 的场景）
- 期号切段：缺「期」字、四位笔误不能借值
- 开奖号：带冒号、不带冒号都不能当杀码
- 日期时间不能当号码；横线号码不能被日期规则误剔
- 别栏目号码不能被取走（含 2 码来源）
- 号码在关键词之前不能取
- 本圈缺值不能跳到上一圈借值
- 同期多值必须判冲突
- 缓存身份指纹必须含 merged
"""
from __future__ import annotations

import base64
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
        """3+1 不足 5 码必须失败，不能凑数。"""
        with self.assertRaises(CrawlError) as ctx:
            self.run_with(content_b='300期【绝杀②码】13 开0000准\n')
        self.assertIn('数量不符合', str(ctx.exception))

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

    # --- 开奖号 ---

    def test_draw_number_with_colon_not_taken(self):
        with self.assertRaises(CrawlError):
            self.run_with(content_a='300期:绝杀三码 开:龙02准\n')

    def test_draw_number_without_colon_not_taken(self):
        with self.assertRaises(CrawlError):
            self.run_with(content_a='300期:绝杀三码 开 05 12 24\n')

    # --- 日期/时间 ---

    def test_date_not_taken_as_numbers(self):
        with self.assertRaises(CrawlError):
            self.run_with(
                content_b='300期【绝杀②码】 更新时间 2026-10-27 已更新 开0000准\n'
            )

    def test_date_glued_to_chinese_still_stripped(self):
        """日期紧贴中文（无空格）也要剔除，\\b 在中文旁不成立是旧缺陷。"""
        with self.assertRaises(CrawlError):
            self.run_with(content_b='300期【绝杀②码】更新10-27开0000准\n')

    def test_time_glued_to_chinese_still_stripped(self):
        with self.assertRaises(CrawlError):
            self.run_with(content_a='300期:绝杀三码时间19:41开00准\n')

    def test_date_ignored_real_numbers_still_taken(self):
        results = self.run_with(
            content_b='300期【绝杀②码】 更新 2026-10-27 13.11 开0000准\n'
        )
        self.assertEqual(results[0].numbers[-2:], ['13', '11'])

    def test_dash_separated_numbers_not_stripped_as_date(self):
        """横线分隔的号码组不能被日期规则误剔（12-13 不是月-日）。"""
        results = self.run_with(content_b='300期【绝杀②码】12-13 开0000准\n')
        self.assertEqual(results[0].numbers[-2:], ['12', '13'])

    # --- 期号边界 ---

    def test_missing_qi_char_does_not_borrow(self):
        with self.assertRaises(CrawlError):
            self.run_with(content_a='300期:绝杀三码\n299:绝杀三码【01.23.24】\n')

    def test_four_digit_issue_does_not_borrow(self):
        with self.assertRaises(CrawlError):
            self.run_with(content_a='300期:绝杀三码\n1300期:绝杀三码【05.06.07】\n')

    # --- 栏目归属 ---

    def test_other_column_before_keyword_not_taken(self):
        """别栏目号码在关键词之前，不能被取走（3 码来源）。"""
        results = self.run_with(
            content_a='300期:杀三肖 41.42.43 绝杀三码【01.35.32】开00准\n'
        )
        self.assertEqual(results[0].numbers[:3], ['01', '35', '32'])

    def test_other_column_in_short_source_not_taken(self):
        """别栏目号码在关键词之前，不能被取走（2 码来源，旧版漏修的路径）。"""
        results = self.run_with(
            content_b='300期:杀三肖 41.42 绝杀②码 13.11 开0000准\n'
        )
        self.assertEqual(results[0].numbers[-2:], ['13', '11'])

    def test_numbers_before_keyword_not_taken(self):
        """号码写在关键词之前时不认，不能取到后面的别栏目号码。"""
        with self.assertRaises(CrawlError):
            self.run_with(
                content_a='300期: 01.35.32 绝杀三码 杀三肖 41.42.43 开00准\n'
            )

    def test_keyword_variant_matched_by_normalization(self):
        """关键词写法差异（②/2）要能命中。"""
        results = self.run_with(content_b='300期【绝杀2码】13.11 开0000准\n')
        self.assertEqual(results[0].numbers[-2:], ['13', '11'])

    # --- 借值与冲突 ---

    def test_previous_cycle_not_borrowed_when_this_cycle_incomplete(self):
        """本圈只出 1 码时不能跳到上一圈借值。"""
        with self.assertRaises(CrawlError):
            self.run_with(
                content_b=(
                    '300期【绝杀②码】已更新\n'
                    '300期【绝杀②码】13 开0000准\n'
                    '299期【绝杀②码】45.34 开猴23准\n'
                    '300期【绝杀②码】11.23 开虎40准\n'
                )
            )

    def test_conflicting_values_in_one_segment_fails(self):
        """同一段内出现两个不同结果必须判冲突。"""
        with self.assertRaises(CrawlError) as ctx:
            self.run_with(
                content_a=(
                    '300期:绝杀三码【01.35.32】开00准\n'
                    '绝杀三码【04.05.06】开00准\n'
                )
            )
        self.assertIn('多个不同结果', str(ctx.exception))

    def test_later_wrapped_period_does_not_override(self):
        """有跨圈分界时，上一圈的同期记录不能覆盖本圈值。"""
        results = self.run_with(
            content_a=(
                '300期:绝杀三码【01.35.32】开00准\n'
                '299期:绝杀三码【01.23.24】开猴23错\n'
                '298期:绝杀三码【11.12.13】开猴23错\n'
                '365期:绝杀三码【21.22.23】开猴23错\n'
                '300期:绝杀三码【04.05.06】开00准\n'
            )
        )
        self.assertEqual(results[0].numbers[:3], ['01', '35', '32'])

    def test_same_issue_two_values_without_wrap_fails(self):
        """没有跨圈分界时同期出现两个不同值，无法判断哪个是本圈，判冲突。"""
        with self.assertRaises(CrawlError) as ctx:
            self.run_with(
                content_a=(
                    '300期:绝杀三码【01.35.32】开00准\n'
                    '299期:绝杀三码【01.23.24】开猴23错\n'
                    '300期:绝杀三码【04.05.06】开00准\n'
                )
            )
        self.assertIn('多个不同结果', str(ctx.exception))

    def test_pending_marker_blocks_previous_cycle(self):
        """本圈标「待更新」时，后面的号码属上一圈，必须失败。"""
        with self.assertRaises(CrawlError) as ctx:
            self.run_with(
                content_b=(
                    '300期【绝杀②码】待更新\n'
                    '300期【绝杀②码】11.23 开虎40准\n'
                )
            )
        self.assertIn('上一圈', str(ctx.exception))

    def test_date_after_colon_stripped(self):
        """日期紧跟在冒号后也要剔除（旧版只认中文/空白前）。"""
        with self.assertRaises(CrawlError):
            self.run_with(content_b='300期【绝杀②码】更新时间:10-27开0000准\n')

    def test_dash_pair_after_chinese_kept(self):
        """中文紧贴的横线两码是号码，不能被日期规则剔掉。"""
        results = self.run_with(content_b='300期绝杀②码12-13 开0000准\n')
        self.assertEqual(results[0].numbers[-2:], ['12', '13'])

    def test_unjing_not_treated_as_pending(self):
        """「未开奖」是号码已出、只是没开奖，不能当未发布。"""
        results = self.run_with(
            content_a='300期:绝杀三码【01.35.32】未开奖\n'
        )
        self.assertEqual(results[0].numbers[:3], ['01', '35', '32'])

    def test_other_column_segment_skipped_not_failed(self):
        """同期别栏目段落（有号码但不是本栏目）应跳过，不能让整站失败。"""
        results = self.run_with(
            content_a=(
                '300期:杀三肖 41.42.43.44.45\n'
                '300期:绝杀三码【01.35.32】开00准\n'
            )
        )
        self.assertEqual(results[0].numbers[:3], ['01', '35', '32'])

    def test_other_column_digits_not_stripped_as_keyword(self):
        """别栏目的「16尾」不能被当关键词数字剔掉，否则会退化成借值。"""
        results = self.run_with(
            content_a=(
                '300期 杀尾 16尾 26尾\n'
                '300期:绝杀三码【04.05.06】开00准\n'
            )
        )
        self.assertEqual(results[0].numbers[:3], ['04', '05', '06'])

    def test_short_window_wrap_not_borrowed(self):
        """每圈只展示近 10 期时，本圈没该期要失败，不能取上一圈的。"""
        with self.assertRaises(CrawlError):
            self.run_with(
                content_a=(
                    '299期:绝杀三码【21.22.23】开00准\n'
                    '298期:绝杀三码【21.22.23】开00准\n'
                    '300期:绝杀三码【44.45.46】开00准\n'
                )
            )

    def test_multi_column_same_keyword_conflicts(self):
        """多栏目同关键词但值不同，不能静默取第一栏。"""
        lines = []
        for column in range(4):
            for value in range(300, 230, -1):
                numbers = '01.02.03' if column == 0 else '11.12.13'
                lines.append(f'{value}期 绝杀三码 {numbers}')
        with self.assertRaises(CrawlError) as ctx:
            self.run_with(content_a='\n'.join(lines))
        self.assertIn('多个不同结果', str(ctx.exception))


class MergedConfigTests(unittest.TestCase):
    """配置校验：count 不能读残留值，merged 顶层 url 必须合法，只允许 top。"""

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

    def _other_entry(self, name, count):
        return {
            'id': f'kill5-91{count:02d}',
            'name': name,
            'url': f'https://c{count}.example/topic/3.html',
            'region': 'top',
            'count': count,
            'source': {'kind': 'static_topic'},
            'parse': {'keywords': ['绝杀五码'], 'anchor': name},
        }

    def test_merged_as_first_target_loads(self):
        loaded = self._load([self._merged_entry()])
        self.assertEqual(len(loaded), 1)
        self.assertEqual(len(loaded[0]['merged']), 2)

    def test_merged_after_normal_target_loads(self):
        """前面有普通 5 码站时正常加载。"""
        loaded = self._load([self._other_entry('普通站', 5), self._merged_entry()])
        self.assertEqual(len(loaded), 2)

    def test_merged_count_uses_own_value(self):
        """merged 校验必须用本条自己的 count（5），不能取上一条的残留值。"""
        loaded = self._load([self._merged_entry()])
        self.assertEqual(loaded[0]['count'], 5)

    def test_merged_rejects_own_count_mismatch(self):
        """本条 count 与各段之和不符时必须拒绝（说明读的是本条的值）。"""
        with self.assertRaises(ValueError) as ctx:
            self._load([self._merged_entry(count=6)])
        self.assertIn('不一致', str(ctx.exception))

    def test_merged_requires_two_sources(self):
        with self.assertRaises(ValueError) as ctx:
            self._load([self._merged_entry(merged=[
                {'url': 'https://a.example/1', 'keywords': ['绝杀三码'], 'count': 5},
            ])])
        self.assertIn('至少需要两个来源', str(ctx.exception))

    def test_merged_rejects_empty_url(self):
        with self.assertRaises(ValueError) as ctx:
            self._load([self._merged_entry(url='')])
        self.assertIn('缺少有效 http(s) url', str(ctx.exception))

    def test_merged_rejects_bottom_region(self):
        with self.assertRaises(ValueError) as ctx:
            self._load([self._merged_entry(region='bottom')])
        self.assertIn('只支持 top', str(ctx.exception))


class MergedIdentityTests(unittest.TestCase):
    """缺陷 7：缓存身份指纹必须包含 merged 来源。"""

    def _fingerprint(self, merged):
        from kill5.identity import target_cache_identity
        target = dict(TARGET)
        target['merged'] = merged
        return target_cache_identity(target, project_name='杀五码_解耦版')

    def test_identity_changes_when_sources_change(self):
        first = self._fingerprint([
            {'url': 'https://a.example/1', 'keywords': ['绝杀三码'], 'count': 3},
            {'url': 'https://b.example/2', 'keywords': ['绝杀②码'], 'count': 2},
        ])
        second = self._fingerprint([
            {'url': 'https://x.example/9', 'keywords': ['绝杀三码'], 'count': 3},
            {'url': 'https://y.example/8', 'keywords': ['绝杀②码'], 'count': 2},
        ])
        self.assertNotEqual(first['config_fingerprint'], second['config_fingerprint'])

    def test_identity_stable_for_same_sources(self):
        merged = [
            {'url': 'https://a.example/1', 'keywords': ['绝杀三码'], 'count': 3},
            {'url': 'https://b.example/2', 'keywords': ['绝杀②码'], 'count': 2},
        ]
        self.assertEqual(
            self._fingerprint(merged)['config_fingerprint'],
            self._fingerprint(merged)['config_fingerprint'],
        )


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


class MergedFetchTests(unittest.TestCase):
    """merged_sources 的抓取与解码（不联网，喂假页面）。"""

    def test_decodes_inline_and_external_script(self):
        from kill5.adapters import merged_sources as ms

        inline = base64.b64encode('300期【绝杀②码】13.11'.encode()).decode()
        external = base64.b64encode('299期【绝杀②码】45.34'.encode()).decode()
        page = f'<script>document.writeln(strdecode("{inline}"))</script>'
        page += '<script src="//cdn.example/x.js?v=2"></script>'

        def fake_fetch(url, encoding=None, **kwargs):
            if 'cdn.example' in url:
                return f'<script>document.writeln(strdecode("{external}"))</script>'
            return page

        with patch.object(ms, 'fetch_text', side_effect=fake_fetch):
            _, content = ms.crawl_merged_source({'url': 'https://a.example/1'})
        self.assertIn('13.11', content)
        self.assertIn('45.34', content)

    def test_empty_decode_raises_instead_of_falling_back(self):
        """解码为空必须报错，不能退回整页原文（fail-open）。"""
        from kill5.adapters import merged_sources as ms
        with patch.object(ms, 'fetch_text', return_value='<html>导航壳</html>'):
            with self.assertRaises(ValueError) as ctx:
                ms.crawl_merged_source({'url': 'https://a.example/1'})
        self.assertIn('解码为空', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
