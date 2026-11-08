import test from 'node:test';
import assert from 'node:assert/strict';
import { ScopedChangeService } from '../../tests/support/scopedChangeService.ts';

const typed = {
  schema: 'motionwright.native.graph-context-failure/1',
  stage: 'NATIVE',
  error_code: 'BackendFailed',
};
function deliver(command, error) {
  // Exercise the actual private JSONL receiver; no service process, grant or
  // Native/Host operation is mocked or executed.
  const service = Object.create(ScopedChangeService.prototype);
  let outcome = null;
  let killed = false, tainted = false, rejectCalls = 0;
  const child = { kill() { killed = true; } };
  service.child = child;
  service.pending = new Map([['"corr"', {
    command, graphMutation: null, resolve(value) { outcome = value; },
    timer: undefined,
  }]]);
  service.transcript = [];
  service.timedOutIds = new Set();
  service.taint = () => { tainted = true; };
  service.rejectPending = () => { rejectCalls++; };
  service.failure = reason => new Error(reason);
  service.receive(child, JSON.stringify({ id: 'corr', ok: false, error }));
  return { outcome, killed, tainted, rejectCalls, pending: service.pending.size, records: service.transcript.length };
}

test('real JSONL receiver preserves only closed Native context failure on its original command', () => {
  const actual = deliver('semantic_graph_authoring_context', typed);
  assert.equal(actual.killed, false);
  assert.equal(actual.tainted, false);
  assert.equal(actual.rejectCalls, 0);
  assert.equal(actual.pending, 0);
  assert.equal(actual.records, 1);
  assert.equal(actual.outcome?.ok, false);
  assert.deepEqual(actual.outcome?.error, typed);
});

test('the same typed rejection is not accepted on another service command', () => {
  const other = deliver('bootstrap', typed);
  assert.equal(other.outcome, null);
  assert.equal(other.killed, true);
  assert.equal(other.tainted, true);
});

test('unknown, extra-field and malformed context errors remain fail-closed', () => {
  for (const error of [
    { ...typed, stage: 'PRIVATE_STAGE' },
    { ...typed, error_code: 'PRIVATE_CODE' },
    { ...typed, message: 'sensitive' },
    { ...typed, error_code: undefined },
    { ...typed, schema: 'other.context/1' },
  ]) {
    const result = deliver('semantic_graph_authoring_context', error);
    assert.equal(result.outcome, null);
    assert.equal(result.killed, true);
    assert.equal(result.tainted, true);
  }
});
