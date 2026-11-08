import test from 'node:test';
import assert from 'node:assert/strict';
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
  const plain = Object.assign(Object.create(null), { schema, stage: 'LOCAL', error_code: null });
  assert.deepEqual(decodeGraphContextFailure(plain), { schema, stage: 'LOCAL', error_code: null });
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
    {}, { schema, stage: 'NATIVE' }, { ...valid, schema: schema + '-foreign' },
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
