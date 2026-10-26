import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import type { CanvasNode, Project } from "./types";
import {
  CANVAS_COPY_BYTES, CANVAS_COPY_LIMIT, CANVAS_DRAFT_PREFIX, canvasContextConflicts, canvasDraftConflicts, canvasDraftHasWork,
  canvasDraftScope, canvasFieldIssue, canvasGroupIssue, canvasRecoveryKey, canvasSubmissionMatches,
  createCanvasDraft, decodeCanvasRecovery, discardCanvasRecovery, editCanvasField, prepareCanvasSubmission, readCanvasRecoveries,
  reconcileCanvasDraft, releaseCanvasSubmission, resolveCanvasField, reviewCanvasContext, writeCanvasRecovery,
  acknowledgeCanvasReparent, canvasHierarchyReviewIssue, editCanvasReparentPolicy,
  proposeCanvasReparent, reviewCanvasHierarchySubmission,
} from "./canvasWorkspaceDraft";
import type { CanvasDraftStorage, CanvasRecovery, CanvasWorkspaceDraft } from "./canvasWorkspaceDraft";

function source() { return structuredClone(fixtureProject); }
const sceneId = fixtureProject.scenes[0].id, nodeId = fixtureProject.scenes[0].nodes[0].id;
const node = (project: Project) => project.scenes[0].nodes[0];
const draftFor = (project: Project) => createCanvasDraft(project, sceneId, nodeId);
function nextProject(project: Project, values: Partial<CanvasNode>) {
  const next = structuredClone(project); next.revision++; Object.assign(node(next), values); return next;
}
function copy(draft: CanvasWorkspaceDraft | null, writerId = "window-a", at = 1): CanvasRecovery {
  return { schema: "motionwright.canvas-recovery/1", scope: canvasDraftScope(source(), sceneId, nodeId), writerId, updatedAt: at, draft };
}
function roundTrip(draft: CanvasWorkspaceDraft) {
  const value = copy(draft);
  return decodeCanvasRecovery(JSON.stringify(value), draft.scope, value.writerId).draft!;
}
class MemoryStorage implements CanvasDraftStorage {
  data = new Map<string, string>();
  known = new Map<string, string>();
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
function retain(storage: MemoryStorage, value: CanvasRecovery) {
  const key = canvasRecoveryKey(value.scope, value.writerId);
  const raw = writeCanvasRecovery(storage, value, storage.known.get(key) ?? null);
  storage.known.set(key, raw); return raw;
}

describe("Canvas input continuity invariants", () => {
  it("keeps raw decimal fragments and exact human text through durable recovery", () => {
    const project = source();
    for (const raw of ["", "-", "1e", " 1.25e+ ", "Infinity", "0x10"]) {
      let draft = editCanvasField(draftFor(project), "x", raw);
      draft = editCanvasField(draft, "text", "  Human decision — keep these spaces.\n🌘\n");
      const recovered = reconcileCanvasDraft(roundTrip(draft), project);
      expect(recovered.fields.x.raw).toBe(raw);
      expect(recovered.fields.text.raw).toBe(draft.fields.text.raw);
      expect(canvasGroupIssue(recovered, project, "transform")).toContain("finite decimal");
      expect(prepareCanvasSubmission(recovered, project, "text", "sent-text").change).toEqual({
        type: "update_canvas_text", scene_id: sceneId, node_id: nodeId, text: draft.fields.text.raw,
      });
    }
    expect(canvasFieldIssue("width", "0")).toBeNull();
    expect(canvasFieldIssue("height", "-0.1")).toContain("zero or greater");
    expect(canvasFieldIssue("opacity", "1.001")).toContain("between zero and one");
    expect(canvasFieldIssue("x", " 1.25e+2 ")).toBeNull();
  });

  it("scopes recovery to project, generation, branch, scene and object identities", () => {
    const project = source(), draft = editCanvasField(draftFor(project), "text", "Only this object");
    for (const property of ["id", "generation", "active_branch"] as const) {
      const other = { ...project, [property]: project[property] + "-other" };
      expect(() => reconcileCanvasDraft(draft, other)).toThrow(/another project/);
    }
    const storage = new MemoryStorage(); retain(storage, copy(draft));
    for (const [scene, object] of [[sceneId + "-other", nodeId], [sceneId, nodeId + "-other"]]) {
      expect(readCanvasRecoveries(storage, canvasDraftScope(project, scene, object)).entries).toEqual([]);
    }
    const malformed = { ...copy(draft), draft: { ...draft, nodeId: "another-object" } };
    expect(() => decodeCanvasRecovery(JSON.stringify(malformed), draft.scope, "window-a")).toThrow(/object scope/);
  });

  it("backs up the largest admitted ordinary text object while retaining a separate numeric edit", () => {
    const project = source(); node(project).text = "a".repeat(100_000);
    let draft = editCanvasField(draftFor(project), "x", "420");
    draft = editCanvasField(draft, "text", "b".repeat(100_000));
    const attempt = prepareCanvasSubmission(draft, project, "text", "large-valid-text");
    const recovered = roundTrip(attempt.draft);
    expect(recovered.fields.text.raw).toHaveLength(100_000);
    expect(recovered.fields.x.raw).toBe("420");
    expect(recovered.awaiting).toEqual(attempt.submission);
    expect(canvasFieldIssue("text", "b".repeat(100_001))).toContain("100,000 UTF-8 bytes");
    expect(canvasFieldIssue("text", "🌘".repeat(25_001))).toContain("100,000 UTF-8 bytes");
  });

  it("requires context review when equal numeric values acquire a different parent, space or kind", () => {
    const project = source();
    let draft = editCanvasField(draftFor(project), "x", "420");
    draft = editCanvasField(draft, "text", "A pending line");
    for (const values of [{ parent_id: project.scenes[0].nodes[1].id }, { coordinate_space: "scene_local" as const }]) {
      const changed = nextProject(project, values), recovered = reconcileCanvasDraft(roundTrip(draft), changed);
      expect(canvasContextConflicts(recovered, changed)).toEqual(["transform"]);
      expect(recovered.fields.x.raw).toBe("420");
      expect(() => prepareCanvasSubmission(recovered, changed, "transform", "blocked-context")).toThrow(/object context/);
      expect(prepareCanvasSubmission(recovered, changed, "text", "independent-text").change).toMatchObject({ text: "A pending line" });
      const reviewed = reviewCanvasContext(recovered, changed);
      expect(prepareCanvasSubmission(reviewed, changed, "transform", "reviewed-context").change).toMatchObject({ transform: { x: 420 } });
    }
    const changedKind = nextProject(project, { kind: "shape" });
    expect(canvasContextConflicts(reconcileCanvasDraft(draft, changedKind), changedKind)).toEqual(["text", "transform"]);
    const sameNumbersNewParent = nextProject(project, { parent_id: project.scenes[0].nodes[1].id, x: 420 });
    const converged = reconcileCanvasDraft(roundTrip(draft), sameNumbersNewParent);
    expect(canvasContextConflicts(converged, sameNumbersNewParent)).toEqual(["transform"]);
    expect(converged.baseContext.parent_id).toBeNull();
    expect(canvasContextConflicts(reviewCanvasContext(converged, sameNumbersNewParent), sameNumbersNewParent)).toEqual([]);
  });

  it("preserves authored X across repeated disjoint revisions and composes the current untouched Y", () => {
    let project = source();
    let draft = editCanvasField(draftFor(project), "x", " 411.25 ");
    const originalStyle = structuredClone(node(project).style);
    // Pure state stress, not a claim about persisted service revisions; the G2
    // suite exercises the actual journal and separate SQLite processes.
    for (let revision = 0; revision < 12; revision++) {
      project = nextProject(project, { y: node(project).y + 3, text: "Peer line " + revision });
      draft = reconcileCanvasDraft(roundTrip(draft), project);
      expect(draft.fields.x.raw).toBe(" 411.25 ");
      expect(draft.fields.y.raw).toBe(String(node(project).y));
      expect(draft.fields.text.raw).toBe(node(project).text);
      expect(canvasDraftConflicts(draft, project)).toEqual([]);
    }
    const attempt = prepareCanvasSubmission(draft, project, "transform", "layout-intent");
    expect(attempt.change).toEqual({ type: "transform_canvas_node", scene_id: sceneId, node_id: nodeId,
      transform: { x: 411.25, y: node(project).y, width: node(project).width, height: node(project).height,
        rotation_deg: node(project).rotation_deg, opacity: node(project).opacity } });
    expect(node(project).style).toEqual(originalStyle);
    expect(node(project).x).toBe(node(source()).x);
  });

  it("requires a field choice for overlapping peer edits without consuming independent input", () => {
    const original = source();
    let draft = editCanvasField(draftFor(original), "text", "My pending line");
    draft = editCanvasField(draft, "x", "420");
    const peer = nextProject(original, { text: "A colleague's saved line", y: 333 });
    draft = reconcileCanvasDraft(roundTrip(draft), peer);
    expect(canvasDraftConflicts(draft, peer)).toEqual([{ field: "text", before: node(original).text,
      current: "A colleague's saved line", proposed: "My pending line", futureRevision: false }]);
    expect(() => prepareCanvasSubmission(draft, peer, "text", "blocked")).toThrow(/overlapping/);
    const keepSaved = resolveCanvasField(draft, peer, "text", "saved");
    expect(keepSaved.fields.text.raw).toBe(node(peer).text);
    expect(keepSaved.fields.x).toEqual(draft.fields.x);
    expect(prepareCanvasSubmission(keepSaved, peer, "transform", "layout").change).toMatchObject({ transform: { x: 420, y: 333 } });
    const keepDraft = resolveCanvasField(draft, peer, "text", "draft");
    expect(keepDraft.fields.text.base).toBe(node(peer).text);
    expect(prepareCanvasSubmission(keepDraft, peer, "text", "reviewed").change).toMatchObject({ text: "My pending line" });
    expect(node(peer).text).toBe("A colleague's saved line");
  });

  it("recognizes only the observed snapshot and retains later raw, including a return to the old base", () => {
    const project = source();
    let draft = editCanvasField(editCanvasField(draftFor(project), "x", "420"), "y", "350");
    const sent = prepareCanvasSubmission(draft, project, "transform", "pending-layout"), payload = JSON.stringify(sent.change);
    draft = editCanvasField(sent.draft, "x", "1e");
    draft = editCanvasField(draft, "y", String(node(project).y));
    draft = editCanvasField(draft, "text", "Next human sentence\n");
    expect(canvasSubmissionMatches(project, draft, sent.submission)).toBe(false);
    expect(reconcileCanvasDraft(roundTrip(draft), project).awaiting).toEqual(sent.submission);
    const saved = nextProject(project, { x: 420, y: 350 });
    const acknowledged = reconcileCanvasDraft(roundTrip(draft), saved);
    expect(acknowledged.awaiting).toBeNull();
    expect(acknowledged.fields.x).toMatchObject({ raw: "1e", base: "420", intent: true });
    expect(acknowledged.fields.y).toMatchObject({ raw: String(node(project).y), base: "350", intent: true });
    expect(acknowledged.fields.text.raw).toBe("Next human sentence\n");
    expect(canvasDraftConflicts(acknowledged, saved)).toEqual([]);
    expect(JSON.stringify(sent.change)).toBe(payload);
  });

  it("uses an input sequence to distinguish the same raw value typed again during an ACK", () => {
    const project = source();
    const sent = prepareCanvasSubmission(editCanvasField(draftFor(project), "x", " 420 "), project, "transform", "same-raw");
    const typedAgain = editCanvasField(editCanvasField(sent.draft, "x", "421"), "x", " 420 ");
    const saved = nextProject(project, { x: 420 });
    const recovered = reconcileCanvasDraft(roundTrip(typedAgain), saved);
    expect(recovered.fields.x.raw).toBe(" 420 ");
    expect(recovered.fields.x.sequence).toBe(sent.submission.fields.x!.sequence + 2);
    expect(canvasDraftHasWork(recovered)).toBe(true);
    const unchanged = reconcileCanvasDraft(sent.draft, saved);
    expect(unchanged.fields.x.raw).toBe("420");
    expect(canvasDraftHasWork(unchanged)).toBe(false);
  });

  it("keeps failed and unconfirmed inputs, and an old failure cannot cancel a new submission", () => {
    const project = source();
    const first = prepareCanvasSubmission(editCanvasField(draftFor(project), "text", "First attempt"), project, "text", "first");
    const released = releaseCanvasSubmission(first.draft, "first");
    expect(released.fields).toEqual(first.draft.fields);
    expect(released.awaiting).toBeNull();
    const second = prepareCanvasSubmission(released, project, "text", "second");
    expect(releaseCanvasSubmission(second.draft, "first")).toBe(second.draft);
    const peer = nextProject(project, { text: "Peer takes this field" });
    const observed = reconcileCanvasDraft(roundTrip(second.draft), peer);
    expect(observed.awaiting).toBeNull();
    expect(observed.fields.text.raw).toBe("First attempt");
    expect(canvasDraftConflicts(observed, peer)).toHaveLength(1);
  });

  it("never recreates a removed object or accepts a future base without explicit review", () => {
    const project = source(), draft = editCanvasField(draftFor(project), "x", "440");
    const removed = structuredClone(project); removed.scenes[0].nodes = []; removed.revision++;
    expect(() => reconcileCanvasDraft(draft, removed)).toThrow(/no longer/);
    expect(removed.scenes[0].nodes).toEqual([]);
    const older = { ...project, revision: project.revision - 1 };
    expect(canvasDraftConflicts(draft, older)).toMatchObject([{ field: "x", futureRevision: true }]);
    expect(() => prepareCanvasSubmission(draft, older, "transform", "older")).toThrow(/overlapping/);
  });

  it("respects saved property and scene locks while preserving disjoint editable fields", () => {
    const project = source(); node(project).property_locks.push("position", "text");
    let draft = editCanvasField(draftFor(project), "x", "440");
    draft = editCanvasField(draft, "text", "Kept under lock");
    expect(canvasGroupIssue(draft, project, "text")).toContain("lock");
    expect(canvasGroupIssue(draft, project, "transform")).toContain("lock");
    draft = resolveCanvasField(draft, project, "x", "saved");
    draft = editCanvasField(draft, "width", "960");
    expect(canvasGroupIssue(draft, project, "transform")).toBeNull();
    project.locks.push({ id: "scene-lock", resource: "scene:" + sceneId, kind: "position", note: "Keep layout" });
    expect(() => prepareCanvasSubmission(draft, project, "transform", "blocked")).toThrow(/lock/);
    expect(draft.fields.width.raw).toBe("960");
    expect(draft.fields.text.raw).toBe("Kept under lock");
  });
});

function reparentSource() {
  const project = source(), scene = project.scenes[0];
  const child = scene.nodes[0], oldParent = scene.nodes[1];
  const newParent = { ...structuredClone(oldParent), id: "018f0000-0000-7000-8000-000000000391", name: "New parent" };
  scene.nodes = [child, oldParent, newParent];
  project.locks = [];
  for (const item of scene.nodes) {
    item.coordinate_space = "project_pixels";
    item.property_locks = [];
    item.keyframes = [];
    item.parent_id = null;
  }
  Object.assign(oldParent, { x: 10, y: 20, width: 100, height: 80, rotation_deg: 90, opacity: 0.4 });
  Object.assign(newParent, { x: 200, y: 40, width: 40, height: 120, rotation_deg: 0, opacity: 0.9 });
  Object.assign(child, { x: 70, y: 65, width: 20, height: 10, rotation_deg: 15, opacity: 0.7, parent_id: oldParent.id, z_index: 1 });
  return { project, scene, child, oldParent, newParent };
}

function reparentDraft(project: Project, parentId: string | null, mode?: "world" | "local") {
  let draft = editCanvasField(draftFor(project), "parent_id", parentId ?? "", project);
  draft = editCanvasField(draft, "z_index", "8", project);
  return mode === undefined ? draft : editCanvasReparentPolicy(draft, project, mode);
}

function advancedReparentProject(project: Project) {
  const next = structuredClone(project); next.revision++;
  const branch = next.branches.find(item => item.id === next.active_branch);
  if (!branch) throw new Error("The source fixture has no active branch");
  branch.head_revision = next.revision;
  return next;
}

describe("Canvas reparent policy preserves original intent and invocation authority", () => {
  it("maps the child center through the direct top-left parent frames without inheriting ancestors", () => {
    const { project, scene, child, oldParent, newParent } = reparentSource();
    const ancestor = { ...structuredClone(newParent), id: "018f0000-0000-7000-8000-000000000392", x: 900, y: -700, rotation_deg: -63 };
    scene.nodes.push(ancestor);
    oldParent.parent_id = ancestor.id;
    newParent.parent_id = ancestor.id;
    const original = structuredClone(project);
    const proposal = proposeCanvasReparent(project, scene.id, child.id, newParent.id, "local");
    // Fp=T(x,y)T(w/2,h/2)R(theta)T(-w/2,-h/2). The old local
    // center is (60,20), so the new project center is (260,60).
    expect(proposal.pose).toEqual({ x: 250, y: 55, width: 20, height: 10, rotation_deg: -75, opacity: 0.7 });
    expect(proposal.positionChanged).toBe(true);
    expect(proposal.rotationChanged).toBe(true);
    expect(project).toEqual(original);
  });

  it("keeps implicit and explicit world geometry exact while only an explicit selector adds the wire policy", () => {
    const { project, scene, child, newParent } = reparentSource();
    child.coordinate_space = "normalized";
    child.keyframes = [{ at: { num: "1", den: "1" }, property: "x", value: 99, interpolation: "linear" }];
    const original = structuredClone(project);
    for (const mode of [undefined, "world"] as const) {
      const proposal = proposeCanvasReparent(project, scene.id, child.id, newParent.id, mode);
      expect(proposal.pose).toEqual({ x: 70, y: 65, width: 20, height: 10, rotation_deg: 15, opacity: 0.7 });
      expect([proposal.positionChanged, proposal.rotationChanged]).toEqual([false, false]);
      const prepared = prepareCanvasSubmission(reparentDraft(project, newParent.id, mode), project, "hierarchy", "world-" + mode);
      expect(prepared.change).toEqual({ type: "reparent_canvas_node", scene_id: scene.id, node_id: child.id,
        parent_id: newParent.id, z_index: 8, ...(mode === undefined ? {} : { preserve: mode }) });
      expect(prepared.submission.reparent?.pose).toEqual(proposal.pose);
      expect(roundTrip(prepared.draft).awaiting).toEqual(prepared.submission);
    }
    expect(project).toEqual(original);
  });

  it("uses root identity and exact same-frame identity without introducing false geometry locks", () => {
    const { project, scene, child, oldParent, newParent } = reparentSource();
    child.parent_id = null;
    Object.assign(newParent, { x: 30, y: -20, rotation_deg: 0 });
    expect(proposeCanvasReparent(project, scene.id, child.id, newParent.id, "local").pose)
      .toEqual({ x: 100, y: 45, width: 20, height: 10, rotation_deg: 15, opacity: 0.7 });
    child.parent_id = newParent.id;
    expect(proposeCanvasReparent(project, scene.id, child.id, null, "local").pose)
      .toEqual({ x: 40, y: 85, width: 20, height: 10, rotation_deg: 15, opacity: 0.7 });
    child.property_locks.push("position", "rotation");
    const unchanged = { x: child.x, y: child.y, width: child.width, height: child.height,
      rotation_deg: child.rotation_deg, opacity: child.opacity };
    expect(proposeCanvasReparent(project, scene.id, child.id, newParent.id, "local").pose).toEqual(unchanged);
    Object.assign(oldParent, { x: newParent.x, y: newParent.y, width: newParent.width, height: newParent.height,
      rotation_deg: newParent.rotation_deg });
    expect(proposeCanvasReparent(project, scene.id, child.id, oldParent.id, "local").pose).toEqual(unchanged);
  });

  it("refuses unsupported local spaces, geometric motion, invalid policies and non-finite results without mutation", () => {
    for (const role of [0, 1, 2]) for (const space of ["normalized", "scene_local"] as const) {
      const { project, scene, child, newParent } = reparentSource();
      scene.nodes[role].coordinate_space = space;
      const original = structuredClone(project);
      expect(() => proposeCanvasReparent(project, scene.id, child.id, newParent.id, "local")).toThrow(/space|project_pixels/);
      expect(project).toEqual(original);
    }
    for (const role of [0, 1, 2]) for (const property of ["x", "y", "width", "height", "rotation_deg"] as const) {
      const { project, scene, child, newParent } = reparentSource();
      scene.nodes[role].keyframes.push({ at: { num: "1", den: "1" }, property, value: 5, interpolation: "hold" });
      const original = structuredClone(project);
      expect(() => proposeCanvasReparent(project, scene.id, child.id, newParent.id, "local")).toThrow(/motion|keyframe/);
      expect(project).toEqual(original);
    }
    const { project, scene, child, newParent } = reparentSource();
    for (const item of scene.nodes) item.keyframes.push({ at: { num: "1", den: "1" }, property: "opacity", value: 0.5, interpolation: "linear" });
    expect(proposeCanvasReparent(project, scene.id, child.id, newParent.id, "local").pose.x).toBe(250);
    for (const invalid of [null, "auto", ["local"]]) {
      expect(() => proposeCanvasReparent(project, scene.id, child.id, newParent.id, invalid as never)).toThrow(/policy/);
    }
    newParent.x = Number.MAX_VALUE; newParent.width = Number.MAX_VALUE;
    expect(() => proposeCanvasReparent(project, scene.id, child.id, newParent.id, "local")).toThrow(/finite/);
  });

  it("checks Position and Rotation only for the resulting changed properties and always retains hierarchy locks", () => {
    const { project, scene, child, oldParent, newParent } = reparentSource();
    Object.assign(oldParent, { x: 0, y: 0, width: 40, height: 40, rotation_deg: 0 });
    Object.assign(newParent, { x: 0, y: 0, width: 40, height: 40, rotation_deg: 90 });
    Object.assign(child, { x: 10, y: 15, width: 20, height: 10, rotation_deg: 0 });
    child.property_locks = ["position"];
    expect(proposeCanvasReparent(project, scene.id, child.id, newParent.id, "local")).toMatchObject({ positionChanged: false, rotationChanged: true });
    child.property_locks = ["rotation"];
    expect(() => proposeCanvasReparent(project, scene.id, child.id, newParent.id, "local")).toThrow(/lock/);
    newParent.rotation_deg = 0; newParent.x = 5;
    expect(proposeCanvasReparent(project, scene.id, child.id, newParent.id, "local")).toMatchObject({ positionChanged: true, rotationChanged: false });
    for (const lock of ["position", "parent", "order"] as const) {
      child.property_locks = [lock];
      expect(() => proposeCanvasReparent(project, scene.id, child.id, newParent.id, "local")).toThrow(/lock/);
    }
    child.property_locks = [];
    for (const kind of ["content", "position"] as const) {
      project.locks = [{ id: "reparent-lock", resource: "scene:" + scene.id, kind, note: "Authored lock" }];
      expect(() => proposeCanvasReparent(project, scene.id, child.id, newParent.id, "local")).toThrow(/lock/);
    }
  });

  it("keeps raw Z and captured child and parent frames across recovery and rejects each frame drift before preparation", () => {
    const { project, scene, child, newParent } = reparentSource();
    const draft = reparentDraft(project, newParent.id, "local");
    const retained = roundTrip(editCanvasField(draft, "z_index", "1e", project));
    expect(retained.fields.z_index.raw).toBe("1e");
    expect(retained.reparent).toEqual(draft.reparent && { ...draft.reparent, sequence: draft.reparent.sequence + 1 });
    expect(() => prepareCanvasSubmission(retained, project, "hierarchy", "incomplete")).toThrow(/Z order/);
    for (const role of [0, 1, 2]) {
      const peer = advancedReparentProject(project);
      peer.scenes[0].nodes[role].x += 1;
      const observed = reconcileCanvasDraft(roundTrip(draft), peer);
      expect(observed.reparent).toEqual(draft.reparent);
      expect(observed.fields.parent_id.raw).toBe(newParent.id);
      expect(() => prepareCanvasSubmission(observed, peer, "hierarchy", "drift-" + role)).toThrow(/context|frame|pose/);
      const reviewed = reviewCanvasContext(observed, peer);
      expect(prepareCanvasSubmission(reviewed, peer, "hierarchy", "reviewed-" + role).change).toMatchObject({ preserve: "local" });
    }
    const disjoint = advancedReparentProject(project);
    disjoint.scenes[0].nodes[0].text = "A separate authored sentence";
    disjoint.scenes[0].nodes[1].style.fill = "#456789";
    expect(prepareCanvasSubmission(reconcileCanvasDraft(draft, disjoint), disjoint, "hierarchy", "disjoint").change)
      .toMatchObject({ scene_id: scene.id, node_id: child.id, preserve: "local" });
  });

  it("does not retrofit frames or policy into legacy UNKNOWN copies and refuses unknown new metadata", () => {
    const { project, newParent } = reparentSource();
    const prepared = prepareCanvasSubmission(reparentDraft(project, newParent.id), project, "hierarchy", "legacy-pending");
    const legacy = structuredClone(prepared.draft);
    legacy.schema = "motionwright.canvas-draft/5";
    delete legacy.reparent; delete legacy.awaiting!.reparent;
    const legacyRaw = JSON.stringify(copy(legacy));
    const decoded = decodeCanvasRecovery(legacyRaw, legacy.scope, "window-a").draft!;
    const later = advancedReparentProject(project);
    later.scenes[0].nodes[0].parent_id = newParent.id; later.scenes[0].nodes[0].z_index = 8;
    const observed = reconcileCanvasDraft(decoded, later);
    expect(observed.awaiting).toEqual(legacy.awaiting);
    expect(observed.reparent).toBeUndefined();
    expect(observed.awaiting?.reparent).toBeUndefined();
    expect(acknowledgeCanvasReparent(observed, later, legacy.awaiting!)).toBe(observed);
    expect(legacyRaw).toBe(JSON.stringify(copy(legacy)));
    for (const mutate of [
      (value: Record<string, unknown>) => { value.preserve = null; },
      (value: Record<string, unknown>) => { value.preserve = ["local"]; },
      (value: Record<string, unknown>) => { value.silentFallback = "world"; },
    ]) {
      const damaged = structuredClone(prepared.draft);
      mutate(damaged.awaiting!.reparent as unknown as Record<string, unknown>);
      expect(() => roundTrip(damaged)).toThrow(/reparent|policy|snapshot/);
    }
  });

  it("requires the original scoped success as well as exact geometry and retains later incomplete author input", () => {
    const { project, scene, child, newParent } = reparentSource();
    const sent = prepareCanvasSubmission(reparentDraft(project, newParent.id, "local"), project, "hierarchy", "original-invocation");
    const payload = JSON.stringify(sent.submission);
    let later = editCanvasField(sent.draft, "z_index", "1e", project);
    later = editCanvasField(later, "text", "  Later authored line\n");
    const returned = advancedReparentProject(project);
    Object.assign(returned.scenes[0].nodes[0], { ...sent.submission.reparent!.pose, parent_id: newParent.id, z_index: 8 });
    const observed = reconcileCanvasDraft(roundTrip(later), returned);
    expect(canvasSubmissionMatches(returned, observed, sent.submission)).toBe(true);
    expect(observed.awaiting).toEqual(sent.submission);
    expect(() => prepareCanvasSubmission(observed, returned, "hierarchy", "peer-match-replay")).toThrow(/unconfirmed/);
    expect(acknowledgeCanvasReparent(observed, returned, { ...sent.submission, id: "another-invocation" })).toBe(observed);
    for (const mutate of [
      (value: Project) => { value.generation += "-foreign"; },
      (value: Project) => { value.revision++; value.branches.find(branch => branch.id === value.active_branch)!.head_revision = value.revision; },
      (value: Project) => { value.scenes[0].nodes[0].x += 1; },
      (value: Project) => { value.scenes[0].nodes[0].rotation_deg += 1; },
      (value: Project) => { value.scenes[0].nodes[0].parent_id = null; },
      (value: Project) => { value.scenes[0].nodes[0].z_index++; },
      (value: Project) => { value.scenes[0].nodes[0].width += 1; },
      (value: Project) => { value.scenes[0].nodes[0].height += 1; },
      (value: Project) => { value.scenes[0].nodes[0].opacity -= 0.1; },
      (value: Project) => { value.scenes[0].nodes[0].coordinate_space = "normalized"; },
      (value: Project) => { value.scenes[0].nodes[0].text = "Unexpected extra text effect"; },
      (value: Project) => { value.scenes[0].nodes[0].style.fill = "#ABCDEF"; },
      (value: Project) => { value.scenes[0].nodes[0].keyframes.push({ at: { num: "1", den: "1" }, property: "opacity", value: 0.2, interpolation: "hold" }); },
    ]) {
      const wrong = structuredClone(returned); mutate(wrong);
      expect(acknowledgeCanvasReparent(observed, wrong, sent.submission)).toBe(observed);
    }
    const alteredPayload = structuredClone(observed);
    alteredPayload.awaiting!.fields.z_index = { ...alteredPayload.awaiting!.fields.z_index!, raw: "9", value: "9" };
    const alteredReply = structuredClone(returned); alteredReply.scenes[0].nodes[0].z_index = 9;
    expect(acknowledgeCanvasReparent(alteredPayload, alteredReply, sent.submission)).toBe(alteredPayload);
    const acknowledged = acknowledgeCanvasReparent(observed, returned, sent.submission);
    expect(acknowledged.awaiting).toBeNull();
    expect(acknowledged.fields.z_index.raw).toBe("1e");
    expect(acknowledged.fields.text.raw).toBe("  Later authored line\n");
    expect(JSON.stringify(sent.submission)).toBe(payload);
    expect(project.scenes[0].nodes[0]).toEqual(child);
    expect(acknowledged.scope).toBe(canvasDraftScope(project, scene.id, child.id));
  });

  it("uses the declared bound only for original local X and Y across non-cardinal, tiny and large finite frames", () => {
    for (const factor of [1, 1e-12, 1e100]) {
      const { project, scene, child, oldParent, newParent } = reparentSource();
      for (const item of scene.nodes) for (const field of ["x", "y", "width", "height"] as const) item[field] *= factor;
      oldParent.rotation_deg = 17; newParent.rotation_deg = -31;
      const sent = prepareCanvasSubmission(reparentDraft(project, newParent.id, "local"), project, "hierarchy", "non-cardinal-" + factor);
      const predicted = sent.submission.reparent!.pose;
      expect(predicted.rotation_deg).toBe(-33);
      expect([predicted.width, predicted.height, predicted.opacity]).toEqual([child.width, child.height, child.opacity]);
      const frozenTerms = [1, predicted.x, predicted.y];
      for (const item of [child, oldParent, newParent]) frozenTerms.push(item.x, item.y, item.width, item.height);
      const bound = 1e-9 + 64 * Number.EPSILON * Math.max(...frozenTerms.map(Math.abs));
      const returned = advancedReparentProject(project);
      Object.assign(returned.scenes[0].nodes[0], { ...predicted, parent_id: newParent.id, z_index: 8,
        x: predicted.x + bound / 2, y: predicted.y - bound / 2 });
      const originalReturn = structuredClone(returned);
      expect(canvasSubmissionMatches(returned, sent.draft, sent.submission)).toBe(false);
      expect(reconcileCanvasDraft(sent.draft, returned).awaiting).toEqual(sent.submission);
      const accepted = acknowledgeCanvasReparent(sent.draft, returned, sent.submission);
      expect(accepted.awaiting).toBeNull();
      expect(accepted.fields.x.base).toBe(String(returned.scenes[0].nodes[0].x));
      expect(accepted.fields.y.raw).toBe(String(returned.scenes[0].nodes[0].y));
      expect(returned).toEqual(originalReturn);
      for (const field of ["x", "y"] as const) {
        const outside = structuredClone(returned); outside.scenes[0].nodes[0][field] = predicted[field] + 4 * bound;
        expect(acknowledgeCanvasReparent(sent.draft, outside, sent.submission)).toBe(sent.draft);
        outside.scenes[0].nodes[0][field] = Infinity;
        expect(acknowledgeCanvasReparent(sent.draft, outside, sent.submission)).toBe(sent.draft);
      }
      if (factor === 1) for (const field of ["rotation_deg", "width", "height", "opacity"] as const) {
        const changedInvariant = structuredClone(returned);
        changedInvariant.scenes[0].nodes[0][field] += 1e-10;
        expect(acknowledgeCanvasReparent(sent.draft, changedInvariant, sent.submission)).toBe(sent.draft);
      }
    }
    for (const mode of [undefined, "world", "local"] as const) {
      const { project, child, oldParent, newParent } = reparentSource();
      const parent = mode === "local" ? oldParent : newParent;
      const sent = prepareCanvasSubmission(reparentDraft(project, parent.id, mode), project, "hierarchy", "exact-" + mode);
      const returned = advancedReparentProject(project);
      Object.assign(returned.scenes[0].nodes[0], { parent_id: parent.id, z_index: 8, x: child.x + 5e-10 });
      expect(acknowledgeCanvasReparent(sent.draft, returned, sent.submission)).toBe(sent.draft);
    }
  });

  it("blocks same-revision UNKNOWN review and preserves original bytes while an advanced revision starts a separate explicit intent", () => {
    const { project, newParent } = reparentSource();
    const sent = prepareCanvasSubmission(reparentDraft(project, newParent.id, "local"), project, "hierarchy", "held-original");
    const storage = new MemoryStorage();
    const original = retain(storage, copy(sent.draft, "window-a", 1));
    const originalKey = canvasRecoveryKey(sent.draft.scope, "window-a");
    expect(canvasHierarchyReviewIssue(sent.draft, project)).toMatch(/later|revision/);
    expect(() => reviewCanvasHierarchySubmission(sent.draft, project)).toThrow(/later|revision/);
    expect(() => discardCanvasRecovery(storage, originalKey, original)).toThrow(/unconfirmed/);
    expect(storage.getItem(originalKey)).toBe(original);
    expect(reconcileCanvasDraft(roundTrip(sent.draft), project).awaiting).toEqual(sent.submission);
    // Confirming an unchanged pending copy must not rewrite updatedAt. The
    // caller separately confirms its session pointer before it may fork.
    expect(retain(storage, copy(roundTrip(sent.draft), "window-a", 9999))).toBe(original);
    const peer = advancedReparentProject(project); peer.title += " peer revision";
    const reviewed = reviewCanvasHierarchySubmission(roundTrip(sent.draft), peer);
    expect(reviewed.awaiting).toBeNull();
    expect(reviewed.fields.parent_id.raw).toBe(newParent.id);
    expect(reviewed.reparent?.preserve).toBe("local");
    retain(storage, copy(reviewed, "separate-intent", 10000));
    expect(storage.getItem(originalKey)).toBe(original);
    expect(storage.length).toBe(2);
    expect(readCanvasRecoveries(storage, sent.draft.scope).entries.find(entry => entry.key === originalKey)?.value.draft?.awaiting)
      .toEqual(sent.submission);
    storage.data.set(originalKey, original + " ");
    expect(() => retain(storage, copy(sent.draft, "window-a", 10001))).toThrow(/changed/);
    expect(storage.getItem(originalKey)).toBe(original + " ");
  });
});

describe("bounded Canvas recovery storage", () => {
  it("compares the last observed bytes before replacing or discarding even a valid externally changed copy", () => {
    const storage = new MemoryStorage(), draft = editCanvasField(draftFor(source()), "text", "Window A's input");
    const observedRaw = retain(storage, copy(draft)), key = canvasRecoveryKey(draft.scope, "window-a");
    const otherRaw = JSON.stringify(copy(editCanvasField(draft, "text", "A valid external correction"), "window-a", 99));
    storage.setItem(key, otherRaw);
    expect(() => writeCanvasRecovery(storage, copy(editCanvasField(draft, "text", "A later local edit")), observedRaw)).toThrow(/changed in another window/);
    expect(storage.getItem(key)).toBe(otherRaw);
    expect(() => discardCanvasRecovery(storage, key, observedRaw)).toThrow(/changed after it was shown/);
    expect(storage.getItem(key)).toBe(otherRaw);
    expect(readCanvasRecoveries(storage, draft.scope).entries[0].raw).toBe(otherRaw);
    const brokenRaw = "{invalid recovery\nKeep every original byte 🌘";
    storage.setItem(key, brokenRaw);
    expect(readCanvasRecoveries(storage, draft.scope).unreadable[0].raw).toBe(brokenRaw);
    expect(() => discardCanvasRecovery(storage, key, otherRaw)).toThrow(/changed after/);
    discardCanvasRecovery(storage, key, brokenRaw);
    expect(storage.getItem(key)).toBeNull();
  });

  it("keeps independent windows and uses durable clean checkpoints without resurrecting stale drafts", () => {
    const storage = new MemoryStorage(), draft = editCanvasField(draftFor(source()), "text", "A window's input");
    retain(storage, copy(draft, "window-a", 100));
    const other = editCanvasField(draft, "text", "B window's independent input");
    retain(storage, copy(other, "window-b", 100));
    const beforeA = storage.getItem(canvasRecoveryKey(draft.scope, "window-a"));
    retain(storage, copy(null, "window-b", 1));
    expect(storage.getItem(canvasRecoveryKey(draft.scope, "window-a"))).toBe(beforeA);
    expect(readCanvasRecoveries(storage, draft.scope).entries[0].value.draft).toBeNull();
    expect(() => retain(storage, copy(other, "window-b"))).toThrow(/immutable/);
    retain(storage, copy(other, "new-window-b"));
    expect(readCanvasRecoveries(storage, draft.scope).entries[0].value.draft?.fields.text.raw).toBe("B window's independent input");
    expect(storage.getItem(canvasRecoveryKey(draft.scope, "window-a"))).toBe(beforeA);
  });

  it("refuses corrupt, unsupported, forged or oversized copies without replacing their bytes", () => {
    const storage = new MemoryStorage(), draft = editCanvasField(draftFor(source()), "x", "1e");
    const key = canvasRecoveryKey(draft.scope, "window-a"), bad = "{unreadable original input";
    storage.setItem(key, bad);
    expect(readCanvasRecoveries(storage, draft.scope).unreadable).toEqual([{ key, raw: bad, writerId: "window-a" }]);
    expect(() => retain(storage, copy(draft))).toThrow();
    expect(storage.getItem(key)).toBe(bad);
    const oversized = copy(editCanvasField(draft, "text", "🌘".repeat(CANVAS_COPY_BYTES)));
    expect(() => decodeCanvasRecovery(JSON.stringify(oversized), draft.scope, "window-a")).toThrow(/1 MiB/);
    const forged = { ...copy(draft), draft: { ...draft, fields: { ...draft.fields, x: { ...draft.fields.x, sequence: -1 } } } };
    expect(() => decodeCanvasRecovery(JSON.stringify(forged), draft.scope, "window-a")).toThrow(/invalid field/);
    const valid = { ...copy(draft), effect_grant: "never-authority", change: { type: "delete_project" } };
    expect(decodeCanvasRecovery(JSON.stringify(valid), draft.scope, "window-a")).not.toHaveProperty("effect_grant");
    expect(decodeCanvasRecovery(JSON.stringify(valid), draft.scope, "window-a")).not.toHaveProperty("change");
    const unknownScopeKey = canvasRecoveryKey("{}", "unknown-window");
    storage.setItem(unknownScopeKey, "Unassignable original bytes");
    expect(readCanvasRecoveries(storage, draft.scope).unreadable).toContainEqual({
      key: unknownScopeKey, raw: "Unassignable original bytes", writerId: "unidentified-1",
    });
  });

  it("keeps the last durable input during quota failure and never evicts pending work at the copy bound", () => {
    const storage = new MemoryStorage(), draft = editCanvasField(draftFor(source()), "text", "Durable input");
    retain(storage, copy(draft));
    const durable = new Map(storage.data); storage.failWrites = true;
    expect(() => retain(storage, copy(editCanvasField(draft, "text", "New input still in memory")))).toThrow(/Quota/);
    expect(storage.data).toEqual(durable); storage.failWrites = false;
    for (let index = 1; index < CANVAS_COPY_LIMIT; index++) retain(storage, copy(draft, "window-" + index));
    const full = new Map(storage.data);
    expect(() => retain(storage, copy(draft, "beyond-the-bound"))).toThrow(/full/);
    expect(storage.data).toEqual(full);
    storage.setItem(CANVAS_DRAFT_PREFIX + "unscoped-extra", "unreadable input");
    const result = readCanvasRecoveries(storage, draft.scope);
    expect(result.limited).toBe(true);
    expect(storage.length).toBe(CANVAS_COPY_LIMIT + 1);
  });

  it("bounds aggregate bytes without discarding another project's input", () => {
    const storage = new MemoryStorage();
    const draft = editCanvasField(draftFor(source()), "text", "x".repeat(110_000));
    let full = false;
    for (let index = 0; index < CANVAS_COPY_LIMIT; index++) {
      const before = new Map(storage.data);
      try { retain(storage, copy(draft, "large-window-" + index)); }
      catch (reason) {
        expect(String(reason)).toContain("4 MiB"); expect(storage.data).toEqual(before); full = true; break;
      }
    }
    expect(full).toBe(true); expect(storage.length).toBeGreaterThan(20); expect(storage.length).toBeLessThan(CANVAS_COPY_LIMIT);
  });
});

describe("Canvas recovery writes are atomic", () => {
  const seeded = () => {
    const storage = new MemoryStorage(), base = draftFor(source());
    const older = copy(null, "older-clean", 10), newer = copy(null, "newer-clean", 11);
    storage.setItem(canvasRecoveryKey(older.scope, older.writerId), JSON.stringify(older));
    storage.setItem(canvasRecoveryKey(newer.scope, newer.writerId), JSON.stringify(newer));
    return { storage, base, older, newer };
  };

  it("does not remove old clean copies before a rejected write", () => {
    const { storage, base, older, newer } = seeded();
    const before = new Map(storage.data);
    storage.failWrites = true;
    const attempted = copy(editCanvasField(base, "text", "A human decision"), "new-author", 12);
    expect(() => retain(storage, attempted)).toThrow(/QuotaExceededError/);
    expect(storage.data).toEqual(before);
    expect(storage.getItem(canvasRecoveryKey(older.scope, older.writerId))).toBe(JSON.stringify(older));
    expect(storage.getItem(canvasRecoveryKey(newer.scope, newer.writerId))).toBe(JSON.stringify(newer));
  });

  it("retires only same-scope old clean copies after confirmed clean replacement", () => {
    const { storage, base, older, newer } = seeded();
    const replacement = copy(null, "new-clean", 20), key = canvasRecoveryKey(replacement.scope, replacement.writerId);
    const originalSet = storage.setItem.bind(storage);
    storage.setItem = (path, raw) => {
      if (path === key) {
        expect(storage.getItem(canvasRecoveryKey(older.scope, older.writerId))).not.toBeNull();
        expect(storage.getItem(canvasRecoveryKey(newer.scope, newer.writerId))).not.toBeNull();
      }
      originalSet(path, raw);
    };
    const retained = retain(storage, replacement);
    expect(storage.getItem(key)).toBe(retained);
    expect(storage.getItem(canvasRecoveryKey(older.scope, older.writerId))).toBeNull();
    expect(storage.getItem(canvasRecoveryKey(newer.scope, newer.writerId))).toBeNull();
    expect(readCanvasRecoveries(storage, base.scope).entries[0].value.draft).toBeNull();
  });

  it("preserves every existing copy when storage silently fails to write", () => {
    const { storage, base } = seeded();
    const before = new Map(storage.data), attempted = copy(editCanvasField(base, "text", "Kept input"), "silent-writer", 21);
    const key = canvasRecoveryKey(attempted.scope, attempted.writerId);
    const originalSet = storage.setItem.bind(storage);
    storage.setItem = (path, raw) => { if (path !== key) originalSet(path, raw); };
    expect(() => retain(storage, attempted)).toThrow(/could not be verified/);
    expect(storage.data).toEqual(before);
  });

  it("does not evict 128 clean copies to hide a capacity error", () => {
    const storage = new MemoryStorage(), base = draftFor(source());
    for (let index = 0; index < CANVAS_COPY_LIMIT; index++) {
      const saved = copy(null, "old-clean-" + index, index);
      storage.setItem(canvasRecoveryKey(saved.scope, saved.writerId), JSON.stringify(saved));
    }
    const before = new Map(storage.data);
    expect(() => retain(storage, copy(editCanvasField(base, "text", "New work"), "newest", 129)))
      .toThrow(/full/);
    expect(storage.data).toEqual(before);
  });
});

describe("Canvas session recovery pointer admission", () => {
  it("confirms the chosen writer or fails closed on silent and thrown writes", async () => {
    const { writeCanvasSessionPointer } = await import("./canvasWorkspaceDraft");
    const entries = new Map<string, string>();
    const read = (key: string) => entries.get(key) ?? null;
    const working = { getItem: read, setItem: (key: string, value: string) => { entries.set(key, value); } };
    writeCanvasSessionPointer(working, "canvas-session", "window-1");
    expect(read("canvas-session")).toBe("window-1");
    expect(() => writeCanvasSessionPointer({ getItem: read, setItem: () => {} }, "canvas-session", "window-2"))
      .toThrow(/not confirmed/);
    expect(read("canvas-session")).toBe("window-1");
    expect(() => writeCanvasSessionPointer({ getItem: read, setItem: () => { throw new Error("session denied"); } },
      "canvas-session", "window-2")).toThrow(/session denied/);
    expect(read("canvas-session")).toBe("window-1");
  });
});

describe("Canvas style uses one recoverable, revision-bound authoring session", () => {
  it("preserves fill, typography and exact raw values through ten other author revisions", async () => {
    const { CANVAS_STYLE_FIELDS, canvasGroupIssue } = await import("./canvasWorkspaceDraft");
    let project = structuredClone(fixtureProject);
    const scene = project.scenes[0], first = scene.nodes[0];
    let draft = createCanvasDraft(project, scene.id, first.id);
    for (const [field, raw] of Object.entries({ fill: "#F7EAD7", font_size: "55", line_height: "1.35", font_weight: "600" })) {
      draft = editCanvasField(draft, field as typeof CANVAS_STYLE_FIELDS[number], raw);
    }
    for (let index = 0; index < 10; index++) {
      project = structuredClone(project); project.revision++;
      project.scenes[0].nodes[0].text = "External copy " + index;
      draft = reconcileCanvasDraft(draft, project);
    }
    expect(draft.fields.fill.raw).toBe("#F7EAD7");
    expect(draft.fields.font_size.raw).toBe("55");
    expect(draft.fields.line_height.raw).toBe("1.35");
    expect(canvasGroupIssue(draft, project, "style")).toBeNull();
    const saved = prepareCanvasSubmission(draft, project, "style", "style-10").change;
    expect(saved.type).toBe("update_canvas_style");
    if (saved.type === "update_canvas_style") expect(saved.style).toMatchObject({ fill: "#F7EAD7", font_size: 55, line_height: 1.35 });
  });

  it("rejects partial typography and composes unrelated saved style changes", async () => {
    const { canvasGroupIssue } = await import("./canvasWorkspaceDraft");
    let project = structuredClone(fixtureProject);
    let draft = editCanvasField(createCanvasDraft(project, project.scenes[0].id, project.scenes[0].nodes[0].id), "font_size", "1e");
    expect(canvasGroupIssue(draft, project, "style")).toContain("Font size");
    expect(() => prepareCanvasSubmission(draft, project, "style", "no")).toThrow();
    draft = editCanvasField(draft, "font_size", "63");
    project = structuredClone(project); project.revision++; project.scenes[0].nodes[0].style.stroke = "#AA3412";
    draft = reconcileCanvasDraft(draft, project);
    expect(canvasGroupIssue(draft, project, "style")).toBeNull();
    const change = prepareCanvasSubmission(draft, project, "style", "composed").change;
    if (change.type === "update_canvas_style") expect(change.style).toMatchObject({ stroke: "#AA3412", font_size: 63 });
  });

  it("keeps a later human style decision through the saved acknowledgement", async () => {
    const { canvasSubmissionMatches } = await import("./canvasWorkspaceDraft");
    let project = structuredClone(fixtureProject);
    let draft = editCanvasField(createCanvasDraft(project, project.scenes[0].id, project.scenes[0].nodes[0].id), "fill", "#132435");
    const submitted = prepareCanvasSubmission(draft, project, "style", "style-attempt");
    draft = editCanvasField(submitted.draft, "fill", "#ABCDEF");
    const saved = structuredClone(project); saved.revision++;
    if (submitted.change.type !== "update_canvas_style") throw new Error("Expected style");
    saved.scenes[0].nodes[0].style = submitted.change.style;
    expect(canvasSubmissionMatches(saved, draft, submitted.submission)).toBe(true);
    draft = reconcileCanvasDraft(draft, saved);
    expect(draft.awaiting).toBeNull();
    expect(draft.fields.fill.raw).toBe("#ABCDEF");
    const next = prepareCanvasSubmission(draft, saved, "style", "later");
    if (next.change.type === "update_canvas_style") expect(next.change.style.fill).toBe("#ABCDEF");
  });

  it("prevents overwriting an overlapping peer fill without explicit review", async () => {
    const { canvasGroupIssue, canvasDraftConflicts, resolveCanvasField } = await import("./canvasWorkspaceDraft");
    let project = structuredClone(fixtureProject);
    let draft = editCanvasField(createCanvasDraft(project, project.scenes[0].id, project.scenes[0].nodes[0].id), "fill", "#123456");
    project = structuredClone(project);project.revision++;project.scenes[0].nodes[0].style.fill = "#888888";
    draft = reconcileCanvasDraft(draft, project);
    expect(canvasDraftConflicts(draft,project).some(c => c.field === "fill")).toBe(true);
    expect(canvasGroupIssue(draft,project,"style")).toContain("overlapping");
    expect(() => prepareCanvasSubmission(draft,project,"style","blocked")).toThrow();
    draft = resolveCanvasField(draft,project,"fill","draft");
    const committed = prepareCanvasSubmission(draft,project,"style","chosen");
    if (committed.change.type === "update_canvas_style") expect(committed.change.style.fill).toBe("#123456");
  });

  it("keeps optional shape typography null instead of silently converting to zero", async () => {
    const { canvasGroupIssue } = await import("./canvasWorkspaceDraft");
    const project = structuredClone(fixtureProject), shape = project.scenes[0].nodes.find(node => node.kind === "shape")!;
    let draft = createCanvasDraft(project,project.scenes[0].id,shape.id);
    expect(canvasGroupIssue(draft,project,"style")).toContain("already match");
    draft = editCanvasField(draft,"stroke","#AE2177");
    const change = prepareCanvasSubmission(draft,project,"style","shape").change;
    expect(change.type).toBe("update_canvas_style");
    if(change.type === "update_canvas_style") {
      expect(change.style.stroke).toBe("#AE2177");
      expect(change.style.font_size).toBeNull();
      expect(change.style.line_height).toBeNull();
      expect(change.style.font_weight).toBeNull();
    }
  });

  it("upgrades valid old v1 Canvas copies without dropping text and numeric drafts", async () => {
    const { CANVAS_STYLE_FIELDS } = await import("./canvasWorkspaceDraft");
    const project = structuredClone(fixtureProject);
    let draft = editCanvasField(createCanvasDraft(project,project.scenes[0].id,project.scenes[0].nodes[0].id),"text","Kept legacy content");
    draft = editCanvasField(draft,"x","345");
    const legacy = structuredClone(draft);
    legacy.schema = "motionwright.canvas-draft/1";
    for (const field of CANVAS_STYLE_FIELDS) delete (legacy.fields as Partial<typeof draft.fields>)[field];
    const scope = draft.scope, raw = JSON.stringify({schema:"motionwright.canvas-recovery/1",scope,writerId:"old",updatedAt:1,draft:legacy});
    const decoded = decodeCanvasRecovery(raw,scope,"old").draft!;
    const migrated = reconcileCanvasDraft(decoded,project);
    expect(migrated.schema).toBe("motionwright.canvas-draft/5");
    expect(migrated.fields.text.raw).toBe("Kept legacy content");
    expect(migrated.fields.x.raw).toBe("345");
    expect(migrated.fields.font_size.raw).toBe(String(project.scenes[0].nodes[0].style.font_size));
  });
});

describe("Canvas style lock authority", () => {
  it("rejects locked style without losing the author's draft", async () => {
    const { canvasGroupIssue } = await import("./canvasWorkspaceDraft");
    const original = structuredClone(fixtureProject);
    const scene = original.scenes[0], node = scene.nodes[0];
    const draft = editCanvasField(createCanvasDraft(original,scene.id,node.id),"fill","#F7EAD7");
    const withSceneLock = structuredClone(original);
    withSceneLock.locks.push({id:"style-scene-lock",resource:"scene:"+scene.id,kind:"style",note:"Approved art direction"});
    expect(canvasGroupIssue(draft,withSceneLock,"style")).toContain("lock");
    expect(() => prepareCanvasSubmission(draft,withSceneLock,"style","blocked")).toThrow();
    const withNodeLock = structuredClone(original);
    withNodeLock.scenes[0].nodes[0].property_locks.push("style");
    expect(canvasGroupIssue(draft,withNodeLock,"style")).toContain("lock");
    expect(draft.fields.fill.raw).toBe("#F7EAD7");
  });
});

describe("Canvas parent and Z authoring uses the same persistent revision-scoped draft", () => {
  it("retains parent and exact Z input through ten independent human text revisions", async () => {
    const { canvasGroupIssue } = await import("./canvasWorkspaceDraft");
    let project = structuredClone(fixtureProject), scene = project.scenes[0];
    const node = scene.nodes[0], parentId = scene.nodes[1].id;
    let draft = createCanvasDraft(project, scene.id, node.id);
    draft = editCanvasField(draft,"parent_id",parentId);
    draft = editCanvasField(draft,"z_index","8");
    for(let i=0;i<10;i++){
      project = structuredClone(project); project.revision++;
      project.scenes[0].nodes[0].text = "Separate author note "+i;
      draft = reconcileCanvasDraft(draft,project);
    }
    expect(draft.fields.parent_id.raw).toBe(parentId);
    expect(draft.fields.z_index.raw).toBe("8");
    expect(canvasGroupIssue(draft,project,"hierarchy")).toBeNull();
    expect(prepareCanvasSubmission(draft,project,"hierarchy","hier-10").change).toMatchObject({
      type:"reparent_canvas_node",parent_id:parentId,z_index:8,
    });
  });

  it("retains incomplete Z raw and rejects invalid parents before any service operation", async () => {
    const { canvasGroupIssue } = await import("./canvasWorkspaceDraft");
    const project = structuredClone(fixtureProject), scene = project.scenes[0], node = scene.nodes[0];
    let draft = editCanvasField(createCanvasDraft(project,scene.id,node.id),"z_index","1e");
    expect(draft.fields.z_index.raw).toBe("1e");
    expect(canvasGroupIssue(draft,project,"hierarchy")).toContain("Z order");
    expect(() => prepareCanvasSubmission(draft,project,"hierarchy","bad")).toThrow();
    draft = editCanvasField(draft,"z_index","2147483648");
    expect(canvasGroupIssue(draft,project,"hierarchy")).toContain("Z order");
    draft = editCanvasField(draft,"z_index","4");
    draft = editCanvasField(draft,"parent_id","not-a-saved-object");
    expect(canvasGroupIssue(draft,project,"hierarchy")).toContain("same saved scene");
    expect(() => prepareCanvasSubmission(draft,project,"hierarchy","invalid-parent")).toThrow();
  });

  it("requires both parent and Z property locks to be clear for the coupled Rust command", async () => {
    const { canvasGroupIssue } = await import("./canvasWorkspaceDraft");
    const source = structuredClone(fixtureProject), scene = source.scenes[0], node = scene.nodes[0];
    for(const property of ["parent","order"] as const){
      const locked = structuredClone(source);locked.scenes[0].nodes[0].property_locks.push(property);
      const draft = editCanvasField(createCanvasDraft(locked,scene.id,node.id),"z_index","7");
      expect(canvasGroupIssue(draft,locked,"hierarchy")).toContain("lock");
      expect(() => prepareCanvasSubmission(draft,locked,"hierarchy","locked")).toThrow();
    }
    const sceneLocked = structuredClone(source);
    sceneLocked.locks.push({id:"hier-lock",resource:"scene:"+scene.id,kind:"content",note:"Human review"});
    const retained = editCanvasField(createCanvasDraft(sceneLocked,scene.id,node.id),"z_index","9");
    expect(canvasGroupIssue(retained,sceneLocked,"hierarchy")).toContain("lock");
    expect(retained.fields.z_index.raw).toBe("9");
  });

  it("acknowledges the saved reparent operation but keeps a later edited Z value", async () => {
    const { canvasSubmissionMatches } = await import("./canvasWorkspaceDraft");
    const project = structuredClone(fixtureProject),scene=project.scenes[0],first=scene.nodes[0];
    let draft = createCanvasDraft(project,scene.id,first.id);
    draft = editCanvasField(draft,"parent_id",scene.nodes[1].id);
    draft = editCanvasField(draft,"z_index","6");
    const attempted = prepareCanvasSubmission(draft,project,"hierarchy","hier-ack");
    draft = editCanvasField(attempted.draft,"z_index","9");
    const saved=advancedReparentProject(project);
    if(attempted.change.type!=="reparent_canvas_node")throw new Error("Wrong command");
    saved.scenes[0].nodes[0].parent_id=attempted.change.parent_id;
    saved.scenes[0].nodes[0].z_index=attempted.change.z_index;
    expect(canvasSubmissionMatches(saved,draft,attempted.submission)).toBe(true);
    // Only the Project returned by this original successful invocation can
    // acknowledge a hierarchy operation; a peer observation is insufficient.
    expect(reconcileCanvasDraft(draft,saved).awaiting).toEqual(attempted.submission);
    draft=acknowledgeCanvasReparent(draft,saved,attempted.submission);
    expect(draft.awaiting).toBeNull();
    expect(draft.fields.z_index.raw).toBe("9");
    expect(draft.fields.parent_id.raw).toBe(scene.nodes[1].id);
    draft=reviewCanvasContext(draft,saved);
    expect(prepareCanvasSubmission(draft,saved,"hierarchy","later").change).toMatchObject({z_index:9});
  });

  it("requires explicit choice on overlapping peer Z changes and rejects parent cycles", async () => {
    const { canvasGroupIssue, canvasDraftConflicts, resolveCanvasField } = await import("./canvasWorkspaceDraft");
    let project=structuredClone(fixtureProject),scene=project.scenes[0],first=scene.nodes[0];
    let draft=editCanvasField(createCanvasDraft(project,scene.id,first.id),"z_index","9");
    project=structuredClone(project);project.revision++;project.scenes[0].nodes[0].z_index=6;
    draft=reconcileCanvasDraft(draft,project);
    expect(canvasDraftConflicts(draft,project).some(conflict=>conflict.field==="z_index")).toBe(true);
    expect(canvasGroupIssue(draft,project,"hierarchy")).toContain("overlapping");
    const reviewed=resolveCanvasField(draft,project,"z_index","draft");
    expect(prepareCanvasSubmission(reviewed,project,"hierarchy","chosen").change).toMatchObject({z_index:9});
    const cyclic=structuredClone(fixtureProject);
    cyclic.scenes[0].nodes[1].parent_id=cyclic.scenes[0].nodes[0].id;
    const candidate=editCanvasField(createCanvasDraft(cyclic,scene.id,first.id),"parent_id",cyclic.scenes[0].nodes[1].id);
    expect(canvasGroupIssue(candidate,cyclic,"hierarchy")).toContain("descendant");
  });

  it("migrates valid v2 recovery without losing text, style or its author intent", async () => {
    const { CANVAS_HIERARCHY_FIELDS } = await import("./canvasWorkspaceDraft");
    const project=structuredClone(fixtureProject),scene=project.scenes[0],first=scene.nodes[0];
    let draft=createCanvasDraft(project,scene.id,first.id);
    draft=editCanvasField(draft,"text","Kept human copy");
    draft=editCanvasField(draft,"fill","#F7EAD7");
    const previous=structuredClone(draft) as typeof draft; previous.schema="motionwright.canvas-draft/2";
    for (const field of CANVAS_HIERARCHY_FIELDS) delete (previous.fields as Partial<typeof draft.fields>)[field];
    const raw=JSON.stringify({schema:"motionwright.canvas-recovery/1",scope:draft.scope,writerId:"v2-author",updatedAt:1,draft:previous});
    const original=raw;
    const decoded=decodeCanvasRecovery(raw,draft.scope,"v2-author").draft!;
    const migrated=reconcileCanvasDraft(decoded,project);
    expect(migrated.schema).toBe("motionwright.canvas-draft/5");
    expect(migrated.fields.text.raw).toBe("Kept human copy");
    expect(migrated.fields.fill.raw).toBe("#F7EAD7");
    expect(migrated.fields.parent_id.raw).toBe(first.parent_id??"");
    expect(migrated.fields.z_index.raw).toBe(String(first.z_index));
    expect(raw).toBe(original);
  });
});

describe("Canvas relations belong to the same persistent scoped authoring session", () => {
  it("retains relation target and kind through ten independent revisions", async () => {
    const { canvasRelationIssue, prepareCanvasRelationSubmission } = await import("./canvasWorkspaceDraft");
    let project = structuredClone(fixtureProject),scene=project.scenes[0];
    const node=scene.nodes[0],other=scene.nodes[1];
    let draft=createCanvasDraft(project,scene.id,node.id);
    draft=editCanvasField(draft,"relation_kind","align_right");
    draft=editCanvasField(draft,"relation_target",other.id);
    for(let i=0;i<10;i++) {
      project=structuredClone(project);project.revision++;
      project.scenes[0].nodes[0].text="Independent revision "+i;
      draft=reconcileCanvasDraft(draft,project);
    }
    expect(draft.fields.relation_kind.raw).toBe("align_right");
    expect(draft.fields.relation_target.raw).toBe(other.id);
    expect(canvasRelationIssue(draft,project,"add")).toBeNull();
    const prepared=prepareCanvasRelationSubmission(draft,project,"add","018f0000-0000-7000-8000-000000000290");
    expect(prepared.change.type).toBe("set_canvas_relations");
    expect(prepared.change.relations).toMatchObject([{kind:"align_right",target_id:other.id}]);
    expect(prepared.draft.awaiting?.relations).toEqual(prepared.change.relations);
  });

  it("matches exact Add acknowledgement, consumes its target once, and forbids a duplicated relation", async () => {
    const { canvasRelationIssue, canvasSubmissionMatches, prepareCanvasRelationSubmission } = await import("./canvasWorkspaceDraft");
    const project=structuredClone(fixtureProject),scene=project.scenes[0],node=scene.nodes[0];
    let draft=createCanvasDraft(project,scene.id,node.id);
    draft=editCanvasField(draft,"relation_kind","follow");
    draft=editCanvasField(draft,"relation_target",scene.nodes[1].id);
    const sent=prepareCanvasRelationSubmission(draft,project,"add","018f0000-0000-7000-8000-000000000291");
    expect(() => prepareCanvasRelationSubmission(sent.draft,project,"add","018f0000-0000-7000-8000-000000000292")).toThrow(/unconfirmed/);
    const saved=structuredClone(project);saved.revision++;
    saved.scenes[0].nodes[0].relations=sent.change.relations;
    expect(canvasSubmissionMatches(saved,sent.draft,sent.submission)).toBe(true);
    draft=reconcileCanvasDraft(sent.draft,saved);
    expect(draft.awaiting).toBeNull();
    expect(draft.fields.relation_kind.raw).toBe("follow");
    expect(draft.fields.relation_target.raw).toBe("");
    expect(draft.relationsBase).toEqual(sent.change.relations);
    draft=editCanvasField(draft,"relation_target",scene.nodes[1].id);
    expect(canvasRelationIssue(draft,saved,"add")).toContain("already exists");
    expect(() => prepareCanvasRelationSubmission(draft,saved,"add","018f0000-0000-7000-8000-000000000292")).toThrow(/already exists/);
  });

  it("requires an explicit choice when a peer changes the saved relation list", async () => {
    const { canvasRelationIssue, canvasDraftConflicts, resolveCanvasField, prepareCanvasRelationSubmission } = await import("./canvasWorkspaceDraft");
    let project=structuredClone(fixtureProject),scene=project.scenes[0],node=scene.nodes[0];
    let draft=editCanvasField(createCanvasDraft(project,scene.id,node.id),"relation_target",scene.nodes[1].id);
    const peer=structuredClone(project);peer.revision++;
    peer.scenes[0].nodes[0].relations=[{id:"018f0000-0000-7000-8000-000000000293",kind:"follow",target_id:scene.nodes[1].id}];
    draft=reconcileCanvasDraft(draft,peer);
    expect(canvasDraftConflicts(draft,peer).some(item=>item.field==="relation_target")).toBe(true);
    expect(canvasRelationIssue(draft,peer,"add")).toContain("Review the changed relation list");
    expect(() => prepareCanvasRelationSubmission(draft,peer,"add","018f0000-0000-7000-8000-000000000294")).toThrow(/changed relation list/);
    draft=resolveCanvasField(draft,peer,"relation_target","draft");
    expect(canvasRelationIssue(draft,peer,"add")).toBeNull();
    const result=prepareCanvasRelationSubmission(draft,peer,"add","018f0000-0000-7000-8000-000000000294");
    expect(result.change.relations).toHaveLength(2);
    expect(result.change.relations[0].id).toBe(peer.scenes[0].nodes[0].relations[0].id);
  });

  it("an uncertain Remove retains its exact list until the author explicitly reviews a newer revision", async () => {
    const { prepareCanvasRelationSubmission, releaseCanvasSubmission, canvasRelationIssue } = await import("./canvasWorkspaceDraft");
    const project=structuredClone(fixtureProject),scene=project.scenes[0],node=scene.nodes[0];
    node.relations=[{id:"018f0000-0000-7000-8000-000000000295",kind:"align_left",target_id:scene.nodes[1].id}];
    const draft=createCanvasDraft(project,scene.id,node.id);
    const pending=prepareCanvasRelationSubmission(draft,project,"remove","018f0000-0000-7000-8000-000000000296",node.relations[0].id);
    expect(pending.change.relations).toEqual([]);
    const peer=structuredClone(project);peer.revision++;peer.scenes[0].nodes[0].text="Other author";
    const held=reconcileCanvasDraft(pending.draft,peer);
    expect(held.awaiting?.relations).toEqual([]);
    expect(canvasRelationIssue(held,peer,"remove",node.relations[0].id)).toContain("unconfirmed");
    expect(() => prepareCanvasRelationSubmission(held,peer,"remove","018f0000-0000-7000-8000-000000000297",node.relations[0].id)).toThrow(/unconfirmed/);
    const reviewed=reconcileCanvasDraft(releaseCanvasSubmission(held,pending.submission.id),peer);
    expect(canvasRelationIssue(reviewed,peer,"remove",node.relations[0].id)).toBeNull();
  });

  it("rejects invalid/self targets, duplicate IDs and saved position locks before grant", async () => {
    const { canvasRelationIssue, prepareCanvasRelationSubmission } = await import("./canvasWorkspaceDraft");
    const project=structuredClone(fixtureProject),scene=project.scenes[0],node=scene.nodes[0];
    let draft=editCanvasField(createCanvasDraft(project,scene.id,node.id),"relation_target",node.id);
    expect(canvasRelationIssue(draft,project,"add")).toContain("another saved");
    expect(() => prepareCanvasRelationSubmission(draft,project,"add","018f0000-0000-7000-8000-000000000298")).toThrow();
    draft=editCanvasField(draft,"relation_target","missing");
    expect(canvasRelationIssue(draft,project,"add")).toContain("another saved");
    draft=editCanvasField(draft,"relation_target",scene.nodes[1].id);
    const locked=structuredClone(project);
    locked.scenes[0].nodes[0].property_locks.push("position");
    expect(canvasRelationIssue(draft,locked,"add")).toContain("lock");
    expect(() => prepareCanvasRelationSubmission(draft,locked,"add","018f0000-0000-7000-8000-000000000298")).toThrow(/lock/);
    expect(draft.fields.relation_target.raw).toBe(scene.nodes[1].id);
  });

  it("preserves later human target change after a saved Add ACK", async () => {
    const { canvasSubmissionMatches, prepareCanvasRelationSubmission } = await import("./canvasWorkspaceDraft");
    const project=structuredClone(fixtureProject),scene=project.scenes[0],node=scene.nodes[0];
    let draft=editCanvasField(createCanvasDraft(project,scene.id,node.id),"relation_target",scene.nodes[1].id);
    const sent=prepareCanvasRelationSubmission(draft,project,"add","018f0000-0000-7000-8000-000000000299");
    draft=editCanvasField(sent.draft,"relation_target","");
    const saved=structuredClone(project);saved.revision++;saved.scenes[0].nodes[0].relations=sent.change.relations;
    expect(canvasSubmissionMatches(saved,draft,sent.submission)).toBe(true);
    draft=reconcileCanvasDraft(draft,saved);
    expect(draft.awaiting).toBeNull();
    expect(draft.fields.relation_target.raw).toBe("");
    expect(draft.fields.relation_target.intent).toBe(true);
  });

  it("migrates a valid v3 copy without losing text, style, hierarchy and saved relation identity", async () => {
    const { CANVAS_RELATION_FIELDS } = await import("./canvasWorkspaceDraft");
    const project=structuredClone(fixtureProject),scene=project.scenes[0],node=scene.nodes[0];
    node.relations=[{id:"018f0000-0000-7000-8000-0000000002a0",kind:"align_left",target_id:scene.nodes[1].id}];
    let draft=createCanvasDraft(project,scene.id,node.id);
    draft=editCanvasField(draft,"text","Legacy human message");
    draft=editCanvasField(draft,"fill","#C15A73");
    draft=editCanvasField(draft,"z_index","17");
    const legacy=structuredClone(draft) as typeof draft;legacy.schema="motionwright.canvas-draft/3";
    for(const field of CANVAS_RELATION_FIELDS) delete (legacy.fields as Partial<typeof draft.fields>)[field];
    delete (legacy as Partial<typeof draft>).relationsBase;
    const scope=draft.scope,original=JSON.stringify({schema:"motionwright.canvas-recovery/1",scope,writerId:"v3-writer",updatedAt:1,draft:legacy});
    const decoded=decodeCanvasRecovery(original,scope,"v3-writer").draft!;
    const migrated=reconcileCanvasDraft(decoded,project);
    expect(migrated.schema).toBe("motionwright.canvas-draft/5");
    expect(migrated.fields.text.raw).toBe("Legacy human message");
    expect(migrated.fields.fill.raw).toBe("#C15A73");
    expect(migrated.fields.z_index.raw).toBe("17");
    expect(migrated.relationsBase).toEqual(node.relations);
    expect(migrated.fields.relation_kind.raw).toBe("align_left");
    expect(migrated.fields.relation_target.raw).toBe("");
  });

  it("quarantines malformed, excessive, duplicated and unknown-kind recovered relation lists", async () => {
    const project=structuredClone(fixtureProject),scene=project.scenes[0],node=scene.nodes[0];
    const draft=createCanvasDraft(project,scene.id,node.id),valid={id:"018f0000-0000-7000-8000-0000000002a1",kind:"follow",target_id:scene.nodes[1].id};
    const read = (list: unknown) => decodeCanvasRecovery(JSON.stringify({schema:"motionwright.canvas-recovery/1",scope:draft.scope,
      writerId:"tampered-writer",updatedAt:1,draft:{...draft,relationsBase:list}}),draft.scope,"tampered-writer");
    expect(() => read([valid,valid])).toThrow(/invalid/);
    expect(() => read([{...valid,kind:"unauthorized_kind"}])).toThrow(/invalid/);
    expect(() => read(Array.from({length:129},(_,i)=>({...valid,id:String(i)})))).toThrow(/invalid/);
    expect(() => read([valid])).not.toThrow();
  });
});

describe("Canvas object removal uses the existing scoped recovery without claiming a service receipt", () => {
  it("retains an exact removal intent and blocks another submission until explicit review", async () => {
    const { prepareCanvasRemovalSubmission, canvasRemovalObservation } = await import("./canvasWorkspaceDraft");
    const project = source(), prepared = prepareCanvasRemovalSubmission(draftFor(project), project, "local-removal");
    expect(prepared.change).toEqual({ type: "remove_canvas_node", scene_id: sceneId, node_id: nodeId });
    const recovered = roundTrip(prepared.draft);
    expect(recovered.schema).toBe("motionwright.canvas-draft/5");
    expect(recovered.awaiting).toEqual(prepared.submission);
    expect(canvasRemovalObservation(recovered, project)).toBe("present");
    expect(canvasSubmissionMatches(project, recovered, prepared.submission)).toBe(false);
    expect(() => prepareCanvasRemovalSubmission(recovered, project, "duplicate")).toThrow(/review/);
    const later = editCanvasField(recovered, "text", "Input after removal was submitted");
    expect(() => prepareCanvasSubmission(later, project, "text", "blocked-text")).toThrow(/unconfirmed/);
    expect(releaseCanvasSubmission(later, "different-local-token")).toBe(later);
    const reviewed = releaseCanvasSubmission(later, prepared.submission.id);
    expect(reviewed.awaiting).toBeNull();
    expect(reviewed.fields).toEqual(later.fields);
    expect(project).toEqual(source());
  });

  it("keeps uncertainty and later raw input after later scoped absence without inventing an ACK", async () => {
    const { prepareCanvasRemovalSubmission, canvasRemovalObservation } = await import("./canvasWorkspaceDraft");
    const project = source(), prepared = prepareCanvasRemovalSubmission(draftFor(project), project, "lost-removal-reply");
    let kept = editCanvasField(prepared.draft, "text", "  Preserve this later line\n");
    kept = editCanvasField(kept, "x", "1e");
    const absent = structuredClone(project); absent.revision++;
    absent.scenes[0].nodes = absent.scenes[0].nodes.filter(item => item.id !== nodeId);
    const recovered = reconcileCanvasDraft(roundTrip(kept), absent);
    expect(canvasRemovalObservation(recovered, absent)).toBe("absent");
    expect(canvasSubmissionMatches(absent, recovered, prepared.submission)).toBe(false);
    expect(recovered.awaiting).toEqual(prepared.submission);
    expect(recovered.fields).toEqual(kept.fields);
    expect(() => prepareCanvasRemovalSubmission(recovered, absent, "forbidden-replay")).toThrow();
    expect(absent.scenes[0].nodes.some(item => item.id === nodeId)).toBe(false);
    const unrelated = nextProject(project, { y: node(project).y + 1 });
    expect(reconcileCanvasDraft(roundTrip(kept), unrelated).awaiting).toEqual(prepared.submission);
    expect(canvasRemovalObservation(kept, unrelated)).toBe("present");
  });

  it("requires the exact scope, an existing scene and a later revision before observing absence", async () => {
    const { prepareCanvasRemovalSubmission, canvasRemovalObservation } = await import("./canvasWorkspaceDraft");
    const project = source(), prepared = prepareCanvasRemovalSubmission(draftFor(project), project, "scope-removal");
    const removed = structuredClone(project); removed.scenes[0].nodes = [];
    expect(canvasRemovalObservation(prepared.draft, removed)).toBe("unconfirmed");
    expect(canvasRemovalObservation(prepared.draft, { ...removed, revision: project.revision - 1 })).toBe("unconfirmed");
    removed.revision++;
    for (const field of ["id", "generation", "active_branch"] as const) {
      expect(canvasRemovalObservation(prepared.draft, { ...removed, [field]: removed[field] + "-other" })).toBe("unconfirmed");
      expect(() => reconcileCanvasDraft(prepared.draft, { ...removed, [field]: removed[field] + "-other" })).toThrow(/another project/);
    }
    expect(canvasRemovalObservation(prepared.draft, { ...removed, scenes: [] })).toBe("unconfirmed");
    expect(() => reconcileCanvasDraft(prepared.draft, { ...removed, scenes: [] })).toThrow(/no longer/);
  });

  it("migrates schema4 input without promoting a local recovery into execution authority", async () => {
    const { prepareCanvasRemovalSubmission } = await import("./canvasWorkspaceDraft");
    const project = source();
    const legacy = editCanvasField(draftFor(project), "text", "Version four author input");
    legacy.schema = "motionwright.canvas-draft/4";
    const original = JSON.stringify(copy(legacy));
    const migrated = reconcileCanvasDraft(decodeCanvasRecovery(original, legacy.scope, "window-a").draft!, project);
    expect(migrated.schema).toBe("motionwright.canvas-draft/5");
    expect(migrated.fields.text.raw).toBe("Version four author input");
    expect(migrated.relationsBase).toEqual(legacy.relationsBase);
    expect(original).toBe(JSON.stringify(copy(legacy)));
    const pending = prepareCanvasSubmission(legacy, project, "text", "schema4-text-ACK");
    pending.draft.schema = "motionwright.canvas-draft/4";
    const retained = reconcileCanvasDraft(roundTrip(pending.draft), project);
    expect(retained.awaiting).toEqual(pending.submission);
    expect(retained.fields.text.raw).toBe(legacy.fields.text.raw);
    const attempt = prepareCanvasRemovalSubmission(draftFor(project), project, "local-only");
    const forged = { ...copy(attempt.draft), request_id: "not-a-service-receipt", effect_grant: "not-authority" };
    const decoded = decodeCanvasRecovery(JSON.stringify(forged), attempt.draft.scope, "window-a");
    expect(decoded).not.toHaveProperty("request_id");
    expect(decoded).not.toHaveProperty("effect_grant");
    const oldSchema = { ...attempt.draft, schema: "motionwright.canvas-draft/4" };
    expect(() => decodeCanvasRecovery(JSON.stringify(copy(oldSchema as CanvasWorkspaceDraft)), attempt.draft.scope, "window-a"))
      .toThrow(/removal snapshot/);
    const invalid = { ...attempt.draft, awaiting: { ...attempt.submission, fields: { text: { raw: "payload" } } } };
    expect(() => decodeCanvasRecovery(JSON.stringify(copy(invalid as CanvasWorkspaceDraft)), attempt.draft.scope, "window-a"))
      .toThrow(/removal snapshot/);
  });

  it("finds removal recovery after node loss without consuming other object, writer or project inputs", async () => {
    const { prepareCanvasRemovalSubmission, readCanvasRemovalRecoveries } = await import("./canvasWorkspaceDraft");
    const project = source(), storage = new MemoryStorage();
    const removed = prepareCanvasRemovalSubmission(draftFor(project), project, "retained-removal");
    const raw = retain(storage, copy(removed.draft));
    const other = editCanvasField(draftFor(project), "text", "Another window's human input");
    retain(storage, copy(other, "other-window"));
    const foreign = { ...project, generation: project.generation + "-other" };
    const foreignDraft = prepareCanvasRemovalSubmission(createCanvasDraft(foreign, sceneId, nodeId), foreign, "foreign-removal").draft;
    retain(storage, { ...copy(foreignDraft, "foreign-window"), scope: foreignDraft.scope });
    const before = new Map(storage.data), absent = structuredClone(project);
    absent.revision++; absent.scenes[0].nodes = [];
    const found = readCanvasRemovalRecoveries(storage, absent, sceneId).entries;
    expect(found).toHaveLength(1);
    expect(found[0].raw).toBe(raw);
    expect(found[0].value.draft?.awaiting?.id).toBe("retained-removal");
    expect(storage.data).toEqual(before);
    discardCanvasRecovery(storage, found[0].key, found[0].raw);
    expect(readCanvasRemovalRecoveries(storage, absent, sceneId).entries).toEqual([]);
    expect(storage.getItem(canvasRecoveryKey(other.scope, "other-window"))).toBe(before.get(canvasRecoveryKey(other.scope, "other-window")));
    expect(storage.getItem(canvasRecoveryKey(foreignDraft.scope, "foreign-window"))).toBe(before.get(canvasRecoveryKey(foreignDraft.scope, "foreign-window")));
  });
});
