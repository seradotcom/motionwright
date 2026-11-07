import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import path from 'node:path';
import {
  independentGraphReaderArgv,
  prepareOriginalReadOnlyHandoff,
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

test('Graph Native cohort must close before writer WAL-to-DELETE handoff', () => {
  const handoff = fs.readFileSync(
    new URL('../../tests/support/graph-owner/same_store_reopen.mjs', import.meta.url), 'utf8');
  const factory = fs.readFileSync(
    new URL('../../tests/support/graph-owner/factory.mjs', import.meta.url), 'utf8');
  const owner = fs.readFileSync(
    new URL('../../../../tooling/native_graph_browser_owner.py', import.meta.url), 'utf8');
  const nativeStop = handoff.indexOf('await factory.quiesceNativeOwner()');
  const writerSwitch = handoff.indexOf("command: 'prepare_read_only_handoff', args: null");
  assert.ok(nativeStop > 0 && writerSwitch > nativeStop);
  assert.match(handoff, /native_owner_quiescence: nativeQuiescence\.ref/);
  assert.match(handoff, /REOPEN_HANDOFF_NATIVE_OWNER_NOT_QUIESCENT/);
  assert.match(factory, /NATIVE_QUIESCED/);
  assert.match(factory, /ownerClose\.status === 'CLOSED' && cohort\.status === 'PASS'/);
  assert.match(factory, /observed\.processes\.length === 1/);
  assert.match(owner, /def graph_quiesce_v6\(self, \*, request_id\):/);
  assert.match(owner, /self\.lifecycle\.close\(reason="explicit-close"\)/);
  assert.match(owner, /cohort, absent = close_complete_cohort\(\)/);
  assert.match(owner, /"NATIVE_QUIESCENCE_MISSING"/);
});
