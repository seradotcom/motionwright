import {useEffect,useRef,useState} from 'react';
import {Check,Code2,Eye,FilePlus2,Film,Lock,RefreshCw,Save,Square,X} from 'lucide-react';
import {nativeCreativeAvailable,nativeDocumentState,proposeNativeCanvas,previewNativeEdit,renderNativeHtml,observeNativeHtmlJob,recoverNativeHtmlPreview,probeNativeHtmlRuntime} from '../api';
import {sameValue} from '../creativeProduction';
import NativeFrameStage from '../NativeFrameStage';
import type {NativeFrameSelection} from '../nativeFrameSelection';
import type {Change,Project,Scene,DeliverableProfile} from '../types';
import {seconds} from '../types';
import NativeNodeInspector from './NativeNodeInspector';
import NativeHtmlContactReview from './NativeHtmlContactReview';
import type {CreativeWorkspaceEdit,NativeSceneDocument,NativeSceneDifference,NativeNode,NativeProtection,HyperframesRenderEvidence} from './nativeTypes';
import './native-editor.css';
type Probe=Awaited<ReturnType<typeof probeNativeHtmlRuntime>>;
interface Draft{document:NativeSceneDocument;baseRevision:number;sourceSha:string|null;stored:NativeSceneDocument|null}
const message=(reason:unknown)=>reason instanceof Error?reason.message:String(reason);
export function nativeHtmlFrameSelection(project:Project,scene:Scene|null,profile:DeliverableProfile|null,evidence:HyperframesRenderEvidence|null,playhead:number):NativeFrameSelection|null {
  if(!scene||!profile||!evidence||evidence.project_id!==project.id||evidence.generation!==project.generation||evidence.revision!==project.revision||evidence.scene_id!==scene.id||evidence.profile_id!==profile.id||!Number.isFinite(playhead)||evidence.frame_count<1)return null;
  const grant=evidence.preview.find(grant=>grant.segment_id===evidence.document_id&&grant.scene_ids.length===1&&grant.scene_ids[0]===scene.id&&grant.frame_count===evidence.frame_count);
  if(!grant?.token)return null;
  const frame=Math.min(evidence.frame_count-1,Math.max(0,Math.floor((playhead-seconds(scene.start))*evidence.rate.num/evidence.rate.den+1e-7)));
  return {token:grant.token,segmentId:grant.segment_id,frameIndex:frame,frameCount:evidence.frame_count};
}
export default function NativeCreativeEditor({project,scene,displayProfile,commit,busy,playhead,onSeek}:{project:Project;scene:Scene|null;displayProfile:DeliverableProfile|null;commit:(change:Change)=>Promise<void>;busy:boolean;playhead:number;onSeek:(time:number)=>void}) {
  const available=nativeCreativeAvailable();
  const saved=project.production_design?.workspace?.native_scenes.find(doc=>doc.scene_id===scene?.id&&doc.profile_id===displayProfile?.id);
  const [draft,setDraft]=useState<Draft|null>(null);const latestDraft=useRef(draft);latestDraft.current=draft;
  const [nodeId,setNodeId]=useState(''),[review,setReview]=useState<{difference:NativeSceneDifference;edit:CreativeWorkspaceEdit}|null>(null);
  const [pending,setPending]=useState<string|null>(null),[error,setError]=useState<string|null>(null);
  const [sourceMode,setSourceMode]=useState(false),[sourceText,setSourceText]=useState('');
  const [runtime,setRuntime]=useState<Probe|null>(null),[acknowledged,setAcknowledged]=useState(false);
  const [attempt,setAttempt]=useState(''),[jobState,setJobState]=useState('idle'),[jobDetail,setJobDetail]=useState('');
  const [evidence,setEvidence]=useState<HyperframesRenderEvidence|null>(null);
  const epoch=useRef(0);
  const unchanged=!!draft?.stored&&sameValue(draft.document,draft.stored);
  const stale=!!draft&&draft.baseRevision!==project.revision;
  const extension=project.extensions.find(extension=>extension.kind==='hyperframes-renderer'&&extension.enabled&&extension.rights_status==='cleared');
  const selectedNode=draft?.document.source.document.nodes.find(node=>node.id===nodeId) ?? null;
  const selection=nativeHtmlFrameSelection(project,scene,displayProfile,evidence,playhead);
  const resetDraft=()=>{setDraft(null);setReview(null);setSourceText('');setError(null);};
  const load=async()=>{
    if(!available||!saved)return;
    const generation=++epoch.current;setPending('load');setError(null);
    try{const observed=await nativeDocumentState(project,saved.id);if(generation!==epoch.current)return;
      setDraft({document:observed.document,stored:structuredClone(observed.document),baseRevision:observed.revision,sourceSha:observed.source_sha256});
      setNodeId(previous=>observed.document.source.document.nodes.some(node=>node.id===previous)?previous:observed.document.source.document.nodes[0]?.id ?? '');
      setReview(null);setSourceText(JSON.stringify(observed.document,null,2));
    }catch(reason){if(generation===epoch.current)setError(message(reason));}finally{if(generation===epoch.current)setPending(null);}
  };
  useEffect(()=>{
    epoch.current++;setPending(null);setDraft(null);setReview(null);setEvidence(null);setNodeId('');setSourceMode(false);setError(null);
    if(available&&saved)void load();
    return()=>{epoch.current++;};
    // Selection resets the native draft; unrelated revisions do not discard edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project.id,project.generation,scene?.id,displayProfile?.id]);
  useEffect(()=>{
    const current=latestDraft.current;
    if(available&&saved&&(!current||sameValue(current.document,current.stored)||sameValue(current.document,saved)))void load();
    // Do not include draft: editing a field must not refetch and erase that field.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project.revision,saved?.id]);
  const updateDocument=(document:NativeSceneDocument)=>{if(!draft)return;setDraft({...draft,document});setReview(null);setSourceText(JSON.stringify(document,null,2));};
  const updateNode=(node:NativeNode)=>{if(!draft)return;updateDocument({...draft.document,source:{renderer:'hyperframes',document:{...draft.document.source.document,nodes:draft.document.source.document.nodes.map(old=>old.id===node.id?node:old)}}});};
  const propose=async()=>{
    if(!scene||!displayProfile)return;setPending('proposal');setError(null);
    try{const value=await proposeNativeCanvas(project,scene.id,displayProfile.id);setDraft({document:value.document,stored:null,baseRevision:project.revision,sourceSha:null});setNodeId(value.document.source.document.nodes[0]?.id ?? '');setReview({difference:value.difference,edit:{kind:'upsert_native_scene',scene:value.document,expected_source_sha256:null}});setSourceText(JSON.stringify(value.document,null,2));}
    catch(reason){setError(message(reason));}finally{setPending(null);}
  };
  const inspect=async(fromJson=false)=>{
    if(!draft||stale)return;setPending('preflight');setError(null);setReview(null);
    try{
      if(fromJson && new TextEncoder().encode(sourceText).length>2*1024*1024)throw new Error('Native document exceeds the bounded input budget.');
      const document=fromJson?JSON.parse(sourceText) as NativeSceneDocument:draft.document;
      if(new TextEncoder().encode(JSON.stringify(document)).length>2*1024*1024)throw new Error('Native document exceeds the bounded input budget.');
      const response=await previewNativeEdit(project,{kind:'upsert_native_scene',scene:document,expected_source_sha256:draft.sourceSha});
      if(response.normalized_edit.kind!=='upsert_native_scene')throw new Error('Native preflight returned a different operation.');
      const normalized=response.normalized_edit.scene;
      setDraft({...draft,document:normalized});setSourceText(JSON.stringify(normalized,null,2));setReview({difference:response.difference,edit:response.normalized_edit});
    }catch(reason){setError(message(reason));}finally{setPending(null);}
  };
  const protect=async(protection:NativeProtection,locked:boolean)=>{
    if(!draft?.stored||!draft.sourceSha||!selectedNode||!unchanged||stale)return;
    await commit({type:'edit_creative_workspace',edit:{kind:'set_native_protection',id:draft.document.id,node_id:selectedNode.id,protection,locked,expected_source_sha256:draft.sourceSha,rationale:`Explicit owner ${locked?'protection':'unlock'} of ${selectedNode.name}`}});
  };
  const probe=async()=>{setPending('probe');setError(null);try{setRuntime(await probeNativeHtmlRuntime(project));setAcknowledged(false);}catch(reason){setError(message(reason));}finally{setPending(null);}};
  const optIn=async()=>{
    if(!runtime?.runtime_receipt_sha256||!acknowledged)return;
    await commit({type:'upsert_extension',extension:{id:project.extensions.find(e=>e.kind==='hyperframes-renderer')?.id ?? crypto.randomUUID(),name:'HyperFrames native HTML profile',kind:'hyperframes-renderer',package_version:runtime.runtime_version_contract,digest_sha256:runtime.runtime_receipt_sha256,license:'Owner-reviewed installed dependency terms',source:'Owner-configured canonical HyperFrames runtime receipt',rights_status:'cleared',enabled:true,permissions:['read_project','read_assets','write_artifacts']}});
  };
  const render=async()=>{
    if(!draft?.stored||!unchanged||stale||pending||['rendering','unknown','cancelling'].includes(jobState))return;
    const id=crypto.randomUUID();setAttempt(id);setJobState('rendering');setPending('render');setError(null);setEvidence(null);
    try{const result=await renderNativeHtml(project,draft.document.id,id);setEvidence(result);setJobState('succeeded');setJobDetail('Current native frames verified. Human creative review is still required.');}
    catch(reason){setJobState('unknown');setJobDetail(message(reason));setError('The attempt is retained. Observe or recover it before starting another render. '+message(reason));}finally{setPending(null);}
  };
  const reconcile=async(action:'status'|'cancel'|'result')=>{
    if(!attempt)return;setError(null);
    try{const response=await observeNativeHtmlJob(project,attempt,action);setJobState(response.result.data.state);setJobDetail(response.result.data.diagnostic ?? 'Native attempt reconciled.');}
    catch(reason){setError(message(reason));}
  };
  const recover=async()=>{if(!attempt||!saved)return;setPending('recover');setError(null);try{const result=await recoverNativeHtmlPreview(project,saved.id,attempt);setEvidence(result);setJobState('succeeded');setJobDetail('Recovered the existing attempt; no new rendering was submitted.');}catch(reason){setError(message(reason));}finally{setPending(null);}};
  const blocked=busy||pending!==null||stale;
  return <section className="native-creative-editor" aria-label="Renderer-native creative editor">
    <header className="native-editor-heading"><div><span className="eyebrow">RENDERER-NATIVE SOURCE</span><h2>HyperFrames composition</h2><p>Retain masks, curves, text, cameras and native source. No universal-IR flattening.</p></div><span className="production-status">LOCAL OPT-IN · CREATIVE REVIEW REQUIRED</span></header>
    {!available&&<div className="production-notice"><strong>Desktop native service required</strong><span>Browser demo mode does not simulate native document admission, runtime installation or completed frames. Existing editable source remains visible in project data.</span></div>}
    {error&&<div className="production-error native-editor-error" role="alert"><span>{error}</span><button className="icon-button" aria-label="Dismiss native editor error" onClick={()=>setError(null)}><X size={14}/></button></div>}
    <details className="native-runtime-panel"><summary><Lock size={14}/> Runtime identity and explicit project opt-in</summary>
      <p>The local installer and Semwright owner configuration must already provide the pinned runtime. Reading a document never installs packages, executes imported source code or changes filesystem grants.</p>
      <div className="production-button-row"><button className="secondary-button" disabled={!available||busy||pending!==null} onClick={probe}><RefreshCw size={13}/> Inspect installed runtime</button>{extension&&<span className="mono">Project runtime {extension.digest_sha256.slice(0,16)}…</span>}</div>
      {runtime&&<><p>{runtime.profile} · {runtime.configured_hyperframes ?? 'not installed'} · Host tools {runtime.host_tools?'available':'unavailable'}</p><p className="mono">{runtime.runtime_receipt_sha256 ?? 'No runtime fingerprint'}</p><p>{runtime.admission}</p>
        <label className="production-check"><input type="checkbox" checked={acknowledged} onChange={e=>setAcknowledged(e.target.checked)}/> I reviewed the installed dependency terms and authorize this exact runtime identity for this project.</label>
        <button className="secondary-button" disabled={!runtime.host_tools||!runtime.runtime_receipt_present||!runtime.runtime_receipt_sha256||!acknowledged||busy||pending!==null} onClick={optIn}>Enable this exact project profile</button></>}
      {extension&&scene&&scene.renderer!=='hyperframes'&&<button className="secondary-button" disabled={busy||pending!==null} onClick={()=>commit({type:'set_scene_renderer',scene_id:scene.id,renderer:'hyperframes'})}>Select HyperFrames for this scene</button>}
    </details>
    {stale&&<div className="production-notice"><strong>Draft r{draft?.baseRevision}; current project r{project.revision}</strong><span>Your edits are preserved locally. Reload the current document and review the difference before applying.</span><button className="secondary-button" onClick={load}>Reload current source</button></div>}
    {!draft?<div className="native-source-empty"><FilePlus2 size={28}/><h3>{saved?'Open the retained native source':'Create an explicit native realization'}</h3><p>Conversion proposes a supported, contained Canvas projection without changing the source scene. Unsupported relations or fonts are reported, not silently dropped.</p>
      <button className="primary-button" disabled={!available||!scene||!displayProfile||busy||pending!==null} onClick={saved?load:propose}>{saved?'Open native document':'Propose from current Canvas'}</button></div>:<>
      <div className="native-editor-toolbar"><label className="production-field"><span>Native realization</span><input value={draft.document.label} disabled={blocked} onChange={e=>updateDocument({...draft.document,label:e.target.value})}/></label>
        <span className="mono">r{draft.baseRevision} · {draft.document.source.document.canvas.width} × {draft.document.source.document.canvas.height} · {draft.document.source.document.canvas.frames} frames</span>
        <button className="secondary-button" onClick={()=>setSourceMode(!sourceMode)}><Code2 size={13}/>{sourceMode?'Object controls':'Structured native source'}</button></div>
      <div className="native-editor-layout"><aside className="native-object-list"><h3>Native objects</h3>{draft.document.source.document.nodes.map(node=><button key={node.id} aria-pressed={node.id===nodeId} onClick={()=>setNodeId(node.id)}><span>{node.name}</span><small>{node.content.kind} · {node.keyframes.length} keys</small></button>)}</aside>
        <div className="native-editing-surface">{sourceMode?<><p className="production-help">Typed data only. The backend validates and normalizes this document before it can be staged. Scripts and arbitrary HTML are not accepted.</p><textarea className="native-source-code" aria-label="Structured native source" spellCheck={false} value={sourceText} disabled={blocked} onChange={e=>{setSourceText(e.target.value);setReview(null);}}/><button className="secondary-button" disabled={blocked} onClick={()=>inspect(true)}>Validate structured source</button></>:selectedNode?<NativeNodeInspector key={selectedNode.id} node={selectedNode} onChange={updateNode} onProtect={protect} canProtect={unchanged&&!stale} busy={blocked}/>:<p>Select an object to edit its native properties.</p>}
          <details className="native-camera-editor"><summary>Native camera and output</summary><div className="native-transform-fields">{(['x','y','zoom','rotation'] as const).map(field=><label className="production-field" key={field}><span>Camera {field}</span><input type="number" step={field==='zoom'?.01:1} value={draft.document.source.document.camera[field]} disabled={blocked} onChange={e=>updateDocument({...draft.document,source:{renderer:'hyperframes',document:{...draft.document.source.document,camera:{...draft.document.source.document.camera,[field]:Number(e.target.value)}}}})}/></label>)}</div>
            <label className="production-check"><input type="checkbox" checked={draft.document.source.document.canvas.background===null} disabled={blocked} onChange={e=>updateDocument({...draft.document,source:{renderer:'hyperframes',document:{...draft.document.source.document,canvas:{...draft.document.source.document.canvas,background:e.target.checked?null:'#111922'}}}})}/> Preserve transparent RGBA background</label><p className="production-help">Output frame rate and duration are exact source fields. Unmatched timeline edits require a deliberate source update, not an automatic speed change.</p></details>
        </div>
        <section className="native-render-view"><header><Film size={15}/><strong>Actual native frame</strong></header>{selection?<NativeFrameStage project={project} selection={selection}/>:<div className="native-no-frame"><Square size={30}/><p>No verified current native frame</p><span>Edited source is not displayed as a finished render. Render or recover its exact revision.</span></div>}
          {scene&&<div className="production-transport"><input aria-label="Native creative playhead" type="range" min={0} max={seconds(scene.duration)} step={displayProfile?1/seconds(displayProfile.frame_rate):1/30} value={Math.max(0,Math.min(seconds(scene.duration),playhead-seconds(scene.start)))} onChange={e=>onSeek(seconds(scene.start)+Number(e.target.value))}/><span className="mono">{Math.max(0,playhead-seconds(scene.start)).toFixed(3)} s</span></div>}
          {evidence&&<p className="production-help">{evidence.frame_count} verified frames · {evidence.rate.num}/{evidence.rate.den} fps · {evidence.alpha?'RGBA':'opaque'} · source {evidence.source_sha256.slice(0,12)}…</p>}
          {evidence&&scene&&<NativeHtmlContactReview project={project} evidence={evidence} onSeek={relative=>onSeek(seconds(scene.start)+relative)}/>}
          <button className="primary-button" disabled={blocked||!unchanged||!extension||scene?.renderer!=='hyperframes'||['rendering','unknown','cancelling'].includes(jobState)} onClick={render}><Film size={14}/> Render saved native revision</button>
          <div className="native-job-controls"><strong>Logical attempt · {jobState}</strong><input aria-label="Native render attempt UUID" value={attempt} placeholder="Existing attempt UUID" onChange={e=>{setAttempt(e.target.value);setJobState('unknown');}}/><div className="production-button-row"><button className="secondary-button" disabled={!attempt||!available} onClick={()=>reconcile('status')}>Observe</button><button className="secondary-button" disabled={!attempt||!available||jobState==='succeeded'} onClick={()=>reconcile('cancel')}>Cancel</button><button className="secondary-button" disabled={!attempt||!saved||pending!==null} onClick={recover}>Recover frames</button></div><p className="production-help">{jobDetail || 'A lost reply never starts another paid or local job automatically. Existing attempts remain in the application receipt history.'}</p></div>
        </section></div>
      {review&&<div className="native-difference" aria-label="Native source difference"><Check size={15}/><div><strong>Scoped native source difference</strong><p>{review.difference.added_nodes.length} added · {review.difference.changed_nodes.length} changed · {review.difference.removed_nodes.length} removed objects. Camera {review.difference.camera_changed?'changed':'unchanged'}; assets {review.difference.asset_dependencies_changed?'changed':'unchanged'}.</p><p>Invalidates {seconds(review.difference.dirty_start).toFixed(3)}–{seconds(review.difference.dirty_end).toFixed(3)} s. This is source comparison, not pixel equivalence.</p></div></div>}
      <footer className="native-editor-actions"><button className="secondary-button" disabled={blocked||sourceMode} onClick={()=>inspect()}><Eye size={13}/> Inspect source changes</button><button className="primary-button" disabled={blocked||!review} onClick={()=>commit({type:'edit_creative_workspace',edit:review!.edit})}><Save size={13}/> Commit as one revision</button><button className="secondary-button" disabled={busy||pending!==null} onClick={saved?load:resetDraft}>Discard local draft</button></footer>
    </>}
  </section>;
}
