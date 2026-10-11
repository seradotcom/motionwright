import { useMemo } from "react";
import { FileText, GitCompareArrows, ShieldAlert } from "lucide-react";
import type { DeliverableProfile, Project } from "./types";
import { inspectVariantContinuity } from "./variantContinuity";
import "./variant-continuity.css";

export default function VariantContinuityReview({project,profile,saved}:{
  project:Project;profile:DeliverableProfile;saved:DeliverableProfile|null;
}) {
  const preview=useMemo(()=>{
    try {return inspectVariantContinuity(project,profile);}
    catch {return null;}
  },[project,profile]);
  if(!preview) return <section className="variant-continuity" aria-label="Narrative cut continuity review">
    <h3>Continuity not assessed</h3>
    <p className="variant-continuity-intro" role="status">The source data exceeded this bounded
      analysis or has invalid timing. No completeness claim is made. Review the project and cut
      using the trusted source editor before making delivery decisions.</p>
  </section>;
  const dirty=!!saved && JSON.stringify(profile)!==JSON.stringify(saved);
  const sourceNames=new Map(project.scenes.map(scene=>[scene.id,scene.name]));
  const claimNames=new Map(project.brief.claims.map(claim=>[claim.id,claim.text]));
  const remaining=preview.findings.length>30?preview.findings.length-30:0;
  return <section className="variant-continuity" aria-label="Narrative cut continuity review">
    <header className="variant-continuity-heading">
      <div><GitCompareArrows size={17} aria-hidden="true"/><div>
        <h3>Narrative cut continuity</h3>
        <p>Editorial evidence check · source generation and revision bound</p>
      </div></div>
      <strong className="variant-continuity-verdict">
        {preview.verdict==="STRUCTURAL_BLOCKERS"?"BLOCKED CUT":
          preview.verdict==="EDITOR_REVIEW_REQUIRED"?"EDITOR REVIEW REQUIRED":"NO AUTOMATED FLAGS"}
      </strong>
    </header>
    <p className="variant-continuity-intro">
      {dirty?"Reviewing unsaved variant choices. ":"Reviewing the current delivery profile. "}
      Missing source scenes, dropped authored claims and caption-boundary conflicts are surfaced before export.
      The analysis does not generate images, grant a renderer or approve the narrative.
    </p>
    <div className="variant-continuity-metrics">
      <div><span>Retained source scenes</span><strong>{preview.selected_scene_ids.length}/{project.scenes.length}</strong></div>
      <div><span>Source scenes omitted</span><strong>{preview.omitted_scene_ids.length}</strong></div>
      <div><span>Authored claims removed</span><strong>{preview.omitted_claim_ids.length}</strong></div>
      <div><span>Resulting cut duration</span><strong>{preview.total_duration.num}/{preview.total_duration.den}s</strong></div>
    </div>
    {preview.omitted_scene_ids.length>0 && <div className="variant-continuity-omitted">
      <strong>Scenes omitted compared with {profile.parent_profile_id?"the parent cut":"the full source timeline"}:</strong>
      <p>{preview.omitted_scene_ids.map(id=>sourceNames.get(id)??"Unknown source").join(" · ")}</p>
    </div>}
    {preview.omitted_claim_ids.length>0 && <div className="variant-continuity-omitted">
      <strong>Claims no longer supported by any retained planned shot:</strong>
      <p>{preview.omitted_claim_ids.map(id=>claimNames.get(id)??"Unresolved claim").join(" · ")}</p>
    </div>}
    <div className="variant-continuity-results">
      <div className="variant-continuity-subhead"><FileText size={14} aria-hidden="true"/>
        <strong>Review findings</strong><small>{preview.findings.length} detected</small></div>
      {preview.findings.length===0
        ?<p className="variant-continuity-clear">No structural or plan-coverage flags were found. Native export, source rights, visual design and human comprehension remain unverified.</p>
        :<div className="variant-continuity-findings">{preview.findings.slice(0,30).map((finding,i)=>
          <article key={finding.code+"-"+finding.scene_id+"-"+finding.claim_id+"-"+i}>
            <span>{finding.severity==="block"?"Requires correction":"Requires editorial review"}</span>
            <strong>{finding.code.replaceAll("_"," ")}</strong>
            <p>{finding.description}</p>
            {finding.scene_id && <small>Scene: {sourceNames.get(finding.scene_id)??"Unknown"}</small>}
          </article>)}
          {remaining>0 && <p>{remaining} more findings require a detailed project review; this display is intentionally bounded.</p>}
        </div>}
    </div>
    <div className="variant-continuity-disclosure">
      <ShieldAlert size={17} aria-hidden="true"/>
      <p>Source: project {preview.project_id.slice(0,8)} · generation {preview.generation.slice(0,8)} · revision {preview.revision}.
        A content hash identifies file bytes, not factual validity, rights or truthful capture. This is not
        a native frame verification, a creative acceptance decision, or permission to publish.</p>
    </div>
  </section>;
}
