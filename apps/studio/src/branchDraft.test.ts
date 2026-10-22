import { describe, expect, test } from "vitest";
import { fixtureProject } from "./fixture";
import type { Project } from "./types";
import {
  acknowledgeBranchDraft, advanceBranchDraft, assertBranchOrigins, branchCopyKey, branchOutcomeMatches, branchPointerKey,
  branchScope, branchUnknown, BRANCH_RECOVERY_PREFIX, createBranchDraft, decodeBranchCopy, editBranchDraft,
  inspectBranchOutcome, MAX_BRANCH_COPIES, prepareBranchSubmission, readBranchCopies, readBranchRecoveryPointer, recoverBranchAfterReadFailure, reconcileBranchDraft,
  retainBranchFailure, retainBranchOrigin, separateBranchIntent, validateBranchName, writeBranchCopy,
} from "./branchDraft";
import type { BranchCopy, BranchCustody, BranchDraft } from "./branchDraft";

const attempt = "20000000-0000-4000-8000-000000000001", other = "20000000-0000-4000-8000-000000000002";
const branchId = "20000000-0000-4000-8000-000000000003";
const source = () => structuredClone(fixtureProject);
const custody = (writerId = attempt, expectedPointer: string | null = null): BranchCustody => ({ writerId, expectedPointer, expectedRaw: null });
class MemoryStorage {
  data = new Map<string, string>();
  get length() { return this.data.size; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
}
function pending() {
  const project = source(), raw = editBranchDraft(createBranchDraft(project), "  Authored branch A  ");
  const prepared = prepareBranchSubmission(raw, project, attempt);
  return { project, draft: prepared.draft, change: prepared.change };
}
function receipt(project: Project): Project {
  const saved = structuredClone(project), active = saved.branches.find(branch => branch.id === project.active_branch)!;
  const workspace = saved.branch_workspaces.find(branch => branch.branch_id === project.active_branch)!;
  saved.branches.push({ ...active, id: branchId, name: "Authored branch A", parent_branch: project.active_branch,
    base_revision: project.revision, head_revision: project.revision, protected: false });
  saved.branch_workspaces.push({ ...structuredClone(workspace), branch_id: branchId, base_revision: project.revision });
  saved.revision++; return saved;
}
const envelope = (draft: BranchDraft): BranchCopy => ({ schema: "motionwright.branch-recovery/1", scope: draft.scope,
  writerId: attempt, updatedAt: 1, draft });

describe("branch author intent and exact original result", () => {
  test.each(["id", "generation", "active_branch"] as const)("raw name belongs to the exact %s", field => {
    const { project, draft } = pending(), foreign = { ...project, [field]: other };
    expect(branchScope(foreign)).not.toBe(draft.scope);
    expect(() => reconcileBranchDraft(draft, foreign)).toThrow(/another project/);
    expect(draft.name).toBe("  Authored branch A  ");
  });
  test("incomplete and invalid raw names are retained through the recovery codec", () => {
    const project = source(), draft = editBranchDraft(createBranchDraft(project), "  \u0001" + "🙂".repeat(40));
    expect(decodeBranchCopy(JSON.stringify(envelope(draft)), draft.scope, attempt).draft).toEqual(draft);
    expect(() => prepareBranchSubmission(draft, project, attempt)).toThrow(/120 UTF-8/);
  });
  test.each(["", "   ", "🙂".repeat(31), "name\u0000", "name\u0085"])("invalid name %j is rejected without rewriting input", name => {
    const project = source(), draft = editBranchDraft(createBranchDraft(project), name);
    expect(() => prepareBranchSubmission(draft, project, attempt)).toThrow(); expect(draft.name).toBe(name);
  });
  test("native ASCII case and UTF-8 size limits are matched without silent truncation", () => {
    const project = source(); expect(validateBranchName("🙂".repeat(30), project)).toBe("🙂".repeat(30));
    expect(() => validateBranchName(project.branches[0].name.toUpperCase(), project)).toThrow(/already in use/);
  });
  test("matching and unrelated property observations cannot acknowledge UNKNOWN", () => {
    const { project, draft } = pending();
    expect(reconcileBranchDraft(draft, receipt(project))).toBe(draft);
    expect(reconcileBranchDraft(draft, { ...project, revision: project.revision + 3 })).toBe(draft);
    expect(() => advanceBranchDraft(draft, receipt(project))).toThrow(/original branch outcome/);
  });
  test("only the original callback with an exact added branch can clear its submitted name", () => {
    const { project, draft } = pending();
    expect(acknowledgeBranchDraft(draft, other, receipt(project))).toBe(draft);
    const next = acknowledgeBranchDraft(draft, attempt, receipt(project));
    expect(next.name).toBe(""); expect(next.pending).toBeNull(); expect(next.baseRevision).toBe(project.revision + 1);
  });
  test.each(["later", "ABA"])("%s author input survives the original branch reply", mode => {
    const { project, draft } = pending(); let later = editBranchDraft(draft, "Later branch B");
    if (mode === "ABA") later = editBranchDraft(later, draft.name);
    expect(acknowledgeBranchDraft(later, attempt, receipt(project)).name).toBe(later.name);
  });
  test.each(["revision", "owner", "missing", "name", "parent", "base", "workspace", "extra"])("an invalid %s original result keeps the unconfirmed name", mode => {
    const { project, draft } = pending(), saved = receipt(project), branch = saved.branches.at(-1)!;
    if (mode === "revision") saved.revision++;
    if (mode === "owner") saved.generation = other;
    if (mode === "missing") saved.branches.pop();
    if (mode === "name") branch.name = "Other author";
    if (mode === "parent") branch.parent_branch = other;
    if (mode === "base") branch.base_revision++;
    if (mode === "workspace") saved.branch_workspaces = saved.branch_workspaces.filter(item => item.branch_id !== branchId);
    if (mode === "extra") saved.branches.push({ ...branch, id: other });
    expect(() => acknowledgeBranchDraft(draft, attempt, saved)).toThrow(); expect(branchUnknown(draft)).toBe(true);
  });
  test("a rejection remains UNKNOWN while a proven local no-dispatch failure can be prepared explicitly", () => {
    const { project, draft, change } = pending();
    const unknown = retainBranchFailure(draft, attempt, "Original delivery was lost");
    expect(() => prepareBranchSubmission(unknown, project, other)).toThrow(/unconfirmed/);
    const unsent = retainBranchFailure(draft, attempt, "Local backup failed before invoke", true);
    expect(prepareBranchSubmission(unsent, project, other).change).toEqual(change);
  });
});

describe("branch copy readback and concurrent writers", () => {
  test("raw bytes and the tab pointer must both be confirmed", () => {
    const { draft } = pending(), store = new MemoryStorage(), tab = new MemoryStorage(), writer = custody();
    const raw = writeBranchCopy(store, tab, draft, writer);
    expect(decodeBranchCopy(raw, draft.scope, attempt).draft).toEqual(draft);
    expect(tab.getItem(branchPointerKey(draft.scope))).toBe(attempt);
    expect(writeBranchCopy(store, tab, draft, writer)).toBe(raw);
  });
  test("a restored UNKNOWN carries its exact source guard through a new writer and reload", () => {
    const { draft } = pending(), store = new MemoryStorage(), tab = new MemoryStorage(), writer = custody();
    const raw = writeBranchCopy(store, tab, draft, writer), key = branchCopyKey(draft.scope, attempt);
    const restored = retainBranchOrigin(decodeBranchCopy(raw, draft.scope, attempt).draft, { key, raw });
    const next = writeBranchCopy(store, tab, restored, custody(other, attempt));
    const reloaded = decodeBranchCopy(next, draft.scope, other).draft;
    expect(reloaded.pending).toEqual(draft.pending); expect(branchUnknown(reloaded)).toBe(true);
    store.setItem(key, raw + " ");
    expect(() => assertBranchOrigins(store, reloaded)).toThrow(/original unconfirmed/);
  });
  test("twenty reloads of one UNKNOWN preserve its raw name within the same recovery budget", () => {
    const { draft } = pending(), store = new MemoryStorage(), tab = new MemoryStorage();
    let writer = custody(), raw = writeBranchCopy(store, tab, draft, writer);
    const original = { key: branchCopyKey(draft.scope, writer.writerId), raw };
    for (let index = 0; index < 20; index++) {
      const priorWriter = writer.writerId;
      const recovered = retainBranchOrigin(decodeBranchCopy(raw, draft.scope, priorWriter).draft,
        { key: branchCopyKey(draft.scope, priorWriter), raw });
      assertBranchOrigins(store, recovered);
      writer = custody("20000000-0000-4000-8000-" + String(index + 100).padStart(12, "0"), priorWriter);
      raw = writeBranchCopy(store, tab, recovered, writer);
      expect(recovered.name).toBe(draft.name); expect(recovered.pending).toEqual(draft.pending);
    }
    expect(store.getItem(original.key)).toBe(original.raw);
    store.setItem(original.key, original.raw + " ");
    expect(() => assertBranchOrigins(store, decodeBranchCopy(raw, draft.scope, writer.writerId).draft))
      .toThrow(/original unconfirmed/);
  });
  test.each(["bytes", "pointer"])("silent %s publication cannot count as a confirmed recovery", mode => {
    const { draft } = pending(), store = new MemoryStorage(), tab = new MemoryStorage(), writer = custody();
    const target = mode === "bytes" ? store : tab, put = target.setItem.bind(target); target.setItem = () => undefined;
    expect(() => writeBranchCopy(store, tab, draft, writer)).toThrow(/not confirmed/);
    target.setItem = put;
    const raw = writeBranchCopy(store, tab, draft, writer);
    expect(store.getItem(branchCopyKey(draft.scope, attempt))).toBe(raw);
    expect(tab.getItem(branchPointerKey(draft.scope))).toBe(attempt);
  });
  test("a peer pointer installed during data publication is retained and blocks publication", () => {
    const { draft } = pending(), store = new MemoryStorage(), tab = new MemoryStorage(), writer = custody();
    const put = store.setItem.bind(store);
    store.setItem = (key, value) => { put(key, value); tab.setItem(branchPointerKey(draft.scope), other); };
    expect(() => writeBranchCopy(store, tab, draft, writer)).toThrow(/pointer changed/);
    expect(tab.getItem(branchPointerKey(draft.scope))).toBe(other);
    expect(() => writeBranchCopy(store, tab, draft, writer)).toThrow(/changed elsewhere/);
  });
  test("a final byte change after pointer publication remains visible and blocks retry", () => {
    const { draft } = pending(), store = new MemoryStorage(), tab = new MemoryStorage(), writer = custody();
    const put = tab.setItem.bind(tab); tab.setItem = (key, value) => { put(key, value); store.setItem(branchCopyKey(draft.scope, attempt), "damaged original bytes"); };
    expect(() => writeBranchCopy(store, tab, draft, writer)).toThrow(/changed while/);
    tab.setItem = put;
    expect(() => writeBranchCopy(store, tab, draft, writer)).toThrow(/changed elsewhere/);
    expect(store.getItem(branchCopyKey(draft.scope, attempt))).toBe("damaged original bytes");
  });
  test.each(["bytes", "pointer"])("another writer's %s is never silently replaced", mode => {
    const { draft } = pending(), store = new MemoryStorage(), tab = new MemoryStorage(), writer = custody();
    writeBranchCopy(store, tab, draft, writer);
    if (mode === "bytes") store.setItem(branchCopyKey(draft.scope, attempt), "peer original");
    else tab.setItem(branchPointerKey(draft.scope), other);
    const before = [...store.data];
    expect(() => writeBranchCopy(store, tab, editBranchDraft(draft, "Later author"), writer)).toThrow(/changed elsewhere/);
    expect([...store.data]).toEqual(before);
  });
  test("full or corrupt copies stay intact when the bounded catalogue refuses another name", () => {
    const { draft } = pending(), store = new MemoryStorage(), tab = new MemoryStorage();
    for (let index = 0; index < MAX_BRANCH_COPIES; index++) store.setItem(BRANCH_RECOVERY_PREFIX + index, "retained-" + index);
    const before = [...store.data];
    expect(() => writeBranchCopy(store, tab, draft, custody())).toThrow(/full/);
    expect([...store.data]).toEqual(before);
    const key = branchCopyKey(draft.scope, other); store.setItem(key, "{broken");
    expect(readBranchCopies(store, draft.scope)).toMatchObject([{ key, raw: "{broken", value: null }]);
  });
  test.each(["scope", "writer", "attempt", "sequence", "extra"])("a corrupted %s envelope cannot become a saved name", mode => {
    const { draft } = pending(), value = envelope(draft);
    if (mode === "scope") value.scope = branchScope({ ...source(), generation: other });
    if (mode === "writer") value.writerId = other;
    if (mode === "attempt") value.draft.pending!.id = "invalid";
    if (mode === "sequence") value.draft.pending!.sequence++;
    if (mode === "extra") (value as unknown as Record<string, unknown>).unadmitted = true;
    expect(() => decodeBranchCopy(JSON.stringify(value), draft.scope, attempt)).toThrow();
  });
});

describe("explicit saved-source review never invents an original acknowledgement", () => {
  async function inspected(later = false) {
    const { project, draft, change } = pending(), saved = receipt(project);
    const input = later ? editBranchDraft(editBranchDraft(draft, "Different B"), draft.name) : draft;
    const outcome = await inspectBranchOutcome(input, saved, { readProject: async () => structuredClone(saved),
      readHistory: async () => [{ revision: saved.revision, change, created_at: "2026-01-01T00:00:00Z" }] });
    const store = new MemoryStorage(), tab = new MemoryStorage(), writer = custody();
    const raw = writeBranchCopy(store, tab, input, writer), origin = { key: branchCopyKey(input.scope, attempt), raw };
    return { draft: input, saved, outcome, store, tab, writer, origin };
  }
  test.each([false, true])("separation preserves UNKNOWN and later ABA=%s without replay", async later => {
    const { draft, saved, outcome, store, tab, origin } = await inspected(later);
    expect(outcome.matchingRevisions).toEqual([saved.revision]); expect(branchOutcomeMatches(draft, saved, outcome)).toBe(true);
    const next = separateBranchIntent(draft, saved, outcome, origin);
    expect(next.name).toBe(later ? draft.name : ""); expect(next.pending).toBeNull();
    writeBranchCopy(store, tab, next, custody(other, attempt));
    expect(store.getItem(origin.key)).toBe(origin.raw); expect(branchUnknown(draft)).toBe(true);
    assertBranchOrigins(store, next);
  });
  test.each(["bytes", "pointer"])("an original changing during %s publication blocks the reloaded successor", async stage => {
    const { draft, saved, outcome, store, tab, origin } = await inspected();
    const next = separateBranchIntent(draft, saved, outcome, origin), writer = custody(other, attempt);
    const target = stage === "bytes" ? store : tab, put = target.setItem.bind(target);
    target.setItem = (key, raw) => { put(key, raw); if (key === (stage === "bytes" ? branchCopyKey(draft.scope, other) : branchPointerKey(draft.scope))) store.setItem(origin.key, origin.raw + " "); };
    expect(() => writeBranchCopy(store, tab, next, writer)).toThrow(/original unconfirmed/);
    const recovered = decodeBranchCopy(store.getItem(branchCopyKey(draft.scope, other))!, draft.scope, other).draft;
    expect(() => assertBranchOrigins(store, recovered)).toThrow(/original unconfirmed/);
    expect(store.getItem(origin.key)).toBe(origin.raw + " ");
  });
  test.each(["gap", "empty", "duplicate"])("%s history cannot allow a separate creation", async mode => {
    const { project, draft, change } = pending(), saved = receipt(project), event = { revision: saved.revision, change, created_at: "now" };
    const events = mode === "empty" ? [] : mode === "duplicate" ? [event, event] : [{ ...event, revision: saved.revision + 1 }];
    await expect(inspectBranchOutcome(draft, saved, { readProject: async () => saved, readHistory: async () => events })).rejects.toThrow(/history/);
    expect(branchUnknown(draft)).toBe(true);
  });
  test("a source change between project reads invalidates the review", async () => {
    const { project, draft, change } = pending(), saved = receipt(project); let reads = 0;
    await expect(inspectBranchOutcome(draft, saved, { readProject: async () => ++reads === 1 ? saved : { ...saved, revision: saved.revision + 1 },
      readHistory: async () => [{ revision: saved.revision, change, created_at: "now" }] })).rejects.toThrow(/changed during/);
  });
});


describe("branch initial read recovery keeps actual author decisions", () => {
  test("adopts an ordinary unread copy, keeping its exact raw name and older source revision", () => {
    const project = source(), recovered = editBranchDraft(createBranchDraft(project), "  ordinary raw branch  ");
    const newer = { ...project, revision: project.revision + 1 };
    const current = createBranchDraft(newer), before = structuredClone(recovered);
    const next = recoverBranchAfterReadFailure(recovered, current, false, newer);
    expect(next).toEqual(recovered); expect(recovered).toEqual(before);
    expect(next.baseRevision).toBe(project.revision);
    expect(() => prepareBranchSubmission(next, newer, attempt)).toThrow(/Review the current/);
  });
  test("adopts untouched UNKNOWN and its exact retained origin without creating another attempt", () => {
    const { project, draft } = pending(), raw = JSON.stringify(envelope(draft));
    const recovered = retainBranchOrigin(draft, { key: branchCopyKey(draft.scope, attempt), raw });
    const next = recoverBranchAfterReadFailure(recovered, createBranchDraft(project), false, project);
    expect(next).toEqual(recovered);
    expect(next.pending).toEqual(draft.pending);
    expect(() => prepareBranchSubmission(next, project, other)).toThrow(/unconfirmed/);
  });
  test.each(["", "  later raw branch  ", "  Authored branch A  "])("retains later explicit raw name %j above both edit sequences", name => {
    const { project, draft } = pending();
    const current = { ...editBranchDraft(createBranchDraft(project), name), sequence: draft.sequence + 7 };
    const next = recoverBranchAfterReadFailure(draft, current, true, project);
    expect(next.name).toBe(name);
    expect(next.sequence).toBe(current.sequence + 1);
    expect(next.pending).toEqual(draft.pending);
    expect(next.baseRevision).toBe(draft.baseRevision);
  });
  test("A to B to A remains a later edit even when equal to the recovered raw name", () => {
    const { project, draft } = pending();
    let current = editBranchDraft(createBranchDraft(project), draft.name);
    current = editBranchDraft(current, "temporary");
    current = editBranchDraft(current, draft.name);
    const next = recoverBranchAfterReadFailure(draft, current, true, project);
    expect(next.name).toBe(draft.name);
    expect(next.sequence).toBeGreaterThan(draft.pending!.sequence);
    expect(acknowledgeBranchDraft(next, attempt, receipt(project)).name).toBe(draft.name);
  });
  test("a retained NOT_SENT becomes UNKNOWN in a new writer while its original bytes remain unchanged", () => {
    const { project, draft } = pending();
    const unsent = retainBranchFailure(draft, attempt, "Local backup failed before dispatch", true);
    const raw = JSON.stringify(envelope(unsent));
    const local = new MemoryStorage(), session = new MemoryStorage();
    const key = branchCopyKey(draft.scope, attempt);
    local.setItem(key, raw); session.setItem(branchPointerKey(draft.scope), attempt);
    const recovered = retainBranchOrigin(decodeBranchCopy(raw, draft.scope, attempt).draft, { key, raw });
    expect(recovered.origins).toEqual([]);
    const next = recoverBranchAfterReadFailure(recovered, createBranchDraft(project), false, project);
    expect(next.pending).toEqual({ ...unsent.pending, outcome: "UNKNOWN" });
    writeBranchCopy(local, session, next, custody(other, attempt));
    expect(local.getItem(key)).toBe(raw);
    const saved = decodeBranchCopy(local.getItem(branchCopyKey(draft.scope, other))!, draft.scope, other).draft;
    expect(saved.pending?.outcome).toBe("UNKNOWN");
    expect(saved.origins).toEqual([]);
    expect(() => prepareBranchSubmission(saved, project, branchId)).toThrow(/unconfirmed/);
  });
  test("refuses an incompatible uncertain attempt instead of replacing its authority", () => {
    const { project, draft } = pending();
    const foreignAttempt = { ...draft, pending: { ...draft.pending!, id: other } };
    const before = structuredClone(foreignAttempt);
    expect(() => recoverBranchAfterReadFailure(draft, foreignAttempt, true, project)).toThrow(/attempts differ/);
    expect(foreignAttempt).toEqual(before);
  });
  test.each(["id", "generation", "active_branch"] as const)("refuses a recovered foreign %s", field => {
    const { project, draft } = pending(), foreign = { ...project, [field]: other };
    expect(() => recoverBranchAfterReadFailure(draft, createBranchDraft(foreign), true, project)).toThrow(/another project/);
  });
  test("deduplicates exact origins and rejects differing bytes for one origin key", () => {
    const { project, draft } = pending(), raw = JSON.stringify(envelope(draft));
    const origin = { key: branchCopyKey(draft.scope, attempt), raw };
    const recovered = retainBranchOrigin(draft, origin);
    const current = { ...createBranchDraft(project), origins: [origin] };
    expect(recoverBranchAfterReadFailure(recovered, current, false, project).origins).toEqual([origin]);
    current.origins = [{ ...origin, raw: raw + " " }];
    expect(() => recoverBranchAfterReadFailure(recovered, current, false, project)).toThrow(/conflicting bytes/);
  });
  test("refuses edit-sequence overflow without replacing the recovered or local raw input", () => {
    const { project, draft } = pending();
    const current = { ...createBranchDraft(project), name: "later", sequence: Number.MAX_SAFE_INTEGER };
    expect(() => recoverBranchAfterReadFailure(draft, current, true, project)).toThrow(/advance safely/);
    expect(() => editBranchDraft(current, "unrecordable")).toThrow(/advance safely/);
    expect(current.name).toBe("later");
  });
  test("adopted work survives a failed successor backup and later typing before its next backup", () => {
    const { project, draft } = pending(), raw = JSON.stringify(envelope(draft));
    const local = new MemoryStorage(), session = new MemoryStorage(), original = branchCopyKey(draft.scope, attempt);
    local.setItem(original, raw); session.setItem(branchPointerKey(draft.scope), attempt);
    const recovered = retainBranchOrigin(draft, { key: original, raw });
    const next = recoverBranchAfterReadFailure(recovered, createBranchDraft(project), false, project);
    const owner = custody(other, attempt);
    const write = local.setItem.bind(local); local.setItem = () => undefined;
    expect(() => writeBranchCopy(local, session, next, owner)).toThrow(/not confirmed/);
    const later = editBranchDraft(next, "");
    local.setItem = write;
    writeBranchCopy(local, session, later, owner);
    expect(decodeBranchCopy(local.getItem(branchCopyKey(draft.scope, other))!, draft.scope, other).draft.name).toBe("");
    expect(local.getItem(original)).toBe(raw);
  });
});
describe("branch recovery pointer observation", () => {
  test("a failed first read remains unknown and can anchor the first successful writer", () => {
    const project = source(), scope = branchScope(project), session = new MemoryStorage();
    const prior = { observed: false, expectedPointer: null };
    const read = session.getItem.bind(session);
    session.getItem = () => { throw new Error("temporary read failure"); };
    expect(() => readBranchRecoveryPointer(session, scope, prior)).toThrow(/temporary/);
    expect(prior).toEqual({ observed: false, expectedPointer: null });
    session.getItem = read; session.setItem(branchPointerKey(scope), attempt);
    expect(readBranchRecoveryPointer(session, scope, prior)).toEqual({ observed: true, expectedPointer: attempt });
  });
  test.each([null, attempt])("a previously observed pointer %j cannot adopt a replacement", value => {
    const scope = branchScope(source()), session = new MemoryStorage();
    session.setItem(branchPointerKey(scope), other);
    expect(() => readBranchRecoveryPointer(session, scope, { observed: true, expectedPointer: value })).toThrow(/changed/);
  });
  test("the first successful null anchors absence rather than granting permission for a later writer", () => {
    const scope = branchScope(source()), session = new MemoryStorage();
    const observation = readBranchRecoveryPointer(session, scope, { observed: false, expectedPointer: null });
    expect(observation).toEqual({ observed: true, expectedPointer: null });
    session.setItem(branchPointerKey(scope), attempt);
    expect(() => readBranchRecoveryPointer(session, scope, observation)).toThrow(/changed/);
  });
  test("publication rechecks ownership after recovery read and preserves the original bytes", () => {
    const { project, draft } = pending(), raw = JSON.stringify(envelope(draft));
    const local = new MemoryStorage(), session = new MemoryStorage(), key = branchCopyKey(draft.scope, attempt);
    local.setItem(key, raw); session.setItem(branchPointerKey(draft.scope), attempt);
    const observation = readBranchRecoveryPointer(session, draft.scope, { observed: false, expectedPointer: null });
    const recovered = recoverBranchAfterReadFailure(draft, createBranchDraft(project), false, project);
    session.setItem(branchPointerKey(draft.scope), branchId);
    expect(() => writeBranchCopy(local, session, recovered, custody(other, observation.expectedPointer))).toThrow(/pointer/);
    expect(local.getItem(key)).toBe(raw);
    expect(local.getItem(branchCopyKey(draft.scope, other))).toBeNull();
  });
});
