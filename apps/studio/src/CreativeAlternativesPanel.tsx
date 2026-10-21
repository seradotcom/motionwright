import { useMemo, useState } from "react";
import { ArrowLeftRight, GitCompareArrows, ShieldAlert } from "lucide-react";
import type { Change, Project } from "./types";
import { inspectCreativeAlternative } from "./creativeAlternativeEvidence";
import { reviewHasWork, useReviewDraft } from "./reviewDraft";
import "./creative-alternatives.css";

type Commit=(change:Change)=>Promise<Project>;
export default function CreativeAlternativesPanel({
  project,commit,
}:{
  project:Project;commit:Commit;
}) {
  const candidates=project.branches.filter(branch=>branch.id!==project.active_branch &&
    project.branch_workspaces.some(snapshot=>snapshot.branch_id===branch.id));
  const [selection,setSelection]=useState<string|null>(null);
  const selected=candidates.some(b=>b.id===selection)?selection:candidates[0]?.id??null;
  const editor=useReviewDraft(project,{kind:"alternative",id:selected??project.active_branch},commit);
  const {direction,rationale}=editor.draft.raw;
  const working=editor.saving,error=editor.error,note=editor.notice;
  const setDirection=(value:"positive"|"negative")=>editor.edit("direction",value);
  const setRationale=(value:string)=>editor.edit("rationale",value);
  const active=project.branches.find(b=>b.id===project.active_branch);
  const alternative=candidates.find(b=>b.id===selected);
  const evaluated=useMemo(()=>{
    if(!selected)return {report:null,error:null};
    try {return {report:inspectCreativeAlternative(project,selected),error:null};}
    catch(e){return {report:null,error:e instanceof Error?e.message:"The comparison cannot be assessed."};}
  },[project,selected]);
  const report=evaluated.report;
  const enabled=!!report && report.differences.length>0 && !editor.blocked &&
    rationale.trim().length>=15 && rationale.trim().length<=1200;

  const record=async()=>{
    if(!enabled || !report || !alternative)return;
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
    await editor.submit({
      type:"add_review",kind:"creative",resource:"project:"+project.id,body:text,
      start:null,end:null,locale:null,profile_id:null,
    });
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
            onChange={e=>setSelection(e.target.value)}>
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
            <p>Creates an open creative review anchored to this branch and revision, not an approved component</p>
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
        {editor.unknown&&<p role="status">Original alternative-review outcome UNKNOWN. The reason is retained and no request is replayed.</p>}
        {editor.unknown&&<button type="button" className="button compact" disabled={working||editor.reviewing}
          onClick={()=>void editor.readOutcome()}>Read saved alternative review and history</button>}
        {editor.outcome&&<section role="region" aria-label="Saved alternative review outcome">
          <p>Read saved revision r{editor.outcome.project.revision} and {editor.outcome.events.length} contiguous history entries.</p>
          <p>Matching authored values at revisions {editor.outcome.matchingValueRevisions.join(", ")||"none"} do not identify or acknowledge the original request.</p>
          <p>The original UNKNOWN copy remains. A later reason is kept; unchanged submitted text starts blank to prevent replay.</p>
          <button type="button" className="button compact" disabled={working||editor.reviewing}
            onClick={editor.separateIntent}>Keep original attempt and start a separate alternative review</button>
        </section>}
        {editor.storageError&&<div role="alert"><p>{editor.storageError}</p>
          <button type="button" className="button compact" onClick={editor.retryBackup}>Retry alternative recovery copy</button></div>}
        {editor.stale&&!editor.unknown&&<div role="status">
          <p>This reason was authored at r{editor.draft.baseRevision}. Compare the current alternatives before keeping it for this revision.</p>
          <button type="button" className="button compact" disabled={working||editor.unknown}
            onClick={editor.advance}>Review alternative reason on current revision</button></div>}
        {reviewHasWork(editor.draft)&&<button type="button" className="button compact"
          onClick={editor.download}>Export alternative recovery copy</button>}
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
