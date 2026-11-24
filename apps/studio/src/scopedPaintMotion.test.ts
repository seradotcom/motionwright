import { describe, expect, it } from "vitest";
import { applyChange, bootstrap, previewCreativePatch } from "./api";
import { fixtureProject } from "./fixture";
import { nodeTransform, createScopedDraft, decodeScopedDraft, draftMotionInput, draftPatch, draftStyleInput,
  encodeScopedDraft, hydrateScopedDraft, keepCurrentDraftValue, queueDraftEdit, rebaseScopedDraft, scopedDraftHasWork, draftEditorDirty,
  retainUnqueuedInputs, scopedDraftConflicts, scopedDraftScope, selectDraftNode, unqueuedInputCount,
  updateDraftMotionInput, updateDraftStyleInput, draftEditorSourceAvailable } from "./scopedChangeDraft";
import { parseScopedMotion, parseScopedStyle, SCOPED_MOTION_PROPERTIES, scopedMotionRows, scopedStyleRaw } from "./scopedPaintMotion";
import type { ScopedChangeDraft } from "./scopedChangeDraft";
import type { CanvasKeyframe, Project } from "./types";

// Editorial fixtures exercise real draft / change / undo behavior, not physical
// renderer output or a persisted service. The companion Service journey is opt-in.
function source() {
  const project: Project = structuredClone(fixtureProject);
  project.locks = []; project.reviews = []; project.branch_workspaces = [];
  project.production_design = { plan: null, heroes: [], capsules: [], patches: [] };
  const scene = project.scenes[0], node = scene.nodes[0];
  node.parent_id = null; node.relations = []; node.property_locks = [];
  node.kind = "shape"; node.text = null; node.x = 360; node.y = 240; node.width = 240; node.height = 120;
  node.rotation_deg = 0; node.opacity = 1; node.coordinate_space = "project_pixels";
  const pose = nodeTransform(node);
  node.keyframes = [0, 1].flatMap(at => SCOPED_MOTION_PROPERTIES.map(property => ({
    at: { num: String(at), den: "1" }, property,
    value: property === "x" && !at ? node.x - 60 : pose[property],
    interpolation: at ? "ease_out_cubic" : "linear",
  } as CanvasKeyframe)));
  return { project, scene, node };
}
const kind = (draft: ScopedChangeDraft, value: ScopedChangeDraft["editor"]["kind"]): ScopedChangeDraft =>
  ({ ...draft, editor: { ...draft.editor, kind: value } });
function proposal() {
  const data = source();
  let draft = kind(createScopedDraft(data.project, data.scene), "style");
  draft = queueDraftEdit(updateDraftStyleInput(draft, "fill", "#C95D63"));
  draft = kind(draft, "motion");
  draft = updateDraftMotionInput(draft, draftMotionInput(draft).map(row => ({
    ...row, at: row.at === "1" ? "3/2" : row.at,
    value: row.property === "opacity" && row.at === "0" ? "0" : row.value,
  })));
  draft = { ...queueDraftEdit(draft), rationale: "Restore readable contrast and retime the complete entrance." };
  return { ...data, draft };
}

describe("scoped style and complete animation repair", () => {
  it("does not invent edits when imported key order or inherited style spelling differs from input normalization", () => {
    const { project, scene, node } = source();
    node.keyframes.reverse();
    node.style.fill = " #FFFFFF ";
    const draft = createScopedDraft(project, scene);
    expect(scopedDraftHasWork(draft)).toBe(false);
    expect(unqueuedInputCount(draft)).toBe(0);
    expect(draftEditorDirty(kind(draft, "style"))).toBe(false);
    expect(draftEditorDirty(kind(draft, "motion"))).toBe(false);
    expect(decodeScopedDraft(encodeScopedDraft(draft), scopedDraftScope(project, scene))).toEqual(draft);
  });

  it("queues both full values, keeps exact rational time and removes obsolete endpoint keys", async () => {
    const { project, scene, node, draft } = proposal();
    const before = structuredClone(project), patch = draftPatch(draft, scene.id);
    const browserBefore = await bootstrap();
    const preview = await previewCreativePatch(project, patch);
    expect(project).toEqual(before); expect(await bootstrap()).toEqual(browserBefore);
    expect(patch.edits.map(edit => edit.kind)).toEqual(["style", "motion"]);
    expect(preview.after[0].style.fill).toBe("#C95D63");
    expect(preview.after[0].keyframes).toHaveLength(12);
    expect(preview.after[0].keyframes.slice(6).every(key => key.at.num === "3" && key.at.den === "2")).toBe(true);
    expect(preview.after[0].id).toBe(node.id);
    const saved = await applyChange(project, { type: "apply_creative_patch", patch });
    expect(saved.revision).toBe(project.revision + 1);
    expect(saved.production_design!.patches).toHaveLength(1);
    expect(saved.scenes[0].nodes.find(item => item.id === node.id)).toEqual(preview.after[0]);
    const undone = await applyChange(saved, { type: "undo_creative_patch", patch_id: saved.production_design!.patches![0].id });
    expect(undone.scenes[0].nodes).toEqual(project.scenes[0].nodes);
    const redone = await applyChange(undone, { type: "undo_creative_patch", patch_id: undone.production_design!.patches![1].id });
    expect(redone.scenes[0].nodes).toEqual(saved.scenes[0].nodes);
  });

  it("clears the whole track as one reversible source edit", async () => {
    const { project, scene, node } = source();
    let draft = kind(createScopedDraft(project, scene), "motion");
    draft = { ...queueDraftEdit(updateDraftMotionInput(draft, [])), rationale: "Keep this object still during the evidence hold." };
    const saved = await applyChange(project, { type: "apply_creative_patch", patch: draftPatch(draft, scene.id) });
    expect(saved.scenes[0].nodes.find(item => item.id === node.id)!.keyframes).toEqual([]);
    const undone = await applyChange(saved, { type: "undo_creative_patch", patch_id: saved.production_design!.patches![0].id });
    expect(undone.scenes[0].nodes).toEqual(project.scenes[0].nodes);
  });

  it.each(["size", "opacity", "timing", "style"] as const)("retains source atomically when %s is locked, including removed channels", async lock => {
    const { project, scene, node, draft } = proposal();
    const locked = await applyChange(project, lock === "timing"
      ? { type: "set_lock", resource: "scene:" + scene.id, kind: "timing", note: "Preserve approved timing" }
      : { type: "set_node_property_lock", scene_id: scene.id, node_id: node.id, property: lock, locked: true });
    const patch = draftPatch(draft, scene.id); patch.base_revision = locked.revision;
    patch.edits[1] = { kind: "motion", node_id: node.id, keyframes: [] };
    const before = structuredClone(locked), browserBefore = await bootstrap();
    await expect(previewCreativePatch(locked, patch)).rejects.toThrow(/lock/i);
    await expect(applyChange(locked, { type: "apply_creative_patch", patch })).rejects.toThrow(/lock/i);
    expect(locked).toEqual(before); expect(await bootstrap()).toEqual(browserBefore);
  });

  it("keeps parked invalid raw fields and the previously queued values through recovery and unrelated rebase", () => {
    const { project, scene, node, draft: queued } = proposal();
    let draft = updateDraftStyleInput(queued, "font_size", "3e");
    const rows = draftMotionInput(draft); rows[0] = { ...rows[0], at: "1/", value: "-" };
    draft = updateDraftMotionInput(draft, rows);
    draft = selectDraftNode(draft, scene.nodes[1]);
    const recovered = decodeScopedDraft(encodeScopedDraft(draft), scopedDraftScope(project, scene));
    const next = structuredClone(project); next.revision++; next.title = "Unrelated title";
    draft = rebaseScopedDraft(recovered, next, next.scenes[0]);
    draft = selectDraftNode(draft, next.scenes[0].nodes.find(item => item.id === node.id));
    expect(draftStyleInput(draft).font_size).toBe("3e");
    expect(draftMotionInput(draft)[0]).toMatchObject({ at: "1/", value: "-" });
    expect(unqueuedInputCount(draft)).toBe(2);
    expect(draftPatch(draft, scene.id).edits).toEqual(queued.edits);
    expect(() => queueDraftEdit(kind(draft, "style"))).toThrow();
    expect(() => queueDraftEdit(kind(draft, "motion"))).toThrow();
  });

  it("retains later style and animation input after the original reviewed receipt", async () => {
    const { project, scene, draft } = proposal();
    const saved = await applyChange(project, { type: "apply_creative_patch", patch: draftPatch(draft, scene.id) });
    let later = updateDraftStyleInput(draft, "stroke_width", "1e");
    const rows = draftMotionInput(later); rows[0].at = "1/";
    later = updateDraftMotionInput(later, rows);
    const retained = retainUnqueuedInputs(later, saved, saved.scenes[0]);
    expect(retained.baseRevision).toBe(saved.revision);
    expect(retained.edits).toEqual([]);
    expect(draftStyleInput(retained).stroke_width).toBe("1e");
    expect(draftMotionInput(retained)[0].at).toBe("1/");
    expect(unqueuedInputCount(retained)).toBe(2);
    expect(retainUnqueuedInputs(draft, saved, saved.scenes[0]).edits).toEqual([]);
    expect(unqueuedInputCount(retainUnqueuedInputs(draft, saved, saved.scenes[0]))).toBe(0);
  });

  it("resolves an overlapping style independently while keeping the complete motion proposal", () => {
    const { project, scene, draft } = proposal(), next = structuredClone(project);
    next.revision++; next.scenes[0].nodes[0].style.fill = "#4488CC";
    const conflicts = scopedDraftConflicts(draft, next.scenes[0]);
    expect(conflicts.map(conflict => conflict.kind)).toEqual(["style"]);
    expect(() => rebaseScopedDraft(draft, next, next.scenes[0])).toThrow();
    const kept = keepCurrentDraftValue(draft, conflicts[0], next.scenes[0]);
    expect(kept.edits.map(edit => edit.kind)).toEqual(["motion"]);
    const rebased = rebaseScopedDraft(kept, next, next.scenes[0]);
    expect(draftStyleInput(rebased).fill).toBe("#4488CC");
    expect(draftPatch(rebased, scene.id).edits).toEqual(draft.edits.filter(edit => edit.kind === "motion"));
  });

  it.each(["parent", "follow", "geometry"] as const)("requires explicit conflict review when motion's %s context changed", change => {
    const { project, draft } = proposal(), next = structuredClone(project);
    if (change === "parent") next.scenes[0].nodes[0].parent_id = "new-parent";
    if (change === "follow") next.scenes[0].nodes[0].relations = [{ id: "new-relation", kind: "follow", target_id: next.scenes[0].nodes[1].id }];
    if (change === "geometry") next.scenes[0].nodes[0].x += 1;
    next.revision++;
    expect(scopedDraftConflicts(draft, next.scenes[0]).find(conflict => conflict.kind === "motion")?.reason).toBe("structure");
    expect(() => rebaseScopedDraft(draft, next, next.scenes[0])).toThrow();
  });

  it("upgrades old drafts only from their exact unchanged source and never restores stored authority", () => {
    const { project, scene } = source();
    const old = JSON.parse(encodeScopedDraft(createScopedDraft(project, scene)));
    old.schema = "motionwright.scoped-change-draft/2";
    for (const field of ["style", "keyframes", "coordinate_space", "relations"]) delete old.editor.source[field];
    old.commitGrant = "untrusted"; old.editor.authority = "untrusted";
    const decoded = decodeScopedDraft(JSON.stringify(old), scopedDraftScope(project, scene));
    expect(draftEditorSourceAvailable(kind(decoded, "motion"))).toBe(false);
    const upgraded = hydrateScopedDraft(decoded, project, scene);
    expect(draftEditorSourceAvailable(kind(upgraded, "motion"))).toBe(true);
    expect(draftMotionInput(upgraded)).toEqual(scopedMotionRows(scene.nodes[0].keyframes));
    expect(JSON.stringify(upgraded)).not.toContain("untrusted");
    const changed = { ...project, revision: project.revision + 1 };
    expect(draftEditorSourceAvailable(kind(hydrateScopedDraft(decoded, changed, scene), "motion"))).toBe(false);
    expect(() => queueDraftEdit(kind(decoded, "motion"))).toThrow(/original/i);
  });

  it("retains exact key times and rejects ambiguous slots, incomplete input, invalid channels and out-of-scene keys", () => {
    const row = { at: "1/30", property: "x", value: "123.5", interpolation: "hold" } as const;
    expect(parseScopedMotion([row])[0]).toEqual({ at: { num: "1", den: "30" }, property: "x", value: 123.5, interpolation: "hold" });
    expect(() => parseScopedMotion([row, { ...row, at: "2/60" }])).toThrow(/same time/);
    for (const value of ["-", "1.", "1e", "Infinity"]) expect(() => parseScopedMotion([{ ...row, value }])).toThrow();
    expect(() => parseScopedMotion([{ ...row, property: "opacity", value: "1.1" }])).toThrow();
    expect(() => parseScopedMotion([{ ...row, at: "2" }], { num: "2", den: "1" })).toThrow(/before its end/);
    const { node } = source(), raw = scopedStyleRaw(node.style);
    expect(parseScopedStyle({ ...raw, fill: "", font_family: "", font_size: "" })).toMatchObject({ fill: null, font_family: null, font_size: null });
    expect(() => parseScopedStyle({ ...raw, font_weight: "2.5" })).toThrow();
  });
});
