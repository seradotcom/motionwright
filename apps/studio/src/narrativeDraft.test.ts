import { describe, expect, it } from "vitest";
import { fixtureBootstrap } from "./fixture";
import type { Project, ProjectEvent } from "./types";
import {
  acknowledgeNarrativeDraft, createNarrativeDraft, decodeNarrativeCopy, decodeNarrativeFile, editNarrativeDraft,
  encodeNarrativeFile, importNarrativeFileDraft, MAX_NARRATIVE_FILE_BYTES,
  inspectNarrativeOutcome, narrativeConflict, narrativeCopyKey, narrativeOutcomeMatches,
  narrativePointerKey, narrativeScope, prepareNarrativeSubmission, readNarrativeCopies,
  reconcileNarrativeDraft, releaseNarrativeOutcome, resolveNarrativeDraft,
  retainNarrativeFailure, retainReviewedNarrativeIntent, writeNarrativeCopy,
} from "./narrativeDraft";
import type { NarrativeCustody, NarrativeDraft, NarrativeStorage, NarrativeTarget } from "./narrativeDraft";

const project = (): Project => structuredClone(fixtureBootstrap.project);
const premise: NarrativeTarget = { kind: "premise" };
const freshCustody = (pointer: string | null = null): NarrativeCustody => ({
  writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer,
});
function sent(source: Project, target: NarrativeTarget = premise, raw = "Submitted Narrative intent") {
  return prepareNarrativeSubmission(editNarrativeDraft(createNarrativeDraft(source, target), raw), source, crypto.randomUUID());
}
function receipt(source: Project, draft: NarrativeDraft): Project {
  const next = structuredClone(source); next.revision++;
  if (draft.target.kind === "premise") next.narrative.premise = draft.pending!.value;
  else next.scenes.find(scene => scene.id === (draft.target as Extract<NarrativeTarget, { kind: "objective" }>).sceneId)!.objective = draft.pending!.value;
  return next;
}
class MemoryStorage implements NarrativeStorage {
  values = new Map<string, string>();
  ignoreWrites = false;
  failAfter: number | null = null;
  writes = 0;
  get length() { return this.values.size; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) {
    if (this.failAfter !== null && this.writes++ >= this.failAfter) throw new Error("Storage outage");
    if (!this.ignoreWrites) this.values.set(key, value);
  }
  removeItem(key: string) { this.values.delete(key); }
}
function journal(saved: Project, draft: NarrativeDraft): ProjectEvent[] {
  return [{ revision: saved.revision, created_at: "2026-10-21T00:00:00Z",
    change: draft.target.kind === "premise" ? { type: "set_narrative_premise", premise: draft.pending!.value }
      : { type: "update_scene_objective", scene_id: draft.target.sceneId, objective: draft.pending!.value } }];
}

describe("Narrative ownership and original replies", () => {
  it("separates equal-valued projects, generations and branches while sharing the project premise across scenes", () => {
    const source = project();
    const original = narrativeScope(source, premise);
    for (const field of ["id", "generation", "active_branch"] as const) {
      const other = { ...source, [field]: source[field] + "-other" };
      expect(other.narrative.premise).toBe(source.narrative.premise);
      expect(narrativeScope(other, premise)).not.toBe(original);
    }
    const first = { kind: "objective" as const, sceneId: source.scenes[0].id };
    const second = { kind: "objective" as const, sceneId: source.scenes[1].id };
    expect(narrativeScope(source, first)).not.toBe(narrativeScope(source, second));
    expect(original).not.toBe(narrativeScope(source, first));
    expect(narrativeScope({ ...source, scenes: [...source.scenes].reverse() }, premise)).toBe(original);
  });

  it("preserves raw whitespace and changes the base only for a disjoint canonical update", () => {
    const source = project();
    const draft = editNarrativeDraft(createNarrativeDraft(source, premise), "  Author line\n\nLater line  \n");
    const updated = { ...source, revision: source.revision + 1, title: "A disjoint title" };
    const reconciled = reconcileNarrativeDraft(draft, updated);
    expect(reconciled.raw).toBe(draft.raw);
    expect(reconciled.baseRevision).toBe(updated.revision);
    expect(narrativeConflict(reconciled, updated)).toBe(false);
    expect(source.title).not.toBe(updated.title);
  });

  it("a matching canonical observation and recovered copy never acknowledge UNKNOWN", () => {
    const source = project(), prepared = sent(source), saved = receipt(source, prepared.draft);
    expect(reconcileNarrativeDraft(prepared.draft, saved)).toBe(prepared.draft);
    const local = new MemoryStorage(), session = new MemoryStorage(), custody = freshCustody();
    writeNarrativeCopy(local, session, prepared.draft, custody);
    const restored = decodeNarrativeCopy(custody.expectedRaw!, prepared.draft.scope, custody.writerId).draft;
    expect(reconcileNarrativeDraft(restored, saved).pending?.outcome).toBe("UNKNOWN");
    expect(() => prepareNarrativeSubmission(restored, saved, crypto.randomUUID())).toThrow(/earlier Narrative outcome/);
  });

  for (const later of ["Later words after submit", "original base", "submitted again"] as const) {
    it(`the original receipt retains a later author decision: ${later}`, () => {
      const source = project(), prepared = sent(source), saved = receipt(source, prepared.draft);
      let draft = editNarrativeDraft(prepared.draft, "An intermediate later edit");
      const raw = later === "original base" ? source.narrative.premise
        : later === "submitted again" ? prepared.draft.raw : later;
      draft = editNarrativeDraft(draft, raw);
      const acknowledged = acknowledgeNarrativeDraft(draft, prepared.draft.pending!.id, saved);
      expect(acknowledged.pending).toBeNull();
      expect(acknowledged.raw).toBe(raw);
      expect(acknowledged.intent).toBe(true);
      expect(acknowledged.base).toBe(prepared.draft.pending!.value);
      expect(acknowledged.baseRevision).toBe(saved.revision);
      expect(narrativeConflict(acknowledged, saved)).toBe(false);
    });
  }

  for (const mismatch of ["id", "generation", "active_branch", "revision", "value", "missing scene"] as const) {
    it(`rejects an original reply with foreign ${mismatch}`, () => {
      const source = project(), target: NarrativeTarget = { kind: "objective", sceneId: source.scenes[0].id };
      const prepared = sent(source, target), saved = receipt(source, prepared.draft);
      if (mismatch === "revision") saved.revision++;
      else if (mismatch === "value") saved.scenes[0].objective = "Another author's words";
      else if (mismatch === "missing scene") saved.scenes = saved.scenes.slice(1);
      else saved[mismatch] += "-foreign";
      expect(() => acknowledgeNarrativeDraft(prepared.draft, prepared.draft.pending!.id, saved)).toThrow();
      expect(prepared.draft.pending?.outcome).toBe("UNKNOWN");
    });
  }

  it("an obsolete local attempt ID cannot consume a newer pending operation", () => {
    const source = project(), prepared = sent(source);
    expect(acknowledgeNarrativeDraft(prepared.draft, crypto.randomUUID(), receipt(source, prepared.draft))).toBe(prepared.draft);
  });

  for (const choice of ["saved", "draft"] as const) {
    it(`resolves an overlapping objective by explicit ${choice} choice without changing another field`, () => {
      const source = project(), target: NarrativeTarget = { kind: "objective", sceneId: source.scenes[0].id };
      const draft = editNarrativeDraft(createNarrativeDraft(source, target), "My unsent objective");
      const updated = structuredClone(source); updated.revision++; updated.scenes[0].objective = "Peer objective";
      expect(narrativeConflict(draft, updated)).toBe(true);
      expect(reconcileNarrativeDraft(draft, updated).raw).toBe("My unsent objective");
      const resolved = resolveNarrativeDraft(draft, updated, choice);
      expect(resolved.raw).toBe(choice === "saved" ? "Peer objective" : "My unsent objective");
      expect(resolved.base).toBe("Peer objective");
      expect(updated.narrative).toEqual(source.narrative);
      expect(narrativeConflict(resolved, updated)).toBe(false);
    });
  }
});

describe("Narrative durable custody", () => {
  for (const interleaving of ["pointer setter", "pointer readback"] as const) {
    it(`refuses confirmation when the UNKNOWN copy is replaced during ${interleaving}`, () => {
      const source = project(), before = structuredClone(source), prepared = sent(source);
      const local = new MemoryStorage(), pointers = new MemoryStorage(), custody = freshCustody();
      const key = narrativeCopyKey(prepared.draft.scope, custody.writerId);
      const pointerKey = narrativePointerKey(prepared.draft.scope);
      const foreignRaw = "Another writer's bytes replaced the UNKNOWN copy";
      let published = false, replaced = false;
      const replaceDuringPublication = () => {
        if (replaced) return;
        replaced = true; local.values.set(key, foreignRaw);
      };
      const session = {
        getItem(requested: string) {
          const result = pointers.getItem(requested);
          if (interleaving === "pointer readback" && published && requested === pointerKey)
            replaceDuringPublication();
          return result;
        },
        setItem(requested: string, value: string) {
          pointers.setItem(requested, value);
          if (requested === pointerKey) {
            published = true;
            if (interleaving === "pointer setter") replaceDuringPublication();
          }
        },
      };
      expect(() => writeNarrativeCopy(local, session, prepared.draft, custody))
        .toThrow(/copy changed while its writer pointer was published/);
      expect(replaced).toBe(true);
      expect(local.getItem(key)).toBe(foreignRaw);
      expect(pointers.getItem(pointerKey)).toBe(custody.writerId);
      // The write's bytes remain its expectation, not the replacement bytes.
      // A later retry must not adopt or overwrite another author's record.
      const expected = custody.expectedRaw!;
      expect(decodeNarrativeCopy(expected, prepared.draft.scope, custody.writerId).draft).toEqual(prepared.draft);
      expect(custody.expectedPointer).toBeNull();
      expect(() => writeNarrativeCopy(local, session, prepared.draft, custody)).toThrow(/changed in another window/);
      expect(custody.expectedRaw).toBe(expected);
      expect(local.getItem(key)).toBe(foreignRaw);
      expect(source).toEqual(before);
    });
  }

  for (const interleaving of ["writer bytes", "writer pointer"] as const) {
    it(`does not overwrite custody changed at ${interleaving} during the recovery budget scan`, () => {
      const source = project(), before = structuredClone(source), prepared = sent(source);
      const local = new MemoryStorage(), session = new MemoryStorage(), custody = freshCustody();
      writeNarrativeCopy(local, session, prepared.draft, custody);
      const ownKey = narrativeCopyKey(prepared.draft.scope, custody.writerId);
      const pointerKey = narrativePointerKey(prepared.draft.scope);
      const original = custody.expectedRaw!, otherWriter = crypto.randomUUID();
      const unrelatedKey = narrativeCopyKey(prepared.draft.scope, otherWriter);
      const unrelatedRaw = "Unrelated original bytes remain unchanged";
      const foreignRaw = "Another writer took custody during the budget scan";
      local.setItem(unrelatedKey, unrelatedRaw);
      const originalKey = local.key.bind(local);
      let changed = false;
      local.key = (index: number) => {
        const key = originalKey(index);
        if (!changed && key === unrelatedKey) {
          changed = true;
          if (interleaving === "writer bytes") local.values.set(ownKey, foreignRaw);
          else session.values.set(pointerKey, otherWriter);
        }
        return key;
      };
      expect(() => writeNarrativeCopy(local, session, prepared.draft, custody)).toThrow(
        interleaving === "writer bytes" ? /changed in another window/ : /changed this tab's recovery pointer/);
      expect(changed).toBe(true);
      expect(local.getItem(ownKey)).toBe(interleaving === "writer bytes" ? foreignRaw : original);
      expect(session.getItem(pointerKey)).toBe(interleaving === "writer pointer" ? otherWriter : custody.writerId);
      expect(local.getItem(unrelatedKey)).toBe(unrelatedRaw);
      expect(custody.expectedRaw).toBe(original);
      expect(custody.expectedPointer).toBe(custody.writerId);
      expect(source).toEqual(before);
    });
  }

  it("verifies data and pointer writes; an unconfirmed pointer never erases the readable copy", () => {
    const prepared = sent(project()), local = new MemoryStorage(), session = new MemoryStorage(), custody = freshCustody();
    session.ignoreWrites = true;
    expect(() => writeNarrativeCopy(local, session, prepared.draft, custody)).toThrow(/pointer was not confirmed/);
    expect(local.getItem(narrativeCopyKey(prepared.draft.scope, custody.writerId))).toBe(custody.expectedRaw);
    expect(decodeNarrativeCopy(custody.expectedRaw!, prepared.draft.scope, custody.writerId).draft.pending?.outcome).toBe("UNKNOWN");
    expect(session.getItem(narrativePointerKey(prepared.draft.scope))).toBeNull();
    session.ignoreWrites = false;
    writeNarrativeCopy(local, session, prepared.draft, custody);
    expect(session.getItem(narrativePointerKey(prepared.draft.scope))).toBe(custody.writerId);
  });

  it("a silently lost data write leaves its previous bytes and pointer intact", () => {
    const source = project(), draft = editNarrativeDraft(createNarrativeDraft(source, premise), "First retained text");
    const local = new MemoryStorage(), session = new MemoryStorage(), custody = freshCustody();
    writeNarrativeCopy(local, session, draft, custody);
    const original = custody.expectedRaw;
    local.ignoreWrites = true;
    expect(() => writeNarrativeCopy(local, session, editNarrativeDraft(draft, "Later text"), custody)).toThrow(/write was not confirmed/);
    expect(custody.expectedRaw).toBe(original);
    expect(local.getItem(narrativeCopyKey(draft.scope, custody.writerId))).toBe(original);
    expect(session.getItem(narrativePointerKey(draft.scope))).toBe(custody.writerId);
  });

  it("refuses a changed writer copy and preserves corrupt or unrelated records during enumeration", () => {
    const prepared = sent(project()), local = new MemoryStorage(), session = new MemoryStorage(), custody = freshCustody();
    writeNarrativeCopy(local, session, prepared.draft, custody);
    const key = narrativeCopyKey(prepared.draft.scope, custody.writerId);
    local.setItem(key, "Original corrupt bytes from a different writer");
    expect(() => writeNarrativeCopy(local, session, prepared.draft, custody)).toThrow(/changed in another window/);
    const list = readNarrativeCopies(local, prepared.draft.scope);
    expect(list.entries).toHaveLength(1);
    expect(list.entries[0].value).toBeNull();
    expect(local.getItem(key)).toBe("Original corrupt bytes from a different writer");
  });

  it("recovery cannot upgrade a NOT_SENT label or injected commands into replay authority", () => {
    const prepared = sent(project()), local = new MemoryStorage(), session = new MemoryStorage(), custody = freshCustody();
    const notSent = retainNarrativeFailure(prepared.draft, prepared.draft.pending!.id, "Pointer failure before dispatch", true);
    writeNarrativeCopy(local, session, notSent, custody);
    const value = JSON.parse(custody.expectedRaw!);
    value.draft.pending.change = { type: "set_narrative_premise", premise: "Injected command" };
    value.draft.pending.effect_grant = "not-an-authority";
    const restored = decodeNarrativeCopy(JSON.stringify(value), prepared.draft.scope, custody.writerId);
    expect(restored.draft.pending?.outcome).toBe("UNKNOWN");
    expect(restored.draft.pending).not.toHaveProperty("change");
    expect(restored.draft.pending).not.toHaveProperty("effect_grant");
  });
});

describe("Narrative explicit outcome review", () => {
  it("reads complete history without granting authorship, then retains the original UNKNOWN during a separate intent", async () => {
    const source = project(), prepared = sent(source), saved = receipt(source, prepared.draft);
    const later = editNarrativeDraft(prepared.draft, "New intent after the unknown outcome");
    let reads = 0;
    const review = await inspectNarrativeOutcome(later, saved, {
      readProject: async () => { reads++; return structuredClone(saved); },
      readHistory: async () => journal(saved, prepared.draft),
    });
    expect(reads).toBe(2);
    expect(review.matchingValueRevisions).toEqual([saved.revision]);
    expect(later.pending?.outcome).toBe("UNKNOWN");
    expect(narrativeOutcomeMatches(later, saved, review)).toBe(true);
    const next = releaseNarrativeOutcome(later, saved, review);
    expect(next.raw).toBe(later.raw);
    expect(next.pending).toBeNull();
    const local = new MemoryStorage(), session = new MemoryStorage(), original = freshCustody();
    writeNarrativeCopy(local, session, later, original);
    const originalRaw = original.expectedRaw;
    const fork = retainReviewedNarrativeIntent(local, session, later, next, original, crypto.randomUUID());
    expect(local.getItem(narrativeCopyKey(later.scope, original.writerId))).toBe(originalRaw);
    expect(decodeNarrativeCopy(fork.expectedRaw!, later.scope, fork.writerId).draft.pending).toBeNull();
    expect(session.getItem(narrativePointerKey(later.scope))).toBe(fork.writerId);
  });

  for (const fault of ["missing history", "wrong revision", "stale view", "changed during read"] as const) {
    it(`refuses outcome review with ${fault}`, async () => {
      const source = project(), prepared = sent(source), saved = receipt(source, prepared.draft);
      let reads = 0;
      await expect(inspectNarrativeOutcome(prepared.draft, fault === "stale view" ? source : saved, {
        readProject: async () => ({ ...saved, revision: fault === "changed during read" && reads++ > 0 ? saved.revision + 1 : saved.revision }),
        readHistory: async () => fault === "missing history" ? [] : journal(saved, prepared.draft).map(event => ({
          ...event, revision: fault === "wrong revision" ? event.revision + 1 : event.revision,
        })),
      })).rejects.toThrow();
      expect(prepared.draft.pending?.outcome).toBe("UNKNOWN");
    });
  }

  for (const fault of ["pointer", "released data"] as const) {
    it(`a failed review fork at ${fault} retains the old UNKNOWN and never releases the selected copy`, async () => {
      const source = project(), prepared = sent(source), saved = receipt(source, prepared.draft);
      const review = await inspectNarrativeOutcome(prepared.draft, saved, {
        readProject: async () => saved, readHistory: async () => journal(saved, prepared.draft),
      });
      const next = releaseNarrativeOutcome(prepared.draft, saved, review);
      const local = new MemoryStorage(), session = new MemoryStorage(), original = freshCustody();
      writeNarrativeCopy(local, session, prepared.draft, original);
      const originalRaw = original.expectedRaw, forkId = crypto.randomUUID();
      if (fault === "pointer") session.ignoreWrites = true;
      else { local.failAfter = 1; local.writes = 0; }
      expect(() => retainReviewedNarrativeIntent(local, session, prepared.draft, next, original, forkId)).toThrow();
      expect(local.getItem(narrativeCopyKey(prepared.draft.scope, original.writerId))).toBe(originalRaw);
      const selected = session.getItem(narrativePointerKey(prepared.draft.scope))!;
      const selectedRaw = local.getItem(narrativeCopyKey(prepared.draft.scope, selected))!;
      expect(decodeNarrativeCopy(selectedRaw, prepared.draft.scope, selected).draft.pending?.outcome).toBe("UNKNOWN");
    });
  }
});

describe("Narrative portable author files", () => {
  it("exports exact author text and comparison state without serializing authority or a NOT_SENT upgrade", () => {
    const source = project(), prepared = sent(source, premise, "  Submitted author text\n");
    let draft = editNarrativeDraft(prepared.draft, "  Later text\n\nPreserve spaces  \n");
    draft = retainNarrativeFailure(draft, prepared.draft.pending!.id, "Stopped before dispatch", true);
    const injected = { ...draft, effect_grant: "not-authority", command: { type: "set_narrative_premise" },
      pending: { ...draft.pending!, receipt: source, request_id: "not-authority" } };
    const raw = encodeNarrativeFile(injected, 1234), file = decodeNarrativeFile(raw, draft.scope);
    expect(file.exportedAt).toBe(1234);
    expect(file.draft.raw).toBe(draft.raw); expect(file.draft.base).toBe(draft.base);
    expect(file.draft.baseRevision).toBe(draft.baseRevision); expect(file.draft.sequence).toBe(draft.sequence);
    expect(file.draft.pending).toEqual({ ...draft.pending, outcome: "UNKNOWN" });
    expect(raw).not.toContain("not-authority"); expect(raw).not.toContain('"receipt"'); expect(raw).not.toContain('"command"');
  });

  for (const fault of ["schema", "project", "generation", "branch", "target", "draft fields", "envelope fields", "pending authority", "pending status"] as const) {
    it(`rejects a portable file with foreign or unsupported ${fault}`, () => {
      const source = project(), prepared = sent(source), value = JSON.parse(encodeNarrativeFile(prepared.draft));
      if (fault === "schema") value.schema = "motionwright.narrative-file/2";
      else if (fault === "project" || fault === "generation" || fault === "branch") {
        const parts = JSON.parse(value.scope);
        parts[{ project: 0, generation: 1, branch: 2 }[fault]] += "-foreign";
        value.scope = JSON.stringify(parts); value.draft.scope = value.scope;
      } else if (fault === "target") value.draft.target.sceneId = source.scenes[0].id;
      else if (fault === "draft fields") value.draft.receipt = source;
      else if (fault === "envelope fields") value.effect_grant = "untrusted";
      else if (fault === "pending authority") value.draft.pending.effect_grant = "untrusted";
      else value.draft.pending.outcome = "ACKNOWLEDGED";
      expect(() => decodeNarrativeFile(JSON.stringify(value), prepared.draft.scope)).toThrow();
      expect(prepared.draft.pending?.outcome).toBe("UNKNOWN");
    });
  }

  it("rejects a scene objective file whose target does not match the bound scene scope", () => {
    const source = project(), target: NarrativeTarget = { kind: "objective", sceneId: source.scenes[0].id };
    const draft = editNarrativeDraft(createNarrativeDraft(source, target), "Selected scene author text");
    const value = JSON.parse(encodeNarrativeFile(draft)); value.draft.target.sceneId = source.scenes[1].id;
    expect(() => decodeNarrativeFile(JSON.stringify(value), draft.scope)).toThrow(/target/);
  });

  it("bounds import by UTF-8 bytes while still allowing export of the author's oversized original text", () => {
    const source = project(), draft = editNarrativeDraft(createNarrativeDraft(source, premise), "é".repeat(MAX_NARRATIVE_FILE_BYTES / 2));
    const raw = encodeNarrativeFile(draft);
    expect(raw.length).toBeLessThan(MAX_NARRATIVE_FILE_BYTES);
    expect(new TextEncoder().encode(raw).length).toBeGreaterThan(MAX_NARRATIVE_FILE_BYTES);
    expect(JSON.parse(raw).draft.raw).toBe(draft.raw);
    expect(() => decodeNarrativeFile(raw, draft.scope)).toThrow(/8 MiB/);
  });

  it("explicit import of an older clean checkpoint preserves its text for conflict review", () => {
    const source = project(), old = createNarrativeDraft(source, premise);
    const file = decodeNarrativeFile(encodeNarrativeFile(old), old.scope);
    const current = structuredClone(source); current.revision++; current.narrative.premise = "A later saved premise";
    const imported = importNarrativeFileDraft(file, current);
    expect(imported.raw).toBe(old.raw); expect(imported.base).toBe(old.base);
    expect(imported.sequence).toBe(old.sequence); expect(imported.intent).toBe(true);
    expect(narrativeConflict(imported, current)).toBe(true);
    expect(current.narrative.premise).toBe("A later saved premise");
  });

  it("imported UNKNOWN remains unresolved even when the saved project contains matching values", () => {
    const source = project(), prepared = sent(source), saved = receipt(source, prepared.draft);
    const file = decodeNarrativeFile(encodeNarrativeFile(prepared.draft), prepared.draft.scope);
    const imported = importNarrativeFileDraft(file, saved);
    expect(imported.pending).toEqual(prepared.draft.pending);
    expect(imported.raw).toBe(prepared.draft.raw);
    expect(() => prepareNarrativeSubmission(imported, saved, crypto.randomUUID())).toThrow(/earlier Narrative outcome/);
  });
});
