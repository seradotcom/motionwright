import type { BranchCombination, BranchCombinationPick, CanvasNode, Change, Project } from "./types";

/** Prepared intent only. Rust remains the mutation/preview authority. */
export interface BranchCombinationInput {
  candidateId: string;
  name: string;
  rationale: string;
  donorBranchId: string;
  sceneId: string;
  picks: BranchCombinationPick[];
}
export interface BranchCombinationInspection {
  change: Extract<Change, { type: "combine_creative_branches" }>;
  choices: { nodeId: string; field: BranchCombinationPick["field"]; current: unknown; donor: unknown }[];
  nativeRenderObserved: false;
  requiresCanonicalPreview: true;
}
const uuid = (value: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
  && value !== "00000000-0000-0000-0000-000000000000";
const bytes = (value: string) => new TextEncoder().encode(value).length;
const asciiLower = (value: string) => value.replace(/[A-Z]/g, letter => letter.toLowerCase());
function same(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  if (Array.isArray(left) || Array.isArray(right)) return Array.isArray(left) && Array.isArray(right)
    && left.length === right.length && left.every((value, i) => same(value, right[i]));
  const a = left as Record<string, unknown>, b = right as Record<string, unknown>;
  return Object.keys(a).length === Object.keys(b).length
    && Object.keys(a).every(key => Object.hasOwn(b, key) && same(a[key], b[key]));
}
function value(node: CanvasNode, field: BranchCombinationPick["field"]): unknown {
  if (field === "motion") return node.keyframes;
  if (field === "style") return node.style;
  if (field === "transform") return { x: node.x, y: node.y, width: node.width, height: node.height,
    rotation_deg: node.rotation_deg, opacity: node.opacity };
  throw new Error("Unknown combination field. Your choices remain editable.");
}
export function inspectBranchCombination(
  project: Project, observed: Project, raw: BranchCombinationInput,
): BranchCombinationInspection {
  // Includes changes to permissions/dependencies represented in the complete
  // Project, even if a malformed caller retained the same revision number.
  if (!same(project, observed)) throw new Error("The source changed. Review both alternatives again.");
  if (!uuid(raw.candidateId) || project.branches.some(branch => branch.id === raw.candidateId))
    throw new Error("A retained, unused candidate identity is required.");
  if (!raw.name.trim() || bytes(raw.name) > 120 || /[\u0000-\u001f\u007f-\u009f]/.test(raw.name)
    || project.branches.some(branch => asciiLower(branch.name) === asciiLower(raw.name)))
    throw new Error("Choose a unique branch name of at most 120 UTF-8 bytes.");
  if (!raw.rationale.trim() || bytes(raw.rationale) > 4000 || /[\u0000-\u0009\u000b-\u001f\u007f-\u009f]/.test(raw.rationale))
    throw new Error("Keep a reason of 1–4,000 UTF-8 bytes; the original input is retained.");
  if (project.branches.length >= 256 || project.branch_workspaces.length >= 256)
    throw new Error("The saved branch budget is full.");
  const base = project.branches.find(branch => branch.id === project.active_branch);
  const donor = project.branches.find(branch => branch.id === raw.donorBranchId);
  const saved = project.branch_workspaces.find(workspace => workspace.branch_id === raw.donorBranchId);
  if (!base || !donor || donor.id === base.id || !saved || saved.base_revision !== donor.base_revision)
    throw new Error("Choose a different saved branch with a matching base.");
  if ([base.head_revision, donor.head_revision, saved.base_revision].some(revision => !Number.isSafeInteger(revision) || revision < 0 || revision > project.revision))
    throw new Error("A branch source revision is invalid.");
  const other = saved.current_state;
  for (const field of ["brief", "audio", "deliverables", "locks"] as const)
    if (!same(project[field], other[field])) throw new Error("Both alternatives need the same brief, voice, profiles and locks.");
  if (!same(project.production_design?.narration_take_lock ?? null, other.production_design?.narration_take_lock ?? null))
    throw new Error("The original narration authority differs between alternatives.");
  const source = project.scenes.find(scene => scene.id === raw.sceneId);
  const alternate = other.scenes.find(scene => scene.id === raw.sceneId);
  if (!source || !alternate || !same([source.start, source.duration, source.renderer], [alternate.start, alternate.duration, alternate.renderer]))
    throw new Error("Both alternatives need the same scene window and renderer.");
  if (!raw.picks.length || raw.picks.length > 64) throw new Error("Choose 1–64 explicit node fields.");
  const used = new Set<string>(); let structural = false;
  const choices = raw.picks.map(pick => {
    const key = pick.node_id + ":" + pick.field;
    if (used.has(key)) throw new Error("A node field was chosen twice.");
    used.add(key);
    const before = source.nodes.find(node => node.id === pick.node_id);
    const after = alternate.nodes.find(node => node.id === pick.node_id);
    if (!before || !after) throw new Error("A chosen object is absent from one alternative.");
    for (const field of ["kind", "parent_id", "coordinate_space", "relations", "property_locks"] as const)
      if (!same(before[field], after[field])) throw new Error("A chosen object's identity context or locks changed.");
    const current = value(before, pick.field), donorValue = value(after, pick.field);
    if (pick.field !== "style" && !same(current, donorValue)) structural = true;
    return { nodeId: pick.node_id, field: pick.field, current: structuredClone(current), donor: structuredClone(donorValue) };
  });
  if (!structural) throw new Error("Choose a changed transform or motion field; recolouring alone is not this operation.");
  const input: BranchCombination = {
    source: { resource: "project:" + project.id, generation: project.generation, revision: project.revision },
    base_branch_id: base.id, base_head_revision: base.head_revision,
    donor_branch_id: donor.id, donor_head_revision: donor.head_revision,
    candidate_branch_id: raw.candidateId, candidate_name: raw.name, scene_id: source.id,
    picks: structuredClone(raw.picks), rationale: raw.rationale,
  };
  return { change: { type: "combine_creative_branches", input }, choices,
    nativeRenderObserved: false, requiresCanonicalPreview: true };
}
