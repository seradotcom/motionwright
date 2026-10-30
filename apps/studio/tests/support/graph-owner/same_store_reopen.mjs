/** Independent read-only Service reopen after the original owner has closed.
 * This is a mechanism proposal. The production source-admission guard remains
 * closed until actual source-bound builds and the CLI ticket contract exist.
 */
import * as fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { SharedPool, OriginalWriter, bindRawPair, stableRead, freshDirectory,
  inside, need, integer, GLOBAL_BYTES } from './custody.mjs';
import { strictJSON, exact, tuple } from './contracts.mjs';
import * as producerGuard from './producer_guard.mjs';
import { observeOpenedSQLiteDescriptor } from './opened_store_descriptor.mjs';
import { requireCoherentProcNamespace, captureLinuxProcessIdentity, expectedLinuxHeldStoreDigest,
  requireHeldStoreWitness, requireSameOpenedProcessAndStore } from './opened_store_identity_join.mjs';

const attempted = new WeakSet();
const presealAttempted = new WeakSet();
const presealOriginals = new WeakMap();
const MiB = 1024 * 1024;
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const allowed = new Set(['get_project', 'project_history', 'opened_store_identity_witness']);

function matchRef(file, expected, maximum) {
  const observed = stableRead(file, maximum);
  need(isDeepStrictEqual(observed.ref, expected), 'REOPEN_ORIGINAL_PIN');
  return observed;
}
function physical(file) {
  // Existing stableRead verifies canonical path, regular single-link file,
  // pinned parents, actual inode and no change across its bounded read.
  return stableRead(file, GLOBAL_BYTES).physical;
}
function same(a, b, code) { need(isDeepStrictEqual(a, b), code); }
function timeout(promise, deadline, code) {
  let timer;
  return Promise.race([promise, new Promise((_resolve, reject) => {
    timer = setTimeout(() => reject(new Error(code)), Math.max(1, deadline - Date.now()));
  })]).finally(() => clearTimeout(timer));
}
function groupAbsent(pid) {
  try { process.kill(-pid, 0); return false; }
  catch (error) { if (error.code === 'ESRCH') return true; throw error; }
}
function validateOracle(value) {
  exact(value, ['schemaVersion', 'observedAt', 'project', 'journal',
    'journalAfterRevisionExclusive', 'journalComplete'], 'REOPEN_ORACLE_FIELDS');
  need(value.schemaVersion === 1 && value.journalAfterRevisionExclusive === 0
    && value.journalComplete === true && UUID.test(value.project?.id)
    && typeof value.observedAt === 'string' && Number.isFinite(Date.parse(value.observedAt))
    && integer(value.project?.revision) && Array.isArray(value.journal)
    && value.journal.length === value.project.revision, 'REOPEN_ORACLE_SHAPE');
  for (const [index, event] of value.journal.entries()) {
    need(event && event.revision === index + 1, 'REOPEN_ORACLE_JOURNAL');
  }
  return value;
}

function requireOriginalAdmission(factory) {
  // R94's unchanged guard has no branded-admission assertion. Namespace
  // import keeps that DENY composition loadable; it does not supply an admitter.
  need(typeof producerGuard.assertOriginalProducerAdmission === 'function',
    'INDEPENDENT_SAME_STORE_REOPEN_PRODUCER_AND_REVIEWED_CONTRACT_UNAVAILABLE');
  producerGuard.assertOriginalProducerAdmission(factory?.admission);
  need(factory?.admission && !Object.hasOwn(factory.admission, 'synthetic_adapter_only'),
    'REOPEN_SYNTHETIC_SOURCE_FORBIDDEN');
}

function activeOriginalLaunch(factory, service) {
  factory.checkOpen();
  const stream = service.processStreams.at(-1);
  const helper = factory.helpers.get(service.helperId);
  const record = helper?.launches[stream?.launch_index];
  need(record && record === factory.launches.get(tuple(record.context))
    && helper.role === 'native' && record.role === 'native'
    && record.context.helper_id === service.helperId
    && record.context.binary_path === service.binaryPath
    && service.binaryPath === factory.admission.nativeService.path
    && record.context.database_path === service.databasePath
    && service.databasePath === factory.options.databasePath
    && record.pid === stream.process_id && integer(record.pid, 2)
    && stream.launch_index === helper.launches.length - 1
    && stream.exit_index === null && !stream.stdout_ended && !stream.stderr_ended
    && !stream.jsonl_reader_closed && record.exit === null
    && record.binding && record.client && record.rawComplete === false,
    'PRESEAL_ORIGINAL_LIVE_SERVICE_LAUNCH_REQUIRED');
  return record;
}

/** Capture through the actual Service, after browser preparation and before
 * admissions seal. The existing raw tee owns response bytes; its immutable
 * stream reference is available only after EOF and is checked below. */
export async function captureOriginalOpenedStoreWitness(factory, service, expected) {
  requireOriginalAdmission(factory);
  return capturePreseal(factory, service, expected, false);
}

/** Finite mechanism fixtures only; cannot create a production Admission. */
export async function captureOriginalOpenedStoreWitnessCore(factory, service, expected) {
  need(factory?.admission?.synthetic_adapter_only === true, 'PRESEAL_FIXTURE_ONLY');
  return capturePreseal(factory, service, expected, true);
}

async function capturePreseal(factory, service, expected, synthetic) {
  need(factory && !presealAttempted.has(factory), 'PRESEAL_NO_REPLAY');
  presealAttempted.add(factory);
  try {
    requireCoherentProcNamespace();
    need(factory.pool instanceof SharedPool, 'PRESEAL_ORIGINAL_POOL_REQUIRED');
    const record = activeOriginalLaunch(factory, service);
    factory.verifyInitializerPhysical();
    const before = physical(factory.options.databasePath);
    same(before, factory.initializerPhysical, 'PRESEAL_NOT_ORIGINAL_PHYSICAL_STORE');
    const identity = captureLinuxProcessIdentity(record.pid);
    const binary = factory.admission.nativeService;
    matchRef(binary.path, binary, GLOBAL_BYTES);
    const oracle = validateOracle(await service.observeStoredProject(expected));
    need(activeOriginalLaunch(factory, service) === record, 'PRESEAL_SERVICE_RESTARTED');
    const request = { id: 'graph-preseal-held-store-' + record.context.helper_id
      + '-' + record.context.launch_index, command: 'opened_store_identity_witness', args: null };
    const requestBytes = Buffer.from(JSON.stringify(request) + '\n');
    const requestRef = await factory.publish(path.join(factory.root,
      'original-service-preseal-request.original.jsonl'), requestBytes);
    const transcriptStart = service.transcript.length;
    const response = await service.rawRequest(request);
    exact(response, ['id', 'ok', 'result'], 'PRESEAL_RESPONSE_FIELDS');
    need(response.id === request.id && response.ok === true, 'PRESEAL_RESPONSE_CORRELATION');
    const witness = requireHeldStoreWitness(response.result, expectedLinuxHeldStoreDigest(before));
    const correlated = service.transcript.slice(transcriptStart)
      .filter(item => item.payload?.id === request.id);
    need(correlated.length === 2 && correlated[0].direction === 'request'
      && correlated[1].direction === 'response'
      && correlated.every(item => item.processId === record.pid), 'PRESEAL_ACTUAL_TRANSCRIPT');
    same(correlated[0].payload, request, 'PRESEAL_LITERAL_REQUEST_CHANGED');
    same(correlated[1].payload, response, 'PRESEAL_ACTUAL_RESPONSE_CHANGED');
    need(activeOriginalLaunch(factory, service) === record, 'PRESEAL_SERVICE_RESTARTED');
    const opened = observeOpenedSQLiteDescriptor({ pid: record.pid, expected: before });
    requireSameOpenedProcessAndStore(opened, opened, identity);
    same(captureLinuxProcessIdentity(record.pid), identity, 'PRESEAL_PROCESS_INCARNATION_CHANGED');
    same(physical(factory.options.databasePath), before, 'PRESEAL_PHYSICAL_STORE_CHANGED');
    matchRef(binary.path, binary, GLOBAL_BYTES);
    const observation = {
      phase: 'original-service-before-owner-seal', source: factory.admission.source,
      binary, context: record.context, process: identity, physical_store: before,
      observation: opened, held_store_digest: witness, request_original: requestRef,
      response, raw_stdout_path: path.join(factory.rawRoot,
        record.context.helper_id + '-' + record.context.launch_index + '.stdout.original'),
      raw_response_scope: 'existing tee; final bytes and unique response required after EOF',
      synthetic_adapter_only: synthetic, gate_credit: false,
    };
    const bytes = Buffer.from(JSON.stringify(observation, null, 2) + '\n');
    need(bytes.length <= 256 * 1024, 'PRESEAL_OBSERVATION_BOUND');
    const ref = await factory.publish(path.join(factory.root,
      'original-service-preseal-witness.original.json'), bytes);
    need(activeOriginalLaunch(factory, service) === record, 'PRESEAL_SERVICE_RESTARTED');
    // Same in-process producer, not a caller-provided observation/file/flag.
    presealOriginals.set(factory, { ref: Object.freeze({ ...ref }), record,
      oracle: structuredClone(oracle), observation: structuredClone(observation), synthetic });
    return Object.freeze({ ...ref });
  } catch (error) { factory?.taint?.(error); throw error; }
}

/** Read the original already published by finalizeClosedStore, never rewrite
 * it or substitute the captured Store. That publisher writes JSON without LF. */
export function publishedOracleReference(factory, destination, finalization) {
  factory.assertClosed();
  need(finalization?.errors?.length === 0 && finalization.serviceClosure?.closed === true
    && finalization.capture?.snapshotDirectory === path.join(destination, 'store'),
    'REOPEN_FINALIZATION_INCOMPLETE');
  const file = inside(factory.root, path.join(destination, 'oracle.json'));
  const original = stableRead(file, 16 * MiB);
  need(original.data.equals(Buffer.from(JSON.stringify(finalization.oracle, null, 2))),
    'REOPEN_PUBLISHED_ORACLE_CHANGED');
  validateOracle(strictJSON(original.data, 16 * MiB));
  const final = strictJSON(matchRef(factory.finalOriginal.path, factory.finalOriginal, 4 * MiB).data);
  need(final.service_closure?.path === finalization.serviceClosure.receipt_path
    && final.service_closure.sha256 === finalization.serviceClosure.receipt_sha256,
    'REOPEN_FINALIZER_OWNER_CLOSURE_MISMATCH');
  return Object.freeze({ ...original.ref });
}

function requirePresealOriginal(factory, ref, oracle, synthetic) {
  const captured = presealOriginals.get(factory);
  need(captured && captured.synthetic === synthetic, 'REOPEN_PRESEAL_PRODUCER_REQUIRED');
  same(ref, captured.ref, 'REOPEN_PRESEAL_REFERENCE_CHANGED');
  inside(factory.root, ref.path);
  const observation = strictJSON(matchRef(ref.path, ref, 256 * 1024).data);
  same(observation, captured.observation, 'REOPEN_PRESEAL_OBSERVATION_CHANGED');
  same(oracle.project, captured.oracle.project, 'REOPEN_PRESEAL_PROJECT_CHANGED');
  same(oracle.journal, captured.oracle.journal, 'REOPEN_PRESEAL_JOURNAL_CHANGED');
  same(observation.source, factory.admission.source, 'REOPEN_PRESEAL_SOURCE_CHANGED');
  same(observation.physical_store, factory.initializerPhysical, 'REOPEN_PRESEAL_STORE_CHANGED');
  matchRef(observation.binary.path, observation.binary, GLOBAL_BYTES);
  same(observation.binary, factory.admission.nativeService, 'REOPEN_PRESEAL_BINARY_CHANGED');
  const record = captured.record;
  need(record === factory.launches.get(tuple(observation.context)) && record.rawComplete
    && record.pid === observation.process.pid && record.exit?.processId === record.pid
    && record.exit.code === 0 && record.exit.signal === null, 'REOPEN_PRESEAL_LAUNCH_NOT_CLOSED');
  same(record.context, observation.context, 'REOPEN_PRESEAL_LAUNCH_CHANGED');
  const request = strictJSON(matchRef(observation.request_original.path,
    observation.request_original, 1024).data);
  exact(request, ['id', 'command', 'args'], 'REOPEN_PRESEAL_REQUEST_FIELDS');
  need(request.command === 'opened_store_identity_witness' && request.args === null,
    'REOPEN_PRESEAL_LITERAL_REQUEST');
  const raw = record.raw.stdout;
  need(raw?.eof === true && raw.path === observation.raw_stdout_path,
    'REOPEN_PRESEAL_RAW_EOF_REQUIRED');
  const rawRef = { path: raw.path, sha256: raw.sha256, size_bytes: raw.size_bytes };
  const bytes = matchRef(raw.path, rawRef, factory.options.maxStreamBytes ?? MiB).data;
  need(bytes.length > 0 && bytes.at(-1) === 10, 'REOPEN_PRESEAL_RAW_LINE_INCOMPLETE');
  let offset = 0, correlated = null;
  while (offset < bytes.length) {
    const end = bytes.indexOf(10, offset), line = bytes.subarray(offset, end);
    const response = strictJSON(line, MiB);
    if (response.id === request.id) {
      need(correlated === null, 'REOPEN_PRESEAL_RAW_RESPONSE_REPEATED');
      same(response, observation.response, 'REOPEN_PRESEAL_RAW_RESPONSE_CHANGED');
      correlated = { stream: rawRef, offset, size_bytes: end + 1 - offset,
        sha256: createHash('sha256').update(bytes.subarray(offset, end + 1)).digest('hex') };
    }
    offset = end + 1;
  }
  need(correlated !== null, 'REOPEN_PRESEAL_RAW_RESPONSE_MISSING');
  const final = strictJSON(matchRef(factory.finalOriginal.path, factory.finalOriginal, 4 * MiB).data);
  same(final.source, factory.admission.source, 'REOPEN_PRESEAL_OWNER_SOURCE');
  inside(factory.root, final.service_closure.path);
  const closure = strictJSON(matchRef(final.service_closure.path, final.service_closure, 4 * MiB).data);
  const participant = closure.participants?.find(item => item.helper_id === observation.context.helper_id);
  const stream = participant?.process_streams[observation.context.launch_index];
  const exit = stream && participant.process_exits[stream.exit_index];
  need(closure.status === 'CLOSED' && closure.database_path === factory.options.databasePath
    && participant?.binary_path === observation.binary.path
    && participant.database_path === factory.options.databasePath
    && stream?.process_id === record.pid && stream.stdout_ended && stream.stderr_ended
    && stream.jsonl_reader_closed && exit?.process_id === record.pid
    && exit.code === 0 && exit.signal === null, 'REOPEN_PRESEAL_OWNER_CLOSURE_JOIN');
  requireSameOpenedProcessAndStore(observation.observation, observation.observation, observation.process);
  const digest = requireHeldStoreWitness(observation.held_store_digest,
    expectedLinuxHeldStoreDigest(factory.initializerPhysical));
  return { ref, digest, response_raw: correlated, service_closure: final.service_closure,
    process: observation.process };
}

/** Production entry point: no adapter/mock argument and no source fallback.
 * An actual factory must already have passed its unchanged producer guard.
 * A caller's CLOSED boolean, a copied database or an internal restart is not
 * sufficient: assertClosed verifies original owner exit/raw/cohort conjunction.
 */
export async function runIndependentSameStoreReopen(factory, options) {
  requireOriginalAdmission(factory);
  return runSameStoreReopenCore(factory, options, { spawn, groupAbsent });
}

/** Exported only for finite mechanism controls. Its adapter may be synthetic;
 * the receipt states that fact and cannot be used as production admission. */
export async function runSameStoreReopenCore(factory, {
  expectedOracleRef, originalPresealWitnessRef, relativeOutput = 'independent-reopen',
  maximumStreamBytes = MiB, timeoutMs = 20000,
}, adapters) {
  need(factory && typeof factory === 'object', 'REOPEN_FACTORY_REQUIRED');
  need(!attempted.has(factory), 'REOPEN_NO_REPLAY');
  attempted.add(factory); // Consume the phase before any effect or reservation.
  let child, writer, outputRoot, exitPromise, rawPromise, failure, observedExit = null;
  const processIdAtEntry = process.pid;
  const deadline = Math.min(factory.deadline, Date.now() + timeoutMs);
  try {
    // A sandbox PID mapped against an outer procfs can name an unrelated
    // process. Refuse that environment before quota, originals or any spawn.
    requireCoherentProcNamespace();
    factory.assertClosed();
    need(factory.sealed === true && factory.closed === true
      && factory.pool instanceof SharedPool, 'REOPEN_ORIGINAL_POOL_AND_SEAL_REQUIRED');
    need(integer(maximumStreamBytes, 1, MiB) && integer(timeoutMs, 1, 30000)
      && Number.isSafeInteger(deadline) && deadline > Date.now(), 'REOPEN_BOUNDS');
    need(typeof relativeOutput === 'string' && /^[a-z][a-z0-9-]{0,63}$/.test(relativeOutput),
      'REOPEN_OUTPUT_COMPONENT');
    outputRoot = inside(factory.root, path.join(factory.root, relativeOutput));
    need(!fs.existsSync(outputRoot), 'REOPEN_OUTPUT_ALREADY_EXISTS');
    inside(factory.root, expectedOracleRef.path);
    const oracleBytes = matchRef(expectedOracleRef.path, expectedOracleRef, 16 * MiB);
    const oracle = validateOracle(strictJSON(oracleBytes.data, 16 * MiB));
    const preseal = requirePresealOriginal(factory, originalPresealWitnessRef,
      oracle, adapters.synthetic === true);
    inside(factory.root, factory.finalOriginal.path);
    matchRef(factory.finalOriginal.path, factory.finalOriginal, 4 * MiB);
    const requestLimit = Math.min(256, oracle.journal.length + 6);
    need(Math.ceil(oracle.journal.length / 100) + 5 <= requestLimit, 'REOPEN_REQUEST_PLAN_BOUND');
    const observedBytes = Buffer.from(JSON.stringify({ project: oracle.project,
      journal: oracle.journal, journalComplete: true, journalAfterRevisionExclusive: 0 }, null, 2) + '\n');
    need(observedBytes.length <= 16 * MiB, 'REOPEN_OBSERVED_ORACLE_BOUND');
    const database = factory.options.databasePath;
    const before = physical(database);
    same(before, factory.initializerPhysical, 'REOPEN_NOT_ORIGINAL_PHYSICAL_STORE');
    const expectedHeldDigest = expectedLinuxHeldStoreDigest(before);
    const executable = factory.admission.ordinaryService;
    matchRef(executable.path, executable, GLOBAL_BYTES);
    need(fs.accessSync(executable.path, fs.constants.X_OK) === undefined, 'REOPEN_BINARY_NOT_EXECUTABLE');
    // This is a slice of the SAME already-consumed parent allocator. There is
    // no new SharedPool, refund, per-worker allowance or per-test reset here.
    const allocation = factory.pool.reserve('independent-same-store-reopen',
      'post-seal-originals', maximumStreamBytes * 2 + observedBytes.length
        + 2 * MiB + requestLimit * 1024 + 512 * 1024, requestLimit + 12);
    const counter = factory.pool.counter(allocation);
    freshDirectory(outputRoot);
    writer = new OriginalWriter(outputRoot, counter);
    const argv = [executable.path, '--database', database];
    // No fixture, reopen command, import, mutation, grant, native connection,
    // changed project generation, synthetic seed or extra argument is accepted.
    const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
      key !== 'MOTIONWRIGHT_GRAPH_CONNECTION' && !key.startsWith('MOTIONWRIGHT_GRAPH_')));
    writer.json(path.join(outputRoot, 'intent.original.json'), {
      schema: 'motionwright.graph.same-original-store-reopen/1',
      source: factory.admission.source, binary: executable, argv,
      environment_removed: ['MOTIONWRIGHT_GRAPH_CONNECTION', 'MOTIONWRIGHT_GRAPH_*'],
      source_oracle: expectedOracleRef, original_owner_final: factory.finalOriginal,
      physical_store_before: before, parent_pid: processIdAtEntry,
      allocation, original_seal_preserved: true, automatic_retry: false,
      allowed_commands: [...allowed], deadline_unix_ms: deadline,
      opened_descriptor_probe: 'A3 R29 unchanged Linux procfs module',
      held_store_witness_command: 'A2 R2 opened_store_identity_witness args:null',
      original_preseal_held_pager_witness: preseal.ref,
      original_preseal_response_raw: preseal.response_raw,
      original_service_closure: preseal.service_closure,
      original_preseal_held_pager_witness_bound: true,
    });
    factory.assertClosed(); // Recheck immediately before the only process spawn.
    matchRef(factory.finalOriginal.path, factory.finalOriginal, 4 * MiB);
    same(physical(database), before, 'REOPEN_STORE_CHANGED_BEFORE_SPAWN');
    matchRef(executable.path, executable, GLOBAL_BYTES);
    child = adapters.spawn(executable.path, argv.slice(1), {
      cwd: path.dirname(executable.path), env: environment,
      stdio: ['pipe', 'pipe', 'pipe'], detached: true,
    });
    exitPromise = new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('exit', (code, signal) => {
        observedExit = { pid: child.pid, code, signal, observed_at: new Date().toISOString() };
        resolve(observedExit);
      });
    });
    exitPromise.catch(() => undefined);
    need(integer(child.pid, 1) && child.pid !== processIdAtEntry
      && child.pid !== factory.ready?.owner_pid, 'REOPEN_NOT_NEW_PROCESS');
    let protocolFailure = null, pending = null, serial = 0, buffer = Buffer.alloc(0), inputBytes = 0;
    const requests = [];
    const rejectProtocol = error => {
      protocolFailure ??= error; pending?.reject(protocolFailure); pending = null;
    };
    const capture = bindRawPair({ root: outputRoot, prefix: 'service', counter,
      streams: { stdout: child.stdout, stderr: child.stderr },
      streamLimit: maximumStreamBytes, deadlineUnixMs: deadline, onFailure: rejectProtocol });
    rawPromise = capture.settled; rawPromise.catch(rejectProtocol);
    // The raw listeners are installed first. Decode only complete bounded lines;
    // originals preserve every byte, including malformed or non-UTF8 output.
    child.stdout.on('data', chunk => {
      if (protocolFailure) return;
      try {
        need(Buffer.isBuffer(chunk), 'REOPEN_RAW_ALREADY_DECODED');
        inputBytes += chunk.length;
        need(inputBytes <= maximumStreamBytes, 'REOPEN_RESPONSE_BOUND');
        buffer = Buffer.concat([buffer, chunk]);
        while (buffer.includes(10)) {
          const end = buffer.indexOf(10), line = buffer.subarray(0, end);
          buffer = buffer.subarray(end + 1);
          const response = strictJSON(line, maximumStreamBytes);
          exact(response, response.ok === true ? ['id', 'ok', 'result'] : ['id', 'ok', 'error'],
            'REOPEN_RESPONSE_FIELDS');
          need(pending && response.id === pending.id && response.ok === true, 'REOPEN_RESPONSE_CORRELATION');
          const current = pending; pending = null; current.resolve(response.result);
        }
      } catch (error) { rejectProtocol(error); }
    });
    child.stdout.once('end', () => {
      if (buffer.length || pending) rejectProtocol(new Error('REOPEN_EOF_WITH_PENDING_RESPONSE'));
    });
    child.stdin.on('error', rejectProtocol);
    const spawnedProcessIdentity = captureLinuxProcessIdentity(child.pid);
    const request = async (command, args) => {
      need(!protocolFailure && pending === null && !observedExit && allowed.has(command),
        'REOPEN_REQUEST_STATE');
      const id = 'same-store-read-' + serial++;
      const value = { id, command, args };
      const raw = Buffer.from(JSON.stringify(value) + '\n');
      need(requests.length < requestLimit && raw.length <= 1024, 'REOPEN_REQUEST_BOUND');
      // Retain and precharge each literal request BEFORE it can reach Service.
      // Failure cannot leave an unrecorded request or a posthoc budget claim.
      requests.push(writer.publish(path.join(outputRoot, 'requests', id + '.original.jsonl'), raw));
      const result = new Promise((resolve, reject) => { pending = { id, resolve, reject }; });
      child.stdin.write(raw, error => { if (error) rejectProtocol(error); });
      return timeout(result, deadline, 'REOPEN_REQUEST_DEADLINE');
    };
    const project = await request('get_project', { request: { project_id: oracle.project.id } });
    same(project, oracle.project, 'REOPEN_FULL_PROJECT_MISMATCH');
    const journal = []; let revision = 0;
    for (;;) {
      const page = await request('project_history', { request: {
        project_id: oracle.project.id, after_revision: revision, limit: 100,
      } });
      need(Array.isArray(page) && page.length <= 100, 'REOPEN_JOURNAL_PAGE');
      if (page.length === 0) break;
      for (const event of page) {
        need(event && event.revision === revision + 1 && event.revision <= oracle.project.revision,
          'REOPEN_JOURNAL_SEQUENCE');
        journal.push(event); revision = event.revision;
      }
    }
    same(journal, oracle.journal, 'REOPEN_COMPLETE_JOURNAL_MISMATCH');
    const firstWitness = requireHeldStoreWitness(
      await request('opened_store_identity_witness', null), expectedHeldDigest);
    need(firstWitness === preseal.digest, 'REOPEN_FIRST_WITNESS_DIFFERS_FROM_ORIGINAL');
    const firstOpened = observeOpenedSQLiteDescriptor({pid:child.pid, expected:before});
    requireSameOpenedProcessAndStore(firstOpened, firstOpened, spawnedProcessIdentity);
    const firstOpenedBytes = Buffer.from(JSON.stringify({
      schema:'motionwright.graph.reopened-store-identity-observation/1',
      phase:'after-first-project-and-complete-journal', observation:firstOpened,
      held_store_digest:firstWitness, request_original:requests.at(-1),
      source:factory.admission.source, binary:executable,
      synthetic_adapter_only:adapters.synthetic === true, gate_credit:false,
    },null,2)+'\n');
    need(firstOpenedBytes.length <= 256 * 1024, 'REOPEN_DESCRIPTOR_ORIGINAL_BOUND');
    const firstOpenedRef = writer.publish(path.join(outputRoot,
      'opened-store-after-history.original.json'), firstOpenedBytes);
    same(await request('get_project', { request: { project_id: oracle.project.id } }), project,
      'REOPEN_PROJECT_CHANGED_DURING_READ');
    const secondWitness = requireHeldStoreWitness(
      await request('opened_store_identity_witness', null), expectedHeldDigest);
    need(secondWitness === preseal.digest, 'REOPEN_SECOND_WITNESS_DIFFERS_FROM_ORIGINAL');
    need(secondWitness === firstWitness, 'REOPEN_HELD_STORE_WITNESS_CHANGED');
    const secondOpened = observeOpenedSQLiteDescriptor({pid:child.pid, expected:before});
    requireSameOpenedProcessAndStore(firstOpened, secondOpened, spawnedProcessIdentity);
    const secondOpenedBytes = Buffer.from(JSON.stringify({
      schema:'motionwright.graph.reopened-store-identity-observation/1',
      phase:'after-second-project-before-eof', observation:secondOpened,
      held_store_digest:secondWitness, request_original:requests.at(-1),
      source:factory.admission.source, binary:executable,
      synthetic_adapter_only:adapters.synthetic === true, gate_credit:false,
    },null,2)+'\n');
    need(secondOpenedBytes.length <= 256 * 1024, 'REOPEN_DESCRIPTOR_ORIGINAL_BOUND');
    const secondOpenedRef = writer.publish(path.join(outputRoot,
      'opened-store-before-eof.original.json'), secondOpenedBytes);
    child.stdin.end();
    const outcomes = await timeout(Promise.allSettled([exitPromise, rawPromise]), deadline,
      'REOPEN_EXIT_EOF_DEADLINE');
    need(outcomes.every(item => item.status === 'fulfilled') && !protocolFailure,
      'REOPEN_CUSTODY_OR_PROTOCOL_FAILURE');
    const exit = outcomes[0].value, raw = outcomes[1].value;
    need(exit.code === 0 && exit.signal === null && exit.pid === child.pid,
      'REOPEN_EXIT_NOT_CLEAN');
    need(adapters.groupAbsent(child.pid), 'REOPEN_OWNED_GROUP_NOT_ABSENT');
    factory.assertClosed();
    matchRef(factory.finalOriginal.path, factory.finalOriginal, 4 * MiB);
    const after = physical(database); same(after, before, 'REOPEN_PHYSICAL_STORE_CHANGED');
    matchRef(executable.path, executable, GLOBAL_BYTES);
    matchRef(expectedOracleRef.path, expectedOracleRef, 16 * MiB);
    requirePresealOriginal(factory, originalPresealWitnessRef, oracle, adapters.synthetic === true);
    const observedRef = writer.publish(path.join(outputRoot, 'observed-oracle.original.json'), observedBytes);
    const receipt = {
      schema: 'motionwright.graph.same-original-store-reopen/1', status: 'CLOSED',
      source: factory.admission.source, parent_pid: processIdAtEntry, service_process: exit,
      original_owner_final: factory.finalOriginal, expected_oracle: expectedOracleRef,
      physical_store_before: before, physical_store_after: after,
      exact_project_equal: true, exact_journal_equal: true, raw, requests,
      observed_oracle: observedRef, allocation, original_seal_preserved: true,
      owned_process_group_absent: true, escaped_descendant_cohort_proved: false,
      service_opened_descriptor_identity_proved: false,
      actual_child_opened_descriptor_observed: true,
      same_spawn_process_incarnation_observed: true,
      opened_descriptor_observations:[firstOpenedRef,secondOpenedRef],
      held_store_digest_matches_initializer_physical_identity:true,
      original_preseal_held_pager_witness:preseal.ref,
      original_preseal_response_raw:preseal.response_raw,
      original_service_closure:preseal.service_closure,
      original_preseal_held_pager_witness_bound:true,
      source_bound_service_pager_query_identity_proved:false,
      identity_scope:'Original preseal Service observation and both new-child witnesses joined through raw EOF and owner-linked Service closure; owner FINAL does not attest the preseal witness hash; product acceptance remains pending',
      native_recovery_oracle_executed: false, formal_acceptance: false, gate_credit: false,
      synthetic_adapter_only: adapters.synthetic === true,
    };
    const result = writer.json(path.join(outputRoot, 'terminal.original.json'), receipt);
    return { receipt: result, observation: receipt };
  } catch (error) {
    failure = error; factory?.taint?.(error);
    // A failed read is not replayed, and cleanup cannot convert UNKNOWN to PASS.
    let groupCleanup = { attempted: false, absent: null };
    if (integer(child?.pid, 1) && child.pid !== processIdAtEntry) {
      groupCleanup.attempted = true;
      // The detached group may outlive its leader. Exit of the leader alone
      // never exempts that owned group from bounded cleanup on failure.
      try { process.kill(-child.pid, 'SIGKILL'); }
      catch (stop) { if (stop.code !== 'ESRCH') failure = new AggregateError([error, stop]); }
    }
    const cleanup = await Promise.allSettled([exitPromise, rawPromise].filter(Boolean)
      .map(value => timeout(value, Date.now() + 2000, 'REOPEN_CLEANUP_DEADLINE')));
    if (groupCleanup.attempted) {
      try { groupCleanup.absent = adapters.groupAbsent(child.pid); }
      catch (stop) { groupCleanup.error = String(stop); }
    }
    if (writer) {
      try { writer.json(path.join(outputRoot, 'failure.original.json'), {
        status: 'UNKNOWN', error: String(failure), actual_pid: child?.pid ?? null,
        service_process: observedExit,
        cleanup: cleanup.map(item => item.status), automatic_retry: false,
        owned_group_cleanup: groupCleanup,
        gate_credit: false, original_seal_preserved: factory?.sealed === true,
      }); } catch (custody) { failure = new AggregateError([failure, custody], 'REOPEN_FAILURE_CUSTODY'); }
    }
    throw failure;
  }
}
