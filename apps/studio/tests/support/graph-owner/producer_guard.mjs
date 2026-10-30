/**
 * Graph case pre-admission source verifier, deliberately read-only and DENY.
 * The independently reviewed A6 OriginalWriter/SharedPool must bind the real
 * authoring test, service build, Native build, actual Host, opened Store and
 * helper launch tuples before producing any MOTIONWRIGHT_GRAPH_CALLBACK_CAP.
 * Never use this parser or a caller string as that approval.
 */
import { createHash } from "node:crypto";

const OPERATIONS = new Set(["authoring_context", "upsert", "remove", "operation_recovery"]);
const EXPECTED_CASES = new Set([
  "graph-source-review", "graph-flow", "graph-lost-reply", "graph-removal",
  "graph-label-causal-diagram", "graph-label-system-flow",
]);
function diagnosticNeed(value, reason) { if (!value) throw new Error(reason); }
function diagnosticExact(value, names, reason) {
  diagnosticNeed(value && typeof value === "object" && !Array.isArray(value), reason);
  diagnosticNeed(Object.keys(value).sort().join("|") === names.slice().sort().join("|"), reason);
}
const hash = value => createHash("sha256").update(value).digest("hex");
// Three Git object IDs in this source are SHA-1 (40 hex); the test hash and
// candidate digest are SHA-256 (64 hex). Never widen either class.
const canonicalGitObject = raw => typeof raw === "string" && /^[0-9a-f]{40}$/.test(raw);
const canonicalDigest = raw => typeof raw === "string" && /^[0-9a-f]{64}$/.test(raw);
const safe = number => Number.isSafeInteger(number) && number >= 0;
const equality = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/**
 * This accepts source evidence for review only.  No branch of this module
 * grants native authority, even when an untrusted caller fabricates a success.
 */
export function inspectSourceBoundCandidate(candidate, externalOriginals = null) {
  const DENY = "DENY_INDEPENDENT_ORIGINAL_NATIVE_PRODUCER_AND_BUILD_REQUIRED";
  diagnosticExact(candidate, [
    "schema", "case_id", "test_title", "source", "launches", "prebackend_denials",
    "proposed_parent_budget", "digest_sha256", "admission",
    "native_authoring_approved", "automatic_retry",
  ], "GRAPH_CANDIDATE_SHAPE");
  diagnosticNeed(candidate.schema === "motionwright.native-graph-source-case/1", "GRAPH_CASE_SCHEMA");
  diagnosticNeed(EXPECTED_CASES.has(candidate.case_id), "GRAPH_CASE_ID");
  diagnosticNeed(typeof candidate.test_title === "string" && candidate.test_title.length > 40, "GRAPH_TEST_TITLE");
  diagnosticExact(candidate.source, ["head", "tree", "test_file_sha256", "sdk"], "GRAPH_CASE_SOURCE_SHAPE");
  diagnosticNeed(canonicalGitObject(candidate.source.head) &&
    canonicalGitObject(candidate.source.tree) &&
    canonicalGitObject(candidate.source.sdk) &&
    canonicalDigest(candidate.source.test_file_sha256),
    "GRAPH_CASE_SOURCE_IDENTITY");
  diagnosticNeed(Array.isArray(candidate.launches) && candidate.launches.length >= 2 && candidate.launches.length <= 8,
    "GRAPH_CASE_LAUNCH_COUNT");
  let callbackTotal = 0;
  for (const [index, launch] of candidate.launches.entries()) {
    diagnosticExact(launch, ["launch_index", "operations"], "GRAPH_LAUNCH_SHAPE");
    diagnosticNeed(launch.launch_index === index && Array.isArray(launch.operations)
      && launch.operations.length <= 64, "GRAPH_LAUNCH_ORDER");
    for (const operation of launch.operations) {
      diagnosticNeed(OPERATIONS.has(operation), "GRAPH_UNAUTHORED_CALLBACK");
      callbackTotal++;
    }
  }
  diagnosticNeed(candidate.launches.at(-1).operations.length === 0, "GRAPH_CLOSED_READONLY_LAUNCH");
  diagnosticNeed(Array.isArray(candidate.prebackend_denials)
    && candidate.prebackend_denials.every(value => typeof value === "string" && value.length > 1),
    "GRAPH_DENIAL_PLAN");
  const cli = 1 + candidate.launches.length + callbackTotal * 8;
  diagnosticExact(candidate.proposed_parent_budget, ["cli", "files", "bytes"], "GRAPH_PARENT_CANDIDATE_SHAPE");
  diagnosticNeed(safe(cli) && cli >= 2 && cli <= 65536
    && equality(candidate.proposed_parent_budget, { cli, files: cli * 3, bytes: cli * 256 }),
    "GRAPH_PARENT_CANDIDATE_MATH");
  diagnosticNeed(candidate.proposed_parent_budget.files <= 4096
    && candidate.proposed_parent_budget.bytes <= 512 * 1024 * 1024,
    "GRAPH_PARENT_CANDIDATE_SHARED_POOL");
  diagnosticNeed(candidate.admission === "DENY_INDEPENDENT_ORIGINAL_PRODUCER_MISSING"
    && candidate.native_authoring_approved === false && candidate.automatic_retry === false,
    "GRAPH_NATIVE_SELF_APPROVAL");
  const { digest_sha256, ...body } = candidate;
  diagnosticNeed(canonicalDigest(digest_sha256)
    && hash(JSON.stringify(body)) === digest_sha256, "GRAPH_CASE_DIGEST");
  // Even an apparently matching original source does not prove that an
  // independently approved Native backend reached the same owner and Store.
  if (externalOriginals !== null) {
    diagnosticNeed(typeof externalOriginals === "object" && !Array.isArray(externalOriginals), "GRAPH_EXTERNAL_ORIGINAL_SHAPE");
  }
  return Object.freeze({
    schema: "motionwright.native-graph-candidate-review/1",
    case_id: candidate.case_id,
    candidate_sha256: digest_sha256,
    authored_launch_count: candidate.launches.length,
    authored_callback_candidates: callbackTotal,
    candidate_cli_budget: cli,
    status: DENY,
    admitted_callback_cap: null,
    caller_granted_authority: false,
    side_effects: 0,
  });
}
export function requireNativeGraphParentAdmission(candidate, original) {
  inspectSourceBoundCandidate(candidate, original);
  throw new Error("GRAPH_NATIVE_PARENT_ORIGINALS_AND_INDEPENDENT_APPROVAL_MISSING");
}


/** Existing J1 original-reader joined to the R74 external status boundary. */
import path from 'node:path';
import https from 'node:https';
import { isDeepStrictEqual } from 'node:util';
import { stableRead, need, integer, inside, canonical, existingDirectory, GLOBAL_BYTES } from './custody.mjs';
import { strictJSON, exact, schema, SPEC_SCHEMA } from './contracts.mjs';

export const PRODUCER_INDEX_SCHEMA = 'motionwright.private.graph-producer-reference-index/0';
export const BINARY_ROLES = Object.freeze([
  'semwright', 'semwrightd', 'native_provider', 'shared_state_actor', 'manim_runner',
  'python3', 'ffmpeg', 'ordinary_service', 'native_graph_session', 'semwright_sandbox',
]);
const RUST_ROLES = BINARY_ROLES.filter(role => !['python3', 'ffmpeg'].includes(role));
const SDK_ROLES = new Set(['semwright', 'semwrightd', 'semwright_sandbox']);
const LIMIT = 1024 * 1024;
const DIGEST = /^[0-9a-f]{64}$/;
const SOURCE = /^[0-9a-f]{40}$/;
const UNAVAILABLE = 'SOURCE_PRODUCER_ORIGINALS_AND_REVIEWED_CONTRACT_UNAVAILABLE';
const admittedObjects = new WeakMap();
export const AUTHORITY_SOURCE_PATHS = Object.freeze([
  'tooling/native_graph_browser_owner.py', 'tooling/native_host_shared_state.py',
  'tooling/native_manim_graph_runtime_intake.py', 'tooling/ci_canvas_service.py',
  'tooling/ci_private_evidence.py', 'tooling/requirements/mlt-g2-custody.txt',
  '.github/workflows/studio-service-verification.yml',
  'apps/studio/tests/support/nativeGraphCase.ts',
  ...['producer_guard.mjs', 'factory.mjs', 'sdk_ticket_prewrite.mjs', 'custody.mjs',
    'contracts.mjs', 'schemas/spec.r2.json', 'schemas/final.r2.json', 'same_store_reopen.mjs',
    'opened_store_descriptor.mjs', 'opened_store_identity_join.mjs']
    .map(name => 'apps/studio/tests/support/graph-owner/' + name),
  ...['native_host_installed_shared_state', 'native_av_failure_guard', 'candidate_smoke',
    'linux_first_use_custody', 'linux_cold_start', 'linux_first_use_accessible',
    'source_checkout_observation', 'storage_schema_contract', 'linux_gui_probe',
    'native_provider_lifecycle', 'runtime_first_use_audit', 'manim_bundled_font_intake',
    'mlt_official_runtime_intake'].map(name => 'tooling/' + name + '.py'),
]);
const CALLGRAPH_PATHS = Object.freeze([
  'apps/studio/tests/semantic-graph-service.spec.ts', 'apps/studio/tests/support/scopedChangeService.ts',
  'apps/studio/src/SemanticGraphWorkbench.tsx', 'apps/studio/src/semanticGraphService.ts',
  'apps/studio/src/App.tsx', 'apps/studio/src/api.ts', 'apps/studio/src/projectMutation.ts',
  'apps/studio/src/semanticGraphDraft.ts', 'apps/studio/src/ProductionDesignWorkspace.tsx',
  'crates/service/examples/studio_review_session.rs', 'crates/native/examples/native-graph-review-session.rs',
  'crates/native/src/production/transport_custody.rs', 'crates/native/src/production/graph_authoring.rs',
  'crates/native/src/production.rs',
]);

function freeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}
function same(a, b, code) { need(isDeepStrictEqual(a, b), code); }
function ownMap(value, fields, code) { exact(value, fields, code); return value; }
function relative(name) {
  need(typeof name === 'string' && name.length > 0 && !name.includes('\\')
    && !name.includes('\0') && !path.posix.isAbsolute(name)
    && name.split('/').every(p => p && p !== '.' && p !== '..'), 'J1_RELATIVE_SOURCE_PATH');
  return name;
}
function oldDigest(ref) { return { bytes: ref.size_bytes, sha256: ref.sha256 }; }
function time(value) {
  need(typeof value === 'string' && /(?:Z|\+00:00)$/.test(value) && Number.isFinite(Date.parse(value)), 'J1_ORIGINAL_TIME');
  return Date.parse(value);
}
function reference(ref, maximum = GLOBAL_BYTES) {
  exact(ref, ['path', 'sha256', 'size_bytes'], 'J1_ORIGINAL_REFERENCE_FIELDS');
  need(DIGEST.test(ref.sha256) && integer(ref.size_bytes, 0, maximum), 'J1_ORIGINAL_SIZE');
  canonical(ref.path); return ref;
}

/** Reads retain references, not binary buffers or a second pool. stableRead
 * holds the actual FD, rejects links and checks identity while hashing. */
class Originals {
  refs = new Map();
  physical = new Map();
  read(ref, maximum = LIMIT) {
    reference(ref, maximum);
    need(this.refs.size < 512 || this.refs.has(ref.path), 'J1_ORIGINAL_COUNT');
    if (this.refs.has(ref.path)) same(this.refs.get(ref.path), ref, 'J1_PATH_RETARGETED');
    const observed = stableRead(ref.path, maximum);
    same(observed.ref, ref, 'J1_ORIGINAL_REFERENCE_MISMATCH');
    if (this.physical.has(ref.path)) same(this.physical.get(ref.path), observed.physical, 'J1_ORIGINAL_INODE_CHANGED');
    else this.physical.set(ref.path, freeze(structuredClone(observed.physical)));
    this.refs.set(ref.path, freeze(structuredClone(ref)));
    return observed.data;
  }
  json(ref, maximum = LIMIT) { return strictJSON(this.read(ref, maximum), maximum); }
  end() { for (const ref of this.refs.values()) { const current = stableRead(ref.path, Math.max(LIMIT, ref.size_bytes)); same(current.ref, ref, 'J1_ORIGINAL_CHANGED_DURING_JOIN'); same(current.physical, this.physical.get(ref.path), 'J1_ORIGINAL_INODE_CHANGED'); } }
}

function sourceObservation(value, expected, files, root, originals) {
  const explicitCheckout = value && Object.hasOwn(value, 'sha');
  need(value && (explicitCheckout ? value.sha : value.checkout) === expected.sha && value.tree === expected.tree, 'J1_SOURCE_SHA_TREE');
  if (explicitCheckout) exact(value, ['sha', 'tree', 'files'], 'J1_EXPLICIT_CHECKOUT_ORIGINAL');
  ownMap(value.files, Object.keys(files), 'J1_SOURCE_FILE_ROSTER');
  for (const [name, digest] of Object.entries(files)) {
    relative(name); need(DIGEST.test(digest.sha256) && integer(digest.bytes, 1, GLOBAL_BYTES), 'J1_SOURCE_FILE_PIN');
    if (explicitCheckout) {
      exact(value.files[name], ['git_bytes', 'git_sha256', 'checkout', 'checkout_encoding'], 'J1_EXPLICIT_SOURCE_FILE');
      same(value.files[name].checkout, digest, 'J1_SOURCE_FILE_DIGEST');
      need(value.files[name].git_bytes === digest.bytes && value.files[name].git_sha256 === digest.sha256
        && value.files[name].checkout_encoding === 'EXACT_GIT_BYTES', 'J1_LINUX_SOURCE_NOT_GIT_BYTES');
    } else same(value.files[name], digest, 'J1_SOURCE_FILE_DIGEST');
    originals.read({ path: inside(root, path.join(root, ...name.split('/'))),
      sha256: digest.sha256, size_bytes: digest.bytes }, GLOBAL_BYTES);
  }
}

function processOriginal(record, expected, originals) {
  ownMap(record, ['command', 'stdout', 'stderr'], 'J1_PROCESS_ORIGINAL_FIELDS');
  const command = originals.json(record.command);
  same(command.argv, expected.argv, 'J1_COMMAND_ARGV');
  need(command.cwd === expected.cwd, 'J1_COMMAND_CWD');
  same(command.selected_environment, expected.selected_environment, 'J1_COMMAND_ENVIRONMENT');
  need(command.process_observation_schema === 'motionwright.studio-command-process-observation/2'
    && command.process_boundary === 'OWNED_POSIX_SESSION_GROUP' && integer(command.pid, 1)
    && command.exit_code === 0 && command.owned_group_after_wait === 'ABSENT'
    && command.escaped_descendants === 'NOT_OBSERVED'
    && command.diagnostic_custody === 'DIRECT_PROCESS_EXIT_OBSERVED'
    && Array.isArray(command.cleanup_errors) && command.cleanup_errors.length === 0,
    'J1_BUILD_PROCESS_NOT_CLOSED');
  need(!Object.hasOwn(command, 'observation') && !Object.hasOwn(command, 'uncertainty_reason')
    && !Object.hasOwn(command, 'error') && !Object.hasOwn(command, 'secondary_observation_errors')
    && !Object.hasOwn(command, 'receipt_error'), 'J1_BUILD_PROCESS_UNKNOWN');
  need(time(command.finished_utc) >= time(command.started_utc), 'J1_PROCESS_TIME_ORDER');
  const stdout = originals.read(record.stdout, 64 * LIMIT);
  originals.read(record.stderr, 64 * LIMIT);
  same(command.stdout, oldDigest(record.stdout), 'J1_STDOUT_NOT_SAME_COMMAND');
  same(command.stderr, oldDigest(record.stderr), 'J1_STDERR_NOT_SAME_COMMAND');
  return { command, stdout };
}

function cargoBuild(role, record, expected, index, reviewed, originals) {
  ownMap(record, ['process', 'retained_binary'], 'J1_BUILD_FIELDS');
  const { command, stdout } = processOriginal(record.process, expected.command, originals);
  const root = SDK_ROLES.has(role) ? index.source.sdk_root : index.source.repository_root;
  need(command.cwd === root && command.argv.includes('build') && command.argv.includes('--locked')
    && command.argv.includes('--message-format=json'), 'J1_NOT_AN_ORIGINAL_CARGO_BUILD');
  const records = new TextDecoder('utf-8', { fatal: true }).decode(stdout).split(/\r?\n/).filter(Boolean).map(line => strictJSON(Buffer.from(line), 4 * LIMIT));
  const finished = records.filter(row => row.reason === 'build-finished');
  need(finished.length === 1 && finished[0].success === true && records.at(-1) === finished[0], 'J1_CARGO_TERMINAL');
  const artifacts = records.filter(row => row.reason === 'compiler-artifact' && row.executable === index.binaries[role].path);
  need(artifacts.length === 1, 'J1_CARGO_EXECUTABLE_BIJECTION');
  const artifact = artifacts[0];
  same({ package_id: artifact.package_id, name: artifact.target?.name, kind: artifact.target?.kind,
    src_path: artifact.target?.src_path }, expected.target, 'J1_CARGO_SOURCE_TARGET');
  inside(root, expected.target.src_path);
  const sourceFiles = SDK_ROLES.has(role) ? reviewed.sdk_files : reviewed.source_files;
  need(Object.hasOwn(sourceFiles, path.relative(root, expected.target.src_path).split(path.sep).join('/')),
    'J1_CARGO_TARGET_SOURCE_NOT_BOUND');
  need(artifact.fresh === false && artifact.profile?.test === false, 'J1_CARGO_CACHE_OR_TEST_HARNESS');
  originals.read(index.binaries[role], GLOBAL_BYTES);
  originals.read(record.retained_binary, GLOBAL_BYTES);
  same(oldDigest(record.retained_binary), oldDigest(index.binaries[role]), 'J1_RETAINED_BINARY_DIFFERS');
  need(record.retained_binary.path !== index.binaries[role].path, 'J1_RETAINED_BINARY_NOT_AN_ORIGINAL');
  return { role, command: record.process.command, cargo_stdout: record.process.stdout,
    retained_binary: record.retained_binary, executable: index.binaries[role] };
}

function ordinarySdkPreflight(value, expected, index, originals) {
  ownMap(value, ['process', 'stdin'], 'J1_PREFLIGHT_FIELDS');
  const { command, stdout } = processOriginal(value.process, expected, originals);
  need(command.argv[0] === index.binaries.ordinary_service.path, 'J1_PREFLIGHT_EXECUTABLE');
  const input = originals.read(value.stdin, 16384);
  same(strictJSON(input), { id: 'phase1-service-preflight', command: 'bootstrap', args: { request: { project_id: null } } }, 'J1_PREFLIGHT_REQUEST');
  same(command.stdin, { path: path.basename(value.stdin.path), ...oldDigest(value.stdin) }, 'J1_PREFLIGHT_STDIN_BINDING');
  const rows = new TextDecoder('utf-8', { fatal: true }).decode(stdout).split(/\r?\n/).filter(Boolean).map(line => strictJSON(Buffer.from(line)));
  need(rows.length === 1, 'J1_PREFLIGHT_REPLY_COUNT');
  exact(rows[0], ['id', 'ok', 'result'], 'J1_PREFLIGHT_REPLY_FIELDS');
  need(rows[0].id === 'phase1-service-preflight' && rows[0].ok === true
    && rows[0].result?.test_transport === 'service-test-jsonl'
    && rows[0].result?.native_sdk?.pinned_revision === index.source.sdk_sha, 'J1_COMPILED_SDK_BINDING');
}

function runtimeOriginals(index, reviewed, originals) {
  const runtime = index.runtime;
  existingDirectory(runtime.root); existingDirectory(runtime.fontconfig_root);
  ownMap(index.runtime_originals, ['producer', 'inventory', 'receipt', 'intake', 'profile_files'], 'J1_RUNTIME_ORIGINAL_FIELDS');
  const raw = index.runtime_originals;
  processOriginal(raw.producer, reviewed.runtime.command, originals);
  need(raw.inventory.path === runtime.manifest && raw.inventory.sha256 === runtime.manifest_sha256
    && raw.intake.path === runtime.intake_receipt && raw.intake.sha256 === runtime.intake_receipt_sha256,
    'J1_RUNTIME_REFERENCE_BINDING');
  const inventory = originals.json(raw.inventory, 2 * LIMIT), intake = originals.json(raw.intake, 2 * LIMIT);
  same(inventory, reviewed.runtime.inventory, 'J1_RUNTIME_PRODUCER_INVENTORY');
  same(intake, reviewed.runtime.intake, 'J1_RUNTIME_SOURCE_INTAKE');
  same(inventory.context, { source_sha: index.source.sha, semwright_sha: index.source.sdk_sha,
    run_id: index.source.run_id, run_attempt: String(index.source.run_attempt) }, 'J1_RUNTIME_CURRENT_CONTEXT');
  need(inventory.schema === 'motionwright.manim.runtime-inventory/2'
    && inventory.manim_version === '0.21.0' && inventory.pyav_version === '15.0.0'
    && inventory.native_render_performed === false && integer(inventory.files, 1)
    && DIGEST.test(inventory.inventory_sha256), 'J1_RUNTIME_INVENTORY_CONTRACT');
  originals.read(raw.receipt, 2 * LIMIT);
  need(raw.receipt.path === path.join(runtime.root, 'runtime.json')
    && inventory.runtime_receipt_sha256 === raw.receipt.sha256, 'J1_RUNTIME_RECEIPT');
  for (const [role, label] of [['python3', 'python'], ['ffmpeg', 'ffmpeg']]) {
    originals.read(index.binaries[role], GLOBAL_BYTES);
    same(inventory.binaries?.[label], { path: index.binaries[role].path, ...oldDigest(index.binaries[role]) }, 'J1_RUNTIME_TOOL');
  }
  need(intake.schema === 'motionwright.manim.graph-runtime-source-intake/1'
    && intake.status === 'SOURCE_INTAKE_ONLY' && intake.profile_id === 'mw-manim-semantic-graph-v1'
    && intake.source_root === index.source.repository_root && intake.runtime_root === runtime.root
    && intake.profile_admitted === false && intake.native_render_performed === false
    && intake.compiled_runner_pin_agreement_verified === false && intake.native_font_open_observed === false
    && intake.pango_layout_observed === false, 'J1_RUNTIME_INTAKE_NOT_AUTHORITY');
  same(raw.profile_files, reviewed.runtime.profile_files, 'J1_GRAPH_RUNTIME_FILE_ROSTER');
  need(Array.isArray(raw.profile_files) && raw.profile_files.length === 15, 'J1_GRAPH_RUNTIME_FIFTEEN_ORIGINALS');
  const names = new Set();
  for (const item of raw.profile_files) {
    exact(item, ['relative_path', 'ref'], 'J1_GRAPH_RUNTIME_FILE_FIELDS');
    relative(item.relative_path); need(!names.has(item.relative_path), 'J1_GRAPH_RUNTIME_DUPLICATE'); names.add(item.relative_path);
    need(item.ref.path === inside(runtime.root, path.join(runtime.root, ...item.relative_path.split('/'))), 'J1_GRAPH_RUNTIME_PATH');
    originals.read(item.ref, 8 * LIMIT);
  }
  need(Array.isArray(intake.files) && intake.files.length === 15 && intake.files_count === 15,
    'J1_RUNTIME_INTAKE_FILE_COUNT');
  const sourceNames = new Set(), runtimeNames = new Set();
  for (const row of intake.files) {
    exact(row, ['source_relative_path', 'runtime_relative_path', 'sha256', 'size_bytes', 'pin_origin'],
      'J1_RUNTIME_INTAKE_ROW');
    relative(row.source_relative_path); relative(row.runtime_relative_path);
    need(!sourceNames.has(row.source_relative_path) && !runtimeNames.has(row.runtime_relative_path), 'J1_RUNTIME_INTAKE_DUPLICATE');
    sourceNames.add(row.source_relative_path); runtimeNames.add(row.runtime_relative_path);
    const saved = raw.profile_files.find(item => item.relative_path === row.runtime_relative_path);
    need(saved && saved.ref.sha256 === row.sha256 && saved.ref.size_bytes === row.size_bytes,
      'J1_RUNTIME_INTAKE_OUTPUT_MISMATCH');
    same(reviewed.source_files[row.source_relative_path], { bytes: row.size_bytes, sha256: row.sha256 },
      'J1_RUNTIME_INTAKE_SOURCE_NOT_BOUND');
    need(row.runtime_relative_path.startsWith('graph-profile-v1/')
      && ['crates/manim-profile/src/semantic_graph_source.rs::BUILTIN_ASSETS',
        'crates/manim-profile/src/semantic_graph_source.rs::helper_sources/include_bytes!'].includes(row.pin_origin),
      'J1_RUNTIME_PIN_ORIGIN');
  }
  for (const field of ['compiler_source', 'source_lock']) {
    const member = intake[field];
    exact(member, ['relative_path', 'sha256', 'size_bytes'], 'J1_RUNTIME_SOURCE_MEMBER');
    same(reviewed.source_files[relative(member.relative_path)], { bytes: member.size_bytes, sha256: member.sha256 },
      'J1_RUNTIME_COMPILER_OR_LOCK');
  }
  const closure = { path: path.join(runtime.root, 'graph-profile-v1', 'source-closure.json'),
    sha256: raw.intake.sha256, size_bytes: raw.intake.size_bytes };
  originals.read(closure, 2 * LIMIT);
}

function callbackPlan(index, reviewed, caseId, originals) {
  need(typeof caseId === 'string' && Object.hasOwn(reviewed.cases, caseId), 'J1_CASE_NOT_REVIEWED');
  const roster = originals.json(index.callback_roster), enforcement = originals.json(index.native_enforcement);
  same(roster, reviewed.callback_roster, 'J1_CALLBACK_ROSTER_NOT_REVIEWED');
  same(enforcement, reviewed.native_enforcement, 'J1_ENFORCEMENT_NOT_REVIEWED');
  // Semantic approval comes from review of exact code, never a receipt boolean.
  need(reviewed.native_enforcing_source && DIGEST.test(reviewed.native_enforcing_source.sha256), 'J1_NATIVE_ENFORCING_SOURCE_UNAVAILABLE');
  same(reviewed.source_files['crates/native/examples/native-graph-review-session.rs'], reviewed.native_enforcing_source,
    'J1_ENFORCEMENT_NOT_THE_BUILT_SOURCE');
  need(reviewed.native_enforcing_source.sha256 !== 'f0cc2efc496f1ab13458395c10b09f502a5cbdc12f906827378c5e40b1716dd9',
    'J1_SOURCE96_HAS_NO_CUMULATIVE_ENFORCEMENT');
  need(Object.hasOwn(reviewed.source_files, 'apps/studio/tests/support/nativeGraphCase.ts')
    && Object.hasOwn(reviewed.source_files, 'apps/studio/tests/support/scopedChangeService.ts')
    && Object.hasOwn(reviewed.source_files, 'apps/studio/tests/semantic-graph-service.spec.ts'), 'J1_ACTUAL_WRAPPER_SOURCE_MISSING');
  const selection = reviewed.cases[caseId];
  exact(selection, ['title', 'callback_caps', 'roster_source_files'], 'J1_REVIEWED_CASE_FIELDS');
  need(typeof selection.title === 'string' && selection.title.length > 0
    && Array.isArray(selection.callback_caps) && selection.callback_caps.length > 0 && selection.callback_caps.length <= 64,
    'J1_CASE_ROSTER_SHAPE');
  for (const name of selection.roster_source_files) need(Object.hasOwn(reviewed.source_files, name), 'J1_CASE_SOURCE_NOT_BOUND');
  need(selection.roster_source_files.length >= 14 && new Set(selection.roster_source_files).size === selection.roster_source_files.length,
    'J1_CASE_CALLGRAPH_INCOMPLETE');
  for (const name of CALLGRAPH_PATHS) need(selection.roster_source_files.includes(name), 'J1_CASE_REQUIRED_CALLGRAPH_SOURCE');
  const cases = roster.cases;
  need(Array.isArray(cases) && cases.length === Object.keys(reviewed.cases).length
    && new Set(cases.map(row => row.case_id)).size === cases.length, 'J1_CALLBACK_CASE_BIJECTION');
  for (const row of cases) {
    need(Object.hasOwn(reviewed.cases, row.case_id), 'J1_FOREIGN_CALLBACK_CASE');
    same(row.callback_caps, reviewed.cases[row.case_id].callback_caps, 'J1_CALLBACK_CAPS_CHANGED');
    need(row.title === reviewed.cases[row.case_id].title, 'J1_CALLBACK_TITLE_CHANGED');
  }
  const clients = selection.callback_caps.map((cap, i) => { need(integer(cap), 'J1_CALLBACK_CAP'); return { ordinal: i + 1, callback_cap: cap }; });
  const calls = 1n + clients.reduce((sum, item) => sum + 1n + 8n * BigInt(item.callback_cap), 0n);
  need(calls * 3n <= 4096n && calls * 256n <= BigInt(GLOBAL_BYTES), 'J1_CALLBACK_POOL_BOUND');
  return { schema: 'motionwright.graph-sdk-ticket-prewrite-plan/0', source: index.source,
    native_service: index.binaries.native_graph_session,
    originals: { callback_roster: index.callback_roster, native_enforcement: index.native_enforcement }, clients };
}

function verifyIndex(indexPath, reviewed, caseId) {
  need(reviewed && DIGEST.test(reviewed.index_sha256), 'J1_REVIEWED_INDEX_PIN_REQUIRED');
  const initial = stableRead(indexPath, LIMIT);
  need(initial.ref.sha256 === reviewed.index_sha256, 'J1_INDEX_NOT_INDEPENDENTLY_PINNED');
  const originals = new Originals();
  const index = originals.json(initial.ref);
  exact(index, ['schema', 'source', 'binaries', 'runtime', 'source_original', 'sdk_original',
    'builds', 'ordinary_preflight', 'runtime_originals', 'callback_roster', 'native_enforcement', 'contract'], 'J1_INDEX_FIELDS');
  same(index.contract, reviewedContract(reviewed), 'J1_REVIEWED_FIELDS_NOT_IN_INDEX');
  need(index.schema === PRODUCER_INDEX_SCHEMA, 'J1_INDEX_SCHEMA');
  schema(index.source, SPEC_SCHEMA.properties.source); schema(index.binaries, SPEC_SCHEMA.properties.binaries);
  ownMap(index.binaries, BINARY_ROLES, 'J1_TEN_CLOSED_BINARY_ROLES'); schema(index.runtime, SPEC_SCHEMA.properties.runtime);
  same(index.source, reviewed.source, 'J1_REVIEWED_SOURCE_CONTEXT');
  existingDirectory(index.source.repository_root); existingDirectory(index.source.sdk_root);
  need(new Set(Object.values(index.binaries).map(ref => ref.path)).size === 10, 'J1_BINARY_ROLE_PATH_ALIAS');
  need(SOURCE.test(index.source.sdk_sha), 'J1_SDK_PIN_SHAPE');
  const source = originals.json(index.source_original, 8 * LIMIT), sdk = originals.json(index.sdk_original, 8 * LIMIT);
  const currentRun = value => value.run_id === index.source.run_id
    && (value.run_attempt === index.source.run_attempt || value.run_attempt === String(index.source.run_attempt));
  need(currentRun(source), 'J1_SOURCE_RUN_ATTEMPT');
  // No cross-run SDK provenance reuse is admitted before the independent
  // approval/transport contract defines and authenticates that exact reuse.
  need(currentRun(sdk), 'J1_SDK_RUN_ATTEMPT');
  for (const name of AUTHORITY_SOURCE_PATHS) need(Object.hasOwn(reviewed.source_files, name), 'J1_AUTHORITY_SOURCE_MISSING:' + name);
  const writerRoles = [...RUST_ROLES, 'source_observation', 'sdk_observation', 'ordinary_preflight', 'runtime'];
  ownMap(reviewed.writer_sources, writerRoles, 'J1_REVIEWED_WRITER_ROSTER');
  for (const [role, names] of Object.entries(reviewed.writer_sources)) {
    need(Array.isArray(names) && names.length >= 1 && names.length <= 16
      && new Set(names).size === names.length, 'J1_WRITER_SOURCE_LIST:' + role);
    for (const name of names) {
      relative(name); need(Object.hasOwn(reviewed.source_files, name), 'J1_WRITER_SOURCE_NOT_BOUND:' + role);
    }
  }
  same(source.source_before, source.source_after, 'J1_SOURCE_CHANGED_DURING_PRODUCER');
  sourceObservation(source.source_before, index.source, reviewed.source_files, index.source.repository_root, originals);
  for (const [name, digest] of Object.entries(index.source.locks)) need(reviewed.source_files[name]?.sha256 === digest, 'J1_EXACT_LOCKS');
  const sourceLock = originals.json({ path: path.join(index.source.repository_root, 'SOURCE_LOCK.json'),
    sha256: reviewed.source_files['SOURCE_LOCK.json'].sha256, size_bytes: reviewed.source_files['SOURCE_LOCK.json'].bytes });
  need(sourceLock.dependencies?.semwright?.revision === index.source.sdk_sha, 'J1_SOURCE_LOCK_SDK');
  need(SOURCE.test(reviewed.sdk_tree), 'J1_SDK_TREE_NOT_REVIEWED');
  same(sdk.source_before, sdk.source_after, 'J1_SDK_SOURCE_CHANGED');
  sourceObservation(sdk.source_before, { sha: index.source.sdk_sha, tree: reviewed.sdk_tree }, reviewed.sdk_files, index.source.sdk_root, originals);
  for (const [record, files, root] of [[source, reviewed.source_files, index.source.repository_root], [sdk, reviewed.sdk_files, index.source.sdk_root]]) {
    ownMap(record.retained_files, Object.keys(files), 'J1_RETAINED_SOURCE_ROSTER');
    for (const [name, digest] of Object.entries(files)) {
      const ref = record.retained_files[name];
      inside(reviewed.execution.producer_root, ref.path);
      need(ref.path !== path.join(root, ...name.split('/')), 'J1_RETAINED_SOURCE_IS_LIVE_PATH');
      same(oldDigest(ref), digest, 'J1_RETAINED_SOURCE_DIGEST'); originals.read(ref, GLOBAL_BYTES);
    }
  }
  ownMap(index.builds, RUST_ROLES, 'J1_EIGHT_RUST_PRODUCERS_REQUIRED');
  ownMap(reviewed.builds, RUST_ROLES, 'J1_REVIEWED_BUILD_ROSTER');
  const builds = RUST_ROLES.map(role => cargoBuild(role, index.builds[role], reviewed.builds[role], index, reviewed, originals));
  ordinarySdkPreflight(index.ordinary_preflight, reviewed.ordinary_preflight, index, originals);
  runtimeOriginals(index, reviewed, originals);
  const plan = callbackPlan(index, reviewed, caseId, originals);
  originals.end();
  return { admission: { source: index.source, binaries: index.binaries, runtime: index.runtime,
    ordinaryService: index.binaries.ordinary_service, nativeService: index.binaries.native_graph_session,
    execution: reviewed.execution,
    sdk_ticket_prewrite: plan, producer_originals: { index: initial.ref, builds } }, originals };
}

/** Fixtures may supply a review object to exercise semantic checks. This export
 * NEVER returns a branded Admission and production factory cannot consume it. */
export function inspectProducerOriginalsCore(indexPath, reviewed, caseId) {
  const result = verifyIndex(indexPath, reviewed, caseId);
  return freeze({ schema: 'motionwright.private.graph-producer-diagnostic/0',
    production_admission: false, synthetic_adapter_only: true,
    verified: structuredClone(result.admission) });
}

/** Independent reopen remains closed until its real producer and owner preseal
 * witness are adopted. This is not another owner, runner or global pool. */
export async function runIndependentSameStoreReopen() {
  throw new Error('INDEPENDENT_SAME_STORE_REOPEN_PRODUCER_AND_REVIEWED_CONTRACT_UNAVAILABLE');
}

// R74 approves these existing review fields as part of the exact index bytes.
// No output hash is embedded in its own source or in this contract.
const CONTRACT_FIELDS = Object.freeze(['source_files', 'sdk_tree', 'sdk_files', 'builds',
  'ordinary_preflight', 'runtime', 'callback_roster', 'native_enforcement',
  'native_enforcing_source', 'cases', 'writer_sources', 'execution']);
export const GRAPH_SCOPE = 'phase1-native-graph';
export const GRAPH_BUDGET = Object.freeze({
  producer_bytes: 384 * 1024 * 1024, producer_members: 768,
  case_bytes: 260 * 1024 * 1024, case_members: 3000,
  shared_bytes: 104 * 1024 * 1024, shared_members: 1232,
  owner_bytes: 48 * 1024 * 1024, owner_files: 768,
  journal_bytes: 32 * 1024 * 1024, journal_files: 384,
  store_file_bytes: 4 * 1024 * 1024, store_files: 4,
});
const GITHUB_REPOSITORY = 'seradotcom/motionwright';
const WORKFLOW_PATH = '.github/workflows/studio-service-verification.yml';
const API_BYTES = 256 * 1024, API_PAGES = 4, API_MS = 12000;
const usedCases = new Set();
let readOnlyMetadataToken;

function reviewedContract(reviewed) {
  return Object.fromEntries(CONTRACT_FIELDS.map(name => [name, reviewed[name]]));
}
function contractReview(index, indexSha) {
  exact(index.contract, CONTRACT_FIELDS, 'J1_ACYCLIC_CONTRACT_FIELDS');
  need(!Object.hasOwn(index.contract, 'index_sha256') && !Object.hasOwn(index.contract, 'source'),
    'J1_CONTRACT_SELF_REFERENCE');
  const execution = index.contract.execution;
  exact(execution, ['scope', 'job', 'job_id', 'deadline_original', 'deadline_unix_ms',
    'producer_root', 'cases_root', 'budget'], 'J1_EXECUTION_FIELDS');
  need(execution.scope === GRAPH_SCOPE && execution.job === 'real-service'
    && integer(execution.job_id, 1) && integer(execution.deadline_unix_ms, 1), 'J1_EXECUTION_CONTEXT');
  reference(execution.deadline_original, 8 * LIMIT + 65536);
  canonical(execution.producer_root); canonical(execution.cases_root);
  same(execution.budget, GRAPH_BUDGET, 'J1_AGGREGATE_ALLOCATION');
  exact(index.contract.cases, [...EXPECTED_CASES], 'J1_SIX_CASE_CONTRACT');
  return { index_sha256: indexSha, source: index.source, ...index.contract };
}

function r74Context(runId, caseId) {
  need(EXPECTED_CASES.has(caseId), 'J1_R74_CLOSED_CASE');
  return 'mw-owner/' + GRAPH_SCOPE + '/' + runId + '/1/' + caseId;
}

/** Pure negative-control surface. It returns no admission brand and accepts no
 * caller identity override. Production calls it only with actual HTTPS bytes. */
export function inspectR74StatusCore(run, rows, binding) {
  exact(binding, ['source_sha', 'run_id', 'case_id', 'index_sha256'], 'J1_R74_BINDING_FIELDS');
  need(SOURCE.test(binding.source_sha) && /^[1-9][0-9]*$/.test(binding.run_id)
    && DIGEST.test(binding.index_sha256), 'J1_R74_BINDING');
  need(run && integer(run.id, 1) && String(run.id) === binding.run_id && run.run_attempt === 1
    && run.head_sha === binding.source_sha && run.repository?.full_name === GITHUB_REPOSITORY
    && run.head_repository?.full_name === GITHUB_REPOSITORY
    && run.event === 'workflow_dispatch' && run.status === 'in_progress'
    && run.path?.split('@', 1)[0] === WORKFLOW_PATH && integer(run.workflow_id, 1)
    && run.actor?.type === 'User' && integer(run.actor.id, 1), 'J1_R74_ORIGINAL_RUN_ACTOR');
  const context = r74Context(binding.run_id, binding.case_id);
  need(Array.isArray(rows) && rows.length <= API_PAGES * 100, 'J1_R74_STATUS_BOUND');
  // The endpoint returns reverse chronological order. Never filter creator or
  // success first: the first matching context, including a rejection, wins.
  let previous = Number.MAX_SAFE_INTEGER;
  for (const row of rows) {
    need(row && integer(row.id, 1) && row.id < previous, 'J1_R74_STATUS_ORDER');
    previous = row.id;
  }
  const selected = rows.find(row => typeof row.context === 'string'
    && row.context.toLowerCase() === context.toLowerCase());
  need(selected, 'J1_R74_STATUS_NOT_PRESENT');
  need(selected.state === 'success' && selected.creator?.type === 'User'
    && selected.creator.id === run.actor.id
    && selected.target_url === 'https://github.com/' + GITHUB_REPOSITORY + '/actions/runs/' + binding.run_id
    && selected.description === 'admission-sha256:' + binding.index_sha256,
    'J1_R74_NEWEST_STATUS_NOT_AUTHORIZED');
  return freeze({ context, status_id: selected.id, actor_id: run.actor.id,
    source_sha: binding.source_sha, run_id: binding.run_id, run_attempt: 1,
    index_sha256: binding.index_sha256,
    meaning: 'OWNER_ADMISSION_ONLY_NEVER_PRODUCT_OR_A5_ACCEPTANCE' });
}

async function actualGitHubGet(url, token, deadline, observations) {
  need(url.startsWith('https://api.github.com/repos/' + GITHUB_REPOSITORY + '/'), 'J1_R74_FIXED_ORIGIN');
  need(Date.now() < deadline, 'J1_R74_READ_DEADLINE');
  const started = new Date().toISOString();
  return new Promise((resolve, reject) => {
    const request = https.get(url, { agent: false, timeout: Math.max(1, deadline - Date.now()),
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'motionwright-graph-r74', 'Cache-Control': 'no-cache' } }, response => {
      const chunks = []; let bytes = 0;
      response.on('data', chunk => {
        bytes += chunk.length;
        if (bytes > API_BYTES) { response.destroy(new Error('J1_R74_RESPONSE_BOUND')); return; }
        chunks.push(chunk);
      });
      response.once('error', reject);
      response.once('end', () => {
        try {
          const raw = Buffer.concat(chunks);
          const original = { url, status: response.statusCode, headers: {
            date: response.headers.date ?? null, age: response.headers.age ?? '0',
            link: response.headers.link ?? null, request_id: response.headers['x-github-request-id'] ?? null },
            started_utc: started, finished_utc: new Date().toISOString(),
            body_base64: raw.toString('base64'), body_sha256: hash(raw), body_bytes: raw.length };
          observations.push(original);
          need(response.statusCode === 200 && response.headers.age === undefined
            || response.statusCode === 200 && response.headers.age === '0', 'J1_R74_HTTP_STATUS_OR_CACHE');
          need(typeof response.headers.date === 'string' && Number.isFinite(Date.parse(response.headers.date))
            && raw.length > 0 && Date.now() <= deadline, 'J1_R74_HTTP_DATE_OR_DEADLINE');
          resolve({ value: strictJSON(raw, API_BYTES), link: response.headers.link ?? '' });
        } catch (error) { reject(error); }
      });
    });
    request.once('error', reject);
    request.once('timeout', () => request.destroy(new Error('J1_R74_READ_DEADLINE')));
    const timer = setTimeout(() => request.destroy(new Error('J1_R74_READ_DEADLINE')), Math.max(1, deadline - Date.now()));
    request.once('close', () => clearTimeout(timer));
  });
}

async function authenticateR74(index, indexRef, caseId, requestedDeadline) {
  need(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_API_URL === 'https://api.github.com'
    && process.env.GITHUB_REPOSITORY === GITHUB_REPOSITORY
    && process.env.GITHUB_EVENT_NAME === 'workflow_dispatch'
    && process.env.GITHUB_SHA === index.source.sha && process.env.GITHUB_WORKFLOW_SHA === index.source.sha
    && process.env.GITHUB_RUN_ID === index.source.run_id && process.env.GITHUB_RUN_ATTEMPT === '1'
    && index.source.run_attempt === 1 && process.env.GITHUB_JOB === 'real-service', 'J1_R74_ACTUAL_ACTIONS_CONTEXT');
  need(process.env.GITHUB_WORKFLOW_REF?.startsWith(GITHUB_REPOSITORY + '/' + WORKFLOW_PATH + '@'), 'J1_R74_WORKFLOW_REF');
  readOnlyMetadataToken ??= process.env.MOTIONWRIGHT_GRAPH_METADATA_TOKEN;
  delete process.env.MOTIONWRIGHT_GRAPH_METADATA_TOKEN;
  const token = readOnlyMetadataToken;
  need(typeof token === 'string' && token.length > 0, 'J1_R74_READ_ONLY_TOKEN_REQUIRED');
  const deadline = Math.min(requestedDeadline, index.contract.execution.deadline_unix_ms, Date.now() + API_MS);
  const observations = [], prefix = 'https://api.github.com/repos/' + GITHUB_REPOSITORY;
  const run = (await actualGitHubGet(prefix + '/actions/runs/' + index.source.run_id, token, deadline, observations)).value;
  const rows = [], context = r74Context(index.source.run_id, caseId).toLowerCase();
  for (let page = 1; page <= API_PAGES; page++) {
    const response = await actualGitHubGet(prefix + '/commits/' + index.source.sha + '/statuses?per_page=100&page=' + page,
      token, deadline, observations);
    need(Array.isArray(response.value) && response.value.length <= 100, 'J1_R74_STATUS_PAGE');
    rows.push(...response.value);
    if (rows.some(row => typeof row.context === 'string' && row.context.toLowerCase() === context)
      || response.value.length < 100 && !response.link.includes('rel="next"')) break;
    need(page < API_PAGES, 'J1_R74_STATUS_PAGINATION_BOUND');
  }
  const binding = { source_sha: index.source.sha, run_id: index.source.run_id, case_id: caseId, index_sha256: indexRef.sha256 };
  return { ...inspectR74StatusCore(run, rows, binding), index: indexRef, observations };
}

export async function admitOriginalProducers(options) {
  need(options && typeof options.producerOriginalPath === 'string', UNAVAILABLE);
  const observed = stableRead(options.producerOriginalPath, LIMIT), index = strictJSON(observed.data, LIMIT);
  const reviewed = contractReview(index, observed.ref.sha256);
  need(integer(options.deadlineUnixMs, Date.now() + 1) && typeof options.privateRoot === 'string', 'J1_CASE_ORIGINAL_DEADLINE');
  inside(reviewed.execution.cases_root, options.privateRoot);
  const once = index.source.run_id + '/1/' + options.caseId;
  need(!usedCases.has(once), 'J1_CASE_ADMISSION_ALREADY_CONSUMED'); usedCases.add(once);
  const approval = await authenticateR74(index, observed.ref, options.caseId, options.deadlineUnixMs);
  const result = verifyIndex(options.producerOriginalPath, reviewed, options.caseId);
  const deadline = result.originals.json(reviewed.execution.deadline_original, 8 * LIMIT + 65536);
  need(deadline.schema === 'motionwright.graph-job-deadline/1' && deadline.job_seconds === 10800
    && deadline.deadline_unix_ms === reviewed.execution.deadline_unix_ms
    && deadline.binding?.GITHUB_SHA === index.source.sha && deadline.binding?.GITHUB_RUN_ID === index.source.run_id
    && deadline.binding?.GITHUB_RUN_ATTEMPT === '1'
    && deadline.jobs?.jobs?.some(row => row.id === reviewed.execution.job_id
      && row.name === 'Real SQLite and Studio / ' + GRAPH_SCOPE), 'J1_ORIGINAL_GRAPH_JOB_DEADLINE');
  const job = deadline.jobs.jobs.find(row => row.id === reviewed.execution.job_id);
  const began = Date.parse(job.started_at);
  need(Number.isFinite(began), 'J1_ORIGINAL_JOB_START');
  for (const process of [...Object.values(index.builds).map(row => row.process),
    index.ordinary_preflight.process, index.runtime_originals.producer]) {
    const command = result.originals.json(process.command);
    same(command.graph_job, { source_sha: index.source.sha, run_id: index.source.run_id,
      run_attempt: 1, job_id: reviewed.execution.job_id, deadline_original: reviewed.execution.deadline_original },
      'J1_PRODUCER_DIFFERENT_ORIGINAL_JOB');
    need(command.deadline_admission?.deadline_mono_ns === deadline.deadline_mono_ns
      && Date.parse(command.started_utc) >= began && Date.parse(command.finished_utc) >= Date.parse(command.started_utc)
      && Date.parse(command.finished_utc) < began + 10800000, 'J1_PRODUCER_OUTSIDE_ORIGINAL_JOB');
  }
  need(Date.now() + 120000 + 1200000 < reviewed.execution.deadline_unix_ms, 'J1_CASE_AND_FINAL_CUSTODY_BUDGET');
  result.originals.end();
  const admission = freeze({ ...result.admission, approval });
  admittedObjects.set(admission, result.originals);
  return admission;
}

export function assertOriginalProducerAdmission(admission) {
  const originals = admittedObjects.get(admission);
  need(originals && Object.isFrozen(admission), 'J1_UNBRANDED_OR_DIAGNOSTIC_ADMISSION');
  originals.end(); return admission;
}

export function ownerReadOnlyMetadataCredential(admission) {
  assertOriginalProducerAdmission(admission);
  need(typeof readOnlyMetadataToken === 'string' && readOnlyMetadataToken.length > 0, 'J1_R74_READ_ONLY_TOKEN_REQUIRED');
  return readOnlyMetadataToken;
}
