import "./evaluator-experiment.css";
import {GitCompareArrows,ShieldAlert,FileCheck2} from "lucide-react";
import type {Change,Project,Scene} from "./types";
import type {CreativeEvaluatorBundle} from "./creativeEvaluator";
import {experimentScope,experimentSelectedScenes} from "./evaluatorExperimentDraft";
import {ExperimentDraftFeedback,useEvaluatorExperimentDraft} from "./useEvaluatorExperimentDraft";

interface ExperimentProps {
  project:Project;scene:Scene|null;selectedEvaluator:CreativeEvaluatorBundle|null;
  commit:(change:Change)=>Promise<Project>;pinBaseline:()=>void;busy:boolean;registrationBlocked:boolean;
}
const desktop=()=>typeof window!=="undefined"&&"__TAURI_INTERNALS__" in window;
export default function EvaluatorExperimentWorkbench(props:ExperimentProps){
  if(!props.selectedEvaluator)return <section className="evaluator-experiment" aria-label="Evaluator candidate experiment">
    <h3>Experiment before admission</h3>
    <p>Register and select an original evaluator first; experiments require a real immutable baseline.</p>
  </section>;
  return <ExperimentEditor key={experimentScope(props.project,props.selectedEvaluator)}
    {...props} selectedEvaluator={props.selectedEvaluator}/>;
}
function ExperimentEditor({project,scene,selectedEvaluator,commit,pinBaseline,busy,registrationBlocked}:Omit<ExperimentProps,"selectedEvaluator">&{selectedEvaluator:CreativeEvaluatorBundle}){
  const editor=useEvaluatorExperimentDraft(project,selectedEvaluator,scene?.id??null,commit,pinBaseline,busy,registrationBlocked);
  const raw=editor.raw("candidateText"),reviewerReason=editor.raw("reviewerReason");
  const ackRights=editor.raw("ackRights")==="true",ackLimit=editor.raw("ackLimit")==="true";
  const result=editor.comparison?.result??null,working=editor.working||editor.comparing;
  let selectedScenes:string[]=[],selectionError:string|null=null;
  try{selectedScenes=experimentSelectedScenes(editor.raw("selectedScenes"));}
  catch(reason){selectionError=reason instanceof Error?reason.message:String(reason);}
  const missingScenes=selectedScenes.filter(id=>!project.scenes.some(value=>value.id===id));
  const rationaleValid=reviewerReason.trim().length>=12&&reviewerReason.trim().length<=400&&!/[\u0000-\u001f\u007f]/u.test(reviewerReason);
  const mayAdmit=!!(result&&desktop()&&!editor.blocked&&!editor.comparing&&ackRights&&ackLimit&&rationaleValid&&!selectionError&&!missingScenes.length);
  const clearConsent=()=>{editor.edit("ackRights","false");editor.edit("ackLimit","false");};
  const prepare=()=>{
    if(working)return;
    const next={...structuredClone(selectedEvaluator),version:selectedEvaluator.version+1};
    editor.edit("candidateText",JSON.stringify(next,null,2));clearConsent();
  };
  const toggleScene=(id:string,checked:boolean)=>{
    if(selectionError)return;
    const values=checked?selectedScenes.includes(id)?selectedScenes:selectedScenes.length>=3?selectedScenes:[...selectedScenes,id]
      :selectedScenes.filter(value=>value!==id);
    editor.edit("selectedScenes",JSON.stringify(values));clearConsent();
  };
  return <section className="evaluator-experiment" aria-label="Evaluator candidate experiment">
    <header className="production-section-title">
      <GitCompareArrows size={18} aria-hidden="true"/>
      <div>
        <h3>Experiment before admission</h3>
        <p>Compare a new data-only recipe against a registered version on up to three original scenes.</p>
      </div>
    </header>
    <p className="production-help">
      The current evaluator never executes downloaded instructions. This local experiment
      evaluates the exact existing typed criteria only. Its technical observations are
      not a creative-quality grade, identity proof, signed extension or verified package.
    </p>
    <ExperimentDraftFeedback editor={editor}/>
    {registrationBlocked&&<p role="status" className="production-help">Resolve the earlier evaluator author-input recovery before registering an experiment. Experimental input and recovery remain available.</p>}
    <>
      <div className="evaluator-experiment-scope">
        <span>Baseline · {selectedEvaluator.label} · v{selectedEvaluator.version}</span>
        <span>Project r{project.revision} · {project.generation.slice(0,8)}…</span>
      </div>
      <div className="production-button-row">
        <button className="secondary-button" type="button" disabled={working}
          onClick={prepare}>Prepare a proposed v{selectedEvaluator.version+1} manifest</button>
      </div>
      <label className="production-field">
        <span>Proposed original recipe JSON (maximum 16 KiB)</span>
        <textarea aria-label="Evaluator experimental candidate JSON" rows={6}
          maxLength={16*1024} value={raw} spellCheck={false}
          onChange={event=>{editor.edit("candidateText",event.target.value);clearConsent();}}
          placeholder='{"schema":"motionwright.creative-evaluator/1", ...}'/>
      </label>
      {(selectionError||missingScenes.length>0)&&<div role="alert">
        <p>{selectionError??"Retained scene choices include unavailable source IDs; none were silently removed."}</p>
        <button className="secondary-button" disabled={!scene}
          onClick={()=>{editor.edit("selectedScenes",JSON.stringify(scene?[scene.id]:[]));clearConsent();}}>Use current working scene</button>
      </div>}
      <fieldset className="evaluator-experiment-scenes">
        <legend>Choose up to three existing scenes to compare</legend>
        {project.scenes.map(entry=><label key={entry.id}>
          <input type="checkbox" checked={selectedScenes.includes(entry.id)}
            disabled={!!selectionError||(!selectedScenes.includes(entry.id)&&selectedScenes.length>=3)}
            onChange={event=>toggleScene(entry.id,event.target.checked)}/>
          <span>{entry.name}</span>
        </label>)}
      </fieldset>
      <div className="production-button-row">
        <button className="primary-button" type="button"
          disabled={working||!raw.trim()||!selectedScenes.length||!!selectionError||missingScenes.length>0}
          onClick={()=>void editor.compare()}><FileCheck2 size={14} aria-hidden="true"/>
          {editor.comparing?"Inspecting bounded source…":"Run disposable technical comparison"}
        </button>
        <span className="production-help">Read-only: no new project revision, provider call or skill installation.</span>
      </div>
      {result&&<section className="evaluator-experiment-results" aria-label="Source-bound evaluator experiment">
        <header>
          <strong>Technical comparison · {result.comparisons.length} source scene(s)</strong>
          <span>r{result.revision} · {result.baselineSha256.slice(0,12)}… → {result.candidateSha256.slice(0,12)}…</span>
        </header>
        <table aria-label="Technical outcome counts by scene">
          <thead><tr><th scope="col">Scene</th><th scope="col">Baseline pass/fail/abstain</th>
            <th scope="col">Candidate pass/fail/abstain</th><th scope="col">Existing criteria regressed</th></tr></thead>
          <tbody>{result.comparisons.map(row=><tr key={row.sceneId}>
            <th scope="row">{row.sceneName}</th>
            <td>{row.baselinePass} / {row.baselineFail} / {row.baselineAbstain}</td>
            <td>{row.candidatePass} / {row.candidateFail} / {row.candidateAbstain}</td>
            <td>{row.matchingCriterionRegressions}</td>
          </tr>)}</tbody>
        </table>
        <p className="production-help">
          {result.addedCriterionIds.length} added criterion ID(s),
          {result.removedCriterionIds.length} removed criterion ID(s). Dropping a failed
          criterion is not an improvement. Human questions always remain not assessed.
        </p>
        <div className="production-notice">
          <ShieldAlert size={14} aria-hidden="true"/>
          <div><strong>Not a sandbox for executable code</strong>
            <span>This experiment never installs or runs scripts. Its outcome is technical-only;
              source rights, creative approval and external reviewer identity are NOT VERIFIED.</span></div>
        </div>
      </section>}
      <div className="evaluator-experiment-approval">
          <label className="production-field"><span>Reason for choosing the candidate (local-only declaration)</span>
            <textarea rows={2} aria-label="Experiment owner review rationale"
              value={reviewerReason} maxLength={400}
              onChange={event=>editor.edit("reviewerReason",event.target.value)}/>
          </label>
          <label className="production-check"><input type="checkbox" checked={ackRights}
            onChange={event=>editor.edit("ackRights",String(event.target.checked))}/>
            I have permission to register this original, non-executable evaluator.
          </label>
          <label className="production-check"><input type="checkbox" checked={ackLimit}
            onChange={event=>editor.edit("ackLimit",String(event.target.checked))}/>
            I reviewed the per-scene technical changes, removed criteria and human abstentions.
            Registration does not approve artistic quality or external code.
          </label>
          <button className="primary-button" type="button" disabled={!mayAdmit}
            onClick={()=>void editor.submit()}>
            {editor.working?"Registering through project CAS…":`Register reviewed v${result?.candidateVersion??selectedEvaluator.version+1} via project CAS`}
          </button>
          {!desktop()&&<p className="production-help" role="status">
            Browser demo may compare local data but cannot claim a canonical desktop/Native SDK admission.
          </p>}
          <p className="production-help">
            The canonical journal stores the admitted evaluator version, not this disposable
            experiment preview or the local-only reason above. Re-run it for later source revisions.
          </p>
      </div>
    </>
  </section>;
}
