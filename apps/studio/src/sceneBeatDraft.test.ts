import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import type { Beat, Project, ProjectEvent } from "./types";
import {
  acknowledgeSceneBeatDraft, BEAT_RECOVERY_PREFIX, beatCopyKey, beatHasInput, beatOutcomeMatches,
  beatPointerKey, beatUnconfirmed, createNewSceneBeatDraft, createSceneBeatDraft, decodeBeatCopy, decodeBeatFile,
  editSceneBeatDraft, inspectSceneBeatOutcome, prepareSceneBeatSubmission, readBeatCopies,
  reconcileSceneBeatDraft, releaseReviewedSceneBeat, resolveSceneBeatDraft, restoreSceneBeatDraft, retainReviewedBeatIntent,
  retainSceneBeatFailure, sceneBeatConflict, sceneBeatDraftValue, sceneBeatScope, writeBeatCopy,
} from "./sceneBeatDraft";
import type { BeatCopy, BeatCustody, BeatStorage, SceneBeatDraft } from "./sceneBeatDraft";

const uuid = (value: number) => "10000000-0000-4000-8000-" + value.toString(16).padStart(12, "0");
const r = (num: string, den = "1") => ({ num, den });
const sourceBeat = (): Beat => ({ id: uuid(80), label: "Fractional source", objective: "Saved objective",
  start: r("1001", "30000"), duration: r("1001", "30000") });
function source(): Project {
  const project = structuredClone(fixtureProject);
  project.locks = []; project.revision = 12;
  project.scenes[0].beats = [sourceBeat()]; project.scenes[0].duration = r("2");
  return project;
}
const draftFor = (project: Project) => createSceneBeatDraft(project, project.scenes[0].id, sourceBeat().id);
const typed = (project: Project) => editSceneBeatDraft(draftFor(project), "objective", "New authored objective");
function receipt(project: Project, draft: SceneBeatDraft): Project {
  const result = structuredClone(project), sent = draft.pending!;
  result.revision = sent.revision + 1;
  result.scenes[0].beats = sent.operation === "remove" ? [] : [structuredClone(sent.value!)];
  return result;
}
function eventFor(draft: SceneBeatDraft): ProjectEvent {
  return { revision: draft.pending!.revision + 1, created_at: "2026-10-21T00:00:00Z", change: draft.pending!.operation === "remove"
    ? { type: "remove_scene_beat", scene_id: draft.sceneId, beat_id: draft.beatId }
    : { type: "upsert_scene_beat", scene_id: draft.sceneId, beat: draft.pending!.value } };
}
class Store implements BeatStorage {
  values = new Map<string, string>();
  drop: ((key: string, value: string) => boolean) | null = null;
  afterSet: ((key: string, value: string) => void) | null = null;
  get length() { return this.values.size; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { if (!this.drop?.(key, value)) this.values.set(key, value); this.afterSet?.(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}
const custody = (id = 1, pointer: string | null = null): BeatCustody => ({ writerId: uuid(id), expectedRaw: null, expectedPointer: pointer });
const copy = (draft: SceneBeatDraft, id = 1): BeatCopy => ({ schema: "motionwright.scene-beat-recovery/1",
  scope: draft.scope, writerId: uuid(id), updatedAt: 1, draft });

describe("beat author intent and original response authority", () => {
  it("isolates all five identity dimensions even with equal saved values", () => {
    const project = source(), draft = draftFor(project);
    for (const field of ["id", "generation", "active_branch"] as const) {
      const foreign = { ...project, [field]: uuid(500) };
      expect(sceneBeatScope(foreign, draft.sceneId, draft.beatId)).not.toBe(draft.scope);
      expect(() => reconcileSceneBeatDraft(draft, foreign)).toThrow("another project");
    }
    expect(sceneBeatScope(project, uuid(501), draft.beatId)).not.toBe(draft.scope);
    expect(sceneBeatScope(project, draft.sceneId, uuid(502))).not.toBe(draft.scope);
  });

  it("retains every raw field, including incomplete time, without altering the saved beat", () => {
    const project = source(); let draft = draftFor(project);
    for (const [field, raw] of [["label", "  New label  "], ["objective", "  New\nobjective  \n"], ["start", "1e"], ["duration", "1001/"]] as const)
      draft = editSceneBeatDraft(draft, field, raw);
    expect(draft.raw).toEqual({ label: "  New label  ", objective: "  New\nobjective  \n", start: "1e", duration: "1001/" });
    expect(draft.sequence).toBe(4); expect(draft.base).toEqual(sourceBeat());
    expect(project.scenes[0].beats).toEqual([sourceBeat()]);
    expect(() => sceneBeatDraftValue(draft, project.scenes[0].duration)).toThrow("Complete");
  });

  it("advances an unrelated revision without erasing unsent raw input", () => {
    const project = source(), draft = editSceneBeatDraft(typed(project), "start", "1e");
    const unrelated = { ...project, title: "Peer title", revision: 13 };
    const next = reconcileSceneBeatDraft(draft, unrelated);
    expect(next.baseRevision).toBe(13); expect(next.raw).toEqual(draft.raw); expect(next.pending).toBeNull();
    expect(sceneBeatConflict(next, unrelated)).toBeNull();
  });

  it("keeps a concurrent retime until text with saved timing is chosen explicitly", () => {
    const project = source(), draft = typed(project), peer = structuredClone(project);
    peer.revision++; peer.scenes[0].beats[0].start = r("1", "4"); peer.scenes[0].beats[0].duration = r("1", "2");
    expect(reconcileSceneBeatDraft(draft, peer)).toBe(draft);
    expect(sceneBeatConflict(draft, peer)).toBe("changed");
    expect(() => prepareSceneBeatSubmission(draft, peer, "upsert", uuid(1))).toThrow("Compare");
    const merged = resolveSceneBeatDraft(draft, peer, "text");
    expect(merged.raw.objective).toBe(draft.raw.objective); expect(merged.raw.start).toBe("0.25");
    const sent = prepareSceneBeatSubmission(merged, peer, "upsert", uuid(2));
    expect(sent.draft.pending?.value).toEqual({ ...peer.scenes[0].beats[0], objective: draft.raw.objective });
  });

  it("preserves a draft beyond a shorter scene and blocks it until its times are repaired", () => {
    const project = source(); let draft = typed(project);
    draft = editSceneBeatDraft(draft, "start", "1"); draft = editSceneBeatDraft(draft, "duration", "1");
    const peer = structuredClone(project); peer.revision++; peer.scenes[0].duration = r("3", "2");
    expect(sceneBeatConflict(draft, peer)).toBe("retimed");
    const kept = resolveSceneBeatDraft(draft, peer, "draft");
    expect(kept.raw).toEqual(draft.raw);
    expect(() => prepareSceneBeatSubmission(kept, peer, "upsert", uuid(1))).toThrow("fit inside");
    const repaired = editSceneBeatDraft(kept, "duration", "0.5");
    expect(prepareSceneBeatSubmission(repaired, peer, "upsert", uuid(2)).draft.pending?.value?.duration).toEqual(r("1", "2"));
  });

  it("does not recreate a removed beat without a separate explicit recreation decision", () => {
    const project = source(), draft = typed(project), peer = structuredClone(project);
    peer.revision++; peer.scenes[0].beats = [];
    expect(sceneBeatConflict(draft, peer)).toBe("removed"); expect(reconcileSceneBeatDraft(draft, peer)).toBe(draft);
    expect(() => prepareSceneBeatSubmission(draft, peer, "upsert", uuid(1))).toThrow("Compare");
    expect(beatHasInput(resolveSceneBeatDraft(draft, peer, "saved"))).toBe(false);
    const recreate = resolveSceneBeatDraft(draft, peer, "draft");
    expect(recreate.allowCreate).toBe(true); expect(recreate.raw).toEqual(draft.raw);
    expect(prepareSceneBeatSubmission(recreate, peer, "upsert", uuid(2)).change).toEqual({
      type: "upsert_scene_beat", scene_id: draft.sceneId, beat: { ...sourceBeat(), objective: draft.raw.objective },
    });
  });

  it("arms the exact submitted snapshot before dispatch without rounding authored time", () => {
    const project = source(), prepared = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(1));
    expect(prepared.draft.pending).toMatchObject({ id: uuid(1), revision: 12, outcome: "UNKNOWN", sequence: 1,
      value: { ...sourceBeat(), objective: "New authored objective" } });
    expect(prepared.change).toEqual({ type: "upsert_scene_beat", scene_id: project.scenes[0].id, beat: prepared.draft.pending!.value });
    expect(() => prepareSceneBeatSubmission(prepared.draft, project, "upsert", uuid(2))).toThrow("unconfirmed");
  });

  it("cannot acknowledge from a matching document observation or another local attempt", () => {
    const project = source(), draft = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(1)).draft;
    const saved = receipt(project, draft);
    expect(reconcileSceneBeatDraft(draft, saved)).toBe(draft);
    expect(acknowledgeSceneBeatDraft(draft, uuid(2), saved)).toBe(draft);
    expect(beatUnconfirmed(draft)).toBe(true);
  });

  it("requires original owner, exact r+1, saved scene membership and exact beat values", () => {
    const project = source(), draft = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(1)).draft;
    const valid = receipt(project, draft), wrong: Project[] = [];
    for (const field of ["id", "generation", "active_branch"] as const) wrong.push({ ...valid, [field]: uuid(500) });
    wrong.push({ ...valid, revision: 12 }, { ...valid, revision: 14 });
    const missing = structuredClone(valid); missing.scenes = missing.scenes.filter(scene => scene.id !== draft.sceneId); wrong.push(missing);
    const different = structuredClone(valid); different.scenes[0].beats[0].objective = "Someone else's matching revision"; wrong.push(different);
    const retimed = structuredClone(valid); retimed.scenes[0].duration = r("3"); wrong.push(retimed);
    for (const value of wrong) expect(() => acknowledgeSceneBeatDraft(draft, uuid(1), value)).toThrow();
    expect(acknowledgeSceneBeatDraft(draft, uuid(1), valid).pending).toBeNull();
  });

  it("keeps later text and incomplete time when the original response arrives", () => {
    const project = source(); let draft = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(1)).draft;
    const saved = receipt(project, draft), submitted = structuredClone(draft.pending);
    draft = editSceneBeatDraft(draft, "objective", "  Later author text\n"); draft = editSceneBeatDraft(draft, "start", "1e");
    const next = acknowledgeSceneBeatDraft(draft, uuid(1), saved);
    expect(next.raw.objective).toBe("  Later author text\n"); expect(next.raw.start).toBe("1e"); expect(next.baseRevision).toBe(13);
    expect(draft.pending).toEqual(submitted); expect(next.pending).toBeNull(); expect(beatHasInput(next)).toBe(true);
  });

  it("returning to the old saved values during a request remains a new intent after ACK", () => {
    const project = source(); let draft = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(1)).draft;
    const saved = receipt(project, draft);
    draft = editSceneBeatDraft(draft, "objective", sourceBeat().objective);
    const next = acknowledgeSceneBeatDraft(draft, uuid(1), saved);
    expect(next.raw.objective).toBe(sourceBeat().objective); expect(beatHasInput(next)).toBe(true);
    const second = prepareSceneBeatSubmission(next, saved, "upsert", uuid(2));
    expect(second.draft.pending?.revision).toBe(13); expect(second.draft.pending?.value).toEqual(sourceBeat());
  });

  it("a removal ACK keeps later input and still requires explicit recreation", () => {
    const project = source(); let draft = prepareSceneBeatSubmission(typed(project), project, "remove", uuid(1)).draft;
    const saved = receipt(project, draft);
    draft = editSceneBeatDraft(draft, "objective", "Decision made after Remove");
    const next = acknowledgeSceneBeatDraft(draft, uuid(1), saved);
    expect(next.pending).toBeNull(); expect(next.raw.objective).toBe("Decision made after Remove");
    expect(next.allowCreate).toBe(false); expect(sceneBeatConflict(next, saved)).toBe("removed");
  });

  it("retains incomplete input under content/timing locks and matches UTF-8 domain bounds", () => {
    const project = source(), draft = typed(project);
    for (const kind of ["content", "timing"] as const) {
      const locked = { ...project, locks: [{ id: uuid(3), kind, resource: "scene:" + draft.sceneId, note: "Protected" }] };
      expect(() => prepareSceneBeatSubmission(draft, locked, "upsert", uuid(1))).toThrow("locked");
    }
    expect(() => sceneBeatDraftValue(editSceneBeatDraft(draft, "label", "é".repeat(81)), r("2"))).toThrow("160 UTF-8");
    expect(() => sceneBeatDraftValue(editSceneBeatDraft(draft, "objective", "é".repeat(2001)), r("2"))).toThrow("4000 UTF-8");
  });
});

describe("read-only uncertain beat outcome review", () => {
  it("requires actual contiguous reads and never treats matching event values as the original receipt", async () => {
    const project = source(), draft = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(1)).draft;
    const saved = receipt(project, draft), events = [eventFor(draft)], calls: string[] = [];
    const review = await inspectSceneBeatOutcome(draft, saved, {
      readProject: async () => { calls.push("project"); return saved; },
      readHistory: async (_project, after, limit) => { calls.push(`history:${after}:${limit}`); return events; },
    });
    expect(calls).toEqual(["project", "history:12:256", "project"]);
    expect(review.matchingValueRevisions).toEqual([13]); expect(beatUnconfirmed(draft)).toBe(true);
    const released = releaseReviewedSceneBeat(draft, saved, review);
    expect(released.pending).toBeNull(); expect(released.raw).toEqual(draft.raw);
    expect(sceneBeatConflict(released, saved)).toBe("changed");
    expect(() => prepareSceneBeatSubmission(released, saved, "upsert", uuid(2))).toThrow("Compare");
  });

  it("also reads the journal when the project revision is unchanged", async () => {
    const project = source(), draft = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(1)).draft;
    let count = 0;
    const review = await inspectSceneBeatOutcome(draft, project, {
      readProject: async () => project, readHistory: async () => { count++; return []; },
    });
    expect(count).toBe(1); expect(review.matchingValueRevisions).toEqual([]);
    expect(releaseReviewedSceneBeat(draft, project, review).raw).toEqual(draft.raw);
  });

  it("rejects gaps, future events and changes during the read", async () => {
    const project = source(), draft = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(1)).draft, saved = receipt(project, draft);
    for (const events of [[], [{ ...eventFor(draft), revision: 14 }], [{ ...eventFor(draft), created_at: "" }]]) {
      await expect(inspectSceneBeatOutcome(draft, saved, { readProject: async () => saved, readHistory: async () => events })).rejects.toThrow();
    }
    let reads = 0;
    await expect(inspectSceneBeatOutcome(draft, saved, {
      readProject: async () => ++reads === 1 ? saved : { ...saved, revision: 14 }, readHistory: async () => [eventFor(draft)],
    })).rejects.toThrow("changed during");
  });

  it("a reviewed result cannot release a different pending operation or newer saved revision", async () => {
    const project = source(), draft = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(1)).draft;
    const review = await inspectSceneBeatOutcome(draft, project, { readProject: async () => project, readHistory: async () => [] });
    expect(beatOutcomeMatches({ ...draft, pending: { ...draft.pending!, id: uuid(2) } }, project, review)).toBe(false);
    expect(beatOutcomeMatches(draft, { ...project, revision: 13 }, review)).toBe(false);
  });
});

describe("verified local beat custody", () => {
  it("persists raw input and verifies the data and writer pointer", () => {
    const project = source(), draft = editSceneBeatDraft(typed(project), "duration", "1e"), local = new Store(), session = new Store(), owner = custody();
    writeBeatCopy(local, session, draft, owner);
    expect(session.getItem(beatPointerKey(draft.scope))).toBe(owner.writerId);
    expect(decodeBeatCopy(owner.expectedRaw!, draft.scope, owner.writerId).draft.raw).toEqual(draft.raw);
    expect(local.getItem(beatCopyKey(draft.scope, owner.writerId))).toBe(owner.expectedRaw);
  });

  it("a recovered NOT_SENT label remains UNKNOWN rather than becoming retry authority", () => {
    const project = source(), armed = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(9)).draft;
    const stopped = retainSceneBeatFailure(armed, uuid(9), "Local writer failed before dispatch", true);
    expect(stopped.pending?.outcome).toBe("NOT_SENT");
    expect(decodeBeatCopy(JSON.stringify(copy(stopped)), stopped.scope, uuid(1)).draft.pending?.outcome).toBe("UNKNOWN");
  });

  it("rejects mismatched scopes, targets and dishonest submitted snapshots", () => {
    const project = source(), armed = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(9)).draft;
    for (const change of [
      { ...armed, beatId: uuid(777) },
      { ...armed, base: { ...armed.base!, start: r("2", "10") } },
      { ...armed, pending: { ...armed.pending!, value: { ...armed.pending!.value!, objective: "Different" } } },
      { ...armed, pending: { ...armed.pending!, sequence: armed.sequence + 1 } },
    ]) expect(() => decodeBeatCopy(JSON.stringify(copy(change)), armed.scope, uuid(1))).toThrow();
  });

  it("a silent data-write failure preserves prior copies and blocks admission", () => {
    const draft = typed(source()), local = new Store(), session = new Store(), owner = custody();
    local.values.set(BEAT_RECOVERY_PREFIX + "foreign", "Earlier original bytes"); local.drop = () => true;
    expect(() => writeBeatCopy(local, session, draft, owner)).toThrow("write was not confirmed");
    expect(owner.expectedRaw).toBeNull(); expect(session.length).toBe(0);
    expect([...local.values.values()]).toEqual(["Earlier original bytes"]);
  });

  it("a silent pointer-write failure retains a readable copy and permits a verified explicit retry", () => {
    const draft = typed(source()), local = new Store(), session = new Store(), owner = custody(); session.drop = () => true;
    expect(() => writeBeatCopy(local, session, draft, owner)).toThrow("pointer was not confirmed");
    expect(owner.expectedRaw).not.toBeNull(); expect(decodeBeatCopy(owner.expectedRaw!, draft.scope, owner.writerId).draft.raw).toEqual(draft.raw);
    session.drop = null; writeBeatCopy(local, session, draft, owner);
    expect(session.getItem(beatPointerKey(draft.scope))).toBe(owner.writerId);
  });

  it("rechecks data after pointer publication so a late peer replacement cannot be admitted", () => {
    const draft = typed(source()), local = new Store(), session = new Store(), owner = custody();
    session.afterSet = () => local.values.set(beatCopyKey(draft.scope, owner.writerId), "Peer replacement");
    expect(() => writeBeatCopy(local, session, draft, owner)).toThrow("changed before");
    expect(local.getItem(beatCopyKey(draft.scope, owner.writerId))).toBe("Peer replacement");
  });

  it("does not overwrite changed own-key bytes or a foreign tab pointer", () => {
    const draft = typed(source()), local = new Store(), session = new Store(), owner = custody();
    writeBeatCopy(local, session, draft, owner); local.values.set(beatCopyKey(draft.scope, owner.writerId), "Peer bytes");
    expect(() => writeBeatCopy(local, session, draft, owner)).toThrow("another window");
    expect(local.getItem(beatCopyKey(draft.scope, owner.writerId))).toBe("Peer bytes");
    const next = custody(2, owner.writerId); session.values.set(beatPointerKey(draft.scope), uuid(999));
    expect(() => writeBeatCopy(local, session, draft, next)).toThrow("pointer");
  });

  it("keeps clean checkpoints immutable and new edits under a separate writer", () => {
    const project = source(), clean = draftFor(project), local = new Store(), session = new Store(), owner = custody();
    writeBeatCopy(local, session, clean, owner); const old = owner.expectedRaw;
    expect(() => writeBeatCopy(local, session, typed(project), owner)).toThrow("immutable");
    writeBeatCopy(local, session, typed(project), custody(2, owner.writerId));
    expect(local.getItem(beatCopyKey(clean.scope, owner.writerId))).toBe(old);
  });

  it("verifies unchanged input without rewriting its original timestamp or bytes", () => {
    const draft = typed(source()), local = new Store(), session = new Store(), owner = custody();
    writeBeatCopy(local, session, draft, owner); const original = owner.expectedRaw;
    local.afterSet = () => { throw new Error("An unchanged draft should not be rewritten"); };
    writeBeatCopy(local, session, draft, owner); expect(owner.expectedRaw).toBe(original);
  });

  it("a failed confirmation checkpoint leaves the armed original untouched", () => {
    const project = source(), draft = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(9)).draft;
    const local = new Store(), session = new Store(), owner = custody(); writeBeatCopy(local, session, draft, owner);
    const original = owner.expectedRaw, confirmed = acknowledgeSceneBeatDraft(draft, uuid(9), receipt(project, draft)), next = custody(2, owner.writerId);
    local.drop = key => key === beatCopyKey(draft.scope, next.writerId);
    expect(() => writeBeatCopy(local, session, confirmed, next)).toThrow("write was not confirmed");
    expect(local.getItem(beatCopyKey(draft.scope, owner.writerId))).toBe(original);
    expect(session.getItem(beatPointerKey(draft.scope))).toBe(owner.writerId);
    expect(decodeBeatCopy(original!, draft.scope, owner.writerId).draft.pending?.outcome).toBe("UNKNOWN");
  });

  it("a cloned tab forks its mutable key while retaining each author's input", () => {
    const draft = typed(source()), local = new Store(), originalSession = new Store(), originalOwner = custody();
    writeBeatCopy(local, originalSession, draft, originalOwner); const original = originalOwner.expectedRaw;
    const clonedSession = new Store(); clonedSession.values = new Map(originalSession.values);
    const a = custody(2, originalOwner.writerId), b = custody(3, originalOwner.writerId);
    writeBeatCopy(local, originalSession, editSceneBeatDraft(draft, "objective", "Window A"), a);
    writeBeatCopy(local, clonedSession, editSceneBeatDraft(draft, "objective", "Window B"), b);
    expect(local.getItem(beatCopyKey(draft.scope, originalOwner.writerId))).toBe(original);
    expect(decodeBeatCopy(a.expectedRaw!, draft.scope, a.writerId).draft.raw.objective).toBe("Window A");
    expect(decodeBeatCopy(b.expectedRaw!, draft.scope, b.writerId).draft.raw.objective).toBe("Window B");
    expect(originalSession.getItem(beatPointerKey(draft.scope))).not.toBe(clonedSession.getItem(beatPointerKey(draft.scope)));
  });

  it("a failed reviewed-intent release leaves the new pointer selecting UNKNOWN", async () => {
    const project = source(), draft = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(9)).draft;
    const review = await inspectSceneBeatOutcome(draft, project, { readProject: async () => project, readHistory: async () => [] });
    const next = releaseReviewedSceneBeat(draft, project, review), local = new Store(), session = new Store(), owner = custody();
    writeBeatCopy(local, session, draft, owner); const original = owner.expectedRaw;
    local.drop = (key, raw) => key === beatCopyKey(draft.scope, uuid(2)) && JSON.parse(raw).draft.pending === null;
    expect(() => retainReviewedBeatIntent(local, session, draft, next, owner, uuid(2))).toThrow("write was not confirmed");
    expect(local.getItem(beatCopyKey(draft.scope, owner.writerId))).toBe(original);
    expect(session.getItem(beatPointerKey(draft.scope))).toBe(uuid(2));
    expect(decodeBeatCopy(local.getItem(beatCopyKey(draft.scope, uuid(2)))!, draft.scope, uuid(2)).draft.pending?.outcome).toBe("UNKNOWN");
  });

  it("lists only the selected owner and scene, retaining corrupt bytes as an explicit unreadable entry", () => {
    const project = source(), draft = typed(project), local = new Store(), session = new Store();
    writeBeatCopy(local, session, draft, custody());
    const foreign = { ...project, generation: uuid(888) }; writeBeatCopy(local, new Store(), typed(foreign), custody(2));
    local.values.set(beatCopyKey(draft.scope, uuid(3)), "CORRUPT ORIGINAL");
    const read = readBeatCopies(local, project, project.scenes[0].id);
    expect(read.entries).toHaveLength(2); expect(read.entries.find(entry => entry.writerId === uuid(3))?.value).toBeNull();
    expect(local.getItem(beatCopyKey(draft.scope, uuid(3)))).toBe("CORRUPT ORIGINAL");
  });

  it("refuses a full recovery namespace without deleting any earlier input", () => {
    const draft = typed(source()), local = new Store(), session = new Store();
    local.values.set(BEAT_RECOVERY_PREFIX + "large-earlier-copy", "x".repeat(4 * 1024 * 1024));
    const original = new Map(local.values);
    expect(() => writeBeatCopy(local, session, draft, custody())).toThrow("recovery is full");
    expect(local.values).toEqual(original); expect(session.length).toBe(0);
  });

  it("backs a newly requested gap beat before creation and still requires an original response", () => {
    const project = source(), beat = { ...sourceBeat(), id: uuid(100), start: r("0"), duration: r("1001", "30000") };
    const draft = createNewSceneBeatDraft(project, project.scenes[0].id, beat), prepared = prepareSceneBeatSubmission(draft, project, "upsert", uuid(9));
    const local = new Store(), session = new Store(), owner = custody(); writeBeatCopy(local, session, prepared.draft, owner);
    expect(decodeBeatCopy(owner.expectedRaw!, draft.scope, owner.writerId).draft.pending?.value).toEqual(beat);
    expect(prepared.change).toEqual({ type: "upsert_scene_beat", scene_id: project.scenes[0].id, beat });
  });

  it("imports a strict portable copy only as a candidate for its original selected scene", () => {
    const project = source(), draft = editSceneBeatDraft(typed(project), "start", "1e"), raw = JSON.stringify(copy(draft));
    expect(decodeBeatFile(raw, project, draft.sceneId).draft).toEqual(draft);
    for (const field of ["id", "generation", "active_branch"] as const)
      expect(() => decodeBeatFile(raw, { ...project, [field]: uuid(999) }, draft.sceneId)).toThrow("another project");
    expect(() => decodeBeatFile(raw, project, uuid(888))).toThrow("another project");
    expect(project.scenes[0].beats).toEqual([sourceBeat()]);
  });

  it("keeps imported pending NOT_SENT unconfirmed and rejects malformed or oversized files", () => {
    const project = source(), armed = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(9)).draft;
    const stopped = retainSceneBeatFailure(armed, uuid(9), "Could not verify backup", true);
    expect(decodeBeatFile(JSON.stringify(copy(stopped)), project, stopped.sceneId).draft.pending?.outcome).toBe("UNKNOWN");
    expect(() => decodeBeatFile("x".repeat(128 * 1024 + 1), project, stopped.sceneId)).toThrow("128 KiB");
    expect(() => decodeBeatFile("{}", project, stopped.sceneId)).toThrow("identity");
  });
});

describe("explicit beat recovery and automatic observation", () => {
  it("retains a chosen clean checkpoint when the saved beat has changed", () => {
    const project = source(), clean = draftFor(project), earlier = structuredClone(clean), peer = structuredClone(project);
    peer.revision++; peer.scenes[0].beats[0].objective = "Saved peer objective";
    const restored = restoreSceneBeatDraft(clean, peer);
    expect(restored.raw).toEqual(earlier.raw); expect(restored.base).toEqual(earlier.base);
    expect(restored.intent).toBe(true); expect(restored.pending).toBeNull();
    expect(sceneBeatConflict(restored, peer)).toBe("changed");
    expect(() => prepareSceneBeatSubmission(restored, peer, "upsert", uuid(901))).toThrow("Compare");
    expect(clean).toEqual(earlier);
  });

  it("keeps an explicitly restored matching checkpoint as intent through a later saved change", () => {
    const project = source(), clean = draftFor(project), restored = restoreSceneBeatDraft(clean, project);
    expect(restored.intent).toBe(true); expect(restored.raw).toEqual(clean.raw);
    const peer = structuredClone(project); peer.revision++; peer.scenes[0].beats[0].objective = "Later saved objective";
    const observed = reconcileSceneBeatDraft(restored, peer);
    expect(observed.raw).toEqual(clean.raw); expect(sceneBeatConflict(observed, peer)).toBe("changed");
  });

  it("still follows a newer saved beat when a clean checkpoint is recovered automatically", () => {
    const project = source(), clean = draftFor(project), peer = structuredClone(project);
    peer.revision++; peer.scenes[0].beats[0].objective = "New saved objective";
    const observed = reconcileSceneBeatDraft(clean, peer);
    expect(observed.raw.objective).toBe("New saved objective");
    expect(observed.baseRevision).toBe(peer.revision); expect(observed.intent).toBe(false);
    expect(observed.pending).toBeNull(); expect(sceneBeatConflict(observed, peer)).toBeNull();
    expect(clean.raw.objective).toBe("Saved objective");
  });

  it("preserves an imported UNKNOWN attempt and later raw input without accepting matching props or another scope", () => {
    const project = source(), armed = prepareSceneBeatSubmission(typed(project), project, "upsert", uuid(902)).draft;
    const later = editSceneBeatDraft(armed, "start", "1e"), observed = receipt(project, armed);
    const restored = restoreSceneBeatDraft(later, observed);
    expect(restored.pending).toEqual(later.pending); expect(restored.raw).toEqual(later.raw);
    expect(restored.sequence).toBe(later.sequence); expect(beatUnconfirmed(restored)).toBe(true);
    expect(() => prepareSceneBeatSubmission(restored, observed, "upsert", uuid(903))).toThrow();
    expect(() => prepareSceneBeatSubmission(restoreSceneBeatDraft(later, project), project, "upsert", uuid(903))).toThrow("unconfirmed");
    for (const field of ["id", "generation", "active_branch"] as const)
      expect(() => restoreSceneBeatDraft(later, { ...observed, [field]: uuid(904) })).toThrow("another project");
  });

  it("does not authorize recreation when an explicitly chosen clean checkpoint targets a removed beat", () => {
    const project = source(), clean = draftFor(project), removed = structuredClone(project);
    removed.revision++; removed.scenes[0].beats = [];
    const restored = restoreSceneBeatDraft(clean, removed);
    expect(restored.raw).toEqual(clean.raw); expect(restored.intent).toBe(true);
    expect(restored.allowCreate).toBe(false); expect(sceneBeatConflict(restored, removed)).toBe("removed");
    expect(() => prepareSceneBeatSubmission(restored, removed, "upsert", uuid(905))).toThrow("Compare");
  });
});
