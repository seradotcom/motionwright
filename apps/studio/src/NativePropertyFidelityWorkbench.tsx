import { useState, type ReactNode } from "react";
import { GitCompareArrows, ShieldAlert } from "lucide-react";
import type { Project } from "./types";
import type { Fidelity } from "./creativeProduction";
import { sameValue } from "./creativeProduction";
import { assertNativeFidelitySource, clearNativePropertyEditor, editNativePropertyReview, nativePropertyEditorHasInput,
  nativePropertyStatuses, removeNativePropertyReview, stageNativePropertyReview, type ProductionFidelityInput } from "./nativePropertyFidelityDraft";

const EXAMPLES = ["camera.focal_length", "camera.center_x", "geometry.mesh", "materials.base_color", "animation.keyframes", "text.content"];

export default function NativePropertyFidelityWorkbench({
  project, capsuleId, select, input, edit, save, busy, blocked, feedback,
}: {
  project: Project; capsuleId: string | null; select: (id: string) => void;
  input: ProductionFidelityInput; edit: (input: ProductionFidelityInput) => void; save: () => void;
  busy: boolean; blocked: boolean; feedback: ReactNode;
}) {
  const capsules = project.production_design?.capsules ?? [], matches = capsules.filter(capsule => capsule.id === capsuleId);
  const selected = matches.length === 1 ? matches[0] : null, saved = selected?.property_fidelity ?? [];
  const [error, setError] = useState<string | null>(null);
  let sourceProblem: string | null = null;
  try { assertNativeFidelitySource(project, selected, input); } catch (reason) { sourceProblem = reason instanceof Error ? reason.message : "The source is unavailable."; }
  const hasChanges = !sameValue(saved, input.entries), editing = nativePropertyEditorHasInput(input);
  const paths = [...new Set([...saved.map(entry => entry.path), ...input.entries.map(entry => entry.path)])];
  const patch = (value: Partial<Omit<ProductionFidelityInput, "kind" | "sourceAssetId" | "sourceSha256" | "entries">>) => {
    setError(null); edit({ ...input, ...value });
  };
  function update(action: (input: ProductionFidelityInput) => ProductionFidelityInput) {
    try { const next = action(input); setError(null); edit(next); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The property review was rejected."); }
  }
  return <section className="native-fidelity-workbench" aria-label="Native per-property fidelity review">
    <div className="production-section-title">
      <GitCompareArrows size={18} aria-hidden="true"/>
      <div><h2>Property-level fidelity negotiation</h2><p>Review translation and loss across the preserved source before saving the complete review.</p></div>
    </div>
    <p className="production-help">These are <strong>operator-authored classifications</strong>, not measured renderer fidelity.
      A property labeled native does not authorize execution or confirm conversion by Blender, HyperFrames or another renderer.</p>
    {!capsules.length && !capsuleId && <p className="production-help">Attach a digest-bound native source above before reviewing individual properties.</p>}
    <label className="production-field"><span>Stored source capsule</span>
      <select aria-label="Native capsule for property negotiation" value={capsuleId ?? ""} disabled={busy} onChange={event => select(event.target.value)}>
        {!capsuleId && <option value="">Choose a preserved source</option>}
        {capsuleId && !selected && <option value={capsuleId}>Earlier source capsule unavailable</option>}
        {capsules.map(capsule => <option key={capsule.id} value={capsule.id}>{capsule.label} · {capsule.scene_id.slice(0, 8)}</option>)}
      </select>
    </label>
    {capsuleId && <>
      <div className="native-fidelity-source"><span>Review source identity</span><code>{input.sourceSha256 || "Unavailable"}</code><span>Project r{project.revision}</span></div>
      {sourceProblem && <p className="production-error" role="alert">{sourceProblem}</p>}
      <div className="native-fidelity-editor">
        <label className="production-field"><span>Semantic property path</span>
          <input aria-label="Native property path" maxLength={128} value={input.path} onChange={event => patch({ path: event.target.value })}
            list="native-property-fidelity-path-examples"/>
          <datalist id="native-property-fidelity-path-examples">{EXAMPLES.map(value => <option key={value} value={value}/>)}</datalist>
          <small>Use a bounded dotted property identity. Incomplete input is retained until you add it to the review.</small>
        </label>
        <label className="production-field"><span>Declared translation outcome</span>
          <select aria-label="Native property fidelity" value={input.fidelity} onChange={event => patch({ fidelity: event.target.value as Fidelity })}>
            {nativePropertyStatuses.map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label className="production-field native-fidelity-note"><span>Loss or translation note</span>
          <textarea aria-label="Native property loss note" maxLength={500} value={input.note} onChange={event => patch({ note: event.target.value })}/>
          <small>Baked, approximate and unavailable properties require a specific loss note.</small>
        </label>
        <div className="production-actions">
          <button type="button" className="secondary-button" disabled={busy || !input.path.trim()}
            onClick={() => update(stageNativePropertyReview)}>{input.editingPath ? "Update property in review" : "Add property to review"}</button>
          <button type="button" className="text-button" disabled={busy || !editing}
            onClick={() => update(clearNativePropertyEditor)}>Clear property fields</button>
        </div>
      </div>
      {error && <p className="production-error" role="alert">{error}</p>}
      <div className="native-fidelity-properties" aria-label="Property classifications before and after">
        {paths.map(path => {
          const before = saved.find(entry => entry.path === path), after = input.entries.find(entry => entry.path === path);
          return <article key={path} className="native-fidelity-property">
            <div><strong className="mono">{path}</strong><span>{!after ? "Removed in draft" : sameValue(before, after) ? "Unchanged" : before ? "Edited in draft" : "Added in draft"}</span></div>
            <div className="production-plan-grid">
              <div><h4>Saved classification</h4><p>{before?.fidelity ?? "Not recorded"}</p>{before?.loss_note && <p>{before.loss_note}</p>}</div>
              <div><h4>Review draft</h4><p>{after?.fidelity ?? "Remove this classification"}</p>{after?.loss_note && <p>{after.loss_note}</p>}</div>
            </div>
            {after && <div className="production-actions">
              <button type="button" className="secondary-button" aria-label={"Edit property review " + path}
                disabled={busy || editing} onClick={() => update(value => editNativePropertyReview(value, path))}>Edit</button>
              <button type="button" className="text-button" aria-label={"Remove property from review " + path}
                disabled={busy || input.editingPath === path} onClick={() => update(value => removeNativePropertyReview(value, path))}>Remove from review</button>
            </div>}
          </article>;
        })}
        {!paths.length && <p className="production-help">No property classifications are recorded or staged for this source.</p>}
      </div>
      {editing && <p className="production-help" role="status">The property fields are retained locally. Add them to the review or clear them before saving the complete review.</p>}
      <button type="button" className="primary-button" disabled={blocked || !!sourceProblem || !hasChanges || editing}
        onClick={save}>Save property review</button>
      <p className="production-help">Save all additions, edits and removals together. Source bytes and the capsule's existing metadata are preserved.</p>
    </>}
    {feedback}
    <div className="native-fidelity-boundary"><ShieldAlert size={16} aria-hidden="true"/>
      <p>Review the original source and rendered result independently. These declarations preserve the distinction between native,
        translated, baked, approximate and unavailable properties; they do not confer parameter edit permissions or renderer approval.</p>
    </div>
  </section>;
}
