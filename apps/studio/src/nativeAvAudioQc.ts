import type {
  DeliveryAudioQcReport, DeliveryAudioQcStatus, NativeAvAudioQcResponse, NativeAudioQcReceiptInventoryResponse,
  NativeAvAudioQcUnavailableReason, MltAvMasterEvidence, NativeMultisegmentAvMasterEvidence, Project,
} from "./types";
import { assertDeliveryAudioTargets } from "./audioRecipeContract";

const statuses: readonly DeliveryAudioQcStatus[] = [
  "not_requested", "within_targets", "outside_targets", "unavailable",
];
const reasons: readonly NativeAvAudioQcUnavailableReason[] = [
  "session_unavailable", "source_changed", "connection_unavailable", "artifact_changed",
  "measurement_unavailable", "unresolved_receipt", "unsupported_platform",
];
const i32Min = -2_147_483_648, i32Max = 2_147_483_647;
const sha256 = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const integer = (value: unknown, min: number, max: number): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= min && value <= max;
const nullableMilli = (value: unknown, min = i32Min, max = i32Max) => value === null || integer(value, min, max);

function requireQc(condition: unknown): asserts condition {
  // Do not copy a native diagnostic or malformed public value into an error.
  if (!condition) throw new Error("The native audio QC response is unavailable or invalid.");
}

function record(value: unknown, keys: readonly string[]): Record<string, unknown> {
  requireQc(value !== null && typeof value === "object" && !Array.isArray(value));
  const result = value as Record<string, unknown>;
  const actual = Object.keys(result);
  requireQc(actual.length === keys.length && keys.every(key => Object.hasOwn(result, key)));
  return result;
}

function status(value: unknown) {
  return typeof value === "string" && statuses.includes(value as DeliveryAudioQcStatus);
}

function report(value: unknown, expectedMasterSha256: string | null): DeliveryAudioQcReport {
  const result = record(value, ["schema", "qc", "measurement_method", "decoded_input", "measurement_windows",
    "true_peak_oversample", "measured_levels", "units", "published_resolution", "master_artifact_sha256", "evidence_scope"]);
  requireQc(result.schema === "motionwright.delivery-audio-qc-report/2"
    && result.evidence_scope === "measured-final-pcm-numeric-report-not-normalization-not-intelligibility-not-human-acceptance"
    && sha256(result.master_artifact_sha256));
  if (expectedMasterSha256 !== null) {
    requireQc(sha256(expectedMasterSha256) && result.master_artifact_sha256 === expectedMasterSha256);
  }
  const qc = record(result.qc, ["schema", "status", "targets", "target_lufs_milli", "target_true_peak_millidbtp",
    "measured_integrated_lufs_milli", "measured_true_peak_millidbtp", "lufs_delta_milli", "lufs_tolerance_milli",
    "lufs_status", "true_peak_status", "evaluated_phase", "decoded_wav_sha256", "decoded_sample_frames",
    "measurement_receipt_id", "measurement_proof_sha256", "evidence_scope"]);
  requireQc(qc.schema === "motionwright.delivery-audio-qc/1"
    && status(qc.status) && status(qc.lufs_status) && status(qc.true_peak_status)
    && qc.evaluated_phase === "decoded_mlt_master"
    && qc.evidence_scope === "final-decoded-pcm16-public-milli-numeric-qc-not-mastered-not-intelligibility-not-human-approved"
    && sha256(qc.decoded_wav_sha256) && sha256(qc.measurement_proof_sha256)
    && typeof qc.measurement_receipt_id === "string"
    && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(qc.measurement_receipt_id)
    && integer(qc.decoded_sample_frames, 1, Number.MAX_SAFE_INTEGER)
    && integer(qc.lufs_tolerance_milli, 0, i32Max)
    && nullableMilli(qc.lufs_delta_milli, -4_294_967_295, 4_294_967_295));
  for (const key of ["target_lufs_milli", "target_true_peak_millidbtp", "measured_integrated_lufs_milli", "measured_true_peak_millidbtp"]) {
    requireQc(nullableMilli(qc[key]));
  }
  if (qc.targets !== null) {
    // Reuse the authored-target wire contract (complete nullable pair, at
    // least one target and exact milli units). It neither mutates nor sets QC.
    try { assertDeliveryAudioTargets(qc.targets); } catch { requireQc(false); }
  }
  const method = record(result.measurement_method, ["engine", "version", "producer_schema_version"]);
  requireQc(typeof method.engine === "string" && /^[a-zA-Z0-9._+-]{1,64}$/.test(method.engine)
    && typeof method.version === "string" && /^[a-zA-Z0-9._+-]{1,64}$/.test(method.version)
    && integer(method.producer_schema_version, 1, 4_294_967_295));
  const decoded = record(result.decoded_input, ["format", "sha256", "sample_rate_hz", "channels", "channel_layout", "sample_interval"]);
  const interval = record(decoded.sample_interval, ["start_sample_frame", "end_sample_frame_exclusive"]);
  requireQc(decoded.format === "wav_pcm_s16le" && sha256(decoded.sha256)
    && decoded.sample_rate_hz === 48_000 && decoded.channels === 2 && decoded.channel_layout === "stereo"
    && interval.start_sample_frame === 0 && integer(interval.end_sample_frame_exclusive, 1, Number.MAX_SAFE_INTEGER)
    && decoded.sha256 === qc.decoded_wav_sha256 && interval.end_sample_frame_exclusive === qc.decoded_sample_frames);
  const windows = record(result.measurement_windows, ["integrated_loudness", "momentary_window_ms", "short_term_window_ms",
    "momentary_and_short_term_position", "loudness_range", "true_peak", "sample_peak"]);
  requireQc(windows.integrated_loudness === "complete_decoded_input_with_provider_gating"
    && windows.loudness_range === "complete_decoded_input_with_provider_gating"
    && windows.momentary_and_short_term_position === "ending_at_decoded_input_end"
    && windows.true_peak === "complete_decoded_input" && windows.sample_peak === "complete_decoded_input"
    && integer(windows.momentary_window_ms, 1, 4_294_967_295) && integer(windows.short_term_window_ms, 1, 4_294_967_295)
    && integer(result.true_peak_oversample, 1, 65_535));
  const levels = record(result.measured_levels, ["integrated_lufs_milli", "momentary_lufs_milli", "short_term_lufs_milli",
    "loudness_range_milli", "true_peak_millidbtp", "sample_peak_millidbfs"]);
  for (const key of ["integrated_lufs_milli", "momentary_lufs_milli", "short_term_lufs_milli", "true_peak_millidbtp", "sample_peak_millidbfs"]) {
    requireQc(nullableMilli(levels[key]));
  }
  requireQc(nullableMilli(levels.loudness_range_milli, 0, 4_294_967_295)
    && levels.integrated_lufs_milli === qc.measured_integrated_lufs_milli
    && levels.true_peak_millidbtp === qc.measured_true_peak_millidbtp);
  const units = {
    integrated_lufs_milli: "LUFS", momentary_lufs_milli: "LUFS", short_term_lufs_milli: "LUFS",
    loudness_range_milli: "LU", true_peak_millidbtp: "dBTP", sample_peak_millidbfs: "dBFS",
    target_lufs_milli: "LUFS", target_true_peak_millidbtp: "dBTP", lufs_delta_milli: "LU", lufs_tolerance_milli: "LU",
  };
  const receivedUnits = record(result.units, Object.keys(units));
  requireQc(Object.entries(units).every(([key, unit]) => receivedUnits[key] === unit));
  const resolution = record(result.published_resolution, ["numerator", "denominator", "rounding_authority"]);
  requireQc(resolution.numerator === 1 && resolution.denominator === 1000
    && resolution.rounding_authority === "identified_provider_integer_milli_output");
  // Preserve the provider's verdict, nulls, authored numbers and actual interval.
  // No rounding, target comparison, normalization or local PASS is performed.
  return structuredClone(value) as DeliveryAudioQcReport;
}

export function readNativeAvAudioQcResponse(value: unknown, expectedMasterSha256: string | null = null): NativeAvAudioQcResponse {
  requireQc(value !== null && typeof value === "object" && !Array.isArray(value));
  const kind = (value as Record<string, unknown>).kind;
  if (kind === "report") {
    const item = record(value, ["kind", "report"]);
    return { kind, report: report(item.report, expectedMasterSha256) };
  }
  if (kind === "not_requested") {
    record(value, ["kind"]);
    return { kind };
  }
  if (kind === "unavailable") {
    const item = record(value, ["kind", "reason"]);
    requireQc(typeof item.reason === "string" && reasons.includes(item.reason as NativeAvAudioQcUnavailableReason));
    return { kind, reason: item.reason as NativeAvAudioQcUnavailableReason };
  }
  throw new Error("The native audio QC response is unavailable or invalid.");
}

/** Format already-admitted integer milli units without rounding or deciding QC. */
export function formatAudioMilli(value: number | null): string {
  if (value === null) return "Unavailable (null)";
  requireQc(Number.isSafeInteger(value));
  const magnitude = Math.abs(value);
  return (value < 0 ? "-" : "") + Math.floor(magnitude / 1000) + "." + String(magnitude % 1000).padStart(3, "0");
}

export const audioQcStatusLabel: Record<DeliveryAudioQcStatus, string> = {
  not_requested: "Not requested", within_targets: "Within numeric targets",
  outside_targets: "Outside numeric targets", unavailable: "Unavailable",
};
export const audioQcUnavailableText: Record<NativeAvAudioQcUnavailableReason, string> = {
  session_unavailable: "The original master session is unavailable or expired. A new app session needs a valid original selector; this read does not create one.",
  source_changed: "The selected source no longer matches this master. No replacement was created.",
  connection_unavailable: "The original measurement connection is unavailable for readback.",
  artifact_changed: "Retained master or measurement bytes no longer match their recorded identity.",
  measurement_unavailable: "The original measurement could not be revalidated.",
  unresolved_receipt: "The retained receipt chain could not be resolved. This read did not retry the original request.",
  unsupported_platform: "Audio measurement readback is not supported on this platform.",
};

type ReviewSource = Pick<Project, "id" | "generation" | "active_branch" | "revision">;
export function nativeAvReadScope(source: ReviewSource, exportToken: string,
  profileId: string | null = null, masterSha256: string | null = null): string {
  // The opaque token resolves request/lane privately in Desktop; neither is
  // serialized here. Optional public profile/master identity narrows the view.
  return JSON.stringify([source.id, source.generation, source.active_branch, source.revision, exportToken, profileId, masterSha256]);
}

/** A cancelled/superseded read cannot publish, including an A -> B -> A change. */
export function createNativeAvReadGuard() {
  let current: { scope: string } | null = null;
  let observed: string | null = null, sequence = 0;
  return {
    viewKey(scope: string) {
      if (scope !== observed) { observed = scope; sequence += 1; }
      return JSON.stringify([sequence, scope]);
    },
    begin(scope: string) { const ticket = { scope }; current = ticket; return ticket; },
    isCurrent(ticket: { scope: string }, visibleScope: string) { return current === ticket && ticket.scope === visibleScope; },
    cancel(ticket: { scope: string }) { if (current === ticket) current = null; },
  };
}

export interface NativeAvReviewSelection { exportToken: string; reviewOnly: boolean; masterSha256: string | null }

/** Keep only the public selector from a completed reply bound to its original
 * source/profile. A later view must still reopen it; no report is adopted here. */
export function nativeAvSelectionFromEvidence(source: ReviewSource, profileId: string,
  evidence: MltAvMasterEvidence | NativeMultisegmentAvMasterEvidence): NativeAvReviewSelection | null {
  if (evidence === null || typeof evidence !== "object" || Array.isArray(evidence)
    || evidence.project_resource !== "project:" + source.id || evidence.generation !== source.generation
    || evidence.revision !== source.revision || evidence.deliverable_id !== profileId
    || typeof evidence.export_token !== "string" || !evidence.export_token || evidence.export_token.length > 4096) return null;
  let digest: unknown;
  if ("master_sha256" in evidence) {
    if (typeof evidence.review_only !== "boolean") return null;
    digest = evidence.master_sha256;
  }
  else {
    const master = evidence.master;
    const artifact = master !== null && typeof master === "object" && !Array.isArray(master)
      ? master.artifact : null;
    digest = artifact !== null && typeof artifact === "object" && !Array.isArray(artifact)
      ? (artifact as Record<string, unknown>).sha256 : null;
  }
  if (!sha256(digest)) return null;
  return { exportToken: evidence.export_token, masterSha256: digest,
    reviewOnly: "review_only" in evidence && (evidence.review_only
      || evidence.evidence_scope === "native-captioned-multisegment-av-review-only-not-decoded-pixel-approved") };
}

/** Read-only hints for remounts in this JavaScript session, never a QC cache,
 * export grant or durable app-restart selector. A fresh desktop read is required. */
export function createNativeAvReviewSelectionStore() {
  const entries = new Map<string, NativeAvReviewSelection>();
  const listeners = new Set<() => void>();
  const key = (source: ReviewSource, profileId: string) =>
    JSON.stringify([source.id, source.generation, source.active_branch, source.revision, profileId]);
  return {
    remember(source: ReviewSource, profileId: string, selection: NativeAvReviewSelection) {
      requireQc(typeof selection.exportToken === "string" && selection.exportToken.length > 0 && selection.exportToken.length <= 4096
        && typeof selection.reviewOnly === "boolean" && (selection.masterSha256 === null || sha256(selection.masterSha256)));
      const scope = key(source, profileId);
      // Only these public selector fields survive. No report/evidence object is retained.
      const hint = { exportToken: selection.exportToken, reviewOnly: selection.reviewOnly, masterSha256: selection.masterSha256 };
      const previous = entries.get(scope);
      if (previous?.exportToken === hint.exportToken && previous.reviewOnly === hint.reviewOnly
        && previous.masterSha256 === hint.masterSha256) return;
      entries.delete(scope);
      entries.set(scope, hint);
      while (entries.size > 128) entries.delete(entries.keys().next().value!);
      for (const notify of listeners) notify();
    },
    read(source: ReviewSource, profileId: string): NativeAvReviewSelection | null {
      const hint = entries.get(key(source, profileId));
      return hint ? { ...hint } : null;
    },
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
  };
}

export const retainedNativeAvReviewSelections = createNativeAvReviewSelectionStore();

/** Discoverable references are not a report. No private native context is accepted. */
export function readNativeAudioQcReceiptInventoryResponse(
  value: unknown, source: ReviewSource, profileId: string,
): NativeAudioQcReceiptInventoryResponse {
  requireQc(value !== null && typeof value === "object" && !Array.isArray(value));
  const kind = (value as Record<string, unknown>).kind;
  if (kind === "unavailable") {
    const response = record(value, ["kind", "reason"]);
    requireQc(typeof response.reason === "string" && reasons.includes(response.reason as NativeAvAudioQcUnavailableReason));
    return value as NativeAudioQcReceiptInventoryResponse;
  }
  const response = record(value, ["kind", "inventory"]);
  requireQc(response.kind === "inventory");
  const inventory = record(response.inventory, [
    "project_id", "generation", "revision", "deliverable_id", "items",
    "legacy_unbound_count", "unavailable_count",
  ]);
  const uuid = (candidate: unknown) => typeof candidate === "string"
    && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(candidate);
  requireQc(uuid(inventory.project_id) && inventory.project_id === source.id
    && uuid(inventory.generation) && inventory.generation === source.generation
    && integer(inventory.revision, 0, Number.MAX_SAFE_INTEGER) && inventory.revision === source.revision
    && uuid(inventory.deliverable_id) && inventory.deliverable_id === profileId
    && integer(inventory.legacy_unbound_count, 0, 20_000)
    && integer(inventory.unavailable_count, 0, 20_000)
    && Array.isArray(inventory.items) && inventory.items.length <= 128);
  const seen = new Set<string>();
  for (const value of inventory.items) {
    const hint = record(value, ["decoded_measurement_receipt_id", "master_sha256"]);
    requireQc(uuid(hint.decoded_measurement_receipt_id) && sha256(hint.master_sha256));
    const id = (hint.decoded_measurement_receipt_id as string).toLowerCase();
    requireQc(!seen.has(id));
    seen.add(id);
  }
  requireQc(inventory.items.length + (inventory.legacy_unbound_count as number)
    + (inventory.unavailable_count as number) <= 20_000);
  return value as NativeAudioQcReceiptInventoryResponse;
}
