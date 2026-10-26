import type { CreativePatch, ScopedCanvasEdit } from "./creativeProduction";
import { sameValue } from "./creativeProduction";
import type { NativeReviewRepairDraft } from "./nativeReviewRepair";
import type { CanvasNode, CanvasTransform, Change, Project, Scene } from "./types";

export type DraftEdit = Extract<ScopedCanvasEdit, { kind: "text" | "transform" }>;
type SourceNode = Pick<CanvasNode, "id" | "kind" | "parent_id" | "text"> & CanvasTransform;
interface DraftEditor {
  nodeId: string;
  kind: "text" | "transform";
  text: string;
  transform: CanvasTransform;
  transformRaw?: Record<keyof CanvasTransform, string>;
  source: SourceNode | null;
}
export interface ScopedChangeDraft {
  schema: "motionwright.scoped-change-draft/2";
  scope: string;
  baseRevision: number;
  rationale: string;
  edits: DraftEdit[];
  origins: SourceNode[];
  editor: DraftEditor;
  parkedEditors: DraftEditor[];
  nativeSource: NativeReviewRepairDraft | null;
}
export interface DraftConflict {
  nodeId: string;
  kind: DraftEdit["kind"];
  reason: "missing" | "structure" | "value";
  label: string;
  before: unknown;
  current: unknown;
  proposed: unknown;
}
export const DRAFT_STORAGE_PREFIX = "motionwright.scoped-change-draft.v1:";
export const MAX_DRAFT_BYTES = 512 * 1024;
const transformFields = ["x", "y", "width", "height", "rotation_deg", "opacity"] as const;
const emptyTransform: CanvasTransform = { x: 0, y: 0, width: 0, height: 0, rotation_deg: 0, opacity: 1 };

function transformInput(editor: DraftEditor, field: keyof CanvasTransform): string {
  return editor.transformRaw?.[field] ?? String(editor.transform[field]);
}
function parseTransformInput(raw: string): number | null {
  if (raw.trim() !== raw || !/^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(raw)) return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}
function editorTransform(editor: DraftEditor): CanvasTransform | null {
  if (!editor.transformRaw) return { ...editor.transform };
  const result = { ...editor.transform };
  for (const field of transformFields) {
    const value = parseTransformInput(editor.transformRaw[field]);
    if (value === null) return null;
    result[field] = value;
  }
  return result;
}
/** Keep unfinished decimal input in the local editor, never in a domain edit. */
export function draftTransformInput(draft: ScopedChangeDraft, field: keyof CanvasTransform): string {
  return transformInput(draft.editor, field);
}
export function updateDraftTransformInput(draft: ScopedChangeDraft, field: keyof CanvasTransform, raw: string): ScopedChangeDraft {
  const transformRaw = Object.fromEntries(transformFields.map(key => [key, transformInput(draft.editor, key)])) as Record<keyof CanvasTransform, string>;
  transformRaw[field] = raw;
  return { ...draft, editor: { ...draft.editor, transformRaw } };
}
export function draftTransformInvalidFields(draft: ScopedChangeDraft): (keyof CanvasTransform)[] {
  return transformFields.filter(field => parseTransformInput(transformInput(draft.editor, field)) === null);
}

export function scopedDraftScope(project: Project, scene: Scene | null): string {
  return JSON.stringify([project.id, project.generation, project.active_branch, scene?.id ?? null]);
}
export function nodeTransform(node: CanvasTransform): CanvasTransform {
  return Object.fromEntries(transformFields.map(field => [field, node[field]])) as unknown as CanvasTransform;
}
function sourceNode(node: SourceNode): SourceNode {
  return { id: node.id, kind: node.kind, parent_id: node.parent_id, text: node.text, ...nodeTransform(node) };
}
export function selectDraftNode(draft: ScopedChangeDraft, node: CanvasNode | undefined): ScopedChangeDraft {
  if (node?.id === draft.editor.nodeId) return draft;
  const parked = draft.parkedEditors.filter(editor => editor.nodeId !== draft.editor.nodeId && editor.nodeId !== node?.id);
  if (editorHasInputs(draft, draft.editor)) parked.push(structuredClone(draft.editor));
  if (parked.length > 128) throw new Error("The local draft is limited to 128 other object inputs. Add or discard inputs before changing object.");
  const restored = draft.parkedEditors.find(editor => editor.nodeId === node?.id);
  return { ...draft, parkedEditors: parked, editor: restored ? { ...restored, kind: draft.editor.kind } : {
    nodeId: node?.id ?? "", kind: draft.editor.kind, text: node?.text ?? "",
    transform: node ? nodeTransform(node) : { ...emptyTransform }, source: node ? sourceNode(node) : null,
  } };
}
export function createScopedDraft(project: Project, scene: Scene | null): ScopedChangeDraft {
  return selectDraftNode({
    schema: "motionwright.scoped-change-draft/2", scope: scopedDraftScope(project, scene),
    baseRevision: project.revision, rationale: "", edits: [], origins: [], parkedEditors: [], nativeSource: null,
    editor: { nodeId: "", kind: "text", text: "", transform: { ...emptyTransform }, source: null },
  }, scene?.nodes[0]);
}
export function draftEditorEdit(draft: ScopedChangeDraft): DraftEdit {
  return editorEdit(draft.editor, draft.editor.kind);
}
function editorEdit(editor: DraftEditor, kind: DraftEdit["kind"]): DraftEdit {
  if (kind === "text") return { kind: "text", node_id: editor.nodeId, text: editor.text };
  const transform = editorTransform(editor);
  if (!transform) throw new Error("Complete every transform value with a finite decimal number before adding it to the proposal.");
  return { kind: "transform", node_id: editor.nodeId, transform };
}
function editValue(edit: DraftEdit): unknown {
  return edit.kind === "text" ? edit.text : edit.transform;
}
function sourceValue(node: SourceNode | CanvasNode, kind: DraftEdit["kind"]): unknown {
  return kind === "text" ? node.text ?? "" : nodeTransform(node);
}
function editorValue(editor: DraftEditor, kind: DraftEdit["kind"]): unknown {
  if (kind === "text") return editor.text;
  // Invalid input remains visible and differs from every numeric domain value.
  // Complete decimal spellings compare numerically, including after a receipt.
  return editorTransform(editor) ?? { ...editor.transformRaw };
}
export function draftEditorDirty(draft: ScopedChangeDraft): boolean {
  return draft.editor.source !== null && !sameValue(editorValue(draft.editor, draft.editor.kind), sourceValue(draft.editor.source, draft.editor.kind));
}
function editorFieldHasInput(draft: ScopedChangeDraft, editor: DraftEditor, kind: DraftEdit["kind"]): boolean {
  if (!editor.source) return false;
  const value = editorValue(editor, kind);
  const queued = draft.edits.find(edit => edit.node_id === editor.nodeId && edit.kind === kind);
  return !sameValue(value, sourceValue(editor.source, kind)) || (queued !== undefined && !sameValue(value, editValue(queued)));
}
function editorHasInputs(draft: ScopedChangeDraft, editor: DraftEditor): boolean {
  return editorFieldHasInput(draft, editor, "text") || editorFieldHasInput(draft, editor, "transform");
}
export function scopedDraftHasWork(draft: ScopedChangeDraft): boolean {
  return draft.edits.length > 0 || draft.rationale.length > 0 ||
    [draft.editor, ...draft.parkedEditors].some(editor => editorHasInputs(draft, editor));
}
export function unqueuedInputCount(draft: ScopedChangeDraft): number {
  return [draft.editor, ...draft.parkedEditors].flatMap(editor => (["text", "transform"] as const)
    .filter(kind => editorFieldHasInput(draft, editor, kind) && !draft.edits.some(edit =>
      edit.node_id === editor.nodeId && edit.kind === kind && sameValue(editValue(edit), editorValue(editor, kind))))).length;
}
export function queueDraftEdit(draft: ScopedChangeDraft): ScopedChangeDraft {
  const source = draft.editor.source;
  if (!source || source.id !== draft.editor.nodeId) throw new Error("Select an object from the draft's source revision.");
  const edit = draftEditorEdit(draft);
  const edits = [...draft.edits.filter(item => item.node_id !== edit.node_id || item.kind !== edit.kind), edit];
  if (edits.length > 128) throw new Error("A scoped proposal is limited to 128 edits.");
  const origins = draft.origins.filter(origin => edits.some(item => item.node_id === origin.id));
  if (!origins.some(origin => origin.id === source.id)) origins.push(structuredClone(source));
  return { ...draft, edits, origins };
}
export function removeDraftEdit(draft: ScopedChangeDraft, nodeId: string, kind: DraftEdit["kind"]): ScopedChangeDraft {
  const edits = draft.edits.filter(edit => edit.node_id !== nodeId || edit.kind !== kind);
  return { ...draft, edits, origins: draft.origins.filter(origin => edits.some(edit => edit.node_id === origin.id)) };
}
export function draftPatch(draft: ScopedChangeDraft, sceneId: string): CreativePatch {
  return { scene_id: sceneId, base_revision: draft.baseRevision, rationale: draft.rationale, edits: structuredClone(draft.edits) };
}

/** Editing recovery only. This does not grant authority or decide render freshness.
 * Every recovered proposal still needs a new domain preview and a transactional CAS.
 */
export function scopedDraftConflicts(draft: ScopedChangeDraft, scene: Scene | null): DraftConflict[] {
  const candidates = draft.edits.map(edit => ({ nodeId: edit.node_id, kind: edit.kind, proposed: editValue(edit),
    source: draft.origins.find(node => node.id === edit.node_id) }));
  for (const editor of [draft.editor, ...draft.parkedEditors]) {
    for (const kind of ["text", "transform"] as const) {
      if (editorFieldHasInput(draft, editor, kind) && !candidates.some(item => item.nodeId === editor.nodeId && item.kind === kind)) {
        candidates.push({ nodeId: editor.nodeId, kind, proposed: editorValue(editor, kind), source: editor.source ?? undefined });
      }
    }
  }
  return candidates.flatMap(({ nodeId, kind, proposed, source }) => {
    const current = scene?.nodes.find(node => node.id === nodeId);
    const reason = !source || !current ? "missing"
      : source.kind !== current.kind || (kind === "transform" && source.parent_id !== current.parent_id) ? "structure"
      : !sameValue(sourceValue(source, kind), sourceValue(current, kind)) ? "value" : null;
    return reason ? [{ nodeId, kind, reason,
      label: current?.name ?? "Unavailable object", before: source ? sourceValue(source, kind) : null,
      current: current ? sourceValue(current, kind) : null, proposed }] : [];
  });
}
export function keepCurrentDraftValue(draft: ScopedChangeDraft, conflict: DraftConflict, scene: Scene | null): ScopedChangeDraft {
  const current = scene?.nodes.find(node => node.id === conflict.nodeId);
  const keepProperty = (editor: DraftEditor): DraftEditor => {
    if (editor.nodeId !== conflict.nodeId || !editor.source) return editor;
    // Resolve one property only. Retain the old structure and other property's
    // source until every overlapping edit has been resolved explicitly.
    if (conflict.kind === "text") {
      const text = current ? current.text ?? "" : editor.source.text ?? "";
      return { ...editor, text, source: { ...editor.source, text } };
    }
    const transform = nodeTransform(current ?? editor.source);
    const next = { ...editor, transform, source: { ...editor.source, ...transform } };
    delete next.transformRaw;
    return next;
  };
  return { ...removeDraftEdit(draft, conflict.nodeId, conflict.kind),
    editor: keepProperty(draft.editor), parkedEditors: draft.parkedEditors.map(keepProperty) };
}
/** An explicit author decision releases the frame binding. Keep the original
 * observation in the rationale as context; it is never a current frame receipt.
 */
export function continueDraftManually(draft: ScopedChangeDraft): ScopedChangeDraft {
  const source = draft.nativeSource;
  if (!source) return draft;
  const context = `Earlier frame observation — context only, not current renderer verification: project ${source.projectId}; generation ${source.generation}; scene ${source.sceneId}; profile ${source.profileId}; revision ${source.revision}; frame ${source.frameIndex}; PNG SHA-256 ${source.frameSha256}.`;
  return { ...draft, nativeSource: null, rationale: draft.rationale + "\n\n" + context };
}
function refreshEditor(draft: ScopedChangeDraft, editor: DraftEditor, node: CanvasNode): DraftEditor {
  const keepTransform = editorFieldHasInput(draft, editor, "transform");
  const next = { ...editor, source: sourceNode(node),
    text: editorFieldHasInput(draft, editor, "text") ? editor.text : node.text ?? "",
    transform: keepTransform ? { ...editor.transform } : nodeTransform(node) };
  if (!keepTransform) delete next.transformRaw;
  else if (editor.transformRaw) next.transformRaw = { ...editor.transformRaw };
  return next;
}
function refreshEditors(draft: ScopedChangeDraft, scene: Scene): Pick<ScopedChangeDraft, "editor" | "parkedEditors"> {
  const selected = scene.nodes.find(node => node.id === draft.editor.nodeId);
  const editor = selected ? refreshEditor(draft, draft.editor, selected)
    : { ...draft.editor, nodeId: "", source: null, text: "", transform: { ...emptyTransform } };
  if (!selected) delete editor.transformRaw;
  const parkedEditors = draft.parkedEditors.flatMap(buffer => {
    const node = scene.nodes.find(item => item.id === buffer.nodeId);
    return node ? [refreshEditor(draft, buffer, node)] : [];
  });
  return { editor, parkedEditors };
}
export function rebaseScopedDraft(draft: ScopedChangeDraft, project: Project, scene: Scene | null): ScopedChangeDraft {
  if (draft.scope !== scopedDraftScope(project, scene) || !scene) throw new Error("The proposal belongs to another project, branch or scene.");
  if (draft.nativeSource) throw new Error("Re-inspect current native frames before replacing a frame-bound repair source.");
  if (scopedDraftConflicts(draft, scene).length) throw new Error("Resolve overlapping object changes before reviewing this proposal on the current revision.");
  return { ...draft, baseRevision: project.revision,
    origins: [...new Set(draft.edits.map(edit => edit.node_id))].map(id => sourceNode(scene.nodes.find(item => item.id === id)!)),
    ...refreshEditors(draft, scene),
  };
}
/** Clear only the reviewed queue after a matching persisted receipt. Inputs that
 * were never queued remain editable, with the committed revision as their base.
 */
export function retainUnqueuedInputs(draft: ScopedChangeDraft, project: Project, scene: Scene | null): ScopedChangeDraft {
  if (!scene || draft.scope !== scopedDraftScope(project, scene)) return draft;
  const next = { ...draft, baseRevision: project.revision, edits: [], origins: [], ...refreshEditors(draft, scene) };
  if (!unqueuedInputCount(next)) return createScopedDraft(project, scene);
  return { ...next, parkedEditors: next.parkedEditors.filter(editor => editorHasInputs(next, editor)) };
}

export function scopedReviewKey(project: Project, scene: Scene | null, draft: ScopedChangeDraft, undoTarget: string | null): string {
  return JSON.stringify([scopedDraftScope(project, scene), project.revision, draft.baseRevision,
    undoTarget ? { type: "undo_creative_patch", patch_id: undoTarget } : draftPatch(draft, scene?.id ?? ""), draft.nativeSource]);
}

export interface ReviewedScopedChange {
  key: string;
  projectId: string;
  generation: string;
  branchId: string;
  revision: number;
  change: Change;
  preview: { source_revision: number; scene_id: string; before: CanvasNode[]; after: CanvasNode[] };
}
export function reviewedChangeWasCommitted(project: Project, reviewed: ReviewedScopedChange): boolean {
  if (project.id !== reviewed.projectId || project.generation !== reviewed.generation ||
      project.active_branch !== reviewed.branchId || project.revision !== reviewed.revision + 1) return false;
  const record = project.production_design?.patches?.at(-1);
  if (!record || record.source_revision !== reviewed.revision || record.scene_id !== reviewed.preview.scene_id ||
      !sameValue(record.before, reviewed.preview.before) || !sameValue(record.after, reviewed.preview.after)) return false;
  return reviewed.change.type === "apply_creative_patch"
    ? record.reverts === null && record.rationale === reviewed.change.patch.rationale
    : reviewed.change.type === "undo_creative_patch" && record.reverts === reviewed.change.patch_id;
}

const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const validTransform = (value: unknown): value is CanvasTransform => record(value) && transformFields.every(field => typeof value[field] === "number" && Number.isFinite(value[field]));
const validTransformRaw = (value: unknown): value is Record<keyof CanvasTransform, string> => record(value) &&
  transformFields.every(field => typeof value[field] === "string");
const validSource = (value: unknown): value is SourceNode => record(value) && typeof value.id === "string" && typeof value.kind === "string" &&
  (value.parent_id === null || typeof value.parent_id === "string") && (value.text === null || typeof value.text === "string") && validTransform(value);
const validEditor = (value: unknown): value is DraftEditor => record(value) && typeof value.nodeId === "string" &&
  ["text", "transform"].includes(String(value.kind)) && typeof value.text === "string" && validTransform(value.transform) &&
  (value.transformRaw === undefined || validTransformRaw(value.transformRaw)) &&
  (value.source === null || (validSource(value.source) && value.source.id === value.nodeId));
const cleanEditor = (editor: DraftEditor): DraftEditor => ({ nodeId: editor.nodeId, kind: editor.kind,
  text: editor.text, transform: nodeTransform(editor.transform), source: editor.source ? sourceNode(editor.source) : null,
  ...(editor.transformRaw ? { transformRaw: Object.fromEntries(transformFields.map(field => [field, editor.transformRaw![field]])) as Record<keyof CanvasTransform, string> } : {}) });
function validNativeSource(value: unknown): value is NativeReviewRepairDraft | null {
  return value === null || (record(value) && value.schema === "motionwright.native-review-repair-draft/1" &&
    ["projectId", "generation", "sceneId", "profileId", "targetNodeId", "rationale"].every(key => typeof value[key] === "string") &&
    Number.isSafeInteger(value.revision) && Number(value.revision) >= 0 && Number.isSafeInteger(value.frameIndex) && Number(value.frameIndex) >= 0 &&
    typeof value.frameSha256 === "string" && /^[a-f0-9]{64}$/.test(value.frameSha256) && ["text", "transform"].includes(String(value.editKind)));
}
/** Local session backups are untrusted input, never persisted previews or authority. */
export function decodeScopedDraft(raw: string, scope: string): ScopedChangeDraft {
  if (new TextEncoder().encode(raw).length > MAX_DRAFT_BYTES) throw new Error("Saved proposal exceeds the local draft budget.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || (value.schema !== "motionwright.scoped-change-draft/1" && value.schema !== "motionwright.scoped-change-draft/2") || value.scope !== scope ||
      !Number.isSafeInteger(value.baseRevision) || Number(value.baseRevision) < 0 || typeof value.rationale !== "string" ||
      !Array.isArray(value.edits) || value.edits.length > 128 || !value.edits.every(edit => record(edit) && typeof edit.node_id === "string" &&
        (edit.kind === "text" ? typeof edit.text === "string" : edit.kind === "transform" && validTransform(edit.transform))) ||
      !Array.isArray(value.origins) || value.origins.length > 128 || !value.origins.every(validSource) ||
      !validEditor(value.editor) || !Array.isArray(value.parkedEditors) || value.parkedEditors.length > 128 || !value.parkedEditors.every(validEditor) ||
      !validNativeSource(value.nativeSource)) {
    throw new Error("Saved proposal has an unsupported shape or belongs to another editing scope.");
  }
  // Project validation and permission checks belong to the service. Only restore
  // the typed editor fields; unknown fields cannot become a command or grant.
  return {
    schema: "motionwright.scoped-change-draft/2", scope, baseRevision: value.baseRevision as number,
    rationale: value.rationale, edits: (value.edits as DraftEdit[]).map(edit => edit.kind === "text"
      ? { kind: "text", node_id: edit.node_id, text: edit.text }
      : { kind: "transform", node_id: edit.node_id, transform: nodeTransform(edit.transform) }),
    origins: (value.origins as SourceNode[]).map(node => ({ id: node.id, kind: node.kind, parent_id: node.parent_id, text: node.text, ...nodeTransform(node) })),
    editor: cleanEditor(value.editor), parkedEditors: value.parkedEditors.map(cleanEditor),
    nativeSource: value.nativeSource ? { schema: "motionwright.native-review-repair-draft/1",
      projectId: value.nativeSource.projectId, generation: value.nativeSource.generation, revision: value.nativeSource.revision,
      sceneId: value.nativeSource.sceneId, profileId: value.nativeSource.profileId, frameIndex: value.nativeSource.frameIndex,
      frameSha256: value.nativeSource.frameSha256, targetNodeId: value.nativeSource.targetNodeId,
      editKind: value.nativeSource.editKind, rationale: value.nativeSource.rationale } : null,
  };
}
export function encodeScopedDraft(draft: ScopedChangeDraft): string {
  const raw = JSON.stringify(draft);
  if (new TextEncoder().encode(raw).length > MAX_DRAFT_BYTES) throw new Error("The proposal is too large for the local draft backup. Keep this view open until you commit or discard it.");
  return raw;
}
