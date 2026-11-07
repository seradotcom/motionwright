import test from 'node:test';
import assert from 'node:assert/strict';
import { GRAPH_CONTINUATION, GRAPH_CONTINUATION_CASES, GRAPH_CONTINUATION_ORIGIN,
  inspectGraphCaseSelection, graphNativeCaseSelected } from '../../tests/support/graph-owner/producer_guard.mjs';

function fixture() {
  const source = { repository_root: '/source/g', sha: 'a'.repeat(40), run_id: '42359999999', run_attempt: 1 };
  const job = { name: 'Real SQLite and Studio / phase1-native-graph', id: 128400000001,
    runner_id: 2000000001, runner_name: 'GitHub Actions 2000000001', run_id: Number(source.run_id),
    run_attempt: 1, head_sha: source.sha, status: 'in_progress', conclusion: null };
  const cases_root = '/source/v/e/gc' + source.run_id + '-1';
  const deadline = { graph_continuation_selector: GRAPH_CONTINUATION,
    binding: { RUNNER_NAME: job.runner_name }, jobs: { jobs: [job] } };
  const continuation = { schema: 'motionwright.graph-unstarted-continuation/1', selector: GRAPH_CONTINUATION,
    origin: structuredClone(GRAPH_CONTINUATION_ORIGIN), selected_cases: [...GRAPH_CONTINUATION_CASES],
    current: { run_id: source.run_id, run_attempt: 1, job_id: job.id,
      runner_id: job.runner_id, runner_name: job.runner_name, cases_root },
    original_contract_cases: 6, required_current_cases: 5, simultaneous_owner_limit: 1,
    historical_cleanup_claimed: false, historical_owner_credentials_or_tickets_reused: false,
    automatic_retry: false, formal_acceptance: false };
  return { index: { source, contract: { execution: { cases_root, continuation } } }, deadline };
}

test('continuation selects five and denies historical UNKNOWN before any owner or ordinary fallback', () => {
  for (const id of GRAPH_CONTINUATION_CASES) assert.equal(graphNativeCaseSelected(GRAPH_CONTINUATION, id), true);
  assert.throws(() => graphNativeCaseSelected(GRAPH_CONTINUATION, 'graph-source-review'), /UNKNOWN_CASE_EXCLUDED/);
  assert.throws(() => graphNativeCaseSelected('unstarted-after-42356210002', 'graph-flow'), /UNKNOWN_NATIVE_SELECTOR/);
  assert.equal(graphNativeCaseSelected(undefined, 'graph-source-review'), false);
  assert.equal(graphNativeCaseSelected('all', 'graph-source-review'), true);
});

test('continuation index retains exact original UNKNOWN and binds five to a distinct runner', () => {
  const { index, deadline } = fixture();
  assert.deepEqual(inspectGraphCaseSelection(index, deadline), [...GRAPH_CONTINUATION_CASES]);
  assert.equal(index.contract.execution.continuation.origin.excluded_case.effects, 'UNKNOWN');
  for (const [field, value] of [['runner_id', GRAPH_CONTINUATION_ORIGIN.runner_id],
    ['runner_name', GRAPH_CONTINUATION_ORIGIN.runner_name], ['id', GRAPH_CONTINUATION_ORIGIN.job_id]]) {
    const wrong = structuredClone(deadline); wrong.jobs.jobs[0][field] = value;
    assert.throws(() => inspectGraphCaseSelection(index, wrong), /NEW_RUNNER_AND_NAMESPACE/);
  }
});

test('continuation rejects added sixth case, changed origin and an old owner namespace', () => {
  const { index, deadline } = fixture();
  for (const mutate of [
    value => value.contract.execution.continuation.selected_cases.push('graph-source-review'),
    value => { value.contract.execution.continuation.origin.excluded_case.effects = 'NONE'; },
    value => { value.contract.execution.continuation.historical_cleanup_claimed = true; },
    value => { value.contract.execution.continuation.simultaneous_owner_limit = 2; },
    value => { value.contract.execution.cases_root = GRAPH_CONTINUATION_ORIGIN.cases_root; },
  ]) {
    const wrong = structuredClone(index); mutate(wrong);
    assert.throws(() => inspectGraphCaseSelection(wrong, deadline), /J1_CONTINUATION_/);
  }
});

test('continuation requires its original bootstrap selector and rejects historical run', () => {
  const { index, deadline } = fixture();
  const wrong = structuredClone(deadline); delete wrong.graph_continuation_selector;
  assert.throws(() => inspectGraphCaseSelection(index, wrong), /BOOTSTRAP_SELECTOR/);
  const old = structuredClone(index); old.source.run_id = GRAPH_CONTINUATION_ORIGIN.run_id;
  assert.throws(() => inspectGraphCaseSelection(old, deadline), /NEW_RUNNER_AND_NAMESPACE/);
});
