import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { readFileSync } from 'node:fs';
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
  assert.throws(() => graphOwnedClientEnvironment({
    ...capability, MOTIONWRIGHT_GRAPH_CONNECTION: path.join(parent, 'clients', 'foreign.connection.json'),
  }, paths), /GRAPH_CLIENT_CONNECTION_UNBOUND/);
  assert.throws(() => graphOwnedClientEnvironment({
    ...capability, MOTIONWRIGHT_GRAPH_CONNECTION: path.join(owner, 'owner', 'clients', 'client-0001.connection.json'),
  }, paths), /GRAPH_CLIENT_CONNECTION_UNBOUND/);
});
