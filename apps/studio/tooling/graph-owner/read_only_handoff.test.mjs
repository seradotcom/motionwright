import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import path from 'node:path';
import {
  independentGraphReaderArgv,
  prepareOriginalReadOnlyHandoff,
  sourceDeniedEnvelope,
} from '../../tests/support/graph-owner/same_store_reopen.mjs';

test('Graph independent second process uses exactly read-only original Store argv', () => {
  const executable = path.resolve('source-original', 'studio_review_session');
  const database = path.resolve('original-store', 'motionwright.sqlite3');
  assert.deepEqual(independentGraphReaderArgv(executable, database),
    [executable, '--database', database, '--read-only']);
  assert.throws(() => independentGraphReaderArgv('relative-service', database),
    /REOPEN_READER_ORIGINAL_ABSOLUTE_PATHS/);
  assert.throws(() => independentGraphReaderArgv(executable, 'relative.sqlite3'),
    /REOPEN_READER_ORIGINAL_ABSOLUTE_PATHS/);
});

test('Graph reader cannot claim handoff without real producer, EOF and original Native RPC', async () => {
  // No synthetic input, caller-supplied receipt or false "approved" field
  // can bypass the independent original-producer authority.
  await assert.rejects(prepareOriginalReadOnlyHandoff({ approved: true }, {}, {}),
    /J1_UNBRANDED_OR_DIAGNOSTIC_ADMISSION/);
  const readback = fs.readFileSync(
    new URL('../../tests/support/graph-owner/same_store_reopen.mjs', import.meta.url), 'utf8');
  const finalizer = fs.readFileSync(
    new URL('../../tests/support/nativeGraphCase.ts', import.meta.url), 'utf8');
  assert.match(finalizer, /await captureOriginalOpenedStoreWitness\(factory, service, expected\)/);
  assert.match(finalizer, /await prepareOriginalReadOnlyHandoff\(factory, service,/);
  assert.match(finalizer, /originalReadOnlyHandoffRef,/);
  assert.match(readback, /const handoff = requireOriginalReadOnlyHandoff\(factory, originalReadOnlyHandoffRef, preseal\)/);
  assert.match(readback, /factory\.sealClientAdmissions\(\)/);
  assert.match(readback, /command: 'prepare_read_only_handoff', args: null/);
  assert.match(readback, /const argv = independentGraphReaderArgv\(executable.path, database\)/);
  assert.match(readback, /bootstrap\?\.read_only === true/);
  assert.match(readback, /REOPEN_HANDOFF_REQUEST_NOT_IN_WRITER_RAW/);
});

test('Graph mounted provider lease must be released before writer WAL-to-DELETE handoff', () => {
  const handoff = fs.readFileSync(
    new URL('../../tests/support/graph-owner/same_store_reopen.mjs', import.meta.url), 'utf8');
  const factory = fs.readFileSync(
    new URL('../../tests/support/graph-owner/factory.mjs', import.meta.url), 'utf8');
  const owner = fs.readFileSync(
    new URL('../../../../tooling/native_graph_browser_owner.py', import.meta.url), 'utf8');
  const nativeStop = handoff.indexOf('await factory.quiesceNativeOwner()');
  const writerSwitch = handoff.indexOf("command: 'prepare_read_only_handoff', args: null");
  assert.ok(nativeStop > 0 && writerSwitch > nativeStop);
  assert.match(handoff, /native_provider_release: nativeQuiescence\.ref/);
  assert.match(handoff, /REOPEN_HANDOFF_PROVIDER_RELEASE_NOT_AUTHENTIC/);
  assert.match(factory, /PROVIDER_RELEASED/);
  // Quiescence must authenticate every retained command-original ref with the
  // real source-owned stable reader. An unbound `matchRef` used to throw only
  // after Studio's original Save and ACK, before independent Store reopen.
  assert.match(factory, /function fileRef\(file, expected, maximum\)/);
  assert.match(factory, /same\(result\.ref, expected, 'ORIGINAL_REFERENCE_MISMATCH'\)/);
  const release = factory.slice(factory.indexOf('async quiesceNativeOwner()'),
    factory.indexOf('\n  expectedServices()', factory.indexOf('async quiesceNativeOwner()')));
  assert.match(release, /fileRef\(row\[key\]\.path, row\[key\], 4 \* MiB\)/);
  assert.doesNotMatch(release, /\bmatchRef\(/);
  assert.match(factory, /released\.lease_shutdown_completed === true/);
  assert.match(owner, /def graph_quiesce_v6\(self, \*, request_id\):/);
  assert.match(owner, /"providers\.release_bound"/);
  assert.match(owner, /"provider_lease_shutdown_completed": True/);
  assert.match(owner, /"PROVIDER_RELEASE_ORIGINAL_MISSING"/);
});

test('Graph writer denial keeps original native and projection Value contracts closed', () => {
  const id = 'ABCDEF12-3456-4789-aBcD-EF1234567890';
  const graph = {
    schema: 'motionwright.graph-mutation-error/1', request_id: id,
    outcome_known: false, code: 'graph_mutation_failed',
  };
  assert.equal(sourceDeniedEnvelope('apply_change', 'stale revision', id), true);
  assert.equal(sourceDeniedEnvelope('apply_change', graph, id), true);
  assert.equal(sourceDeniedEnvelope('apply_change', graph, '00000000-0000-0000-0000-000000000001'), false);
  assert.equal(sourceDeniedEnvelope('apply_change', {...graph, code: 'invented'}, id), false);
  assert.equal(sourceDeniedEnvelope('apply_change', {...graph, private_diagnostic: 'secret'}, id), false);
  assert.equal(sourceDeniedEnvelope('get_project', graph, id), false);
  const projection = {code: 'stale_revision', expected: 'revision:2', actual: 'revision:3'};
  for (const command of ['project_projection_head', 'project_scene_page', 'project_scene_nodes']) {
    assert.equal(sourceDeniedEnvelope(command, projection, null), true);
    assert.equal(sourceDeniedEnvelope(command, {code: 'not_found'}, null), true);
    assert.equal(sourceDeniedEnvelope(command, {...projection, status: 'unreviewed'}, null), false);
    assert.equal(sourceDeniedEnvelope(command, {code: 'too_large', limit: -1}, null), false);
  }
  // Source GraphBackend has a Result<Value, Value> context failure channel.
  // No approval is derived from this pure wire-shape test.
  assert.equal(sourceDeniedEnvelope('semantic_graph_authoring_context', {source_error: 'value'}, null), true);
  assert.equal(sourceDeniedEnvelope('semantic_graph_operation_recovery', {invented: true}, null), false);
  assert.equal(sourceDeniedEnvelope('get_project', {invented: true}, null), false);
});
