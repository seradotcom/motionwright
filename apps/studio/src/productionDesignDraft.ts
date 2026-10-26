import { sameProjectRevision } from "./projectMutation";
import { applyProductionDesignChange, defaultHeroConfig, planContentDigest, realizeProductHero, sameValue, validateProductionPlan } from "./creativeProduction";
import type { HeroConfig, NativeCapsule, ProductHeroInstance, ProductionPlan } from "./creativeProduction";
import type { CanvasNode, Change, Project, ProjectEvent } from "./types";

export type ProductionTarget = { kind: "hero"; sceneId: string | null } | { kind: "plan" } | { kind: "source"; sceneId: string | null };
export type ProductionInput = { kind: "hero"; config: HeroConfig }
  | { kind: "plan"; plan: ProductionPlan; reviewer: string; note: string }
  | { kind: "source"; assetId: string; renderer: string; version: string };
export type ProductionValue = ProductHeroInstance | ProductionPlan | NativeCapsule | null;
export type ProductionOperation = "save" | "approve" | "detach";
export type ProductionChange = Extract<Change, { type: "upsert_product_hero" | "detach_product_hero" | "set_production_plan" | "upsert_native_capsule" }>;
export interface ProductionPending {
  /** Local comparison snapshot, never a service request, grant, or replay token. */
  id: string; revision: number; sequence: number; operation: ProductionOperation;
  input: ProductionInput; value: ProductionValue; nodesBefore: CanvasNode[] | null;
  outcome: "UNKNOWN" | "NOT_SENT"; diagnostic?: string;
}
export interface ProductionOrigin { key: string; raw: string }
export interface ProductionDraft {
  schema: "motionwright.production-draft/1"; scope: string; target: ProductionTarget;
  baseRevision: number; base: ProductionValue; input: ProductionInput;
  sequence: number; intent: boolean; pending: ProductionPending | null;
  /** Exact retained comparisons, never grants or permission to replay. Older v1 copies may omit this field. */
  origins?: ProductionOrigin[];
}
export const PRODUCTION_RECOVERY_PREFIX = "motionwright.production-recovery.v1:";
export const PRODUCTION_POINTER_PREFIX = "motionwright.production-writer.v1:";
export const MAX_PRODUCTION_COPY_BYTES = 1024 * 1024;
export const MAX_PRODUCTION_RECOVERY_BYTES = 4 * 1024 * 1024;
export const MAX_PRODUCTION_COPIES = 64;
export const MAX_PRODUCTION_HISTORY = 2048;
export const MAX_PRODUCTION_ORIGINS = 16;
const SOURCE_LOSS = "Source preserved as an opaque attachment. Parameter editing and renderer execution are not admitted.";
const SOURCE_EDITOR_HINT = "Open with the original application's trusted environment";
export const productionScope = (project: Project, target: ProductionTarget) => JSON.stringify([
  project.id, project.generation, project.active_branch, target.kind, target.kind === "plan" ? null : target.sceneId,
]);
export const productionCopyKey = (scope: string, writer: string) => PRODUCTION_RECOVERY_PREFIX + encodeURIComponent(scope) + ":" + writer;
export const productionPointerKey = (scope: string) => PRODUCTION_POINTER_PREFIX + encodeURIComponent(scope);
export const productionLabel = (target: ProductionTarget) => target.kind === "hero" ? "Component" : target.kind === "source" ? "Source attachment" : "Production plan";
export const productionHasInput = (draft: ProductionDraft) => draft.intent;
export const productionHasWork = (draft: ProductionDraft) => draft.intent || draft.pending !== null;
export const productionUnconfirmed = (draft: ProductionDraft) => draft.pending?.outcome === "UNKNOWN";
const clone = <T,>(value: T): T => structuredClone(value);
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const uuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const bytes = (value: string) => new TextEncoder().encode(value).length;
const exact = (value: Record<string, unknown>, keys: readonly string[], optional: readonly string[] = []) =>
  keys.every(key => Object.hasOwn(value, key)) && Object.keys(value).every(key => keys.includes(key) || optional.includes(key));

export function defaultProductionPlan(project: Project): ProductionPlan {
  return { objective: project.brief.objective || "", audience: project.brief.audience || "", concept: project.narrative.premise || "",
    reference_constraints: [], exclusions: ["Do not present a graphic study as observed product behavior."],
    shots: project.scenes.map(scene => ({ scene_id: scene.id, purpose: scene.objective || scene.name, claim_ids: [], asset_ids: [], evidence_kind: "graphic_study" })),
    clock: { kind: "timeline" }, approval: null };
}
export function productionValue(project: Project, target: ProductionTarget): ProductionValue {
  // This editor creates one attachment per explicit intent; no existing capsule
  // is chosen implicitly as its base, even when the scene already has sources.
  if (target.kind === "source") return null;
  if (target.kind === "plan") return clone(project.production_design?.plan ?? null);
  if (target.sceneId !== null && project.scenes.filter(scene => scene.id === target.sceneId).length !== 1)
    throw new Error("The component scene is unavailable or ambiguous in the current project.");
  const matches = project.production_design?.heroes.filter(hero => hero.scene_id === target.sceneId) ?? [];
  if (matches.length > 1) throw new Error("The scene has ambiguous managed component identities.");
  return clone(matches[0] ?? null);
}
function inputFromSaved(project: Project, target: ProductionTarget): ProductionInput {
  if (target.kind === "source") return { kind: "source", assetId: "", renderer: "hyperframes", version: "unverified" };
  const value = productionValue(project, target);
  return target.kind === "hero" ? { kind: "hero", config: clone((value as ProductHeroInstance | null)?.config ?? defaultHeroConfig()) }
    : { kind: "plan", plan: { ...clone((value as ProductionPlan | null) ?? defaultProductionPlan(project)), approval: null }, reviewer: "", note: "" };
}
function checkScope(draft: ProductionDraft, project: Project) {
  if (productionScope(project, draft.target) !== draft.scope) throw new Error("This Production input belongs to another project, generation, branch, or scene.");
  productionValue(project, draft.target);
}
export function createProductionDraft(project: Project, target: ProductionTarget): ProductionDraft {
  return { schema: "motionwright.production-draft/1", scope: productionScope(project, target), target: clone(target),
    baseRevision: project.revision, base: productionValue(project, target), input: inputFromSaved(project, target), sequence: 0, intent: false, pending: null, origins: [] };
}
export function editProductionDraft(draft: ProductionDraft, input: ProductionInput): ProductionDraft {
  if (input.kind !== draft.target.kind) throw new Error("Production input cannot cross editor targets.");
  if (!integer(draft.sequence) || draft.sequence === Number.MAX_SAFE_INTEGER)
    throw new Error("The Production edit sequence cannot advance safely. Download the current input before continuing.");
  return { ...draft, input: clone(input), sequence: draft.sequence + 1, intent: true };
}
export interface ProductionPointerObservation { observed: boolean; expectedPointer: string | null }
export function readProductionRecoveryPointer(storage: Pick<Storage, "getItem">, scope: string,
  prior: ProductionPointerObservation): ProductionPointerObservation {
  const identity: unknown = JSON.parse(scope);
  if (!Array.isArray(identity) || identity.length !== 5 || identity.slice(0, 3).some(value => typeof value !== "string" || !value)
    || !["hero", "plan", "source"].includes(identity[3]) || (identity[3] === "plan" ? identity[4] !== null : identity[4] !== null && (typeof identity[4] !== "string" || !identity[4])))
    throw new Error("The Production recovery owner is invalid.");
  const pointer = storage.getItem(productionPointerKey(scope));
  if (prior.observed && pointer !== prior.expectedPointer) throw new Error("The earlier Production recovery pointer changed.");
  return { observed: true, expectedPointer: pointer };
}
const productionAttempt = (pending: ProductionPending) => ({ id: pending.id, revision: pending.revision, sequence: pending.sequence,
  operation: pending.operation, input: pending.input, value: pending.value, nodesBefore: pending.nodesBefore });
/** Reading again cannot manufacture an edit or replace an edit made while recovery was blocked. */
export function recoverProductionAfterReadFailure(recovered: ProductionDraft, current: ProductionDraft,
  editedWhileBlocked: boolean, project: Project): ProductionDraft {
  checkScope(recovered, project); checkScope(current, project);
  if (recovered.scope !== current.scope) throw new Error("The recovered Production input belongs to another editor target.");
  if (current.pending && (!recovered.pending || !sameValue(productionAttempt(current.pending), productionAttempt(recovered.pending))))
    throw new Error("The current and recovered Production attempts differ.");
  if (![current.sequence, recovered.sequence].every(integer)) throw new Error("The recovered Production edit sequence is invalid.");
  const maximum = Math.max(current.sequence, recovered.sequence);
  if (editedWhileBlocked && maximum === Number.MAX_SAFE_INTEGER) throw new Error("The Production edit sequence cannot advance safely.");
  const origins = new Map<string, ProductionOrigin>();
  for (const origin of [...(recovered.origins ?? []), ...(current.origins ?? [])]) {
    const earlier = origins.get(origin.key);
    if (earlier && earlier.raw !== origin.raw) throw new Error("A retained Production origin has conflicting bytes.");
    origins.set(origin.key, clone(origin));
  }
  if (origins.size > MAX_PRODUCTION_ORIGINS) throw new Error("The Production recovery origin limit was reached. Download the retained input.");
  return reconcileProductionDraft({ ...recovered,
    input: clone(editedWhileBlocked ? current.input : recovered.input),
    intent: editedWhileBlocked ? true : recovered.intent,
    sequence: editedWhileBlocked ? maximum + 1 : recovered.sequence,
    pending: recovered.pending ? { ...clone(recovered.pending), outcome: "UNKNOWN" } : null,
    origins: [...origins.values()],
  }, project);
}
/** Changes observed through props never consume an original pending request. */
export function reconcileProductionDraft(draft: ProductionDraft, project: Project): ProductionDraft {
  checkScope(draft, project);
  if (draft.pending || draft.intent) return draft;
  const value = productionValue(project, draft.target);
  if (draft.baseRevision === project.revision && sameValue(draft.base, value)) return draft;
  return { ...draft, base: value, baseRevision: project.revision, input: inputFromSaved(project, draft.target) };
}
export function productionConflict(draft: ProductionDraft, project: Project): boolean {
  checkScope(draft, project);
  return productionHasWork(draft) && (draft.baseRevision !== project.revision || !sameValue(draft.base, productionValue(project, draft.target)));
}
export function resolveProductionDraft(draft: ProductionDraft, project: Project, choice: "saved" | "draft"): ProductionDraft {
  checkScope(draft, project);
  if (productionUnconfirmed(draft)) throw new Error("Inspect the unconfirmed Production outcome before choosing another base.");
  return { ...draft, base: productionValue(project, draft.target), baseRevision: project.revision,
    input: choice === "saved" ? inputFromSaved(project, draft.target) : draft.input, intent: choice === "draft", pending: null };
}
export function cleanProductionPlan(plan: ProductionPlan): ProductionPlan {
  return { ...clone(plan), reference_constraints: plan.reference_constraints.map(value => value.trim()).filter(Boolean),
    exclusions: plan.exclusions.map(value => value.trim()).filter(Boolean), approval: null };
}
/** No caller may dispatch after this asynchronous preparation unless its source
 * identity, revision, sequence, and mounted editor epoch are still current. */
export async function prepareProductionSubmission(draft: ProductionDraft, project: Project, id: string,
  operation: ProductionOperation = "save"): Promise<{ draft: ProductionDraft; change: ProductionChange }> {
  checkScope(draft, project);
  if (!uuid(id) || productionUnconfirmed(draft)) throw new Error("Review the unconfirmed Production attempt before a separate save.");
  if (productionConflict(draft, project)) throw new Error("Review the current Production revision before saving this draft.");
  const input = clone(draft.input), target = draft.target;
  const resource = target.kind !== "plan" && target.sceneId ? "scene:" + target.sceneId : null;
  const lockKinds = target.kind === "hero" && operation !== "detach" ? ["content", "position", "style", "timing", "renderer"] : ["content"];
  if (project.locks.some(lock => (lock.resource === "project:" + project.id || lock.resource === resource) && lockKinds.includes(lock.kind)))
    throw new Error("This Production scope is locked. Your input remains editable.");
  let change: ProductionChange, value: ProductionValue, nodesBefore: CanvasNode[] | null = null;
  if (target.kind === "hero" && input.kind === "hero") {
    const scene = project.scenes.find(scene => scene.id === target.sceneId);
    if (!scene || scene.renderer !== "motion-canvas" || Number(scene.duration.num) / Number(scene.duration.den) < 2)
      throw new Error("Select a Motion Canvas scene at least two seconds long.");
    const existing = productionValue(project, target) as ProductHeroInstance | null;
    if (operation === "approve") throw new Error("Component saving does not grant concept approval.");
    if (operation === "detach") {
      if (!existing) throw new Error("The managed component no longer exists.");
      change = { type: "detach_product_hero", instance_id: existing.id }; value = null; nodesBefore = clone(scene.nodes);
    } else {
      const instanceId = existing?.id ?? crypto.randomUUID(), config = { ...clone(input.config), layout: input.config.layout ?? "product_hero_reveal" as const };
      const baseline = await realizeProductHero(instanceId, config);
      value = { id: instanceId, scene_id: scene.id, component_version: 1, config, baseline };
      change = { type: "upsert_product_hero", instance_id: instanceId, scene_id: scene.id, config: clone(config) };
    }
  } else if (target.kind === "source" && input.kind === "source") {
    if (operation !== "save") throw new Error("A source attachment only supports an explicit save.");
    if (!draft.intent) throw new Error("Edit the source input to create an explicit new attachment intent.");
    const scenes = project.scenes.filter(scene => scene.id === target.sceneId);
    const assets = project.assets.filter(asset => asset.id === input.assetId);
    if (scenes.length !== 1) throw new Error("Select one existing scene for this source attachment.");
    if (assets.length !== 1 || !assets[0].content_sha256 || !/^[0-9a-f]{64}$/.test(assets[0].content_sha256))
      throw new Error("Select one existing source asset with its valid SHA-256 digest.");
    if (project.production_design?.capsules.some(capsule => capsule.id === id))
      throw new Error("This attachment identity already exists. Start a separate explicit intent.");
    const capsule: NativeCapsule = { id, scene_id: scenes[0].id, source_asset_id: assets[0].id,
      source_sha256: assets[0].content_sha256, label: assets[0].name,
      fidelity: { renderer: input.renderer, renderer_version: input.version, visual: "unavailable", temporal: "unavailable",
        structural: "native", editable: "unavailable", losses: [SOURCE_LOSS], evidence_sha256: null },
      editable_parameters: [], native_editor_hint: SOURCE_EDITOR_HINT };
    change = { type: "upsert_native_capsule", capsule }; value = clone(capsule);
    // Reuse the existing validation on a temporary copy. This is neither a
    // service result nor renderer/native admission, and never updates the UI.
    await applyProductionDesignChange(clone(project), change);
  } else if (target.kind === "plan" && input.kind === "plan") {
    if (operation === "detach") throw new Error("A Production plan is not a detachable component.");
    const plan = cleanProductionPlan(input.plan);
    if (operation === "approve") plan.approval = { reviewer: input.reviewer, note: input.note, content_sha256: await planContentDigest(plan) };
    await validateProductionPlan(project, plan);
    value = plan; change = { type: "set_production_plan", plan: clone(plan) };
  } else throw new Error("Production input and target disagree.");
  return { change, draft: { ...draft, intent: true, pending: { id, revision: project.revision, sequence: draft.sequence,
    operation, input, value, nodesBefore, outcome: "UNKNOWN" } } };
}
export function productionPreparationMatches(source: ProductionDraft, preparedFrom: Project, current: ProductionDraft, project: Project): boolean {
  return source.scope === current.scope && source.sequence === current.sequence && sameValue(source.input, current.input)
    && sameValue(source.pending, current.pending) && sameProjectRevision(preparedFrom, project)
    && productionScope(project, source.target) === source.scope
    && (source.target.kind === "source" ? sameValue(preparedFrom, project)
      : sameValue(productionValue(preparedFrom, source.target), productionValue(project, source.target)));
}
export function retainProductionFailure(draft: ProductionDraft, id: string, diagnostic: string, notSent = false): ProductionDraft {
  return draft.pending?.id === id ? { ...draft, pending: { ...draft.pending, outcome: notSent ? "NOT_SENT" : "UNKNOWN", diagnostic } } : draft;
}
/** Only the Project returned by the original throwing coordinator is admissible. */
export function acknowledgeProductionDraft(draft: ProductionDraft, id: string, receipt: Project): ProductionDraft {
  const pending = draft.pending;
  if (!pending || pending.id !== id) return draft;
  checkScope(draft, receipt);
  const saved = productionValue(receipt, draft.target);
  const sourceMatches = draft.target.kind === "source" ? receipt.production_design?.capsules.filter(capsule => capsule.id === pending.id) ?? [] : [];
  const matches = draft.target.kind === "source"
    ? pending.operation === "save" && sourceMatches.length === 1 && sourceMatches[0].scene_id === draft.target.sceneId
      && sameValue(sourceMatches[0], pending.value)
    : draft.target.kind === "hero" && pending.operation === "save"
    ? saved !== null && pending.value !== null && "config" in saved && "config" in pending.value
      && saved.id === pending.value.id && saved.scene_id === pending.value.scene_id
      && saved.component_version === pending.value.component_version && sameValue(saved.config, pending.value.config)
    : sameValue(saved, pending.value);
  if (receipt.revision !== pending.revision + 1 || !matches)
    throw new Error("The original Production reply does not confirm the exact submitted snapshot.");
  if (pending.operation === "detach" && draft.target.kind === "hero") {
    const sceneId = draft.target.sceneId;
    if (!sameValue(receipt.scenes.find(scene => scene.id === sceneId)?.nodes, pending.nodesBefore))
      throw new Error("The original detach reply did not preserve the editable scene objects.");
  }
  const later = draft.sequence !== pending.sequence;
  return { ...draft, base: saved, baseRevision: receipt.revision, pending: null,
    // Never replace raw author input with the cleaned submission. In particular
    // list spacing and later reviewer/note changes stay available after an ACK.
    intent: later || pending.operation === "detach" };
}

export interface ProductionOutcomeTransport {
  readProject(projectId: string): Promise<Project>;
  readHistory(project: Project, afterRevision: number, limit: number): Promise<ProjectEvent[]>;
}
export interface ProductionOutcomeReview { scope: string; pending: ProductionPending; project: Project; events: ProjectEvent[]; matchingValueRevisions: number[] }
const sameObserved = (left: Project, right: Project, target: ProductionTarget) => sameProjectRevision(left, right)
  && (target.kind === "source" ? sameValue(left, right) : sameValue(productionValue(left, target), productionValue(right, target)));
export function productionOutcomeMatches(draft: ProductionDraft, project: Project, review: ProductionOutcomeReview | null): boolean {
  return !!review && productionUnconfirmed(draft) && review.scope === draft.scope && productionScope(project, draft.target) === draft.scope
    && sameValue(draft.pending, review.pending) && sameObserved(project, review.project, draft.target);
}
export async function inspectProductionOutcome(draft: ProductionDraft, selected: Project, transport: ProductionOutcomeTransport): Promise<ProductionOutcomeReview> {
  checkScope(draft, selected);
  if (!productionUnconfirmed(draft)) throw new Error("There is no unconfirmed Production outcome to inspect.");
  const pending = clone(draft.pending!), observed = clone(await transport.readProject(selected.id));
  if (!sameObserved(observed, selected, draft.target) || productionScope(observed, draft.target) !== draft.scope)
    throw new Error("Reload the current saved project before inspecting this Production outcome.");
  if (!integer(observed.revision) || observed.revision < pending.revision || observed.revision - pending.revision > MAX_PRODUCTION_HISTORY)
    throw new Error("The Production history is outside the bounded review window.");
  const events: ProjectEvent[] = []; let cursor = pending.revision;
  do {
    const page = await transport.readHistory(observed, cursor, 256);
    if (!Array.isArray(page) || page.length > 256 || (!page.length && cursor < observed.revision)) throw new Error("Production history is incomplete.");
    for (const event of page) {
      if (!event || !integer(event.revision) || event.revision !== cursor + 1 || event.revision > observed.revision
        || !event.change || typeof event.change.type !== "string" || typeof event.created_at !== "string" || !event.created_at)
        throw new Error("Production history is not a contiguous revision sequence.");
      events.push(clone(event)); cursor = event.revision;
    }
  } while (cursor < observed.revision);
  const after = await transport.readProject(selected.id);
  if (!sameObserved(after, observed, draft.target)) throw new Error("The project changed during the Production history read. Inspect it again.");
  return { scope: draft.scope, pending, project: observed, events,
    matchingValueRevisions: events.filter(event => {
      if (draft.target.kind === "plan") return event.change.type === "set_production_plan" && sameValue(event.change.plan, pending.value);
      if (draft.target.kind === "source") return event.change.type === "upsert_native_capsule" && record(event.change.capsule)
        && event.change.capsule.id === pending.id && sameValue(event.change.capsule, pending.value);
      if (pending.operation === "detach") return event.change.type === "detach_product_hero"
        && event.change.instance_id === (draft.base as ProductHeroInstance | null)?.id;
      const expected = pending.value as ProductHeroInstance;
      return event.change.type === "upsert_product_hero" && event.change.instance_id === expected.id
        && event.change.scene_id === expected.scene_id && sameValue(event.change.config, expected.config);
    }).map(event => event.revision) };
}
export function releaseProductionOutcome(draft: ProductionDraft, project: Project, review: ProductionOutcomeReview): ProductionDraft {
  if (!productionOutcomeMatches(draft, project, review)) throw new Error("Read the current saved project and full history before starting a separate Production intent.");
  if (project.revision <= draft.pending!.revision)
    throw new Error("The unconfirmed Production attempt could still commit at this revision. Keep its original copy until the saved project advances.");
  return { ...draft, base: productionValue(project, draft.target), baseRevision: project.revision, intent: true, pending: null };
}

export type ProductionStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
export interface ProductionCustody { writerId: string; expectedRaw: string | null; expectedPointer: string | null }
export interface ProductionCopy { schema: "motionwright.production-recovery/1"; scope: string; writerId: string; updatedAt: number; draft: ProductionDraft }
export interface ProductionRecoveryEntry { key: string; writerId: string; raw: string; value: ProductionCopy | null }
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(part => typeof part === "string");
function inputShape(value: unknown): value is ProductionInput {
  if (!record(value)) return false;
  if (value.kind === "source") return exact(value, ["kind", "assetId", "renderer", "version"])
    && ["assetId", "renderer", "version"].every(key => typeof value[key] === "string");
  if (value.kind === "hero") {
    const config = value.config;
    return exact(value, ["kind", "config"]) && record(config) && exact(config, ["eyebrow", "headline", "body", "wordmark", "foreground", "accent", "motion"], ["layout"])
      && ["eyebrow", "headline", "body", "wordmark", "foreground", "accent"].every(key => typeof config[key] === "string")
      && typeof config.motion === "boolean" && (config.layout === undefined || ["product_hero_reveal", "split_explanation", "metric_evidence", "step_sequence", "contrast_comparison", "editorial_quote"].includes(String(config.layout)));
  }
  if (value.kind !== "plan" || !exact(value, ["kind", "plan", "reviewer", "note"]) || typeof value.reviewer !== "string" || typeof value.note !== "string") return false;
  const plan = value.plan;
  if (!record(plan) || !exact(plan, ["objective", "audience", "concept", "reference_constraints", "exclusions", "shots", "clock", "approval"])
    || !["objective", "audience", "concept"].every(key => typeof plan[key] === "string") || !strings(plan.reference_constraints) || !strings(plan.exclusions)
    || !Array.isArray(plan.shots) || plan.approval !== null || !record(plan.clock)) return false;
  if (!plan.shots.every(shot => record(shot) && exact(shot, ["scene_id", "purpose", "claim_ids", "asset_ids", "evidence_kind"])
    && typeof shot.scene_id === "string" && typeof shot.purpose === "string" && strings(shot.claim_ids) && strings(shot.asset_ids)
    && ["graphic_study", "real_capture", "licensed_footage", "synthetic_labeled"].includes(String(shot.evidence_kind)))) return false;
  const clock = plan.clock;
  return clock.kind === "timeline" ? exact(clock, ["kind"])
    : clock.kind === "voice" ? exact(clock, ["kind", "voice_track_id"]) && typeof clock.voice_track_id === "string"
      : clock.kind === "music" && exact(clock, ["kind", "asset_id", "beats"]) && typeof clock.asset_id === "string" && Array.isArray(clock.beats)
        && clock.beats.every(at => record(at) && exact(at, ["num", "den"]) && typeof at.num === "string" && typeof at.den === "string");
}
function nodesShape(value: unknown): boolean {
  return Array.isArray(value) && value.every(node => record(node)
    && exact(node, ["id", "name", "kind", "parent_id", "x", "y", "width", "height", "rotation_deg", "opacity", "text", "coordinate_space", "z_index", "style", "relations", "property_locks", "keyframes"])
    && ["id", "name", "kind", "coordinate_space"].every(key => typeof node[key] === "string")
    && ["parent_id", "text"].every(key => node[key] === null || typeof node[key] === "string")
    && ["x", "y", "width", "height", "rotation_deg", "opacity", "z_index"].every(key => typeof node[key] === "number" && Number.isFinite(node[key]))
    && record(node.style) && exact(node.style, ["fill", "stroke", "stroke_width", "font_family", "font_size", "font_weight", "line_height", "blend_mode"])
    && Object.values(node.style).every(item => item === null || typeof item === "string" || (typeof item === "number" && Number.isFinite(item)))
    && strings(node.property_locks) && Array.isArray(node.relations)
    && node.relations.every(relation => record(relation) && exact(relation, ["id", "kind", "target_id"]) && Object.values(relation).every(item => typeof item === "string"))
    && Array.isArray(node.keyframes) && node.keyframes.every(frame => record(frame) && exact(frame, ["at", "property", "value", "interpolation"])
      && record(frame.at) && exact(frame.at, ["num", "den"]) && typeof frame.at.num === "string" && typeof frame.at.den === "string"
      && typeof frame.property === "string" && typeof frame.interpolation === "string" && typeof frame.value === "number" && Number.isFinite(frame.value)));
}
function valueShape(value: unknown, kind: ProductionTarget["kind"]): boolean {
  if (value === null) return true;
  if (!record(value)) return false;
  if (kind === "source") {
    const fidelity = value.fidelity;
    const text = (item: unknown, maximum: number) => typeof item === "string" && !!item.trim()
      && bytes(item) <= maximum && !/[\u0000-\u0009\u000b-\u001f\u007f]/u.test(item);
    return exact(value, ["id", "scene_id", "source_asset_id", "source_sha256", "label", "fidelity", "editable_parameters", "native_editor_hint"])
      && uuid(value.id) && typeof value.scene_id === "string" && !!value.scene_id
      && typeof value.source_asset_id === "string" && !!value.source_asset_id
      && typeof value.source_sha256 === "string" && /^[0-9a-f]{64}$/.test(value.source_sha256) && text(value.label, 256)
      && Array.isArray(value.editable_parameters) && value.editable_parameters.length === 0 && value.native_editor_hint === SOURCE_EDITOR_HINT
      && record(fidelity) && exact(fidelity, ["renderer", "renderer_version", "visual", "temporal", "structural", "editable", "losses", "evidence_sha256"])
      && text(fidelity.renderer, 128) && text(fidelity.renderer_version, 128)
      && fidelity.visual === "unavailable" && fidelity.temporal === "unavailable" && fidelity.structural === "native" && fidelity.editable === "unavailable"
      && sameValue(fidelity.losses, [SOURCE_LOSS]) && fidelity.evidence_sha256 === null;
  }
  if (kind === "hero") return exact(value, ["id", "scene_id", "component_version", "config", "baseline"])
    && typeof value.id === "string" && typeof value.scene_id === "string" && integer(value.component_version)
    && inputShape({ kind: "hero", config: value.config }) && nodesShape(value.baseline);
  return inputShape({ kind: "plan", plan: { ...value, approval: null }, reviewer: "", note: "" })
    && (value.approval === null || (record(value.approval) && exact(value.approval, ["reviewer", "note", "content_sha256"])
      && typeof value.approval.reviewer === "string" && typeof value.approval.note === "string"
      && typeof value.approval.content_sha256 === "string" && /^[0-9a-f]{64}$/.test(value.approval.content_sha256)));
}
function cleanDraft(value: unknown, scope: string, originDepth = 0): ProductionDraft {
  if (!record(value) || !exact(value, ["schema", "scope", "target", "baseRevision", "base", "input", "sequence", "intent", "pending"], ["origins"])
    || value.schema !== "motionwright.production-draft/1" || value.scope !== scope || !record(value.target)
    || !integer(value.baseRevision) || !integer(value.sequence) || typeof value.intent !== "boolean" || !inputShape(value.input))
    throw new Error("This Production recovery has an unsupported shape.");
  const target = value.target, identity: unknown = JSON.parse(scope);
  if ((target.kind !== "hero" && target.kind !== "plan" && target.kind !== "source") || value.input.kind !== target.kind
    || !exact(target, target.kind === "plan" ? ["kind"] : ["kind", "sceneId"])
    || (target.kind !== "plan" && target.sceneId !== null && (typeof target.sceneId !== "string" || !target.sceneId))
    || !Array.isArray(identity) || identity.length !== 5 || identity.slice(0, 3).some(item => typeof item !== "string" || !item)
    || identity[3] !== target.kind || identity[4] !== (target.kind === "plan" ? null : target.sceneId))
    throw new Error("This Production recovery belongs to another editor identity.");
  // Closed shapes keep grants and execution authority out of portable copies.
  // Saved values are comparison data only; they never enter a Project mutation.
  if (!valueShape(value.base, target.kind) || (target.kind === "source" && value.base !== null)) throw new Error("Unreadable Production base snapshot.");
  let pending: ProductionPending | null = null;
  if (value.pending !== null) {
    const sent = value.pending;
    if (!record(sent) || !exact(sent, ["id", "revision", "sequence", "operation", "input", "value", "nodesBefore", "outcome"], ["diagnostic"])
      || !uuid(sent.id) || !integer(sent.revision) || !integer(sent.sequence) || sent.sequence > value.sequence
      || !["save", "approve", "detach"].includes(String(sent.operation)) || !inputShape(sent.input) || sent.input.kind !== target.kind
      || !valueShape(sent.value, target.kind) || (sent.nodesBefore !== null && !nodesShape(sent.nodesBefore))
      || (sent.operation === "approve" && target.kind !== "plan") || (sent.operation === "detach" && target.kind !== "hero")
      || (sent.operation === "detach" ? sent.value !== null || sent.nodesBefore === null : sent.value === null || sent.nodesBefore !== null)
      || !["UNKNOWN", "NOT_SENT"].includes(String(sent.outcome)) || (sent.diagnostic !== undefined && typeof sent.diagnostic !== "string"))
      throw new Error("Unreadable Production pending comparison snapshot.");
    if (target.kind === "source") {
      const capsule = sent.value;
      if (sent.revision !== value.baseRevision || sent.operation !== "save" || sent.input.kind !== "source" || !record(capsule)
        || capsule.id !== sent.id || capsule.scene_id !== target.sceneId || capsule.source_asset_id !== sent.input.assetId
        || !record(capsule.fidelity) || capsule.fidelity.renderer !== sent.input.renderer || capsule.fidelity.renderer_version !== sent.input.version)
        throw new Error("The source attachment snapshot does not match its exact creation intent.");
    }
    // A recovered NOT_SENT is never permission to replay or authorize a call.
    pending = { ...clone(sent), outcome: "UNKNOWN" } as unknown as ProductionPending;
  }
  const originalValues = value.origins === undefined ? [] : value.origins, origins: ProductionOrigin[] = [], keys = new Set<string>();
  if (!Array.isArray(originalValues) || originalValues.length > MAX_PRODUCTION_ORIGINS
    || (originalValues.length > 0 && originDepth >= MAX_PRODUCTION_ORIGINS)) throw new Error("The Production recovery origins exceed their bounded limit.");
  for (const origin of originalValues) {
    if (!record(origin) || !exact(origin, ["key", "raw"]) || typeof origin.key !== "string" || typeof origin.raw !== "string" || keys.has(origin.key))
      throw new Error("Unreadable Production recovery origin.");
    const writerId = origin.key.slice(origin.key.lastIndexOf(":") + 1);
    if (origin.key !== productionCopyKey(scope, writerId)) throw new Error("A Production recovery origin belongs to another editor.");
    const original = decodeProductionCopyAtDepth(origin.raw, scope, writerId, originDepth + 1);
    if (!productionUnconfirmed(original.draft)) throw new Error("A Production recovery origin has no retained unconfirmed attempt.");
    keys.add(origin.key); origins.push({ key: origin.key, raw: origin.raw });
  }
  return { schema: "motionwright.production-draft/1", scope, target: clone(target) as ProductionTarget,
    baseRevision: value.baseRevision, base: clone(value.base) as ProductionValue, input: clone(value.input),
    sequence: value.sequence, intent: value.intent, pending, origins };
}
export function decodeProductionCopy(raw: string, scope: string, writerId: string): ProductionCopy {
  return decodeProductionCopyAtDepth(raw, scope, writerId, 0);
}
function decodeProductionCopyAtDepth(raw: string, scope: string, writerId: string, originDepth: number): ProductionCopy {
  if (bytes(raw) > MAX_PRODUCTION_COPY_BYTES) throw new Error("The Production recovery exceeds 1 MiB. Download the complete input before reducing it.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || !exact(value, ["schema", "scope", "writerId", "updatedAt", "draft"])
    || value.schema !== "motionwright.production-recovery/1" || value.scope !== scope || value.writerId !== writerId || !uuid(writerId) || !integer(value.updatedAt))
    throw new Error("The Production copy is unreadable or belongs to another writer.");
  return { schema: "motionwright.production-recovery/1", scope, writerId, updatedAt: value.updatedAt, draft: cleanDraft(value.draft, scope, originDepth) };
}
export function assertProductionOrigins(storage: Pick<Storage, "getItem">, draft: ProductionDraft) {
  for (const origin of draft.origins ?? []) if (storage.getItem(origin.key) !== origin.raw)
    throw new Error("An original unconfirmed Production copy changed. Current input remains exportable; another save requires its retained original.");
}
export function retainProductionOrigin(draft: ProductionDraft, origin: ProductionOrigin): ProductionDraft {
  if (!productionUnconfirmed(draft)) return draft;
  const writerId = origin.key.slice(origin.key.lastIndexOf(":") + 1);
  if (origin.key !== productionCopyKey(draft.scope, writerId)
    || !sameValue(decodeProductionCopy(origin.raw, draft.scope, writerId).draft, cleanDraft(draft, draft.scope)))
    throw new Error("The recovered Production attempt does not match its exact original copy.");
  const origins = draft.origins ?? [];
  const previous = origins.find(value => value.key === origin.key);
  if (previous && previous.raw !== origin.raw) throw new Error("A retained Production origin has conflicting bytes.");
  // Retain the first bytes of this attempt. Reopening it must not recursively
  // nest each reload's envelope or exhaust storage without a new author action.
  if (origins.some(value => {
    const retained = decodeProductionCopy(value.raw, draft.scope, value.key.slice(value.key.lastIndexOf(":") + 1)).draft.pending;
    return retained !== null && sameValue(productionAttempt(retained), productionAttempt(draft.pending!));
  })) return draft;
  if (origins.length >= MAX_PRODUCTION_ORIGINS) throw new Error("The Production recovery origin limit was reached. Download the retained input.");
  return { ...draft, origins: [...origins, clone(origin)] };
}
export function encodeProductionCopy(copy: ProductionCopy): string {
  const raw = JSON.stringify(copy); decodeProductionCopy(raw, copy.scope, copy.writerId); return raw;
}
/** Explicit export contains editable input and comparison snapshots, no grant. */
export function encodeProductionFile(draft: ProductionDraft): string {
  return JSON.stringify({ schema: "motionwright.production-file/1", exportedAt: Date.now(), draft: cleanDraft(draft, draft.scope) }, null, 2);
}
export function readProductionCopies(storage: ProductionStorage, scope: string): { entries: ProductionRecoveryEntry[]; limited: boolean } {
  const prefix = PRODUCTION_RECOVERY_PREFIX + encodeURIComponent(scope) + ":", entries: ProductionRecoveryEntry[] = [];
  let limited = false;
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index);
    if (!key?.startsWith(prefix)) continue;
    if (entries.length >= MAX_PRODUCTION_COPIES) { limited = true; continue; }
    const raw = storage.getItem(key); if (raw === null) continue;
    const writerId = key.slice(prefix.length); let value: ProductionCopy | null = null;
    try { value = decodeProductionCopy(raw, scope, writerId); } catch { /* Preserve corrupt bytes until explicit discard. */ }
    entries.push({ key, writerId, raw, value });
  }
  return { entries, limited };
}
export function writeProductionCopy(storage: ProductionStorage, session: Pick<Storage, "getItem" | "setItem">,
  draft: ProductionDraft, custody: ProductionCustody): ProductionCustody {
  const key = productionCopyKey(draft.scope, custody.writerId), pointerKey = productionPointerKey(draft.scope);
  const check = () => {
    assertProductionOrigins(storage, draft);
    const pointer = session.getItem(pointerKey);
    if (pointer !== custody.expectedPointer && pointer !== custody.writerId) throw new Error("Another writer changed this Production recovery pointer. Earlier copies were kept.");
    if (storage.getItem(key) !== custody.expectedRaw) throw new Error("The Production copy changed in another window. Earlier bytes were kept.");
  };
  check();
  let raw = encodeProductionCopy({ schema: "motionwright.production-recovery/1", scope: draft.scope,
    writerId: custody.writerId, updatedAt: Date.now(), draft });
  if (custody.expectedRaw !== null) {
    const old = decodeProductionCopy(custody.expectedRaw, draft.scope, custody.writerId).draft;
    if (!productionHasWork(old) && !sameValue(old, draft)) throw new Error("A clean Production checkpoint is immutable. Use a separate writer.");
    // Compare stored fields before recovery changes NOT_SENT into UNKNOWN. Only
    // the already-optional, empty origins field may be absent in a legacy v1.
    const retained = JSON.parse(custody.expectedRaw).draft, candidate = JSON.parse(raw).draft;
    if (!Object.hasOwn(retained, "origins") && Array.isArray(candidate.origins) && candidate.origins.length === 0) delete candidate.origins;
    if (sameValue(retained, candidate)) raw = custody.expectedRaw;
  }
  let total = bytes(key) + bytes(raw), count = 1;
  for (let index = 0; index < storage.length; index++) {
    const other = storage.key(index);
    if (!other?.startsWith(PRODUCTION_RECOVERY_PREFIX) || other === key) continue;
    total += bytes(other) + bytes(storage.getItem(other) ?? ""); count++;
  }
  if (total > MAX_PRODUCTION_RECOVERY_BYTES || count > MAX_PRODUCTION_COPIES) throw new Error("Production recovery is full (64 copies / 4 MiB). Discard only copies you no longer need.");
  check(); // Storage enumeration can interleave with another tab's writes.
  if (raw !== custody.expectedRaw) storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The Production recovery data write was not confirmed.");
  custody.expectedRaw = raw;
  check(); // A peer may publish a pointer while the data write is confirmed.
  session.setItem(pointerKey, custody.writerId);
  if (session.getItem(pointerKey) !== custody.writerId) throw new Error("The Production writer pointer was not confirmed.");
  if (storage.getItem(key) !== raw) throw new Error("The Production copy changed while its writer pointer was published.");
  custody.expectedPointer = custody.writerId;
  assertProductionOrigins(storage, draft); return custody;
}
export function retainReviewedProductionIntent(storage: ProductionStorage, session: Pick<Storage, "getItem" | "setItem">,
  before: ProductionDraft, next: ProductionDraft, original: ProductionCustody, writerId: string): ProductionCustody {
  if (!productionUnconfirmed(before) || next.pending || before.scope !== next.scope || !uuid(writerId)
    || writerId === original.writerId || original.expectedRaw === null) throw new Error("Invalid Production recovery fork.");
  const key = productionCopyKey(before.scope, original.writerId), raw = storage.getItem(key);
  if (raw !== original.expectedRaw || raw === null || !sameValue(decodeProductionCopy(raw, before.scope, original.writerId).draft, before))
    throw new Error("Retain the exact earlier Production attempt before continuing.");
  const custody = { writerId, expectedRaw: null, expectedPointer: original.expectedPointer };
  writeProductionCopy(storage, session, before, custody);
  if (storage.getItem(key) !== raw) throw new Error("The earlier Production attempt changed during review.");
  writeProductionCopy(storage, session, next, custody);
  if (storage.getItem(key) !== raw) throw new Error("The earlier Production attempt changed during review.");
  return custody;
}
