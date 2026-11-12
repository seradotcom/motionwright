import * as fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { isDeepStrictEqual } from 'node:util';
import { SharedPool, OriginalWriter, bindRawPair, inventoryTree, copyTreeExclusive,
  stableRead, freshDirectory, existingDirectory, pinDirectory, safeParents, canonical, inside, need, integer, GLOBAL_BYTES, GLOBAL_FILES } from './custody.mjs';
import { strictJSON, schema, SPEC_SCHEMA, FINAL_SCHEMA, exact, tuple, validateServiceClosure,
  ERROR_CODES, GRAPH_RUNTIME_STAGES, same, BoundedJsonLines, hasNoSemanticGraphs,
  developmentGraphSourceSelection } from './contracts.mjs';
import { admitOriginalProducers, admitOriginalDevelopmentProducers, assertOriginalProducerAdmission,
  assertGraphProducerAdmission, ownerReadOnlyMetadataCredential,
  originalGraphCaseLayout, GRAPH_BUDGET } from './producer_guard.mjs';
import { snapshotSdkTicketPlan, SdkTicketPrewrite } from './sdk_ticket_prewrite.mjs';
import { reserveOriginalDevelopmentReader, openOriginalDevelopmentReadOnly,
  assertOriginalDevelopmentReaderClosed } from './dev_live_reader.mjs';

const MiB = 1024 * 1024;

/** Validate controller limits from already authenticated staging inputs. */
export function verifyGraphControllerFileLimit(binaries, kernelLimits) {
  const roles = ['native_provider', 'manim_runner', 'python3', 'ffmpeg', 'semwright_sandbox'];
  const sizes = roles.map(role => {
    const ref = binaries?.[role];
    need(ref && typeof ref.path === 'string' && path.isAbsolute(ref.path)
      && typeof ref.sha256 === 'string' && /^[0-9a-f]{64}$/.test(ref.sha256)
      && integer(ref.size_bytes, 1, 256 * MiB), 'CASE_CONTROLLER_STAGING_EXECUTABLE');
    return ref.size_bytes;
  });
  const expected = Math.max(GRAPH_BUDGET.store_file_bytes, ...sizes);
  need(typeof kernelLimits === 'string', 'CASE_CONTROLLER_KERNEL_FILE_BOUND');
  const rows = kernelLimits.split('\n').filter(row => row.startsWith('Max file size'));
  const row = rows.length === 1
    ? /^Max file size[ \t]+([0-9]+|unlimited)[ \t]+([0-9]+|unlimited)[ \t]+bytes[ \t]*$/.exec(rows[0]) : null;
  need(row && row[1] === String(expected) && row[2] === String(expected),
    'CASE_CONTROLLER_KERNEL_FILE_BOUND');
  return expected;
}

// A real Graph owner must never forward the CI process environment wholesale
// to native children. Keep trusted source/run identities and approved client
// capabilities, but not the runner's credentials, loader hooks, or aliases.
const GRAPH_EXEC_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin';
const GRAPH_BASE_CHILD_ENV = Object.freeze({
  PATH: GRAPH_EXEC_PATH, LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8',
  TZ: 'UTC', PYTHONNOUSERSITE: '1', RUST_BACKTRACE: '1',
});
const GRAPH_CLIENT_FIELDS = Object.freeze([
  'MOTIONWRIGHT_GRAPH_CONNECTION', 'MOTIONWRIGHT_GRAPH_CALLBACK_CAP',
  'MOTIONWRIGHT_GRAPH_PRIVATE_MAX_BYTES', 'MOTIONWRIGHT_GRAPH_PRIVATE_MAX_FILES',
]);

/** Exact source-bound client capability and deterministic private profile. */
export function graphOwnedClientEnvironment(capability, paths) {
  need(capability && Object.getPrototypeOf(capability) === Object.prototype,
    'GRAPH_CLIENT_ENV_BODY');
  need(Object.keys(capability).sort().join('|') === [...GRAPH_CLIENT_FIELDS].sort().join('|'),
    'GRAPH_CLIENT_ENV_FIELDS');
  need(paths && typeof paths.private_root === 'string' && path.isAbsolute(paths.private_root)
    && paths.owner_home === path.join(paths.private_root, 'home')
    && paths.owner_runtime === path.join(paths.private_root, 'runtime')
    && paths.owner_state === path.join(paths.private_root, 'state'),
    'GRAPH_CLIENT_PRIVATE_PATHS');
  // The Python owner creates its clients directly under the sealed
  // <case>/owner/clients directory. A second /owner component was a
  // synthetic-fixture assumption that denied every genuine connection.
  const connection = capability.MOTIONWRIGHT_GRAPH_CONNECTION;
  need(typeof connection === 'string' && path.isAbsolute(connection)
    && path.dirname(connection) === path.join(paths.private_root, 'clients')
    && /^[^\\/]+\.connection\.json$/.test(path.basename(connection)),
    'GRAPH_CLIENT_CONNECTION_UNBOUND');
  for (const name of GRAPH_CLIENT_FIELDS.slice(1)) {
    const value = capability[name];
    need(typeof value === 'string' && /^(0|[1-9][0-9]{0,17})$/.test(value),
      'GRAPH_CLIENT_QUOTA_UNBOUNDED');
  }
  const ownerConfig = path.join(paths.private_root, 'config');
  const ownerWork = path.join(paths.private_root, 'work');
  return Object.freeze({ ...GRAPH_BASE_CHILD_ENV,
    HOME: paths.owner_home, XDG_RUNTIME_DIR: paths.owner_runtime,
    XDG_STATE_HOME: paths.owner_state, XDG_CONFIG_HOME: ownerConfig,
    XDG_CACHE_HOME: ownerWork, TMPDIR: ownerWork,
    ...capability, MOTIONWRIGHT_GRAPH_STORE_FILE_BYTES: '4194304' });
}

/** One vetted R74 Python owner, never runner-provided arbitrary environment. */
export function graphOwnerSpawnEnvironment(spec, root, restriction, initializer, metadataToken, runner = process.env) {
  const source = spec?.source, paths = spec?.paths, runtime = spec?.runtime, binaries = spec?.binaries;
  need(source && typeof source.sha === 'string' && /^[0-9a-f]{40}$/.test(source.sha)
    && typeof source.run_id === 'string' && /^[1-9][0-9]*$/.test(source.run_id)
    && source.run_attempt === 1
    && typeof source.sdk_root === 'string' && path.isAbsolute(source.sdk_root),
    'GRAPH_OWNER_SOURCE_IDENTITY');
  // Source-authored makeSpec creates a fresh child "owner" below the case
  // custody root. That owner root must NOT already exist before Python starts.
  need(paths && typeof root === 'string' && path.isAbsolute(root)
    && paths.private_root === path.join(root, 'owner')
    && paths.owner_home === path.join(paths.private_root, 'home')
    && paths.owner_runtime === path.join(paths.private_root, 'runtime')
    && paths.owner_state === path.join(paths.private_root, 'state')
    && paths.owner_socket === path.join(paths.private_root, 'runtime', 'broker.sock')
    && paths.manim_work === path.join(paths.private_root, 'work')
    && runtime && typeof runtime.root === 'string' && path.isAbsolute(runtime.root)
    && binaries && typeof binaries.python3?.path === 'string' && path.isAbsolute(binaries.python3.path)
    && typeof binaries.ffmpeg?.path === 'string' && path.isAbsolute(binaries.ffmpeg.path),
    'GRAPH_OWNER_RUNTIME_INPUTS');
  need(restriction && Number.isSafeInteger(restriction.bytes) && restriction.bytes > 0
    && Number.isSafeInteger(restriction.files) && restriction.files > 0
    && initializer && typeof initializer.path === 'string' && path.isAbsolute(initializer.path)
    && typeof initializer.sha256 === 'string' && /^[a-f0-9]{64}$/.test(initializer.sha256),
    'GRAPH_OWNER_ORIGINAL_BUDGET');
  const workflowRef = runner?.GITHUB_WORKFLOW_REF;
  need(typeof workflowRef === 'string'
    && workflowRef.startsWith('seradotcom/motionwright/.github/workflows/studio-service-verification.yml@')
    && workflowRef.length < 512, 'GRAPH_OWNER_WORKFLOW_REF');
  need(metadataToken == null || typeof metadataToken === 'string' && metadataToken.length > 0,
    'GRAPH_OWNER_READ_ONLY_TOKEN');
  const result = { ...GRAPH_BASE_CHILD_ENV,
    HOME: paths.owner_home, XDG_RUNTIME_DIR: paths.owner_runtime,
    XDG_STATE_HOME: paths.owner_state,
    XDG_CONFIG_HOME: path.join(paths.private_root, 'config'),
    XDG_CACHE_HOME: path.join(paths.private_root, 'work'),
    // Python starts before the fresh owner child exists. Its temporary files
    // must remain in the already admitted 0700 parent, never fall back to /tmp.
    TMPDIR: root,
    SEMWRIGHT_CHECKOUT: source.sdk_root,
    SEMWRIGHT_TEST_MANIM_RUNTIME: runtime.root,
    // The sealed producer stores these actual captured tools under binaries;
    // runtime contains only the native profile root and manifests.
    SEMWRIGHT_TEST_PYTHON: binaries.python3.path,
    SEMWRIGHT_TEST_FFMPEG: binaries.ffmpeg.path,
    GITHUB_ACTIONS: 'true', GITHUB_API_URL: 'https://api.github.com',
    GITHUB_REPOSITORY: 'seradotcom/motionwright',
    GITHUB_EVENT_NAME: 'workflow_dispatch', GITHUB_JOB: 'real-service',
    GITHUB_SHA: source.sha, GITHUB_WORKFLOW_SHA: source.sha,
    GITHUB_RUN_ID: source.run_id, GITHUB_RUN_ATTEMPT: '1',
    GITHUB_WORKFLOW_REF: workflowRef,
    GIT_OPTIONAL_LOCKS: '0', GIT_NO_LAZY_FETCH: '1',
    MOTIONWRIGHT_GRAPH_OWNER_MAX_BYTES: String(restriction.bytes),
    MOTIONWRIGHT_GRAPH_OWNER_MAX_FILES: String(restriction.files),
    MOTIONWRIGHT_GRAPH_INITIALIZER_ORIGINAL: initializer.path,
    MOTIONWRIGHT_GRAPH_INITIALIZER_SHA256: initializer.sha256,
    MOTIONWRIGHT_GRAPH_PARENT_ROOT: root };
  if (metadataToken != null) result.MOTIONWRIGHT_GRAPH_METADATA_TOKEN = metadataToken;
  return Object.freeze(result);
}

function bounded(promise, deadline, code) {
  let timer;
  const rejected = new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error(code)), Math.max(1, deadline - Date.now())); });
  return Promise.race([promise, rejected]).finally(() => clearTimeout(timer));
}
async function completeAll(promises, deadline, code) {
  const results = await bounded(Promise.allSettled(promises), deadline, code);
  const failures = results.filter(item => item.status === 'rejected').map(item => item.reason);
  if (failures.length) throw new AggregateError(failures, code);
  return results.map(item => item.value);
}
function fileRef(file, expected, maximum) {
  const result = stableRead(file, maximum); if (expected) same(result.ref, expected, 'ORIGINAL_REFERENCE_MISMATCH'); return result;
}
function safeExit(exit, pid) {
  need(exit && exit.processId === pid && integer(pid, 1) && integer(exit.code, 0, 0)
    && exit.signal === null && typeof exit.observedAt === 'string'
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(exit.observedAt)
    && Number.isFinite(Date.parse(exit.observedAt)), 'OBSERVED_SERVICE_EXIT_NOT_CLEAN');
}
const production = Object.freeze({ spawn, admitSource: async options => {
  const admitted = await admitOriginalProducers(options);
  assertOriginalProducerAdmission(admitted);
  need(admitted && !Object.hasOwn(admitted,'synthetic_adapter_only'),'SYNTHETIC_ADMISSION_FORBIDDEN');
  schema(admitted.source,SPEC_SCHEMA.properties.source);
  schema(admitted.binaries,SPEC_SCHEMA.properties.binaries);
  schema(admitted.runtime,SPEC_SCHEMA.properties.runtime);
  for (const role of ['ordinaryService','nativeService']) schema(admitted[role],SPEC_SCHEMA.properties.binaries.properties.semwright);
  return admitted;
} });

/** Production entry point has no mock/skip flag. Source guard runs before the
 * initializer or owner is allowed to launch. The injected Core constructor is
 * exported for clearly synthetic unit adapters, not the browser entry point. */
export async function createGraphCaseFactory(options) {
  return GraphCaseFactoryCore.create(options, production);
}

const development = Object.freeze({spawn, admitSource: async options => {
  const admitted = await admitOriginalDevelopmentProducers(options);
  assertGraphProducerAdmission(admitted);
  need(admitted.development && !admitted.approval
    && !Object.hasOwn(admitted,'synthetic_adapter_only'), 'DEV_FORMAL_OR_SYNTHETIC_ADMISSION_FORBIDDEN');
  schema(admitted.source,SPEC_SCHEMA.properties.source);
  schema(admitted.binaries,SPEC_SCHEMA.properties.binaries);
  schema(admitted.runtime,SPEC_SCHEMA.properties.runtime);
  for (const role of ['ordinaryService','nativeService']) schema(admitted[role],SPEC_SCHEMA.properties.binaries.properties.semwright);
  return admitted;
}});
export async function createGraphDevelopmentCaseFactory(options) {
  return GraphCaseFactoryCore.create(options, development);
}

/** Pure layout calculation, never an admission. Production supplies only the
 * cases_root and case ID already bound by the original index/status. */
export function graphCaseLayout(casesRoot, caseId) {
  // Use the same pure, closed case mapping as R74's original producer guard.
  // This public helper is never authority to create a Native case.
  return originalGraphCaseLayout(casesRoot, caseId);
}

// The six typed-Graph scenarios do not import media blobs. Bound the small
// managed CAS sidecar alongside SQLite without exhausting the original 3,000
// member pool when its full archive copy is independently pre-reserved.
export const GRAPH_CAS_ALLOWANCE = Object.freeze({ bytes: 4 * MiB, files: 16 });
const GRAPH_CAS_ANCHOR = '.motionwright-cas-root-v1.json';

/** A real Store::open creates a managed CAS root beside SQLite. Keep the four
 * kernel-bounded SQLite files distinct from the separately prepaid CAS slice.
 * Input comes only from the physical, pinned, no-follow inventoryTree. */
export function verifyGraphManagedStoreEntries(entries, sqliteName, casId, allowance = GRAPH_CAS_ALLOWANCE) {
  need(sqliteName === 'motionwright.sqlite3'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(casId),
  'CASE_CAS_ROOT_ID');
  need(allowance && allowance.bytes === GRAPH_CAS_ALLOWANCE.bytes
    && allowance.files === GRAPH_CAS_ALLOWANCE.files, 'CASE_CAS_ALLOWANCE_CHANGED');
  need(Array.isArray(entries)
    && entries.length <= 2 * (GRAPH_BUDGET.store_files + allowance.files) + 1,
  'CASE_STORE_TREE_BOUND');
  const control = '.motionwright-cas-' + casId;
  const sqliteNames = new Set([sqliteName, sqliteName + '-wal', sqliteName + '-shm', sqliteName + '-journal']);
  const casRequired = new Set(['blobs', 'blobs/' + GRAPH_CAS_ANCHOR, control,
    control + '/locks', control + '/root.lock', control + '/catalog.json']);
  const observed = new Set();
  let sqliteFiles = 0, casFiles = 0, casBytes = 0;
  for (const row of entries) {
    need(row && typeof row.path === 'string' && !observed.has(row.path)
      && !row.path.includes('\\') && !row.path.includes(String.fromCharCode(0))
      && !row.path.split('/').some(part => part === '..' || part === '.'),
    'CASE_STORE_DUPLICATE_OR_TRAVERSAL');
    observed.add(row.path);
    if (row.path === '') {
      need(row.kind === 'directory', 'CASE_STORE_ROOT_KIND'); continue;
    }
    if (sqliteNames.has(row.path)) {
      need(row.kind === 'file' && Number.isSafeInteger(row.sizeBytes)
        && row.sizeBytes >= 0 && row.sizeBytes <= GRAPH_BUDGET.store_file_bytes
        && /^[0-9a-f]{64}$/.test(row.sha256), 'CASE_SQLITE_FILE_KIND_OR_BOUND');
      sqliteFiles++; continue;
    }
    const directory = row.path === 'blobs' || row.path === control || row.path === control + '/locks'
      || row.path === 'blobs/sha256' || row.path === 'blobs/.staging'
      || /^blobs\/sha256\/[0-9a-f]{2}$/.test(row.path);
    const file = row.path === 'blobs/' + GRAPH_CAS_ANCHOR
      || row.path === control + '/root.lock' || row.path === control + '/catalog.json'
      || row.path.startsWith(control + '/locks/') && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.lock$/.test(row.path.slice((control + '/locks/').length))
      || /^blobs\/sha256\/([0-9a-f]{2})\/([0-9a-f]{64})$/.test(row.path)
        && row.path.split('/')[3].startsWith(row.path.split('/')[2]);
    need(directory || file, 'CASE_CAS_PATH_UNDECLARED');
    if (directory) need(row.kind === 'directory', 'CASE_CAS_DIRECTORY_KIND');
    else {
      need(row.kind === 'file' && Number.isSafeInteger(row.sizeBytes)
        && row.sizeBytes >= 0 && /^[0-9a-f]{64}$/.test(row.sha256),
      'CASE_CAS_FILE_KIND');
      casFiles++; casBytes += row.sizeBytes;
      need(casFiles <= allowance.files && casBytes <= allowance.bytes, 'CASE_CAS_FOOTPRINT_BOUND');
    }
  }
  need(observed.has(sqliteName) && sqliteFiles <= GRAPH_BUDGET.store_files, 'CASE_SQLITE_FILE_ROSTER');
  need([...casRequired].every(entry => observed.has(entry)), 'CASE_CAS_MANAGED_ROOT_INCOMPLETE');
  return Object.freeze({ sqlite_files: sqliteFiles, cas_files: casFiles, cas_bytes: casBytes });
}

/** Eligibility only: original physical Store validation is performed separately
 * by verifyStoreAllocation before any archived bytes or directory is created. */
export function verifyGraphManagedCopyBounds(entries, original, source, databasePath, sequence) {
  need(original && Array.isArray(original.entries), 'CASE_STORE_INVENTORY_MISSING');
  same(entries, original.entries, 'CASE_STORE_CHANGED_BEFORE_COPY');
  const files = entries.filter(entry => entry.kind === 'file');
  const bytes = files.reduce((total, entry) => total + entry.sizeBytes, 0);
  need(files.every(entry => Number.isSafeInteger(entry.sizeBytes) && entry.sizeBytes >= 0)
    && Number.isSafeInteger(bytes) && bytes > 0, 'CASE_STORE_COPY_BYTES_INVALID');
  need(sequence === 0 && source === path.dirname(databasePath)
    && bytes <= GRAPH_BUDGET.store_file_bytes * GRAPH_BUDGET.store_files + GRAPH_CAS_ALLOWANCE.bytes
    && files.length <= GRAPH_BUDGET.store_files + GRAPH_CAS_ALLOWANCE.files,
    'CASE_SINGLE_ORIGINAL_STORE_COPY');
  return Object.freeze({ bytes, files: files.length });
}

export class GraphCaseFactoryCore {
  static async create(options, adapters) {
    const admission = await adapters.admitSource(options);
    need(admission && admission.source && admission.ordinaryService && admission.nativeService,
      'SOURCE_ADMISSION_MISSING');
    let layout = {};
    if (admission.binaries && !admission.synthetic_adapter_only) {
      assertGraphProducerAdmission(admission);
      const context = admission.development?.context ?? admission.approval?.context;
      const expected = admission.development
        ? 'mw-development/' + admission.source.run_id + '/1/' + options.caseId
        : 'mw-owner/phase1-native-graph/' + admission.source.run_id + '/1/' + options.caseId;
      need(context === expected,
        'CASE_LAYOUT_NOT_THE_ADMITTED_CASE');
      // The browser's outputDir is only a provisional, untrusted test path.
      // Containment must be checked on the case root derived from the
      // independently admitted producer (g1..g6), never on that placeholder.
      layout = graphCaseLayout(admission.execution.cases_root, options.caseId);
      inside(admission.execution.cases_root, layout.privateRoot);
      need(!fs.existsSync(layout.privateRoot), 'CASE_CANONICAL_ROOT_ALREADY_EXISTS');
    }
    const selected = admission.binaries ? {
      ...options,
      ...layout,
      ownerScript: path.join(admission.source.repository_root, 'tooling', 'native_graph_browser_owner.py'),
      python: admission.binaries.python3.path,
      ...(!admission.synthetic_adapter_only ? {
        ownerBytes: admission.execution.budget.owner_bytes, ownerFiles: admission.execution.budget.owner_files,
        journalBytes: admission.execution.budget.journal_bytes, journalFiles: admission.execution.budget.journal_files,
        maxStreamBytes: MiB,
        deadlineUnixMs: Math.min(options.deadlineUnixMs, admission.execution.deadline_unix_ms - 1200000),
      } : {}),
    } : options;
    return new GraphCaseFactoryCore(selected, admission, adapters);
  }
  constructor(options, admission, adapters) {
    this.options = options; this.admission = admission; this.adapters = adapters;
    // Source admission alone supplies C_i. Caller options and observed callback
    // counts are not authority. Missing authentic input denies before creation.
    const ticketPlan = snapshotSdkTicketPlan(admission);
    this.originalProduction = !admission.synthetic_adapter_only;
    if (this.originalProduction) {
      assertGraphProducerAdmission(admission);
      same(admission.execution.budget, GRAPH_BUDGET, 'CASE_ALLOCATION_NOT_ADMITTED');
      inside(admission.execution.cases_root, options.privateRoot);
      const expectedLayout = graphCaseLayout(admission.execution.cases_root, options.caseId);
      need(options.privateRoot === expectedLayout.privateRoot && options.databasePath === expectedLayout.databasePath,
        'CASE_CANONICAL_LAYOUT_CHANGED');
      need(options.databasePath === path.join(options.privateRoot, 'store', 'motionwright.sqlite3'), 'CASE_STORE_LAYOUT');
      // The controller stages authenticated executables. Each actual Store
      // process separately confirms its fixed 4 MiB soft and hard limits.
      verifyGraphControllerFileLimit(admission.binaries, fs.readFileSync('/proc/self/limits', 'utf8'));
    }
    if (fs.existsSync(options.privateRoot)) {
      need(!this.originalProduction, 'CASE_CANONICAL_ROOT_ALREADY_EXISTS');
      this.root = existingDirectory(options.privateRoot);
      const pin = pinDirectory(this.root);
      try {
        need(fs.readdirSync('/proc/self/fd/' + pin.fd).length === 0, 'CASE_ROOT_NOT_FRESH_EMPTY');
        fs.fchmodSync(pin.fd, 0o700); pin.verify();
      } finally { pin.close(); }
    } else this.root = freshDirectory(options.privateRoot);
    this.deadline = options.deadlineUnixMs;
    need(integer(this.deadline, Date.now() + 1, Date.now() + 900000), 'CASE_DEADLINE');
    this.pool = new SharedPool(); this.failures = []; this.launches = new Map(); this.helpers = new Map();
    if (this.originalProduction) {
      // Keep J2's one original 512 MiB/4096 allocator. The unavailable capacity
      // is irrevocably withheld, so every existing writer shares the 260/3000
      // allocation. Directory members also consume archive capacity.
      this.pool.reserve('aggregate-unavailable', 'aggregate-holdback', GLOBAL_BYTES - GRAPH_BUDGET.case_bytes,
        GLOBAL_FILES - GRAPH_BUDGET.case_members);
      this.pool.reserve('directory-members', 'archive-directory-capacity', 1, 256);
      this.pool.reserve('original-sqlite-files', 'kernel-bounded-original-store',
        GRAPH_BUDGET.store_file_bytes * GRAPH_BUDGET.store_files, GRAPH_BUDGET.store_files);
      // Store::open itself writes a managed CAS anchor and sibling control next
      // to SQLite. Charge these external files before the initializer starts.
      this.casAllowance = this.pool.reserve('original-managed-cas-files', 'store-cas',
        GRAPH_CAS_ALLOWANCE.bytes, GRAPH_CAS_ALLOWANCE.files);
      this.casRestriction = this.pool.external(this.casAllowance);
    }
    this.sdkTickets = new SdkTicketPrewrite(this.pool, ticketPlan);
    this.declaredNativeLaunches = ticketPlan.plan.clients.length;
    this.clientAdmissionTail = Promise.resolve();
    this.sealed = false; this.owner = null; this.ownerStart = null; this.closePromise = null; this.closed = false;
    this.nativeQuiescence = null; // None -> consumed -> original owner receipt; never replayed.
    this.metadata = new OriginalWriter(this.root, this.pool.counter(this.pool.reserve('parent-metadata', 'metadata', 4 * MiB, 128)));
    this.closureWriter = new OriginalWriter(this.root, this.pool.counter(this.pool.reserve('parent-close-and-errors', 'close', 4 * MiB, 64)));
    this.evidenceWriter = new OriginalWriter(this.root, this.pool.counter(this.pool.reserve('service-evidence-metadata', 'evidence', 16 * MiB, 128)));
    this.ownerAllowance = this.pool.reserve('python-owner', 'external-owner', options.ownerBytes ?? 64 * MiB, options.ownerFiles ?? 1024);
    this.ownerRestriction = this.pool.external(this.ownerAllowance);
    this.metadata.json(path.join(this.root, 'parent-source-binding.original.json'), admission);
    this.metadata.json(path.join(this.root, 'sdk-ticket-precharge.original.json'), this.sdkTickets.observation());
    if (this.originalProduction) {
      this.requireFutureCapacity();
      const allocation = this.pool.reserve('case-consumption', 'immutable-case-consumption', 8192, 1);
      const writer = new OriginalWriter(admission.execution.producer_root, this.pool.counter(allocation));
      writer.json(path.join(admission.execution.producer_root, 'consumption', options.caseId + '.original.json'), {
        case_id: options.caseId, private_root: this.root, index: admission.producer_originals.index,
        ...(admission.development ? {development_authorization: {
          kind:admission.development.kind, index_sha256:admission.development.index_sha256,
          request_sha256:admission.development.request_sha256,
          actor_id:admission.development.actor_id,
        }} : {status_id: admission.approval.status_id}),
        source: admission.source,
        meaning: 'IRREVOCABLY_CONSUMED_BEFORE_INITIALIZER', automatic_retry: false,
      });
    }
    this.rawRoot = freshDirectory(path.join(this.root, 'raw'));
    this.rawStreamCapture = this;
    this.evidenceCapture = this;
  }
  async publish(file, bytes) {
    try { return this.evidenceWriter.publish(inside(this.root,file),Buffer.from(bytes)); }
    catch(error) { this.taint(error); throw error; }
  }
  async inventory(directory) { try { return inventoryTree(directory); } catch (error) { this.taint(error); throw error; } }
  async copy(source,destination) { return this.copyEvidence(source,destination); }
  async copyClosedStore(source,captureRoot) {
    const snapshotDirectory = path.join(captureRoot,'store');
    const entries = await this.copyEvidence(source,snapshotDirectory,captureRoot);
    return { snapshotDirectory, entries };
  }
  /**
   * Reserve one post-close, source-built LIVE reader BEFORE creating any
   * initializer/Native writer. The already authenticated R74 original factory
   * owns the only SharedPool; this reservation does not grant callbacks or
   * alter the original Source/R74 authorization rules.
   */
  reserveDevelopmentReadOnlyOriginals() {
    return reserveOriginalDevelopmentReader(this);
  }
  /**
   * Source-owned writer closure and independent read-only process. The
   * returned ScopedChangeService is an actual live read-only child with its
   * own bounded raw stdout/stderr originals. No stored oracle or copied DB
   * masquerades as a live process.
   */
  async openIndependentlyCustodiedReadOnlyService(writer, project, history) {
    need(this.developmentOriginalService
      && typeof this.developmentOriginalService.createReadOnly === 'function'
      && this.developmentReaderReservation,
      'GRAPH_DEV_LIVE_READER_ORIGINAL_WRITER_FACTORY_MISSING');
    return openOriginalDevelopmentReadOnly(this,writer,project,history,
      this.developmentOriginalService);
  }
  assertDevelopmentReadOnlyClosed() {
    return assertOriginalDevelopmentReaderClosed(this);
  }
  async createNativeService(Service, options, makeSpec) {
    this.checkOpen();
    if (this.developmentReaderReservation) {
      need(!this.developmentOriginalService,
        'GRAPH_DEV_WRITER_CLASS_ALREADY_BOUND');
      this.developmentOriginalService=Service;
    }
    need(options.databasePath === this.options.databasePath && !fs.existsSync(options.databasePath), 'INITIALIZER_REQUIRES_FRESH_EXPLICIT_STORE');
    const common = { ...options, rawStreamCapture:this, evidenceCapture:this, graphCustodyRequired:true };
    let initializer;
    try {
      initializer = await Service.create({ ...common, binaryPath:this.admission.ordinaryService.path,
        processOwner:undefined, seedFixture:true });
      const fixture = await initializer.fixtureBootstrap();
      let originalProject = fixture.project;
      if (this.developmentReaderReservation) {
        // The ordinary fixture has authored Canvas/ProductHero content.
        // Retagging that scene as Manim is invalid: Native must not discard it.
        // Use the existing scoped Store operations to add one empty scene
        // BEFORE mounting Native, preserving every pre-existing scene/profile.
        need(Array.isArray(originalProject?.scenes)
          && originalProject?.deliverables?.[0]?.id
          && hasNoSemanticGraphs(originalProject),
          'GRAPH_DEV_INITIALIZER_REAL_SCENE_OR_SOURCE_UNAVAILABLE');
        const before = structuredClone(originalProject);
        originalProject = await initializer.apply(originalProject, {
          type:'add_scene', name:'Synthetic Native Graph development scene',
          objective:'One authentic Graph Save and independent persistence verification',
          duration_seconds:5,
        });
        const priorIds = new Set(before.scenes.map(scene => scene.id));
        const added = originalProject.scenes.filter(scene => !priorIds.has(scene.id));
        need(added.length === 1
          && originalProject.scenes.length === before.scenes.length + 1,
          'GRAPH_DEV_INITIALIZER_REAL_SCENE_OR_SOURCE_UNAVAILABLE');
        originalProject = await initializer.apply(originalProject, {
          type:'set_scene_renderer', scene_id:added[0].id,
          renderer:'manim-community',
        });
        same(await initializer.project(originalProject.id),originalProject,
          'GRAPH_DEV_INITIALIZER_AUTHENTIC_SQLITE_READBACK_CHANGED');
        this.developmentSourceSelection = developmentGraphSourceSelection(before,originalProject);
        this.metadata.json(path.join(this.root,
          'graph-dev-manifest-scene-source.original.json'),{
          source:this.admission.source, project_id:originalProject.id,
          scene_id:this.developmentSourceSelection.sceneId,
          profile_id:this.developmentSourceSelection.profileId,
          prior_revision:before.revision,
          selected_revision:originalProject.revision,
          declared_renderer:'manim-community',
          pre_native_scoped_store_mutation:true, native_grant_issued:false,
          automatic_retry:false, release_acceptance:false,
        });
      }
      await initializer.close();
      await this.completeInitializer();
      const physical = this.initializerPhysical;
      this.configureSpec(await makeSpec(originalProject,physical,this));
      // The fixed Graph recipe can use 5s + 5s + 30s; its existing Native CLI
      // transport is bounded at 45s. Let that original result and custody return
      // before the outer RPC expires, without changing the case or Host deadline.
      return await Service.create({ ...common, timeoutMs:options.timeoutMs ?? 50_000,
        binaryPath:this.admission.nativeService.path, processOwner:this, seedFixture:false });
    } catch(error) {
      this.taint(error);
      if(initializer) { try { await initializer.close(); } catch(cleanup) { this.taint(cleanup); } }
      if(this.owner) { try { await this.closeAfterClients({closed:false,receipt_path:null,receipt_sha256:null}); } catch(cleanup) { this.taint(cleanup); } }
      throw error;
    }
  }
  taint(error) { this.sdkTickets?.fail(); this.failures.push(String(error)); }
  checkOpen() {
    need(!this.sealed && !this.closed && Date.now() < this.deadline && this.failures.length === 0, 'CASE_ADMISSIONS_CLOSED');
    this.sdkTickets.assertSource(this.admission);
    this.sdkTickets.assertReady(this.pool);
    if (this.originalProduction) this.requireFutureCapacity();
  }
  requireFutureCapacity() {
    const natives = [...this.launches.values()].filter(row => row.role === 'native').length;
    const initializer = [...this.launches.values()].some(row => row.role === 'initializer');
    const remaining = this.declaredNativeLaunches - natives;
    need(integer(remaining, 0, 3), 'CASE_DECLARED_LAUNCH_BOUND');
    // The copy and independent reopen have not executed yet. Their maximum
    // allocations remain available before each actual launch; no refund is used.
    const bytes = remaining * (GRAPH_BUDGET.journal_bytes + 2 * MiB)
      + (initializer ? 0 : 2 * MiB) + (this.owner ? 0 : 2 * MiB)
      + 16 * MiB + 20 * MiB + 768 * 1024;
    // Pre-reserve the full original SQLite+CAS archive copy BEFORE any
    // initializer or Native launch. A four-file-only estimate would accept
    // callbacks that cannot later be closed within the 3,000-member pool.
    const snapshotFiles = GRAPH_BUDGET.store_files + (this.originalProduction ? this.casAllowance.files : 0);
    const files = remaining * (GRAPH_BUDGET.journal_files + 2)
      + (initializer ? 0 : 2) + (this.owner ? 0 : 2) + snapshotFiles + 268;
    need(this.pool.reservedBytes + bytes <= this.pool.maximumBytes
      && this.pool.reservedFiles + files <= this.pool.maximumFiles, 'CASE_FULL_REMAINING_CUSTODY_NOT_RESERVED');
  }
  verifyStoreAllocation() {
    if (!this.originalProduction) return;
    const folder = path.dirname(this.options.databasePath), base = path.basename(this.options.databasePath);
    need(this.casAllowance && this.casRestriction &&
      this.casAllowance.bytes === this.casRestriction.bytes &&
      this.casAllowance.files === this.casRestriction.files, 'CASE_CAS_PRECHARGE_MISSING');
    // The complete SQLite/CAS parent remains in this same original inventory.
    // Its CAS files have an independent prepaid quota; SQLite retains exactly
    // its four kernel-size-bounded file slots and original path.
    const entries = inventoryTree(folder, {
      bytes: GRAPH_BUDGET.store_file_bytes * GRAPH_BUDGET.store_files + this.casAllowance.bytes,
      files: GRAPH_BUDGET.store_files + this.casAllowance.files,
    });
    const anchorPath = path.join(folder, 'blobs', GRAPH_CAS_ANCHOR);
    const anchorOriginal = stableRead(anchorPath, 65536);
    const anchorEntry = entries.find(row => row.path === 'blobs/' + GRAPH_CAS_ANCHOR);
    need(anchorEntry && anchorEntry.kind === 'file'
      && anchorEntry.sha256 === anchorOriginal.ref.sha256
      && anchorEntry.sizeBytes === anchorOriginal.ref.size_bytes, 'CASE_CAS_ANCHOR_CHANGED');
    const anchor = strictJSON(anchorOriginal.data, 65536);
    exact(anchor, ['format_version', 'root_id', 'control', 'canonical_root',
      'directory_id', 'control_id', 'lock_id'], 'CASE_CAS_ANCHOR_FIELDS');
    need(anchor.format_version === 1
      && anchor.canonical_root === path.join(folder, 'blobs')
      && anchor.control === path.join(folder, '.motionwright-cas-' + anchor.root_id)
      && [anchor.directory_id, anchor.control_id, anchor.lock_id]
        .every(value => typeof value === 'string' && value.length > 0 && value.length <= 256),
      'CASE_CAS_ANCHOR_IDENTITY');
    const summary = verifyGraphManagedStoreEntries(entries, base, anchor.root_id, this.casAllowance);
    // Bind the exact original inventory to the later archive copy. Rechecking
    // the size alone would admit changed CAS siblings between observations.
    return Object.freeze({ entries, cas_root_id: anchor.root_id, ...summary });
  }
  reserveLaunch(context) {
    try {
    this.checkOpen(); const id = tuple(context);
    need(!this.launches.has(id), 'SERVICE_TUPLE_REUSED');
    const role = context.binary_path === this.admission.ordinaryService.path ? 'initializer'
      : context.binary_path === this.admission.nativeService.path ? 'native' : null;
    need(role && context.database_path === this.options.databasePath, 'SERVICE_SOURCE_OR_STORE_MISMATCH');
    if (!this.admission.synthetic_adapter_only) {
      const original = this.admission[role === 'initializer' ? 'ordinaryService' : 'nativeService'];
      fileRef(original.path,original,GLOBAL_BYTES);
    }
    let helper = this.helpers.get(context.helper_id);
    if (!helper) { helper = { role, binaryPath: context.binary_path, launches: [] }; this.helpers.set(context.helper_id, helper); }
    need(helper.role === role && helper.launches.length === context.launch_index, 'SERVICE_NONCONTIGUOUS_LAUNCH');
    if (role === 'initializer') need(context.launch_index === 0 && ![...this.launches.values()].some(item => item.role === role), 'INITIALIZER_REPEATED');
    // An A3 restart consumes a fresh predeclared slot; UUIDs are not fabricated
    // before A3 generates them. Bind to its original tuple, exactly once.
    const ticket = role === 'native' ? this.sdkTickets.bindLaunch(context) : null;
    const streamLimit = this.options.maxStreamBytes ?? MiB;
    const raw = this.pool.reserve('service-raw/' + id, 'service-raw', streamLimit * 2, 2);
    const record = { id, context: Object.freeze({ ...context }), role, pid: null, rawComplete: false,
      exit: null, binding: null, client: null, clientAttempted: false, ticket, rawCounter: this.pool.counter(raw) };
    // Consume the journal slice before environmentForNewClient can mount.
    if (role === 'native') {
      const reservation = this.pool.reserve('native-journal/' + id, 'native-journal', this.options.journalBytes ?? 64 * MiB, this.options.journalFiles ?? 512);
      record.journal = this.pool.external(reservation);
    }
    this.launches.set(id, record); helper.launches.push(record);
    this.metadata.json(path.join(this.root, 'reservation-' + this.launches.size + '.original.json'),
      { context, role, raw, native_journal: record.journal ?? null, sdk_ticket: ticket, allocations: this.pool.observations() });
    } catch (error) { this.taint(error); throw error; }
  }
  bind(context, streams) {
    try {
      const id = tuple(context), record = this.launches.get(id);
      need(record && !record.binding && !record.bindAttempted && isDeepStrictEqual(record.context, context), 'RAW_BIND_WITHOUT_RESERVATION');
      record.bindAttempted = true;
      need(integer(streams.process_id, 1) && streams.exited instanceof Promise, 'RAW_BIND_ACTUAL_PROCESS_REQUIRED');
      record.pid = streams.process_id;
      // Install this observation before any publication or raw preparation can
      // fail. Even a tainted launch remains an actual process to close.
      record.exitPromise = streams.exited.then(exit => {
        record.exit = structuredClone(exit);
        this.metadata.json(path.join(this.root, 'exit-' + context.helper_id + '-' + context.launch_index + '.original.json'), { context, exit });
        safeExit(exit, record.pid); return exit;
      }).catch(error => { this.taint(error); throw error; });
      record.exitPromise.catch(() => undefined);
      this.checkOpen();
      this.metadata.json(path.join(this.root, 'launch-' + context.helper_id + '-' + context.launch_index + '.original.json'), { context, process_id: record.pid });
      const binding = bindRawPair({ root: this.rawRoot, prefix: context.helper_id + '-' + context.launch_index,
        counter: record.rawCounter, streams, streamLimit: this.options.maxStreamBytes ?? MiB,
        deadlineUnixMs: this.deadline, onFailure: error => this.taint(error) });
      record.binding = binding;
      record.rawPromise = binding.settled.then(raw => { record.rawComplete = true; record.raw = raw; return raw; })
        .catch(error => { this.taint(error); throw error; });
      record.rawPromise.catch(() => undefined);
      // Settlement covers both raw sinks only. Real exit is observed above;
      // neither sink waits for owner shutdown or Service's own close promise.
      return { settled: record.rawPromise.then(() => undefined) };
    } catch (error) { this.taint(error); throw error; }
  }
  async completeInitializer() {
    try {
      need(!this.initializerOriginal, 'INITIALIZER_CLOSURE_REPLAY');
    const initializers = [...this.launches.values()].filter(item => item.role === 'initializer');
    need(initializers.length === 1, 'ORIGINAL_INITIALIZER_MISSING'); const item = initializers[0];
    need(item.binding && item.exitPromise, 'INITIALIZER_NEVER_BOUND');
    await completeAll([item.exitPromise, item.rawPromise], this.deadline, 'INITIALIZER_CLOSURE_DEADLINE');
    safeExit(item.exit, item.pid);
    this.verifyStoreAllocation();
    this.initializerPhysical = Object.freeze(stableRead(this.options.databasePath,GLOBAL_BYTES).physical);
    this.initializerPhysicalOriginal = this.metadata.json(path.join(this.root,'initializer-store-physical.original.json'),this.initializerPhysical);
    this.initializerOriginal = this.metadata.json(path.join(this.root, 'initializer.original.json'), {
      context: item.context, process_id: item.pid, exit: item.exit, raw: item.raw, physical_store_original: this.initializerPhysicalOriginal,
    });
    return this.initializerOriginal;
    } catch (error) { this.taint(error); throw error; }
  }
  verifyInitializerPhysical() {
    need(this.initializerPhysical && this.initializerPhysicalOriginal, 'INITIALIZER_PHYSICAL_BINDING_MISSING');
    const retained = strictJSON(fileRef(this.initializerPhysicalOriginal.path,this.initializerPhysicalOriginal,65536).data);
    same(retained,this.initializerPhysical,'INITIALIZER_PHYSICAL_ORIGINAL_CHANGED');
    same(stableRead(this.options.databasePath,GLOBAL_BYTES).physical,this.initializerPhysical,'INITIALIZER_PHYSICAL_STORE_REPLACED');
  }
  async beforeLaunch(context) {
    try {
      this.checkOpen(); const record = this.launches.get(tuple(context));
      need(record && record.role === 'native' && !record.clientAttempted && isDeepStrictEqual(record.context, context), 'CLIENT_WITHOUT_PRIOR_SLICE');
      record.clientAttempted = true;
      const ticket = this.sdkTickets.claimClient(record.context);
      this.metadata.json(path.join(this.root, 'sdk-ticket-client-' + ticket.ordinal + '.intent.original.json'), ticket);
      // Serialize the existing owner channel. A failed/UNKNOWN predecessor
      // rejects every queued admission; none is refunded or sent again.
      const result = this.clientAdmissionTail.then(() => this.#admitPrechargedClient(record));
      this.clientAdmissionTail = result.then(() => undefined);
      this.clientAdmissionTail.catch(() => undefined);
      return await result;
    } catch (error) { this.taint(error); throw error; }
  }
  async #admitPrechargedClient(record) {
    try {
      this.checkOpen();
      this.verifyInitializerPhysical();
      await this.startOwner();
      this.checkOpen(); this.verifyInitializerPhysical();
      const response = await this.request({ op: 'new-client', id: record.id }, 'CLIENT');
      this.checkOpen();
      exact(response, ['event','id','client_id','child_env','connection_receipt'], 'CLIENT_REPLY_FIELDS');
      exact(response.child_env, ['MOTIONWRIGHT_GRAPH_CONNECTION'], 'CLIENT_ENV_FIELDS');
      const connectionPath = response.child_env.MOTIONWRIGHT_GRAPH_CONNECTION;
      need(connectionPath === response.connection_receipt, 'CLIENT_CONNECTION_REFERENCE');
      inside(this.spec.paths.private_root, connectionPath);
      const original = fileRef(connectionPath, null, 65536), connection = strictJSON(original.data, 65536);
      exact(connection, ['schema','executable','executable_sha256','socket','session_file','output_root','resource'], 'CONNECTION_FIELDS');
      need(connection.schema === 'motionwright-semwright-connection/1' && connection.executable === this.spec.binaries.semwright.path
        && connection.executable_sha256 === this.spec.binaries.semwright.sha256
        && connection.socket === this.spec.paths.owner_socket && connection.resource === 'project:' + this.spec.store.project_id, 'CONNECTION_SOURCE_OR_STORE');
      inside(this.spec.paths.private_root, connection.session_file); inside(this.spec.paths.private_root, connection.output_root);
      need(![...this.launches.values()].some(other => other.client && (other.client.client_id === response.client_id || other.client.connectionPath === connectionPath)), 'CLIENT_REPLY_REUSED');
      const callbackEnv = this.sdkTickets.confirmClient(record.context, response.client_id, connection.session_file, this.spec.paths.private_root);
      record.client = { client_id: response.client_id, connectionPath, connectionOriginal: original.ref, connection };
      this.metadata.json(path.join(this.root, 'client-' + response.client_id + '.original.json'), { id: record.id, reply: response, connection: original.ref, quota: record.journal, sdk_ticket: record.ticket });
      this.checkOpen();
      return graphOwnedClientEnvironment({ ...response.child_env, ...callbackEnv,
        MOTIONWRIGHT_GRAPH_PRIVATE_MAX_BYTES: String(record.journal.bytes),
        MOTIONWRIGHT_GRAPH_PRIVATE_MAX_FILES: String(record.journal.files) }, this.spec.paths);
    } catch (error) { this.taint(error); throw error; }
  }
  environmentForNewClient(context) { return this.beforeLaunch(context); }
  async startOwner() {
    if (this.ownerStart) return this.ownerStart;
    this.ownerStart = this.launchOwner().catch(error => { this.taint(error); throw error; });
    this.ownerStart.catch(() => undefined); return this.ownerStart;
  }
  async launchOwner() {
    this.checkOpen();
    need(this.initializerOriginal, 'INITIALIZER_MUST_CLOSE_BEFORE_OWNER');
    need(this.spec, 'OWNER_SPEC_NOT_CONFIGURED');
    schema(this.spec, SPEC_SCHEMA);
    same(this.spec.source, this.admission.source, 'SPEC_SOURCE_NOT_ADMITTED');
    need(this.spec.store.database_path === this.options.databasePath && this.spec.limits.deadline_unix_ms === this.deadline, 'SPEC_STORE_OR_DEADLINE');
    const specOriginal = this.metadata.json(path.join(this.root, 'owner-spec.original.json'), this.spec);
    this.specOriginal = specOriginal;
    const rawAllocation = this.pool.reserve('python-owner-raw', 'owner-raw', 2 * (this.options.maxStreamBytes ?? MiB), 2);
    const argv = ['-I','-B',this.options.ownerScript,'--spec',specOriginal.path];
    const bootstrap = this.sdkTickets.claimBootstrap();
    this.metadata.json(path.join(this.root, 'sdk-ticket-bootstrap.intent.original.json'), bootstrap);
    const env = graphOwnerSpawnEnvironment(this.spec, this.root, this.ownerRestriction,
      this.initializerOriginal, this.originalProduction ? ownerReadOnlyMetadataCredential(this.admission) : null);
    this.metadata.json(path.join(this.root, 'owner-launch.intent.original.json'), { executable: this.options.python, argv,
      quota: this.ownerRestriction, initializer: this.initializerOriginal, source: this.admission.source });
    const child = this.adapters.spawn(this.options.python, argv, { stdio: ['pipe','pipe','pipe'], env, windowsHide: true });
    this.owner = child; this.messages = []; this.waiters = new Map(); this.received = new Set(); this.ownerProtocolFailure = null;
    this.ownerExit = new Promise(resolve => {
      child.once('close', (code, signal) => resolve({ process_id: child.pid, code, signal }));
      child.once('error', error => { this.taint(error); this.protocolFail(error); });
    });
    this.ownerRaw = bindRawPair({ root: this.rawRoot, prefix: 'python-owner', counter: this.pool.counter(rawAllocation), streams: child,
      streamLimit: this.options.maxStreamBytes ?? MiB, deadlineUnixMs: this.deadline, onFailure: error => this.taint(error) });
    const protocol = new BoundedJsonLines(message => this.receive(message));
    child.stdout.on('data', chunk => { try { protocol.push(chunk); } catch (error) { this.protocolFail(error); } });
    child.stdout.once('end', () => { try { protocol.end(); } catch (error) { this.protocolFail(error); } });
    child.stdin.on('error', error => this.protocolFail(error));
    const ready = await this.wait('READY', '', this.deadline);
    exact(ready, ['event','schema','case_id','source_sha','owner_pid','private_root','native_authoring_approved'], 'READY_FIELDS');
    need(ready.schema === 'motionwright.graph-browser-owner/6' && ready.case_id === this.spec.case_id
      && ready.source_sha === this.admission.source.sha && ready.owner_pid === child.pid
      && ready.private_root === this.spec.paths.private_root && ready.native_authoring_approved === false, 'READY_BINDING');
    this.ready = ready; return ready;
  }
  configureSpec(spec) { need(!this.ownerStart && !this.spec, 'OWNER_SPEC_REUSED'); schema(spec, SPEC_SCHEMA); this.spec = structuredClone(spec); }
  protocolFail(error) {
    if (!this.ownerProtocolFailure) this.ownerProtocolFailure = error;
    this.taint(error); for (const waiter of this.waiters?.values() ?? []) waiter.reject(error); this.waiters?.clear();
  }
  receive(message) {
    need(message && ['READY','CLIENT','PROVIDER_RELEASED','ERROR','FINAL_RECEIPT'].includes(message.event), 'OWNER_EVENT');
    if (message.event === 'ERROR') {
      need(ERROR_CODES.has(message.code), 'OWNER_ERROR_CODE');
      const stage = message.runtime_stage;
      need(stage === undefined || (message.code === 'RUNTIME_INVALID' &&
        GRAPH_RUNTIME_STAGES.has(stage)), 'OWNER_RUNTIME_STAGE_CODE');
      const hint = stage && stage !== 'UNCLASSIFIED' ? ' OWNER_STAGE:' + stage : '';
      this.protocolFail(new Error('OWNER_ERROR:' + message.code + hint)); return;
    }
    const key = message.event + ':' + (message.id ?? '');
    need(!this.received.has(key), 'OWNER_EVENT_REPLAY'); this.received.add(key);
    const waiter = this.waiters.get(key);
    if (waiter) { this.waiters.delete(key); waiter.resolve(message); }
    else { need(message.event === 'READY' && !this.messages.some(item => item.event === 'READY'), 'UNSOLICITED_OWNER_MESSAGE'); this.messages.push(message); }
  }
  wait(event, id, deadline) {
    if (this.ownerProtocolFailure) return Promise.reject(this.ownerProtocolFailure);
    const index = this.messages.findIndex(message => message.event === event && (message.id ?? '') === id);
    if (index >= 0) return Promise.resolve(this.messages.splice(index,1)[0]);
    const key = event + ':' + id; need(!this.waiters.has(key), 'DUPLICATE_OWNER_WAITER');
    const promise = new Promise((resolve,reject) => this.waiters.set(key,{resolve,reject}));
    return bounded(promise, deadline, 'OWNER_REPLY_DEADLINE').finally(() => this.waiters.delete(key));
  }
  request(message, event, cleanup = false) {
    try {
    if (message.op === 'new-client') {
      this.checkOpen(); this.sdkTickets.claimClientRequest(message.id);
    }
    need(this.owner && (cleanup || !this.ownerProtocolFailure), 'OWNER_UNAVAILABLE');
    const waiting = this.wait(event, message.id, Math.min(this.deadline, Date.now() + (this.spec?.limits.close_timeout_ms ?? 30000)));
    waiting.catch(() => undefined);
    const raw = Buffer.from(JSON.stringify(message) + '\n'); need(raw.length <= 1024, 'CONTROL_MESSAGE_BOUND');
    this.closureWriter.publish(path.join(this.root, 'control-' + this.controlSequence++ + '.original.jsonl'), raw);
    this.owner.stdin.write(raw, error => { if (error) this.protocolFail(error); });
    return waiting.catch(error => { if (message.op === 'new-client') this.taint(error); throw error; });
    } catch (error) { if (message.op === 'new-client') this.taint(error); throw error; }
  }
  controlSequence = 0;
  sealClientAdmissions() { this.sealed = true; }

  /** Release the real Semwright Native owner cohort while this exact Studio
   * writer is still alive. A prior Ready/boolean cannot replace the original
   * closed process tree, terminal streams and source-bound receipt. */
  async quiesceNativeOwner() {
    need(this.owner && this.ready && this.nativeQuiescence === null
      && !this.closed && this.failures.length === 0,
      'PROVIDER_RELEASE_ONE_SHOT');
    this.nativeQuiescence = false;
    this.sealClientAdmissions();
    const natives = [...this.launches.values()].filter(row => row.role === 'native');
    this.sdkTickets.requireComplete(natives.map(row => row.context));
    need(natives.length > 0 && natives.every(row => row.client),
      'PROVIDER_RELEASE_UNBOUND_CLIENT');
    const id = 'release-' + this.options.caseId;
    const reply = await this.request({ op:'quiesce', id }, 'PROVIDER_RELEASED');
    exact(reply, ['event','id','status','receipt_path','receipt_sha256','owner_pid',
                  'lease_shutdown_completed','native_process_closure_inferred'],
      'PROVIDER_RELEASE_RESPONSE_FIELDS');
    need(reply.event === 'PROVIDER_RELEASED' && reply.id === id
      && reply.status === 'RELEASED' && reply.owner_pid === this.ready.owner_pid
      && reply.lease_shutdown_completed === true
      && reply.native_process_closure_inferred === false
      && /^[0-9a-f]{64}$/.test(reply.receipt_sha256),
      'PROVIDER_RELEASE_RESPONSE_UNVERIFIED');
    inside(this.spec.paths.private_root, reply.receipt_path);
    const original = fileRef(reply.receipt_path, null, 65536);
    need(original.ref.sha256 === reply.receipt_sha256,
      'PROVIDER_RELEASE_ORIGINAL_DIGEST');
    const proof = strictJSON(original.data, 65536);
    same(proof.source, this.admission.source, 'PROVIDER_RELEASE_SOURCE_CHANGED');
    const binding = proof.original_mount_observation;
    const fresh = proof.fresh_mount_observation, released = proof.provider_release_observation;
    need(proof.schema === 'motionwright.graph-mounted-provider-release/1'
      && proof.owner_pid === this.ready.owner_pid
      && proof.source_verified_before_release === true
      && proof.provider_lease_shutdown_completed === true
      && proof.native_process_closure_inferred === false
      && proof.owner_broker_remains_live === true
      && proof.automatic_retry === false && proof.graph_acceptance === false
      && binding?.provider === 'driver:motionwright'
      && binding?.mount?.root === 'motionwright-data'
      && binding.mount.path === path.dirname(this.options.databasePath)
      && fresh?.binding_ref && fresh.connection === binding.connection
      && fresh.provider_generation === binding.provider_generation
      && released?.connection === binding.connection
      && released.provider_generation === binding.provider_generation
      && released.binding_ref === fresh.binding_ref
      && released.lease_shutdown_completed === true
      && released.native_process_closure_inferred === false
      && proof.original_store?.device === proof.current_store?.device
      && proof.original_store?.inode === proof.current_store?.inode,
      'PROVIDER_RELEASE_NOT_AUTHENTIC');
    for (const row of [proof.mount_command_originals, proof.release_command_originals]) {
      for (const key of ['terminal','stdout','stderr']) {
        need(row?.[key]?.path, 'PROVIDER_RELEASE_COMMAND_ORIGINALS_MISSING');
        inside(this.spec.paths.private_root, row[key].path);
        fileRef(row[key].path, row[key], 4 * MiB);
      }
    }
    const result = Object.freeze({
      ref: Object.freeze({ ...original.ref }), response: Object.freeze({ ...reply }),
    });
    this.nativeQuiescence = result;
    this.metadata.json(path.join(this.root, 'provider-release-parent.original.json'), {
      source:this.admission.source, owner_pid:reply.owner_pid,
      original:result.ref, raw_response:reply,
      automatic_retry:false, gate_credit:false,
    });
    return result;
  }


  expectedServices() {
    return new Map([...this.helpers].map(([id, value]) => [id, { binaryPath: value.binaryPath, launches: value.launches.map(item => ({ pid: item.pid, rawComplete: item.rawComplete, exit:item.exit })) }]));
  }
  async validateBarrier(barrier) {
    need(this.failures.length === 0, 'PARENT_CUSTODY_ALREADY_TAINTED');
    this.sdkTickets.requireComplete([...this.launches.values()].filter(item => item.role === 'native').map(item => item.context));
    exact(barrier, ['closed','receipt_path','receipt_sha256'], 'SERVICE_BARRIER_FIELDS');
    need(barrier.closed === true && typeof barrier.receipt_path === 'string' && /^[0-9a-f]{64}$/.test(barrier.receipt_sha256), 'SERVICE_BARRIER_NOT_CLOSED');
    inside(this.root, barrier.receipt_path);
    const original = stableRead(barrier.receipt_path, 4 * MiB);
    need(original.ref.sha256 === barrier.receipt_sha256, 'SERVICE_BARRIER_HASH');
    for (const record of this.launches.values()) {
      need(record.binding && record.exitPromise && (record.role !== 'native' || record.client), 'SERVICE_UNMATCHED_RESERVATION');
      await completeAll([record.rawPromise, record.exitPromise], this.deadline, 'SERVICE_EXTERNAL_OBSERVATION_DEADLINE');
      safeExit(record.exit, record.pid);
    }
    validateServiceClosure(strictJSON(original.data, 4 * MiB), this.expectedServices(), this.options.databasePath);
    need(this.failures.length === 0, 'PARENT_CUSTODY_TAINTED_DURING_BARRIER'); return original.ref;
  }
  async closeAfterClients(barrier) {
    this.sealClientAdmissions();
    if (this.closePromise) return this.closePromise;
    this.closePromise = this.closeOwner(barrier); this.closePromise.catch(() => undefined); return this.closePromise;
  }
  async closeOwner(barrier) {
    let validated = null, closeError = null, finalMessage = null;
    try { validated = await this.validateBarrier(barrier); } catch (error) { closeError = error; this.taint(error); }
    const safeBarrier = validated ? barrier : { closed:false,receipt_path:null,receipt_sha256:null };
    const closeId = 'close-' + this.options.caseId;
    try {
      if (this.owner) {
        // Invalid/false barriers and earlier protocol errors still send cleanup.
        if (this.ownerProtocolFailure) {
          const raw = Buffer.from(JSON.stringify({op:'close',id:closeId,service_barrier:safeBarrier})+'\n');
          this.closureWriter.publish(path.join(this.root,'cleanup-control.original.jsonl'),raw);
          this.owner.stdin.write(raw); this.owner.stdin.end();
        } else finalMessage = await this.request({op:'close',id:closeId,service_barrier:safeBarrier},'FINAL_RECEIPT',true);
        this.owner.stdin.end();
        const [exit, raw] = await completeAll([this.ownerExit,this.ownerRaw.settled],
          Math.min(this.deadline,Date.now()+this.spec.limits.close_timeout_ms),'OWNER_EXIT_OR_EOF_DEADLINE');
        this.ownerExitObserved = exit; this.ownerRawObserved = raw;
        this.closureWriter.json(path.join(this.root,'owner-parent-terminal.original.json'),{exit,raw});
        need(exit.process_id === this.ready?.owner_pid && integer(exit.code,0,0) && exit.signal === null, 'OWNER_ACTUAL_EXIT_NOT_CLEAN');
        need(finalMessage && validated && !closeError && this.failures.length === 0, 'OWNER_CLOSURE_INCOMPLETE');
        this.validateFinal(finalMessage, validated); this.closed = true;
      } else throw new Error('OWNER_NEVER_STARTED');
    } catch (error) {
      this.taint(error);
      // Target only the actual child handle. Source-owned Python cleanup handles
      // its cohort; forced termination never establishes CLOSED.
      if (this.owner && this.owner.exitCode === null) { try { this.owner.kill('SIGTERM'); } catch (cleanup) { this.taint(cleanup); } }
      try { this.closureWriter.json(path.join(this.root,'parent-close-error.original.json'),{errors:this.failures,closed:false}); }
      catch(custodyError) { throw new AggregateError([error,custodyError],'Owner closure and error-original retention failed'); }
      throw error;
    }
  }
  validateFinal(message, serviceOriginal) {
    this.verifyStoreAllocation();
    exact(message,['event','id','case_id','status','receipt_path','receipt_sha256','owner_cohort_absent','owner_streams_eof','capture_permitted'],'FINAL_MESSAGE_FIELDS');
    need(message.case_id === this.spec.case_id && message.status === 'CLOSED' && message.owner_cohort_absent === true
      && message.owner_streams_eof === true && message.capture_permitted === true,'FINAL_MESSAGE_CONJUNCTION');
    inside(this.spec.paths.private_root,message.receipt_path);
    const original = stableRead(message.receipt_path, 4*MiB); need(original.ref.sha256 === message.receipt_sha256,'FINAL_HASH');
    const final = strictJSON(original.data,4*MiB); schema(final,FINAL_SCHEMA);
    same(final.source,this.spec.source,'FINAL_SOURCE'); same(final.spec_original,this.specOriginal,'FINAL_SPEC'); same(final.service_closure,serviceOriginal,'FINAL_SERVICE_ORIGINAL');
    need(final.case_id === this.spec.case_id && final.status === 'CLOSED' && final.errors.length === 0
      && ['service_closure_validated','same_physical_database','owner_cohort_absent','owner_streams_eof','capture_permitted'].every(name => final[name] === true)
      && final.native_authoring_approved === false && final.formal_acceptance === false,'FINAL_CLOSED_CONJUNCTION');
    const natives = [...this.launches.values()].filter(item => item.role === 'native');
    this.sdkTickets.requireComplete(natives.map(item => item.context));
    need(final.client_roster.length === natives.length,'FINAL_CLIENT_COUNT');
    for (const [index,row] of final.client_roster.entries()) {
      const expected = natives[index];
      need(row.id === expected.id && row.helper_id === expected.context.helper_id && row.launch_index === expected.context.launch_index
        && row.client_id === expected.client.client_id && row.status === 'READY','FINAL_CLIENT_TUPLE');
      same(row.connection_original,expected.client.connectionOriginal,'FINAL_CONNECTION_ORIGINAL');
      need(row.output_root === expected.client.connection.output_root && row.session_original?.path === expected.client.connection.session_file,'FINAL_CLIENT_PATH');
      fileRef(row.session_original.path,row.session_original,MiB);
      // Journals remain in their original sibling directory. Check original
      // bytes against the pre-admitted allowance; this is verification, never
      // the admission mechanism (the Native writer enforces its slice before writes).
      const journals = inventoryTree(expected.client.connectionPath+'.graph-originals',{bytes:expected.journal.bytes,files:expected.journal.files});
      need(journals.filter(entry=>entry.kind==='file').every(entry=>entry.sizeBytes<=16*MiB),'JOURNAL_RECORD_BOUND');
    }
    for(const name of ['source_inputs','owner_close','database_initial','database_final']) {
      need(final[name]!==null,'FINAL_MISSING_ORIGINAL'); inside(this.spec.paths.private_root,final[name].path); fileRef(final[name].path,final[name],4*MiB);
    }
    const first=strictJSON(stableRead(final.database_initial.path,4*MiB).data), last=strictJSON(stableRead(final.database_final.path,4*MiB).data);
    this.verifyInitializerPhysical();
    for (const value of [first,last]) same({path:value.path,device:value.device,inode:value.inode},this.initializerPhysical,'FINAL_NOT_INITIALIZED_PHYSICAL_STORE');
    need(['device','inode'].every(name=>typeof first[name]==='string'&&/^[0-9]+$/.test(first[name])&&typeof last[name]==='string')
      && first.path===this.options.databasePath && last.path===first.path && first.device===last.device && first.inode===last.inode,'FINAL_PHYSICAL_STORE');
    const complete=strictJSON(stableRead(final.owner_close.path,4*MiB).data), cohort=complete.cohort;
    need(complete.status==='CLOSED'&&complete.source_validated_before_close===true&&complete.owner_streams_eof===true
      &&complete.lifecycle?.status==='CLOSED'&&cohort?.status==='PASS'&&cohort.subreaper===true
      &&cohort.complete_empty_descendant_tree?.root_observed===true&&cohort.complete_empty_descendant_tree.truncated===false
      &&cohort.complete_empty_descendant_tree.processes?.length===1
      &&cohort.complete_empty_descendant_tree.processes[0].pid===this.ready.owner_pid,'FINAL_COMPLETE_COHORT_ORIGINAL');
    if (!this.admission.synthetic_adapter_only) for (const role of ['ordinaryService','nativeService']) {
      const original = this.admission[role]; fileRef(original.path,original,GLOBAL_BYTES);
    }
    const inputs=strictJSON(stableRead(final.source_inputs.path,4*MiB).data);
    need(inputs.binary_source_causality_validated_by_this_helper===true,'FINAL_PRODUCER_CAUSALITY_MISSING');
    this.metadata.json(path.join(this.root, 'sdk-ticket-roster-binding.original.json'), this.sdkTickets.observation());
    this.finalOriginal=original.ref;
  }
  assertClosed() { need(this.closed && this.failures.length===0 && this.ownerExitObserved?.code===0 && this.ownerRawObserved,'OWNER_NOT_CLOSED'); }
  async copyEvidence(source,destination,captureRoot = null) {
    try {
      this.assertClosed(); inside(this.root,destination);
      if (captureRoot !== null) {
        inside(this.root,captureRoot);
        need(destination === path.join(captureRoot,'store'),'CAPTURE_ROOT_AND_STORE_MISMATCH');
      }
      const entries=inventoryTree(source), files=entries.filter(entry=>entry.kind==='file');
      const bytes=files.reduce((total,entry)=>total+entry.sizeBytes,0);
      need(files.length>0 && bytes>0,'EMPTY_STORE_COPY');
      if (this.originalProduction) {
        const original = this.verifyStoreAllocation();
        // The original SQLite and managed CAS are copied together, with their
        // distinct prepaid limits and identical physical source manifest.
        same({ bytes, files: files.length }, verifyGraphManagedCopyBounds(entries, original,
          source, this.options.databasePath, this.copySequence), 'CASE_COPY_BOUNDS_MISMATCH');
      }
      const allocation=this.pool.reserve('copy/'+this.copySequence++,'archive-copy',bytes,files.length);
      this.closureWriter.json(path.join(this.root,'copy-'+allocation.sequence+'.intent.original.json'),
        {source,destination,allocation,entries,...(captureRoot === null ? {} : {capture_root:captureRoot})});
      const writer=new OriginalWriter(this.root,this.pool.counter(allocation));
      if (captureRoot !== null) {
        // The copy slice and intent already exist before either directory is
        // created. Existing or partial capture roots are never reused.
        safeParents(this.root,captureRoot); freshDirectory(captureRoot);
      }
      return copyTreeExclusive(source,destination,writer);
    } catch(error) { this.taint(error); throw error; }
  }
  copySequence=0;
}
