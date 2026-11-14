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
    return {'schema': 'motionwright.native.fixedtxt-workflow-request/2',
        'owner_resources': copy.deepcopy(route.OWNER_RESOURCE_POLICY),
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
            'caption_source': original('caption.original'), 'red_native': original('red-native.original', route.HISTORICAL_PAIR['RED']),
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

    def write_control_originals(self, control, request):
        """Create genuine-shape standalone clock/request originals for focused source tests."""
        request_raw = route.canonical(request)
        started = time.monotonic() - 1
        clock = {
            'schema': 'motionwright.native.fixedtxt-original-clock/1',
            'context': self.current,
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'started_monotonic': started,
            'deadline_monotonic': started + request['limits']['attempt_seconds'],
            'request_sha256': route.sha(request_raw),
        }
        (control / 'request.original.json').write_bytes(request_raw)
        (control / 'clock.original.json').write_bytes(route.canonical(clock))
        return clock

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
                'started_monotonic':now-10,
                'deadline_monotonic':now-10+request['limits']['attempt_seconds'],
                'request_sha256':route.sha(raw)}
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
        for label in ('historical-request-v1', 'absent-policy', 'larger-policy'):
            invalid = copy.deepcopy(request)
            if label == 'historical-request-v1':
                invalid['schema'] = 'motionwright.native.fixedtxt-workflow-request/1'
            elif label == 'absent-policy':
                del invalid['owner_resources']
            else:
                invalid['owner_resources']['pids_max'] += 1
            with self.subTest(label=label), self.assertRaises(route.WorkflowRejected):
                route.checked_request(route.canonical(invalid), current=self.current)
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
            # Six separately bounded pre-owner diagnostics supplement the original budget.
            self.assertEqual(capacity['declared_files'], sum(actual.values()) + 15 + 3 + 5 + 6)
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
        sizes += [128 * 1024, 128 * 1024, 16384]  # Original/derived observer custody.
        sizes += [65536] * 5  # Real active resource-prefix maxima, no future terminal.
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
        envelope = 8192 + 64 + 512 * 1024 + 8192
        # Five separately reserved resource records include worst-case padding;
        # their real USTAR packet must fit, with at most one extra record block.
        self.assertGreaterEqual(capacity['review_declared_capsule_bound'], len(packed) + envelope)
        self.assertLessEqual(capacity['review_declared_capsule_bound'], len(packed) + envelope + 10240)
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
            self.write_control_originals(control, request)
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
        self.assertLess(source_check, owner.index('--action bootstrap'))
        actual = hashlib.sha256((TOOLING / 'native_film180_evidence_selection.py').read_bytes()).hexdigest()
        self.assertIn(actual, owner)

    def test_original_plan_builder_keeps_all180_existing_supervision_and_exact_Q(self):
        original = fixed.strict(self.p,512*1024)
        request = source_request(self.current,original)
        source = {'files':{row['path']:row['sha256'] for row in original['source_bindings']}}
        known = {('inputs',fixed.CYCLE_MEMBER):self.q,('control','request.original.json'):route.canonical(request)}
        import native_fixedtxt_observer_overlay as overlay
        # This older structural fixture has no observed source bundle. Its
        # current-pair attempt must stay negative; it cannot certify custody.
        with self.assertRaisesRegex(overlay.OverlayRejected, 'observer_custody_original_missing'):
            route.make_plan(request, self.current, self.q, source, known=known)
        # Preserve the original historical structural control and its scope.
        with mock.patch.object(route, 'PAIR', route.HISTORICAL_PAIR):
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
            (layout['control_root']/'request.original.json').write_bytes(route.canonical(request))
            (layout['control_root']/'clock.original.json').write_bytes(b'123')
            (layout['control_root']/'preparation').mkdir(mode=0o700)
            original=layout['control_root']/'preparation/prepare-00.stderr'
            original.write_bytes(b'original failure stderr')
            known,failures=route.collect_workflow_originals(current,request,partial=True)
            self.assertEqual(known[('control','preparation/prepare-00.stderr')],b'original failure stderr')
            self.assertIn({'root':'runtime_custody','reason':'ORIGINAL_ROOT_ABSENT'},failures)
            observation = layout['observation_root']
            observation.mkdir(mode=0o700)
            case = observation / request['fixture']
            case.mkdir(mode=0o700)
            log = case / 'semwrightd.original.private.log'
            log.write_bytes(b'original daemon launch diagnostic')
            terminal_directory = case / 'native-result.fixed-text'
            terminal_directory.mkdir(mode=0o700)
            terminal = terminal_directory / 'render-terminal.original-value.json'
            terminal_raw = b'original render terminal failure'
            terminal.write_bytes(terminal_raw)
            terminal_name = request['fixture'] + '/native-result.fixed-text/' + terminal.name
            unrelated = case / 'unselected-private-file'; unrelated.write_bytes(b'not admitted by this selection')
            extra, extra_failures = route.collect_workflow_originals(current, request, partial=True)
            self.assertEqual(extra[('owner_diagnostics', request['fixture'] + '/' + log.name)],
                             b'original daemon launch diagnostic')
            self.assertEqual(extra[('owner_diagnostics', terminal_name)], terminal_raw)
            self.assertNotIn(('owner_diagnostics', 'original-attempt-error.json'), extra)
            self.assertFalse(any(name.endswith(unrelated.name) for _, name in extra))
            # Exchange the real parent after its leaf stat, before its open.
            # Observe the actual opened fd as well as the eventual rejection.
            diagnostic_name = request['fixture'] + '/' + log.name
            original_log_info = log.stat()
            original_log_identity = (
                original_log_info.st_dev, original_log_info.st_ino)
            outside = Path(directory) / 'outside-observation'
            outside.mkdir(mode=0o700)
            foreign_log = outside / log.name
            foreign_log.write_bytes(b'X' * original_log_info.st_size)
            foreign_log_info = foreign_log.stat()
            foreign_log_identity = (
                foreign_log_info.st_dev, foreign_log_info.st_ino)
            self.assertNotEqual(
                original_log_identity, foreign_log_identity)
            held_case = observation / (request['fixture'] + '-held')
            watched_code = route._read_owner_diagnostic.__code__
            race = {'renamed': False, 'swaps': 0, 'opened': []}

            def exchange_diagnostic_parent(frame, event, _argument):
                if frame.f_code is not watched_code:
                    return None
                if event == 'line':
                    local = frame.f_locals
                    if local.get('name') == diagnostic_name:
                        if (not race['renamed']
                                and 'before_name' in local
                                and 'parents' in local
                                and local.get('fd') == -1):
                            case.rename(held_case)
                            race['renamed'] = True
                            case.symlink_to(
                                outside, target_is_directory=True)
                            race['swaps'] += 1
                        elif (race['renamed'] and not race['opened']
                                and type(local.get('fd')) is int
                                and local['fd'] >= 0):
                            opened_info = os.fstat(local['fd'])
                            race['opened'].append((
                                opened_info.st_dev, opened_info.st_ino))
                return exchange_diagnostic_parent

            previous_trace = sys.gettrace()
            try:
                sys.settrace(exchange_diagnostic_parent)
                raced, raced_failures = route.collect_workflow_originals(
                    current, request, partial=True)
            finally:
                sys.settrace(previous_trace)
                if race['renamed']:
                    if case.is_symlink():
                        case.unlink()
                    held_case.rename(case)
            self.assertEqual(race['swaps'], 1)
            self.assertEqual(
                race['opened'], [original_log_identity])
            self.assertNotIn(
                foreign_log_identity, race['opened'])
            self.assertNotIn(
                ('owner_diagnostics', diagnostic_name), raced)
            self.assertIn({
                'root': 'owner_diagnostics',
                'relative_path': diagnostic_name,
                'reason': 'WorkflowRejected',
            }, raced_failures)
            self.assertEqual(
                raced[('control', 'preparation/prepare-00.stderr')],
                b'original failure stderr')
            self.assertEqual(
                (log.stat().st_dev, log.stat().st_ino),
                original_log_identity)
            original_error = case / 'original-attempt-error.json'
            original_error.write_bytes(b'{"error":"original failure"}')
            extra, _ = route.collect_workflow_originals(current, request, partial=True)
            self.assertEqual(extra[('owner_diagnostics', request['fixture'] + '/' + original_error.name)],
                             original_error.read_bytes())
            terminal.write_bytes(b'x' * (8 * 1024 * 1024 + 1))
            extra, omitted_terminal = route.collect_workflow_originals(current, request, partial=True)
            self.assertNotIn(('owner_diagnostics', terminal_name), extra)
            self.assertIn({'root': 'owner_diagnostics', 'relative_path': terminal_name,
                           'reason': 'ORIGINAL_RESOURCE_BOUND'}, omitted_terminal)
            terminal.write_bytes(terminal_raw)
            log.write_bytes(b'x' * (1024 * 1024 + 1))
            extra, omitted_log = route.collect_workflow_originals(current, request, partial=True)
            self.assertNotIn(('owner_diagnostics', request['fixture'] + '/' + log.name), extra)
            self.assertIn({'root':'owner_diagnostics', 'relative_path':request['fixture'] + '/' + log.name,
                           'reason':'ORIGINAL_RESOURCE_BOUND'}, omitted_log)
            log.unlink(); log.symlink_to(original)
            extra, refused_link = route.collect_workflow_originals(current, request, partial=True)
            self.assertNotIn(('owner_diagnostics', request['fixture'] + '/' + log.name), extra)
            self.assertTrue(any(row['root'] == 'owner_diagnostics' for row in refused_link))
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
                    'overflow':False,'deadline_exceeded':False,
                    'native_outcome':'NOT_INFERRED'}
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ,{
                'RUNNER_TEMP':directory,'GITHUB_WORKSPACE':directory,'SEMWRIGHT_CHECKOUT':directory}):
            control=route.owned_layout(self.current)['control_root'];control.mkdir(mode=0o700)
            self.write_control_originals(control, request)
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
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
                'RUNNER_TEMP':directory, 'GITHUB_WORKSPACE':directory, 'SEMWRIGHT_CHECKOUT':directory}):
            control = route.owned_layout(self.current)['control_root']; control.mkdir(mode=0o700)
            (control / 'runtime-bootstrap.original.json').write_bytes(route.canonical(base))
            commands = route.preparation_commands(source_request(self.current, fixed.strict(self.p,512*1024)), self.current)
            daemon = [command for command in commands if 'semwright-daemon' in command]
            self.assertEqual(len(daemon), 1)
            self.assertIn('--locked', daemon[0])
            self.assertEqual(daemon[0][daemon[0].index('--features') + 1],
                             'semwright-daemon/private-host-stdin-custody')
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

    def test_external_review_polling_is_paced_under_original_clock_and_journal_limit(self):
        # This checks the real transport pacing rule, without issuing a GET,
        # manufacturing an R28 receipt or running any Native workload.
        self.assertEqual(route.API_JOURNAL_MAX, 1024 * 1024)
        self.assertEqual(route.DELIVERY_MAX, 65536)
        self.assertEqual(route.DELIVERY_POLL_SECONDS, 20.0)
        now = time.monotonic()
        self.assertEqual(route.delivery_poll_delay(now + 200), 20.0)
        near = route.delivery_poll_delay(time.monotonic() + 0.25)
        self.assertGreater(near, 0)
        self.assertLessEqual(near, 0.25)
        self.assertEqual(route.delivery_poll_delay(time.monotonic() - 1), 0.0)
        import inspect
        actual = inspect.getsource(route.wait_for_owner)
        self.assertEqual(actual.count('time.sleep(delivery_poll_delay(wait_until))'), 2)
        self.assertNotIn('time.sleep(min(1,', actual)

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


class FixedTxtSourceKitPinTests(unittest.TestCase):
    def test_current_repository_admits_the_pinned_factory(self):
        import fixedtxt_source_kit as kit
        root = TOOLING.parent
        lock = json.loads((root / 'SOURCE_LOCK.json').read_bytes())
        selected = lock['dependencies']['semwright']
        self.assertEqual(kit.selected_pins(), selected['verification']['evidence']['path'])
        factory = (root / kit.FACTORY).read_bytes()
        self.assertEqual(hashlib.sha256(factory).hexdigest(), kit.FACTORY_SHA)
        declared = re.search(rb'const EXPECTED_SDK: &str = "([0-9a-f]{40})";', factory)
        self.assertIsNotNone(declared)
        self.assertEqual(declared.group(1).decode(), selected['revision'])
        template = re.search(rb'const TEMPLATE_SHA256: &str =\s*"([0-9a-f]{64})";', factory)
        self.assertIsNotNone(template)
        self.assertEqual(template.group(1).decode(), kit.TEMPLATE_SHA)

        # Producer transport and independent reader select the same exact
        # observer derivations. These identities do not grant any execution.
        import native_fixedtxt_observer_overlay as overlay
        rust = (root / 'crates/native/examples/native-film180-e2e.rs').read_text()
        block = rust.split('const OBSERVED_NATIVE_SOURCES:', 1)[1].split('];', 1)[0]
        pins = dict(re.findall(r'\(\s*"(RED|GREEN)",\s*"([0-9a-f]{64})",?\s*\)', block))
        self.assertEqual(pins, overlay.OBSERVED)

        # Cross-language producer/selector contract. Derive the filename from
        # the actual Python argv, Rust command route, retained directory and
        # terminal write, so matching fixture/selector mistakes cannot pass.
        import ast
        caller = ast.parse((root / 'tooling/native_film180_original_g2.py').read_text())
        one_case = next(node for node in caller.body
                        if isinstance(node, ast.FunctionDef) and node.name == 'fixed_one_case')
        assignments = [node.value for node in ast.walk(one_case)
                       if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'evidence'
                               for target in node.targets)]
        self.assertEqual(len(assignments), 1)
        evidence = assignments[0]
        self.assertIsInstance(evidence, ast.BinOp)
        self.assertIsInstance(evidence.op, ast.Div)
        self.assertEqual(ast.unparse(evidence.left), 'attempt.evidence_dir')
        evidence_name = ast.literal_eval(evidence.right)
        commands = [node for node in ast.walk(one_case)
                    if isinstance(node, ast.Call) and ast.unparse(node.func) == 'attempt.command'
                    and len(node.args) > 1 and isinstance(node.args[1], ast.List)
                    and node.args[1].elts
                    and isinstance(node.args[1].elts[0], ast.Constant)
                    and node.args[1].elts[0].value == 'render-fixed-text-v4']
        self.assertEqual(len(commands), 1)
        self.assertEqual([ast.unparse(arg) for arg in commands[0].args[1].elts[1:]],
                         ['str(database)', 'str(connection)', 'str(custody_dir)',
                          'str(case_path)', 'str(evidence)'])
        command_arm = rust.split('"render-fixed-text-v4" => {', 1)[1].split('"stage-audio" =>', 1)[0]
        self.assertEqual(re.findall(r'let (\w+) = path_arg\(&mut args\);', command_arm),
                         ['database', 'connection', 'custody', 'case', 'evidence'])
        self.assertIn('fixed_text_v4::run(&database, &connection, &custody, &case, &evidence).await?;',
                      command_arm)
        module = rust.split('mod fixed_text_v4 {', 1)[1].split('\n}\n', 1)[0]
        run = module.split('pub(super) async fn run(', 1)[1]
        directory = re.search(r'let retained = evidence\.with_extension\("([^"]+)"\);', run)
        self.assertIsNotNone(directory)
        self.assertIn('.dispatch(&coordinator, &original, &request, &retained)', run)
        dispatch = module.split('async fn dispatch(', 1)[1].split('pub(super) async fn run(', 1)[0]
        self.assertIn('retained: &Path,', dispatch.split(') ->', 1)[0])
        terminal = re.search(r'stdin_readback::new_json\(\s*&retained\.join\("([^"]+)"\),'
                             r'\s*&status,\s*8 \* 1024 \* 1024,', dispatch)
        self.assertIsNotNone(terminal)
        relative = (Path(evidence_name).with_suffix('.' + directory.group(1))
                    / terminal.group(1)).as_posix()
        fixture = 'fixed-multirun-control'
        self.assertEqual(route._owner_diagnostic_limits(fixture)[fixture + '/' + relative],
                         8 * 1024 * 1024)

        # The actual first-attempt source kit emits its direct Cargo root at
        # target/debug, and its six exact externs never gain arbitrary paths.
        package_id = "root-canvas"
        manifest = str(root / "Cargo.toml")
        target = root / "synthetic-target"  # No Rust build or file creation.
        packages = [{
            "id": package_id, "name": "semwright-driver-motion-canvas",
            "source": kit.SDK_SOURCE, "manifest_path": manifest,
        }]
        deps = []
        entries = [(kit.EXTERNS[0], package_id)]
        for name in kit.EXTERNS[1:]:
            dep_id = "dep-" + name
            packages.append({
                "id": dep_id, "name": name.replace("_", "-"),
                "source": kit.SDK_SOURCE if name.startswith("semwright_") else None,
                "manifest_path": manifest,
            })
            deps.append({"name": name, "pkg": dep_id,
                         "dep_kinds": [{"kind": None, "target": None}]})
            entries.append((name, dep_id))
        metadata = {"packages": packages, "resolve": {"nodes": [
            {"id": package_id, "deps": deps},
        ]}}
        messages = [{"reason": "build-finished", "success": True}]
        for name, pid in entries:
            directory = target / ("debug" if name == kit.EXTERNS[0] else "debug/deps")
            messages.append({
                "reason": "compiler-artifact", "package_id": pid,
                "manifest_path": manifest, "target": {"name": name, "kind": ["lib"]},
                "profile": {"test": False},
                "filenames": [str(directory / ("lib" + name + ".rlib"))],
            })
        with mock.patch.object(kit.custody, "fingerprint",
                               return_value={"verified": "synthetic-selector-only"}):
            driver, libraries, native_paths = kit.cargo_libraries(
                metadata, messages, target)
            self.assertEqual(driver["id"], package_id)
            self.assertEqual(set(libraries), set(kit.EXTERNS))
            self.assertEqual(native_paths, [])
            self.assertEqual(
                Path(libraries[kit.EXTERNS[0]]["path"]).parent, target / "debug")
            for name in kit.EXTERNS[1:]:
                self.assertEqual(Path(libraries[name]["path"]).parent,
                                 target / "debug/deps")
            # An arbitrary direct-root/dependency swap still fails closed.
            for index, parent in ((1, target / "debug/deps"),
                                  (2, target / "debug")):
                mutated = copy.deepcopy(messages)
                member = mutated[index]["filenames"][0]
                mutated[index]["filenames"] = [str(parent / Path(member).name)]
                with self.subTest(index=index), self.assertRaisesRegex(
                        ValueError, "rlib is not in the one fresh target"):
                    kit.cargo_libraries(metadata, mutated, target)

    def test_previous_consumer_pin_is_refused_by_current_source(self):
        import fixedtxt_source_kit as kit
        previous = '0a904fd2d7feff806607d6786b9a3575f716cae2'
        with mock.patch.object(kit, 'SDK', previous), \
                mock.patch.object(kit, 'SDK_SOURCE',
                    'git+https://github.com/seradotcom/semwright?rev=' + previous + '#' + previous):
            with self.assertRaisesRegex(ValueError, 'requires the exact selected SDK'):
                kit.selected_pins()



def failed_output_fixture():
    """Bound synthetic originals only; no renderer, grant or Native result."""
    from native_film180_evidence_selection import fixedtxt_frames
    current = {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
               "run_id": "34567", "run_attempt": "1"}
    fixture = "fixed-multirun-control"
    request = {"fixture": fixture, "arm": "RED"}
    helper = b"// Synthetic source control: never executed.\n"
    runtime = {"semwright_revision": current["semwright_sha"], "driver_sha256": "3" * 64,
        "node_sha256": "4" * 64, "native_source_sha256": {"RED": "5" * 64},
        "render_helper": {"path": "/unfollowed/helper", **route.original_descriptor(helper)},
        "dependency_lock": {"path": "/unfollowed/lock", "bytes": 100, "sha256": "6" * 64}}
    cycle = {"schema": "motionwright.fixed-text-native-cycle-inputs/4", "arm": "RED", "runtime": runtime}
    cycle_raw = route.canonical(cycle)
    plan = {"schema": fixed.PLAN_SCHEMA, "context": current, "arm": "RED", "fixture": fixture,
        "cycle_input_sha256": route.sha(cycle_raw),
        "source_bindings": [{"path": path, "sha256": "c" * 64} for path in fixed.source_paths()],
        "arm_sources": {"RED": {"source_sha": current["semwright_sha"], "native_source_sha256": "5" * 64}}}
    compiled = {"id": "separate-motion-project", "generation": "d" * 32, "revision": 3}
    compiled_raw = route.canonical(compiled)
    studio = {"id": "11111111-1111-4111-8111-111111111111",
              "generation": "22222222-2222-4222-8222-222222222222", "revision": 29}
    job = "mc1:" + compiled["id"] + ":" + compiled["generation"] + ":3:" + "e" * 64 + ":render_job:render-" + "f" * 32
    config = {"authoring": True, "name": "frames", "width": 1920, "height": 1080,
        "fpsNum": 30, "fpsDen": 1, "firstFrame": 0, "endFrameExclusive": 180,
        "textPixelObservationFrames": list(fixedtxt_frames(fixture)),
        "renderInputDigest": "1" * 64, "fontResourcesDigest": "2" * 64, "dependencyLockDigest": "6" * 64}
    stdin = b"globalThis.__SEMWRIGHT_RENDER_INPUT__ = " + route.canonical(config).rstrip(b"\n") + b";\n" + helper
    provider = {"id": "driver:motion-canvas", "kind": "driver", "version": "synthetic"}
    provenance = {"provider": provider["id"], "source": provider["kind"], "provider_version": provider["version"]}
    start = {"provenance_checked": True, "execution": {"provenance": provenance,
             "dry_run": False, "backend": "driver:motion-canvas"},
             "data": {"job_ref": job, "state": "starting"}}
    terminal = {"replayed": False, "result": {"provenance_checked": True,
        "execution": {"provenance": provenance, "dry_run": False, "backend": "driver:motion-canvas"},
        "data": {"job_ref": job, "state": "failed", "host_failure": {"kind": "timeout", "outcome_known": False}}}}
    capture = {"provider": provider, "provider_process_id": 123, "tool": "motion-node",
        "job_id": "distinct-host-job", "request_id": "host-request", "parent_request_id": "broker-parent",
        "session_id": "owner-session", "stdin_bytes": len(stdin), "stdin_sha256": route.sha(stdin),
        "terminal_file_required": True}
    pipe = {"write_state": "write_all_completed", "partial_delivery_possible": False,
        "io_error_kind": None, "raw_os_error": None, "child_consumption": "not_observed",
        "native_render_success": "not_inferred"}
    receipt = {"project_id": studio["id"], "generation": studio["generation"], "revision": studio["revision"],
        "request_id": "fixed-text-v4-red-" + fixture + ":motion:0:render-start", "stage": "completed",
        "command": "driver.motion-canvas.render.start",
        "payload": {"broker_request_id": capture["parent_request_id"], "result": start}}
    terminal["receipt"] = {"project_id": studio["id"], "generation": studio["generation"],
        "revision": studio["revision"], "request_id": "fixed-text-v4-red-" + fixture + ":motion:0:render-status-6",
        "stage": "completed", "command": "driver.motion-canvas.render.status",
        "payload": {"job_ref": job, "result": terminal["result"]}}
    rows = {
        "owner-workspace.json": {"schema": "synthetic-placeholder-not-an-owner"},
        "saved-production-project.json": studio,
        "fixed-text-case.json": {"schema": "motionwright.private-fixed-text-render-case/4",
            "arm": "RED", "key": fixture, "runtime": runtime,
            "compiled_project": {"path": "/unfollowed/compiled", **route.original_descriptor(compiled_raw)}},
        "canonical-component/evidence/project.derived.json": compiled,
        "native-result.fixed-text/render-terminal.original-value.json": terminal,
        "native-result.host-stdin/broker-receipt.snapshot.json": {
            "schema": "motionwright.private-production-receipt-snapshot/1",
            "source": "existing StudioService receipt", "wire_bytes": False, "lookup_error": None,
            "snapshot_grants_authority": False, "receipt": receipt},
        "native-result.host-stdin/session.original.json": {"schema": "semwright.host-stdin-custody/1",
            "provider": provider, "tool": "motion-node", "driver_sha256": runtime["driver_sha256"],
            "tool_sha256": runtime["node_sha256"]},
        "native-result.host-stdin/capture-0001.json": capture,
        "native-result.host-stdin/capture-0001.terminal.json": pipe,
        "native-result.host-stdin/readback.observation.json": {
            "schema": "motionwright.private-host-stdin-readback/1", "native_success_returned": False,
            "native_return_snapshot_retained": True, "original_identity_unchanged": True,
            "new_capture_ordinals_retained": [1], "capture_read_errors": [], "automatic_retry": False,
            "selected_capture_join": {"matched": True, "ordinal": 1, "capture": capture, "terminal": pipe,
                "pipe_write_all_completed": True, "child_consumption": "not_observed",
                "native_render_success": "not_inferred",
                "joined_to": "original completed render-start Broker parent and persisted session",
                "Host_job_id_equals_MC_job_ref_claimed": False}},
    }
    originals = {fixture + "/" + name: route.canonical(value) for name, value in rows.items()}
    originals[fixture + "/native-result.host-stdin/capture-0001.stdin"] = stdin
    return current, request, originals, route.canonical(plan), cycle_raw


class FailedOutputCustodyTests(unittest.TestCase):
    def setUp(self):
        self.current, self.request, self.originals, self.plan, self.cycle = failed_output_fixture()
        self.fixture = self.request["fixture"]

    def join(self, originals=None, plan=None, cycle=None):
        return route._failed_render_join(self.current, self.request,
            self.originals if originals is None else originals,
            self.plan if plan is None else plan, self.cycle if cycle is None else cycle)

    def test_negative_join_preserves_distinct_projects_host_job_and_unknown(self):
        joined = self.join()
        self.assertNotEqual(joined["studio_project"]["id"], joined["motion_canvas_project"]["id"])
        self.assertNotEqual(joined["studio_project"]["generation"], joined["motion_canvas_project"]["generation"])
        self.assertNotEqual(joined["host"]["job_id"], joined["motion_canvas_job_ref"])
        self.assertEqual(joined["directory"], "render-" + "f" * 32)
        self.assertEqual(joined["native_state"], "failed")
        self.assertIs(joined["host_outcome_known"], False)
        self.assertIs(joined["Host_job_id_equals_MC_job_ref_claimed"], False)
        # No success result, output artifact, or fabricated host-binding exists.
        with self.assertRaises((KeyError, ValueError)):
            fixed.resolve_native_directory({"fixture": self.fixture},
                {"observation/" + name: raw for name, raw in self.originals.items()}, cycle_raw=self.cycle)

    def test_negative_join_rejects_foreign_source_project_parent_session_and_input(self):
        changes = [
            ("saved-production-project.json", lambda v: v.update(id="33333333-3333-4333-8333-333333333333")),
            ("canonical-component/evidence/project.derived.json", lambda v: v.update(id="foreign-motion-project")),
            ("native-result.host-stdin/broker-receipt.snapshot.json",
                lambda v: v["receipt"]["payload"].update(broker_request_id="foreign-parent")),
            ("native-result.host-stdin/broker-receipt.snapshot.json",
                lambda v: v["receipt"]["payload"]["result"]["data"].update(job_ref="foreign-job")),
            ("native-result.host-stdin/session.original.json", lambda v: v.update(driver_sha256="0"*64)),
            ("native-result.host-stdin/capture-0001.json", lambda v: v.update(session_id="foreign-session")),
            ("native-result.host-stdin/readback.observation.json", lambda v: v.update(original_identity_unchanged=False)),
            ("native-result.host-stdin/readback.observation.json", lambda v: v.update(native_success_returned=True)),
            ("native-result.host-stdin/capture-0001.terminal.json", lambda v: v.update(write_state="interrupted")),
            ("native-result.fixed-text/render-terminal.original-value.json",
                lambda v: v["result"]["data"]["host_failure"].update(outcome_known=True)),
            ("native-result.fixed-text/render-terminal.original-value.json",
                lambda v: v["result"]["data"].update(job_ref="mc1:x:y:1:z:render_job:../elsewhere")),
            ("native-result.fixed-text/render-terminal.original-value.json",
                lambda v: v["result"]["execution"]["provenance"].update(provider_version="foreign-provider")),
            ("native-result.fixed-text/render-terminal.original-value.json",
                lambda v: v["receipt"].update(command="driver.motion-canvas.render.start")),
            ("native-result.fixed-text/render-terminal.original-value.json",
                lambda v: v["receipt"].update(project_id="foreign-studio")),
            ("native-result.fixed-text/render-terminal.original-value.json",
                lambda v: v["receipt"].update(request_id="fixed-text-v4-red-fixed-multirun-control:motion:0:render-status-331")),
            ("native-result.fixed-text/render-terminal.original-value.json",
                lambda v: v["receipt"]["payload"].update(job_ref="foreign-job")),
            ("native-result.fixed-text/render-terminal.original-value.json",
                lambda v: v["receipt"]["payload"].update(result={})),
            ("native-result.host-stdin/broker-receipt.snapshot.json",
                lambda v: v["receipt"]["payload"]["result"]["execution"].update(dry_run=True)),
        ]
        for name, change in changes:
            with self.subTest(original=name):
                values = dict(self.originals); key = self.fixture + "/" + name
                value = json.loads(values[key]); change(value); values[key] = route.canonical(value)
                with self.assertRaises(route.WorkflowRejected):
                    self.join(values)
        plan = json.loads(self.plan); plan["context"]["source_sha"] = "9" * 40
        with self.assertRaisesRegex(route.WorkflowRejected, "source-binding"):
            self.join(plan=route.canonical(plan))
        bad = dict(self.originals); key=self.fixture + "/native-result.host-stdin/capture-0001.stdin"
        bad[key] = bad[key].replace(b'"width":1920', b'"width":1080')
        with self.assertRaisesRegex(route.WorkflowRejected, "stdin"):
            self.join(bad)
        with self.assertRaisesRegex(route.WorkflowRejected, "source-binding"):
            self.join(cycle=self.cycle + b" ")

    def test_negative_diagnostics_keep_existing_source_roster_and_exact_gate(self):
        import ast
        source = (TOOLING / "native_fixedtxt_workflow.py").read_text()
        self.assertIn("tooling/native_fixedtxt_workflow.py", fixed.source_paths())
        self.assertNotIn("tooling/native_fixedtxt_failed_output.py", fixed.source_paths())
        self.assertEqual(route.FAILED_OUTPUT_RECEIPT_MAX, 65536)
        self.assertEqual(route.FAILED_OUTPUT_METADATA_MAX, 65536)
        workflow = (TOOLING.parent / ".github/workflows/native-mlt-sequence-e2e.yml").read_text()
        tree = ast.parse(Path(__file__).read_text())
        count = sum(item.name.startswith("test_") for cls in tree.body if isinstance(cls, ast.ClassDef)
                    for item in cls.body if isinstance(item, ast.FunctionDef))
        self.assertEqual(count, 34)
        self.assertIn("'dotted:tooling.tests.test_native_fixedtxt_workflow':34", workflow)
        self.assertIn("--pattern test_native_fixedtxt_workflow.py", workflow)
        self.assertIn("      - 'tooling/native_fixedtxt_workflow.py'", workflow)
        self.assertIn("      - 'tooling/tests/test_native_fixedtxt_workflow.py'", workflow)
        collector = next(node for node in ast.parse(source).body
                         if isinstance(node, ast.FunctionDef) and node.name == "collect_workflow_originals")
        self.assertIn("_collect_failed_output", ast.unparse(collector))
        self.assertNotIn("native-png", route._failed_render_input_limits(self.fixture))

    @unittest.skipUnless(os.name == "posix", "Linux openat custody; no Windows substitute")
    def test_physical_inventory_preserves_receipt_and_never_opens_png_bytes(self):
        import native_evidence_projection as core
        joined = self.join()
        with tempfile.TemporaryDirectory() as temporary:
            owner = Path(temporary); owner.chmod(0o700)
            output = owner / "output" / joined["directory"]; output.mkdir(parents=True, mode=0o700)
            frames = output / "frames"; frames.mkdir(mode=0o700)
            for frame in range(180):
                (frames / f"{frame:06d}.png").write_bytes(b"synthetic metadata-only leaf")
            receipt = route.canonical({"version": 2, "ok": False,
                "render_input_digest": joined["render_input_digest"], "error_class": "render_wait_timeout",
                "local_stack_only": "synthetic local diagnostic"})
            (output / "native-failure-receipt.json").write_bytes(receipt)
            other = owner / "output" / ("render-" + "0" * 32); other.mkdir(mode=0o700)
            (other / "private-unselected").write_bytes(b"outside selected render")
            audit = {"active": False, "opens": []}
            def observe(event, args):
                if audit["active"] and event == "open" and isinstance(args[0], str):
                    audit["opens"].append(args[0])
            sys.addaudithook(observe)
            handles = core.RootHandles({"owner": str(owner)})
            try:
                audit["active"] = True
                inventory, raw = route._observe_failed_output(handles, joined, 65536)
            finally:
                audit["active"] = False
                handles.close()
            self.assertEqual(raw, receipt)
            self.assertEqual(len(inventory["frames"]), 180)
            self.assertEqual(inventory["receipt_status"], "original_observed")
            self.assertFalse(any(name.endswith(".png") or name == "private-unselected" for name in audit["opens"]))
            self.assertLess(len(route.canonical(inventory)), route.FAILED_OUTPUT_METADATA_MAX)
            self.assertNotIn("local_stack_only", inventory)

    @unittest.skipUnless(os.name == "posix", "Linux openat custody; no Windows substitute")
    def test_physical_receipt_rejects_link_fifo_wrong_binding_and_oversize(self):
        import native_evidence_projection as core
        joined = self.join()
        with tempfile.TemporaryDirectory() as temporary:
            owner = Path(temporary); owner.chmod(0o700)
            output = owner / "output" / joined["directory"]; output.mkdir(parents=True, mode=0o700)
            receipt = output / "native-failure-receipt.json"
            outside = owner / "outside"; outside.write_bytes(b"unselected")
            def refused():
                handles=core.RootHandles({"owner":str(owner)})
                try:
                    with self.assertRaises((route.WorkflowRejected, OSError)):
                        route._observe_failed_output(handles, joined, 65536)
                finally:
                    handles.close()
            receipt.symlink_to(outside); refused(); receipt.unlink()
            os.link(outside,receipt); refused(); receipt.unlink()
            os.mkfifo(receipt); refused(); receipt.unlink()
            receipt.write_bytes(b"x" * 65537); refused(); receipt.unlink()
            receipt.write_bytes(route.canonical({"version":2,"ok":False,"render_input_digest":"0"*64}))
            refused(); receipt.unlink()
            frames=output/"frames"; frames.mkdir(mode=0o700)
            (frames/"000180.png").write_bytes(b"outside frame roster"); refused()

    @unittest.skipUnless(os.name == "posix", "Linux openat custody; no Windows substitute")
    def test_physical_parent_exchange_never_opens_foreign_receipt(self):
        import native_evidence_projection as core
        joined = self.join()
        with tempfile.TemporaryDirectory() as temporary:
            owner=Path(temporary); owner.chmod(0o700)
            output=owner/"output"/joined["directory"]; output.mkdir(parents=True,mode=0o700)
            raw=route.canonical({"version":2,"ok":False,"render_input_digest":joined["render_input_digest"]})
            leaf=output/"native-failure-receipt.json"; leaf.write_bytes(raw)
            original_identity=(leaf.stat().st_dev,leaf.stat().st_ino)
            foreign=owner/"foreign"; foreign.mkdir(mode=0o700)
            foreign_leaf=foreign/leaf.name; foreign_leaf.write_bytes(b"x"*len(raw))
            foreign_identity=(foreign_leaf.stat().st_dev,foreign_leaf.stat().st_ino)
            moved=output.with_name(output.name+"-held")
            state={"swapped":False,"opened":[]}
            watched=route._failed_output_receipt.__code__
            def exchange(frame,event,arg):
                if frame.f_code is not watched:
                    return None
                if event == "line":
                    local=frame.f_locals
                    if not state["swapped"] and "named" in local and local.get("fd")==-1:
                        output.rename(moved); output.symlink_to(foreign,target_is_directory=True)
                        state["swapped"]=True
                    elif state["swapped"] and not state["opened"] and local.get("fd",-1)>=0:
                        info=os.fstat(local["fd"]); state["opened"].append((info.st_dev,info.st_ino))
                return exchange
            handles=core.RootHandles({"owner":str(owner)})
            previous=sys.gettrace()
            try:
                sys.settrace(exchange)
                with self.assertRaisesRegex(route.WorkflowRejected,"mutated"):
                    route._observe_failed_output(handles,joined,65536)
            finally:
                sys.settrace(previous); handles.close()
                if state["swapped"]:
                    output.unlink(); moved.rename(output)
            self.assertTrue(state["swapped"])
            self.assertEqual(state["opened"],[original_identity])
            self.assertNotIn(foreign_identity,state["opened"])
            self.assertEqual(leaf.read_bytes(),raw)

    @unittest.skipUnless(os.name == "posix", "Linux openat custody; no Windows substitute")
    def test_collector_retains_absence_and_enforces_shared_budget_without_owner_search(self):
        import native_evidence_projection as core
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ,{"RUNNER_TEMP":temporary}):
            root=Path(temporary)
            owner=root/"motionwright-native-av-g2-synthetic"; owner.mkdir(mode=0o700)
            observation=root/"observation"; observation.mkdir(mode=0o700)
            info=owner.stat()
            originals=dict(self.originals)
            originals[self.fixture+"/owner-workspace.json"]=route.canonical({
                "schema":"motionwright.native.mlt-g2-owner-workspace/1","path":str(owner),
                "motionwright_sha":self.current["source_sha"],"semwright_sha":self.current["semwright_sha"],
                "run_id":self.current["run_id"],"run_attempt":"1",
                "device":info.st_dev,"inode":info.st_ino,"uid":info.st_uid,"gid":info.st_gid})
            for name,raw in originals.items():
                path=observation/name; path.parent.mkdir(parents=True,mode=0o700,exist_ok=True); path.write_bytes(raw)
            terminal=self.fixture+"/native-result.fixed-text/render-terminal.original-value.json"
            known={("owner_diagnostics",terminal):originals[terminal],
                ("control",fixed.PLAN_MEMBER):self.plan,("inputs",fixed.CYCLE_MEMBER):self.cycle}
            handles=core.RootHandles({"observation":str(observation)})
            try:
                extra, failures=route._collect_failed_output(handles,known,self.current,self.request,8*1024*1024,20)
                report=json.loads(extra[("failed_output",self.fixture+"/failed-output.observation.json")])
                self.assertEqual(failures,[])
                self.assertEqual(report["join_status"],"originals_joined")
                self.assertEqual(report["output_observation"]["directory_status"],"absent")
                self.assertEqual(report["output_observation"]["receipt_status"],"absent")
                self.assertIs(report["native_outcome_reinterpreted"],False)
                self.assertEqual(sum(map(len,extra.values()))<=8*1024*1024,True)
                joined=self.join()
                output=owner/"output"/joined["directory"]; output.mkdir(parents=True,mode=0o700)
                no_receipt, missing=route._collect_failed_output(handles,known,self.current,self.request,8*1024*1024,20)
                observed=json.loads(no_receipt[("failed_output",self.fixture+"/failed-output.observation.json")])
                self.assertEqual(missing,[])
                self.assertEqual(observed["output_observation"]["directory_status"],"present")
                self.assertEqual(observed["output_observation"]["receipt_status"],"absent")
                receipt=route.canonical({"version":2,"ok":False,"render_input_digest":joined["render_input_digest"]})
                (output/"native-failure-receipt.json").write_bytes(receipt)
                limited, resource_errors=route._collect_failed_output(handles,known,self.current,self.request,8*1024*1024,11)
                limited_report=json.loads(limited[("failed_output",self.fixture+"/failed-output.observation.json")])
                self.assertEqual(limited_report["output_observation"]["receipt_status"],"not_retained")
                self.assertNotIn(("failed_output",self.fixture+"/native-failure-receipt.original.json"),limited)
                self.assertTrue(any(row["reason"]=="ORIGINAL_RESOURCE_BOUND" for row in resource_errors))
                complete, errors=route._collect_failed_output(handles,known,self.current,self.request,8*1024*1024,20)
                self.assertEqual(errors,[])
                self.assertEqual(complete[("failed_output",self.fixture+"/native-failure-receipt.original.json")],receipt)
                complete_report=json.loads(complete[("failed_output",self.fixture+"/failed-output.observation.json")])
                self.assertEqual(complete_report["output_observation"]["receipt_status"],"original_retained")
                tiny, omitted=route._collect_failed_output(handles,known,self.current,self.request,0,0)
                self.assertEqual(tiny,{})
                self.assertTrue(all(row["reason"]=="ORIGINAL_RESOURCE_BOUND" for row in omitted))
                empty, errors=route._collect_failed_output(handles,{},self.current,self.request,8*1024*1024,20)
                self.assertEqual((empty,errors),({},[]))
            finally:
                handles.close()


if __name__ == '__main__':
    unittest.main()
