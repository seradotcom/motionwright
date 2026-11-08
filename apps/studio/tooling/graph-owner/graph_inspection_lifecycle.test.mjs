import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const ui = readFileSync(new URL('../../src/SemanticGraphWorkbench.tsx', import.meta.url), 'utf8');
const caseSource = readFileSync(new URL('../../tests/semantic-graph-service.spec.ts', import.meta.url), 'utf8');

// Source-owned terminal and gate vocabularies. These are labels only: neither
// the DOM nor this test can create a Native context or authorize any mutation.
const terminals = [
  'GRAPH_CONTEXT_INSPECTION_RESOLVED',
  'GRAPH_CONTEXT_INSPECTION_REJECTED',
  'GRAPH_CONTEXT_INSPECTION_OBSOLETE',
];
const guards = [
  'GRAPH_SAVE_READY', 'GRAPH_SAVE_BUSY', 'GRAPH_SAVE_PENDING',
  'GRAPH_SAVE_CONTEXT_UNAVAILABLE', 'GRAPH_SAVE_CONTEXT_BLOCKED',
  'GRAPH_SAVE_SOURCE_STALE', 'GRAPH_SAVE_CONTEXT_INVALID',
  'GRAPH_SAVE_INPUT_INVALID', 'GRAPH_SAVE_BACKUP_UNCONFIRMED',
  'GRAPH_SAVE_RECOVERY_UNCONFIRMED', 'GRAPH_SAVE_PROJECT_CHANGED',
  'GRAPH_SAVE_LOCKED', 'GRAPH_SAVE_IDENTITY_UNAVAILABLE',
  'GRAPH_SAVE_VARIANT_OCCUPIED', 'GRAPH_SAVE_SAVED_IDENTITY_UNAVAILABLE',
];

test('real Graph inspection terminal is observed before original Save admission', () => {
  const click = caseSource.indexOf('await inspect.click();');
  const terminal = caseSource.indexOf('data-graph-inspection-phase');
  const saveGate = caseSource.indexOf('data-graph-save-gate');
  const confirm = caseSource.indexOf('await expect(save,');
  assert.ok(click > 0 && click < terminal && terminal < saveGate && saveGate < confirm);
  assert.match(ui, /const value = await current\.current\.readContext\(before,/);
  assert.match(ui, /setInspectionPhase\("GRAPH_CONTEXT_INSPECTION_PENDING"\)/);
  assert.match(ui, /setInspectionPhase\(terminal\)/);
  assert.match(ui, /terminal = "GRAPH_CONTEXT_INSPECTION_RESOLVED"/);
  assert.match(ui, /terminal = "GRAPH_CONTEXT_INSPECTION_REJECTED"/);
  assert.match(caseSource, /toHaveAttribute\([\s\S]*?"data-graph-inspection-phase",\s*\/\^/);
  assert.match(caseSource, /timeout: 0/);
});

test('terminal and Save guard identifiers are closed and match UI and case source', () => {
  assert.equal(new Set([...terminals, ...guards]).size, terminals.length + guards.length);
  for (const id of [...terminals, ...guards]) {
    assert.match(id, /^GRAPH_[A-Z_]+$/);
    assert.ok(ui.includes('"' + id + '"'), 'missing UI code ' + id);
    assert.ok(caseSource.includes('"' + id + '"') || caseSource.includes(id),
      'missing test whitelist ' + id);
  }
  assert.match(ui, /const canSave = saveGuards\.length === 0/);
  assert.match(ui, /disabled=\{!canSave\}/);
  assert.match(ui, /data-graph-save-gate=\{saveGate\}/);
  assert.match(ui, /data-graph-save-gate="GRAPH_SAVE_SAVED_IDENTITY_UNAVAILABLE" disabled/);
  assert.match(caseSource, /closedGates\.has\(value\)/);
  assert.match(caseSource, /GRAPH_SAVE_GATE_UNRECOGNIZED/);
  assert.match(caseSource, /GRAPH_CONTEXT_INSPECTION_UNRECOGNIZED/);
  assert.match(caseSource, /new Error\("GRAPH_SOURCE_ADMISSION_NOT_READY: " \+ phase \+ " \/ " \+ safeGates\)/);
  assert.doesNotMatch(ui, /setContext\(\{[^\n]*availability: ["']ready["']/);
});

test('readContext rejection or stale source never changes the Save guard itself', () => {
  assert.match(ui, /terminal = "GRAPH_CONTEXT_INSPECTION_REJECTED"/);
  assert.match(ui, /token !== epoch\.current \|\| sequence !== session\.draft\.sequence/);
  assert.match(ui, /graphSame\(graphSourceSnapshot\(before\), graphSourceSnapshot\(current\.current\.project\)\)/);
  assert.match(ui, /GRAPH_SAVE_CONTEXT_UNAVAILABLE/);
  assert.match(ui, /GRAPH_SAVE_CONTEXT_BLOCKED/);
  assert.match(ui, /GRAPH_SAVE_SOURCE_STALE/);
  assert.match(ui, /GRAPH_SAVE_CONTEXT_INVALID/);
  assert.match(ui, /const canSave = saveGuards\.length === 0/);
});

test('original case uses actual service with no invented Native DTO, delay or retry', () => {
  assert.match(caseSource, /const service = await createGraphServiceCase\(info, "graph-flow"/);
  assert.match(caseSource, /await service\.attachFixturePage\(page, initial\.id\)/);
  assert.match(caseSource, /await prepare\(page, "system_flow"\)/);
  assert.match(caseSource, /await button\(page, "Save semantic Graph"\)\.click\(\)/);
  assert.doesNotMatch(caseSource, /page\.route\([^\n]*semantic_graph_authoring_context/);
  // Existing original test.setTimeout(120_000) remains unchanged. Inspect
  // waits inside that owner bound; prepare() does not introduce another timer.
  assert.match(caseSource, /test\.setTimeout\(120_000\)/);
  const prepare = caseSource.split('async function prepare(', 2)[1].split('\ntest(', 1)[0];
  assert.doesNotMatch(prepare, /\bsetTimeout\(/);
});
