import { useLayoutEffect, useRef, useState } from "react";
import { ArchiveRestore, ClipboardCopy, FolderOpen, PackageCheck, RefreshCw } from "lucide-react";
import { acknowledgeRuntimeProfileResult, chooseRuntimeProfile, discardUnsentRuntimePreparation, installRuntimeProfile, readOriginalRuntimeProfile,
  readRuntimeRetention, rollbackRuntimeProfile, runtimeProfileInventory, runtimeUnconfirmed,
  RUNTIME_RETENTION_EVENT, type RuntimeInstallation, type RuntimeInventory, type RuntimePlan, type RuntimeRetention } from "./runtimeProfileApi";
import type { Project } from "./types";
import "./runtime-profiles.css";
const size=(bytes:number)=>bytes<1024?`${bytes} B`:bytes<1024**2?`${(bytes/1024).toFixed(2)} KiB`:`${(bytes/1024**2).toFixed(2)} MiB`;
const stateLabel:Record<RuntimeInstallation["state"],string>={installed:"Installed · original files verified",copy_completed:"Copy completed · read files to verify current bytes",
  outcome_unknown:"Original outcome unconfirmed",partial_copy:"Partial copy retained",files_unverified:"Current files could not be verified",missing_files:"Original files unavailable",rolled_back:"Rolled back · files preserved"};
export default function RuntimeProfilesPanel({project,desktop}:{project:Project;desktop:boolean}){
  const [inventory,setInventory]=useState<RuntimeInventory|null>(null),[plan,setPlan]=useState<RuntimePlan|null>(null);
  const [retained,setRetained]=useState<RuntimeRetention[]>([]),[busy,setBusy]=useState<string|null>(null);
  const [error,setError]=useState<string|null>(null),[note,setNote]=useState<string|null>(null);
  const [retentionReadable,setRetentionReadable]=useState(true);
  const epoch=useRef(0);
  const scope=JSON.stringify([project.id,project.generation,project.revision,project.active_branch,desktop]);
  const refreshRetention=()=>{
    try {
      setRetained(readRuntimeRetention(project));
      setRetentionReadable(true);
    } catch {
      setRetained([]);
      setRetentionReadable(false);
    }
  };
  useLayoutEffect(()=>{
    const current=++epoch.current;
    setPlan(null);setInventory(null);setError(null);setNote(null);setBusy(null);
    refreshRetention();
    if(desktop){
      setBusy("Reading local runtime inventory…");
      void runtimeProfileInventory(project).then(result=>{if(epoch.current===current)setInventory(result);})
        .catch(e=>{if(epoch.current===current)setError(e instanceof Error?e.message:"Runtime inventory is unavailable.");})
        .finally(()=>{if(epoch.current===current)setBusy(null);});
    }
    window.addEventListener(RUNTIME_RETENTION_EVENT,refreshRetention);
    return()=>{epoch.current+=1;window.removeEventListener(RUNTIME_RETENTION_EVENT,refreshRetention);};
    // The entire persisted scope and desktop capability invalidate ownership, including A-B-A.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[scope]);
  const updateOriginal=(result:RuntimeInstallation)=>{
    setInventory(prior=>prior?{...prior,profiles:[...prior.profiles.filter(row=>row.install_id!==result.install_id),result]}:prior);
  };
  const run=async(label:string,action:(owns:()=>void)=>Promise<void>)=>{
    if(busy)return;
    const current=++epoch.current;
    const owns=()=>{if(epoch.current!==current)throw new Error("This runtime action no longer owns the selected project view.");};
    setBusy(label);setError(null);setNote(null);
    try{await action(owns);}catch(e){if(epoch.current===current)setError(e instanceof Error?e.message:"The runtime operation was not confirmed. Read its original result.");}
    finally{if(epoch.current===current){setBusy(null);refreshRetention();}}
  };
  const refresh=()=>run("Reading local runtime inventory…",async owns=>{const result=await runtimeProfileInventory(project);owns();setInventory(result);});
  const choose=()=>run("Inspecting the selected package and its original bytes…",async owns=>{
    setPlan(null);
    const result=await chooseRuntimeProfile(project);owns();setPlan(result);
    if(!result)setNote("Package selection was cancelled. No runtime copy was installed.");
  });
  const install=()=>run("Copying the reviewed runtime package…",async owns=>{
    if(!plan)return;
    const result=await installRuntimeProfile(project,plan,owns);owns();setPlan(null);updateOriginal(result);
    setNote("The local copy was installed and its bytes were verified. Configure its paths through your separately authorized Native provider before rendering.");
  });
  const read=(original:{install_id:string;manifest_sha256:string})=>run("Reading the original runtime files…",async owns=>{
    const result=await readOriginalRuntimeProfile(project,original,"read_only");
    owns();updateOriginal(result);
    if(retentionReadable)acknowledgeRuntimeProfileResult(project,result);
    setNote("The original local operation was read.");
  });
  const rollback=(original:RuntimeInstallation)=>run("Rolling back the original installed copy…",async owns=>{
    const result=await rollbackRuntimeProfile(project,original,owns);owns();updateOriginal(result);setPlan(null);
    setNote("The original copy was moved to its disabled folder. Files and projects were preserved. Stop or reconfigure any already running provider separately.");
  });
  const copy=(value:string)=>run("Copying the selected runtime value…",async owns=>{await navigator.clipboard.writeText(value);owns();setNote("The selected runtime path or lock digest was copied.");});
  const unknown=retained.filter(runtimeUnconfirmed);
  const effectBlocked=!retentionReadable||unknown.length>0;
  return <section className="runtime-profiles" aria-label="Offline runtime profiles">
    <header><PackageCheck size={18} aria-hidden="true"/><div><h3>Offline runtime profiles</h3>
      <p>Review a local package, install one private copy, or roll back its original installation.</p></div></header>
    <p>A package contains an already prepared Motion Canvas or HyperFrames runtime. Inspection shows its actual file size and fixed destination.
      Installing copies those files without executing them. Provider configuration and render permission remain separate.</p>
    <div className="integration-runtime-actions">
      <button type="button" className="button" disabled={!desktop||!!busy||!inventory?.supported||effectBlocked}
        onClick={()=>void choose()}><FolderOpen size={14} aria-hidden="true"/>Choose offline runtime package</button>
      <button type="button" className="button secondary" disabled={!desktop||!!busy} onClick={()=>void refresh()}>
        <RefreshCw size={14} aria-hidden="true"/>Read installed profile inventory</button>
    </div>
    {!desktop&&<p className="runtime-profile-note">Use Motionwright Desktop to inspect local packages. Editing, saving and portable project work remain available here.</p>}
    {inventory?.platform==="macos-aarch64"&&<p className="runtime-profile-note">Use a Motion Canvas package prepared for Apple Silicon. Packages with extended permissions or attributes cannot be copied here; their original security metadata is preserved. HyperFrames offline installation is not available on this Mac.</p>}
    {inventory&&!inventory.supported&&<p className="runtime-profile-note">No offline profile is currently admitted for this operating system and architecture. {inventory.alternatives}</p>}
    {!retentionReadable&&<p role="alert" className="runtime-profile-error">The browser’s runtime history could not be read. You can check saved installations. New installations and rollbacks are paused.</p>}
    {busy&&<p role="status">{busy}</p>}
    {error&&<p role="alert" className="runtime-profile-error">{error}</p>}
    {note&&<p role="status" className="runtime-profile-note">{note}</p>}
    {unknown.map(row=><article className="runtime-profile-original" key={row.kind+row.install_id}>
      <strong>{row.kind==="install"?"Installation":"Rollback"} awaiting its original result</strong>
      <code>{row.install_id}</code>
      <p>{row.state==="prepared"?"The browser retained this preparation before dispatch. It can be discarded only while it remains unsent.":"The request may have completed. Read the original operation before another local effect."}</p>
      <button type="button" className="button secondary" disabled={!!busy||!desktop} onClick={()=>void read(row)}>Read original runtime outcome</button>
      {row.state==="prepared"&&<button type="button" className="button secondary" disabled={!!busy} onClick={()=>{
        try{epoch.current+=1;discardUnsentRuntimePreparation(project,row);setRetained(readRuntimeRetention(project));setPlan(null);setNote("The unsent preparation was discarded. No runtime effect was repeated.");}catch(e){setError(e instanceof Error?e.message:"Preparation could not be discarded.");}
      }}>Discard unsent preparation</button>}
    </article>)}
    {plan&&<article className="runtime-profile-plan" aria-label="Reviewed runtime installation">
      <h4>{plan.label} {plan.version} · {plan.platform}</h4>
      <dl><div><dt>Files to copy</dt><dd>{plan.file_count.toLocaleString()} files · {size(plan.total_bytes)} · {plan.total_bytes.toLocaleString()} bytes</dd></div>
        <div><dt>Space required, including reserve</dt><dd>{size(plan.required_free_bytes)}</dd></div>
        <div><dt>Space available at inspection</dt><dd>{size(plan.available_bytes)}</dd></div>
        <div><dt>Destination</dt><dd><code>{plan.destination}</code></dd></div></dl>
      <p>The reviewed entry and lock match the supported profile. Node {plan.declared_node_version} is declared by the package; this inspection does not execute a version probe.
        The inspection expires after 15 minutes. Rollback preserves this copy in a disabled folder.</p>
      <button type="button" className="button" disabled={!!busy||effectBlocked} onClick={()=>void install()}>Install reviewed local copy</button>
    </article>}
    {!!inventory?.profiles.length&&<div className="runtime-profile-list" aria-label="Original runtime installations">{inventory.profiles.map(item=><article key={item.install_id}>
      <div className="runtime-profile-title"><h4>{item.label} {item.version}</h4><span>{stateLabel[item.state]}</span></div>
      <p>{size(item.total_bytes)} · {item.file_count.toLocaleString()} files · <code>{item.install_id}</code></p>
      <dl><div><dt>{item.state==="rolled_back"?"Preserved files":"Installation destination"}</dt><dd><code>{item.state==="rolled_back"?item.disabled_destination:item.destination}</code></dd></div></dl>
      <div className="integration-runtime-actions"><button type="button" className="button secondary" disabled={!!busy} onClick={()=>void read(item)}>Read original files</button>
        <button type="button" className="button secondary" disabled={!!busy||!item.rollback_available||effectBlocked} onClick={()=>void rollback(item)}><ArchiveRestore size={14} aria-hidden="true"/>Roll back and keep files</button></div>
      {item.state==="installed"&&<details><summary>Paths for the separately authorized provider</summary>
        <dl>{[["Node",item.node_path],["Runtime entry",item.entry_path],["Runtime lock",item.lock_path],["Runtime lock SHA-256",item.lock_sha256]].map(([label,value])=>
          <div key={label}><dt>{label}</dt><dd><code>{value}</code><button type="button" className="button secondary" disabled={!!busy} aria-label={`Copy ${label}`} onClick={()=>void copy(value)}><ClipboardCopy size={12} aria-hidden="true"/></button></dd></div>)}</dl>
        <p>These paths identify verified local files. They do not start a Host, change its policy or supply a session credential.</p>
      </details>}
    </article>)}</div>}
  </section>;
}
