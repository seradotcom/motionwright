import { useState } from "react";
import type { Project, Change } from "./types";
import type { BrandRule, BrandProfile, TastePreference } from "./brandGovernance";
import { brandDigest } from "./brandGovernance";

type Props={project:Project; commit:(change:Change)=>Promise<void>; busy:boolean};
export default function BrandTasteWorkbench({project,commit,busy}:Props) {
  const design=project.production_design;
  const stored=design?.brand_profile;
  const [base,setBase]=useState(project.revision);
  const [brandName,setBrandName]=useState(stored?.label ?? "");
  const [forbidden,setForbidden]=useState(stored?.rules.filter(r=>r.kind==="forbidden_phrase").map(r=>r.phrase).join("\n") ?? "");
  const [accents,setAccents]=useState(stored?.rules.find(r=>r.kind==="allowed_accents")?.colors.join(", ") ?? "");
  const [wordmark,setWordmark]=useState(stored?.rules.find(r=>r.kind==="required_wordmark")?.text ?? "");
  const [tasteName,setTasteName]=useState(design?.taste_profile?.label ?? "");
  const [preferences,setPreferences]=useState<TastePreference[]>(design?.taste_profile?.preferences ?? []);
  const [sceneId,setSceneId]=useState(project.scenes[0]?.id ?? "");
  const [ruleId,setRuleId]=useState(stored?.rules[0]?.id ?? "");
  const [campaign,setCampaign]=useState("");
  const [author,setAuthor]=useState("");
  const [rationale,setRationale]=useState("");
  const [decision,setDecision]=useState("");
  const [error,setError]=useState<string|null>(null);
  const stale=base!==project.revision;
  const disabled=busy||stale;
  const reload=()=>{
    const d=project.production_design;const b=d?.brand_profile;
    setBrandName(b?.label??"");setForbidden(b?.rules.filter(r=>r.kind==="forbidden_phrase").map(r=>r.phrase).join("\n")??"");
    setAccents(b?.rules.find(r=>r.kind==="allowed_accents")?.colors.join(", ")??"");
    setWordmark(b?.rules.find(r=>r.kind==="required_wordmark")?.text??"");
    setTasteName(d?.taste_profile?.label??"");setPreferences(d?.taste_profile?.preferences??[]);
    setRuleId(b?.rules[0]?.id??"");setBase(project.revision);setError(null);
  };
  const submit=async(change:Change)=>{
    setError(null);
    try {await commit(change);setBase(project.revision+1);}
    catch(e){setError(e instanceof Error?e.message:String(e));}
  };
  const saveBrand=()=>{
    const rules:BrandRule[]=[];
    const old=stored?.rules??[];
    const idFor=(kind:BrandRule["kind"],text?:string)=>old.find(r=>r.kind===kind&&(text===undefined||(r.kind==="forbidden_phrase"&&r.phrase===text)))?.id??crypto.randomUUID();
    for(const phrase of forbidden.split("\n").map(v=>v.trim()).filter(Boolean))rules.push({kind:"forbidden_phrase",id:idFor("forbidden_phrase",phrase),phrase});
    const colors=accents.split(",").map(v=>v.trim()).filter(Boolean);
    if(colors.length) rules.push({kind:"allowed_accents",id:idFor("allowed_accents"),colors});
    if(wordmark.trim()) rules.push({kind:"required_wordmark",id:idFor("required_wordmark"),text:wordmark.trim()});
    const profile:BrandProfile={id:stored?.id??crypto.randomUUID(),label:brandName.trim(),version:(stored?.version??0)+1,rules};
    submit({type:"set_brand_governance",profile,exceptions:design?.brand_exceptions??[]});
  };
  const addException=async()=>{
    if(!stored) return;
    await submit({type:"set_brand_governance",profile:stored,exceptions:[
      ...(design?.brand_exceptions??[]),
      {id:crypto.randomUUID(),rule_id:ruleId||stored.rules[0]?.id||"",scene_id:sceneId,brand_sha256:await brandDigest(stored),
       campaign,author,rationale},
    ]});
  };
  return <div className="production-plan" aria-label="Brand and taste governance">
    <div className="production-section-title"><div><h2>Brand rules and creative taste</h2>
      <p>Brand constraints are mandatory. Taste is advisory. Campaign exceptions have one scene, one rule and one policy digest.</p></div></div>
    {stale&&<div className="production-notice" role="status">The project changed since this draft. <button className="secondary-button" onClick={reload}>Reload governing state</button></div>}
    {error&&<p className="production-error" role="alert">{error}</p>}
    <section className="production-approval">
      <h3>BrandProfile · enforced in authored scenes</h3>
      <div className="production-plan-grid">
        <label className="production-field"><span>Brand identity</span><input value={brandName} onChange={e=>setBrandName(e.target.value)}/></label>
        <label className="production-field"><span>Allowed accent colors (#RRGGBB, comma separated)</span><input value={accents} onChange={e=>setAccents(e.target.value)}/></label>
        <label className="production-field"><span>Required hero wordmark (optional rule)</span><input value={wordmark} onChange={e=>setWordmark(e.target.value)}/></label>
        <label className="production-field"><span>Forbidden copy phrases (one per line)</span><textarea rows={4} value={forbidden} onChange={e=>setForbidden(e.target.value)}/></label>
      </div>
      <p className="production-help">Saving a changed policy advances its version. Existing exceptions require explicit revocation/re-approval because their policy digest becomes stale. The recorded author is not authenticated by this panel.</p>
      <div className="production-button-row">
        <button className="primary-button" disabled={disabled} onClick={saveBrand}>Save brand policy</button>
        <button className="secondary-button" disabled={disabled||!(design?.brand_exceptions?.length)} onClick={()=>submit({type:"set_brand_governance",profile:stored??null,exceptions:[]})}>Revoke campaign exceptions</button>
      </div>
    </section>
    <section className="production-approval">
      <h3>Campaign exceptions · scope cannot escape one scene</h3>
      {design?.brand_exceptions?.map(e=><div className="production-button-row" key={e.id}>
        <span className="mono">{e.campaign} · {project.scenes.find(s=>s.id===e.scene_id)?.name} · rule {e.rule_id.slice(0,8)} · {e.author}</span>
        <button className="secondary-button" disabled={disabled||!stored} onClick={()=>submit({type:"set_brand_governance",profile:stored??null,exceptions:(design?.brand_exceptions??[]).filter(v=>v.id!==e.id)})}>Revoke</button>
      </div>)}
      <div className="production-plan-grid">
        <label className="production-field"><span>Scene scope</span><select value={sceneId} onChange={e=>setSceneId(e.target.value)}>{project.scenes.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        <label className="production-field"><span>Brand rule</span><select value={ruleId||stored?.rules[0]?.id||""} onChange={e=>setRuleId(e.target.value)}>{stored?.rules.map(r=><option key={r.id} value={r.id}>{r.kind} · {r.id.slice(0,8)}</option>)}</select></label>
        <label className="production-field"><span>Campaign</span><input value={campaign} onChange={e=>setCampaign(e.target.value)}/></label>
        <label className="production-field"><span>Recorded author</span><input value={author} onChange={e=>setAuthor(e.target.value)}/></label>
        <label className="production-field"><span>Reason for exception</span><textarea rows={2} value={rationale} onChange={e=>setRationale(e.target.value)}/></label>
      </div>
      <button className="secondary-button" disabled={disabled||!stored||!sceneId||!campaign.trim()||!author.trim()||!rationale.trim()} onClick={addException}>Record scene-scoped exception</button>
    </section>
    <section className="production-approval">
      <h3>TasteProfile · preferences cannot override brand rules</h3>
      <label className="production-field"><span>Taste label</span><input value={tasteName} onChange={e=>setTasteName(e.target.value)}/></label>
      {preferences.map((pref,i)=><div className="production-plan-grid" key={i}>
        <label className="production-field"><span>Axis</span><input value={pref.axis} onChange={e=>setPreferences(v=>v.map((p,j)=>j===i?{...p,axis:e.target.value}:p))}/></label>
        <label className="production-field"><span>Preference</span><input value={pref.preference} onChange={e=>setPreferences(v=>v.map((p,j)=>j===i?{...p,preference:e.target.value}:p))}/></label>
        <label><input type="checkbox" checked={pref.inferred} onChange={e=>setPreferences(v=>v.map((p,j)=>j===i?{...p,inferred:e.target.checked}:p))}/> Inferred, not reviewed</label>
        <button className="secondary-button" disabled={disabled} onClick={()=>setPreferences(v=>v.filter((_,j)=>j!==i))}>Remove</button>
      </div>)}
      <div className="production-button-row">
        <button className="secondary-button" disabled={disabled||preferences.length>=64} onClick={()=>setPreferences(v=>[...v,{axis:"",preference:"",inferred:true}])}>Add preference</button>
        <button className="primary-button" disabled={disabled} onClick={()=>submit({type:"set_taste_profile",profile:{id:design?.taste_profile?.id??crypto.randomUUID(),label:tasteName,preferences}})}>Save taste</button>
      </div>
    </section>
    <section className="production-approval">
      <h3>CreativeDecision · project-scoped record</h3>
      <p className="production-help">A recorded decision documents who claimed a choice and why. It cannot waive a brand rule or approve external asset rights.</p>
      {(design?.project_decisions??[]).slice(-8).map(v=><p key={v.id}><strong>{v.author}:</strong> {v.decision} <small>({project.scenes.find(s=>s.id===v.scene_id)?.name})</small></p>)}
      <label className="production-field"><span>Decision</span><textarea rows={2} value={decision} onChange={e=>setDecision(e.target.value)}/></label>
      <button className="secondary-button" disabled={disabled||!sceneId||!author.trim()||!rationale.trim()||!decision.trim()} onClick={()=>submit({type:"record_creative_decision",decision:{id:crypto.randomUUID(),scene_id:sceneId,author,decision,rationale}})}>Record decision for selected scene</button>
    </section>
  </div>;
}
