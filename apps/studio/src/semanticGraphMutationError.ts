/** Closed errors from the original Graph mutation IPC; never an authority or ACK. */
export interface GraphMutationError {
  schema: "motionwright.graph-mutation-error/1";
  request_id: string;
  outcome_known: boolean;
  code: "graph_mutation_failed";
}

export const GRAPH_MUTATION_UNKNOWN_MESSAGE = "The Graph change outcome is unconfirmed. Keep the original request and inspect its outcome before another intention.";
export const GRAPH_MUTATION_REJECTED_MESSAGE = "The Graph change was rejected and was not applied. Your input is retained; inspect the saved source before a new intention.";

export interface GraphMutationFailure {
  outcome: "UNKNOWN" | "REJECTED";
  diagnostic: typeof GRAPH_MUTATION_UNKNOWN_MESSAGE | typeof GRAPH_MUTATION_REJECTED_MESSAGE;
}

const fields = ["schema", "request_id", "outcome_known", "code"] as const;
const requestKey = (value: unknown): value is string => typeof value === "string" && value.length === 36
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
  && value !== "00000000-0000-0000-0000-000000000000";

/** Call only for the actual original IPC rejection, never recovered author data.
 * JSON strings, Error.message and arbitrary diagnostics are deliberately not parsed.
 * A valid shape alone cannot establish provenance or native operation authority. */
export function decodeGraphMutationError(value: unknown, originalRequestId: string): GraphMutationError | null {
  try {
    if (!requestKey(originalRequestId) || typeof value !== "object" || value === null || Array.isArray(value)) return null;
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) return null;
    const keys = Reflect.ownKeys(value);
    if (keys.length !== fields.length || fields.some(field => !Object.hasOwn(value, field))) return null;
    const data = Object.getOwnPropertyDescriptors(value);
    if (fields.some(field => !("value" in data[field]) || !data[field].enumerable)) return null;
    const schema: unknown = data.schema.value, id: unknown = data.request_id.value;
    const known: unknown = data.outcome_known.value, code: unknown = data.code.value;
    if (schema !== "motionwright.graph-mutation-error/1" || code !== "graph_mutation_failed"
        || !requestKey(id) || id !== originalRequestId || typeof known !== "boolean") return null;
    return { schema, request_id: id, outcome_known: known, code };
  } catch {
    // Even a malformed object with throwing accessors/proxies yields no knownness.
    return null;
  }
}

export function unknownGraphMutationFailure(): GraphMutationFailure {
  return { outcome: "UNKNOWN", diagnostic: GRAPH_MUTATION_UNKNOWN_MESSAGE };
}

export function graphMutationFailure(reason: unknown, originalRequestId: string): GraphMutationFailure {
  return decodeGraphMutationError(reason, originalRequestId)?.outcome_known === true
    ? { outcome: "REJECTED", diagnostic: GRAPH_MUTATION_REJECTED_MESSAGE }
    : unknownGraphMutationFailure();
}

export type GraphMutationSettlement = { ok: true }
  | { ok: false; phase: "commit" | "reply"; failure: GraphMutationFailure };

/** Keep the original Promise rejection separate from ACK validation and custody.
 * A later callback exception cannot become a native known rejection, even if it
 * resembles the wire error. Neither branch retries, changes a key, or grants ACK. */
export async function settleGraphMutation<T>(original: Promise<T>, originalRequestId: string,
  receiveReply: (receipt: T) => void | Promise<void>): Promise<GraphMutationSettlement> {
  let receipt: T;
  try { receipt = await original; }
  catch (reason) { return { ok: false, phase: "commit", failure: graphMutationFailure(reason, originalRequestId) }; }
  try { await receiveReply(receipt); return { ok: true }; }
  catch { return { ok: false, phase: "reply", failure: unknownGraphMutationFailure() }; }
}
