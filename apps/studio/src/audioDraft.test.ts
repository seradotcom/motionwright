import { describe, expect, it } from "vitest";
import { fixtureBootstrap } from "./fixture";
import type { Project } from "./types";
import {
  AUDIO_COPY_LIMIT, AUDIO_DRAFT_PREFIX, audioConflicts, audioDraftHasWork, audioDraftScope,
  audioRecoveryKey, audioSubmissionMatches, createAudioDraft, decodeAudioRecovery, editAudioField,
  observeAudioDraft, parseAudioNumber, prepareAudioSubmission, readAudioRecoveries,
  reconcileAudioDraft, resolveAudioField, retainAudioSubmission, writeAudioRecovery,
} from "./audioDraft";
import type { AudioDraft, AudioDraftGroup, AudioRecovery, AudioSubmission } from "./audioDraft";

const voiceId = "11111111-1111-4111-8111-111111111111", segmentId = "22222222-2222-4222-8222-222222222222";
const cueId = "33333333-3333-4333-8333-333333333333", writer = "44444444-4444-4444-8444-444444444444";
function project(): Project {
  const value = structuredClone(fixtureBootstrap.project); value.locks = [];
  value.audio.voice_tracks = [{ id: voiceId, asset_id: value.assets[0]?.id ?? voiceId, label: "Unit fixture only",
    sample_rate_hz: 48000, channels: 1, measured_duration: { num: "2", den: "1" }, source_sha256: "a".repeat(64), loudness_lufs: null, true_peak_dbfs: null }];
  value.audio.active_voice_track_id = voiceId;
  value.audio.transcript = [{ id: segmentId, voice_track_id: voiceId, start: { num: "1", den: "3" }, end: { num: "5", den: "3" }, text: "Original", speaker: "Narrator", alignment: { kind: "unknown" } }];
  value.audio.cues = [{ id: cueId, at: { num: "1", den: "3" }, label: "Original cue", source_segment_id: segmentId, evidence: "transcript_aligned" }];
  return value;
}
const mix: AudioDraftGroup = { kind: "mix" }, segment: AudioDraftGroup = { kind: "transcript", id: segmentId, voiceId }, cue: AudioDraftGroup = { kind: "cue", id: cueId };
function saved(source: Project, attempt: AudioSubmission) {
  const value = structuredClone(source); value.revision++;
  const change = attempt.change;
  if (change.type === "set_mix_intent") value.audio.mix = structuredClone(change.mix);
  else if (change.type === "upsert_transcript_segment") value.audio.transcript = [...value.audio.transcript.filter(item => item.id !== change.segment.id), structuredClone(change.segment)];
  else value.audio.cues = [...value.audio.cues.filter(item => item.id !== change.cue.id), structuredClone(change.cue)];
  return value;
}
function recovery(draft: AudioDraft): AudioRecovery { return { schema: "motionwright.audio-recovery/1", scope: draft.scope, writer, updatedAt: 1000, draft }; }
class MemoryStorage implements Storage {
  data = new Map<string, string>();
  get length() { return this.data.size; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
  removeItem(key: string) { this.data.delete(key); }
  clear() { this.data.clear(); }
}

describe("audio draft field preservation (unit; no service or native claim)", () => {
  it.each(["", " ", "Infinity", "-Infinity", "NaN", "0x10", "1_000", "3,5", "3 seconds", "--2", "1e309"])("rejects invalid decimal input %j", raw => expect(() => parseAudioNumber(raw)).toThrow());
  it.each(["0", "-12.5", ".25", "1.", "+2", "1e-3", "  3.0  "])("accepts finite decimal input %j", raw => expect(parseAudioNumber(raw)).toBe(Number(raw)));
  it("preserves dirty fields and refreshes only untouched fields through multiple revisions", () => {
    const p = project(); let draft = editAudioField(createAudioDraft(p, mix), "voiceGain", "-3.");
    const next = structuredClone(p); next.revision++; next.audio.mix.music_gain_db = -18;
    draft = reconcileAudioDraft(draft, next);
    expect(draft.fields.voiceGain.raw).toBe("-3."); expect(draft.fields.musicGain.raw).toBe("-18");
    next.revision++; next.title = "Unrelated"; draft = reconcileAudioDraft(draft, next);
    expect(draft.fields.voiceGain.raw).toBe("-3."); expect(audioConflicts(draft, next)).toEqual([]);
    expect(prepareAudioSubmission(draft, next).change).toMatchObject({ mix: { voice_gain_db: -3, music_gain_db: -18 } });
  });
  it.each(["saved", "draft"] as const)("resolves only the conflicting field with %s", choice => {
    const p = project(); let draft = editAudioField(createAudioDraft(p, mix), "voiceGain", "-3");
    draft = editAudioField(draft, "targetLufs", "-18"); const next = structuredClone(p); next.revision++; next.audio.mix.voice_gain_db = -9;
    expect(audioConflicts(draft, next).map(item => item.field)).toEqual(["voiceGain"]);
    expect(() => prepareAudioSubmission(draft, next)).toThrow("overlapping");
    draft = resolveAudioField(draft, next, "voiceGain", choice);
    expect(draft.fields.voiceGain.raw).toBe(choice === "saved" ? "-9" : "-3"); expect(draft.fields.targetLufs.raw).toBe("-18"); expect(audioConflicts(draft, next)).toEqual([]);
  });
  it.each([mix, segment, cue])("keeps Old → Sent → Old until canonical ACK in $kind", group => {
    const p = project(), field = group.kind === "mix" ? "voiceGain" : group.kind === "transcript" ? "text" : "label";
    const initial = createAudioDraft(p, group), old = initial.fields[field].raw, sent = group.kind === "mix" ? "-7" : "Submitted";
    let draft = editAudioField(initial, field, sent); const attempt = prepareAudioSubmission(draft, p);
    draft = retainAudioSubmission(draft, attempt); draft = editAudioField(draft, field, old);
    const before = reconcileAudioDraft(draft, p); expect(before.fields[field].raw).toBe(old); expect(audioDraftHasWork(before)).toBe(true);
    const final = saved(p, attempt), result = observeAudioDraft(before, final);
    expect(result.acknowledged).toBe(true); expect(result.draft.fields[field].raw).toBe(old); expect(result.draft.awaiting).toBeNull();
    expect(prepareAudioSubmission(result.draft, final)).toMatchObject({ revision: final.revision });
  });
  it("does not confuse a resolved callback or another revision/generation/branch with ACK", () => {
    const p = project(), draft = editAudioField(createAudioDraft(p, mix), "voiceGain", "-7"), attempt = prepareAudioSubmission(draft, p);
    expect(audioSubmissionMatches(p, mix, attempt)).toBe(false);
    for (const mutate of [(q: Project) => q.revision++, (q: Project) => { q.generation = crypto.randomUUID(); }, (q: Project) => { q.active_branch = "other"; }]) {
      const q = saved(p, attempt); mutate(q); expect(audioSubmissionMatches(q, mix, attempt)).toBe(false);
    }
  });
  it("acknowledges canonical f32 mix precision", () => {
    const p = project(), draft = editAudioField(createAudioDraft(p, mix), "voiceGain", "0.123456789"), attempt = prepareAudioSubmission(draft, p);
    const q = saved(p, attempt); q.audio.mix.voice_gain_db = Math.fround(q.audio.mix.voice_gain_db);
    expect(audioSubmissionMatches(q, mix, attempt)).toBe(true);
  });
  it.each([segment, cue])("preserves exact 1/3 rational times in $kind text-only edits", group => {
    const p = project(), field = group.kind === "transcript" ? "text" : "label";
    const attempt = prepareAudioSubmission(editAudioField(createAudioDraft(p, group), field, "Changed words"), p);
    if (attempt.change.type === "upsert_transcript_segment") { expect(attempt.change.segment.start).toEqual({ num: "1", den: "3" }); expect(attempt.change.segment.end).toEqual({ num: "5", den: "3" }); }
    else if (attempt.change.type === "upsert_audio_cue") { expect(attempt.change.cue.at).toEqual({ num: "1", den: "3" }); expect(attempt.change.cue.source_segment_id).toBe(segmentId); }
    else throw new Error("Unexpected unit fixture change");
  });
  it("keeps invalid raw timing without manufacturing a zero or a valid interval", () => {
    const p = project(); let draft = editAudioField(createAudioDraft(p, segment), "start", " ");
    expect(() => prepareAudioSubmission(draft, p)).toThrow("blank");
    draft = editAudioField(draft, "start", "1.0001"); draft = editAudioField(draft, "end", "1.0002");
    expect(() => prepareAudioSubmission(draft, p)).toThrow("millisecond");
  });
  it.each(["1e100", "-121", "25"])("rejects mix value beyond canonical bounds %s", raw => {
    const p = project(); expect(() => prepareAudioSubmission(editAudioField(createAudioDraft(p, mix), "voiceGain", raw), p)).toThrow();
  });
  it("keeps a removed source draft without recreating it", () => {
    const p = project(), draft = editAudioField(createAudioDraft(p, segment), "text", "Retain these words"); const next = structuredClone(p); next.revision++; next.audio.transcript = [];
    expect(reconcileAudioDraft(draft, next)).toEqual(draft); expect(() => prepareAudioSubmission(draft, next)).toThrow("removed"); expect(audioConflicts(draft, next)[0].current).toBe("(removed)");
  });
  it("new cue clears only acknowledged input and rotates identity while retaining newer typing", () => {
    const p = project(), group: AudioDraftGroup = { kind: "new-cue" };
    let draft = createAudioDraft(p, group); const oldId = draft.creationId;
    draft = editAudioField(editAudioField(draft, "at", "1"), "label", "Submitted cue"); const attempt = prepareAudioSubmission(draft, p);
    draft = editAudioField(retainAudioSubmission(draft, attempt), "label", "Next cue");
    const result = observeAudioDraft(draft, saved(p, attempt));
    expect(result.acknowledged).toBe(true); expect(result.draft.fields.at.raw).toBe(""); expect(result.draft.fields.label.raw).toBe("Next cue"); expect(result.draft.creationId).not.toBe(oldId);
  });
  it("an unconfirmed new cue cannot silently overwrite its existing stable id", () => {
    const p = project(); let draft = createAudioDraft(p, { kind: "new-cue" }); draft = editAudioField(editAudioField(draft, "at", "1"), "label", "Submitted");
    const attempt = prepareAudioSubmission(draft, p), next = saved(p, attempt); next.revision++;
    draft = { ...retainAudioSubmission(draft, attempt), awaiting: null };
    expect(() => prepareAudioSubmission(draft, next)).toThrow("already exists");
  });
  it("separates new transcript drafts by voice, project generation and branch", () => {
    const p = project(), a = audioDraftScope(p, { kind: "new-transcript", voiceId }), q = structuredClone(p); q.generation = crypto.randomUUID();
    expect(a).not.toBe(audioDraftScope(q, { kind: "new-transcript", voiceId }));
    expect(a).not.toBe(audioDraftScope(p, { kind: "new-transcript", voiceId: crypto.randomUUID() }));
    expect(() => reconcileAudioDraft(createAudioDraft(p, mix), q)).toThrow("another project");
  });
});

describe("bounded audio recovery", () => {
  it("roundtrips pending snapshot and newer raw input without replay", () => {
    const p = project(); let draft = editAudioField(createAudioDraft(p, mix), "voiceGain", "-3");
    draft = editAudioField(retainAudioSubmission(draft, prepareAudioSubmission(draft, p)), "voiceGain", "0");
    const storage = new MemoryStorage(), value = recovery(draft); writeAudioRecovery(storage, value);
    expect(readAudioRecoveries(storage, draft.scope, mix).copies[0].value).toEqual(value);
    expect(() => prepareAudioSubmission(readAudioRecoveries(storage, draft.scope, mix).copies[0].value!.draft, p)).toThrow("previous save");
  });
  it("preserves corrupt owned bytes and refuses an overwrite", () => {
    const p = project(), draft = editAudioField(createAudioDraft(p, mix), "voiceGain", "-3"), storage = new MemoryStorage(), key = audioRecoveryKey(draft.scope, writer);
    storage.setItem(key, "{broken"); expect(() => writeAudioRecovery(storage, recovery(draft))).toThrow();
    expect(storage.getItem(key)).toBe("{broken"); expect(readAudioRecoveries(storage, draft.scope, mix).copies[0].value).toBeNull();
  });
  it("removes only this writer's clean acknowledged buffer", () => {
    const p = project(), draft = editAudioField(createAudioDraft(p, mix), "voiceGain", "-3"), storage = new MemoryStorage();
    writeAudioRecovery(storage, recovery(draft)); const other = { ...recovery(draft), writer: crypto.randomUUID() }; writeAudioRecovery(storage, other);
    writeAudioRecovery(storage, recovery(createAudioDraft(p, mix)));
    expect(storage.length).toBe(1); expect(storage.getItem(audioRecoveryKey(other.scope, other.writer))).not.toBeNull();
  });
  it("keeps all existing dirty and unreadable copies at capacity", () => {
    const p = project(), draft = editAudioField(createAudioDraft(p, mix), "voiceGain", "-3"), storage = new MemoryStorage();
    for (let i = 0; i < AUDIO_COPY_LIMIT; i++) storage.setItem(AUDIO_DRAFT_PREFIX + "foreign-" + i, "unreadable " + i);
    expect(() => writeAudioRecovery(storage, recovery(draft))).toThrow("capacity"); expect(storage.length).toBe(AUDIO_COPY_LIMIT);
  });
  it("reports a write that cannot be read back", () => {
    const p = project(), draft = editAudioField(createAudioDraft(p, mix), "voiceGain", "-3"), storage = new MemoryStorage(); storage.setItem = () => {};
    expect(() => writeAudioRecovery(storage, recovery(draft))).toThrow("read back");
  });
  it.each(["missing mix", "foreign change", "wrong revision", "wrong writer", "bad fields", "wrong group"])("rejects malformed recovery: %s", mode => {
    const p = project(); let draft = editAudioField(createAudioDraft(p, mix), "voiceGain", "-3"); draft = retainAudioSubmission(draft, prepareAudioSubmission(draft, p));
    const value = recovery(draft) as any;
    if (mode === "missing mix") delete value.draft.awaiting.change.mix;
    if (mode === "foreign change") value.draft.awaiting.change = { type: "upsert_audio_cue", cue: {} };
    if (mode === "wrong revision") value.draft.awaiting.revision = -1;
    if (mode === "wrong writer") value.writer = crypto.randomUUID();
    if (mode === "bad fields") value.draft.fields.voiceGain.raw = {};
    if (mode === "wrong group") value.draft.group = { kind: "new-cue" };
    expect(() => decodeAudioRecovery(JSON.stringify(value), draft.scope, mix, writer)).toThrow();
  });
});
