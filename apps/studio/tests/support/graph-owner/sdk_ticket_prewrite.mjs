/** Private J2 consumer proposal, protocol /0: NOT a Native authority DTO.
 * Only the original-producer admission may supply this plan. The unchanged
 * production guard denies while authentic A3 declaration / A4 enforcement
 * originals are missing. A file hash alone does not authenticate either one.
 */
import path from 'node:path';
import { SharedPool, stableRead, need, integer, GLOBAL_BYTES, GLOBAL_FILES } from './custody.mjs';
import { exact, same, schema, SPEC_SCHEMA, tuple } from './contracts.mjs';

export const CALLBACK_CAP_ENV = 'MOTIONWRIGHT_GRAPH_CALLBACK_CAP';
export const TICKET_PLAN_SCHEMA = 'motionwright.graph-sdk-ticket-prewrite-plan/0';
const ORIGINAL_LIMIT = 1024 * 1024;

function freeze(value) {
  if (value && typeof value === 'object') {
    for (const entry of Object.values(value)) freeze(entry);
    Object.freeze(value);
  }
  return value;
}
function verifyOriginal(ref) {
  exact(ref, ['path', 'sha256', 'size_bytes'], 'J2_ORIGINAL_REFERENCE_FIELDS');
  need(typeof ref.sha256 === 'string' && /^[0-9a-f]{64}$/.test(ref.sha256)
    && integer(ref.size_bytes, 1, ORIGINAL_LIMIT), 'J2_ORIGINAL_REFERENCE_BOUND');
  same(stableRead(ref.path, ORIGINAL_LIMIT).ref, ref, 'J2_ORIGINAL_CHANGED');
}

/** Validates/snapshots data from admitSource, never options or environment.
 * The source verifier, NOT this shape/hash checker, must prove causal origin
 * and that the exact built Native enforces C_i before every callback effect.
 */
export function snapshotSdkTicketPlan(admission) {
  const input = admission?.sdk_ticket_prewrite;
  exact(input, ['schema', 'source', 'native_service', 'originals', 'clients'], 'J2_AUTHENTIC_PLAN_REQUIRED');
  need(input.schema === TICKET_PLAN_SCHEMA, 'J2_PLAN_VERSION');
  schema(input.source, SPEC_SCHEMA.properties.source);
  schema(input.native_service, SPEC_SCHEMA.properties.binaries.properties.semwright);
  same(input.source, admission.source, 'J2_SOURCE_NOT_ADMITTED');
  same(input.native_service, admission.nativeService, 'J2_NATIVE_NOT_ADMITTED');
  exact(input.originals, ['callback_roster', 'native_enforcement'], 'J2_PRODUCER_ORIGINALS_REQUIRED');
  for (const ref of Object.values(input.originals)) verifyOriginal(ref);
  need(Array.isArray(input.clients) && input.clients.length >= 1 && input.clients.length <= 64,
    'J2_DECLARED_CLIENT_ROSTER_REQUIRED');
  let calls = 1n;
  for (const [index, client] of input.clients.entries()) {
    exact(client, ['ordinal', 'callback_cap'], 'J2_CLIENT_PLAN_FIELDS');
    need(client.ordinal === index + 1 && integer(client.callback_cap), 'J2_CLIENT_CAP_OR_ORDER');
    calls += 1n + 8n * BigInt(client.callback_cap);
  }
  // BigInt prevents rounded arithmetic from silently authorizing an overflow.
  need(calls * 3n <= BigInt(GLOBAL_FILES) && calls * 256n <= BigInt(GLOBAL_BYTES), 'J2_GLOBAL_TICKET_BOUND');
  need(Buffer.byteLength(JSON.stringify(input), 'utf8') <= 65536, 'J2_PLAN_BYTE_BOUND');
  return freeze({ plan: structuredClone(input), maximum_cli_calls: Number(calls),
    file_slots: Number(calls * 3n), bytes: Number(calls * 256n) });
}

/** One irrevocable slice of the factory's existing allocator. No second pool,
 * refunds, regenerated C_i, ticket reads, or posthoc admission is provided.
 * The callbacks themselves execute in Native; their cumulative runtime
 * enforcement must be supplied/proved by A4 before the guard can admit this.
 */
export class SdkTicketPrewrite {
  #pool; #snapshot; #allocation; #restriction; #bindings = []; #byId = new Map();
  #bootstrapAttempted = false; #attemptCount = 0; #confirmedCount = 0; #failed = false;
  constructor(pool, snapshot) {
    need(pool instanceof SharedPool && pool.maximumBytes === GLOBAL_BYTES && pool.maximumFiles === GLOBAL_FILES,
      'J2_EXISTING_GLOBAL_POOL_REQUIRED');
    this.#pool = pool; this.#snapshot = snapshot;
    this.#allocation = pool.reserve('sdk-ticket-prewrite', 'sdk-ticket-prewrite', snapshot.bytes, snapshot.file_slots);
    // External writers get prepaid capacity. Do not also issue a counter or
    // charge their bytes through OriginalWriter: that would double-use a slice.
    this.#restriction = pool.external(this.#allocation);
  }
  fail() { this.#failed = true; }
  assertSource(admission) {
    try {
      same(admission?.source, this.#snapshot.plan.source, 'J2_ADMITTED_SOURCE_CHANGED');
      same(admission?.nativeService, this.#snapshot.plan.native_service, 'J2_ADMITTED_NATIVE_CHANGED');
    } catch (error) { this.fail(); throw error; }
  }
  assertReady(pool) {
    try {
      need(!this.#failed && pool === this.#pool, 'J2_ADMISSION_TAINTED_OR_POOL_REPLACED');
      for (const ref of Object.values(this.#snapshot.plan.originals)) verifyOriginal(ref);
    } catch (error) { this.fail(); throw error; }
  }
  bindLaunch(context) {
    try {
      this.assertReady(this.#pool);
      const id = tuple(context), ordinal = this.#bindings.length + 1;
      need(!this.#byId.has(id) && ordinal <= this.#snapshot.plan.clients.length, 'J2_UNDECLARED_OR_REUSED_CLIENT');
      const planned = this.#snapshot.plan.clients[ordinal - 1];
      const value = freeze({ ordinal, id, context: structuredClone(context), callback_cap: planned.callback_cap,
        maximum_cli_calls: 1 + 8 * planned.callback_cap,
        file_slots: 3 * (1 + 8 * planned.callback_cap), bytes: 256 * (1 + 8 * planned.callback_cap) });
      const state = { value, attempted: false, requestIssued: false, confirmed: false };
      this.#bindings.push(state); this.#byId.set(id, state); return value;
    } catch (error) { this.fail(); throw error; }
  }
  claimClient(context) {
    try {
      this.assertReady(this.#pool); const state = this.#byId.get(tuple(context));
      need(state && !state.attempted && state.value.ordinal === this.#attemptCount + 1,
        'J2_CLIENT_ATTEMPT_REUSED_OR_OUT_OF_ORDER');
      same(state.value.context, context, 'J2_CLIENT_TUPLE_CHANGED');
      // Irrevocably consume before any await, intent write, owner or CLI effect.
      state.attempted = true; this.#attemptCount++; return state.value;
    } catch (error) { this.fail(); throw error; }
  }
  claimBootstrap() {
    try {
      this.assertReady(this.#pool); need(!this.#bootstrapAttempted, 'J2_BOOTSTRAP_REPLAY');
      this.#bootstrapAttempted = true;
      return Object.freeze({ maximum_cli_calls: 1, file_slots: 3, bytes: 256 });
    } catch (error) { this.fail(); throw error; }
  }
  claimClientRequest(id) {
    try {
      this.assertReady(this.#pool); const state = this.#byId.get(id);
      need(this.#bootstrapAttempted && state?.attempted && !state.requestIssued && !state.confirmed
        && state.value.ordinal === this.#confirmedCount + 1, 'J2_UNCHARGED_OR_REPLAYED_CLIENT_REQUEST');
      state.requestIssued = true;
    } catch (error) { this.fail(); throw error; }
  }
  confirmClient(context, clientId, sessionFile, ownerRoot) {
    try {
      this.assertReady(this.#pool); const state = this.#byId.get(tuple(context));
      need(state && state.attempted && state.requestIssued && !state.confirmed && state.value.ordinal === this.#confirmedCount + 1,
        'J2_CLIENT_CONFIRMATION_ORDER');
      same(state.value.context, context, 'J2_CLIENT_TUPLE_CHANGED');
      const expectedId = 'client-' + String(state.value.ordinal).padStart(4, '0');
      need(clientId === expectedId && sessionFile === path.join(ownerRoot, 'clients', expectedId + '.session'),
        'J2_FIXED_CLIENT_SESSION_REQUIRED');
      state.confirmed = true; this.#confirmedCount++;
      return Object.freeze({ [CALLBACK_CAP_ENV]: String(state.value.callback_cap) });
    } catch (error) { this.fail(); throw error; }
  }
  requireComplete(contexts) {
    try {
    this.assertReady(this.#pool);
    need(this.#bootstrapAttempted && contexts.length === this.#snapshot.plan.clients.length
      && this.#bindings.length === contexts.length && this.#attemptCount === contexts.length
      && this.#confirmedCount === contexts.length, 'J2_INCOMPLETE_DECLARED_ROSTER');
    for (const [index, context] of contexts.entries()) same(this.#bindings[index].value.context, context, 'J2_FINAL_ROSTER_CHANGED');
    } catch (error) { this.fail(); throw error; }
  }
  observation() {
    return { schema: 'motionwright.graph-sdk-ticket-prewrite-observation/0',
      plan: this.#snapshot.plan, allocation: this.#allocation, restriction: this.#restriction,
      maximum_cli_calls: this.#snapshot.maximum_cli_calls,
      bootstrap_attempted: this.#bootstrapAttempted, attempted_clients: this.#attemptCount,
      confirmed_clients: this.#confirmedCount, failed: this.#failed,
      bindings: this.#bindings.map(state => ({ ...state.value, attempted: state.attempted,
        request_issued: state.requestIssued, confirmed: state.confirmed })),
      native_runtime_enforcement_proved_by_this_consumer: false,
      authentic_original_verifier_required: true, automatic_refund: false, gate_credit: false };
  }
}
