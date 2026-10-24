import { describe, expect, it } from "vitest";
import { fixtureBootstrap } from "./fixture";
import {
  createNarrativeDraft, decodeNarrativeCopy, editNarrativeDraft, encodeNarrativeCopy,
  narrativeConflict, narrativeCopyKey, narrativePointerKey, narrativeUnconfirmed, prepareNarrativeSubmission,
  readNarrativeCopies, writeNarrativeCopy,
} from "./narrativeDraft";
import type { NarrativeCustody, NarrativeDraft, NarrativeStorage } from "./narrativeDraft";
import {
  assertNarrativeRecoverySource, emptyNarrativeRecoveryObservation, originalNarrativeRecoveryEntry,
  readNarrativeRecoverySource, recoverNarrativeSource, rememberNarrativeRecoveryEntry,
} from "./narrativeRecovery";

class LocalStorage implements NarrativeStorage {
  values = new Map<string, string>();
  unavailable = new Set<string>();
  get length() { return this.values.size; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  getItem(key: string) {
    if (this.unavailable.has(key)) throw new Error("Read unavailable");
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}
const newWriter = (pointer: string | null = null): NarrativeCustody => ({
  writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer,
});
function setup() {
  const project = structuredClone(fixtureBootstrap.project);
  const current = createNarrativeDraft(project, { kind: "premise" });
  const original = prepareNarrativeSubmission(editNarrativeDraft(current, "Earlier submitted premise"),
    project, crypto.randomUUID()).draft;
  const storage = new LocalStorage(), session = new LocalStorage(), writer = newWriter();
  writeNarrativeCopy(storage, session, original, writer);
  const key = narrativeCopyKey(original.scope, writer.writerId);
  return { project, current, original, storage, session, writer, key,
    pointerKey: narrativePointerKey(original.scope), raw: storage.getItem(key)!,
    observation: emptyNarrativeRecoveryObservation() };
}
function listOriginal(value: ReturnType<typeof setup>) {
  return readNarrativeCopies(value.storage, value.original.scope,
    entry => rememberNarrativeRecoveryEntry(value.observation, entry));
}
function replaceOriginal(value: ReturnType<typeof setup>, draft: NarrativeDraft) {
  value.storage.setItem(value.key, encodeNarrativeCopy({ schema: "motionwright.narrative-recovery/1",
    scope: draft.scope, writerId: value.writer.writerId, updatedAt: 1, draft }));
}

describe("Narrative initial recovery authority", () => {
  it("an unread pointer does not authorize publication of a new copy", () => {
    const value = setup();
    expect(() => assertNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)).toThrow(/Read this tab/);
    expect(value.session.getItem(value.pointerKey)).toBe(value.writer.writerId);
    expect(value.storage.getItem(value.key)).toBe(value.raw);
  });

  it("an observed empty pointer permits a verified first writer", () => {
    const value = setup(); value.session.removeItem(value.pointerKey);
    expect(readNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)).toBeNull();
    const writer = newWriter();
    assertNarrativeRecoverySource(value.storage, value.session, value.current.scope, value.observation);
    writeNarrativeCopy(value.storage, value.session, value.current, writer);
    assertNarrativeRecoverySource(value.storage, value.session, value.current.scope, value.observation, writer);
    expect(value.storage.getItem(value.key)).toBe(value.raw);
  });

  it("a pointer read failure remains unobserved until a successful explicit read", () => {
    const value = setup(); value.session.unavailable.add(value.pointerKey);
    expect(() => readNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)).toThrow(/Read unavailable/);
    expect(value.observation.pointerObserved).toBe(false);
    value.session.unavailable.clear();
    expect(readNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)?.pending).toEqual(value.original.pending);
    expect(value.session.getItem(value.pointerKey)).toBe(value.writer.writerId);
  });

  for (const later of ["Later author text", "", "original base"] as const) {
    it(`a recovered UNKNOWN retains the subsequent decision: ${later}`, () => {
      const value = setup(); value.storage.unavailable.add(value.key);
      expect(() => readNarrativeRecoverySource(value.storage, value.session,
        value.current.scope, value.observation)).toThrow(/Read unavailable/);
      const raw = later === "original base" ? value.current.base : later;
      const edited = editNarrativeDraft(editNarrativeDraft(value.current, "Intermediate typing"), raw);
      value.storage.unavailable.clear(); listOriginal(value);
      const recovered = readNarrativeRecoverySource(value.storage, value.session,
        value.current.scope, value.observation)!;
      const next = recoverNarrativeSource(recovered, edited, value.project);
      expect(next.raw).toBe(raw);
      expect(next.intent).toBe(true);
      expect(next.pending).toEqual(value.original.pending);
      expect(next.sequence).toBeGreaterThan(next.pending!.sequence);
      expect(() => prepareNarrativeSubmission(next, value.project, crypto.randomUUID())).toThrow(/earlier Narrative outcome/);
      expect(value.storage.getItem(value.key)).toBe(value.raw);
      expect(value.session.getItem(value.pointerKey)).toBe(value.writer.writerId);
    });
  }

  it("an untouched new buffer recovers the original draft without substituting its saved base", () => {
    const value = setup();
    const recovered = readNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)!;
    expect(recoverNarrativeSource(recovered, value.current, value.project)).toEqual(value.original);
  });

  for (const base of ["Earlier saved premise", ""]) {
    it(`a Project update preserves an explicit return to ${base ? "the old base" : "a blank base"} and the recovered UNKNOWN`, () => {
      const value = setup(); value.project.narrative.premise = base;
      const current = createNarrativeDraft(value.project, { kind: "premise" });
      const edited = editNarrativeDraft(editNarrativeDraft(current, "Intermediate author text"), base);
      const updated = structuredClone(value.project);
      updated.revision++; updated.narrative.premise = "A newer saved premise";
      const next = recoverNarrativeSource(value.original, edited, updated);
      expect(next.raw).toBe(base);
      expect(next.intent).toBe(true);
      expect(next.pending).toEqual(value.original.pending);
      expect(next.sequence).toBeGreaterThan(next.pending!.sequence);
      expect(() => prepareNarrativeSubmission(next, updated, crypto.randomUUID())).toThrow(/earlier Narrative outcome/);
      expect(value.storage.getItem(value.key)).toBe(value.raw);
    });

    it(`an empty recovered pointer preserves an explicit return to ${base ? "the old base" : "a blank base"} as an overlapping intent`, () => {
      const value = setup(); value.project.narrative.premise = base;
      const current = createNarrativeDraft(value.project, { kind: "premise" });
      const edited = editNarrativeDraft(editNarrativeDraft(current, "Intermediate author text"), base);
      const updated = structuredClone(value.project);
      updated.revision++; updated.narrative.premise = "A newer saved premise";
      const next = recoverNarrativeSource(null, edited, updated);
      expect(next.raw).toBe(base);
      expect(next.base).toBe(base);
      expect(next.baseRevision).toBe(current.baseRevision);
      expect(next.intent).toBe(true);
      expect(next.pending).toBeNull();
      expect(narrativeConflict(next, updated)).toBe(true);
      expect(() => prepareNarrativeSubmission(next, updated, crypto.randomUUID())).toThrow(/overlapping Narrative value/);
    });
  }

  it("an empty recovered pointer refreshes an untouched buffer from the current Project", () => {
    const value = setup(), updated = structuredClone(value.project);
    updated.revision++; updated.narrative.premise = "A newer saved premise";
    const next = recoverNarrativeSource(null, value.current, updated);
    expect(next.raw).toBe(updated.narrative.premise);
    expect(next.base).toBe(updated.narrative.premise);
    expect(next.baseRevision).toBe(updated.revision);
    expect(next.sequence).toBe(0);
    expect(next.intent).toBe(false);
    expect(next.pending).toBeNull();
  });

  it("a later enumeration error still retains the first listed original bytes", () => {
    const value = setup(); value.storage.unavailable.add(value.key);
    expect(() => readNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)).toThrow();
    value.storage.unavailable.clear();
    const otherKey = narrativeCopyKey(value.current.scope, crypto.randomUUID());
    value.storage.setItem(otherKey, "unreadable later entry"); value.storage.unavailable.add(otherKey);
    expect(() => listOriginal(value)).toThrow(/Read unavailable/);
    expect(originalNarrativeRecoveryEntry(value.current.scope, value.observation)?.raw).toBe(value.raw);
    replaceOriginal(value, value.current);
    expect(() => readNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)).toThrow(/bytes changed/);
    expect(originalNarrativeRecoveryEntry(value.current.scope, value.observation)?.value?.draft.pending)
      .toEqual(value.original.pending);
  });

  it("a valid same-writer replacement cannot replace the UNKNOWN observed by the recovery list", () => {
    const value = setup(); value.storage.unavailable.add(value.key);
    expect(() => readNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)).toThrow();
    value.storage.unavailable.clear(); listOriginal(value); replaceOriginal(value, value.current);
    const replacement = value.storage.getItem(value.key);
    expect(() => readNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)).toThrow(/bytes changed/);
    expect(originalNarrativeRecoveryEntry(value.current.scope, value.observation)?.raw).toBe(value.raw);
    expect(value.storage.getItem(value.key)).toBe(replacement);
  });

  it("a foreign pointer cannot be adopted during the recovery retry", () => {
    const value = setup();
    readNarrativeRecoverySource(value.storage, value.session, value.current.scope, value.observation);
    const other = crypto.randomUUID(); value.session.setItem(value.pointerKey, other);
    expect(() => readNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)).toThrow(/original recovery pointer/);
    expect(value.session.getItem(value.pointerKey)).toBe(other);
    expect(value.storage.getItem(value.key)).toBe(value.raw);
  });

  it("corrupt source bytes remain downloadable and never become an empty valid draft", () => {
    const value = setup(); value.storage.setItem(value.key, "{broken original\n");
    expect(() => readNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)).toThrow();
    const retained = originalNarrativeRecoveryEntry(value.current.scope, value.observation);
    expect(retained?.raw).toBe("{broken original\n"); expect(retained?.value).toBeNull();
    expect(() => assertNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)).toThrow();
  });

  it("a missing observed copy is not silently replaced by later bytes under the same key", () => {
    const value = setup(); value.storage.removeItem(value.key);
    expect(() => readNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)).toThrow(/missing recovery copy/);
    value.storage.setItem(value.key, value.raw);
    expect(() => readNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)).toThrow(/bytes changed/);
  });

  for (const identity of ["id", "generation", "active_branch"] as const) {
    it(`a recovered source cannot cross project ${identity}`, () => {
      const value = setup(), other = { ...value.project, [identity]: value.project[identity] + "-other" };
      expect(() => recoverNarrativeSource(value.original,
        createNarrativeDraft(other, { kind: "premise" }), other)).toThrow(/another project/);
    });
  }

  it("a recovered premise cannot replace a scene objective", () => {
    const value = setup();
    const other = createNarrativeDraft(value.project, { kind: "objective", sceneId: value.project.scenes[0].id });
    expect(() => recoverNarrativeSource(value.original, other, value.project)).toThrow(/another project/);
  });

  it("an existing live comparison cannot be consumed by a recovery read", () => {
    const value = setup();
    expect(() => recoverNarrativeSource(value.original, value.original, value.project)).toThrow(/active Narrative comparison/);
  });

  it("a restored NOT_SENT comparison is kept as UNKNOWN", () => {
    const value = setup();
    replaceOriginal(value, { ...value.original, pending: { ...value.original.pending!, outcome: "NOT_SENT" } });
    const recovered = readNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation)!;
    expect(narrativeUnconfirmed(recoverNarrativeSource(recovered, value.current, value.project))).toBe(true);
  });

  it("its own interrupted publication is admissible only with the original pending comparison intact", () => {
    const value = setup();
    readNarrativeRecoverySource(value.storage, value.session, value.current.scope, value.observation);
    const next = recoverNarrativeSource(value.original, editNarrativeDraft(value.current, "Later text"), value.project);
    const writer = newWriter(value.writer.writerId);
    writeNarrativeCopy(value.storage, value.session, next, writer);
    assertNarrativeRecoverySource(value.storage, value.session, next.scope, value.observation, writer);
    const owned = decodeNarrativeCopy(writer.expectedRaw!, next.scope, writer.writerId);
    const invalidRaw = encodeNarrativeCopy({ ...owned, draft: { ...owned.draft, pending: null } });
    value.storage.setItem(narrativeCopyKey(next.scope, writer.writerId), invalidRaw);
    expect(() => assertNarrativeRecoverySource(value.storage, value.session, next.scope,
      value.observation, { ...writer, expectedRaw: invalidRaw })).toThrow(/did not retain/);
    expect(value.storage.getItem(value.key)).toBe(value.raw);
  });

  it("a changed original is rejected even after this runtime published its separate copy", () => {
    const value = setup();
    readNarrativeRecoverySource(value.storage, value.session, value.current.scope, value.observation);
    const writer = newWriter(value.writer.writerId);
    writeNarrativeCopy(value.storage, value.session, value.original, writer);
    replaceOriginal(value, value.current);
    expect(() => assertNarrativeRecoverySource(value.storage, value.session,
      value.current.scope, value.observation, writer)).toThrow(/bytes changed/);
    expect(decodeNarrativeCopy(writer.expectedRaw!, value.current.scope, writer.writerId).draft.pending)
      .toEqual(value.original.pending);
  });

  it("sequence overflow preserves both drafts instead of rounding a later author decision", () => {
    const value = setup(), later = { ...editNarrativeDraft(value.current, "Keep this text"), sequence: Number.MAX_SAFE_INTEGER };
    const before = structuredClone([value.original, later]);
    expect(() => recoverNarrativeSource(value.original, later, value.project)).toThrow(/represented safely/);
    expect([value.original, later]).toEqual(before);
  });
});
