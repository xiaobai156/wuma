# -*- coding: utf-8 -*-
"""发财小哥 4 码特例：只允许该站、该 URL、该关键词、bottom 方向的 count=4。"""
import json
import tempfile
import unittest
from pathlib import Path

from kill5.config import FACAI_URL, load_targets


class FacaiCountExceptionTests(unittest.TestCase):
    """count 固定 5 的例外必须逐条写死，不能放宽成通用规则。"""

    def _load(self, targets):
        tmp = tempfile.TemporaryDirectory(prefix='wuma-facai-cfg-')
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'targets.json'
        path.write_text(
            json.dumps({'schema_version': 2, 'targets': targets}, ensure_ascii=False),
            encoding='utf-8',
        )
        return load_targets(path, allow_legacy=True)

    def _entry(self, **over):
        entry = {
            'id': 'kill5-9255',
            'name': '发财小哥',
            'url': FACAI_URL,
            'region': 'bottom',
            'count': 4,
            'source': {'kind': 'decoded_script'},
            'parse': {
                'keywords': ['杀4码'],
                'anchor': '发财小哥',
                'decoded_anchor_only': '发财小哥',
                'decoded_anchor_chunks': 31,
                'first_issue_chain': True,
            },
        }
        entry.update(over)
        return entry

    def test_facai_count_4_is_allowed(self):
        targets = self._load([self._entry()])
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]['name'], '发财小哥')
        self.assertEqual(targets[0]['count'], 4)

    def test_other_site_count_4_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._load([self._entry(name='别的站')])
        self.assertIn('count 必须固定为 5', str(ctx.exception))

    def test_facai_with_other_url_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._load([self._entry(url='https://other.example/topic/1.html')])
        self.assertIn('count 必须固定为 5', str(ctx.exception))

    def test_facai_with_other_keywords_is_rejected(self):
        entry = self._entry()
        entry['parse'] = dict(entry['parse'], keywords=['绝杀四码'])
        with self.assertRaises(ValueError) as ctx:
            self._load([entry])
        self.assertIn('count 必须固定为 5', str(ctx.exception))

    def test_facai_with_top_region_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._load([self._entry(region='top')])
        self.assertIn('count 必须固定为 5', str(ctx.exception))

    def test_facai_with_other_count_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._load([self._entry(count=3)])
        self.assertIn('count 必须固定为 5', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
