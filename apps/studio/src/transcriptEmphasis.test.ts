import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { applyChange } from "./api";
import { captionTextRuns, decodeTranscriptEmphasis, encodeTranscriptEmphasis, entireTranscriptEmphasis, sameTranscriptEmphasis,
  toggleTranscriptWord, transcriptWords, trimTranscriptEmphasis, validateTranscriptEmphasis } from "./transcriptEmphasis";
import { audioConflicts, audioDraftScope, audioSubmissionMatches, createAudioDraft, decodeAudioRecovery, editAudioField,
  observeAudioDraft, prepareAudioSubmission, retainAudioSubmission } from "./audioDraft";
import { inspectCaptionReadability } from "./captionReadability";
import { sampleProgramCaption } from "./programCaption";
import type { AudioDraftGroup } from "./audioDraft";

const voiceId = "11111111-1111-4111-8111-111111111111", segmentId = "22222222-2222-4222-8222-222222222222";
const writer = "44444444-4444-4444-8444-444444444444";
function source() {
  const p = structuredClone(fixtureProject); p.locks = []; p.branch_workspaces = [];
  p.audio.voice_tracks = [{ id: voiceId, asset_id: p.assets[0].id, label: "Synthetic caption style fixture",
    sample_rate_hz: 48000, channels: 1, measured_duration: { num: "2", den: "1" },
    source_sha256: p.assets[0].content_sha256!, loudness_lufs: null, true_peak_dbfs: null }];
  p.audio.active_voice_track_id = voiceId; p.audio.cues = [];
  p.audio.transcript = [{ id: segmentId, voice_track_id: voiceId, start: { num: "1", den: "3" },
    end: { num: "5", den: "3" }, text: "Una decisión clara", speaker: "Author", alignment: { kind: "manual" } }];
  p.deliverables[0].voice_track_id = voiceId;
  return p;
}
const group: AudioDraftGroup = { kind: "transcript", id: segmentId, voiceId };
const mark = (text: string, index = 1) => toggleTranscriptWord(text, [], index);
const record = (draft: ReturnType<typeof createAudioDraft>) => JSON.stringify({
  schema: "motionwright.audio-recovery/1", scope: draft.scope, writer, updatedAt: 1000, draft,
});
describe("source-bound caption emphasis", () => {
  it("uses Unicode scalars, groups adjacent selected words and preserves all inner text", () => {
    const text = "  Año 👩‍🔧 très clair\nfin  ", words = transcriptWords(text);
    expect(words[1]).toEqual({ start: 6, end: 9, text: "👩‍🔧" });
    let selected = toggleTranscriptWord(text, [], 1); selected = toggleTranscriptWord(text, selected, 2);
    expect(selected).toEqual([{ start: 6, end: 14, text: "👩‍🔧 très" }]);
    expect(captionTextRuns(text, selected).map(run => run.text).join("")).toBe(text.trim());
    expect(captionTextRuns(text, selected).filter(run => run.emphasis)).toEqual([{ text: "👩‍🔧 très", emphasis: true }]);
    expect(toggleTranscriptWord(text, selected, 1)).toEqual([words[2]]);
    expect(trimTranscriptEmphasis(text, selected)[0].start).toBe(4);
    expect(captionTextRuns("\u0085mot\u0085", undefined)).toEqual([{ text: "mot", emphasis: false }]);
  });
  it("keeps complete-cue emphasis separate from word timing", () => {
    for (const text of ["An original thought", "Una decisión clara", "Une décision claire"]) {
      const ranges = entireTranscriptEmphasis(text);
      expect(ranges).toHaveLength(1);
      expect(captionTextRuns(text, ranges)).toEqual([{ text, emphasis: true }]);
      expect(decodeTranscriptEmphasis(encodeTranscriptEmphasis(ranges), text)).toEqual(ranges);
    }
    expect(captionTextRuns("Original")).toEqual([{ text: "Original", emphasis: false }]);
    expect(sameTranscriptEmphasis("Original", undefined, [])).toBe(true);
  });
  it("rejects stale words, partial words, overlaps, controls, extras and a ninth range", () => {
    const text = "Una decisión clara", valid = mark(text)[0];
    for (const invalid of [[{ ...valid, text: "decisions" }], [{ ...valid, start: 5, text: "ecisión" }],
      [valid, valid], [{ ...valid, end: 100 }], [{ ...valid, start: -1 }], [{ ...valid, extra: true }], null]) {
      expect(() => validateTranscriptEmphasis(text, invalid)).toThrow("matching");
    }
    expect(() => validateTranscriptEmphasis("Una\r decisión", [{ start: 0, end: 3, text: "Una" }])).toThrow();
    const words = Array.from({ length: 17 }, (_, index) => "word" + index).join(" ");
    expect(() => validateTranscriptEmphasis(words, transcriptWords(words).filter((_, index) => index % 2 === 0))).toThrow();
  });
  it("rejects a stale style atomically through the existing change consumer", async () => {
    const p = source(), original = structuredClone(p), segment = { ...p.audio.transcript[0], emphasis: mark("Una decisión clara") };
    segment.text = "Otra decisión clara";
    await expect(applyChange(p, { type: "upsert_transcript_segment", segment })).rejects.toThrow("matching");
    expect(p).toEqual(original);
  });
  it("retains a text/style conflict until an explicit reselection and keeps rational time", () => {
    const p = source(); p.audio.transcript[0].emphasis = mark(p.audio.transcript[0].text);
    let draft = editAudioField(createAudioDraft(p, group), "text", "Otra decisión clara");
    expect(() => prepareAudioSubmission(draft, p)).toThrow("matching");
    const restored = decodeAudioRecovery(record(draft), draft.scope, group, writer).draft;
    expect(restored.fields.text.raw).toBe("Otra decisión clara");
    draft = editAudioField(restored, "emphasis", encodeTranscriptEmphasis(mark(restored.fields.text.raw)));
    expect(prepareAudioSubmission(draft, p).change).toMatchObject({ segment: {
      text: "Otra decisión clara", emphasis: [{ start: 5, end: 13, text: "decisión" }],
      start: { num: "1", den: "3" }, end: { num: "5", den: "3" },
    } });
  });
  it("requires the saved style for ACK and keeps a later clear through a late reply", async () => {
    const p = source(); let draft = editAudioField(createAudioDraft(p, group), "emphasis", encodeTranscriptEmphasis(mark(p.audio.transcript[0].text)));
    const attempt = prepareAudioSubmission(draft, p);
    draft = editAudioField(retainAudioSubmission(draft, attempt), "emphasis", "");
    expect(() => prepareAudioSubmission(draft, p)).toThrow("not confirmed");
    const restored = decodeAudioRecovery(record(draft), draft.scope, group, writer).draft;
    const saved = await applyChange(p, attempt.change), mismatched = structuredClone(saved);
    delete mismatched.audio.transcript[0].emphasis;
    expect(audioSubmissionMatches(mismatched, group, attempt)).toBe(false);
    expect(audioSubmissionMatches(saved, group, attempt)).toBe(true);
    const observed = observeAudioDraft(restored, saved);
    expect(observed.acknowledged).toBe(true); expect(observed.draft.fields.emphasis.raw).toBe("");
    const clear = prepareAudioSubmission(observed.draft, saved);
    expect(clear.change.type === "upsert_transcript_segment" && clear.change.segment.emphasis).toBeUndefined();
    const reopened = JSON.parse(JSON.stringify(saved));
    expect(reopened.audio.transcript[0]).toEqual(saved.audio.transcript[0]);
  });
  it("migrates only neutral legacy recovery fields and preserves an unconfirmed attempt", () => {
    const p = source(), draft = editAudioField(createAudioDraft(p, group), "speaker", "Author two");
    const attempt = prepareAudioSubmission(draft, p), retained = retainAudioSubmission(draft, attempt);
    const legacy = JSON.parse(record(retained)); delete legacy.draft.fields.emphasis; delete legacy.draft.awaiting.raw.emphasis;
    const restored = decodeAudioRecovery(JSON.stringify(legacy), audioDraftScope(p, group), group, writer);
    expect(restored.draft.fields.emphasis).toEqual({ raw: "", base: "", baseRevision: p.revision });
    expect(restored.draft.awaiting?.change).toEqual(attempt.change);
    expect(() => prepareAudioSubmission(restored.draft, p)).toThrow("not confirmed");
    legacy.draft.awaiting.change.segment.emphasis = mark(p.audio.transcript[0].text);
    expect(() => decodeAudioRecovery(JSON.stringify(legacy), draft.scope, group, writer)).toThrow("legacy");
  });
  it("does not overwrite another writer's style when other fields are retained", () => {
    const p = source();
    let draft = editAudioField(createAudioDraft(p, group), "emphasis", encodeTranscriptEmphasis(mark(p.audio.transcript[0].text)));
    draft = editAudioField(draft, "speaker", "Retain this speaker");
    const next = structuredClone(p); next.revision++; next.audio.transcript[0].emphasis = entireTranscriptEmphasis(next.audio.transcript[0].text);
    expect(audioConflicts(draft, next).map(item => item.field)).toEqual(["emphasis"]);
    expect(() => prepareAudioSubmission(draft, next)).toThrow("overlapping");
    expect(draft.fields.speaker.raw).toBe("Retain this speaker");
  });
  it("negotiates SubRip loss only for selected cues and reports stale ranges before export", () => {
    const p = source(), profile = p.deliverables[0]; p.audio.transcript[0].emphasis = mark(p.audio.transcript[0].text);
    expect(inspectCaptionReadability(p, profile).findings.some(f => f.code.startsWith("EMPHASIS_"))).toBe(false);
    profile.caption_format = "srt";
    expect(inspectCaptionReadability(p, profile).findings.some(f => f.code === "EMPHASIS_FORMAT_UNSUPPORTED")).toBe(true);
    profile.included_scene_ids = [p.scenes[1].id];
    expect(inspectCaptionReadability(p, profile).findings.some(f => f.code.startsWith("EMPHASIS_"))).toBe(false);
    profile.included_scene_ids = []; profile.caption_format = "web_vtt"; p.audio.transcript[0].text = "Otra decisión clara";
    expect(inspectCaptionReadability(p, profile).findings.some(f => f.code === "EMPHASIS_SOURCE_CONFLICT")).toBe(true);
  });
  it("samples the saved cue and style on the shared source clock with an exclusive end", () => {
    const p = source(), scene = p.scenes[0], profile = p.deliverables[0];
    p.audio.transcript[0].emphasis = mark(p.audio.transcript[0].text);
    const before = structuredClone(p);
    expect(sampleProgramCaption(p, scene, 0, profile)?.cue).toBeNull();
    expect(sampleProgramCaption(p, scene, 1 / 3, profile)?.runs.filter(run => run.emphasis)).toEqual([{ text: "decisión", emphasis: true }]);
    expect(sampleProgramCaption(p, scene, 5 / 3, profile)?.cue).toBeNull();
    profile.included_scene_ids = [p.scenes[1].id];
    expect(sampleProgramCaption(p, scene, 1, profile)).toBeNull();
    profile.included_scene_ids = []; expect(p).toEqual(before);
  });
  it("refuses overlapping, UNKNOWN and stale-source cues rather than choosing a caption", () => {
    const p = source(), scene = p.scenes[0], profile = p.deliverables[0];
    p.audio.transcript.push({ ...p.audio.transcript[0], id: writer });
    expect(sampleProgramCaption(p, scene, 1, profile)?.issue).toContain("overlap"); p.audio.transcript.pop();
    p.audio.transcript[0].alignment = { kind: "unknown" };
    expect(sampleProgramCaption(p, scene, 1, profile)?.issue).toContain("timing");
    p.audio.transcript[0].alignment = { kind: "measured", engine: "fixture", source_sha256: "b".repeat(64), confidence_millis: null };
    expect(sampleProgramCaption(p, scene, 1, profile)?.issue).toContain("different");
    profile.burn_in_captions = true; profile.voice_track_id = null;
    expect(sampleProgramCaption(p, scene, 1, profile)?.issue).toContain("Select");
  });
});
