import { useEffect,useRef,useState } from "react";
import { previewSceneBeatTimeline,projectHistoryRecent } from "./api";
import { useCreativeWorkbenchDraft,WorkbenchDraftFeedback } from "./useCreativeWorkbenchDraft";
import { beatTimelineEnd,beatTimelineHistory,sceneBeatTimelineProposal } from "./sceneBeatTimeline";
import type { BeatTimelineRaw,BeatTimelineUndo,SceneBeatTimelineChange } from "./sceneBeatTimeline";
import { formatBeatTime,inspectBeatCoverage,parseBeatTime } from "./sceneBeatTiming";
import { sameEditorialValue,assessEditorialPreferences } from "./narrativeEditing";
import { validateSemanticSfx } from "./semanticSfx";
import { graphFrameTime } from "./semanticGraph";
import { editorFps } from "./timecode";
import { timelineSelectionScope } from "./timelineSelection";
import type { TimelineEditorRequest } from "./timelineSelection";
import type { Beat,Change,Project,RationalTime,Scene } from "./types";
import { seconds } from "./types";
import "./scene-beat-timeline.css";

interface Props {
  project:Project;scene:Scene;commit:(change:Change)=>Promise<Project>;busy:boolean;
  onClose:()=>void;playhead:number;rate:RationalTime;onSeek:(time:number)=>void;
  request?:TimelineEditorRequest|null;onRequestHandled:(nonce:number)=>void;
}
type Comparison={change:SceneBeatTimelineChange;after:Project;sequence:number;revision:number};
const message=(reason:unknown)=>reason instanceof Error?reason.message:String(reason);
const descriptions:Record<BeatTimelineRaw["operation"],string>={
  split:"Keep the original identity on the first part and create one editable second part with the same text. Existing bindings and SFX remain on the original identity.",
  trim:"Keep an interval inside this beat. Other beats keep their times; any gap remains visible.",
  move:"Move this beat with its duration intact. The destination must fit without overlapping another beat.",
  ripple:"Change this beat's duration and shift every later beat by the same exact amount. The scene length stays fixed.",
  reorder:"Move this beat to a new position in a fully covered scene. Every beat retains its duration, text and identity.",
  undo:"Restore only the beat values in the saved change, provided the whole current beat timeline and scene duration still match its result.",
};
function BeatTable({beats,label,onSeek,sceneStart}:{beats:Beat[];label:string;onSeek:(time:number)=>void;sceneStart:number}) {
  return <section aria-label={label} className="beat-timing-comparison"><h3>{label}</h3><table>
    <thead><tr><th>Beat</th><th>Start</th><th>End</th><th>Duration</th></tr></thead>
    <tbody>{beats.map(beat=><tr key={beat.id} data-beat-id={beat.id}><th><button className="text-button"
      onClick={()=>onSeek(sceneStart+seconds(beat.start))}>{beat.label}</button></th>
      <td>{formatBeatTime(beat.start)}</td><td>{formatBeatTime(beatTimelineEnd(beat))}</td><td>{formatBeatTime(beat.duration)}</td></tr>)}</tbody>
  </table>{!beats.length&&<p>No saved beats.</p>}</section>;
}
export default function SceneBeatTimelineEditor(props:Props) {
  const {project,scene,commit,busy,onClose,playhead,rate,onSeek,request,onRequestHandled}=props;
  const editor=useCreativeWorkbenchDraft(project,{kind:"beat_timeline",sceneId:scene.id},commit,busy);
  const input=editor.draft.input;
  if(input.kind!=="beat_timeline")throw new Error("The beat timeline input belongs to another editor.");
  const raw=input.config;
  const [comparison,setComparison]=useState<Comparison|null>(null),[error,setError]=useState<string|null>(null);
  const [preparing,setPreparing]=useState(false),[history,setHistory]=useState<BeatTimelineUndo[]>([]);
  const [historyError,setHistoryError]=useState<string|null>(null),[historyBusy,setHistoryBusy]=useState(false);
  const mounted=useRef(true),epoch=useRef(0),handledRequest=useRef<number|null>(null);
  const sourceScope=JSON.stringify([project.id,project.generation,project.active_branch,scene.id]);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;epoch.current++;};},[]);
  useEffect(()=>{setComparison(null);setError(null);},[project.revision,sourceScope,editor.draft.sequence]);
  useEffect(()=>{
    if(!request||request.nonce===handledRequest.current||request.scope!==timelineSelectionScope(project)
      ||request.sceneId!==scene.id||request.selection.kind!=="beat")return;
    handledRequest.current=request.nonce;
    if(scene.beats.some(beat=>beat.id===request.selection.id)&&raw.beatId!==request.selection.id)
      editor.edit({...input,config:{...raw,beatId:request.selection.id}});
    onRequestHandled(request.nonce);
    // One explicit navigation request, never a repeated source-revision effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[request?.nonce,sourceScope]);
  useEffect(()=>{
    let active=true;setHistory([]);setHistoryError(null);setHistoryBusy(true);
    projectHistoryRecent(project,project.revision,128).then(events=>{
      if(active)setHistory(beatTimelineHistory(events,project,scene.id));
    }).catch(reason=>{if(active)setHistoryError(message(reason));}).finally(()=>{if(active)setHistoryBusy(false);});
    return()=>{active=false;};
  },[project.id,project.generation,project.active_branch,project.revision,scene.id]);

  const edit=(patch:Partial<BeatTimelineRaw>)=>{
    setError(null);setComparison(null);editor.edit({...input,config:{...raw,...patch}});
  };
  let proposal:SceneBeatTimelineChange|null=null,proposalError:string|null=null;
  try{proposal=sceneBeatTimelineProposal(project,scene.id,raw);}catch(reason){proposalError=message(reason);}
  const ready=!!comparison&&!!proposal&&!editor.blocked&&!preparing&&comparison.revision===project.revision
    &&comparison.sequence===editor.draft.sequence&&sameEditorialValue(comparison.change,proposal);
  const savedCoverage=inspectBeatCoverage(scene.beats,scene.duration);
  const afterScene=comparison?.after.scenes.find(item=>item.id===scene.id);
  const afterCoverage=afterScene?inspectBeatCoverage(afterScene.beats,afterScene.duration):null;
  const historyMayReplace=!editor.draft.intent&&!editor.draft.pending&&!editor.busy;

  async function preview() {
    if(!proposal||editor.blocked||preparing)return;
    const token=editor.authorToken(),candidate=structuredClone(proposal),sequence=editor.draft.sequence,revision=project.revision,started=++epoch.current;
    setPreparing(true);setError(null);setComparison(null);
    try {
      const after=await previewSceneBeatTimeline(project,candidate);
      if(mounted.current&&started===epoch.current&&editor.tokenCurrent(token))setComparison({change:candidate,after,sequence,revision});
    }catch(reason){if(mounted.current&&started===epoch.current)setError(message(reason));}
    finally{if(mounted.current&&started===epoch.current)setPreparing(false);}
  }
  function useFrame() {
    try {
      const frame=Math.round(playhead*editorFps(rate));
      if(!Number.isSafeInteger(frame)||frame<0)throw new Error("The displayed frame is outside the editable timeline.");
      const absolute=graphFrameTime(frame,rate),start=scene.start;
      const local=parseBeatTime((BigInt(absolute.num)*BigInt(start.den)-BigInt(start.num)*BigInt(absolute.den))+"/"+(BigInt(absolute.den)*BigInt(start.den)));
      edit({start:formatBeatTime(local)});
    }catch(reason){setError(message(reason));}
  }
  const affectedSfx=(project.audio.sfx_cues??[]).filter(cue=>cue.scene_id===scene.id);
  const beforeAllocations=assessEditorialPreferences(project);
  const afterAllocations=comparison?assessEditorialPreferences(comparison.after):null;
  return <section className="workspace-scroll scene-beat-timeline" aria-label="Scene beat timeline editor">
    <header className="workspace-heading"><div><h2>Edit scene beat timing</h2><p>{scene.name} · r{project.revision} · {formatBeatTime(scene.duration)} seconds</p></div>
      <button className="secondary-button" disabled={editor.busy||preparing} onClick={onClose}>Back to storyboard</button></header>
    <p>All controls use exact scene-local seconds. One reviewed operation saves the affected beats together and marks this scene as draft.</p>
    <div className="beat-timing-fields">
      <label>Beat<select aria-label="Timeline beat" value={raw.beatId} disabled={editor.busy||preparing||raw.operation==="undo"}
        onChange={event=>edit({beatId:event.target.value})}><option value="">Select beat</option>
        {scene.beats.map((beat,index)=><option value={beat.id} key={beat.id}>{index+1}. {beat.label}</option>)}</select></label>
      <label>Operation<select aria-label="Beat timing operation" value={raw.operation} disabled={editor.busy||preparing}
        onChange={event=>edit({operation:event.target.value as BeatTimelineRaw["operation"]})}>
        <option value="split">Split</option><option value="trim">Trim</option><option value="move">Move</option>
        <option value="ripple">Ripple duration</option><option value="reorder">Reorder</option>
        {raw.undo&&<option value="undo">Conditional undo / redo</option>}</select></label>
      {["split","trim","move"].includes(raw.operation)&&<label>{raw.operation==="split"?"Split time":"Start"}
        <input aria-label="Beat operation start" value={raw.start} maxLength={128} disabled={editor.busy||preparing} onChange={event=>edit({start:event.target.value})}/></label>}
      {raw.operation==="trim"&&<label>End<input aria-label="Beat operation end" value={raw.end} maxLength={128}
        disabled={editor.busy||preparing} onChange={event=>edit({end:event.target.value})}/></label>}
      {raw.operation==="ripple"&&<label>Duration<input aria-label="Beat operation duration" value={raw.duration} maxLength={128}
        disabled={editor.busy||preparing} onChange={event=>edit({duration:event.target.value})}/></label>}
      {raw.operation==="reorder"&&<label>Position<input aria-label="Beat operation position" value={raw.position} maxLength={3}
        disabled={editor.busy||preparing} onChange={event=>edit({position:event.target.value})}/></label>}
    </div>
    <p className="beat-timing-explanation">{descriptions[raw.operation]}</p>
    {["split","trim","move"].includes(raw.operation)&&<button className="secondary-button" disabled={editor.busy||preparing} onClick={useFrame}>Use displayed frame for start</button>}
    <label className="beat-timing-rationale">Reason<textarea aria-label="Beat timing rationale" rows={2} maxLength={4000} value={raw.rationale}
      disabled={editor.busy||preparing} onChange={event=>edit({rationale:event.target.value})}/></label>
    {proposalError&&editor.draft.intent&&<p role="status" className="production-notice">{proposalError}</p>}
    {error&&<p role="alert" className="production-error">{error}</p>}
    <div className="beat-timing-actions"><button className="secondary-button" disabled={!proposal||editor.blocked||preparing} onClick={()=>void preview()}>Preview beat timing</button>
      <button className="button button-primary" disabled={!ready} onClick={()=>{setComparison(null);void editor.send("save");}}>Apply beat timing as one revision</button></div>
    <div className="beat-timing-comparisons">
      <BeatTable beats={scene.beats} label="Saved beat timing" sceneStart={seconds(scene.start)} onSeek={onSeek}/>
      {afterScene&&<BeatTable beats={afterScene.beats} label="Proposed beat timing" sceneStart={seconds(scene.start)} onSeek={onSeek}/>}
    </div>
    {savedCoverage.ok&&!savedCoverage.exact&&<p className="production-notice">The saved beat timeline has a gap. Resolve full coverage when the selected production recipe requires it.</p>}
    {afterCoverage?.ok&&!afterCoverage.exact&&<p className="production-notice">This proposal retains a timing gap. The gap stays visible; no content or hold is invented to fill it.</p>}
    {comparison&&<section aria-label="Beat timing dependency comparison"><h3>Linked production timing</h3>
      <p>Scene length, other scene positions, Canvas motion and camera clocks retain their saved values.</p>
      {affectedSfx.length>0&&<table><thead><tr><th>SFX</th><th>Saved frame</th><th>Proposed frame</th></tr></thead><tbody>{affectedSfx.map(cue=>{
        const before=validateSemanticSfx(project,cue),after=validateSemanticSfx(comparison.after,cue);
        return <tr key={cue.id}><th>{cue.event.replaceAll("_"," ")}</th><td>{before.absolute_start_frame}</td><td>{after.absolute_start_frame}</td></tr>;
      })}</tbody></table>}
      {afterAllocations&&<ul>{afterAllocations.filter(value=>!sameEditorialValue(value,beforeAllocations.find(before=>before.beat_id===value.beat_id))).map(value=><li key={value.beat_id}>
        {project.narrative.beats.find(beat=>beat.id===value.beat_id)?.label}: allocation {value.allocated_duration?formatBeatTime(value.allocated_duration)+" seconds":"unbound"}
        {value.minimum_shortfall&&BigInt(value.minimum_shortfall.num)>0n?" · minimum hold is not met":""}</li>)}</ul>}
    </section>}
    <WorkbenchDraftFeedback editor={editor} project={project}/>
    <section aria-label="Saved beat timing history" className="beat-timing-history"><h3>Undo and redo saved timing</h3>
      <p>Read from the most recent 128 project changes. An inverse keeps later unrelated edits and must still match its complete saved result.</p>
      {historyBusy&&<p role="status">Reading saved changes…</p>}{historyError&&<p role="alert">{historyError}</p>}
      {!historyBusy&&!history.length&&!historyError&&<p>No saved beat timing transaction in this history window.</p>}
      {history.map(item=><div key={item.eventRevision}><span>r{item.eventRevision} · {item.change.rationale}</span>
        <button className="secondary-button" disabled={!historyMayReplace||preparing} onClick={()=>edit({operation:"undo",undo:item,rationale:"Reverse beat timing from r"+item.eventRevision+". Keep unrelated edits."})}>Review inverse r{item.eventRevision}</button></div>)}
      {!historyMayReplace&&history.length>0&&<p>Keep or finish the current input before preparing an inverse.</p>}
    </section>
  </section>;
}
