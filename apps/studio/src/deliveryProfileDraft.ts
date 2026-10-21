import { sameProjectRevision, type ProjectActivity, type ProjectRevision } from "./projectMutation";

export interface DeliveryProjectState extends ProjectActivity {
  project: ProjectRevision | null;
}

export interface DeliveryDispatchView {
  currentProject: DeliveryProjectState;
  renderedProject: ProjectRevision;
  profileId: string | null;
  inputSerial: number;
  renderedInputSerial: number;
  actionKey: string;
  ready: boolean;
  draftBlocked: boolean;
  mounted: boolean;
}

export interface DeliveryDispatchIntent {
  project: ProjectRevision;
  profileId: string;
  inputSerial: number;
  actionKey: string;
}

export interface DeliveryDispatchGesture {
  /** Call only before an effect is sent. Backend consent/CAS remain mandatory. */
  beforeDispatch: () => void;
  /** View admission only after dispatch; a false result never cancels the effect. */
  stillCurrent: () => boolean;
}

function serial(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 0;
}

/**
 * Bind one local gesture to the rendered Project, profile and pending inputs.
 * The reader must use current refs, including the existing App actor. It is
 * called again after the native grant awaits and before the original invoke.
 * Nothing here grants execution, persists approval, changes a request UUID or
 * retries an effect. A completed/unknown backend result stays that result.
 */
export function captureDeliveryDispatch(
  intent: DeliveryDispatchIntent,
  readCurrent: () => DeliveryDispatchView,
): DeliveryDispatchGesture {
  const source: ProjectRevision = {
    id: intent.project.id, generation: intent.project.generation,
    revision: intent.project.revision, active_branch: intent.project.active_branch,
  };
  const profileId = intent.profileId;
  const inputSerial = intent.inputSerial;
  const actionKey = intent.actionKey;
  const first = readCurrent();
  const actorSerial = first.currentProject.serial;
  const matches = (): boolean => {
    try {
      const view = readCurrent();
      return Boolean(source.id && source.generation && source.active_branch && profileId && actionKey)
        && serial(source.revision) && serial(inputSerial) && serial(actorSerial)
        && view.mounted === true && view.ready === true && view.draftBlocked === false
        && view.currentProject.busy === false && view.currentProject.serial === actorSerial
        && sameProjectRevision(view.currentProject.project, source)
        && sameProjectRevision(view.renderedProject, source)
        && view.profileId === profileId && view.actionKey === actionKey
        && view.inputSerial === inputSerial && view.renderedInputSerial === inputSerial;
    } catch {
      return false;
    }
  };
  const beforeDispatch = () => {
    if (!matches()) {
      throw new Error("The native action was not sent because the project, pending input or active operation changed. Review the current delivery inputs before starting a new action.");
    }
  };
  beforeDispatch();
  return { beforeDispatch, stillCurrent: matches };
}

import { useEffect, useRef, useState } from "react";
import { recipeDraftWriterId } from "./deliveryRecipe";
import { bootstrap, projectHistory } from "./api";
import { sameValue } from "./creativeProduction";
import type { Change, DeliverableProfile, Project, ProjectEvent } from "./types";

const profileFields = [
  "name", "width", "height", "language", "captions", "caption_format",
  "video_codec", "audio_codec", "audio_sample_rate_hz", "brand_profile",
  "cut_label", "framing_strategy", "crop_approved", "timing_locked",
  "voice_track_id", "included_scene_ids", "protected_scene_ids",
  "burn_in_captions", "frame_rate", "color_space", "container", "adaptation_notes",
] as const;
type ProfileField = typeof profileFields[number];
const profileUuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const profileDecimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
const bytes = (text: string) => new TextEncoder().encode(text).length;
const fieldKey = (key: string): boolean => (profileFields as readonly string[]).includes(key)
  || key === "cut_mode"
  || key.startsWith("text:") && profileUuid.test(key.slice(5));
const currentField = (current: Record<string, string>, key: string) => current[key] ?? "null";
function profileFrameRateJson(value: unknown): string {
  if (!closed(value, ["num", "den"]) || typeof value.num !== "string" || typeof value.den !== "string") {
    throw new Error("The saved frame rate requires exact num and den strings.");
  }
  // Object property order is a transport detail. Preserve the exact validated
  // integers; do not round, reduce the fraction, or normalize other fields.
  return JSON.stringify(parseProfileField("frame_rate", value.num + "/" + value.den));
}
function recoveredFrameRateJson(encoded: string): string {
  const value: unknown = JSON.parse(encoded);
  // Check the original spelling before reordering. In particular, JSON.parse
  // must not silently admit duplicate keys from a retained recovery copy.
  if (JSON.stringify(value) !== encoded) throw new Error("Invalid saved frame rate JSON.");
  return profileFrameRateJson(value);
}
const fieldsOf = (profile: DeliverableProfile): Record<string, string> => ({
  // Local editor state only: canonical [] continues to mean the full sequence.
  cut_mode: JSON.stringify(profile.included_scene_ids.length ? "custom" : "full"),
  ...Object.fromEntries(profileFields.map(key => [key, key === "frame_rate"
    ? profileFrameRateJson(profile[key]) : JSON.stringify(profile[key])])),
  ...Object.fromEntries(Object.entries(profile.text_overrides).map(([id, value]) => ["text:" + id, JSON.stringify(value)])),
});
function displayField(key: string, encoded: string): string {
  const value: unknown = JSON.parse(encoded);
  if (value === null) return "";
  if (key === "adaptation_notes") return (value as string[]).join("\n");
  if (key === "frame_rate") {
    const rate = value as DeliverableProfile["frame_rate"];
    return rate.num + "/" + rate.den;
  }
  return typeof value === "string" ? value : typeof value === "number" || typeof value === "boolean"
    ? String(value) : JSON.stringify(value);
}
export const profileCanonicalFields = fieldsOf;
export function profileRawFields(profile: DeliverableProfile): Record<string, string> {
  return Object.fromEntries(Object.entries(fieldsOf(profile)).map(([key, value]) => [key, displayField(key, value)]));
}

export interface ProfileFieldEdit {
  /** JSON of the typed saved field, not its potentially ambiguous display text. */
  base: string;
  value: string;
}
export interface ProfileMetadataDraft {
  version: 1;
  scope: string;
  revision: number;
  edits: Record<string, ProfileFieldEdit>;
  sequence?: number;
  versions?: Record<string, number>;
  /** Historical captures without original remain UNKNOWN; recovery is never reply authority. */
  awaiting?: {
    revision: number; fields: Record<string, { raw: string; target: string }>;
    original?: { id: string; profile: DeliverableProfile; versions: Record<string, number> };
  };
}
export const PROFILE_DRAFT_STORAGE_PREFIX = "motionwright.delivery-profile-draft/1:";
export const profileMetadataScope = (project: Project, profileId: string): string =>
  JSON.stringify([project.id, project.generation, project.active_branch, profileId]);
export const profileMetadataKey = (scope: string, writerId: string): string =>
  PROFILE_DRAFT_STORAGE_PREFIX + scope + ":" + writerId;
export const newProfileMetadataDraft = (scope: string, revision: number): ProfileMetadataDraft =>
  ({ version: 1, scope, revision, edits: {} });

function rawExactlyMatchesSavedField(key: string, raw: string, saved: string): boolean {
  if (raw !== displayField(key, saved)) return false;
  try { return JSON.stringify(parseProfileField(key, raw)) === saved; }
  catch { return false; }
}

function keepCutContext(draft: ProfileMetadataDraft, key: string, current: Record<string, string>): boolean {
  const companionKey = key === "cut_mode" ? "included_scene_ids" : key === "included_scene_ids" ? "cut_mode" : null;
  const companion = companionKey ? draft.edits[companionKey] : undefined;
  return Boolean(companionKey && companion
    && !rawExactlyMatchesSavedField(companionKey, companion.value, currentField(current, companionKey)));
}

export function editProfileMetadata(
  draft: ProfileMetadataDraft, key: string, raw: string, current: Record<string, string>,
): ProfileMetadataDraft {
  if (!fieldKey(key) || typeof raw !== "string") throw new Error("Unknown profile field.");
  const next = structuredClone(draft);
  const saved = currentField(current, key);
  if (rawExactlyMatchesSavedField(key, raw, saved) && !next.awaiting?.fields[key] && !keepCutContext(next, key, current)) delete next.edits[key];
  else next.edits[key] = { base: next.edits[key]?.base ?? saved, value: raw };
  if (key === "cut_mode") {
    // A mode gesture also owns its current list, including canonical [].
    // Otherwise a later peer custom list could silently fill an empty draft.
    if (next.edits.cut_mode && !next.edits.included_scene_ids) {
      const selection = currentField(current, "included_scene_ids");
      next.edits.included_scene_ids = { base: selection, value: displayField("included_scene_ids", selection) };
    }
    const cutFields = ["included_scene_ids", "cut_mode"];
    if (cutFields.every(field => !next.awaiting?.fields[field]
      && (!next.edits[field] || rawExactlyMatchesSavedField(field, next.edits[field].value, currentField(current, field))))) {
      for (const field of cutFields) delete next.edits[field];
    }
  }
  next.sequence = (draft.sequence ?? 0) + 1;
  next.versions = { ...draft.versions };
  for (const field of Object.keys(next.versions)) if (!next.edits[field]) delete next.versions[field];
  for (const field of Object.keys(next.edits)) {
    if (field === key || !draft.edits[field]) next.versions[field] = next.sequence;
  }
  return next;
}

function parseProfileField(key: string, raw: string): unknown {
  if (key === "cut_mode") {
    if (raw !== "full" && raw !== "custom") throw new Error("Choose the full sequence or a custom cut.");
    return raw;
  }
  if (key.startsWith("text:")) {
    if (raw === "") return null;
    if (!raw.trim() || bytes(raw) > 16_384) throw new Error("Localized text must contain text and fit within 16,384 UTF-8 bytes.");
    return raw;
  }
  if (["width", "height", "audio_sample_rate_hz"].includes(key)) {
    if (!profileDecimal.test(raw.trim())) throw new Error("Enter a complete decimal number for " + key.replaceAll("_", " ") + ".");
    const value = Number(raw);
    if (!Number.isSafeInteger(value) || (key === "audio_sample_rate_hz"
      ? ![44100, 48000, 96000].includes(value) : value < 1 || value > 16384)) {
      throw new Error(key === "audio_sample_rate_hz" ? "Choose 44.1, 48 or 96 kHz." : "Profile dimensions must be whole pixels between 1 and 16,384.");
    }
    return value;
  }
  if (["captions", "crop_approved", "timing_locked", "burn_in_captions"].includes(key)) {
    if (raw !== "true" && raw !== "false") throw new Error("Choose a boolean value for " + key.replaceAll("_", " ") + ".");
    return raw === "true";
  }
  if (key === "name" || key === "language") {
    const limit = key === "name" ? 160 : 64;
    if (!raw.trim() || bytes(raw) > limit) throw new Error(key === "name"
      ? "Profile name must contain text and fit within 160 UTF-8 bytes."
      : "Language must contain text and fit within 64 UTF-8 bytes.");
    return raw;
  }
  if (key === "brand_profile" || key === "cut_label") {
    // Empty optional input is converted only on explicit Save, never on typing.
    if (!raw.trim()) return null;
    if (bytes(raw) > 256) throw new Error("Optional profile labels must fit within 256 UTF-8 bytes.");
    return raw;
  }
  if (key === "voice_track_id") {
    if (raw !== "" && !profileUuid.test(raw)) throw new Error("Choose a current voice take.");
    return raw || null;
  }
  if (key === "adaptation_notes") {
    // This editor defines one trimmed, nonblank note per line at explicit Save.
    const notes = raw.split("\n").map(value => value.trim()).filter(Boolean);
    if (notes.length > 64 || notes.some(value => bytes(value) > 1000)) throw new Error("Use at most 64 notes, each within 1,000 UTF-8 bytes.");
    return notes;
  }
  if (key === "included_scene_ids" || key === "protected_scene_ids") {
    const value: unknown = JSON.parse(raw);
    if (!Array.isArray(value) || value.length > 4096 || value.some(id => typeof id !== "string" || !profileUuid.test(id))
      || new Set(value).size !== value.length) throw new Error("Scene selections must contain at most 4,096 distinct scene IDs.");
    return value;
  }
  if (key === "frame_rate") {
    const match = /^([1-9]\d*)\/([1-9]\d*)$/.exec(raw);
    if (!match || match[1].length > 19 || match[2].length > 19) throw new Error("Choose an exact positive frame rate.");
    const num = BigInt(match[1]), den = BigInt(match[2]), max = 9223372036854775807n;
    if (num > max || den > max || num > den * 240n) throw new Error("Frame rate must be positive and no greater than 240 fps.");
    return { num: match[1], den: match[2] };
  }
  const choices: Partial<Record<ProfileField, readonly string[]>> = {
    caption_format: ["web_vtt", "srt"], video_codec: ["h264", "hevc", "prores_422_hq", "vp9", "av1"],
    audio_codec: ["aac", "pcm_s16_le", "opus"], framing_strategy: ["replan", "crop"],
    color_space: ["rec709", "display_p3", "rec2020"], container: ["mp4", "mov", "webm", "mkv"],
  };
  if (!(choices[key as ProfileField] ?? []).includes(raw)) throw new Error("Choose a supported " + key.replaceAll("_", " ") + ".");
  return raw;
}
function applyProfileField(profile: DeliverableProfile, key: string, value: unknown): void {
  if (key === "cut_mode") return; // Never add editor-only mode to the canonical profile.
  if (key.startsWith("text:")) {
    if (value === null) delete profile.text_overrides[key.slice(5)];
    else profile.text_overrides[key.slice(5)] = value as string;
  } else {
    (profile as unknown as Record<string, unknown>)[key] = value;
  }
}
/** Recover historical array-only drafts without guessing a new canonical meaning. */
export function profileMetadataCutMode(current: DeliverableProfile, draft: ProfileMetadataDraft): "full" | "custom" {
  const explicit = draft.edits.cut_mode?.value;
  if (explicit === "full" || explicit === "custom") return explicit;
  const oldSelection = draft.edits.included_scene_ids;
  if (oldSelection) {
    try { return (parseProfileField("included_scene_ids", oldSelection.value) as string[]).length ? "custom" : "full"; }
    catch { /* Invalid historical raw remains in the review, without a different mode. */ }
  }
  return current.included_scene_ids.length ? "custom" : "full";
}

/** UI parsing of edited fields only. Rust still validates the entire Project. */
export function proposedProfileMetadata(current: DeliverableProfile, draft: ProfileMetadataDraft): DeliverableProfile {
  const profile = structuredClone(current);
  for (const [key, edit] of Object.entries(draft.edits)) applyProfileField(profile, key, parseProfileField(key, edit.value));
  const cutMode = profileMetadataCutMode(current, draft);
  if (cutMode === "custom" && !profile.included_scene_ids.length) throw new Error("Choose at least one scene for the custom cut, or choose Use full sequence explicitly.");
  if (cutMode === "full" && profile.included_scene_ids.length) throw new Error("The full sequence and custom scene choices disagree. Review the narrative cut.");
  if (profile.framing_strategy === "crop" && !profile.crop_approved) throw new Error("Approve the crop before saving crop framing.");
  if (profile.parent_profile_id !== null && !profile.adaptation_notes.length) throw new Error("A derived profile requires at least one adaptation note.");
  return profile;
}
/** Layout feedback never replaces the exact raw control values or the Save parser. */
export function previewProfileMetadata(current: DeliverableProfile, draft: ProfileMetadataDraft): DeliverableProfile {
  const profile = structuredClone(current);
  for (const [key, edit] of Object.entries(draft.edits)) {
    try { applyProfileField(profile, key, parseProfileField(key, edit.value)); }
    catch { /* Incomplete text remains in its control; native readiness is blocked. */ }
  }
  return profile;
}
export function profileMetadataConflicts(draft: ProfileMetadataDraft, current: Record<string, string>): string[] {
  return Object.entries(draft.edits).filter(([key, edit]) => {
    const saved = currentField(current, key);
    if (saved === edit.base) return false;
    try { return saved !== JSON.stringify(parseProfileField(key, edit.value)); }
    catch { return true; }
  }).map(([key]) => key);
}
export function submitProfileMetadata(
  draft: ProfileMetadataDraft, profile: DeliverableProfile, revision: number, originalId?: string,
): ProfileMetadataDraft {
  if (!Number.isSafeInteger(revision) || revision < draft.revision || draft.awaiting || !Object.keys(draft.edits).length)
    throw new Error("Review the pending profile input before sending another save.");
  if (!validScope(draft.scope) || (JSON.parse(draft.scope) as string[])[3] !== profile.id
    || originalId !== undefined && !profileUuid.test(originalId)) throw new Error("The original profile attempt identity is invalid.");
  const proposed = proposedProfileMetadata(profile, draft), target = fieldsOf(proposed);
  const next = structuredClone(draft);
  next.revision = revision;
  next.awaiting = { revision, fields: Object.fromEntries(Object.entries(draft.edits).map(([key, edit]) =>
    [key, { raw: edit.value, target: currentField(target, key) }])) };
  if (originalId !== undefined) next.awaiting.original = {
    id: originalId, profile: structuredClone(proposed),
    versions: Object.fromEntries(Object.keys(draft.edits).map(key => [key, draft.versions?.[key] ?? 0])),
  };
  return next;
}
/** Incoming saved values never consume author input or acknowledge its request. */
export function reconcileProfileMetadata(
  draft: ProfileMetadataDraft, _current: Record<string, string>, revision: number, scope: string,
): ProfileMetadataDraft {
  const next = structuredClone(draft);
  if (scope !== draft.scope || !Number.isSafeInteger(revision) || revision < draft.revision) return next;
  next.revision = revision;
  return next;
}
/** The live original Promise<Project> is the only caller that owns this ID.
 * A recovered capture alone cannot invoke an acknowledgement or replay a save.
 */
export function acknowledgeProfileMetadata(draft: ProfileMetadataDraft, originalId: string, receipt: Project): ProfileMetadataDraft {
  const awaiting = draft.awaiting, original = awaiting?.original;
  if (!awaiting || !original || original.id !== originalId) return structuredClone(draft);
  const saved = receipt.deliverables.find(profile => profile.id === original.profile.id);
  if (profileMetadataScope(receipt, original.profile.id) !== draft.scope || receipt.revision !== awaiting.revision + 1
    || receipt.revision < draft.revision || !saved || !sameValue(saved, original.profile))
    throw new Error("The original reply did not confirm this exact profile upsert. UNKNOWN and author input were retained.");
  const next = structuredClone(draft), current = fieldsOf(saved);
  for (const key of Object.keys(awaiting.fields)) {
    const edit = next.edits[key];
    if (!edit) continue;
    if ((draft.versions?.[key] ?? 0) === original.versions[key] && !keepCutContext(next, key, current)) {
      delete next.edits[key]; if (next.versions) delete next.versions[key];
    } else next.edits[key] = { base: currentField(current, key), value: edit.value };
  }
  delete next.awaiting; next.revision = receipt.revision;
  return next;
}

export function resolveProfileMetadata(
  draft: ProfileMetadataDraft, key: string, current: Record<string, string>, keep: "saved" | "draft",
): ProfileMetadataDraft {
  if (draft.awaiting) throw new Error("The original profile outcome is UNKNOWN. Inspect its saved source and history before a separate intent.");
  const next = structuredClone(draft);
  // The local mode and scene list form one cut decision; reviewing either
  // resolves that pair, while other metadata inputs remain independent.
  const keys = key === "cut_mode" || key === "included_scene_ids" ? ["included_scene_ids", "cut_mode"] : [key];
  for (const field of keys) {
    if (keep === "saved") delete next.edits[field];
    else if (next.edits[field]) next.edits[field] = { base: currentField(current, field), value: next.edits[field].value };
    if (!next.edits[field] && next.versions) delete next.versions[field];
  }
  return next;
}

const PROFILE_COPY_MAX_BYTES = 2 * 1024 * 1024;
const PROFILE_FIELD_MAX_BYTES = 512 * 1024;
export interface ProfileMetadataRecovery {
  draft: ProfileMetadataDraft;
  blocked: string | null;
  warning: string | null;
  observedRaw?: string | null;
  /** The current own copy is unknown until an explicit successful read. */
  readUnverified?: true;
  /** True only after this exact write and readback completed. */
  writeVerified?: boolean;
}
type ProfileStorage = Pick<Storage, "getItem" | "setItem" | "removeItem" | "key" | "length">;
const profileMemory = new Map<string, ProfileMetadataRecovery>();
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function closed(value: unknown, required: string[], optional: string[] = []): value is Record<string, unknown> {
  return object(value) && required.every(key => Object.hasOwn(value, key))
    && Object.keys(value).every(key => required.includes(key) || optional.includes(key));
}
function boundedText(value: unknown): value is string {
  return typeof value === "string" && bytes(value) <= PROFILE_FIELD_MAX_BYTES;
}
function validScope(scope: string): boolean {
  try {
    const parts: unknown = JSON.parse(scope);
    return Array.isArray(parts) && parts.length === 4 && parts.every(part => typeof part === "string" && profileUuid.test(part))
      && JSON.stringify(parts) === scope;
  } catch { return false; }
}
function scopedProfileCopy(scope: string, key: string): boolean {
  const prefix = PROFILE_DRAFT_STORAGE_PREFIX + scope + ":";
  return validScope(scope) && key.startsWith(prefix) && profileUuid.test(key.slice(prefix.length));
}
function canonicalProfileField(key: string, encoded: string): boolean {
  try {
    if (key === "frame_rate") { recoveredFrameRateJson(encoded); return true; }
    const value: unknown = JSON.parse(encoded);
    if (JSON.stringify(value) !== encoded) return false;
    if (key === "adaptation_notes") return Array.isArray(value) && value.length <= 64
      && value.every(note => typeof note === "string" && Boolean(note.trim()) && bytes(note) <= 1000);
    return JSON.stringify(parseProfileField(key, displayField(key, encoded))) === encoded;
  } catch { return false; }
}
function validProfileSnapshot(value: unknown, profileId: string): value is DeliverableProfile {
  if (!closed(value, [...profileFields, "id", "parent_profile_id", "source_revision", "text_overrides"])
    || value.id !== profileId || !(value.parent_profile_id === null || typeof value.parent_profile_id === "string" && profileUuid.test(value.parent_profile_id))
    || !(value.source_revision === null || typeof value.source_revision === "number" && serial(value.source_revision))
    || !object(value.text_overrides) || Object.keys(value.text_overrides).length > 4096) return false;
  try {
    const canonical = fieldsOf(value as unknown as DeliverableProfile);
    return Object.entries(canonical).every(([key, encoded]) => fieldKey(key) && canonicalProfileField(key, encoded));
  } catch { return false; }
}

export function decodeProfileMetadata(raw: string, scope: string): ProfileMetadataDraft {
  if (bytes(raw) > PROFILE_COPY_MAX_BYTES) throw new Error("Profile recovery copy exceeds 2 MiB.");
  const value: unknown = JSON.parse(raw);
  // Own copies have one canonical JSON spelling; duplicate keys/unknown edits
  // cannot be silently reduced by JSON.parse and then admitted as that copy.
  if (JSON.stringify(value) !== raw || !validScope(scope)
    || !closed(value, ["version", "scope", "revision", "edits"], ["awaiting", "sequence", "versions"])
    || value.version !== 1 || value.scope !== scope || !Number.isSafeInteger(value.revision)
    || (value.revision as number) < 0 || !object(value.edits)
    || Object.keys(value.edits).length > profileFields.length + 1 + 4096) throw new Error("The profile recovery copy has an unsupported identity or format.");
  for (const [key, edit] of Object.entries(value.edits)) {
    if (!fieldKey(key) || !closed(edit, ["base", "value"]) || !boundedText(edit.base) || !boundedText(edit.value)) {
      throw new Error("The profile recovery copy has an invalid field.");
    }
    // A base is an encoded canonical field; authored raw may be incomplete.
    if (!canonicalProfileField(key, edit.base)) throw new Error("Invalid canonical base in the recovery copy.");
    if (key === "frame_rate") edit.base = recoveredFrameRateJson(edit.base);
  }
  if ((value.sequence !== undefined || value.versions !== undefined)
    && (!serial(Number(value.sequence)) || typeof value.sequence !== "number" || !object(value.versions)
      || Object.keys(value.versions).some(key => !Object.hasOwn(value.edits as object, key)
        || !serial(Number((value.versions as Record<string, unknown>)[key]))
        || typeof (value.versions as Record<string, unknown>)[key] !== "number"
        || Number((value.versions as Record<string, unknown>)[key]) > Number(value.sequence))))
    throw new Error("The profile recovery edit sequence is invalid.");
  if (Object.hasOwn(value, "awaiting")) {
    const sent = value.awaiting;
    if (!closed(sent, ["revision", "fields"], ["original"]) || !Number.isSafeInteger(sent.revision)
      || (sent.revision as number) < 0 || (sent.revision as number) > (value.revision as number)
      || !object(sent.fields) || !Object.keys(sent.fields).length
      || Object.keys(sent.fields).length > profileFields.length + 1 + 4096) throw new Error("Invalid pending profile input.");
    for (const [key, field] of Object.entries(sent.fields)) {
      if (!fieldKey(key) || !Object.hasOwn(value.edits, key) || !closed(field, ["raw", "target"])
        || !boundedText(field.raw) || !boundedText(field.target)) throw new Error("Invalid pending profile field.");
      const target = key === "frame_rate" ? recoveredFrameRateJson(field.target) : field.target;
      if (JSON.stringify(parseProfileField(key, field.raw)) !== target) throw new Error("Invalid pending profile field.");
      if (key === "frame_rate") field.target = target;
    }
    if (sent.original !== undefined) {
      const original = sent.original;
      if (!closed(original, ["id", "profile", "versions"]) || typeof original.id !== "string" || !profileUuid.test(original.id)
        || !validProfileSnapshot(original.profile, (JSON.parse(scope) as string[])[3])
        || !object(original.versions) || Object.keys(original.versions).length !== Object.keys(sent.fields).length
        || Object.entries(original.versions).some(([key, version]) => !Object.hasOwn(sent.fields as object, key)
          || typeof version !== "number" || !serial(version) || version > Number(value.sequence ?? 0)))
        throw new Error("The original profile capture is invalid.");
      const proposed = fieldsOf(original.profile);
      if (Object.entries(sent.fields).some(([key, field]) => currentField(proposed, key) !== (field as { target: string }).target))
        throw new Error("The original profile capture disagrees with its authored fields.");
    }
  }
  return value as unknown as ProfileMetadataDraft;
}
export function loadProfileMetadata(
  key: string, scope: string, revision: number, storage?: ProfileStorage,
): ProfileMetadataRecovery {
  if (!scopedProfileCopy(scope, key) || !serial(revision)) throw new Error("Invalid profile recovery identity.");
  const cached = profileMemory.get(key);
  if (cached) {
    if (cached.draft.scope !== scope) throw new Error("A cached profile copy belongs to another scope.");
    return structuredClone(cached);
  }
  const entry: ProfileMetadataRecovery = { draft: newProfileMetadataDraft(scope, revision), blocked: null, warning: null };
  try {
    if (!scopedProfileCopy(scope, key)) throw new Error("Invalid profile recovery key.");
    const raw = (storage ?? localStorage).getItem(key);
    entry.observedRaw = raw;
    if (raw !== null) {
      try { entry.draft = decodeProfileMetadata(raw, scope); }
      catch { entry.blocked = "The stored profile draft could not be read. Its original bytes are retained."; }
    }
  } catch {
    entry.readUnverified = true;
    entry.blocked = "The profile recovery copy could not be read. Read it again before saving or starting a native action.";
    entry.warning = "Profile recovery storage is unavailable. New input stays in this window.";
  }
  profileMemory.set(key, structuredClone(entry));
  return entry;
}
export function persistProfileMetadata(
  key: string, draft: ProfileMetadataDraft, storage?: ProfileStorage,
): ProfileMetadataRecovery {
  if (!scopedProfileCopy(draft.scope, key) || !serial(draft.revision)) throw new Error("Invalid profile recovery identity.");
  const previous = profileMemory.get(key) ?? loadProfileMetadata(key, draft.scope, draft.revision, storage);
  if (previous.draft.scope !== draft.scope) throw new Error("A cached profile copy belongs to another scope.");
  const entry = { ...previous, draft: structuredClone(draft), writeVerified: false };
  profileMemory.set(key, structuredClone(entry)); // Keep all input even if storage refuses it.
  if (entry.blocked) return structuredClone(entry);
  try {
    const target = storage ?? localStorage;
    const observeOwnCopy = () => {
      try { return target.getItem(key); }
      catch (failure) {
        entry.readUnverified = true;
        entry.blocked = "The profile recovery copy could not be read. Read it again before saving or starting a native action.";
        throw failure;
      }
    };
    const original = observeOwnCopy();
    if (entry.observedRaw === undefined ? original !== null : original !== entry.observedRaw) {
      entry.observedRaw = original;
      entry.blocked = "The recovery copy changed outside this view. Both that copy and this window's input are retained.";
      throw new Error("Review the separate recovery copy before replacing it.");
    }
    const raw = Object.keys(draft.edits).length || draft.awaiting ? JSON.stringify(draft) : null;
    if (raw !== null) {
      decodeProfileMetadata(raw, draft.scope);
      if (target.length > 4096) throw new Error("Too many storage keys to verify profile recovery safely.");
      let total = bytes(raw), count = 1;
      for (let index = 0; index < target.length; index++) {
        const other = target.key(index);
        if (other?.startsWith(PROFILE_DRAFT_STORAGE_PREFIX) && other !== key) {
          total += bytes(target.getItem(other) ?? ""); count++;
        }
      }
      if (count > 64 || total > 8 * 1024 * 1024) throw new Error("Profile recovery copies reached their 64-copy / 8 MiB budget.");
      target.setItem(key, raw);
    } else target.removeItem(key);
    const after = observeOwnCopy();
    if (after !== raw) {
      entry.observedRaw = after;
      entry.blocked = "The profile recovery write could not be verified. The observed copy and this window's input are retained.";
      throw new Error("Recovery readback differs from the input.");
    }
    entry.observedRaw = raw;
    entry.warning = null;
    entry.writeVerified = true;
  } catch (failure) {
    entry.warning = "The draft is kept in this window, but its recovery copy could not be saved: " + String(failure);
  }
  profileMemory.set(key, structuredClone(entry));
  return structuredClone(entry);
}
/** Read only the own copy after an observation failure. This never removes or
 * rewrites storage, sends a request, or replaces input typed while it was unreadable. */
export function rereadProfileMetadata(
  key: string, scope: string, revision: number, storage?: ProfileStorage,
): ProfileMetadataRecovery {
  const entry = loadProfileMetadata(key, scope, revision, storage);
  if (!entry.readUnverified) return entry;
  try {
    const target = storage ?? localStorage;
    const raw = target.getItem(key);
    if (target.getItem(key) !== raw) throw new Error("The profile recovery copy changed while it was read.");
    const previousRaw = entry.observedRaw;
    entry.observedRaw = raw;
    delete entry.readUnverified;
    entry.warning = null;
    if (previousRaw !== undefined) {
      entry.blocked = raw === previousRaw ? null
        : "The recovery copy changed outside this view. Both that copy and this window's input are retained.";
    } else if (raw === null) {
      entry.blocked = null;
    } else if (entry.draft.awaiting || Object.keys(entry.draft.edits).length) {
      entry.blocked = "The recovered copy and this window's later input are separate. The original bytes are retained for explicit review.";
    } else {
      try { entry.draft = decodeProfileMetadata(raw, scope); entry.blocked = null; }
      catch { entry.blocked = "The stored profile draft could not be read. Its original bytes are retained."; }
    }
  } catch (failure) {
    entry.readUnverified = true;
    entry.blocked = "The profile recovery copy could not be read. Read it again before saving or starting a native action.";
    entry.warning = "The original copy and this window's input are retained: " + String(failure);
  }
  profileMemory.set(key, structuredClone(entry));
  return structuredClone(entry);
}

export function discardUnreadableProfileMetadata(
  key: string, scope: string, revision: number, storage?: ProfileStorage,
): ProfileMetadataRecovery {
  const entry = loadProfileMetadata(key, scope, revision, storage);
  if (!entry.blocked) return entry;
  if (entry.draft.awaiting) throw new Error("The original UNKNOWN profile copy must remain retained.");
  if (entry.readUnverified) throw new Error("Read the original recovery copy successfully before deciding whether to discard it.");
  const target = storage ?? localStorage, original = target.getItem(key);
  if (entry.observedRaw === undefined || original !== entry.observedRaw) throw new Error("The original copy changed; it was not removed.");
  target.removeItem(key);
  if (target.getItem(key) !== null) throw new Error("The original copy could not be removed.");
  entry.blocked = null; entry.warning = null; entry.observedRaw = null;
  profileMemory.set(key, entry);
  return persistProfileMetadata(key, entry.draft, storage);
}
export interface ProfileRecoveryCopy { key: string; revision: number | null; fields: number; readable: boolean }
export function profileMetadataCopies(scope: string, ownKey: string, storage?: ProfileStorage):
  { copies: ProfileRecoveryCopy[]; warning: string | null } {
  const copies: ProfileRecoveryCopy[] = [];
  try {
    const target = storage ?? localStorage;
    if (target.length > 4096) throw new Error("Too many recovery keys to inspect.");
    for (let index = 0; index < target.length; index++) {
      const key = target.key(index);
      if (!key || key === ownKey || !scopedProfileCopy(scope, key)) continue;
      if (copies.length >= 64) throw new Error("More than 64 separate profile copies require review.");
      try {
        const draft = decodeProfileMetadata(target.getItem(key) ?? "", scope);
        copies.push({ key, revision: draft.revision, fields: Object.keys(draft.edits).length, readable: true });
      } catch { copies.push({ key, revision: null, fields: 0, readable: false }); }
    }
    return { copies, warning: null };
  } catch (failure) { return { copies, warning: "Recovery copies could not all be read: " + String(failure) }; }
}
export function recoverProfileMetadata(
  ownKey: string, copyKey: string, scope: string, current: Record<string, string>, revision: number, storage?: ProfileStorage,
): ProfileMetadataRecovery {
  const own = loadProfileMetadata(ownKey, scope, revision, storage);
  if (own.blocked || own.draft.awaiting || Object.keys(own.draft.edits).length || ownKey === copyKey || !scopedProfileCopy(scope, copyKey)) {
    throw new Error("Keep or resolve this window's profile input before recovering another copy.");
  }
  const target = storage ?? localStorage, raw = target.getItem(copyKey);
  if (raw === null) throw new Error("The separate recovery copy no longer exists.");
  const draft = decodeProfileMetadata(raw, scope);
  if (target.getItem(copyKey) !== raw) throw new Error("The separate recovery copy changed while it was read.");
  return persistProfileMetadata(ownKey, reconcileProfileMetadata(draft, current, revision, scope), target);
}


export interface RetainedProfileMetadataCopy {
  key: string;
  profileId: string;
  source: "storage" | "window";
  /** Exact snapshot bytes represented by this row; never authority to recreate a profile. */
  raw: string;
  draft: ProfileMetadataDraft | null;
  warning: string | null;
}
function removedProfileScope(project: Project, key: string): { scope: string; profileId: string } | null {
  if (key.length > 300 || !key.startsWith(PROFILE_DRAFT_STORAGE_PREFIX)) return null;
  const end = key.lastIndexOf("]:");
  if (end < PROFILE_DRAFT_STORAGE_PREFIX.length) return null;
  const scope = key.slice(PROFILE_DRAFT_STORAGE_PREFIX.length, end + 1);
  if (!scopedProfileCopy(scope, key)) return null;
  const [projectId, generation, branch, profileId] = JSON.parse(scope) as string[];
  if (projectId !== project.id || generation !== project.generation || branch !== project.active_branch
    || project.deliverables.some(profile => profile.id === profileId)) return null;
  return { scope, profileId };
}
/** Read retained input for missing profiles. Neither scanning nor downloading a
 * snapshot updates recovery storage, recreates a profile, or admits production. */
export function retainedProfileMetadataCopies(project: Project, storage?: ProfileStorage):
  { copies: RetainedProfileMetadataCopy[]; warning: string | null } {
  const copies = new Map<string, RetainedProfileMetadataCopy>();
  const stored = new Map<string, string>(), warnings = new Set<string>();
  const add = (key: string, raw: string, source: "storage" | "window", warning: string | null = null) => {
    const identity = removedProfileScope(project, key);
    if (!identity) return;
    if (copies.size >= 64) {
      warnings.add("More than 64 retained profile snapshots require review. Their original copies remain untouched.");
      return;
    }
    let draft: ProfileMetadataDraft | null;
    try { draft = decodeProfileMetadata(raw, identity.scope); }
    catch { draft = null; }
    if (draft && !draft.awaiting && !Object.keys(draft.edits).length) return;
    copies.set(source + ":" + key, { key, profileId: identity.profileId, source, raw, draft, warning });
  };
  try {
    const target = storage ?? localStorage;
    if (target.length > 4096) throw new Error("Too many recovery keys to inspect.");
    for (let index = 0; index < target.length; index++) {
      const key = target.key(index);
      if (!key || !removedProfileScope(project, key)) continue;
      try {
        const raw = target.getItem(key);
        if (target.getItem(key) !== raw) throw new Error("A retained recovery copy changed while it was read. Refresh to review its current bytes.");
        if (raw !== null) { stored.set(key, raw); add(key, raw, "storage"); }
      } catch (failure) { warnings.add("A retained profile copy could not be read: " + String(failure)); }
    }
  } catch (failure) { warnings.add("Retained profile recovery could not be fully read: " + String(failure)); }
  // A failed write may leave more recent authored input only in this window.
  // Keep it separate from different or corrupt stored bytes; never overwrite
  // the stored row with the cached draft as part of a read operation.
  for (const [key, entry] of profileMemory) {
    if (!removedProfileScope(project, key) || !entry.draft.awaiting && !Object.keys(entry.draft.edits).length) continue;
    const raw = JSON.stringify(entry.draft);
    if (stored.get(key) === raw) continue;
    add(key, raw, "window", entry.warning ?? entry.blocked ?? (stored.has(key)
      ? "This window's input is separate from its stored recovery copy. Both snapshots are retained."
      : "This input is kept in this window. Download a copy before closing the window."));
  }
  return { copies: [...copies.values()], warning: [...warnings].join(" ") || null };
}


export const PROFILE_WRITER_PREFIX = "motionwright.delivery-profile-writer.v1:";
export interface ProfileWriterState {
  writerId: string; expectedPointer: string | null | undefined; readUnverified: boolean; error: string | null;
}
type ProfileTab = Pick<Storage, "getItem" | "setItem">;
export function readProfileWriter(scope: string, fallback: string, tab: ProfileTab): ProfileWriterState {
  if (!validScope(scope) || !profileUuid.test(fallback)) throw new Error("Invalid profile writer identity.");
  const state: ProfileWriterState = { writerId: fallback, expectedPointer: undefined, readUnverified: false, error: null };
  try {
    const pointer = tab.getItem(PROFILE_WRITER_PREFIX + scope);
    if (pointer !== null && !profileUuid.test(pointer)) throw new Error("The tab's profile writer pointer is invalid.");
    state.expectedPointer = pointer; state.writerId = pointer ?? fallback;
  } catch (failure) {
    state.readUnverified = true; state.error = "The profile writer pointer could not be read: " + String(failure);
  }
  return state;
}
export function verifyProfileWriter(scope: string, state: ProfileWriterState, tab: ProfileTab, reread = false): void {
  try {
    const key = PROFILE_WRITER_PREFIX + scope, pointer = tab.getItem(key);
    if (state.readUnverified) {
      if (!reread) throw new Error("Read the profile writer pointer again before saving.");
      if (pointer !== null && pointer !== state.writerId) throw new Error("Another retained writer owns this profile pointer. Both inputs remain separate.");
      state.expectedPointer = pointer; state.readUnverified = false;
    }
    if (pointer !== state.expectedPointer && pointer !== state.writerId)
      throw new Error("Another writer changed this tab's profile pointer. Its original input was kept.");
    if (pointer !== state.writerId) tab.setItem(key, state.writerId);
    if (tab.getItem(key) !== state.writerId) throw new Error("The profile writer pointer write was not confirmed.");
    state.expectedPointer = state.writerId; state.error = null;
  } catch (failure) { state.error = String(failure); throw failure; }
}
export const profileCopyVerified = (entry: ProfileMetadataRecovery | undefined): boolean =>
  Boolean(entry?.writeVerified && !entry.blocked && !entry.warning);
export interface ProfileOutcomeTransport {
  readProject(id: string): Promise<Project>;
  readHistory(project: Project, after: number, limit: number): Promise<ProjectEvent[]>;
}
export interface ProfileOutcome {
  scope: string; awaiting: NonNullable<ProfileMetadataDraft["awaiting"]>;
  project: Project; events: ProjectEvent[]; matchingValueRevisions: number[];
}
const sameProfileOwner = (scope: string, project: Project): boolean =>
  validScope(scope) && JSON.stringify((JSON.parse(scope) as string[]).slice(0, 3))
    === JSON.stringify([project.id, project.generation, project.active_branch]);
export function profileOutcomeMatches(draft: ProfileMetadataDraft, project: Project, outcome: ProfileOutcome | null): boolean {
  return Boolean(outcome && draft.awaiting && outcome.scope === draft.scope && sameProfileOwner(draft.scope, project)
    && sameValue(draft.awaiting, outcome.awaiting) && sameValue(project, outcome.project));
}
export async function inspectProfileOutcome(
  draft: ProfileMetadataDraft, selected: Project, transport: ProfileOutcomeTransport,
): Promise<ProfileOutcome> {
  if (!draft.awaiting || !sameProfileOwner(draft.scope, selected)) throw new Error("Select the exact project, generation and branch of this UNKNOWN profile capture.");
  const awaiting = structuredClone(draft.awaiting), saved = structuredClone(await transport.readProject(selected.id));
  if (!sameValue(saved, selected)) throw new Error("The saved project changed. Reload it before reading this profile outcome.");
  if (!serial(saved.revision) || saved.revision < awaiting.revision || saved.revision - awaiting.revision > 2048)
    throw new Error("The profile history is outside its bounded 2048-revision inspection window.");
  const events: ProjectEvent[] = []; let cursor = awaiting.revision;
  do {
    const page = await transport.readHistory(saved, cursor, 256);
    if (!Array.isArray(page) || page.length > 256 || !page.length && cursor < saved.revision)
      throw new Error("The saved profile history is incomplete.");
    for (const event of page) {
      if (!event || !serial(event.revision) || event.revision !== cursor + 1 || event.revision > saved.revision
        || !event.change || typeof event.change.type !== "string" || typeof event.created_at !== "string" || !event.created_at)
        throw new Error("The saved profile history is not a contiguous revision sequence.");
      events.push(structuredClone(event)); cursor = event.revision;
    }
  } while (cursor < saved.revision);
  if (!sameValue(await transport.readProject(selected.id), saved)) throw new Error("The saved project changed during inspection. Read it and the history again.");
  const profileId = (JSON.parse(draft.scope) as string[])[3];
  return { scope: draft.scope, awaiting, project: saved, events,
    matchingValueRevisions: events.filter(event => {
      if (event.change.type !== "upsert_deliverable") return false;
      const candidate = event.change.profile;
      if (!object(candidate) || candidate.id !== profileId) return false;
      if (!validProfileSnapshot(candidate, profileId)) throw new Error("The saved history contains an invalid profile upsert.");
      if (awaiting.original) return sameValue(candidate, awaiting.original.profile);
      const current = fieldsOf(candidate);
      return Object.entries(awaiting.fields).every(([key, field]) => currentField(current, key) === field.target);
    }).map(event => event.revision) };
}
export function separateProfileIntent(draft: ProfileMetadataDraft, project: Project, outcome: ProfileOutcome): ProfileMetadataDraft {
  if (!profileOutcomeMatches(draft, project, outcome))
    throw new Error("Read the current saved profile and complete history before starting a separate intent.");
  const id = (JSON.parse(draft.scope) as string[])[3], profile = project.deliverables.find(item => item.id === id);
  if (!profile) throw new Error("The original profile was removed. Its UNKNOWN capture remains retained.");
  const next = structuredClone(draft), current = fieldsOf(profile), awaiting = draft.awaiting!;
  for (const key of Object.keys(awaiting.fields)) {
    const edit = next.edits[key];
    if (!edit) continue;
    // Historical captures did not record original edit versions. An equal raw
    // value cannot prove that no later author gesture occurred; retain it.
    const later = !awaiting.original
      || (draft.versions?.[key] ?? 0) !== awaiting.original.versions[key];
    if (!later) { delete next.edits[key]; if (next.versions) delete next.versions[key]; }
  }
  for (const [key, edit] of Object.entries(next.edits)) next.edits[key] = { base: currentField(current, key), value: edit.value };
  delete next.awaiting; next.revision = project.revision;
  return next;
}
/** Publish a separate pointer only after both retained copies were verified.
 * Failure never deletes or acknowledges the original UNKNOWN capture.
 */
export function retainSeparateProfileIntent(
  ownKey: string, draft: ProfileMetadataDraft, next: ProfileMetadataDraft, writer: ProfileWriterState,
  newWriterId: string, storage: ProfileStorage, tab: ProfileTab,
): ProfileWriterState {
  if (!draft.awaiting || next.awaiting || next.scope !== draft.scope || newWriterId === writer.writerId || !profileUuid.test(newWriterId)
    || ownKey !== profileMetadataKey(draft.scope, writer.writerId))
    throw new Error("Invalid separate profile intent.");
  verifyProfileWriter(draft.scope, writer, tab);
  const prior = profileMemory.get(ownKey), original = storage.getItem(ownKey);
  const retained = original === null ? null : decodeProfileMetadata(original, draft.scope);
  // A saved-source observation advances only the comparison revision in memory.
  // Verify all author input and the capture against the original durable bytes
  // without rewriting that historical revision while publishing a new writer.
  if (!retained || prior?.observedRaw !== original || prior.blocked || prior.warning || prior.readUnverified
    || retained.revision > draft.revision || !sameValue(retained, { ...draft, revision: retained.revision }))
    throw new Error("The original UNKNOWN profile copy was not retained exactly.");
  const newKey = profileMetadataKey(draft.scope, newWriterId);
  if (storage.getItem(newKey) !== null || profileMemory.has(newKey)) throw new Error("The new profile writer already has a retained copy.");
  if (!profileCopyVerified(persistProfileMetadata(newKey, draft, storage))
    || !profileCopyVerified(persistProfileMetadata(newKey, next, storage)))
    throw new Error("The separate profile intent could not be retained.");
  if (storage.getItem(ownKey) !== original) throw new Error("The original profile copy changed during inspection.");
  const candidate: ProfileWriterState = { writerId: newWriterId, expectedPointer: writer.writerId, readUnverified: false, error: null };
  verifyProfileWriter(draft.scope, candidate, tab);
  if (storage.getItem(ownKey) !== original) throw new Error("The original UNKNOWN copy changed while the separate pointer was published.");
  return candidate;
}

let profileUnloadProtected = false;
const profileWriters = new Map<string, ProfileWriterState>();
const profileListeners = new Map<string, Set<() => void>>();
// Original-promise display state only; ProjectMutationGate remains the sole
// canonical Project coordinator. These entries never replay an operation.
const profileFlights = new Set<string>();
const notifyProfile = (key: string) => profileListeners.get(key)?.forEach(listener => listener());
function writerFor(scope: string, fallback: string): ProfileWriterState {
  if (!profileUnloadProtected) {
    profileUnloadProtected = true;
    window.addEventListener("beforeunload", event => {
      if ([...profileMemory.values()].some(entry => (entry.blocked || entry.warning || entry.writeVerified === false)
        && (entry.draft.awaiting || Object.keys(entry.draft.edits).length))) {
        event.preventDefault(); event.returnValue = "";
      }
    });
  }
  const cached = profileWriters.get(scope);
  if (cached) return cached;
  let writer: ProfileWriterState;
  try { writer = readProfileWriter(scope, fallback, window.sessionStorage); }
  catch (failure) { writer = { writerId: fallback, expectedPointer: undefined, readUnverified: true, error: String(failure) }; }
  profileWriters.set(scope, writer); return writer;
}

export function useProfileMetadataDraft(
  project: Project, profileId: string, commit: (change: Change) => Promise<Project>,
  currentProjectState: () => DeliveryProjectState,
) {
  const [legacyWriterId] = useState(recipeDraftWriterId), [, render] = useState(0);
  const [action, setAction] = useState<{ key: string; error: string | null; message: string | null }>({ key: "", error: null, message: null });
  const [copyEpoch, setCopyEpoch] = useState(0);
  const [inspection, setInspection] = useState<{ key: string; reading: boolean; outcome: ProfileOutcome | null }>({ key: "", reading: false, outcome: null });
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const profile = project.deliverables.find(item => item.id === profileId) ?? null;
  const scope = profileMetadataScope(project, profileId);
  const writer = profile ? writerFor(scope, legacyWriterId) : { writerId: legacyWriterId, expectedPointer: null, readUnverified: false, error: null };
  const key = profileMetadataKey(scope, writer.writerId), current = profile ? fieldsOf(profile) : {};
  const stored: ProfileMetadataRecovery = profile ? loadProfileMetadata(key, scope, project.revision)
    : { draft: newProfileMetadataDraft(scope, project.revision), blocked: null, warning: null };
  const visible = reconcileProfileMetadata(stored.draft, current, project.revision, scope), pending = profileFlights.has(key);
  const live = useRef({ key, project, profile, current, draft: visible });
  live.current = { key, project, profile, current, draft: visible };
  useEffect(() => {
    const listener = () => render(value => value + 1), set = profileListeners.get(key) ?? new Set<() => void>();
    set.add(listener); profileListeners.set(key, set);
    return () => { set.delete(listener); if (!set.size) profileListeners.delete(key); };
  }, [key]);
  const report = (error: string | null, message: string | null = null) => {
    if (mounted.current && live.current.key === key) setAction({ key, error, message });
  };
  const retain = (draft: ProfileMetadataDraft): ProfileMetadataRecovery => {
    const recovery = persistProfileMetadata(key, draft);
    if ((draft.awaiting || Object.keys(draft.edits).length) && profileCopyVerified(recovery)) {
      try { verifyProfileWriter(scope, writer, window.sessionStorage); }
      catch (failure) { writer.error = String(failure); }
    }
    if (mounted.current && live.current.key === key) live.current.draft = draft;
    notifyProfile(key); return recovery;
  };
  const update = (draft: ProfileMetadataDraft) => {
    if (!mounted.current || live.current.key !== key || !live.current.profile) return;
    return retain(draft);
  };
  useEffect(() => {
    if (live.current.key !== key || !live.current.profile) return;
    // Observations may advance a local comparison revision, but never rewrite
    // recovery bytes, remove author edits or consume an original capture.
    const entry = profileMemory.get(key);
    if (entry) profileMemory.set(key, { ...entry, draft: reconcileProfileMetadata(
      entry.draft, live.current.current, live.current.project.revision, scope) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, project.revision, JSON.stringify(current)]);
  const conflicts = profileMetadataConflicts(visible, current);
  const values: Record<string, string> = { ...(profile ? profileRawFields(profile) : {}),
    ...Object.fromEntries(Object.entries(visible.edits).map(([field, edit]) => [field, edit.value])),
    cut_mode: profile ? profileMetadataCutMode(profile, visible) : "full" };
  const locked = project.locks.some(lock => lock.kind === "content" && lock.resource === "project:" + project.id);
  const sourceBehind = visible.revision > project.revision;
  const recoveryBlocked = Boolean(stored.blocked || stored.warning || stored.writeVerified === false || writer.error);
  const copyView = useRef<{ key: string; epoch: number; copies: ProfileRecoveryCopy[]; warning: string | null }>({ key: "", epoch: -1, copies: [], warning: null });
  if (copyView.current.key !== key || copyView.current.epoch !== copyEpoch)
    copyView.current = { key, epoch: copyEpoch, ...profileMetadataCopies(scope, key) };
  const setFields = (changes: Record<string, string>) => {
    if (live.current.key !== key || !live.current.profile) return;
    try {
      let next = live.current.draft;
      for (const [field, value] of Object.entries(changes)) next = editProfileMetadata(next, field, value, live.current.current);
      update(next); report(null);
    } catch (failure) { report(String(failure)); }
  };
  const setPreview = (candidate: DeliverableProfile) => {
    const state = live.current;
    if (state.key !== key || !state.profile || candidate.id !== state.profile.id) return;
    const before = fieldsOf(previewProfileMetadata(state.profile, state.draft)), after = fieldsOf(candidate);
    setFields(Object.fromEntries([...new Set([...Object.keys(before), ...Object.keys(after)])]
      .filter(field => currentField(before, field) !== currentField(after, field))
      .map(field => [field, displayField(field, currentField(after, field))])));
  };
  const resolve = (field: string, keep: "saved" | "draft") => {
    if (live.current.key !== key || profileFlights.has(key) || sourceBehind || live.current.draft.awaiting) return;
    try {
      update(resolveProfileMetadata(live.current.draft, field, live.current.current, keep));
      report(null, keep === "draft" ? "This input is retained against the current saved value. Saving remains explicit." : null);
    } catch (failure) { report(String(failure)); }
  };
  const save = async () => {
    const state = live.current, recovery = profileMemory.get(key);
    if (!mounted.current || state.key !== key || !state.profile || profileFlights.has(key) || locked
      || state.draft.revision > state.project.revision || recovery?.blocked || recovery?.warning || writer.error
      || state.draft.awaiting || profileMetadataConflicts(state.draft, state.current).length || !Object.keys(state.draft.edits).length) return;
    const originalId = crypto.randomUUID(); let proposed: DeliverableProfile, sent: ProfileMetadataDraft;
    try {
      const actor = currentProjectState();
      if (actor.busy || !sameProjectRevision(actor.project, state.project)) throw new Error("The current project operation changed. Review the current profile before saving.");
      proposed = proposedProfileMetadata(state.profile, state.draft);
      sent = submitProfileMetadata(state.draft, state.profile, state.project.revision, originalId);
    } catch (failure) { report(String(failure)); return; }
    const unsent = state.draft;
    profileFlights.add(key); notifyProfile(key); report(null);
    try {
      const retained = retain(sent);
      if (!profileCopyVerified(retained) || writer.error) {
        retain(unsent);
        report("The save was not sent because the profile copy and pointer were not both verified. Your input was kept.");
        return;
      }
      // Keep original authority even if the editor unmounts or selects a different
      // profile while this callback is pending. Later input is read from its key.
      const receipt = await commit({ type: "upsert_deliverable", profile: proposed });
      const latest = profileMemory.get(key)?.draft ?? sent;
      retain(acknowledgeProfileMetadata(latest, originalId, receipt));
      report(null, "The original reply confirmed this exact profile upsert. Later author input was retained.");
    } catch (failure) { report(String(failure)); }
    finally { profileFlights.delete(key); notifyProfile(key); }
  };
  const readOutcome = async () => {
    if (live.current.key !== key || profileFlights.has(key) || !live.current.draft.awaiting || inspection.key === key && inspection.reading) return;
    const selected = live.current.project, draft = structuredClone(live.current.draft);
    setInspection({ key, reading: true, outcome: null }); report(null);
    try {
      const outcome = await inspectProfileOutcome(draft, selected, {
        readProject: async id => {
          const boot = await bootstrap(id);
          if (!boot.project || boot.project_selection.status !== "selected") throw new Error("The saved profile project is unavailable.");
          return boot.project;
        }, readHistory: projectHistory,
      });
      if (!mounted.current || live.current.key !== key) return;
      if (!profileOutcomeMatches(live.current.draft, live.current.project, outcome))
        throw new Error("The profile owner, capture or saved source changed. Inspect it again.");
      setInspection({ key, reading: false, outcome });
    } catch (failure) {
      report(String(failure)); if (mounted.current && live.current.key === key) setInspection({ key, reading: false, outcome: null });
    }
  };
  const outcome = inspection.key === key && profileOutcomeMatches(visible, project, inspection.outcome) ? inspection.outcome : null;
  const continueAfterReview = () => {
    if (live.current.key !== key || profileFlights.has(key) || !outcome) return;
    try {
      const before = live.current.draft, next = separateProfileIntent(before, live.current.project, outcome);
      if (writer.error) throw new Error("Verify the original profile recovery pointer before starting a separate intent.");
      const candidate = retainSeparateProfileIntent(key, before, next, writer, crypto.randomUUID(), window.localStorage, window.sessionStorage);
      profileWriters.set(scope, candidate);
      setInspection({ key: "", reading: false, outcome: null }); render(value => value + 1);
      setAction({ key: profileMetadataKey(scope, candidate.writerId), error: null,
        message: "The original UNKNOWN copy was kept. The separate intent did not acknowledge or replay its request." });
    } catch (failure) { report(String(failure)); }
  };
  const retryRecovery = () => {
    if (live.current.key !== key || profileFlights.has(key)) return;
    try {
      if (writer.readUnverified) verifyProfileWriter(scope, writer, window.sessionStorage, true);
      const retained = retain(live.current.draft);
      if (!profileCopyVerified(retained) || writer.error) throw new Error(writer.error ?? retained.blocked ?? retained.warning ?? "Recovery is not verified.");
      report(null, "The current profile input and writer pointer were read back. Saving remains an explicit action.");
    } catch (failure) { report(String(failure)); }
    render(value => value + 1);
  };
  const download = () => {
    const raw = JSON.stringify(live.current.draft), url = URL.createObjectURL(new Blob([raw], { type: "application/json" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = "motionwright-profile-input-" + writer.writerId + ".json";
    anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  const error = [...new Set([action.key === key ? action.error : null, stored.blocked, stored.warning, writer.error, copyView.current.warning]
    .filter((value): value is string => Boolean(value)))].join(" ") || null;
  return {
    values, savedValues: current, edits: visible.edits, conflicts,
    profile: profile ? previewProfileMetadata(profile, visible) : null,
    dirty: Object.keys(visible.edits).length > 0, awaiting: Boolean(visible.awaiting),
    sentFields: visible.awaiting?.fields ?? {}, pending, locked, sourceBehind,
    recoveryBlocked, unreadableCopy: Boolean(stored.blocked),
    recoveryReadUnverified: Boolean(stored.readUnverified), error, message: action.key === key ? action.message : null,
    outcome, reviewing: inspection.key === key && inspection.reading, readOutcome, continueAfterReview, retryRecovery, download,
    set: (field: string, value: string) => setFields({ [field]: value }), setFields, setPreview, resolve, save,
    copies: copyView.current.copies, refreshCopies: () => setCopyEpoch(value => value + 1),
    recoverCopy: (copyKey: string) => {
      if (live.current.key !== key || profileFlights.has(key) || sourceBehind) return;
      try {
        const recovered = recoverProfileMetadata(key, copyKey, scope, live.current.current, live.current.project.revision);
        update(recovered.draft); report(null, "A separate profile draft was recovered for review. Its original copy is retained.");
      } catch (failure) { report(String(failure)); }
    },
    rereadRecoveryCopy: () => {
      if (live.current.key !== key || profileFlights.has(key)) return;
      try {
        const recovered = rereadProfileMetadata(key, scope, live.current.project.revision);
        live.current.draft = reconcileProfileMetadata(recovered.draft, live.current.current, live.current.project.revision, scope);
        render(value => value + 1); report(null);
      } catch (failure) { report(String(failure)); }
    },
    discardUnreadableCopy: () => {
      if (live.current.key !== key || profileFlights.has(key) || live.current.draft.awaiting) return;
      try { update(discardUnreadableProfileMetadata(key, scope, live.current.project.revision).draft); report(null); }
      catch (failure) { report(String(failure)); }
    },
  };
}
