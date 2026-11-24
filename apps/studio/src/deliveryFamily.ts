import type { DeliverableProfile, DeliverySettingPropagation, Project, SharedDeliverySetting } from "./types";

export type SharedDeliveryField = SharedDeliverySetting["field"];
export const SHARED_DELIVERY_FIELDS: {field: SharedDeliveryField; label: string}[] = [
  {field:"brand_profile",label:"Brand reference"},
  {field:"frame_rate",label:"Frame rate"},
  {field:"caption_format",label:"Caption sidecar format"},
  {field:"video_codec",label:"Video codec"},
  {field:"audio_codec",label:"Audio codec"},
  {field:"audio_sample_rate_hz",label:"Audio sample rate"},
  {field:"color_space",label:"Color space"},
  {field:"container",label:"Container"},
];

export function readDeliverySetting(profile: DeliverableProfile, field: SharedDeliveryField): SharedDeliverySetting {
  switch(field){
    case "brand_profile": return {field,value:profile.brand_profile};
    case "frame_rate": return {field,value:structuredClone(profile.frame_rate)};
    case "caption_format": return {field,value:profile.caption_format};
    case "video_codec": return {field,value:profile.video_codec};
    case "audio_codec": return {field,value:profile.audio_codec};
    case "audio_sample_rate_hz": return {field,value:profile.audio_sample_rate_hz};
    case "color_space": return {field,value:profile.color_space};
    case "container": return {field,value:profile.container};
  }
}

export function sameDeliverySetting(a: SharedDeliverySetting,b: SharedDeliverySetting): boolean {
  if(a.field!==b.field)return false;
  if(a.field==="frame_rate" && b.field==="frame_rate")
    return a.value.num===b.value.num && a.value.den===b.value.den;
  return a.value===b.value;
}

export function displayDeliverySetting(setting: SharedDeliverySetting): string {
  if(setting.field==="frame_rate") return setting.value.num+"/"+setting.value.den+" fps";
  if(setting.field==="audio_sample_rate_hz")return setting.value+" Hz";
  return setting.value===null?"None":String(setting.value);
}

function descendant(project:Project,profile:DeliverableProfile,sourceId:string):boolean{
  const seen=new Set([profile.id]);
  let next=profile.parent_profile_id;
  while(next){
    if(seen.has(next))return false;
    seen.add(next);
    if(next===sourceId)return true;
    next=project.deliverables.find(p=>p.id===next)?.parent_profile_id??null;
  }
  return false;
}

const protectedKinds=new Set(["content","style","timing","renderer"]);
function locked(project:Project,resource:string):boolean{
  return project.locks.some(lock=>lock.resource===resource && protectedKinds.has(lock.kind));
}

export function deliveryFamilyRows(project:Project,sourceId:string,field:SharedDeliveryField){
  const source=project.deliverables.find(p=>p.id===sourceId);
  if(!source || project.deliverables.length>128)return [];
  const value=readDeliverySetting(source,field);
  return project.deliverables.filter(p=>p.id!==source.id && descendant(project,p,source.id)).map(profile=>{
    const expected=readDeliverySetting(profile,field);
    const resource="deliverable:"+profile.id;
    const reason=project.reviews.some(r=>r.anchor.profile_id===profile.id || r.anchor.resource===resource)
      ?"Reviewed version; derive a new profile."
      :locked(project,"project:"+project.id)||locked(project,resource)
        ?"Protected by a lock."
        :sameDeliverySetting(value,expected)?"Already matches the saved source.":null;
    return {profile,expected,reason};
  });
}

export function buildDeliverySettingPropagation(project:Project,sourceId:string,field:SharedDeliveryField,ids:string[]):DeliverySettingPropagation{
  const source=project.deliverables.find(p=>p.id===sourceId);
  if(!source || ids.length===0 || ids.length>=128 || new Set(ids).size!==ids.length)
    throw new Error("Select distinct descendants of the saved source.");
  const rows=deliveryFamilyRows(project,sourceId,field);
  const targets=ids.map(id=>{
    const row=rows.find(r=>r.profile.id===id);
    if(!row || row.reason)throw new Error("The selected variant is no longer eligible: "+id);
    return {profile_id:id,expected:row.expected};
  });
  return {project_id:project.id,generation:project.generation,branch_id:project.active_branch,
    expected_revision:project.revision,source_profile_id:sourceId,source:readDeliverySetting(source,field),targets};
}

/** Browser simulation uses the same reviewed scope; actual saving is Rust + Store. */
export function applyDeliverySettingPropagation(project:Project,proposal:DeliverySettingPropagation):DeliverableProfile[]{
  if(project.id!==proposal.project_id || project.generation!==proposal.generation ||
     project.active_branch!==proposal.branch_id || project.revision!==proposal.expected_revision)
    throw new Error("Delivery family source scope changed.");
  const current=buildDeliverySettingPropagation(project,proposal.source_profile_id,proposal.source.field,proposal.targets.map(t=>t.profile_id));
  if(!sameDeliverySetting(current.source,proposal.source) ||
     current.targets.some((t,i)=>!sameDeliverySetting(t.expected,proposal.targets[i].expected)))
    throw new Error("Delivery setting changed after review.");
  const ids=new Set(proposal.targets.map(t=>t.profile_id));
  return project.deliverables.map(profile=>ids.has(profile.id)
    ? {...profile,[proposal.source.field]:structuredClone(proposal.source.value)}
    : profile);
}
