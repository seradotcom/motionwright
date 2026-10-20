import { describe, expect, it } from "vitest";
import { fixtureBootstrap } from "./fixture";
import type { Project } from "./types";
import {
  acknowledgeBriefSubmission, BRIEF_DRAFT_STORAGE_PREFIX, briefDraftConflicts, briefDraftHasWork,
  briefDraftScope, briefDraftStorageKey, briefGroupHasChanges, briefSubmissionMatches, createBriefDraft,
  decodeBriefRecovery, editBriefField, encodeBriefRecovery, MAX_BRIEF_DRAFT_BYTES, MAX_BRIEF_RECOVERY_BYTES, MAX_BRIEF_RECOVERY_COPIES,
  prepareBriefSubmission, readBriefRecoveries, reconcileBriefDraft, resolveBriefField, retainBriefSubmissionIntent, writeBriefRecovery, writeBriefSessionPointer,
} from "./briefDraft";
import type { BriefDraft, BriefRecovery, BriefSubmission, DraftStorage } from "./briefDraft";

function project(): Project { return structuredClone(fixtureBootstrap.project); }
function persisted(source: Project, submission: BriefSubmission): Project {
  const next = structuredClone(source); next.revision++;
  if (submission.change.type === "rename_project") next.title = submission.change.title;
  else {
    const { objective, audience, constraints, exclusions } = submission.change;
    Object.assign(next.brief, { objective, audience, constraints: [...constraints], exclusions: [...exclusions] });
  }
  return next;
}
function recovery(draft: BriefDraft | null, source: Project, sessionId = "session-a", updatedAt = 1): BriefRecovery {
  return { schema: "motionwright.brief-recovery/1", scope: briefDraftScope(source), sessionId, updatedAt, draft };
}
class MemoryStorage implements DraftStorage {
  data = new Map<string, string>();
  failWrites = false;
  get length() { return this.data.size; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) {
    if (this.failWrites) throw new Error("QuotaExceededError");
    this.data.set(key, value);
  }
  removeItem(key: string) { this.data.delete(key); }
}

describe("brief authoring snapshots", () => {
  it("recognizes a saved title without consuming the other four field drafts", () => {
    const source = project();
    let draft = createBriefDraft(source);
    draft = editBriefField(draft, "title", "  New project title  ");
    draft = editBriefField(draft, "objective", "Pending objective");
    draft = editBriefField(draft, "audience", "Pending audience");
    draft = editBriefField(draft, "constraints", "  Keep the manual layout  \n\n");
    draft = editBriefField(draft, "exclusions", "No invented proof\n");
    const sent = prepareBriefSubmission(draft, source, "title");
    expect(sent.change).toEqual({ type: "rename_project", title: "New project title" });
    const next = persisted(source, sent);
    const acknowledged = acknowledgeBriefSubmission(draft, next, sent);
    expect(acknowledged.fields.title.raw).toBe("New project title");
    for (const field of ["objective", "audience", "constraints", "exclusions"] as const) {
      expect(acknowledged.fields[field].raw).toBe(draft.fields[field].raw);
    }
    expect(briefDraftHasWork(acknowledged)).toBe(true);
    expect(source.title).not.toBe(next.title);
  });

  it("builds set_brief from untouched current fields and edited raw, preserving claims", () => {
    const source = project();
    let draft = editBriefField(createBriefDraft(source), "constraints", " Keep the typography \n\nPreserve timing\n");
    draft = editBriefField(draft, "title", "Title stays pending");
    const current = structuredClone(source); current.revision += 2;
    current.brief.audience = "A colleague updated this audience";
    const sent = prepareBriefSubmission(draft, current, "brief");
    expect(sent.revision).toBe(current.revision);
    expect(sent.change).toEqual({ type: "set_brief", objective: current.brief.objective,
      audience: current.brief.audience, constraints: ["Keep the typography", "Preserve timing"], exclusions: current.brief.exclusions });
    expect(sent.change).not.toHaveProperty("claims");
    const next = persisted(current, sent);
    const acknowledged = acknowledgeBriefSubmission(draft, next, sent);
    expect(acknowledged.fields.title.raw).toBe("Title stays pending");
    expect(acknowledged.fields.constraints.raw).toBe("Keep the typography\nPreserve timing");
    expect(next.brief.claims).toEqual(source.brief.claims);
  });

  it("retains an immutable payload while the author continues typing", () => {
    const source = project();
    let draft = editBriefField(createBriefDraft(source), "objective", "Sent objective");
    draft = editBriefField(draft, "constraints", "First\nSecond");
    const sent = prepareBriefSubmission(draft, source, "brief");
    const exactPayload = JSON.stringify(sent.change);
    draft = editBriefField(draft, "objective", "Later objective typed during await");
    draft = editBriefField(draft, "constraints", "First\nSecond\nLater line\n");
    const next = persisted(source, sent);
    expect(JSON.stringify(sent.change)).toBe(exactPayload);
    const acknowledged = acknowledgeBriefSubmission(draft, next, sent);
    expect(acknowledged.fields.objective).toEqual({ raw: "Later objective typed during await",
      base: "Sent objective", baseRevision: next.revision });
    expect(acknowledged.fields.constraints.raw).toBe("First\nSecond\nLater line\n");
    expect(acknowledged.fields.constraints.base).toEqual(["First", "Second"]);
    expect(briefDraftConflicts(acknowledged, next)).toEqual([]);
    expect(briefGroupHasChanges(acknowledged, next, "brief")).toBe(true);
  });

  it("does not acknowledge a resolved callback with no new canonical revision", () => {
    const source = project(), draft = editBriefField(createBriefDraft(source), "title", "Unconfirmed title");
    const sent = prepareBriefSubmission(draft, source, "title");
    expect(briefSubmissionMatches(source, sent)).toBe(false);
    expect(acknowledgeBriefSubmission(draft, source, sent)).toBe(draft);
  });

  it("keeps an intentional return to the old base across unmount before and after a pending write appears", () => {
    const source = project(); source.brief.objective = "Old";
    const sentDraft = editBriefField(createBriefDraft(source), "objective", "Sent");
    const sent = prepareBriefSubmission(sentDraft, source, "brief");
    const returned = editBriefField(sentDraft, "objective", "Old", true);
    expect(returned.fields.objective.base).toBe("Old");
    expect(returned.fields.objective.baseRevision).toBe(source.revision);
    expect(briefDraftHasWork(returned)).toBe(true);
    const firstRemount = reconcileBriefDraft(returned, source);
    expect(firstRemount.fields.objective).toEqual(returned.fields.objective);
    expect(briefDraftHasWork(firstRemount)).toBe(true);
    const savedCopy = recovery(firstRemount, source);
    const decoded = decodeBriefRecovery(encodeBriefRecovery(savedCopy), savedCopy.scope, savedCopy.sessionId);
    const canonical = persisted(source, sent);
    const reopened = reconcileBriefDraft(decoded.draft!, canonical);
    expect(reopened.fields.objective).toEqual({ raw: "Old", base: "Old", baseRevision: source.revision, intent: true });
    expect(briefDraftConflicts(reopened, canonical)).toMatchObject([{ field: "objective", before: "Old", current: "Sent", proposed: "Old" }]);
    expect(() => prepareBriefSubmission(reopened, canonical, "brief")).toThrow(/Resolve/);
    const resolved = resolveBriefField(reopened, canonical, "objective", "draft");
    const second = prepareBriefSubmission(resolved, canonical, "brief");
    expect(second.change).toMatchObject({ type: "set_brief", objective: "Old" });
    expect(canonical.brief.objective).toBe("Sent");
    const exactNext = persisted(canonical, second);
    const acknowledged = acknowledgeBriefSubmission(resolved, exactNext, second);
    expect(briefDraftHasWork(acknowledged)).toBe(false);
    expect(acknowledged.fields.objective).not.toHaveProperty("intent");
  });

  it("bases a return-to-old edit on a matching observed write without consuming the newer intent", () => {
    const source = project(); source.brief.objective = "Old";
    const sentDraft = editBriefField(createBriefDraft(source), "objective", "Sent");
    const sent = prepareBriefSubmission(sentDraft, source, "brief");
    const returned = editBriefField(sentDraft, "objective", "Old", true);
    const canonical = persisted(source, sent);
    const acknowledged = acknowledgeBriefSubmission(returned, canonical, sent);
    expect(acknowledged.fields.objective).toEqual({ raw: "Old", base: "Sent", baseRevision: canonical.revision });
    expect(briefDraftHasWork(acknowledged)).toBe(true);
    expect(briefDraftConflicts(acknowledged, canonical)).toEqual([]);
    expect(reconcileBriefDraft(acknowledged, canonical).fields.objective.raw).toBe("Old");
  });

  it("retains write intent when navigation happens before the author returns to the old value", () => {
    const source = project(); source.brief.objective = "Old";
    const sentDraft = editBriefField(createBriefDraft(source), "objective", "Sent");
    const sent = prepareBriefSubmission(sentDraft, source, "brief");
    const protectedDraft = retainBriefSubmissionIntent(sentDraft, source, sent);
    expect(protectedDraft.fields.objective.intent).toBe(true);
    expect(protectedDraft.fields.audience).not.toHaveProperty("intent");
    const savedCopy = recovery(protectedDraft, source);
    const decoded = decodeBriefRecovery(encodeBriefRecovery(savedCopy), savedCopy.scope, savedCopy.sessionId);
    const remounted = reconcileBriefDraft(decoded.draft!, source);
    // The replacement editor has no pending request reference. Its field alone
    // must carry the intent when the author changes the raw input afterwards.
    const returned = editBriefField(remounted, "objective", "Old");
    expect(briefDraftHasWork(returned)).toBe(true);
    expect(returned.fields.objective.base).toBe("Old");
    const canonical = persisted(source, sent);
    const reopened = reconcileBriefDraft(returned, canonical);
    expect(reopened.fields.objective.raw).toBe("Old");
    expect(briefDraftConflicts(reopened, canonical).map(conflict => conflict.field)).toEqual(["objective"]);
    expect(resolveBriefField(reopened, canonical, "objective", "saved").fields.objective).toEqual({
      raw: "Sent", base: "Sent", baseRevision: canonical.revision,
    });
  });

  it("does not recognize a skipped revision or another field value as its snapshot", () => {
    const source = project(), draft = editBriefField(createBriefDraft(source), "audience", "Specific audience");
    const sent = prepareBriefSubmission(draft, source, "brief");
    const next = persisted(source, sent);
    next.revision++;
    expect(briefSubmissionMatches(next, sent)).toBe(false);
    expect(acknowledgeBriefSubmission(draft, next, sent)).toBe(draft);
    next.revision--; next.brief.constraints.push("Concurrent different input");
    expect(briefSubmissionMatches(next, sent)).toBe(false);
  });

  it.each(["id", "generation", "active_branch"] as const)("cannot recognize or reconcile another %s scope", field => {
    const source = project(), draft = editBriefField(createBriefDraft(source), "title", "Scoped title");
    const sent = prepareBriefSubmission(draft, source, "title"), other = persisted(source, sent);
    other[field] = "different-scope";
    expect(briefSubmissionMatches(other, sent)).toBe(false);
    expect(acknowledgeBriefSubmission(draft, other, sent)).toBe(draft);
    expect(() => reconcileBriefDraft(draft, other)).toThrow(/another project, generation or branch/);
  });

  it("compares only the submitted group and never consumes an unrelated raw field", () => {
    const source = project();
    let draft = editBriefField(createBriefDraft(source), "title", "New title");
    draft = editBriefField(draft, "audience", "Pending audience A");
    const sent = prepareBriefSubmission(draft, source, "title"), next = persisted(source, sent);
    next.brief.audience = "Concurrent audience B";
    expect(briefSubmissionMatches(next, sent)).toBe(true);
    const kept = acknowledgeBriefSubmission(draft, next, sent);
    expect(kept.fields.audience.raw).toBe("Pending audience A");
    expect(briefDraftConflicts(kept, next).map(conflict => conflict.field)).toEqual(["audience"]);
  });
});

describe("field-level concurrent review", () => {
  function overlap() {
    const source = project();
    let draft = editBriefField(createBriefDraft(source), "objective", "Author A objective");
    draft = editBriefField(draft, "constraints", "Author A constraint\n");
    const current = structuredClone(source); current.revision++;
    current.brief.objective = "Author B objective"; current.brief.audience = "Author B audience";
    return { source, current, draft };
  }
  it("keeps the dirty constraint, adopts untouched audience and flags only objective", () => {
    const { current, draft } = overlap(), next = reconcileBriefDraft(draft, current);
    expect(next.fields.constraints.raw).toBe("Author A constraint\n");
    expect(next.fields.audience.raw).toBe("Author B audience");
    expect(next.fields.objective.raw).toBe("Author A objective");
    expect(briefDraftConflicts(next, current).map(conflict => conflict.field)).toEqual(["objective"]);
    expect(() => prepareBriefSubmission(next, current, "brief")).toThrow(/Resolve/);
    expect(current.brief.constraints).not.toEqual(["Author A constraint"]);
  });
  it("keeping the saved overlap resolves only that field and does not commit", () => {
    const { current, draft } = overlap(), before = JSON.stringify(current);
    const next = resolveBriefField(reconcileBriefDraft(draft, current), current, "objective", "saved");
    expect(next.fields.objective.raw).toBe("Author B objective");
    expect(next.fields.constraints.raw).toBe("Author A constraint\n");
    expect(briefDraftConflicts(next, current)).toEqual([]);
    expect(JSON.stringify(current)).toBe(before);
    expect(prepareBriefSubmission(next, current, "brief").change).toMatchObject({
      objective: "Author B objective", audience: "Author B audience", constraints: ["Author A constraint"],
    });
  });
  it("keeping the author overlap changes its base, then requires a fresh explicit submission", () => {
    const { current, draft } = overlap(), before = JSON.stringify(current);
    const next = resolveBriefField(reconcileBriefDraft(draft, current), current, "objective", "draft");
    expect(next.fields.objective).toEqual({ raw: "Author A objective", base: "Author B objective", baseRevision: current.revision });
    expect(next.fields.constraints.raw).toBe("Author A constraint\n");
    expect(briefDraftConflicts(next, current)).toEqual([]);
    expect(JSON.stringify(current)).toBe(before);
    const another = structuredClone(current); another.revision++; another.brief.objective = "Author C objective";
    expect(briefDraftConflicts(next, another).map(conflict => conflict.field)).toEqual(["objective"]);
  });
  it("does not block title saving because a separate brief field conflicts", () => {
    const { current, draft } = overlap();
    const next = editBriefField(draft, "title", "Independent title");
    expect(prepareBriefSubmission(next, current, "title").change).toEqual({ type: "rename_project", title: "Independent title" });
    expect(() => prepareBriefSubmission(next, current, "brief")).toThrow(/Resolve/);
  });
  it("retains raw formatting when another writer independently saves the same semantic value", () => {
    const source = project(), draft = editBriefField(createBriefDraft(source), "constraints", " Same constraint \n\n");
    const current = structuredClone(source); current.revision++; current.brief.constraints = ["Same constraint"];
    const next = reconcileBriefDraft(draft, current);
    expect(next.fields.constraints.raw).toBe(" Same constraint \n\n");
    expect(briefDraftConflicts(next, current)).toEqual([]);
    expect(briefGroupHasChanges(next, current, "brief")).toBe(false);
    expect(briefDraftHasWork(next)).toBe(true);
    expect(() => prepareBriefSubmission(next, current, "brief")).toThrow(/already match/);
  });
  it("requires an explicit new base for a recovered input from a later revision", () => {
    const source = project(), future = structuredClone(source); future.revision += 10;
    const draft = editBriefField(createBriefDraft(future), "audience", "Future draft");
    const next = reconcileBriefDraft(draft, source);
    expect(next.fields.audience).toEqual(draft.fields.audience);
    expect(briefDraftConflicts(next, source)).toMatchObject([{ field: "audience", futureRevision: true }]);
    expect(() => prepareBriefSubmission(next, source, "brief")).toThrow(/Resolve/);
    const resolved = resolveBriefField(next, source, "audience", "draft");
    expect(prepareBriefSubmission(resolved, source, "brief").change).toMatchObject({ audience: "Future draft" });
  });
});

describe("bounded local recovery without project authority", () => {
  it("round-trips rejected raw input and list formatting, without enforcing domain submission limits", () => {
    const source = project();
    let draft = editBriefField(createBriefDraft(source), "audience", "x".repeat(2001));
    draft = editBriefField(draft, "constraints", Array.from({ length: 129 }, (_, i) => " item " + i + " ").join("\n") + "\n\n");
    const saved = recovery(draft, source);
    expect(decodeBriefRecovery(encodeBriefRecovery(saved), saved.scope, saved.sessionId)).toEqual(saved);
    expect(draft.fields.audience.raw.length).toBe(2001);
    expect(source.brief.audience).not.toBe(draft.fields.audience.raw);
  });
  it("drops unknown commands, grants, receipts and field properties on recovery", () => {
    const source = project(), draft = editBriefField(createBriefDraft(source), "objective", "Safe editable input");
    const saved = recovery(draft, source);
    const injected = JSON.parse(JSON.stringify(saved));
    injected.effect_grant = "not-authority"; injected.receipt = { committed: true };
    injected.draft.change = { type: "rename_project", title: "not-restored" };
    injected.draft.fields.objective.grant = "not-restored";
    injected.draft.fields.claims = { raw: "not-a-brief-field", base: "", baseRevision: 1 };
    expect(decodeBriefRecovery(JSON.stringify(injected), saved.scope, saved.sessionId)).toEqual(saved);
  });
  it("rejects another scope, writer identity, unsupported schema or invalid source shape", () => {
    const source = project(), saved = recovery(createBriefDraft(source), source), raw = encodeBriefRecovery(saved);
    expect(() => decodeBriefRecovery(raw, "other-scope", saved.sessionId)).toThrow(/scope/);
    expect(() => decodeBriefRecovery(raw, saved.scope, "other-session")).toThrow(/scope/);
    expect(() => decodeBriefRecovery(raw.replace("brief-recovery/1", "brief-recovery/99"), saved.scope, saved.sessionId)).toThrow(/version/);
    const invalid = JSON.parse(raw); invalid.draft.fields.constraints.base = "not-an-array";
    expect(() => decodeBriefRecovery(JSON.stringify(invalid), saved.scope, saved.sessionId)).toThrow(/invalid field/);
    const invalidIntent = JSON.parse(raw); invalidIntent.draft.fields.objective.intent = "confirmed";
    expect(() => decodeBriefRecovery(JSON.stringify(invalidIntent), saved.scope, saved.sessionId)).toThrow(/invalid field/);
  });
  it("preserves corrupted copies while listing other valid recovery records", () => {
    const source = project(), storage = new MemoryStorage();
    const valid = recovery(editBriefField(createBriefDraft(source), "objective", "Valid pending draft"), source);
    writeBriefRecovery(storage, valid);
    const badKey = briefDraftStorageKey(valid.scope, "damaged-session");
    storage.setItem(badKey, "{broken raw recovery");
    const before = [...storage.data];
    const result = readBriefRecoveries(storage, valid.scope);
    expect(result.entries).toHaveLength(1);
    expect(result.unreadable).toEqual([{ key: badKey, sessionId: "damaged-session" }]);
    expect([...storage.data]).toEqual(before);
  });
  it("stores two sessions independently and keeps older inputs after a clean checkpoint", () => {
    const source = project(), storage = new MemoryStorage();
    const a = recovery(editBriefField(createBriefDraft(source), "objective", "A pending"), source, "a", 1);
    const b = recovery(editBriefField(createBriefDraft(source), "objective", "B pending"), source, "b", 2);
    writeBriefRecovery(storage, a); writeBriefRecovery(storage, b);
    const beforeA = storage.getItem(briefDraftStorageKey(a.scope, "a"));
    writeBriefRecovery(storage, recovery(null, source, "b", 3));
    const result = readBriefRecoveries(storage, a.scope);
    expect(result.entries.map(entry => [entry.value.sessionId, entry.value.draft?.fields.objective.raw ?? null])).toEqual([["b", null], ["a", "A pending"]]);
    expect(storage.getItem(briefDraftStorageKey(a.scope, "a"))).toBe(beforeA);
  });

  it("completes more than 64 clean editing sessions without filling the recovery namespace", () => {
    const source = project(), storage = new MemoryStorage();
    for (let i = 0; i < MAX_BRIEF_RECOVERY_COPIES * 2; i++) {
      const session = "writer-" + i;
      const draft = editBriefField(createBriefDraft(source), "objective", "Saved input " + i);
      // Repeated timestamps exercise ordering when multiple writes share a clock tick.
      writeBriefRecovery(storage, recovery(draft, source, session, 1));
      writeBriefRecovery(storage, recovery(null, source, session, 1));
      const copies = readBriefRecoveries(storage, briefDraftScope(source));
      expect(copies.entries[0].value.sessionId).toBe(session);
      expect(copies.entries[0].value.draft).toBeNull();
      expect(copies.entries).toHaveLength(1);
    }
    expect(storage.length).toBe(1);
  });

  it("keeps the last completed marker above older unfinished input through repeated clean sessions", () => {
    const source = project(), storage = new MemoryStorage();
    const old = recovery(editBriefField(createBriefDraft(source), "objective", "Older unfinished input"), source, "old", 10);
    writeBriefRecovery(storage, old);
    const oldRaw = storage.getItem(briefDraftStorageKey(old.scope, "old"));
    for (let i = 0; i < 70; i++) {
      writeBriefRecovery(storage, recovery(null, source, "completed-" + i, 1));
    }
    const copies = readBriefRecoveries(storage, old.scope);
    expect(copies.entries).toHaveLength(2);
    expect(copies.entries[0].value).toMatchObject({ sessionId: "completed-69", draft: null });
    expect(storage.getItem(briefDraftStorageKey(old.scope, "old"))).toBe(oldRaw);
  });

  it("makes completed records immutable so new input cannot race checkpoint compaction", () => {
    const source = project(), storage = new MemoryStorage(), clean = recovery(null, source);
    writeBriefRecovery(storage, clean);
    const original = storage.getItem(briefDraftStorageKey(clean.scope, clean.sessionId));
    writeBriefRecovery(storage, { ...clean, updatedAt: 100 });
    expect(storage.getItem(briefDraftStorageKey(clean.scope, clean.sessionId))).toBe(original);
    const input = recovery(editBriefField(createBriefDraft(source), "objective", "New input"), source);
    expect(() => writeBriefRecovery(storage, input)).toThrow(/immutable/);
    expect(storage.getItem(briefDraftStorageKey(clean.scope, clean.sessionId))).toBe(original);
    writeBriefRecovery(storage, { ...input, sessionId: "new-writer" });
    expect(readBriefRecoveries(storage, clean.scope).entries[0].value.draft?.fields.objective.raw).toBe("New input");
  });

  it("requires explicit cleanup when unrelated projects have filled the brief recovery namespace", () => {
    const source = project(), storage = new MemoryStorage();
    storage.setItem("unrelated-theme", "dark");
    for (let i = 0; i < MAX_BRIEF_RECOVERY_COPIES; i++) {
      const other = project(); other.id = "completed-project-" + i;
      writeBriefRecovery(storage, recovery(null, other));
    }
    const before = new Map(storage.data);
    const next = recovery(editBriefField(createBriefDraft(source), "audience", "New pending audience"), source);
    expect(() => writeBriefRecovery(storage, next)).toThrow(/budget is full/);
    expect(storage.data).toEqual(before);
    // Only the author can choose an unrelated clean copy for removal. That
    // explicit action makes enough capacity for the newly authored draft.
    const other = project(); other.id = "completed-project-0";
    storage.removeItem(briefDraftStorageKey(briefDraftScope(other), "session-a"));
    writeBriefRecovery(storage, next);
    expect(readBriefRecoveries(storage, next.scope).entries[0].value.draft?.fields.audience.raw).toBe("New pending audience");
    expect(storage.getItem("unrelated-theme")).toBe("dark");
    expect(storage.length).toBe(MAX_BRIEF_RECOVERY_COPIES + 1);
  });

  it("refuses a namespace occupied by other scopes' input without silently pruning it", () => {
    const source = project(), storage = new MemoryStorage();
    for (let i = 0; i < MAX_BRIEF_RECOVERY_COPIES; i++) {
      const other = project(); other.id = "pending-project-" + i;
      writeBriefRecovery(storage, recovery(editBriefField(createBriefDraft(other), "objective", "Unfinished " + i), other));
    }
    const before = [...storage.data];
    const next = recovery(editBriefField(createBriefDraft(source), "audience", "Still in memory"), source);
    expect(() => writeBriefRecovery(storage, next)).toThrow(/budget is full/);
    expect([...storage.data]).toEqual(before);
    expect(next.draft?.fields.audience.raw).toBe("Still in memory");
  });

  it("does not remove the last clean marker if its replacement cannot be written", () => {
    const source = project(), storage = new MemoryStorage();
    writeBriefRecovery(storage, recovery(editBriefField(createBriefDraft(source), "objective", "Older unfinished input"), source, "old", 1));
    writeBriefRecovery(storage, recovery(null, source, "clean", 2));
    const before = [...storage.data]; storage.failWrites = true;
    expect(() => writeBriefRecovery(storage, recovery(null, source, "new-clean", 3))).toThrow(/Quota/);
    expect([...storage.data]).toEqual(before);
    expect(readBriefRecoveries(storage, briefDraftScope(source)).entries[0].value.draft).toBeNull();
  });

  it("surfaces malformed key identities without altering their bytes", () => {
    const source = project(), storage = new MemoryStorage();
    const key = BRIEF_DRAFT_STORAGE_PREFIX + "[broken identity";
    storage.setItem(key, "Unreadable original");
    const copies = readBriefRecoveries(storage, briefDraftScope(source));
    expect(copies.unreadable).toEqual([{ key, sessionId: "unidentified-1", unscoped: true }]);
    expect(storage.getItem(key)).toBe("Unreadable original");
  });

  it("keeps a bounded actionable list when external storage exceeds the copy limit", () => {
    const source = project(), storage = new MemoryStorage();
    for (let i = 0; i < MAX_BRIEF_RECOVERY_COPIES + 1; i++) storage.setItem(briefDraftStorageKey(briefDraftScope(source), "broken-" + i), "{broken");
    const copies = readBriefRecoveries(storage, briefDraftScope(source));
    expect(copies.limited).toBe(true);
    expect(copies.unreadable).toHaveLength(MAX_BRIEF_RECOVERY_COPIES);
    expect(storage.length).toBe(MAX_BRIEF_RECOVERY_COPIES + 1);
    storage.removeItem(copies.unreadable[0].key);
    expect(readBriefRecoveries(storage, briefDraftScope(source)).limited).toBe(false);
  });
  it("does not overwrite an existing valid copy when the browser rejects storage", () => {
    const source = project(), storage = new MemoryStorage();
    const original = recovery(editBriefField(createBriefDraft(source), "title", "Original local draft"), source);
    writeBriefRecovery(storage, original);
    const before = [...storage.data]; storage.failWrites = true;
    const changed = recovery(editBriefField(original.draft!, "title", "Later raw input"), source);
    expect(() => writeBriefRecovery(storage, changed)).toThrow(/Quota/);
    expect([...storage.data]).toEqual(before);
    expect(changed.draft!.fields.title.raw).toBe("Later raw input");
  });
  it("measures UTF-8 recovery bytes and leaves prior data untouched on oversize", () => {
    const source = project(), storage = new MemoryStorage();
    const original = recovery(editBriefField(createBriefDraft(source), "title", "Retained copy"), source);
    writeBriefRecovery(storage, original); const before = [...storage.data];
    const oversize = recovery(editBriefField(original.draft!, "objective", "界".repeat(Math.ceil(MAX_BRIEF_DRAFT_BYTES / 3))), source);
    expect(() => writeBriefRecovery(storage, oversize)).toThrow(/size limit/);
    expect([...storage.data]).toEqual(before);
    expect(oversize.draft!.fields.objective.raw).toContain("界");
  });
  it("refuses a full namespace without evicting another draft or touching other storage", () => {
    const source = project(), storage = new MemoryStorage();
    storage.setItem(BRIEF_DRAFT_STORAGE_PREFIX + "unreadable-existing-key", "x".repeat(MAX_BRIEF_RECOVERY_BYTES));
    storage.setItem("unrelated-theme", "dark");
    const before = [...storage.data];
    const next = recovery(editBriefField(createBriefDraft(source), "audience", "Keep in memory"), source);
    expect(() => writeBriefRecovery(storage, next)).toThrow(/budget is full/);
    expect([...storage.data]).toEqual(before);
  });
});


describe("brief author-copy write-before-prune admission", () => {
  const seeded = () => {
    const source = project(), storage = new MemoryStorage();
    const older = recovery(null, source, "clean-older", 10);
    const newer = recovery(null, source, "clean-newer", 11);
    // External previously persisted copies; intentionally keep BOTH until the
    // proposed replacement is atomically confirmed.
    storage.setItem(briefDraftStorageKey(older.scope, older.sessionId), JSON.stringify(older));
    storage.setItem(briefDraftStorageKey(newer.scope, newer.sessionId), JSON.stringify(newer));
    return { source, storage, older, newer };
  };

  it("does not prune earlier clean bytes before a quota-rejected new author copy", () => {
    const { source, storage } = seeded();
    const input = recovery(editBriefField(createBriefDraft(source), "title", "Kept original author intent"),
      source, "new-author", 12);
    const before = new Map(storage.data);
    storage.failWrites = true;
    expect(() => writeBriefRecovery(storage, input)).toThrow(/QuotaExceededError/);
    expect(storage.data).toEqual(before);
    expect(input.draft!.fields.title.raw).toBe("Kept original author intent");
  });

  it("does not silently report a new copy as durable if a browser ignores setItem", () => {
    const { source, storage } = seeded();
    const input = recovery(editBriefField(createBriefDraft(source), "objective", "Unsaved work"),
      source, "silent-writer", 12);
    const key = briefDraftStorageKey(input.scope, input.sessionId);
    const originalSet = storage.setItem.bind(storage);
    const before = new Map(storage.data);
    storage.setItem = (candidate, bytes) => {
      if (candidate !== key) originalSet(candidate, bytes);
    };
    expect(() => writeBriefRecovery(storage, input)).toThrow(/not confirmed/i);
    expect(storage.data).toEqual(before);
  });

  it("checks the replacement exists BEFORE pruning only clean copies in the exact scope", () => {
    const { source, storage, older, newer } = seeded();
    const unrelated = project(); unrelated.id += "-foreign";
    const other = recovery(editBriefField(createBriefDraft(unrelated), "objective", "Another project"),
      unrelated, "other-writer", 12);
    const otherKey = briefDraftStorageKey(other.scope, other.sessionId);
    storage.setItem(otherKey, JSON.stringify(other));
    const next = recovery(null, source, "verified-clean", 13);
    const key = briefDraftStorageKey(next.scope, next.sessionId);
    const savedOther = storage.getItem(otherKey);
    const originalSet = storage.setItem.bind(storage);
    storage.setItem = (candidate, raw) => {
      if (candidate === key) {
        expect(storage.getItem(briefDraftStorageKey(older.scope, older.sessionId))).not.toBeNull();
        expect(storage.getItem(briefDraftStorageKey(newer.scope, newer.sessionId))).not.toBeNull();
      }
      originalSet(candidate, raw);
    };
    writeBriefRecovery(storage, next);
    expect(readBriefRecoveries(storage, next.scope).entries.map(item => item.value.sessionId))
      .toEqual(["verified-clean"]);
    expect(storage.getItem(otherKey)).toBe(savedOther);
  });

  it("requires explicit clean-only scope cleanup instead of silently evicting another project's checkpoint", () => {
    const { source } = seeded(), storage = new MemoryStorage();
    for (let i = 0; i < MAX_BRIEF_RECOVERY_COPIES; i++) {
      const other = project(); other.id += "-clean-only-" + i;
      const clean = recovery(null, other, "writer-" + i, i);
      storage.setItem(briefDraftStorageKey(clean.scope, clean.sessionId), JSON.stringify(clean));
    }
    const before = new Map(storage.data);
    const input = recovery(editBriefField(createBriefDraft(source), "audience", "Another author"),
      source, "must-not-evict-foreign", 75);
    expect(() => writeBriefRecovery(storage, input)).toThrow(/budget is full/);
    expect(storage.data).toEqual(before);
  });
});


describe("brief session writer readback is required for automatic recovery", () => {
  it("accepts only a verified tab writer and rejects silent, thrown or mismatched pointers", () => {
    const values = new Map<string, string>();
    const read = (key: string) => values.get(key) ?? null;
    const working = {
      getItem: read, setItem: (key: string, value: string) => { values.set(key, value); },
    };
    writeBriefSessionPointer(working, "brief-session:scoped", "writer-A");
    expect(read("brief-session:scoped")).toBe("writer-A");
    expect(() => writeBriefSessionPointer({
      getItem: read, setItem: () => {},
    }, "brief-session:scoped", "writer-B")).toThrow(/not confirmed/);
    expect(read("brief-session:scoped")).toBe("writer-A");
    expect(() => writeBriefSessionPointer({
      getItem: read, setItem: () => { throw new Error("denied"); },
    }, "brief-session:scoped", "writer-B")).toThrow(/denied/);
    expect(read("brief-session:scoped")).toBe("writer-A");
  });

  it("does not replace a clean checkpoint when the storage response is silent", () => {
    const source = project(), storage = new MemoryStorage();
    const copy = recovery(null, source, "completed-clean", 1);
    const key = briefDraftStorageKey(copy.scope, copy.sessionId);
    const originalSet = storage.setItem.bind(storage);
    storage.setItem = (target, raw) => {
      if (target !== key) originalSet(target, raw);
    };
    expect(() => writeBriefRecovery(storage, copy)).toThrow(/not confirmed/i);
    expect(storage.getItem(key)).toBeNull();
  });
});
