import { useState } from "react";
import CompositionStudy from "./CompositionStudy";
import { inheritedTextInk } from "./canvasInheritedInk";
import { seconds } from "./types";
import { BranchRecovery, useBranchDraft } from "./useBranchDraft";
import { branchUnknown } from "./branchDraft";
import { unavailableBranchCombination } from "./branchCombinationDraft";
import type { BranchCombinationCapability } from "./branchCombinationDraft";
import type { BranchCombinationField, CanvasNode, Change, Project } from "./types";

const labels: Record<BranchCombinationField,string> = {transform:"Composition",style:"Style",motion:"Motion"};
const choices = ["transform","style","motion"] as const;
const PAGE_SIZE=12;
function FieldValue({node,field}:{node:CanvasNode|undefined;field:BranchCombinationField}) {
  if(!node)return <p>Object missing in this source. The retained choice has not been removed.</p>;
  if(field==="motion")return node.keyframes.length?<ol>{node.keyframes.map((key,index)=><li key={index}>
    {key.property} → {key.value} at {key.at.num}/{key.at.den}s · {key.interpolation.replaceAll("_"," ")}
  </li>)}</ol>:<p>No authored keyframes.</p>;
  const values=field==="style"?Object.entries(node.style):Object.entries({x:node.x,y:node.y,width:node.width,height:node.height,rotation_deg:node.rotation_deg,opacity:node.opacity});
  return <dl>{values.map(([name,value])=><div key={name}><dt>{name.replaceAll("_"," ")}</dt><dd>{value===null?"None":String(value)}</dd></div>)}</dl>;
}

export default function BranchCombinationEditor({project,commit,capability=unavailableBranchCombination,playhead=0,onSeek}:{
  project:Project;commit:(change:Change)=>Promise<Project>;capability?:BranchCombinationCapability;playhead?:number;onSeek?:(absoluteSeconds:number)=>void;
}) {
  const [opening,setOpening]=useState(false),[openError,setOpenError]=useState<string|null>(null),[page,setPage]=useState(0);
  const [localTime,setLocalTime]=useState(0),[profileId,setProfileId]=useState("");
  const editor=useBranchDraft(project,commit,opening,capability),draft=editor.session.draft,raw=draft.combination;
  const base=project.branches.find(branch=>branch.id===project.active_branch);
  const donors=project.branches.filter(branch=>branch.id!==project.active_branch&&project.branch_workspaces.some(w=>w.branch_id===branch.id));
  const donor=donors.find(branch=>branch.id===raw?.donorBranchId);
  const saved=project.branch_workspaces.find(w=>w.branch_id===raw?.donorBranchId)?.current_state;
  const scene=project.scenes.find(item=>item.id===raw?.sceneId),other=saved?.scenes.find(item=>item.id===raw?.sceneId);
  const count=scene?.nodes.length??0,pages=Math.max(1,Math.ceil(count/PAGE_SIZE)),safePage=Math.min(page,pages-1);
  const candidate=raw?.completed?project.branches.find(branch=>branch.id===raw.candidateId):undefined;
  const takeLocked=!!project.production_design?.narration_take_lock;
  const unavailable=capability.status==="unavailable"?capability.reason:null;
  const profile=project.deliverables.find(item=>item.id===profileId)??project.deliverables[0];
  const time=scene?Math.max(0,Math.min(seconds(scene.duration),onSeek?playhead-seconds(scene.start):localTime)):0;
  const surface=(palette:Project["visual_language"]["palette"])=>palette.find(token=>["surface","background"].includes(token.name.toLowerCase()))?.value??"#0F1216";
  const candidateNodes=scene&&editor.combinationPreview?scene.nodes.map(node=>
    editor.combinationPreview!.preview.after.find(after=>after.id===node.id)??node):null;
  function toggle(nodeId:string,field:BranchCombinationField) {
    if(!raw)return;
    const selected=raw.picks.some(pick=>pick.node_id===nodeId&&pick.field===field);
    editor.editCombination({picks:selected?raw.picks.filter(pick=>!(pick.node_id===nodeId&&pick.field===field)):[...raw.picks,{node_id:nodeId,field}]});
  }
  async function openCandidate() {
    if(!candidate||opening||editor.busy||branchUnknown(draft)||takeLocked)return;
    setOpening(true);setOpenError(null);
    try {await commit({type:"checkout_branch",branch_id:candidate.id});}
    catch(reason){setOpenError(reason instanceof Error?reason.message:String(reason));}
    finally{setOpening(false);}
  }
  return <section className="creative-alternatives-decision" aria-label="Combine editable alternatives">
    <header className="creative-alternatives-heading"><div><div>
      <h3>Combine into a separate candidate</h3>
      <p>Keep the current composition and choose fields from another saved strategy.</p>
    </div></div></header>
    {unavailable&&<p role="status">{unavailable} You can keep a recoverable draft; creating a candidate requires a canonical source preview.</p>}
    {!raw?<>
      <p>The current branch and the donor remain available. The result starts as an inactive editable branch with its own review.</p>
      <button type="button" className="button compact" disabled={editor.busy||branchUnknown(draft)||!!editor.session.backupError}
        onClick={editor.startCombination}>Start a combination draft</button>
      {(draft.name||branchUnknown(draft)||editor.session.backupError)&&<BranchRecovery editor={editor}/>}
    </>:<>
      <div className="creative-alternatives-chooser">
        <label><span className="field-label">Base strategy</span><strong>{base?.name??"Unavailable active branch"}</strong>
          <small>Draft source r{draft.baseRevision}; displayed project r{project.revision}</small></label>
        <label><span className="field-label">Donor strategy</span>
          <select aria-label="Combination donor branch" value={raw.donorBranchId} onChange={event=>editor.editCombination({donorBranchId:event.target.value})}>
            <option value="">Choose a saved alternative</option>
            {raw.donorBranchId&&!donor&&<option value={raw.donorBranchId}>Retained donor unavailable · {raw.donorBranchId}</option>}
            {donors.map(branch=><option key={branch.id} value={branch.id}>{branch.name} · r{branch.head_revision}</option>)}
          </select></label>
        <label><span className="field-label">Scene to combine</span>
          <select aria-label="Combination scene" value={raw.sceneId} onChange={event=>{editor.editCombination({sceneId:event.target.value});setPage(0);}}>
            <option value="">Choose a scene</option>
            {raw.sceneId&&!scene&&<option value={raw.sceneId}>Retained scene unavailable · {raw.sceneId}</option>}
            {project.scenes.map(item=><option key={item.id} value={item.id}>{item.name}{saved&&!saved.scenes.some(s=>s.id===item.id)?" · absent from donor":""}</option>)}
          </select></label>
      </div>
      <label className="creative-alternatives-reason"><span className="field-label">Candidate name</span>
        <input aria-label="Combination candidate name" value={draft.name} onChange={event=>editor.edit(event.target.value)}/></label>
      <label className="creative-alternatives-reason"><span className="field-label">Reason for this combination</span>
        <textarea aria-label="Combination reason" rows={3} value={raw.rationale} onChange={event=>editor.editCombination({rationale:event.target.value})}/></label>
      <p>Fields left unchosen stay with the base strategy. Motion replaces the chosen object's entire keyframe schedule, including removal of its earlier keys.</p>
      {scene&&<fieldset><legend>Choose donor fields for existing objects</legend>
        {scene.nodes.slice(safePage*PAGE_SIZE,(safePage+1)*PAGE_SIZE).map(node=><div key={node.id}>
          <strong>{node.name}</strong>
          {!other?.nodes.some(n=>n.id===node.id)&&<span> · Missing from donor</span>}
          {choices.map(field=><label key={field}><input type="checkbox"
            aria-label={"Use donor "+field+" for "+node.name}
            checked={raw.picks.some(pick=>pick.node_id===node.id&&pick.field===field)}
            onChange={()=>toggle(node.id,field)}/>{labels[field]}</label>)}
        </div>)}
        <p role="status">Objects {count?safePage*PAGE_SIZE+1:0}–{Math.min((safePage+1)*PAGE_SIZE,count)} of {count}. {raw.picks.length} retained field choices.</p>
        {pages>1&&<div><button type="button" className="button compact" disabled={safePage===0} onClick={()=>setPage(safePage-1)}>Previous objects</button>
          <button type="button" className="button compact" disabled={safePage===pages-1} onClick={()=>setPage(safePage+1)}>Next objects</button></div>}
      </fieldset>}
      {!!raw.picks.length&&<div className="creative-alternatives-diffs" aria-label="Combination source fields">
        {raw.picks.map((pick,index)=>{
          const current=scene?.nodes.find(node=>node.id===pick.node_id),donated=other?.nodes.find(node=>node.id===pick.node_id);
          return <details key={pick.node_id+pick.field+index}><summary>{labels[pick.field]} · {current?.name??pick.node_id}</summary>
            <h4>Current base</h4><FieldValue node={current} field={pick.field}/>
            <h4>Saved donor</h4><FieldValue node={donated} field={pick.field}/>
            <button type="button" className="button compact" onClick={()=>editor.editCombination({picks:raw.picks.filter((_,i)=>i!==index)})}>Remove this retained choice</button>
          </details>;
        })}
      </div>}
      {scene&&other&&profile&&<section aria-label="Editable strategy source studies">
        <label><span className="field-label">Comparison profile</span><select aria-label="Combination comparison profile" value={profile.id} onChange={event=>setProfileId(event.target.value)}>
          {project.deliverables.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        <div className="production-compare-grid">
          <figure><figcaption>BASE · current editable source</figcaption><CompositionStudy nodes={scene.nodes} width={profile.width} height={profile.height}
            time={time} background={surface(project.visual_language.palette)} ink={inheritedTextInk(project.visual_language.palette)} textOverrides={profile.text_overrides}
            label="Combination base source study"/></figure>
          <figure><figcaption>DONOR · saved editable source</figcaption><CompositionStudy nodes={other.nodes} width={profile.width} height={profile.height}
            time={time} background={surface(saved!.visual_language.palette)} ink={inheritedTextInk(saved!.visual_language.palette)} textOverrides={profile.text_overrides}
            label="Combination donor source study"/></figure>
          {candidateNodes&&<figure><figcaption>CANDIDATE · canonical source diff</figcaption><CompositionStudy nodes={candidateNodes} width={profile.width} height={profile.height}
            time={time} background={surface(project.visual_language.palette)} ink={inheritedTextInk(project.visual_language.palette)} textOverrides={profile.text_overrides}
            label="Combination candidate source study"/></figure>}
        </div>
        <label><span className="field-label">Shared scene time · {time.toFixed(3)} s</span><input aria-label="Combination comparison time" type="range" min="0" max={seconds(scene.duration)} step="0.001" value={time}
          onChange={event=>{const value=Number(event.target.value);setLocalTime(value);onSeek?.(seconds(scene.start)+value);}}/></label>
        <p>These bounded editorial studies share the same scene time. They draw flat text, shapes and circles; parenting, imported media and advanced paint require native frames. Source inequality does not establish a visually different or successful strategy.</p>
      </section>}
      <button type="button" className="button compact" disabled={editor.busy||editor.previewingCombination||!editor.combinationAvailable||branchUnknown(draft)||!!raw.completed||editor.conflict}
        onClick={()=>void editor.previewCombination()}>{editor.previewingCombination?"Reading source preview…":"Read canonical combination preview"}</button>
      {editor.combinationPreview&&<section aria-label="Canonical combination preview">
        <h4>Reviewed source changes at r{editor.combinationPreview.source.revision}</h4>
        <p>The canonical preview covers {editor.combinationPreview.preview.after.length} editable objects. This is an authoring diff; native frames and creative quality are not yet assessed.</p>
        {raw.picks.map((pick,index)=><details key={pick.node_id+pick.field+index}><summary>{labels[pick.field]} · {scene?.nodes.find(node=>node.id===pick.node_id)?.name??pick.node_id}</summary>
          <h5>Before</h5><FieldValue node={editor.combinationPreview!.preview.before.find(node=>node.id===pick.node_id)} field={pick.field}/>
          <h5>Candidate</h5><FieldValue node={editor.combinationPreview!.preview.after.find(node=>node.id===pick.node_id)} field={pick.field}/>
        </details>)}
      </section>}
      <button type="button" className="button button-primary" disabled={!editor.canSaveCombination}
        onClick={()=>void editor.saveCombination()}>Create inactive combined candidate</button>
      {candidate&&<section aria-label="Saved combined candidate"><p>Saved branch: <strong>{candidate.name}</strong>. The current source branch remains active.</p>
        {takeLocked&&<p role="status">The recorded narration currently blocks branch checkout. The candidate remains saved; its source has not been activated.</p>}
        <button type="button" className="button compact" disabled={editor.busy||takeLocked||branchUnknown(draft)} onClick={()=>void openCandidate()}>Open saved combined candidate</button>
        <button type="button" className="button compact" disabled={editor.busy||branchUnknown(draft)} onClick={editor.startCombination}>Keep this copy and start another candidate</button>
      </section>}
      {openError&&<p role="alert">{openError}</p>}
      <BranchRecovery editor={editor}/>
    </>}
  </section>;
}
