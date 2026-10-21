import { describe, expect, test } from "vitest";
import { fixtureProject } from "./fixture";
import {
  acknowledgeReviewDraft, advanceReviewDraft, createReviewDraft, decodeReviewCopy, editReviewDraft,
  inspectReviewOutcome, separateReviewedIntent, reviewOutcomeMatches, retainSeparateReviewIntent,
  encodeReviewCopy, MAX_REVIEW_COPIES, prepareReviewSubmission, reconcileReviewDraft, retainReviewFailure,
  REVIEW_RECOVERY_PREFIX, reviewCopyKey, reviewHasInput, reviewPointerKey, reviewScope, reviewTimes,
  reviewUnknown, writeReviewCopy,
} from "./reviewDraft";
import type { ReviewChange, ReviewCopy, ReviewCustody, ReviewDraft } from "./reviewDraft";
import type { Project } from "./types";

const attempt = "20000000-0000-4000-8000-000000000001";
const other = "20000000-0000-4000-8000-000000000002";
const newReview = "20000000-0000-4000-8000-000000000003";
const source = () => structuredClone(fixtureProject);
const commentDraft = (project = source()) => editReviewDraft(
  createReviewDraft(project, { kind: "comment", id: "scene:" + project.scenes[0].id }), "body", "  Submitted comment  ");
const commentChange = (draft: ReviewDraft, project = source()): ReviewChange => ({
  type: "add_review", kind: draft.raw.kind, resource: "scene:" + project.scenes[0].id,
  body: draft.raw.body.trim(), start: null, end: null, locale: null, profile_id: null,
});
function pendingComment() {
  const project = source(), draft = commentDraft(project), change = commentChange(draft, project);
  return { project, draft: prepareReviewSubmission(draft, project, change, attempt), change };
}
function receipt(project: Project, change: ReviewChange): Project {
  const saved = structuredClone(project); saved.revision++;
  if (change.type === "add_review") {
    saved.reviews.push({ id: newReview, kind: change.kind, body: change.body, status: "open", resolution: null, resolved_at: null,
      created_at: "2026-01-01T00:00:00Z", anchor: { resource: change.resource, branch_id: project.active_branch,
        revision: project.revision, start: change.start, end: change.end, locale: change.locale, profile_id: change.profile_id } });
  } else {
    const target = saved.reviews.find(review => review.id === change.review_id)!;
    target.status = change.type === "resolve_review" ? "resolved" : "needs_recheck";
    target.resolution = change.type === "resolve_review" ? change.resolution : null;
    target.resolved_at = change.type === "resolve_review" ? "2026-01-01T00:00:00Z" : null;
  }
  return saved;
}
class MemoryStorage {
  data = new Map<string, string>();
  get length() { return this.data.size; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
}
const custody = (writerId = attempt, expectedPointer: string | null = null): ReviewCustody =>
  ({ writerId, expectedRaw: null, expectedPointer });
const copy = (draft: ReviewDraft): ReviewCopy =>
  ({ schema: "motionwright.review-recovery/1", scope: draft.scope, writerId: attempt, updatedAt: 1, draft });

describe("exact review owner and raw author input", () => {
  for (const field of ["id", "generation", "active_branch"] as const) {
    test("separates equal raw drafts under a different " + field, () => {
      const project = source(), target = { kind: "comment" as const, id: "scene:" + project.scenes[0].id };
      expect(reviewScope(project, target)).not.toBe(reviewScope({ ...project, [field]: other }, target));
      expect(() => reconcileReviewDraft(commentDraft(project), { ...project, [field]: other })).toThrow(/another project/);
    });
  }
  test("separates comment, resolution and alternative targets", () => {
    const project = source(), id = project.scenes[0].id;
    expect(new Set([reviewScope(project, { kind: "comment", id: "scene:" + id }),
      reviewScope(project, { kind: "resolution", id }), reviewScope(project, { kind: "alternative", id }),
      reviewScope(project, { kind: "comment", id: "project:" + project.id })]).size).toBe(4);
  });
  test("preserves whitespace and incomplete input without numeric coercion", () => {
    let draft = commentDraft();
    draft = editReviewDraft(draft, "rangeStart", "1e"); draft = editReviewDraft(draft, "rangeEnd", "1001/");
    expect(draft.raw).toMatchObject({ body: "  Submitted comment  ", rangeStart: "1e", rangeEnd: "1001/" });
    expect(decodeReviewCopy(encodeReviewCopy(copy(draft)), draft.scope, attempt).draft).toEqual(draft);
  });
  test("uses exact fractions for an authored time range", () => {
    const project = source(), draft = commentDraft(project);
    Object.assign(draft.raw, { timeMode: "range", rangeStart: "1001/30000", rangeEnd: "1/2" });
    expect(reviewTimes(project, "scene:" + project.scenes[0].id, draft.raw, 0))
      .toEqual({ start: { num: "1001", den: "30000" }, end: { num: "1", den: "2" } });
  });
  test.each(["1e", "1001/", "", "0x1", "Infinity"])("incomplete or invalid time %j remains unsaveable and unchanged", value => {
    const project = source(), draft = commentDraft(project);
    Object.assign(draft.raw, { timeMode: "range", rangeStart: value, rangeEnd: "1" });
    expect(() => reviewTimes(project, "scene:" + project.scenes[0].id, draft.raw, 0)).toThrow();
    expect(draft.raw.rangeStart).toBe(value);
  });
  test.each([["-1", "1"], ["2", "1"], ["0", "8"]])("range %s to %s cannot leave its selected scene", (start, end) => {
    const project = source(), draft = commentDraft(project);
    Object.assign(draft.raw, { timeMode: "range", rangeStart: start, rangeEnd: end });
    expect(() => reviewTimes(project, "scene:" + project.scenes[0].id, draft.raw, 0)).toThrow(/within its resource/);
  });
  test("clean observations update only the base while active raw intent retains its source", () => {
    const project = source(), clean = createReviewDraft(project, { kind: "comment", id: "project:" + project.id });
    const later = { ...project, revision: project.revision + 3 };
    expect(reconcileReviewDraft(clean, later).baseRevision).toBe(later.revision);
    const authored = editReviewDraft(clean, "body", "Review at original source");
    expect(reconcileReviewDraft(authored, later)).toEqual(authored);
    expect(advanceReviewDraft(authored, later).raw).toEqual(authored.raw);
    expect(advanceReviewDraft(authored, later).baseRevision).toBe(later.revision);
  });
});

describe("original callback acknowledgement and uncertainty", () => {
  test("a valid original add receipt clears only its unchanged submitted comment", () => {
    const { project, draft, change } = pendingComment();
    const saved = acknowledgeReviewDraft(draft, attempt, receipt(project, change));
    expect(saved.raw.body).toBe(""); expect(saved.pending).toBeNull(); expect(reviewHasInput(saved)).toBe(false);
  });
  test("an equal observed Project cannot acknowledge or clear the original pending input", () => {
    const { project, draft, change } = pendingComment();
    expect(reconcileReviewDraft(draft, receipt(project, change))).toEqual(draft);
    expect(reviewUnknown(reconcileReviewDraft(draft, receipt(project, change)))).toBe(true);
  });
  test("an unrelated callback identity cannot acknowledge this original request", () => {
    const { project, draft, change } = pendingComment();
    expect(acknowledgeReviewDraft(draft, other, receipt(project, change))).toBe(draft);
  });
  test.each(["body", "rangeStart"] as const)("a later %s edit survives the exact original acknowledgement", field => {
    const { project, draft, change } = pendingComment();
    const later = editReviewDraft(draft, field, field === "body" ? "  Later comment\n" : "1e");
    const saved = acknowledgeReviewDraft(later, attempt, receipt(project, change));
    expect(saved.raw).toEqual(later.raw); expect(saved.pending).toBeNull(); expect(reviewHasInput(saved)).toBe(true);
  });
  test.each(["id", "generation", "active_branch"] as const)("an original reply for another %s retains UNKNOWN", field => {
    const { project, draft, change } = pendingComment();
    const returned = { ...receipt(project, change), [field]: other };
    expect(() => acknowledgeReviewDraft(draft, attempt, returned)).toThrow(/another project/);
    expect(reviewUnknown(draft)).toBe(true); expect(draft.raw.body).toBe("  Submitted comment  ");
  });
  test.each([0, 2])("revision offset %s cannot replace the exact original receipt", offset => {
    const { project, draft, change } = pendingComment();
    const returned = receipt(project, change); returned.revision = project.revision + offset;
    expect(() => acknowledgeReviewDraft(draft, attempt, returned)).toThrow(/exact review/);
  });
  test.each(["missing", "body", "anchor", "duplicate"] as const)("a %s review mismatch cannot authorize clearing", mode => {
    const { project, draft, change } = pendingComment();
    const returned = receipt(project, change), review = returned.reviews.at(-1)!;
    if (mode === "missing") returned.reviews.pop();
    if (mode === "body") review.body = "Other body";
    if (mode === "anchor") review.anchor.revision++;
    if (mode === "duplicate") returned.reviews.push({ ...review, id: other });
    expect(() => acknowledgeReviewDraft(draft, attempt, returned)).toThrow(/exact review/);
  });
  test("uncertain failure and reload preserve the original attempt and prohibit replay", () => {
    const { project, draft, change } = pendingComment();
    const failed = retainReviewFailure(draft, attempt, "Delivery may have failed");
    const recovered = decodeReviewCopy(encodeReviewCopy(copy(failed)), failed.scope, attempt).draft;
    expect(recovered.raw).toEqual(draft.raw); expect(recovered.pending?.id).toBe(attempt);
    expect(() => prepareReviewSubmission(recovered, project, change, other)).toThrow(/UNKNOWN/);
    expect(() => advanceReviewDraft(recovered, project)).toThrow(/original review outcome/);
  });
  test("a proven unsent storage failure can be submitted only through a new explicit attempt", () => {
    const { project, draft, change } = pendingComment();
    const unsent = retainReviewFailure(draft, attempt, "No callback invoked", true);
    expect(reviewUnknown(unsent)).toBe(false);
    expect(prepareReviewSubmission(unsent, project, change, other).pending?.id).toBe(other);
  });
  test("resolution acknowledgement checks the exact item, anchor and authored reason", () => {
    const project = source(), target = project.reviews[0];
    let draft = createReviewDraft(project, { kind: "resolution", id: target.id });
    draft = editReviewDraft(draft, "resolution", "  Checked original evidence  ");
    const change: ReviewChange = { type: "resolve_review", review_id: target.id, resolution: draft.raw.resolution.trim() };
    draft = prepareReviewSubmission(draft, project, change, attempt);
    const returned = receipt(project, change);
    expect(acknowledgeReviewDraft(draft, attempt, returned).raw.resolution).toBe("");
    returned.reviews[0].anchor.resource = "project:" + project.id;
    expect(() => acknowledgeReviewDraft(draft, attempt, returned)).toThrow(/exact review/);
  });
  test("a later resolution survives both resolution and explicit reopen receipts", () => {
    const project = source(), id = project.reviews[0].id;
    let draft = editReviewDraft(createReviewDraft(project, { kind: "resolution", id }), "resolution", "Submitted reason");
    const change: ReviewChange = { type: "resolve_review", review_id: id, resolution: draft.raw.resolution };
    draft = prepareReviewSubmission(draft, project, change, attempt);
    draft = editReviewDraft(draft, "resolution", "  Later detailed reason  ");
    const saved = receipt(project, change);
    draft = acknowledgeReviewDraft(draft, attempt, saved);
    expect(draft.raw.resolution).toBe("  Later detailed reason  ");
    const reopen: ReviewChange = { type: "reopen_review", review_id: id };
    draft = prepareReviewSubmission(draft, saved, reopen, other);
    draft = acknowledgeReviewDraft(draft, other, receipt(saved, reopen));
    expect(draft.raw.resolution).toBe("  Later detailed reason  "); expect(reviewHasInput(draft)).toBe(true);
  });
  test("an authored draft cannot silently move to a later source or missing scene", () => {
    const project = source(), draft = commentDraft(project), change = commentChange(draft, project);
    expect(() => prepareReviewSubmission(draft, { ...project, revision: project.revision + 1 }, change, attempt)).toThrow(/current revision/);
    expect(() => prepareReviewSubmission(draft, { ...project, scenes: [] }, change, attempt)).toThrow(/no longer present/);
  });
  test("the request must preserve the exact raw range and resource binding", () => {
    const project = source(), draft = commentDraft(project);
    Object.assign(draft.raw, { timeMode: "range", rangeStart: "1/3", rangeEnd: "2/3" });
    const change = commentChange(draft, project);
    expect(() => prepareReviewSubmission(draft, project, change, attempt)).toThrow(/exact authored review time/);
    if (change.type !== "add_review") throw new Error("Unexpected unit arrangement");
    change.start = { num: "1", den: "3" }; change.end = { num: "2", den: "3" };
    expect(prepareReviewSubmission(draft, project, change, attempt).pending?.change).toEqual(change);
  });
});

describe("verified recovery custody and bounded storage", () => {
  test("a write is accepted only with exact bytes and a matching tab pointer readback", () => {
    const draft = commentDraft(), store = new MemoryStorage(), tab = new MemoryStorage(), writer = custody();
    writeReviewCopy(store, tab, draft, writer);
    const raw = store.getItem(reviewCopyKey(draft.scope, attempt))!;
    expect(raw).toBe(writer.expectedRaw); expect(tab.getItem(reviewPointerKey(draft.scope))).toBe(attempt);
    expect(decodeReviewCopy(raw, draft.scope, attempt).draft).toEqual(draft);
  });
  test("a cloned tab forks its writer and never rewrites the earlier copy", () => {
    const draft = commentDraft(), store = new MemoryStorage(), tab = new MemoryStorage(), first = custody();
    writeReviewCopy(store, tab, draft, first);
    const original = store.getItem(reviewCopyKey(draft.scope, attempt));
    const fork = custody(other, attempt); writeReviewCopy(store, tab, editReviewDraft(draft, "body", "Fork reason"), fork);
    expect(store.getItem(reviewCopyKey(draft.scope, attempt))).toBe(original);
    expect(tab.getItem(reviewPointerKey(draft.scope))).toBe(other);
  });
  test("a peer byte change is preserved and blocks an overwrite", () => {
    const draft = commentDraft(), store = new MemoryStorage(), tab = new MemoryStorage(), writer = custody();
    writeReviewCopy(store, tab, draft, writer);
    const key = reviewCopyKey(draft.scope, attempt); store.setItem(key, "peer recovery bytes");
    expect(() => writeReviewCopy(store, tab, draft, writer)).toThrow(/Another window/);
    expect(store.getItem(key)).toBe("peer recovery bytes");
  });
  test("a peer pointer change is preserved and blocks an overwrite", () => {
    const draft = commentDraft(), store = new MemoryStorage(), tab = new MemoryStorage(), writer = custody();
    tab.setItem(reviewPointerKey(draft.scope), other);
    expect(() => writeReviewCopy(store, tab, draft, writer)).toThrow(/Another writer/);
    expect(store.length).toBe(0); expect(tab.getItem(reviewPointerKey(draft.scope))).toBe(other);
  });
  test("silent write loss cannot count as confirmed custody", () => {
    const draft = commentDraft(), store = new MemoryStorage(), tab = new MemoryStorage();
    store.setItem = () => {};
    expect(() => writeReviewCopy(store, tab, draft, custody())).toThrow(/write was not confirmed/);
    expect(tab.length).toBe(0);
  });
  test("silent pointer loss keeps the written copy but cannot acknowledge custody", () => {
    const draft = commentDraft(), store = new MemoryStorage(), tab = new MemoryStorage();
    tab.setItem = () => {};
    expect(() => writeReviewCopy(store, tab, draft, custody())).toThrow(/pointer or bytes/);
    expect(store.length).toBe(1); expect(tab.length).toBe(0);
  });
  test("the copy-count budget blocks a new write without eviction or author loss", () => {
    const draft = commentDraft(), store = new MemoryStorage(), tab = new MemoryStorage();
    for (let i = 0; i < MAX_REVIEW_COPIES; i++) store.setItem(REVIEW_RECOVERY_PREFIX + i, "old-" + i);
    const earlier = [...store.data];
    expect(() => writeReviewCopy(store, tab, draft, custody())).toThrow(/256 copies/);
    expect([...store.data]).toEqual(earlier); expect(draft.raw.body).toBe("  Submitted comment  "); expect(tab.length).toBe(0);
  });
  test("the byte budget blocks a new write without rewriting earlier recovery", () => {
    const draft = commentDraft(), store = new MemoryStorage(), tab = new MemoryStorage();
    store.setItem(REVIEW_RECOVERY_PREFIX + "other", "x".repeat(4 * 1024 * 1024));
    const earlier = [...store.data];
    expect(() => writeReviewCopy(store, tab, draft, custody())).toThrow(/4 MiB/);
    expect([...store.data]).toEqual(earlier); expect(tab.length).toBe(0);
  });
  test("oversized raw input remains in memory when a recovery record cannot be encoded", () => {
    const draft = editReviewDraft(commentDraft(), "body", "x".repeat(130 * 1024));
    const store = new MemoryStorage(), tab = new MemoryStorage();
    expect(() => writeReviewCopy(store, tab, draft, custody())).toThrow(/128 KiB/);
    expect(draft.raw.body.length).toBe(130 * 1024); expect(store.length).toBe(0);
  });
  test("a clean acknowledged checkpoint cannot be rewritten by a later intent", () => {
    const { project, draft, change } = pendingComment();
    const clean = acknowledgeReviewDraft(draft, attempt, receipt(project, change));
    const store = new MemoryStorage(), tab = new MemoryStorage(), writer = custody();
    writeReviewCopy(store, tab, clean, writer);
    const original = store.getItem(reviewCopyKey(clean.scope, attempt));
    expect(() => writeReviewCopy(store, tab, editReviewDraft(clean, "body", "Later author"), writer)).toThrow(/immutable/);
    expect(store.getItem(reviewCopyKey(clean.scope, attempt))).toBe(original);
  });
  test.each(["schema", "scope", "writer", "shape", "attempt"] as const)("a corrupt or foreign %s recovery is rejected without normalization", mode => {
    const { draft } = pendingComment(), value = copy(draft) as unknown as Record<string, unknown>;
    if (mode === "schema") value.schema = "motionwright.review-recovery/999";
    if (mode === "scope") value.scope = reviewScope({ ...source(), generation: other }, { kind: "comment", id: "scene:" + source().scenes[0].id });
    if (mode === "writer") value.writerId = other;
    if (mode === "shape") (value.draft as ReviewDraft).raw.body = 12 as unknown as string;
    if (mode === "attempt") (value.draft as ReviewDraft).pending!.id = "unknown";
    expect(() => decodeReviewCopy(JSON.stringify(value), draft.scope, attempt)).toThrow();
  });
});

describe("explicit UNKNOWN inspection and separate author intent", () => {
  async function inspect(later = false) {
    const data = pendingComment(), saved = receipt(data.project, data.change);
    const draft = later ? editReviewDraft(data.draft, "body", "  Separate later intent  ") : data.draft;
    let reads = 0;
    const outcome = await inspectReviewOutcome(draft, saved, {
      readProject: async () => { reads++; return structuredClone(saved); },
      readHistory: async () => [{ revision: saved.revision, change: data.change, created_at: "2026-01-01T00:00:00Z" }],
    });
    return { ...data, draft, saved, outcome, reads };
  }
  test("two exact source reads and contiguous history are observations, never original acknowledgement", async () => {
    const { draft, saved, outcome, reads } = await inspect();
    expect(reads).toBe(2); expect(outcome.matchingValueRevisions).toEqual([saved.revision]);
    expect(reviewOutcomeMatches(draft, saved, outcome)).toBe(true);
    expect(draft.pending?.id).toBe(attempt); expect(reviewUnknown(draft)).toBe(true);
    expect(draft.raw.body).toBe("  Submitted comment  ");
  });
  test("unchanged submitted text starts blank after explicit inspection rather than replaying add_review", async () => {
    const { draft, saved, outcome } = await inspect();
    const next = separateReviewedIntent(draft, saved, outcome);
    expect(next.raw.body).toBe(""); expect(next.pending).toBeNull(); expect(next.baseRevision).toBe(saved.revision);
    expect(reviewUnknown(draft)).toBe(true);
    expect(() => prepareReviewSubmission(next, saved, commentChange(next, saved), other)).toThrow(/exact draft/);
  });
  test("a later raw intent uses a new attempt identity without consuming or changing the original UNKNOWN", async () => {
    const { draft, saved, outcome } = await inspect(true), original = structuredClone(draft);
    const next = separateReviewedIntent(draft, saved, outcome);
    expect(next.raw.body).toBe("  Separate later intent  ");
    const pending = prepareReviewSubmission(next, saved, commentChange(next, saved), other);
    expect(pending.pending?.id).toBe(other); expect(pending.pending?.revision).toBe(saved.revision);
    expect(draft).toEqual(original); expect(reviewUnknown(draft)).toBe(true);
  });
  test.each(["gap", "empty", "duplicate", "beyond"] as const)("a %s journal cannot release UNKNOWN", async mode => {
    const { project, draft, change } = pendingComment(), saved = receipt(project, change);
    const event = { revision: saved.revision, change, created_at: "2026-01-01T00:00:00Z" };
    const events = mode === "empty" ? [] : mode === "duplicate" ? [event, event]
      : [{ ...event, revision: event.revision + (mode === "gap" || mode === "beyond" ? 1 : 0) }];
    await expect(inspectReviewOutcome(draft, saved, {
      readProject: async () => saved, readHistory: async () => events,
    })).rejects.toThrow(/history/);
    expect(reviewUnknown(draft)).toBe(true);
  });
  test("a changed second source read invalidates the inspection", async () => {
    const { project, draft, change } = pendingComment(), saved = receipt(project, change); let count = 0;
    await expect(inspectReviewOutcome(draft, saved, {
      readProject: async () => ++count === 1 ? saved : { ...saved, revision: saved.revision + 1 },
      readHistory: async () => [{ revision: saved.revision, change, created_at: "2026-01-01T00:00:00Z" }],
    })).rejects.toThrow(/changed during review/);
  });
  test("a foreign identity or a different pending attempt cannot reuse the inspection", async () => {
    const { draft, saved, outcome } = await inspect();
    expect(reviewOutcomeMatches(draft, { ...saved, generation: other }, outcome)).toBe(false);
    expect(() => separateReviewedIntent({ ...draft, pending: { ...draft.pending!, id: other } }, saved, outcome)).toThrow(/complete history/);
  });
  test("a zero-event inspection of unchanged saved state still retains UNKNOWN", async () => {
    const { project, draft } = pendingComment();
    const outcome = await inspectReviewOutcome(draft, project, { readProject: async () => project, readHistory: async () => [] });
    expect(outcome.events).toEqual([]); expect(outcome.matchingValueRevisions).toEqual([]);
    expect(reviewUnknown(draft)).toBe(true); expect(separateReviewedIntent(draft, project, outcome).raw.body).toBe("");
  });
  test("a separate writer retains the original exact UNKNOWN bytes and verifies the new pointer", async () => {
    const { draft, saved, outcome } = await inspect(true), store = new MemoryStorage(), tab = new MemoryStorage(), original = custody();
    writeReviewCopy(store, tab, draft, original);
    const retained = store.getItem(reviewCopyKey(draft.scope, attempt));
    const next = separateReviewedIntent(draft, saved, outcome);
    const writer = retainSeparateReviewIntent(store, tab, draft, next, original, other);
    expect(store.getItem(reviewCopyKey(draft.scope, attempt))).toBe(retained);
    expect(decodeReviewCopy(retained!, draft.scope, attempt).draft.pending?.outcome).toBe("UNKNOWN");
    expect(tab.getItem(reviewPointerKey(draft.scope))).toBe(other);
    expect(decodeReviewCopy(writer.expectedRaw!, draft.scope, other).draft).toEqual(next);
  });
  test("failure after staging the new writer leaves an UNKNOWN checkpoint and preserves every original byte", async () => {
    const { draft, saved, outcome } = await inspect(true), store = new MemoryStorage(), tab = new MemoryStorage(), original = custody();
    writeReviewCopy(store, tab, draft, original);
    const retained = store.getItem(reviewCopyKey(draft.scope, attempt)), originalWrite = store.setItem.bind(store);
    store.setItem = (key, value) => {
      const record = JSON.parse(value);
      if (record.writerId === other && record.draft.pending === null) throw new Error("Second stage quota failure");
      originalWrite(key, value);
    };
    expect(() => retainSeparateReviewIntent(store, tab, draft, separateReviewedIntent(draft, saved, outcome), original, other))
      .toThrow(/Second stage/);
    expect(store.getItem(reviewCopyKey(draft.scope, attempt))).toBe(retained);
    expect(tab.getItem(reviewPointerKey(draft.scope))).toBe(other);
    expect(decodeReviewCopy(store.getItem(reviewCopyKey(draft.scope, other))!, draft.scope, other).draft).toEqual(draft);
  });
  test("verifying identical retained input never rewrites its original timestamp or bytes", () => {
    const draft = commentDraft(), store = new MemoryStorage(), tab = new MemoryStorage(), writer = custody();
    writeReviewCopy(store, tab, draft, writer);
    const original = store.getItem(reviewCopyKey(draft.scope, attempt));
    writeReviewCopy(store, tab, structuredClone(draft), writer);
    expect(store.getItem(reviewCopyKey(draft.scope, attempt))).toBe(original);
  });
  test("a known unsent draft can be explicitly reviewed against a newer source without weakening UNKNOWN", () => {
    const { project, draft } = pendingComment(), later = { ...project, revision: project.revision + 1 };
    const unsent = retainReviewFailure(draft, attempt, "No request invoked", true);
    expect(advanceReviewDraft(unsent, later).pending).toBeNull();
    expect(() => advanceReviewDraft(draft, later)).toThrow(/original review outcome/);
  });
  test("native UTF-8 text budgets are checked before invoking a request without truncating raw input", () => {
    const project = source(), draft = editReviewDraft(commentDraft(project), "body", "\u{1F642}".repeat(2001));
    expect(() => prepareReviewSubmission(draft, project, commentChange(draft, project), attempt)).toThrow(/domain contract/);
    expect(draft.raw.body).toBe("\u{1F642}".repeat(2001)); expect(draft.pending).toBeNull();
  });
  test("a dismissed review cannot become a submitted resolution attempt", () => {
    const project = source(); project.reviews[0].status = "dismissed";
    const draft = editReviewDraft(createReviewDraft(project, { kind: "resolution", id: project.reviews[0].id }), "resolution", "Authored reason");
    expect(() => prepareReviewSubmission(draft, project, { type: "resolve_review", review_id: project.reviews[0].id, resolution: draft.raw.resolution }, attempt))
      .toThrow(/dismissed/);
    expect(draft.raw.resolution).toBe("Authored reason");
  });
});
