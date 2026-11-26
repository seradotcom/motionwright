import type { CreativePatch, ScopedCanvasEdit } from "./creativeProduction";
import { sameValue } from "./creativeProduction";
import type { NativeReviewRepairDraft } from "./nativeReviewRepair";
import type { CanvasNode, CanvasTransform, Change, Project, Scene } from "./types";
import { CANVAS_STYLE_FIELDS, cleanScopedMotion, cleanScopedStyle, parseScopedMotion, parseScopedStyle,
  scopedMotionRows, scopedStyleRaw, validScopedMotion, validScopedMotionRows, validScopedStyle, validScopedStyleRaw } from "./scopedPaintMotion";
import type { ScopedMotionRow, ScopedStyleRaw } from "./scopedPaintMotion";
import { arrangeCanvasFrames, emptyArrangementSelection, validArrangementSelection } from "./canvasArrangement";
import type { ArrangementSelection } from "./canvasArrangement";
import { defaultMotionRangeInput, retimeMotionRows, validMotionRangeInput } from "./timelineMotionRange";
import type { MotionRangeInput } from "./timelineMotionRange";
import type { DeliverableProfile } from "./types";
import { focusTransferEdits, type FocusTransferInput } from "./focusTransfer";
import { measuredComparisonEdits, type MeasuredComparisonInput } from "./measuredComparison";

export type DraftEdit = Extract<ScopedCanvasEdit, { kind: "text" | "transform" | "style" | "motion" }>;
type SourceNode = Pick<CanvasNode, "id" | "kind" | "parent_id" | "text"> & CanvasTransform &
  Partial<Pick<CanvasNode, "style" | "keyframes" | "coordinate_space" | "relations">>;
interface DraftEditor {
  nodeId: string;
  kind: DraftEdit["kind"];
  text: string;
  transform: CanvasTransform;
  transformRaw?: Record<keyof CanvasTransform, string>;
  styleRaw?: ScopedStyleRaw;
  motionRaw?: ScopedMotionRow[];
  motionRangeRaw?: MotionRangeInput;
  source: SourceNode | null;
}
export interface ScopedChangeDraft {
  schema: "motionwright.scoped-change-draft/5";
  scope: string;
  baseRevision: number;
  rationale: string;
  edits: DraftEdit[];
  origins: SourceNode[];
  editor: DraftEditor;
  parkedEditors: DraftEditor[];
  nativeSource: NativeReviewRepairDraft | null;
  arrangement?: ArrangementSelection;
  /** Generated recipe admission depends on the whole saved source revision,
   * not just the geometry fields that the resulting patch edits. */
  recipeSource?: { kind: "focus_transfer" | "measured_comparison"; revision: number };
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
const draftKinds = ["text", "transform", "style", "motion"] as const;
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

function styleInput(editor: DraftEditor): ScopedStyleRaw {
  return editor.styleRaw ?? (editor.source?.style ? scopedStyleRaw(editor.source.style)
    : Object.fromEntries(CANVAS_STYLE_FIELDS.map(field => [field, ""])) as ScopedStyleRaw);
}
function motionInput(editor: DraftEditor): ScopedMotionRow[] {
  return editor.motionRaw ?? scopedMotionRows(editor.source?.keyframes ?? []);
}
export function draftStyleInput(draft: ScopedChangeDraft): ScopedStyleRaw {
  return { ...styleInput(draft.editor) };
}
export function updateDraftStyleInput(draft: ScopedChangeDraft, field: keyof ScopedStyleRaw, raw: string): ScopedChangeDraft {
  return { ...draft, editor: { ...draft.editor, styleRaw: { ...styleInput(draft.editor), [field]: raw } } };
}
export function draftMotionInput(draft: ScopedChangeDraft): ScopedMotionRow[] {
  return motionInput(draft.editor).map(row => ({ ...row }));
}
export function updateDraftMotionInput(draft: ScopedChangeDraft, rows: ScopedMotionRow[]): ScopedChangeDraft {
  if (!validScopedMotionRows(rows)) throw new Error("A motion draft is limited to 128 typed keyframe rows.");
  return { ...draft, editor: { ...draft.editor, motionRaw: rows.map(row => ({ ...row })) } };
}
export function updateDraftMotionRangeInput(draft: ScopedChangeDraft, input: MotionRangeInput): ScopedChangeDraft {
  if (!validMotionRangeInput(input)) throw new Error("Animation timing inputs exceed their bounded draft.");
  return {...draft,editor:{...draft.editor,motionRangeRaw:{mode:input.mode,start:input.start,end:input.end}}};
}
export function applyDraftMotionRange(draft: ScopedChangeDraft, duration: Scene["duration"]): ScopedChangeDraft {
  const rows=draftMotionInput(draft);
  const next=updateDraftMotionInput(draft,retimeMotionRows(rows,draft.editor.motionRangeRaw??defaultMotionRangeInput(rows),duration));
  delete next.editor.motionRangeRaw;
  return next;
}
function sourceHasKind(source: SourceNode | null | undefined, kind: DraftEdit["kind"]): boolean {
  return Boolean(source && (kind === "style" ? source.style !== undefined : kind === "motion" ? source.keyframes !== undefined : true));
}
export function draftEditorSourceAvailable(draft: ScopedChangeDraft): boolean {
  return draft.editor.source?.id === draft.editor.nodeId && sourceHasKind(draft.editor.source, draft.editor.kind);
}
export function scopedDraftScope(project: Project, scene: Scene | null): string {
  return JSON.stringify([project.id, project.generation, project.active_branch, scene?.id ?? null]);
}
export function nodeTransform(node: CanvasTransform): CanvasTransform {
  return Object.fromEntries(transformFields.map(field => [field, node[field]])) as unknown as CanvasTransform;
}
function sourceNode(node: SourceNode): SourceNode {
  return { id: node.id, kind: node.kind, parent_id: node.parent_id, text: node.text, ...nodeTransform(node),
    ...(node.style ? { style: cleanScopedStyle(node.style) } : {}),
    ...(node.keyframes ? { keyframes: cleanScopedMotion(node.keyframes) } : {}),
    ...(node.coordinate_space ? { coordinate_space: node.coordinate_space } : {}),
    ...(node.relations ? { relations: node.relations.map(relation => ({ id: relation.id, kind: relation.kind, target_id: relation.target_id })) } : {}) };
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
    schema: "motionwright.scoped-change-draft/5", scope: scopedDraftScope(project, scene),
    baseRevision: project.revision, rationale: "", edits: [], origins: [], parkedEditors: [], nativeSource: null,
    arrangement: emptyArrangementSelection(),
    editor: { nodeId: "", kind: "text", text: "", transform: { ...emptyTransform }, source: null },
  }, scene?.nodes[0]);
}
/** Old text/transform backups can acquire the additional read-only source fields
 * only while their exact original revision and original object values still match.
 * A missing original object is never reconstructed from current data.
 */
export function hydrateScopedDraft(draft: ScopedChangeDraft, project: Project, scene: Scene | null): ScopedChangeDraft {
  if (draft.scope !== scopedDraftScope(project, scene) || draft.baseRevision !== project.revision || !scene) return draft;
  const hydrate = (source: SourceNode): SourceNode => {
    if (source.style !== undefined || source.keyframes !== undefined) return source;
    const current = scene.nodes.find(node => node.id === source.id);
    return current && source.kind === current.kind && source.parent_id === current.parent_id && source.text === current.text &&
      sameValue(nodeTransform(source), nodeTransform(current)) ? sourceNode(current) : source;
  };
  const editor = (value: DraftEditor): DraftEditor => ({ ...value, source: value.source ? hydrate(value.source) : null });
  return { ...draft, origins: draft.origins.map(hydrate), editor: editor(draft.editor), parkedEditors: draft.parkedEditors.map(editor) };
}
export function draftEditorEdit(draft: ScopedChangeDraft): DraftEdit {
  return editorEdit(draft.editor, draft.editor.kind);
}
function editorEdit(editor: DraftEditor, kind: DraftEdit["kind"]): DraftEdit {
  if (!sourceHasKind(editor.source, kind)) throw new Error("The original values for this edit are unavailable. Review the draft on the current revision or start a new draft.");
  if (kind === "text") return { kind: "text", node_id: editor.nodeId, text: editor.text };
  if (kind === "style") return { kind: "style", node_id: editor.nodeId, style: parseScopedStyle(styleInput(editor)) };
  if (kind === "motion") return { kind: "motion", node_id: editor.nodeId, keyframes: parseScopedMotion(motionInput(editor)) };
  const transform = editorTransform(editor);
  if (!transform) throw new Error("Complete every transform value with a finite decimal number before adding it to the proposal.");
  return { kind: "transform", node_id: editor.nodeId, transform };
}
function editValue(edit: DraftEdit): unknown {
  switch (edit.kind) {
    case "text": return edit.text;
    case "transform": return edit.transform;
    case "style": return edit.style;
    case "motion": return edit.keyframes;
  }
}
function sourceValue(node: SourceNode | CanvasNode, kind: DraftEdit["kind"]): unknown {
  switch (kind) {
    case "text": return node.text ?? "";
    case "transform": return nodeTransform(node);
    case "style": return node.style;
    case "motion": return node.keyframes;
  }
}
function editorValue(editor: DraftEditor, kind: DraftEdit["kind"]): unknown {
  if (kind === "text") return editor.text;
  if (kind === "style") {
    if (!editor.styleRaw) return editor.source?.style;
    try { return parseScopedStyle(styleInput(editor)); } catch { return { invalidStyle: styleInput(editor) }; }
  }
  if (kind === "motion") {
    if (!editor.motionRaw) return editor.source?.keyframes;
    try { return parseScopedMotion(motionInput(editor)); } catch { return { invalidMotion: motionInput(editor) }; }
  }
  // Invalid input remains visible and differs from every numeric domain value.
  // Complete decimal spellings compare numerically, including after a receipt.
  return editorTransform(editor) ?? { ...editor.transformRaw };
}
export function draftEditorDirty(draft: ScopedChangeDraft): boolean {
  return draft.editor.source !== null && !sameValue(editorValue(draft.editor, draft.editor.kind), sourceValue(draft.editor.source, draft.editor.kind));
}
function editorFieldHasInput(draft: ScopedChangeDraft, editor: DraftEditor, kind: DraftEdit["kind"]): boolean {
  if (kind === "motion" && editor.motionRangeRaw) return true;
  // Without original values, recovered raw input cannot be classified as
  // unchanged. Keep it recoverable; queueing still requires its source.
  if (!sourceHasKind(editor.source, kind)) return kind === "transform" ? editor.transformRaw !== undefined
    : kind === "style" ? editor.styleRaw !== undefined : kind === "motion" ? editor.motionRaw !== undefined : false;
  const value = editorValue(editor, kind);
  const queued = draft.edits.find(edit => edit.node_id === editor.nodeId && edit.kind === kind);
  return !sameValue(value, sourceValue(editor.source!, kind)) || (queued !== undefined && !sameValue(value, editValue(queued)));
}
function editorHasInputs(draft: ScopedChangeDraft, editor: DraftEditor): boolean {
  return draftKinds.some(kind => editorFieldHasInput(draft, editor, kind));
}
export function scopedDraftHasWork(draft: ScopedChangeDraft): boolean {
  return draft.edits.length > 0 || draft.rationale.length > 0 || Boolean(draft.arrangement?.nodeIds.length) ||
    [draft.editor, ...draft.parkedEditors].some(editor => editorHasInputs(draft, editor));
}
export function unqueuedInputCount(draft: ScopedChangeDraft): number {
  return [draft.editor, ...draft.parkedEditors].flatMap(editor => draftKinds
    .filter(kind => editorFieldHasInput(draft, kind === "motion" ? { ...editor, motionRangeRaw: undefined } : editor, kind) && !draft.edits.some(edit =>
      edit.node_id === editor.nodeId && edit.kind === kind && sameValue(editValue(edit), editorValue(editor, kind))))).length +
    (draft.arrangement?.nodeIds.length ? 1 : 0) + [draft.editor,...draft.parkedEditors].filter(editor=>editor.motionRangeRaw).length;
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
export function updateDraftArrangement(draft: ScopedChangeDraft, selection: ArrangementSelection): ScopedChangeDraft {
  if (!validArrangementSelection(selection)) throw new Error("Choose at most 128 distinct frames and a supported arrangement.");
  return { ...draft, arrangement: { action: selection.action, nodeIds: [...selection.nodeIds] } };
}
/** Build ordinary scoped edits from current source values. Selection preferences
 * carry no prepared geometry; stale revisions require the existing rebase flow.
 * Existing movement drafts are never overwritten by a generated arrangement.
 */
export function stageDraftArrangement(draft: ScopedChangeDraft, project: Project, scene: Scene | null): ScopedChangeDraft {
  if (!scene || draft.scope !== scopedDraftScope(project, scene) || draft.baseRevision !== project.revision)
    throw new Error("Review this proposal on the current project revision before staging an arrangement.");
  if (draft.nativeSource) throw new Error("A native-frame repair has a fixed target. Keep that repair separate from a multi-object arrangement.");
  const working = hydrateScopedDraft(draft, project, scene);
  const selection = working.arrangement ?? emptyArrangementSelection();
  const selected = new Set(selection.nodeIds), result = arrangeCanvasFrames(scene, selection);
  if (working.edits.some(edit => selected.has(edit.node_id) && (edit.kind === "transform" || edit.kind === "motion")))
    throw new Error("Selected objects already have proposed movement. Review or remove those edits before arranging them.");
  for (const editor of [working.editor, ...working.parkedEditors].filter(item => selected.has(item.nodeId))) {
    const current = scene.nodes.find(node => node.id === editor.nodeId)!;
    if (!editor.source || !sameValue(sourceNode(editor.source), sourceNode(current)))
      throw new Error("A selected object's original values are unavailable or changed. Review its draft source first.");
    if (editorFieldHasInput(working, editor, "transform") || editorFieldHasInput(working, editor, "motion"))
      throw new Error("A selected object has uncommitted movement inputs. Stage or discard those inputs before arranging it.");
  }
  const edits = [...working.edits, ...result.edits];
  if (edits.length > 128) throw new Error("This proposal exceeds 128 edits. Keep the existing proposal and choose fewer frames.");
  const moved = new Set(result.edits.map(edit => edit.node_id));
  const origins = [...working.origins];
  for (const id of moved) if (!origins.some(node => node.id === id)) origins.push(sourceNode(scene.nodes.find(node => node.id === id)!));
  const updateEditor = (editor: DraftEditor): DraftEditor => {
    const next = { ...editor };
    for (const edit of result.edits.filter(item => item.node_id === editor.nodeId)) {
      if (edit.kind === "transform") { next.transform = { ...edit.transform }; delete next.transformRaw; }
      else next.motionRaw = scopedMotionRows(edit.keyframes);
    }
    return next;
  };
  const editor = updateEditor(working.editor), parkedEditors = working.parkedEditors.map(updateEditor);
  for (const id of moved) {
    if (id === editor.nodeId || parkedEditors.some(item => item.nodeId === id)) continue;
    const node = scene.nodes.find(item => item.id === id)!;
    parkedEditors.push(updateEditor({ nodeId: id, kind: "transform", text: node.text ?? "", transform: nodeTransform(node), source: sourceNode(node) }));
  }
  if (parkedEditors.length > 128) throw new Error("The arrangement would exceed the recoverable object-input budget. Keep this proposal and choose fewer frames.");
  return { ...working, edits, origins, editor, parkedEditors,
    rationale: working.rationale.trim() ? working.rationale : result.rationale,
    arrangement: { action: selection.action, nodeIds: [] } };
}
/** Stage the reusable two-object transfer through the same recoverable
 * scoped proposal, original snapshots, review and atomic save as manual motion. */
export function stageDraftFocusTransfer(draft:ScopedChangeDraft,project:Project,scene:Scene|null,
  profile:DeliverableProfile|null,input:FocusTransferInput):ScopedChangeDraft {
  if(!scene||!profile||draft.scope!==scopedDraftScope(project,scene)||draft.baseRevision!==project.revision)
    throw new Error("Choose the current scene and saved output profile before staging a focus transfer.");
  if(draft.nativeSource||draft.edits.length||unqueuedInputCount(draft)>0||draft.arrangement?.nodeIds.length)
    throw new Error("Finish or discard the current scoped proposal before staging a focus transfer.");
  const working=hydrateScopedDraft(draft,project,scene), result=focusTransferEdits(project,scene,profile,input);
  const origins=result.edits.map(edit=>sourceNode(scene.nodes.find(node=>node.id===edit.node_id)!));
  const updateEditor=(editor:DraftEditor):DraftEditor=>{
    const edit=result.edits.find(item=>item.node_id===editor.nodeId);
    return edit?{...editor,motionRaw:scopedMotionRows(edit.keyframes)}:editor;
  };
  const editor=updateEditor(working.editor),parkedEditors=working.parkedEditors.map(updateEditor);
  for(const edit of result.edits){
    if(editor.nodeId===edit.node_id||parkedEditors.some(item=>item.nodeId===edit.node_id))continue;
    const node=scene.nodes.find(item=>item.id===edit.node_id)!;
    parkedEditors.push(updateEditor({nodeId:node.id,kind:"motion",text:node.text??"",transform:nodeTransform(node),source:sourceNode(node)}));
  }
  if(parkedEditors.length>128)throw new Error("The focus transfer would exceed the recoverable object input budget.");
  return {...working,edits:result.edits,origins,editor,parkedEditors,rationale:working.rationale.trim()||result.rationale,
    recipeSource:{kind:"focus_transfer",revision:project.revision}};
}

/** Stage source-bound comparison as ordinary original object transforms. */
export function stageDraftMeasuredComparison(draft:ScopedChangeDraft,project:Project,scene:Scene|null,
  input:MeasuredComparisonInput):ScopedChangeDraft {
  if(!scene||draft.scope!==scopedDraftScope(project,scene)||draft.baseRevision!==project.revision)
    throw new Error("Choose the current scene revision before staging a measured comparison.");
  if(draft.nativeSource||draft.edits.length||unqueuedInputCount(draft)>0||draft.arrangement?.nodeIds.length)
    throw new Error("Finish or discard the current scoped proposal before staging a measured comparison.");
  const working=hydrateScopedDraft(draft,project,scene),result=measuredComparisonEdits(project,scene,input);
  const origins=result.edits.map(edit=>sourceNode(scene.nodes.find(node=>node.id===edit.node_id)!));
  const updateEditor=(editor:DraftEditor):DraftEditor=>{
    const edit=result.edits.find(item=>item.node_id===editor.nodeId);
    if(!edit)return editor;
    const next={...editor,transform:{...edit.transform}};delete next.transformRaw;return next;
  };
  const editor=updateEditor(working.editor),parkedEditors=working.parkedEditors.map(updateEditor);
  for(const edit of result.edits){
    if(editor.nodeId===edit.node_id||parkedEditors.some(item=>item.nodeId===edit.node_id))continue;
    const node=scene.nodes.find(item=>item.id===edit.node_id)!;
    parkedEditors.push(updateEditor({nodeId:node.id,kind:"transform",text:node.text??"",transform:nodeTransform(node),source:sourceNode(node)}));
  }
  if(parkedEditors.length>128)throw new Error("The comparison exceeds the recoverable object input budget.");
  return {...working,edits:result.edits,origins,editor,parkedEditors,rationale:working.rationale.trim()||result.rationale,
    recipeSource:{kind:"measured_comparison",revision:project.revision}};
}

export function removeDraftEdit(draft: ScopedChangeDraft, nodeId: string, kind: DraftEdit["kind"]): ScopedChangeDraft {
  const edits = draft.edits.filter(edit => edit.node_id !== nodeId || edit.kind !== kind);
  return { ...draft, edits, origins: draft.origins.filter(origin => edits.some(edit => edit.node_id === origin.id)) };
}
export function draftPatch(draft: ScopedChangeDraft, sceneId: string): CreativePatch {
  if(draft.recipeSource && draft.recipeSource.revision!==draft.baseRevision)
    throw new Error("Restage the recipe on its saved source revision.");
  return { scene_id: sceneId, base_revision: draft.baseRevision, rationale: draft.rationale, edits: structuredClone(draft.edits) };
}

/** Editing recovery only. This does not grant authority or decide render freshness.
 * Every recovered proposal still needs a new domain preview and a transactional CAS.
 */
export function scopedDraftConflicts(draft: ScopedChangeDraft, scene: Scene | null): DraftConflict[] {
  const candidates = draft.edits.map(edit => ({ nodeId: edit.node_id, kind: edit.kind, proposed: editValue(edit),
    source: draft.origins.find(node => node.id === edit.node_id) }));
  for (const editor of [draft.editor, ...draft.parkedEditors]) {
    for (const kind of draftKinds) {
      if (editorFieldHasInput(draft, editor, kind) && !candidates.some(item => item.nodeId === editor.nodeId && item.kind === kind)) {
        candidates.push({ nodeId: editor.nodeId, kind, proposed: editorValue(editor, kind), source: editor.source ?? undefined });
      }
    }
  }
  return candidates.flatMap(({ nodeId, kind, proposed, source }) => {
    const current = scene?.nodes.find(node => node.id === nodeId);
    const spatial = kind === "transform" || kind === "motion";
    const reason = !source || !current || !sourceHasKind(source, kind) ? "missing"
      : source.kind !== current.kind || (spatial && (source.parent_id !== current.parent_id ||
        (source.coordinate_space !== undefined && source.coordinate_space !== current.coordinate_space) ||
        (source.relations !== undefined && !sameValue(source.relations, current.relations)))) ||
        (kind === "motion" && !sameValue(nodeTransform(source), nodeTransform(current))) ? "structure"
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
    if (conflict.kind === "style") {
      const style = current?.style ?? editor.source.style;
      const next = { ...editor, source: { ...editor.source, ...(style ? { style: cleanScopedStyle(style) } : {}) } };
      delete next.styleRaw;
      return next;
    }
    if (conflict.kind === "motion") {
      const keyframes = current?.keyframes ?? editor.source.keyframes;
      const next = { ...editor, source: { ...editor.source, ...(keyframes ? { keyframes: cleanScopedMotion(keyframes) } : {}) } };
      delete next.motionRaw; delete next.motionRangeRaw;
      return next;
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
  // A receipt for other queued work cannot reconstruct a recovered source.
  if (!editor.source) return editor;
  const keepTransform = editorFieldHasInput(draft, editor, "transform");
  const keepStyle = editorFieldHasInput(draft, editor, "style"), keepMotion = editorFieldHasInput(draft, editor, "motion");
  const next = { ...editor, source: sourceNode(node),
    text: editorFieldHasInput(draft, editor, "text") ? editor.text : node.text ?? "",
    transform: keepTransform ? { ...editor.transform } : nodeTransform(node) };
  if (!keepTransform) delete next.transformRaw;
  else if (editor.transformRaw) next.transformRaw = { ...editor.transformRaw };
  if (!keepStyle) delete next.styleRaw;
  else next.styleRaw = { ...styleInput(editor) };
  if (!keepMotion) delete next.motionRaw;
  else next.motionRaw = motionInput(editor).map(row => ({ ...row }));
  return next;
}
function refreshEditors(draft: ScopedChangeDraft, scene: Scene): Pick<ScopedChangeDraft, "editor" | "parkedEditors"> {
  const selected = scene.nodes.find(node => node.id === draft.editor.nodeId);
  const editor = selected ? refreshEditor(draft, draft.editor, selected)
    : { ...draft.editor, nodeId: "", source: null, text: "", transform: { ...emptyTransform } };
  if (!selected) { delete editor.transformRaw; delete editor.styleRaw; delete editor.motionRaw; delete editor.motionRangeRaw; }
  const parkedEditors = draft.parkedEditors.flatMap(buffer => {
    const node = scene.nodes.find(item => item.id === buffer.nodeId);
    return node ? [refreshEditor(draft, buffer, node)] : [];
  });
  return { editor, parkedEditors };
}
export function rebaseScopedDraft(draft: ScopedChangeDraft, project: Project, scene: Scene | null): ScopedChangeDraft {
  if (draft.scope !== scopedDraftScope(project, scene) || !scene) throw new Error("The proposal belongs to another project, branch or scene.");
  if (draft.nativeSource) throw new Error("Re-inspect current native frames before replacing a frame-bound repair source.");
  if (draft.recipeSource && draft.recipeSource.revision !== project.revision)
    throw new Error("Discard or retain this older proposal separately, then restage the recipe on the current saved source. Object-only rebase cannot validate its data, clock or output profile.");
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
  // Only the matching committed proposal consumes its recipe source. Unqueued
  // human input remains an ordinary draft, not a regenerated recipe.
  delete next.recipeSource;
  if (!unqueuedInputCount(next)) return createScopedDraft(project, scene);
  return { ...next, parkedEditors: next.parkedEditors.filter(editor => editorHasInputs(next, editor)) };
}

export function scopedReviewKey(project: Project, scene: Scene | null, draft: ScopedChangeDraft, undoTarget: string | null): string {
  return JSON.stringify([scopedDraftScope(project, scene), project.revision, draft.baseRevision,
    undoTarget ? { type: "undo_creative_patch", patch_id: undoTarget } : draftPatch(draft, scene?.id ?? ""), draft.nativeSource, draft.recipeSource ?? null]);
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
  (value.parent_id === null || typeof value.parent_id === "string") && (value.text === null || typeof value.text === "string") && validTransform(value) &&
  (value.style === undefined || validScopedStyle(value.style)) && (value.keyframes === undefined || validScopedMotion(value.keyframes)) &&
  (value.coordinate_space === undefined || ["project_pixels", "normalized", "scene_local"].includes(String(value.coordinate_space))) &&
  (value.relations === undefined || (Array.isArray(value.relations) && value.relations.length <= 128 && value.relations.every(relation =>
    record(relation) && typeof relation.id === "string" && typeof relation.target_id === "string" &&
    ["align_left", "align_center_x", "align_right", "align_top", "align_center_y", "align_bottom", "follow", "attach"].includes(String(relation.kind)))));
const validEditor = (value: unknown): value is DraftEditor => record(value) && typeof value.nodeId === "string" &&
  (draftKinds as readonly string[]).includes(String(value.kind)) && typeof value.text === "string" && validTransform(value.transform) &&
  (value.transformRaw === undefined || validTransformRaw(value.transformRaw)) &&
  (value.styleRaw === undefined || validScopedStyleRaw(value.styleRaw)) &&
  (value.motionRaw === undefined || validScopedMotionRows(value.motionRaw)) &&
  (value.motionRangeRaw === undefined || validMotionRangeInput(value.motionRangeRaw)) &&
  (value.source === null || (validSource(value.source) && value.source.id === value.nodeId));
const cleanEditor = (editor: DraftEditor): DraftEditor => ({ nodeId: editor.nodeId, kind: editor.kind,
  text: editor.text, transform: nodeTransform(editor.transform), source: editor.source ? sourceNode(editor.source) : null,
  ...(editor.transformRaw ? { transformRaw: Object.fromEntries(transformFields.map(field => [field, editor.transformRaw![field]])) as Record<keyof CanvasTransform, string> } : {}),
  ...(editor.styleRaw ? { styleRaw: Object.fromEntries(CANVAS_STYLE_FIELDS.map(field => [field, editor.styleRaw![field]])) as ScopedStyleRaw } : {}),
  ...(editor.motionRangeRaw ? { motionRangeRaw: {mode:editor.motionRangeRaw.mode,start:editor.motionRangeRaw.start,end:editor.motionRangeRaw.end} } : {}),
  ...(editor.motionRaw ? { motionRaw: editor.motionRaw.map(row => ({ at: row.at, property: row.property, value: row.value, interpolation: row.interpolation })) } : {}) });
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
  if (!record(value) || !["motionwright.scoped-change-draft/1", "motionwright.scoped-change-draft/2", "motionwright.scoped-change-draft/3", "motionwright.scoped-change-draft/4", "motionwright.scoped-change-draft/5"].includes(String(value.schema)) || value.scope !== scope ||
      !Number.isSafeInteger(value.baseRevision) || Number(value.baseRevision) < 0 || typeof value.rationale !== "string" ||
      !Array.isArray(value.edits) || value.edits.length > 128 || !value.edits.every(edit => record(edit) && typeof edit.node_id === "string" &&
        (edit.kind === "text" ? typeof edit.text === "string" : edit.kind === "transform" ? validTransform(edit.transform)
          : edit.kind === "style" ? validScopedStyle(edit.style) : edit.kind === "motion" && validScopedMotion(edit.keyframes))) ||
      !Array.isArray(value.origins) || value.origins.length > 128 || !value.origins.every(validSource) ||
      !validEditor(value.editor) || !Array.isArray(value.parkedEditors) || value.parkedEditors.length > 128 || !value.parkedEditors.every(validEditor) ||
      !validNativeSource(value.nativeSource) || (value.arrangement !== undefined && !validArrangementSelection(value.arrangement))
      || (value.recipeSource !== undefined && (!record(value.recipeSource) ||
        Object.keys(value.recipeSource).length !== 2 ||
        typeof value.recipeSource.kind !== "string" || !["focus_transfer","measured_comparison"].includes(value.recipeSource.kind) ||
        value.recipeSource.revision !== value.baseRevision || value.nativeSource !== null))) {
    throw new Error("Saved proposal has an unsupported shape or belongs to another editing scope.");
  }
  // Project validation and permission checks belong to the service. Only restore
  // the typed editor fields; unknown fields cannot become a command or grant.
  return {
    schema: "motionwright.scoped-change-draft/5", scope, baseRevision: value.baseRevision as number,
    rationale: value.rationale, edits: (value.edits as DraftEdit[]).map(edit => edit.kind === "text"
      ? { kind: "text", node_id: edit.node_id, text: edit.text }
      : edit.kind === "transform" ? { kind: "transform", node_id: edit.node_id, transform: nodeTransform(edit.transform) }
        : edit.kind === "style" ? { kind: "style", node_id: edit.node_id, style: cleanScopedStyle(edit.style) }
          : { kind: "motion", node_id: edit.node_id, keyframes: cleanScopedMotion(edit.keyframes) }),
    origins: (value.origins as SourceNode[]).map(sourceNode),
    editor: cleanEditor(value.editor), parkedEditors: value.parkedEditors.map(cleanEditor),
    arrangement: value.arrangement ? { action: value.arrangement.action, nodeIds: [...value.arrangement.nodeIds] } : emptyArrangementSelection(),
    ...(value.recipeSource ? {recipeSource:{kind:(value.recipeSource as NonNullable<ScopedChangeDraft["recipeSource"]>).kind,
      revision:value.baseRevision as number}} : {}),
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
