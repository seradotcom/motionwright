import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { graphOwnerSpawnEnvironment, graphOwnedClientEnvironment } from '../../tests/support/graph-owner/factory.mjs';

// These paths mirror createGraphServiceCase's actual makeSpec, not the old
// artificial factory-root-equals-owner-root testing arrangement.
const parent = path.join(path.sep, 'private', 'graph-case');
const owner = path.join(parent, 'owner');
const paths = {
  private_root: owner,
  owner_home: path.join(owner, 'home'),
  owner_runtime: path.join(owner, 'runtime'),
  owner_state: path.join(owner, 'state'),
  owner_socket: path.join(owner, 'runtime', 'broker.sock'),
  manim_work: path.join(owner, 'work'),
};
const spec = {
  source: { sha: 'a'.repeat(40), sdk_root: path.join(path.sep, 'source', 'semwright'), run_id: '1234', run_attempt: 1 },
  runtime: { root: path.join(path.sep, 'source', 'manim') },
  binaries: { python3: { path: path.join(path.sep, 'usr', 'bin', 'python3') }, ffmpeg: { path: path.join(path.sep, 'usr', 'bin', 'ffmpeg') } },
  paths,
};
const runner = { GITHUB_WORKFLOW_REF: 'seradotcom/motionwright/.github/workflows/studio-service-verification.yml@refs/heads/main',
  GH_TOKEN: 'UNTRUSTED_SECRET', LD_PRELOAD: 'UNTRUSTED_LOADER' };
const original = { path: path.join(parent, 'initializer.original.json'), sha256: 'f'.repeat(64) };

test('real Graph owner spec launches with one owner child root inside parent custody', () => {
  const source = readFileSync(new URL('../../tests/support/nativeGraphCase.ts', import.meta.url), 'utf8');
  assert.match(source, /const root = join\(factory\.root, "owner"\)/);
  assert.match(source, /private_root: root, owner_home: join\(root, "home"\)/);
  const env = graphOwnerSpawnEnvironment(spec, parent, { bytes: 4096, files: 15 }, original, null, runner);
  assert.equal(env.MOTIONWRIGHT_GRAPH_PARENT_ROOT, parent);
  assert.equal(env.HOME, paths.owner_home);
  assert.equal(env.XDG_CONFIG_HOME, path.join(owner, 'config'));
  assert.equal(env.XDG_CACHE_HOME, path.join(owner, 'work'));
  // The child "owner" root MUST be absent until the Python owner creates it.
  // Parent TMPDIR therefore has to be the already admitted custody parent.
  assert.equal(env.TMPDIR, parent);
  assert.notEqual(env.TMPDIR, paths.private_root);
  assert.equal(env.SEMWRIGHT_TEST_PYTHON, spec.binaries.python3.path);
  assert.equal(env.MOTIONWRIGHT_GRAPH_STORE_FILE_BYTES, undefined);
  assert.equal(env.GH_TOKEN, undefined);
  assert.equal(env.LD_PRELOAD, undefined);
  assert.throws(() => graphOwnerSpawnEnvironment({ ...spec, paths: { ...paths, private_root: parent } },
    parent, { bytes: 4096, files: 15 }, original, null, runner), /GRAPH_OWNER_/);
});

test('real Python owner connection under clients receives exactly bound client capabilities', () => {
  const python = readFileSync(new URL('../../../../tooling/native_graph_browser_owner.py', import.meta.url), 'utf8');
  assert.match(python, /self\.paths = \{name: private_directory\(lifecycle\.evidence \/ name\)/);
  assert.match(python, /path = self\.paths\["clients"\] \/ \(identifier \+ "\.connection\.json"\)/);
  const capability = {
    MOTIONWRIGHT_GRAPH_CONNECTION: path.join(owner, 'clients', 'client-0001.connection.json'),
    MOTIONWRIGHT_GRAPH_CALLBACK_CAP: '12',
    MOTIONWRIGHT_GRAPH_PRIVATE_MAX_BYTES: '4096',
    MOTIONWRIGHT_GRAPH_PRIVATE_MAX_FILES: '2',
  };
  const env = graphOwnedClientEnvironment(capability, paths);
  assert.equal(env.MOTIONWRIGHT_GRAPH_CONNECTION, capability.MOTIONWRIGHT_GRAPH_CONNECTION);
  assert.equal(env.XDG_CONFIG_HOME, path.join(owner, 'config'));
  assert.equal(env.TMPDIR, path.join(owner, 'work'));
  assert.equal(env.MOTIONWRIGHT_GRAPH_CALLBACK_CAP, '12');
  assert.equal(env.MOTIONWRIGHT_GRAPH_STORE_FILE_BYTES, '4194304');
  assert.throws(() => graphOwnedClientEnvironment({ ...capability,
    MOTIONWRIGHT_GRAPH_STORE_FILE_BYTES: '67032280' }, paths), /GRAPH_CLIENT_ENV_FIELDS/);
  assert.throws(() => graphOwnedClientEnvironment({
    ...capability, MOTIONWRIGHT_GRAPH_CONNECTION: path.join(parent, 'clients', 'foreign.connection.json'),
  }, paths), /GRAPH_CLIENT_CONNECTION_UNBOUND/);
  assert.throws(() => graphOwnedClientEnvironment({
    ...capability, MOTIONWRIGHT_GRAPH_CONNECTION: path.join(owner, 'owner', 'clients', 'client-0001.connection.json'),
  }, paths), /GRAPH_CLIENT_CONNECTION_UNBOUND/);
});

test('Graph initializer selects a fixed Store bound without inherited overrides', () => {
  const source = readFileSync(new URL('../../tests/support/scopedChangeService.ts', import.meta.url), 'utf8');
  const start = source.indexOf('function closedGraphInitializerEnvironment(');
  const end = source.indexOf('\n/** Observe the actual Linux child', start);
  const body = stripTypeScriptTypes(source.slice(start, end), { mode: 'strip' });
  const environment = new Function('dirname', 'isAbsolute', body + '; return closedGraphInitializerEnvironment;')(
    path.dirname, path.isAbsolute)(path.join(parent, 'store', 'motionwright.sqlite3'));
  assert.equal(environment.MOTIONWRIGHT_GRAPH_STORE_FILE_BYTES, '4194304');
  assert.equal(environment.GH_TOKEN, undefined);
});

// This checks an inert Linux process's real limits, without opening a Store.
test('Graph Store limit observer requires both actual kernel limits before continuing',
  { skip: process.platform !== 'linux' }, async () => {
    const { spawn } = await import('node:child_process');
    const { once } = await import('node:events');
    const { requireGraphStoreFileLimit } = await import('../../tests/support/scopedChangeService.ts');
    const child = spawn('python3', ['-c',
      'import resource,sys; resource.setrlimit(resource.RLIMIT_FSIZE,(4194304,4194304)); sys.stdin.read()'],
      { stdio: ['pipe', 'pipe', 'pipe'], env: { PATH: process.env.PATH } });
    const exited = once(child, 'close');
    try {
      await once(child, 'spawn');
      await requireGraphStoreFileLimit(child);
    } finally {
      child.stdin.end();
      await exited;
    }
  });

test('Graph Store limit observer refuses a process with only its soft limit lowered',
  { skip: process.platform !== 'linux' }, async () => {
    const { spawn } = await import('node:child_process');
    const { once } = await import('node:events');
    const { requireGraphStoreFileLimit } = await import('../../tests/support/scopedChangeService.ts');
    const child = spawn('python3', ['-c',
      'import resource,sys; resource.setrlimit(resource.RLIMIT_FSIZE,(4194304,8388608)); sys.stdin.read()'],
      { stdio: ['pipe', 'pipe', 'pipe'], env: { PATH: process.env.PATH } });
    const exited = once(child, 'close');
    try {
      await once(child, 'spawn');
      await assert.rejects(() => requireGraphStoreFileLimit(child), /both 4194304-byte/);
    } finally {
      child.stdin.end();
      await exited;
    }
  });

test('Graph Store limit observation fails closed without a live Linux child', async () => {
  const { requireGraphStoreFileLimit } = await import('../../tests/support/scopedChangeService.ts');
  await assert.rejects(() => requireGraphStoreFileLimit({ pid: undefined,
    exitCode: null, signalCode: null, killed: false }), /Graph Store/);
});

test('controller kernel limits match the five authenticated staged executables, not Store limits', async () => {
  const { verifyGraphControllerFileLimit } = await import('../../tests/support/graph-owner/factory.mjs');
  const sizes = { native_provider: 67032280, manim_runner: 4092912,
    python3: 8020928, ffmpeg: 342488, semwright_sandbox: 2182280 };
  const binaries = Object.fromEntries(Object.entries(sizes).map(([role, size_bytes]) =>
    [role, { path: path.join(parent, role), sha256: 'a'.repeat(64), size_bytes }]));
  // Inert kernel-report text exercises validation; it is not a process observation.
  const limits = (soft, hard) => `Limit                     Soft Limit           Hard Limit           Units\nMax file size             ${soft}             ${hard}             bytes\n`;
  assert.equal(verifyGraphControllerFileLimit(binaries, limits(67032280, 67032280)), 67032280);
  assert.throws(() => verifyGraphControllerFileLimit(binaries, limits(4194304, 4194304)), /CASE_CONTROLLER_KERNEL_FILE_BOUND/);
  assert.throws(() => verifyGraphControllerFileLimit(binaries, limits(67032280, 'unlimited')), /CASE_CONTROLLER_KERNEL_FILE_BOUND/);
  assert.throws(() => verifyGraphControllerFileLimit({ ...binaries, python3: undefined },
    limits(67032280, 67032280)), /CASE_CONTROLLER_STAGING_EXECUTABLE/);
  assert.throws(() => verifyGraphControllerFileLimit({ ...binaries,
    native_provider: { ...binaries.native_provider, size_bytes: 256 * 1024 * 1024 + 1 } },
    limits(67032280, 67032280)), /CASE_CONTROLLER_STAGING_EXECUTABLE/);
});
