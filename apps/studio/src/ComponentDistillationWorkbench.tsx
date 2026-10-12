import { FileCheck2, Save } from "lucide-react";
import { useEffect, useState } from "react";
import type { CanvasNode, Change, Project, Scene } from "./types";
import type { ComponentDistillationInput, DistillationNegative, DistillationReview, DistillationVerdict } from "./componentDistillation";
import { DISTILLATION_RENDERER } from "./componentDistillation";

type Props={project:Project;busy:boolean;commit:(change:Change)=>Promise<void>};
const originalKinds=new Set(["text","rectangle","circle"]);
const compatibleNodes=(scene:Scene):CanvasNode[]=>scene.nodes.filter(node=>
  originalKinds.has(node.kind) && !node.parent_id && node.relations.length===0 &&
  node.property_locks.length===0 && node.keyframes.length<=8);
function negativeCase(scene:Scene):DistillationNegative|null{
  if(scene.renderer!=="motion-canvas")
    return {scene_id:scene.id,node_id:null,rejection:"non_motion_canvas"};
  const locked=scene.nodes.find(node=>node.property_locks.length>0);
  if(locked)return {scene_id:scene.id,node_id:locked.id,rejection:"locked_object"};
  const unsupported=scene.nodes.find(node=>!originalKinds.has(node.kind));
  return unsupported?{scene_id:scene.id,node_id:unsupported.id,rejection:"unsupported_kind"}:null;
}
const errorText=(e:unknown)=>e instanceof Error?e.message:String(e);
export default function ComponentDistillationWorkbench({project,busy,commit}:Props){
  const proposals=project.production_design?.distillations??[];
  const sourceScenes=project.scenes.filter(scene=>
    scene.renderer==="motion-canvas" && compatibleNodes(scene).length>=2);
  const negativeScenes=project.scenes.flatMap(scene=>{
    const negative=negativeCase(scene);
    return negative?[{scene,negative}]:[];
  });
  const [aId,setA]=useState(""),[bId,setB]=useState(""),[negativeId,setNegative]=useState("");
  const [rolesCount,setRolesCount]=useState(2);
  const [author,setAuthor]=useState(""),[label,setLabel]=useState("");
  const [rights,setRights]=useState(""),[requirements,setRequirements]=useState("");
  const [reviewId,setReviewId]=useState(""),[reviewer,setReviewer]=useState("");
  const [note,setNote]=useState(""),[verdict,setVerdict]=useState<DistillationVerdict>("compatible_proposal");
  const [base,setBase]=useState(project.revision);
  const [error,setError]=useState<string|null>(null);
  useEffect(()=>{setBase(project.revision);setA("");setB("");setNegative("");setReviewId("");setError(null);
    // Human draft content is retained across project-generation transitions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project.generation]);
  const a=sourceScenes.find(scene=>scene.id===aId);
  const b=sourceScenes.find(scene=>scene.id===bId);
  const bad=negativeScenes.find(item=>item.scene.id===negativeId);
  const selected=compatibleNodes(a??{nodes:[]} as Scene).slice(0,rolesCount);
  const matches=b?selected.map(node=>compatibleNodes(b).find(candidate=>candidate.kind===node.kind)):null;
  const genuine=!!a && !!b && a.id!==b.id && !!bad &&
    bad.scene.id!==a.id && bad.scene.id!==b.id && selected.length===rolesCount &&
    matches?.length===rolesCount && matches.every(Boolean) &&
    new Set(matches.map(node=>node?.id)).size===rolesCount;
  const stale=base!==project.revision;
  const locked=project.locks.some(lock=>lock.resource==="project:"+project.id && lock.kind==="content");
  const requirementsList=requirements.split("\n").map(value=>value.trim()).filter(Boolean);
  const canPropose=genuine && !stale && !busy && !locked && !!label.trim() &&
    !!author.trim() && !!rights.trim() && requirementsList.length>0 && requirementsList.length<=12;
  const target=proposals.find(candidate=>candidate.input.id===reviewId && !candidate.review);
  const canReview=!!target && !!reviewer.trim() && !!note.trim() &&
    reviewer.trim().toLowerCase()!==target.input.author.toLowerCase() &&
    !busy && !stale && !locked;
  async function propose(){
    if(!canPropose || !a || !b || !bad || !matches)return;
    const input:ComponentDistillationInput={
      id:crypto.randomUUID(),label:label.trim(),author:author.trim(),
      original_rights_attestation:rights.trim(),
      source_project_id:project.id,source_generation:project.generation,
      source_revision:project.revision,
      roles:selected.map((node,i)=>({key:"slot_"+String(i+1).padStart(2,"0"),kind:node.kind})),
      positives:[
        {scene_id:a.id,node_ids:selected.map(node=>node.id)},
        {scene_id:b.id,node_ids:matches.map(node=>node!.id)},
      ],
      negatives:[bad.negative],requirements:requirementsList,
    };
    setError(null);
    try{
      await commit({type:"propose_component_distillation",input});
      setBase(project.revision+1);setLabel("");
    }catch(e){setError(errorText(e));}
  }
  async function review(){
    if(!canReview || !target)return;
    const record:DistillationReview={
      reviewer:reviewer.trim(),rationale:note.trim(),verdict,
      source_revision:target.input.source_revision,compatibility:DISTILLATION_RENDERER,
    };
    setError(null);
    try{
      await commit({type:"review_component_distillation",id:target.input.id,review:record});
      setBase(project.revision+1);setReviewId("");setNote("");
    }catch(e){setError(errorText(e));}
  }
  const sourceOptions=(value:string,onChange:(value:string)=>void,label:string)=>
    <select aria-label={label} value={value} onChange={e=>onChange(e.target.value)}>
      <option value="">Choose an authored Motion Canvas scene</option>
      {sourceScenes.map(scene=><option value={scene.id} key={scene.id}>{scene.name}</option>)}
    </select>;
  return <div className="production-split">
    <section className="production-inspector" aria-label="Component distillation proposal controls">
      <div className="production-section-title"><div>
        <h2>Component distillation</h2>
        <p>Evidence-bound proposals Â· no code installation</p>
      </div></div>
      <p className="production-help">Extract source-owned semantic Canvas roles from two genuinely varied
        original scenes. Provide an actually rejected counterexample and an author rights declaration.
        Source code, unverified screenshots and arbitrary permissions are never admitted.</p>
      {stale && <div className="production-notice" role="status">
        <strong>Source revision {base} is stale; current revision {project.revision}.</strong>
        <button className="secondary-button" onClick={()=>{setBase(project.revision);setError(null);}}>Reload source revision</button>
      </div>}
      <h3>Source variation evidence</h3>
      <label className="production-field"><span>Positive scene A</span>
        {sourceOptions(aId,setA,"Distillation source A")}</label>
      <label className="production-field"><span>Positive scene B</span>
        {sourceOptions(bId,setB,"Distillation source B")}</label>
      <label className="production-field"><span>Semantic roles</span>
        <select aria-label="Distillation role count" value={rolesCount}
          onChange={e=>setRolesCount(Number(e.target.value))}>
          {[2,3,4,5,6].map(n=><option key={n} value={n}>{n} original Canvas objects</option>)}
        </select>
      </label>
      <p className="production-help">{genuine?
        "Matching editable source roles: "+selected.map(n=>n.kind).join(", "):
        "Two separate source scenes need matching authored semantic objects."}</p>
      <h3>Observed negative evidence</h3>
      <label className="production-field"><span>Rejected scene</span>
        <select aria-label="Distillation negative scene" value={negativeId}
          onChange={e=>setNegative(e.target.value)}>
          <option value="">Choose actually incompatible or locked source</option>
          {negativeScenes.filter(n=>n.scene.id!==a?.id && n.scene.id!==b?.id).map(item=>
            <option value={item.scene.id} key={item.scene.id}>
              {item.scene.name} Â· {item.negative.rejection}</option>)}
        </select>
      </label>
      {!negativeScenes.length && <p role="status" className="production-help">
        No negative case exists. Keep a rejected or locked source scene as real counterevidence.</p>}
      <h3>Original author constraints</h3>
      <label className="production-field"><span>Candidate name</span>
        <input aria-label="Distillation label" maxLength={120} value={label}
          onChange={e=>setLabel(e.target.value)}/></label>
      <label className="production-field"><span>Original author</span>
        <input aria-label="Distillation author" maxLength={120} value={author}
          onChange={e=>setAuthor(e.target.value)}/></label>
      <label className="production-field"><span>Declaration of original-content rights</span>
        <textarea aria-label="Distillation rights" rows={2} maxLength={480}
          value={rights} onChange={e=>setRights(e.target.value)}/></label>
      <label className="production-field"><span>Versioned requirements (one per line)</span>
        <textarea aria-label="Distillation requirements" rows={3} value={requirements}
          onChange={e=>setRequirements(e.target.value)}/></label>
      <button className="primary-button" disabled={!canPropose} onClick={()=>void propose()}>
        <Save size={14}/> Propose original component
      </button>
      <h3>Separate human compatibility review</h3>
      <label className="production-field"><span>Unreviewed proposal</span>
        <select aria-label="Distillation review candidate" value={reviewId}
          onChange={e=>setReviewId(e.target.value)}>
          <option value="">Choose an immutable source proposal</option>
          {proposals.filter(p=>!p.review).map(p=><option key={p.input.id} value={p.input.id}>
            {p.input.label} Â· revision {p.input.source_revision}</option>)}
        </select>
      </label>
      <label className="production-field"><span>Independent reviewer</span>
        <input aria-label="Distillation reviewer" maxLength={120} value={reviewer}
          onChange={e=>setReviewer(e.target.value)}/></label>
      <label className="production-field"><span>Verdict</span>
        <select aria-label="Distillation verdict" value={verdict}
          onChange={e=>setVerdict(e.target.value as DistillationVerdict)}>
          <option value="compatible_proposal">Compatible proposal Â· not admitted</option>
          <option value="rejected">Reject source candidate</option>
        </select></label>
      <label className="production-field"><span>Review rationale</span>
        <textarea aria-label="Distillation review rationale" rows={2} maxLength={1600}
          value={note} onChange={e=>setNote(e.target.value)}/></label>
      <button className="secondary-button" disabled={!canReview} onClick={()=>void review()}>
        <FileCheck2 size={14}/> Record review only
      </button>
      {error && <p className="production-error" role="alert">{error}</p>}
    </section>
    <section className="production-viewer" aria-label="Distillation source evidence">
      <div className="production-viewer-toolbar"><div>
        <strong>Frozen proposal registry</strong>
        <span>{proposals.length} source proposals Â· no automatic installation</span>
      </div></div>
      <div className="production-capsule-list">
        {proposals.map(candidate=><article key={candidate.input.id}>
          <header><strong>{candidate.input.label}</strong><span>
            {candidate.review?candidate.review.verdict==="compatible_proposal"?
              "Reviewed proposal / not admitted":"Rejected":"Pending independent review"}
          </span></header>
          <p>{candidate.samples.length} frozen positive source variations Â·
            {candidate.input.negatives.length} observed negative sample(s) Â·
            {candidate.input.roles.length} original roles</p>
          <p className="mono">Source revision {candidate.input.source_revision} Â·
            generation {candidate.input.source_generation}</p>
          <p>{candidate.input.requirements.join(" Â· ")}</p>
          {candidate.review && <p>{candidate.review.reviewer}: {candidate.review.rationale}</p>}
        </article>)}
        {!proposals.length && <p className="production-help">
          No proposals exist. Compatible variations, observed rejection and a rights
          declaration are necessary to create a record.</p>}
      </div>
      <div className="production-evidence-note"><strong>Proposal â‰  admission</strong>
        <p>Reviews never install code, expand SDK permissions, certify IP rights
          or promote this candidate to the executable component registry.</p></div>
    </section>
  </div>;
}
