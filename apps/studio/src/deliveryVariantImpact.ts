/** A bounded comparison of source-authored delivery intents.
 * This is advisory change accounting, NOT a native render/cache invalidation
 * oracle, actual media comparison, authorization gate or quality approval.
 */
import type { DeliverableProfile, Project, RationalTime } from "./types";

export type VariantImpact = "picture" | "sound" | "captions" | "master" | "editorial";
export type VariantBaseline = "saved" | "parent";
export type VariantComparisonState =
  "NO_BASELINE" | "CHANGES_NEED_REVIEW" | "NO_FIELD_DIFFERENCES" | "UNRESOLVED";

export interface VariantImpactChange {
  field: string;
  label: string;
  before: string;
  after: string;
  impacts: VariantImpact[];
  note: string;
}

export interface VariantImpactReport {
  schema: "motionwright.delivery-variant-impact/1";
  projectId: string;
  generation: string;
  revision: number;
  profileId: string;
  baselineId: string | null;
  baselineKind: VariantBaseline;
  state: VariantComparisonState;
  changes: VariantImpactChange[];
  impactedStages: VariantImpact[];
  issues: string[];
  count: number;
  sourceNote: string;
}

const MAX_PROFILES=128, MAX_SCENES=512, MAX_TEXT_NODES=2048;
const MAX_OVERRIDES=512, MAX_NOTES=256;
const ORDER:VariantImpact[]=["picture","sound","captions","master","editorial"];
const INT=/^[0-9]{1,18}$/;

function rate(time:RationalTime):string {
  if(!time || !INT.test(time.num) || !INT.test(time.den))
    throw new Error("Invalid bounded delivery frame rate");
  let num=BigInt(time.num), den=BigInt(time.den);
  if(den===0n || num===0n)throw new Error("Invalid delivery frame rate");
  const gcd=(a:bigint,b:bigint):bigint=>{
    while(b!==0n){const t=a%b;a=b;b=t;}return a;
  };
  const divisor=gcd(num,den);
  num/=divisor;den/=divisor;
  return String(num)+"/"+String(den);
}
function formatYes(value:boolean):string {return value?"Enabled":"Disabled";}
function formatValue(value:string|null):string {return value ?? "None";}
function asLabel(value:string,maximum=66):string {
  return value.length>maximum ? Array.from(value).slice(0,maximum).join("")+"…" : value;
}
function arraysEqual(a:string[],b:string[]):boolean {
  return a.length===b.length && a.every((value,i)=>value===b[i]);
}
function selected(project:Project,profile:DeliverableProfile):string[] {
  if(!profile.included_scene_ids.length)return project.scenes.map(s=>s.id);
  return [...profile.included_scene_ids];
}
function sceneSummary(project:Project,ids:string[]):string {
  const sceneName=new Map(project.scenes.map(scene=>[scene.id,scene.name]));
  const names=ids.slice(0,3).map(id=>asLabel(sceneName.get(id)??"Missing source scene",35));
  return ids.length+"/"+project.scenes.length+" source scenes"+
    (names.length ? " · "+names.join(" / ")+(ids.length>3?" / …":"") : "");
}
function overrideSummary(text:string|undefined):string {
  // Do not output private localized copy into logs, evidence or any diff.
  return text===undefined?"Source text":"Explicit copy · "+Array.from(text).length+" code points";
}
function profileBounded(profile:DeliverableProfile):void {
  if(!profile ||
    Object.keys(profile.text_overrides ?? {}).length>MAX_OVERRIDES ||
    profile.included_scene_ids.length>MAX_SCENES ||
    profile.protected_scene_ids.length>MAX_SCENES ||
    profile.adaptation_notes.length>MAX_NOTES)
    throw new Error("Delivery profile exceeds bounded comparison limits");
}

/** Compares exactly the source fields the app owns. Impacts are conservative,
 * potential review stages, not claims of renderer dirty windows or cache hits.
 */
export function inspectVariantImpact(
  project:Project,
  candidate:DeliverableProfile,
  baseline:DeliverableProfile|null,
  kind:VariantBaseline,
):VariantImpactReport {
  if(project.deliverables.length>MAX_PROFILES ||
    project.scenes.length>MAX_SCENES ||
    project.scenes.reduce((n,s)=>n+s.nodes.length,0)>MAX_TEXT_NODES)
    throw new Error("Source project exceeds bounded variant comparison limits");
  profileBounded(candidate);
  if(baseline)profileBounded(baseline);
  const issues:string[]=[];
  const changes:VariantImpactChange[]=[];
  const add=(field:string,label:string,before:string,after:string,
    impacts:VariantImpact[],note:string)=>{
    if(before!==after)changes.push({field,label,before,after,impacts,note});
  };
  const addArray=(field:string,label:string,before:string[],after:string[],
    impacts:VariantImpact[],note:string,summarize:(ids:string[])=>string)=>{
    if(!arraysEqual(before,after))changes.push({
      field,label,before:summarize(before),after:summarize(after),impacts,note,
    });
  };
  const issue=(message:string)=>{
    if(!issues.includes(message))issues.push(message);
  };
  const knownProfiles=new Map(project.deliverables.map(profile=>[profile.id,profile]));
  if(kind==="saved" && baseline && baseline.id!==candidate.id)
    issue("Saved-draft baseline has a different profile identity; compare neither as authoritative.");
  if(kind==="parent" &&
    candidate.parent_profile_id!==baseline?.id)
    issue("The referenced parent profile is missing or differs from the comparison baseline.");

  if(candidate.parent_profile_id) {
    const visited=new Set<string>([candidate.id]);
    let pointer:string|null=candidate.parent_profile_id;
    while(pointer!==null) {
      if(visited.has(pointer)){
        issue("The profile lineage contains a cycle and needs canonical review.");
        break;
      }
      visited.add(pointer);
      if(visited.size>MAX_PROFILES){
        issue("The profile lineage exceeds the bounded inspection budget.");
        break;
      }
      const ancestor=knownProfiles.get(pointer);
      if(!ancestor){
        issue("The variant references a parent that is not present in the current project.");
        break;
      }
      pointer=ancestor.parent_profile_id;
    }
  }
  if(candidate.source_revision!==null &&
    candidate.source_revision>project.revision)
    issue("Variant source revision is newer than the inspected project; provenance is inconsistent.");
  if(candidate.source_revision!==null &&
    candidate.source_revision<project.revision && candidate.parent_profile_id)
    issue("This derivative was created from an older project revision; recheck its source assumptions.");
  const knownText=new Map(project.scenes.flatMap(s=>
    s.nodes.filter(n=>n.kind==="text").map(n=>[n.id,{scene:s.name,node:n.name}] as const)));
  const nodeRefs=new Set(knownText.keys());
  const allOverrides=new Set([
    ...Object.keys(candidate.text_overrides ?? {}),
    ...Object.keys(baseline?.text_overrides ?? {}),
  ]);
  for(const key of allOverrides){
    if(!nodeRefs.has(key))
      issue("A localized text override refers to an object absent from the current source project.");
  }

  if(baseline) {
    const picture:VariantImpact[]=["picture","master"];
    const audiovisual:VariantImpact[]=["picture","sound","captions","master"];
    const sound:VariantImpact[]=["sound","captions","master"];
    const caps:VariantImpact[]=["captions"];
    const master:VariantImpact[]=["master"];
    const editorial:VariantImpact[]=["editorial"];
    const labeled=<K extends keyof DeliverableProfile>(field:K,label:string,
      impacts:VariantImpact[],note:string,
      display:(value:DeliverableProfile[K])=>string=value=>String(value))=>{
      add(String(field),label,display(baseline[field]),display(candidate[field]),impacts,note);
    };
    labeled("name","Profile name",editorial,"Identifier only; rendering does not change.");
    labeled("language","Delivery locale",["picture","captions","sound","master"],
      "Check native text, localized narration and caption language; this comparison does not translate.");
    add("frame_size","Output frame",String(baseline.width)+" × "+String(baseline.height),
      String(candidate.width)+" × "+String(candidate.height),picture,
      "Native aspect-specific composition needs review; coordinates are not automatically reflowed.");
    add("frame_rate","Frame rate",rate(baseline.frame_rate),rate(candidate.frame_rate),
      audiovisual,"Check native frame clock and source voice synchronization.");
    labeled("framing_strategy","Framing strategy",picture,
      "Crop and replan are different authored intents; neither is approved here.");
    labeled("crop_approved","Crop consent",["editorial","picture"],"Consent requires native framing review.",formatYes);
    labeled("color_space","Color space",["picture","master"],"Verify native color pipeline.");
    labeled("brand_profile","Brand selection",picture,
      "Check current brand constraints and authored visual language.",formatValue);
    labeled("voice_track_id","Narration source",sound,
      "Recheck source SHA, transcript alignment, mix and timing.",formatValue);
    labeled("timing_locked","Narration timing lock",["editorial","sound"],
      "A changed lock is not authorization to stretch recorded audio.",formatYes);
    labeled("captions","Caption sidecar",caps,"Caption export still requires valid transcript timing.",formatYes);
    labeled("caption_format","Sidecar format",caps,"Re-export sidecar as requested.");
    labeled("burn_in_captions","Caption burn-in",["captions","picture","master"],
      "Burn-in requires native pixels and safe-area review.",formatYes);
    labeled("video_codec","Video codec",master,"Recheck renderer codec support before mastering.");
    labeled("audio_codec","Audio codec",master,"Recheck native mastering support.");
    labeled("audio_sample_rate_hz","Audio sample rate",["sound","master"],
      "Check measured voice format and resampling authorization.");
    labeled("container","Container",master,"Confirm output mux compatibility.");
    labeled("cut_label","Editorial cut label",editorial,"Label only; does not modify the cut.",formatValue);
    labeled("source_revision","Recorded source revision",editorial,
      "Historical snapshot metadata; not an automatically validated branch.",
      v=>v===null?"Unspecified":"r"+String(v));
    labeled("parent_profile_id","Parent lineage",editorial,
      "Change in ancestry needs independent source/provenance review.",formatValue);
    addArray("included_scene_ids","Source scene selection",
      selected(project,baseline),selected(project,candidate),
      audiovisual,"Revalidate scene timing, transcript rebasing, music and native master.",
      ids=>sceneSummary(project,ids));
    addArray("protected_scene_ids","Protected source scenes",
      baseline.protected_scene_ids,candidate.protected_scene_ids,
      editorial,"Confirm intended protected narrative coverage.",
      ids=>ids.length+" protected · "+ids.slice(0,3).map(id=>
        asLabel(project.scenes.find(scene=>scene.id===id)?.name??"Missing source",35)).join(" / "));
    addArray("adaptation_notes","Adaptation notes",
      baseline.adaptation_notes,candidate.adaptation_notes,
      editorial,"Operator notes; not a pixel or media edit.",
      ids=>ids.length+" authored notes (wording compared; open the profile for exact text)");
    // Text override fields are compared by semantic object identity. The
    // original localized text never appears in the report or CI logs.
    for(const key of [...allOverrides].sort()){
      const a=baseline.text_overrides?.[key],b=candidate.text_overrides?.[key];
      if(a===b)continue;
      const bound=knownText.get(key);
      changes.push({
        field:"text_override:"+key,
        label:bound?"Text: "+asLabel(bound.scene)+" / "+asLabel(bound.node):
          "Unresolved text object",
        before:overrideSummary(a),after:overrideSummary(b),impacts:picture,
        note:bound?"Copy differs from the baseline, even if character counts match. Inspect exact text in the localized editor, then review native glyph fit and animation.":
          "No current semantic object matches this override; do not infer a safe edit.",
      });
    }
  }
  // We don't assert that no content changes means no render impact.
  // A source asset, provider generation or runtime may change independently.
  const impactedStages=ORDER.filter(stage=>
    changes.some(change=>change.impacts.includes(stage)));
  const state:VariantComparisonState=issues.length>0?"UNRESOLVED":
    !baseline?"NO_BASELINE":changes.length?"CHANGES_NEED_REVIEW":"NO_FIELD_DIFFERENCES";
  return {
    schema:"motionwright.delivery-variant-impact/1",
    projectId:project.id,generation:project.generation,revision:project.revision,
    profileId:candidate.id,baselineId:baseline?.id??null,baselineKind:kind,
    state,changes,impactedStages,issues,count:changes.length,
    sourceNote:"This only compares bounded authored profile fields. Potential stages are not a cache invalidation oracle, native readback, external asset SHA audit or approved creative/render output.",
  };
}
