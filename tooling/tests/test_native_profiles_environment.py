"""Regression for the original Hero prepare-03 environment loss; no Native."""
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import native_profiles_workflow as route


class PreparationEnvironmentTests(unittest.TestCase):
    CURRENT = {'source_sha':'4670ea0b34e2d4a5bd99df0f1d6e5605438a62ea',
               'semwright_sha':'fcf2ff1b4f40f864827cf2d4f391d1da43e9ebfe',
               'run_id':'42391195075','run_attempt':'1'}
    ORIGINAL = {'PATH':'/usr/bin:/bin','GITHUB_WORKSPACE':'/home/runner/work/motionwright/motionwright',
                'RUNNER_TEMP':'/home/runner/work/_temp','SEMWRIGHT_CHECKOUT':'/home/runner/work/motionwright/motionwright/_semwright',
                'MW_NATIVE_RESOURCE_ORIGIN':'/home/runner/work/_temp/owner/origin.original.json',
                'CI':'true','GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted'}

    def environment(self, original):
        with mock.patch.dict(os.environ,original,clear=True):
            return route.clean_environment(self.CURRENT,preparation=True)

    def test_passes_original_runner_signals_without_passing_credentials(self):
        original = {**self.ORIGINAL,'GH_TOKEN':'test-token-not-a-real-credential',
                    'ACTIONS_RUNTIME_TOKEN':'test-token-not-a-real-credential'}
        actual = self.environment(original)
        for key in ('CI','GITHUB_ACTIONS','RUNNER_ENVIRONMENT'):
            self.assertEqual(actual[key],original[key])
        self.assertNotIn('GH_TOKEN',actual)
        self.assertNotIn('ACTIONS_RUNTIME_TOKEN',actual)

    def test_missing_or_negative_provenance_is_not_manufactured(self):
        for key in ('CI','GITHUB_ACTIONS','RUNNER_ENVIRONMENT'):
            original = dict(self.ORIGINAL); original.pop(key)
            with self.subTest(missing=key):
                self.assertNotIn(key,self.environment(original))
        for key,value in (('CI','false'),('GITHUB_ACTIONS','false'),
                          ('RUNNER_ENVIRONMENT','self-hosted')):
            original = {**self.ORIGINAL,key:value}
            with self.subTest(key=key,value=value):
                self.assertEqual(self.environment(original)[key],value)
        original = {**self.ORIGINAL,'CIRCLECI':'true'}
        self.assertNotIn('CIRCLECI',self.environment(original))


if __name__ == '__main__':
    unittest.main()
