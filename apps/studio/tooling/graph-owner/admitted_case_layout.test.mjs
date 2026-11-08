import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { readFileSync } from 'node:fs';
import { inside } from '../../tests/support/graph-owner/custody.mjs';
import { graphCaseLayout } from '../../tests/support/graph-owner/factory.mjs';
import { originalGraphCaseLayout } from '../../tests/support/graph-owner/producer_guard.mjs';

test('authenticated g1-g6 layout, never the transient Playwright output, is checked for containment', () => {
  // Use short absolute roots: real R74 Linux unix-domain sockets have a
  // strict 100-byte budget, and the Windows checkout is intentionally long.
  const casesRoot = path.join(path.parse(process.cwd()).root, 'c', 'graph-cases');
  const playwrightRoot = path.join(path.parse(process.cwd()).root, 'test-results', 'graph-flow', 'native-graph-custody');
  assert.throws(() => inside(casesRoot, playwrightRoot), /PATH_ESCAPED/);

  const cases = [
    'graph-flow', 'graph-label-causal-diagram', 'graph-label-system-flow',
    'graph-lost-reply', 'graph-removal', 'graph-source-review',
  ];
  for (const [ordinal, id] of cases.entries()) {
    const layout = graphCaseLayout(casesRoot, id);
    assert.deepEqual(layout, originalGraphCaseLayout(casesRoot, id));
    assert.equal(layout.privateRoot, path.join(casesRoot, 'g' + (ordinal + 1)));
    assert.equal(inside(casesRoot, layout.privateRoot), layout.privateRoot);
    assert.equal(layout.databasePath, path.join(layout.privateRoot, 'store', 'motionwright.sqlite3'));
  }
  // Source-level guard: the production factory consumes the independently
  // admitted layout, not the provisional outputDir passed by the browser.
  const factory = readFileSync(new URL('../../tests/support/graph-owner/factory.mjs', import.meta.url), 'utf8');
  const factoryCreate = factory.split('static async create(options, adapters) {')[1]
    ?.split('constructor(options, admission, adapters) {')[0];
  assert.ok(factoryCreate, 'Graph production factory preflight must exist');
  assert.doesNotMatch(factoryCreate, /inside\(admission\.execution\.cases_root,\s*options\.privateRoot\)/);
  assert.match(factoryCreate, /inside\(admission\.execution\.cases_root,\s*layout\.privateRoot\)/);
  // Keep the mandatory post-normalization constructor binding: callers must
  // never override the independently admitted case root or physical database.
  assert.match(factory, /options\.privateRoot === expectedLayout\.privateRoot && options\.databasePath === expectedLayout\.databasePath/);
  // The earlier producer admission also used to reject the transient caller
  // path before any R74 review. Test both boundaries so the bug cannot recur.
  const producerGuard = readFileSync(new URL('../../tests/support/graph-owner/producer_guard.mjs', import.meta.url), 'utf8');
  const producerAdmission = producerGuard.split('export async function admitOriginalProducers(options) {')[1]
    ?.split('export function assertOriginalProducerAdmission(admission) {')[0];
  assert.ok(producerAdmission, 'Original producer preflight must exist');
  assert.doesNotMatch(producerAdmission, /inside\(reviewed\.execution\.cases_root,\s*options\.privateRoot\)/);
  assert.match(producerAdmission, /originalGraphCaseLayout\(reviewed\.execution\.cases_root,\s*options\.caseId\)/);
});