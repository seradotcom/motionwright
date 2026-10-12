import { ArrowLeftRight, CircleHelp, FileDiff, ShieldAlert } from "lucide-react";
import { useMemo } from "react";
import { inspectVariantImpact, type VariantImpact, type VariantImpactReport } from "./deliveryVariantImpact";
import type { DeliverableProfile, Project } from "./types";
import "./delivery-variant-impact.css";

const LABEL:Record<VariantImpact,string>={
  picture:"Picture / native layout",sound:"Narration / mix",
  captions:"Caption file / burn-in",master:"AV mastering",
  editorial:"Editorial / metadata",
};
const ORDER:VariantImpact[]=["picture","sound","captions","master","editorial"];

function ImpactComparison({report,label}:{report:VariantImpactReport|null;label:string}) {
  const count=report===null?"Unassessed":
    report.state==="NO_BASELINE"?"No baseline":
    report.state==="UNRESOLVED"?"Needs source review":
    report.count===0?"No field differences":
    String(report.count)+" changed fields";
  return <details className="variant-impact-set">
    <summary>
      <FileDiff size={15} aria-hidden="true"/>
      <strong>{label}</strong>
      <span>{count}</span>
    </summary>
    {report===null ? <div className="variant-impact-inner" role="status">
      Bounded profile comparison could not be completed. No field equivalence
      or delivery readiness is implied.
    </div> : <div className="variant-impact-inner">
      <p className="variant-impact-source mono">
        r{report.revision} · generation {report.generation.slice(0,8)} · profile {report.profileId.slice(0,8)}
      </p>
      {report.issues.length>0 && <div className="variant-impact-issues">
        <strong><CircleHelp size={14} aria-hidden="true"/> Unresolved provenance</strong>
        <ul>{report.issues.map(item=><li key={item}>{item}</li>)}</ul>
      </div>}
      {!report.baselineId && <p className="variant-impact-empty">
        No source baseline is available for this comparison. Select a saved
        profile or an existing parent; no differences can be certified.
      </p>}
      {report.baselineId && <>
        {report.changes.length===0 ? <p className="variant-impact-empty">
          No differences among the explicitly compared profile fields.
          Referenced assets, source code, fonts, provider generation and
          rendered pixels still require independent checks.
        </p> : <>
          <div className="variant-impact-scope" aria-label="Potential downstream review">
            <strong>Potential review stages</strong>
            <div>
              {ORDER.map(stage=><span key={stage} className={report.impactedStages.includes(stage)?"flagged":"unchanged"}>
                {LABEL[stage]} · {report.impactedStages.includes(stage)?"Review":"No field trigger"}
              </span>)}
            </div>
          </div>
          <div className="variant-impact-diff">
            <div className="variant-impact-head">
              <span>Authored field</span><span>Baseline → Current</span>
            </div>
            <ul>
              {report.changes.map(change=><li key={change.field}>
                <div className="variant-impact-row">
                  <strong>{change.label}</strong>
                  <div className="variant-impact-values">
                    <span>{change.before}</span>
                    <span className="variant-impact-arrow" aria-label="to">→</span>
                    <span>{change.after}</span>
                  </div>
                </div>
                <p>{change.note}</p>
              </li>)}
            </ul>
          </div>
        </>}
      </>}
      <p className="variant-impact-caveat">
        <ShieldAlert size={14} aria-hidden="true"/>
        <span>{report.sourceNote} No changed-field count is a verification PASS.</span>
      </p>
    </div>}
  </details>;
}

export default function DeliveryVariantImpactReview({
  project,draft,saved,
}:{
  project:Project;draft:DeliverableProfile;saved:DeliverableProfile|null;
}) {
  const comparisons=useMemo(()=>{
    const parent=draft.parent_profile_id
      ?project.deliverables.find(p=>p.id===draft.parent_profile_id)??null:null;
    const build=(base:DeliverableProfile|null,kind:"saved"|"parent")=>{
      try{return inspectVariantImpact(project,draft,base,kind);}
      catch{return null;}
    };
    return {
      saved:build(saved,"saved"),
      parent:draft.parent_profile_id?build(parent,"parent"):undefined,
    };
  },[project,draft,saved]);
  return <section className="delivery-impact-review" aria-label="Delivery variant impact comparison">
    <header className="delivery-impact-heading">
      <div>
        <ArrowLeftRight size={17} aria-hidden="true"/>
        <div>
          <h3>Variant change impact</h3>
          <p>What this profile actually changes compared with its sources</p>
        </div>
      </div>
      <span>READ ONLY · NOT RENDER EVIDENCE</span>
    </header>
    <p className="delivery-impact-intro">
      Compare this edit against its saved profile and, for derivatives,
      its parent. Candidate downstream stages are editorial indications,
      not real cache invalidations or proofs of native pixel fidelity.
    </p>
    <div className="delivery-impact-ledger">
      <ImpactComparison label="Current draft vs saved profile" report={comparisons.saved}/>
      {comparisons.parent!==undefined &&
        <ImpactComparison label="Variant vs parent profile" report={comparisons.parent}/>}
    </div>
  </section>;
}
