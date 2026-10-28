"""G0 owner configuration/custody controls; no Host, render or native PASS.

Binary contents below are inert fixture files. Their SHA-256 values are computed
from those actual files, never used as production tool identities.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

TOOLING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLING))
import ci_private_evidence as custody
import native_manim_graph_cycle as graph
import native_manim_graph_runtime_intake as intake

COMPILER_ORIGINAL = TOOLING.parent / intake.COMPILER


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def actual_runtime_manifest(driver, runner, python, ffmpeg):
    """Evaluate only the existing owner's literal manifest constructor."""
    tree = ast.parse((TOOLING / 'native_manim_e2e.py').read_text())
    body = next(row for row in tree.body if isinstance(row, ast.FunctionDef) and row.name == '_run_single')
    candidates = [row.value for row in ast.walk(body) if isinstance(row, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == 'manifest' for target in row.targets)]
    assert len(candidates) == 1
    environment = dict(driver=driver, runner=runner, PYTHON=python, FFMPEG=ffmpeg,
                       digest=digest, MANIM_VERSION='0.21.0', editable=False)
    return eval(compile(ast.Expression(candidates[0]), 'actual-owner-manifest-fixture', 'eval'), environment)


class GraphRuntimeSourceIntakeControls(unittest.TestCase):
    """Real filesystem staging with inert fixture bytes, never font/native evidence."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo, self.runtime, self.evidence = [self.root / name for name in ('repo','runtime','evidence')]
        for path in (self.repo, self.runtime, self.evidence):
            path.mkdir(mode=0o700)
        self.receipt = self.evidence / 'graph-runtime-source-closure.json'
        self.profile = self.runtime / intake.PROFILE_DIRECTORY
        original = COMPILER_ORIGINAL.read_bytes()
        pins, modules = intake.compiler_pin_specs(original)
        self.assertEqual(len(pins), 9)
        self.assertEqual(len(modules), 6)
        self.compiler = self.repo / intake.COMPILER
        self.originals = {}
        # Preserve the real closed Rust syntax; replace only each fixture pin's
        # hash/size with the actual inert bytes written below, not a font mock.
        text = original.decode('utf-8')
        for pin in pins:
            relative = intake.FONT_ORIGINALS + '/' + pin['relative_path']
            raw = ('INERT G0 ASSET: ' + pin['relative_path']).encode()
            old = f'sha256: "{pin["sha256"]}",\n        size_bytes: {pin["size_bytes"]},'
            new = f'sha256: "{hashlib.sha256(raw).hexdigest()}",\n        size_bytes: {len(raw)},'
            self.assertEqual(text.count(old), 1)
            text = text.replace(old, new, 1)
            self.write_original(relative, raw)
        for module in modules:
            self.write_original('tooling/' + module + '.py',
                                ('raise AssertionError("G0 helper must never execute: ' + module + '")\n').encode())
        self.write_original(intake.COMPILER, text.encode())
        self.write_original('SOURCE_LOCK.json', b'{"fixture":"SOURCE_ONLY_NOT_ADMISSION"}\n')

    def write_original(self, relative, raw):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.originals[relative] = raw

    def prepare(self):
        return intake.prepare_graph_runtime(self.repo, self.runtime, self.receipt)

    def snapshot(self):
        return {str(path.relative_to(self.runtime)):
                ('link', os.readlink(path)) if path.is_symlink() else
                ('dir',) if path.is_dir() else ('file', path.read_bytes())
                for path in self.runtime.rglob('*')}

    def assert_not_staged(self):
        self.assertFalse(os.path.lexists(self.profile))
        self.assertFalse(os.path.lexists(self.receipt))

    def test_fresh_copies_fifteen_originals_and_exact_provenance_without_execution(self):
        receipt = self.prepare()
        self.assertEqual(receipt['files_count'], 15)
        self.assertEqual(receipt['status'], 'SOURCE_INTAKE_ONLY')
        for key in ('compiled_runner_pin_agreement_verified','native_font_open_observed',
                    'pango_layout_observed','native_render_performed','profile_admitted'):
            self.assertFalse(receipt[key])
        for row in receipt['files']:
            source = self.originals[row['source_relative_path']]
            self.assertEqual((self.runtime / row['runtime_relative_path']).read_bytes(), source)
            self.assertEqual(row['sha256'], hashlib.sha256(source).hexdigest())
            self.assertEqual(row['size_bytes'], len(source))
        self.assertEqual(receipt['compiler_source']['sha256'], digest(self.compiler))
        self.assertEqual((self.profile / intake.CLOSURE_NAME).read_bytes(), self.receipt.read_bytes())
        for relative, raw in self.originals.items():
            self.assertEqual((self.repo / relative).read_bytes(), raw)

    def test_existing_complete_copy_is_verification_only_and_keeps_original_bytes(self):
        original = self.prepare()
        before = self.snapshot()
        with mock.patch.object(intake, '_write_new', side_effect=AssertionError('existing closure must not write')):
            self.assertEqual(self.prepare(), original)
        self.assertEqual(before, self.snapshot())

    def test_changed_pinned_asset_rejected_before_any_staging(self):
        relative = next(name for name in self.originals if name.startswith(intake.FONT_ORIGINALS))
        path = self.repo / relative
        raw = path.read_bytes()
        path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        with self.assertRaisesRegex(ValueError, 'graph_runtime_original_font_pin'):
            self.prepare()
        self.assert_not_staged()

    def test_nonliteral_helper_route_is_rejected_before_any_staging(self):
        self.compiler.write_bytes(self.compiler.read_bytes().replace(
            b'include_bytes!("../../../tooling/manim_graph_runtime.py")',
            b'include_bytes!(concat!("../../../tooling/", "manim_graph_runtime.py"))', 1))
        with self.assertRaisesRegex(ValueError, 'graph_runtime_helper_literal'):
            self.prepare()
        self.assert_not_staged()

    def test_source_symlink_hardlink_and_fifo_are_rejected_without_following(self):
        target = self.repo / 'tooling/manim_graph_runtime.py'
        raw = target.read_bytes()
        outside = self.root / 'outside-source'
        outside.write_bytes(raw)
        for kind in ('symlink', 'hardlink', 'fifo'):
            with self.subTest(kind=kind):
                target.unlink()
                if kind == 'symlink': target.symlink_to(outside)
                elif kind == 'hardlink': os.link(outside, target)
                else: os.mkfifo(target)
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assert_not_staged()
                target.unlink()
                target.write_bytes(raw)
        self.assertEqual(outside.read_bytes(), raw)

    def test_fifo_substituted_after_namespace_check_is_rejected_without_waiting(self):
        capture = intake.capture_regular
        target = self.repo / 'tooling/manim_graph_runtime.py'
        changed = []
        def replace_before_open(path, limit):
            if path == target and not changed:
                target.unlink()
                os.mkfifo(target)
                changed.append(True)
            return capture(path, limit)
        with mock.patch.object(intake, 'capture_regular', side_effect=replace_before_open):
            with self.assertRaisesRegex(ValueError, 'graph_runtime_original_capture'):
                self.prepare()
        self.assertEqual(changed, [True])
        self.assert_not_staged()

    def test_empty_or_partial_existing_destination_is_never_completed(self):
        self.profile.mkdir()
        before = self.snapshot()
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(self.snapshot(), before)
        (self.profile / 'helpers').mkdir()
        (self.profile / 'helpers/manim_graph_runtime.py').write_bytes(b'original partial bytes')
        before = self.snapshot()
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.receipt.exists())

    def test_existing_changed_or_extra_file_is_never_repaired(self):
        self.prepare()
        target = self.profile / 'helpers/manim_graph_runtime.py'
        target.write_bytes(b'preserve this differing original')
        before = self.snapshot()
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(before, self.snapshot())
        target.write_bytes(self.originals['tooling/manim_graph_runtime.py'])
        (self.profile / 'unselected.py').write_bytes(b'raise AssertionError("never execute")')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'graph_runtime_unexpected_existing_member'): self.prepare()
        self.assertEqual(before, self.snapshot())

    def test_interrupted_copy_retained_and_cannot_be_resumed(self):
        writer = intake._write_new
        calls = []
        def interrupt(parent, name, raw):
            calls.append(name)
            if len(calls) == 2:
                raise OSError('G0 observed filesystem interruption')
            return writer(parent, name, raw)
        with mock.patch.object(intake, '_write_new', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'filesystem interruption'): self.prepare()
        self.assertTrue(self.profile.is_dir())
        self.assertFalse((self.profile / intake.CLOSURE_NAME).exists())
        self.assertFalse(self.receipt.exists())
        before = self.snapshot()
        self.assertEqual(sum(value[0] == 'file' for value in before.values()), 1)
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(before, self.snapshot())

    def test_source_changed_during_copy_does_not_publish_completed_closure(self):
        writer = intake._write_new
        calls = []
        def change_source(parent, name, raw):
            result = writer(parent, name, raw)
            calls.append(name)
            if len(calls) == 1:
                path = self.repo / 'tooling/manim_graph_runtime.py'
                path.write_bytes(path.read_bytes().replace(b'raise', b'RAISE', 1))
            return result
        with mock.patch.object(intake, '_write_new', side_effect=change_source):
            with self.assertRaisesRegex(ValueError, 'graph_runtime_original_changed'): self.prepare()
        self.assertFalse((self.profile / intake.CLOSURE_NAME).exists())
        self.assertFalse(self.receipt.exists())
        self.assertEqual(len(calls), 15)

    def test_existing_receipt_collision_denies_before_runtime_mutation(self):
        self.receipt.write_bytes(b'original unrelated receipt')
        with self.assertRaisesRegex(ValueError, 'graph_runtime_existing_receipt_differs'): self.prepare()
        self.assertFalse(self.profile.exists())
        self.assertEqual(self.receipt.read_bytes(), b'original unrelated receipt')

    def test_real_compiler_original_has_closed_nine_assets_and_six_helpers(self):
        pins, modules = intake.compiler_pin_specs(COMPILER_ORIGINAL.read_bytes())
        self.assertEqual((len(pins), tuple(modules)), (9, intake.HELPERS))
        self.assertEqual(sum(row['size_bytes'] for row in pins), 100265)

    def test_owner_stages_before_provider_manifest_and_host_configuration(self):
        tree = ast.parse((TOOLING / 'native_manim_e2e.py').read_text())
        body = next(row for row in tree.body if isinstance(row, ast.FunctionDef) and row.name == '_run_single')
        calls = [row for row in ast.walk(body) if isinstance(row, ast.Call)]
        intake_call = next(row for row in calls if isinstance(row.func, ast.Name)
                           and row.func.id == 'prepare_graph_runtime')
        provider_call = next(row for row in calls if isinstance(row.func, ast.Name)
                             and row.func.id == 'provider_manifest')
        self.assertEqual([ast.unparse(row) for row in intake_call.args],
                         ['ROOT','MANIM_RUNTIME','graph_runtime_intake_path'])
        self.assertLess(intake_call.lineno, provider_call.lineno)
        configurations = [row for row in calls if isinstance(row.func, ast.Name)
                          and row.func.id == 'write_private_json']
        self.assertTrue(all(intake_call.lineno < row.lineno for row in configurations))


class GraphProbeManifestControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.paths = {}
        for name in ('bin', 'config', 'motionwright-data', 'evidence'):
            path = self.root / name
            path.mkdir(mode=0o700)
            self.paths[name] = path
        self.files = {}
        for name in ('native', 'driver', 'manim-runner', 'python3', 'ffmpeg'):
            path = self.root / ('fixture-' + name)
            path.write_bytes(('inert G0 fixture ' + name).encode())
            self.files[name] = path
        self.tools = {name:self.files[name] for name in ('manim-runner', 'python3', 'ffmpeg')}
        self.runtime = actual_runtime_manifest(self.files['driver'], self.files['manim-runner'],
                                              self.files['python3'], self.files['ffmpeg'])
        self.runtime_path = self.paths['config'] / 'manim-driver.json'
        self.write_runtime()

    def write_runtime(self):
        self.runtime_path.write_text(json.dumps(self.runtime))

    def create(self):
        return graph.provider_manifest({'application_version':'fixture-1'}, self.paths,
                    self.files['native'], digest=digest, evidence=self.paths['evidence'],
                    runtime_manifest_path=self.runtime_path, sealed_tools=self.tools)

    def assert_no_provider_writes(self):
        self.assertFalse((self.paths['config'] / 'motionwright-driver.json').exists())
        self.assertFalse((self.paths['bin'] / 'motionwright-native-provider').exists())

    def test_actual_runtime_pins_resources_and_only_probe_mounts_are_reused(self):
        original = self.runtime_path.read_bytes()
        value = json.loads(self.create().read_bytes())
        self.assertEqual(value['resources'], self.runtime['resources'])
        self.assertEqual(value['request_timeout_ms'], 30000)
        self.assertTrue(value['interfaces']['host_tools'])
        self.assertEqual({row['root']:row['read_only'] for row in value['mounts']},
                         {'motionwright-data':False,'manim-runtime':True,'manim-work':False,'fontconfig':True})
        for tool in value['tools']:
            self.assertEqual(tool['sha256'], digest(self.tools[tool['name']]))
            self.assertNotIn('motionwright-data', tool['mounts'])
            self.assertNotIn('manim-output', tool['mounts'])
        self.assertEqual(value['tools'][0]['mounts'], ['manim-runtime','manim-work','fontconfig'])
        self.assertEqual(self.runtime_path.read_bytes(), original)
        identity = json.loads((self.paths['evidence']/'graph-provider-identity.json').read_bytes())
        self.assertEqual(identity['probe_runtime_manifest_sha256'], hashlib.sha256(original).hexdigest())
        self.assertFalse(identity['serialized_authority_used'])

    def test_tool_changed_since_the_original_manifest_fails_before_provider_copy(self):
        self.files['manim-runner'].write_bytes(b'changed inert binary')
        with self.assertRaisesRegex(ValueError, 'graph_probe_original_tool_binding'):
            self.create()
        self.assert_no_provider_writes()

    def test_wrong_delegation_or_tool_dependency_fails_before_provider_copy(self):
        original = copy.deepcopy(self.runtime)
        cases = [
            lambda value: value['mounts'][0].update(read_only=False),
            lambda value: value['tools'][0].update(dependencies=['python3']),
            lambda value: value['tools'][1].update(root='foreign-python-root'),
            lambda value: value['tools'][0]['mounts'].append('motionwright-data'),
        ]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                self.runtime = copy.deepcopy(original)
                mutate(self.runtime)
                self.write_runtime()
                with self.assertRaises(ValueError): self.create()
                self.assert_no_provider_writes()

    def test_duplicate_tool_or_boolean_resource_is_rejected(self):
        self.runtime['tools'][2] = copy.deepcopy(self.runtime['tools'][1])
        self.write_runtime()
        with self.assertRaises(ValueError): self.create()
        self.assert_no_provider_writes()
        self.runtime = actual_runtime_manifest(self.files['driver'],self.files['manim-runner'],self.files['python3'],self.files['ffmpeg'])
        self.runtime['resources']['processes'] = True
        self.write_runtime()
        with self.assertRaisesRegex(ValueError, 'graph_probe_original_resource_profile'): self.create()
        self.assert_no_provider_writes()

    @unittest.skipUnless(hasattr(os, 'symlink'), 'symlink unavailable')
    def test_tool_symlink_cannot_replace_original_path_identity(self):
        alias = self.root / 'python-alias'
        alias.symlink_to(self.files['python3'])
        self.tools['python3'] = alias
        with self.assertRaisesRegex(ValueError, 'graph_probe_original_tool_file'): self.create()
        self.assert_no_provider_writes()

    def test_source_owner_call_passes_its_existing_manifest_and_tools(self):
        tree = ast.parse((TOOLING / 'native_manim_e2e.py').read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'provider_manifest']
        self.assertEqual(len(calls), 1)
        arguments = {key.arg:key.value for key in calls[0].keywords}
        self.assertEqual(ast.unparse(arguments['runtime_manifest_path']), 'manifest_path')
        self.assertEqual(ast.literal_eval(ast.Expression(ast.Dict(keys=arguments['sealed_tools'].keys,
                         values=[ast.Constant(ast.unparse(v)) for v in arguments['sealed_tools'].values]))),
                         {'manim-runner':'runner','python3':'PYTHON','ffmpeg':'FFMPEG'})


class GraphProbeOriginalCustodyControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.owner = Path(self.temp.name).resolve() / 'owner'
        self.owner.mkdir(mode=0o700)

    def attempt(self, phase, token):
        path = self.owner / phase / 'work' / ('.graph-text-probe-' + token)
        path.mkdir(parents=True, mode=0o700)
        return path

    def snapshot(self):
        raw, complete = custody.gather_snapshot({'owner':self.owner}, preserve_partial=True)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            entries = {member.name:archive.extractfile(member).read() for member in archive.getmembers()}
        return entries, json.loads(entries['snapshot-manifest.json']), complete

    def test_complete_and_partial_probe_bytes_are_retained_without_parsing_or_inventing(self):
        original = self.attempt('g1','a'*64)
        reopened = self.attempt('g4','b'*64)
        first = {'stdin.bin':b'{"original":"input"}', 'intent.json':b'{"intent":1}',
                 'spawned.json':b'{"pid":123}', 'stdout.bin':b'\xff\x00original', 'stderr.bin':b'',
                 'exit.json':b'{"code":2}', 'terminal.json':b'{"deadline_expired":true}'}
        second = {'intent.json':b'{"partial":', 'stdin.bin':b'original', 'stdout.bin':b'partial\xff',
                  'stderr.bin':b'', 'terminal.json':b''}
        for folder, files in ((original,first),(reopened,second)):
            for name, raw in files.items(): (folder/name).write_bytes(raw)
        entries, manifest, complete = self.snapshot()
        self.assertTrue(complete)  # complete custody of observed originals, not native success
        for folder, files in ((original,first),(reopened,second)):
            for name, raw in files.items():
                key = 'owner/' + (folder/name).relative_to(self.owner).as_posix()
                self.assertEqual(entries[key], raw)
        self.assertNotIn('owner/'+(reopened/'exit.json').relative_to(self.owner).as_posix(), entries)
        self.assertFalse(manifest['complete_owner_state_claimed'])
        directories = [row for row in manifest['roots']['owner']['files'] if row.get('kind')=='metadata-only-directory']
        self.assertEqual(len(directories), 2)
        self.assertTrue(all(row['native_completion_claimed'] is False for row in directories))

    def test_empty_reserved_attempt_is_metadata_only_without_a_synthetic_intent(self):
        reserved = self.attempt('g2','c'*64)
        self.attempt('g2','not-a-real-attempt-digest')
        entries, manifest, complete = self.snapshot()
        self.assertTrue(complete)
        rows = [row for row in manifest['roots']['owner']['files'] if row.get('kind')=='metadata-only-directory']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['archive_path'], 'owner/'+reserved.relative_to(self.owner).as_posix())
        self.assertFalse(rows[0]['native_completion_claimed'])
        self.assertEqual(set(entries), {'snapshot-manifest.json'})
        self.assertEqual(list(reserved.iterdir()), [])

    @unittest.skipUnless(hasattr(os, 'symlink'), 'symlink unavailable')
    def test_owner_symlink_metadata_never_reads_the_external_stream(self):
        attempt = self.attempt('g1','d'*64)
        outside = self.owner.parent/'unselected-secret'
        outside.write_bytes(b'outside original must not be opened')
        (attempt/'stdout.bin').symlink_to(outside)
        entries, manifest, _ = self.snapshot()
        self.assertEqual(set(entries), {'snapshot-manifest.json'})
        links = [row for row in manifest['roots']['owner']['files'] if row.get('kind')=='metadata-only-nonregular']
        self.assertEqual(len(links), 1)
        self.assertFalse(links[0]['followed_or_opened'])
        self.assertEqual(outside.read_bytes(), b'outside original must not be opened')


if __name__ == '__main__':
    unittest.main()
