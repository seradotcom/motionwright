import {useEffect,useState} from 'react';
import {Check,Eye,Lock,RotateCcw} from 'lucide-react';
import {nativeLocalizedRepairPreflight} from '../api';
import type {Change,Project} from '../types';
import type {NativeSceneDocument,NativeRepairOperation,PoseRepairProperty,LocalizedNativeRepairPreflight} from './nativeTypes';

type Kind='pose'|'blur'|'text'|'keyframe';
const channels:PoseRepairProperty[]=['x','y','width','height','scale_x','scale_y','rotation','opacity'];
const explain=(reason:unknown)=>reason instanceof Error?reason.message:String(reason);
export default function NativeRepairWorkbench({
  project,document,sourceSha,nodeId,busy,commit
}:{project:Project;document:NativeSceneDocument;sourceSha:string;nodeId:string;
  busy:boolean;commit:(change:Change)=>Promise<void>}){
  const node=document.source.document.nodes.find(n=>n.id===nodeId)??null;
  const [kind,setKind]=useState<Kind>('pose');
  const [property,setProperty]=useState<PoseRepairProperty>('y');
  const [value,setValue]=useState('');
  const [copy,setCopy]=useState('');
  const [keyIndex,setKeyIndex]=useState(0);
  const [rationale,setRationale]=useState('Local source repair for explicit human review');
  const [candidate,setCandidate]=useState<LocalizedNativeRepairPreflight|null>(null);
  const [working,setWorking]=useState(false),[error,setError]=useState<string|null>(null);
  useEffect(()=>{setCandidate(null);setError(null);},[project.revision,document.id,sourceSha,nodeId]);
  useEffect(()=>{
    if(!node)return;
    setValue(String(kind==='blur'?node.effects.blur:kind==='keyframe'?node.keyframes[keyIndex]?.value??0:node.pose[property]));
    if(node.content.kind==='text')setCopy(node.content.runs[0]?.text??'');
    setCandidate(null);
  },[nodeId,kind,property,keyIndex]);
  const frame=node?.keyframes[keyIndex];
  const locked=kind==='pose'?!!node?.locked_properties.includes(property):
    kind==='blur'?!!(node?.locked_properties.includes('blur')||node?.locked_fields?.includes('appearance')):
    kind==='text'?!!node?.locked_fields?.includes('content'):
    !!(node?.locked_fields?.includes('keyframes')||(frame&&node?.locked_properties.includes(frame.property)));
  const edit=():NativeRepairOperation=>{
    if(!node)throw new Error('Select an editable source object.');
    if(locked)throw new Error('This native property is protected by a human lock.');
    if(kind==='text'){
      if(node.content.kind!=='text'||!node.content.runs.length)throw new Error('No native text run in selected object.');
      if(!copy.trim())throw new Error('A repair may not erase the original caption.');
      return {kind:'replace_text_run',node_id:node.id,run_index:0,expected_text:node.content.runs[0].text,next_text:copy};
    }
    if(!value.trim())throw new Error('Choose an exact bounded numeric value.');
    const next=Number(value);
    if(!Number.isFinite(next))throw new Error('A numeric native repair must be finite.');
    if(kind==='blur')return {kind:'set_blur',node_id:node.id,expected:node.effects.blur,next};
    if(kind==='keyframe'){
      if(!frame)throw new Error('Select an existing exact rational keyframe.');
      return {kind:'set_keyframe_value',node_id:node.id,property:frame.property,
        frame:frame.frame,subframe:frame.subframe??null,expected:frame.value,next};
    }
    return {kind:'set_pose',node_id:node.id,property,expected:node.pose[property],next};
  };
  const inspect=async()=>{
    setCandidate(null);setError(null);setWorking(true);
    try{
      const reviewed=await nativeLocalizedRepairPreflight(project,document.id,sourceSha,rationale,[edit()]);
      if(reviewed.proposal.expected_source_sha256!==sourceSha||reviewed.proposal.changed_nodes.join()!==nodeId)
        throw new Error('Repair provider returned a different native source or object.');
      setCandidate(reviewed);
    }catch(reason){setError(explain(reason));}finally{setWorking(false);}
  };
  const apply=async()=>{
    if(!candidate)return;
    setWorking(true);setError(null);
    try{
      if(candidate.proposal.expected_source_sha256!==sourceSha)throw new Error('Stale source since repair preflight.');
      await commit({type:'edit_creative_workspace',edit:candidate.normalized_edit});
      setCandidate(null);
    }catch(reason){setError(explain(reason));}finally{setWorking(false);}
  };
  return <details className="native-protection native-repair" aria-label="Localized source repair">
    <summary><RotateCcw size={13}/> Review an atomic native repair</summary>
    <p className="production-help">One scoped source change at a time. Human locks and all other native objects remain untouched. No renderer execution.</p>
    <label className="production-field"><span>Repair field</span><select value={kind} disabled={busy||working}
      onChange={e=>{setKind(e.target.value as Kind);setCandidate(null);}}>
      <option value="pose">Transform / opacity</option><option value="blur">Blur</option>
      {node?.content.kind==='text'&&<option value="text">Text · first run</option>}
      {!!node?.keyframes.length&&<option value="keyframe">Exact keyframe</option>}
    </select></label>
    {kind==='pose'&&<label className="production-field"><span>Property</span><select value={property}
      disabled={busy||working} onChange={e=>{setProperty(e.target.value as PoseRepairProperty);setCandidate(null);}}>
      {channels.map(p=><option key={p} value={p}>{p.replaceAll('_',' ')}</option>)}
    </select></label>}
    {kind==='keyframe'&&<label className="production-field"><span>Authored frame / subframe</span>
      <select value={keyIndex} disabled={busy||working} onChange={e=>{setKeyIndex(Number(e.target.value));setCandidate(null);}}>
        {node?.keyframes.map((item,index)=><option key={index} value={index}>
          {item.property} · {item.frame}{item.subframe?' + '+item.subframe.num+'/'+item.subframe.den:''} frames
        </option>)}
      </select></label>}
    {kind==='text'&&node?.content.kind==='text'
      ?<label className="production-field"><span>Revised text</span><textarea rows={3} value={copy}
        disabled={busy||working||locked} onChange={e=>{setCopy(e.target.value);setCandidate(null);}}/></label>
      :<label className="production-field"><span>Exact proposed value</span><input type="number" step="any"
        value={value} disabled={busy||working||locked}
        onChange={e=>{setValue(e.target.value);setCandidate(null);}}/></label>}
    {locked&&<p className="production-help"><Lock size={12}/> Human protection requires a separate explicit unlock.</p>}
    <label className="production-field"><span>Reason for this repair</span><textarea rows={2} maxLength={2000}
      value={rationale} disabled={busy||working}
      onChange={e=>{setRationale(e.target.value);setCandidate(null);}}/></label>
    {error&&<p className="production-error" role="alert">{error}</p>}
    <div className="production-button-row">
      <button className="secondary-button" disabled={busy||working||!node||locked} onClick={inspect}>
        <Eye size={13}/> Preflight exact native repair</button>
      {candidate&&<button className="primary-button" disabled={busy||working} onClick={apply}>
        <Check size={13}/> Commit reviewed repair revision</button>}
    </div>
    {candidate&&<div className="native-difference"><div><strong>Uncommitted native repair proposal</strong>
      <p>{candidate.proposal.changed_properties.join(', ')}</p>
      <p>Frames {candidate.proposal.dirty_first_frame}–{candidate.proposal.dirty_end_frame_exclusive} require new verification.</p>
      <p>Source {sourceSha.slice(0,12)} → {candidate.proposal.proposed_source_sha256.slice(0,12)} …</p>
      <p>Human creative approval remains necessary; no pixel equivalence is claimed.</p>
    </div></div>}
  </details>;
}
