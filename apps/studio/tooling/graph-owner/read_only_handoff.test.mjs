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
