import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import {
  continueDraftManually, createScopedDraft, decodeScopedDraft, draftPatch, encodeScopedDraft,
  draftEditorDirty, draftTransformInput, draftTransformInvalidFields,
  keepCurrentDraftValue, MAX_DRAFT_BYTES, nodeTransform, queueDraftEdit,
  rebaseScopedDraft, removeDraftEdit, retainUnqueuedInputs, reviewedChangeWasCommitted,
  scopedDraftConflicts, scopedDraftHasWork, scopedDraftScope, scopedReviewKey,
  selectDraftNode, unqueuedInputCount, updateDraftTransformInput,
} from "./scopedChangeDraft";
import type { ReviewedScopedChange, ScopedChangeDraft } from "./scopedChangeDraft";
import type { CanvasTransform } from "./types";

// Pure editor-state tests. These fixtures are not evidence of service persistence,
// native rendering, IPC, or permission enforcement; those require integration tests.
function source() {
  const project = structuredClone(fixtureProject);
  project.production_design = { plan: null, heroes: [], capsules: [], patches: [] };
  const scene = project.scenes[0];
  scene.nodes[1] = { ...scene.nodes[1], kind: "text", text: "A second original line" };
  return { project, scene, first: scene.nodes[0], second: scene.nodes[1] };
}
function copyInput(draft: ScopedChangeDraft, text: string): ScopedChangeDraft {
  return { ...draft, editor: { ...draft.editor, text } };
}
function transformInput(draft: ScopedChangeDraft, x: number): ScopedChangeDraft {
  return { ...draft, editor: { ...draft.editor, kind: "transform", transform: { ...draft.editor.transform, x } } };
}
function rawTransformInput(draft: ScopedChangeDraft, field: keyof CanvasTransform, raw: string): ScopedChangeDraft {
  return updateDraftTransformInput({ ...draft, editor: { ...draft.editor, kind: "transform" } }, field, raw);
}
function queuedCopy() {
  const fixture = source();
  let draft = createScopedDraft(fixture.project, fixture.scene);
  draft = queueDraftEdit(copyInput(draft, "Reviewed replacement"));
  draft = { ...draft, rationale: "Shorten this line while keeping other authored work." };
  return { ...fixture, draft };
}
function matchingReceipt() {
  const { project, scene, first, draft } = queuedCopy();
  const before = [structuredClone(first)];
  const after = [{ ...structuredClone(first), text: "Reviewed replacement" }];
  const reviewed: ReviewedScopedChange = {
    key: scopedReviewKey(project, scene, draft, null), projectId: project.id,
    generation: project.generation, branchId: project.active_branch, revision: project.revision,
    change: { type: "apply_creative_patch", patch: draftPatch(draft, scene.id) },
    preview: { source_revision: project.revision, scene_id: scene.id, before, after },
  };
  const committed = structuredClone(project);
  committed.revision++;
  committed.scenes[0].nodes[0].text = after[0].text;
  committed.production_design!.patches!.push({
    id: "stored-patch", scene_id: scene.id, source_revision: project.revision,
    rationale: draft.rationale, before: structuredClone(before), after: structuredClone(after), reverts: null,
  });
  return { project, draft, reviewed, committed };
}
function matchingTransformReceipt() {
  const { project, scene, first, second } = source();
  const draft = { ...queueDraftEdit(transformInput(createScopedDraft(project, scene), 100)), rationale: "Move this object only." };
  const before = [structuredClone(first)];
  const after = [{ ...structuredClone(first), x: 100 }];
  const reviewed: ReviewedScopedChange = {
    key: scopedReviewKey(project, scene, draft, null), projectId: project.id,
    generation: project.generation, branchId: project.active_branch, revision: project.revision,
    change: { type: "apply_creative_patch", patch: draftPatch(draft, scene.id) },
    preview: { source_revision: project.revision, scene_id: scene.id, before, after },
  };
  const committed = structuredClone(project);
  committed.revision++;
  committed.scenes[0].nodes[0].x = 100;
  committed.production_design!.patches!.push({
    id: "stored-transform-patch", scene_id: scene.id, source_revision: project.revision,
    rationale: draft.rationale, before: structuredClone(before), after: structuredClone(after), reverts: null,
  });
  return { project, scene, first, second, draft, reviewed, committed };
}

describe("raw scoped transform recovery", () => {
  it.each(["1e", "1.", "-", "", " ", "Infinity", "1e309"])("retains incomplete or non-finite input %j without replacing the queued transform or blocking independent copy", raw => {
    const { project, scene, first } = source();
    const queued = queueDraftEdit(transformInput(createScopedDraft(project, scene), first.x + 23));
    const draft = rawTransformInput(queued, "x", raw);
    const before = structuredClone(draft);
    expect(draftTransformInput(draft, "x")).toBe(raw);
    expect(draftTransformInvalidFields(draft)).toEqual(["x"]);
    expect(draftEditorDirty(draft)).toBe(true);
    expect(unqueuedInputCount(draft)).toBe(1);
    expect(scopedDraftConflicts(draft, scene)).toEqual([]);
    expect(() => queueDraftEdit(draft)).toThrow();
    expect(draft).toEqual(before);
    expect(draftPatch(draft, scene.id)).toEqual(draftPatch(queued, scene.id));

    const copy = queueDraftEdit(copyInput({ ...draft, editor: { ...draft.editor, kind: "text" } }, "Independent copy"));
    expect(copy.edits).toEqual([...queued.edits, { kind: "text", node_id: first.id, text: "Independent copy" }]);
    expect(draftTransformInput(copy, "x")).toBe(raw);
    expect(unqueuedInputCount(copy)).toBe(1);
  });

  it.each([["1e2", 100], [".5", 0.5]] as const)("queues complete input %s as a finite numeric edit without raw fields in the proposal", (raw, expected) => {
    const { project, scene, first } = source();
    const draft = rawTransformInput(createScopedDraft(project, scene), "x", raw);
    expect(draftTransformInvalidFields(draft)).toEqual([]);
    const queued = queueDraftEdit(draft);
    expect(queued.edits).toEqual([{ kind: "transform", node_id: first.id, transform: { ...nodeTransform(first), x: expected } }]);
    expect(unqueuedInputCount(queued)).toBe(0);
    expect(JSON.stringify(draftPatch(queued, scene.id))).not.toContain("transformRaw");
    expect(draftTransformInput(draft, "x")).toBe(raw);
  });

  it("preserves active and parked raw input through object changes, recovery and an independent revision", () => {
    const { project, scene, first, second } = source();
    let draft = rawTransformInput(createScopedDraft(project, scene), "x", "1e");
    draft = rawTransformInput(draft, "width", "");
    expect(scopedDraftHasWork(draft)).toBe(true);
    draft = selectDraftNode({ ...draft, editor: { ...draft.editor, kind: "text" } }, second);
    draft = rawTransformInput(draft, "y", "-");
    expect(unqueuedInputCount(draft)).toBe(2);
    const restored = decodeScopedDraft(encodeScopedDraft(draft), draft.scope);
    expect(restored).toEqual(draft);
    expect(restored.schema).toBe("motionwright.scoped-change-draft/2");
    expect(draftTransformInput(restored, "y")).toBe("-");

    const current = structuredClone(project);
    current.revision++;
    current.scenes[0].nodes[0].style.fill = "#E0C080";
    const rebased = rebaseScopedDraft(restored, current, current.scenes[0]);
    expect(rebased.baseRevision).toBe(current.revision);
    const back = selectDraftNode(rebased, structuredClone(first));
    expect(draftTransformInput(back, "x")).toBe("1e");
    expect(draftTransformInput(back, "width")).toBe("");
    expect(back.editor.transform).toEqual(nodeTransform(first));
    expect(unqueuedInputCount(back)).toBe(2);
    expect(selectDraftNode(back, structuredClone(first))).toBe(back);
    expect(draftTransformInput(selectDraftNode(back, second), "y")).toBe("-");
    expect(back.edits).toEqual([]);
  });

  it("migrates a numeric schema-1 backup without losing its queue, parked input or numeric editing compatibility", () => {
    const { project, scene, first, second } = source();
    let draft = queueDraftEdit(transformInput(createScopedDraft(project, scene), first.x + 19));
    draft = copyInput(selectDraftNode(draft, second), "Legacy parked copy");
    const legacy = JSON.parse(encodeScopedDraft(draft));
    legacy.schema = "motionwright.scoped-change-draft/1";
    delete legacy.editor.transformRaw;
    for (const editor of legacy.parkedEditors) delete editor.transformRaw;
    const restored = decodeScopedDraft(JSON.stringify(legacy), draft.scope);
    expect(restored.schema).toBe("motionwright.scoped-change-draft/2");
    expect(restored.baseRevision).toBe(draft.baseRevision);
    expect(restored.edits).toEqual(draft.edits);
    expect(restored.origins).toEqual(draft.origins);
    expect(restored.editor.text).toBe("Legacy parked copy");
    const edited = queueDraftEdit(transformInput(selectDraftNode(restored, first), first.x + 47));
    expect(edited.edits).toEqual([{ kind: "transform", node_id: first.id, transform: { ...nodeTransform(first), x: first.x + 47 } }]);
    expect(selectDraftNode(edited, second).editor.text).toBe("Legacy parked copy");
    expect(decodeScopedDraft(encodeScopedDraft(edited), edited.scope)).toEqual(edited);
  });

  it("resolves text independently of incomplete transform input and explicitly retires raw input when keeping the current transform", () => {
    const { project, draft: initial } = queuedCopy();
    let draft = rawTransformInput(initial, "x", "1e");
    const current = structuredClone(project);
    current.revision++;
    current.scenes[0].nodes[0].text = "Keep this peer copy";
    current.scenes[0].nodes[0].x += 7;
    let conflicts = scopedDraftConflicts(draft, current.scenes[0]);
    expect(conflicts.map(conflict => conflict.kind).sort()).toEqual(["text", "transform"]);
    draft = keepCurrentDraftValue(draft, conflicts.find(conflict => conflict.kind === "text")!, current.scenes[0]);
    expect(draft.editor.text).toBe("Keep this peer copy");
    expect(draftTransformInput(draft, "x")).toBe("1e");
    conflicts = scopedDraftConflicts(draft, current.scenes[0]);
    expect(conflicts).toEqual([expect.objectContaining({ kind: "transform", reason: "value" })]);
    expect(() => rebaseScopedDraft(draft, current, current.scenes[0])).toThrow("overlapping");
    const resolved = keepCurrentDraftValue(draft, conflicts[0], current.scenes[0]);
    expect(draftTransformInvalidFields(resolved)).toEqual([]);
    expect(unqueuedInputCount(resolved)).toBe(0);
    expect(draftTransformInput(resolved, "x")).toBe(String(current.scenes[0].nodes[0].x));
    const rebased = rebaseScopedDraft(resolved, current, current.scenes[0]);
    expect(queueDraftEdit(rebased).edits).toEqual([{ kind: "transform", node_id: rebased.editor.nodeId, transform: nodeTransform(current.scenes[0].nodes[0]) }]);
  });

  it("keeps a later incomplete transform after the earlier transform receives its matching receipt", () => {
    const { project, scene, first, second, draft: initial, reviewed, committed } = matchingTransformReceipt();
    let draft = rawTransformInput(initial, "x", "1e");
    draft = copyInput(selectDraftNode(draft, second), "Another object's later text");
    expect(scopedReviewKey(project, scene, draft, null)).toBe(reviewed.key);
    expect(reviewedChangeWasCommitted(committed, reviewed)).toBe(true);
    const retained = retainUnqueuedInputs(draft, committed, committed.scenes[0]);
    expect(retained.baseRevision).toBe(committed.revision);
    expect(retained.edits).toEqual([]);
    expect(retained.origins).toEqual([]);
    expect(retained.editor.text).toBe("Another object's later text");
    expect(unqueuedInputCount(retained)).toBe(2);
    const restored = decodeScopedDraft(encodeScopedDraft(retained), retained.scope);
    const back = selectDraftNode(restored, committed.scenes[0].nodes.find(node => node.id === first.id));
    expect(back.editor.source?.x).toBe(100);
    expect(draftTransformInput(back, "x")).toBe("1e");
    expect(draftTransformInvalidFields(back)).toEqual(["x"]);
    expect(() => queueDraftEdit(back)).toThrow();
    expect(draftPatch(back, scene.id).edits).toEqual([]);
  });

  it("clears a fully committed equivalent numeric spelling without leaving phantom work after the receipt", () => {
    const { draft: initial, reviewed, committed } = matchingTransformReceipt();
    const draft = rawTransformInput(initial, "x", "1e2");
    expect(unqueuedInputCount(draft)).toBe(0);
    expect(reviewedChangeWasCommitted(committed, reviewed)).toBe(true);
    const retained = retainUnqueuedInputs(draft, committed, committed.scenes[0]);
    expect(scopedDraftHasWork(retained)).toBe(false);
    expect(retained.rationale).toBe("");
    expect(retained.editor.transform.x).toBe(100);
  });
});

describe("scoped change editing conservation", () => {
  it("keeps both unqueued properties when changing editing mode and target object", () => {
    const { project, scene, first, second } = source();
    let draft = createScopedDraft(project, scene);
    expect(scopedDraftHasWork(draft)).toBe(false);
    draft = transformInput(copyInput(draft, "Unqueued first text"), first.x + 41);
    expect(unqueuedInputCount(draft)).toBe(2);
    draft = selectDraftNode(draft, second);
    expect(draft.parkedEditors).toHaveLength(1);
    expect(scopedDraftHasWork(draft)).toBe(true);
    draft = copyInput(draft, "Unqueued second text");
    expect(unqueuedInputCount(draft)).toBe(3);
    draft = selectDraftNode(draft, first);
    expect(draft.editor.text).toBe("Unqueued first text");
    expect(draft.editor.transform.x).toBe(first.x + 41);
    expect(draft.parkedEditors[0].text).toBe("Unqueued second text");
    expect(unqueuedInputCount(draft)).toBe(3);
  });

  it("deduplicates queued property edits while retaining a later unqueued revision", () => {
    const { project, scene } = source();
    let draft = queueDraftEdit(copyInput(createScopedDraft(project, scene), "First version"));
    expect(unqueuedInputCount(draft)).toBe(0);
    draft = copyInput(draft, "Second version");
    expect(unqueuedInputCount(draft)).toBe(1);
    expect(draft.edits[0]).toMatchObject({ text: "First version" });
    draft = queueDraftEdit(draft);
    expect(draft.edits).toHaveLength(1);
    expect(draft.origins).toHaveLength(1);
    expect(draft.edits[0]).toMatchObject({ text: "Second version" });
    expect(unqueuedInputCount(draft)).toBe(0);
  });

  it("does not let later editor changes mutate the reviewed proposal snapshot", () => {
    const { scene, draft } = queuedCopy();
    const snapshot = draftPatch(draft, scene.id);
    const before = structuredClone(snapshot);
    draft.rationale = "A later rationale";
    draft.edits[0] = { kind: "text", node_id: draft.editor.nodeId, text: "Different copy" };
    expect(snapshot).toEqual(before);
  });

  it("round-trips a queue and parked inputs without depending on object identity", () => {
    const { project, scene, first, second } = source();
    let draft = queueDraftEdit(copyInput(createScopedDraft(project, scene), "Queued first text"));
    draft = transformInput(draft, first.x + 10);
    draft = selectDraftNode(draft, second);
    draft = copyInput(draft, "Second object text");
    const restored = decodeScopedDraft(encodeScopedDraft(draft), draft.scope);
    expect(restored).toEqual(draft);
    const back = selectDraftNode(restored, structuredClone(first));
    expect(back.editor.text).toBe("Queued first text");
    expect(back.editor.transform.x).toBe(first.x + 10);
    expect(unqueuedInputCount(back)).toBe(2);
    expect(selectDraftNode(back, structuredClone(first))).toBe(back);
  });

  it("survives more than 128 queue/remove cycles without retaining historical origins", () => {
    const { project, scene, first } = source();
    const targets = Array.from({ length: 160 }, (_, index) => ({
      ...structuredClone(first), id: `cycle-object-${index}`, name: `Editable object ${index}`,
      text: `Original line ${index}`,
    }));
    scene.nodes.push(...targets);
    let draft = queueDraftEdit(copyInput(createScopedDraft(project, scene), "Keep this queued first line"));
    for (const target of targets) {
      draft = selectDraftNode(draft, target);
      draft = queueDraftEdit(copyInput(draft, `Proposed ${target.text}`));
      expect(draft.origins.map(origin => origin.id).sort()).toEqual([first.id, target.id].sort());
      draft = removeDraftEdit(draft, target.id, "text");
      expect(draft.origins.map(origin => origin.id)).toEqual([first.id]);
      // Removing an edit from the queue retains its editor input. Explicitly
      // restore this object's original input before moving to another object,
      // so the legitimate parked-input budget is not the subject of this test.
      expect(draft.editor.text).toBe(`Proposed ${target.text}`);
      draft = copyInput(draft, target.text);
      draft = decodeScopedDraft(encodeScopedDraft(draft), draft.scope);
      expect(draft.edits).toHaveLength(1);
      expect(draft.origins).toHaveLength(1);
      expect(unqueuedInputCount(draft)).toBe(0);
    }
    expect(draft.edits[0]).toMatchObject({ node_id: first.id, text: "Keep this queued first line" });
    expect(scopedDraftConflicts(draft, scene)).toEqual([]);
  });

  it("removes an origin only after its last queued property while preserving editable source inputs", () => {
    const { project, first, draft: initial } = queuedCopy();
    let draft = queueDraftEdit(transformInput(initial, first.x + 33));
    draft = removeDraftEdit(draft, first.id, "text");
    expect(draft.edits).toEqual([expect.objectContaining({ kind: "transform", node_id: first.id })]);
    expect(draft.origins).toHaveLength(1);
    expect(draft.editor.text).toBe("Reviewed replacement");
    draft = removeDraftEdit(draft, first.id, "transform");
    expect(draft.edits).toEqual([]);
    expect(draft.origins).toEqual([]);
    expect(draft.editor.source?.id).toBe(first.id);
    expect(unqueuedInputCount(draft)).toBe(2);
    const restored = decodeScopedDraft(encodeScopedDraft(draft), draft.scope);
    const next = structuredClone(project);
    next.scenes[0].nodes[0].text = "A conflicting current line";
    expect(scopedDraftConflicts(restored, next.scenes[0])).toEqual([
      expect.objectContaining({ nodeId: first.id, kind: "text", reason: "value" }),
    ]);
  });

  it("prunes recovered retired origins before queueing another object", () => {
    const { second, draft: initial } = queuedCopy();
    const legacy = { ...initial, origins: [initial.origins[0], ...Array.from({ length: 127 }, (_, index) => ({
      ...initial.origins[0], id: `retired-origin-${index}`,
    }))] };
    let draft = decodeScopedDraft(encodeScopedDraft(legacy), legacy.scope);
    draft = queueDraftEdit(copyInput(selectDraftNode(draft, second), "Another queued line"));
    expect(draft.origins.map(origin => origin.id).sort()).toEqual([initial.editor.nodeId, second.id].sort());
    expect(decodeScopedDraft(encodeScopedDraft(draft), draft.scope)).toEqual(draft);
  });
});

describe("explicit continuation from a historical native frame", () => {
  function frameDraft() {
    const fixture = queuedCopy();
    let draft = transformInput(fixture.draft, fixture.first.x + 19);
    draft = copyInput(selectDraftNode(draft, fixture.second), "Other uncommitted text");
    draft.nativeSource = {
      schema: "motionwright.native-review-repair-draft/1", projectId: fixture.project.id,
      generation: fixture.project.generation, revision: fixture.project.revision,
      sceneId: fixture.scene.id, profileId: fixture.project.deliverables[0].id,
      frameIndex: 53, frameSha256: "c".repeat(64), targetNodeId: fixture.first.id,
      editKind: "text", rationale: "Manual observation in the earlier native frame",
    };
    return { ...fixture, draft };
  }

  it("preserves the base, queue, inputs and frame metadata without treating historical pixels as a current review", () => {
    const { project, scene, draft } = frameDraft();
    const original = structuredClone(draft);
    const manual = continueDraftManually(draft);
    expect(manual.nativeSource).toBeNull();
    expect(manual.baseRevision).toBe(draft.baseRevision);
    expect(manual.scope).toBe(draft.scope);
    expect(manual.edits).toEqual(draft.edits);
    expect(manual.origins).toEqual(draft.origins);
    expect(manual.editor).toEqual(draft.editor);
    expect(manual.parkedEditors).toEqual(draft.parkedEditors);
    expect(unqueuedInputCount(manual)).toBe(2);
    expect(manual.rationale).toContain(draft.rationale);
    for (const metadata of [project.id, project.generation, scene.id, draft.nativeSource!.profileId,
      `revision ${project.revision}`, "frame 53", "c".repeat(64)]) {
      expect(manual.rationale).toContain(metadata);
    }
    expect(manual.rationale).toContain("not current renderer verification");
    expect(scopedReviewKey(project, scene, manual, null)).not.toBe(scopedReviewKey(project, scene, draft, null));
    expect(continueDraftManually(manual)).toEqual(manual);
    expect(decodeScopedDraft(encodeScopedDraft(manual), manual.scope)).toEqual(manual);
    expect(draft).toEqual(original);
  });

  it("does not bypass overlap checks or silently advance the base before an explicit rebase", () => {
    const { project, first, draft } = frameDraft();
    const current = structuredClone(project);
    current.revision++;
    current.scenes[0].nodes[0].text = "Newer copy from another operator";
    let manual = continueDraftManually(draft);
    expect(manual.baseRevision).toBe(project.revision);
    expect(manual.baseRevision).not.toBe(current.revision);
    const [conflict] = scopedDraftConflicts(manual, current.scenes[0]);
    expect(conflict).toMatchObject({ nodeId: first.id, kind: "text", reason: "value" });
    expect(() => rebaseScopedDraft(manual, current, current.scenes[0])).toThrow("overlapping");
    manual = keepCurrentDraftValue(manual, conflict, current.scenes[0]);
    const rebased = rebaseScopedDraft(manual, current, current.scenes[0]);
    expect(rebased.baseRevision).toBe(current.revision);
    expect(rebased.editor.text).toBe("Other uncommitted text");
    expect(rebased.parkedEditors[0].transform.x).toBe(first.x + 19);
    expect(rebased.parkedEditors[0].text).toBe("Newer copy from another operator");
    expect(unqueuedInputCount(rebased)).toBe(2);
    expect(rebased.nativeSource).toBeNull();
    expect(rebased.rationale).toContain(`revision ${project.revision}`);
  });
});

describe("explicit revision reconciliation", () => {
  it("rebases an independent style edit while preserving queued and parked inputs", () => {
    const { project, scene, first, second, draft: initial } = queuedCopy();
    let draft = transformInput(initial, first.x + 18);
    draft = selectDraftNode(draft, second);
    draft = copyInput(draft, "Second unqueued line");
    const next = structuredClone(project);
    next.revision++;
    next.scenes[0].nodes[0].style.fill = "#E0C080";
    const originalDraft = structuredClone(draft);
    expect(scopedDraftConflicts(draft, next.scenes[0])).toEqual([]);
    const rebased = rebaseScopedDraft(draft, next, next.scenes[0]);
    expect(rebased.baseRevision).toBe(next.revision);
    expect(rebased.edits).toEqual(draft.edits);
    expect(rebased.editor.text).toBe("Second unqueued line");
    expect(selectDraftNode(rebased, scene.nodes[0]).editor.transform.x).toBe(first.x + 18);
    expect(draft).toEqual(originalDraft);
  });

  it("detects parked input conflicts even when the selected object is unchanged", () => {
    const { project, scene, second } = source();
    let draft = copyInput(createScopedDraft(project, scene), "Parked authored line");
    draft = selectDraftNode(draft, second);
    const next = structuredClone(project);
    next.revision++;
    next.scenes[0].nodes[0].text = "Another operator's line";
    expect(scopedDraftConflicts(draft, next.scenes[0])).toEqual([
      expect.objectContaining({ nodeId: scene.nodes[0].id, kind: "text", reason: "value", proposed: "Parked authored line" }),
    ]);
    expect(() => rebaseScopedDraft(draft, next, next.scenes[0])).toThrow("overlapping");
  });

  it("resolves only the conflicted text while retaining the object's pending transform", () => {
    const { project, scene, first, draft: initial } = queuedCopy();
    let draft = queueDraftEdit(transformInput(initial, first.x + 25));
    const next = structuredClone(project);
    next.revision++;
    next.scenes[0].nodes[0].text = "Keep this newer text";
    const [conflict] = scopedDraftConflicts(draft, next.scenes[0]);
    expect(conflict).toMatchObject({ kind: "text", reason: "value" });
    draft = keepCurrentDraftValue(draft, conflict, next.scenes[0]);
    expect(draft.edits).toEqual([{ kind: "transform", node_id: first.id, transform: { ...nodeTransform(first), x: first.x + 25 } }]);
    expect(draft.editor.text).toBe("Keep this newer text");
    expect(draft.editor.transform.x).toBe(first.x + 25);
    expect(scopedDraftConflicts(draft, next.scenes[0])).toEqual([]);
    expect(rebaseScopedDraft(draft, next, next.scenes[0]).baseRevision).toBe(next.revision);
    expect(scene.nodes[0].text).toBe(first.text);
  });

  it("keeps another property's conflict visible when resolving one changed property", () => {
    const { project, first, draft: initial } = queuedCopy();
    let draft = queueDraftEdit(transformInput(initial, first.x + 25));
    const next = structuredClone(project);
    next.revision++;
    next.scenes[0].nodes[0].text = "Later text";
    next.scenes[0].nodes[0].x += 7;
    let conflicts = scopedDraftConflicts(draft, next.scenes[0]);
    expect(conflicts.map(conflict => conflict.kind).sort()).toEqual(["text", "transform"]);
    draft = keepCurrentDraftValue(draft, conflicts.find(conflict => conflict.kind === "text")!, next.scenes[0]);
    conflicts = scopedDraftConflicts(draft, next.scenes[0]);
    expect(conflicts).toEqual([expect.objectContaining({ kind: "transform", reason: "value" })]);
    expect(() => rebaseScopedDraft(draft, next, next.scenes[0])).toThrow("overlapping");
  });

  it("rejects transformed object reparenting but can explicitly retain current transform and independent copy", () => {
    const { project, first, second, draft: initial } = queuedCopy();
    const draft = queueDraftEdit(transformInput(initial, first.x + 25));
    const next = structuredClone(project);
    next.revision++;
    next.scenes[0].nodes[0].parent_id = second.id;
    const [conflict] = scopedDraftConflicts(draft, next.scenes[0]);
    expect(conflict).toMatchObject({ kind: "transform", reason: "structure" });
    const resolved = keepCurrentDraftValue(draft, conflict, next.scenes[0]);
    expect(resolved.edits).toEqual(initial.edits);
    expect(scopedDraftConflicts(resolved, next.scenes[0])).toEqual([]);
    expect(rebaseScopedDraft(resolved, next, next.scenes[0]).editor.source?.parent_id).toBe(second.id);
  });

  it("requires explicit removal of work for a deleted object without losing another object", () => {
    const { project, first, second, draft: initial } = queuedCopy();
    let draft = copyInput(selectDraftNode(initial, second), "Keep this other input");
    const next = structuredClone(project);
    next.revision++;
    next.scenes[0].nodes = next.scenes[0].nodes.filter(node => node.id !== first.id);
    const [conflict] = scopedDraftConflicts(draft, next.scenes[0]);
    expect(conflict).toMatchObject({ nodeId: first.id, reason: "missing" });
    expect(() => rebaseScopedDraft(draft, next, next.scenes[0])).toThrow("overlapping");
    draft = keepCurrentDraftValue(draft, conflict, next.scenes[0]);
    const rebased = rebaseScopedDraft(draft, next, next.scenes[0]);
    expect(rebased.editor.text).toBe("Keep this other input");
    expect(rebased.parkedEditors).toEqual([]);
    expect(rebased.edits).toEqual([]);
  });

  it.each(["id", "generation", "active_branch"] as const)("does not rebase across a different %s", field => {
    const { project, scene, draft } = queuedCopy();
    const different = { ...project, [field]: "different-identity", revision: project.revision + 1 };
    expect(scopedDraftScope(different, scene)).not.toBe(draft.scope);
    expect(() => rebaseScopedDraft(draft, different, scene)).toThrow("another project");
    expect(() => decodeScopedDraft(encodeScopedDraft(draft), scopedDraftScope(different, scene))).toThrow("scope");
  });

  it("does not move a saved proposal to another scene or a stale native frame", () => {
    const { project, scene, draft } = queuedCopy();
    expect(() => rebaseScopedDraft(draft, project, { ...scene, id: "other-scene" })).toThrow("another project");
    expect(() => rebaseScopedDraft(draft, project, null)).toThrow("another project");
    const native = { ...draft, nativeSource: {
      schema: "motionwright.native-review-repair-draft/1" as const, projectId: project.id,
      generation: project.generation, revision: project.revision, sceneId: scene.id,
      profileId: project.deliverables[0].id, frameIndex: 2, frameSha256: "a".repeat(64),
      targetNodeId: draft.editor.nodeId, editKind: "text" as const, rationale: "Manual frame observation",
    } };
    expect(() => rebaseScopedDraft(native, { ...project, revision: project.revision + 1 }, scene)).toThrow("Re-inspect");
  });
});

describe("a matching stored receipt clears only reviewed work", () => {
  it("keeps later copy, unqueued transform, and another object's text after a commit", () => {
    const { project, draft: initial, reviewed, committed } = matchingReceipt();
    let draft = copyInput(initial, "A later unqueued rewrite");
    draft = transformInput(draft, draft.editor.transform.x + 24);
    draft = selectDraftNode(draft, project.scenes[0].nodes[1]);
    draft = copyInput(draft, "Other unqueued text");
    expect(reviewedChangeWasCommitted(committed, reviewed)).toBe(true);
    const retained = retainUnqueuedInputs(draft, committed, committed.scenes[0]);
    expect(retained.baseRevision).toBe(committed.revision);
    expect(retained.edits).toEqual([]);
    expect(retained.origins).toEqual([]);
    expect(retained.editor.text).toBe("Other unqueued text");
    expect(retained.parkedEditors[0].text).toBe("A later unqueued rewrite");
    expect(retained.parkedEditors[0].source?.text).toBe("Reviewed replacement");
    expect(unqueuedInputCount(retained)).toBe(3);
  });

  it("retains an explicitly reverted input while the earlier queued value commits", () => {
    const { project, draft, committed } = matchingReceipt();
    const revertedInput = copyInput(draft, project.scenes[0].nodes[0].text!);
    expect(unqueuedInputCount(revertedInput)).toBe(1);
    const retained = retainUnqueuedInputs(revertedInput, committed, committed.scenes[0]);
    expect(retained.editor.text).toBe(project.scenes[0].nodes[0].text);
    expect(retained.editor.source?.text).toBe("Reviewed replacement");
    expect(unqueuedInputCount(retained)).toBe(1);
  });

  it("clears a fully committed draft and rationale only after matching state", () => {
    const { draft, committed, reviewed } = matchingReceipt();
    expect(reviewedChangeWasCommitted(committed, reviewed)).toBe(true);
    const cleared = retainUnqueuedInputs(draft, committed, committed.scenes[0]);
    expect(scopedDraftHasWork(cleared)).toBe(false);
    expect(cleared.rationale).toBe("");
    expect(cleared.editor.text).toBe("Reviewed replacement");
    expect(cleared.baseRevision).toBe(committed.revision);
  });

  it.each(["id", "generation", "active_branch"] as const)("does not accept a matching-looking receipt from another %s", field => {
    const { committed, reviewed } = matchingReceipt();
    expect(reviewedChangeWasCommitted({ ...committed, [field]: "other" }, reviewed)).toBe(false);
  });

  it.each(["revision", "scene", "before", "after", "rationale", "reverts"] as const)("does not erase work on an unrelated %s receipt", field => {
    const { committed, reviewed } = matchingReceipt();
    const changed = structuredClone(committed);
    const record = changed.production_design!.patches![0];
    if (field === "revision") changed.revision++;
    if (field === "scene") record.scene_id = "other-scene";
    if (field === "before") record.before[0].text = "Different preimage";
    if (field === "after") record.after[0].text = "Different result";
    if (field === "rationale") record.rationale = "Another rationale";
    if (field === "reverts") record.reverts = "another-patch";
    expect(reviewedChangeWasCommitted(changed, reviewed)).toBe(false);
  });

  it("accepts an undo receipt only for the specifically reviewed patch", () => {
    const { committed, reviewed } = matchingReceipt();
    const undo = { ...reviewed, change: { type: "undo_creative_patch" as const, patch_id: "original-patch" } };
    committed.production_design!.patches![0].reverts = "original-patch";
    expect(reviewedChangeWasCommitted(committed, undo)).toBe(true);
    committed.production_design!.patches![0].reverts = "other-patch";
    expect(reviewedChangeWasCommitted(committed, undo)).toBe(false);
  });
});

describe("untrusted session draft decoding", () => {
  it("keeps recovered raw input without granting a missing source or silently emptying its backup", () => {
    const { project, scene, first, second } = source();
    const draft = rawTransformInput(createScopedDraft(project, scene), "x", "1e");
    const saved = JSON.parse(encodeScopedDraft(draft));
    // A damaged recovery record is accepted by the nullable-source schema.
    // This does not pretend that the disabled live controls authored it.
    saved.editor.source = null;
    const restored = decodeScopedDraft(JSON.stringify(saved), draft.scope);
    expect(restored.editor.source).toBeNull();
    expect(draftTransformInput(restored, "x")).toBe("1e");
    expect(scopedDraftHasWork(restored)).toBe(true);
    expect(unqueuedInputCount(restored)).toBe(1);
    expect(() => queueDraftEdit(restored)).toThrow("Select an object from the draft's source revision.");
    expect(scopedDraftConflicts(restored, scene)).toEqual([
      expect.objectContaining({ nodeId: first.id, kind: "transform", reason: "missing", before: null }),
    ]);
    expect(() => rebaseScopedDraft(restored, { ...project, revision: project.revision + 1 }, scene)).toThrow("overlapping");
    expect(decodeScopedDraft(encodeScopedDraft(restored), draft.scope)).toEqual(restored);

    const parked = selectDraftNode(restored, second);
    expect(scopedDraftHasWork(parked)).toBe(true);
    const returned = selectDraftNode(decodeScopedDraft(encodeScopedDraft(parked), draft.scope), first);
    expect(returned.editor.source).toBeNull();
    expect(draftTransformInput(returned, "x")).toBe("1e");
    expect(() => queueDraftEdit(returned)).toThrow("Select an object from the draft's source revision.");
    expect(draftPatch(returned, scene.id).edits).toEqual([]);

    const completed = updateDraftTransformInput(returned, "x", "1e2");
    expect(draftTransformInvalidFields(completed)).toEqual([]);
    expect(scopedDraftHasWork(completed)).toBe(true);
    expect(unqueuedInputCount(completed)).toBe(1);
    expect(decodeScopedDraft(encodeScopedDraft(completed), draft.scope)).toEqual(completed);
    const completedParked = selectDraftNode(completed, second);
    expect(scopedDraftHasWork(completedParked)).toBe(true);
    const completedReturned = selectDraftNode(decodeScopedDraft(encodeScopedDraft(completedParked), draft.scope), first);
    expect(completedReturned.editor.source).toBeNull();
    expect(draftTransformInput(completedReturned, "x")).toBe("1e2");
    expect(unqueuedInputCount(completedReturned)).toBe(1);
    expect(scopedDraftConflicts(completedReturned, scene)).toEqual([
      expect.objectContaining({ nodeId: first.id, kind: "transform", reason: "missing", before: null }),
    ]);
    expect(() => rebaseScopedDraft(completedReturned, { ...project, revision: project.revision + 1 }, scene)).toThrow("overlapping");
    expect(() => queueDraftEdit(completedReturned)).toThrow("Select an object from the draft's source revision.");
    expect(scopedDraftHasWork(createScopedDraft(project, { ...scene, nodes: [] }))).toBe(false);
  });

  it("does not infer a recovered input's missing source from another object's matching receipt", () => {
    const { scene, second, draft: initial, reviewed, committed } = matchingTransformReceipt();
    const draft = rawTransformInput(selectDraftNode(initial, second), "x", "1e");
    const saved = JSON.parse(encodeScopedDraft(draft));
    saved.editor.source = null;
    const restored = decodeScopedDraft(JSON.stringify(saved), draft.scope);
    expect(reviewedChangeWasCommitted(committed, reviewed)).toBe(true);
    const retained = retainUnqueuedInputs(restored, committed, committed.scenes[0]);
    expect(retained.baseRevision).toBe(committed.revision);
    expect(retained.edits).toEqual([]);
    expect(retained.origins).toEqual([]);
    expect(retained.editor.source).toBeNull();
    expect(draftTransformInput(retained, "x")).toBe("1e");
    expect(scopedDraftHasWork(retained)).toBe(true);
    expect(unqueuedInputCount(retained)).toBe(1);
    expect(decodeScopedDraft(encodeScopedDraft(retained), draft.scope)).toEqual(retained);

    const completed = updateDraftTransformInput(retained, "x", "1e2");
    expect(draftTransformInvalidFields(completed)).toEqual([]);
    expect(scopedDraftHasWork(completed)).toBe(true);
    expect(unqueuedInputCount(completed)).toBe(1);
    expect(decodeScopedDraft(encodeScopedDraft(completed), draft.scope)).toEqual(completed);
    const parked = selectDraftNode(completed, committed.scenes[0].nodes[0]);
    expect(scopedDraftHasWork(parked)).toBe(true);
    const returned = selectDraftNode(decodeScopedDraft(encodeScopedDraft(parked), draft.scope), second);
    expect(returned.editor.source).toBeNull();
    expect(draftTransformInput(returned, "x")).toBe("1e2");
    expect(unqueuedInputCount(returned)).toBe(1);
    expect(scopedDraftConflicts(returned, committed.scenes[0])).toEqual([
      expect.objectContaining({ nodeId: second.id, kind: "transform", reason: "missing", before: null }),
    ]);
    expect(() => rebaseScopedDraft(returned, committed, committed.scenes[0])).toThrow("overlapping");
    expect(() => queueDraftEdit(returned)).toThrow("Select an object from the draft's source revision.");
    expect(draftPatch(retained, scene.id).edits).toEqual([]);
  });

  it("restores typed editing fields and discards injected previews, commands and grants at every depth", () => {
    const { project, scene, draft } = queuedCopy();
    const injected = JSON.parse(encodeScopedDraft(updateDraftTransformInput(draft, "x", String(draft.editor.transform.x))));
    const authority = { preview: { approved: true }, effect_grants: ["forged"], command: "apply_change" };
    Object.assign(injected, authority);
    Object.assign(injected.edits[0], authority);
    Object.assign(injected.origins[0], authority);
    Object.assign(injected.editor, authority);
    Object.assign(injected.editor.source, authority);
    Object.assign(injected.editor.transform, authority);
    Object.assign(injected.editor.transformRaw, authority);
    injected.parkedEditors = [{ ...structuredClone(injected.editor), nodeId: scene.nodes[1].id,
      source: { ...structuredClone(injected.editor.source), id: scene.nodes[1].id } }];
    injected.nativeSource = {
      schema: "motionwright.native-review-repair-draft/1", projectId: project.id,
      generation: project.generation, revision: project.revision, sceneId: scene.id,
      profileId: project.deliverables[0].id, frameIndex: 3, frameSha256: "b".repeat(64),
      targetNodeId: scene.nodes[0].id, editKind: "text", rationale: "Manual source observation", ...authority,
    };
    const decoded = decodeScopedDraft(JSON.stringify(injected), draft.scope);
    expect(decoded.edits).toEqual(draft.edits);
    expect(decoded.nativeSource?.frameSha256).toBe("b".repeat(64));
    const raw = encodeScopedDraft(decoded);
    expect(raw).not.toContain("effect_grants");
    expect(raw).not.toContain("approved");
    expect(raw).not.toContain("apply_change");
    expect(raw).not.toContain("preview");
  });

  it.each(["active null field", "parked numeric field", "active array"])("rejects a malformed raw transform backup: %s", problem => {
    const { project, scene, second } = source();
    const draft = rawTransformInput(createScopedDraft(project, scene), "x", "1e");
    const bad = JSON.parse(encodeScopedDraft(draft));
    if (problem === "active null field") bad.editor.transformRaw.x = null;
    if (problem === "active array") bad.editor.transformRaw = ["1e"];
    if (problem === "parked numeric field") {
      bad.parkedEditors = [{ ...structuredClone(bad.editor), nodeId: second.id,
        source: { ...structuredClone(bad.editor.source), id: second.id } }];
      bad.parkedEditors[0].transformRaw.x = 7;
    }
    expect(() => decodeScopedDraft(JSON.stringify(bad), draft.scope)).toThrow("unsupported shape");
    expect(draftTransformInput(draft, "x")).toBe("1e");
  });

  it.each(["negative revision", "fractional revision", "invalid transform", "wrong source", "excess edits", "malformed source"])("rejects %s without silently constructing an empty replacement", problem => {
    const { draft } = queuedCopy();
    const bad = JSON.parse(encodeScopedDraft(draft));
    if (problem === "negative revision") bad.baseRevision = -1;
    if (problem === "fractional revision") bad.baseRevision = 1.5;
    if (problem === "invalid transform") bad.editor.transform.opacity = null;
    if (problem === "wrong source") bad.editor.source.id = "another-object";
    if (problem === "excess edits") bad.edits = Array.from({ length: 129 }, () => bad.edits[0]);
    if (problem === "malformed source") bad.nativeSource = { effect_grants: ["forged"] };
    expect(() => decodeScopedDraft(JSON.stringify(bad), draft.scope)).toThrow("unsupported shape");
  });

  it("bounds UTF-8 bytes rather than character count and leaves the live draft unmodified on failure", () => {
    const { draft } = queuedCopy();
    const oversized = { ...draft, rationale: "é".repeat(Math.floor(MAX_DRAFT_BYTES / 2) + 1) };
    const raw = JSON.stringify(oversized);
    expect(raw.length).toBeLessThan(MAX_DRAFT_BYTES);
    expect(new TextEncoder().encode(raw).length).toBeGreaterThan(MAX_DRAFT_BYTES);
    expect(() => encodeScopedDraft(oversized)).toThrow("too large");
    expect(() => decodeScopedDraft(raw, draft.scope)).toThrow("budget");
    expect(oversized.rationale.length).toBe(Math.floor(MAX_DRAFT_BYTES / 2) + 1);
    expect(scopedDraftHasWork(oversized)).toBe(true);
  });

  it("invalidates the review identity when queued copy, rationale, revision or undo target changes", () => {
    const { project, scene, draft } = queuedCopy();
    const key = scopedReviewKey(project, scene, draft, null);
    const changedCopy = queueDraftEdit(copyInput(draft, "A different reviewed line"));
    expect(scopedReviewKey(project, scene, changedCopy, null)).not.toBe(key);
    expect(scopedReviewKey(project, scene, { ...draft, rationale: "New justification" }, null)).not.toBe(key);
    expect(scopedReviewKey({ ...project, revision: project.revision + 1 }, scene, draft, null)).not.toBe(key);
    expect(scopedReviewKey(project, scene, draft, "an-undo-target")).not.toBe(key);
  });
});
