import { Camera, CircleDashed, LockKeyhole, Move, Plus, Unlock } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import CanvasStructureEditor from "./CanvasStructureEditor";
import type {
  CanvasNode,
  CanvasTransform,
  Change,
  MotionInterpolation,
  MotionProperty,
  NodeProperty,
  Project,
  Scene,
} from "./types";
import { rationalSeconds, seconds } from "./types";
import VisualLanguageEditor from "./VisualLanguageEditor";

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

const MOTION_PROPERTIES: MotionProperty[] = ["x", "y", "width", "height", "rotation_deg", "opacity"];

function easedProgress(progress: number, interpolation: MotionInterpolation) {
  if (interpolation === "hold") return 0;
  if (interpolation === "ease_in_out") return progress * progress * (3 - 2 * progress);
  return progress;
}

function previewTransform(node: CanvasNode, playhead: number): CanvasTransform {
  const output = nodeTransform(node);
  for (const property of MOTION_PROPERTIES) {
    const keys = node.keyframes
      .filter((keyframe) => keyframe.property === property)
      .sort((left, right) => seconds(left.at) - seconds(right.at));
    if (keys.length === 0) continue;
    let fromValue = output[property];
    let fromTime = 0;
    for (const keyframe of keys) {
      const keyTime = seconds(keyframe.at);
      if (playhead < keyTime) {
        const span = keyTime - fromTime;
        if (span <= 0) break;
        const progress = Math.max(0, Math.min(1, (playhead - fromTime) / span));
        output[property] = fromValue + (keyframe.value - fromValue) * easedProgress(progress, keyframe.interpolation);
        break;
      }
      fromValue = keyframe.value;
      fromTime = keyTime;
      output[property] = keyframe.value;
    }
  }
  return output;
}

function propertyLock(property: MotionProperty): NodeProperty {
  if (property === "x" || property === "y") return "position";
  if (property === "width" || property === "height") return "size";
  if (property === "rotation_deg") return "rotation";
  return "opacity";
}

function createNode(scene: Scene, kind: "text" | "shape" | "group"): CanvasNode {
  const count = scene.nodes.filter((node) => node.kind === kind).length + 1;
  const zIndex = scene.nodes.reduce((highest, node) => Math.max(highest, node.z_index), 0) + 1;
  const dimensions = kind === "text"
    ? { width: 520, height: 120 }
    : kind === "group"
      ? { width: 640, height: 360 }
      : { width: 360, height: 220 };
  return {
    id: crypto.randomUUID(),
    name: kind[0].toUpperCase() + kind.slice(1) + " " + count,
    kind,
    parent_id: null,
    x: 240 + (count % 4) * 34,
    y: 220 + (count % 4) * 34,
    width: dimensions.width,
    height: dimensions.height,
    rotation_deg: 0,
    opacity: 1,
    text: kind === "text" ? "Text" : null,
    coordinate_space: "project_pixels",
    z_index: zIndex,
    style: {
      fill: kind === "text" || kind === "group" ? null : "#1D242C",
      stroke: kind === "text" ? null : "#5A6570",
      stroke_width: kind === "text" ? 0 : 1,
      font_family: kind === "text" ? "Instrument Sans Variable" : null,
      font_size: kind === "text" ? 64 : null,
      font_weight: kind === "text" ? 700 : null,
      line_height: kind === "text" ? 1.05 : null,
      blend_mode: "normal",
    },
    relations: [],
    property_locks: [],
    keyframes: [],
  };
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
  const [playhead, setPlayhead] = useState(0);
  const [motionProperty, setMotionProperty] = useState<MotionProperty>("opacity");
  const [motionValue, setMotionValue] = useState(scene?.nodes[0]?.opacity ?? 1);
  const [motionInterpolation, setMotionInterpolation] = useState<MotionInterpolation>("linear");
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
    setPlayhead(0);
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

  useEffect(() => {
    if (selectedNode) setMotionValue(nodeTransform(selectedNode)[motionProperty]);
  }, [selectedNode, motionProperty]);

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
  const sceneContentLocked = project.locks.some(
    (lock) => lock.resource === "scene:" + scene.id && lock.kind === "content",
  );
  const sceneDurationSeconds = seconds(scene.duration);
  const maxPlayhead = Math.max(0, sceneDurationSeconds - 0.001);

  const addNode = async (kind: "text" | "shape" | "group") => {
    const node = createNode(scene, kind);
    await commit({ type: "add_canvas_node", scene_id: scene.id, node });
    setSelectedNodeId(node.id);
    setFormTransform(nodeTransform(node));
    setTextValue(node.text ?? "");
  };

  const selectNode = (node: CanvasNode) => {
    setSelectedNodeId(node.id);
    setFormTransform(draftTransforms[node.id] ?? nodeTransform(node));
    setTextValue(node.text ?? "");
  };

  const objectTransform = (node: CanvasNode) => draftTransforms[node.id] ?? nodeTransform(node);
  const stageTransform = (node: CanvasNode) => draftTransforms[node.id] ?? previewTransform(node, playhead);

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

  const commitKeyframe = () => {
    if (!selectedNode) return;
    const at = Math.max(0, Math.min(playhead, maxPlayhead));
    void commit({
      type: "set_canvas_keyframe",
      scene_id: scene.id,
      node_id: selectedNode.id,
      keyframe: {
        at: rationalSeconds(at),
        property: motionProperty,
        value: motionValue,
        interpolation: motionInterpolation,
      },
    });
  };

  const removeKeyframe = (at: ReturnType<typeof rationalSeconds>, property: MotionProperty) => {
    if (!selectedNode) return;
    void commit({
      type: "remove_canvas_keyframe",
      scene_id: scene.id,
      node_id: selectedNode.id,
      at,
      property,
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
          <label className="motion-playhead">
            <span className="mono">{playhead.toFixed(2)}s / {sceneDurationSeconds.toFixed(2)}s</span>
            <input
              aria-label="Motion playhead"
              type="range"
              min="0"
              max={Math.max(maxPlayhead, 0)}
              step="0.001"
              value={Math.min(playhead, maxPlayhead)}
              onChange={(event) => setPlayhead(numberValue(event.target.value, 0))}
            />
          </label>
          <span className="status-pill status-unknown">EDITORIAL PREVIEW</span>
          <span className="mono">{scene.nodes.length} objects</span>
        </div>
      </header>

      <div className="canvas-body">
        <aside className="canvas-object-tree" aria-label="Canvas object tree">
          <div className="canvas-panel-heading">Objects</div>
          <div className="canvas-create-row" aria-label="Create canvas object">
            {(["text", "shape", "group"] as const).map((kind) => (
              <button
                type="button"
                className="canvas-create-button"
                key={kind}
                disabled={sceneContentLocked || scenePositionLocked}
                onClick={() => void addNode(kind)}
              >
                <Plus size={10} />
                {kind}
              </button>
            ))}
          </div>
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
              const transform = stageTransform(node);
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

              <section className="motion-editor" aria-label="Typed motion keyframes">
                <div className="motion-editor-heading">
                  <div>
                    <span className="field-label">Motion</span>
                    <strong>{selectedNode.keyframes.length} keyframe{selectedNode.keyframes.length === 1 ? "" : "s"}</strong>
                  </div>
                  <span className="mono">{playhead.toFixed(3)}s</span>
                </div>
                <div className="canvas-field-grid motion-fields">
                  <label>
                    <span>Property</span>
                    <select
                      aria-label="Motion property"
                      value={motionProperty}
                      onChange={(event) => setMotionProperty(event.target.value as MotionProperty)}
                    >
                      {MOTION_PROPERTIES.map((property) => <option value={property} key={property}>{property.replace("_deg", "")}</option>)}
                    </select>
                  </label>
                  <label>
                    <span>Value</span>
                    <input
                      aria-label="Motion keyframe value"
                      type="number"
                      step={motionProperty === "opacity" ? "0.05" : "1"}
                      min={motionProperty === "opacity" || motionProperty === "width" || motionProperty === "height" ? "0" : undefined}
                      max={motionProperty === "opacity" ? "1" : undefined}
                      value={motionValue}
                      onChange={(event) => setMotionValue(numberValue(event.target.value, motionValue))}
                    />
                  </label>
                  <label>
                    <span>Interpolation</span>
                    <select
                      aria-label="Motion interpolation"
                      value={motionInterpolation}
                      onChange={(event) => setMotionInterpolation(event.target.value as MotionInterpolation)}
                    >
                      <option value="linear">Linear</option>
                      <option value="ease_in_out">Ease in/out</option>
                      <option value="hold">Hold</option>
                    </select>
                  </label>
                </div>
                <button
                  className="button full"
                  type="button"
                  disabled={scenePositionLocked || selectedNode.property_locks.includes(propertyLock(motionProperty))}
                  onClick={commitKeyframe}
                >
                  Add or replace keyframe at playhead
                </button>
                <div className="motion-keyframe-list">
                  {selectedNode.keyframes.length === 0 ? (
                    <span className="canvas-empty-note">No authored motion on this object.</span>
                  ) : [...selectedNode.keyframes]
                    .sort((left, right) => seconds(left.at) - seconds(right.at) || left.property.localeCompare(right.property))
                    .map((keyframe) => (
                      <div className="motion-keyframe-row" key={keyframe.at.num + "/" + keyframe.at.den + ":" + keyframe.property}>
                        <button
                          type="button"
                          className="motion-keyframe-jump"
                          onClick={() => setPlayhead(Math.min(seconds(keyframe.at), maxPlayhead))}
                          title="Move playhead to keyframe"
                        >
                          <span className="mono">{seconds(keyframe.at).toFixed(3)}s</span>
                          <strong>{keyframe.property.replace("_deg", "")}</strong>
                          <span>{keyframe.value} · {keyframe.interpolation.replaceAll("_", " ")}</span>
                        </button>
                        <button
                          type="button"
                          className="icon-button"
                          aria-label={"Remove " + keyframe.property + " keyframe at " + seconds(keyframe.at).toFixed(3) + " seconds"}
                          disabled={scenePositionLocked || selectedNode.property_locks.includes(propertyLock(keyframe.property))}
                          onClick={() => removeKeyframe(keyframe.at, keyframe.property)}
                        >
                          ×
                        </button>
                      </div>
                    ))}
                </div>
                <p className="inspector-note">
                  Preview interpolation is editorial only. Native Film export rejects motion it cannot preserve exactly.
                </p>
              </section>

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
                {(["position", "size", "rotation", "opacity", "text", "style", "parent", "order"] as NodeProperty[]).map((property) => {
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

              <CanvasStructureEditor scene={scene} node={selectedNode} commit={commit} />
            </>
          )}
          <VisualLanguageEditor project={project} commit={commit} />
        </aside>
      </div>
    </div>
  );
}
