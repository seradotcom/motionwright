import { Link2, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type {
  CanvasNode,
  Change,
  NodeStyle,
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
  commit,
}: {
  scene: Scene;
  node: CanvasNode;
  commit: Commit;
}) {
  const [style, setStyle] = useState<NodeStyle>(() => structuredClone(node.style));
  const [parentId, setParentId] = useState(node.parent_id ?? "");
  const [zIndex, setZIndex] = useState(node.z_index);
  const [relationKind, setRelationKind] = useState<RelationKind>("align_left");
  const [relationTarget, setRelationTarget] = useState("");

  useEffect(() => {
    setStyle(structuredClone(node.style));
    setParentId(node.parent_id ?? "");
    setZIndex(node.z_index);
    setRelationTarget("");
  }, [node.id, node.parent_id, node.z_index, node.style]);

  const otherNodes = useMemo(
    () => scene.nodes.filter((candidate) => candidate.id !== node.id),
    [node.id, scene.nodes],
  );
  const styleDirty = JSON.stringify(style) !== JSON.stringify(node.style);
  const hierarchyDirty =
    (parentId || null) !== node.parent_id || zIndex !== node.z_index;
  const styleLocked = node.property_locks.includes("style");
  const parentLocked = node.property_locks.includes("parent");
  const orderLocked = node.property_locks.includes("order");
  const positionLocked = node.property_locks.includes("position");

  const commitStyle = () =>
    commit({
      type: "update_canvas_style",
      scene_id: scene.id,
      node_id: node.id,
      style: {
        ...style,
        fill: nullableText(style.fill ?? ""),
        stroke: nullableText(style.stroke ?? ""),
        font_family: nullableText(style.font_family ?? ""),
      },
    });

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
        <div className="canvas-style-grid">
          <label>
            <span>Fill</span>
            <input
              aria-label="Canvas fill"
              value={style.fill ?? ""}
              disabled={styleLocked}
              placeholder="none"
              maxLength={512}
              onChange={(event) => setStyle({ ...style, fill: event.target.value })}
            />
          </label>
          <label>
            <span>Stroke</span>
            <input
              aria-label="Canvas stroke"
              value={style.stroke ?? ""}
              disabled={styleLocked}
              placeholder="none"
              maxLength={512}
              onChange={(event) => setStyle({ ...style, stroke: event.target.value })}
            />
          </label>
          <label>
            <span>Stroke width</span>
            <input
              aria-label="Canvas stroke width"
              type="number"
              min="0"
              max="1000"
              step="0.5"
              value={style.stroke_width}
              disabled={styleLocked}
              onChange={(event) =>
                setStyle({
                  ...style,
                  stroke_width: finiteOr(event.target.value, style.stroke_width),
                })
              }
            />
          </label>
          <label>
            <span>Blend</span>
            <select
              aria-label="Canvas blend mode"
              value={style.blend_mode}
              disabled={styleLocked}
              onChange={(event) =>
                setStyle({
                  ...style,
                  blend_mode: event.target.value as NodeStyle["blend_mode"],
                })
              }
            >
              <option value="normal">Normal</option>
              <option value="multiply">Multiply</option>
              <option value="screen">Screen</option>
              <option value="add">Add</option>
            </select>
          </label>
        </div>

        {node.kind === "text" && (
          <div className="canvas-style-grid typography-grid">
            <label>
              <span>Font family</span>
              <input
                aria-label="Canvas font family"
                value={style.font_family ?? ""}
                disabled={styleLocked}
                maxLength={512}
                onChange={(event) =>
                  setStyle({ ...style, font_family: event.target.value })
                }
              />
            </label>
            <label>
              <span>Size</span>
              <input
                aria-label="Canvas font size"
                type="number"
                min="1"
                max="2048"
                value={style.font_size ?? 16}
                disabled={styleLocked}
                onChange={(event) =>
                  setStyle({
                    ...style,
                    font_size: finiteOr(event.target.value, style.font_size ?? 16),
                  })
                }
              />
            </label>
            <label>
              <span>Weight</span>
              <input
                aria-label="Canvas font weight"
                type="number"
                min="1"
                max="1000"
                value={style.font_weight ?? 400}
                disabled={styleLocked}
                onChange={(event) =>
                  setStyle({
                    ...style,
                    font_weight: Math.trunc(
                      finiteOr(event.target.value, style.font_weight ?? 400),
                    ),
                  })
                }
              />
            </label>
            <label>
              <span>Line height</span>
              <input
                aria-label="Canvas line height"
                type="number"
                min="0.1"
                max="20"
                step="0.05"
                value={style.line_height ?? 1}
                disabled={styleLocked}
                onChange={(event) =>
                  setStyle({
                    ...style,
                    line_height: finiteOr(event.target.value, style.line_height ?? 1),
                  })
                }
              />
            </label>
          </div>
        )}
        <button
          type="button"
          className="button full"
          disabled={!styleDirty || styleLocked}
          onClick={() => void commitStyle()}
        >
          Commit style
        </button>
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
