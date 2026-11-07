"""Real event-file regression for Hero's MLT preparation environment; no Native."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_g2_custody as custody
import native_profiles_workflow as route


class PreparationEventTests(unittest.TestCase):
    CURRENT = {'source_sha': '8a6bff775226c7d8ea5eb5855b38305b86719590',
               'semwright_sha': 'fcf2ff1b4f40f864827cf2d4f391d1da43e9ebfe',
               'run_id': '42393445406', 'run_attempt': '1'}
    ORIGINAL = {'PATH': '/usr/bin:/bin',
                'GITHUB_WORKSPACE': '/home/runner/work/motionwright/motionwright',
                'RUNNER_TEMP': '/home/runner/work/_temp',
                'SEMWRIGHT_CHECKOUT': '/home/runner/work/motionwright/motionwright/_semwright',
                'MW_NATIVE_RESOURCE_ORIGIN': '/home/runner/work/_temp/owner/origin.original.json',
                'CI': 'true', 'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}

    def environment(self, original, *, preparation=True):
        with mock.patch.dict(os.environ, original, clear=True):
            return route.clean_environment(self.CURRENT, preparation=preparation)

    def test_original_event_path_reaches_real_reader_only_during_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            event = Path(temporary).resolve() / 'event.original.json'
            original = b'{"inputs":{"mode":"prepare","owner_permissions":""}}\n'
            event.write_bytes(original)
            source = {**self.ORIGINAL, 'GITHUB_EVENT_PATH': str(event)}
            actual = self.environment(source)
            self.assertEqual(actual['GITHUB_EVENT_PATH'], str(event))
            self.assertEqual(custody.context(actual), self.CURRENT)
            self.assertEqual(custody.retained_reference_input(actual), '')
            self.assertEqual(event.read_bytes(), original)
            self.assertNotIn('GITHUB_EVENT_PATH', self.environment(source, preparation=False))

    def test_missing_empty_or_invalid_event_remains_rejected(self):
        for source in (dict(self.ORIGINAL), {**self.ORIGINAL, 'GITHUB_EVENT_PATH': ''}):
            with self.subTest(source_has_path='GITHUB_EVENT_PATH' in source):
                actual = self.environment(source)
                self.assertEqual(actual.get('GITHUB_EVENT_PATH'), source.get('GITHUB_EVENT_PATH'))
                with self.assertRaisesRegex(custody.CustodyRejected, '^reference_dispatch_event_path$'):
                    custody.retained_reference_input(actual)
        with tempfile.TemporaryDirectory() as temporary:
            event = Path(temporary).resolve() / 'invalid-event.json'
            for value, code in (({'inputs': []}, 'reference_dispatch_inputs'),
                                ({'inputs': {custody.REFERENCE_INPUT: 'unadmitted-reference',
                                             'verification_scope': 'repaired-g2'}},
                                 'retained_reference_repaired_g2_only')):
                with self.subTest(code=code):
                    event.write_text(json.dumps(value), encoding='utf-8')
                    actual = self.environment({**self.ORIGINAL, 'GITHUB_EVENT_PATH': str(event)})
                    with self.assertRaisesRegex(custody.CustodyRejected, '^' + code + '$'):
                        custody.retained_reference_input(actual)


if __name__ == '__main__':
    unittest.main()
