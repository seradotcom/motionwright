import { Link2, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { CANVAS_STYLE_FIELDS, canvasFieldHasInput, canvasFieldIssue } from "./canvasWorkspaceDraft";
import type { CanvasDraftField, CanvasWorkspaceDraft } from "./canvasWorkspaceDraft";
import type {
  CanvasNode,
  Change,
  RelationKind,
  Scene,
} from "./types";

type Commit = (change: Change) => Promise<void>;

const nullableText = (value: string): string | null => {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
};

const finiteOr = (value: string, fallback: number): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

export default function CanvasStructureEditor({
  scene,
  node,
  commit, draft, edit, saveStyle, styleIssue, pending,
}: {
  scene: Scene;
  node: CanvasNode;
  commit: Commit;
  draft: CanvasWorkspaceDraft;
  edit: (field: CanvasDraftField, raw: string) => void;
  saveStyle: () => void;
  styleIssue: string | null;
  pending: boolean;
}) {
  const [parentId, setParentId] = useState(node.parent_id ?? "");
  const [zIndex, setZIndex] = useState(node.z_index);
  const [relationKind, setRelationKind] = useState<RelationKind>("align_left");
  const [relationTarget, setRelationTarget] = useState("");

  useEffect(() => {
    setParentId(node.parent_id ?? "");
    setZIndex(node.z_index);
    setRelationTarget("");
  }, [node.id, node.parent_id, node.z_index]);

  const otherNodes = useMemo(
    () => scene.nodes.filter((candidate) => candidate.id !== node.id),
    [node.id, scene.nodes],
  );
  const styleDirty = CANVAS_STYLE_FIELDS.some(field => canvasFieldHasInput(draft.fields[field]));
  const hierarchyDirty =
    (parentId || null) !== node.parent_id || zIndex !== node.z_index;
  const styleLocked = node.property_locks.includes("style");
  const parentLocked = node.property_locks.includes("parent");
  const orderLocked = node.property_locks.includes("order");
  const positionLocked = node.property_locks.includes("position");

  const addRelation = () => {
    if (!relationTarget || positionLocked) return;
    return commit({
      type: "set_canvas_relations",
      scene_id: scene.id,
      node_id: node.id,
      relations: [
        ...node.relations,
        {
          id: crypto.randomUUID(),
          kind: relationKind,
          target_id: relationTarget,
        },
      ],
    });
  };

  return (
    <div className="canvas-structure-editor">
      <div className="canvas-editor-section">
        <span className="field-label">Hierarchy</span>
        <label>
          <span>Parent</span>
          <select
            aria-label="Canvas parent"
            value={parentId}
            disabled={parentLocked}
            onChange={(event) => setParentId(event.target.value)}
          >
            <option value="">Scene root</option>
            {otherNodes.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Z order</span>
          <input
            aria-label="Canvas z order"
            type="number"
            step="1"
            value={zIndex}
            disabled={orderLocked}
            onChange={(event) => setZIndex(Math.trunc(finiteOr(event.target.value, zIndex)))}
          />
        </label>
        <button
          type="button"
          className="button full"
          disabled={!hierarchyDirty || parentLocked || orderLocked}
          onClick={() =>
            commit({
              type: "reparent_canvas_node",
              scene_id: scene.id,
              node_id: node.id,
              parent_id: parentId || null,
              z_index: zIndex,
            })
          }
        >
          Commit hierarchy
        </button>
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
        <div className="relation-composer">
          <select
            aria-label="Canvas relation kind"
            value={relationKind}
            disabled={positionLocked || otherNodes.length === 0}
            onChange={(event) => setRelationKind(event.target.value as RelationKind)}
          >
            <option value="align_left">Align left</option>
            <option value="align_center_x">Align center X</option>
            <option value="align_right">Align right</option>
            <option value="align_top">Align top</option>
            <option value="align_center_y">Align center Y</option>
            <option value="align_bottom">Align bottom</option>
            <option value="follow">Follow</option>
            <option value="attach">Attach</option>
          </select>
          <select
            aria-label="Canvas relation target"
            value={relationTarget}
            disabled={positionLocked || otherNodes.length === 0}
            onChange={(event) => setRelationTarget(event.target.value)}
          >
            <option value="">Target…</option>
            {otherNodes.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="plain-icon"
            aria-label="Add canvas relation"
            disabled={!relationTarget || positionLocked}
            onClick={() => void addRelation()}
          >
            <Link2 size={13} />
          </button>
        </div>
        <div className="relation-list">
          {node.relations.map((relation) => {
            const target = scene.nodes.find(
              (candidate) => candidate.id === relation.target_id,
            );
            return (
              <div className="relation-row" key={relation.id}>
                <span>{relation.kind.replaceAll("_", " ")}</span>
                <strong>{target?.name ?? relation.target_id.slice(0, 8)}</strong>
                <button
                  type="button"
                  className="plain-icon"
                  aria-label={"Remove relation " + relation.id}
                  disabled={positionLocked}
                  onClick={() =>
                    void commit({
                      type: "set_canvas_relations",
                      scene_id: scene.id,
                      node_id: node.id,
                      relations: node.relations.filter(
                        (candidate) => candidate.id !== relation.id,
                      ),
                    })
                  }
                >
                  <Trash2 size={12} />
                </button>
              </div>
            );
          })}
          {node.relations.length === 0 && (
            <span className="visual-empty">No semantic relations.</span>
          )}
        </div>
      </div>

      <div className="canvas-editor-section destructive-section">
        <button
          type="button"
          className="button destructive"
          onClick={() =>
            void commit({
              type: "remove_canvas_node",
              scene_id: scene.id,
              node_id: node.id,
            })
          }
        >
          <Trash2 size={12} />
          Remove object
        </button>
        <span>Removal is rejected while children or incoming relations still reference this object.</span>
      </div>
    </div>
  );
}
