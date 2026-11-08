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

test('a rejected old Native inspection cannot claim current Project/source identity', () => {
  const begin = ui.indexOf('async function inspectContext()');
  const end = ui.indexOf('async function send(operation:', begin);
  const inspection = ui.slice(begin, end);
  const rejected = inspection.split('} catch (reason) {')[1]?.split('} finally {')[0]
    ?? inspection.split('} catch {')[1]?.split('} finally {')[0];
  assert.ok(rejected, 'the original closed-rejection handler is present');
  // A remotely advanced project may have the same local edit epoch. Genuine
  // Native rejection must be attributed only to its original snapshot.
  for (const sourceGuard of [
    'mounted.current', 'token === epoch.current',
    'sequence === session.draft.sequence',
    'graphSame(graphSourceSnapshot(before), graphSourceSnapshot(current.current.project))',
    'profileId === session.draft.input.variant_profile_ref',
  ]) assert.ok(rejected.includes(sourceGuard), 'missing rejection source guard: ' + sourceGuard);
  assert.match(rejected, /terminal = "GRAPH_CONTEXT_INSPECTION_REJECTED"/);
  assert.doesNotMatch(rejected, /message\(reason\)|String\(reason\)|reason\.(?:message|stack)/);
});

test('validated Native context failures expose only closed stage/code attributes on the original rejection', () => {
  const begin = ui.indexOf('async function inspectContext()');
  const end = ui.indexOf('async function send(operation:', begin);
  const rejection = ui.slice(begin, end);
  assert.match(ui, /import \{ decodeGraphContextFailure \} from "\.\/semanticGraphContextFailure"/);
  assert.match(rejection, /decodeGraphContextFailure\(reason\)/);
  assert.match(rejection, /setContextFailure\(decoded \?/);
  assert.match(ui, /data-graph-context-failure-stage=\{currentContextFailure\?\.stage\}/);
  assert.match(ui, /data-graph-context-failure-code=\{currentContextFailure \?/);
  assert.match(ui, /inspectionPhase === "GRAPH_CONTEXT_INSPECTION_REJECTED"/);
  for (const guard of ['token === epoch.current', 'sequence === session.draft.sequence',
    'profileId === session.draft.input.variant_profile_ref',
    'graphSame(graphSourceSnapshot(before), graphSourceSnapshot(current.current.project))']) {
    assert.ok(rejection.includes(guard), 'rejection not bound to original source: ' + guard);
  }
  assert.match(ui, /setContextFailure\(null\)/);
  assert.doesNotMatch(rejection, /message\(reason\)|String\(reason\)|reason\.(?:message|stack)/);
});

test('Graph source rejection retains 097e first line and optional exact second line after decoder validation', () => {
  const prepare = caseSource.split('async function prepare(', 2)[1].split('\ntest(', 1)[0];
  assert.match(caseSource, /import \{ decodeGraphContextFailure \} from "\.\.\/src\/semanticGraphContextFailure"/);
  assert.match(prepare, /new Error\("GRAPH_SOURCE_ADMISSION_NOT_READY: " \+ phase \+ " \/ " \+ safeGates\)/);
  assert.match(prepare, /phase === "GRAPH_CONTEXT_INSPECTION_REJECTED"/);
  assert.match(prepare, /data-graph-context-failure-stage/);
  assert.match(prepare, /data-graph-context-failure-code/);
  assert.match(prepare, /decodeGraphContextFailure\(\{/);
  assert.match(prepare, /GRAPH_CONTEXT_FAILURE: /);
  assert.match(prepare, /verified\.error_code \?\? "NONE"/);
  assert.doesNotMatch(prepare, /message\(reason\)|String\(reason\)|JSON\.stringify\(.*(?:context|failure)/);
  assert.doesNotMatch(prepare, /setTimeout\(/);
});

test('a rejected real Graph context cannot render raw backend error through the inspection UI', () => {
  const begin = ui.indexOf('async function inspectContext()');
  const end = ui.indexOf('async function send(operation:', begin);
  assert.ok(begin >= 0 && end > begin);
  const inspection = ui.slice(begin, end);
  const rejection = inspection.split('} catch (reason) {')[1]?.split('} finally {')[0]
    ?? inspection.split('} catch {')[1]?.split('} finally {')[0];
  assert.ok(rejection, 'real readContext rejection handler exists');
  // The Host/Service retains private original diagnostics; its arbitrary
  // Error.message, path or DTO cannot be reflected into author-facing DOM.
  assert.doesNotMatch(rejection, /message\(reason\)|String\(reason\)|reason\.(?:message|stack)/);
  assert.match(rejection, /session\.error = "GRAPH_CONTEXT_INSPECTION_REJECTED:/);
  assert.match(rejection, /terminal = "GRAPH_CONTEXT_INSPECTION_REJECTED"/);
  assert.match(ui, /GRAPH_SAVE_CONTEXT_UNAVAILABLE/);
  assert.match(ui, /const canSave = saveGuards\.length === 0/);
});
