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
import os
from pathlib import Path
import shutil
import stat
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
    writable_bootstrap = False
    wrong_witness_on = 0

    def __init__(self, binary, database, output, timeout):
        self.process = None
        self.sequence = 0
        self.taints = []
        self.command = [str(binary), '--database', str(database), '--read-only']
        self.database = Path(database)
        self.witness_count = 0
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
            return {'test_transport': 'service-test-jsonl', 'read_only': not self.writable_bootstrap}
        if command == 'opened_store_identity_witness':
            self.witness_count += 1
            if self.witness_count == self.wrong_witness_on:
                return 'f' * 64
            info = self.database.stat()
            return hashlib.sha256(f'linux:{os.major(info.st_dev)}:{os.minor(info.st_dev)}:{info.st_ino}'.encode()).hexdigest()
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
        FakeReader.writable_bootstrap = False
        FakeReader.wrong_witness_on = 0

    def run_main(self, fail_names=(), mutate_report=None, omit_flags=(), extra_flags=()):
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
        args.extend(extra_flags)
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

    def assert_executed_once(self, physical=False):
        self.assertEqual(len(FakeReader.instances), 1)
        expected = ['bootstrap', 'reopen', 'get_project', 'project_history', 'get_project']
        if physical:
            expected = ['bootstrap', 'opened_store_identity_witness', 'reopen', 'opened_store_identity_witness',
                        'get_project', 'project_history', 'get_project', 'opened_store_identity_witness']
        self.assertEqual(FakeReader.instances[0].calls, expected)
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



@unittest.skipUnless(sys.platform == 'linux', 'Original Linux inode preflight runs only on Linux')
class PhysicalOriginalRootTests(RuntimeFixture):
    """Source-only FakeReader tests; they neither create nor re-admit a real CAS Store."""

    def make_physical_source(self, *, invalid_root_identity=False, wrong_source_path=False):
        target = self.results / 'case-one'
        source = target / 'service'
        blobs = source / 'blobs'
        blobs.mkdir(parents=True)
        root_id = '01a1a823-b250-7157-94b2-46f874da5dfe'
        control = source / ('.motionwright-cas-' + root_id)
        control.mkdir()
        lock = control / 'root.lock'
        lock.touch()
        (source / 'studio.sqlite3').write_bytes((target / 'closed-store' / 'store' / 'studio.sqlite3').read_bytes())
        token = lambda path: f"unix:{path.stat().st_dev}:{path.stat().st_ino}"
        anchor = {'format_version': 1, 'root_id': root_id, 'canonical_root': str(blobs.resolve()),
                  'control': str(control.resolve()), 'directory_id': token(blobs),
                  'control_id': token(control), 'lock_id': token(lock)}
        if invalid_root_identity:
            anchor['directory_id'] = 'unix:0:0'
        dump(blobs / '.motionwright-cas-root-v1.json', anchor)
        store = target / 'closed-store' / 'store'
        shutil.copytree(source, store, dirs_exist_ok=True)
        manifest_path = target / 'closed-store' / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['entries'] = reader_module.tree_entries(store)
        manifest['sourceDirectory'] = str(source) if not wrong_source_path else str(source) + '-wrong'
        manifest['snapshotDirectory'] = str(store)
        physical_entries = []
        for item in reader_module.tree_entries(source):
            info = (source / item['path']).lstat()
            physical_entries.append({'path': item['path'], 'kind': item['kind'],
                                     'dev': str(info.st_dev), 'ino': str(info.st_ino),
                                     'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid})
        info = (source / 'studio.sqlite3').stat()
        witness = hashlib.sha256(f'linux:{os.major(info.st_dev)}:{os.minor(info.st_dev)}:{info.st_ino}'.encode()).hexdigest()
        writer = {'helperId': 'SOURCE-ONLY-HELPER', 'processId': 313131,
                  'launchIndex': 0, 'requestId': 'SOURCE-ONLY-WITNESS'}
        manifest['physicalIdentity'] = {'schemaVersion': 1, 'platform': 'linux',
                                        'databaseWitnessSha256': witness, 'entries': physical_entries, 'writer': writer}
        manifest['readOnlyHandoff'] = True
        manifest['readOnlyHandoffReceipt'] = {**writer, 'requestId': 'SOURCE-ONLY-HANDOFF',
                                             'response': {'read_only_handoff': True, 'journal_mode': 'delete'}}
        manifest['lifecycle'][0]['exits'][0]['processId'] = 313131
        digest = reader_module.digest_json(manifest['entries'])
        for key in ('sourceBeforeSha256', 'sourceAfterSha256', 'snapshotSha256'):
            manifest[key] = digest
        dump(manifest_path, manifest)
        outcomes_path = target / 'closed-store-outcomes.json'
        outcomes = json.loads(outcomes_path.read_text())
        outcomes['capture'] = manifest
        outcomes['closures'][0]['exits'][0]['processId'] = 313131
        closure_path = target / 'closed-store-service-closure.json'
        dump(closure_path, {'schema': 'motionwright.service-closure/1', 'status': 'CLOSED',
             'directory': str(source), 'database_path': str(source / 'studio.sqlite3'),
             'sealed_helper_ids': ['SOURCE-ONLY-HELPER'], 'participants': [{
                 'helper_id': 'SOURCE-ONLY-HELPER', 'database_path': str(source / 'studio.sqlite3'),
                 'binary_path': 'mock-only', 'launch_count': 1, 'close_status': 'fulfilled',
                 'active_child_count': 0, 'starting_count': 0, 'closing_count': 0, 'pending_count': 0, 'taints': [],
                 'process_streams': [{'launch_index': 0, 'process_id': 313131, 'exit_index': 0,
                                     'stdout_ended': True, 'stderr_ended': True, 'jsonl_reader_closed': True}],
                 'process_exits': [{'process_id': 313131, 'code': 0, 'signal': None,
                                    'observed_at': '2026-10-26T00:00:00Z'}]}]})
        outcomes['serviceClosure'] = {'closed': True, 'receipt_path': str(closure_path),
                                      'receipt_sha256': reader_module.sha256(closure_path)}
        dump(outcomes_path, outcomes)
        attachment = target / 'attachments' / 'closed-store-outcomes-exact-copy.json'
        attachment.write_bytes(outcomes_path.read_bytes())
        evidence_path = target / 'production-design-service-evidence.json'
        evidence = json.loads(evidence_path.read_text())
        service = evidence['services'][0]
        service['databasePath'] = str(source / 'studio.sqlite3')
        service['processExits'][0]['processId'] = 313131
        service['transcript'] = [
            {'processId': 313131, 'direction': 'request',
             'payload': {'id': 'SOURCE-ONLY-HANDOFF', 'command': 'prepare_read_only_handoff'}},
            {'processId': 313131, 'direction': 'response',
             'payload': {'id': 'SOURCE-ONLY-HANDOFF', 'ok': True,
                         'result': {'read_only_handoff': True, 'journal_mode': 'delete'}}},
            {'processId': 313131, 'direction': 'request',
             'payload': {'id': 'SOURCE-ONLY-WITNESS', 'command': 'opened_store_identity_witness'}},
            {'processId': 313131, 'direction': 'response',
             'payload': {'id': 'SOURCE-ONLY-WITNESS', 'ok': True, 'result': witness}},
        ]
        dump(evidence_path, evidence)
        (target / 'attachments' / 'production-design-service-evidence-exact-copy.json').write_bytes(evidence_path.read_bytes())
        return source

    def rewrite_manifest(self, change):
        target = self.results / 'case-one'
        path = target / 'closed-store' / 'manifest.json'
        value = json.loads(path.read_text())
        change(value)
        dump(path, value)
        outcome_path = target / 'closed-store-outcomes.json'
        outcomes = json.loads(outcome_path.read_text())
        outcomes['capture'] = value
        dump(outcome_path, outcomes)
        (target / 'attachments' / 'closed-store-outcomes-exact-copy.json').write_bytes(outcome_path.read_bytes())

    def rewrite_evidence(self, change):
        target = self.results / 'case-one'
        path = target / 'production-design-service-evidence.json'
        value = json.loads(path.read_text())
        change(value)
        dump(path, value)
        (target / 'attachments' / 'production-design-service-evidence-exact-copy.json').write_bytes(path.read_bytes())

    def test_fresh_reader_uses_real_original_physical_store_not_a_copy(self):
        source = self.make_physical_source()
        code, raised, report, _, _ = self.run_main(extra_flags=('--physical-original-store',))
        self.assertIsNone(raised); self.assertEqual(code, 0, report)
        self.assert_executed_once(physical=True)
        self.assertEqual(FakeReader.instances[0].command[2], str(source / 'studio.sqlite3'))
        self.assertEqual(report['cases'][0]['readback']['readbackMode'], 'original_physical_store')
        self.assertEqual(report['cases'][0]['readback']['physicalSourceBeforeSha256'],
                         report['cases'][0]['readback']['physicalSourceAfterSha256'])
        self.assertEqual(list((self.output / 'readback-copies').glob('*/store')), [])
        case = report['cases'][0]
        observation = case['readback']
        self.assertTrue(observation['readOnly'])
        self.assertEqual(observation['physicalIdentityBeforeSha256'], case['originalPhysicalIdentitySha256'])
        self.assertEqual(observation['physicalIdentityAfterSha256'], case['originalPhysicalIdentitySha256'])
        self.assertEqual(observation['databaseIdentityWitnesses'], {
            stage: case['originalDatabaseWitnessSha256'] for stage in ('beforeReopen', 'afterReopen', 'afterReadback')})
        self.assertEqual(case['originalReadOnlyHandoff']['processId'], 313131)
        self.assertEqual(observation['rpcCount'], 8)

    def test_writer_mode_response_is_rejected_after_one_rpc_without_retry(self):
        self.make_physical_source()
        FakeReader.writable_bootstrap = True
        code, raised, report, _, _ = self.run_main(extra_flags=('--physical-original-store',))
        self.assertIsNone(raised); self.assertEqual(code, 1)
        self.assertEqual(FakeReader.instances[0].calls, ['bootstrap'])
        self.assertEqual(FakeReader.instances[0].closes, 1)
        self.assertFalse(report['cases'][0]['readback']['readOnly'])
        self.assertIn('read-only service transport', report['cases'][0]['readback']['primaryFailure'])

    def test_changed_held_database_witness_is_rejected_at_each_observation(self):
        self.make_physical_source()
        for ordinal, stage in enumerate(('beforeReopen', 'afterReopen', 'afterReadback'), 1):
            with self.subTest(stage=stage):
                self.output = self.base / ('witness-' + str(ordinal))
                FakeReader.instances = []
                FakeReader.wrong_witness_on = ordinal
                code, raised, report, _, _ = self.run_main(extra_flags=('--physical-original-store',))
                self.assertIsNone(raised); self.assertEqual(code, 1)
                self.assertEqual(len(FakeReader.instances), 1); self.assertEqual(FakeReader.instances[0].closes, 1)
                result = report['cases'][0]['readback']
                self.assertEqual(result['databaseIdentityWitnesses'][stage], 'f' * 64)
                self.assertIn('held-descriptor witness differs at ' + stage, result['primaryFailure'])
                self.assertEqual(result['rpcCount'], (2, 4, 8)[ordinal - 1])

    def test_physical_permissions_change_during_reader_is_rejected_after_clean_close(self):
        source = self.make_physical_source()
        original_close = FakeReader.close
        def changed(instance):
            result = original_close(instance)
            database = source / 'studio.sqlite3'
            database.chmod(stat.S_IMODE(database.stat().st_mode) ^ 0o040)
            return result
        with mock.patch.object(FakeReader, 'close', changed):
            code, raised, report, _, _ = self.run_main(extra_flags=('--physical-original-store',))
        self.assertIsNone(raised); self.assertEqual(code, 1)
        self.assert_executed_once(physical=True)
        self.assertTrue(report['cases'][0]['readback']['close']['clean'])
        self.assertIn('physical identity', ' '.join(report['cases'][0]['readback']['errors']))

    def test_original_handoff_response_from_another_process_is_rejected(self):
        self.make_physical_source()
        self.rewrite_evidence(lambda value: value['services'][0]['transcript'][1].update(processId=999999))
        code, raised, report, _, _ = self.run_main(extra_flags=('--physical-original-store',))
        self.assertIsNone(raised); self.assertEqual(code, 1); self.assertEqual(FakeReader.instances, [])
        self.assertIn('no unique correlated response', ' '.join(report['cases'][0]['errors']))

    def test_unknown_original_handoff_is_rejected_without_transport(self):
        self.make_physical_source()
        def unknown(value):
            value['services'][0]['transcript'][1]['payload'] = {
                'id': 'SOURCE-ONLY-HANDOFF', 'ok': False, 'error': 'UNKNOWN_SOURCE_ONLY_FIXTURE'}
        self.rewrite_evidence(unknown)
        code, raised, report, _, _ = self.run_main(extra_flags=('--physical-original-store',))
        self.assertIsNone(raised); self.assertEqual(code, 1); self.assertEqual(FakeReader.instances, [])
        self.assertIn('did not succeed; no retry', ' '.join(report['cases'][0]['errors']))

    def test_handoff_marker_without_original_rpc_proof_is_rejected(self):
        self.make_physical_source()
        self.rewrite_evidence(lambda value: value['services'][0].update(transcript=[]))
        code, raised, report, _, _ = self.run_main(extra_flags=('--physical-original-store',))
        self.assertIsNone(raised); self.assertEqual(code, 1); self.assertEqual(FakeReader.instances, [])
        self.assertIn('Exactly one original writer handoff', ' '.join(report['cases'][0]['errors']))

    def test_byte_identical_database_replacement_is_rejected_before_transport(self):
        source = self.make_physical_source()
        database = source / 'studio.sqlite3'
        original = self.base / 'retained-inert-original'
        database.rename(original)
        shutil.copy2(original, database)
        self.assertEqual(reader_module.sha256(database), reader_module.sha256(original))
        self.assertNotEqual(database.stat().st_ino, original.stat().st_ino)
        code, raised, report, _, _ = self.run_main(extra_flags=('--physical-original-store',))
        self.assertIsNone(raised); self.assertEqual(code, 1, report)
        self.assertEqual(FakeReader.instances, [])
        self.assertIn('physical identity', ' '.join(report['errors']))

    def test_permissions_change_is_rejected_before_transport(self):
        source = self.make_physical_source()
        database = source / 'studio.sqlite3'
        database.chmod(stat.S_IMODE(database.stat().st_mode) ^ 0o040)
        code, raised, report, _, _ = self.run_main(extra_flags=('--physical-original-store',))
        self.assertIsNone(raised); self.assertEqual(code, 1, report)
        self.assertEqual(FakeReader.instances, [])
        self.assertIn('physical identity', ' '.join(report['errors']))

    def test_real_transport_argv_requires_read_only_mode(self):
        transport = reader_module.JsonlReader(self.binary, self.base / 'studio.sqlite3', self.output, 10)
        self.assertIn('--read-only', transport.command)
        self.assertNotIn('--desktop-invoke-fixture', transport.command)

    def test_copied_directory_with_a_new_inode_is_rejected_before_transport(self):
        source = self.make_physical_source()
        preserved = self.base / 'original-moved-away'
        source.rename(preserved)  # Physical original remains valid, but is not the target path.
        shutil.copytree(preserved, source)  # Exact bytes, different directory identities.
        code, raised, report, _, _ = self.run_main(extra_flags=('--physical-original-store',))
        self.assertIsNone(raised); self.assertEqual(code, 1)
        self.assertEqual(FakeReader.instances, [])
        self.assertFalse(report['transportExecuted'])
        self.assertIn('physical identity', ' '.join(report['errors']))

    def test_missing_original_physical_store_blocks_transport(self):
        source = self.make_physical_source()
        shutil.rmtree(source)
        code, raised, report, _, _ = self.run_main(extra_flags=('--physical-original-store',))
        self.assertIsNone(raised); self.assertEqual(code, 1)
        self.assertEqual(FakeReader.instances, [])
        self.assertIn('missing or redirected', ' '.join(report['errors']))

    def test_forged_root_identity_blocks_transport(self):
        self.make_physical_source(invalid_root_identity=True)
        code, raised, report, _, _ = self.run_main(extra_flags=('--physical-original-store',))
        self.assertIsNone(raised); self.assertEqual(code, 1)
        self.assertEqual(FakeReader.instances, [])
        self.assertIn('physical identity', ' '.join(report['errors']))

    def test_unbound_source_path_blocks_transport(self):
        self.make_physical_source(wrong_source_path=True)
        code, raised, report, _, _ = self.run_main(extra_flags=('--physical-original-store',))
        self.assertIsNone(raised); self.assertEqual(code, 1)
        self.assertEqual(FakeReader.instances, [])
        self.assertIn('Original writer closure does not bind this Store', ' '.join(report['cases'][0]['errors']))

    def test_physical_mode_without_executed_transport_is_rejected(self):
        self.make_physical_source()
        code, raised, report, _, _ = self.run_main(omit_flags=('--execute-readback',),
                                                    extra_flags=('--physical-original-store',))
        self.assertIsInstance(raised, ValueError)
        self.assertIsNone(code)  # CLI rejects the invalid mode before writing any report.
        self.assertIsNone(report)
        self.assertEqual(FakeReader.instances, [])


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining])
