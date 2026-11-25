import { useEffect, useRef, useState } from "react";
import { Download, FileBox, RefreshCw, Save } from "lucide-react";
import type { Change, DeliverableProfile, Project, Scene, ProjectEvent } from "./types";
import { bootstrap, projectHistory } from "./api";
import { sameValue } from "./creativeProduction";
import { MANIM_EDITABLE_MIME, acknowledgeManimPending, confirmManimEdit, exportManimSource,
  inspectAttachedManim, inspectManimSource, manimAsset, manimExportSubject, manimScope,
  matchingManimHistory, manimControlRows, pendingScopeKey, previewCurrent, previewManimEdit, proposedNode,
  readManimPending, retainManimPending, type ManimAttached, type ManimEditRequest,
  type ManimPending, type ManimPreview, type ManimProperty, type ManimScope, type ManimSnapshot,
} from "./manimEditableSource";

type Props = {project: Project; scene: Scene; profile: DeliverableProfile; busy: boolean;
  commit: (change: Change) => Promise<Project>; refreshProject: () => Promise<Project>};
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
type Recovery = {raw: string | null; value: ManimPending | null; error: string | null};
function recovery(key: string): Recovery {
  try {
    const raw = window.localStorage.getItem(key);
    return {raw,value:raw===null?null:readManimPending(raw,key),error:null};
  } catch (reason) { return {raw:null,value:null,error:message(reason)}; }
}
function sameProject(a: Project, b: Project) {
  return a.id===b.id && a.generation===b.generation && a.active_branch===b.active_branch && a.revision===b.revision;
}

export default function ManimEditableSourceWorkbench({project,scene,profile,busy,commit,refreshProject}: Props) {
  const source = manimScope(project,scene.id,profile.id);
  const key = pendingScopeKey(source);
  const [retained,setRetained] = useState<Recovery>(()=>recovery(key));
  const [snapshot,setSnapshot] = useState<ManimSnapshot|null>(null);
  const [destination,setDestination] = useState("");
  const [originalId,setOriginalId] = useState("");
  const [editedId,setEditedId] = useState("");
  const [nodeId,setNodeId] = useState("");
  const [property,setProperty] = useState<ManimProperty>("transform");
  const [attached,setAttached] = useState<ManimAttached|null>(null);
  const [preview,setPreview] = useState<ManimPreview|null>(null);
  const [reading,setReading] = useState(false);
  const [error,setError] = useState<string|null>(null);
  const [notice,setNotice] = useState<string|null>(null);
  const [journal,setJournal] = useState<{raw:string; project:Project; matches:number[]}|null>(null);
  const active = useRef(false);
  const current = useRef({project,source});
  current.current = {project,source};
  const installed = "__TAURI_INTERNALS__" in window;
  const blocked = busy || reading || active.current || !installed || !!retained.error || !!retained.value;
  const assets = project.assets.filter(asset=>asset.media_type===MANIM_EDITABLE_MIME && /^[0-9a-f]{64}$/.test(asset.content_sha256 ?? ""));
  const sourceCurrent = snapshot && sameValue(snapshot.source,source) ? snapshot : null;
  const attachedCurrent = attached && sameValue(attached.source,source) && attached.asset_id===originalId &&
    assets.some(asset=>asset.id===originalId && asset.content_sha256===attached.source_sha256) ? attached : null;
  const makeRequest = (): ManimEditRequest => {
    const original=manimAsset(project,originalId), edited=manimAsset(project,editedId);
    if (!attachedCurrent || !attachedCurrent.node_ids.includes(nodeId) || originalId===editedId) throw new Error("Read one original package and choose a separate edited package and node.");
    if (property==="controls" && !attachedCurrent.editable_parameters.includes("one_node.controls"))
      throw new Error("This source inspector does not admit a complete object-controls patch.");
    return {source,original_asset_id:originalId,original_sha256:original.content_sha256!,
      edited_asset_id:editedId,edited_sha256:edited.content_sha256!,node_id:nodeId,property};
  };
  let reviewed: ManimPreview|null = null;
  try { if(preview && sameValue(preview.request,makeRequest()) && previewCurrent(project,preview)) reviewed=preview; } catch { /* A source change invalidates the preview. */ }
  const before = reviewed && scene.nodes.find(node=>node.id===reviewed!.request.node_id);
  const after = reviewed && before ? proposedNode(before,reviewed.preview.change) : null;
  const rows=before&&after?manimControlRows(before,after):[];
  useEffect(()=>{
    const onStorage=(event:StorageEvent)=>{if(event.key===key || event.key===null) setRetained(recovery(key));};
    window.addEventListener("storage",onStorage);
    return ()=>window.removeEventListener("storage",onStorage);
  },[key]);

  const run = async (action:()=>Promise<void>, allowReview=false) => {
    if (active.current || busy || !installed || (!allowReview && (retained.error || retained.value))) return;
    active.current=true; setReading(true);setError(null);setNotice(null);
    try { await action(); }
    catch (reason) {setError(message(reason));}
    finally {active.current=false;setReading(false);setRetained(recovery(key));}
  };
  const checkScope = (expected:ManimScope) => {
    if (!sameValue(current.current.source,expected)) throw new Error("The selected saved source changed during inspection.");
  };
  const inspect = () => void run(async()=>{
    setSnapshot(null); const result=await inspectManimSource(source);checkScope(source);setSnapshot(result);
  });
  const publish = () => void run(async()=>{
    if(!sourceCurrent) return;
    const path=destination.trim();manimExportSubject(sourceCurrent,path);checkScope(source);
    const pending:ManimPending={kind:"export",source,path,bundle_sha256:sourceCurrent.bundle_sha256,size_bytes:sourceCurrent.size_bytes};
    const raw=retainManimPending(window.localStorage,pending);setRetained({raw,value:pending,error:null});
    // Unknown transport outcomes leave the original retained record locked.
    const result=await exportManimSource(sourceCurrent,path);
    acknowledgeManimPending(window.localStorage,pending,raw);
    setNotice("Compiled source exported: "+result.path+". Import the exact original package as a native source asset before reviewing an edited package.");
  });
  const inspectOriginal = () => void run(async()=>{
    setAttached(null);setPreview(null);const asset=manimAsset(project,originalId);
    const result=await inspectAttachedManim({source,asset_id:asset.id,expected_sha256:asset.content_sha256!});
    checkScope(source);setAttached(result);setNodeId(result.node_ids[0]??"");
    setProperty(result.editable_parameters.includes("one_node.controls")?"controls":"transform");
    setNotice("The attached original matches the saved controls. No native code was run.");
  });
  const review = () => void run(async()=>{
    setPreview(null);const request=makeRequest(), result=await previewManimEdit(request);
    checkScope(source);if(!previewCurrent(current.current.project,result)) throw new Error("The edited source changed during preview.");
    setPreview(result);
  });
  const apply = () => void run(async()=>{
    if(!reviewed || !before) return;
    checkScope(reviewed.request.source);
    // Re-read the exact compiler comparison immediately before retaining a normal
    // user-reviewed Change. No imported Python and no second writer are used.
    const fresh=await previewManimEdit(reviewed.request);
    checkScope(reviewed.request.source);
    if (!sameValue(fresh,reviewed) || !previewCurrent(current.current.project,fresh)) throw new Error("The reviewed edit changed; read the proposal again.");
    const pending:ManimPending={kind:"edit",source,result:fresh};
    const raw=retainManimPending(window.localStorage,pending);setRetained({raw,value:pending,error:null});
    const saved=await commit(fresh.preview.change);
    if(!confirmManimEdit(project,fresh,saved)) throw new Error("The original response did not confirm the exact one-node edit. Read the saved project; do not resubmit.");
    acknowledgeManimPending(window.localStorage,pending,raw);
    setAttached(null);setPreview(null);setNotice("The reviewed object controls are confirmed in r"+saved.revision+". Both attached packages are retained.");
  });
  const inspectOutcome = () => void run(async()=>{
    if (!retained.value || retained.value.kind!=="edit" || !retained.raw) return;
    setJournal(null);
    const pending=retained.value, raw=retained.raw;
    const fresh=await refreshProject();
    if (fresh.revision<pending.source.revision || fresh.revision-pending.source.revision>2048)
      throw new Error("The saved journal is outside the bounded review window.");
    let cursor=pending.source.revision; const events:ProjectEvent[]=[];
    while(cursor<fresh.revision) {
      const page=await projectHistory(fresh,cursor,Math.min(100,fresh.revision-cursor));
      if (!page.length || page.length>100) throw new Error("The saved journal is incomplete.");
      for(const event of page) {
        if(event.revision!==cursor+1 || event.revision>fresh.revision) throw new Error("The saved journal is not contiguous.");
        events.push(event);cursor=event.revision;
      }
    }
    const reread=await bootstrap(fresh.id);
    if(!reread.project || reread.project_selection.status!=="selected" || !sameProject(fresh,reread.project)) throw new Error("The saved project changed during inspection.");
    const matches=matchingManimHistory(pending,fresh,events);
    if(window.localStorage.getItem(key)!==raw) throw new Error("The original retained attempt changed during inspection.");
    setJournal({raw,project:fresh,matches});
  },true);
  const archive = () => void run(async()=>{
    if(!retained.value || !retained.raw) return;
    if(retained.value.kind==="edit" && (!journal || journal.raw!==retained.raw || !sameProject(journal.project,current.current.project)))
      throw new Error("Read the current saved journal before archiving this original attempt.");
    // Preserve exact bytes in a distinct bounded archive; never turn inspection
    // into acknowledgement of the original effect or replay the old request.
    const prefix=key+":archive:";
    const archives=Array.from({length:window.localStorage.length},(_,i)=>window.localStorage.key(i)).filter(name=>name?.startsWith(prefix));
    if(archives.length>=16) throw new Error("The bounded Manim review archive is full.");
    const archiveKey=prefix+crypto.randomUUID();
    if(window.localStorage.getItem(key)!==retained.raw) throw new Error("The retained original attempt changed.");
    window.localStorage.setItem(archiveKey,retained.raw);
    if(window.localStorage.getItem(archiveKey)!==retained.raw) throw new Error("The archived attempt could not be retained.");
    acknowledgeManimPending(window.localStorage,retained.value,retained.raw);
    setSnapshot(null);setAttached(null);setPreview(null);setJournal(null);setDestination("");
    setNotice("The original uncertain operation remains archived, not acknowledged. Choose a distinct new intent.");
  },true);
  return <section className="production-shot" aria-label="Manim editable source">
    <div className="production-section-title"><FileBox size={18} aria-hidden="true"/><div>
      <h3>Manim editable source exchange</h3>
      <p>Compile the saved scene into the closed V3 source package, or compare two attached compiler-produced packages. No imported Python is executed.</p>
    </div></div>
    <p className="production-help">Selected output: {profile.name} · saved r{project.revision}. This verifies source bytes and controls, not rendered pixels.</p>
    <button type="button" className="secondary-button" disabled={blocked} onClick={inspect}><RefreshCw size={14} aria-hidden="true"/> Inspect saved Manim source</button>
    {sourceCurrent && <div className="production-notice" aria-label="Compiled Manim source">
      <strong>Closed source package ready</strong>
      <span className="mono" style={{overflowWrap:"anywhere"}}>sha256:{sourceCurrent.bundle_sha256}</span>
      <span>{Number(sourceCurrent.size_bytes).toLocaleString()} bytes · .mwedit.zip</span>
      <label className="production-field"><span>New package destination</span><input aria-label="Manim source export destination" value={destination} onChange={event=>setDestination(event.target.value)} disabled={blocked} maxLength={1600} autoComplete="off" spellCheck={false}/></label>
      <button type="button" className="secondary-button" disabled={blocked || !destination.trim()} onClick={publish}><Download size={14} aria-hidden="true"/> Export inspected Manim package</button>
    </div>}
    <h4>Review a returned editable package</h4>
    <p className="production-help">Import the original and a separate edited .mwedit.zip from Assets. These files appear in the package selectors automatically. Comparing a package verifies its contents against the trusted compiler before any project edit.</p>
    <div className="production-plan-grid">
      <label className="production-field"><span>Original package</span><select disabled={blocked} value={originalId} onChange={event=>{setOriginalId(event.target.value);setAttached(null);setPreview(null);setNodeId("");}}><option value="">Choose original attached package</option>{assets.map(asset=><option key={asset.id} value={asset.id}>{asset.name}</option>)}</select></label>
      <label className="production-field"><span>Edited package</span><select disabled={blocked} value={editedId} onChange={event=>{setEditedId(event.target.value);setPreview(null);}}><option value="">Choose separately attached package</option>{assets.filter(asset=>asset.id!==originalId).map(asset=><option key={asset.id} value={asset.id}>{asset.name}</option>)}</select></label>
    </div>
    <button type="button" className="secondary-button" disabled={blocked || !originalId} onClick={inspectOriginal}>Read original attached Manim package</button>
    {attachedCurrent && <div className="production-notice">
      <span>Original r{attachedCurrent.origin_revision}; current r{project.revision}. {attachedCurrent.node_ids.length} editable nodes.</span>
      <label className="production-field"><span>Original node</span><select disabled={blocked} value={nodeId} onChange={event=>{setNodeId(event.target.value);setPreview(null);}}>{scene.nodes.filter(node=>attachedCurrent.node_ids.includes(node.id)).map(node=><option key={node.id} value={node.id}>{node.name}</option>)}</select></label>
      <label className="production-field"><span>Controls to reintegrate</span><select disabled={blocked} value={property} onChange={event=>{setProperty(event.target.value as ManimProperty);setPreview(null);}}>
        {attachedCurrent.editable_parameters.includes("one_node.controls")&&<option value="controls">All changed controls of this object</option>}
        <option value="transform">Transform only</option><option value="text">Text only</option><option value="style">Style only</option></select></label>
      <button className="secondary-button" type="button" disabled={blocked || !editedId || editedId===originalId || !nodeId} onClick={review}>Compare returned package</button>
    </div>}
    {reviewed && before && after && <section className="production-notice" aria-label="Reviewed Manim source edit">
      <strong>{before.name}: {property==="controls"?"complete object controls":property}</strong>
      <table aria-label="Manim control changes" style={{width:"100%",tableLayout:"fixed"}}>
        <thead><tr><th scope="col">Control</th><th scope="col">Saved</th><th scope="col">Returned source</th></tr></thead>
        <tbody>{rows.map(row=><tr key={row.field}><th scope="row">{row.field}</th>
          <td style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{row.before}</td>
          <td style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{row.after}</td></tr>)}</tbody>
      </table>
      <p className="production-help">The compiler confirmed the complete package differs only in these controls of this object.
        They save together in one revision; both source assets remain. {property==="controls"&&"The saved patch can be reviewed or undone in Revisions."}</p>
      <button type="button" className="primary-button" disabled={blocked} onClick={apply}><Save size={14} aria-hidden="true"/> Save reviewed Manim edit</button>
    </section>}
    {retained.error && <p role="alert" className="production-error">Retained Manim attempt could not be read. No new operation is allowed: {retained.error}</p>}
    {retained.value && <section className="production-notice" aria-label="Unconfirmed Manim source operation">
      <strong>Original {retained.value.kind} result is unconfirmed</strong>
      <span>r{retained.value.source.revision}. Never resubmit this retained operation.</span>
      {retained.value.kind==="export" ? <>
        <span>{retained.value.path}</span><span className="mono" style={{overflowWrap:"anywhere"}}>sha256:{retained.value.bundle_sha256}</span>
        <p className="production-help">Inspect the original destination manually. Archiving does not certify publication and requires a distinct new path.</p>
        <button type="button" className="secondary-button" disabled={busy || reading} onClick={archive}>I inspected the destination; retain uncertain export</button>
      </>:<>
        <button type="button" className="secondary-button" disabled={busy || reading} onClick={inspectOutcome}>Read saved Manim edit history</button>
        {journal && journal.raw===retained.raw && sameProject(journal.project,project) && <><p>Matching change revisions: {journal.matches.join(", ")||"none"}. Matching history is observation, not the original reply.</p>
          <button type="button" className="secondary-button" disabled={busy || reading} onClick={archive}>Retain uncertain edit; review a distinct proposal</button></>}
      </>}
    </section>}
    {reading && <p role="status">Working with the exact saved source…</p>}
    {error && <p role="alert" className="production-error">{error}</p>}
    {notice && <p role="status" className="production-help">{notice}</p>}
    {!installed && <p className="production-help">Source compilation and inspection require the actual desktop runtime.</p>}
  </section>;
}
