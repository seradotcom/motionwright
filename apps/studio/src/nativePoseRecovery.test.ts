import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { applyChange } from "./api";
import { acknowledgePose, decodePoseRecovery, freshPoseRecovery, inspectPoseOutcome, parsePoseRaw,
  poseConflict, poseRecoveryKey, poseScope, preparePose, rebasePose, retainSeparatePose,
  writePoseRecovery } from "./nativePoseRecovery";
import type { ProjectEvent } from "./types";

const ATTEMPT = "9a000000-0000-4000-8000-000000000001";
function source() {
  const project = structuredClone(fixtureProject), scene = project.scenes[0], node = scene.nodes[0];
  scene.beats = []; scene.renderer = "motion-canvas"; project.locks = [];
  if (project.production_design) project.production_design.camera_reframes = [];
  Object.assign(node, { kind: "shape", coordinate_space: "project_pixels", x: 1100, y: 430,
    width: 360, height: 220, rotation_deg: 0, opacity: 1, keyframes: [], property_locks: [], relations: [] });
  node.style.blend_mode = "normal";
  const parent = { ...structuredClone(node), id: "9a000000-0000-4000-8000-000000000002", kind: "clip_group", parent_id: null };
  node.parent_id = parent.id; scene.nodes.push(parent);
  const profile = project.deliverables[0], draft = freshPoseRecovery(project, scene, node, profile);
  draft.raw.x = "1000.000"; draft.dirty = true;
  return { project, scene, node, parent, profile, draft };
}
function storage() {
  const records = new Map<string, string>();
  return { records, get length() { return records.size; }, key: (index: number) => [...records.keys()][index] ?? null,
    getItem: (key: string) => records.get(key) ?? null, setItem: (key: string, value: string) => { records.set(key, value); } };
}
describe("native pose durable custody", () => {
  it("preserves incomplete and precisely formatted author input across reopening", () => {
    const f = source(), store = storage();
    f.draft.raw.x = "-"; f.draft.raw.y = "00410.00"; f.draft.raw.endFrame = "";
    const scope = poseScope(f.project, f.scene, f.node), raw = writePoseRecovery(store, f.draft, null);
    const recovered = decodePoseRecovery(store.getItem(poseRecoveryKey(scope))!, scope);
    expect(recovered.raw).toEqual(f.draft.raw);
    expect(store.getItem(poseRecoveryKey(scope))).toBe(raw);
    expect(() => parsePoseRaw(recovered.raw)).toThrow(/complete finite/);
    expect(() => preparePose(recovered, f.project, f.scene, f.node, f.profile, ATTEMPT)).toThrow();
  });
  it("requires explicit rebase when only the root Clip or project revision changes", () => {
    const f = source();
    f.parent.x += 10;
    expect(poseConflict(f.draft, f.project, f.scene, f.node, f.profile)).toBe(true);
    expect(() => preparePose(f.draft, f.project, f.scene, f.node, f.profile, ATTEMPT)).toThrow(/Review/);
    const reviewed = rebasePose(f.draft, f.project, f.scene, f.node, f.profile);
    expect(reviewed.raw).toEqual(f.draft.raw);
    expect(poseConflict(reviewed, f.project, f.scene, f.node, f.profile)).toBe(false);
    f.project.revision++;
    expect(poseConflict(reviewed, f.project, f.scene, f.node, f.profile)).toBe(true);
  });
  it("retains original pending intent before any write and only acknowledges its exact response", async () => {
    const f = source(), store = storage(), scope = f.draft.authority.scope;
    const submission = preparePose(f.draft, f.project, f.scene, f.node, f.profile, ATTEMPT);
    const before = writePoseRecovery(store, f.draft, null);
    const armed = writePoseRecovery(store, submission.draft, before);
    const recovered = decodePoseRecovery(armed, scope);
    expect(recovered.pending?.submitted).toEqual(submission.change);
    expect(() => preparePose(recovered, f.project, f.scene, f.node, f.profile, ATTEMPT)).toThrow(/Review/);
    expect(() => acknowledgePose(recovered, ATTEMPT, f.project)).toThrow();
    const saved = await applyChange(f.project, submission.change);
    expect(() => acknowledgePose(recovered, "other", saved)).toThrow(/original/);
    const tampered = structuredClone(saved);
    tampered.scenes[0].nodes.find(n => n.id === f.parent.id)!.width++;
    expect(() => acknowledgePose(recovered, ATTEMPT, tampered)).toThrow(/exact pose/);
    const confirmed = acknowledgePose(recovered, ATTEMPT, saved);
    expect(confirmed.pending).toBeNull();
    expect(confirmed.raw).toEqual(f.draft.raw);
    expect(confirmed.authority.revision).toBe(saved.revision);
    writePoseRecovery(store, confirmed, armed);
    expect(decodePoseRecovery(store.getItem(poseRecoveryKey(scope))!, scope)).toEqual(confirmed);
  });
  it("refuses a foreign, malformed or concurrently replaced local recovery copy", () => {
    const f = source(), store = storage(), scope = f.draft.authority.scope;
    const raw = writePoseRecovery(store, f.draft, null);
    expect(() => decodePoseRecovery(raw, "foreign")).toThrow();
    expect(() => decodePoseRecovery(raw.replace('"dirty":true', '"dirty":"true"'), scope)).toThrow();
    const changed = { ...f.draft, raw: { ...f.draft.raw, x: "800" } };
    const newer = writePoseRecovery(store, changed, raw);
    expect(() => writePoseRecovery(store, f.draft, raw)).toThrow(/Another window/);
    expect(store.getItem(poseRecoveryKey(scope))).toBe(newer);
  });
  it("requires bounded saved history and preserves an unresolved attempt before a distinct edit", async () => {
    const f = source(), store = storage(), scope = f.draft.authority.scope;
    const submission = preparePose(f.draft, f.project, f.scene, f.node, f.profile, ATTEMPT);
    const original = writePoseRecovery(store, submission.draft, null);
    const saved = await applyChange(f.project, submission.change);
    const history: ProjectEvent[] = [{ revision: saved.revision, change: submission.change, created_at: "2026-11-23T00:00:00Z" }];
    const readProject = async () => structuredClone(saved);
    await expect(inspectPoseOutcome(submission.draft, saved, { readProject, readHistory: async () => [] })).rejects.toThrow(/incomplete/);
    const review = await inspectPoseOutcome(submission.draft, saved, { readProject, readHistory: async () => history });
    expect(review.matchingValueRevisions).toEqual([saved.revision]);
    expect(submission.draft.pending).not.toBeNull();
    const scene = saved.scenes[0], node = scene.nodes.find(n => n.id === f.node.id)!, profile = saved.deliverables[0];
    const next = retainSeparatePose(store, submission.draft, original, saved, scene, node, profile, review);
    expect(store.getItem(poseRecoveryKey(scope) + ":unknown:" + ATTEMPT)).toBe(original);
    expect(next.draft.pending).toBeNull();
    expect(next.draft.blocked).toEqual([submission.change]);
    expect(() => preparePose(next.draft, saved, scene, node, profile, ATTEMPT)).toThrow(/earlier unconfirmed/);
    next.draft.raw.x = "900"; next.draft.dirty = true;
    expect(preparePose(next.draft, saved, scene, node, profile, ATTEMPT).change).not.toEqual(submission.change);
  });
});

it("preserves exact waypoint and independent fade input, then acknowledges one complete change", async () => {
  const f = source(), store = storage(), scope = f.draft.authority.scope;
  f.draft.raw.fade = "out";
  f.draft.raw.fadeEndFrame = "015";
  f.draft.raw.waypoints = [{frame:"010",curve:"hold",x:"-",y:"00415.00",width:"300",height:"180",rotation_deg:"-8"}];
  const incomplete = writePoseRecovery(store, f.draft, null);
  const recovered = decodePoseRecovery(incomplete, scope);
  expect(recovered.raw).toEqual(f.draft.raw);
  expect(() => preparePose(recovered, f.project, f.scene, f.node, f.profile, ATTEMPT)).toThrow(/complete finite/);
  recovered.raw.waypoints![0].x = "1010.00";
  const submission = preparePose(recovered, f.project, f.scene, f.node, f.profile, ATTEMPT);
  expect(submission.change.keyframes).toHaveLength(17);
  const armed = writePoseRecovery(store, submission.draft, incomplete);
  const pending = decodePoseRecovery(armed, scope);
  const saved = await applyChange(f.project, submission.change);
  const confirmed = acknowledgePose(pending, ATTEMPT, saved);
  expect(confirmed.pending).toBeNull();
  expect(confirmed.raw).toEqual(recovered.raw);
  expect(confirmed.authority.revision).toBe(saved.revision);
});

it("retains exact entry/hold/exit input and never resends an unconfirmed sequence", async () => {
  const f = source(), store = storage(), scope = f.draft.authority.scope;
  Object.assign(f.draft.raw, {fade:"in_out",fadeEndFrame:"015",fadeOutStartFrame:"030",
    fadeOutEndFrame:"-",fadeOutCurve:"linear"});
  const incomplete = writePoseRecovery(store, f.draft, null);
  const recovered = decodePoseRecovery(incomplete, scope);
  expect(recovered.raw).toEqual(f.draft.raw);
  expect(() => parsePoseRaw(recovered.raw)).toThrow(/fade-out end frame/);
  recovered.raw.fadeOutEndFrame = "045";
  const submission = preparePose(recovered, f.project, f.scene, f.node, f.profile, ATTEMPT);
  const pending = decodePoseRecovery(writePoseRecovery(store, submission.draft, incomplete), scope);
  expect(pending.pending?.submitted.keyframes.filter(key => key.property === "opacity")).toHaveLength(4);
  expect(() => preparePose(pending, f.project, f.scene, f.node, f.profile, ATTEMPT)).toThrow(/Review/);
  const saved = await applyChange(f.project, submission.change);
  const confirmed = acknowledgePose(pending, ATTEMPT, saved);
  expect(confirmed.raw).toEqual(recovered.raw);
  expect(confirmed.pending).toBeNull();
  const reopened = freshPoseRecovery(saved, saved.scenes[0], saved.scenes[0].nodes[0], saved.deliverables[0]);
  expect(parsePoseRaw(reopened.raw)).toEqual(parsePoseRaw(recovered.raw));
});
