import { Link2, Trash2 } from "lucide-react";
import { useMemo } from "react";
import { CANVAS_STYLE_FIELDS, CANVAS_HIERARCHY_FIELDS, CANVAS_TRANSFORM_FIELDS, CANVAS_FIELD_LABELS,
  canvasDraftHasWork, canvasDraftReparentProposal, canvasFieldHasInput, canvasFieldIssue } from "./canvasWorkspaceDraft";
import type { CanvasDraftField, CanvasWorkspaceDraft } from "./canvasWorkspaceDraft";
import type {
  CanvasNode,
  Project,
  ReparentCoordinatePolicy,
  Scene,
} from "./types";

const nullableText = (value: string): string | null => {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
};

const finiteOr = (value: string, fallback: number): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

export default function CanvasStructureEditor({
  project,
  scene,
  node,
  remove, removalErrorId, draft, edit, saveStyle, styleIssue, saveHierarchy, hierarchyIssue,
  relation, relationIssue, pending, editReparentPolicy,
}: {
  project: Project;
  scene: Scene;
  node: CanvasNode;
  remove: () => void;
  removalErrorId?: string;
  draft: CanvasWorkspaceDraft;
  edit: (field: CanvasDraftField, raw: string) => void;
  editReparentPolicy: (preserve: ReparentCoordinatePolicy | undefined) => void;
  saveStyle: () => void;
  styleIssue: string | null;
  saveHierarchy: () => void;
  hierarchyIssue: string | null;
  relation: (operation: "add" | "remove", relationId?: string) => void;
  relationIssue: string | null;
  pending: boolean;
}) {
  const otherNodes = useMemo(
    () => scene.nodes.filter((candidate) => candidate.id !== node.id),
    [node.id, scene.nodes],
  );
  const styleDirty = CANVAS_STYLE_FIELDS.some(field => canvasFieldHasInput(draft.fields[field]));
  const hierarchyDirty = draft.reparent !== undefined || CANVAS_HIERARCHY_FIELDS.some(field => canvasFieldHasInput(draft.fields[field]));
  const styleLocked = node.property_locks.includes("style");
  const parentLocked = node.property_locks.includes("parent");
  const orderLocked = node.property_locks.includes("order");
  const positionLocked = node.property_locks.includes("position");
  const proposed = useMemo(() => {
    if (!hierarchyDirty || (draft.awaiting?.group === "hierarchy" && !draft.reparent)) return null;
    try { return { pose: canvasDraftReparentProposal(draft, project).pose, error: null }; }
    catch (reason) { return { pose: null, error: reason instanceof Error ? reason.message : String(reason) }; }
  }, [draft, project, hierarchyDirty]);

  return (
    <div className="canvas-structure-editor">
      <div className="canvas-editor-section">
        <span className="field-label">Hierarchy</span>
        <p className="semantic-footnote">Parent, Z order and the preservation choice remain recoverable. Commit hierarchy writes one change.</p>
        <label>
          <span>Parent</span>
          <select aria-label="Canvas parent" value={draft.fields.parent_id.raw}
            disabled={pending || parentLocked || orderLocked}
            aria-invalid={Boolean(canvasFieldIssue("parent_id", draft.fields.parent_id.raw))}
            onChange={event => edit("parent_id", event.target.value)}>
            <option value="">Scene root</option>
            {draft.fields.parent_id.raw && !otherNodes.some(item => item.id === draft.fields.parent_id.raw)
              && <option value={draft.fields.parent_id.raw}>Missing parent · review</option>}
            {otherNodes.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}
          </select>
        </label>
        <label>
          <span>Z order</span>
          <input aria-label="Canvas z order" type="text" inputMode="numeric" role="spinbutton"
            aria-valuemin={-2147483648} aria-valuemax={2147483647}
            aria-valuenow={canvasFieldIssue("z_index",draft.fields.z_index.raw) ? undefined : Number(draft.fields.z_index.raw)}
            aria-invalid={Boolean(canvasFieldIssue("z_index",draft.fields.z_index.raw))}
            value={draft.fields.z_index.raw} disabled={pending || parentLocked || orderLocked}
            onChange={event => edit("z_index",event.target.value)}/>
          {canvasFieldIssue("z_index",draft.fields.z_index.raw) && <span className="production-error" role="status">
            {canvasFieldIssue("z_index",draft.fields.z_index.raw)}</span>}
        </label>
        <label>
          <span>Preserve when reparenting</span>
          <select aria-label="Canvas reparent preservation" value={draft.reparent?.preserve ?? ""}
            disabled={pending || parentLocked || orderLocked}
            onChange={event => editReparentPolicy(event.target.value === "" ? undefined : event.target.value as ReparentCoordinatePolicy)}>
            <option value="">World (unchanged default)</option>
            <option value="world">World - keep project geometry</option>
            <option value="local">Local - keep parent-relative pose</option>
          </select>
        </label>
        <p className="semantic-footnote">World keeps the saved pose. Local maps it between the two direct parent frames;
          size and opacity stay unchanged. Local is unavailable for geometric motion or uninterpreted coordinate spaces.</p>
        {proposed?.pose && <section className="semantic-footnote" role="region" aria-label="Proposed Canvas reparent pose" style={{ overflowWrap: "anywhere" }}>
          <strong>Proposed saved pose</strong>
          <p>{CANVAS_TRANSFORM_FIELDS.map(field => CANVAS_FIELD_LABELS[field] + " " + String(proposed.pose![field])).join(" | ")}</p>
          <span>The retained child and parent frames are checked again before Commit.</span>
        </section>}
        {proposed?.error && proposed.error !== hierarchyIssue && <p className="semantic-footnote" role="status">{proposed.error}</p>}
        {draft.awaiting?.group === "hierarchy" && !draft.awaiting.reparent && <p className="semantic-footnote" role="status">
          This older retained submission has no recorded frame snapshot. Its original policy remains unspecified.
        </p>}
        <button type="button" className="button full"
          disabled={!hierarchyDirty || pending || parentLocked || orderLocked || hierarchyIssue !== null}
          title={hierarchyIssue ?? undefined} onClick={() => saveHierarchy()}>Commit hierarchy</button>
        {hierarchyDirty && hierarchyIssue && <p className="semantic-footnote" role="status">{hierarchyIssue}</p>}
        <span className="semantic-footnote">
          Coordinate space: <strong>{node.coordinate_space.replaceAll("_", " ")}</strong>
        </span>
      </div>

      <div className="canvas-editor-section">
        <span className="field-label">Object style</span>
        <p className="semantic-footnote">Edits remain recoverable across revisions; only Commit style writes to the project.</p>
        <div className="canvas-style-grid">
          {(["fill", "stroke", "font_family"] as const).filter(field => field !== "font_family" || node.kind === "text").map(field => (
            <label key={field}>
              <span>{field === "font_family" ? "Font family" : field === "fill" ? "Fill" : "Stroke"}</span>
              <input aria-label={"Canvas " + (field === "font_family" ? "font family" : field)}
                value={draft.fields[field].raw} disabled={styleLocked || pending}
                maxLength={512} aria-invalid={Boolean(canvasFieldIssue(field,draft.fields[field].raw))}
                onChange={event => edit(field,event.target.value)}/>
            </label>
          ))}
          <label><span>Stroke width</span>
            <input aria-label="Canvas stroke width" type="text" inputMode="decimal" role="spinbutton"
              aria-valuemin={0} aria-valuemax={1000}
              aria-valuenow={canvasFieldIssue("stroke_width",draft.fields.stroke_width.raw) ? undefined : Number(draft.fields.stroke_width.raw)}
              aria-invalid={Boolean(canvasFieldIssue("stroke_width",draft.fields.stroke_width.raw))}
              value={draft.fields.stroke_width.raw} disabled={styleLocked || pending}
              onChange={event => edit("stroke_width",event.target.value)}/>
          </label>
          <label><span>Blend</span>
            <select aria-label="Canvas blend mode" value={draft.fields.blend_mode.raw}
              disabled={styleLocked || pending} onChange={event => edit("blend_mode",event.target.value)}>
              <option value="normal">Normal</option><option value="multiply">Multiply</option>
              <option value="screen">Screen</option><option value="add">Add</option>
            </select>
          </label>
        </div>
        {node.kind === "text" && <div className="canvas-style-grid typography-grid">
          {(["font_size","font_weight","line_height"] as const).map(field => {
            const issue = canvasFieldIssue(field,draft.fields[field].raw);
            const shown = draft.fields[field].raw;
            const title = field === "font_size" ? "Font size" : field === "font_weight" ? "Font weight" : "Line height";
            return <label key={field}><span>{title}</span>
              <input aria-label={"Canvas " + title.toLowerCase()} type="text" inputMode="decimal" role="spinbutton"
                aria-valuemin={field === "line_height" ? 0.1 : 1} aria-valuemax={field === "font_size" ? 2048 : field === "font_weight" ? 1000 : 20}
                aria-valuenow={!shown || issue ? undefined : Number(shown)} aria-invalid={Boolean(issue)}
                value={shown} disabled={styleLocked || pending} onChange={event => edit(field,event.target.value)}/>
              {issue && <span className="production-error" role="status">{issue}</span>}
            </label>;
          })}
        </div>}
        <button type="button" className="button full"
          disabled={!styleDirty || styleLocked || pending || styleIssue !== null}
          title={styleIssue ?? undefined} onClick={() => saveStyle()}>Commit style</button>
        {styleDirty && styleIssue && <p className="semantic-footnote" role="status">{styleIssue}</p>}
      </div>

      <div className="canvas-editor-section">
        <span className="field-label">Relations</span>
        <p className="semantic-footnote">Relation choices are local until Add or Remove commits the exact saved list.</p>
        <div className="relation-composer">
          <select aria-label="Canvas relation kind" value={draft.fields.relation_kind.raw}
            disabled={pending || positionLocked || otherNodes.length === 0}
            onChange={event => edit("relation_kind",event.target.value)}>
            <option value="align_left">Align left</option><option value="align_center_x">Align center X</option>
            <option value="align_right">Align right</option><option value="align_top">Align top</option>
            <option value="align_center_y">Align center Y</option><option value="align_bottom">Align bottom</option>
            <option value="follow">Follow</option><option value="attach">Attach</option>
          </select>
          <select aria-label="Canvas relation target" value={draft.fields.relation_target.raw}
            disabled={pending || positionLocked || otherNodes.length === 0}
            onChange={event => edit("relation_target",event.target.value)}>
            <option value="">Target…</option>
            {draft.fields.relation_target.raw && !otherNodes.some(candidate => candidate.id === draft.fields.relation_target.raw)
              && <option value={draft.fields.relation_target.raw}>Missing saved target · review</option>}
            {otherNodes.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}
          </select>
          <button type="button" className="plain-icon" aria-label="Add canvas relation"
            disabled={pending || positionLocked || relationIssue !== null}
            onClick={() => relation("add")}><Link2 size={13}/></button>
        </div>
        {canvasFieldHasInput(draft.fields.relation_target) && relationIssue &&
          <p className="semantic-footnote" role="status">{relationIssue}</p>}
        <div className="relation-list">
          {node.relations.map(item => <div className="relation-row" key={item.id}>
            <span>{item.kind.replaceAll("_"," ")}</span>
            <strong>{scene.nodes.find(candidate => candidate.id === item.target_id)?.name ?? item.target_id.slice(0,8)}</strong>
            <button type="button" className="plain-icon" aria-label={"Remove relation " + item.id}
              disabled={pending || positionLocked || Boolean(draft.awaiting)}
              onClick={() => relation("remove",item.id)}><Trash2 size={12}/></button>
          </div>)}
          {node.relations.length === 0 && <span className="visual-empty">No semantic relations.</span>}
        </div>
      </div>

      <div className="canvas-editor-section destructive-section">
        <button
          type="button"
          className="button destructive"
          aria-describedby={removalErrorId}
          aria-disabled={pending || canvasDraftHasWork(draft)}
          title={canvasDraftHasWork(draft) ? "Commit or preserve the current object draft before removing it." : undefined}
          onClick={() => { if (!pending && !canvasDraftHasWork(draft)) remove(); }}
        >
          <Trash2 size={12} />
          Remove object
        </button>
        <span>Removal is rejected while children or incoming relations still reference this object.</span>
      </div>
    </div>
  );
}
