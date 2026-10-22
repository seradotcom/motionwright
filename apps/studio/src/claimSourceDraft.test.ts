import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import type { Project, ProjectEvent } from "./types";
import {
  acknowledgeClaimDraft, claimChange, claimConflict, claimCopyKey, claimFields, claimPointerKey, claimScope, claimUnknown,
  createClaimDraft, decodeClaimCopy, editClaimDraft, encodeClaimCopy, inspectClaimOutcome, parseClaimRaw,
  prepareClaimSubmission, readClaimCopies, readClaimRecoveryPointer, reconcileClaimDraft, recoverClaimAfterReadFailure, releaseClaimOutcome, resolveClaimDraft,
  retainClaimFailure, retainReviewedClaimIntent, writeClaimCopy,
} from "./claimSourceDraft";
import type { ClaimCustody, ClaimDraft, ClaimStorage } from "./claimSourceDraft";

const claimId = "00000000-0000-4000-8000-000000000001";
const otherId = "00000000-0000-4000-8000-000000000002";
const attempt = "00000000-0000-4000-8000-000000000003";
const writer = "00000000-0000-4000-8000-000000000004";
const forkId = "00000000-0000-4000-8000-000000000005";
const newId = "00000000-0000-4000-8000-000000000006";
function source(): Project {
  const p = structuredClone(fixtureProject); p.locks = []; p.revision = 10; p.production_design = emptyProductionDesign();
  p.brief.claims = [
    { id: claimId, text: "Synthetic claim", context: "Fixture context", source: { kind: "url", url: "https://example.test/v1" }, source_revision: "v1" },
    { id: otherId, text: "Other author's claim", context: "Preserve all fields", source: { kind: "manual_note", label: "Original note" }, source_revision: null },
  ];
  return p;
}
function armed() {
  const project = source(), draft = editClaimDraft(createClaimDraft(project, claimId), "url", "https://example.test/v2");
  return { project, draft, prepared: prepareClaimSubmission(draft, project, attempt) };
}
function receipt(project: Project, draft: ClaimDraft): Project {
  const p = structuredClone(project), change = draft.pending!.change; p.revision++;
  if (change.type === "update_claim") p.brief.claims = p.brief.claims.map(claim => claim.id === change.claim_id ? { id: claim.id, ...change.replacement } : claim);
  else p.brief.claims.push({ id: newId, text: change.text, source: change.source, context: change.context, source_revision: change.source_revision });
  return p;
}
class MemoryStorage implements ClaimStorage {
  values = new Map<string, string>();
  get length() { return this.values.size; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}
const custody = (): ClaimCustody => ({ writerId: writer, expectedRaw: null, expectedPointer: null });
const journal = (project: Project, draft: ClaimDraft): ProjectEvent[] => [
  { revision: project.revision, change: draft.pending!.change, created_at: "2026-10-22T00:00:00Z" },
];

describe("Claim author input, identity and exact original response", () => {
  it("isolates project, generation, branch and individual claim buffers", () => {
    const p = source(), scope = claimScope(p, claimId);
    for (const field of ["id", "generation", "active_branch"] as const) expect(claimScope({ ...p, [field]: newId }, claimId)).not.toBe(scope);
    expect(claimScope(p, otherId)).not.toBe(scope); expect(claimScope(p, "new")).not.toBe(scope);
  });
  for (const raw of ["", "1e", "-", "01", "1.5", "9007199254740993"]) it("retains incomplete or inexact source revision " + JSON.stringify(raw), () => {
    const p = source(); let draft = createClaimDraft(p, claimId);
    draft = editClaimDraft(editClaimDraft(draft, "sourceKind", "project_revision"), "projectRevision", raw);
    expect(() => claimChange(draft, p)).toThrow();
    expect(decodeClaimCopy(encodeClaimCopy(draft, writer, 1), draft.scope, writer).draft.raw.projectRevision).toBe(raw);
    expect(p.brief.claims[0].source).toEqual({ kind: "url", url: "https://example.test/v1" });
  });
  it("keeps inactive source buffers and optional empty versus absent pin distinct", () => {
    const p = source(); let draft = editClaimDraft(createClaimDraft(p, claimId), "url", "  retained URL  ");
    draft = editClaimDraft(editClaimDraft(draft, "sourceKind", "project_revision"), "projectRevision", "1e");
    draft = editClaimDraft(draft, "sourceKind", "url");
    expect(parseClaimRaw(draft.raw, p, claimId).source).toEqual({ kind: "url", url: "  retained URL  " });
    expect(draft.raw.projectRevision).toBe("1e");
    draft = editClaimDraft(draft, "pin", ""); expect(parseClaimRaw(draft.raw, p, claimId).source_revision).toBe("");
    draft = editClaimDraft(draft, "pinEnabled", false); expect(parseClaimRaw(draft.raw, p, claimId).source_revision).toBeNull();
  });
  it("measures source fields and copies in UTF-8 bytes without truncating raw input", () => {
    const p = source(), draft = editClaimDraft(createClaimDraft(p, claimId), "context", "\u00e9".repeat(2001));
    expect(() => claimChange(draft, p)).toThrow("UTF-8"); expect(draft.raw.context.length).toBe(2001);
    const huge = editClaimDraft(draft, "context", "\u00e9".repeat(140000));
    expect(() => encodeClaimCopy(huge, writer)).toThrow("256 KiB"); expect(huge.raw.context.length).toBe(140000);
  });
  it("binds a source-only edit to the complete preimage and same claim ID", () => {
    const { project, prepared } = armed(), change = prepared.change;
    expect(change.type).toBe("update_claim");
    if (change.type !== "update_claim") throw new Error("Expected update");
    expect(change.claim_id).toBe(claimId); expect(change.expected).toEqual(claimFields(project.brief.claims[0]));
    expect(change.replacement.text).toBe(change.expected.text); expect(change.replacement.context).toBe(change.expected.context);
    expect(change.replacement.source_revision).toBe("v1");
  });
  it("rejects a changed preimage even when the project revision matches", () => {
    const { project, draft } = armed(); project.brief.claims[0].context = "Another author's context";
    expect(() => prepareClaimSubmission(draft, project, attempt)).toThrow("base");
    expect(draft.raw.context).toBe("Fixture context");
  });
  for (const resource of ["project", "claim"]) it("checks the existing " + resource + " content lock before arming", () => {
    const { project, draft } = armed();
    project.locks.push({ id: newId, resource: resource + ":" + (resource === "project" ? project.id : claimId), kind: "content", note: "Author lock" });
    expect(() => prepareClaimSubmission(draft, project, attempt)).toThrow("locked"); expect(draft.pending).toBeNull();
  });
  it("rejects a capture-bound source change without rewriting its claim or capture", () => {
    const { project, draft } = armed();
    project.production_design!.captures = [{ claim_id: claimId, source_revision: 2 } as NonNullable<NonNullable<Project["production_design"]>["captures"]>[number]];
    const before = structuredClone(project);
    expect(() => prepareClaimSubmission(draft, project, attempt)).toThrow("bound"); expect(project).toEqual(before);
  });
  it("does not acknowledge an observed project with identical submitted values", () => {
    const { project, prepared } = armed(), observed = receipt(project, prepared.draft);
    expect(reconcileClaimDraft(prepared.draft, observed)).toBe(prepared.draft);
    expect(claimUnknown(prepared.draft)).toBe(true); expect(claimConflict(prepared.draft, observed)).toBe(true);
  });
  it("keeps later input even after typing B and returning to the original submitted A", () => {
    const { project, prepared } = armed();
    const later = editClaimDraft(editClaimDraft(prepared.draft, "url", "B"), "url", prepared.draft.raw.url);
    const next = acknowledgeClaimDraft(later, attempt, receipt(project, prepared.draft), project);
    expect(next.raw).toEqual(later.raw); expect(next.intent).toBe(true); expect(next.sequence).toBe(later.sequence); expect(next.pending).toBeNull();
  });
  const badReplies: Array<[string, (p: Project) => void]> = [
    ["project", p => { p.id = newId; }], ["generation", p => { p.generation = newId; }],
    ["branch", p => { p.active_branch = newId; }], ["revision", p => { p.revision++; }],
    ["target source", p => { p.brief.claims[0].source = null; }],
    ["deleted other claim", p => { p.brief.claims.pop(); }],
    ["changed other claim", p => { p.brief.claims[1].context = "Overwritten"; }],
    ["reordered claims", p => { p.brief.claims.reverse(); }],
  ];
  for (const [label, mutate] of badReplies) it("rejects an original ACK with wrong " + label + " and keeps UNKNOWN", () => {
    const { project, prepared } = armed(), bad = receipt(project, prepared.draft); mutate(bad);
    expect(() => acknowledgeClaimDraft(prepared.draft, attempt, bad, project)).toThrow();
    expect(claimUnknown(prepared.draft)).toBe(true); expect(prepared.draft.raw.url).toBe("https://example.test/v2");
  });
  it("rejects a reply for another local attempt", () => {
    const { project, prepared } = armed();
    expect(() => acknowledgeClaimDraft(prepared.draft, newId, receipt(project, prepared.draft), project)).toThrow();
  });
  it("accepts exactly one appended claim while preserving all previous claim fields", () => {
    const project = source(), draft = editClaimDraft(createClaimDraft(project, "new"), "text", "New synthetic claim");
    const prepared = prepareClaimSubmission(draft, project, attempt), saved = receipt(project, prepared.draft);
    expect(acknowledgeClaimDraft(prepared.draft, attempt, saved, project).raw.text).toBe("");
    const extra = structuredClone(saved); extra.brief.claims.push({ ...extra.brief.claims.at(-1)!, id: forkId });
    expect(() => acknowledgeClaimDraft(prepared.draft, attempt, extra, project)).toThrow();
    const changed = structuredClone(saved); changed.brief.claims[0].text = "Changed";
    expect(() => acknowledgeClaimDraft(prepared.draft, attempt, changed, project)).toThrow("preserve");
  });
});

describe("Claim recovery custody without replay", () => {
  it("preserves corrupt and foreign copies and rejects extra execution authority", () => {
    const { draft } = armed(), raw = encodeClaimCopy(draft, writer, 1), storage = new MemoryStorage();
    storage.setItem(claimCopyKey(draft.scope, writer), "{corrupt");
    expect(readClaimCopies(storage, draft.scope).entries[0].value).toBeNull();
    expect(storage.getItem(claimCopyKey(draft.scope, writer))).toBe("{corrupt");
    expect(() => decodeClaimCopy(raw, claimScope(source(), otherId), writer)).toThrow();
    const authority = JSON.parse(raw); authority.grant = "not-authority";
    expect(() => decodeClaimCopy(JSON.stringify(authority), draft.scope, writer)).toThrow();
    expect(() => decodeClaimCopy(raw.slice(0, -1) + ',"scope":' + JSON.stringify(draft.scope) + "}", draft.scope, writer)).toThrow();
  });
  for (const uncertain of [false, true]) it("recovers original incomplete input after a read failure without inventing an edit, UNKNOWN=" + uncertain, () => {
    const { project, draft, prepared } = armed(), original = editClaimDraft(uncertain ? prepared.draft : draft, "projectRevision", "1e");
    const raw = encodeClaimCopy(original, writer, 1), recovered = decodeClaimCopy(raw, original.scope, writer).draft;
    const current = createClaimDraft(project, claimId), storage = new MemoryStorage(), tab = new MemoryStorage();
    storage.setItem(claimCopyKey(original.scope, writer), raw); tab.setItem(claimPointerKey(original.scope), writer);
    const next = recoverClaimAfterReadFailure(current, recovered, false);
    expect(next).toEqual(recovered); expect(next.raw.projectRevision).toBe("1e"); expect(next.sequence).toBe(recovered.sequence);
    const fork: ClaimCustody = { writerId: forkId, expectedRaw: null, expectedPointer: writer };
    writeClaimCopy(storage, tab, next, fork);
    expect(storage.getItem(claimCopyKey(original.scope, writer))).toBe(raw);
    expect(decodeClaimCopy(fork.expectedRaw!, next.scope, forkId).draft).toEqual(recovered);
  });
  for (const returnToInitial of [false, true]) it("preserves an explicit edit made while recovery is blocked, return-to-initial=" + returnToInitial, () => {
    const { project, prepared } = armed(), original = structuredClone(prepared.draft);
    const initial = createClaimDraft(project, claimId); let current = editClaimDraft(initial, "context", "Later author input");
    if (returnToInitial) current = editClaimDraft(current, "context", initial.raw.context);
    const next = recoverClaimAfterReadFailure(current, prepared.draft, true);
    expect(next.raw).toEqual(current.raw); expect(next.intent).toBe(true);
    expect(next.sequence).toBeGreaterThan(prepared.draft.pending!.sequence); expect(next.pending).toEqual(prepared.draft.pending);
    expect(prepared.draft).toEqual(original);
  });
  it("keeps a newer ordinary draft active while retaining the earlier ordinary copy", () => {
    const { project, draft } = armed(), current = editClaimDraft(createClaimDraft(project, claimId), "text", "Later author text");
    const original = encodeClaimCopy(draft, writer, 1);
    expect(recoverClaimAfterReadFailure(current, draft, true)).toEqual(current);
    expect(encodeClaimCopy(draft, writer, 1)).toBe(original);
  });
  it("two failed original reads do not become author edits before a successful recovery", () => {
    const { project, prepared } = armed(), original = encodeClaimCopy(prepared.draft, writer, 1);
    const storage = new MemoryStorage(), key = claimCopyKey(prepared.draft.scope, writer); storage.setItem(key, original);
    const current = createClaimDraft(project, claimId); let reads = 0;
    const read = () => { if (++reads <= 2) throw new Error("Injected read failure"); return storage.getItem(key)!; };
    expect(read).toThrow("read failure"); expect(read).toThrow("read failure");
    const recovered = decodeClaimCopy(read(), current.scope, writer).draft;
    expect(recoverClaimAfterReadFailure(current, recovered, false)).toEqual(recovered);
    expect(storage.getItem(key)).toBe(original); expect(current.sequence).toBe(0); expect(current.intent).toBe(false);
  });
  it("a recovered UNKNOWN is not a later edit when separate-copy publication must be retried", () => {
    const { project, prepared } = armed(), storage = new MemoryStorage(), tab = new MemoryStorage(), c = custody();
    const original = writeClaimCopy(storage, tab, prepared.draft, c), recovered = decodeClaimCopy(original, prepared.draft.scope, writer).draft;
    const next = recoverClaimAfterReadFailure(createClaimDraft(project, claimId), recovered, false);
    const fork: ClaimCustody = { writerId: forkId, expectedRaw: null, expectedPointer: writer }; let failed = false;
    storage.setItem = (key, value) => { if (!failed) { failed = true; throw new Error("Injected copy failure"); } storage.values.set(key, value); };
    expect(() => writeClaimCopy(storage, tab, next, fork)).toThrow("copy failure");
    expect(recoverClaimAfterReadFailure(next, recovered, false)).toEqual(recovered);
    writeClaimCopy(storage, tab, next, fork);
    expect(decodeClaimCopy(fork.expectedRaw!, next.scope, forkId).draft).toEqual(recovered);
    expect(storage.getItem(claimCopyKey(next.scope, writer))).toBe(original);
  });
  it("refuses a foreign owner or target while recovering a failed read", () => {
    const { project, draft } = armed();
    expect(() => recoverClaimAfterReadFailure(createClaimDraft(project, otherId), draft, false)).toThrow("owner or target");
    expect(() => recoverClaimAfterReadFailure(createClaimDraft({ ...project, active_branch: newId }, claimId), draft, true)).toThrow("owner or target");
  });
  it("does not replace an active uncertain attempt with a different recovered attempt", () => {
    const { project, draft, prepared } = armed(), current = prepareClaimSubmission(draft, project, newId).draft;
    const retained = structuredClone(current);
    expect(() => recoverClaimAfterReadFailure(current, prepared.draft, true)).toThrow("different attempt");
    expect(current).toEqual(retained);
  });
  it("first pointer read failure remains unobserved until a later valid writer is read", () => {
    const { project, prepared } = armed(), tab = new MemoryStorage(), key = claimPointerKey(prepared.draft.scope);
    tab.setItem(key, writer); const prior = { observed: false, value: null };
    const get = tab.getItem.bind(tab); let fail = true;
    tab.getItem = name => { if (fail) throw new Error("Injected pointer read failure"); return get(name); };
    expect(() => readClaimRecoveryPointer(tab, prepared.draft.scope, prior)).toThrow("pointer read failure");
    expect(prior).toEqual({ observed: false, value: null }); fail = false;
    const observed = readClaimRecoveryPointer(tab, prepared.draft.scope, prior);
    expect(observed).toEqual({ observed: true, value: writer });
    expect(recoverClaimAfterReadFailure(createClaimDraft(project, claimId), prepared.draft, false)).toEqual(prepared.draft);
  });
  it("a known writer cannot be replaced after its original data read failed", () => {
    const { prepared } = armed(), tab = new MemoryStorage(), key = claimPointerKey(prepared.draft.scope);
    tab.setItem(key, writer);
    const observed = readClaimRecoveryPointer(tab, prepared.draft.scope, { observed: false, value: null });
    tab.setItem(key, forkId);
    expect(() => readClaimRecoveryPointer(tab, prepared.draft.scope, observed)).toThrow("pointer changed");
    expect(observed).toEqual({ observed: true, value: writer }); expect(tab.getItem(key)).toBe(forkId);
  });
  it("observed null is a custody observation rather than a failed pointer read", () => {
    const { draft } = armed(), tab = new MemoryStorage();
    const observed = readClaimRecoveryPointer(tab, draft.scope, { observed: false, value: null });
    expect(observed).toEqual({ observed: true, value: null });
    tab.setItem(claimPointerKey(draft.scope), writer);
    expect(() => readClaimRecoveryPointer(tab, draft.scope, observed)).toThrow("pointer changed");
  });
  it("a repeated pointer read failure cannot erase a prior successful observation", () => {
    const { draft } = armed(), tab = new MemoryStorage(); tab.setItem(claimPointerKey(draft.scope), writer);
    const observed = readClaimRecoveryPointer(tab, draft.scope, { observed: false, value: null });
    tab.getItem = () => { throw new Error("Later read failure"); };
    expect(() => readClaimRecoveryPointer(tab, draft.scope, observed)).toThrow("Later read failure");
    expect(observed).toEqual({ observed: true, value: writer });
  });
  it("pointer replacement after a first recovered observation blocks successor publication", () => {
    const { project, prepared } = armed(), storage = new MemoryStorage(), tab = new MemoryStorage(), c = custody();
    const original = writeClaimCopy(storage, tab, prepared.draft, c);
    const observed = readClaimRecoveryPointer(tab, prepared.draft.scope, { observed: false, value: null });
    const recovered = decodeClaimCopy(storage.getItem(claimCopyKey(prepared.draft.scope, writer))!, prepared.draft.scope, writer).draft;
    const next = recoverClaimAfterReadFailure(createClaimDraft(project, claimId), recovered, false);
    tab.setItem(claimPointerKey(next.scope), otherId);
    const fork: ClaimCustody = { writerId: forkId, expectedRaw: null, expectedPointer: observed.value };
    expect(() => writeClaimCopy(storage, tab, next, fork)).toThrow("pointer");
    expect(storage.getItem(claimCopyKey(next.scope, writer))).toBe(original);
    expect(storage.getItem(claimCopyKey(next.scope, forkId))).toBeNull();
    expect(tab.getItem(claimPointerKey(next.scope))).toBe(otherId); expect(claimUnknown(next)).toBe(true);
  });
  it("recovers a NOT_SENT checkpoint conservatively as UNKNOWN", () => {
    const { prepared } = armed(), notSent = retainClaimFailure(prepared.draft, attempt, "Local copy failed", true);
    const restored = decodeClaimCopy(encodeClaimCopy(notSent, writer, 1), notSent.scope, writer).draft;
    expect(claimUnknown(restored)).toBe(true); expect(() => claimChange(restored, source())).toThrow();
  });
  for (const mode of ["data-silent", "data-throw", "pointer-silent", "pointer-throw", "pointer-write-throw"] as const)
    it("does not confirm custody after " + mode, () => {
      const { prepared } = armed(), storage = new MemoryStorage(), tab = new MemoryStorage(), c = custody();
      const put = mode.startsWith("data") ? storage : tab;
      put.setItem = (key, value) => {
        if (mode.endsWith("write-throw")) put.values.set(key, value);
        if (mode.endsWith("throw")) throw new Error("Injected storage failure");
      };
      expect(() => writeClaimCopy(storage, tab, prepared.draft, c)).toThrow();
      expect(prepared.draft.pending?.id).toBe(attempt); expect(claimUnknown(prepared.draft)).toBe(true);
    });
  it("detects an interleaved data replacement during pointer publication", () => {
    const { prepared } = armed(), storage = new MemoryStorage(), tab = new MemoryStorage(), c = custody();
    tab.setItem = (key, value) => { tab.values.set(key, value); storage.values.set(claimCopyKey(prepared.draft.scope, writer), "Other writer bytes"); };
    expect(() => writeClaimCopy(storage, tab, prepared.draft, c)).toThrow("changed during");
    expect(storage.getItem(claimCopyKey(prepared.draft.scope, writer))).toBe("Other writer bytes");
  });
  it("rechecks custody after budget enumeration and preserves the interleaved writer", () => {
    const { prepared } = armed(), storage = new MemoryStorage(), tab = new MemoryStorage(), c = custody();
    storage.values.set("unrelated", "kept");
    storage.key = () => { tab.values.set(claimPointerKey(prepared.draft.scope), otherId); return "unrelated"; };
    expect(() => writeClaimCopy(storage, tab, prepared.draft, c)).toThrow("Another writer");
    expect(storage.getItem(claimCopyKey(prepared.draft.scope, writer))).toBeNull();
  });
  it("does not overwrite an earlier saved checkpoint with new author input", () => {
    const project = source(), clean = createClaimDraft(project, claimId), storage = new MemoryStorage(), tab = new MemoryStorage(), c = custody();
    const original = writeClaimCopy(storage, tab, clean, c);
    expect(() => writeClaimCopy(storage, tab, editClaimDraft(clean, "context", "New text"), c)).toThrow("checkpoint");
    expect(storage.getItem(claimCopyKey(clean.scope, writer))).toBe(original);
  });
  it("stable read-history-read on the original revision cannot release a future commit", async () => {
    const { project, prepared } = armed();
    const review = await inspectClaimOutcome(prepared.draft, project, { readProject: async () => structuredClone(project), readHistory: async () => [] });
    expect(() => releaseClaimOutcome(prepared.draft, project, review)).toThrow("could still commit");
    expect(claimUnknown(prepared.draft)).toBe(true);
  });
  it("requires complete history and a stable second read without acknowledging equal values", async () => {
    const { project, prepared } = armed(), saved = receipt(project, prepared.draft);
    const review = await inspectClaimOutcome(prepared.draft, saved, { readProject: async () => structuredClone(saved), readHistory: async () => journal(saved, prepared.draft) });
    expect(review.matchingValueRevisions).toEqual([saved.revision]); expect(claimUnknown(prepared.draft)).toBe(true);
    expect(releaseClaimOutcome(prepared.draft, saved, review).pending).toBeNull();
    await expect(inspectClaimOutcome(prepared.draft, saved, { readProject: async () => structuredClone(saved), readHistory: async () => [] })).rejects.toThrow("incomplete");
    let read = 0;
    await expect(inspectClaimOutcome(prepared.draft, saved, {
      readProject: async () => { const value = structuredClone(saved); if (read++) value.brief.claims[1].context = "Changed without valid authority"; return value; },
      readHistory: async () => journal(saved, prepared.draft),
    })).rejects.toThrow("changed");
  });
  for (const phase of [1, 2]) it("retains exact old UNKNOWN bytes when separate-copy phase " + phase + " fails", async () => {
    const { project, prepared } = armed(), saved = receipt(project, prepared.draft), storage = new MemoryStorage(), tab = new MemoryStorage(), c = custody();
    const original = writeClaimCopy(storage, tab, prepared.draft, c);
    const review = await inspectClaimOutcome(prepared.draft, saved, { readProject: async () => structuredClone(saved), readHistory: async () => journal(saved, prepared.draft) });
    const next = releaseClaimOutcome(prepared.draft, saved, review);
    let puts = 0;
    storage.setItem = (key, value) => { if (++puts === phase) throw new Error("Injected fork failure"); storage.values.set(key, value); };
    expect(() => retainReviewedClaimIntent(storage, tab, prepared.draft, next, c, forkId)).toThrow();
    expect(storage.getItem(claimCopyKey(prepared.draft.scope, writer))).toBe(original);
    expect(decodeClaimCopy(original, prepared.draft.scope, writer).draft.pending?.id).toBe(attempt);
  });
  it("a confirmed separate edit retains the exact UNKNOWN copy and uses the later explicit base", async () => {
    const { project, prepared } = armed(), saved = receipt(project, prepared.draft), storage = new MemoryStorage(), tab = new MemoryStorage(), c = custody();
    const original = writeClaimCopy(storage, tab, prepared.draft, c);
    const review = await inspectClaimOutcome(prepared.draft, saved, { readProject: async () => structuredClone(saved), readHistory: async () => journal(saved, prepared.draft) });
    const next = releaseClaimOutcome(prepared.draft, saved, review), fork = retainReviewedClaimIntent(storage, tab, prepared.draft, next, c, forkId);
    expect(storage.getItem(claimCopyKey(prepared.draft.scope, writer))).toBe(original);
    expect(decodeClaimCopy(fork.expectedRaw!, next.scope, forkId).draft.pending).toBeNull();
    expect(next.baseRevision).toBe(saved.revision); expect(next.raw).toEqual(prepared.draft.raw);
    expect(() => claimChange(next, saved)).toThrow("no changes");
  });
  for (const claimIndex of [0, 1]) it("rejects a fresh document with the same stamp but changed claim " + claimIndex, async () => {
    const { project, prepared } = armed(), saved = receipt(project, prepared.draft), fresh = structuredClone(saved);
    fresh.brief.claims[claimIndex].context = "Different content under the same stamp";
    const retained = structuredClone(prepared.draft); let historyCalls = 0;
    await expect(inspectClaimOutcome(prepared.draft, saved, {
      readProject: async () => structuredClone(fresh),
      readHistory: async () => { historyCalls++; return journal(saved, prepared.draft); },
    })).rejects.toThrow("Reload");
    expect(historyCalls).toBe(0); expect(prepared.draft).toEqual(retained);
  });
  for (const claimIndex of [0, 1]) it("cannot release into an unreviewed same-stamp document with changed claim " + claimIndex, async () => {
    const { project, prepared } = armed(), saved = receipt(project, prepared.draft);
    const review = await inspectClaimOutcome(prepared.draft, saved, {
      readProject: async () => structuredClone(saved), readHistory: async () => journal(saved, prepared.draft),
    });
    const changed = structuredClone(saved); changed.brief.claims[claimIndex].context = "Changed after inspection";
    const retained = structuredClone(prepared.draft);
    expect(() => releaseClaimOutcome(prepared.draft, changed, review)).toThrow("current project");
    expect(prepared.draft).toEqual(retained); expect(claimUnknown(prepared.draft)).toBe(true);
  });
  it("explicitly choosing current values preserves the old draft object and does not dispatch", () => {
    const { project, draft } = armed(), current = structuredClone(project); current.revision++; current.brief.claims[0].context = "External context";
    const kept = resolveClaimDraft(draft, current, "draft"); expect(kept.raw).toEqual(draft.raw); expect(kept.base?.context).toBe("External context");
    const saved = resolveClaimDraft(draft, current, "saved"); expect(saved.raw.context).toBe("External context"); expect(draft.raw.context).toBe("Fixture context");
  });
});

describe("Claim pointer publication after a partial local write", () => {
  function partial(previousWriter = false) {
    const { project, prepared } = armed();
    const draft = editClaimDraft(prepared.draft, "projectRevision", "1e");
    const storage = new MemoryStorage(), tab = new MemoryStorage();
    const original = previousWriter ? writeClaimCopy(storage, tab, createClaimDraft(project, claimId), custody()) : null;
    const c: ClaimCustody = { writerId: forkId, expectedRaw: null, expectedPointer: previousWriter ? writer : null };
    const key = claimCopyKey(draft.scope, forkId), pointer = claimPointerKey(draft.scope);
    const coldRead = () => {
      const selected = tab.getItem(pointer);
      if (selected === null) throw new Error("Reload has no claim writer pointer");
      const raw = storage.getItem(claimCopyKey(draft.scope, selected));
      if (raw === null) throw new Error("Reload has no selected claim recovery copy");
      return decodeClaimCopy(raw, draft.scope, selected).draft;
    };
    return { draft, storage, tab, c, key, pointer, original, coldRead };
  }
  function faultPointer(tab: MemoryStorage, pointer: string, mode: "silent" | "throw-before" | "write-throw", once = true) {
    const put = tab.setItem.bind(tab); let active = true;
    tab.setItem = (key, value) => {
      if (active && key === pointer) {
        if (once) active = false;
        if (mode === "write-throw") put(key, value);
        if (mode !== "silent") throw new Error("Injected pointer publication failure");
        return;
      }
      put(key, value);
    };
    return () => { tab.setItem = put; };
  }
  for (const previousWriter of [false, true]) for (const mode of ["silent", "throw-before"] as const)
    it("same-draft retry publishes its own pointer after " + mode + ", earlier-writer=" + previousWriter, () => {
      const { draft, storage, tab, c, key, pointer, original, coldRead } = partial(previousWriter);
      const retained = structuredClone(draft), expectedPointer = c.expectedPointer;
      faultPointer(tab, pointer, mode);
      expect(() => writeClaimCopy(storage, tab, draft, c)).toThrow();
      const data = storage.getItem(key);
      expect(data).not.toBeNull(); expect(c.expectedRaw).toBe(data);
      expect(tab.getItem(pointer)).toBe(expectedPointer); expect(c.expectedPointer).toBe(expectedPointer);
      expect(writeClaimCopy(storage, tab, draft, c)).toBe(data);
      expect(tab.getItem(pointer)).toBe(forkId); expect(c.expectedPointer).toBe(forkId);
      expect(storage.getItem(key)).toBe(data);
      expect(coldRead()).toEqual(retained); expect(coldRead().raw.projectRevision).toBe("1e");
      expect(claimUnknown(coldRead())).toBe(true); expect(draft).toEqual(retained);
      if (original !== null) expect(storage.getItem(claimCopyKey(draft.scope, writer))).toBe(original);
    });
  for (const mode of ["silent", "throw-before"] as const)
    it("same-draft retry remains blocked while pointer publication still " + mode + " fails", () => {
      const { draft, storage, tab, c, key, pointer, coldRead } = partial();
      faultPointer(tab, pointer, mode, false);
      expect(() => writeClaimCopy(storage, tab, draft, c)).toThrow();
      const data = storage.getItem(key);
      expect(() => writeClaimCopy(storage, tab, draft, c)).toThrow();
      expect(storage.getItem(key)).toBe(data); expect(c.expectedRaw).toBe(data);
      expect(c.expectedPointer).toBeNull(); expect(tab.getItem(pointer)).toBeNull();
      expect(coldRead).toThrow("no claim writer"); expect(claimUnknown(draft)).toBe(true);
    });
  it("a setter that writes the new pointer then throws cannot grant the active session publication custody", () => {
    const { draft, storage, tab, c, key, pointer, coldRead } = partial();
    faultPointer(tab, pointer, "write-throw");
    expect(() => writeClaimCopy(storage, tab, draft, c)).toThrow("publication failure");
    const data = storage.getItem(key);
    expect(tab.getItem(pointer)).toBe(forkId); expect(c.expectedPointer).toBeNull();
    expect(() => writeClaimCopy(storage, tab, draft, c)).toThrow("Another writer");
    expect(c.expectedPointer).toBeNull(); expect(storage.getItem(key)).toBe(data);
    expect(coldRead()).toEqual(draft); expect(claimUnknown(coldRead())).toBe(true);
  });
  it("same-draft retry cannot overwrite another writer published after the original pointer failure", () => {
    const { draft, storage, tab, c, key, pointer, original } = partial(true);
    faultPointer(tab, pointer, "silent");
    expect(() => writeClaimCopy(storage, tab, draft, c)).toThrow();
    const data = storage.getItem(key);
    tab.setItem(pointer, otherId);
    expect(() => writeClaimCopy(storage, tab, draft, c)).toThrow("Another writer");
    expect(tab.getItem(pointer)).toBe(otherId); expect(c.expectedPointer).toBe(writer);
    expect(storage.getItem(key)).toBe(data);
    expect(storage.getItem(claimCopyKey(draft.scope, writer))).toBe(original);
  });
  it("same-draft pointer repair rechecks its data after publication without overwriting an interleaved replacement", () => {
    const { draft, storage, tab, c, key, pointer } = partial();
    faultPointer(tab, pointer, "silent");
    expect(() => writeClaimCopy(storage, tab, draft, c)).toThrow();
    tab.setItem = (name, value) => { tab.values.set(name, value); storage.values.set(key, "Replacement during retry publication"); };
    expect(() => writeClaimCopy(storage, tab, draft, c)).toThrow("changed during");
    expect(storage.getItem(key)).toBe("Replacement during retry publication");
    expect(claimUnknown(draft)).toBe(true);
  });
});
