import { useEffect, useMemo, useState } from "react";
import { FileCheck2, RefreshCw, Save } from "lucide-react";
import type { Change, Project, Scene } from "./types";
import { EVALUATOR_SCHEMA, validateEvaluator } from "./creativeEvaluator";
import type { CreativeCriterion, CreativeEvaluatorBundle } from "./creativeEvaluator";

type Props={
  project:Project;scene:Scene|null;commit:(change:Change)=>Promise<void>;
  busy:boolean;onOpenCanvas:()=>void;
};
const explanation=(error:unknown)=>error instanceof Error?error.message:String(error);
const defaultHumanQuestion="Does this scene communicate its intended idea clearly to a human viewer?";

export default function CreativeEvaluatorWorkbench({project,scene,commit,busy,onOpenCanvas}:Props){
  const bundles=project.production_design?.evaluator_bundles??[];
  const reports=project.production_design?.evaluation_reports??[];
  const [label,setLabel]=useState("Original scene evaluation");
  const [purpose,setPurpose]=useState("Review observable editable structure and request independent human feedback");
  const [author,setAuthor]=useState("");
  const [steps,setSteps]=useState("Inspect original authored scene elements.\nConfirm technical constraints without claiming aesthetic approval.");
  const [minimum,setMinimum]=useState("2");
  const [requiredText,setRequiredText]=useState("");
  const [safeMargin,setSafeMargin]=useState("50");
  const [humanQuestion,setHumanQuestion]=useState(defaultHumanQuestion);
  const [selected,setSelected]=useState("");
  const [base,setBase]=useState(project.revision);
  const [error,setError]=useState<string|null>(null);
  const [importText,setImportText]=useState("");
  const [confirmImport,setConfirmImport]=useState(false);
  const [transferStatus,setTransferStatus]=useState<string|null>(null);
  const stale=base!==project.revision;
  const locked=project.locks.some(lock=>
    ["project:"+project.id,"scene:"+scene?.id].includes(lock.resource)&&lock.kind==="content");
  const latest=useMemo(()=>bundles.find(bundle=>bundle.id+":"+bundle.version===selected)??null,
    [bundles,selected]);
  const selectedReports=reports.filter(report=>report.scene_id===scene?.id).slice(-8).reverse();

  useEffect(()=>{
    if(bundles.length && !selected)setSelected(bundles[bundles.length-1].id+":"+bundles[bundles.length-1].version);
  },[bundles,selected]);
  // Switching projects or generations drops revision-sensitive local controls.
  useEffect(()=>{
    setSelected("");setBase(project.revision);setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project.generation]);

  function createBundle():CreativeEvaluatorBundle{
    const criteria:CreativeCriterion[]=[
      {kind:"minimum_nodes",id:crypto.randomUUID(),minimum:Number(minimum)},
    ];
    if(requiredText.trim())criteria.push({kind:"text_contains",id:crypto.randomUUID(),phrase:requiredText.trim()});
    criteria.push({kind:"project_pixel_safe_area",id:crypto.randomUUID(),margin_permille:Number(safeMargin)});
    criteria.push({kind:"human_question",id:crypto.randomUUID(),question:humanQuestion.trim()});
    return {
      schema:EVALUATOR_SCHEMA,id:crypto.randomUUID(),version:1,
      label:label.trim(),purpose:purpose.trim(),author_attestation:author.trim(),
      playbook:steps.split("\n").map(line=>line.trim()).filter(Boolean),
      criteria,
    };
  }
  const preflight=(()=>{
    try{validateEvaluator({
      schema:EVALUATOR_SCHEMA,id:"00000000-0000-4000-8000-000000000006",
      version:1,label,purpose,author_attestation:author,
      playbook:steps.split("\n").map(line=>line.trim()).filter(Boolean),
      criteria:[
        {kind:"minimum_nodes",id:"00000000-0000-4000-8000-000000000007",minimum:Number(minimum)},
        {kind:"project_pixel_safe_area",id:"00000000-0000-4000-8000-000000000008",margin_permille:Number(safeMargin)},
        {kind:"human_question",id:"00000000-0000-4000-8000-000000000009",question:humanQuestion},
        ...(requiredText.trim()?[{kind:"text_contains" as const,id:"00000000-0000-4000-8000-000000000010",phrase:requiredText}]:[]),
      ],
    });return null;}catch(e){return explanation(e);}
  })();
  const enabled=!busy&&!stale&&!locked;
  const submit=async()=>{
    if(!enabled||preflight)return;
    setError(null);
    try{
      const bundle=createBundle();
      await commit({type:"register_creative_evaluator",bundle});
      setSelected(bundle.id+":1");
      setBase(project.revision+1);
    }catch(e){setError(explanation(e));}
  };
  const importManifest=async()=>{
    if(!enabled||!confirmImport)return;
    setError(null);setTransferStatus(null);
    try{
      if(new TextEncoder().encode(importText).length>16*1024)
        throw new Error("Evaluator import exceeds bounded JSON budget.");
      const bundle=JSON.parse(importText) as CreativeEvaluatorBundle;
      validateEvaluator(bundle);
      const latestVersion=Math.max(0,...bundles.filter(v=>v.id===bundle.id).map(v=>v.version));
      if(bundle.version!==latestVersion+1)
        throw new Error("Manifest version must follow locally recorded source history.");
      await commit({type:"register_creative_evaluator",bundle});
      setSelected(bundle.id+":"+bundle.version);setBase(project.revision+1);
      setImportText("");setConfirmImport(false);setTransferStatus("Data-only manifest registered. Source rights remain author-declared.");
    }catch(e){setError(explanation(e));}
  };
  const copyManifest=async()=>{
    if(!latest)return;
    setTransferStatus(null);
    try{await navigator.clipboard.writeText(JSON.stringify(latest,null,2));
      setTransferStatus("Versioned data-only JSON copied.");
    }catch(e){setError(explanation(e));}
  };
  const evaluate=async()=>{
    if(!enabled||!latest||!scene)return;
    setError(null);
    try{
      await commit({type:"evaluate_creative_scene",
        evaluator_id:latest.id,evaluator_version:latest.version,scene_id:scene.id});
      setBase(project.revision+1);
    }catch(e){setError(explanation(e));}
  };
  return <div className="production-plan" aria-label="Creative evaluation playbooks">
    <div className="production-section-title">
      <FileCheck2 size={18}/>
      <div><h2>Creative evaluation</h2>
        <p>Versioned original playbooks · technical proof is not aesthetic approval</p></div>
    </div>
    <p className="production-help">
      Record a bounded, data-only evaluation recipe and apply it to an editable scene.
      This does not execute a skill, install a provider, grade artistic quality or
      approve third-party rights. Human questions always remain not assessed.
    </p>
    {stale && <div className="production-notice" role="status">
      <strong>Source revision {base}; project is now {project.revision}.</strong>
      <span>Reconcile the project before registering or evaluating again.</span>
      <button className="secondary-button" onClick={()=>{setBase(project.revision);setError(null);}}>
        <RefreshCw size={14}/> Reload revision</button>
    </div>}
    <div className="production-section-title"><h3>Author reusable criteria</h3></div>
    <div className="production-plan-grid">
      <label className="production-field"><span>Evaluator label</span>
        <input aria-label="Evaluator label" value={label} maxLength={100}
          onChange={e=>setLabel(e.target.value)}/></label>
      <label className="production-field"><span>Purpose</span>
        <input aria-label="Evaluator purpose" value={purpose} maxLength={280}
          onChange={e=>setPurpose(e.target.value)}/></label>
    </div>
    <label className="production-field"><span>Owner's original-content declaration</span>
      <textarea aria-label="Evaluator author declaration" rows={2} value={author} maxLength={240}
        placeholder="State why you are authorized to publish this original checklist"
        onChange={e=>setAuthor(e.target.value)}/></label>
    <label className="production-field"><span>Editorial playbook (one data-only instruction per line)</span>
      <textarea aria-label="Evaluator playbook" rows={3} value={steps}
        onChange={e=>setSteps(e.target.value)}/></label>
    <div className="production-plan-grid">
      <label className="production-field"><span>Minimum editable objects</span>
        <input aria-label="Evaluator minimum nodes" type="number" min={1} max={256}
          value={minimum} onChange={e=>setMinimum(e.target.value)}/></label>
      <label className="production-field"><span>Safe-area margin (per thousand, max 150)</span>
        <input aria-label="Evaluator safe-area margin" type="number" min={0} max={150}
          value={safeMargin} onChange={e=>setSafeMargin(e.target.value)}/></label>
    </div>
    <label className="production-field"><span>Required visible editable text (optional)</span>
      <input aria-label="Evaluator required text" maxLength={120} value={requiredText}
        onChange={e=>setRequiredText(e.target.value)}/></label>
    <label className="production-field"><span>Question for a human creative reviewer</span>
      <textarea aria-label="Evaluator human question" maxLength={250} rows={2}
        value={humanQuestion} onChange={e=>setHumanQuestion(e.target.value)}/></label>
    {preflight && <p role="status" className="production-help">Recipe validation: {preflight}</p>}
    <button className="primary-button" disabled={!enabled||!!preflight} onClick={()=>void submit()}>
      <Save size={14}/> Register data-only evaluator
    </button>
    <div className="production-section-title"><h3>Reuse an exact original evaluator</h3></div>
    <p className="production-help">Copy a versioned JSON recipe or import an author-authorized data-only
      manifest. Import never runs instructions or installs execution capabilities.
      A new project starts with version one; updates retain historical source versions.</p>
    <label className="production-field"><span>Versioned evaluator JSON</span>
      <textarea aria-label="Import evaluator JSON" rows={4} value={importText}
        onChange={e=>setImportText(e.target.value)} spellCheck={false}/>
    </label>
    <label className="production-check">
      <input aria-label="Confirm evaluator source rights" type="checkbox" checked={confirmImport}
        onChange={e=>setConfirmImport(e.target.checked)}/>
      I have permission to use this original data-only evaluator.
    </label>
    <button className="secondary-button" disabled={!enabled||!importText.trim()||!confirmImport}
      onClick={()=>void importManifest()}>Import original evaluator JSON</button>
    <button className="secondary-button" disabled={!latest}
      onClick={()=>void copyManifest()}>Copy selected evaluator JSON</button>
    {transferStatus && <p role="status" className="production-help">{transferStatus}</p>}
    <div className="production-section-title"><h3>Evaluate current scene</h3></div>
    <label className="production-field"><span>Immutable evaluator version</span>
      <select aria-label="Evaluator source version" value={selected} onChange={e=>setSelected(e.target.value)}>
        <option value="">Select registered evaluator version</option>
        {bundles.map(bundle=><option value={bundle.id+":"+bundle.version}
          key={bundle.id+":"+bundle.version}>{bundle.label} · v{bundle.version}</option>)}
      </select></label>
    <p className="production-help">
      Selected scene: {scene?.name??"none"}. This produces a source-revision-bound
      technical report only; ask a human to assess the creative question separately.
    </p>
    <button className="primary-button" disabled={!enabled||!scene||!latest}
      onClick={()=>void evaluate()}>
      <FileCheck2 size={14}/> Evaluate authored scene
    </button>
    <button className="secondary-button" disabled={!scene} onClick={onOpenCanvas}>
      Inspect original Canvas objects
    </button>
    {error && <p role="alert" className="production-error">{error}</p>}
    <div className="production-section-title"><h3>Source-bound observations</h3></div>
    {selectedReports.length?selectedReports.map(report=><article key={report.id} className="production-evidence-note">
      <strong>{report.conclusion==="fail"?"Technical constraints failed":
        report.conclusion==="needs_human_review"?"Needs human creative review":
        "Technical checks passed only"}</strong>
      <p>Source revision {report.source_revision} · evaluator version {report.evaluator_version} ·
        human creative approval: {report.creative_approval}</p>
      <ul>{report.observations.map(result=><li key={result.criterion_id}>
        {result.outcome==="not_assessed"?"Not assessed":
          result.outcome==="pass"?"Technical pass":"Technical fail"}: {result.explanation}</li>)}</ul>
    </article>):<p className="production-help">No evaluator reports for this scene.</p>}
  </div>;
}
