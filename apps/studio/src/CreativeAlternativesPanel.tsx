import { useMemo, useState } from "react";
import { ArrowLeftRight, GitCompareArrows, ShieldAlert } from "lucide-react";
import type { Change, Project } from "./types";
import { inspectCreativeAlternative } from "./creativeAlternativeEvidence";
import "./creative-alternatives.css";

type Commit=(change:Change)=>Promise<void>;
export default function CreativeAlternativesPanel({
  project,commit,
}:{
  project:Project;commit:Commit;
}) {
  const candidates=project.branches.filter(branch=>branch.id!==project.active_branch &&
    project.branch_workspaces.some(snapshot=>snapshot.branch_id===branch.id));
  const [selection,setSelection]=useState<string|null>(null);
  const selected=candidates.some(b=>b.id===selection)?selection:candidates[0]?.id??null;
  const [direction,setDirection]=useState<"positive"|"negative">("positive");
  const [rationale,setRationale]=useState("");
  const [working,setWorking]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [note,setNote]=useState<string|null>(null);
  const active=project.branches.find(b=>b.id===project.active_branch);
  const alternative=candidates.find(b=>b.id===selected);
  const evaluated=useMemo(()=>{
    if(!selected)return {report:null,error:null};
    try {return {report:inspectCreativeAlternative(project,selected),error:null};}
    catch(e){return {report:null,error:e instanceof Error?e.message:"The comparison cannot be assessed."};}
  },[project,selected]);
  const report=evaluated.report;
  const enabled=!!report && report.differences.length>0 && !working &&
    rationale.trim().length>=15 && rationale.trim().length<=1200;

  const record=async()=>{
    if(!enabled || !report || !alternative)return;
    setError(null);setNote(null);setWorking(true);
    const exactKeys=Object.entries(report.count_by_kind)
      .sort(([a],[b])=>a.localeCompare(b))
      .map(([kind,n])=>kind+":"+n).join(", ");
    const text=[
      "MANUAL "+direction.toUpperCase()+" CREATIVE ALTERNATIVE REVIEW",
      "No automatic preference, source quality, native render or reusable component distillation is claimed.",
      "Observed project: "+report.project_id,
      "Generation: "+report.generation,
      "Revision: "+report.observed_revision,
      "Compared active branch: "+(active?.name??"unknown")+" ("+report.active_branch_id+")",
      "Saved alternative: "+alternative.name+" ("+report.candidate_branch_id+")",
      "Alternative base/head: r"+report.candidate_base_revision+"/r"+report.candidate_head_revision,
      "Editable source differences: "+report.differences.length+" ("+exactKeys+")",
      "Human editorial reason: "+rationale.trim(),
    ].join("\n");
    try {
      await commit({
        type:"add_review",
        kind:"creative",
        resource:"project:"+project.id,
        body:text,
        start:null,
        end:null,
        locale:null,
        profile_id:null,
      });
      setRationale("");
      setNote("Manual "+direction+" study recorded as a new project revision. This is an open review, not an approved component or media artifact.");
    } catch(e){
      setError(e instanceof Error?e.message:"The revisioned review could not be recorded.");
    } finally {setWorking(false);}
  };
  return <section className="creative-alternatives" aria-label="Creative alternatives evidence">
    <header className="creative-alternatives-heading">
      <div><GitCompareArrows size={17} aria-hidden="true"/><div>
        <h3>Compare editable alternatives</h3>
        <p>Positive/negative studies come from explicit human judgement, not automatic ranking</p>
      </div></div>
      <span className="mono">project r{project.revision}</span>
    </header>
    <p className="creative-alternatives-intro">
      Compare your current branch against an already saved alternative.
      Text, source objects, camera, keyframes, scene timing and editorial plan are checked
      using stable identities. The review does not open a native renderer or determine
      which design is more effective.
    </p>
    {!candidates.length?<p className="creative-alternatives-empty" role="status">
      No saved alternative is available. Create a branch in Changes, edit its source,
      then return here to inspect its differences.
    </p>:<>
      <div className="creative-alternatives-chooser">
        <label><span className="field-label">Current branch</span>
          <strong>{active?.name??"Unknown current branch"}</strong></label>
        <ArrowLeftRight size={17} aria-hidden="true"/>
        <label><span className="field-label">Compare against saved branch</span>
          <select aria-label="Creative alternative branch" value={selected??""}
            onChange={e=>{setSelection(e.target.value);setError(null);setNote(null);}}>
            {candidates.map(b=><option value={b.id} key={b.id}>{b.name} · r{b.head_revision}</option>)}
          </select></label>
      </div>
      {evaluated.error?<p className="creative-alternatives-error" role="alert">
        Alternative not assessed: {evaluated.error}
      </p>:report&&<>
        <div className="creative-alternatives-counts" aria-label="Alternative source comparison counts">
          <div><span>Source scenes</span><strong>{report.active_scene_count} / {report.candidate_scene_count}</strong></div>
          <div><span>Editable objects</span><strong>{report.active_object_count} / {report.candidate_object_count}</strong></div>
          <div><span>Authoring changes</span><strong>{report.differences.length}</strong></div>
          <div><span>Render proof</span><strong>NOT TESTED</strong></div>
        </div>
        {report.differences.length?
          <div className="creative-alternatives-diffs" aria-label="Source alternative differences">
            {report.differences.slice(0,30).map((change,i)=><article key={change.kind+change.scene_id+change.node_id+i}>
              <strong>{change.kind.replaceAll("_"," ")}</strong>
              <p>{change.summary}</p>
              <span className="mono">{change.scene_id?
                "scene:"+change.scene_id.slice(0,8):"project"}
                {change.node_id?" · node:"+change.node_id.slice(0,8):""}</span>
            </article>)}
            {report.differences.length>30&&<p>
              {report.differences.length-30} additional changes were counted but not shown.
              Review the full branch before any human acceptance.
            </p>}
          </div>:<p className="creative-alternatives-empty">
            The two saved authoring states are structurally equivalent under the bounded
            properties inspected here. This does not mean their native pixels were compared.
          </p>}
      </>}
      {report&&report.differences.length>0&&<div className="creative-alternatives-decision">
        <div className="creative-alternatives-heading">
          <div><ShieldAlert size={17} aria-hidden="true"/><div>
            <h3>Record your observation</h3>
            <p>Creates a normal, open creative review anchored to this branch and revision</p>
          </div></div>
        </div>
        <div className="creative-alternatives-choice" role="group" aria-label="Manual variation direction">
          <button type="button" className={direction==="positive"?"active":""}
            aria-pressed={direction==="positive"} onClick={()=>setDirection("positive")}>
            Positive study
          </button>
          <button type="button" className={direction==="negative"?"active":""}
            aria-pressed={direction==="negative"} onClick={()=>setDirection("negative")}>
            Negative study
          </button>
        </div>
        <label className="creative-alternatives-reason"><span className="field-label">Human editorial rationale (15–1,200 characters)</span>
          <textarea aria-label="Alternative review rationale" value={rationale} maxLength={1200}
            rows={3} onChange={e=>setRationale(e.target.value)}
            placeholder="Which specific design decision works or fails, and what should be preserved?"/>
        </label>
        <button type="button" className="button button-primary" disabled={!enabled}
          onClick={()=>void record()}>
          {working?"Recording source review…":"Record manual "+direction+" observation"}
        </button>
        {error&&<p className="creative-alternatives-error" role="alert">{error}</p>}
        {note&&<p className="creative-alternatives-status" role="status">{note}</p>}
      </div>}
    </>}
    <p className="creative-alternatives-boundary">
      A saved positive study does not automatically distill a new component.
      Negative studies do not prohibit a branch by themselves. Source deltas
      are not native frame parity, quality evidence, user preference or rights validation.
      The canonical branch merge and Semwright Native SDK remain the only project mutation authority.
    </p>
  </section>;
}
