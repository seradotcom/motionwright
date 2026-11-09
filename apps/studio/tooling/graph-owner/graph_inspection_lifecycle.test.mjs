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

test('a second same-render Native context inspection cannot dispatch twice or overwrite its phase', () => {
  const begin = ui.indexOf('async function inspectContext()');
  const end = ui.indexOf('async function send(operation:', begin);
  assert.ok(begin >= 0 && end > begin, 'original Native inspection path exists');
  const inspection = ui.slice(begin, end);
  assert.match(ui, /const inspectionToken = useRef<number \| null>\(null\)/);
  const guard = 'if (!current.current.readContext || inspecting || inspectionToken.current !== null || busy) return;';
  assert.ok(inspection.includes(guard), 'live token guard must precede Native call');
  assert.ok(inspection.indexOf(guard) < inspection.indexOf('const token = ++epoch.current'));
  assert.ok(inspection.indexOf('inspectionToken.current = token') <
    inspection.indexOf('await current.current.readContext('), 'latch must be set synchronously');
  assert.match(inspection, /if \(inspectionToken\.current === token\) \{\s*inspectionToken\.current = null;/);
  assert.match(inspection, /setInspectionPhase\(terminal\)/);
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

// React's reviewing flag belongs to the last render. Two author actions in
// that render must not launch two saved-outcome transport reads, and an older
// finally must not clear the newer review's in-progress state.
test('a second same-render saved Graph outcome review cannot dispatch a duplicate request', () => {
  const begin = ui.indexOf('async function readOutcome()');
  const end = ui.indexOf('function fork(', begin);
  assert.ok(begin >= 0 && end > begin, 'the original saved-outcome reader exists');
  const reader = ui.slice(begin, end);
  assert.match(ui, /const outcomeReviewToken = useRef<number \| null>\(null\)/);
  const guard = 'if (busy || reviewing || outcomeReviewToken.current !== null || !session.draft.pending) return;';
  assert.ok(reader.includes(guard), 'a synchronous live review latch must block same-render reentry');
  assert.ok(reader.indexOf(guard) < reader.indexOf('const token = ++epoch.current'));
  assert.ok(reader.indexOf('outcomeReviewToken.current = token') <
    reader.indexOf('await inspectGraphOutcome('), 'latch must be owned before the original transport');
  assert.match(reader, /if \(outcomeReviewToken\.current === token\) \{\s*outcomeReviewToken\.current = null;/);
  assert.match(reader, /setReviewing\(false\)/);
});

test('obsolete Graph outcome rejection does not report an error against a changed source', () => {
  const begin = ui.indexOf('async function readOutcome()');
  const end = ui.indexOf('function fork(', begin);
  assert.ok(begin >= 0 && end > begin, 'the Graph outcome inspector exists');
  const inspection = ui.slice(begin, end);
  assert.match(inspection, /const inspectedDraft = clone\(session\.draft\)/);
  assert.match(inspection, /const inspectedProject = clone\(current\.current\.project\)/);
  const rejected = inspection.split('} catch (reason) {')[1]?.split('} finally {')[0];
  assert.ok(rejected, 'the original rejected outcome reader exists');
  for (const guard of [
    'mounted.current', 'token === epoch.current',
    'graphSame(session.draft, inspectedDraft)',
    'graphSame(current.current.project, inspectedProject)',
  ]) assert.ok(rejected.includes(guard), 'old outcome error not bound to original intent: ' + guard);
  assert.match(rejected, /session\.error = message\(reason\)/);
});

// Preparation includes an asynchronous BrandProfile digest. A rejection that
// belongs to an old editor/source must not become the replacement Project's error.
test('late rejected Graph preparation cannot write an error into a replaced source', () => {
  const start = ui.indexOf('async function send(');
  const end = ui.indexOf('session.inFlight = true', start);
  assert.ok(start >= 0 && end > start, 'original Graph preparation boundary exists');
  const preflight = ui.slice(start, end);
  assert.match(preflight, /prepared = await prepareGraphSubmission\(/);
  const rejected = preflight.split('} catch (reason) {').at(-1)?.split('} finally {')[0];
  assert.ok(rejected, 'original asynchronous preparation catch exists');
  assert.match(rejected, /session\.draft\.pending\?\.id === id/, 'original pending intent still receives NOT_SENT on custody failure');
  for (const guard of [
    'mounted.current', 'token === epoch.current',
    'graphSame(source, session.draft)',
    'graphSame(graphSourceSnapshot(project), graphSourceSnapshot(current.current.project))',
  ]) assert.ok(rejected.includes(guard), 'stale preparation rejection is not source-bound: ' + guard);
  assert.match(rejected, /failGraphSubmission\(session\.draft, id, message\(reason\), true\)/);
});

// React state and the render-time canSave snapshot can lag a second local
// handler entry. The current mutable session, not only last-rendered guards,
// must reject duplicate preparation and commit for the same original Graph.
test('a second Graph Save handler entry cannot start while its original intention is active', () => {
  const begin = ui.indexOf('async function send(operation:');
  const end = ui.indexOf('async function readOutcome()', begin);
  assert.ok(begin >= 0 && end > begin, 'the actual Graph Save path exists');
  const save = ui.slice(begin, end);
  const guard = 'if (!canSave || session.preparing || session.inFlight || session.draft.pending) return;';
  assert.ok(save.includes(guard), 'the live session must forbid duplicate Save before preflight');
  assert.ok(save.indexOf(guard) < save.indexOf('const id = crypto.randomUUID()'));
  assert.ok(save.indexOf(guard) < save.indexOf('session.preparing = true'));
  assert.match(save, /session\.inFlight = true/);
  assert.match(save, /finally \{ session\.inFlight = false; notify\(session\); \}/);
});

// A rejected refresh from the former Project must not overwrite the active
// editor's error after an external Project update or a new author draft.
test('a late rejected Project refresh cannot report its old error to a new source', () => {
  const begin = ui.indexOf('async function refresh()');
  const end = ui.indexOf('function retryBackup()', begin);
  assert.ok(begin >= 0 && end > begin, 'the original Project refresh boundary exists');
  const refresh = ui.slice(begin, end);
  assert.match(refresh, /const originalProject = clone\(current\.current\.project\)/);
  assert.match(refresh, /const originalDraft = clone\(session\.draft\)/);
  assert.match(refresh, /const token = epoch\.current/);
  assert.match(refresh, /await current\.current\.refreshProject\(\)/);
  const rejection = refresh.split('catch (reason) {')[1];
  assert.ok(rejection, 'refresh rejection handler exists');
  for (const guard of [
    'mounted.current', 'token === epoch.current',
    'graphSame(current.current.project, originalProject)',
    'graphSame(session.draft, originalDraft)',
  ]) assert.ok(rejection.includes(guard), 'obsolete refresh error is not source-bound: ' + guard);
  assert.match(rejection, /session\.error = message\(reason\)/);
});

// The saved-view coordinator rejects an older read when a second readView
// starts. React's busy prop can lag both click handlers in the same render,
// so the Graph editor must not supersede its own still-pending refresh.
test('a second same-render saved Project refresh cannot supersede the original read', () => {
  const begin = ui.indexOf('async function refresh()');
  const end = ui.indexOf('function retryBackup()', begin);
  assert.ok(begin >= 0 && end > begin, 'the original saved Project refresh exists');
  const refresh = ui.slice(begin, end);
  assert.match(ui, /const refreshToken = useRef<number \| null>\(null\)/);
  const guard = 'if (refreshToken.current !== null) return;';
  assert.ok(refresh.includes(guard), 'must reject reentry before a second readView');
  assert.ok(refresh.indexOf(guard) < refresh.indexOf('const token = epoch.current'));
  assert.ok(refresh.indexOf('refreshToken.current = token') <
    refresh.indexOf('await current.current.refreshProject()'), 'latch acquired synchronously');
  assert.match(refresh, /finally \{\s*if \(refreshToken\.current === token\) refreshToken\.current = null;/);
});
