import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { randomUUID } from 'node:crypto';
import { ScopedChangeService } from '../../tests/support/scopedChangeService.ts';
import { decodeGraphContextFailure } from '../../src/semanticGraphContextFailure.ts';

const schema = 'motionwright.native.graph-context-failure/1';
const stages = ['ADMISSION', 'BINDING', 'CUSTODY', 'LOCAL', 'TRANSPORT', 'NATIVE'];
const codes = ['Unsupported', 'Unavailable', 'PermissionDenied', 'ConsentRequired',
  'PolicyDenied', 'NotFound', 'AmbiguousTarget', 'StaleReference', 'BackendFailed',
  'Timeout', 'InvalidArgument', 'PluginProtocolError', 'SandboxDenied', 'Conflict',
  'Cancelled', 'Internal', 'ProtocolMismatch', 'ResourceExhausted'];

test('context failure decoder preserves every declared stage and error code without granting authority', () => {
  for (const stage of stages) for (const error_code of [null, ...codes]) {
    const original = { schema, stage, error_code };
    const decoded = decodeGraphContextFailure(original);
    assert.deepEqual(decoded, original);
    assert.notEqual(decoded, original);
  }
});

test('context failure decoder rejects malformed or private diagnostics without reading accessors', () => {
  const canary = 'PRIVATE_CONTEXT_CANARY';
  const valid = { schema, stage: 'NATIVE', error_code: 'Timeout' };
  let accessorReads = 0;
  const accessor = { ...valid };
  Object.defineProperty(accessor, 'error_code', { enumerable: true, get() { accessorReads++; throw new Error(canary); } });
  const hidden = { ...valid };
  Object.defineProperty(hidden, 'stage', { value: 'NATIVE', enumerable: false });
  const symbol = { ...valid, [Symbol(canary)]: canary };
  const diagnostics = Object.assign(new Error(canary), valid);
  const malformed = [null, undefined, false, 1, [], [valid], JSON.stringify(valid), canary,
    {}, Object.assign(Object.create(null), valid), { schema, stage: 'NATIVE' }, { ...valid, schema: schema + '-foreign' },
    { ...valid, stage: 'native' }, { ...valid, stage: canary },
    { ...valid, error_code: 'timeout' }, { ...valid, error_code: canary },
    { ...valid, error_code: undefined }, { ...valid, error_code: {} },
    { ...valid, private: canary }, Object.create(valid), diagnostics,
    accessor, hidden, symbol, new Proxy({}, { ownKeys() { throw new Error(canary); } })];
  const decoded = malformed.map(value => decodeGraphContextFailure(value));
  assert.ok(decoded.every(value => value === null));
  assert.equal(accessorReads, 0);
  assert.equal(JSON.stringify(decoded).includes(canary), false);
});

test('pending context JSONL preserves only its closed rejection while foreign errors taint transport', async () => {
  // Exercise the actual invoke/rawRequest/receive path. The process interface
  // is inert: no executable, SQLite Store, Native owner or permission is created.
  const payload = { schema, stage: 'NATIVE', error_code: 'Timeout' };
  const cases = [
    { command: 'semantic_graph_authoring_context', payload, valid: true },
    { command: 'semantic_graph_authoring_context', payload: { schema, stage: 'ADMISSION', error_code: null }, valid: true },
    { command: 'get_project', payload, valid: false },
    { command: 'semantic_graph_authoring_context', payload: { ...payload, error_code: 'PRIVATE_CONTEXT_CANARY' }, valid: false },
    { command: 'semantic_graph_authoring_context', payload: { ...payload, private: 'PRIVATE_CONTEXT_CANARY' }, valid: false },
    { command: 'semantic_graph_authoring_context', payload, foreignId: true, valid: false },
  ];
  for (const item of cases) {
    const service = new ScopedChangeService('/inert/native', path.join(tmpdir(), randomUUID(), 'store.sqlite3'),
      false, { timeoutMs: 1_000 });
    let writes = 0, kills = 0;
    const child = { pid: 123, kill() { kills++; return true; }, stdin: { write(wire) {
      writes++;
      const request = JSON.parse(wire);
      service.receive(child, JSON.stringify({ id: item.foreignId ? 'foreign-id' : request.id,
        ok: false, error: item.payload }));
    } } };
    service.child = child;
    await assert.rejects(service.invoke(item.command), reason => {
      if (item.valid) {
        assert.deepEqual(reason, item.payload);
        assert.notEqual(reason, item.payload);
      } else {
        assert.ok(reason instanceof Error);
        assert.match(reason.message, /Invalid service JSONL response/);
        assert.equal(reason.message.includes('PRIVATE_CONTEXT_CANARY'), false);
      }
      return true;
    });
    assert.equal(writes, 1);
    assert.equal(kills, item.valid ? 0 : 1);
    assert.deepEqual(service.lifecycleTaints.map(row => row.kind), item.valid ? [] : ['transport_failure']);
  }
});
