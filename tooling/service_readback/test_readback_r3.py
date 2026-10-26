"""Source-only regression tests. Fake transport; Popen is forbidden in every test.

Run --target service_archive_readback_r2.py for the causal pre-repair failures.
No StudioService, SQLite connection, npm, browser, or network is used.
"""
import argparse
import base64
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

parser = argparse.ArgumentParser()
parser.add_argument('--target', default=str(Path(__file__).with_name('service_archive_readback_r3.py')))
known, remaining = parser.parse_known_args()
spec = importlib.util.spec_from_file_location('readback_under_test', known.target)
reader_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader_module)


class FakeReader:
    instances = []
    fail_rpc = False
    fail_close = False

    def __init__(self, binary, database, output, timeout):
        self.process = None
        self.sequence = 0
        self.taints = []
        self.command = [str(binary), '--database', str(database), '--desktop-invoke-fixture']
        self.calls = []
        self.closes = 0
        FakeReader.instances.append(self)

    def start(self):
        self.process = types.SimpleNamespace(pid=424242, returncode=0)

    def rpc(self, command, request):
        self.sequence += 1
        self.calls.append(command)
        if self.fail_rpc and command == 'get_project':
            raise ValueError('PRIMARY_FAKE_RPC_FAILURE')
        if command == 'bootstrap':
            return {'test_transport': 'service-test-jsonl', 'fixture': {'label': 'synthetic_test_fixture'},
                    'desktop_invoke_fixture': True, 'native_sdk': {'mode': 'tauri'}}
        if command == 'project_history':
            return []
        return {'id': 'mock-project', 'generation': 'mock-generation', 'revision': 0}

    def close(self):
        self.closes += 1
        return {'observedAt': '2026-10-26T00:00:00Z', 'processId': self.process.pid if self.process else None,
                'returnCode': 0 if self.process else None,
                'taints': [], 'errors': ['FAKE_CLOSE_FAILURE'] if self.fail_close else [],
                'command': self.command, 'rpcCount': self.sequence, 'clean': bool(self.process) and not self.fail_close}


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def case_fixture(root, case='case-one'):
    target = root / case
    store = target / 'closed-store' / 'store'
    store.mkdir(parents=True)
    (store / 'studio.sqlite3').write_bytes(b'INERT MOCK STORE; NEVER OPENED BY SQLITE')
    oracle = {'schemaVersion': 1, 'observedAt': '2026-10-26T00:00:00Z',
              'project': {'id': 'mock-project', 'generation': 'mock-generation', 'revision': 0},
              'journal': [], 'journalAfterRevisionExclusive': 0, 'journalComplete': True}
    entries = reader_module.tree_entries(store)
    digest = reader_module.digest_json(entries)
    closure = {'databasePath': str(target / 'service' / 'studio.sqlite3'), 'status': 'fulfilled',
               'exits': [{'code': 0, 'signal': None}], 'taints': []}
    manifest = {'schemaVersion': 1, 'databaseRelativePath': 'studio.sqlite3', 'entries': entries,
                'sourceBeforeSha256': digest, 'sourceAfterSha256': digest, 'snapshotSha256': digest,
                'sourceDirectory': str(target / 'service'), 'snapshotDirectory': str(store),
                'lifecycle': [{key: closure[key] for key in ('databasePath', 'exits', 'taints')}]}
    outcomes = {'schemaVersion': 1, 'errors': [], 'preparation': [{'status': 'fulfilled'}],
                'closures': [closure], 'capture': manifest, 'oracle': oracle}
    evidence = {'final': oracle['project'], 'canonicalOracle': oracle, 'primaryFailure': None, 'failures': [],
                'services': [{'binaryPath': 'mock-only', 'processExits': closure['exits'], 'lifecycleTaints': []}]}
    dump(target / 'closed-store' / 'manifest.json', manifest)
    dump(target / 'closed-store' / 'oracle.json', oracle)
    dump(target / 'closed-store-outcomes.json', outcomes)
    dump(target / 'production-design-service-evidence.json', evidence)
    attachments = []
    for name in ('closed-store-outcomes', 'production-design-service-evidence'):
        path = target / 'attachments' / (name + '-exact-copy.json')
        path.parent.mkdir(exist_ok=True)
        path.write_bytes((target / (name + '.json')).read_bytes())
        attachments.append({'name': name, 'contentType': 'application/json', 'path': str(path)})
    test = {'projectId': 'chromium', 'projectName': 'chromium', 'expectedStatus': 'passed', 'status': 'expected',
            'results': [{'status': 'passed', 'retry': 0, 'errors': [], 'attachments': attachments}]}
    spec = {'id': 'mock-spec-' + case, 'file': 'production-design-service.spec.ts', 'line': 1, 'column': 1,
            'title': 'SOURCE-ONLY MOCK ' + case, 'ok': True, 'tests': [test]}
    return spec


def report_fixture(root, specs):
    return {'config': {'projects': [{'id': 'chromium', 'name': 'chromium', 'outputDir': str(root), 'retries': 0, 'repeatEach': 1}]},
            'suites': [{'title': 'mock', 'file': 'production-design-service.spec.ts', 'specs': specs}],
            'errors': [], 'stats': {'expected': len(specs), 'unexpected': 0, 'skipped': 0, 'flaky': 0}}


class RuntimeFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='readback-source-only-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.results = self.base / 'results'
        self.results.mkdir()
        self.spec = case_fixture(self.results)
        self.report = self.base / 'playwright.original.json'
        dump(self.report, report_fixture(self.results, [self.spec]))
        self.roster = self.base / 'playwright-list.original.json'
        listed = report_fixture(self.results, [copy.deepcopy(self.spec)])
        listed['stats'].update(expected=0, skipped=1)
        listed['suites'][0]['specs'][0]['tests'][0]['results'] = []
        listed['suites'][0]['specs'][0]['tests'][0]['status'] = 'skipped'
        dump(self.roster, listed)
        self.receipt = self.base / 'provenance.mock-receipt.json'
        dump(self.receipt, {'testOnly': True, 'notAProvenanceReceipt': 'source-only transport regression'})
        self.binary = self.base / 'INERT-NOT-EXECUTABLE.bin'
        self.binary.write_bytes(b'SOURCE ONLY MOCK; POPEN IS FORBIDDEN')
        self.output = self.base / 'output'
        FakeReader.instances = []
        FakeReader.fail_rpc = False
        FakeReader.fail_close = False

    def run_main(self, fail_names=(), mutate_report=None, omit_flags=()):
        if mutate_report:
            value = json.loads(self.report.read_text()); mutate_report(value); dump(self.report, value)
        args = ['readback', '--results-root', str(self.results), '--output', str(self.output), '--expected-cases', '1',
                '--playwright-exit-code', '0', '--execute-readback', '--expected-binary-sha256', reader_module.sha256(self.binary),
                '--backend-source-sha', 'a' * 40, '--provenance-receipt', str(self.receipt)]
        if hasattr(reader_module, 'bind_playwright_roster'):
            args += ['--playwright-json', str(self.report), '--expected-playwright-sha256', reader_module.sha256(self.report),
                     '--expected-roster-json', str(self.roster), '--expected-roster-sha256', reader_module.sha256(self.roster),
                     '--expected-provenance-sha256', reader_module.sha256(self.receipt), '--provenance-reviewed']
        for flag in omit_flags:
            index = args.index(flag)
            del args[index:index + (1 if flag in ('--provenance-reviewed', '--execute-readback') else 2)]
        original = reader_module.write_json
        def failing(path, value, *a, **kw):
            if Path(path).name in fail_names:
                raise OSError('INJECTED_RECEIPT_WRITE_FAILURE:' + Path(path).name)
            return original(path, value, *a, **kw)
        stdout, stderr = io.StringIO(), io.StringIO()
        raised, code = None, None
        with mock.patch.object(reader_module, 'JsonlReader', FakeReader), \
             mock.patch.object(reader_module.subprocess, 'Popen', side_effect=AssertionError('REAL POPEN FORBIDDEN')), \
             mock.patch.object(reader_module, 'write_json', side_effect=failing), \
             mock.patch.dict(reader_module.os.environ, {'MOTIONWRIGHT_SERVICE_TEST_BIN': str(self.binary)}), \
             mock.patch.object(sys, 'argv', args), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = reader_module.main()
            except BaseException as error:
                raised = error
        saved = None
        for name in ('readback-report.failure.json', 'readback-report.json'):
            path = self.output / name
            if path.is_file():
                try: saved = json.loads(path.read_text()); break
                except ValueError: pass
        emitted = None
        for line in (stdout.getvalue() + '\n' + stderr.getvalue()).splitlines():
            try:
                value = json.loads(line)
                if isinstance(value.get('observation'), dict): emitted = value['observation']
            except (ValueError, AttributeError): pass
        return code, raised, saved or emitted, stdout.getvalue(), stderr.getvalue()

    def assert_executed_once(self):
        self.assertEqual(len(FakeReader.instances), 1)
        self.assertEqual(FakeReader.instances[0].calls, ['bootstrap', 'reopen', 'get_project', 'project_history', 'get_project'])
        self.assertEqual(FakeReader.instances[0].closes, 1)


class TransportCustodyTests(RuntimeFixture):
    def test_per_copy_receipt_failure_keeps_actual_execution_and_close_in_caller(self):
        code, raised, report, _, _ = self.run_main({'readback.json'})
        self.assert_executed_once()
        self.assertIsNone(raised)
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'FAILED')
        self.assertTrue(report['transportExecuted'])
        result = report['cases'][0]['readback']
        self.assertIsInstance(result, dict)
        self.assertTrue(result['transportExecuted'])
        self.assertEqual(result['close']['processId'], 424242)
        self.assertEqual(result['close']['rpcCount'], 5)
        self.assertTrue(any('INJECTED_RECEIPT_WRITE_FAILURE' in error for error in result['errors']))

    def test_final_report_write_failure_emits_actual_facts_without_rerun(self):
        code, raised, report, stdout, _ = self.run_main({'readback-report.json'})
        self.assert_executed_once()
        self.assertIsNone(raised)
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'FAILED')
        self.assertTrue(report['transportExecuted'])
        self.assertEqual(report['cases'][0]['readback']['close']['rpcCount'], 5)
        self.assertIn('INJECTED_RECEIPT_WRITE_FAILURE', stdout)

    def test_all_optional_report_writes_fail_but_stdout_keeps_execution_facts(self):
        code, raised, report, _, _ = self.run_main({'readback.json', 'readback-report.json', 'readback-report.failure.json'})
        self.assert_executed_once()
        self.assertIsNone(raised)
        self.assertEqual(code, 1)
        self.assertTrue(report['transportExecuted'])
        self.assertEqual(report['status'], 'FAILED')
        self.assertEqual(report['cases'][0]['readback']['close']['processId'], 424242)

    def test_primary_rpc_and_close_failures_survive_a_receipt_failure(self):
        FakeReader.fail_rpc = True; FakeReader.fail_close = True
        code, raised, report, _, _ = self.run_main({'readback.json'})
        self.assertIsNone(raised); self.assertEqual(code, 1)
        self.assertEqual(len(FakeReader.instances), 1); self.assertEqual(FakeReader.instances[0].closes, 1)
        self.assertTrue(report['transportExecuted'])
        result = report['cases'][0]['readback']
        self.assertTrue(any('PRIMARY_FAKE_RPC_FAILURE' in error for error in result['errors']))
        self.assertIn('FAKE_CLOSE_FAILURE', result['close']['errors'])
        self.assertTrue(any('INJECTED_RECEIPT_WRITE_FAILURE' in error for error in result['errors']))

    def test_unexpected_caller_boundary_error_keeps_completed_transport_facts(self):
        original = reader_module.read_fresh_copy
        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('INJECTED_CALLER_BOUNDARY_FAILURE')
        with mock.patch.object(reader_module, 'read_fresh_copy', side_effect=interrupted):
            code, raised, report, _, _ = self.run_main()
        self.assert_executed_once(); self.assertIsNone(raised); self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'FAILED'); self.assertTrue(report['transportExecuted'])
        self.assertEqual(report['cases'][0]['readback']['close']['rpcCount'], 5)

    def test_post_write_close_failure_never_publishes_a_passing_final_receipt(self):
        original_open = Path.open
        class FailAfterClose:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self.stream
            def __exit__(self, *args):
                self.stream.close()
                raise OSError('INJECTED_AFTER_COMPLETE_JSON_CLOSE')
        def delayed(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            mode = args[0] if args else kwargs.get('mode', 'r')
            if mode == 'x' and (path.name == 'readback-report.json' or path.name.startswith('readback-report.json.pending-')):
                return FailAfterClose(stream)
            return stream
        with mock.patch.object(Path, 'open', delayed):
            code, raised, report, _, _ = self.run_main()
        self.assert_executed_once()
        self.assertFalse((self.output / 'readback-report.json').exists(), 'A complete but unconfirmed PASS must not be published')
        self.assertIsNone(raised); self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'FAILED'); self.assertTrue(report['transportExecuted'])
        retained = list(self.output.glob('readback-report.json.pending-*'))
        self.assertEqual(len(retained), 1)
        self.assertEqual(json.loads(retained[0].read_text())['cases'][0]['readback']['close']['rpcCount'], 5)


@unittest.skipUnless(hasattr(reader_module, 'bind_playwright_roster'), 'R3-only roster and durable observation contracts')
class RosterAndDurabilityTests(RuntimeFixture):
    def test_successful_mock_control_joins_original_ids_and_keeps_durable_close(self):
        code, raised, report, _, _ = self.run_main()
        self.assert_executed_once(); self.assertIsNone(raised); self.assertEqual(code, 0)
        self.assertEqual(report['status'], 'READBACK_VERIFIED')
        self.assertEqual(report['playwrightRoster'][0]['playwrightId'], self.spec['id'])
        self.assertEqual(report['playwrightRoster'][0]['case'], 'case-one')
        records = [json.loads(line) for line in (self.output / 'execution-observations.jsonl').read_text().splitlines()]
        closed = [item for item in records if item['kind'] == 'case_closed']
        self.assertEqual(len(closed), 1); self.assertTrue(closed[0]['observation']['transportExecuted'])
        self.assertEqual(closed[0]['observation']['close']['rpcCount'], 5)
        self.assertEqual(report['sourceAuthentication'], 'NOT_ESTABLISHED_BY_THIS_SCRIPT')

    def test_original_base64_outcomes_attachment_joins_by_exact_bytes(self):
        def body(value):
            attachment = value['suites'][0]['specs'][0]['tests'][0]['results'][0]['attachments'][0]
            attachment['body'] = base64.b64encode(Path(attachment.pop('path')).read_bytes()).decode()
        code, raised, report, _, _ = self.run_main(mutate_report=body)
        self.assertIsNone(raised); self.assertEqual(code, 0); self.assert_executed_once()
        self.assertEqual(report['playwrightRoster'][0]['case'], 'case-one')

    def test_same_count_wrong_ids_are_rejected_before_transport(self):
        def change(value): value['suites'][0]['specs'][0]['id'] = 'different-real-selection'
        code, raised, report, _, _ = self.run_main(mutate_report=change)
        self.assertIsNone(raised); self.assertEqual(code, 1); self.assertEqual(FakeReader.instances, [])
        self.assertFalse(report['transportExecuted']); self.assertIn('independent expected roster', ' '.join(report['errors']))

    def test_missing_or_wrong_attachment_cannot_be_replaced_by_case_count(self):
        def change(value): value['suites'][0]['specs'][0]['tests'][0]['results'][0]['attachments'] = []
        code, raised, report, _, _ = self.run_main(mutate_report=change)
        self.assertIsNone(raised); self.assertEqual(code, 1); self.assertEqual(FakeReader.instances, [])
        self.assertIn('closed-store-outcomes attachment', ' '.join(report['errors']))

    def test_modified_attachment_bytes_fail_before_any_transport(self):
        attachment = Path(self.spec['tests'][0]['results'][0]['attachments'][0]['path'])
        attachment.write_bytes(attachment.read_bytes() + b' ')
        code, raised, report, _, _ = self.run_main()
        self.assertIsNone(raised); self.assertEqual(code, 1); self.assertEqual(FakeReader.instances, [])
        self.assertIn('exact-byte case match', ' '.join(report['errors']))

    def test_retry_or_expected_failure_is_not_accepted_as_a_clean_roster(self):
        def change(value): value['suites'][0]['specs'][0]['tests'][0]['results'][0]['retry'] = 1
        code, raised, report, _, _ = self.run_main(mutate_report=change)
        self.assertIsNone(raised); self.assertEqual(code, 1); self.assertEqual(FakeReader.instances, [])
        self.assertIn('first-attempt', ' '.join(report['errors']))

    def test_expected_list_is_not_an_execution_report(self):
        dump(self.roster, json.loads(self.report.read_text()))
        code, raised, report, _, _ = self.run_main()
        self.assertIsNone(raised); self.assertEqual(code, 1); self.assertEqual(FakeReader.instances, [])
        self.assertIn('--list output', ' '.join(report['errors']))

    def test_execution_requires_external_report_roster_receipt_and_review(self):
        for index, flag in enumerate(('--playwright-json', '--expected-playwright-sha256', '--expected-roster-json',
                                      '--expected-roster-sha256', '--provenance-receipt', '--expected-provenance-sha256', '--provenance-reviewed')):
            with self.subTest(flag=flag):
                self.output = self.base / ('omitted-' + str(index))
                code, raised, report, _, _ = self.run_main(omit_flags=(flag,))
                self.assertIsNone(raised); self.assertEqual(code, 1); self.assertEqual(FakeReader.instances, [])
                self.assertFalse(report['transportExecuted'])

    def test_observation_fsync_failure_after_start_preserves_pid_and_prevents_rpc(self):
        original = reader_module.DurableObservations.record
        def failing(instance, kind, case, observation):
            if kind == 'transport_observed':
                with mock.patch.object(reader_module.os, 'fsync', side_effect=OSError('INJECTED_OBSERVATION_FSYNC_FAILURE')):
                    return original(instance, kind, case, observation)
            return original(instance, kind, case, observation)
        with mock.patch.object(reader_module.DurableObservations, 'record', failing):
            code, raised, report, _, _ = self.run_main()
        self.assertIsNone(raised); self.assertEqual(code, 1); self.assertTrue(report['transportExecuted'])
        self.assertEqual(len(FakeReader.instances), 1); self.assertEqual(FakeReader.instances[0].calls, [])
        self.assertEqual(FakeReader.instances[0].closes, 1)
        self.assertEqual(report['cases'][0]['readback']['close']['processId'], 424242)
        self.assertEqual(report['observationJournal']['status'], 'FAILED')

    def test_windows_attachment_paths_are_joined_without_slug_or_traversal(self):
        self.assertEqual(reader_module.report_relative(r'E:\run\artifacts\case-id\attachments\copy.json', 'E:/run/artifacts'),
                         'case-id/attachments/copy.json')
        with self.assertRaises(ValueError):
            reader_module.report_relative(r'E:\run\other\copy.json', 'E:/run/artifacts')
        with self.assertRaises(ValueError):
            reader_module.report_relative(r'E:\run\artifacts\case-id\..\other\copy.json', 'E:/run/artifacts')


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining])
