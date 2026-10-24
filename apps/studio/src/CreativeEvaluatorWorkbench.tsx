import { useEffect, useMemo, useState } from "react";
import { FileCheck2, Save } from "lucide-react";
import EvaluatorExperimentWorkbench from "./EvaluatorExperimentWorkbench";
import { EvaluatorDraftFeedback, useCreativeEvaluatorDraft } from "./useCreativeEvaluatorDraft";
import type { Change, Project, Scene } from "./types";
import { EVALUATOR_SCHEMA, validateEvaluator } from "./creativeEvaluator";

type Props={
  project:Project;scene:Scene|null;commit:(change:Change)=>Promise<Project>;
  busy:boolean;onOpenCanvas:()=>void;
};
const boundedInteger=(raw:string)=>{
  const value=raw.trim();
  return value&&/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value)
    &&Number.isSafeInteger(Number(value))?Number(value):Number.NaN;
};

export default function CreativeEvaluatorWorkbench({project,scene,commit,busy,onOpenCanvas}:Props){
  const editor=useCreativeEvaluatorDraft(project,commit,busy);
  const bundles=project.production_design?.evaluator_bundles??[];
  const reports=project.production_design?.evaluation_reports??[];
  const label=editor.raw("label"),purpose=editor.raw("purpose"),author=editor.raw("author");
  const steps=editor.raw("steps"),minimum=editor.raw("minimum"),requiredText=editor.raw("requiredText");
  const safeMargin=editor.raw("safeMargin"),humanQuestion=editor.raw("humanQuestion");
  const selected=editor.raw("selected"),importText=editor.raw("importText");
  const confirmImport=editor.raw("confirmImport")==="true";
  const [transferStatus,setTransferStatus]=useState<string|null>(null);
  const locked=project.locks.some(lock=>
    ["project:"+project.id,"scene:"+scene?.id].includes(lock.resource)&&lock.kind==="content");
  const latest=useMemo(()=>bundles.find(bundle=>bundle.id+":"+bundle.version===selected)??null,
    [bundles,selected]);
  const selectedReports=reports.filter(report=>report.scene_id===scene?.id).slice(-8).reverse();
  useEffect(()=>{setTransferStatus(null);},[project.id,project.generation,project.active_branch]);

  // Validation-only IDs never enter a submitted change. The original request
  // receives its stable IDs once, when the retained input is explicitly sent.
  const preflight=(()=>{
    try{validateEvaluator({
      schema:EVALUATOR_SCHEMA,id:"00000000-0000-4000-8000-000000000006",
      version:1,label,purpose,author_attestation:author,
      playbook:steps.split("\n").map(line=>line.trim()).filter(Boolean),
      criteria:[
        {kind:"minimum_nodes",id:"00000000-0000-4000-8000-000000000007",minimum:boundedInteger(minimum)},
        {kind:"project_pixel_safe_area",id:"00000000-0000-4000-8000-000000000008",margin_permille:boundedInteger(safeMargin)},
        {kind:"human_question",id:"00000000-0000-4000-8000-000000000009",question:humanQuestion},
        ...(requiredText.trim()?[{kind:"text_contains" as const,id:"00000000-0000-4000-8000-000000000010",phrase:requiredText}]:[]),
      ],
    });return null;}catch(reason){return reason instanceof Error?reason.message:String(reason);}
  })();
  const enabled=!editor.blocked&&!locked;
  const submit=async()=>{if(enabled&&!preflight)await editor.submit({kind:"author"});};
  const importManifest=async()=>{
    if(enabled&&confirmImport){setTransferStatus(null);await editor.submit({kind:"import"});}
  };
  const copyManifest=async()=>{
    if(!latest)return;
    setTransferStatus(null);
    try{await navigator.clipboard.writeText(JSON.stringify(latest,null,2));
      setTransferStatus("Versioned data-only JSON copied.");
    }catch(reason){editor.reportError(reason);}
  };
  const evaluate=async()=>{
    if(enabled&&latest&&scene)await editor.submit({kind:"evaluate",sceneId:scene.id});
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
    <EvaluatorDraftFeedback editor={editor}/>
    <div className="production-section-title"><h3>Author reusable criteria</h3></div>
    <div className="production-plan-grid">
      <label className="production-field"><span>Evaluator label</span>
        <input aria-label="Evaluator label" value={label} maxLength={100}
          onChange={e=>editor.edit("label",e.target.value)}/></label>
      <label className="production-field"><span>Purpose</span>
        <input aria-label="Evaluator purpose" value={purpose} maxLength={280}
          onChange={e=>editor.edit("purpose",e.target.value)}/></label>
    </div>
    <label className="production-field"><span>Owner's original-content declaration</span>
      <textarea aria-label="Evaluator author declaration" rows={2} value={author} maxLength={240}
        placeholder="State why you are authorized to publish this original checklist"
        onChange={e=>editor.edit("author",e.target.value)}/></label>
    <label className="production-field"><span>Editorial playbook (one data-only instruction per line)</span>
      <textarea aria-label="Evaluator playbook" rows={3} value={steps}
        onChange={e=>editor.edit("steps",e.target.value)}/></label>
    <div className="production-plan-grid">
      <label className="production-field"><span>Minimum editable objects</span>
        <input aria-label="Evaluator minimum nodes" type="text" inputMode="numeric"
          value={minimum} onChange={e=>editor.edit("minimum",e.target.value)}/></label>
      <label className="production-field"><span>Safe-area margin (per thousand, max 150)</span>
        <input aria-label="Evaluator safe-area margin" type="text" inputMode="numeric"
          value={safeMargin} onChange={e=>editor.edit("safeMargin",e.target.value)}/></label>
    </div>
    <label className="production-field"><span>Required visible editable text (optional)</span>
      <input aria-label="Evaluator required text" maxLength={120} value={requiredText}
        onChange={e=>editor.edit("requiredText",e.target.value)}/></label>
    <label className="production-field"><span>Question for a human creative reviewer</span>
      <textarea aria-label="Evaluator human question" maxLength={250} rows={2}
        value={humanQuestion} onChange={e=>editor.edit("humanQuestion",e.target.value)}/></label>
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
        onChange={e=>editor.edit("importText",e.target.value)} spellCheck={false}/>
    </label>
    <label className="production-check">
      <input aria-label="Confirm evaluator source rights" type="checkbox" checked={confirmImport}
        onChange={e=>editor.edit("confirmImport",String(e.target.checked))}/>
      I have permission to use this original data-only evaluator.
    </label>
    <button className="secondary-button" disabled={!enabled||!importText.trim()||!confirmImport}
      onClick={()=>void importManifest()}>Import original evaluator JSON</button>
    <button className="secondary-button" disabled={!latest}
      onClick={()=>void copyManifest()}>Copy selected evaluator JSON</button>
    {transferStatus && <p role="status" className="production-help">{transferStatus}</p>}
    <div className="production-section-title"><h3>Evaluate current scene</h3></div>
    <label className="production-field"><span>Immutable evaluator version</span>
      <select aria-label="Evaluator source version" value={selected} onChange={e=>editor.edit("selected",e.target.value)}>
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
    <EvaluatorExperimentWorkbench project={project} scene={scene} selectedEvaluator={latest}
      busy={busy||editor.working}
      registrationBlocked={!!editor.session.draft.pending||!!editor.session.backupError||!!editor.session.failedSource}
      pinBaseline={()=>{if(selected&&editor.session.draft.fields.selected?.raw!==selected)editor.edit("selected",selected);}}
      commit={commit}/>
  </div>;
}
