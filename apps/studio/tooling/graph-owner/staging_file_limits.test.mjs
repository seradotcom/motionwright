// Real kernel observations on inert Python children; no Service/Native launch.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { observeGraphStoreFileBound } from '../../tests/support/scopedChangeService.ts';
import { verifyGraphParentFileLimit } from '../../tests/support/graph-owner/factory.mjs';
import { graphRuntimeDiagnosticHint } from '../../tests/support/graph-owner/contracts.mjs';

test('Graph parent requires soft four and the exact reviewed hard staging ceiling', () => {
  const plan = { case_soft_file_bytes: 4194304, case_hard_file_bytes: 66908064 };
  verifyGraphParentFileLimit('Max file size             4194304              66908064             bytes', plan);
  for (const line of ['Max file size 4194304 4194304 bytes', 'Max file size 66908064 66908064 bytes',
    'Max file size 4194304 unlimited bytes', 'Max file size 4194304 67108864 bytes']) {
    assert.throws(() => verifyGraphParentFileLimit(line, plan), /CASE_SOURCE_BOUND_KERNEL_FILE_LIMITS/);
  }
});

test('Graph diagnostic releases only closed stage numbers and terminal enum', () => {
  const row = { soft_bytes: 4194304, hard_bytes: 4194304, required_file_bytes: 66908064,
    staging_total_bytes: 79364392, terminal_kind: 'NOT_STARTED' };
  assert.equal(graphRuntimeDiagnosticHint(row), ' OWNER_FSIZE:4194304/4194304 OWNER_STAGING_FILE:66908064'
    + ' OWNER_STAGING_TOTAL:79364392 OWNER_TERMINAL:NOT_STARTED');
  assert.throws(() => graphRuntimeDiagnosticHint({ ...row, original_stderr: '/private/TOKEN' }),
    /OWNER_RUNTIME_DIAGNOSTIC_FIELDS/);
  for (const change of [{ terminal_kind: '/private/TOKEN' }, { hard_bytes: 'CREDENTIAL' },
    { required_file_bytes: 67108865 }, { staging_total_bytes: 109051905 }]) {
    assert.throws(() => graphRuntimeDiagnosticHint({ ...row, ...change }), /OWNER_RUNTIME_DIAGNOSTIC_VALUES/);
  }
});

async function actualChild(code, body) {
  const child = spawn('/usr/bin/python3', ['-I', '-B', '-c',
    'import resource,sys,time\n' + code + '\nprint("READY", flush=True)\n'
    + (body ?? '') + '\nsys.stdin.read()\n'], { stdio: ['pipe', 'pipe', 'pipe'] });
  const closed = once(child, 'close');
  let errors = '';
  child.stderr.on('data', chunk => { errors += chunk; });
  const [ready] = await once(child.stdout, 'data');
  assert.equal(String(ready), 'READY\n', errors);
  return { child, finish: async () => {
    child.stdin.end(); const [code, signal] = await closed;
    assert.equal(code, 0, errors); assert.equal(signal, null);
  } };
}

test('actual Store PID observation waits for hard four before allowing RPC', { skip: process.platform !== 'linux' }, async () => {
  const active = await actualChild('resource.setrlimit(resource.RLIMIT_FSIZE, (4194304,66908064))',
    'time.sleep(0.04)\nresource.setrlimit(resource.RLIMIT_FSIZE, (4194304,4194304))');
  try {
    const observation = await observeGraphStoreFileBound(active.child, Date.now() + 1000);
    assert.equal(observation.process_id, active.child.pid);
    assert.equal(observation.soft_bytes, 4194304);
    assert.equal(observation.hard_bytes, 4194304);
    assert.equal(observation.source, 'linux-proc-actual-child');
  } finally { await active.finish(); }
});

test('actual Store PID observation rejects an unclamped hard limit', { skip: process.platform !== 'linux' }, async () => {
  const active = await actualChild('resource.setrlimit(resource.RLIMIT_FSIZE, (4194304,66908064))');
  try {
    await assert.rejects(observeGraphStoreFileBound(active.child, Date.now() + 100),
      /GRAPH_STORE_KERNEL_LIMITS_NOT_CLAMPED_BEFORE_RPC/);
  } finally { await active.finish(); }
});

test('actual Store PID observation rejects a different soft limit', { skip: process.platform !== 'linux' }, async () => {
  const active = await actualChild('resource.setrlimit(resource.RLIMIT_FSIZE, (4194303,4194304))');
  try {
    await assert.rejects(observeGraphStoreFileBound(active.child, Date.now() + 1000),
      /GRAPH_STORE_KERNEL_LIMITS_INVALID/);
  } finally { await active.finish(); }
});
