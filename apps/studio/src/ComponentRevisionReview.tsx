import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { HeroConfig } from "./creativeProduction";
import type { CanvasNode, Project } from "./types";
import { componentRevisionCurrent, componentRevisionField, inspectComponentRevision } from "./componentRevisionPreview";
import type { ComponentRevisionPreview } from "./componentRevisionPreview";

export function useComponentRevisionReview(project: Project, sceneId: string | null,
  instanceId: string | null, config: HeroConfig, sequence: number) {
  const [preview, setPreview] = useState<ComponentRevisionPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let current = true;
    setPreview(null); setError(null);
    if (!sceneId || !instanceId) { setLoading(false); return; }
    setLoading(true);
    inspectComponentRevision(project, sceneId, instanceId, config, sequence).then(value => {
      if (current) { setPreview(value); setLoading(false); }
    }, reason => {
      if (current) { setError(reason instanceof Error ? reason.message : String(reason)); setLoading(false); }
    });
    return () => { current = false; };
  }, [project, sceneId, instanceId, config, sequence]);
  const ready = !!sceneId && !!instanceId
    && componentRevisionCurrent(preview, project, sceneId, instanceId, config, sequence);
  return { preview: ready ? preview : null, ready, error, loading };
}

const fieldLabels: Record<string, string> = {
  name: "Object label", kind: "Shape", parent_id: "Parent", coordinate_space: "Coordinate space",
  x: "Horizontal position", y: "Vertical position", width: "Width", height: "Height",
  rotation_deg: "Rotation", opacity: "Opacity", z_index: "Layer order", text: "Copy",
  keyframes: "Motion", property_locks: "Editing locks", relations: "Layout relations",
  "style.fill": "Fill", "style.stroke": "Stroke", "style.stroke_width": "Stroke width",
  "style.font_family": "Font family", "style.font_size": "Font size", "style.font_weight": "Font weight",
  "style.line_height": "Line height", "style.blend_mode": "Blend mode",
};
function Value({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span>None</span>;
  if (typeof value === "string") return <span style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{value === "" ? "(empty)" : value}</span>;
  if (typeof value === "object") return <details><summary>Inspect exact values</summary>
    <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 240, overflow: "auto" }}>{JSON.stringify(value, null, 2)}</pre></details>;
  return <span>{String(value)}</span>;
}

export default function ComponentRevisionReview({ review, renderScene }: {
  review: ReturnType<typeof useComponentRevisionReview>;
  renderScene: (nodes: CanvasNode[], label: string) => ReactNode;
}) {
  const preview = review.preview;
  return <section className="production-evidence-note" aria-label="Component change review">
    <h3>Review the component change</h3>
    <p>{preview ? `Existing component v${preview.beforeInstance.component_version} · proposed component v${preview.afterInstance.component_version}. ` : "Supported component v1 comparison. "}This comparison keeps your saved objects and shows the result of the existing merge in the 1920 × 1080 authoring space.</p>
    {review.error && <p role="alert" className="production-error">{review.error} The saved scene is unchanged. Resolve the conflict in the parameters or Canvas before updating.</p>}
    {!preview && !review.error && <p role="status">Preparing the current comparison…</p>}
    {preview && <>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
        <section aria-label="Saved component scene"><h4>Saved scene</h4>
          {renderScene(preview.before, "Saved scene with authored objects")}</section>
        <section aria-label="Proposed merged component scene"><h4>After this change</h4>
          {renderScene(preview.after, "Proposed merged scene with authored objects retained")}</section>
      </div>
      <p>{preview.rows.length} component objects retain their identities. {preview.unrelatedNodeIds.length} other scene object{preview.unrelatedNodeIds.length === 1 ? " remains" : "s remain"} unchanged.</p>
      {preview.rows.every(row => row.changedFields.length === 0) && <p>No saved object values change in this comparison.</p>}
      {preview.rows.filter(row => row.changedFields.length || row.retainedFields.length).map(row => <details key={row.id} open={row.retainedFields.length > 0}>
        <summary>{row.role} · {row.changedFields.length} changed field{row.changedFields.length === 1 ? "" : "s"}{row.retainedFields.length ? ` · ${row.retainedFields.length} authored field${row.retainedFields.length === 1 ? "" : "s"} kept` : ""}</summary>
        {row.changedFields.length > 0 && <table><thead><tr><th>Property</th><th>Saved</th><th>After this change</th></tr></thead>
          <tbody>{row.changedFields.map(path => <tr key={path}><th>{fieldLabels[path] ?? path}</th>
            <td><Value value={componentRevisionField(row.before, path)}/></td><td><Value value={componentRevisionField(row.after, path)}/></td></tr>)}</tbody></table>}
        {row.retainedFields.length > 0 && <><h4>Kept from your objects</h4><dl>{row.retainedFields.map(path => <div key={path}>
          <dt>{fieldLabels[path] ?? path}</dt><dd><Value value={componentRevisionField(row.after, path)}/></dd></div>)}</dl></>}
      </details>)}
      <p>This is an editorial comparison of saved object data. Updating still uses the existing service transaction and its current locks. Native frames and creative acceptance remain separate checks.</p>
    </>}
  </section>;
}
