import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import type { Project, VisualLanguage } from "./types";
import {
  VISUAL_COPY_BYTES, VISUAL_COPY_LIMIT, VISUAL_DRAFT_FIELDS, VISUAL_DRAFT_PREFIX, VISUAL_KEY_LIMIT, VISUAL_TOTAL_BYTES,
  createVisualDraft, decodeVisualRecovery, discardVisualRecovery, editVisualField, prepareVisualSubmission, readVisualRecoveries,
  reconcileVisualDraft, releaseVisualSubmission, resolveVisualField, visualDraftConflicts, visualDraftHasWork, visualDraftInput,
  visualDraftIssue, visualDraftScope, visualRecoveryKey, visualSubmissionMatches, writeVisualRecovery,
} from "./visualLanguageDraft";
import type { VisualDraftStorage, VisualLanguageDraft, VisualLanguageInput, VisualRecovery } from "./visualLanguageDraft";

// These are pure editor/storage tests with explicit Project and storage doubles.
// The separate G2 suite exercises real grants, service revisions and SQLite.
const source = (): Project => structuredClone(fixtureProject);
function next(project: Project, language: Partial<VisualLanguage> = {}): Project {
  const result = structuredClone(project); result.revision++;
  Object.assign(result.visual_language, language);
  if (Object.keys(language).length) result.visual_language.version++;
  return result;
}
function acknowledged(project: Project, language: VisualLanguage): Project {
  const result = next(project); result.visual_language = structuredClone(language); return result;
}
function copy(draft: VisualLanguageDraft | null, writerId = "window-a", updatedAt = 1,
  scope = draft?.scope ?? visualDraftScope(source())): VisualRecovery {
  return { schema: "motionwright.visual-recovery/1", scope, writerId, updatedAt, draft };
}
function roundTrip(draft: VisualLanguageDraft): VisualLanguageDraft {
  const value = copy(draft); return decodeVisualRecovery(JSON.stringify(value), value.scope, value.writerId).draft!;
}
function nameDraft(project = source(), name = "  Human system 🌘  "): VisualLanguageDraft {
  return editVisualField(createVisualDraft(project), "name", name);
}
function motionDraft(raw: string, project = source()): VisualLanguageDraft {
  const initial = createVisualDraft(project), input = visualDraftInput(initial);
  return editVisualField(initial, "motion_grammar", input.motion_grammar.map((verb, index) => index ? verb : { ...verb, duration_ms: raw }));
}
class MemoryStorage implements VisualDraftStorage {
  data = new Map<string, string>();
  reads = 0; writes = 0; removals = 0; keys = 0;
  failWrites = false; failRemovals = false;
  unreadable = new Set<string>();
  beforeGet: ((key: string) => void) | null = null;
  afterSet: ((key: string, value: string) => void) | null = null;
  get length() { return this.data.size; }
  key(index: number): string | null { this.keys++; return [...this.data.keys()][index] ?? null; }
  getItem(key: string) {
    this.reads++; this.beforeGet?.(key);
    if (this.unreadable.has(key)) throw new Error("Storage read denied");
    return this.data.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.writes++;
    if (this.failWrites) throw new Error("QuotaExceededError");
    this.data.set(key, value); this.afterSet?.(key, value);
  }
  removeItem(key: string) {
    this.removals++;
    if (this.failRemovals) throw new Error("Storage removal denied");
    this.data.delete(key);
  }
}
function retain(storage: MemoryStorage, value: VisualRecovery, expectedRaw: string | null = null) {
  const key = visualRecoveryKey(value.scope, value.writerId), raw = writeVisualRecovery(storage, value, expectedRaw);
  return { key, raw };
}

describe("visual-language intent and acknowledgement", () => {
  it.each(["", " ", "-", "1e", " 1.25e+ ", "0x10", "Infinity", "NaN"])("retains the exact incomplete duration %j without turning it into zero", raw => {
    const project = source(), draft = motionDraft(raw, project), frozenProject = JSON.stringify(project);
    const recovered = reconcileVisualDraft(roundTrip(draft), next(project));
    expect(visualDraftInput(recovered).motion_grammar[0].duration_ms).toBe(raw);
    expect(visualDraftHasWork(recovered)).toBe(true);
    expect(visualDraftIssue(recovered, next(project))).toMatch(/duration/);
    expect(() => prepareVisualSubmission(recovered, next(project), "invalid-duration")).toThrow(/duration/);
    expect(JSON.stringify(project)).toBe(frozenProject);
  });

  it.each([["0", 0], [" 2e2 ", 200], ["+120000", 120_000], ["200.0", 200]])("converts complete duration %j only for an explicit save", (raw, expected) => {
    const project = source(), draft = motionDraft(raw, project);
    const prepared = prepareVisualSubmission(draft, project, "duration");
    expect(visualDraftInput(prepared.draft).motion_grammar[0].duration_ms).toBe(raw);
    expect(prepared.change.visual_language.motion_grammar[0].duration_ms).toBe(expected);
    expect(project.visual_language.motion_grammar[0].duration_ms).toBe(180);
  });

  it("validates every dirty group even after an earlier group already changes the project", () => {
    const project = source(), draft = editVisualField(motionDraft("", project), "name", "A valid changed name");
    expect(visualDraftIssue(draft, project)).toMatch(/duration/);
    expect(() => prepareVisualSubmission(draft, project, "all-groups")).toThrow(/duration/);
  });

  it("keeps exact text, collection order and independent raw copies without borrowing object references", () => {
    const project = source(), before = JSON.stringify(project), initial = createVisualDraft(project);
    const input = visualDraftInput(initial);
    input.palette[0].value = "  #abc123  ";
    let draft = editVisualField(initial, "palette", input.palette);
    draft = editVisualField(draft, "name", "  A human line\n🌘  ");
    draft = editVisualField(draft, "anti_slop_rules", ["  First rule  ", "Second\nline"]);
    input.palette[0].value = "external mutation";
    const returned = visualDraftInput(draft); returned.palette.reverse(); returned.palette[0].value = "another external mutation";
    const recovered = roundTrip(draft);
    expect(visualDraftInput(recovered).name).toBe("  A human line\n🌘  ");
    expect(visualDraftInput(recovered).palette[0].value).toBe("  #abc123  ");
    expect(visualDraftInput(recovered).anti_slop_rules).toEqual(["  First rule  ", "Second\nline"]);
    expect(visualDraftInput(initial).palette).toEqual(project.visual_language.palette);
    expect(JSON.stringify(project)).toBe(before);
    const prepared = prepareVisualSubmission(recovered, project, "explicit-trim");
    expect(prepared.change.visual_language.name).toBe("A human line\n🌘");
    expect(prepared.change.visual_language.palette[0].value).toBe("#abc123");
    expect(prepared.change.visual_language.anti_slop_rules).toEqual(["First rule", "Second\nline"]);
    expect(visualDraftInput(prepared.draft)).toEqual(visualDraftInput(recovered));
  });

  it("preserves a human palette through ten disjoint peer revisions and composes only current untouched groups", () => {
    let project = source(), draft = nameDraft(project);
    const palette = visualDraftInput(draft).palette; palette[0].value = "#13579B";
    draft = editVisualField(draft, "palette", palette);
    const originalBase = structuredClone(draft.fields.palette), originalName = structuredClone(draft.fields.name);
    for (let revision = 1; revision <= 10; revision++) {
      project = next(project, {
        fonts: [{ family: "Peer font " + revision, source: "Authored source note " + revision, rights_status: "unknown" }],
        type_tokens: [{ name: "Peer scale", value: revision + "px" }],
      });
      project.title = "Unrelated peer decision " + revision;
      draft = reconcileVisualDraft(roundTrip(draft), project);
      expect(draft.fields.palette).toEqual(originalBase);
      expect(draft.fields.name).toEqual(originalName);
      expect(visualDraftInput(draft).fonts).toEqual(project.visual_language.fonts);
      expect(draft.fields.fonts.baseRevision).toBe(project.revision);
      expect(visualDraftConflicts(draft, project)).toEqual([]);
    }
    const projectBefore = JSON.stringify(project), prepared = prepareVisualSubmission(draft, project, "after-ten-revisions");
    expect(prepared.change.visual_language).toEqual({ ...project.visual_language, version: project.visual_language.version + 1,
      name: "Human system 🌘", palette });
    expect(prepared.submission.revision).toBe(project.revision);
    expect(JSON.stringify(project)).toBe(projectBefore);
  });

  it("treats a whole collection as the conflict unit and preserves peer groups after either explicit choice", () => {
    const original = source(), palette = structuredClone(original.visual_language.palette);
    palette[0].value = "#123ABC";
    let draft = editVisualField(nameDraft(original), "palette", palette);
    const peerPalette = structuredClone(original.visual_language.palette); peerPalette[1].name = "Peer renamed token";
    const peer = next(original, { palette: peerPalette, fonts: [{ family: "Peer font", source: "", rights_status: "restricted" }] });
    draft = reconcileVisualDraft(roundTrip(draft), peer);
    expect(visualDraftConflicts(draft, peer)).toMatchObject([{ field: "palette", futureRevision: false }]);
    expect(() => prepareVisualSubmission(draft, peer, "blocked-conflict")).toThrow(/overlapping/);
    const chooseDraft = resolveVisualField(draft, peer, "palette", "draft");
    const own = prepareVisualSubmission(chooseDraft, peer, "reviewed-palette");
    expect(own.change.visual_language.palette).toEqual(palette);
    expect(own.change.visual_language.fonts).toEqual(peer.visual_language.fonts);
    expect(own.change.visual_language.name).toBe("Human system 🌘");
    const chooseSaved = resolveVisualField(draft, peer, "palette", "saved");
    expect(visualDraftInput(chooseSaved).palette).toEqual(peerPalette);
    expect(chooseSaved.fields.name).toEqual(draft.fields.name);
    expect(prepareVisualSubmission(chooseSaved, peer, "saved-palette").change.visual_language.palette).toEqual(peerPalette);
    expect(original.visual_language.palette[0].value).not.toBe("#123ABC");
  });

  it("does not merge indexes after a peer reorders or removes a collection row", () => {
    const original = source(), palette = structuredClone(original.visual_language.palette); palette[0].value = "#FF0000";
    const draft = editVisualField(createVisualDraft(original), "palette", palette);
    for (const peerPalette of [[...original.visual_language.palette].reverse(), original.visual_language.palette.slice(1)]) {
      const peer = next(original, { palette: peerPalette });
      expect(visualDraftConflicts(reconcileVisualDraft(roundTrip(draft), peer), peer)).toMatchObject([{ field: "palette" }]);
      expect(() => prepareVisualSubmission(draft, peer, "do-not-guess-rows")).toThrow(/overlapping/);
    }
  });

  it("keeps future-revision intent until an explicit saved/draft decision even if values happen to match", () => {
    const project = source(), draft = nameDraft(project), older = { ...project, revision: project.revision - 1 };
    expect(visualDraftConflicts(draft, older)).toMatchObject([{ field: "name", futureRevision: true }]);
    const recovered = reconcileVisualDraft(roundTrip(draft), older);
    expect(recovered.fields.name).toEqual(draft.fields.name);
    expect(() => prepareVisualSubmission(recovered, older, "future-base")).toThrow(/overlapping/);
    expect(prepareVisualSubmission(resolveVisualField(recovered, older, "name", "draft"), older, "explicit-future-review").change.visual_language.name).toBe("Human system 🌘");
    expect(visualDraftHasWork(resolveVisualField(recovered, older, "name", "saved"))).toBe(false);
  });

  it("retains name and palette edits made while a real snapshot would be awaiting acknowledgement", () => {
    const project = source(), sent = prepareVisualSubmission(nameDraft(project, "Submitted name"), project, "first");
    const frozenPayload = JSON.stringify(sent.change), frozenSnapshot = JSON.stringify(sent.submission);
    let late = editVisualField(sent.draft, "name", "Later human name");
    const palette = visualDraftInput(late).palette; palette[0].value = "#123456";
    late = editVisualField(late, "palette", palette);
    const saved = acknowledged(project, sent.change.visual_language), recovered = reconcileVisualDraft(roundTrip(late), saved);
    expect(visualSubmissionMatches(saved, late, sent.submission)).toBe(true);
    expect(recovered.awaiting).toBeNull();
    expect(visualDraftInput(recovered).name).toBe("Later human name");
    expect(visualDraftInput(recovered).palette).toEqual(palette);
    expect(recovered.fields.name).toMatchObject({ base: "Submitted name", baseRevision: saved.revision, intent: true });
    expect(recovered.fields.palette.intent).toBe(true);
    expect(visualDraftConflicts(recovered, saved)).toEqual([]);
    expect(prepareVisualSubmission(recovered, saved, "second").change.visual_language).toMatchObject({ name: "Later human name", palette, version: 3 });
    expect(JSON.stringify(sent.change)).toBe(frozenPayload);
    expect(JSON.stringify(sent.submission)).toBe(frozenSnapshot);
  });

  it("distinguishes both a return to the old base and an away-and-back edit of the submitted raw", () => {
    const project = source(), sent = prepareVisualSubmission(nameDraft(project, "  Sent  "), project, "sequence");
    const saved = acknowledged(project, sent.change.visual_language);
    const oldBase = editVisualField(sent.draft, "name", project.visual_language.name);
    const recoveredBase = reconcileVisualDraft(roundTrip(oldBase), saved);
    expect(visualDraftInput(recoveredBase).name).toBe(project.visual_language.name);
    expect(recoveredBase.fields.name).toMatchObject({ base: "Sent", intent: true });
    const typedAgain = editVisualField(editVisualField(sent.draft, "name", "Another value"), "name", "  Sent  ");
    const recoveredAgain = reconcileVisualDraft(roundTrip(typedAgain), saved);
    expect(visualDraftInput(recoveredAgain).name).toBe("  Sent  ");
    expect(recoveredAgain.fields.name.sequence).toBe(sent.submission.fields.name.sequence + 2);
    expect(visualDraftHasWork(recoveredAgain)).toBe(true);
    expect(visualDraftHasWork(reconcileVisualDraft(sent.draft, saved))).toBe(false);
  });

  it.each(["project", "generation", "branch", "revision", "version", "value"])("refuses an ACK with a different %s", mismatch => {
    const project = source(), sent = prepareVisualSubmission(nameDraft(project), project, "scope");
    const other = acknowledged(project, sent.change.visual_language);
    if (mismatch === "project") other.id += "-other";
    if (mismatch === "generation") other.generation += "-other";
    if (mismatch === "branch") other.active_branch += "-other";
    if (mismatch === "revision") other.revision++;
    if (mismatch === "version") other.visual_language.version++;
    if (mismatch === "value") other.visual_language.palette[0].value = "#111111";
    expect(visualSubmissionMatches(other, sent.draft, sent.submission)).toBe(false);
    expect(visualDraftInput(sent.draft).name).toBe("  Human system 🌘  ");
  });

  it("compares values independently of Rust JSON object-key order, while preserving row order", () => {
    const project = source(), sent = prepareVisualSubmission(motionDraft("200", project), project, "rust-json");
    const saved = acknowledged(project, sent.change.visual_language);
    saved.visual_language.motion_grammar = saved.visual_language.motion_grammar.map(verb => ({
      reduced_motion: verb.reduced_motion, duration_ms: verb.duration_ms, meaning: verb.meaning, name: verb.name,
    }));
    saved.visual_language.palette = saved.visual_language.palette.map(token => ({ value: token.value, name: token.name }));
    expect(visualSubmissionMatches(saved, roundTrip(sent.draft), sent.submission)).toBe(true);
    expect(visualDraftHasWork(reconcileVisualDraft(roundTrip(sent.draft), saved))).toBe(false);
    saved.visual_language.palette.reverse();
    expect(visualSubmissionMatches(saved, sent.draft, sent.submission)).toBe(false);
  });

  it("does not replay a restored awaiting submission or accept a resolved callback without its Project", () => {
    const project = source(), sent = prepareVisualSubmission(nameDraft(project), project, "unknown-result");
    const restored = reconcileVisualDraft(roundTrip(sent.draft), project);
    expect(restored.awaiting).toEqual(sent.submission);
    expect(visualDraftIssue(restored, project)).toMatch(/unconfirmed/);
    expect(() => prepareVisualSubmission(restored, project, "automatic-retry")).toThrow(/unconfirmed/);
    const released = releaseVisualSubmission(restored, "unknown-result");
    expect(released.fields).toEqual(restored.fields);
    const second = prepareVisualSubmission(released, project, "new-explicit-save");
    expect(releaseVisualSubmission(second.draft, "unknown-result")).toBe(second.draft);
  });

  it("keeps the original inputs when a newer saved revision does not confirm the snapshot", () => {
    const project = source(), sent = prepareVisualSubmission(nameDraft(project), project, "different-result");
    const peer = next(project, { name: "Peer canonical name" });
    const recovered = reconcileVisualDraft(roundTrip(sent.draft), peer);
    expect(recovered.awaiting).toBeNull();
    expect(recovered.fields.name).toEqual(sent.draft.fields.name);
    expect(visualDraftConflicts(recovered, peer)).toMatchObject([{ field: "name" }]);
    const laterMatching = next(acknowledged(project, sent.change.visual_language));
    expect(visualSubmissionMatches(laterMatching, sent.draft, sent.submission)).toBe(false);
    const converged = reconcileVisualDraft(roundTrip(sent.draft), laterMatching);
    expect(visualDraftHasWork(converged)).toBe(true);
    expect(converged.fields.name.raw).toBe(sent.draft.fields.name.raw);
  });

  it("does not share mutable payload or snapshot objects with the retained awaiting draft", () => {
    const project = source(), prepared = prepareVisualSubmission(nameDraft(project), project, "owned-snapshot");
    const raw = JSON.stringify(prepared.draft);
    prepared.change.visual_language.palette[0].value = "external change";
    prepared.submission.fields.name.value = "external receipt change";
    expect(JSON.stringify(prepared.draft)).toBe(raw);
    expect(project.visual_language.palette[0].value).toBe(source().visual_language.palette[0].value);
  });

  it("keeps the draft through style protect/unlock and rejects the current lock", () => {
    const project = source(), draft = nameDraft(project), locked = next(project);
    locked.locks.push({ id: "style-lock", resource: "project:" + project.id, kind: "style", note: "Keep approved direction" });
    const kept = reconcileVisualDraft(roundTrip(draft), locked);
    expect(visualDraftInput(kept).name).toBe(visualDraftInput(draft).name);
    expect(visualDraftIssue(kept, locked)).toMatch(/style lock/);
    expect(() => prepareVisualSubmission(kept, locked, "locked")).toThrow(/style lock/);
    const unlocked = next(locked); unlocked.locks = unlocked.locks.filter(lock => lock.id !== "style-lock");
    expect(prepareVisualSubmission(reconcileVisualDraft(kept, unlocked), unlocked, "explicit-after-unlock").change.visual_language.name).toBe("Human system 🌘");
    const unrelatedLock = next(project); unrelatedLock.locks.push({ id: "content", resource: "project:" + project.id, kind: "content", note: "Not a style lock" });
    expect(visualDraftIssue(draft, unrelatedLock)).toBeNull();
  });

  it("uses the current canonical version, rejects exhaustion and keeps the original Project unchanged", () => {
    const project = source(), draft = nameDraft(project), current = next(project); current.visual_language.version = 999;
    expect(prepareVisualSubmission(draft, current, "v1000").change.visual_language.version).toBe(1000);
    current.visual_language.version = 1000;
    expect(() => prepareVisualSubmission(draft, current, "v1001")).toThrow(/version 1,000/);
    current.visual_language.version = 999; current.revision = Number.MAX_SAFE_INTEGER;
    expect(() => prepareVisualSubmission(draft, current, "unsafe-revision")).toThrow(/advance safely/);
    expect(project.visual_language.version).toBe(1);
  });

  it("does not block or rewrite untouched imported blank rules and unknown/restricted font rights", () => {
    const project = source(); project.visual_language.anti_slop_rules = ["", "  "];
    project.visual_language.fonts = [{ family: " Unknown family ", source: "", rights_status: "unknown" },
      { family: "Restricted family", source: "  retained provenance  ", rights_status: "restricted" }];
    const prepared = prepareVisualSubmission(nameDraft(project, "A new name"), project, "keep-imported-groups");
    expect(prepared.change.visual_language.anti_slop_rules).toEqual(["", "  "]);
    expect(prepared.change.visual_language.fonts).toEqual(project.visual_language.fonts);
    expect(roundTrip(prepared.draft).awaiting).toEqual(prepared.submission);
    const editedRules = editVisualField(createVisualDraft(project), "anti_slop_rules", ["New rule", ""]);
    expect(visualDraftIssue(editedRules, project)).toMatch(/contain text/);
  });

  it.each(["id", "generation", "active_branch"] as const)("isolates current intent from another %s", property => {
    const project = source(), draft = nameDraft(project), other = { ...project, [property]: project[property] + "-other" };
    expect(() => reconcileVisualDraft(draft, other)).toThrow(/another project/);
    expect(() => prepareVisualSubmission(draft, other, "foreign")).toThrow(/another project/);
    const storage = new MemoryStorage(); retain(storage, copy(draft));
    expect(readVisualRecoveries(storage, visualDraftScope(other))).toEqual({ entries: [], unreadable: [], limited: false });
  });
});

describe("visual-language input bounds", () => {
  const invalid: [string, (input: VisualLanguageInput) => VisualLanguageInput][] = [
    ["name UTF-8", input => ({ ...input, name: "🌘".repeat(65) })],
    ["token name", input => ({ ...input, palette: [{ name: "x".repeat(129), value: "#abc" }] })],
    ["token value", input => ({ ...input, type_tokens: [{ name: "scale", value: "x".repeat(1025) }] })],
    ["motion name", input => ({ ...input, motion_grammar: [{ ...input.motion_grammar[0], name: "x".repeat(129) }] })],
    ["motion meaning", input => ({ ...input, motion_grammar: [{ ...input.motion_grammar[0], meaning: "x".repeat(1001) }] })],
    ["negative duration", input => ({ ...input, motion_grammar: [{ ...input.motion_grammar[0], duration_ms: "-1" }] })],
    ["fractional duration", input => ({ ...input, motion_grammar: [{ ...input.motion_grammar[0], duration_ms: "1.5" }] })],
    ["excess duration", input => ({ ...input, motion_grammar: [{ ...input.motion_grammar[0], duration_ms: "120001" }] })],
    ["rule UTF-8", input => ({ ...input, anti_slop_rules: ["🌘".repeat(251)] })],
    ["font family", input => ({ ...input, fonts: [{ family: "x".repeat(257), source: "", rights_status: "unknown" }] })],
    ["font source", input => ({ ...input, fonts: [{ family: "A font", source: "x".repeat(2049), rights_status: "restricted" }] })],
    ["palette size", input => ({ ...input, palette: Array.from({ length: 257 }, () => ({ name: "x", value: "y" })) })],
    ["type size", input => ({ ...input, type_tokens: Array.from({ length: 257 }, () => ({ name: "x", value: "y" })) })],
    ["motion size", input => ({ ...input, motion_grammar: Array.from({ length: 129 }, () => ({ ...input.motion_grammar[0] })) })],
    ["rule size", input => ({ ...input, anti_slop_rules: Array.from({ length: 513 }, () => "A rule") })],
    ["font size", input => ({ ...input, fonts: Array.from({ length: 129 }, () => ({ family: "A font", source: "", rights_status: "unknown" as const })) })],
  ];
  it.each(invalid)("retains invalid %s for correction and rejects save", (_label, change) => {
    const project = source(); let draft = createVisualDraft(project);
    const input = change(visualDraftInput(draft));
    for (const field of VISUAL_DRAFT_FIELDS) draft = editVisualField(draft, field, input[field]);
    const recovered = roundTrip(draft);
    expect(visualDraftInput(recovered)).toEqual(input);
    expect(visualDraftIssue(recovered, project)).not.toBeNull();
    expect(() => prepareVisualSubmission(recovered, project, "invalid-bound")).toThrow();
    expect(project).toEqual(source());
  });

  it("retains ill-formed UTF-16 input in an exact JSON recovery but cannot dispatch it as canonical text", () => {
    const project = source(), raw = "Human note \ud800 end \udfff", draft = nameDraft(project, raw);
    const encoded = JSON.stringify(copy(draft)), restored = roundTrip(draft);
    expect(visualDraftInput(restored).name).toBe(raw);
    // A JSON envelope remains lossless even when a downloaded Blob uses UTF-8.
    const downloaded = new TextDecoder().decode(new TextEncoder().encode(JSON.stringify({ raw: encoded })));
    expect(JSON.parse(JSON.parse(downloaded).raw).draft.fields.name.raw).toBe(raw);
    expect(() => prepareVisualSubmission(restored, project, "invalid-unicode")).toThrow(/Unicode/);
  });

  it("can retain a fully populated admitted system together with all changed groups and the submitted snapshot", () => {
    const project = source();
    const language = (letter: string): VisualLanguage => ({ version: 1, name: letter.repeat(256),
      palette: Array.from({ length: 256 }, () => ({ name: letter.repeat(128), value: letter.repeat(1024) })),
      type_tokens: Array.from({ length: 256 }, () => ({ name: letter.repeat(128), value: letter.repeat(1024) })),
      motion_grammar: Array.from({ length: 128 }, () => ({ name: letter.repeat(128), meaning: letter.repeat(1000), duration_ms: 120_000, reduced_motion: "static_equivalent" })),
      anti_slop_rules: Array.from({ length: 512 }, () => letter.repeat(1000)),
      fonts: Array.from({ length: 128 }, () => ({ family: letter.repeat(256), source: letter.repeat(2048), rights_status: "unknown" })),
    });
    project.visual_language = language("a"); let draft = createVisualDraft(project);
    const input = visualDraftInput(createVisualDraft({ ...project, visual_language: language("b") }));
    for (const field of VISUAL_DRAFT_FIELDS) draft = editVisualField(draft, field, input[field]);
    const prepared = prepareVisualSubmission(draft, project, "maximum-collections"), raw = JSON.stringify(copy(prepared.draft));
    expect(new TextEncoder().encode(raw).length).toBeLessThan(VISUAL_COPY_BYTES);
    const storage = new MemoryStorage(), retained = retain(storage, copy(prepared.draft));
    expect(decodeVisualRecovery(retained.raw, draft.scope, "window-a").draft!.awaiting).toEqual(prepared.submission);
    expect(prepared.change.visual_language).toEqual({ ...language("b"), version: 2 });
  });
});

describe("exact and bounded visual recovery", () => {
  const malformed: [string, (value: VisualRecovery) => void][] = [
    ["recovery unknown field", value => { Object.assign(value, { effect_grant: "not-authority" }); }],
    ["wrong schema", value => { value.schema = "motionwright.visual-recovery/2" as never; }],
    ["foreign outer scope", value => { value.scope = JSON.stringify(["other", "generation", "branch"]); }],
    ["foreign writer", value => { value.writerId = "another-window"; }],
    ["unsafe timestamp", value => { value.updatedAt = Number.MAX_SAFE_INTEGER + 1; }],
    ["draft unknown field", value => { Object.assign(value.draft!, { version: 123 }); }],
    ["draft wrong scope", value => { value.draft!.scope = JSON.stringify(["other", "generation", "branch"]); }],
    ["unknown group", value => { Object.assign(value.draft!.fields, { version: value.draft!.fields.name }); }],
    ["field unknown field", value => { Object.assign(value.draft!.fields.name, { permission: true }); }],
    ["unsafe sequence", value => { value.draft!.fields.name.sequence = Number.MAX_SAFE_INTEGER + 1; }],
    ["negative base revision", value => { value.draft!.fields.name.baseRevision = -1; }],
    ["invalid intent", value => { value.draft!.fields.name.intent = false as never; }],
    ["invalid canonical base", value => { value.draft!.fields.name.base = ""; }],
    ["unknown token property", value => { value.draft!.fields.palette.raw = '[{"name":"x","value":"#123","secret":true}]'; }],
    ["duplicate raw token property", value => { value.draft!.fields.palette.raw = '[{"name":"x","name":"y","value":"#123"}]'; }],
    ["duplicate canonical token property", value => { value.draft!.fields.palette.base = '[{"name":"x","name":"y","value":"#123"}]'; }],
    ["number duration in raw", value => { value.draft!.fields.motion_grammar.raw = JSON.stringify(source().visual_language.motion_grammar); }],
    ["invented reduced motion", value => { value.draft!.fields.motion_grammar.raw = '[{"name":"x","meaning":"y","duration_ms":"1","reduced_motion":"invented"}]'; }],
    ["invented rights", value => { value.draft!.fields.fonts.raw = '[{"family":"x","source":"","rights_status":"approved_by_ai"}]'; }],
    ["snapshot unknown field", value => { Object.assign(value.draft!.awaiting!, { replay: true }); }],
    ["snapshot wrong scope", value => { value.draft!.awaiting!.scope = "another-scope"; }],
    ["snapshot unsafe revision", value => { value.draft!.awaiting!.revision = Number.MAX_SAFE_INTEGER; }],
    ["snapshot impossible version", value => { value.draft!.awaiting!.version = 1; }],
    ["snapshot unsafe sequence", value => { value.draft!.awaiting!.fields.name.sequence = 2; }],
    ["snapshot target does not match", value => { value.draft!.awaiting!.fields.name.value = "Another target"; }],
    ["snapshot field unknown property", value => { Object.assign(value.draft!.awaiting!.fields.name, { approved: true }); }],
  ];
  it.each(malformed)("preserves and rejects %s instead of reinterpreting the copy", (_label, mutate) => {
    const project = source(), prepared = prepareVisualSubmission(nameDraft(project), project, "snapshot");
    const originalScope = prepared.draft.scope, value = copy(prepared.draft); mutate(value);
    const raw = JSON.stringify(value), storage = new MemoryStorage(), key = visualRecoveryKey(originalScope, "window-a");
    storage.data.set(key, raw);
    expect(() => decodeVisualRecovery(raw, originalScope, "window-a")).toThrow();
    const read = readVisualRecoveries(storage, originalScope);
    expect(read.entries).toEqual([]);
    expect(read.unreadable).toEqual([{ key, raw, writerId: "window-a" }]);
    expect(() => writeVisualRecovery(storage, copy(nameDraft(project)), raw)).toThrow();
    expect(storage.data.get(key)).toBe(raw);
    expect(storage.writes).toBe(0);
  });

  it("rejects duplicate envelope and awaiting keys, including a last-key null that would discard an unknown result", () => {
    const prepared = prepareVisualSubmission(nameDraft(), source(), "snapshot"), value = copy(prepared.draft);
    const original = JSON.stringify(value);
    for (const raw of [original.replace('"draft":', '"draft":null,"draft":'),
      original.replace('"awaiting":{', '"awaiting":null,"awaiting":{'),
      original.replace(/}}}$/, '}},"awaiting":null}}')]) {
      expect(raw).not.toBe(original);
      expect(() => decodeVisualRecovery(raw, value.scope, value.writerId)).toThrow();
    }
  });

  it("does not allow noncanonical key spelling to impersonate a writer or scope", () => {
    const draft = nameDraft(), value = copy(draft), raw = JSON.stringify(value), storage = new MemoryStorage();
    const key = VISUAL_DRAFT_PREFIX + JSON.stringify([draft.scope, "window-a"], null, 1);
    storage.data.set(key, raw);
    expect(readVisualRecoveries(storage, draft.scope).unreadable[0]).toMatchObject({ key, raw });
    expect(() => decodeVisualRecovery(raw, JSON.stringify(JSON.parse(draft.scope), null, 1), "window-a")).toThrow(/identity/);
    expect(storage.data.get(key)).toBe(raw);
  });

  it("compares exact observed bytes before replacing and discarding a valid peer correction", () => {
    const storage = new MemoryStorage(), draft = nameDraft(), observed = retain(storage, copy(draft));
    const peerRaw = JSON.stringify(copy(editVisualField(draft, "name", "A peer corrected this copy"), "window-a", 99));
    storage.data.set(observed.key, peerRaw);
    expect(() => writeVisualRecovery(storage, copy(editVisualField(draft, "name", "Later local input")), observed.raw)).toThrow(/changed in another window/);
    expect(() => discardVisualRecovery(storage, observed.key, observed.raw)).toThrow(/changed after/);
    expect(storage.data.get(observed.key)).toBe(peerRaw);
    expect(readVisualRecoveries(storage, draft.scope).entries[0].raw).toBe(peerRaw);
    discardVisualRecovery(storage, observed.key, peerRaw);
    expect(storage.data.has(observed.key)).toBe(false);
  });

  it("rechecks writer bytes after scanning storage and retains a correction arriving before the final write", () => {
    const storage = new MemoryStorage(), draft = nameDraft(), observed = retain(storage, copy(draft));
    const peerRaw = JSON.stringify(copy(nameDraft(source(), "Peer arrived during scan"), "window-a", 99));
    let ownReads = 0;
    storage.beforeGet = key => { if (key === observed.key && ++ownReads === 3) storage.data.set(key, peerRaw); };
    expect(() => writeVisualRecovery(storage, copy(nameDraft(source(), "Later input")), observed.raw)).toThrow(/changed in another window/);
    expect(storage.data.get(observed.key)).toBe(peerRaw);
    expect(storage.writes).toBe(1);
  });

  it("reports a non-verifiable write without overwriting a competing readback again", () => {
    const storage = new MemoryStorage(), draft = nameDraft(), value = copy(draft), key = visualRecoveryKey(draft.scope, value.writerId);
    const peerRaw = JSON.stringify(copy(nameDraft(source(), "Peer readback"), "window-a", 99));
    storage.afterSet = written => { if (written === key) storage.data.set(key, peerRaw); };
    expect(() => writeVisualRecovery(storage, value, null)).toThrow(/could not be verified/);
    expect(storage.data.get(key)).toBe(peerRaw);
    expect(storage.writes).toBe(1);
  });

  it("keeps memory, the old bytes, peer copies and clean checkpoints when quota denies the new backup", () => {
    const storage = new MemoryStorage(), original = nameDraft(), observed = retain(storage, copy(original));
    retain(storage, copy(nameDraft(source(), "Peer independent copy"), "window-b"));
    retain(storage, copy(null, "clean-window"));
    const prepared = prepareVisualSubmission(editVisualField(original, "name", "Latest human input"), source(), "quota-blocked");
    const memory = JSON.stringify(prepared.draft), prior = new Map(storage.data); storage.failWrites = true;
    expect(() => writeVisualRecovery(storage, copy(prepared.draft), observed.raw)).toThrow(/QuotaExceeded/);
    expect(storage.data).toEqual(prior);
    expect(JSON.stringify(prepared.draft)).toBe(memory);
    expect(storage.removals).toBe(0);
    storage.failWrites = false;
    const recovered = writeVisualRecovery(storage, copy(prepared.draft), observed.raw);
    expect(decodeVisualRecovery(recovered, original.scope, "window-a").draft).toEqual(prepared.draft);
  });

  it("keeps independent dirty writers while recording immutable clean checkpoints with a monotonic local order", () => {
    const storage = new MemoryStorage(), draft = nameDraft();
    const own = retain(storage, copy(draft, "window-a", 100));
    const peer = retain(storage, copy(nameDraft(source(), "Peer input"), "window-b", 1));
    const peerValue = decodeVisualRecovery(peer.raw, draft.scope, "window-b");
    expect(peerValue.updatedAt).toBe(101);
    const clean = retain(storage, copy(null, "window-b", 1), peer.raw);
    expect(decodeVisualRecovery(clean.raw, draft.scope, "window-b").updatedAt).toBe(102);
    expect(storage.data.get(own.key)).toBe(own.raw);
    expect(() => writeVisualRecovery(storage, copy(draft, "window-b"), clean.raw)).toThrow(/immutable/);
    expect(writeVisualRecovery(storage, copy(null, "window-b", 500), clean.raw)).toBe(clean.raw);
    const fresh = retain(storage, copy(nameDraft(source(), "New input after clean"), "new-window-b", 1));
    expect(decodeVisualRecovery(fresh.raw, draft.scope, "new-window-b").updatedAt).toBe(103);
    expect(storage.data.get(own.key)).toBe(own.raw);
  });

  it("does not delete a clean checkpoint that became a peer draft before optional collection", () => {
    const storage = new MemoryStorage(), draft = nameDraft(), oldClean = retain(storage, copy(null, "old-clean"));
    const corrected = JSON.stringify(copy(nameDraft(source(), "Peer rescued input"), "old-clean", 99));
    storage.afterSet = () => { storage.data.set(oldClean.key, corrected); };
    const fresh = retain(storage, copy(draft, "fresh"));
    expect(storage.data.get(oldClean.key)).toBe(corrected);
    expect(storage.data.get(fresh.key)).toBe(fresh.raw);
  });

  it("does not report a verified backup as unconfirmed merely because optional old-clean collection is denied", () => {
    const storage = new MemoryStorage(), oldClean = retain(storage, copy(null, "old-clean"));
    storage.failRemovals = true;
    const fresh = retain(storage, copy(nameDraft(), "fresh"));
    expect(storage.data.get(fresh.key)).toBe(fresh.raw);
    expect(storage.data.get(oldClean.key)).toBe(oldClean.raw);
  });

  it("reports explicit removal denial and preserves raw unreadable bytes for a new deliberate decision", () => {
    const storage = new MemoryStorage(), scope = visualDraftScope(source()), key = visualRecoveryKey(scope, "damaged");
    const raw = "{ broken recovery\nKeep every byte \ud800 🌘"; storage.data.set(key, raw);
    expect(readVisualRecoveries(storage, scope).unreadable).toEqual([{ key, raw, writerId: "damaged" }]);
    storage.failRemovals = true;
    expect(() => discardVisualRecovery(storage, key, raw)).toThrow(/removal denied/);
    expect(storage.data.get(key)).toBe(raw);
    expect(() => discardVisualRecovery(storage, "foreign-application", raw)).toThrow(/changed after/);
    storage.failRemovals = false; discardVisualRecovery(storage, key, raw);
    expect(storage.data.has(key)).toBe(false);
  });

  it("reports unreadable storage without guessing or overwriting the inaccessible writer", () => {
    const storage = new MemoryStorage(), draft = nameDraft(), own = retain(storage, copy(draft));
    const other = retain(storage, copy(nameDraft(source(), "Independent"), "window-b"));
    storage.unreadable.add(own.key);
    const read = readVisualRecoveries(storage, draft.scope);
    expect(read.entries.map(entry => entry.raw)).toEqual([other.raw]);
    expect(read.unreadable).toEqual([{ key: own.key, raw: null, writerId: "window-a" }]);
    expect(() => writeVisualRecovery(storage, copy(draft), own.raw)).toThrow(/read denied/);
    expect(storage.data.get(own.key)).toBe(own.raw);
  });

  it("refuses clock and input-sequence overflow without changing any retained copy", () => {
    const storage = new MemoryStorage(), draft = nameDraft(), value = copy(draft, "future-clock", Number.MAX_SAFE_INTEGER);
    const raw = JSON.stringify(value), key = visualRecoveryKey(value.scope, value.writerId); storage.data.set(key, raw);
    expect(() => writeVisualRecovery(storage, copy(draft, "new-writer"), null)).toThrow(/clock/);
    expect(storage.data.get(key)).toBe(raw); expect(storage.data.size).toBe(1);
    draft.fields.name.sequence = Number.MAX_SAFE_INTEGER;
    expect(() => editVisualField(draft, "name", "Cannot increment safely")).toThrow(/sequence/);
    expect(draft.fields.name.raw).toBe("  Human system 🌘  ");
  });

  it("bounds individual, total-byte and copy-count reads while preserving every stored byte", () => {
    const scope = visualDraftScope(source()), storage = new MemoryStorage();
    const oversized = "x".repeat(VISUAL_COPY_BYTES + 1), firstKey = visualRecoveryKey(scope, "oversized");
    storage.data.set(firstKey, oversized);
    expect(() => decodeVisualRecovery(oversized, scope, "oversized")).toThrow(/8 MiB/);
    expect(readVisualRecoveries(storage, scope).unreadable[0].raw).toBe(oversized);
    const secondKey = visualRecoveryKey(scope, "second"); storage.data.set(secondKey, "y".repeat(VISUAL_TOTAL_BYTES - oversized.length));
    const before = new Map(storage.data), read = readVisualRecoveries(storage, scope);
    expect(read.limited).toBe(true);
    expect(read.unreadable[1]).toEqual({ key: secondKey, raw: null, writerId: "second" });
    expect(() => writeVisualRecovery(storage, copy(nameDraft()), null)).toThrow(/reading budget/);
    expect(storage.data).toEqual(before); expect(storage.writes).toBe(0);
    const many = new MemoryStorage();
    for (let index = 0; index <= VISUAL_COPY_LIMIT; index++) many.data.set(visualRecoveryKey(scope, "copy-" + index), "Unreadable copy " + index);
    const manyBefore = new Map(many.data), limited = readVisualRecoveries(many, scope);
    expect(limited.limited).toBe(true); expect(limited.unreadable).toHaveLength(VISUAL_COPY_LIMIT);
    expect(() => writeVisualRecovery(many, copy(nameDraft()), null)).toThrow(/128 copies/);
    expect(many.data).toEqual(manyBefore);
  });

  it("bounds scanning even when unrelated applications fill storage with more than 4,096 keys", () => {
    const storage = new MemoryStorage(), scope = visualDraftScope(source());
    for (let index = 0; index <= VISUAL_KEY_LIMIT; index++) storage.data.set("other-app:" + index, "external bytes");
    expect(readVisualRecoveries(storage, scope)).toEqual({ entries: [], unreadable: [], limited: true });
    expect(storage.keys).toBe(VISUAL_KEY_LIMIT); expect(storage.reads).toBe(0);
    expect(() => writeVisualRecovery(storage, copy(nameDraft()), null)).toThrow(/4,096/);
    expect(storage.writes).toBe(0); expect(storage.removals).toBe(0);
  });

  it.each([NaN, -1, Infinity, Number.MAX_SAFE_INTEGER + 1])("does not adopt or write through an invalid storage length %j", count => {
    const storage = new MemoryStorage(), draft = nameDraft(), kept = retain(storage, copy(draft));
    Object.defineProperty(storage, "length", { get: () => count });
    expect(readVisualRecoveries(storage, draft.scope)).toEqual({ entries: [], unreadable: [], limited: true });
    expect(() => writeVisualRecovery(storage, copy(draft, "new-writer"), null)).toThrow(/storage|keys|read/i);
    expect(storage.data).toEqual(new Map([[kept.key, kept.raw]]));
  });

  it.each(["duplicate", "missing"] as const)("does not adopt a %s key index as a complete recovery list", fault => {
    const storage = new MemoryStorage(), draft = nameDraft();
    const own = retain(storage, copy(draft)), peer = retain(storage, copy(nameDraft(source(), "Peer copy"), "window-b"));
    const originalKey = storage.key.bind(storage);
    storage.key = index => index === 1 ? fault === "duplicate" ? own.key : null : originalKey(index);
    expect(readVisualRecoveries(storage, draft.scope)).toEqual({ entries: [], unreadable: [], limited: true });
    expect(() => writeVisualRecovery(storage, copy(draft, "new-writer"), null)).toThrow(/changed|incomplete|keys/i);
    expect(storage.data).toEqual(new Map([[own.key, own.raw], [peer.key, peer.raw]]));
  });

  it("invalidates the complete candidate list when keys are reordered or replaced during its read", () => {
    for (const replace of [false, true]) {
      const storage = new MemoryStorage(), draft = nameDraft(), own = retain(storage, copy(draft));
      const peer = retain(storage, copy(nameDraft(source(), "Peer copy"), "window-b"));
      retain(storage, copy(nameDraft(source(), "Third copy"), "window-c"));
      let changed = false;
      storage.beforeGet = key => {
        if (key !== own.key || changed) return; changed = true;
        storage.data.delete(peer.key);
        storage.data.set(replace ? visualRecoveryKey(draft.scope, "replacement") : peer.key, peer.raw);
      };
      const observed = readVisualRecoveries(storage, draft.scope);
      expect(observed.limited).toBe(true); expect(observed.entries).toEqual([]);
      expect(storage.data.get(own.key)).toBe(own.raw);
    }
  });

  it("does not adopt an earlier value when another copy changes its bytes during the same scan", () => {
    const storage = new MemoryStorage(), draft = nameDraft(), own = retain(storage, copy(draft));
    const peer = retain(storage, copy(nameDraft(source(), "Peer copy"), "window-b"));
    const corrected = JSON.stringify(copy(nameDraft(source(), "Correction during read"), "window-a", 999));
    storage.beforeGet = key => { if (key === peer.key) storage.data.set(own.key, corrected); };
    const observed = readVisualRecoveries(storage, draft.scope);
    expect(observed.limited).toBe(true); expect(observed.entries).toEqual([]);
    expect(storage.data.get(own.key)).toBe(corrected);
  });

  it("does not write after another copy changes within the observed storage scan", () => {
    const storage = new MemoryStorage(), draft = nameDraft(), own = retain(storage, copy(draft));
    const peer = retain(storage, copy(nameDraft(source(), "Peer copy"), "window-b"));
    const corrected = JSON.stringify(copy(nameDraft(source(), "Correction during write scan"), "window-a", 999));
    storage.beforeGet = key => { if (key === peer.key) storage.data.set(own.key, corrected); };
    const writes = storage.writes;
    expect(() => writeVisualRecovery(storage, copy(draft, "new-writer"), null)).toThrow(/changed|incomplete|read/i);
    expect(storage.data.get(own.key)).toBe(corrected); expect(storage.writes).toBe(writes);
    expect(storage.data.size).toBe(2);
  });

  it("reports a key added during scanning without adopting a partial list", () => {
    const storage = new MemoryStorage(), draft = nameDraft(), own = retain(storage, copy(draft));
    const extraKey = visualRecoveryKey(draft.scope, "arrived-during-scan"), extraRaw = JSON.stringify(copy(draft, "arrived-during-scan"));
    storage.beforeGet = key => { if (key === own.key) storage.data.set(extraKey, extraRaw); };
    const observed = readVisualRecoveries(storage, draft.scope);
    expect(observed.limited).toBe(true); expect(observed.entries).toEqual([]);
    expect(storage.data.get(extraKey)).toBe(extraRaw);
  });
});
