from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kill5.validator import extract_issue_numbers


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "baseline"


class BaselineParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(
            (FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8")
        )

    def test_all_representative_snapshots_match_frozen_results(self):
        issue = self.manifest["issue"]
        for representative in self.manifest["representatives"]:
            fixture_path = FIXTURE_DIR / representative["fixture"]
            content = fixture_path.read_text(encoding="utf-8")
            digest = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
            self.assertEqual(digest, representative["fixture_sha256"])

            target = representative["target"]
            expected = representative["expected"]["numbers"]
            with self.subTest(
                source=representative["source_kind"],
                name=target["name"],
            ):
                found = extract_issue_numbers(
                    content,
                    [issue],
                    keywords=target.get("keywords"),
                    expected_count=target.get("count"),
                    position=target.get("position", "first"),
                    strict_ambiguous=True,
                    allow_duplicate_numbers=False,
                    anchor=target.get("anchor"),
                    stop_anchor=target.get("stop_anchor"),
                    region=target.get("region"),
                    issue_position_window=target.get("issue_position_window"),
                    first_issue_chain=target.get("first_issue_chain", False),
                    keyword_before_issue=target.get(
                        "keyword_before_issue", False
                    ),
                    keyword_before_issue_window=target.get(
                        "keyword_before_issue_window"
                    ),
                )
                self.assertEqual(found, {issue: expected})


if __name__ == "__main__":
    unittest.main()
