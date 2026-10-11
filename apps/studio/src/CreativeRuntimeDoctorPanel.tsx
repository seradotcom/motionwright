import { useEffect, useMemo, useState } from "react";
import { Activity, ClipboardCopy, ScanSearch, ShieldAlert } from "lucide-react";
import { inspectCreativeRuntime } from "./api";
import type {
  Bootstrap, CreativeRuntimeDoctorReport, ProductionRuntimeCompatibility, Project,
} from "./types";
import "./creative-runtime-doctor.css";

export default function CreativeRuntimeDoctorPanel({
  project, nativeSdk, runtime,
}:{
  project:Project;
  nativeSdk:Bootstrap["native_sdk"];
  runtime:ProductionRuntimeCompatibility|null;
}) {
  const [report,setReport]=useState<CreativeRuntimeDoctorReport|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [note,setNote]=useState<string|null>(null);
  const sourceScope = [
    project.id,project.generation,project.revision,nativeSdk.mode,
    runtime?.status ?? "unknown",runtime?.connection_identity ?? "none",
    runtime?.executable_sha256 ?? "none",
  ].join(":");
  useEffect(()=>{
    setReport(null);setError(null);setNote(null);
  },[sourceScope]);

  const usedRenderers=useMemo(
    ()=>report?.renderers.filter(renderer=>renderer.scene_count>0)??[],
    [report],
  );
  const inspect=async()=>{
    if(busy)return;
    setBusy(true);setError(null);setReport(null);setNote(null);
    try {
      const current=await inspectCreativeRuntime(project);
      if(current.project_id!==project.id ||
         current.generation!==project.generation ||
         current.revision!==project.revision ||
         current.schema!=="motionwright.creative-runtime-doctor/1") {
        throw new Error("Runtime diagnosis is stale or unrecognized. Refresh this project.");
      }
      setReport(current);
    } catch(e){
      setError(e instanceof Error?e.message:"The trusted runtime diagnosis failed.");
    } finally {setBusy(false);}
  };
  const copy=async()=>{
    if(!report||busy)return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(report,null,2));
      setNote("This redacted local preflight report was copied. It does not prove installation or render readiness.");
    } catch {
      setNote("Clipboard permission was denied; the report was not copied.");
    }
  };
  return <section className="creative-doctor" aria-label="Creative runtime doctor">
    <header className="creative-doctor-heading">
      <div><ScanSearch size={18} aria-hidden="true"/><div>
        <h3>Creative runtime doctor</h3>
        <p>Read-only, project-scoped diagnosis · exact Native SDK source pin</p>
      </div></div>
      <span className="creative-doctor-source">SDK {nativeSdk.pinned_revision.slice(0,12)}…</span>
    </header>
    <p className="creative-doctor-intro">
      Check your actual project, the saved Semwright connection and its version-only CLI probe.
      The report distinguishes authored renderer support from an installed, authorized
      Broker/Driver Host. It never launches a render, downloads a package, installs a driver,
      deletes a source file or grants an effect.
    </p>
    <div className="integration-runtime-actions">
      <button type="button" className="button" disabled={busy}
        aria-label={busy?"Checking creative environment":report?"Run diagnosis again":"Run creative runtime doctor"} onClick={()=>void inspect()}>
        <Activity size={14} aria-hidden="true"/>
        {busy?"Checking project and CLI…":report?"Run diagnosis again":"Run creative runtime doctor"}
      </button>
      <button type="button" className="button secondary" disabled={busy||!report}
        onClick={()=>void copy()}><ClipboardCopy size={14} aria-hidden="true"/> Copy sanitized report</button>
    </div>
    {error && <p className="creative-doctor-error" role="alert">{error}</p>}
    {note && <p className="creative-doctor-status" role="status">{note}</p>}
    {!report && !busy && !error && <p className="creative-doctor-status">
      No diagnosis has been run for this project revision. Run the local check explicitly.
      {nativeSdk.mode!=="tauri"?" Browser demo mode cannot inspect real runtime files.":""}
    </p>}
    {report && <>
      <div className="creative-doctor-verdict" role="status">
        <strong>{report.verdict.replaceAll("_"," ").toUpperCase()}</strong>
        <span>{report.runtime_version_detail}</span>
      </div>
      <div className="creative-doctor-counts" aria-label="Creative doctor source metrics">
        <div><span>Project revision</span><strong>{report.revision}</strong></div>
        <div><span>Scenes needing renderer</span><strong>{usedRenderers.reduce((n,item)=>n+item.scene_count,0)}</strong></div>
        <div><span>Active renderer families</span><strong>{usedRenderers.length}</strong></div>
        <div><span>Delivery profiles</span><strong>{report.outputs.length}</strong></div>
      </div>
      <div className="creative-doctor-two">
        <section aria-label="Runtime and creative checks">
          <h4>Native runtime evidence</h4>
          <div className="creative-doctor-rows">
            {report.checks.map(check=><article key={check.id}>
              <div><strong>{check.id.replaceAll("_"," ")}</strong>
                <span>{check.status.replaceAll("_"," ")}</span></div>
              <p>{check.detail}</p>
            </article>)}
          </div>
        </section>
        <section aria-label="Required creative renderer profiles">
          <h4>Renderer requirements in this project</h4>
          <div className="creative-doctor-rows">
            {usedRenderers.length?usedRenderers.map(renderer=><article key={renderer.renderer}>
              <div><strong>{renderer.renderer}</strong><span>{renderer.scene_count} scene(s)</span></div>
              <p>{renderer.state.replaceAll("_"," ")}.
                {renderer.required_semwright_driver?" Declared Semwright route: "+renderer.required_semwright_driver+".":""}
                {" "}No provider capability/readback has been verified by this doctor.</p>
            </article>):<p className="creative-doctor-status">No scenes currently require a renderer.</p>}
          </div>
          <h4>Authoring memory footprint</h4>
          <p className="creative-doctor-note">Numbers are theoretical raw RGBA bytes for one frame.
            They are not an installer download size, render cache size, reserved storage or memory benchmark.</p>
          <div className="creative-doctor-rows">
            {report.outputs.map(out=><article key={out.profile_id}>
              <div><strong>{out.width}×{out.height}</strong>
                <span>{(out.estimated_uncompressed_rgba_frame_bytes/1_000_000).toFixed(2)} MB/frame</span></div>
              <p>No final render or installation footprint has been validated.</p>
            </article>)}
          </div>
        </section>
      </div>
      <div className="creative-doctor-closure">
        <ShieldAlert size={16} aria-hidden="true"/>
        <p>Project {report.project_id.slice(0,8)}… · generation {report.generation.slice(0,8)}…
          . Ready for native production: <strong>No</strong>.
          A source declaration, compatible CLI and this local preflight are not a live Broker handshake,
          installed provider, media acceptance, signed package or human creative approval.
          Installation sizing, destination and rollback are intentionally not performed.</p>
      </div>
    </>}
  </section>;
}
