import { useEffect,useRef,useState } from "react";
import "./evaluator-experiment.css";
import {GitCompareArrows,ShieldAlert,FileCheck2} from "lucide-react";
import type {Change,Project,Scene} from "./types";
import type {CreativeEvaluatorBundle} from "./creativeEvaluator";
import {
  previewEvaluatorExperiment,revalidateEvaluatorExperiment,
  type EvaluatorExperimentResult,
} from "./evaluatorExperiment";

interface ExperimentProps {
  project:Project;scene:Scene|null;selectedEvaluator:CreativeEvaluatorBundle|null;
  commit:(change:Change)=>Promise<void>;busy:boolean;
}
const desktop=()=>typeof window!=="undefined"&&"__TAURI_INTERNALS__" in window;
const errorText=(value:unknown)=>value instanceof Error?value.message:String(value);

export default function EvaluatorExperimentWorkbench({
  project,scene,selectedEvaluator,commit,busy
}:ExperimentProps){
  const key=[project.id,project.generation,project.revision,scene?.id??"",
    selectedEvaluator?.id??"",selectedEvaluator?.version??0].join(":");
  const current=useRef(key);current.current=key;
  const generation=useRef(0);
  const inflight=useRef(false);
  const [raw,setRaw]=useState("");
  const [selectedScenes,setSelectedScenes]=useState<string[]>(scene?[scene.id]:[]);
  const [pending,setPending]=useState<{key:string;result:EvaluatorExperimentResult}|null>(null);
  const [ackRights,setAckRights]=useState(false);
  const [ackLimit,setAckLimit]=useState(false);
  const [reviewerReason,setReviewerReason]=useState("");
  const [working,setWorking]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [notice,setNotice]=useState<string|null>(null);
  useEffect(()=>{
    generation.current++;
    setPending(null);setAckRights(false);setAckLimit(false);
    setWorking(false);setError(null);setNotice(null);
    setSelectedScenes(scene?[scene.id]:[]);
  },[key,scene?.id]);
  const reset=()=>{
    generation.current++;setPending(null);setAckRights(false);setAckLimit(false);
    setNotice(null);setError(null);
  };
  const prepare=()=>{
    if(!selectedEvaluator)return;
    const next={...structuredClone(selectedEvaluator),version:selectedEvaluator.version+1};
    reset();setRaw(JSON.stringify(next,null,2));
    setNotice("Edit criteria or editorial guidance before inspection. A version-number-only change is not an experiment.");
  };
  const toggleScene=(id:string,checked:boolean)=>{
    reset();
    setSelectedScenes(old=>checked?
      old.includes(id)?old:old.length>=3?old:[...old,id]:
      old.filter(item=>item!==id));
  };
  const preview=async()=>{
    if(busy||working||!selectedEvaluator||!raw.trim()||!selectedScenes.length)return;
    reset();setWorking(true);
    const token=++generation.current,sourceKey=key;
    try{
      const result=await previewEvaluatorExperiment(project,selectedEvaluator.id,
        selectedEvaluator.version,raw,selectedScenes);
      if(token!==generation.current || current.current!==sourceKey)return;
      setPending({key:sourceKey,result});
    }catch(cause){
      if(token===generation.current && current.current===sourceKey)setError(errorText(cause));
    }finally{
      if(token===generation.current && current.current===sourceKey)setWorking(false);
    }
  };
  const result=pending?.key===key?pending.result:null;
  const rationaleValid=reviewerReason.trim().length>=12 &&
    reviewerReason.trim().length<=400 && !/[\u0000-\u001f\u007f]/u.test(reviewerReason);
  const mayAdmit=!!(result&&desktop()&&!busy&&!working&&!inflight.current &&
    ackRights&&ackLimit&&rationaleValid);
  const admit=async()=>{
    if(!mayAdmit||!result||current.current!==key||inflight.current)return;
    inflight.current=true;setWorking(true);setNotice(null);setError(null);
    const token=++generation.current;
    try{
      const bundle=await revalidateEvaluatorExperiment(project,result);
      if(token!==generation.current||current.current!==key)
        throw new Error("Source changed during owner admission. Re-run the experiment.");
      await commit({type:"register_creative_evaluator",bundle});
      if(token===generation.current&&current.current===key){
        setPending(null);
        setNotice("Candidate registered through canonical project CAS. Artistic quality and identity were not authenticated.");
      }
    }catch(cause){
      if(token===generation.current&&current.current===key)setError(errorText(cause));
    }finally{
      inflight.current=false;
      if(token===generation.current&&current.current===key)setWorking(false);
    }
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
    {!selectedEvaluator?<div className="production-empty">
      <p>Register and select an original evaluator first; experiments require a real immutable baseline.</p>
    </div>:<>
      <div className="evaluator-experiment-scope">
        <span>Baseline · {selectedEvaluator.label} · v{selectedEvaluator.version}</span>
        <span>Project r{project.revision} · {project.generation.slice(0,8)}…</span>
      </div>
      <div className="production-button-row">
        <button className="secondary-button" type="button" disabled={working||busy}
          onClick={prepare}>Prepare a proposed v{selectedEvaluator.version+1} manifest</button>
      </div>
      <label className="production-field">
        <span>Proposed original recipe JSON (maximum 16 KiB)</span>
        <textarea aria-label="Evaluator experimental candidate JSON" rows={6}
          maxLength={16*1024} value={raw} spellCheck={false}
          onChange={event=>{setRaw(event.target.value);reset();}}
          disabled={working||busy} placeholder='{"schema":"motionwright.creative-evaluator/1", ...}'/>
      </label>
      <fieldset className="evaluator-experiment-scenes">
        <legend>Choose up to three existing scenes to compare</legend>
        {project.scenes.map(entry=><label key={entry.id}>
          <input type="checkbox" checked={selectedScenes.includes(entry.id)}
            disabled={busy||working||(!selectedScenes.includes(entry.id)&&selectedScenes.length>=3)}
            onChange={event=>toggleScene(entry.id,event.target.checked)}/>
          <span>{entry.name}</span>
        </label>)}
      </fieldset>
      <div className="production-button-row">
        <button className="primary-button" type="button"
          disabled={busy||working||!raw.trim()||!selectedScenes.length}
          onClick={()=>void preview()}><FileCheck2 size={14} aria-hidden="true"/>
          {working?"Inspecting bounded source…":"Run disposable technical comparison"}
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
        <div className="evaluator-experiment-approval">
          <label className="production-field"><span>Reason for choosing the candidate (local-only declaration)</span>
            <textarea rows={2} aria-label="Experiment owner review rationale"
              value={reviewerReason} maxLength={400} disabled={busy||working}
              onChange={event=>setReviewerReason(event.target.value)}/>
          </label>
          <label className="production-check"><input type="checkbox" checked={ackRights}
            disabled={busy||working} onChange={event=>setAckRights(event.target.checked)}/>
            I have permission to register this original, non-executable evaluator.
          </label>
          <label className="production-check"><input type="checkbox" checked={ackLimit}
            disabled={busy||working} onChange={event=>setAckLimit(event.target.checked)}/>
            I reviewed the per-scene technical changes, removed criteria and human abstentions.
            Registration does not approve artistic quality or external code.
          </label>
          <button className="primary-button" type="button" disabled={!mayAdmit}
            onClick={()=>void admit()}>
            {working?"Registering through project CAS…":`Register reviewed v${result.candidateVersion} via project CAS`}
          </button>
          {!desktop()&&<p className="production-help" role="status">
            Browser demo may compare local data but cannot claim a canonical desktop/Native SDK admission.
          </p>}
          <p className="production-help">
            The canonical journal stores the admitted evaluator version, not this disposable
            experiment preview or the local-only reason above. Re-run it for later source revisions.
          </p>
        </div>
      </section>}
    </>}
    {notice&&<p role="status" className="production-help">{notice}</p>}
    {error&&<p role="alert" className="production-error">{error}</p>}
  </section>;
}
