import { Camera, CircleDashed, LockKeyhole, Move, Unlock } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CanvasNode, CanvasTransform, Change, NodeProperty, Project, Scene } from "./types";

type Commit = (change: Change) => Promise<void>;

const PROJECT_WIDTH = 1920;
const PROJECT_HEIGHT = 1080;

function nodeTransform(node: CanvasNode): CanvasTransform {
  return {
    x: node.x,
    y: node.y,
    width: node.width,
    height: node.height,
    rotation_deg: node.rotation_deg,
    opacity: node.opacity,
  };
}

function numberValue(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export default function CanvasWorkspace({
  project,
  scene,
  commit,
}: {
  project: Project;
  scene: Scene | null;
  commit: Commit;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(scene?.nodes[0]?.id ?? null);
  const [draftTransforms, setDraftTransforms] = useState<Record<string, CanvasTransform>>({});
  const [formTransform, setFormTransform] = useState<CanvasTransform | null>(scene?.nodes[0] ? nodeTransform(scene.nodes[0]) : null);
  const [textValue, setTextValue] = useState(scene?.nodes[0]?.text ?? "");
  const drag = useRef<{
    pointerId: number;
    nodeId: string;
    clientX: number;
    clientY: number;
    initial: CanvasTransform;
  } | null>(null);

  useEffect(() => {
    const next = scene?.nodes[0] ?? null;
    setSelectedNodeId(next?.id ?? null);
    setFormTransform(next ? nodeTransform(next) : null);
    setTextValue(next?.text ?? "");
    setDraftTransforms({});
  }, [scene?.id]);

  const selectedNode = useMemo(
    () => scene?.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [scene, selectedNodeId],
  );

  useEffect(() => {
    if (!selectedNode) {
      setFormTransform(null);
      setTextValue("");
      return;
    }
    setFormTransform(nodeTransform(selectedNode));
    setTextValue(selectedNode.text ?? "");
  }, [
    selectedNode?.id,
    selectedNode?.x,
    selectedNode?.y,
    selectedNode?.width,
    selectedNode?.height,
    selectedNode?.rotation_deg,
    selectedNode?.opacity,
    selectedNode?.text,
  ]);

  if (!scene) {
    return (
      <div className="empty-workspace">
        <Move size={24} />
        <strong>Select a scene</strong>
        <span>Canvas edits are scoped to stable scene and object identities.</span>
      </div>
    );
  }

  const scenePositionLocked = project.locks.some(
    (lock) => lock.resource === "scene:" + scene.id && lock.kind === "position",
  );

  const selectNode = (node: CanvasNode) => {
    setSelectedNodeId(node.id);
    setFormTransform(draftTransforms[node.id] ?? nodeTransform(node));
    setTextValue(node.text ?? "");
  };

  const objectTransform = (node: CanvasNode) => draftTransforms[node.id] ?? nodeTransform(node);

  const beginDrag = (event: React.PointerEvent<HTMLButtonElement>, node: CanvasNode) => {
    selectNode(node);
    if (scenePositionLocked || node.property_locks.includes("position")) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      pointerId: event.pointerId,
      nodeId: node.id,
      clientX: event.clientX,
      clientY: event.clientY,
      initial: objectTransform(node),
    };
  };

  const moveDrag = (event: React.PointerEvent<HTMLButtonElement>) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    const stage = event.currentTarget.closest(".canvas-stage") as HTMLElement | null;
    if (!stage) return;
    const width = Math.max(stage.clientWidth, 1);
    const height = Math.max(stage.clientHeight, 1);
    const next = {
      ...active.initial,
      x: active.initial.x + ((event.clientX - active.clientX) / width) * PROJECT_WIDTH,
      y: active.initial.y + ((event.clientY - active.clientY) / height) * PROJECT_HEIGHT,
    };
    setDraftTransforms((current) => ({ ...current, [active.nodeId]: next }));
    if (active.nodeId === selectedNodeId) setFormTransform(next);
  };

  const endDrag = (event: React.PointerEvent<HTMLButtonElement>) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    drag.current = null;
    const next = draftTransforms[active.nodeId];
    if (!next) return;
    void commit({
      type: "transform_canvas_node",
      scene_id: scene.id,
      node_id: active.nodeId,
      transform: next,
    }).finally(() => {
      setDraftTransforms((current) => {
        const copy = { ...current };
        delete copy[active.nodeId];
        return copy;
      });
    });
  };

  const commitTransform = () => {
    if (!selectedNode || !formTransform) return;
    void commit({
      type: "transform_canvas_node",
      scene_id: scene.id,
      node_id: selectedNode.id,
      transform: formTransform,
    });
  };

  const toggleLock = (property: NodeProperty) => {
    if (!selectedNode) return;
    void commit({
      type: "set_node_property_lock",
      scene_id: scene.id,
      node_id: selectedNode.id,
      property,
      locked: !selectedNode.property_locks.includes(property),
    });
  };

  return (
    <div className="canvas-workspace">
      <header className="canvas-toolbar">
        <div>
          <strong>Canvas</strong>
          <span>{scene.name}</span>
        </div>
        <div className="canvas-toolbar-state">
          <span className="status-pill status-unknown">DESIGN REPRESENTATION</span>
          <span className="mono">{scene.nodes.length} objects</span>
        </div>
      </header>

      <div className="canvas-body">
        <aside className="canvas-object-tree" aria-label="Canvas object tree">
          <div className="canvas-panel-heading">Objects</div>
          {scene.nodes.length === 0 ? (
            <div className="canvas-empty-note">This scene has no semantic canvas objects yet.</div>
          ) : (
            <div className="canvas-tree-list">
              {[...scene.nodes].sort((a, b) => b.z_index - a.z_index).map((node) => (
                <button
                  type="button"
                  key={node.id}
                  className={"canvas-tree-row" + (selectedNodeId === node.id ? " selected" : "")}
                  onClick={() => selectNode(node)}
                >
                  <span className="canvas-kind">{node.kind}</span>
                  <span>{node.name}</span>
                  {node.property_locks.length > 0 && <LockKeyhole size={12} aria-label="Object has property locks" />}
                </button>
              ))}
            </div>
          )}
          <div className="canvas-camera-summary">
            <Camera size={14} />
            <div>
              <strong>Camera</strong>
              <span>{scene.camera.zoom.toFixed(2)}× · safe {Math.round(scene.camera.safe_margin * 100)}%</span>
            </div>
          </div>
        </aside>

        <section className="canvas-stage-shell" aria-label="Semantic canvas">
          <div className="canvas-stage-meta">
            <span>{PROJECT_WIDTH} × {PROJECT_HEIGHT}</span>
            <span>Semantic state · no renderer evidence</span>
          </div>
          <div className="canvas-stage">
            <div className="canvas-safe-area" style={{ inset: (scene.camera.safe_margin * 100) + "%" }}>
              <span>SAFE</span>
            </div>
            {scene.nodes.map((node) => {
              const transform = objectTransform(node);
              const positionLocked = scenePositionLocked || node.property_locks.includes("position");
              return (
                <button
                  type="button"
                  key={node.id}
                  aria-label={"Canvas object " + node.name}
                  className={[
                    "canvas-object",
                    node.kind === "text" ? "text-object" : "",
                    selectedNodeId === node.id ? "selected" : "",
                    positionLocked ? "locked" : "",
                  ].join(" ")}
                  style={{
                    left: ((transform.x / PROJECT_WIDTH) * 100) + "%",
                    top: ((transform.y / PROJECT_HEIGHT) * 100) + "%",
                    width: ((transform.width / PROJECT_WIDTH) * 100) + "%",
                    height: ((transform.height / PROJECT_HEIGHT) * 100) + "%",
                    opacity: transform.opacity,
                    transform: "rotate(" + transform.rotation_deg + "deg)",
                    zIndex: node.z_index + 2,
                  }}
                  onPointerDown={(event) => beginDrag(event, node)}
                  onPointerMove={moveDrag}
                  onPointerUp={endDrag}
                  onPointerCancel={() => { drag.current = null; }}
                  onClick={() => selectNode(node)}
                >
                  <span className="canvas-object-label">{node.text || node.name}</span>
                  {positionLocked && <LockKeyhole className="canvas-object-lock" size={12} />}
                </button>
              );
            })}
            {scene.nodes.length === 0 && (
              <div className="canvas-stage-empty">
                <CircleDashed size={25} />
                <strong>No semantic objects</strong>
                <span>The renderer preview is intentionally not fabricated.</span>
              </div>
            )}
          </div>
        </section>

        <aside className="canvas-properties" aria-label="Canvas properties">
          <div className="canvas-panel-heading">Object</div>
          {!selectedNode || !formTransform ? (
            <div className="canvas-empty-note">Select an object to inspect its semantic properties.</div>
          ) : (
            <>
              <div className="canvas-selection-title">
                <div>
                  <strong>{selectedNode.name}</strong>
                  <span>{selectedNode.kind} · z{selectedNode.z_index}</span>
                </div>
                {selectedNode.property_locks.length > 0 && <span className="status-pill status-review">LOCKED FIELDS</span>}
              </div>

              <div className="canvas-field-grid">
                {([
                  ["X", "x"],
                  ["Y", "y"],
                  ["W", "width"],
                  ["H", "height"],
                  ["Rotation", "rotation_deg"],
                  ["Opacity", "opacity"],
                ] as const).map(([label, key]) => (
                  <label key={key}>
                    <span>{label}</span>
                    <input
                      aria-label={"Canvas " + label}
                      type="number"
                      step={key === "opacity" ? "0.05" : "1"}
                      min={key === "opacity" ? "0" : undefined}
                      max={key === "opacity" ? "1" : undefined}
                      value={formTransform[key]}
                      onChange={(event) => setFormTransform({
                        ...formTransform,
                        [key]: numberValue(event.target.value, formTransform[key]),
                      })}
                      disabled={
                        scenePositionLocked ||
                        (["x", "y"].includes(key) && selectedNode.property_locks.includes("position")) ||
                        (["width", "height"].includes(key) && selectedNode.property_locks.includes("size")) ||
                        (key === "rotation_deg" && selectedNode.property_locks.includes("rotation")) ||
                        (key === "opacity" && selectedNode.property_locks.includes("opacity"))
                      }
                    />
                  </label>
                ))}
              </div>
              <button className="button full" type="button" onClick={commitTransform} disabled={scenePositionLocked}>
                Commit transform
              </button>

              <label className="canvas-text-editor">
                <span className="field-label">Text</span>
                <textarea
                  aria-label="Canvas text"
                  rows={4}
                  value={textValue}
                  disabled={selectedNode.property_locks.includes("text")}
                  onChange={(event) => setTextValue(event.target.value)}
                />
              </label>
              <button
                className="button full"
                type="button"
                disabled={selectedNode.property_locks.includes("text") || textValue === (selectedNode.text ?? "")}
                onClick={() => commit({
                  type: "update_canvas_text",
                  scene_id: scene.id,
                  node_id: selectedNode.id,
                  text: textValue || null,
                })}
              >
                Commit text
              </button>

              <div className="property-locks">
                <span className="field-label">Property locks</span>
                {(["position", "size", "rotation", "opacity", "text"] as NodeProperty[]).map((property) => {
                  const locked = selectedNode.property_locks.includes(property);
                  return (
                    <button type="button" className={"lock-toggle" + (locked ? " active" : "")} key={property} onClick={() => toggleLock(property)}>
                      {locked ? <LockKeyhole size={12} /> : <Unlock size={12} />}
                      <span>{property}</span>
                    </button>
                  );
                })}
              </div>

              <div className="camera-controls">
                <span className="field-label">Camera</span>
                <label>
                  <span>Zoom</span>
                  <input
                    aria-label="Camera zoom"
                    type="number"
                    min="0.01"
                    max="100"
                    step="0.05"
                    value={scene.camera.zoom}
                    onChange={(event) => {
                      const zoom = numberValue(event.target.value, scene.camera.zoom);
                      void commit({ type: "set_camera", scene_id: scene.id, camera: { ...scene.camera, zoom } });
                    }}
                  />
                </label>
                <label>
                  <span>Safe area</span>
                  <input
                    aria-label="Camera safe area"
                    type="number"
                    min="0"
                    max="0.49"
                    step="0.01"
                    value={scene.camera.safe_margin}
                    onChange={(event) => {
                      const safe_margin = numberValue(event.target.value, scene.camera.safe_margin);
                      void commit({ type: "set_camera", scene_id: scene.id, camera: { ...scene.camera, safe_margin } });
                    }}
                  />
                </label>
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
