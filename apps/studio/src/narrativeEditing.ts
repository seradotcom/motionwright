import { formatBeatTime, parseBeatTime } from "./sceneBeatTiming";
import type { Change, NarrativeBeat, Project, ProjectLock, RationalTime, Scene } from "./types";

export interface NarrativeEditSource { project_id: string; generation: string; branch_id: string; base_revision: number }
export type NarrativeRealizationTarget = { kind: "scene"; scene_id: string }
  | { kind: "scene_beat"; scene_id: string; scene_beat_id: string };
export interface NarrativeRealizationBinding { id: string; beat_id: string; target: NarrativeRealizationTarget }
export interface NarrativeRelation { id: string; from_beat_id: string; to_beat_id: string; kind: "precedes" | "adjacent"; reason: string }
export interface NarrativeMinimumHold { beat_id: string; duration: RationalTime }
export interface EditorialScriptApproval {
  id: string; script_id: string; script_version: number; script_sha256: string;
  source: NarrativeEditSource; reviewer: string; reason: string;
}
export interface EditorialScriptRelease { approval: EditorialScriptApproval; source: NarrativeEditSource; reviewer: string; reason: string }
export interface EditorialScript {
  id: string; version: number; text: string; sha256: string;
  approval: EditorialScriptApproval | null; last_release: EditorialScriptRelease | null;
}
export interface NarrativeRelationImpact {
  relation_id: string; from_beat_id: string; to_beat_id: string; was_satisfied: boolean; is_satisfied: boolean;
}
export interface NarrativeReorderImpact {
  source: NarrativeEditSource; before_order: string[]; after_order: string[]; moved_beat_ids: string[];
  affected_relations: NarrativeRelationImpact[]; conflicting_relation_ids: string[];
  affected_binding_ids: string[]; affected_scene_ids: string[]; unbound_moved_beat_ids: string[];
  dependency_scope: "declared_editorial_relations_only_native_dependencies_unknown";
}
export interface NarrativeBeatTimingAssessment {
  beat_id: string; binding_ids: string[]; allocated_duration: RationalTime | null;
  minimum_hold: RationalTime | null; preferred_duration: RationalTime | null;
  minimum_shortfall: RationalTime | null; preferred_delta: RationalTime | null; contradictory_preferences: boolean;
}
export interface NarrativeSceneTimingDelta {
  scene_id: string; before_start: RationalTime; after_start: RationalTime;
  before_duration: RationalTime; after_duration: RationalTime;
}
export interface NarrativeTimingFeasibility {
  command_allowed: boolean; rejection: string | null; scene_changes: NarrativeSceneTimingDelta[];
  before_preferences: NarrativeBeatTimingAssessment[]; after_preferences: NarrativeBeatTimingAssessment[] | null;
  timing_locks_present: ProjectLock[];
}
export type NarrativeTimingDecision = "keep_current" | "apply_duration";
export interface NarrativeTimingProposal {
  id: string; source: NarrativeEditSource; scene_id: string; original_duration: RationalTime; requested_duration: RationalTime;
  reason: string; script_snapshot: EditorialScript | null; feasibility: NarrativeTimingFeasibility;
  decision: { decision: NarrativeTimingDecision; source: NarrativeEditSource; reason: string } | null;
}
export interface NarrativeEditingState {
  version: 1; bindings: NarrativeRealizationBinding[]; relations: NarrativeRelation[]; minimum_holds: NarrativeMinimumHold[];
  script: EditorialScript | null; last_reorder: NarrativeReorderImpact | null; timing_proposals: NarrativeTimingProposal[];
}
export type NarrativeEdit =
  | { kind: "update_beat"; beat: NarrativeBeat }
  | { kind: "set_beat_protection"; beat_id: string; protected: boolean; reason: string }
  | { kind: "set_minimum_hold"; beat_id: string; duration: RationalTime | null }
  | { kind: "upsert_binding"; binding: NarrativeRealizationBinding }
  | { kind: "remove_binding"; binding_id: string }
  | { kind: "upsert_relation"; relation: NarrativeRelation }
  | { kind: "remove_relation"; relation_id: string }
  | { kind: "reorder"; expected_order: string[]; ordered_ids: string[] }
  | { kind: "set_script"; script_id: string; expected_version: number | null; text: string }
  | { kind: "approve_script"; approval_id: string; script_id: string; expected_version: number; expected_sha256: string; reviewer: string; reason: string }
  | { kind: "release_script_approval"; approval_id: string; expected_sha256: string; reviewer: string; reason: string }
  | { kind: "propose_duration"; proposal_id: string; scene_id: string; requested_duration: RationalTime; reason: string }
  | { kind: "decide_duration"; proposal_id: string; decision: NarrativeTimingDecision; reason: string };
export type NarrativeEditingChange = { type: "edit_narrative"; expected: NarrativeEditSource; edit: NarrativeEdit }
  | { type: "add_narrative_beat"; label: string; objective: string; audience_takeaway: string; claim_ids: string[]; preferred_duration: RationalTime | null };

export const narrativeEditingScope = (project: Project) => JSON.stringify([project.id, project.generation, project.active_branch]);
export const narrativeEditingSource = (project: Project): NarrativeEditSource => ({ project_id: project.id,
  generation: project.generation, branch_id: project.active_branch, base_revision: project.revision });
export const emptyNarrativeEditing = (): NarrativeEditingState => ({ version: 1, bindings: [], relations: [], minimum_holds: [],
  script: null, last_reorder: null, timing_proposals: [] });
export const narrativeEditingState = (project: Pick<Project, "narrative">) => project.narrative.editing ?? emptyNarrativeEditing();
export function narrativeEditChange(project: Project, edit: NarrativeEdit): NarrativeEditingChange {
  if (!Number.isSafeInteger(project.revision) || project.revision < 0) throw new Error("The current revision cannot be represented exactly.");
  return { type: "edit_narrative", expected: narrativeEditingSource(project), edit };
}
export function sameEditorialValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object") return false;
  if (Array.isArray(left) || Array.isArray(right)) return Array.isArray(left) && Array.isArray(right)
    && left.length === right.length && left.every((value, index) => sameEditorialValue(value, right[index]));
  const a = Object.keys(left).sort(), b = Object.keys(right).sort();
  return a.length === b.length && a.every((key, index) => key === b[index]
    && sameEditorialValue(Reflect.get(left, key), Reflect.get(right, key)));
}
export const editorialTime = (value: RationalTime | null | undefined) => value ? formatBeatTime(value) : "";
export function parseEditorialDuration(raw: string, optional = false): RationalTime | null {
  if (optional && raw.trim() === "") return null;
  const value = parseBeatTime(raw);
  if (BigInt(value.num) <= 0n) throw new Error("An editorial duration must be greater than zero.");
  return value;
}

// Read-only comparison of authored allocations. There is no simulated backend
// or solver here; native/runtime time is never inferred from text length.
type Q = { n: bigint; d: bigint };
const gcd = (a: bigint, b: bigint): bigint => { a = a < 0n ? -a : a; while (b) [a, b] = [b, a % b]; return a || 1n; };
const q = (v: RationalTime): Q => { formatBeatTime(v); return { n: BigInt(v.num), d: BigInt(v.den) }; };
const cmp = (a: Q, b: Q) => a.n * b.d < b.n * a.d ? -1 : a.n * b.d > b.n * a.d ? 1 : 0;
function rat(n: bigint, d: bigint): Q {
  const g = gcd(n, d); n /= g; d /= g;
  if (n < -9_223_372_036_854_775_808n || n > 9_223_372_036_854_775_807n || d <= 0n || d > 9_223_372_036_854_775_807n)
    throw new Error("The exact editorial allocation exceeds the canonical i64 rational range.");
  return { n, d };
}
const add = (a: Q, b: Q) => rat(a.n * b.d + b.n * a.d, a.d * b.d);
const sub = (a: Q, b: Q) => rat(a.n * b.d - b.n * a.d, a.d * b.d);
const wire = (v: Q): RationalTime => ({ num: String(v.n), den: String(v.d) });
function interval(target: NarrativeRealizationTarget, scenes: Scene[]): [Q, Q] {
  const scene = scenes.find(value => value.id === target.scene_id);
  if (!scene) throw new Error("Missing realization scene: " + target.scene_id);
  if (target.kind === "scene") return [q(scene.start), add(q(scene.start), q(scene.duration))];
  const beat = scene.beats.find(value => value.id === target.scene_beat_id);
  if (!beat) throw new Error("Missing realization SceneBeat: " + target.scene_beat_id);
  const start = add(q(scene.start), q(beat.start));
  return [start, add(start, q(beat.duration))];
}
function union(intervals: [Q, Q][]): Q | null {
  if (!intervals.length) return null;
  intervals.sort((a, b) => cmp(a[0], b[0]) || cmp(a[1], b[1]));
  let [start, end] = intervals[0], total: Q = { n: 0n, d: 1n };
  for (const [nextStart, nextEnd] of intervals.slice(1)) {
    if (cmp(nextStart, end) <= 0) { if (cmp(nextEnd, end) > 0) end = nextEnd; }
    else { total = add(total, sub(end, start)); start = nextStart; end = nextEnd; }
  }
  return add(total, sub(end, start));
}
export function assessEditorialPreferences(project: Pick<Project, "narrative" | "scenes">): NarrativeBeatTimingAssessment[] {
  const state = narrativeEditingState(project);
  return project.narrative.beats.map(beat => {
    const bindings = state.bindings.filter(binding => binding.beat_id === beat.id);
    const allocation = union(bindings.map(binding => interval(binding.target, project.scenes)));
    const minimum = state.minimum_holds.find(hold => hold.beat_id === beat.id)?.duration ?? null;
    const preference = beat.preferred_duration;
    return { beat_id: beat.id, binding_ids: bindings.map(binding => binding.id), allocated_duration: allocation ? wire(allocation) : null,
      minimum_hold: minimum, preferred_duration: preference,
      minimum_shortfall: allocation && minimum ? wire(cmp(allocation, q(minimum)) < 0 ? sub(q(minimum), allocation) : { n: 0n, d: 1n }) : null,
      preferred_delta: allocation && preference ? wire(sub(allocation, q(preference))) : null,
      contradictory_preferences: !!minimum && !!preference && cmp(q(minimum), q(preference)) > 0 };
  });
}
export function inspectEditorialReorder(project: Project, after: string[]): NarrativeReorderImpact {
  const before = project.narrative.beats.map(beat => beat.id), ids = new Set(before);
  if (before.length !== after.length || new Set(after).size !== before.length || after.some(id => !ids.has(id)))
    throw new Error("Keep every original editorial beat exactly once when reordering.");
  const moved = before.filter((id, i) => after[i] !== id).sort();
  const state = narrativeEditingState(project);
  const predicate = (relation: NarrativeRelation, order: string[]) => {
    const from = order.indexOf(relation.from_beat_id), to = order.indexOf(relation.to_beat_id);
    if (from < 0 || to < 0) throw new Error("Editorial relation references a missing beat: " + relation.id);
    return relation.kind === "precedes" ? from < to : to === from + 1;
  };
  const relations = state.relations.map(relation => ({ relation_id: relation.id, from_beat_id: relation.from_beat_id,
    to_beat_id: relation.to_beat_id, was_satisfied: predicate(relation, before), is_satisfied: predicate(relation, after) }));
  const bindings = state.bindings.filter(binding => moved.includes(binding.beat_id));
  return { source: narrativeEditingSource(project), before_order: before, after_order: [...after], moved_beat_ids: moved,
    affected_relations: relations.filter(relation => relation.was_satisfied !== relation.is_satisfied),
    conflicting_relation_ids: relations.filter(relation => !relation.is_satisfied).map(relation => relation.relation_id),
    affected_binding_ids: bindings.map(binding => binding.id), affected_scene_ids: [...new Set(bindings.map(binding => binding.target.scene_id))].sort(),
    unbound_moved_beat_ids: moved.filter(id => !bindings.some(binding => binding.beat_id === id)),
    dependency_scope: "declared_editorial_relations_only_native_dependencies_unknown" };
}

/** The original caller and local intent ID are checked by the draft helper.
 * These predicates additionally reject an original response for a different
 * payload, advanced revision, branch, generation or project. Props/history are
 * never passed here as receipts by the component. */
export function assertEditorialReceipt(change: NarrativeEditingChange, before: { source: NarrativeEditSource; beatIds: string[]; scriptJson: string }, receipt: Project): void {
  const expected = before.source;
  if (narrativeEditingScope(receipt) !== JSON.stringify([expected.project_id, expected.generation, expected.branch_id])
    || receipt.revision !== expected.base_revision + 1) throw new Error("The original editorial response has foreign or advanced authority.");
  const state = narrativeEditingState(receipt);
  let matches = false;
  if (change.type === "add_narrative_beat") {
    const added = receipt.narrative.beats.filter(beat => !before.beatIds.includes(beat.id));
    matches = added.length === 1 && receipt.narrative.beats.length === before.beatIds.length + 1
      && new Set(receipt.narrative.beats.map(beat => beat.id)).size === receipt.narrative.beats.length
      && before.beatIds.every(id => receipt.narrative.beats.some(beat => beat.id === id))
      && sameEditorialValue({ label: added[0].label, objective: added[0].objective, audience_takeaway: added[0].audience_takeaway,
        claim_ids: added[0].claim_ids, preferred_duration: added[0].preferred_duration }, { label: change.label, objective: change.objective,
        audience_takeaway: change.audience_takeaway, claim_ids: change.claim_ids, preferred_duration: change.preferred_duration });
  } else {
    if (!sameEditorialValue(change.expected, expected)) throw new Error("The dispatched editorial source differs from its retained intent.");
    const edit = change.edit;
    switch (edit.kind) {
      case "update_beat": matches = sameEditorialValue(receipt.narrative.beats.find(beat => beat.id === edit.beat.id), edit.beat); break;
      case "set_beat_protection": matches = edit.protected
        ? receipt.narrative.protected_sections.some(section => section.beat_id === edit.beat_id && section.reason === edit.reason)
        : !receipt.narrative.protected_sections.some(section => section.beat_id === edit.beat_id); break;
      case "set_minimum_hold": matches = sameEditorialValue(state.minimum_holds.find(hold => hold.beat_id === edit.beat_id)?.duration ?? null, edit.duration); break;
      case "upsert_binding": matches = sameEditorialValue(state.bindings.find(binding => binding.id === edit.binding.id), edit.binding); break;
      case "remove_binding": matches = !state.bindings.some(binding => binding.id === edit.binding_id); break;
      case "upsert_relation": matches = sameEditorialValue(state.relations.find(relation => relation.id === edit.relation.id), edit.relation); break;
      case "remove_relation": matches = !state.relations.some(relation => relation.id === edit.relation_id); break;
      case "reorder": matches = sameEditorialValue(receipt.narrative.beats.map(beat => beat.id), edit.ordered_ids)
        && sameEditorialValue(state.last_reorder?.source, expected) && sameEditorialValue(state.last_reorder?.before_order, edit.expected_order); break;
      case "set_script": matches = state.script?.id === edit.script_id && state.script.version === (edit.expected_version ?? 0) + 1
        && state.script.text === edit.text && state.script.approval === null; break;
      case "approve_script": matches = state.script?.id === edit.script_id && state.script.version === edit.expected_version
        && state.script.sha256 === edit.expected_sha256 && state.script.approval?.id === edit.approval_id
        && state.script.approval.reviewer === edit.reviewer && state.script.approval.reason === edit.reason
        && sameEditorialValue(state.script.approval.source, expected); break;
      case "release_script_approval": matches = state.script?.approval === null
        && state.script.sha256 === edit.expected_sha256 && state.script.last_release?.approval.id === edit.approval_id
        && state.script.last_release.reviewer === edit.reviewer && state.script.last_release.reason === edit.reason
        && sameEditorialValue(state.script.last_release.source, expected); break;
      case "propose_duration": {
        const proposal = state.timing_proposals.find(value => value.id === edit.proposal_id);
        matches = !!proposal && sameEditorialValue(proposal.source, expected) && proposal.scene_id === edit.scene_id
          && sameEditorialValue(proposal.requested_duration, edit.requested_duration) && proposal.reason === edit.reason
          && sameEditorialValue(proposal.script_snapshot, JSON.parse(before.scriptJson)) && proposal.decision === null; break;
      }
      case "decide_duration": {
        const proposal = state.timing_proposals.find(value => value.id === edit.proposal_id);
        matches = !!proposal && proposal.decision?.decision === edit.decision && proposal.decision.reason === edit.reason
          && sameEditorialValue(proposal.decision.source, expected)
          && (edit.decision === "keep_current" || receipt.scenes.some(scene => scene.id === proposal.scene_id && sameEditorialValue(scene.duration, proposal.requested_duration))); break;
      }
    }
  }
  if (!matches) throw new Error("The original editorial response does not confirm the exact requested value. Keep UNKNOWN and inspect the saved project.");
}

/** Raw field bases are individual author buffers, not a second Project store. */
export function editorialFieldBase(project: Project, key: string): string {
  if (key === "script.text") return narrativeEditingState(project).script?.text ?? "";
  if (key === "order") return JSON.stringify(project.narrative.beats.map(beat => beat.id));
  const match = /^beat:([^:]+):(label|objective|audience_takeaway|preferred|minimum)$/.exec(key);
  if (match) {
    const beat = project.narrative.beats.find(beat => beat.id === match[1]);
    if (!beat) throw new Error("The editorial beat was removed: " + match[1]);
    switch (match[2]) {
      case "label": return beat.label;
      case "objective": return beat.objective;
      case "audience_takeaway": return beat.audience_takeaway;
      case "preferred": return editorialTime(beat.preferred_duration);
      case "minimum": return editorialTime(narrativeEditingState(project).minimum_holds.find(hold => hold.beat_id === beat.id)?.duration);
    }
  }
  // New-resource controls and reviewer/reason buffers have no saved value.
  if (/^(new\.(label|objective|takeaway|preferred)|decision\.(reviewer|reason)|binding\.(beat|scene|target)|relation\.(from|to|kind|reason)|proposal\.(scene|duration|reason)|proposal:[^:]+:reason|protection:[^:]+:reason)$/.test(key)) return "";
  throw new Error("Unsupported editorial input field: " + key);
}

// Compile-time conformance once A1 applies the shared wire patch.
export const asEditorialChange = (change: NarrativeEditingChange): Change => change;
