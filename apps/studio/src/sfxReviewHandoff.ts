/** Open, human-authored native SFX listening issues are ordinary creative
 * Reviews. This is a read-only handoff back to the original cue editor,
 * NOT a second issue tracker or automatic creative-quality verdict.
 */
import type { Project, RationalTime } from "./types";

const DIGEST=/^[0-9a-f]{64}$/;
export interface OriginalSfxReviewHandoff {
  reviewId:string;
  cueId:string;
  sceneId:string;
  sceneName:string;
  profileId:string;
  reviewRevision:number;
  fromFrame:number;
  toFrame:number;
  observation:string;
  sourceSha256:string;
  sameOriginalWav:boolean;
  status:"open"|"needs_recheck";
}

function frameAt30(value:RationalTime|null):number|null {
  if(!value)return null;
  try {
    const n=BigInt(value.num),d=BigInt(value.den);
    if(d<=0n||n<0n||(n*30n)%d!==0n)return null;
    const f=n*30n/d;
    return f<=36000n?Number(f):null;
  }catch{return null;}
}

export function openOriginalSfxReviewHandoffs(project:Project):OriginalSfxReviewHandoff[]{
  const original=new Map((project.audio.sfx_cues??[]).map(cue=>[cue.id,cue]));
  const scenes=new Map(project.scenes.map(scene=>[scene.id,scene]));
  const profiles=new Set(project.deliverables.map(profile=>profile.id));
  const rows:OriginalSfxReviewHandoff[]=[];
  for(const review of project.reviews){
    if(review.kind!=="creative" || !["open","needs_recheck"].includes(review.status)
      ||review.anchor.branch_id!==project.active_branch
      ||review.body.length>3000)continue;
    let event:Record<string,unknown>;
    try {
      const parsed:unknown=JSON.parse(review.body);
      if(!parsed || typeof parsed!=="object" || Array.isArray(parsed))continue;
      event=parsed as Record<string,unknown>;
    }catch{continue;}
    if(event.schema!=="motionwright.manual-native-sfx-listening-issue/1"
      ||event.verdict!=="needs_adjustment"
      ||event.interpretation!=="human_annotated_after_player_play_event_not_creative_approval"
      ||!event.source ||typeof event.source!=="object"||Array.isArray(event.source))
      continue;
    const src=event.source as Record<string,unknown>;
    if(typeof src.cue_id!=="string"||typeof src.scene_id!=="string"
      ||typeof src.deliverable_id!=="string"
      ||typeof src.project_id!=="string"||typeof src.generation!=="string"
      ||typeof src.original_wav_sha256!=="string"
      ||typeof src.exact_mix_pcm_sha256!=="string"
      ||typeof src.native_mp4_sha256!=="string"
      ||typeof src.start_frame!=="number"||typeof src.end_frame_exclusive!=="number"
      ||typeof src.revision!=="number"
      ||typeof event.observation!=="string")
      continue;
    const cue=original.get(src.cue_id);
    const scene=scenes.get(src.scene_id);
    const start=frameAt30(review.anchor.start),end=frameAt30(review.anchor.end);
    const observation=event.observation.trim();
    if(!cue||!scene||!profiles.has(src.deliverable_id)
      ||!DIGEST.test(src.original_wav_sha256)
      ||!DIGEST.test(src.exact_mix_pcm_sha256)
      ||!DIGEST.test(src.native_mp4_sha256)
      ||src.scene_id!==cue.scene_id
      ||src.project_id!==project.id
      ||src.generation!==project.generation
      ||src.revision!==review.anchor.revision
      ||src.revision>project.revision
      ||src.video_frame_rate!=="30/1"
      ||src.sample_rate_hz!==48000
      ||typeof src.absolute_pcm_sample!=="number"
      ||src.absolute_pcm_sample!==src.start_frame*1600
      ||review.anchor.resource!=="scene:"+cue.scene_id
      ||review.anchor.profile_id!==src.deliverable_id
      ||start===null||end===null
      ||start!==src.start_frame||end!==src.end_frame_exclusive
      ||!Number.isSafeInteger(start)||!Number.isSafeInteger(end)
      ||start<0||end<=start||end>36000
      ||observation.length<10||[...observation].length>640
      ||/[\u0000-\u0009\u000b-\u001f\u007f]/u.test(observation))
      continue;
    rows.push({
      reviewId:review.id,cueId:cue.id,sceneId:cue.scene_id,
      sceneName:scene.name,profileId:src.deliverable_id,
      reviewRevision:review.anchor.revision,
      fromFrame:start,toFrame:end,observation,
      sourceSha256:src.original_wav_sha256,
      sameOriginalWav:cue.source_sha256===src.original_wav_sha256,
      status:review.status as OriginalSfxReviewHandoff["status"],
    });
  }
  return rows.sort((a,b)=>a.reviewRevision-b.reviewRevision
    ||a.fromFrame-b.fromFrame||a.reviewId.localeCompare(b.reviewId));
}
