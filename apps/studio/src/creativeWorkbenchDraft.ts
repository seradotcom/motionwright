/** Frontend author-buffer custody only. The existing App coordinator, grants,
 * StudioService and storage remain the sole authority for Project mutations. */
import type { CanvasNode, Change, Project, ProjectEvent } from "./types";
import { sameProjectRevision } from "./projectMutation";
import { sameValue } from "./creativeProduction";
import { applyProductStageChange, defaultProductStageConfig, validateProductStageConfig } from "./productStage";
import type { ProductStageConfig } from "./productStage";
import { applyProceduralChange, defaultProceduralConfig, defaultProceduralInfluence, defaultProceduralPath, normalizedProceduralConfig, validateProceduralConfig } from "./proceduralField";
import type { ProceduralConfig, ProceduralInfluence, ProceduralPath } from "./proceduralField";
import { applyStageCaptureChange } from "./stageCapture";
import { applyMeasuredBinding, applyMeasuredTable, detachMeasuredBinding, measuredOutput, validateMeasuredTable, validateMeasuredView } from "./measuredData";
import type { MeasuredBinding, MeasuredTable, MeasuredView } from "./measuredData";

export type WorkbenchKind = "stage" | "procedural" | "measured";
export type WorkbenchTarget = { kind: WorkbenchKind; sceneId: string | null };
export type StageRaw = Omit<ProductStageConfig, "focus_x_permille" | "focus_y_permille"> & { focus_x_permille: string; focus_y_permille: string };
export const PROCEDURAL_NUMBERS = ["seed", "count", "columns", "origin_x", "origin_y", "area_width", "area_height", "size", "opacity_percent", "reveal_step_frames", "reveal_duration_frames"] as const;
type ProceduralNumber = typeof PROCEDURAL_NUMBERS[number];
export const INFLUENCE_NUMBERS = ["center_x", "center_y", "radius", "offset_x", "offset_y", "scale_percent", "rotation_deg"] as const;
type InfluenceNumber = typeof INFLUENCE_NUMBERS[number];
export type InfluenceRaw = Omit<ProceduralInfluence, InfluenceNumber> & Record<InfluenceNumber, string> & { enabled: boolean };
export type ProceduralPathRaw = { closed: boolean; points: { x_percent: string; y_percent: string }[] };
export const rawProceduralPath = (path: ProceduralPath = defaultProceduralPath()): ProceduralPathRaw => ({
  closed: path.closed, points: path.points.map(point => ({ x_percent: String(point.x_percent), y_percent: String(point.y_percent) })),
});
export type ProceduralRaw = Omit<Required<ProceduralConfig>, ProceduralNumber | "influence" | "path"> & Record<ProceduralNumber, string> & { influence?: InfluenceRaw; path?: ProceduralPathRaw };
export function rawProceduralInfluence(config = defaultProceduralInfluence(), enabled = true): InfluenceRaw {
  return { ...Object.fromEntries(Object.entries(config).map(([key, value]) =>
    [key, INFLUENCE_NUMBERS.includes(key as InfluenceNumber) ? String(value) : value])), enabled } as InfluenceRaw;
}
export interface MeasuredPresentationRaw {
  selectedNode: string; selectedKey: string; view: MeasuredView["kind"];
  decimals: string; axisMin: string; axisMax: string; maxWidth: string;
}
export interface MeasuredRaw extends MeasuredPresentationRaw {
  draftId: string; label: string; assetId: string; rowsText: string;
}
export interface MeasuredBindingRaw extends MeasuredPresentationRaw {
  sceneId: string; tableId: string;
}
export type WorkbenchInput = { kind: "stage"; config: StageRaw; captureId: string }
  | { kind: "procedural"; config: ProceduralRaw }
  | { kind: "measured"; selectedTable: string; tables: Record<string, MeasuredRaw>;
      selectedBinding?: string; bindings?: Record<string, MeasuredBindingRaw> };
export type WorkbenchChange = Extract<Change, { type: "upsert_product_stage" | "detach_product_stage" | "bind_stage_capture" | "detach_stage_capture"
  | "upsert_procedural_field" | "detach_procedural_field" | "upsert_measured_table" | "upsert_measured_binding" | "detach_measured_binding" }>;
export type WorkbenchOperation = "save" | "detach" | "bind-source" | "detach-source" | "table" | "binding" | { detachBinding: string };
export interface WorkbenchPending {
  /** Comparison data only. No stored Change is ever dispatched or replayed. */
  id: string; revision: number; sequence: number; input: WorkbenchInput; submitted: WorkbenchChange;
  nodesBefore: CanvasNode[] | null; outcome: "UNKNOWN" | "NOT_SENT"; diagnostic?: string;
}
export interface WorkbenchDraft {
  schema: "motionwright.creative-workbench-draft/1"; scope: string; target: WorkbenchTarget;
  baseRevision: number; base: WorkbenchInput; input: WorkbenchInput; sequence: number; intent: boolean; pending: WorkbenchPending | null;
}
export interface MeasuredByteProof { scope: string; tableKey: string; assetId: string; digest: string; rowsText: string }
export const WORKBENCH_RECOVERY_PREFIX = "motionwright.creative-workbench-recovery.v1:";
export const WORKBENCH_POINTER_PREFIX = "motionwright.creative-workbench-writer.v1:";
export const MAX_WORKBENCH_COPY_BYTES = 1024 * 1024, MAX_WORKBENCH_RECOVERY_BYTES = 4 * 1024 * 1024, MAX_WORKBENCH_COPIES = 64;
const MAX_HISTORY = 2048;
const clone = <T,>(value: T): T => structuredClone(value);
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: readonly string[], optional: readonly string[] = []) => keys.every(key => Object.hasOwn(value, key)) && Object.keys(value).every(key => keys.includes(key) || optional.includes(key));
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const uuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const bytes = (value: string) => new TextEncoder().encode(value).length;
export const workbenchScope = (project: Project, target: WorkbenchTarget) => JSON.stringify([project.id, project.generation, project.active_branch, target.kind, target.sceneId]);
export const workbenchCopyKey = (scope: string, writer: string) => WORKBENCH_RECOVERY_PREFIX + encodeURIComponent(scope) + ":" + writer;
export const workbenchPointerKey = (scope: string) => WORKBENCH_POINTER_PREFIX + encodeURIComponent(scope);
export const workbenchLabel = (target: WorkbenchTarget) => ({ stage: "ProductStage", procedural: "Procedural", measured: "Measured data" })[target.kind];
export const workbenchHasWork = (draft: WorkbenchDraft) => draft.intent || draft.pending !== null;
export const workbenchUnconfirmed = (draft: WorkbenchDraft) => draft.pending?.outcome === "UNKNOWN";
function checkScope(draft: WorkbenchDraft, project: Project) {
  if (workbenchScope(project, draft.target) !== draft.scope) throw new Error("This author input belongs to another project, generation, branch or scene.");
  if (draft.target.sceneId !== null && project.scenes.filter(scene => scene.id === draft.target.sceneId).length !== 1)
    throw new Error("The working scene is unavailable or ambiguous.");
}
/** Raw decimal grammar is deliberately separate from JS Number coercion. */
export function authoredInteger(raw: string, label: string, min: number, max: number): number {
  if (!/^-?[0-9]+$/.test(raw.trim())) throw new Error(label + " requires a complete integer. Your exact input is retained.");
  const value = Number(raw.trim());
  if (!Number.isSafeInteger(value) || value < min || value > max) throw new Error(label + " is outside the supported integer range.");
  return value;
}
export function stageConfig(raw: StageRaw): ProductStageConfig {
  const config = { ...raw, focus_x_permille: authoredInteger(raw.focus_x_permille, "ProductStage focus X", 150, 850), focus_y_permille: authoredInteger(raw.focus_y_permille, "ProductStage focus Y", 200, 800) };
  validateProductStageConfig(config); return config;
}
/** Presentation only: these are the existing new-Stage constraints. A bound
 * Stage can still be updated at capacity. Preparation also runs the existing
 * project-level validator; only the original service response can confirm a save. */
export function productStageCreationIssue(project: Project, sceneId: string | null): string | null {
  const stages = project.production_design?.stages ?? [];
  if (sceneId === null || stages.some(stage => stage.scene_id === sceneId)) return null;
  if (project.production_design?.heroes.some(hero => hero.scene_id === sceneId))
    return "This scene already contains a ProductHero. Choose another scene for a new ProductStage. Your input remains with this scene.";
  if (stages.length >= 16)
    return "The project already has 16 ProductStages. Update an existing Stage or explicitly detach one before adding another. Your input is retained.";
  return null;
}
export function proceduralConfig(raw: ProceduralRaw): ProceduralConfig {
  const { influence, path, ...fields } = raw, result = { ...fields } as unknown as ProceduralConfig;
  for (const key of PROCEDURAL_NUMBERS) result[key] = authoredInteger(raw[key], key, 0, key === "seed" ? 0xffffffff : 1920);
  if (influence?.enabled) {
    const { enabled: _enabled, ...values } = influence;
    const parsed = { ...values } as unknown as ProceduralInfluence;
    for (const key of INFLUENCE_NUMBERS) parsed[key] = authoredInteger(influence[key], "Influence " + key, -4096, 4096);
    result.influence = parsed;
  }
  if (raw.distribution === "path") {
    if (!path) throw new Error("Path distribution requires an editable polyline.");
    result.path = { closed: path.closed, points: path.points.map((point, index) => ({
      x_percent: authoredInteger(point.x_percent, "Path point " + (index + 1) + " X", 0, 100),
      y_percent: authoredInteger(point.y_percent, "Path point " + (index + 1) + " Y", 0, 100),
    })) };
  }
  validateProceduralConfig(result); return result;
}
function rawStage(config: ProductStageConfig): StageRaw { return { ...clone(config), focus_x_permille: String(config.focus_x_permille), focus_y_permille: String(config.focus_y_permille) }; }
function rawProcedural(config: ProceduralConfig): ProceduralRaw {
  const { influence, path, ...result } = normalizedProceduralConfig(config);
  return { ...Object.fromEntries(Object.entries(result).map(([key, value]) => [key, PROCEDURAL_NUMBERS.includes(key as ProceduralNumber) ? String(value) : value])),
    ...(influence ? { influence: rawProceduralInfluence(influence) } : {}), ...(path ? { path: rawProceduralPath(path) } : {}) } as ProceduralRaw;
}
const newMeasuredPresentation = (): MeasuredPresentationRaw => ({
  selectedNode: "", selectedKey: "", view: "counter", decimals: "2", axisMin: "", axisMax: "", maxWidth: "",
});
function newMeasuredRaw(table?: MeasuredTable): MeasuredRaw {
  return { draftId: table?.id ?? crypto.randomUUID(), label: table?.label ?? "", assetId: table?.source_asset_id ?? "", rowsText: JSON.stringify(table?.rows ?? [], null, 2),
    ...newMeasuredPresentation() };
}
type MeasuredInput = Extract<WorkbenchInput, { kind: "measured" }>;
function rawMeasuredBinding(binding: MeasuredBinding): MeasuredBindingRaw {
  return { ...newMeasuredPresentation(), sceneId: binding.scene_id, tableId: binding.table_id,
    selectedNode: binding.node_id, selectedKey: binding.datum_key, view: binding.view.kind,
    ...(binding.view.kind === "bar" ? { axisMin: String(binding.view.axis_min_milli),
      axisMax: String(binding.view.axis_max_milli), maxWidth: String(binding.view.max_width_px) }
      : { decimals: String(binding.view.decimals) }) };
}
export function measuredPresentation(input: MeasuredInput): MeasuredPresentationRaw {
  if (!input.selectedBinding) return measuredRaw(input);
  const raw = input.bindings?.[input.selectedBinding];
  if (!raw) throw new Error("The selected measured binding draft is unavailable.");
  return raw;
}
export function selectMeasuredBinding(input: MeasuredInput, bindingId: string, project: Project, sceneId: string | null): MeasuredInput {
  if (!bindingId) return { ...input, selectedBinding: "" };
  const saved = project.production_design?.measured_bindings?.find(binding => binding.id === bindingId && binding.scene_id === sceneId);
  const retained = input.bindings?.[bindingId];
  if ((!saved && !retained) || (retained && retained.sceneId !== sceneId))
    throw new Error("The measured binding is unavailable in this scene.");
  if (!retained && Object.keys(input.bindings ?? {}).length >= 64)
    throw new Error("Retained measured binding drafts reached 64. Discard an unneeded draft before opening another.");
  const tableId = retained?.tableId ?? saved!.table_id;
  const selected = project.production_design?.measured_tables?.some(table => table.id === tableId)
    ? selectMeasuredTable(input, tableId, project) : input;
  return { ...selected, selectedBinding: bindingId, bindings: { ...selected.bindings,
    [bindingId]: retained ?? rawMeasuredBinding(saved!) } };
}
export function editMeasuredPresentation(input: MeasuredInput, patch: Partial<MeasuredPresentationRaw>): MeasuredInput {
  if (!input.selectedBinding) return { ...input, tables: { ...input.tables,
    [input.selectedTable]: { ...measuredRaw(input), ...patch } } };
  const raw = input.bindings?.[input.selectedBinding];
  if (!raw) throw new Error("The selected measured binding draft is unavailable.");
  if (patch.selectedNode !== undefined && patch.selectedNode !== raw.selectedNode)
    throw new Error("An existing measured binding keeps its original Canvas node.");
  return { ...input, bindings: { ...input.bindings, [input.selectedBinding]: { ...raw, ...patch } } };
}
/** Explicit discard affects only the selected output draft, never its source
 * table, other output buffers, saved Canvas objects or an uncertain request. */
export function discardMeasuredPresentation(input: MeasuredInput, project: Project, sceneId: string | null): MeasuredInput {
  if (!input.selectedBinding) return editMeasuredPresentation(input, newMeasuredPresentation());
  const bindingId = input.selectedBinding, saved = project.production_design?.measured_bindings?.find(binding => binding.id === bindingId && binding.scene_id === sceneId);
  const bindings = { ...input.bindings };
  if (saved) bindings[bindingId] = rawMeasuredBinding(saved);
  else delete bindings[bindingId];
  return { ...input, selectedBinding: saved ? bindingId : "", bindings };
}
export function measuredBindingProposal(input: MeasuredInput, project: Project, sceneId: string | null, newId: string):
  Extract<WorkbenchChange, { type: "upsert_measured_binding" }> {
  const raw = measuredPresentation(input), editing = input.selectedBinding;
  let tableId = measuredRaw(input).draftId;
  if (!editing && Object.keys(input.bindings ?? {}).length >= 64)
    throw new Error("Retained measured binding drafts reached 64. Discard an unneeded draft before binding another output.");
  if (editing) {
    const retained = input.bindings![editing], saved = project.production_design?.measured_bindings?.find(binding => binding.id === editing);
    if (!saved) throw new Error("This measured binding was removed. Keep or discard its draft explicitly; it cannot be recreated by an update.");
    if (saved.scene_id !== sceneId || retained.sceneId !== sceneId || saved.node_id !== retained.selectedNode || saved.table_id !== retained.tableId)
      throw new Error("The measured binding node, source or scene identity changed. Its retained input cannot move to a different target.");
    tableId = retained.tableId;
  }
  if (!sceneId) throw new Error("Choose the working scene and Canvas node.");
  const change = { type: "upsert_measured_binding" as const, binding_id: editing || newId,
    scene_id: sceneId, node_id: raw.selectedNode, table_id: tableId, datum_key: raw.selectedKey, view: measuredView(raw) };
  applyMeasuredBinding(clone(project), change);
  return change;
}
export function measuredRaw(input: Extract<WorkbenchInput, { kind: "measured" }>): MeasuredRaw {
  if (!Object.hasOwn(input.tables, input.selectedTable)) throw new Error("The selected measured draft is unavailable.");
  return input.tables[input.selectedTable];
}
export function selectMeasuredTable(input: Extract<WorkbenchInput, { kind: "measured" }>, key: string, project: Project): typeof input {
  const table = project.production_design?.measured_tables?.find(value => value.id === key);
  if (key !== "new" && !table) throw new Error("The selected measured table is no longer present.");
  return { ...input, selectedTable: key, tables: { ...input.tables, [key]: input.tables[key] ?? newMeasuredRaw(table) } };
}
export function workbenchSavedInput(project: Project, target: WorkbenchTarget, previous?: WorkbenchInput): WorkbenchInput {
  if (target.kind === "stage") {
    const stage = project.production_design?.stages?.find(value => value.scene_id === target.sceneId);
    return { kind: "stage", config: rawStage(stage?.config ?? defaultProductStageConfig()), captureId: project.production_design?.stage_capture_cards?.find(value => value.stage_id === stage?.id)?.capture_id ?? "" };
  }
  if (target.kind === "procedural") return { kind: "procedural", config: rawProcedural(project.production_design?.procedural_fields?.find(value => value.scene_id === target.sceneId)?.config ?? defaultProceduralConfig()) };
  const initial: Extract<WorkbenchInput, { kind: "measured" }> = { kind: "measured", selectedTable: "new", tables: { new: newMeasuredRaw() } };
  let result = initial;
  if (previous?.kind === "measured") {
    const saved = project.production_design?.measured_tables?.find(value => value.id === measuredRaw(previous).draftId);
    if (saved) result = selectMeasuredTable(initial, saved.id, project);
    if (previous.selectedBinding) {
      const binding = project.production_design?.measured_bindings?.find(value => value.id === previous.selectedBinding && value.scene_id === target.sceneId);
      const raw = binding ? rawMeasuredBinding(binding) : previous.bindings?.[previous.selectedBinding];
      if (raw) result = { ...result, selectedBinding: previous.selectedBinding,
        bindings: { [previous.selectedBinding]: clone(raw) } };
    }
  }
  return result;
}
export function createWorkbenchDraft(project: Project, target: WorkbenchTarget): WorkbenchDraft {
  const initial = workbenchSavedInput(project, target);
  const draft: WorkbenchDraft = { schema: "motionwright.creative-workbench-draft/1", scope: workbenchScope(project, target), target: clone(target), baseRevision: project.revision,
    base: clone(initial), input: initial, sequence: 0, intent: false, pending: null };
  checkScope(draft, project); return draft;
}
export function editWorkbenchDraft(draft: WorkbenchDraft, input: WorkbenchInput): WorkbenchDraft {
  if (input.kind !== draft.target.kind) throw new Error("Author input cannot cross workbench types.");
  return { ...draft, input: clone(input), sequence: draft.sequence + 1, intent: true };
}
export function reconcileWorkbenchDraft(draft: WorkbenchDraft, project: Project): WorkbenchDraft {
  checkScope(draft, project);
  if (workbenchHasWork(draft) || draft.baseRevision === project.revision) return draft;
  const saved = workbenchSavedInput(project, draft.target, draft.input);
  return { ...draft, baseRevision: project.revision, base: clone(saved), input: saved };
}
export function workbenchConflict(draft: WorkbenchDraft, project: Project): boolean { checkScope(draft, project); return workbenchHasWork(draft) && draft.baseRevision !== project.revision; }
export function resolveWorkbenchDraft(draft: WorkbenchDraft, project: Project, choice: "saved" | "draft"): WorkbenchDraft {
  checkScope(draft, project);
  if (workbenchUnconfirmed(draft)) throw new Error("Read the uncertain outcome before choosing another base.");
  const saved = workbenchSavedInput(project, draft.target, draft.input);
  return { ...draft, baseRevision: project.revision, base: clone(saved), input: choice === "saved" ? saved : draft.input, intent: choice === "draft", pending: null };
}
export function measuredTable(raw: MeasuredRaw, project: Project): MeasuredTable {
  const stored = project.production_design?.measured_tables?.find(value => value.id === raw.draftId);
  const source = project.assets.find(asset => asset.id === raw.assetId);
  const table: MeasuredTable = { schema: "motionwright.measured-table/1", id: raw.draftId, label: raw.label, version: stored ? stored.version + 1 : 1,
    source_asset_id: raw.assetId, source_sha256: source?.content_sha256 ?? "", rows: JSON.parse(raw.rowsText) as MeasuredTable["rows"] };
  validateMeasuredTable(table, project); return table;
}
export function measuredView(raw: MeasuredPresentationRaw): MeasuredView {
  const result: MeasuredView = raw.view === "bar" ? { kind: "bar", axis_min_milli: authoredInteger(raw.axisMin, "Axis minimum", -1e9, 1e9),
    axis_max_milli: authoredInteger(raw.axisMax, "Axis maximum", -1e9, 1e9), max_width_px: authoredInteger(raw.maxWidth, "Bar width", 16, 1920) }
    : { kind: raw.view, decimals: authoredInteger(raw.decimals, "Decimal places", 0, 3) };
  validateMeasuredView(result); return result;
}
export function measuredProofMatches(draft: WorkbenchDraft, project: Project, proof: MeasuredByteProof | null): boolean {
  if (draft.input.kind !== "measured" || !proof || proof.scope !== draft.scope || workbenchScope(project, draft.target) !== draft.scope) return false;
  const raw = measuredRaw(draft.input), asset = project.assets.find(value => value.id === raw.assetId);
  return proof.tableKey === draft.input.selectedTable && proof.assetId === raw.assetId && proof.digest === asset?.content_sha256 && proof.rowsText === raw.rowsText;
}
/** Existing realizers/domain functions are validation only; this never publishes
 * a Project. The returned new Change goes only to the original coordinator. */
export async function prepareWorkbenchSubmission(draft: WorkbenchDraft, project: Project, id: string, operation: WorkbenchOperation,
  proof: MeasuredByteProof | null = null): Promise<{ draft: WorkbenchDraft; change: WorkbenchChange }> {
  checkScope(draft, project);
  if (!uuid(id) || workbenchUnconfirmed(draft) || workbenchConflict(draft, project)) throw new Error("Review the saved revision and any unconfirmed attempt before a separate save.");
  const scene = project.scenes.find(value => value.id === draft.target.sceneId), input = clone(draft.input);
  let change: WorkbenchChange, nodesBefore: CanvasNode[] | null = null;
  if (input.kind !== "measured" && (!scene || scene.renderer !== "motion-canvas" || Number(scene.duration.num) / Number(scene.duration.den) < 2)) throw new Error("Choose a Motion Canvas scene at least two seconds long.");
  if (input.kind === "stage") {
    const stored = project.production_design?.stages?.find(value => value.scene_id === scene!.id);
    if (operation === "save") {
      const config = stageConfig(input.config), instanceId = stored?.id ?? crypto.randomUUID();
      change = { type: "upsert_product_stage", instance_id: instanceId, scene_id: scene!.id, config };
      // Reuse the existing collision, capacity, lock and merge guards without
      // publishing this validation copy or acquiring mutation authority.
      await applyProductStageChange(clone(project), change);
    } else if (operation === "detach") {
      if (!stored || project.production_design?.stage_capture_cards?.some(value => value.stage_id === stored.id)) throw new Error("Detach the source tile before releasing the original ProductStage.");
      change = { type: "detach_product_stage", instance_id: stored.id }; nodesBefore = clone(scene!.nodes);
    } else if (operation === "bind-source" || operation === "detach-source") {
      if (!stored) throw new Error("Save the original ProductStage before changing its source tile.");
      change = operation === "bind-source" ? { type: "bind_stage_capture", stage_id: stored.id, capture_id: input.captureId } : { type: "detach_stage_capture", stage_id: stored.id };
      await applyStageCaptureChange(clone(project), change); nodesBefore = clone(scene!.nodes);
    } else throw new Error("Unsupported ProductStage action.");
  } else if (input.kind === "procedural") {
    const stored = project.production_design?.procedural_fields?.find(value => value.scene_id === scene!.id);
    if (operation === "save") {
      const config = proceduralConfig(input.config), instanceId = stored?.id ?? crypto.randomUUID();
      change = { type: "upsert_procedural_field", instance_id: instanceId, scene_id: scene!.id, config };
      // Complete authoring preflight: duration, bounds, locks, overrides and count.
      await applyProceduralChange(clone(project), change);
    } else if (operation === "detach" && stored) { change = { type: "detach_procedural_field", instance_id: stored.id }; nodesBefore = clone(scene!.nodes); }
    else throw new Error("Unsupported procedural action or missing field.");
  } else {
    const raw = measuredRaw(input);
    if (operation === "table") {
      if (!measuredProofMatches(draft, project, proof)) throw new Error("Select the exact imported source bytes again; a recovered copy is never source verification.");
      const table = measuredTable(raw, project); applyMeasuredTable(clone(project), table); change = { type: "upsert_measured_table", table };
    } else if (operation === "binding") {
      if (!scene) throw new Error("Choose the working scene and Canvas node.");
      change = measuredBindingProposal(input, project, scene.id, crypto.randomUUID());
    } else if (typeof operation === "object") {
      const binding = project.production_design?.measured_bindings?.find(value => value.id === operation.detachBinding && value.scene_id === scene?.id);
      if (!binding || !scene) throw new Error("This measured binding belongs to another scene or no longer exists.");
      detachMeasuredBinding(clone(project), binding.id); change = { type: "detach_measured_binding", binding_id: binding.id }; nodesBefore = clone(scene.nodes);
    } else throw new Error("Unsupported measured action.");
  }
  const lockKinds = operation === "detach" || operation === "detach-source" ? ["content"] : ["content", "position", "style", "timing", "renderer"];
  if (input.kind !== "measured" && project.locks.some(lock => ["project:" + project.id, "scene:" + scene!.id].includes(lock.resource) && lockKinds.includes(lock.kind))) throw new Error("The selected scene or project is locked.");
  return { change, draft: { ...draft, intent: true, pending: { id, revision: project.revision, sequence: draft.sequence, input, submitted: clone(change), nodesBefore, outcome: "UNKNOWN" } } };
}
export function workbenchPreparationMatches(source: WorkbenchDraft, before: Project, current: WorkbenchDraft, project: Project): boolean {
  return source.scope === current.scope && source.sequence === current.sequence && sameValue(source.input, current.input) && sameValue(source.pending, current.pending)
    && sameProjectRevision(before, project) && workbenchScope(project, source.target) === source.scope;
}
export function retainWorkbenchFailure(draft: WorkbenchDraft, id: string, diagnostic: string, notSent = false): WorkbenchDraft {
  return draft.pending?.id === id ? { ...draft, pending: { ...draft.pending, outcome: notSent ? "NOT_SENT" : "UNKNOWN", diagnostic } } : draft;
}
export function submittedWorkbenchMatches(project: Project, change: WorkbenchChange): boolean {
  const design = project.production_design;
  switch (change.type) {
    case "upsert_product_stage": { const value = design?.stages?.find(item => item.id === change.instance_id); return !!value && value.scene_id === change.scene_id && value.component_version === 1 && sameValue(value.config, change.config); }
    case "detach_product_stage": return !design?.stages?.some(item => item.id === change.instance_id);
    case "bind_stage_capture": return sameValue(design?.stage_capture_cards?.find(item => item.stage_id === change.stage_id), { stage_id: change.stage_id, capture_id: change.capture_id, version: 1 });
    case "detach_stage_capture": return !design?.stage_capture_cards?.some(item => item.stage_id === change.stage_id);
    case "upsert_procedural_field": { const value = design?.procedural_fields?.find(item => item.id === change.instance_id); return !!value && value.scene_id === change.scene_id && value.generator_version === 1 && sameValue(normalizedProceduralConfig(value.config), normalizedProceduralConfig(change.config)); }
    case "detach_procedural_field": return !design?.procedural_fields?.some(item => item.id === change.instance_id);
    case "upsert_measured_table": return sameValue(design?.measured_tables?.find(item => item.id === change.table.id), change.table);
    case "upsert_measured_binding": {
      const binding = design?.measured_bindings?.find(item => item.id === change.binding_id), datum = design?.measured_tables?.find(item => item.id === change.table_id)?.rows.find(row => row.key === change.datum_key);
      const node = project.scenes.find(scene => scene.id === change.scene_id)?.nodes.find(item => item.id === change.node_id);
      if (!binding || !datum || !node) return false;
      const output = measuredOutput(datum, change.view);
      return binding.scene_id === change.scene_id && binding.node_id === change.node_id && binding.table_id === change.table_id && binding.datum_key === change.datum_key
        && sameValue(binding.view, change.view) && sameValue(binding.baseline, output) && (output.kind === "text" ? node.text === output.value : node.width === output.width_px);
    }
    case "detach_measured_binding": return !design?.measured_bindings?.some(item => item.id === change.binding_id);
  }
}
/** One successful operation cannot mark a different author buffer as saved. */
function remainingInput(input: WorkbenchInput, target: WorkbenchTarget, project: Project): boolean {
  try {
    if (input.kind === "stage") {
      const saved = project.production_design?.stages?.find(value => value.scene_id === target.sceneId);
      const capture = project.production_design?.stage_capture_cards?.find(value => value.stage_id === saved?.id)?.capture_id ?? "";
      return !saved || !sameValue(stageConfig(input.config), saved.config) || input.captureId !== capture;
    }
    if (input.kind === "procedural") {
      const saved = project.production_design?.procedural_fields?.find(value => value.scene_id === target.sceneId);
      return !saved || input.config.influence?.enabled === false || (input.config.path !== undefined && input.config.distribution !== "path")
        || !sameValue(normalizedProceduralConfig(proceduralConfig(input.config)), normalizedProceduralConfig(saved.config));
    }
    const retainedBindings = Object.entries(input.bindings ?? {}).some(([id, raw]) => {
      const binding = project.production_design?.measured_bindings?.find(value => value.id === id);
      return !binding || binding.scene_id !== target.sceneId || raw.sceneId !== binding.scene_id
        || raw.tableId !== binding.table_id || raw.selectedNode !== binding.node_id
        || raw.selectedKey !== binding.datum_key || !sameValue(measuredView(raw), binding.view)
        || (raw.view === "bar" ? raw.decimals !== "2" : raw.axisMin !== "" || raw.axisMax !== "" || raw.maxWidth !== "");
    });
    return retainedBindings || Object.values(input.tables).some(raw => {
      const table = project.production_design?.measured_tables?.find(value => value.id === raw.draftId);
      if (!table) return raw.label !== "" || raw.assetId !== "" || raw.rowsText !== "[]" || raw.selectedNode !== "" || raw.selectedKey !== ""
        || raw.view !== "counter" || raw.decimals !== "2" || raw.axisMin !== "" || raw.axisMax !== "" || raw.maxWidth !== "";
      if (raw.label !== table.label || raw.assetId !== table.source_asset_id || !sameValue(JSON.parse(raw.rowsText), table.rows)) return true;
      if (!raw.selectedNode && !raw.selectedKey) return raw.view !== "counter" || raw.decimals !== "2" || raw.axisMin !== "" || raw.axisMax !== "" || raw.maxWidth !== "";
      const binding = project.production_design?.measured_bindings?.find(value => value.scene_id === target.sceneId && value.node_id === raw.selectedNode);
      return !binding || binding.table_id !== table.id || binding.datum_key !== raw.selectedKey || !sameValue(binding.view, measuredView(raw))
        // Inactive view parameters remain authored work too.
        || (raw.view === "bar" ? raw.decimals !== "2" : raw.axisMin !== "" || raw.axisMax !== "" || raw.maxWidth !== "");
    });
  } catch { return true; }
}
export function acknowledgeWorkbenchDraft(draft: WorkbenchDraft, id: string, receipt: Project): WorkbenchDraft {
  const pending = draft.pending; if (!pending || pending.id !== id) return draft;
  checkScope(draft, receipt);
  if (receipt.revision !== pending.revision + 1 || !submittedWorkbenchMatches(receipt, pending.submitted)) throw new Error("The original reply does not confirm the exact submitted workbench operation.");
  if (pending.nodesBefore !== null && !sameValue(receipt.scenes.find(scene => scene.id === draft.target.sceneId)?.nodes, pending.nodesBefore)) throw new Error("The original reply did not preserve the editable Canvas nodes.");
  // Keep raw text and every table buffer, including later selections. A clean
  // checkpoint remains immutable before any future author input is written.
  let input = draft.input;
  if (input.kind === "measured" && pending.submitted.type === "upsert_measured_table") {
    const table = pending.submitted.table, currentNew = input.tables.new;
    // A reviewed UNKNOWN can leave both the original "new" alias and a
    // separately edited saved-table buffer. Never promote one over the other.
    const destination = input.tables[table.id];
    if (currentNew?.draftId === table.id && (!destination || sameValue(destination, currentNew))) input = { ...input, selectedTable: input.selectedTable === "new" ? table.id : input.selectedTable,
      tables: { ...input.tables, [table.id]: currentNew, new: newMeasuredRaw() } };
  }
  if (input.kind === "measured" && pending.input.kind === "measured" && !pending.input.selectedBinding
    && pending.submitted.type === "upsert_measured_binding") {
    const change = pending.submitted, tableKey = pending.input.selectedTable, raw = input.tables[tableKey];
    // Promote only the original node/table buffer. Later presentation input
    // follows that same identity; a newly selected node or output stays separate.
    if (raw?.draftId === change.table_id && raw.selectedNode === change.node_id && !input.bindings?.[change.binding_id]) {
      const { selectedNode, selectedKey, view, decimals, axisMin, axisMax, maxWidth } = raw;
      input = { ...input,
        selectedBinding: !input.selectedBinding && input.selectedTable === tableKey ? change.binding_id : input.selectedBinding,
        tables: { ...input.tables, [tableKey]: { ...raw, ...newMeasuredPresentation() } },
        bindings: { ...input.bindings, [change.binding_id]: { sceneId: change.scene_id, tableId: change.table_id,
          selectedNode, selectedKey, view, decimals, axisMin, axisMax, maxWidth } } };
    }
  }
  return { ...draft, input, base: workbenchSavedInput(receipt, draft.target, input), baseRevision: receipt.revision, pending: null, intent: draft.sequence !== pending.sequence || pending.submitted.type.startsWith("detach_") || remainingInput(input, draft.target, receipt) };
}
export interface WorkbenchOutcomeTransport { readProject(id: string): Promise<Project>; readHistory(project: Project, after: number, limit: number): Promise<ProjectEvent[]> }
export interface WorkbenchOutcomeReview { scope: string; pending: WorkbenchPending; project: Project; events: ProjectEvent[]; matchingValueRevisions: number[] }
export function workbenchOutcomeMatches(draft: WorkbenchDraft, project: Project, review: WorkbenchOutcomeReview | null): boolean {
  return !!review && workbenchUnconfirmed(draft) && draft.scope === review.scope && workbenchScope(project, draft.target) === draft.scope
    && sameValue(draft.pending, review.pending) && sameProjectRevision(project, review.project);
}
export async function inspectWorkbenchOutcome(draft: WorkbenchDraft, selected: Project, transport: WorkbenchOutcomeTransport): Promise<WorkbenchOutcomeReview> {
  checkScope(draft, selected); if (!workbenchUnconfirmed(draft)) throw new Error("There is no uncertain workbench attempt to inspect.");
  const pending = clone(draft.pending!), project = clone(await transport.readProject(selected.id));
  checkScope(draft, project);
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
export function releaseWorkbenchOutcome(draft: WorkbenchDraft, project: Project, review: WorkbenchOutcomeReview): WorkbenchDraft {
  if (!workbenchOutcomeMatches(draft, project, review)) throw new Error("Read the current project and its complete history before a separate intent.");
  if (project.revision <= draft.pending!.revision)
    throw new Error("The unconfirmed workbench attempt could still commit at this revision. Keep its original copy until the saved project advances.");
  return { ...draft, baseRevision: project.revision, base: workbenchSavedInput(project, draft.target, draft.input), pending: null, intent: true };
}

export type WorkbenchStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
export interface WorkbenchCustody { writerId: string; expectedRaw: string | null; expectedPointer: string | null }
export interface WorkbenchCopy { schema: "motionwright.creative-workbench-recovery/1"; scope: string; writerId: string; updatedAt: number; draft: WorkbenchDraft }
export interface WorkbenchRecoveryEntry { key: string; writerId: string; raw: string; value: WorkbenchCopy | null }
const allStrings = (value: Record<string, unknown>, keys: readonly string[]) => keys.every(key => typeof value[key] === "string");
const stageTextKeys = ["screen_heading", "screen_detail", "action_label", "author_attestation", "screen_surface", "foreground", "accent", "focus_x_permille", "focus_y_permille"];
function influenceRawShape(value: unknown): value is InfluenceRaw {
  return record(value) && exact(value, ["enabled", "shape", "falloff", "fill", ...INFLUENCE_NUMBERS])
    && typeof value.enabled === "boolean" && allStrings(value, ["fill", ...INFLUENCE_NUMBERS])
    && ["radial", "horizontal", "vertical"].includes(String(value.shape)) && ["linear", "smoothstep"].includes(String(value.falloff));
}
function proceduralPathRawShape(value: unknown): value is ProceduralPathRaw {
  return record(value) && exact(value, ["closed", "points"]) && typeof value.closed === "boolean"
    && Array.isArray(value.points) && value.points.length >= 2 && value.points.length <= 16
    && value.points.every(point => record(point) && exact(point, ["x_percent", "y_percent"])
      && allStrings(point, ["x_percent", "y_percent"])
      && String(point.x_percent).length <= 128 && String(point.y_percent).length <= 128);
}
function inputShape(value: unknown): value is WorkbenchInput {
  if (!record(value)) return false;
  if (value.kind === "stage") return exact(value, ["kind", "config", "captureId"]) && typeof value.captureId === "string" && record(value.config)
    && exact(value.config, ["frame", "animate_focus", ...stageTextKeys]) && allStrings(value.config, stageTextKeys)
    && ["browser", "flat_panel", "generic_handset"].includes(String(value.config.frame)) && typeof value.config.animate_focus === "boolean";
  if (value.kind === "procedural") return exact(value, ["kind", "config"]) && record(value.config)
    && exact(value.config, [...PROCEDURAL_NUMBERS, "distribution", "shape", "fill"], ["influence", "path"])
    && (value.config.path === undefined || proceduralPathRawShape(value.config.path))
    && (value.config.influence === undefined || influenceRawShape(value.config.influence)) && allStrings(value.config, [...PROCEDURAL_NUMBERS, "fill"])
    && ["grid", "adaptive_grid", "path", "staggered", "scatter", "radial", "spiral"].includes(String(value.config.distribution)) && ["square", "circle"].includes(String(value.config.shape));
  if (value.kind !== "measured" || !exact(value, ["kind", "selectedTable", "tables"], ["selectedBinding", "bindings"]) || typeof value.selectedTable !== "string" || !record(value.tables)
    || Object.keys(value.tables).length > 17 || !Object.hasOwn(value.tables, value.selectedTable)) return false;
  if (value.bindings !== undefined && (!record(value.bindings) || Object.keys(value.bindings).length > 64
    || !Object.entries(value.bindings).every(([id, raw]) => uuid(id) && record(raw)
      && exact(raw, ["sceneId", "tableId", "selectedNode", "selectedKey", "view", "decimals", "axisMin", "axisMax", "maxWidth"])
      && allStrings(raw, ["sceneId", "tableId", "selectedNode", "selectedKey", "decimals", "axisMin", "axisMax", "maxWidth"])
      && uuid(raw.sceneId) && uuid(raw.tableId) && uuid(raw.selectedNode) && ["counter", "label", "bar"].includes(String(raw.view))))) return false;
  if (value.selectedBinding !== undefined && (typeof value.selectedBinding !== "string"
    || (value.selectedBinding !== "" && (!uuid(value.selectedBinding) || !record(value.bindings) || !Object.hasOwn(value.bindings, value.selectedBinding))))) return false;
  return Object.entries(value.tables).every(([key, raw]) => (key === "new" || uuid(key)) && record(raw)
    && exact(raw, ["draftId", "label", "assetId", "rowsText", "selectedNode", "selectedKey", "view", "decimals", "axisMin", "axisMax", "maxWidth"])
    && allStrings(raw, ["draftId", "label", "assetId", "rowsText", "selectedNode", "selectedKey", "decimals", "axisMin", "axisMax", "maxWidth"])
    && uuid(raw.draftId) && (key === "new" || raw.draftId === key) && ["counter", "label", "bar"].includes(String(raw.view)));
}
function tableShape(value: unknown): boolean {
  return record(value) && exact(value, ["schema", "id", "label", "version", "source_asset_id", "source_sha256", "rows"])
    && value.schema === "motionwright.measured-table/1" && uuid(value.id) && typeof value.label === "string" && integer(value.version) && value.version > 0
    && uuid(value.source_asset_id) && typeof value.source_sha256 === "string" && /^[0-9a-f]{64}$/.test(value.source_sha256) && Array.isArray(value.rows) && value.rows.length <= 64
    && value.rows.every(row => record(row) && exact(row, ["key", "label", "milli", "unit"]) && allStrings(row, ["key", "label", "unit"]) && typeof row.milli === "number" && Number.isSafeInteger(row.milli));
}
function changeShape(value: unknown, kind: WorkbenchKind): value is WorkbenchChange {
  if (!record(value) || typeof value.type !== "string") return false;
  if (kind === "stage") {
    if (value.type === "detach_product_stage") return exact(value, ["type", "instance_id"]) && uuid(value.instance_id);
    if (value.type === "bind_stage_capture") return exact(value, ["type", "stage_id", "capture_id"]) && uuid(value.stage_id) && uuid(value.capture_id);
    if (value.type === "detach_stage_capture") return exact(value, ["type", "stage_id"]) && uuid(value.stage_id);
    if (value.type !== "upsert_product_stage" || !exact(value, ["type", "instance_id", "scene_id", "config"]) || !uuid(value.instance_id) || !uuid(value.scene_id) || !record(value.config)) return false;
    try { validateProductStageConfig(value.config as unknown as ProductStageConfig); return true; } catch { return false; }
  }
  if (kind === "procedural") {
    if (value.type === "detach_procedural_field") return exact(value, ["type", "instance_id"]) && uuid(value.instance_id);
    if (value.type !== "upsert_procedural_field" || !exact(value, ["type", "instance_id", "scene_id", "config"]) || !uuid(value.instance_id) || !uuid(value.scene_id) || !record(value.config)
      || !exact(value.config, [...PROCEDURAL_NUMBERS, "distribution", "shape", "fill"], ["influence", "path"])) return false;
    try { validateProceduralConfig(value.config as unknown as ProceduralConfig); return true; } catch { return false; }
  }
  if (value.type === "upsert_measured_table") return exact(value, ["type", "table"]) && tableShape(value.table);
  if (value.type === "detach_measured_binding") return exact(value, ["type", "binding_id"]) && uuid(value.binding_id);
  if (value.type !== "upsert_measured_binding" || !exact(value, ["type", "binding_id", "scene_id", "node_id", "table_id", "datum_key", "view"])
    || ![value.binding_id, value.scene_id, value.node_id, value.table_id].every(uuid) || typeof value.datum_key !== "string" || !record(value.view)) return false;
  try { validateMeasuredView(value.view as MeasuredView); return true; } catch { return false; }
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
function cleanDraft(value: unknown, scope: string): WorkbenchDraft {
  if (!record(value) || !exact(value, ["schema", "scope", "target", "baseRevision", "base", "input", "sequence", "intent", "pending"])
    || value.schema !== "motionwright.creative-workbench-draft/1" || value.scope !== scope || !record(value.target)
    || !integer(value.baseRevision) || !integer(value.sequence) || typeof value.intent !== "boolean" || !inputShape(value.input) || !inputShape(value.base))
    throw new Error("Unsupported workbench recovery shape. Earlier bytes were retained.");
  const target = value.target, identity: unknown = JSON.parse(scope);
  if (!exact(target, ["kind", "sceneId"]) || !["stage", "procedural", "measured"].includes(String(target.kind)) || value.input.kind !== target.kind || value.base.kind !== target.kind
    || (target.sceneId !== null && (typeof target.sceneId !== "string" || !target.sceneId)) || !Array.isArray(identity) || identity.length !== 5
    || identity.slice(0, 3).some(item => typeof item !== "string" || !item) || identity[3] !== target.kind || identity[4] !== target.sceneId)
    throw new Error("This workbench copy belongs to another editor scope.");
  let pending: WorkbenchPending | null = null;
  if (value.pending !== null) {
    const sent = value.pending;
    if (!record(sent) || !exact(sent, ["id", "revision", "sequence", "input", "submitted", "nodesBefore", "outcome"], ["diagnostic"])
      || !uuid(sent.id) || !integer(sent.revision) || sent.revision !== value.baseRevision || !integer(sent.sequence) || sent.sequence > value.sequence
      || !inputShape(sent.input) || sent.input.kind !== target.kind || !changeShape(sent.submitted, target.kind as WorkbenchKind)
      || (sent.nodesBefore !== null && !nodesShape(sent.nodesBefore)) || !["UNKNOWN", "NOT_SENT"].includes(String(sent.outcome))
      || (sent.diagnostic !== undefined && typeof sent.diagnostic !== "string")) throw new Error("Unreadable workbench comparison snapshot.");
    if ("scene_id" in sent.submitted && sent.submitted.scene_id !== target.sceneId) throw new Error("The comparison snapshot belongs to another scene.");
    const preservesNodes = ["detach_product_stage", "bind_stage_capture", "detach_stage_capture", "detach_procedural_field", "detach_measured_binding"].includes(sent.submitted.type);
    if (preservesNodes !== (sent.nodesBefore !== null)) throw new Error("Incomplete Canvas preservation comparison.");
    // Recovered NOT_SENT is conservative UNKNOWN, never permission to replay.
    pending = { ...clone(sent), outcome: "UNKNOWN" } as unknown as WorkbenchPending;
  }
  return { schema: "motionwright.creative-workbench-draft/1", scope, target: clone(target) as WorkbenchTarget,
    baseRevision: value.baseRevision, base: clone(value.base), input: clone(value.input), sequence: value.sequence, intent: value.intent, pending };
}
export function decodeWorkbenchCopy(raw: string, scope: string, writerId: string): WorkbenchCopy {
  if (bytes(raw) > MAX_WORKBENCH_COPY_BYTES) throw new Error("The workbench copy exceeds 1 MiB. Download the complete input before reducing it.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || !exact(value, ["schema", "scope", "writerId", "updatedAt", "draft"]) || value.schema !== "motionwright.creative-workbench-recovery/1"
    || value.scope !== scope || value.writerId !== writerId || !uuid(writerId) || !integer(value.updatedAt)) throw new Error("Unreadable or foreign workbench recovery copy.");
  return { schema: "motionwright.creative-workbench-recovery/1", scope, writerId, updatedAt: value.updatedAt, draft: cleanDraft(value.draft, scope) };
}
export function encodeWorkbenchCopy(copy: WorkbenchCopy): string { const raw = JSON.stringify(copy); decodeWorkbenchCopy(raw, copy.scope, copy.writerId); return raw; }
export function encodeWorkbenchFile(draft: WorkbenchDraft): string {
  return JSON.stringify({ schema: "motionwright.creative-workbench-file/1", exportedAt: Date.now(), draft: cleanDraft(draft, draft.scope) }, null, 2);
}
export function readWorkbenchCopies(storage: WorkbenchStorage, scope: string): { entries: WorkbenchRecoveryEntry[]; limited: boolean } {
  const prefix = WORKBENCH_RECOVERY_PREFIX + encodeURIComponent(scope) + ":", entries: WorkbenchRecoveryEntry[] = []; let limited = false;
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index); if (!key?.startsWith(prefix)) continue;
    if (entries.length >= MAX_WORKBENCH_COPIES) { limited = true; continue; }
    const raw = storage.getItem(key); if (raw === null) continue;
    const writerId = key.slice(prefix.length); let value: WorkbenchCopy | null = null;
    try { value = decodeWorkbenchCopy(raw, scope, writerId); } catch { /* Corrupt bytes remain available for explicit inspection/discard. */ }
    entries.push({ key, writerId, raw, value });
  }
  return { entries, limited };
}
export function writeWorkbenchCopy(storage: WorkbenchStorage, session: Pick<Storage, "getItem" | "setItem">,
  draft: WorkbenchDraft, custody: WorkbenchCustody): WorkbenchCustody {
  const key = workbenchCopyKey(draft.scope, custody.writerId), pointerKey = workbenchPointerKey(draft.scope);
  const check = () => {
    const pointer = session.getItem(pointerKey);
    if (pointer !== custody.expectedPointer && pointer !== custody.writerId) throw new Error("Another window changed the workbench writer pointer. Earlier copies were retained.");
    if (storage.getItem(key) !== custody.expectedRaw) throw new Error("The workbench copy changed in another window. Earlier bytes were retained.");
  };
  check();
  const raw = encodeWorkbenchCopy({ schema: "motionwright.creative-workbench-recovery/1", scope: draft.scope, writerId: custody.writerId, updatedAt: Date.now(), draft });
  if (custody.expectedRaw !== null) {
    const previous = decodeWorkbenchCopy(custody.expectedRaw, draft.scope, custody.writerId).draft;
    if (!workbenchHasWork(previous) && !sameValue(previous, draft)) throw new Error("A clean workbench checkpoint is immutable. Use a separate writer.");
  }
  let total = bytes(key) + bytes(raw), count = 1;
  for (let index = 0; index < storage.length; index++) {
    const other = storage.key(index); if (!other?.startsWith(WORKBENCH_RECOVERY_PREFIX) || other === key) continue;
    total += bytes(other) + bytes(storage.getItem(other) ?? ""); count++;
  }
  if (total > MAX_WORKBENCH_RECOVERY_BYTES || count > MAX_WORKBENCH_COPIES) throw new Error("Workbench recovery is full (64 copies / 4 MiB). Discard only copies you no longer need.");
  check(); // Recheck custody after enumeration can interleave another writer.
  storage.setItem(key, raw); if (storage.getItem(key) !== raw) throw new Error("The workbench data write was not confirmed.");
  custody.expectedRaw = raw;
  session.setItem(pointerKey, custody.writerId); if (session.getItem(pointerKey) !== custody.writerId) throw new Error("The workbench writer pointer was not confirmed.");
  if (storage.getItem(key) !== raw) throw new Error("The workbench data changed while its pointer was published.");
  custody.expectedPointer = custody.writerId; return custody;
}
export function retainReviewedWorkbenchIntent(storage: WorkbenchStorage, pointers: Pick<Storage, "getItem" | "setItem">,
  before: WorkbenchDraft, next: WorkbenchDraft, original: WorkbenchCustody, writer: string): WorkbenchCustody {
  if (!workbenchUnconfirmed(before) || next.pending || before.scope !== next.scope || !uuid(writer) || writer === original.writerId || original.expectedRaw === null) throw new Error("Invalid workbench recovery fork.");
  const key = workbenchCopyKey(before.scope, original.writerId), raw = storage.getItem(key);
  if (raw === null || raw !== original.expectedRaw || !sameValue(decodeWorkbenchCopy(raw, before.scope, original.writerId).draft, before)) throw new Error("Retain the exact earlier attempt before continuing.");
  const custody = { writerId: writer, expectedRaw: null, expectedPointer: original.expectedPointer };
  writeWorkbenchCopy(storage, pointers, before, custody);
  if (storage.getItem(key) !== raw) throw new Error("The earlier attempt changed during review.");
  writeWorkbenchCopy(storage, pointers, next, custody);
  if (storage.getItem(key) !== raw) throw new Error("The earlier attempt changed during review.");
  return custody;
}
