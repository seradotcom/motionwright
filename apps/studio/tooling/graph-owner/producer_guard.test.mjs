import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import * as fs from "node:fs";
import path from "node:path";
import { tmpdir } from "node:os";
import { stripTypeScriptTypes } from "node:module";
import { SharedPool } from "../../tests/support/graph-owner/custody.mjs";
import { graphCaseLayout } from "../../tests/support/graph-owner/factory.mjs";
import { inspectSourceBoundCandidate, requireNativeGraphParentAdmission,
  admitOriginalProducers, runIndependentSameStoreReopen, inspectR74StatusCore,
  assertOriginalProducerAdmission, inspectProducerOriginalsCore, AUTHORITY_SOURCE_PATHS,
  BINARY_ROLES, PRODUCER_INDEX_SCHEMA, GRAPH_BUDGET } from "../../tests/support/graph-owner/producer_guard.mjs";

function candidate() {
  const operations = ["authoring_context", "upsert"];
  const launches = [{ launch_index: 0, operations }, { launch_index: 1, operations: [] }];
  const cli = 1 + 2 + 8 * operations.length;
  const body = {
    schema: "motionwright.native-graph-source-case/1",
    case_id: "graph-flow", test_title: "Synthetic flow stores exact event times and rejects a real stale CAS without a new event",
    source: { head: "a".repeat(40), tree: "b".repeat(40),
      test_file_sha256: "c".repeat(64), sdk: "d".repeat(40) },
    launches, prebackend_denials: ["stale CAS grant"],
    proposed_parent_budget: { cli, files: cli * 3, bytes: cli * 256 },
    admission: "DENY_INDEPENDENT_ORIGINAL_PRODUCER_MISSING",
    native_authoring_approved: false, automatic_retry: false,
  };
  return { ...body, digest_sha256: createHash("sha256").update(JSON.stringify(body)).digest("hex") };
}
test("reviewed source shape never grants a Native callback", () => {
  const result = inspectSourceBoundCandidate(candidate());
  assert.equal(result.status, "DENY_INDEPENDENT_ORIGINAL_NATIVE_PRODUCER_AND_BUILD_REQUIRED");
  assert.equal(result.admitted_callback_cap, null);
  assert.equal(result.authored_callback_candidates, 2);
  assert.equal(result.side_effects, 0);
  assert.throws(() => requireNativeGraphParentAdmission(candidate(), { approved: true }),
    /GRAPH_NATIVE_PARENT_ORIGINALS_AND_INDEPENDENT_APPROVAL_MISSING/);
});
test("caller approval, cap override or replay cannot amend a source plan", () => {
  for (const change of [
    { callback_cap: 100 }, { native_authoring_approved: true },
    { automatic_retry: true }, { admission: "PASS" },
  ]) {
    assert.throws(() => inspectSourceBoundCandidate({ ...candidate(), ...change }), /GRAPH_/);
  }
});
test("invalid callback identity, launch order and capacities fail closed", () => {
  const badOperations = candidate();
  badOperations.launches[0].operations = ["unreviewed-backend-effect"];
  assert.throws(() => inspectSourceBoundCandidate(badOperations), /GRAPH_UNAUTHORED_CALLBACK/);
  const skipped = candidate();
  skipped.launches[1].launch_index = 8;
  assert.throws(() => inspectSourceBoundCandidate(skipped), /GRAPH_LAUNCH_ORDER/);
  const forged = candidate();
  forged.proposed_parent_budget.bytes += 1;
  assert.throws(() => inspectSourceBoundCandidate(forged), /GRAPH_PARENT_CANDIDATE_MATH/);
});
test("missing or tampered plan digest and source identity are never accepted", () => {
  const missing = candidate(); delete missing.digest_sha256;
  assert.throws(() => inspectSourceBoundCandidate(missing), /GRAPH_CANDIDATE_SHAPE/);
  const altered = candidate(); altered.source.head = "x".repeat(64);
  assert.throws(() => inspectSourceBoundCandidate(altered), /GRAPH_CASE_SOURCE_IDENTITY/);
  const changed = candidate(); changed.test_title += " amended after source seal";
  assert.throws(() => inspectSourceBoundCandidate(changed), /GRAPH_CASE_DIGEST/);
});

test("actual companion six case outputs reach review DENY rather than invalid source width", async () => {
  const { sixSourceAuthoredGraphCases } = await import("../../tests/support/nativeGraphCase.ts");
  const wrapper = fs.readFileSync(new URL("../../tests/support/nativeGraphCase.ts", import.meta.url), "utf8");
  const declared = wrapper.match(/const TEST_SHA = "([0-9a-f]{64})";/)?.[1];
  const actual = createHash("sha256").update(fs.readFileSync(new URL("../../tests/semantic-graph-service.spec.ts", import.meta.url))).digest("hex");
  assert.match(declared, /^[0-9a-f]{64}$/);
  if (actual !== declared) {
    // A composed caller is not equivalent to the historical diagnostic source.
    // Preserve the actual mismatch and require DENY; production uses R74/J1.
    assert.throws(sixSourceAuthoredGraphCases, /GRAPH_CASE_SOURCE_CHANGED/);
    return;
  }
  const real = sixSourceAuthoredGraphCases();
  assert.equal(real.length, 6);
  for (const candidate of real) {
    const review = inspectSourceBoundCandidate(candidate);
    assert.equal(review.candidate_sha256, candidate.digest_sha256);
    assert.equal(review.status, "DENY_INDEPENDENT_ORIGINAL_NATIVE_PRODUCER_AND_BUILD_REQUIRED");
    assert.equal(review.admitted_callback_cap, null);
    assert.equal(review.side_effects, 0);
  }
});
test("wrong source widths or source forgery fail before review", () => {
  const longGit = candidate();
  longGit.source.head = "a".repeat(64);
  assert.throws(() => inspectSourceBoundCandidate(longGit), /GRAPH_CASE_SOURCE_IDENTITY/);
  const shortDigest = candidate();
  shortDigest.source.test_file_sha256 = "c".repeat(40);
  assert.throws(() => inspectSourceBoundCandidate(shortDigest), /GRAPH_CASE_SOURCE_IDENTITY/);
  const alienSDK = candidate();
  alienSDK.source.sdk = "z".repeat(40);
  assert.throws(() => inspectSourceBoundCandidate(alienSDK), /GRAPH_CASE_SOURCE_IDENTITY/);
});
test("retained R57 private factory entry points stay fail closed", async () => {
  await assert.rejects(admitOriginalProducers({ approved: true }),
    /SOURCE_PRODUCER_ORIGINALS_AND_REVIEWED_CONTRACT_UNAVAILABLE/);
  await assert.rejects(runIndependentSameStoreReopen({ verified: true }),
    /INDEPENDENT_SAME_STORE_REOPEN_PRODUCER_AND_REVIEWED_CONTRACT_UNAVAILABLE/);
});


// LOCAL SYNTHETIC CONTROLS ONLY: no GitHub, build, SDK or Native execution.
const sdkSha = 'e64a2d28d1d866c539a2c40824cbb57f86a13c10';
const hash = raw => createHash('sha256').update(raw).digest('hex');
const rawJson = value => Buffer.from(JSON.stringify(value) + '\n');
const oldDigest = ref => ({ bytes: ref.size_bytes, sha256: ref.sha256 });
const callgraph = [
  'apps/studio/tests/semantic-graph-service.spec.ts', 'apps/studio/tests/support/scopedChangeService.ts',
  'apps/studio/src/SemanticGraphWorkbench.tsx', 'apps/studio/src/semanticGraphService.ts', 'apps/studio/src/App.tsx',
  'apps/studio/src/api.ts', 'apps/studio/src/projectMutation.ts', 'apps/studio/src/semanticGraphDraft.ts',
  'apps/studio/src/ProductionDesignWorkspace.tsx', 'crates/service/examples/studio_review_session.rs',
  'crates/native/examples/native-graph-review-session.rs', 'crates/native/src/production/transport_custody.rs',
  'crates/native/src/production/graph_authoring.rs', 'crates/native/src/production.rs',
];
const targetSources = {
  semwright: 'src/semwright.rs', semwrightd: 'src/semwrightd.rs', semwright_sandbox: 'src/sandbox.rs',
  ordinary_service: 'crates/service/examples/studio_review_session.rs',
  native_graph_session: 'crates/native/examples/native-graph-review-session.rs',
  native_provider: 'crates/native/src/bin/native-provider.rs',
  shared_state_actor: 'crates/native/examples/shared-state-actor.rs',
  manim_runner: 'crates/manim-runner/src/main.rs',
};

function fixture(name) {
  const output = fs.mkdtempSync(path.join(tmpdir(), "graph-r74-inert-originals-"));
  const root = path.join(output, name), repo = path.join(root, 'repo'), sdk = path.join(root, 'sdk'), runtime = path.join(root, 'runtime');
  for (const dir of [repo, sdk, runtime, path.join(root, 'fonts')]) fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const put = (name, raw) => {
    const file = path.isAbsolute(name) ? name : path.join(root, name);
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    raw = Buffer.isBuffer(raw) ? raw : Buffer.from(raw);
    fs.writeFileSync(file, raw); return { path: file, sha256: hash(raw), size_bytes: raw.length };
  };
  const json = (name, value) => put(name, rawJson(value));
  const replace = (ref, value) => Object.assign(ref, json(ref.path, value));
  const load = ref => JSON.parse(fs.readFileSync(ref.path));
  const sourceFiles = {}, sdkFiles = {};
  const sourceFile = (name, raw = '// inert private fixture ' + name + '\n') => {
    const item = put(path.join(repo, name), raw); sourceFiles[name] = oldDigest(item); return item;
  };
  for (const name of [...callgraph, ...AUTHORITY_SOURCE_PATHS, 'apps/studio/tests/support/nativeGraphCase.ts',
    'Cargo.lock', 'rust-toolchain.toml', 'apps/studio/package-lock.json',
    'crates/manim-profile/src/semantic_graph_source.rs']) sourceFile(name);
  sourceFile('SOURCE_LOCK.json', rawJson({ dependencies: { semwright: { revision: sdkSha } } }));
  for (const role of Object.keys(targetSources)) {
    const isSdk = ['semwright', 'semwrightd', 'semwright_sandbox'].includes(role);
    if (isSdk) sdkFiles[targetSources[role]] = oldDigest(put(path.join(sdk, targetSources[role]), '// sdk fixture\n'));
    else if (!sourceFiles[targetSources[role]]) sourceFile(targetSources[role]);
  }
  sdkFiles['Cargo.lock'] = oldDigest(put(path.join(sdk, 'Cargo.lock'), 'fixture locked SDK\n'));
  const source = { repository_root: repo, sha: 'a'.repeat(40), tree: 'b'.repeat(40), sdk_root: sdk, sdk_sha: sdkSha,
    locks: Object.fromEntries(['Cargo.lock', 'SOURCE_LOCK.json', 'apps/studio/package-lock.json', 'rust-toolchain.toml'].map(name => [name, sourceFiles[name].sha256])),
    run_id: '12345', run_attempt: 1 };
  const binaries = Object.fromEntries(BINARY_ROLES.map(role => [role, put('bin/' + role, 'INERT_NOT_EXECUTABLE:' + role + '\n')]));
  function processRecord(label, argv, cwd, stdout, extra = {}) {
    const out = put('originals/' + label + '.stdout.log', stdout), err = put('originals/' + label + '.stderr.log', '');
    const command = { argv, cwd, started_utc: '2026-10-29T00:00:00+00:00', finished_utc: '2026-10-29T00:00:01+00:00',
      process_observation_schema: 'motionwright.studio-command-process-observation/2', process_boundary: 'OWNED_POSIX_SESSION_GROUP',
      pid: 321, exit_code: 0, owned_group_after_wait: 'ABSENT', escaped_descendants: 'NOT_OBSERVED',
      diagnostic_custody: 'DIRECT_PROCESS_EXIT_OBSERVED', selected_environment: {}, cleanup_errors: [],
      stdout: oldDigest(out), stderr: oldDigest(err), ...extra };
    return { record: { command: json('originals/' + label + '.command.json', command), stdout: out, stderr: err },
      expected: { argv, cwd, selected_environment: {} } };
  }
  const builds = {}, reviewedBuilds = {};
  for (const role of Object.keys(targetSources)) {
    const isSdk = ['semwright', 'semwrightd', 'semwright_sandbox'].includes(role), cwd = isSdk ? sdk : repo;
    const target = { package_id: 'private-fixture#' + role + '@0.0.0', name: role, kind: ['example'], src_path: path.join(cwd, targetSources[role]) };
    const artifact = { reason: 'compiler-artifact', package_id: target.package_id,
      target: { name: target.name, kind: target.kind, src_path: target.src_path }, executable: binaries[role].path,
      fresh: false, profile: { test: false } };
    const process = processRecord(role, ['/fixture/cargo', 'build', '--locked', '--example', role, '--jobs', '1', '--message-format=json'], cwd,
      Buffer.concat([rawJson(artifact), rawJson({ reason: 'build-finished', success: true })]));
    builds[role] = { process: process.record, retained_binary: put('originals/' + role + '.binary.original', fs.readFileSync(binaries[role].path)) };
    reviewedBuilds[role] = { command: process.expected, target };
  }
  const stdin = json('originals/phase1-service-preflight.stdin.original', { id: 'phase1-service-preflight', command: 'bootstrap', args: { request: { project_id: null } } });
  const preflight = processRecord('phase1-service-preflight', [binaries.ordinary_service.path, '--database', path.join(root, 'unused-preflight.sqlite3')], repo,
    rawJson({ id: 'phase1-service-preflight', ok: true, result: { test_transport: 'service-test-jsonl', native_sdk: { pinned_revision: sdkSha } } }),
    { stdin: { path: path.basename(stdin.path), ...oldDigest(stdin) } });
  const runtimeReceipt = json(path.join(runtime, 'runtime.json'), { schema: 2, fixture: true });
  const runtimeInventory = { schema: 'motionwright.manim.runtime-inventory/2', context: { source_sha: source.sha, semwright_sha: sdkSha, run_id: source.run_id, run_attempt: '1' },
    manim_version: '0.21.0', pyav_version: '15.0.0', native_render_performed: false, files: 1, inventory_sha256: 'c'.repeat(64),
    runtime_receipt_sha256: runtimeReceipt.sha256,
    binaries: { python: { path: binaries.python3.path, ...oldDigest(binaries.python3) }, ffmpeg: { path: binaries.ffmpeg.path, ...oldDigest(binaries.ffmpeg) } } };
  const runtimeFiles = [], intakeRows = [];
  for (let i = 0; i < 15; i++) {
    const sourceName = i < 9 ? 'crates/manim-profile/testdata/graph-fonts/fixture' + i + '.woff2' : 'tooling/fixture_helper' + i + '.py';
    const relativePath = 'graph-profile-v1/' + (i < 9 ? 'fonts/' : 'helpers/') + path.basename(sourceName);
    const src = sourceFile(sourceName), ref = put(path.join(runtime, relativePath), fs.readFileSync(src.path));
    runtimeFiles.push({ relative_path: relativePath, ref });
    intakeRows.push({ source_relative_path: sourceName, runtime_relative_path: relativePath, sha256: ref.sha256, size_bytes: ref.size_bytes,
      pin_origin: 'crates/manim-profile/src/semantic_graph_source.rs::' + (i < 9 ? 'BUILTIN_ASSETS' : 'helper_sources/include_bytes!') });
  }
  const intake = { schema: 'motionwright.manim.graph-runtime-source-intake/1', status: 'SOURCE_INTAKE_ONLY', profile_id: 'mw-manim-semantic-graph-v1',
    source_root: repo, runtime_root: runtime, profile_admitted: false, native_render_performed: false,
    compiled_runner_pin_agreement_verified: false, native_font_open_observed: false, pango_layout_observed: false,
    files: intakeRows, files_count: 15,
    compiler_source: { relative_path: 'crates/manim-profile/src/semantic_graph_source.rs', sha256: sourceFiles['crates/manim-profile/src/semantic_graph_source.rs'].sha256, size_bytes: sourceFiles['crates/manim-profile/src/semantic_graph_source.rs'].bytes },
    source_lock: { relative_path: 'SOURCE_LOCK.json', sha256: sourceFiles['SOURCE_LOCK.json'].sha256, size_bytes: sourceFiles['SOURCE_LOCK.json'].bytes } };
  const intakeRef = json('originals/intake.json', intake);
  put(path.join(runtime, 'graph-profile-v1/source-closure.json'), fs.readFileSync(intakeRef.path));
  const inventoryRef = json('originals/runtime-inventory.json', runtimeInventory);
  const runtimeProcess = processRecord('runtime-provision', ['/fixture/python', 'existing-runtime-producer.py'], repo, 'inert fixture producer observation\n');
  const roster = { schema: 'PRIVATE_INERT_ROSTER_FIXTURE', cases: [{ case_id: 'graph-source-review', title: 'private fixture', callback_caps: [5, 0] }] };
  const enforcement = { schema: 'PRIVATE_INERT_ENFORCEMENT_FIXTURE', source: source.sha, not_a_real_implementation: true };
  const observation = { checkout: source.sha, tree: source.tree, files: sourceFiles };
  const sdkObservation = { checkout: sdkSha, tree: 'd'.repeat(40), files: sdkFiles };
  const index = { schema: PRODUCER_INDEX_SCHEMA, source, binaries,
    runtime: { root: runtime, manifest: inventoryRef.path, manifest_sha256: inventoryRef.sha256, intake_receipt: intakeRef.path, intake_receipt_sha256: intakeRef.sha256, fontconfig_root: path.join(root, 'fonts') },
    source_original: json('originals/receipt.json', { run_id: source.run_id, run_attempt: '1', source_before: observation, source_after: observation }),
    sdk_original: json('originals/sdk-receipt.json', { run_id: source.run_id, run_attempt: '1', source_before: sdkObservation, source_after: sdkObservation }),
    builds, ordinary_preflight: { process: preflight.record, stdin },
    runtime_originals: { producer: runtimeProcess.record, inventory: inventoryRef, receipt: runtimeReceipt, intake: intakeRef, profile_files: runtimeFiles },
    callback_roster: json('originals/callback-roster.json', roster), native_enforcement: json('originals/native-enforcement.json', enforcement) };
  const reviewed = { index_sha256: '', source: structuredClone(source), source_files: structuredClone(sourceFiles), sdk_tree: sdkObservation.tree,
    sdk_files: structuredClone(sdkFiles), builds: reviewedBuilds,
    writer_sources: Object.fromEntries([...Object.keys(targetSources), 'source_observation', 'sdk_observation', 'ordinary_preflight', 'runtime']
      .map(role => [role, ['tooling/ci_canvas_service.py', '.github/workflows/studio-service-verification.yml']])),
    ordinary_preflight: preflight.expected,
    runtime: { command: runtimeProcess.expected, inventory: structuredClone(runtimeInventory), intake: structuredClone(intake), profile_files: structuredClone(runtimeFiles) },
    callback_roster: structuredClone(roster), native_enforcement: structuredClone(enforcement),
    native_enforcing_source: structuredClone(sourceFiles['crates/native/examples/native-graph-review-session.rs']),
    cases: { 'graph-source-review': { title: 'private fixture', callback_caps: [5, 0], roster_source_files: [...callgraph] } } };
  // Existing J1 original-reader fixture extended only with the acyclic contract.
  // These inert bytes cannot produce a branded admission or run a Native child.
  reviewed.execution = { scope: 'phase1-native-graph', job: 'real-service', job_id: 456,
    deadline_original: json('originals/deadline.json', { fixture_only: true }),
    deadline_unix_ms: Date.now() + 10800000, producer_root: root, cases_root: path.join(root, 'cases'), budget: GRAPH_BUDGET };
  fs.mkdirSync(reviewed.execution.cases_root);
  const cases = ['graph-source-review', 'graph-flow', 'graph-lost-reply', 'graph-removal', 'graph-label-causal-diagram', 'graph-label-system-flow'];
  reviewed.cases = Object.fromEntries(cases.map(case_id => [case_id,
    { title: 'inert private fixture ' + case_id, callback_caps: [5, 0], roster_source_files: [...callgraph] }]));
  reviewed.callback_roster = { schema: 'PRIVATE_INERT_ROSTER_FIXTURE', cases: cases.map(case_id =>
    ({ case_id, title: reviewed.cases[case_id].title, callback_caps: [5, 0] })) };
  replace(index.callback_roster, reviewed.callback_roster);
  for (const [original, files, sourceRoot, label] of [[index.source_original, sourceFiles, repo, 'product'], [index.sdk_original, sdkFiles, sdk, 'sdk']]) {
    const record = load(original);
    record.retained_files = Object.fromEntries(Object.keys(files).map(name =>
      [name, put('retained-' + label + '/' + name, fs.readFileSync(path.join(sourceRoot, name)))]));
    replace(original, record);
  }
  index.contract = Object.fromEntries(Object.entries(reviewed).filter(([key]) => !['index_sha256', 'source'].includes(key)));
  index.contract = structuredClone(index.contract);
  const indexPath = path.join(root, 'private-index.json');
  const seal = () => { const ref = json(indexPath, index); reviewed.index_sha256 = ref.sha256; return ref; };
  const updateStream = (process, value) => {
    const ref = put(process.stdout.path, value); Object.assign(process.stdout, ref);
    const cmd = load(process.command); cmd.stdout = oldDigest(ref); replace(process.command, cmd);
  };
  seal(); return { root, repo, indexPath, index, reviewed, put, json, replace, load, seal, updateStream };
}


function r74Fixture() {
  const binding = { source_sha: 'a'.repeat(40), run_id: '12345', case_id: 'graph-source-review', index_sha256: 'f'.repeat(64) };
  const run = { id: 12345, run_attempt: 1, head_sha: binding.source_sha,
    repository: { full_name: 'seradotcom/motionwright' }, head_repository: { full_name: 'seradotcom/motionwright' },
    event: 'workflow_dispatch', status: 'in_progress', path: '.github/workflows/studio-service-verification.yml', workflow_id: 45,
    actor: { type: 'User', id: 7 }, triggering_actor: { type: 'User', id: 99 } };
  const status = { id: 300, context: 'mw-owner/phase1-native-graph/12345/1/graph-source-review', state: 'success',
    creator: { type: 'User', id: 7 }, target_url: 'https://github.com/seradotcom/motionwright/actions/runs/12345',
    description: 'admission-sha256:' + binding.index_sha256 };
  return { binding, run, status };
}
test('R74 selects newest case-insensitive context across every creator before validating', () => {
  const { binding, run, status } = r74Fixture();
  assert.equal(inspectR74StatusCore(run, [{ ...status, context: status.context.toUpperCase() }], binding).status_id, 300);
  for (const newer of [{ ...status, id: 301, state: 'failure' }, { ...status, id: 301, creator: { type: 'User', id: 99 } },
    { ...status, id: 301, state: 'pending' }]) {
    assert.throws(() => inspectR74StatusCore(run, [newer, status], binding), /J1_R74_NEWEST_STATUS_NOT_AUTHORIZED/);
  }
  assert.throws(() => inspectR74StatusCore(run, [status, { ...status, id: 301 }], binding), /J1_R74_STATUS_ORDER/);
});
test('R74 binds raw index digest, original actor, exact source, run and attempt without an approval flag', () => {
  const { binding, run, status } = r74Fixture();
  for (const changed of [{ ...status, description: 'admission-sha256:' + '0'.repeat(64) },
    { ...status, target_url: status.target_url + '/attempts/2' }, { ...status, creator: run.triggering_actor }]) {
    assert.throws(() => inspectR74StatusCore(run, [changed], binding), /J1_R74_/);
  }
  for (const changed of [{ ...run, run_attempt: 2 }, { ...run, head_sha: 'b'.repeat(40) },
    { ...run, event: 'pull_request' }, { ...run, actor: { type: 'Bot', id: 7 } }]) {
    assert.throws(() => inspectR74StatusCore(changed, [status], binding), /J1_R74_ORIGINAL_RUN_ACTOR/);
  }
  const diagnostic = inspectR74StatusCore(run, [status], binding);
  assert.equal(diagnostic.meaning, 'OWNER_ADMISSION_ONLY_NEVER_PRODUCT_OR_A5_ACCEPTANCE');
  assert.throws(() => assertOriginalProducerAdmission(diagnostic), /J1_UNBRANDED_OR_DIAGNOSTIC_ADMISSION/);
});
test('all independently reviewed fields are in the raw private index, not a mutable out-of-band review', () => {
  const f = fixture('acyclic-contract');
  const baseline = inspectProducerOriginalsCore(f.indexPath, f.reviewed, 'graph-source-review');
  assert.equal(baseline.production_admission, false);
  assert.equal(Object.hasOwn(f.index.contract, 'index_sha256'), false);
  for (const key of Object.keys(f.index.contract)) {
    const changed = structuredClone(f.reviewed); changed[key] = { unrelated_reviewed_field: key };
    assert.throws(() => inspectProducerOriginalsCore(f.indexPath, changed, 'graph-source-review'), /J1_REVIEWED_FIELDS_NOT_IN_INDEX/);
  }
  assert.throws(() => assertOriginalProducerAdmission(baseline), /J1_UNBRANDED_OR_DIAGNOSTIC_ADMISSION/);
});
test('source, retained original, fresh build, and complete runtime roles cannot be substituted', () => {
  for (const mutation of ['retained-source', 'cached-build', 'missing-role']) {
    const f = fixture(mutation);
    if (mutation === 'retained-source') {
      const receipt = f.load(f.index.source_original);
      const name = Object.keys(receipt.retained_files)[0];
      fs.appendFileSync(receipt.retained_files[name].path, 'changed');
    } else if (mutation === 'cached-build') {
      const process = f.index.builds.native_graph_session.process;
      const rows = fs.readFileSync(process.stdout.path, 'utf8').trim().split('\n').map(JSON.parse);
      rows[0].fresh = true; f.updateStream(process, rows.map(row => JSON.stringify(row) + '\n').join('')); f.seal();
    } else { delete f.index.binaries.semwright_sandbox; f.seal(); }
    assert.throws(() => inspectProducerOriginalsCore(f.indexPath, f.reviewed, 'graph-source-review'), /J1_|SCHEMA_/);
  }
});
test('complete synthetic originals and caller review flags never authenticate a production case', async () => {
  const f = fixture('no-fake-admission');
  await assert.rejects(admitOriginalProducers({ producerOriginalPath: f.indexPath, caseId: 'graph-source-review',
    privateRoot: path.join(f.root, 'cases', 'one'), deadlineUnixMs: Date.now() + 120000, reviewed: true, approved: true }),
    /J1_R74_ACTUAL_ACTIONS_CONTEXT/);
});
test('one unchanged SharedPool fits maximum authored case including closure; exhaustion precedes the next effect', () => {
  assert.equal(GRAPH_BUDGET.producer_bytes + 6 * GRAPH_BUDGET.case_bytes + GRAPH_BUDGET.shared_bytes, 2 * 1024 ** 3);
  assert.equal(GRAPH_BUDGET.producer_members + 6 * GRAPH_BUDGET.case_members + GRAPH_BUDGET.shared_members, 20000);
  const pool = new SharedPool(), MiB = 1024 ** 2;
  const reserve = (id, bytes, files) => pool.reserve(id, id, bytes, files);
  reserve('holdback', 252 * MiB, 1096); reserve('directories', 1, 256); reserve('store', 16 * MiB, 4);
  reserve('sdk', 60 * 256, 180); reserve('metadata', 24 * MiB, 320); reserve('owner', 48 * MiB, 768);
  reserve('consumption', 8192, 1); reserve('initializer-raw', 2 * MiB, 2); reserve('owner-raw', 2 * MiB, 2);
  for (let n = 0; n < 3; n++) { reserve('journal-' + n, 32 * MiB, 384); reserve('native-raw-' + n, 2 * MiB, 2); }
  reserve('copy', 16 * MiB, 4); reserve('reopen', 20 * MiB + 768 * 1024, 268);
  assert.ok(pool.reservedBytes <= 512 * MiB); assert.ok(pool.reservedFiles <= 4096);
  let effects = 0;
  assert.throws(() => { reserve('impossible', 512 * MiB - pool.reservedBytes + 1, 1); effects++; }, /POOL_EXHAUSTED/);
  assert.equal(effects, 0);
  assert.throws(() => reserve('impossible', 1, 1), /RESERVATION_REUSE/);
});

test('six current caller IDs route through the actual R94 factory and deny before an unapproved Service', async () => {
  const { createGraphServiceCase } = await import('../../tests/support/nativeGraphCase.ts');
  const caller = fs.readFileSync(new URL('../../tests/semantic-graph-service.spec.ts', import.meta.url), 'utf8');
  const ids = [...caller.matchAll(/createGraphServiceCase\(info, "(graph-[a-z-]+)",/g)].map(row => row[1]);
  assert.deepEqual(ids, ['graph-source-review', 'graph-flow', 'graph-lost-reply', 'graph-removal']);
  assert.ok(caller.includes('for (const kind of ["causal_diagram", "system_flow"] as const)'));
  assert.ok(caller.includes('"graph-label-" + kind.replaceAll("_", "-")'));
  ids.push('graph-label-causal-diagram', 'graph-label-system-flow');
  const selection = process.env.MOTIONWRIGHT_GRAPH_NATIVE_CASE;
  const producer = process.env.MOTIONWRIGHT_GRAPH_PRODUCER_ORIGINAL;
  const root = fs.mkdtempSync(path.join(tmpdir(), 'graph-r94-actual-routing-'));
  process.env.MOTIONWRIGHT_GRAPH_NATIVE_CASE = 'all'; delete process.env.MOTIONWRIGHT_GRAPH_PRODUCER_ORIGINAL;
  try {
    for (const id of ids) {
      const info = { timeout: 120000, outputDir: path.join(root, id), outputPath: (...pieces) => path.join(root, id, ...pieces) };
      await assert.rejects(createGraphServiceCase(info, id, { databasePath: path.join(root, id, 'ordinary.sqlite3'), desktopInvokeFixture: true }),
        /SOURCE_PRODUCER_ORIGINALS_AND_REVIEWED_CONTRACT_UNAVAILABLE/);
    }
    assert.deepEqual(fs.readdirSync(root), []);
  } finally {
    if (selection === undefined) delete process.env.MOTIONWRIGHT_GRAPH_NATIVE_CASE; else process.env.MOTIONWRIGHT_GRAPH_NATIVE_CASE = selection;
    if (producer === undefined) delete process.env.MOTIONWRIGHT_GRAPH_PRODUCER_ORIGINAL; else process.env.MOTIONWRIGHT_GRAPH_PRODUCER_ORIGINAL = producer;
  }
});

test('six source case layouts fit the actual Actions workspace socket path without another namespace', () => {
  const casesRoot = '/home/runner/work/motionwright/motionwright/v/e/graph-cases';
  const ids = ['graph-flow', 'graph-label-causal-diagram', 'graph-label-system-flow',
    'graph-lost-reply', 'graph-removal', 'graph-source-review'];
  const layouts = ids.map(id => graphCaseLayout(casesRoot, id));
  assert.equal(new Set(layouts.map(row => row.privateRoot)).size, 6);
  for (const [n, row] of layouts.entries()) {
    assert.equal(row.privateRoot, path.join(casesRoot, 'g' + (n + 1)));
    assert.equal(row.databasePath, path.join(row.privateRoot, 'store', 'motionwright.sqlite3'));
    assert.equal(row.ownerSocket, path.join(row.privateRoot, 'owner/runtime/broker.sock'));
    assert.equal(Buffer.byteLength(row.ownerSocket), 88);
    assert.equal(Buffer.byteLength(path.join(path.dirname(row.ownerSocket), 'bootstrap.sock')), 91);
  }
  assert.equal(Buffer.byteLength(path.join(casesRoot, '1/native-graph-custody/owner/runtime/broker.sock')), 108);
});

test('oversize or undeclared canonical layouts deny before any next effect, including UTF-8 expansion', () => {
  let effects = 0;
  for (const [root, id, code] of [
    ['/home/runner/work/motionwright/motionwright/v/e/graph-cases/' + 'x'.repeat(13), 'graph-flow', /CASE_OWNER_SOCKET_PATH_EXCEEDS_100_BYTES/],
    ['/home/runner/work/motionwright/motionwright/v/e/graph-cases/' + 'é'.repeat(7), 'graph-flow', /CASE_OWNER_SOCKET_PATH_EXCEEDS_100_BYTES/],
    ['/tmp/graph-cases', 'unreviewed-case', /CASE_LAYOUT_UNDECLARED_ID/],
    ['/tmp/graph-cases/../elsewhere', 'graph-flow', /PATH_SHAPE/],
  ]) {
    assert.throws(() => { graphCaseLayout(root, id); effects++; }, code);
  }
  assert.equal(effects, 0);
});

test('actual wrapper function routes Service and owner to factory paths using an explicitly inert interface fixture', async () => {
  // This executes the exact wrapper function after Node type stripping. The
  // stub cannot admit or launch anything; authentic production admission is
  // separately covered by the unchanged six negative routes above.
  const raw = fs.readFileSync(new URL('../../tests/support/nativeGraphCase.ts', import.meta.url), 'utf8');
  const start = raw.indexOf('export async function createGraphServiceCase(');
  const end = raw.indexOf('\nexport function graphOutputPath(', start);
  assert.ok(start >= 0 && end > start);
  const body = stripTypeScriptTypes(raw.slice(start, end), { mode: 'strip' }).replace(/^export /, '');
  const casesRoot = '/home/runner/work/motionwright/motionwright/v/e/graph-cases';
  const ids = ['graph-flow', 'graph-label-causal-diagram', 'graph-label-system-flow',
    'graph-lost-reply', 'graph-removal', 'graph-source-review'];
  for (const id of ids) {
    const layout = graphCaseLayout(casesRoot, id), service = {}, factories = new WeakMap();
    let requested, received, spec;
    const factory = { root: layout.privateRoot, options: layout, deadline: 12345,
      admission: { source: { inert_fixture: true }, binaries: {}, runtime: {} },
      async createNativeService(_Service, options, makeSpec) {
        received = options; spec = await makeSpec({ id: 'inert-project' }); return service;
      } };
    const run = new Function('createGraphCaseFactory', 'ScopedChangeService', 'selectedCases', 'factories', 'join', 'process',
      'return ' + body)(async options => { requested = options; return factory; },
        { create() { throw new Error('INERT_FIXTURE_MUST_NOT_LAUNCH_SERVICE'); } }, new Set(ids), factories, path.join,
        { env: { MOTIONWRIGHT_GRAPH_NATIVE_CASE: 'all', MOTIONWRIGHT_GRAPH_PRODUCER_ORIGINAL: '/inert/index' } });
    const requestedRoot = path.join(casesRoot, 'full-playwright-title-that-exceeds-owner-socket-limit', 'native-graph-custody');
    assert.equal(await run({ timeout: 120000, outputDir: path.dirname(requestedRoot),
      outputPath() { throw new Error('WRAPPER_MUST_NOT_CREATE_LONG_PLAYWRIGHT_DIRECTORY'); } }, id,
      { databasePath: '/inert/ordinary.sqlite3', desktopInvokeFixture: true }), service);
    assert.equal(requested.privateRoot, requestedRoot);
    assert.equal(received.databasePath, layout.databasePath);
    assert.equal(spec.store.database_path, layout.databasePath);
    assert.equal(spec.paths.private_root, path.join(layout.privateRoot, 'owner'));
    assert.equal(spec.paths.owner_socket, layout.ownerSocket);
    assert.equal(factories.get(service), factory);
  }
});
