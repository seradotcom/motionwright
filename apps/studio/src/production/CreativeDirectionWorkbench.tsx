import {useEffect,useMemo,useState} from 'react';
import {BookOpen,GitCompareArrows,ShieldAlert} from 'lucide-react';
import {creativeDirectionSourceState,creativeDirectionStudy} from '../api';
import type {Project,Scene} from '../types';
import type {
 CreativeDirection,CreativeDirectionStudyReport,CreativeDirectionStudyRequest,
 CreativeEvidenceKind,CreativeReferenceStudy,CreativeRhythm,NarrativeStructure
} from './directionTypes';

type ConceptDraft={
 id:string;title:string;metaphor:string;structure:NarrativeStructure;
 rhythm:CreativeRhythm;distinct_visual_argument:string;
 narrative_purpose:string;audience_takeaway:string;
 evidence_kind:CreativeEvidenceKind;claim_ids:string[];
};
const presets=[
 {structure:'problem_action_outcome',rhythm:'accelerating',
  title:'Problem → action → observable outcome',metaphor:'An original task journey'},
 {structure:'before_after',rhythm:'deliberate',
  title:'Before → after contrast',metaphor:'An evidence mirror'},
 {structure:'causal_walkthrough',rhythm:'staccato',
  title:'Explained causal sequence',metaphor:'A set of linked decisions'},
 {structure:'editorial_reveal',rhythm:'continuous',
  title:'Editorial reveal',metaphor:'A progressive examination'},
] as const;
function draft(index:number):ConceptDraft{
 const preset=presets[index];
 return {id:crypto.randomUUID(),title:preset.title,metaphor:preset.metaphor,
  structure:preset.structure,rhythm:preset.rhythm,
  distinct_visual_argument:'Use a structurally original sequence, not a palette swap or copied reference.',
  narrative_purpose:'Explain this real scene without inventing unsupported product performance.',
  audience_takeaway:'Distinguish an owner-observed action from illustrative graphics.',
  evidence_kind:'graphic_illustration',claim_ids:[]};
}
const format=(value:string)=>value.replaceAll('_',' ');
const failure=(error:unknown)=>error instanceof Error?error.message:String(error);
export default function CreativeDirectionWorkbench({
 project,scene,available,busy
}:{project:Project;scene:Scene|null;available:boolean;busy:boolean}){
 const media=useMemo(()=>project.assets.filter(asset=>
  !!asset.content_sha256&&!!asset.source_revision&&
  (asset.media_type.startsWith('image/')||asset.media_type.startsWith('video/'))),[project.assets]);
 const [assetId,setAssetId]=useState('');
 const [productVersion,setProductVersion]=useState('');
 const [referenceId,setReferenceId]=useState(()=>crypto.randomUUID());
 const [terms,setTerms]=useState('');
 const [rights,setRights]=useState(false);
 const [hierarchy,setHierarchy]=useState('');
 const [framing,setFraming]=useState('');
 const [transition,setTransition]=useState('');
 const [referenceRhythm,setReferenceRhythm]=useState('');
 const [originality,setOriginality]=useState('');
 const [concepts,setConcepts]=useState<ConceptDraft[]>(()=>[draft(0),draft(1)]);
 const [result,setResult]=useState<CreativeDirectionStudyReport|null>(null);
 const [error,setError]=useState<string|null>(null),[working,setWorking]=useState(false);
 const chosen=media.find(a=>a.id===assetId);
 useEffect(()=>{setResult(null);setError(null);},[project.id,project.generation,project.revision,scene?.id]);
 useEffect(()=>{setResult(null);setRights(false);setReferenceId(crypto.randomUUID());},[assetId]);
 const update=(index:number,change:Partial<ConceptDraft>)=>{
  setConcepts(existing=>existing.map((item,i)=>i===index?{...item,...change}:item));setResult(null);
 };
 const inspect=async()=>{
  setWorking(true);setError(null);setResult(null);
  try{
   if(!scene||!chosen?.content_sha256||!chosen.source_revision)
    throw new Error('Select an imported reference with an exact SHA-256 and source version.');
   if(!project.brief.objective.trim()||!project.brief.audience.trim())
    throw new Error('Set the objective and target audience in the canonical Project Brief first.');
   if(!productVersion.trim()||!rights||
     ![terms,hierarchy,framing,transition,referenceRhythm,originality].every(v=>v.trim()))
    throw new Error('Provide the current product version, licensed source terms, four observed reference features and a non-copying constraint.');
   const source=await creativeDirectionSourceState(project);
   if(source.project_id!==project.id||source.generation!==project.generation||
      source.revision!==project.revision)
    throw new Error('Canonical source state changed before the concept comparison.');
   const reference:CreativeReferenceStudy={
    id:referenceId,asset_id:chosen.id,content_sha256:chosen.content_sha256,
    source_revision:chosen.source_revision,owner_usage_note:terms,
    owner_attests_licensed_use:rights,observed_hierarchy:hierarchy,
    observed_framing:framing,observed_transition:transition,
    observed_rhythm:referenceRhythm,originality_constraint:originality
   };
   const alternatives:CreativeDirection[]=concepts.map(concept=>{
    if(concept.evidence_kind!=='graphic_illustration'&&!concept.claim_ids.length)
      throw new Error('A real product/footage claim needs an explicit, actually sourced Brief claim.');
    return {
     id:concept.id,title:concept.title,metaphor:concept.metaphor,
     structure:concept.structure,rhythm:concept.rhythm,
     distinct_visual_argument:concept.distinct_visual_argument,
     reference_ids:[reference.id],
     shot_studies:[{
      scene_id:scene.id,narrative_purpose:concept.narrative_purpose,
      audience_takeaway:concept.audience_takeaway,
      claim_ids:concept.claim_ids,
      evidence_kind:concept.evidence_kind,
      evidence_asset_id:concept.evidence_kind==='graphic_illustration'?null:chosen.id
     }]
    };
   });
   const request:CreativeDirectionStudyRequest={
    schema:'motionwright.creative-direction-study/1',
    project_id:project.id,generation:project.generation,revision:project.revision,
    project_sha256:source.project_sha256,product_version:productVersion,
    references:[reference],alternatives
   };
   const response=await creativeDirectionStudy(project,request);
   if(response.report.project_sha256!==source.project_sha256||
      response.report.candidate_reviews.length!==concepts.length||
      response.report.winning_concept_id!==null||response.report.renderer_executed||
      response.concept_selected||response.source_media_opened||response.project_modified)
    throw new Error('Direction preflight returned a different source or implicit user approval.');
   setResult(response.report);
  }catch(reason){setError(failure(reason));}finally{setWorking(false);}
 };
 return <details className="native-contact-review" aria-label="Source-bound creative direction comparison">
  <summary><BookOpen size={14}/> Compare original concepts and narrative evidence</summary>
  <p className="production-help">Two to four distinct editorial arguments, analyzed imported references and product-version claim checks. This is a read-only creative proposal—not a preference prediction, renderer result, copyright clearance or product demonstration.</p>
  {!available&&<p className="production-help">Open Motionwright desktop to validate a source against its canonical project revision.</p>}
  {!scene&&<p className="production-error">Select an existing scene to plan its original narrative evidence.</p>}
  <section className="native-job-controls">
   <strong>Owner-declared reference and current product build</strong>
   <label className="production-field"><span>Actual imported image/video source</span>
    <select value={assetId} disabled={busy||working} onChange={e=>setAssetId(e.target.value)}>
     <option value="">Choose project media</option>
     {media.map(asset=><option key={asset.id} value={asset.id}>{asset.name} · {asset.source_revision}</option>)}
    </select>
   </label>
   {chosen&&<p className="production-help mono">Source SHA-256 {chosen.content_sha256} · source revision {chosen.source_revision}</p>}
   <label className="production-field"><span>Current product version to test claims against</span>
    <input value={productVersion} maxLength={160} disabled={busy||working} placeholder="Exact owner-declared build/version"
      onChange={e=>{setProductVersion(e.target.value);setResult(null);}}/>
   </label>
   <label className="production-field"><span>Rights and permitted reference use</span>
    <textarea rows={2} value={terms} maxLength={1000} disabled={busy||working}
     onChange={e=>{setTerms(e.target.value);setResult(null);}}/>
   </label>
   <label className="production-check">
    <input type="checkbox" checked={rights} disabled={busy||working}
     onChange={e=>{setRights(e.target.checked);setResult(null);}}/>
    I attest that I can use this particular source as a creative reference.
   </label>
   {([
    ['Visual hierarchy',hierarchy,setHierarchy],
    ['Observed framing',framing,setFraming],
    ['Observed transition',transition,setTransition],
    ['Observed rhythm',referenceRhythm,setReferenceRhythm],
    ['Originality: what must NOT be copied',originality,setOriginality],
   ] as const).map(([label,value,set])=><label className="production-field" key={label}>
    <span>{label}</span><textarea rows={2} value={value} disabled={busy||working}
     onChange={e=>{set(e.target.value);setResult(null);}}/>
   </label>)}
   <p className="production-help"><ShieldAlert size={12}/> Notes are your observations, not pixel analysis performed by the application. A license declaration is not independently verified.</p>
  </section>
  {concepts.map((concept,index)=><section className="native-job-controls" key={concept.id}>
    <strong>Concept {index+1} · editable draft</strong>
    <label className="production-field"><span>Concept title</span><input value={concept.title} disabled={busy||working}
      onChange={e=>update(index,{title:e.target.value})}/></label>
    <label className="production-field"><span>Metaphor (must differ)</span><input value={concept.metaphor}
      disabled={busy||working} onChange={e=>update(index,{metaphor:e.target.value})}/></label>
    <div className="native-transform-fields">
     <label className="production-field"><span>Narrative structure</span><select value={concept.structure}
       disabled={busy||working} onChange={e=>update(index,{structure:e.target.value as NarrativeStructure})}>
       {presets.map(p=><option key={p.structure} value={p.structure}>{format(p.structure)}</option>)}
     </select></label>
     <label className="production-field"><span>Motion/rhythm</span><select value={concept.rhythm}
       disabled={busy||working} onChange={e=>update(index,{rhythm:e.target.value as CreativeRhythm})}>
       {presets.map(p=><option key={p.rhythm} value={p.rhythm}>{format(p.rhythm)}</option>)}
     </select></label>
    </div>
    {([
      ['Distinct original visual argument','distinct_visual_argument'],
      ['Shot narrative purpose','narrative_purpose'],
      ['Audience takeaway','audience_takeaway'],
    ] as const).map(([title,key])=><label className="production-field" key={key}>
      <span>{title}</span><textarea rows={2} disabled={busy||working} value={concept[key]}
       onChange={e=>update(index,{[key]:e.target.value})}/>
    </label>)}
    <label className="production-field"><span>What type of visual evidence?</span>
      <select value={concept.evidence_kind} disabled={busy||working}
       onChange={e=>update(index,{evidence_kind:e.target.value as CreativeEvidenceKind})}>
       <option value="graphic_illustration">Original graphic illustration · not product proof</option>
       <option value="real_product_capture">Owner-attested actual product capture</option>
       <option value="licensed_footage">Licensed source footage</option>
      </select>
    </label>
    {project.brief.claims.length>0&&<fieldset className="native-difference">
     <legend>Canonical Brief claims in this scene</legend>
     {project.brief.claims.map(claim=><label className="production-check" key={claim.id}>
       <input type="checkbox" disabled={busy||working} checked={concept.claim_ids.includes(claim.id)}
        onChange={e=>update(index,{claim_ids:e.target.checked?
          [...concept.claim_ids,claim.id]:concept.claim_ids.filter(id=>id!==claim.id)})}/>
       {claim.text}
     </label>)}
    </fieldset>}
   </section>)}
  <div className="production-button-row">
   <button className="secondary-button" type="button" disabled={busy||working||concepts.length>=4}
    onClick={()=>{setConcepts(existing=>[...existing,draft(existing.length)]);setResult(null);}}>
     Add distinct concept
   </button>
   <button className="secondary-button" type="button"
    disabled={busy||working||concepts.length<=2}
    onClick={()=>{setConcepts(existing=>existing.slice(0,-1));setResult(null);}}>
     Remove last concept
   </button>
   <button className="primary-button" type="button" disabled={busy||working||!available||!scene||!chosen}
    onClick={()=>void inspect()}><GitCompareArrows size={14}/> Compare source-backed concepts</button>
  </div>
  {error&&<p className="production-error" role="alert">{error}</p>}
  {result&&<section className="native-job-controls" aria-label="Unapproved creative comparison report">
   <strong>{result.candidate_reviews.length} distinct concepts validated against revision {result.revision}</strong>
   <p className="production-help">No concept selected, no media pixels inspected and no product claims verified. A human must review the source media, originality and narrative arguments before creating or approving any ProductionPlan.</p>
   {result.candidate_reviews.map((review,index)=><div key={review.concept_id} className="native-difference">
    <strong>{concepts[index]?.title??review.concept_id}</strong>
    <p className="mono">Source {review.concept_sha256.slice(0,16)}… · reference analysis {review.reference_analysis_sha256.slice(0,16)}…</p>
    {review.claim_reviews.map(row=><p key={row.claim_id}>{project.brief.claims.find(c=>c.id===row.claim_id)?.text??row.claim_id}: <strong>{format(row.status)}</strong> {row.source_version&&' · source build '+row.source_version}</p>)}
    {!review.claim_reviews.length&&<p>No product claim has been attached; this remains a design study.</p>}
   </div>)}
   <p className="production-help">Owner selection: pending. This comparison does not commit a project revision or create a renderer grant.</p>
  </section>}
 </details>;
}
