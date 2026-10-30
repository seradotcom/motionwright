"""Synthetic transport/clock/source controls, never native or oracle evidence."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import zipfile

TOOLING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLING))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_fixedtxt_workflow as route
import native_fixedtxt_g2_admission as fixed
import native_fixedtxt_g2_supervisor as supervisor
from test_native_fixedtxt_external_g2 import external_fixture


def inline_namespace(marker):
    import ast
    import textwrap
    workflow = (TOOLING.parent / '.github/workflows/native-mlt-sequence-e2e.yml').read_text()
    code = textwrap.dedent(workflow.split("<<'" + marker + "'\n", 1)[1]
                          .split('\n          ' + marker, 1)[0])
    parsed = ast.parse(code)
    if marker == 'PY_DELIVERY':
        assert isinstance(parsed.body[-1], ast.Expr) and parsed.body[-1].value.func.id == 'main'
        parsed.body.pop()
    namespace = {'__name__': 'synthetic_inline_control'}
    return parsed, namespace


def source_request(current, plan):
    import native_film180_evidence_selection as selection
    rows = selection.fixedtxt_required_members(plan['fixture'], fixed.NATIVE_SLOT)
    limits = {row['role']: row['maximum'] for row in plan['members']
              if row['role'] in set(rows.values())}
    def original(member, digest='2' * 64):
        return {'member': member, 'bytes': 10, 'sha256': digest}
    return {'schema': 'motionwright.native.fixedtxt-workflow-request/1',
        'source_sha': current['source_sha'], 'source_tree': 'e' * 40,
        'semwright_sha': current['semwright_sha'], 'semwright_tree': 'f' * 40,
        'arm': plan['arm'], 'fixture': plan['fixture'], 'purpose': 'candidate24',
        'admission_id': 'synthetic-source-test', 'limits': plan['limits'], 'attempt_minutes': 10,
        'preparation_seconds': 120, 'native_seconds': 120, 'upload_seconds': 30,
        'preparation_stream_bytes': 65536, 'review_capsule_bytes': 64 * 1024 * 1024,
        'role_limits': limits,
        'sources': {'artifact': {'artifact_id': 33, 'run_id': 22, 'run_attempt': 1,
                    'source_sha': 'b' * 40, 'zip_sha256': 'c' * 64},
            'template_film': original('film.original.json'), 'managed_project': original('project.original.json'),
            'caption_source': original('caption.original'), 'red_native': original('red-native.original', route.PAIR['RED']),
            'measurement_protocol': original('measurement.original.json'), 'project_files': []}}


def api_run(run_id, source, *, created='2026-10-30T01:00:00Z', status='in_progress'):
    return {'id': int(run_id), 'run_attempt': 1, 'event': 'workflow_dispatch', 'head_sha': source,
        'workflow_id': 111, 'path': route.WORKFLOW_PATH, 'created_at': created, 'status': status,
        'conclusion': 'success' if status == 'completed' else None,
        'repository': {'id': 12, 'full_name': route.REPO}, 'head_repository': {'id': 12},
        'actor': {'id': 45, 'type': 'User'}}


def api_artifact(run, *, artifact_id=67, name=None, archive=b'original archive'):
    return {'id': artifact_id, 'name': name, 'size_in_bytes': len(archive), 'expired': False,
        'digest': 'sha256:' + route.sha(archive), 'created_at': '2026-10-30T01:00:30Z',
        'workflow_run': {'id': run['id'], 'head_sha': run['head_sha'],
                         'repository_id': 12, 'head_repository_id': 12}}


class OriginalTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = external_fixture()

    def setUp(self):
        self.current, self.p, self.r, self.q, self.dispatch = self.fixture
        self.workflow = {'id': 111, 'path': route.WORKFLOW_PATH, 'state': 'active'}
        self.target = api_run(self.current['run_id'], self.current['source_sha'])
        self.delivery = api_run('45678', self.current['source_sha'], created='2026-10-30T01:01:00Z', status='completed')
        self.review = api_artifact(self.target, artifact_id=89,
            name='motionwright-fixedtxt-preparation-' + self.current['run_id'] + '-1')
        self.members = {
            'dispatch.original.json': self.dispatch,
            'target-run.original.json': route.canonical(self.target),
            'delivery-run.original.json': route.canonical({**self.delivery, 'status': 'in_progress', 'conclusion': None}),
            'workflow.original.json': route.canonical(self.workflow),
            'review-artifact.original.json': route.canonical(self.review),
            'handoff.original.json': route.canonical({
                'schema': 'motionwright.native.fixedtxt-workflow-delivery/1',
                'target_run_id': self.current['run_id'], 'delivery_run_id': str(self.delivery['id']),
                'source_sha': self.current['source_sha'], 'workflow_id': self.workflow['id'],
                'review_artifact_id': self.review['id'], 'review_artifact_sha256': self.review['digest'][7:],
                'dispatch_original': route.original_descriptor(self.dispatch)})}

    def accept(self, members=None):
        with mock.patch.dict(os.environ, {'RUNNER_TEMP': '/synthetic-private-runner'}):
            return route.validate_delivery_originals(self.members if members is None else members,
                current=self.current, target=self.target, delivery=self.delivery, workflow=self.workflow,
                review_artifact=self.review, held_plan=self.p, held_runtime=self.r, held_cycle=self.q)

    def packet(self, members=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
            for name, raw in (self.members if members is None else members).items():
                archive.writestr(name, raw)
        raw = stream.getvalue()
        item = api_artifact(self.delivery, name=route.artifact_name(self.current['run_id']), archive=raw)
        return raw, item

    def test_original_R28_bytes_cross_real_zip_reader_then_existing_admission(self):
        archive, item = self.packet()
        originals = route.delivery_members(archive, item=item)
        held = self.accept(originals)
        self.assertEqual((held.dispatch_raw, held.plan_raw, held.runtime_raw, held.cycle_raw),
                         (self.dispatch, self.p, self.r, self.q))
        self.assertLessEqual(len(archive), 65536)
        self.assertFalse(hasattr(held, 'native_success'))

    def test_duplicate_is_rejected_before_provenance_or_content_filtering(self):
        archive, selected = self.packet()
        poisoned = {**selected, 'id': selected['id'] + 1, 'expired': True, 'digest': 'not-a-digest'}
        with self.assertRaisesRegex(route.WorkflowRejected, 'not-unique'):
            route.select_delivery_artifact({'total_count': 2, 'artifacts': [selected, poisoned]}, self.current['run_id'])
        self.assertIsNone(route.select_delivery_artifact({'total_count': 0, 'artifacts': []}, self.current['run_id']))
        with self.assertRaises(route.WorkflowRejected):
            route.select_delivery_artifact({'total_count': 1, 'artifacts': []}, self.current['run_id'])

    def test_server_actor_type_number_source_workflow_attempt_and_repository_are_not_interchangeable(self):
        mutations = [lambda x: x['actor'].update(id=46), lambda x: x['actor'].update(type='Bot'),
            lambda x: x.update(head_sha='d' * 40), lambda x: x.update(workflow_id=112),
            lambda x: x.update(run_attempt=2), lambda x: x.update(run_attempt=True),
            lambda x: x.update(path='different.yml'), lambda x: x['head_repository'].update(id=13),
            lambda x: x.update(id=self.target['id']), lambda x: x.update(created_at='2026-10-29T23:59:59Z')]
        for change in mutations:
            with self.subTest(change=change):
                original = self.delivery
                self.delivery = copy.deepcopy(original)
                change(self.delivery)
                with self.assertRaises(route.WorkflowRejected):
                    self.accept()
                self.delivery = original

    def test_failed_delivery_closed_target_and_review_after_delivery_deny(self):
        original = self.delivery
        self.delivery = {**original, 'conclusion': 'failure'}
        with self.assertRaises(route.WorkflowRejected):
            self.accept()
        self.delivery = original
        self.target['status'] = 'completed'
        with self.assertRaises(route.WorkflowRejected):
            self.accept()
        self.target['status'] = 'in_progress'
        self.review['created_at'] = '2026-10-30T01:02:00Z'
        with self.assertRaises(route.WorkflowRejected):
            self.accept()

    def test_dispatch_p_r_q_and_review_original_changes_deny_without_replacement(self):
        for field in ('p', 'r', 'q'):
            original = getattr(self, field)
            setattr(self, field, original + b' ')
            with self.subTest(field=field), self.assertRaises(fixed.FixedTxtRejected):
                self.accept()
            setattr(self, field, original)
        changed = dict(self.members)
        changed['dispatch.original.json'] += b' '
        with self.assertRaises(route.WorkflowRejected):
            self.accept(changed)
        self.review['digest'] = 'sha256:' + '0' * 64
        with self.assertRaises(route.WorkflowRejected):
            self.accept()

    def test_original_zip_digest_extra_member_symlink_duplicate_and_total_bound(self):
        archive, item = self.packet()
        with self.assertRaises(route.WorkflowRejected):
            route.delivery_members(archive + b'x', item=item)
        extra = {**self.members, 'unreviewed.txt': b'x'}
        raw, metadata = self.packet(extra)
        with self.assertRaises(route.WorkflowRejected):
            route.delivery_members(raw, item=metadata)
        for duplicate, symlink in ((True, False), (False, True)):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as packet:
                for name, data in self.members.items():
                    info = zipfile.ZipInfo(name)
                    if symlink and name == 'dispatch.original.json':
                        info.create_system = 3
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    packet.writestr(info, data)
                if duplicate:
                    packet.writestr('dispatch.original.json', self.dispatch)
            raw = stream.getvalue()
            metadata = api_artifact(self.delivery, name=route.artifact_name(self.current['run_id']), archive=raw)
            with self.assertRaises(route.WorkflowRejected):
                route.delivery_members(raw, item=metadata)
        oversized = dict(self.members)
        oversized['dispatch.original.json'] = b'x' * 65536
        raw, metadata = self.packet(oversized)
        with self.assertRaises(route.WorkflowRejected):
            route.delivery_members(raw, item=metadata)

    def test_original_api_metadata_identity_ignores_links_but_not_expiry_digest_or_origin(self):
        _, item = self.packet()
        self.assertEqual(route.artifact_identity(item), route.artifact_identity({**item, 'url': 'ignored presentation link'}))
        for field, value in (('expired', True), ('digest', 'sha256:' + '3' * 64), ('id', item['id']+1)):
            self.assertNotEqual(route.artifact_identity(item), route.artifact_identity({**item, field: value}))


class OriginalBudgetAndPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.current, cls.p, cls.r, cls.q, cls.dispatch = external_fixture()

    def test_single_clock_preserves_early_start_and_upload_stays_inside_existing_custody(self):
        limits = {'attempt_seconds': 600, 'custody_seconds': 120}
        with mock.patch.object(supervisor.time, 'monotonic', return_value=250):
            clock = supervisor.AttemptClock(limits, started=100, upload_seconds=30)
            self.assertEqual((clock.started, clock.total, clock.work, clock.custody_end), (100,700,580,670))
            self.assertEqual(clock.reserve('work'),580)
            with self.assertRaises(supervisor.SupervisionRejected):
                clock.reserve('work')
        with mock.patch.object(supervisor.time, 'monotonic', return_value=590):
            self.assertEqual(clock.reserve('custody'),670)
        for start in (True, float('nan'), 251):
            with mock.patch.object(supervisor.time, 'monotonic', return_value=250):
                with self.assertRaises(supervisor.SupervisionRejected):
                    supervisor.AttemptClock(limits, started=start)

    def test_expired_original_start_cannot_be_replaced_with_a_fresh_attempt(self):
        with mock.patch.object(supervisor.time, 'monotonic', return_value=1000):
            clock = supervisor.AttemptClock({'attempt_seconds': 600, 'custody_seconds': 120}, started=100)
            with self.assertRaises(supervisor.SupervisionRejected):
                clock.reserve('work')
        request = source_request(self.current, fixed.strict(self.p, 512*1024))
        original = {'started_monotonic':100, 'deadline_monotonic':700}
        clock = route.PreparationClock(original, request)
        with mock.patch.object(route.time, 'monotonic', return_value=221):
            with self.assertRaises(route.WorkflowRejected):
                clock.reserve('prepare-00')

    def test_boot_id_context_and_original_request_are_bound_through_actual_no_follow_files(self):
        plan = fixed.strict(self.p,512*1024)
        request = source_request(self.current,plan)
        raw = route.canonical(request)
        with tempfile.TemporaryDirectory() as name:
            layout = fixed.owner_layout(current=self.current,runner_temp=name)
            root = Path(layout['control_root']); root.mkdir(mode=0o700)
            now = time.monotonic()
            clock = {'schema':'motionwright.native.fixedtxt-original-clock/1','context':self.current,
                'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'started_monotonic':now-10,'deadline_monotonic':now-10+600,'request_sha256':route.sha(raw)}
            altered_clock = route.canonical({**clock, 'started_monotonic':now})
            originals = {'clock.original.json':route.canonical(clock),'request.original.json':raw}
            for filename,data in originals.items():
                (root/filename).write_bytes(data)
                # Keep the negative mutation within the manifest's byte ceiling
                # even if its decimal monotonic representation is one digit longer.
                limit = max(len(data), len(altered_clock)) if filename == 'clock.original.json' else len(data)
                plan['members'].append({'root':'control','relative_path':filename,'role':'input-original',
                    'maximum':limit,'expected_sha256':route.sha(data)})
            observed, _ = route.admitted_original_clock(plan,current=self.current,runner_temp=name)
            self.assertEqual(observed,clock)
            (root/'clock.original.json').write_bytes(route.canonical({**clock,'started_monotonic':now}))
            with self.assertRaises(route.WorkflowRejected):
                route.admitted_original_clock(plan,current=self.current,runner_temp=name)
            (root/'clock.original.json').unlink()
            (root/'clock.original.json').symlink_to(root/'request.original.json')
            with self.assertRaises(Exception):
                route.admitted_original_clock(plan,current=self.current,runner_temp=name)

    def test_requests_keep_existing_roster_budgets_and_auxiliaries_separate(self):
        plan = fixed.strict(self.p,512*1024)
        request = source_request(self.current,plan)
        route.checked_request(route.canonical(request),current=self.current)
        for field,value in (('attempt_minutes',11),('native_seconds',500),('upload_seconds',120)):
            bad = {**request,field:value}
            with self.subTest(field=field), self.assertRaises(route.WorkflowRejected):
                route.checked_request(route.canonical(bad),current=self.current)
        bad = copy.deepcopy(request); bad['role_limits']['selected-rgba'] -= 4
        with self.assertRaises(route.WorkflowRejected):
            route.checked_request(route.canonical(bad),current=self.current)
        bad = {**request,'purpose':'auxiliary-calibration'}
        with self.assertRaises(route.WorkflowRejected):
            route.checked_request(route.canonical(bad),current=self.current)
        self.assertEqual(route.CALIBRATION_KEYS,('fixed-multirun-control','fixed-multirun','fixed-camera-scene'))

    def test_capacity_uses_every_original_and_existing_padding_files_and_envelope(self):
        from collections import Counter
        import native_film180_evidence_selection as selection
        request = source_request(self.current, fixed.strict(self.p, 512 * 1024))
        for fixture in selection.FIXEDTXT_KEYS:
            selected = {**request, 'fixture': fixture}
            capacity = route.preflight_request_capacity(selected)
            actual = Counter(selection.fixedtxt_required_members(fixture, fixed.NATIVE_SLOT).values())
            self.assertEqual(capacity['mandatory_role_counts'], dict(actual))
            self.assertEqual(capacity['declared_files'], sum(actual.values()) + 15)
            self.assertGreater(capacity['declared_capsule_bound'], capacity['declared_raw_bytes'])
            self.assertFalse(capacity['future_original_sizes_verified'])
            exact = copy.deepcopy(selected)
            exact['limits']['capsule_bytes'] = capacity['declared_capsule_bound']
            exact['limits']['files'] = capacity['declared_files']
            # This boundary control reduces the final cap from the original
            # request. Keep its review cap inside that same existing contract.
            exact['review_capsule_bytes'] = min(exact['review_capsule_bytes'], exact['limits']['capsule_bytes'])
            route.preflight_request_capacity(exact)
            for field in ('capsule_bytes', 'files'):
                too_small = copy.deepcopy(exact)
                too_small['limits'][field] -= 1
                with self.subTest(fixture=fixture, field=field), self.assertRaises(route.WorkflowRejected):
                    route.preflight_request_capacity(too_small)
        impossible = copy.deepcopy(request)
        impossible['role_limits']['native-png'] = 256 * 1024 * 1024
        with self.assertRaisesRegex(route.WorkflowRejected, 'preparation-declared-capsule-budget'):
            route.checked_request(route.canonical(impossible), current=self.current)

    def test_review_capsule_preflight_uses_real_packer_and_stops_bootstrap_before_setup(self):
        import ast
        request = source_request(self.current, fixed.strict(self.p, 512 * 1024))
        capacity = route.preflight_request_capacity(request)
        sizes = [49152, 65536, 16384, 512 * 1024, 65536, 1024 * 1024]
        sizes += [request['sources'][key]['bytes'] for key in
                  ('template_film', 'managed_project', 'caption_source', 'red_native', 'measurement_protocol')]
        sizes += [item['original']['bytes'] for item in request['sources']['project_files']]
        # The public preparation function contains the same 15 commands. No
        # command, provider or setup executes in this packer arithmetic control.
        tree = ast.parse((TOOLING / 'native_fixedtxt_workflow.py').read_text())
        preparation = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                           and node.name == 'preparation_commands')
        commands = next(node.value for node in preparation.body
                        if isinstance(node, ast.Return) and isinstance(node.value, ast.List))
        self.assertEqual(len(commands.elts), 15)
        sizes += [request['preparation_stream_bytes'], request['preparation_stream_bytes'], 0, 0, 0, 0] * len(commands.elts)
        sizes += [0, 0]  # original intake ZIP and its copy, not yet observed
        packed = fixed._pack_originals({f'original-{index:03}.bin': bytes(size)
                                       for index, size in enumerate(sizes)}, bytes(512 * 1024))
        self.assertEqual(capacity['review_declared_files'], len(sizes))
        self.assertEqual(capacity['review_declared_raw_bytes'], sum(sizes))
        self.assertEqual(capacity['review_declared_capsule_bound'], len(packed) + 8192 + 64 + 512 * 1024 + 8192)
        self.assertFalse(capacity['future_original_sizes_verified'])
        exact = {**request, 'review_capsule_bytes': capacity['review_declared_capsule_bound']}
        route.checked_request(route.canonical(exact), current=self.current)
        for limit in (1, 10240, capacity['review_declared_capsule_bound'] - 1):
            bad = {**request, 'review_capsule_bytes': limit}
            with self.subTest(limit=limit), self.assertRaisesRegex(route.WorkflowRejected, 'declared-review-capsule-budget'):
                route.checked_request(route.canonical(bad), current=self.current)
            code, namespace = inline_namespace('PY_CLOCK')
            environment = {'MW_FIXEDTXT_REQUEST': route.canonical(bad).decode(),
                'MW_FIXEDTXT_BOOTSTRAP': '{}', 'MW_FIXEDTXT_DELIVERY': '',
                'GITHUB_REPOSITORY': route.REPO, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_RUN_ID': self.current['run_id'],
                'GITHUB_SHA': self.current['source_sha'], 'EXPECTED_SOURCE_SHA': self.current['source_sha']}
            with mock.patch.dict(os.environ, environment, clear=True), \
                    mock.patch.object(Path, 'mkdir') as create, mock.patch.object(subprocess, 'run') as effect:
                with self.assertRaisesRegex(ValueError, 'declared-review-capsule-budget'):
                    exec(compile(code, '<actual-inline-review-budget-bootstrap>', 'exec'), namespace)
                create.assert_not_called(); effect.assert_not_called()

    def test_observed_source_zip_is_reserved_twice_before_copy_or_later_preparation(self):
        import native_mlt_g2_custody as custody
        import ci_private_evidence as private
        request = source_request(self.current, fixed.strict(self.p, 512 * 1024))
        capacity = route.preflight_request_capacity(request)
        request['review_capsule_bytes'] = capacity['review_declared_capsule_bound']
        archive = bytes(20000)  # inert own data; never accepted as a real ZIP
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {'RUNNER_TEMP': directory}):
            control = route.owned_layout(self.current)['control_root']; control.mkdir(mode=0o700)
            with mock.patch.object(custody, 'download_artifact', return_value=archive) as download, \
                    mock.patch.object(custody, 'zip_member') as extract, \
                    mock.patch.object(private, 'new_bytes') as copy, \
                    mock.patch.object(route, 'execute_preparation_commands') as setup:
                with self.assertRaisesRegex(route.WorkflowRejected, 'declared-review-capsule-budget'):
                    route.intake_sources(request, self.current)
                self.assertEqual(download.call_count, 1)
                extract.assert_not_called(); copy.assert_not_called(); setup.assert_not_called()
        expanded = {**request, 'review_capsule_bytes': 64 * 1024 * 1024}
        with_zip = route.preflight_request_capacity(expanded, source_zip_bytes=len(archive))
        self.assertEqual(with_zip['review_declared_raw_bytes'] - capacity['review_declared_raw_bytes'], 2 * len(archive))
        self.assertEqual(with_zip['source_zip_observed_bytes'], len(archive))

    def test_actual_bootstrap_rejects_impossible_capacity_before_any_directory_or_setup(self):
        request = source_request(self.current, fixed.strict(self.p, 512 * 1024))
        request['role_limits']['native-png'] = 256 * 1024 * 1024
        code, namespace = inline_namespace('PY_CLOCK')
        environment = {'MW_FIXEDTXT_REQUEST': route.canonical(request).decode(),
            'MW_FIXEDTXT_BOOTSTRAP': '{}', 'MW_FIXEDTXT_DELIVERY': '',
            'GITHUB_REPOSITORY': route.REPO, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
            'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_RUN_ID': self.current['run_id'],
            'GITHUB_SHA': self.current['source_sha'], 'EXPECTED_SOURCE_SHA': self.current['source_sha']}
        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.object(Path, 'mkdir') as create, mock.patch.object(subprocess, 'run') as effect:
            with self.assertRaisesRegex(ValueError, 'preparation-declared-capsule-budget'):
                exec(compile(code, '<actual-inline-capacity-bootstrap>', 'exec'), namespace)
            create.assert_not_called()
            effect.assert_not_called()
        workflow = (TOOLING.parent / '.github/workflows/native-mlt-sequence-e2e.yml').read_text()
        owner = workflow.split('  fixedtxt-original-owner:\n', 1)[1]
        source_check = owner.index('Bind the early capacity counts')
        self.assertLess(source_check, owner.index('toolchain: 1.98.1'))
        self.assertLess(source_check, owner.index('actions/setup-node'))
        self.assertLess(source_check, owner.index('sudo -n apt-get'))
        actual = hashlib.sha256((TOOLING / 'native_film180_evidence_selection.py').read_bytes()).hexdigest()
        self.assertIn(actual, owner)

    def test_original_plan_builder_keeps_all180_existing_supervision_and_exact_Q(self):
        original = fixed.strict(self.p,512*1024)
        request = source_request(self.current,original)
        source = {'files':{row['path']:row['sha256'] for row in original['source_bindings']}}
        known = {('inputs',fixed.CYCLE_MEMBER):self.q,('control','request.original.json'):route.canonical(request)}
        raw = route.make_plan(request,self.current,self.q,source,known=known)
        result = fixed.validate_plan(fixed.strict(raw,512*1024),current=self.current)
        self.assertEqual(sum(row['role']=='native-png' for row in result['members']),180)
        self.assertEqual(result['case_roster'],original['case_roster'])
        self.assertEqual(result['limits'],original['limits'])
        self.assertEqual([row['expected_sha256'] for row in result['members']
                          if row['relative_path']==fixed.CYCLE_MEMBER],[route.sha(self.q)])
        for row in result['members']:
            if row['relative_path']=='supervision/work.stdout':
                self.assertEqual((row['role'],row['maximum'],row['expected_sha256']),
                                 ('attempt-original',1024*1024+1,None))

    def test_native_children_cannot_receive_transport_or_runner_credentials(self):
        env = {'PATH':'/bin','GITHUB_SHA':'a'*40,'GH_TOKEN':'private','GITHUB_TOKEN':'private',
               'ACTIONS_RUNTIME_TOKEN':'private','ACTIONS_RESULTS_URL':'private','GH_HOST':'github.com',
               'MW_FIXEDTXT_REQUEST':'raw','RUNNER_TEMP':'/synthetic-private-runner'}
        result = route.native_environment(env,self.dispatch)
        self.assertNotIn('private',result.values())
        self.assertFalse(any('TOKEN' in key or key.startswith(('GH_','ACTIONS_')) for key in result))
        self.assertEqual(result['MW_MLT_G2_ADMISSION'].encode(),self.dispatch)
        self.assertEqual(result['MW_FIXEDTXT_WORKFLOW_ROUTE'],'original-r28-delivery/1')

    def test_failure_collection_retains_raw_streams_and_names_every_budget_omission(self):
        current=self.current
        request=source_request(current,fixed.strict(self.p,512*1024))
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ,{'RUNNER_TEMP':directory}):
            layout=route.owned_layout(current)
            layout['control_root'].mkdir(mode=0o700)
            layout['inputs_root'].mkdir(mode=0o700)
            (layout['control_root']/'clock.original.json').write_bytes(b'123')
            (layout['control_root']/'preparation').mkdir(mode=0o700)
            original=layout['control_root']/'preparation/prepare-00.stderr'
            original.write_bytes(b'original failure stderr')
            known,failures=route.collect_workflow_originals(current,request,partial=True)
            self.assertEqual(known[('control','preparation/prepare-00.stderr')],b'original failure stderr')
            self.assertIn({'root':'runtime_custody','reason':'ORIGINAL_ROOT_ABSENT'},failures)
            limited={**request,'review_capsule_bytes':1024*1024+4}
            kept,omitted=route.collect_workflow_originals(current,limited,partial=True)
            self.assertEqual(kept,{('control','clock.original.json'):b'123'})
            self.assertTrue(any(row.get('relative_path')=='preparation/prepare-00.stderr'
                                and row['reason']=='ORIGINAL_RESOURCE_BOUND' for row in omitted))
            self.assertEqual(original.read_bytes(),b'original failure stderr')

    def test_failed_installation_copies_privileged_originals_once_then_stops(self):
        request=source_request(self.current,fixed.strict(self.p,512*1024))
        phases=[]
        class MechanicalGroup:
            def __init__(self,*args): pass
            def close(self): pass
        def execute(argv,**kwargs):
            phase=kwargs['phase']; phases.append(phase)
            return {'original_complete':True,'exit_code':1 if phase=='prepare-12' else 0,
                    'overflow':False,'deadline_exceeded':False}
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ,{
                'RUNNER_TEMP':directory,'GITHUB_WORKSPACE':directory,'SEMWRIGHT_CHECKOUT':directory}):
            control=route.owned_layout(self.current)['control_root'];control.mkdir(mode=0o700)
            (control/'runtime-bootstrap.original.json').write_text('{}')
            commands=[['mechanical-source-command'] for _ in range(15)]
            clock={'started_monotonic':time.monotonic(),'deadline_monotonic':time.monotonic()+600}
            with mock.patch.object(route,'preparation_commands',return_value=commands), \
                    mock.patch.object(supervisor,'OwnerCgroup',MechanicalGroup), \
                    mock.patch.object(supervisor,'_run_original',side_effect=execute):
                with self.assertRaises(route.WorkflowRejected):
                    route.execute_preparation_commands(request,self.current,clock)
            self.assertEqual(phases,['prepare-%02d'%number for number in range(14)])
            self.assertNotIn('prepare-14',phases)
            timeline=route.PreparationClock({'started_monotonic':100,'deadline_monotonic':700},request)
            with mock.patch.object(route.time,'monotonic',return_value=300):
                self.assertEqual(timeline.reserve('prepare-13'),670)
                with self.assertRaises(route.WorkflowRejected):
                    timeline.reserve('prepare-14')

    def test_runtime_preparation_is_still_the_existing_external_runtime_contract(self):
        import native_mlt_g2_custody as custody
        full = fixed.strict(self.dispatch,512*1024)
        base = {key:value for key,value in full.items() if key!='fixedtxt'}
        base['schema']=custody.FILM_DISPATCH_SCHEMA
        self.assertEqual(custody.runtime_preparation_dispatch(route.canonical(base),self.current,scenario=route.SCOPE),base)
        with self.assertRaises(Exception):
            custody.validate_dispatch(route.canonical(base),self.current,scenario=route.SCOPE)
        with self.assertRaises(Exception):
            custody.begin({'MW_MLT_SCOPE':route.SCOPE})


class WorkflowDeliveryJobTests(unittest.TestCase):
    def test_courier_kernel_bounds_both_streams_before_child_exec(self):
        import resource
        code, namespace = inline_namespace('PY_DELIVERY')
        exec(compile(code, '<actual-inline-delivery-file-limit>', 'exec'), namespace)
        before = resource.getrlimit(resource.RLIMIT_FSIZE)
        limit = namespace['api_stream_file_limit']()
        for fd, stream in ((1, 'stdout'), (2, 'stderr')):
            with self.subTest(stream=stream), tempfile.TemporaryDirectory() as directory:
                paths = {name: Path(directory) / name for name in ('stdout', 'stderr')}
                child = ('import os\n'
                         'os.write(' + str(fd) + ', b"x" * ' + str(limit + 1) + ')\n'
                         'os.write(' + str(fd) + ', b"y")\n')
                with paths['stdout'].open('xb') as output, paths['stderr'].open('xb') as error:
                    result = subprocess.run([sys.executable, '-B', '-E', '-s', '-c', child],
                        stdin=subprocess.DEVNULL, stdout=output, stderr=error, timeout=5,
                        preexec_fn=namespace['limit_api_stream_files'], check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(paths[stream].stat().st_size, limit)
                self.assertTrue(all(path.stat().st_size <= limit for path in paths.values()))
        self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE), before)

    def test_courier_ceiling_even_with_zero_exit_preserves_bytes_and_emits_no_artifact(self):
        code, namespace = inline_namespace('PY_DELIVERY')
        exec(compile(code, '<actual-inline-delivery-ceiling>', 'exec'), namespace)
        current, _, _, _, dispatch = external_fixture()
        for stream in ('stdout', 'stderr'):
            with self.subTest(stream=stream), tempfile.TemporaryDirectory() as directory:
                calls = []
                maximum = namespace['api_stream_file_limit']()
                def saturated(command, **kwargs):
                    calls.append(command)
                    self.assertIs(kwargs['preexec_fn'], namespace['limit_api_stream_files'])
                    self.assertGreater(kwargs['timeout'], 0)
                    self.assertLessEqual(kwargs['timeout'], 30)
                    kwargs['stdout'].write(b'{}' + b' ' * (maximum - 2) if stream == 'stdout' else b'{}')
                    if stream == 'stderr':
                        kwargs['stderr'].write(b'x' * maximum)
                    return type('Completed', (), {'returncode': 0})()
                environment = {'GITHUB_REPOSITORY': route.REPO, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                    'GITHUB_RUN_ATTEMPT': '1', 'EXPECTED_SOURCE_SHA': current['source_sha'],
                    'GITHUB_SHA': current['source_sha'], 'GITHUB_RUN_ID': '45678',
                    'RUNNER_TEMP': directory, 'GITHUB_OUTPUT': directory + '/outputs',
                    'GH_TOKEN': 'synthetic-never-used-on-network', 'REQUESTED_SCOPE': route.SCOPE,
                    'MW_FIXEDTXT_EXTERNAL_DISPATCH': dispatch.decode(), 'MW_FIXEDTXT_REQUEST': '',
                    'MW_FIXEDTXT_DELIVERY': route.canonical({'target_run_id': current['run_id'],
                        'review_artifact_id': 89, 'review_artifact_sha256': '4' * 64}).decode()}
                with mock.patch.dict(os.environ, environment, clear=True), \
                        mock.patch.object(namespace['subprocess'], 'run', side_effect=saturated):
                    with self.assertRaisesRegex(ValueError, 'original-stream-limit-reached'):
                        namespace['main']()
                root = Path(directory) / 'mw-fixedtxt-r28-delivery-45678-1'
                self.assertEqual((root / ('workflow.' + stream)).stat().st_size, maximum)
                self.assertEqual(len(calls), 1)
                self.assertFalse((root / 'artifact').exists())
                self.assertFalse((Path(directory) / 'outputs').exists())

    def test_actual_inline_job_uses_only_GET_and_keeps_originals_without_checkout_or_effects(self):
        import ast
        import textwrap
        workflow = (TOOLING.parent / '.github/workflows/native-mlt-sequence-e2e.yml').read_text()
        delivery = re.search(r'(?ms)^  fixedtxt-r28-original-delivery:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)',workflow).group(1)
        self.assertEqual(delivery.count('uses:'),1)
        self.assertIn('uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a',delivery)
        self.assertNotIn('actions/checkout',delivery)
        self.assertNotIn('cargo ',delivery)
        self.assertNotIn('apt-get',delivery)
        self.assertNotIn('native_fixedtxt_g2_supervisor.py',delivery)
        self.assertIn("inputs.fixedtxt_mode != 'deliver' && (github.event_name == 'pull_request'",workflow)
        self.assertIn("inputs.fixedtxt_mode != 'deliver' && (inputs.verification_scope == 'normal'",workflow)
        self.assertIn("format('native-fixedtxt-delivery-{0}', github.run_id)",workflow)
        code = textwrap.dedent(delivery.split("<<'PY_DELIVERY'\n",1)[1].split('\n          PY_DELIVERY',1)[0])
        parsed = ast.parse(code)
        self.assertIsInstance(parsed.body[-1],ast.Expr)
        self.assertEqual(parsed.body[-1].value.func.id,'main')
        parsed.body.pop()
        namespace = {'__name__':'synthetic_delivery_source_test'}
        exec(compile(parsed,'<exact-source-inline-delivery>','exec'),namespace)
        current,p,r,q,dispatch = external_fixture()
        workflow_value = {'id':111,'path':route.WORKFLOW_PATH,'state':'active'}
        target = api_run(current['run_id'],current['source_sha'])
        delivery_run = api_run('45678',current['source_sha'],created='2026-10-30T01:01:00Z')
        review = api_artifact(target,artifact_id=89,name='motionwright-fixedtxt-preparation-'+current['run_id']+'-1')
        prefix='repos/'+route.REPO+'/actions/'
        responses={route.WORKFLOW_ENDPOINT:route.canonical(workflow_value),
                   prefix+'runs/'+current['run_id']:route.canonical(target),
                   prefix+'runs/45678':route.canonical(delivery_run),
                   prefix+'artifacts/89':route.canonical(review)}
        calls=[]
        def read(command,**kwargs):
            self.assertEqual(command[:4],['/usr/bin/gh','api','--method','GET'])
            self.assertIs(kwargs['preexec_fn'], namespace['limit_api_stream_files'])
            calls.append(command[4])
            kwargs['stdout'].write(responses[command[4]])
            return type('Completed',(),{'returncode':0})()
        with tempfile.TemporaryDirectory() as directory:
            environment={'GITHUB_REPOSITORY':route.REPO,'GITHUB_EVENT_NAME':'workflow_dispatch',
                'GITHUB_RUN_ATTEMPT':'1','EXPECTED_SOURCE_SHA':current['source_sha'],'GITHUB_SHA':current['source_sha'],
                'GITHUB_RUN_ID':'45678','RUNNER_TEMP':directory,'GITHUB_OUTPUT':directory+'/outputs',
                'GH_TOKEN':'synthetic-never-used-on-network','MW_FIXEDTXT_EXTERNAL_DISPATCH':dispatch.decode(),
                'REQUESTED_SCOPE':route.SCOPE,'MW_FIXEDTXT_REQUEST':'',
                'MW_FIXEDTXT_DELIVERY':route.canonical({'target_run_id':current['run_id'],
                    'review_artifact_id':89,'review_artifact_sha256':review['digest'][7:]}).decode()}
            with mock.patch.dict(os.environ,environment,clear=True), mock.patch.object(namespace['subprocess'],'run',side_effect=read):
                namespace['main']()
                with self.assertRaises(FileExistsError):
                    namespace['main']()
            output=Path(directory)/'mw-fixedtxt-r28-delivery-45678-1/artifact'
            members={path.name:path.read_bytes() for path in output.iterdir()}
            self.assertEqual(set(members),{'dispatch.original.json','target-run.original.json',
                'delivery-run.original.json','workflow.original.json','review-artifact.original.json','handoff.original.json'})
            self.assertEqual(members['dispatch.original.json'],dispatch)
            self.assertEqual(members['target-run.original.json'],responses[prefix+'runs/'+current['run_id']])
            self.assertEqual(len(calls),4)
            self.assertLessEqual(sum(map(len,members.values())),60*1024)
            with mock.patch.dict(os.environ,{'RUNNER_TEMP':'/synthetic-private-runner'}):
                held=route.validate_delivery_originals(members,current=current,target=target,
                    delivery={**delivery_run,'status':'completed','conclusion':'success'},workflow=workflow_value,
                    review_artifact=review,held_plan=p,held_runtime=r,held_cycle=q)
            self.assertEqual(held.dispatch_raw,dispatch)

    def test_wait_consumes_one_artifact_and_second_invocation_cannot_restart_transport(self):
        current,p,r,q,dispatch=external_fixture()
        template=OriginalTransportTests()
        template.fixture=(current,p,r,q,dispatch)
        template.setUp()
        archive,item=template.packet()
        routes=[]
        def get(api,route_name,**kwargs):
            routes.append(route_name)
            if route_name==route.WORKFLOW_ENDPOINT:
                return route.canonical(template.workflow)
            if '/actions/artifacts?name=' in route_name:
                return route.canonical({'total_count':1,'artifacts':[item]})
            if route_name.endswith('/runs/'+current['run_id']):
                return route.canonical(template.target)
            if route_name.endswith('/runs/'+str(template.delivery['id'])):
                return route.canonical(template.delivery)
            if route_name.endswith('/artifacts/'+str(item['id'])+'/zip'):
                return archive
            if route_name.endswith('/artifacts/'+str(item['id'])):
                return route.canonical(item)
            if route_name.endswith('/artifacts/'+str(template.review['id'])):
                return route.canonical(template.review)
            raise AssertionError('unexpected API read')
        request=source_request(current,fixed.strict(p,512*1024))
        now=time.monotonic()
        clock={'started_monotonic':now-30,'deadline_monotonic':now+570}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(route.ReadOnlyApi,'get',new=get), \
                mock.patch.dict(os.environ,{'RUNNER_TEMP':'/synthetic-private-runner'}):
            held=route.wait_for_owner(current=current,request=request,clock=clock,directory=directory,
                originals={'plan':p,'runtime':r,'cycle':q},
                review={'artifact_id':template.review['id'],'artifact_sha256':template.review['digest'][7:]})
            self.assertEqual(held.dispatch_raw,dispatch)
            self.assertEqual((Path(directory)/'transport.original.zip').read_bytes(),archive)
            self.assertEqual((Path(directory)/'dispatch.original.json').read_bytes(),dispatch)
            self.assertTrue((Path(directory)/'transport.consume.intent.json').is_file())
            first_count=len(routes)
            with self.assertRaises(FileExistsError):
                route.wait_for_owner(current=current,request=request,clock=clock,directory=directory,
                    originals={'plan':p,'runtime':r,'cycle':q},
                    review={'artifact_id':template.review['id'],'artifact_sha256':template.review['digest'][7:]})
            self.assertEqual(len(routes),first_count)


if __name__ == '__main__':
    unittest.main()
