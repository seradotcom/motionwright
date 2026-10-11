/** Read-only narrative coverage and source trace for a delivery cut.
 * Editing and saving variants still use the canonical Semwright Native SDK.
 */
import type { DeliverableProfile, Project, RationalTime, Scene } from "./types";

export type ContinuitySeverity = "block" | "review";
export interface ContinuityFinding {
  code: string;
  severity: ContinuitySeverity;
  description: string;
  scene_id: string | null;
  claim_id: string | null;
}
export interface VariantContinuityReview {
  project_id: string;
  generation: string;
  revision: number;
  profile_id: string;
  verdict: "STRUCTURAL_BLOCKERS" | "EDITOR_REVIEW_REQUIRED" | "NO_AUTOMATED_FLAGS";
  selected_scene_ids: string[];
  omitted_scene_ids: string[];
  total_duration: RationalTime;
  omitted_claim_ids: string[];
  retained_claim_ids: string[];
  findings: ContinuityFinding[];
}
type Fraction = {n:bigint;d:bigint};
const integer = /^-?[0-9]+$/;
const sourceDigest = /^[a-f0-9]{64}$/;
function rational(value:RationalTime):Fraction {
  if(!integer.test(value.num)||!integer.test(value.den)||BigInt(value.den)<=0n)
    throw new Error("Invalid rational edit clock.");
  return {n:BigInt(value.num),d:BigInt(value.den)};
}
function add(a:Fraction,b:Fraction):Fraction {
  const n=a.n*b.d+b.n*a.d,d=a.d*b.d;
  let x=n<0n?-n:n,y=d;
  while(y){const t=y;y=x%y;x=t;}
  const g=x||1n;
  return {n:n/g,d:d/g};
}
function cmp(a:Fraction,b:Fraction):number {
  const z=a.n*b.d-b.n*a.d;
  return z<0n?-1:z>0n?1:0;
}
function keptScenes(project:Project,profile:DeliverableProfile):Scene[] {
  const included=profile.included_scene_ids;
  if(!included.length)return project.scenes;
  const selected=new Set(included);
  return project.scenes.filter(s=>selected.has(s.id));
}
export function inspectVariantContinuity(
  project:Project, profile:DeliverableProfile,
):VariantContinuityReview {
  if(project.scenes.length>4096 || project.brief.claims.length>1024 ||
     (project.production_design?.plan?.shots.length??0)>4096 ||
     profile.included_scene_ids.length>4096 || profile.protected_scene_ids.length>4096)
    throw new Error("Cut review input exceeds the bounded source budget.");
  const findings:ContinuityFinding[]=[];
  const flag=(code:string,severity:ContinuitySeverity,description:string,
    scene_id:string|null=null,claim_id:string|null=null)=>
    findings.push({code,severity,description,scene_id,claim_id});
  const projectIds=project.scenes.map(scene=>scene.id);
  const used=new Set<string>();
  let previous=-1;
  for(const id of profile.included_scene_ids) {
    const position=projectIds.indexOf(id);
    if(position<0)flag("UNKNOWN_SCENE","block","This cut references a scene absent from the current project.",id);
    if(used.has(id))flag("DUPLICATE_SCENE","block","The custom cut lists one scene more than once.",id);
    if(position>=0 && position<=previous)
      flag("SCENE_ORDER","block","The custom cut changes the source scene order.",id);
    used.add(id);
    if(position>=0)previous=position;
  }
  const retained=keptScenes(project,profile);
  const retainedIds=new Set(retained.map(scene=>scene.id));
  if(profile.included_scene_ids.length && !retained.length)
    flag("EMPTY_CUT","block","This custom cut has no retained source scenes.");
  for(const id of profile.protected_scene_ids) {
    if(!projectIds.includes(id))
      flag("UNKNOWN_PROTECTION","block","Protected scene no longer exists.",id);
    else if(!retainedIds.has(id))
      flag("OMITTED_PROTECTED","block","The edit omits an explicitly protected narrative scene.",id);
  }
  const parent=project.deliverables.find(p=>p.id===profile.parent_profile_id);
  if(profile.parent_profile_id && !parent)
    flag("UNKNOWN_PARENT","block","This derivative references a missing parent profile.");
  if(profile.parent_profile_id===profile.id)
    flag("CYCLIC_PARENT","block","A delivery profile cannot derive from itself.");
  const baseline=parent?keptScenes(project,parent):project.scenes;
  const omitted=baseline.filter(scene=>!retainedIds.has(scene.id)).map(scene=>scene.id);
  const plan=project.production_design?.plan;
  if(!plan)flag("PLAN_NOT_ADMITTED","review",
    "No authored production plan exists; factual coverage and creative intent cannot be fully assessed.");
  const shots=new Map(plan?.shots.map(shot=>[shot.scene_id,shot])??[]);
  const coverage=(scenes:Scene[])=>{
    const ids=new Set<string>();
    for(const scene of scenes)for(const claim of shots.get(scene.id)?.claim_ids??[])ids.add(claim);
    return ids;
  };
  const baselineClaims=coverage(baseline),keptClaims=coverage(retained);
  const lost=[...baselineClaims].filter(id=>!keptClaims.has(id)).sort();
  for(const id of lost)flag("CLAIM_LOST","review",
    "All planned scenes supporting this authored claim are absent from the cut; review the argument.",
    null,id);
  for(const id of [...keptClaims].sort()) {
    const claim=project.brief.claims.find(item=>item.id===id);
    if(!claim){flag("UNKNOWN_CLAIM","block","A retained shot references a missing brief claim.",null,id);continue;}
    const reference=claim.source;
    const trustedAsset=reference?.kind==="asset"
      ?project.assets.some(asset=>asset.id===reference.asset_id && sourceDigest.test(asset.content_sha256??""))
      :false;
    const sourceShot=retained.some(scene=>{
      const shot=shots.get(scene.id);
      if(!shot || !shot.claim_ids.includes(id) ||
        !["real_capture","licensed_footage"].includes(shot.evidence_kind))return false;
      return shot.asset_ids.some(assetId=>project.assets.some(asset=>
        asset.id===assetId && sourceDigest.test(asset.content_sha256??"") &&
        (shot.evidence_kind==="real_capture"
          ?(asset.media_type.startsWith("image/")||asset.media_type.startsWith("video/"))
          :asset.media_type.startsWith("video/"))));
    });
    if(!trustedAsset||!sourceShot)
      flag("CLAIM_NOT_SOURCE_BOUND","review",
        "An authored claim lacks a matching digest-bound claim source and retained source-bearing shot. It is not verified product evidence.",
        null,id);
  }
  for(const scene of retained)if(!shots.has(scene.id))
    flag("SHOT_INTENT_UNKNOWN","review",
      "A retained scene has no associated narrative decision in the production plan.",scene.id);
  if(parent && parent.language!==profile.language){
    const textNodes=retained.flatMap(s=>s.nodes.filter(n=>n.kind==="text"));
    const missing=textNodes.filter(n=>!profile.text_overrides[n.id]?.trim());
    if(missing.length)flag("LOCALIZED_COPY_INCOMPLETE","block",
      missing.length+" text objects have no explicit localized wording. No translation is inferred.");
    if(parent.voice_track_id && profile.voice_track_id===parent.voice_track_id)
      flag("PARENT_VOICE_REUSED","block",
        "The derivative reuses its parent language's voice source; authorize a distinct source.");
  }
  let total:Fraction={n:0n,d:1n};
  for(const scene of retained){
    const duration=rational(scene.duration);
    if(cmp(duration,{n:0n,d:1n})<=0)
      flag("SCENE_DURATION","block","A retained scene has an invalid duration.",scene.id);
    total=add(total,duration);
  }
  if(profile.captions && profile.included_scene_ids.length>0){
    const transcript=profile.voice_track_id
      ?project.audio.transcript.filter(s=>s.voice_track_id===profile.voice_track_id)
      :project.audio.transcript;
    if(transcript.length*retained.length>100_000)
      flag("CAPTION_BUDGET","review","Transcript/cut review exceeds the inspection budget.");
    else {
      let partial=0;
      for(const segment of transcript){
        const from=rational(segment.start),to=rational(segment.end);
        for(const scene of retained){
          const start=rational(scene.start),end=add(start,rational(scene.duration));
          if(cmp(from,end)<0 && cmp(to,start)>0 &&
             (cmp(from,start)<0 || cmp(to,end)>0)){partial++;break;}
        }
      }
      if(partial)flag("CAPTION_CROSSES_CUT","block",
        partial+" transcript segment(s) cross retained scene boundaries; native caption reflow requires an explicit timing correction.");
    }
  }
  if(project.narrative.protected_sections.length)
    flag("UNMAPPED_PROTECTED_BEATS","review",
      "Protected narrative beats have no scene mapping in this document schema; a human must review their inclusion.");
  const verdict:VariantContinuityReview["verdict"]=
    findings.some(f=>f.severity==="block")?"STRUCTURAL_BLOCKERS":
    findings.some(f=>f.severity==="review")?"EDITOR_REVIEW_REQUIRED":"NO_AUTOMATED_FLAGS";
  return {
    project_id:project.id,generation:project.generation,revision:project.revision,
    profile_id:profile.id,verdict,selected_scene_ids:retained.map(s=>s.id),
    omitted_scene_ids:omitted,total_duration:{num:String(total.n),den:String(total.d)},
    omitted_claim_ids:lost,retained_claim_ids:[...keptClaims].sort(),findings,
  };
}
