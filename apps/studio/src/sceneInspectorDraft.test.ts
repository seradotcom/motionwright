import { describe, expect, test } from "vitest";
import { fixtureProject } from "./fixture";
import {
  acknowledgeInspectorDraft, advanceInspectorDraft, createInspectorDraft, decodeInspectorCopy, editInspectorDraft,
  encodeInspectorCopy, inspectInspectorOutcome, inspectorChange, inspectorCopyKey, inspectorDuration,
  inspectorHasInput, inspectorOutcomeMatches, inspectorPointerKey, inspectorScope, inspectorUnknown,
  MAX_INSPECTOR_COPIES, prepareInspectorSubmission, reconcileInspectorDraft, retainInspectorFailure,
  retainSeparateInspectorIntent, separateInspectedIntent, writeInspectorCopy,
} from "./sceneInspectorDraft";
import type { InspectorChange, InspectorCopy, InspectorCustody, InspectorDraft, InspectorField } from "./sceneInspectorDraft";
import type { Project, ProjectEvent } from "./types";

const attempt = "20000000-0000-4000-8000-000000000001", other = "20000000-0000-4000-8000-000000000002";
const source = () => { const project = structuredClone(fixtureProject); project.locks = []; return project; };
const rawDraft = (project = source(), field: InspectorField = "objective", value = "  Authored objective  ") =>
  editInspectorDraft(createInspectorDraft(project, project.scenes[0].id), field, value);
function sent(field: InspectorField = "objective", value = "  Authored objective  ") {
  const project = source(), raw = rawDraft(project, field, value), change = inspectorChange(raw, project, field);
  return { project, change, draft: prepareInspectorSubmission(raw, project, change, attempt) };
}
function receipt(project: Project, change: InspectorChange): Project {
  const saved = structuredClone(project); saved.revision++;
  const scene = saved.scenes.find(item => item.id === change.scene_id)!;
  if (change.type === "update_scene_objective") scene.objective = change.objective;
  else scene.duration = structuredClone(change.duration);
  return saved;
}
class MemoryStorage {
  data = new Map<string, string>();
  get length() { return this.data.size; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
}
const custody = (writerId = attempt, expectedPointer: string | null = null): InspectorCustody =>
  ({ writerId, expectedRaw: null, expectedPointer });
const copy = (draft: InspectorDraft): InspectorCopy =>
  ({ schema: "motionwright.scene-inspector-recovery/1", scope: draft.scope, writerId: attempt, updatedAt: 1, draft });
const journal = (project: Project, change: InspectorChange): ProjectEvent[] =>
  [{ revision: project.revision + 1, change, created_at: "2026-01-01T00:00:00Z" } as ProjectEvent];

describe("Inspector author input and source scope", () => {
  for (const field of ["id", "generation", "active_branch"] as const) test("isolates equal scene IDs by " + field, () => {
    const project = source(), draft = rawDraft(project);
    expect(inspectorScope(project, project.scenes[0].id)).not.toBe(inspectorScope({ ...project, [field]: other }, project.scenes[0].id));
    expect(() => reconcileInspectorDraft(draft, { ...project, [field]: other })).toThrow(/another project/);
  });
  test("separates scene targets", () => {
    const project = source();
    expect(inspectorScope(project, project.scenes[0].id)).not.toBe(inspectorScope(project, project.scenes[1].id));
  });
  test("roundtrips raw whitespace, fraction and incomplete text", () => {
    const draft = editInspectorDraft(rawDraft(), "duration", "  1001/  ");
    expect(decodeInspectorCopy(encodeInspectorCopy(copy(draft)), draft.scope, attempt).draft).toEqual(draft);
    expect(draft.raw).toEqual({ objective: "  Authored objective  ", duration: "  1001/  " });
  });
  test("clean input follows saved source but edited text never rebases from props", () => {
    const project = source(), clean = createInspectorDraft(project, project.scenes[0].id);
    const saved = receipt(project, { type: "update_scene_objective", scene_id: project.scenes[0].id, objective: "Saved update" });
    expect(reconcileInspectorDraft(clean, saved).raw.objective).toBe("Saved update");
    const dirty = rawDraft(project); expect(reconcileInspectorDraft(dirty, saved)).toEqual(dirty);
  });
  test.each([["1001/30000", "1001", "30000"], ["1e-3", "1", "1000"], ["  6.2500  ", "25", "4"], ["86400", "86400", "1"]])(
    "uses exact rational duration %s", (raw, num, den) => { expect(inspectorDuration(raw)).toEqual({ num, den }); });
  test.each(["", "1001/", "1e", "0", "-1", "86400.0001", "Infinity", "0x1"])(
    "retains but does not submit invalid duration %j", raw => {
      const draft = rawDraft(source(), "duration", raw);
      expect(() => inspectorDuration(draft.raw.duration)).toThrow(); expect(draft.raw.duration).toBe(raw);
    });
  test("enforces UTF-8 objective budget without truncating input", () => {
    const project = source(), draft = rawDraft(project, "objective", "界".repeat(1334));
    expect(() => inspectorChange(draft, project, "objective")).toThrow(/UTF-8/);
    expect(draft.raw.objective).toHaveLength(1334);
  });
  for (const field of ["objective", "duration"] as const) test("respects existing " + field + " lock", () => {
    const project = source(), kind = field === "objective" ? "content" : "timing";
    project.locks.push({ id: other, resource: "scene:" + project.scenes[0].id, kind, note: "Protected" });
    const draft = rawDraft(project, field, field === "objective" ? "New objective" : "6");
    expect(() => inspectorChange(draft, project, field)).toThrow(/locked/);
  });
  test("removed scene retains its input but cannot dispatch", () => {
    const project = source(), draft = rawDraft(project); project.scenes.shift();
    expect(reconcileInspectorDraft(draft, project)).toEqual(draft);
    expect(() => inspectorChange(draft, project, "objective")).toThrow(/no longer present/);
  });
});

describe("original Inspector acknowledgement", () => {
  for (const field of ["objective", "duration"] as const) test("only original " + field + " receipt confirms submitted value", () => {
    const value = field === "objective" ? "  Authored objective  " : "6.25";
    const { project, draft, change } = sent(field, value), saved = receipt(project, change);
    expect(acknowledgeInspectorDraft(draft, other, saved)).toEqual(draft);
    const accepted = acknowledgeInspectorDraft(draft, attempt, saved);
    expect(accepted.pending).toBeNull(); expect(accepted.dirty[field]).toBe(false);
    expect(accepted.baseRevision).toBe(saved.revision);
  });
  test("matching later observations never clear UNKNOWN", () => {
    const { project, draft, change } = sent(), saved = receipt(project, change);
    expect(reconcileInspectorDraft(draft, saved)).toEqual(draft);
    expect(inspectorUnknown(reconcileInspectorDraft(retainInspectorFailure(draft, attempt, "lost reply"), saved))).toBe(true);
  });
  test("a later edit back to the old spelling is still authored", () => {
    const { project, draft, change } = sent();
    let later = editInspectorDraft(draft, "objective", "Later edit");
    later = editInspectorDraft(later, "objective", draft.raw.objective);
    const accepted = acknowledgeInspectorDraft(later, attempt, receipt(project, change));
    expect(accepted.raw.objective).toBe(draft.raw.objective); expect(accepted.dirty.objective).toBe(true);
  });
  test("objective ACK retains an independent incomplete duration", () => {
    const { project, draft, change } = sent();
    const accepted = acknowledgeInspectorDraft(editInspectorDraft(draft, "duration", "1e"), attempt, receipt(project, change));
    expect(accepted.raw.duration).toBe("1e"); expect(accepted.dirty.duration).toBe(true);
    expect(accepted.dirty.objective).toBe(false);
  });
  test.each(["id", "generation", "active_branch", "revision", "operation"] as const)(
    "wrong original reply %s cannot release UNKNOWN", kind => {
      const { project, draft, change } = sent(), saved = receipt(project, change);
      if (kind === "revision") saved.revision++;
      else if (kind === "operation") saved.scenes[0].objective = "Unrelated operation";
      else saved[kind] = other;
      expect(() => acknowledgeInspectorDraft(draft, attempt, saved)).toThrow();
      expect(inspectorUnknown(draft)).toBe(true);
    });
  test("stale input requires explicit advance while UNKNOWN cannot advance or replay", () => {
    const { project, draft, change } = sent(), saved = receipt(project, change);
    expect(() => advanceInspectorDraft(draft, saved)).toThrow(/UNKNOWN/);
    expect(() => prepareInspectorSubmission(draft, project, change, other)).toThrow(/UNKNOWN/);
    const unsent = rawDraft(project);
    expect(() => prepareInspectorSubmission(unsent, saved, change, other)).toThrow(/current revision/);
    expect(advanceInspectorDraft(unsent, saved).baseRevision).toBe(saved.revision);
  });
});

describe("verified Inspector custody", () => {
  test("writes exact raw copy and verified writer pointer", () => {
    const storage = new MemoryStorage(), tab = new MemoryStorage(), draft = rawDraft(), owner = custody();
    writeInspectorCopy(storage, tab, draft, owner);
    expect(tab.getItem(inspectorPointerKey(draft.scope))).toBe(attempt);
    expect(decodeInspectorCopy(storage.getItem(inspectorCopyKey(draft.scope, attempt))!, draft.scope, attempt).draft).toEqual(draft);
    writeInspectorCopy(storage, tab, draft, owner);
  });
  test.each(["throw", "silent", "pointer-throw", "pointer-silent"] as const)("rejects unverified %s write", failure => {
    const storage = new MemoryStorage(), tab = new MemoryStorage(), draft = rawDraft();
    const sink = failure.startsWith("pointer") ? tab : storage;
    sink.setItem = () => { if (failure.endsWith("throw")) throw new Error("denied"); };
    expect(() => writeInspectorCopy(storage, tab, draft, custody())).toThrow();
    expect(draft.raw.objective).toBe("  Authored objective  ");
  });
  test("conflicting writers retain original bytes", () => {
    const storage = new MemoryStorage(), tab = new MemoryStorage(), draft = rawDraft(), owner = custody();
    writeInspectorCopy(storage, tab, draft, owner);
    const key = inspectorCopyKey(draft.scope, attempt); storage.setItem(key, "peer bytes");
    expect(() => writeInspectorCopy(storage, tab, editInspectorDraft(draft, "objective", "Later"), owner)).toThrow(/Another window/);
    expect(storage.getItem(key)).toBe("peer bytes");
  });
  test("copy budget blocks without evicting any prior copy", () => {
    const storage = new MemoryStorage(), tab = new MemoryStorage(), draft = rawDraft();
    for (let i = 0; i < MAX_INSPECTOR_COPIES; i++) storage.setItem(inspectorCopyKey(draft.scope, "existing-" + i), "original-" + i);
    const before = [...storage.data];
    expect(() => writeInspectorCopy(storage, tab, draft, custody())).toThrow(/full/);
    expect([...storage.data]).toEqual(before);
  });
  test("clean checkpoint cannot be mutated by the old writer", () => {
    const storage = new MemoryStorage(), tab = new MemoryStorage(), project = source(), clean = createInspectorDraft(project, project.scenes[0].id), owner = custody();
    writeInspectorCopy(storage, tab, clean, owner);
    expect(() => writeInspectorCopy(storage, tab, editInspectorDraft(clean, "objective", "New intent"), owner)).toThrow(/immutable/);
  });
  test.each(["owner", "writer", "unknown-key", "field", "raw", "revision"] as const)("rejects corrupted %s recovery", kind => {
    const { draft } = sent(), value = copy(draft);
    if (kind === "owner") value.scope = inspectorScope({ ...source(), generation: other }, source().scenes[0].id);
    else if (kind === "writer") value.writerId = other;
    else if (kind === "unknown-key") Object.assign(value.draft, { untrusted: true });
    else if (kind === "field") value.draft.pending!.field = "duration";
    else if (kind === "raw") value.draft.pending!.raw.objective = "Other source";
    else value.draft.pending!.revision++;
    expect(() => decodeInspectorCopy(JSON.stringify(value), draft.scope, attempt)).toThrow();
  });
});

describe("explicit inspection and separate scene intent", () => {
  for (const later of [false, true]) test("keeps original UNKNOWN while separating " + (later ? "later input" : "unchanged input"), async () => {
    const { project, draft: original, change } = sent(), saved = receipt(project, change);
    const draft = later ? editInspectorDraft(original, "objective", "  Later intent  ") : original;
    const outcome = await inspectInspectorOutcome(draft, saved, {
      readProject: async () => structuredClone(saved), readHistory: async () => journal(project, change),
    });
    expect(outcome.matchingValueRevisions).toEqual([saved.revision]);
    expect(inspectorUnknown(draft)).toBe(true); expect(inspectorOutcomeMatches(draft, saved, outcome)).toBe(true);
    const next = separateInspectedIntent(draft, saved, outcome);
    expect(next.pending).toBeNull(); expect(next.raw.objective).toBe(later ? "  Later intent  " : saved.scenes[0].objective);
    expect(next.dirty.objective).toBe(later);
    const storage = new MemoryStorage(), tab = new MemoryStorage(), owner = custody();
    writeInspectorCopy(storage, tab, draft, owner);
    const key = inspectorCopyKey(draft.scope, attempt), originalBytes = storage.getItem(key);
    retainSeparateInspectorIntent(storage, tab, draft, next, owner, other);
    expect(storage.getItem(key)).toBe(originalBytes);
    expect(decodeInspectorCopy(originalBytes!, draft.scope, attempt).draft.pending?.outcome).toBe("UNKNOWN");
    if (later) expect(prepareInspectorSubmission(next, saved, inspectorChange(next, saved, "objective"), other).pending?.id).not.toBe(attempt);
    else expect(inspectorHasInput(next)).toBe(false);
  });
  test.each(["missing", "gap", "changed", "scope"] as const)("rejects %s saved-source/history inspection", async failure => {
    const { project, draft, change } = sent(), saved = receipt(project, change); let reads = 0;
    await expect(inspectInspectorOutcome(draft, saved, {
      readProject: async () => {
        reads++; return failure === "changed" && reads > 1 ? { ...saved, revision: saved.revision + 1 }
          : failure === "scope" ? { ...saved, generation: other } : saved;
      },
      readHistory: async () => failure === "missing" ? [] : failure === "gap"
        ? [{ ...journal(project, change)[0], revision: saved.revision + 1 }] : journal(project, change),
    })).rejects.toThrow();
    expect(inspectorUnknown(draft)).toBe(true);
  });
  test("storage failure during separation never removes the UNKNOWN source", async () => {
    const { project, draft, change } = sent(), saved = receipt(project, change);
    const outcome = await inspectInspectorOutcome(draft, saved, { readProject: async () => saved, readHistory: async () => journal(project, change) });
    const next = separateInspectedIntent(draft, saved, outcome), storage = new MemoryStorage(), tab = new MemoryStorage(), owner = custody();
    writeInspectorCopy(storage, tab, draft, owner);
    const key = inspectorCopyKey(draft.scope, attempt), before = storage.getItem(key);
    tab.setItem = () => { throw new Error("pointer denied"); };
    expect(() => retainSeparateInspectorIntent(storage, tab, draft, next, owner, other)).toThrow(/pointer denied/);
    expect(storage.getItem(key)).toBe(before);
    expect([...storage.data.values()].every(raw => JSON.parse(raw).draft.pending?.outcome === "UNKNOWN")).toBe(true);
  });
});
