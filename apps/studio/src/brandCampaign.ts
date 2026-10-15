import type {Project} from "./types";
import {brandDigest,validateBrandProfile} from "./brandGovernance";

export const BRAND_ROLLOUT_SCHEMA="motionwright.brand-campaign-rollout/1" as const;
export interface BrandCampaignRollout {
  schema:typeof BRAND_ROLLOUT_SCHEMA;
  project_id:string;generation:string;expected_revision:number;brand_sha256:string;
  campaign:string;scene_ids:string[];accent:string|null;wordmark:string|null;
  reviewer_declaration:string;rationale:string;
  accepts_semantic_component_rebuild:true;
}
export interface BrandCampaignRow {
  scene_id:string;hero_id:string;scene_name:string;
  from_accent:string;to_accent:string;
  from_wordmark:string;to_wordmark:string;
  changed:boolean;
}
export interface BrandCampaignPreview {
  schema:typeof BRAND_ROLLOUT_SCHEMA;
  project_id:string;generation:string;revision:number;brand_sha256:string;
  rows:BrandCampaignRow[];changed_count:number;
  owner_review_required:true;committed:false;output_rendered:false;
  publication_approved:false;
}
const deny=(message:string):never=>{throw new Error(message);};
const bounded=(value:string,max:number)=>
  !!value.trim() && new TextEncoder().encode(value).length<=max &&
  !/[\u0000-\u001f\u007f]/u.test(value);
/** Client-side preview only; authoritative component merge/locks/CAS remain in Rust. */
export async function previewBrandCampaign(
  project:Project,proposal:BrandCampaignRollout
):Promise<BrandCampaignPreview> {
  const profile=project.production_design?.brand_profile;
  if(!profile) return deny("Save a current BrandProfile before reviewing any brand propagation.");
  validateBrandProfile(profile);
  const brandSha256=await brandDigest(profile);
  if(proposal.schema!==BRAND_ROLLOUT_SCHEMA ||
      proposal.project_id!==project.id||proposal.generation!==project.generation||
      proposal.expected_revision!==project.revision ||
      proposal.brand_sha256!==brandSha256 ||
      !bounded(proposal.campaign,160)||
      !bounded(proposal.reviewer_declaration,160)||
      !bounded(proposal.rationale,1200)||
      proposal.accepts_semantic_component_rebuild!==true ||
      proposal.scene_ids.length<1||proposal.scene_ids.length>8||
      new Set(proposal.scene_ids).size!==proposal.scene_ids.length||
      (proposal.accent===null&&proposal.wordmark===null))
    return deny("Campaign must be current, owner-reviewed, explicit and bound to 1–8 unique scenes.");
  const palette=profile.rules.find(rule=>rule.kind==="allowed_accents");
  const mark=profile.rules.find(rule=>rule.kind==="required_wordmark");
  if(proposal.accent!==null && (!palette||palette.kind!=="allowed_accents"||
      !palette.colors.some(color=>color.toLowerCase()===proposal.accent?.toLowerCase())))
    return deny("Only allowed accents from the current saved policy can propagate.");
  if(proposal.wordmark!==null && (!mark||mark.kind!=="required_wordmark"||
      mark.text!==proposal.wordmark||[...proposal.wordmark].length>8))
    return deny("Required wordmark must match the source policy and the eight-character Hero limit.");
  if(project.locks.some(lock=>lock.resource==="project:"+project.id&&
      ["content","style","position","timing"].includes(lock.kind)))
    return deny("The project is locked; cannot prepare a cross-scene rollout.");
  const rows:BrandCampaignRow[]=[];
  for(const id of proposal.scene_ids){
    const scene=project.scenes.find(scene=>scene.id===id);
    const hero=project.production_design?.heroes.find(hero=>hero.scene_id===id);
    if(!scene||!hero) return deny("Each target must be an existing attached Hero scene.");
    if(scene.status==="approved"||scene.status==="review"||
        project.reviews.some(review=>review.anchor.resource==="scene:"+id &&
          ["open","needs_recheck"].includes(review.status)))
      return deny("Protected creative review or approved scene must be resolved first.");
    if(project.locks.some(lock=>lock.resource==="scene:"+id&&
        ["content","style","position","timing","renderer"].includes(lock.kind)))
      return deny("The target scene has a content/style/timing lock.");
    const scoped=[palette?.id,mark?.id];
    if(project.production_design?.brand_exceptions?.some(exception=>
      exception.scene_id===id && (
        proposal.accent!==null&&exception.rule_id===scoped[0]||
        proposal.wordmark!==null&&exception.rule_id===scoped[1]
      )))return deny("Resolve the saved scene-scoped brand exception before propagation.");
    const to_accent=proposal.accent??hero.config.accent;
    const to_wordmark=proposal.wordmark??hero.config.wordmark;
    rows.push({
      scene_id:id,hero_id:hero.id,scene_name:scene.name,
      from_accent:hero.config.accent,to_accent,
      from_wordmark:hero.config.wordmark,to_wordmark,
      changed:hero.config.accent!==to_accent||hero.config.wordmark!==to_wordmark,
    });
  }
  const changed=rows.filter(row=>row.changed).length;
  if(!changed)return deny("No existing Hero component would change under this rollout.");
  return {
    schema:BRAND_ROLLOUT_SCHEMA,
    project_id:project.id,generation:project.generation,
    revision:project.revision,brand_sha256:brandSha256,
    rows,changed_count:changed,owner_review_required:true,
    committed:false,output_rendered:false,publication_approved:false,
  };
}
export function validateBrandCampaignReceipt(
  project:Project,rollout:BrandCampaignRollout,preview:BrandCampaignPreview
):boolean{
  return rollout.schema===BRAND_ROLLOUT_SCHEMA &&
    preview.schema===BRAND_ROLLOUT_SCHEMA &&
    rollout.project_id===project.id&&rollout.generation===project.generation &&
    rollout.expected_revision===project.revision &&
    preview.project_id===project.id&&preview.generation===project.generation &&
    preview.revision===project.revision &&
    preview.brand_sha256===rollout.brand_sha256 &&
    preview.owner_review_required===true&&
    preview.committed===false&&preview.output_rendered===false&&
    preview.publication_approved===false &&
    preview.changed_count>0;
}
