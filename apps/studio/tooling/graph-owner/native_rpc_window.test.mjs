import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { randomUUID } from 'node:crypto';
import { GraphCaseFactoryCore } from '../../tests/support/graph-owner/factory.mjs';
import { hasNoSemanticGraphs, developmentGraphSourceSelection } from '../../tests/support/graph-owner/contracts.mjs';
import { ScopedChangeService } from '../../tests/support/scopedChangeService.ts';

test('Native RPC retains a response within its existing 45-second transport window without retry', async t => {
  // Only the transport clock and external process are inert. Execute the actual
  // factory selection, RPC deadline, JSONL correlation and permanent taint path.
  // This fixture creates no Store, producer, Native owner or authorization.
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const databasePath = path.join(tmpdir(), randomUUID(), 'motionwright.sqlite3');
  const launches = [];
  const initializer = { async fixtureBootstrap() { return { project: { id: 'inert' } }; }, async close() {} };
  let native;
  const Service = { async create(options) {
    launches.push(options);
    if (options.seedFixture) return initializer;
    native = new ScopedChangeService('/inert/native', databasePath, false, { timeoutMs: options.timeoutMs });
    return native;
  } };
  const factory = {
    options: { databasePath }, admission: { ordinaryService: { path: '/inert/ordinary' }, nativeService: { path: '/inert/native' } },
    checkOpen() {}, async completeInitializer() {}, configureSpec() {}, taint(error) { throw error; },
  };
  assert.equal(await GraphCaseFactoryCore.prototype.createNativeService.call(factory, Service,
    { databasePath }, async () => ({})), native);
  assert.equal(launches[0].timeoutMs, undefined, 'ordinary initializer keeps its normal RPC window');
  let writes = 0, respond = true;
  const child = { pid: 123, stdin: { write(wire) {
    writes++;
    if (respond) {
      const request = JSON.parse(wire);
      setTimeout(() => native.receive(child, JSON.stringify({ id: request.id, ok: true, result: 'original-response' })), 44_000);
    }
  } } };
  native.child = child;
  const pending = native.rawRequest({ command: 'semantic_graph_authoring_context', args: {} })
    .then(value => ({ value }), error => ({ error }));
  t.mock.timers.tick(44_000);
  const outcome = await pending;
  assert.deepEqual(outcome, { value: { id: native.transcript[0].payload.id, ok: true, result: 'original-response' } });
  assert.equal(native.lifecycleTaints.length, 0);
  assert.equal(writes, 1);

  respond = false;
  const unanswered = native.rawRequest({ command: 'semantic_graph_authoring_context', args: {} })
    .then(value => ({ value }), error => ({ error }));
  t.mock.timers.tick(50_000);
  const failure = await unanswered;
  assert.match(failure.error?.message ?? '', /timed out/);
  assert.deepEqual(native.lifecycleTaints.map(row => row.kind), ['request_timeout']);
  assert.equal(writes, 2, 'one write per original request, including the timed-out request');
});

test('Graph initializer accepts the domain omitted-empty serialization without changing the original project', () => {
  const omitted = Object.freeze({ production_design: Object.freeze({}) });
  const explicit = Object.freeze({
    production_design: Object.freeze({ semantic_graphs: Object.freeze([]) }),
  });
  for (const project of [omitted, explicit]) {
    const original = JSON.stringify(project);
    const design = project.production_design;
    assert.equal(hasNoSemanticGraphs(project), true);
    assert.equal(project.production_design, design);
    assert.equal(JSON.stringify(project), original);
  }
  assert.equal(Object.hasOwn(omitted.production_design, 'semantic_graphs'), false);
});

test('Graph initializer rejects missing design and present malformed or nonempty semantic graphs', () => {
  for (const project of [undefined, null, {}, { production_design: undefined },
    { production_design: null }, { production_design: [] },
    { production_design: '' }, { production_design: 0 }, { production_design: false }]) {
    assert.equal(hasNoSemanticGraphs(project), false);
  }
  // Present undefined is not the domain's omitted JSON property.
  for (const semantic_graphs of [undefined, null, {}, '', 0, false, [{}]]) {
    assert.equal(hasNoSemanticGraphs({ production_design: { semantic_graphs } }), false);
  }
});


function developmentSourceSnapshots() {
  // Pure source-contract inputs, never a Store, Host or physical result.
  const id = n => '00000000-0000-4000-8000-' + String(n).padStart(12, '0');
  const before = {
    id:id(1), generation:id(2), active_branch:id(3), revision:5,
    scenes:[{ id:id(4), renderer:'motion-canvas', nodes:[{ id:id(5) }] }],
    deliverables:[{ id:id(6), included_scene_ids:[] }],
    production_design:{ heroes:[{ id:id(7), scene_id:id(4) }] },
  };
  const after = structuredClone(before);
  after.revision += 2;
  after.scenes.push({
    id:id(8), renderer:'manim-community', nodes:[], beats:[],
    camera:{ center_x:960, center_y:540, zoom:1, rotation_deg:0, safe_margin:0.05 },
    duration:{ num:'5', den:'1' },
  });
  return { before, after };
}
test('development source selection preserves authored Canvas and selects the new empty Manim scene', () => {
  const { before, after } = developmentSourceSnapshots();
  const retained = JSON.stringify({ before, after });
  const selection = developmentGraphSourceSelection(before, after);
  assert.deepEqual(selection, { projectId:before.id,
    sceneId:after.scenes[1].id, profileId:before.deliverables[0].id });
  assert.equal(Object.isFrozen(selection), true);
  assert.equal(JSON.stringify({ before, after }), retained);
  assert.equal(Object.hasOwn(after.production_design, 'semantic_graphs'), false);
});
test('development source selection rejects retagged authored scenes and changed original identities', () => {
  const mutations = [
    after => { after.scenes.pop(); after.scenes[0].renderer = 'manim-community'; },
    after => { after.scenes[0].nodes = []; },
    after => { after.production_design.heroes = []; },
    after => { after.scenes[1].nodes.push({ id:'unexpected' }); },
    after => { after.scenes[1].camera.zoom = 2; },
    after => { after.scenes[1].renderer = 'motion-canvas'; },
    after => { after.deliverables[0].id = after.id; },
    after => { after.id = after.generation; },
    after => { after.active_branch = after.generation; },
    after => { after.revision--; },
    after => { after.scenes[1].id = after.scenes[0].id; },
    after => { after.production_design.semantic_graphs = [{}]; },
  ];
  for (const mutate of mutations) {
    const { before, after } = developmentSourceSnapshots();
    mutate(after);
    assert.throws(() => developmentGraphSourceSelection(before, after),
      /GRAPH_DEV_INITIALIZER_NATIVE_MANIM_SOURCE_NOT_READY/);
  }
});
