import type { Change, Project, RationalTime } from "./types";
import { rationalSeconds } from "./types";

/** Editor buffers only. The Project, service grants and SQLite CAS retain authority. */
export type AudioDraftGroup =
  | { kind: "mix" }
  | { kind: "transcript"; id: string; voiceId: string }
  | { kind: "cue"; id: string }
  | { kind: "new-transcript"; voiceId: string | null }
  | { kind: "new-cue" };
export interface AudioFieldDraft { raw: string; base: string; baseRevision: number; intent?: true }
export interface AudioSubmission {
  scope: string; revision: number; raw: Record<string, string>;
  change: Extract<Change, { type: "set_mix_intent" | "upsert_transcript_segment" | "upsert_audio_cue" }>;
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
  text: "Transcript", at: "Cue time", label: "Cue label",
};
export const audioGroupName = (group: AudioDraftGroup) => group.kind === "mix" ? "Mix intent"
  : group.kind === "new-transcript" ? "New transcript" : group.kind === "new-cue" ? "New cue"
  : group.kind === "transcript" ? "Transcript " + group.id : "Cue " + group.id;
export const audioFields = (group: AudioDraftGroup): string[] => group.kind === "mix"
  ? ["voiceGain", "musicGain", "targetLufs", "targetPeak"]
  : group.kind === "transcript" || group.kind === "new-transcript" ? ["start", "end", "speaker", "text"]
  : ["at", "label"];
export const audioDraftScope = (project: Project, group: AudioDraftGroup) =>
  JSON.stringify([project.id, project.generation, project.active_branch, group]);
const seconds = (time: RationalTime) => String(Number(time.num) / Number(time.den));
const isNew = (group: AudioDraftGroup) => group.kind.startsWith("new-");
export function audioCanonicalFields(project: Project, group: AudioDraftGroup): Record<string, string> | null {
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
  if (["text", "speaker", "label"].includes(field)) return raw.trim();
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
  if (group.kind === "mix") {
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
  return { scope: draft.scope, revision: project.revision, raw, change };
}
export function retainAudioSubmission(draft: AudioDraft, submission: AudioSubmission): AudioDraft {
  if (draft.scope !== submission.scope) throw new Error("The submitted audio scope changed.");
  return { ...draft, awaiting: structuredClone(submission), fields: Object.fromEntries(Object.entries(draft.fields)
    .map(([key, value]) => [key, { ...value, ...(audioFieldHasInput(value) ? { intent: true as const } : {}) }])) };
}
export function audioSubmissionMatches(project: Project, group: AudioDraftGroup, attempt: AudioSubmission): boolean {
  if (audioDraftScope(project, group) !== attempt.scope || project.revision !== attempt.revision + 1) return false;
  const { change } = attempt;
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
      || !attempt.change || !["set_mix_intent", "upsert_transcript_segment", "upsert_audio_cue"].includes(attempt.change.type)) throw new Error("Invalid pending audio save.");
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
