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
import type { Change, DeliverableProfile, Project } from "./types";

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
  || key.startsWith("text:") && profileUuid.test(key.slice(5));
const currentField = (current: Record<string, string>, key: string) => current[key] ?? "null";
const fieldsOf = (profile: DeliverableProfile): Record<string, string> => ({
  ...Object.fromEntries(profileFields.map(key => [key, JSON.stringify(profile[key])])),
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
  /** Local input sent for review; never a grant, receipt or proof of a commit. */
  awaiting?: { revision: number; fields: Record<string, { raw: string; target: string }> };
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

export function editProfileMetadata(
  draft: ProfileMetadataDraft, key: string, raw: string, current: Record<string, string>,
): ProfileMetadataDraft {
  if (!fieldKey(key) || typeof raw !== "string") throw new Error("Unknown profile field.");
  const next = structuredClone(draft);
  const saved = currentField(current, key);
  if (rawExactlyMatchesSavedField(key, raw, saved) && !next.awaiting?.fields[key]) delete next.edits[key];
  else next.edits[key] = { base: next.edits[key]?.base ?? saved, value: raw };
  return next;
}

function parseProfileField(key: string, raw: string): unknown {
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
  if (key.startsWith("text:")) {
    if (value === null) delete profile.text_overrides[key.slice(5)];
    else profile.text_overrides[key.slice(5)] = value as string;
  } else {
    (profile as unknown as Record<string, unknown>)[key] = value;
  }
}
/** UI parsing of edited fields only. Rust still validates the entire Project. */
export function proposedProfileMetadata(current: DeliverableProfile, draft: ProfileMetadataDraft): DeliverableProfile {
  const profile = structuredClone(current);
  for (const [key, edit] of Object.entries(draft.edits)) applyProfileField(profile, key, parseProfileField(key, edit.value));
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
  draft: ProfileMetadataDraft, profile: DeliverableProfile, revision: number,
): ProfileMetadataDraft {
  if (!Number.isSafeInteger(revision) || revision < draft.revision || draft.awaiting || !Object.keys(draft.edits).length) {
    throw new Error("Review the pending profile input before sending another save.");
  }
  const proposed = proposedProfileMetadata(profile, draft), target = fieldsOf(proposed);
  const next = structuredClone(draft);
  next.revision = revision;
  next.awaiting = { revision, fields: Object.fromEntries(Object.entries(draft.edits).map(([key, edit]) =>
    [key, { raw: edit.value, target: currentField(target, key) }])) };
  return next;
}
/**
 * Only an observed later Project of this exact scope can agree with all sent
 * fields. This is value agreement, not attribution to a particular request.
 */
export function reconcileProfileMetadata(
  draft: ProfileMetadataDraft, current: Record<string, string>, revision: number, scope: string,
): ProfileMetadataDraft {
  const next = structuredClone(draft);
  if (scope !== draft.scope || !Number.isSafeInteger(revision) || revision < draft.revision) return next;
  const awaiting = next.awaiting;
  if (awaiting && revision > awaiting.revision
    && Object.entries(awaiting.fields).every(([key, sent]) => currentField(current, key) === sent.target)) {
    for (const [key, sent] of Object.entries(awaiting.fields)) {
      const edit = next.edits[key];
      if (!edit) continue;
      if (edit.value === sent.raw || rawExactlyMatchesSavedField(key, edit.value, currentField(current, key))) delete next.edits[key];
      else next.edits[key] = { base: currentField(current, key), value: edit.value };
    }
    delete next.awaiting;
  }
  for (const [key, edit] of Object.entries(next.edits)) {
    if (!next.awaiting?.fields[key] && rawExactlyMatchesSavedField(key, edit.value, currentField(current, key))) delete next.edits[key];
  }
  next.revision = revision;
  return next;
}
export function resolveProfileMetadata(
  draft: ProfileMetadataDraft, key: string, current: Record<string, string>, keep: "saved" | "draft",
): ProfileMetadataDraft {
  const next = structuredClone(draft);
  if (keep === "saved") delete next.edits[key];
  else if (next.edits[key]) next.edits[key] = { base: currentField(current, key), value: next.edits[key].value };
  // An explicit local review cannot cancel or retry the original request.
  if (next.awaiting) {
    delete next.awaiting.fields[key];
    if (!Object.keys(next.awaiting.fields).length) delete next.awaiting;
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
    const value: unknown = JSON.parse(encoded);
    if (JSON.stringify(value) !== encoded) return false;
    if (key === "adaptation_notes") return Array.isArray(value) && value.length <= 64
      && value.every(note => typeof note === "string" && Boolean(note.trim()) && bytes(note) <= 1000);
    return JSON.stringify(parseProfileField(key, displayField(key, encoded))) === encoded;
  } catch { return false; }
}
export function decodeProfileMetadata(raw: string, scope: string): ProfileMetadataDraft {
  if (bytes(raw) > PROFILE_COPY_MAX_BYTES) throw new Error("Profile recovery copy exceeds 2 MiB.");
  const value: unknown = JSON.parse(raw);
  // Own copies have one canonical JSON spelling; duplicate keys/unknown edits
  // cannot be silently reduced by JSON.parse and then admitted as that copy.
  if (JSON.stringify(value) !== raw || !validScope(scope)
    || !closed(value, ["version", "scope", "revision", "edits"], ["awaiting"])
    || value.version !== 1 || value.scope !== scope || !Number.isSafeInteger(value.revision)
    || (value.revision as number) < 0 || !object(value.edits)
    || Object.keys(value.edits).length > profileFields.length + 4096) throw new Error("The profile recovery copy has an unsupported identity or format.");
  for (const [key, edit] of Object.entries(value.edits)) {
    if (!fieldKey(key) || !closed(edit, ["base", "value"]) || !boundedText(edit.base) || !boundedText(edit.value)) {
      throw new Error("The profile recovery copy has an invalid field.");
    }
    // A base is an encoded canonical field; authored raw may be incomplete.
    if (!canonicalProfileField(key, edit.base)) throw new Error("Invalid canonical base in the recovery copy.");
  }
  if (Object.hasOwn(value, "awaiting")) {
    const sent = value.awaiting;
    if (!closed(sent, ["revision", "fields"]) || !Number.isSafeInteger(sent.revision)
      || (sent.revision as number) < 0 || (sent.revision as number) > (value.revision as number)
      || !object(sent.fields) || !Object.keys(sent.fields).length
      || Object.keys(sent.fields).length > profileFields.length + 4096) throw new Error("Invalid pending profile input.");
    for (const [key, field] of Object.entries(sent.fields)) {
      if (!fieldKey(key) || !Object.hasOwn(value.edits, key) || !closed(field, ["raw", "target"])
        || !boundedText(field.raw) || !boundedText(field.target)
        || JSON.stringify(parseProfileField(key, field.raw)) !== field.target) throw new Error("Invalid pending profile field.");
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
  const entry = { ...previous, draft: structuredClone(draft) };
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


export function useProfileMetadataDraft(
  project: Project, profileId: string, commit: (change: Change) => Promise<void>,
  currentProjectState: () => DeliveryProjectState,
) {
  const [writerId] = useState(recipeDraftWriterId);
  const [, render] = useState(0);
  const [pending, setPending] = useState(false);
  const [action, setAction] = useState<{ key: string; error: string | null; message: string | null }>({ key: "", error: null, message: null });
  const [copyEpoch, setCopyEpoch] = useState(0);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const profile = project.deliverables.find(item => item.id === profileId) ?? null;
  const scope = profileMetadataScope(project, profileId), key = profileMetadataKey(scope, writerId);
  const current = profile ? fieldsOf(profile) : {};
  const stored: ProfileMetadataRecovery = profile ? loadProfileMetadata(key, scope, project.revision)
    : { draft: newProfileMetadataDraft(scope, project.revision), blocked: null, warning: null };
  const visible = reconcileProfileMetadata(stored.draft, current, project.revision, scope);
  const live = useRef({ key, project, profile, current, draft: visible });
  live.current = { key, project, profile, current, draft: visible };
  const update = (draft: ProfileMetadataDraft) => {
    if (!mounted.current || live.current.key !== key || !live.current.profile) return;
    live.current.draft = draft;
    const recovery = persistProfileMetadata(key, draft);
    render(value => value + 1);
    return recovery;
  };
  useEffect(() => {
    if (live.current.key !== key || !live.current.profile) return;
    // Read the current in-memory input, not an earlier effect's captured draft.
    update(reconcileProfileMetadata(live.current.draft, live.current.current, live.current.project.revision, scope));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, project.revision, JSON.stringify(current)]);
  const conflicts = profileMetadataConflicts(visible, current);
  const values = { ...(profile ? profileRawFields(profile) : {}),
    ...Object.fromEntries(Object.entries(visible.edits).map(([field, edit]) => [field, edit.value])) };
  const locked = project.locks.some(lock => lock.kind === "content" && lock.resource === "project:" + project.id);
  const sourceBehind = visible.revision > project.revision;
  const copyView = useRef<{ key: string; epoch: number; copies: ProfileRecoveryCopy[]; warning: string | null }>({ key: "", epoch: -1, copies: [], warning: null });
  if (copyView.current.key !== key || copyView.current.epoch !== copyEpoch) {
    copyView.current = { key, epoch: copyEpoch, ...profileMetadataCopies(scope, key) };
  }
  const setFields = (changes: Record<string, string>) => {
    if (live.current.key !== key || !live.current.profile) return;
    try {
      let next = live.current.draft;
      for (const [field, value] of Object.entries(changes)) next = editProfileMetadata(next, field, value, live.current.current);
      update(next);
      setAction({ key, error: null, message: null });
    } catch (failure) { setAction({ key, error: String(failure), message: null }); }
  };
  const setPreview = (candidate: DeliverableProfile) => {
    const state = live.current;
    if (state.key !== key || !state.profile || candidate.id !== state.profile.id) return;
    const before = fieldsOf(previewProfileMetadata(state.profile, state.draft)), after = fieldsOf(candidate);
    const changes = Object.fromEntries([...new Set([...Object.keys(before), ...Object.keys(after)])]
      .filter(field => currentField(before, field) !== currentField(after, field))
      .map(field => [field, displayField(field, currentField(after, field))]));
    // Only controls changed by this gesture are updated. Invalid raw in another
    // control must not be replaced with the preview's last valid saved value.
    setFields(changes);
  };
  const resolve = (field: string, keep: "saved" | "draft") => {
    if (live.current.key !== key || inFlight.current || sourceBehind) return;
    update(resolveProfileMetadata(live.current.draft, field, live.current.current, keep));
    setAction({ key, error: null, message: keep === "draft" ? "The input is kept against the current saved value. Saving remains an explicit action." : null });
  };
  const save = async () => {
    const state = live.current;
    const recovery = profileMemory.get(key);
    if (!mounted.current || state.key !== key || !state.profile || inFlight.current || locked
      || state.draft.revision > state.project.revision || recovery?.blocked || state.draft.awaiting
      || profileMetadataConflicts(state.draft, state.current).length || !Object.keys(state.draft.edits).length) return;
    let proposed: DeliverableProfile, sent: ProfileMetadataDraft;
    try {
      const actor = currentProjectState();
      if (actor.busy || !sameProjectRevision(actor.project, state.project)) throw new Error("The current project operation changed. Review the current profile before saving.");
      proposed = proposedProfileMetadata(state.profile, state.draft);
      sent = submitProfileMetadata(state.draft, state.profile, state.project.revision);
    } catch (failure) { setAction({ key, error: failure instanceof Error ? failure.message : String(failure), message: null }); return; }
    const unsent = state.draft;
    inFlight.current = true; setPending(true);
    setAction({ key, error: null, message: null });
    try {
      const retained = update(sent);
      if (!retained || retained.blocked) {
        // No commit was called. Keep the authored input without an in-memory
        // sent capture; a blocked/unknown storage copy is never overwritten.
        update(unsent);
        if (mounted.current && live.current.key === key) setAction({ key,
          error: "The save was not sent because its recovery copy needs review. Your input is retained.", message: null });
        return;
      }
      await commit({ type: "upsert_deliverable", profile: proposed });
      if (mounted.current && live.current.key === key) setAction({ key, error: null,
        message: "The save request returned. Only matching values observed in a later saved revision confirm the submitted input; later typing stays in this window." });
    } catch (failure) {
      if (mounted.current && live.current.key === key) setAction({ key, error: failure instanceof Error ? failure.message : String(failure), message: null });
    } finally {
      inFlight.current = false;
      if (mounted.current) setPending(false);
    }
  };
  const error = [...new Set([action.key === key ? action.error : null, stored.blocked, stored.warning, copyView.current.warning]
    .filter((value): value is string => Boolean(value)))].join(" ") || null;
  return {
    values, savedValues: current, edits: visible.edits, conflicts,
    profile: profile ? previewProfileMetadata(profile, visible) : null,
    dirty: Object.keys(visible.edits).length > 0, awaiting: Boolean(visible.awaiting),
    sentFields: visible.awaiting?.fields ?? {}, pending, locked, sourceBehind,
    recoveryBlocked: Boolean(stored.blocked), recoveryReadUnverified: Boolean(stored.readUnverified),
    error, message: action.key === key ? action.message : null,
    set: (field: string, value: string) => setFields({ [field]: value }), setFields, setPreview, resolve, save,
    copies: copyView.current.copies, refreshCopies: () => setCopyEpoch(value => value + 1),
    recoverCopy: (copyKey: string) => {
      if (live.current.key !== key || inFlight.current || sourceBehind) return;
      try {
        const recovered = recoverProfileMetadata(key, copyKey, scope, live.current.current, live.current.project.revision);
        update(recovered.draft);
        setAction({ key, error: null, message: "A separate window's draft was recovered for review. Its original copy is retained." });
      } catch (failure) { setAction({ key, error: String(failure), message: null }); }
    },
    rereadRecoveryCopy: () => {
      if (live.current.key !== key || inFlight.current) return;
      try {
        const recovered = rereadProfileMetadata(key, scope, live.current.project.revision);
        live.current.draft = reconcileProfileMetadata(recovered.draft, live.current.current, live.current.project.revision, scope);
        render(value => value + 1);
        setAction({ key, error: null, message: null });
      } catch (failure) { setAction({ key, error: String(failure), message: null }); }
    },
    discardUnreadableCopy: () => {
      if (live.current.key !== key || inFlight.current) return;
      try {
        const recovered = discardUnreadableProfileMetadata(key, scope, live.current.project.revision);
        update(recovered.draft); setAction({ key, error: null, message: null });
      } catch (failure) { setAction({ key, error: String(failure), message: null }); }
    },
  };
}
