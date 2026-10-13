import { useEffect, useState } from "react";
import { GitCompareArrows, ShieldAlert } from "lucide-react";
import type { Change, Project } from "./types";
import type { Fidelity, NativeCapsule, NativePropertyFidelity } from "./creativeProduction";
import { validateNativePropertyFidelity } from "./creativeProduction";

type Commit = (change: Change) => Promise<void>;
const STATUSES: Fidelity[] = ["native", "translated", "baked", "approximate", "unavailable"];
const EXAMPLES = [
  "camera.focal_length", "camera.center_x", "geometry.mesh",
  "materials.base_color", "animation.keyframes", "text.content",
];

export default function NativePropertyFidelityWorkbench({
  project, commit, busy,
}: {project:Project;commit:Commit;busy:boolean}) {
  const capsules = project.production_design?.capsules ?? [];
  const [selectedId,setSelectedId] = useState("");
  const selected = capsules.find(c=>c.id===selectedId) ?? capsules[0] ?? null;
  const [path,setPath] = useState("camera.focal_length");
  const [fidelity,setFidelity] = useState<Fidelity>("unavailable");
  const [note,setNote] = useState("The original value is preserved in the native source; no parameter edit or renderer readback was verified.");
  const [error,setError] = useState<string|null>(null);
  const [result,setResult] = useState<string|null>(null);
  const source = selected && project.assets.find(asset=>asset.id===selected.source_asset_id);
  const sourceMatches = !!selected && source?.content_sha256 === selected.source_sha256;
  const existing = selected?.property_fidelity ?? [];
  const lossy = ["baked", "approximate", "unavailable"].includes(fidelity);
  useEffect(()=>{setError(null);setResult(null);},[selected?.id,project.revision]);

  const persist = async (capsule:NativeCapsule,entries:NativePropertyFidelity[]) => {
    if (!sourceMatches || busy) return;
    try {
      validateNativePropertyFidelity(entries);
      setError(null);setResult(null);
      await commit({
        type:"upsert_native_capsule",
        capsule:{...structuredClone(capsule),property_fidelity:entries},
      });
      setResult("Source-bound property classifications committed. The original asset is unchanged; no native property edit was performed.");
    } catch (e) {
      setError(e instanceof Error?e.message:"Property negotiation was rejected.");
    }
  };
  const save = async () => {
    if (!selected || !sourceMatches) return;
    const name=path.trim();
    const declaration:NativePropertyFidelity={
      path:name,fidelity,loss_note:note.trim() || null,
    };
    const next=existing.filter(entry=>entry.path!==name).map(entry=>structuredClone(entry));
    next.push(declaration);
    await persist(selected,next);
  };
  const remove = async (name:string) => {
    if (!selected) return;
    await persist(selected,existing.filter(entry=>entry.path!==name));
  };
  return <section className="native-fidelity-workbench"
    aria-label="Native per-property fidelity review">
    <div className="production-section-title">
      <GitCompareArrows size={18} aria-hidden="true"/>
      <div>
        <h2>Property-level fidelity negotiation</h2>
        <p>Describe intended translation or loss before editing a preserved native source.</p>
      </div>
    </div>
    <p className="production-help">
      These are <strong>operator-authored classifications</strong>, not measured renderer fidelity.
      Even a property labeled native does not authorize execution or imply that a Blender,
      HyperFrames or other source format has been independently converted.
    </p>
    {capsules.length===0?<p className="production-help">
      Attach a digest-bound native source above before reviewing individual properties.
    </p>:<>
      <label className="production-field">
        <span>Stored source capsule</span>
        <select aria-label="Native capsule for property negotiation"
          value={selected?.id??""} onChange={e=>setSelectedId(e.target.value)}>
          {capsules.map(c=><option key={c.id} value={c.id}>{c.label} · {c.scene_id.slice(0,8)}</option>)}
        </select>
      </label>
      <div className="native-fidelity-source">
        <span>Source identity</span>
        <code>{selected?.source_sha256.slice(0,16)}…</code>
        <span>Project r{project.revision}</span>
      </div>
      {!sourceMatches && <p className="production-error" role="alert">
        The imported asset is unavailable or its SHA-256 no longer matches this capsule. Property classifications are read-only.
      </p>}
      <div className="native-fidelity-editor">
        <label className="production-field">
          <span>Semantic property path</span>
          <input aria-label="Native property path" maxLength={128}
            value={path} onChange={e=>setPath(e.target.value)}
            list="native-property-fidelity-path-examples"/>
          <datalist id="native-property-fidelity-path-examples">
            {EXAMPLES.map(v=><option key={v} value={v}/>)}
          </datalist>
          <small>Use a bounded, dotted property identity, not a file path or executable command.</small>
        </label>
        <label className="production-field">
          <span>Declared translation outcome</span>
          <select aria-label="Native property fidelity" value={fidelity}
            onChange={e=>setFidelity(e.target.value as Fidelity)}>
            {STATUSES.map(status=><option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <label className="production-field native-fidelity-note">
          <span>{lossy?"Required description of the loss or unavailable edit":"Operator notes (optional)"}</span>
          <textarea aria-label="Native property loss explanation" maxLength={500}
            rows={3} value={note} onChange={e=>setNote(e.target.value)}
            placeholder="Describe the exact property behavior that is missing or approximated."/>
        </label>
        <button type="button" className="secondary-button"
          onClick={()=>void save()} disabled={busy || !sourceMatches || (lossy&&!note.trim())}>
          Record property classification
        </button>
      </div>
      <div className="native-fidelity-report" aria-label="Saved property fidelity classifications">
        <div className="native-fidelity-report-head">
          <strong>Declared source properties</strong><span>{existing.length}/64 recorded</span>
        </div>
        {existing.map(entry=><article key={entry.path} className="native-fidelity-property">
          <div>
            <strong className="mono">{entry.path}</strong>
            <span>{entry.fidelity}</span>
            <button type="button" className="secondary-button"
              aria-label={"Remove property classification "+entry.path}
              onClick={()=>void remove(entry.path)} disabled={busy || !sourceMatches}>
              Remove
            </button>
          </div>
          {entry.loss_note && <p>{entry.loss_note}</p>}
        </article>)}
        {existing.length===0 && <p className="production-help">No property-by-property claims have been recorded for this native source.</p>}
      </div>
      {error&&<p className="production-error" role="alert">{error}</p>}
      {result&&<p className="production-help" role="status">{result}</p>}
    </>}
    <div className="native-fidelity-boundary">
      <ShieldAlert size={16} aria-hidden="true"/>
      <p>Source bytes, canvas objects, and existing editable parameter grants remain unchanged.
        This is an audit of intended property translation, not proof that a provider can render,
        preserve or edit a property. Recheck the original source and the rendered result independently.</p>
    </div>
  </section>;
}
