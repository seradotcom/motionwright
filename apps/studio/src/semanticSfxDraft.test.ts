import { afterEach, describe, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import type { Project, ProjectEvent } from "./types";
import { SFX_CUE_SCHEMA } from "./semanticSfx";
import {
  acknowledgeSfxDraft, assertSfxOrigins, createSfxDraft, decodeSfxCopy, editSfxDraft,
  encodeSfxCopy, inspectSfxOutcome, parseSfxRaw, prepareSfxSubmission,
  readSfxCatalogue, readSfxPointer, reconcileSfxDraft, recoverSfxRead,
  resolveSfxDraft, retainSfxFailure, retainSfxOrigin, separateSfxIntent,
  sfxChange, sfxConflict, sfxCopyKey, sfxLocked, sfxOutcomeMatches,
  sfxPointerKey, sfxScope, writeSfxCopy, MAX_SFX_COPY_BYTES,
} from "./semanticSfxDraft";
import type { SfxCue, SfxCustody, SfxDraft, SfxStorage } from "./semanticSfxDraft";

// Synthetic source-contract tests. No browser, service, SQLite or PCM evidence.
const id = (n: number) => "00000000-0000-4000-8000-" + n.toString(16).padStart(12, "0");
const writer = id(71), previousWriter = id(72), attempt = id(73), source = id(74);
const cueId = id(75), otherCue = id(76), sha = "a".repeat(64);
function ready(): Project {
  const project = structuredClone(fixtureProject);
  project.locks = [];
  project.scenes = project.scenes.slice(0, 2);
  project.scenes.forEach((scene, index) => {
    scene.start = { num: String(index), den: "1" };
    scene.duration = { num: "1", den: "1" }; scene.beats = [];
  });
  project.assets.push({ id: source, name: "Synthetic source.wav", media_type: "audio/wav",
    content_sha256: sha, source_revision: "synthetic-unit-only" });
  const cue: SfxCue = { schema: SFX_CUE_SCHEMA, id: cueId, scene_id: project.scenes[0].id,
    event: "ui_focus", anchor: { kind: "scene_start" }, offset_frames: 2,
    source_asset_id: source, source_sha256: sha, source_frames: 20,
    gain_milli_db: -12000, attack_frames: 2, release_frames: 4,
    original_source_attestation: "Synthetic source contract only" };
  project.audio.sfx_cues = [cue, { ...structuredClone(cue), id: otherCue, scene_id: project.scenes[1].id }];
  return project;
}
const changed = (p: Project) => editSfxDraft(createSfxDraft(p, cueId), { gainMilliDb: "-9000" });
function response(p: Project, draft: SfxDraft): Project {
  const next = structuredClone(p), change = draft.pending!.change;
  next.revision++;
  if (change.type === "remove_semantic_sfx") next.audio.sfx_cues = next.audio.sfx_cues!.filter(cue => cue.id !== change.cue_id);
  else {
    const index = next.audio.sfx_cues!.findIndex(cue => cue.id === change.cue.id);
    if (index < 0) next.audio.sfx_cues!.push(structuredClone(change.cue));
    else next.audio.sfx_cues![index] = structuredClone(change.cue);
  }
  return next;
}
class Memory implements SfxStorage {
  values = new Map<string, string>();
  writes: [string, string][] = [];
  mode: "normal" | "drop" | "throw" | "write-then-throw" = "normal";
  afterWrite?: (key: string, value: string) => void;
  get length() { return this.values.size; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) {
    this.writes.push([key, value]);
    if (this.mode === "throw") throw new Error("Injected publication failure");
    if (this.mode !== "drop") this.values.set(key, value);
    this.afterWrite?.(key, value);
    if (this.mode === "write-then-throw") throw new Error("Injected uncertain publication");
  }
}
const custody = (pointer: string | null = null): SfxCustody =>
  ({ writerId: writer, expectedRaw: null, expectedPointer: pointer });
afterEach(() => vi.restoreAllMocks());

describe("SFX raw intent and original response authority", () => {
  it.each(["1e", "", "-", "01", " 1"])("retains unfinished numeric text %j without coercion or admission", raw => {
    const p = ready(), draft = editSfxDraft(changed(p), { offsetFrames: raw, attestation: "   " });
    expect(draft.raw.offsetFrames).toBe(raw); expect(draft.raw.attestation).toBe("   ");
    expect(decodeSfxCopy(encodeSfxCopy(draft, writer, 1), draft.scope, writer).draft.raw).toEqual(draft.raw);
    expect(() => sfxChange(draft, p)).toThrow();
    expect(reconcileSfxDraft(draft, p)).toBe(draft);
  });
  it("binds edits to the stable cue and prevents moving its identity to another scene", () => {
    const p = ready(), draft = changed(p), change = sfxChange(draft, p);
    expect(change.type).toBe("upsert_semantic_sfx");
    if (change.type === "upsert_semantic_sfx") expect(change.cue.id).toBe(cueId);
    expect(() => sfxChange(editSfxDraft(draft, { sceneId: p.scenes[1].id }), p)).toThrow(/cannot move/);
    expect(() => sfxChange(createSfxDraft(p, cueId), p)).toThrow(/no changes/);
    expect(p.audio.sfx_cues!.map(cue => cue.id)).toEqual([cueId, otherCue]);
  });
  it("checks cue preimage independently from revision and preserves input on either conflict", () => {
    const p = ready(), draft = changed(p), changedCue = structuredClone(p), changedRevision = structuredClone(p);
    changedCue.audio.sfx_cues![0].source_frames++;
    changedRevision.revision++; changedRevision.scenes[0].name = "Peer edit";
    for (const other of [changedCue, changedRevision]) {
      expect(sfxConflict(draft, other)).toBe(true);
      expect(() => prepareSfxSubmission(draft, other, attempt)).toThrow(/Compare/);
      expect(reconcileSfxDraft(draft, other)).toBe(draft);
    }
    expect(draft.raw.gainMilliDb).toBe("-9000");
  });
  it("keeps source hash and envelope admission strict while allowing explicit cross-cut source intent", () => {
    const p = ready(), draft = changed(p);
    expect(() => sfxChange(editSfxDraft(draft, { sourceSha256: "b".repeat(64) }), p)).toThrow(/digest/);
    expect(() => sfxChange(editSfxDraft(draft, { attackFrames: "10", releaseFrames: "10" }), p)).toThrow(/envelope/);
    const raw = { ...draft.raw, offsetFrames: "15", sourceFrames: "30" };
    expect(() => parseSfxRaw(raw, p, cueId)).toThrow(/tail/);
    expect(parseSfxRaw({ ...raw, tailPolicy: "continue_across_cuts" }, p, cueId).tail_policy).toBe("continue_across_cuts");
    expect(Object.hasOwn(parseSfxRaw(draft.raw, p, cueId), "tail_policy")).toBe(false);
    expect(() => parseSfxRaw({ ...raw, sourceFrames: "60", tailPolicy: "continue_across_cuts" }, p, cueId)).toThrow(/complete project/);
  });
  it.each(["generation", "active_branch", "id"] as const)("rejects foreign %s without changing authored input", key => {
    const p = ready(), draft = changed(p), other = structuredClone(p); other[key] = id(99);
    expect(sfxConflict(draft, other)).toBe(true);
    expect(() => sfxChange(draft, other)).toThrow(/another project/);
    expect(draft.raw.gainMilliDb).toBe("-9000");
  });
  it.each(["content", "timing"] as const)("honors project and source-scene %s locks while retaining raw", kind => {
    for (const scope of ["project:", "scene:"]) {
      const p = ready(), draft = editSfxDraft(changed(p), { releaseFrames: "1e" });
      p.locks.push({ id: id(88), kind, resource: scope + (scope === "project:" ? p.id : p.scenes[0].id), note: "Synthetic lock" });
      expect(sfxLocked(p, cueId, draft.raw)).toBe(true);
      expect(() => prepareSfxSubmission(draft, p, attempt, "remove")).toThrow(/locked/);
      expect(draft.raw.releaseFrames).toBe("1e");
    }
  });
  it("allows an explicit Remove despite unfinished upsert input and preserves late input as an absent-target draft", () => {
    const p = ready(), raw = editSfxDraft(changed(p), { releaseFrames: "1e" });
    const pending = prepareSfxSubmission(raw, p, attempt, "remove").draft;
    const late = editSfxDraft(pending, { offsetFrames: "1e" }), receipt = response(p, pending);
    const result = acknowledgeSfxDraft(late, attempt, receipt, p);
    expect(result.pending).toBeNull(); expect(result.intent).toBe(true);
    expect(result.raw).toEqual(late.raw); expect(result.base!.id).toBe(cueId);
    expect(reconcileSfxDraft(result, receipt)).toBe(result);
    expect(() => sfxChange(result, receipt)).toThrow(/removed/);
    expect(() => resolveSfxDraft(result, receipt, "saved")).toThrow(/absent/);
    expect(receipt.audio.sfx_cues!.map(cue => cue.id)).toEqual([otherCue]);
  });
  it("acknowledges only the original exact response and never erases input authored while awaiting it", () => {
    const p = ready(), pending = prepareSfxSubmission(changed(p), p, attempt).draft;
    const late = editSfxDraft(pending, { offsetFrames: "1e" }), receipt = response(p, pending);
    const result = acknowledgeSfxDraft(late, attempt, receipt, p);
    expect(result.raw.offsetFrames).toBe("1e"); expect(result.intent).toBe(true);
    expect(result.baseRevision).toBe(receipt.revision); expect(result.pending).toBeNull();
    expect(result.base).toEqual(receipt.audio.sfx_cues![0]);
    for (const corrupt of [
      (r: Project) => { r.revision++; },
      (r: Project) => { r.generation = id(101); },
      (r: Project) => { r.audio.sfx_cues!.reverse(); },
      (r: Project) => { r.audio.sfx_cues![1].gain_milli_db = -3000; },
      (r: Project) => { r.assets.reverse(); },
    ]) {
      const rejected = structuredClone(receipt); corrupt(rejected);
      expect(() => acknowledgeSfxDraft(late, attempt, rejected, p)).toThrow();
      expect(late.pending!.id).toBe(attempt); expect(late.raw.offsetFrames).toBe("1e");
    }
    expect(() => acknowledgeSfxDraft(late, id(102), receipt, p)).toThrow(/original/);
  });
  it("keeps saved props and matching journal values observational until an explicit separate intent", async () => {
    const p = ready(), pending = prepareSfxSubmission(changed(p), p, attempt).draft;
    const unknown = retainSfxFailure(pending, attempt, "bridge lost reply"), receipt = response(p, pending);
    expect(reconcileSfxDraft(unknown, receipt)).toBe(unknown);
    expect(() => resolveSfxDraft(unknown, receipt, "draft")).toThrow(/uncertain/);
    const review = await inspectSfxOutcome(unknown, receipt, {
      readProject: async () => structuredClone(receipt),
      readHistory: async () => [{ revision: receipt.revision, change: pending.pending!.change, created_at: "2026-10-22T00:00:00Z" }] as ProjectEvent[],
    });
    expect(review.matchingRevisions).toEqual([receipt.revision]); expect(unknown.pending!.outcome).toBe("UNKNOWN");
    expect(sfxOutcomeMatches(unknown, receipt, review)).toBe(true);
    expect(sfxOutcomeMatches(editSfxDraft(unknown, { gainMilliDb: "-8000" }), receipt, review)).toBe(false);
    const next = separateSfxIntent(unknown, receipt, review);
    expect(next.pending).toBeNull(); expect(next.raw).toEqual(unknown.raw); expect(next.sequence).toBe(unknown.sequence + 1);
    expect(unknown.pending!.outcome).toBe("UNKNOWN");
  });
  it("refuses same-revision separation, truncated history and a changed second read", async () => {
    const p = ready(), pending = prepareSfxSubmission(changed(p), p, attempt).draft;
    const same = await inspectSfxOutcome(pending, p, { readProject: async () => p, readHistory: async () => [] });
    expect(() => separateSfxIntent(pending, p, same)).toThrow(/could still commit/);
    const saved = response(p, pending);
    await expect(inspectSfxOutcome(pending, saved, { readProject: async () => saved, readHistory: async () => [] })).rejects.toThrow(/incomplete/);
    let reads = 0;
    await expect(inspectSfxOutcome(pending, saved, {
      readProject: async () => ++reads === 1 ? saved : { ...saved, revision: saved.revision + 1 },
      readHistory: async () => [{ revision: saved.revision, change: pending.pending!.change, created_at: "now" }] as ProjectEvent[],
    })).rejects.toThrow(/changed during/);
  });
  it("preserves explicit clear and ABA edits when a delayed initial recovery read succeeds", () => {
    const p = ready(), recovered = prepareSfxSubmission(changed(p), p, attempt).draft;
    const initial = createSfxDraft(p, cueId), edited = editSfxDraft(editSfxDraft(initial, { attestation: "later" }), { attestation: initial.raw.attestation });
    const result = recoverSfxRead(edited, recovered, true);
    expect(result.raw).toEqual(edited.raw); expect(result.intent).toBe(true);
    expect(result.pending!.id).toBe(attempt); expect(result.sequence).toBeGreaterThan(edited.sequence);
    expect(recoverSfxRead(editSfxDraft(initial, { attestation: "" }), recovered, true).raw.attestation).toBe("");
    expect(recoverSfxRead(initial, recovered, false).raw).toEqual(recovered.raw);
  });
});

describe("SFX durable copy and pointer custody", () => {
  it.each([null, previousWriter])("does not report success on repeated silent pointer publication with previous pointer %s", previous => {
    const p = ready(), draft = changed(p), data = new Memory(), pointers = new Memory(), state = custody(previous);
    if (previous) pointers.values.set(sfxPointerKey(draft.scope), previous);
    pointers.mode = "drop";
    expect(() => writeSfxCopy(data, pointers, draft, state)).toThrow(/pointer/);
    const first = data.getItem(sfxCopyKey(draft.scope, writer)); expect(first).not.toBeNull();
    expect(() => writeSfxCopy(data, pointers, draft, state)).toThrow(/pointer/);
    expect(state.expectedPointer).toBe(previous); expect(data.getItem(sfxCopyKey(draft.scope, writer))).toBe(first);
  });
  it.each(["drop", "throw"] as const)("retry after %s republishes the confirmed exact bytes before succeeding", mode => {
    const p = ready(), draft = changed(p), data = new Memory(), pointers = new Memory(), state = custody(previousWriter);
    pointers.values.set(sfxPointerKey(draft.scope), previousWriter); pointers.mode = mode;
    vi.spyOn(Date, "now").mockReturnValue(1000);
    expect(() => writeSfxCopy(data, pointers, draft, state)).toThrow();
    const original = data.getItem(sfxCopyKey(draft.scope, writer));
    vi.spyOn(Date, "now").mockReturnValue(2000); pointers.mode = "normal";
    expect(writeSfxCopy(data, pointers, draft, state)).toBe(original);
    expect(pointers.getItem(sfxPointerKey(draft.scope))).toBe(writer);
    expect(state.expectedPointer).toBe(writer); expect(data.writes).toHaveLength(1);
  });
  it("fails closed after a pointer write that changed storage and then threw", () => {
    const draft = changed(ready()), data = new Memory(), pointers = new Memory(), state = custody();
    pointers.mode = "write-then-throw";
    expect(() => writeSfxCopy(data, pointers, draft, state)).toThrow(/uncertain publication/);
    const original = data.getItem(sfxCopyKey(draft.scope, writer)); pointers.mode = "normal";
    expect(() => writeSfxCopy(data, pointers, draft, state)).toThrow(/Another writer/);
    expect(data.getItem(sfxCopyKey(draft.scope, writer))).toBe(original);
    expect(state.expectedPointer).toBeNull();
  });
  it("requires both exact data readback and data recheck after pointer publication", () => {
    const draft = changed(ready()), data = new Memory(), pointers = new Memory(), state = custody();
    data.mode = "drop";
    expect(() => writeSfxCopy(data, pointers, draft, state)).toThrow(/bytes were not confirmed/);
    expect(pointers.writes).toHaveLength(0); expect(state.expectedRaw).toBeNull();
    data.mode = "normal";
    pointers.afterWrite = () => data.values.set(sfxCopyKey(draft.scope, writer), "foreign bytes");
    expect(() => writeSfxCopy(data, pointers, draft, state)).toThrow(/changed during/);
    expect(data.getItem(sfxCopyKey(draft.scope, writer))).toBe("foreign bytes");
  });
  it("does not overwrite a foreign copy or a changed pointer", () => {
    const draft = changed(ready()), data = new Memory(), pointers = new Memory(), state = custody();
    data.values.set(sfxCopyKey(draft.scope, writer), "foreign bytes");
    expect(() => writeSfxCopy(data, pointers, draft, state)).toThrow(/Another writer/);
    expect(data.writes).toHaveLength(0); data.values.clear();
    pointers.values.set(sfxPointerKey(draft.scope), previousWriter);
    expect(() => writeSfxCopy(data, pointers, draft, state)).toThrow(/Another writer/);
    expect(data.writes).toHaveLength(0); expect(pointers.writes).toHaveLength(0);
  });
  it("allows a first confirmed null read and rejects known-writer deletion", () => {
    const draft = changed(ready()), pointers = new Memory();
    expect(readSfxPointer(pointers, draft.scope, { observed: false, value: null })).toEqual({ observed: true, value: null });
    expect(() => readSfxPointer(pointers, draft.scope, { observed: true, value: previousWriter })).toThrow(/changed/);
  });
  it("normalizes retained NOT_SENT to UNKNOWN and rejects corrupt, foreign or oversized copies", () => {
    const p = ready(), pending = prepareSfxSubmission(changed(p), p, attempt).draft;
    const live = retainSfxFailure(pending, attempt, "copy failed before dispatch", true);
    const raw = encodeSfxCopy(live, writer, 1), copy = decodeSfxCopy(raw, live.scope, writer);
    expect(copy.draft.pending!.outcome).toBe("UNKNOWN"); expect(live.pending!.outcome).toBe("NOT_SENT");
    expect(() => decodeSfxCopy(raw + " ", live.scope, writer)).toThrow();
    expect(() => decodeSfxCopy(raw, sfxScope({ ...p, generation: id(90) }, cueId), writer)).toThrow();
    expect(() => decodeSfxCopy(raw, live.scope, previousWriter)).toThrow();
    expect(() => encodeSfxCopy(editSfxDraft(live, { attestation: "x".repeat(MAX_SFX_COPY_BYTES) }), writer)).toThrow(/256 KiB/);
  });
  it("retains immutable original UNKNOWN bytes and stops if any original changes", () => {
    const p = ready(), pending = prepareSfxSubmission(changed(p), p, attempt).draft;
    const raw = encodeSfxCopy(pending, previousWriter, 1), key = sfxCopyKey(pending.scope, previousWriter);
    const retained = retainSfxOrigin(pending, { key, raw }), data = new Memory(), pointers = new Memory();
    data.values.set(key, raw); expect(retained.origins).toEqual([{ key, raw }]);
    expect(() => assertSfxOrigins(data, retained)).not.toThrow();
    writeSfxCopy(data, pointers, retained, custody()); expect(data.getItem(key)).toBe(raw);
    data.values.set(key, raw + " ");
    expect(() => writeSfxCopy(data, pointers, editSfxDraft(retained, { offsetFrames: "1e" }), custody())).toThrow(/original uncertain/);
    expect(data.getItem(key)).toBe(raw + " ");
  });
  it("catalogues absent targets and exact corrupt bytes without crossing branch or generation owners", () => {
    const p = ready(), draft = changed(p), data = new Memory();
    const key = sfxCopyKey(draft.scope, writer), raw = encodeSfxCopy(draft, writer, 1);
    data.values.set(key, raw);
    data.values.set(sfxCopyKey(draft.scope, previousWriter), "{corrupt-original-bytes");
    const foreign = changed({ ...p, active_branch: id(91) });
    data.values.set(sfxCopyKey(foreign.scope, id(92)), encodeSfxCopy(foreign, id(92), 1));
    p.audio.sfx_cues = p.audio.sfx_cues!.filter(cue => cue.id !== cueId);
    const entries = readSfxCatalogue(data, p).entries;
    expect(entries).toHaveLength(2);
    expect(entries.find(entry => entry.key === key)!.value!.draft.raw).toEqual(draft.raw);
    const corrupt = entries.find(entry => entry.writerId === previousWriter)!;
    expect(corrupt.value).toBeNull(); expect(corrupt.raw).toBe("{corrupt-original-bytes"); expect(corrupt.error).toBeTruthy();
  });
});
