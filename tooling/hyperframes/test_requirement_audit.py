#!/usr/bin/env python3
"""Conservative 64-requirement v0.5 delta inventory; not full 208-ID acceptance."""
from __future__ import annotations
import json
from pathlib import Path
import re
import unittest
ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'docs/creative-production/V05_DELTA_REQUIREMENTS.json'
class DeltaAuditTests(unittest.TestCase):
    def setUp(self):
        self.doc=json.loads(DATA.read_text())
    def test_exact_64_known_delta_identifiers_and_all_16_epics(self):
        rows=self.doc['requirements']
        self.assertEqual(len(rows),64)
        self.assertEqual(len({row['id']for row in rows}),64)
        self.assertEqual([row['id']for row in rows],[f'MW05-E{epic:02d}-{item:02d}'
            for epic in range(16)for item in range(1,5)])
        self.assertEqual({row['epic']for row in rows},{f'E{i:02d}'for i in range(16)})
        self.assertTrue(all(row['title'] and len(row['title'])<160 for row in rows))
        self.assertEqual(self.doc['baseline_acceptance'],'NOT_RECONCILED_IN_THIS_BRANCH')
    def test_no_requirement_can_inherit_a_pass_or_human_approval_from_code_presence(self):
        for row in self.doc['requirements']:
            self.assertIn(row['implementation_state'],('PARTIAL','NOT_VERIFIED','BLOCKED'))
            self.assertEqual(row['release_acceptance'],'NOT_CLOSED')
            self.assertEqual(row['human_review'],'NOT_PERFORMED')
            self.assertEqual(bool(row['code_evidence']),
                row['implementation_state']!='NOT_VERIFIED')
            for file in row['code_evidence']:
                self.assertFalse(Path(file).is_absolute())
                self.assertTrue((ROOT/file).is_file(),file)
    def test_host_browser_cannot_be_described_as_completed_before_same_sha_ci(self):
        blocked=next(row for row in self.doc['requirements']if row['id']=='MW05-E04-01')
        self.assertEqual(blocked['implementation_state'],'BLOCKED')
        self.assertEqual(blocked['release_acceptance'],'NOT_CLOSED')
if __name__=='__main__':unittest.main()
