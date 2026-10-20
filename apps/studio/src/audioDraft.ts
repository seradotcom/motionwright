import type { Change, Project, ProjectEvent, RationalTime } from "./types";
import { rationalSeconds } from "./types";

/** Editor buffers only. The Project, service grants and SQLite CAS retain authority. */
export type AudioDraftGroup =
  | { kind: "active-voice" }
  | { kind: "mix" }
  | { kind: "transcript"; id: string; voiceId: string }
  | { kind: "cue"; id: string }
  | { kind: "new-transcript"; voiceId: string | null }
  | { kind: "new-cue" };
export interface AudioFieldDraft { raw: string; base: string; baseRevision: number; intent?: true }
export interface AudioSubmission {
  scope: string; revision: number; raw: Record<string, string>;
  selection?: { intentId: string; outcome: "UNKNOWN" | "ACKNOWLEDGED"; diagnostic: string | null };
  change: Extract<Change, { type: "set_active_voice_track" | "set_mix_intent" | "upsert_transcript_segment" | "upsert_audio_cue" }>;
}
export interface AudioDraft {
  schema: "motionwright.audio-draft/1";
  scope: string; group: AudioDraftGroup; fields: Record<string, AudioFieldDraft>;
  creationId: string; awaiting: AudioSubmission | null;
}
export const AUDIO_DRAFT_PREFIX = "motionwright.audio-recovery.v1:";
export const AUDIO_COPY_BYTES = 64 * 1024;
export const AUDIO_COPY_LIMIT = 128;
export const AUDIO_TOTAL_BYTES = 4 * 1024 * 1024;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
export const audioFieldLabels: Record<string, string> = {
  voiceGain: "Voice gain dB", musicGain: "Music gain dB", targetLufs: "Target LUFS",
  targetPeak: "Target true peak dBFS", start: "Start", end: "End", speaker: "Speaker",
  text: "Transcript", at: "Cue time", label: "Cue label", trackId: "Active voice",
};
export const audioGroupName = (group: AudioDraftGroup) => group.kind === "active-voice" ? "Active voice" : group.kind === "mix" ? "Mix intent"
  : group.kind === "new-transcript" ? "New transcript" : group.kind === "new-cue" ? "New cue"
  : group.kind === "transcript" ? "Transcript " + group.id : "Cue " + group.id;
export const audioFields = (group: AudioDraftGroup): string[] => group.kind === "active-voice" ? ["trackId"] : group.kind === "mix"
  ? ["voiceGain", "musicGain", "targetLufs", "targetPeak"]
  : group.kind === "transcript" || group.kind === "new-transcript" ? ["start", "end", "speaker", "text"]
  : ["at", "label"];
export const audioDraftScope = (project: Project, group: AudioDraftGroup) =>
  JSON.stringify([project.id, project.generation, project.active_branch, group]);
const seconds = (time: RationalTime) => String(Number(time.num) / Number(time.den));
const isNew = (group: AudioDraftGroup) => group.kind.startsWith("new-");
export function audioCanonicalFields(project: Project, group: AudioDraftGroup): Record<string, string> | null {
  if (group.kind === "active-voice") return { trackId: project.audio.active_voice_track_id ?? "" };
  if (isNew(group)) return Object.fromEntries(audioFields(group).map(field => [field, ""]));
  if (group.kind === "mix") return {
    voiceGain: String(project.audio.mix.voice_gain_db), musicGain: String(project.audio.mix.music_gain_db),
    targetLufs: project.audio.mix.target_lufs == null ? "" : String(project.audio.mix.target_lufs),
    targetPeak: project.audio.mix.target_true_peak_dbfs == null ? "" : String(project.audio.mix.target_true_peak_dbfs),
  };
  if (group.kind === "transcript") {
    const segment = project.audio.transcript.find(item => item.id === group.id && item.voice_track_id === group.voiceId);
    return segment ? { start: seconds(segment.start), end: seconds(segment.end), speaker: segment.speaker ?? "", text: segment.text } : null;
  }
  const cue = project.audio.cues.find(item => group.kind === "cue" && item.id === group.id);
  return cue ? { at: seconds(cue.at), label: cue.label } : null;
}
export function parseAudioNumber(raw: string): number {
  const value = raw.trim();
  if (!decimal.test(value) || !Number.isFinite(Number(value))) throw new Error("Enter a finite decimal number; blank input is not zero.");
  return Number(value);
}
function comparable(field: string, raw: string): string {
  if (["text", "speaker", "label", "trackId"].includes(field)) return raw.trim();
  if (["targetLufs", "targetPeak"].includes(field) && !raw.trim()) return "";
  try {
    const value = parseAudioNumber(raw);
    return String(["voiceGain", "musicGain", "targetLufs", "targetPeak"].includes(field) ? Math.fround(value) : value);
  } catch { return "invalid:" + raw; }
}
export const audioFieldHasInput = (field: AudioFieldDraft) => field.intent === true || field.raw !== field.base;
export const audioDraftHasWork = (draft: AudioDraft) => !!draft.awaiting || Object.values(draft.fields).some(audioFieldHasInput);
function assertScope(draft: AudioDraft, project: Project) {
  if (draft.scope !== audioDraftScope(project, draft.group)) throw new Error("This audio draft belongs to another project, generation, branch or source.");
}
export function createAudioDraft(project: Project, group: AudioDraftGroup, creationId = crypto.randomUUID()): AudioDraft {
  const source = audioCanonicalFields(project, group);
  if (!source) throw new Error("The audio item is no longer in the current project.");
  return { schema: "motionwright.audio-draft/1", scope: audioDraftScope(project, group), group: structuredClone(group),
    creationId, awaiting: null, fields: Object.fromEntries(audioFields(group).map(key => [key,
      { raw: source[key], base: source[key], baseRevision: project.revision }])) };
}
export function editAudioField(draft: AudioDraft, field: string, raw: string): AudioDraft {
  if (!audioFields(draft.group).includes(field)) throw new Error("Unknown audio field.");
  return { ...draft, fields: { ...draft.fields, [field]: { ...draft.fields[field], raw,
    ...(draft.awaiting ? { intent: true as const } : {}) } } };
}
export function audioConflicts(draft: AudioDraft, project: Project) {
  assertScope(draft, project);
  const source = audioCanonicalFields(project, draft.group);
  return audioFields(draft.group).flatMap(field => {
    const input = draft.fields[field], current = source?.[field] ?? "(removed)";
    return audioFieldHasInput(input) && (!source || input.baseRevision > project.revision
      || (comparable(field, current) !== comparable(field, input.base) && comparable(field, current) !== comparable(field, input.raw)))
      ? [{ field, before: input.base, current, proposed: input.raw, futureRevision: input.baseRevision > project.revision }] : [];
  });
}
export function reconcileAudioDraft(draft: AudioDraft, project: Project): AudioDraft {
  assertScope(draft, project);
  const source = audioCanonicalFields(project, draft.group);
  if (!source) return draft;
  const fields = { ...draft.fields };
  for (const field of audioFields(draft.group)) {
    const input = fields[field], dirty = audioFieldHasInput(input), current = source[field];
    if (dirty && input.baseRevision > project.revision) continue;
    if (!dirty || comparable(field, current) === comparable(field, input.base) || comparable(field, current) === comparable(field, input.raw)) {
      fields[field] = { ...input, raw: dirty ? input.raw : current, base: current, baseRevision: project.revision };
    }
  }
  return { ...draft, fields };
}
export function resolveAudioField(draft: AudioDraft, project: Project, field: string, choice: "saved" | "draft"): AudioDraft {
  assertScope(draft, project);
  if (draft.group.kind === "active-voice" && draft.awaiting) throw new Error("Review the retained selection before another intent.");
  const source = audioCanonicalFields(project, draft.group);
  if (!source) throw new Error("The source was removed. Keep this recovery copy or discard it explicitly; saving cannot recreate the removed item.");
  return { ...draft, awaiting: null, fields: { ...draft.fields, [field]: {
    raw: choice === "saved" ? source[field] : draft.fields[field].raw, base: source[field], baseRevision: project.revision,
  } } };
}
function authoredTime(raw: string): RationalTime {
  const value = parseAudioNumber(raw);
  if (value < 0 || !Number.isSafeInteger(Math.round(value * 1000))) throw new Error("Manual timing must be non-negative and fit exact milliseconds.");
  return rationalSeconds(value);
}
const timeEqual = (left: RationalTime, right: RationalTime) => BigInt(left.num) * BigInt(right.den) === BigInt(right.num) * BigInt(left.den);
const timeBefore = (left: RationalTime, right: RationalTime) => BigInt(left.num) * BigInt(right.den) < BigInt(right.num) * BigInt(left.den);
const bytes = (value: string) => new TextEncoder().encode(value).byteLength;
function bounded(raw: string, max: number, required: boolean, name: string) {
  const value = raw.trim();
  if ((required && !value) || bytes(value) > max) throw new Error(name + " is empty or exceeds its byte limit.");
  return value;
}
export function prepareAudioSubmission(input: AudioDraft, project: Project): AudioSubmission {
  assertScope(input, project);
  if (input.awaiting) throw new Error("The previous save is not confirmed. Refresh the saved project and review its result before another save.");
  const draft = reconcileAudioDraft(input, project), { group } = draft;
  if (!audioCanonicalFields(project, group)) throw new Error("The edited source was removed; its draft was kept.");
  if (audioConflicts(draft, project).length) throw new Error("Resolve the overlapping audio fields before saving.");
  if (!Object.values(draft.fields).some(audioFieldHasInput)) throw new Error("There are no pending audio inputs.");
  const raw = Object.fromEntries(audioFields(group).map(field => [field, draft.fields[field].raw]));
  let change: AudioSubmission["change"];
  if (group.kind === "active-voice") {
    if (project.production_design?.narration_take_lock
      || project.locks.some(lock => lock.resource === "project:" + project.id && lock.kind === "content")) throw new Error("Voice selection is protected.");
    if (!uuid.test(raw.trackId) || !project.audio.voice_tracks.some(track => track.id === raw.trackId)) throw new Error("Select an existing measured voice.");
    change = { type: "set_active_voice_track", track_id: raw.trackId };
  } else if (group.kind === "mix") {
    const float = (key: string, optional = false) => {
      if (optional && !raw[key].trim()) return null;
      const value = parseAudioNumber(raw[key]);
      if (!Number.isFinite(Math.fround(value))) throw new Error("Mix values must fit the canonical finite audio range.");
      if (!optional && (value < -120 || value > 24)) throw new Error("Mix gains must be between -120 and 24 dB.");
      return value;
    };
    change = { type: "set_mix_intent", mix: { voice_gain_db: float("voiceGain")!, music_gain_db: float("musicGain")!,
      target_lufs: float("targetLufs", true), target_true_peak_dbfs: float("targetPeak", true) } };
  } else if (group.kind === "transcript" || group.kind === "new-transcript") {
    const old = group.kind === "transcript" ? project.audio.transcript.find(segment => segment.id === group.id)! : null;
    if (!old && project.audio.transcript.some(segment => segment.id === draft.creationId)) throw new Error("The pending segment already exists in the saved project. Review that segment; this draft cannot silently overwrite it.");
    const voiceId = group.voiceId;
    if (!voiceId || !project.audio.voice_tracks.some(track => track.id === voiceId)) throw new Error("Select a measured voice before adding a transcript segment.");
    // Preserve exact existing rationals when only text/speaker changes. Manual time inputs use milliseconds.
    const start = old && !audioFieldHasInput(draft.fields.start) ? old.start : authoredTime(raw.start);
    const end = old && !audioFieldHasInput(draft.fields.end) ? old.end : authoredTime(raw.end);
    if (!timeBefore(start, end)) throw new Error("Transcript end must be after its start at millisecond precision.");
    change = { type: "upsert_transcript_segment", segment: { id: old?.id ?? draft.creationId, voice_track_id: voiceId,
      start: structuredClone(start), end: structuredClone(end), text: bounded(raw.text, 8000, true, "Transcript"),
      speaker: bounded(raw.speaker, 256, false, "Speaker") || null, alignment: { kind: "manual" } } };
  } else {
    const old = group.kind === "cue" ? project.audio.cues.find(cue => cue.id === group.id)! : null;
    if (!old && project.audio.cues.some(cue => cue.id === draft.creationId)) throw new Error("The pending cue already exists in the saved project. Review that cue; this draft cannot silently overwrite it.");
    change = { type: "upsert_audio_cue", cue: { id: old?.id ?? draft.creationId,
      at: old && !audioFieldHasInput(draft.fields.at) ? structuredClone(old.at) : authoredTime(raw.at),
      label: bounded(raw.label, 512, true, "Cue label"), source_segment_id: old?.source_segment_id ?? null, evidence: "manual" } };
  }
  return { scope: draft.scope, revision: project.revision, raw, change,
    ...(group.kind === "active-voice" ? { selection: { intentId: crypto.randomUUID(), outcome: "UNKNOWN" as const, diagnostic: null } } : {}) };
}
export function retainAudioSubmission(draft: AudioDraft, submission: AudioSubmission): AudioDraft {
  if (draft.scope !== submission.scope) throw new Error("The submitted audio scope changed.");
  return { ...draft, awaiting: structuredClone(submission), fields: Object.fromEntries(Object.entries(draft.fields)
    .map(([key, value]) => [key, { ...value, ...(audioFieldHasInput(value) ? { intent: true as const } : {}) }])) };
}
export function audioSubmissionMatches(project: Project, group: AudioDraftGroup, attempt: AudioSubmission): boolean {
  if (audioDraftScope(project, group) !== attempt.scope || project.revision !== attempt.revision + 1) return false;
  const { change } = attempt;
  // Project equality, including r+1, cannot acknowledge a lost selection reply.
  if (change.type === "set_active_voice_track") return false;
  if (change.type === "set_mix_intent") return Object.keys(change.mix).every(key => {
    const field = key as keyof typeof change.mix, expected = change.mix[field], actual = project.audio.mix[field];
    return expected === null ? actual === null : actual !== null && Math.fround(expected) === Math.fround(actual);
  });
  if (change.type === "upsert_transcript_segment") {
    const expected = change.segment, actual = project.audio.transcript.find(item => item.id === expected.id);
    return !!actual && actual.voice_track_id === expected.voice_track_id && actual.text === expected.text
      && actual.speaker === expected.speaker && actual.alignment.kind === "manual"
      && timeEqual(actual.start, expected.start) && timeEqual(actual.end, expected.end);
  }
  const expected = change.cue, actual = project.audio.cues.find(item => item.id === expected.id);
  return !!actual && actual.label === expected.label && actual.source_segment_id === expected.source_segment_id
    && actual.evidence === "manual" && timeEqual(actual.at, expected.at);
}
export function observeAudioDraft(draft: AudioDraft, project: Project, nextCreationId = crypto.randomUUID()) {
  assertScope(draft, project);
  const attempt = draft.awaiting;
  if (!attempt || !audioSubmissionMatches(project, draft.group, attempt)) return { draft: reconcileAudioDraft(draft, project), acknowledged: false };
  const source = audioCanonicalFields(project, draft.group)!;
  const fields = Object.fromEntries(audioFields(draft.group).map(field => [field, {
    raw: draft.fields[field].raw === attempt.raw[field] ? source[field] : draft.fields[field].raw,
    base: source[field], baseRevision: project.revision,
  }]));
  return { draft: { ...draft, fields, awaiting: null, creationId: isNew(draft.group) ? nextCreationId : draft.creationId }, acknowledged: true };
}

export interface AudioRecovery { schema: "motionwright.audio-recovery/1"; scope: string; writer: string; updatedAt: number; draft: AudioDraft }
export interface AudioRecoveryCopy { key: string; raw: string; value: AudioRecovery | null; writer: string }
export const audioRecoveryKey = (scope: string, writer: string) => AUDIO_DRAFT_PREFIX + encodeURIComponent(scope) + ":" + writer;
function validStoredAudioTime(value: unknown): value is RationalTime {
  if (!value || typeof value !== "object") return false;
  const time = value as RationalTime;
  if (typeof time.num !== "string" || typeof time.den !== "string" || !/^-?\d{1,19}$/.test(time.num) || !/^\d{1,19}$/.test(time.den)) return false;
  return BigInt(time.num) >= 0n && BigInt(time.num) <= 9223372036854775807n && BigInt(time.den) > 0n && BigInt(time.den) <= 9223372036854775807n;
}
function validateStoredAudioAttempt(attempt: AudioSubmission, group: AudioDraftGroup, creationId: string) {
  const change = attempt.change;
  if (group.kind === "active-voice") {
    if (change.type !== "set_active_voice_track" || !uuid.test(change.track_id)
      || Object.keys(change).sort().join() !== "track_id,type"
      || Object.keys(attempt.raw).join() !== "trackId" || attempt.raw.trackId !== change.track_id
      || !attempt.selection || !uuid.test(attempt.selection.intentId)
      || !["UNKNOWN", "ACKNOWLEDGED"].includes(attempt.selection.outcome)
      || (attempt.selection.diagnostic !== null && typeof attempt.selection.diagnostic !== "string")) throw new Error("Invalid stored voice selection.");
    return;
  }
  if (attempt.selection !== undefined) throw new Error("Selection outcome is not valid for this audio group.");
  if (group.kind === "mix") {
    if (change.type !== "set_mix_intent" || !change.mix || Object.keys(change.mix).sort().join() !== "music_gain_db,target_lufs,target_true_peak_dbfs,voice_gain_db") throw new Error("Invalid stored mix snapshot.");
    for (const [key, value] of Object.entries(change.mix)) {
      if (key.startsWith("target_") && value === null) continue;
      if (typeof value !== "number" || !Number.isFinite(Math.fround(value)) || (key.endsWith("gain_db") && (value < -120 || value > 24))) throw new Error("Invalid stored mix value.");
    }
  } else if (group.kind === "transcript" || group.kind === "new-transcript") {
    if (change.type !== "upsert_transcript_segment" || !change.segment) throw new Error("Invalid stored transcript snapshot.");
    const segment = change.segment;
    if (segment.id !== (group.kind === "transcript" ? group.id : creationId) || !uuid.test(segment.id)
      || segment.voice_track_id !== group.voiceId || !uuid.test(segment.voice_track_id)
      || !validStoredAudioTime(segment.start) || !validStoredAudioTime(segment.end) || !timeBefore(segment.start, segment.end)
      || typeof segment.text !== "string" || !segment.text.trim() || bytes(segment.text) > 8000
      || (segment.speaker !== null && (typeof segment.speaker !== "string" || bytes(segment.speaker) > 256))
      || segment.alignment?.kind !== "manual") throw new Error("Invalid stored transcript values.");
  } else {
    if (change.type !== "upsert_audio_cue" || !change.cue) throw new Error("Invalid stored cue snapshot.");
    const cue = change.cue;
    if (cue.id !== (group.kind === "cue" ? group.id : creationId) || !uuid.test(cue.id) || !validStoredAudioTime(cue.at)
      || typeof cue.label !== "string" || !cue.label.trim() || bytes(cue.label) > 512
      || (cue.source_segment_id !== null && !uuid.test(cue.source_segment_id)) || cue.evidence !== "manual") throw new Error("Invalid stored cue values.");
  }
}
export function decodeAudioRecovery(raw: string, scope: string, group: AudioDraftGroup, writer: string): AudioRecovery {
  if (bytes(raw) > AUDIO_COPY_BYTES) throw new Error("Audio recovery copy exceeds its size bound.");
  const value = JSON.parse(raw) as AudioRecovery;
  if (!value || value.schema !== "motionwright.audio-recovery/1" || value.scope !== scope || value.writer !== writer
    || !uuid.test(writer) || !Number.isSafeInteger(value.updatedAt) || value.updatedAt < 0) throw new Error("Invalid audio recovery identity.");
  const draft = value.draft, keys = audioFields(group);
  if (!draft || draft.schema !== "motionwright.audio-draft/1" || draft.scope !== scope || !uuid.test(draft.creationId)
    || JSON.stringify(draft.group) !== JSON.stringify(group) || !draft.fields || Object.keys(draft.fields).sort().join() !== [...keys].sort().join()) throw new Error("Invalid audio recovery scope or fields.");
  for (const key of keys) {
    const field = draft.fields[key];
    if (!field || typeof field.raw !== "string" || typeof field.base !== "string" || !Number.isSafeInteger(field.baseRevision)
      || field.baseRevision < 0 || (field.intent !== undefined && field.intent !== true)) throw new Error("Invalid audio recovery field.");
  }
  if (draft.awaiting !== null) {
    const attempt = draft.awaiting;
    if (!attempt || attempt.scope !== scope || !Number.isSafeInteger(attempt.revision) || attempt.revision < 0
      || !attempt.raw || keys.some(key => typeof attempt.raw[key] !== "string")
      || !attempt.change || !["set_active_voice_track", "set_mix_intent", "upsert_transcript_segment", "upsert_audio_cue"].includes(attempt.change.type)) throw new Error("Invalid pending audio save.");
    validateStoredAudioAttempt(attempt, group, draft.creationId);
    // Decoding never authorizes this stored operation. It is used only for readback acknowledgement;
    // all new mutations are reconstructed from fields and the currently observed Project.
  }
  return value;
}
export function readAudioRecoveries(storage: Pick<Storage, "length" | "key" | "getItem">, scope: string, group: AudioDraftGroup) {
  const prefix = AUDIO_DRAFT_PREFIX + encodeURIComponent(scope) + ":", copies: AudioRecoveryCopy[] = [];
  let limited = false;
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index);
    if (!key?.startsWith(prefix)) continue;
    if (copies.length >= AUDIO_COPY_LIMIT) { limited = true; break; }
    const raw = storage.getItem(key); if (raw === null) continue;
    const writer = key.slice(prefix.length);
    let value: AudioRecovery | null = null;
    try { value = decodeAudioRecovery(raw, scope, group, writer); } catch { /* Preserve unrecognized bytes. */ }
    copies.push({ key, raw, value, writer });
  }
  return { copies, limited };
}
export function writeAudioRecovery(storage: Storage, recovery: AudioRecovery) {
  const key = audioRecoveryKey(recovery.scope, recovery.writer), raw = JSON.stringify(recovery);
  decodeAudioRecovery(raw, recovery.scope, recovery.draft.group, recovery.writer);
  const previous = storage.getItem(key);
  if (previous !== null) decodeAudioRecovery(previous, recovery.scope, recovery.draft.group, recovery.writer);
  if (!audioDraftHasWork(recovery.draft)) { if (previous !== null) storage.removeItem(key); return; }
  let count = 0, total = 0;
  for (let index = 0; index < storage.length; index++) {
    const other = storage.key(index);
    if (other?.startsWith(AUDIO_DRAFT_PREFIX) && other !== key) { count++; total += bytes(storage.getItem(other) ?? ""); }
  }
  if (count + 1 > AUDIO_COPY_LIMIT || total + bytes(raw) > AUDIO_TOTAL_BYTES) throw new Error("Audio recovery capacity is full. Existing drafts and unreadable copies were kept.");
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The audio recovery write could not be read back.");
}

export interface ActiveVoiceOutcomeReview {
  scope: string; attemptFingerprint: string; project: Project; events: ProjectEvent[];
  matchingValueRevisions: number[];
}
export const activeVoiceGroup: AudioDraftGroup = { kind: "active-voice" };
export const activeVoiceIsUnconfirmed = (draft: AudioDraft) =>
  draft.group.kind === "active-voice" && draft.awaiting?.selection?.outcome === "UNKNOWN";
export function prepareActiveVoiceSelection(project: Project, trackId: string, intentId: string = crypto.randomUUID()): AudioDraft {
  if (!uuid.test(intentId)) throw new Error("The local selection intent identity is invalid.");
  if (project.production_design?.narration_take_lock
    || project.locks.some(lock => lock.resource === "project:" + project.id && lock.kind === "content")) {
    throw new Error("The recorded source or project content is protected.");
  }
  if (!uuid.test(trackId) || !project.audio.voice_tracks.some(track => track.id === trackId)) {
    throw new Error("The selected measured voice is no longer in this project.");
  }
  if (project.audio.active_voice_track_id === trackId) throw new Error("This measured voice is already active.");
  const draft = editAudioField(createAudioDraft(project, activeVoiceGroup), "trackId", trackId);
  const submission = prepareAudioSubmission(draft, project);
  submission.selection = { intentId, outcome: "UNKNOWN", diagnostic: null };
  return retainAudioSubmission(draft, submission);
}
export function settleActiveVoiceSelection(
  draft: AudioDraft, attempt: AudioSubmission, outcome: "ACKNOWLEDGED" | "UNKNOWN", diagnostic: string | null,
): AudioDraft {
  const current = draft.awaiting;
  if (draft.group.kind !== "active-voice" || !current?.selection || !attempt.selection
    || current.scope !== attempt.scope || current.revision !== attempt.revision
    || current.selection.intentId !== attempt.selection.intentId
    || JSON.stringify(current.change) !== JSON.stringify(attempt.change)
    || JSON.stringify(current.raw) !== JSON.stringify(attempt.raw)) return draft;
  return { ...draft, awaiting: { ...current, selection: { ...current.selection, outcome, diagnostic } } };
}
export function retainActiveVoiceRecord(
  storage: Storage, session: Pick<Storage, "getItem" | "setItem">, pointerKey: string, record: AudioRecovery,
) {
  if (record.draft.group.kind !== "active-voice" || !record.draft.awaiting?.selection) throw new Error("A retained selection record is required.");
  writeAudioRecovery(storage, record);
  const key = audioRecoveryKey(record.scope, record.writer), raw = JSON.stringify(record);
  if (storage.getItem(key) !== raw) throw new Error("The selection record could not be verified before sending.");
  session.setItem(pointerKey, record.writer);
  if (session.getItem(pointerKey) !== record.writer || storage.getItem(key) !== raw) {
    throw new Error("The selection pointer or record could not be read back; no Project change was sent.");
  }
}
export interface ActiveVoiceReply {
  outcome: "NOT_SENT" | "ACKNOWLEDGED" | "UNKNOWN"; record: AudioRecovery;
  diagnostic: string | null; custodyError: string | null;
}
/** One call through the existing mutation coordinator. A local intent ID is
 * never the API request ID. Retain the original reply before later custody. */
export async function sendRetainedActiveVoiceSelection(
  storage: Storage, session: Pick<Storage, "getItem" | "setItem">, pointerKey: string,
  record: AudioRecovery, commit: (change: Change) => Promise<void>,
  preserve: (record: AudioRecovery) => void,
): Promise<ActiveVoiceReply> {
  const message = (error: unknown): string => {
    try {
      const value: unknown = error instanceof Error ? error.message : error;
      return typeof value === "string" ? value : String(value);
    } catch {
      return "The original diagnostic could not be displayed.";
    }
  };
  record = structuredClone(record);
  if (!activeVoiceIsUnconfirmed(record.draft)) return {
    outcome: "NOT_SENT", record, diagnostic: "A new selection requires its retained uncertain attempt.", custodyError: null,
  };
  try { retainActiveVoiceRecord(storage, session, pointerKey, record); }
  catch (error) {
    const diagnostic = message(error);
    const retained = { ...record, draft: settleActiveVoiceSelection(record.draft, record.draft.awaiting!, "UNKNOWN", "NOT_SENT: " + diagnostic) };
    const failures: string[] = [];
    try { preserve(retained); } catch (secondary) { failures.push(message(secondary)); }
    try { writeAudioRecovery(storage, retained); } catch (secondary) { failures.push(message(secondary)); }
    return { outcome: "NOT_SENT", record: retained, diagnostic, custodyError: failures.length ? failures.join(" · ") : null };
  }
  let outcome: "ACKNOWLEDGED" | "UNKNOWN", diagnostic: string | null = null;
  try { await commit(record.draft.awaiting!.change); outcome = "ACKNOWLEDGED"; }
  catch (error) { outcome = "UNKNOWN"; diagnostic = message(error); }
  const retained = { ...record, updatedAt: Math.max(record.updatedAt, Date.now()),
    draft: settleActiveVoiceSelection(record.draft, record.draft.awaiting!, outcome, diagnostic) };
  const custody: string[] = [];
  // A notification or storage error cannot replace this original outcome.
  try { preserve(retained); } catch (error) { custody.push(message(error)); }
  try { writeAudioRecovery(storage, retained); } catch (error) { custody.push(message(error)); }
  return { outcome, record: retained, diagnostic, custodyError: custody.length ? custody.join(" · ") : null };
}
export interface ActiveVoiceOutcomeTransport {
  readProject: (id: string) => Promise<Project>;
  readHistory: (project: Project, afterRevision: number, limit: number) => Promise<ProjectEvent[]>;
}
/** Read-only observation of the existing Project and journal. Their change
 * values cannot identify or acknowledge a request whose response was lost. */
export async function inspectActiveVoiceOutcome(
  draft: AudioDraft, selected: Project, transport: ActiveVoiceOutcomeTransport,
): Promise<ActiveVoiceOutcomeReview> {
  assertScope(draft, selected);
  if (!activeVoiceIsUnconfirmed(draft)) throw new Error("There is no unconfirmed voice selection to inspect.");
  const attempt = structuredClone(draft.awaiting!);
  const observed = structuredClone(await transport.readProject(selected.id));
  if (audioDraftScope(observed, activeVoiceGroup) !== draft.scope
    || JSON.stringify(observed) !== JSON.stringify(selected)) {
    throw new Error("The saved Project changed. Reload it before inspecting this selection.");
  }
  if (!Number.isSafeInteger(observed.revision) || observed.revision < attempt.revision
    || observed.revision - attempt.revision > 2048) throw new Error("The selection history range is unavailable or exceeds its review bound.");
  const events: ProjectEvent[] = [];
  let cursor = attempt.revision;
  do {
    const page = await transport.readHistory(observed, cursor, 256);
    if (!Array.isArray(page) || page.length > 256 || (!page.length && cursor < observed.revision)) {
      throw new Error("The selection history is incomplete.");
    }
    for (const event of page) {
      if (!event || !Number.isSafeInteger(event.revision) || event.revision !== cursor + 1
        || event.revision > observed.revision || !event.change || typeof event.change.type !== "string"
        || typeof event.created_at !== "string" || !event.created_at) {
        throw new Error("The saved history changed or is not a contiguous revision sequence.");
      }
      events.push(structuredClone(event)); cursor = event.revision;
    }
  } while (cursor < observed.revision);
  const after = await transport.readProject(selected.id);
  if (JSON.stringify(after) !== JSON.stringify(observed)) throw new Error("The saved Project changed during inspection. Inspect it again.");
  const trackId = attempt.change.type === "set_active_voice_track" ? attempt.change.track_id : null;
  return { scope: draft.scope, attemptFingerprint: JSON.stringify(attempt), project: observed, events,
    matchingValueRevisions: events.filter(event => event.change.type === "set_active_voice_track"
      && event.change.track_id === trackId).map(event => event.revision) };
}
export function activeVoiceReviewMatches(draft: AudioDraft, project: Project, review: ActiveVoiceOutcomeReview | null): boolean {
  return Boolean(review && activeVoiceIsUnconfirmed(draft) && review.scope === draft.scope
    && audioDraftScope(project, activeVoiceGroup) === draft.scope
    && review.attemptFingerprint === JSON.stringify(draft.awaiting)
    && JSON.stringify(review.project) === JSON.stringify(project));
}
/** The old record remains unchanged. Only an explicit later click can retain
 * and send a new selection. Reload before that click restores the old UNKNOWN. */
export function prepareSeparateActiveVoiceIntent(
  storage: Storage, record: AudioRecovery, project: Project, review: ActiveVoiceOutcomeReview,
): AudioDraft {
  if (!activeVoiceReviewMatches(record.draft, project, review)) throw new Error("Inspect this selection against the current saved Project first.");
  const key = audioRecoveryKey(record.scope, record.writer), raw = storage.getItem(key);
  if (raw === null) throw new Error("Retain the earlier selection record before starting another intent.");
  const decoded = decodeAudioRecovery(raw, record.scope, activeVoiceGroup, record.writer);
  if (JSON.stringify(decoded.draft) !== JSON.stringify(record.draft)) throw new Error("The earlier selection record changed. Inspect it again.");
  if (storage.getItem(key) !== raw) throw new Error("The earlier selection record changed during readback.");
  return createAudioDraft(project, activeVoiceGroup);
}
