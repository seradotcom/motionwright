import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { emptyNarrativeEditing, narrativeEditChange, narrativeEditingScope } from "./narrativeEditing";
import { acknowledgeEditorialSubmission, adoptEditorialFile, compareEditorialField, decodeEditorialCopy, editEditorialField,
  editorialCopies, editorialCopyKey, editorialFieldConflict, editorialPointerKey, editorialRaw, editorialReviewCurrent, exportEditorialDraft,
  forkEditorialWriter, importEditorialDraft, inspectEditorialOutcome, openEditorialSession, persistEditorialSession,
  prepareEditorialSubmission, rebaseReviewedEditorialFields, restoreEditorialCopy, retainEditorialFailure, retryEditorialCustody } from "./narrativeEditingDraft";
import type { EditorialSession } from "./narrativeEditingDraft";
import type { Project, ProjectEvent } from "./types";

const id = (n: number): ReturnType<Crypto["randomUUID"]> => `018f0000-0000-7000-8000-${String(n).padStart(12, "0")}`;
class Memory {
  data = new Map<string, string>(); onGet: ((key: string) => void) | null = null; silent = false;
  get length() { return this.data.size; } key(i: number) { return [...this.data.keys()][i] ?? null; }
  getItem(key: string) { this.onGet?.(key); return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { if (!this.silent) this.data.set(key, value); }
}
function setup() {
  const project = structuredClone(fixtureProject); project.schema_version = 2; project.locks = [];
  project.narrative = { premise: "Fixture source", beats: [], protected_sections: [], editing: emptyNarrativeEditing() };
  const storage = new Memory(), pointers = new Memory(), session = openEditorialSession(project, storage, pointers);
  return { project, storage, pointers, session };
}
function armed() {
  const setupValue = setup(), { project, session, storage, pointers } = setupValue;
  editEditorialField(session, project, "script.text", "Sent wording"); persistEditorialSession(session, storage, pointers);
  const change = narrativeEditChange(project, { kind: "set_script", script_id: id(800), expected_version: null, text: "Sent wording" });
  const prepared = prepareEditorialSubmission(session, project, change, ["script.text"], null, id(801)); session.draft = prepared.draft;
  forkEditorialWriter(session); persistEditorialSession(session, storage, pointers);
  const receipt = structuredClone(project); receipt.revision++;
  receipt.narrative.editing!.script = { id: id(800), version: 1, text: "Sent wording", sha256: "a".repeat(64), approval: null, last_release: null };
  return { ...setupValue, change, prepared, receipt };
}
function event(project: Project, change: unknown): ProjectEvent {
  return { revision: project.revision, change: JSON.parse(JSON.stringify(change)), created_at: "2026-10-21T00:00:00Z" };
}
describe("Editorial raw input, original ACK and exact recovery custody", () => {
  it("preserves raw partial time and script whitespace through a new runtime", () => {
    const { project, session, storage, pointers } = setup();
    editEditorialField(session, project, "proposal.duration", "1e"); editEditorialField(session, project, "script.text", "  Words\n  "); persistEditorialSession(session, storage, pointers);
    const reopened = openEditorialSession(project, storage, pointers);
    expect(editorialRaw(reopened.draft, project, "proposal.duration")).toBe("1e"); expect(editorialRaw(reopened.draft, project, "script.text")).toBe("  Words\n  ");
    expect(reopened.custody.writer).not.toBe(session.custody.writer);
  });
  it("isolates draft ownership by project, generation and branch", () => {
    const { project, session, storage, pointers } = setup(); editEditorialField(session, project, "script.text", "Only this scope"); persistEditorialSession(session, storage, pointers);
    for (const field of ["id", "generation", "active_branch"] as const) { const foreign = structuredClone(project); foreign[field] = id(999); expect(openEditorialSession(foreign, storage, pointers).draft.fields).toEqual({}); }
  });
  it("a prop observation with the saved value never consumes an UNKNOWN intent", () => {
    const { session, receipt, prepared } = armed(); expect(editorialFieldConflict(session.draft, receipt, "script.text")).toBe(true);
    expect(session.draft.pending?.id).toBe(prepared.id); expect(session.draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("only the original intent ID and original command may acknowledge a receipt", () => {
    const { session, change, receipt, prepared } = armed();
    expect(() => acknowledgeEditorialSubmission(session.draft, id(999), change, receipt)).toThrow(/unrelated/);
    const wrong = structuredClone(change); if (wrong.type === "edit_narrative" && wrong.edit.kind === "set_script") wrong.edit.text = "Other";
    expect(() => acknowledgeEditorialSubmission(session.draft, prepared.id, wrong, receipt)).toThrow(/unrelated/);
    expect(acknowledgeEditorialSubmission(session.draft, prepared.id, change, receipt).fields).toEqual({});
  });
  it("late typing including a return to the old base survives the original ACK", () => {
    const { session, project, change, receipt, prepared } = armed();
    editEditorialField(session, project, "script.text", "Later"); editEditorialField(session, project, "script.text", "");
    const next = acknowledgeEditorialSubmission(session.draft, prepared.id, change, receipt);
    expect(next.fields["script.text"].raw).toBe(""); expect(next.fields["script.text"].base).toBe("Sent wording"); expect(next.pending).toBeNull();
  });
  it("an ACK for one group does not consume independent duration input", () => {
    const { session, project, change, receipt, prepared } = armed(); editEditorialField(session, project, "proposal.duration", "1001/30000");
    const next = acknowledgeEditorialSubmission(session.draft, prepared.id, change, receipt); expect(next.fields["proposal.duration"].raw).toBe("1001/30000");
  });
  it("silent copy write failure blocks preparation until its exact attempt is reconciled", () => {
    const { session, project, storage, pointers } = setup(); editEditorialField(session, project, "script.text", "Keep"); storage.silent = true;
    expect(() => persistEditorialSession(session, storage, pointers)).toThrow(/write\/readback/);
    expect(() => prepareEditorialSubmission(session, project, narrativeEditChange(project, { kind: "set_script", script_id: id(800), expected_version: null, text: "Keep" }), ["script.text"])).toThrow(/Confirm/);
    storage.silent = false; retryEditorialCustody(session, storage, pointers); expect(session.custody.attempt).toBeNull();
  });
  it("silent pointer write failure retains the attempted raw copy and blocks new intent", () => {
    const { session, project, storage, pointers } = setup(); editEditorialField(session, project, "script.text", "Keep"); pointers.silent = true;
    expect(() => persistEditorialSession(session, storage, pointers)).toThrow(/pointer/); expect(session.custody.attempt === null).toBe(false); expect(storage.length).toBe(1);
  });
  it("the final data read detects corruption caused while confirming the pointer", () => {
    const { session, project, storage, pointers } = setup(); editEditorialField(session, project, "script.text", "Keep");
    const key = editorialCopyKey(session.draft.scope, session.custody.writer); let armedPointer = false;
    const original = pointers.setItem.bind(pointers); pointers.setItem = (name, value) => { original(name, value); armedPointer = true; };
    pointers.onGet = () => { if (armedPointer) storage.data.set(key, "corrupt after pointer"); };
    expect(() => persistEditorialSession(session, storage, pointers)).toThrow(/final pointer/); expect(session.custody.expectedRaw).toBeNull();
  });
  it("another writer's pointer cannot be adopted by retrying an incomplete own write", () => {
    const { session, project, storage, pointers } = setup(); editEditorialField(session, project, "script.text", "Own"); pointers.silent = true;
    expect(() => persistEditorialSession(session, storage, pointers)).toThrow(); pointers.silent = false;
    pointers.setItem(editorialPointerKey(session.draft.scope), id(999)); expect(() => retryEditorialCustody(session, storage, pointers)).toThrow(/exact bytes\/pointer/);
  });
  it("a cloned tab forks before editing and cannot overwrite the original unknown key", () => {
    const { session, project, storage, pointers } = armed(); const key = editorialCopyKey(session.draft.scope, session.custody.writer), original = storage.getItem(key);
    const clonePointers = new Memory(); clonePointers.data = new Map(pointers.data);
    const cloned = openEditorialSession(project, storage, clonePointers); editEditorialField(cloned, project, "script.text", "Clone input"); persistEditorialSession(cloned, storage, clonePointers);
    expect(storage.getItem(key)).toBe(original); expect(cloned.draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("a transiently unreadable source containing UNKNOWN is reread before any new operation", () => {
    const { session, project, storage, pointers } = armed(); const key = editorialCopyKey(session.draft.scope, session.custody.writer), original = storage.getItem(key)!;
    storage.onGet = name => { if (name === key) throw new Error("Temporary source read failure"); };
    const reopened = openEditorialSession(project, storage, pointers); storage.onGet = null;
    expect(reopened.failedSource === null).toBe(false); editEditorialField(reopened, project, "script.text", "Later while blocked");
    retryEditorialCustody(reopened, storage, pointers); expect(storage.getItem(key)).toBe(original);
    expect(reopened.draft.pending?.outcome).toBe("UNKNOWN"); expect(reopened.draft.fields["script.text"].raw).toBe("Later while blocked");
    expect(() => prepareEditorialSubmission(reopened, project, narrativeEditChange(project, { kind: "set_script", script_id: id(800), expected_version: null, text: "Later while blocked" }), ["script.text"])).toThrow(/UNKNOWN/);
  });
  it("a missing pointed copy never becomes a fresh saveable session through Retry", () => {
    const { project, storage, pointers } = setup(); pointers.setItem(editorialPointerKey(narrativeEditingScope(project)), id(999));
    const reopened = openEditorialSession(project, storage, pointers); expect(() => retryEditorialCustody(reopened, storage, pointers)).toThrow(/still missing/); expect(reopened.failedSource === null).toBe(false);
  });
  it("a recovered NOT_SENT record is conservative UNKNOWN while the live diagnostic stays exact", () => {
    const { session, prepared, storage, pointers } = armed(); session.draft = retainEditorialFailure(session.draft, prepared.id, "Before callback", true);
    persistEditorialSession(session, storage, pointers); expect(session.draft.pending?.outcome).toBe("NOT_SENT");
    expect(decodeEditorialCopy(session.custody.expectedRaw!, session.draft.scope, session.custody.writer).draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("ACK retention failure leaves the immutable armed operation available on reopen", () => {
    const { session, project, change, prepared, receipt, storage, pointers } = armed(); const armedKey = editorialCopyKey(session.draft.scope, session.custody.writer), original = storage.getItem(armedKey);
    const next = acknowledgeEditorialSubmission(session.draft, prepared.id, change, receipt); forkEditorialWriter(session); session.draft = next;
    pointers.silent = true; expect(() => persistEditorialSession(session, storage, pointers)).toThrow(/pointer/);
    expect(storage.getItem(armedKey)).toBe(original); expect(openEditorialSession(project, storage, pointers).draft.pending?.id).toBe(prepared.id);
  });
  it("complete history observations permit review but do not acknowledge or replay", async () => {
    const { session, change, receipt, prepared } = armed(); const events = [event(receipt, change)];
    const review = await inspectEditorialOutcome(session, receipt, { readProject: async () => receipt, readHistory: async () => events });
    expect(review.matchingRevisions).toEqual([receipt.revision]); expect(session.draft.pending?.id).toBe(prepared.id); expect(editorialReviewCurrent(session, receipt, review)).toBe(true);
  });
  it("history holes, changed project and late edits reject stale outcome review", async () => {
    const { session, project, change, receipt } = armed();
    await expect(inspectEditorialOutcome(session, receipt, { readProject: async () => receipt, readHistory: async () => [] })).rejects.toThrow(/incomplete/);
    await expect(inspectEditorialOutcome(session, project, { readProject: async () => receipt, readHistory: async () => [] })).rejects.toThrow(/Reload/);
    await expect(inspectEditorialOutcome(session, receipt, { readProject: async () => receipt, readHistory: async () => { editEditorialField(session, receipt, "proposal.reason", "Later"); return [event(receipt, change)]; } })).rejects.toThrow(/changed during/);
  });
  it("reviewed comparison retains UNKNOWN even after an explicit choice of a new base", async () => {
    const { session, change, receipt, storage, pointers, prepared } = armed();
    const review = await inspectEditorialOutcome(session, receipt, { readProject: async () => receipt, readHistory: async () => [event(receipt, change)] });
    const next = rebaseReviewedEditorialFields(session, receipt, review, storage, pointers);
    expect(session.draft.pending?.id).toBe(prepared.id); expect(editorialReviewCurrent(session, receipt, next)).toBe(true);
    const reopened = openEditorialSession(receipt, storage, pointers); expect(reopened.draft.pending?.id).toBe(prepared.id);
    expect(() => prepareEditorialSubmission(reopened, receipt, narrativeEditChange(receipt, { kind: "set_script", script_id: id(800), expected_version: 1, text: "Another explicit choice" }), ["script.text"])).toThrow(/UNKNOWN/);
  });
  it("observed foreign copy bytes or pointer block a reviewed new intent before preserving its base", async () => {
    const { session, change, receipt, storage, pointers } = armed();
    const review = await inspectEditorialOutcome(session, receipt, { readProject: async () => receipt, readHistory: async () => [event(receipt, change)] });
    storage.data.set(editorialCopyKey(session.draft.scope, session.custody.writer), "foreign replacement");
    expect(() => rebaseReviewedEditorialFields(session, receipt, review, storage, pointers)).toThrow(/outside this writer/); expect(session.draft.pending === null).toBe(false);
  });
  it("author choice preserves the prior copy before rebasing conflicting raw input", () => {
    const { session, project, storage, pointers } = setup(); editEditorialField(session, project, "script.text", "Typed"); persistEditorialSession(session, storage, pointers);
    const key = editorialCopyKey(session.draft.scope, session.custody.writer), old = storage.getItem(key);
    const current = structuredClone(project); current.narrative.editing!.script = { id: id(800), version: 1, text: "Saved elsewhere", sha256: "a".repeat(64), approval: null, last_release: null };
    expect(editorialFieldConflict(session.draft, current, "script.text")).toBe(true); forkEditorialWriter(session); compareEditorialField(session, current, "script.text", false); persistEditorialSession(session, storage, pointers);
    expect(storage.getItem(key)).toBe(old); expect(session.draft.fields["script.text"].raw).toBe("Typed"); expect(session.draft.fields["script.text"].base).toBe("Saved elsewhere");
  });
  it("export remains available without storage and import is explicit scoped comparison data", () => {
    const { session, project } = setup(); editEditorialField(session, project, "proposal.duration", "1e");
    const file = exportEditorialDraft(session.draft), imported = importEditorialDraft(file, session.draft.scope);
    expect(imported.fields["proposal.duration"].raw).toBe("1e"); expect(() => importEditorialDraft(file, JSON.stringify([id(999), project.generation, project.active_branch]))).toThrow(/foreign/);
  });
  it("a clean checkpoint is not offered as recovered historical author content", () => {
    const { session, storage, pointers } = setup(); persistEditorialSession(session, storage, pointers);
    expect(() => restoreEditorialCopy(session, session.custody.expectedRaw!, session.custody.writer, storage, pointers)).toThrow(/clean checkpoint/);
  });
  it("UNKNOWN cannot be discarded through copy restore or file import", () => {
    const { session, project, storage, pointers } = armed(); const blank = openEditorialSession(project, new Memory(), new Memory()).draft;
    expect(() => adoptEditorialFile(session, blank, storage, pointers)).toThrow(/UNKNOWN/);
    expect(() => restoreEditorialCopy(session, session.custody.expectedRaw!, session.custody.writer, storage, pointers)).toThrow(/UNKNOWN/);
  });
  it("failed retention of an explicitly imported UNKNOWN cannot roll back to a saveable clean buffer", () => {
    const unknown = armed(), { session, project, storage, pointers } = setup();
    const imported = importEditorialDraft(exportEditorialDraft(unknown.session.draft), session.draft.scope);
    persistEditorialSession(session, storage, pointers);
    const originalSet = pointers.setItem.bind(pointers); let writes = 0;
    pointers.setItem = (key, value) => { writes++; if (writes < 2) originalSet(key, value); };
    expect(() => adoptEditorialFile(session, imported, storage, pointers)).toThrow(/pointer/);
    expect(session.draft.pending?.id).toBe(unknown.prepared.id);
    pointers.setItem = originalSet; retryEditorialCustody(session, storage, pointers);
    expect(session.draft.pending?.outcome).toBe("UNKNOWN");
    expect(() => prepareEditorialSubmission(session, project, unknown.change, ["script.text"])).toThrow(/UNKNOWN/);
  });
});

describe("Editorial first-observation recovery regressions", () => {
  it("a failed first pointer read recovers its UNKNOWN while retaining a later explicit blank", () => {
    const { session, project, storage, pointers, prepared, change } = armed();
    const sourceKey = editorialCopyKey(session.draft.scope, session.custody.writer), original = storage.getItem(sourceKey);
    const pointerKey = editorialPointerKey(session.draft.scope);
    pointers.onGet = key => { if (key === pointerKey) throw new Error("First pointer read unavailable"); };
    const reopened = openEditorialSession(project, storage, pointers); pointers.onGet = null;
    editEditorialField(reopened, project, "script.text", "Typed while blocked");
    editEditorialField(reopened, project, "script.text", "");
    expect(() => persistEditorialSession(reopened, storage, pointers)).toThrow(/failed source/);
    retryEditorialCustody(reopened, storage, pointers);
    expect(reopened.failedSource).toBeNull(); expect(reopened.draft.pending?.id).toBe(prepared.id);
    expect(reopened.draft.fields["script.text"].raw).toBe("");
    expect(storage.getItem(sourceKey)).toBe(original);
    expect(openEditorialSession(project, storage, pointers).draft.fields["script.text"].raw).toBe("");
    expect(() => prepareEditorialSubmission(reopened, project, change, ["script.text"])).toThrow(/UNKNOWN/);
  });

  it("a first successful empty pointer read retains an explicit old base against newer Project props", () => {
    const { project, storage, pointers } = setup();
    pointers.onGet = () => { throw new Error("Initial pointer read unavailable"); };
    const reopened = openEditorialSession(project, storage, pointers); pointers.onGet = null;
    editEditorialField(reopened, project, "script.text", "Temporary text");
    editEditorialField(reopened, project, "script.text", "");
    const newer = structuredClone(project); newer.revision++;
    newer.narrative.editing!.script = { id: id(800), version: 1, text: "Saved elsewhere", sha256: "a".repeat(64), approval: null, last_release: null };
    retryEditorialCustody(reopened, storage, pointers);
    expect(editorialRaw(reopened.draft, newer, "script.text")).toBe("");
    expect(editorialFieldConflict(reopened.draft, newer, "script.text")).toBe(true);
    expect(openEditorialSession(newer, storage, pointers).draft.fields["script.text"].raw).toBe("");
  });

  it("same-writer replacement of observed corrupt bytes cannot become permission to save", () => {
    const { session, project, storage, pointers, change } = armed();
    const key = editorialCopyKey(session.draft.scope, session.custody.writer), corrupt = "{broken original UNKNOWN copy";
    storage.data.set(key, corrupt); const reopened = openEditorialSession(project, storage, pointers);
    editEditorialField(reopened, project, "script.text", "Keep later author input");
    const before = structuredClone(reopened.draft);
    const replacement = JSON.stringify({ schema: "motionwright.editorial-copy/1", writer: session.custody.writer, draft: setup().session.draft });
    storage.data.set(key, replacement);
    expect(() => retryEditorialCustody(reopened, storage, pointers)).toThrow(/original.*bytes changed/);
    expect(reopened.draft).toEqual(before); expect(reopened.failedSource?.raw).toBe(corrupt);
    expect(pointers.getItem(editorialPointerKey(session.draft.scope))).toBe(session.custody.writer);
    expect(storage.getItem(key)).toBe(replacement);
    expect(() => prepareEditorialSubmission(reopened, project, change, ["script.text"])).toThrow(/Confirm/);
  });

  it("a successfully decoded original must still match before publishing the first new writer", () => {
    const { session, project, storage, pointers, prepared } = armed();
    const key = editorialCopyKey(session.draft.scope, session.custody.writer);
    const reopened = openEditorialSession(project, storage, pointers);
    const replacement = JSON.stringify({ schema: "motionwright.editorial-copy/1", writer: session.custody.writer, draft: setup().session.draft });
    storage.data.set(key, replacement); editEditorialField(reopened, project, "script.text", "Later input");
    const count = storage.length;
    expect(() => persistEditorialSession(reopened, storage, pointers)).toThrow(/original.*bytes changed/);
    expect(storage.length).toBe(count); expect(reopened.draft.pending?.id).toBe(prepared.id);
    expect(pointers.getItem(editorialPointerKey(session.draft.scope))).toBe(session.custody.writer);
  });

  it("an original observed during a partial listing remains pinned when a later index fails", () => {
    const { session, project, storage, pointers } = armed();
    const key = editorialCopyKey(session.draft.scope, session.custody.writer), original = storage.getItem(key)!;
    storage.onGet = name => { if (name === key) throw new Error("First copy read unavailable"); };
    const reopened = openEditorialSession(project, storage, pointers); storage.onGet = null;
    storage.key = index => { if (index === 0) return key; throw new Error("Later storage index unavailable"); };
    expect(() => editorialCopies(storage, session.draft.scope, reopened.failedSource)).toThrow(/Later storage index/);
    expect(reopened.failedSource?.raw).toBe(original);
    storage.data.set(key, JSON.stringify({ schema: "motionwright.editorial-copy/1", writer: session.custody.writer, draft: setup().session.draft }));
    expect(() => retryEditorialCustody(reopened, storage, pointers)).toThrow(/original.*bytes changed/);
    expect(reopened.failedSource?.raw).toBe(original);
  });

  it("an observed null during partial listing cannot later be replaced by a clean same-writer copy", () => {
    const { session, project, storage, pointers, change } = armed();
    const key = editorialCopyKey(session.draft.scope, session.custody.writer);
    storage.onGet = name => { if (name === key) throw new Error("First copy read unavailable"); };
    const reopened = openEditorialSession(project, storage, pointers); storage.onGet = null;
    editEditorialField(reopened, project, "script.text", "Later author input after the read failure");
    const before = structuredClone(reopened.draft);
    // key(0) can observe W immediately before another runtime removes W.
    // A subsequent index failure must not undo the successful null read.
    storage.data.delete(key); storage.data.set("unrelated-list-entry", "retained");
    storage.key = index => { if (index === 0) return key; throw new Error("Later storage index unavailable"); };
    expect(() => editorialCopies(storage, session.draft.scope, reopened.failedSource)).toThrow(/Later storage index/);
    expect(reopened.failedSource?.raw).toBeNull();
    const replacement = JSON.stringify({ schema: "motionwright.editorial-copy/1", writer: session.custody.writer, draft: setup().session.draft });
    storage.data.set(key, replacement);
    expect(() => retryEditorialCustody(reopened, storage, pointers)).toThrow(/original.*bytes changed/);
    expect(reopened.failedSource?.rawObserved).toBe(true); expect(reopened.failedSource?.raw).toBeNull();
    expect(reopened.draft).toEqual(before); expect(storage.getItem(key)).toBe(replacement);
    expect(pointers.getItem(editorialPointerKey(session.draft.scope))).toBe(session.custody.writer);
    expect(() => prepareEditorialSubmission(reopened, project, change, ["script.text"])).toThrow(/Confirm/);
  });

  it("changed bytes during recovery confirmation cannot mutate the author buffer first", () => {
    const { session, project, storage, pointers } = armed();
    const key = editorialCopyKey(session.draft.scope, session.custody.writer);
    storage.onGet = name => { if (name === key) throw new Error("Initial source unavailable"); };
    const reopened = openEditorialSession(project, storage, pointers); storage.onGet = null;
    editEditorialField(reopened, project, "proposal.reason", "Later independent explanation");
    const before = structuredClone(reopened.draft); let reads = 0;
    storage.onGet = name => { if (name === key && ++reads === 2) storage.data.set(key, "replaced during confirmation"); };
    expect(() => retryEditorialCustody(reopened, storage, pointers)).toThrow(/changed/);
    expect(reopened.draft).toEqual(before); expect(reopened.failedSource === null).toBe(false);
  });

  it("interrupted first publication retries its exact custody without losing UNKNOWN or later blank input", () => {
    const { session, project, storage, pointers, prepared } = armed();
    const key = editorialCopyKey(session.draft.scope, session.custody.writer), original = storage.getItem(key);
    storage.onGet = name => { if (name === key) throw new Error("Initial source unavailable"); };
    const reopened = openEditorialSession(project, storage, pointers); storage.onGet = null;
    editEditorialField(reopened, project, "script.text", "Later input"); pointers.silent = true;
    expect(() => retryEditorialCustody(reopened, storage, pointers)).toThrow(/pointer/);
    expect(reopened.draft.pending?.id).toBe(prepared.id);
    editEditorialField(reopened, project, "script.text", ""); pointers.silent = false;
    retryEditorialCustody(reopened, storage, pointers);
    expect(reopened.draft.pending?.id).toBe(prepared.id); expect(reopened.draft.fields["script.text"].raw).toBe("");
    expect(storage.getItem(key)).toBe(original); expect(reopened.custody.attempt).toBeNull();
    expect(openEditorialSession(project, storage, pointers).draft.pending?.id).toBe(prepared.id);
  });
});

describe("Editorial outcome review observes the entire saved Project", () => {
  it.each(["deliverables", "audio", "production_design"] as const)("rejects changed %s at the same stamp before, during and after review", async field => {
    const { session, receipt, change, prepared } = armed();
    receipt.production_design = emptyProductionDesign();
    const changed = structuredClone(receipt);
    if (field === "deliverables") changed.deliverables[0].width += 2;
    else if (field === "audio") changed.audio.mix.voice_gain_db += 1;
    else delete changed.production_design;
    expect([changed.id, changed.generation, changed.active_branch, changed.revision])
      .toEqual([receipt.id, receipt.generation, receipt.active_branch, receipt.revision]);
    expect(changed.narrative).toEqual(receipt.narrative); expect(changed.scenes).toEqual(receipt.scenes); expect(changed.locks).toEqual(receipt.locks);
    expect(changed).not.toEqual(receipt);
    const draftBefore = structuredClone(session.draft); let historyReads = 0;
    await expect(inspectEditorialOutcome(session, receipt, {
      readProject: async () => structuredClone(changed),
      readHistory: async () => { historyReads++; return [event(receipt, change)]; },
    })).rejects.toThrow(/Reload the current saved project/);
    expect(historyReads).toBe(0);
    let reads = 0;
    await expect(inspectEditorialOutcome(session, receipt, {
      readProject: async () => structuredClone(++reads === 1 ? receipt : changed),
      readHistory: async () => { historyReads++; return [event(receipt, change)]; },
    })).rejects.toThrow(/saved project changed during outcome review/);
    expect(reads).toBe(2); expect(historyReads).toBe(1);
    const review = await inspectEditorialOutcome(session, receipt, {
      readProject: async () => structuredClone(receipt), readHistory: async () => [event(receipt, change)],
    });
    expect(editorialReviewCurrent(session, receipt, review)).toBe(true);
    expect(editorialReviewCurrent(session, changed, review)).toBe(false);
    const separate = narrativeEditChange(changed, { kind: "set_script", script_id: id(800), expected_version: 1, text: "A separately chosen script" });
    expect(() => prepareEditorialSubmission(session, changed, separate, [], review)).toThrow(/UNKNOWN is retained/);
    expect(session.draft).toEqual(draftBefore); expect(session.draft.pending?.id).toBe(prepared.id);
  });

  it("accepts an identical full Project on both reads regardless of object key order without acknowledging UNKNOWN", async () => {
    const { session, receipt, change, prepared } = armed();
    receipt.production_design = emptyProductionDesign();
    const reordered = Object.fromEntries(Object.entries(receipt).reverse()) as unknown as Project;
    const before = structuredClone(session.draft); let reads = 0;
    const review = await inspectEditorialOutcome(session, receipt, {
      readProject: async () => { reads++; return structuredClone(reordered); },
      readHistory: async () => [event(receipt, change)],
    });
    expect(reads).toBe(2); expect(review.observed).toEqual(receipt);
    expect(review.matchingRevisions).toEqual([receipt.revision]);
    expect(editorialReviewCurrent(session, structuredClone(receipt), review)).toBe(true);
    expect(session.draft).toEqual(before); expect(session.draft.pending?.id).toBe(prepared.id);
    expect(session.draft.pending?.outcome).toBe("UNKNOWN");
  });
});
