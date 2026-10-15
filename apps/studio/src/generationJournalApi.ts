/** Read-only desktop journal queries. A declaration never grants payment authority. */
import { invoke } from "@tauri-apps/api/core";
import type { Project } from "./types";
import type {
  PaidGenerationSpec, PaidGenerationDraftPreview, PaidGenerationReservationPreview,
  PaidGenerationJournalObservation,
} from "./paidGenerationTypes";

const DIGEST = /^[0-9a-f]{64}$/;
const desktop = () => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
const scope = (project: Project) => ({ project_id: project.id, generation: project.generation, revision: project.revision });
function fail(): never { throw new Error("Generation journal response is stale, malformed or claims ungranted execution."); }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail();
  return value as Record<string, unknown>;
}
function exactKeys(value: Record<string, unknown>, keys: string[]) {
  if (Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) fail();
}
function money(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > 1_000_000_000_000) fail();
  return value as number;
}
function digest(value: unknown) { if (typeof value !== "string" || !DIGEST.test(value)) fail(); }
function stable(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(stable).join(",") + "]";
  if (value !== null && typeof value === "object") {
    const object = value as Record<string, unknown>;
    return "{" + Object.keys(object).sort().map(key => JSON.stringify(key) + ":" + stable(object[key])).join(",") + "}";
  }
  const encoded = JSON.stringify(value);
  if (typeof encoded !== "string") fail();
  return encoded;
}
function budget(value: unknown) {
  const b = record(value);
  exactKeys(b, ["schema", "project_reserved_microusd", "provider_reported_cost_microusd", "unresolved_reserved_microusd", "currency", "cost_reports_are_provider_verified", "actual_payment_settled", "may_retry_paid_submission"]);
  if (b.schema !== "motionwright.paid-generation-budget-preview/1" || b.currency !== "USD_micro" ||
      b.cost_reports_are_provider_verified !== false || b.actual_payment_settled !== false || b.may_retry_paid_submission !== false) fail();
  const reserved = money(b.project_reserved_microusd);
  if (money(b.provider_reported_cost_microusd) > reserved || money(b.unresolved_reserved_microusd) > reserved) fail();
}
function envelope(project: Project, value: unknown, schema: string, fields: string[]) {
  const result = record(value);
  exactKeys(result, ["schema", "project_id", "generation", "revision", ...fields]);
  if (result.schema !== schema || result.project_id !== project.id || result.generation !== project.generation || result.revision !== project.revision) fail();
  budget(result.budget);
  return result;
}
export function validateDraftReply(project: Project, spec: PaidGenerationSpec, value: unknown): PaidGenerationDraftPreview {
  const reply = envelope(project, value, "motionwright.paid-generation-draft-preview/1", ["normalized_edit", "specification", "budget", "provider_capability_signature_verified", "owner_execution_grant", "provider_called", "payment_charged", "committed"]);
  for (const flag of ["provider_capability_signature_verified", "owner_execution_grant", "provider_called", "payment_charged", "committed"]) if (reply[flag] !== false) fail();
  const normalized = record(reply.specification);
  digest(normalized.logical_idempotency_sha256);
  if (stable({ ...normalized, logical_idempotency_sha256: "" }) !== stable({ ...spec, logical_idempotency_sha256: "" })) fail();
  if (stable(reply.normalized_edit) !== stable({ kind: "create", spec: normalized })) fail();
  return reply as unknown as PaidGenerationDraftPreview;
}
export function validateReservationReply(project: Project, jobId: string, value: unknown): PaidGenerationReservationPreview {
  const reply = envelope(project, value, "motionwright.paid-generation-budget-preflight/1", ["normalized_edit", "budget", "committed", "provider_called", "payment_charged", "owner_execution_grant"]);
  for (const flag of ["committed", "provider_called", "payment_charged", "owner_execution_grant"]) if (reply[flag] !== false) fail();
  const edit = record(reply.normalized_edit);
  exactKeys(edit, ["kind", "job_id", "expected_history_sha256", "event"]);
  if (edit.kind !== "append" || edit.job_id !== jobId) fail();
  digest(edit.expected_history_sha256);
  const job = project.paid_generation?.jobs.find(row => row.spec.id === jobId);
  if (!job || job.state !== "draft") fail();
  const event = record(edit.event);
  exactKeys(event, ["id", "kind"]);
  if (typeof event.id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(event.id)) fail();
  if (stable(event.kind) !== stable({ kind: "reserve", maximum_charge_microusd: job!.spec.max_charge_microusd })) fail();
  return reply as unknown as PaidGenerationReservationPreview;
}
export function validateJournalReply(project: Project, value: unknown): PaidGenerationJournalObservation {
  const reply = envelope(project, value, "motionwright.paid-generation-ledger-observation/1", ["rows", "budget", "read_only", "remote_api_called", "execution_authority"]);
  if (reply.read_only !== true || reply.remote_api_called !== false || reply.execution_authority !== "NONE_GRANTED" || !Array.isArray(reply.rows) || reply.rows.length > 256) fail();
  const rows = reply.rows as unknown[];
  const seen = new Set<string>();
  if (rows.length !== (project.paid_generation?.jobs.length ?? 0)) fail();
  for (const raw of rows) {
    const row = record(raw);
    exactKeys(row, ["job_id", "kind", "scope", "state", "history_sha256", "provider_id", "model_id", "model_version", "variant_index", "max_charge_microusd", "source_sha256", "known_task_id", "reconciliation_query", "reported_spend_microusd", "owner_approval_is_authenticated", "provider_success_is_authenticated"]);
    if (typeof row.job_id !== "string" || seen.has(row.job_id)) fail();
    seen.add(row.job_id as string);
    const job = project.paid_generation?.jobs.find(candidate => candidate.spec.id === row.job_id);
    if (!job || row.kind !== job.spec.kind || row.state !== job.state || row.provider_id !== job.spec.provider_id ||
        row.model_id !== job.spec.model_id || row.model_version !== job.spec.model_version || row.variant_index !== job.spec.variant_index ||
        stable(row.scope) !== stable(job.spec.scope) || row.known_task_id !== job.task_id ||
        row.source_sha256 !== job.spec.logical_idempotency_sha256 || row.max_charge_microusd !== job.spec.max_charge_microusd ||
        row.reported_spend_microusd !== job.provider_reported_spend_microusd ||
        row.owner_approval_is_authenticated !== false || row.provider_success_is_authenticated !== false) fail();
    digest(row.history_sha256); digest(row.source_sha256); money(row.max_charge_microusd);
    if (row.reported_spend_microusd !== null) money(row.reported_spend_microusd);
    if (row.state === "unknown_outcome") {
      const query = record(row.reconciliation_query);
      exactKeys(query, ["schema", "job_id", "provider_id", "logical_idempotency_sha256", "known_task_id", "operation", "launches_new_task", "billing_verified", "provider_observation_verified"]);
      if (query.schema !== "motionwright.paid-generation-reconcile-query/1" || query.operation !== "query_existing_task_only" ||
          query.job_id !== row.job_id || query.provider_id !== row.provider_id || query.known_task_id !== row.known_task_id ||
          query.logical_idempotency_sha256 !== row.source_sha256 || query.launches_new_task !== false ||
          query.billing_verified !== false || query.provider_observation_verified !== false) fail();
    } else if (row.reconciliation_query !== null) fail();
  }
  return reply as unknown as PaidGenerationJournalObservation;
}

export async function nativePaidGenerationPreflight(project: Project, specification: PaidGenerationSpec): Promise<PaidGenerationDraftPreview> {
  if (!desktop()) throw new Error("Paid provider intents require local StudioService; browser demos cannot reserve or charge.");
  const result: unknown = await invoke("native_paid_generation_preflight", { request: { ...scope(project), specification } });
  return validateDraftReply(project, specification, result);
}
export async function nativePaidGenerationReservePreflight(project: Project, jobId: string): Promise<PaidGenerationReservationPreview> {
  if (!desktop()) throw new Error("A reservation requires the canonical desktop project revision.");
  const result: unknown = await invoke("native_paid_generation_reserve_preflight", { request: { ...scope(project), job_id: jobId } });
  return validateReservationReply(project, jobId, result);
}
export async function nativePaidGenerationJournal(project: Project): Promise<PaidGenerationJournalObservation> {
  if (!desktop()) throw new Error("Paid task status is not simulated in a browser demo.");
  const result: unknown = await invoke("native_paid_generation_journal", { request: scope(project) });
  return validateJournalReply(project, result);
}
