/** Local author/reviewer input custody. Project mutations remain exclusively
 * with the original App coordinator and StudioService effect-grant authority. */
import type { CanvasNode, Project, ProjectEvent, Scene } from "./types";
import { sameProjectRevision } from "./projectMutation";
import { sameValue } from "./creativeProduction";
import { applyDistillationChange, DISTILLATION_RENDERER, validateDistillationProposal } from "./componentDistillation";
import type { ComponentDistillationProposal, DistillationChange, DistillationNegative, DistillationVerdict } from "./componentDistillation";

export type DistillationTarget = { kind: "author" } | { kind: "review"; proposalId: string | null };
export interface DistillationAuthorRaw { aId: string; bId: string; negativeId: string; rolesCount: string; label: string; author: string; rights: string; requirements: string }
export interface DistillationReviewRaw { reviewer: string; note: string; verdict: DistillationVerdict }
export type DistillationRaw = DistillationAuthorRaw | DistillationReviewRaw;
export interface DistillationPending {
  /** Comparison data only; never a service request, grant or replay token. */
  id: string; revision: number; sequence: number; raw: DistillationRaw;
  submitted: DistillationChange; expected: ComponentDistillationProposal;
  outcome: "UNKNOWN" | "NOT_SENT"; diagnostic?: string;
}
export interface DistillationDraft {
  schema: "motionwright.component-distillation-draft/1"; scope: string; target: DistillationTarget;
  baseRevision: number; baseProposal: ComponentDistillationProposal | null; raw: DistillationRaw;
  sequence: number; intent: boolean; pending: DistillationPending | null;
  /** Author-session UI preference, not a saved review or execution authority. */
  selection: string;
}
export const DISTILLATION_RECOVERY_PREFIX = "motionwright.component-distillation-recovery.v1:";
export const DISTILLATION_POINTER_PREFIX = "motionwright.component-distillation-writer.v1:";
export const MAX_DISTILLATION_COPY_BYTES = 1024 * 1024, MAX_DISTILLATION_RECOVERY_BYTES = 4 * 1024 * 1024, MAX_DISTILLATION_COPIES = 64;
const MAX_HISTORY = 2048;
const clone = <T,>(value: T): T => structuredClone(value);
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: readonly string[], optional: readonly string[] = []) => keys.every(key => Object.hasOwn(value, key)) && Object.keys(value).every(key => keys.includes(key) || optional.includes(key));
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const uuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const bytes = (value: string) => new TextEncoder().encode(value).length;
const originalKinds = new Set(["text", "rectangle", "circle"]);
export const distillationScope = (project: Project, target: DistillationTarget) => JSON.stringify([project.id, project.generation, project.active_branch, target.kind, target.kind === "review" ? target.proposalId : null]);
export const distillationCopyKey = (scope: string, writer: string) => DISTILLATION_RECOVERY_PREFIX + encodeURIComponent(scope) + ":" + writer;
export const distillationPointerKey = (scope: string) => DISTILLATION_POINTER_PREFIX + encodeURIComponent(scope);
export const distillationLabel = (target: DistillationTarget) => "Distillation " + target.kind;
export const distillationHasWork = (draft: DistillationDraft) => draft.intent || draft.pending !== null;
export const distillationUnconfirmed = (draft: DistillationDraft) => draft.pending?.outcome === "UNKNOWN";
export const distillationCompatibleNodes = (scene: Scene): CanvasNode[] => scene.nodes.filter(node => originalKinds.has(node.kind) && !node.parent_id && node.relations.length === 0 && node.property_locks.length === 0 && node.keyframes.length <= 8);
export function distillationNegativeCase(scene: Scene): DistillationNegative | null {
  if (scene.renderer !== "motion-canvas") return { scene_id: scene.id, node_id: null, rejection: "non_motion_canvas" };
  const locked = scene.nodes.find(node => node.property_locks.length > 0);
  if (locked) return { scene_id: scene.id, node_id: locked.id, rejection: "locked_object" };
  const unsupported = scene.nodes.find(node => !originalKinds.has(node.kind));
  return unsupported ? { scene_id: scene.id, node_id: unsupported.id, rejection: "unsupported_kind" } : null;
}
function checkScope(draft: DistillationDraft, project: Project) {
  if (distillationScope(project, draft.target) !== draft.scope) throw new Error("This Distillation input belongs to another project, generation, branch or proposal.");
}
function proposal(project: Project, id: string | null): ComponentDistillationProposal | null {
  if (!id) return null;
  const found = (project.production_design?.distillations ?? []).filter(value => value.input.id === id);
  if (found.length > 1) throw new Error("The immutable proposal identity is ambiguous.");
  return found[0] ?? null;
}
function blankAuthor(): DistillationAuthorRaw { return { aId: "", bId: "", negativeId: "", rolesCount: "2", label: "", author: "", rights: "", requirements: "" }; }
function rawReview(saved: ComponentDistillationProposal | null): DistillationReviewRaw {
  return { reviewer: saved?.review?.reviewer ?? "", note: saved?.review?.rationale ?? "", verdict: saved?.review?.verdict ?? "compatible_proposal" };
}
function savedRaw(target: DistillationTarget, saved: ComponentDistillationProposal | null): DistillationRaw {
  if (target.kind === "review") return rawReview(saved);
  if (!saved) return blankAuthor();
  const input = saved.input;
  return { aId: input.positives[0]?.scene_id ?? "", bId: input.positives[1]?.scene_id ?? "", negativeId: input.negatives[0]?.scene_id ?? "",
    rolesCount: String(input.roles.length), label: input.label, author: input.author, rights: input.original_rights_attestation, requirements: input.requirements.join("\n") };
}
function currentBase(draft: DistillationDraft, project: Project) {
  return proposal(project, draft.target.kind === "review" ? draft.target.proposalId : draft.baseProposal?.input.id ?? null);
}
export function createDistillationDraft(project: Project, target: DistillationTarget): DistillationDraft {
  const baseProposal = target.kind === "review" ? proposal(project, target.proposalId) : null;
  return { schema: "motionwright.component-distillation-draft/1", scope: distillationScope(project, target), target: clone(target), baseRevision: project.revision,
    baseProposal: clone(baseProposal), raw: savedRaw(target, baseProposal), sequence: 0, intent: false, pending: null, selection: "" };
}
export function editDistillationDraft(draft: DistillationDraft, raw: DistillationRaw): DistillationDraft {
  if (!rawShape(raw, draft.target.kind)) throw new Error("Author and reviewer input cannot cross editor types.");
  return { ...draft, raw: clone(raw), sequence: draft.sequence + 1, intent: true };
}
export function selectDistillationReview(draft: DistillationDraft, id: string): DistillationDraft {
  if (draft.target.kind !== "author" || (id !== "" && !uuid(id))) throw new Error("Invalid Distillation proposal selection.");
  return { ...draft, selection: id };
}
export function reconcileDistillationDraft(draft: DistillationDraft, project: Project): DistillationDraft {
  checkScope(draft, project);
  if (distillationHasWork(draft) || draft.baseRevision === project.revision) return draft;
  // Even a clean author/reviewer checkpoint keeps the author's raw spelling.
  return { ...draft, baseRevision: project.revision, baseProposal: clone(currentBase(draft, project)) };
}
/** A failed first read is not an observed empty pointer. Keep the first
 * successful observation even if reading or decoding its copy then fails. */
export interface DistillationRecoveryPointer {
  observed: boolean; value: string | null;
  /** First successful byte observation, including an observed missing copy. */
  firstRead?: { key: string; writerId: string; raw: string | null };
}
export function observeDistillationRecoveryCopy(storage: Pick<Storage, "getItem">, scope: string,
  observation: DistillationRecoveryPointer): DistillationRecoveryPointer["firstRead"] {
  if (!observation.observed || observation.value === null) return undefined;
  const writerId = observation.value, key = distillationCopyKey(scope, writerId), raw = storage.getItem(key);
  if (observation.firstRead && (observation.firstRead.key !== key || observation.firstRead.raw !== raw))
    throw new Error("The original Distillation recovery bytes changed. The first observation remains available for download.");
  // Pin before decoding or another inventory/read operation can fail.
  observation.firstRead ??= { key, writerId, raw };
  return observation.firstRead;
}
export function readDistillationRecovery(storage: Pick<Storage, "getItem">, pointers: Pick<Storage, "getItem">,
  current: DistillationDraft, project: Project, observation: DistillationRecoveryPointer): DistillationDraft {
  checkScope(current, project);
  const pointerKey = distillationPointerKey(current.scope), pointer = pointers.getItem(pointerKey);
  if (observation.observed && pointer !== observation.value) throw new Error("The earlier pointer changed. Review its retained copies before another attempt.");
  observation.observed = true; observation.value = pointer;
  if (pointer === null) {
    if (pointers.getItem(pointerKey) !== null) throw new Error("The earlier pointer changed while recovery was being read.");
    return current;
  }
  const { key, raw } = observeDistillationRecoveryCopy(storage, current.scope, observation)!;
  if (raw === null) throw new Error("The earlier copy is still missing. Download the current input while recovery is unavailable.");
  const recovered = decodeDistillationCopy(raw, current.scope, pointer).draft;
  if (current.pending && (!recovered.pending || current.pending.id !== recovered.pending.id
    || current.pending.revision !== recovered.pending.revision || current.pending.sequence !== recovered.pending.sequence
    || !sameValue(current.pending.raw, recovered.pending.raw) || !sameValue(current.pending.submitted, recovered.pending.submitted)
    || !sameValue(current.pending.expected, recovered.pending.expected)))
    throw new Error("The current input retains a different Distillation attempt. Keep both copies before another decision.");
  let next = current;
  if (!distillationHasWork(current) && current.sequence === 0) next = reconcileDistillationDraft(recovered, project);
  else if (distillationUnconfirmed(recovered)) {
    const sequence = Math.max(recovered.sequence, current.sequence) + 1;
    if (!integer(sequence)) throw new Error("The Distillation edit sequence is exhausted. Download both inputs before continuing.");
    next = { ...recovered, raw: clone(current.raw), selection: current.selection, intent: true, sequence };
  }
  if (storage.getItem(key) !== raw || pointers.getItem(pointerKey) !== pointer)
    throw new Error("The earlier Distillation copy or pointer changed while recovery was being read.");
  return next;
}
export function distillationConflict(draft: DistillationDraft, project: Project): boolean { checkScope(draft, project); return distillationHasWork(draft) && draft.baseRevision !== project.revision; }
export function resolveDistillationDraft(draft: DistillationDraft, project: Project, choice: "saved" | "draft"): DistillationDraft {
  checkScope(draft, project);
  if (distillationUnconfirmed(draft)) throw new Error("Read the uncertain Distillation outcome before choosing a new base.");
  const base = currentBase(draft, project);
  return { ...draft, baseRevision: project.revision, baseProposal: clone(base), raw: choice === "saved" ? savedRaw(draft.target, base) : draft.raw,
    intent: choice === "draft", pending: null };
}
/** The existing synchronous domain validator derives comparison data only.
 * No prepared Project is published; the fresh Change goes to the coordinator. */
export function prepareDistillationSubmission(draft: DistillationDraft, project: Project, id: string): { draft: DistillationDraft; change: DistillationChange } {
  checkScope(draft, project);
  if (!uuid(id) || !draft.intent || distillationUnconfirmed(draft) || distillationConflict(draft, project)) throw new Error("Review the current revision and any earlier uncertain attempt before a separate Distillation decision.");
  let change: DistillationChange;
  if (draft.target.kind === "author") {
    const raw = draft.raw as DistillationAuthorRaw;
    if (!/^[0-9]+$/.test(raw.rolesCount.trim())) throw new Error("Choose a complete Distillation role count.");
    const count = Number(raw.rolesCount.trim());
    if (!Number.isSafeInteger(count) || count < 2 || count > 6) throw new Error("Choose 2–6 original Canvas roles.");
    const a = project.scenes.find(scene => scene.id === raw.aId && scene.renderer === "motion-canvas"), b = project.scenes.find(scene => scene.id === raw.bId && scene.renderer === "motion-canvas");
    const negativeScene = project.scenes.find(scene => scene.id === raw.negativeId), negative = negativeScene ? distillationNegativeCase(negativeScene) : null;
    const selected = a ? distillationCompatibleNodes(a).slice(0, count) : [], matches = b ? selected.map(node => distillationCompatibleNodes(b).find(candidate => candidate.kind === node.kind)) : [];
    if (!a || !b || a.id === b.id || !negative || negative.scene_id === a.id || negative.scene_id === b.id || selected.length !== count
      || matches.length !== count || matches.some(node => !node) || new Set(matches.map(node => node?.id)).size !== count)
      throw new Error("Choose two distinct compatible source variations and genuine independent counterevidence.");
    change = { type: "propose_component_distillation", input: { id: crypto.randomUUID(), label: raw.label.trim(), author: raw.author.trim(),
      original_rights_attestation: raw.rights.trim(), source_project_id: project.id, source_generation: project.generation, source_revision: project.revision,
      roles: selected.map((node, index) => ({ key: "slot_" + String(index + 1).padStart(2, "0"), kind: node.kind })),
      positives: [{ scene_id: a.id, node_ids: selected.map(node => node.id) }, { scene_id: b.id, node_ids: matches.map(node => node!.id) }],
      negatives: [negative], requirements: raw.requirements.split("\n").map(value => value.trim()).filter(Boolean) } };
  } else {
    const candidate = proposal(project, draft.target.proposalId), raw = draft.raw as DistillationReviewRaw;
    if (!candidate) throw new Error("Choose an existing source proposal before reviewing.");
    if (candidate.review) throw new Error("The recorded review is immutable. Your later rationale remains a separate editable draft.");
    change = { type: "review_component_distillation", id: candidate.input.id, review: { reviewer: raw.reviewer.trim(), rationale: raw.note.trim(), verdict: raw.verdict,
      source_revision: candidate.input.source_revision, compatibility: DISTILLATION_RENDERER } };
  }
  const checked = clone(project); applyDistillationChange(checked, change);
  const expected = proposal(checked, change.type === "propose_component_distillation" ? change.input.id : change.id);
  if (!expected) throw new Error("The source validator did not produce the expected immutable proposal.");
  return { change, draft: { ...draft, intent: true, pending: { id, revision: project.revision, sequence: draft.sequence, raw: clone(draft.raw),
    submitted: clone(change), expected: clone(expected), outcome: "UNKNOWN" } } };
}
export function retainDistillationFailure(draft: DistillationDraft, id: string, diagnostic: string, notSent = false): DistillationDraft {
  return draft.pending?.id === id ? { ...draft, pending: { ...draft.pending, outcome: notSent ? "NOT_SENT" : "UNKNOWN", diagnostic } } : draft;
}
export function acknowledgeDistillationDraft(draft: DistillationDraft, id: string, receipt: Project): DistillationDraft {
  const pending = draft.pending; if (!pending || pending.id !== id) return draft;
  checkScope(draft, receipt);
  if (receipt.revision !== pending.revision + 1 || !sameValue(proposal(receipt, pending.expected.input.id), pending.expected))
    throw new Error("The original reply does not confirm the exact submitted Distillation input, frozen samples and review.");
  return { ...draft, baseRevision: receipt.revision, baseProposal: clone(pending.expected), pending: null,
    intent: draft.sequence !== pending.sequence || !sameValue(draft.raw, pending.raw) };
}
export interface DistillationOutcomeTransport { readProject(id: string): Promise<Project>; readHistory(project: Project, after: number, limit: number): Promise<ProjectEvent[]> }
export interface DistillationOutcomeReview { scope: string; pending: DistillationPending; project: Project; events: ProjectEvent[]; matchingValueRevisions: number[] }
export function distillationOutcomeMatches(draft: DistillationDraft, project: Project, review: DistillationOutcomeReview | null): boolean {
  return !!review && distillationUnconfirmed(draft) && draft.scope === review.scope && distillationScope(project, draft.target) === draft.scope
    && sameValue(draft.pending, review.pending) && sameProjectRevision(project, review.project);
}
export async function inspectDistillationOutcome(draft: DistillationDraft, selected: Project, transport: DistillationOutcomeTransport): Promise<DistillationOutcomeReview> {
  checkScope(draft, selected); if (!distillationUnconfirmed(draft)) throw new Error("There is no uncertain Distillation attempt to inspect.");
  const pending = clone(draft.pending!), project = clone(await transport.readProject(selected.id)); checkScope(draft, project);
  if (!sameProjectRevision(project, selected)) throw new Error("Reload the saved project before inspecting this outcome.");
  if (!integer(project.revision) || project.revision < pending.revision || project.revision - pending.revision > MAX_HISTORY) throw new Error("History is outside the bounded 2048-revision review window.");
  const events: ProjectEvent[] = []; let cursor = pending.revision;
  do {
    const page = await transport.readHistory(project, cursor, 256);
    if (!Array.isArray(page) || page.length > 256 || (!page.length && cursor < project.revision)) throw new Error("The saved history is incomplete.");
    for (const event of page) {
      if (!event || !integer(event.revision) || event.revision !== cursor + 1 || event.revision > project.revision || !event.change || typeof event.change.type !== "string" || typeof event.created_at !== "string" || !event.created_at)
        throw new Error("The saved history is not a contiguous revision sequence.");
      events.push(clone(event)); cursor = event.revision;
    }
  } while (cursor < project.revision);
  const after = await transport.readProject(selected.id);
  if (!sameProjectRevision(after, project)) throw new Error("The saved project changed during inspection. Read it again.");
  return { scope: draft.scope, pending, project, events, matchingValueRevisions: events.filter(event => sameValue(event.change, pending.submitted)).map(event => event.revision) };
}
export function releaseDistillationOutcome(draft: DistillationDraft, project: Project, review: DistillationOutcomeReview): DistillationDraft {
  if (!distillationOutcomeMatches(draft, project, review)) throw new Error("Read the current project and its complete history before a separate intent.");
  return { ...draft, baseRevision: project.revision, baseProposal: clone(proposal(project, draft.pending!.expected.input.id)), pending: null, intent: true };
}

export type DistillationStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
export interface DistillationCustody { writerId: string; expectedRaw: string | null; expectedPointer: string | null }
export interface DistillationCopy { schema: "motionwright.component-distillation-recovery/1"; scope: string; writerId: string; updatedAt: number; draft: DistillationDraft }
export interface DistillationRecoveryEntry { key: string; writerId: string; raw: string; value: DistillationCopy | null }
const allStrings = (value: Record<string, unknown>, keys: readonly string[]) => keys.every(key => typeof value[key] === "string");
function rawShape(value: unknown, kind: DistillationTarget["kind"]): value is DistillationRaw {
  if (!record(value)) return false;
  if (kind === "author") { const keys = ["aId", "bId", "negativeId", "rolesCount", "label", "author", "rights", "requirements"]; return exact(value, keys) && allStrings(value, keys); }
  return exact(value, ["reviewer", "note", "verdict"]) && allStrings(value, ["reviewer", "note"]) && ["compatible_proposal", "rejected"].includes(String(value.verdict));
}

function nodesShape(value: unknown): boolean {
  return Array.isArray(value) && value.every(node => record(node)
    && exact(node, ["id", "name", "kind", "parent_id", "x", "y", "width", "height", "rotation_deg", "opacity", "text", "coordinate_space", "z_index", "style", "relations", "property_locks", "keyframes"])
    && ["id", "name", "kind", "coordinate_space"].every(key => typeof node[key] === "string")
    && ["parent_id", "text"].every(key => node[key] === null || typeof node[key] === "string")
    && ["x", "y", "width", "height", "rotation_deg", "opacity", "z_index"].every(key => typeof node[key] === "number" && Number.isFinite(node[key]))
    && record(node.style) && exact(node.style, ["fill", "stroke", "stroke_width", "font_family", "font_size", "font_weight", "line_height", "blend_mode"])
    && Object.values(node.style).every(item => item === null || typeof item === "string" || (typeof item === "number" && Number.isFinite(item)))
    && Array.isArray(node.property_locks) && node.property_locks.every(item => typeof item === "string") && Array.isArray(node.relations)
    && node.relations.every(relation => record(relation) && exact(relation, ["id", "kind", "target_id"]) && Object.values(relation).every(item => typeof item === "string"))
    && Array.isArray(node.keyframes) && node.keyframes.every(frame => record(frame) && exact(frame, ["at", "property", "value", "interpolation"])
      && record(frame.at) && exact(frame.at, ["num", "den"]) && typeof frame.at.num === "string" && typeof frame.at.den === "string"
      && typeof frame.property === "string" && typeof frame.interpolation === "string" && typeof frame.value === "number" && Number.isFinite(frame.value)));
}
function proposalShape(value: unknown, projectId: unknown): value is ComponentDistillationProposal {
  if (!record(value) || !exact(value, ["schema", "input", "samples", "review"]) || !record(value.input)) return false;
  const input = value.input;
  if (!exact(input, ["id", "label", "author", "original_rights_attestation", "source_project_id", "source_generation", "source_revision", "roles", "positives", "negatives", "requirements"])
    || !uuid(input.id) || input.source_project_id !== projectId || !allStrings(input, ["label", "author", "original_rights_attestation", "source_project_id", "source_generation"])
    || !integer(input.source_revision) || !Array.isArray(input.roles) || !Array.isArray(input.positives) || !Array.isArray(input.negatives)
    || !Array.isArray(input.requirements) || !input.requirements.every(value => typeof value === "string") || !Array.isArray(value.samples)) return false;
  if (!input.roles.every(role => record(role) && exact(role, ["key", "kind"]) && allStrings(role, ["key", "kind"]))
    || !input.positives.every(selector => record(selector) && exact(selector, ["scene_id", "node_ids"]) && uuid(selector.scene_id) && Array.isArray(selector.node_ids) && selector.node_ids.every(uuid))
    || !input.negatives.every(negative => record(negative) && exact(negative, ["scene_id", "node_id", "rejection"]) && uuid(negative.scene_id) && (negative.node_id === null || uuid(negative.node_id)) && ["non_motion_canvas", "locked_object", "unsupported_kind"].includes(String(negative.rejection)))
    || !value.samples.every(sample => record(sample) && exact(sample, ["scene_id", "nodes"]) && uuid(sample.scene_id) && nodesShape(sample.nodes))) return false;
  if (value.review !== null && (!record(value.review) || !exact(value.review, ["reviewer", "rationale", "verdict", "source_revision", "compatibility"])
    || !allStrings(value.review, ["reviewer", "rationale", "verdict", "compatibility"]) || !integer(value.review.source_revision))) return false;
  try { validateDistillationProposal(value as unknown as ComponentDistillationProposal); return true; } catch { return false; }
}
function changeShape(value: unknown, expected: ComponentDistillationProposal, target: DistillationTarget, revision: number, generation: unknown): value is DistillationChange {
  if (!record(value)) return false;
  if (target.kind === "author") return exact(value, ["type", "input"]) && value.type === "propose_component_distillation" && sameValue(value.input, expected.input)
    && expected.review === null && expected.input.source_revision === revision && expected.input.source_generation === generation;
  return exact(value, ["type", "id", "review"]) && value.type === "review_component_distillation" && value.id === target.proposalId
    && expected.input.id === target.proposalId && expected.review !== null && sameValue(value.review, expected.review);
}
function cleanDraft(value: unknown, scope: string): DistillationDraft {
  if (!record(value) || !exact(value, ["schema", "scope", "target", "baseRevision", "baseProposal", "raw", "sequence", "intent", "pending", "selection"])
    || value.schema !== "motionwright.component-distillation-draft/1" || value.scope !== scope || !record(value.target)
    || !integer(value.baseRevision) || !integer(value.sequence) || typeof value.intent !== "boolean" || typeof value.selection !== "string") throw new Error("Unsupported Distillation recovery shape. Earlier bytes were retained.");
  const target = value.target, identity: unknown = JSON.parse(scope);
  if ((target.kind !== "author" && target.kind !== "review") || (target.kind === "author" ? !exact(target, ["kind"])
    : !exact(target, ["kind", "proposalId"]) || (target.proposalId !== null && !uuid(target.proposalId)))
    || !Array.isArray(identity) || identity.length !== 5 || identity.slice(0, 3).some(item => typeof item !== "string" || !item)
    || identity[3] !== target.kind || identity[4] !== (target.kind === "review" ? target.proposalId : null)
    || !rawShape(value.raw, target.kind) || (value.selection !== "" && (!uuid(value.selection) || target.kind !== "author"))) throw new Error("This Distillation copy belongs to another editor scope.");
  if (value.baseProposal !== null && (!proposalShape(value.baseProposal, identity[0]) || value.baseProposal.input.source_revision > value.baseRevision
    || (target.kind === "review" && value.baseProposal.input.id !== target.proposalId))) throw new Error("The saved immutable comparison is unreadable or foreign.");
  let pending: DistillationPending | null = null;
  if (value.pending !== null) {
    const sent = value.pending;
    if (!record(sent) || !exact(sent, ["id", "revision", "sequence", "raw", "submitted", "expected", "outcome"], ["diagnostic"])
      || !uuid(sent.id) || !integer(sent.revision) || sent.revision !== value.baseRevision || !integer(sent.sequence) || sent.sequence > value.sequence
      || !rawShape(sent.raw, target.kind) || !proposalShape(sent.expected, identity[0]) || sent.expected.input.source_revision > sent.revision
      || !changeShape(sent.submitted, sent.expected, target as DistillationTarget, sent.revision, identity[1])
      || !["UNKNOWN", "NOT_SENT"].includes(String(sent.outcome)) || (sent.diagnostic !== undefined && typeof sent.diagnostic !== "string")) throw new Error("Unreadable Distillation comparison snapshot.");
    if (target.kind === "review" && (value.baseProposal === null || value.baseProposal.review !== null
      || !sameValue({ ...sent.expected, review: null }, value.baseProposal))) throw new Error("The pending review does not preserve its frozen original source.");
    // A recovered NOT_SENT record is conservative UNKNOWN, never replay permission.
    pending = { ...clone(sent), outcome: "UNKNOWN" } as unknown as DistillationPending;
  }
  return { schema: "motionwright.component-distillation-draft/1", scope, target: clone(target) as DistillationTarget, baseRevision: value.baseRevision,
    baseProposal: clone(value.baseProposal) as ComponentDistillationProposal | null, raw: clone(value.raw), sequence: value.sequence,
    intent: value.intent, pending, selection: value.selection };
}
export function decodeDistillationCopy(raw: string, scope: string, writerId: string): DistillationCopy {
  if (bytes(raw) > MAX_DISTILLATION_COPY_BYTES) throw new Error("The Distillation copy exceeds 1 MiB. Download the complete input before reducing it.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || !exact(value, ["schema", "scope", "writerId", "updatedAt", "draft"]) || value.schema !== "motionwright.component-distillation-recovery/1"
    || value.scope !== scope || value.writerId !== writerId || !uuid(writerId) || !integer(value.updatedAt)) throw new Error("Unreadable or foreign Distillation recovery copy.");
  return { schema: "motionwright.component-distillation-recovery/1", scope, writerId, updatedAt: value.updatedAt, draft: cleanDraft(value.draft, scope) };
}
export function encodeDistillationCopy(copy: DistillationCopy): string { const raw = JSON.stringify(copy); decodeDistillationCopy(raw, copy.scope, copy.writerId); return raw; }
export function encodeDistillationFile(draft: DistillationDraft): string {
  return JSON.stringify({ schema: "motionwright.component-distillation-file/1", exportedAt: Date.now(), draft: cleanDraft(draft, draft.scope) }, null, 2);
}
export function readDistillationCopies(storage: DistillationStorage, scope: string): { entries: DistillationRecoveryEntry[]; limited: boolean } {
  const prefix = DISTILLATION_RECOVERY_PREFIX + encodeURIComponent(scope) + ":", entries: DistillationRecoveryEntry[] = []; let limited = false;
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index); if (!key?.startsWith(prefix)) continue;
    if (entries.length >= MAX_DISTILLATION_COPIES) { limited = true; continue; }
    const raw = storage.getItem(key); if (raw === null) continue;
    const writerId = key.slice(prefix.length); let value: DistillationCopy | null = null;
    try { value = decodeDistillationCopy(raw, scope, writerId); } catch { /* Corrupt bytes remain available for explicit inspection/discard. */ }
    entries.push({ key, writerId, raw, value });
  }
  return { entries, limited };
}
export function writeDistillationCopy(storage: DistillationStorage, session: Pick<Storage, "getItem" | "setItem">,
  draft: DistillationDraft, custody: DistillationCustody): DistillationCustody {
  const key = distillationCopyKey(draft.scope, custody.writerId), pointerKey = distillationPointerKey(draft.scope);
  const check = () => {
    const pointer = session.getItem(pointerKey);
    if (pointer !== custody.expectedPointer && pointer !== custody.writerId) throw new Error("Another window changed the distillation writer pointer. Earlier copies were retained.");
    if (storage.getItem(key) !== custody.expectedRaw) throw new Error("The distillation copy changed in another window. Earlier bytes were retained.");
  };
  check();
  const raw = encodeDistillationCopy({ schema: "motionwright.component-distillation-recovery/1", scope: draft.scope, writerId: custody.writerId, updatedAt: Date.now(), draft });
  if (custody.expectedRaw !== null) {
    const previous = decodeDistillationCopy(custody.expectedRaw, draft.scope, custody.writerId).draft;
    if (!distillationHasWork(previous) && !sameValue(previous, draft)) throw new Error("A clean distillation checkpoint is immutable. Use a separate writer.");
  }
  let total = bytes(key) + bytes(raw), count = 1;
  for (let index = 0; index < storage.length; index++) {
    const other = storage.key(index); if (!other?.startsWith(DISTILLATION_RECOVERY_PREFIX) || other === key) continue;
    total += bytes(other) + bytes(storage.getItem(other) ?? ""); count++;
  }
  if (total > MAX_DISTILLATION_RECOVERY_BYTES || count > MAX_DISTILLATION_COPIES) throw new Error("Distillation recovery is full (64 copies / 4 MiB). Discard only copies you no longer need.");
  check(); // Recheck custody after enumeration can interleave another writer.
  storage.setItem(key, raw); if (storage.getItem(key) !== raw) throw new Error("The distillation data write was not confirmed.");
  custody.expectedRaw = raw;
  session.setItem(pointerKey, custody.writerId); if (session.getItem(pointerKey) !== custody.writerId) throw new Error("The distillation writer pointer was not confirmed.");
  if (storage.getItem(key) !== raw) throw new Error("The distillation data changed while its pointer was published.");
  custody.expectedPointer = custody.writerId; return custody;
}
export function retainReviewedDistillationIntent(storage: DistillationStorage, pointers: Pick<Storage, "getItem" | "setItem">,
  before: DistillationDraft, next: DistillationDraft, original: DistillationCustody, writer: string): DistillationCustody {
  if (!distillationUnconfirmed(before) || next.pending || before.scope !== next.scope || !uuid(writer) || writer === original.writerId || original.expectedRaw === null) throw new Error("Invalid distillation recovery fork.");
  const key = distillationCopyKey(before.scope, original.writerId), raw = storage.getItem(key);
  if (raw === null || raw !== original.expectedRaw || !sameValue(decodeDistillationCopy(raw, before.scope, original.writerId).draft, before)) throw new Error("Retain the exact earlier attempt before continuing.");
  const custody = { writerId: writer, expectedRaw: null, expectedPointer: original.expectedPointer };
  writeDistillationCopy(storage, pointers, before, custody);
  if (storage.getItem(key) !== raw) throw new Error("The earlier attempt changed during review.");
  writeDistillationCopy(storage, pointers, next, custody);
  if (storage.getItem(key) !== raw) throw new Error("The earlier attempt changed during review.");
  return custody;
}
