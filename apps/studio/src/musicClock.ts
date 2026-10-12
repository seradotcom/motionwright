/** Author-marked music grid and source-bound scene-cut review.
 *
 * This is an editorial timing contract, not beat detection, decoded audio
 * measurement, mix rendering or evidence that a licensed track is owned.
 * Every actual cut is rechecked by the Rust domain behind the native SDK.
 */
import type { Project, RationalTime } from "./types";

type Fraction = { n: bigint; d: bigint };
const MAX_TIME = 86_400n;
const MAX_GRID_BEATS = 512;
const SHA = /^[0-9a-f]{64}$/;
const SIGNED_MAX = (1n << 63n) - 1n;

function gcd(a: bigint, b: bigint): bigint {
  while (b) [a, b] = [b, a % b];
  return a === 0n ? 1n : a;
}
function normalized(value: Fraction): Fraction {
  if (value.d === 0n) throw new Error("Clock denominator cannot be zero.");
  const sign = value.d < 0n ? -1n : 1n;
  const n = value.n * sign, d = value.d * sign;
  const common = gcd(n < 0n ? -n : n, d);
  const result = { n: n / common, d: d / common };
  if (result.d <= 0n || result.d > SIGNED_MAX || result.n > SIGNED_MAX ||
      result.n < -SIGNED_MAX - 1n) throw new Error("Rational music clock overflow.");
  return result;
}
function fraction(at: RationalTime): Fraction {
  if (!/^-?[0-9]+$/.test(at.num) || !/^-?[0-9]+$/.test(at.den)) {
    throw new Error("Music clock requires decimal integer rational components.");
  }
  return normalized({n: BigInt(at.num), d: BigInt(at.den)});
}
function asTime(value: Fraction): RationalTime {
  const t = normalized(value);
  return {num: String(t.n), den: String(t.d)};
}
function compare(a: Fraction, b: Fraction): number {
  const x = a.n * b.d - b.n * a.d;
  return x < 0n ? -1 : x > 0n ? 1 : 0;
}
const add = (a: Fraction, b: Fraction): Fraction =>
  normalized({n:a.n*b.d+b.n*a.d,d:a.d*b.d});
const subtract = (a: Fraction, b: Fraction): Fraction =>
  normalized({n:a.n*b.d-b.n*a.d,d:a.d*b.d});
const ZERO: Fraction = {n:0n,d:1n};
const MAX_DURATION: Fraction = {n:MAX_TIME,d:1n};

export function formatMarkedBeat(at: RationalTime): string {
  const t = fraction(at);
  // Only exact millisecond stamps are suitable for the manual paste editor.
  const ms = t.n * 1000n / t.d;
  if (t.n < 0n) throw new Error("Music clock cannot contain negative beats.");
  if (ms * t.d !== t.n * 1000n) return String(t.n) + "/" + String(t.d);
  return (ms / 1000n).toString() + "." + (ms % 1000n).toString().padStart(3,"0");
}

export function parseMarkedBeatGrid(lines: string): RationalTime[] {
  if (lines.length > 8192) throw new Error("Beat grid text exceeds the bounded source budget.");
  const items = lines.split(/\r?\n/u).map(x => x.trim()).filter(Boolean);
  if (!items.length || items.length > MAX_GRID_BEATS) {
    throw new Error("Provide 1–512 exact author-marked beat timestamps.");
  }
  let prev: Fraction | null = null;
  return items.map((raw, index) => {
    const decimal = /^([0-9]{1,5})(?:\.([0-9]{1,3}))?$/.exec(raw);
    const ratio = /^([0-9]{1,10})\/([1-9][0-9]{0,9})$/.exec(raw);
    if (!decimal && !ratio) throw new Error("Beat " + (index+1) + " requires seconds.mmm or an exact seconds numerator/denominator.");
    const value = decimal
      ? normalized({n:BigInt(decimal[1]) * 1000n + BigInt((decimal[2] ?? "").padEnd(3,"0") || "0"),d:1000n})
      : normalized({n:BigInt(ratio![1]),d:BigInt(ratio![2])});
    if (compare(value, MAX_DURATION) > 0 || (prev && compare(value,prev) <= 0)) {
      throw new Error("Beat grid must use strictly increasing times within 24 hours.");
    }
    prev = value;
    return asTime(value);
  });
}

export function generateAuthorBeatGrid(
  bpm: number, count: number, offset: RationalTime,
): RationalTime[] {
  if (!Number.isInteger(bpm) || bpm < 30 || bpm > 300 ||
      !Number.isInteger(count) || count < 1 || count > MAX_GRID_BEATS) {
    throw new Error("Beat grid requires declared 30–300 integer BPM and 1–512 ticks.");
  }
  const start = fraction(offset);
  if (compare(start,ZERO)<0) throw new Error("Music offset cannot be negative.");
  const period = normalized({n:60n,d:BigInt(bpm)});
  const beats: RationalTime[] = [];
  for (let i=0;i<count;i++) {
    const next=add(start,{n:period.n*BigInt(i),d:period.d});
    if (compare(next,MAX_DURATION)>0) throw new Error("Generated beat exceeds the 24-hour editor clock.");
    beats.push(asTime(next));
  }
  return beats;
}

export interface MusicCutProposal {
  scene_id: string;
  beat_index: number;
  asset_id: string;
  source_sha256: string;
  original_end: RationalTime;
  target_end: RationalTime;
  old_duration: RationalTime;
  new_duration: RationalTime;
  ripple: RationalTime;
  downstream_scene_ids: string[];
  affected_cue_count: number;
  affected_marker_count: number;
  affected_transcript_count: number;
  approval_invalidated: boolean;
  anchors_need_acknowledgement: boolean;
}

export function inspectMusicCut(
  project: Project, sceneId: string, beatIndex: number, expectedSourceSha256?: string,
): MusicCutProposal {
  const plan = project.production_design?.plan;
  if (!plan || plan.clock.kind !== "music") {
    throw new Error("Save an owner-marked music clock before aligning a scene cut.");
  }
  // Retain TypeScript's discriminated clock narrowing inside find callbacks.
  const clock = plan.clock;
  const source = project.assets.find(x=>x.id===clock.asset_id && x.media_type.startsWith("audio/"));
  if (!source?.content_sha256 || !SHA.test(source.content_sha256) ||
      (expectedSourceSha256 && expectedSourceSha256!==source.content_sha256)) {
    throw new Error("Selected audio source identity changed. Revalidate its digest.");
  }
  if (!Number.isInteger(beatIndex) || beatIndex<0 || beatIndex>=plan.clock.beats.length) {
    throw new Error("Selected music beat is missing from the saved grid.");
  }
  const index=project.scenes.findIndex(scene=>scene.id===sceneId);
  if (index<0) throw new Error("Scene is no longer in the project.");
  const scene=project.scenes[index];
  const target=fraction(plan.clock.beats[beatIndex]);
  const start=fraction(scene.start);
  const duration=fraction(scene.duration);
  const newDuration=subtract(target,start);
  if (compare(newDuration,ZERO)<=0 || compare(newDuration,MAX_DURATION)>0) {
    throw new Error("Music beat must produce a positive scene duration within one day.");
  }
  const delta=subtract(newDuration,duration);
  if (compare(delta,ZERO)===0) throw new Error("Selected beat already matches the scene cut.");
  const affected=project.scenes.slice(index);
  if (project.locks.some(lock=>
    lock.kind==="timing" && (lock.resource==="project:"+project.id ||
    affected.some(item=>lock.resource==="scene:"+item.id))
  )) throw new Error("A project or downstream scene timing lock protects this cut.");
  if (project.deliverables.some(profile=>
    profile.timing_locked && (!profile.included_scene_ids.length ||
    affected.some(item=>profile.included_scene_ids.includes(item.id)))
  )) throw new Error("A timing-locked delivery profile protects this cut.");
  if (scene.nodes.some(node=>node.keyframes.some(key=>
      compare(fraction(key.at),newDuration)>=0))) {
    throw new Error("This cut would strand an authored canvas keyframe.");
  }
  if (scene.beats.some(beat=>compare(add(fraction(beat.start),fraction(beat.duration)),newDuration)>0)) {
    throw new Error("This cut would strand an authored scene beat.");
  }
  const oldEnd=add(start,duration);
  const firstAffected=compare(target,oldEnd)<0 ? target : oldEnd;
  const cueCount=project.audio.cues.filter(cue=>compare(fraction(cue.at),firstAffected)>=0).length;
  const markerCount=project.markers.filter(marker=>compare(fraction(marker.at),firstAffected)>=0).length;
  const transcriptCount=project.audio.transcript.filter(segment=>compare(fraction(segment.end),firstAffected)>0).length;
  return {
    scene_id:scene.id,beat_index:beatIndex,asset_id:source.id,source_sha256:source.content_sha256,
    original_end:asTime(oldEnd),target_end:asTime(target),
    old_duration:asTime(duration),new_duration:asTime(newDuration),ripple:asTime(delta),
    downstream_scene_ids:affected.slice(1).map(s=>s.id),
    affected_cue_count:cueCount,affected_marker_count:markerCount,
    affected_transcript_count:transcriptCount,approval_invalidated:!!plan.approval,
    anchors_need_acknowledgement:cueCount+markerCount+transcriptCount>0,
  };
}

export function applyMusicCutToProject(project: Project, proposal: MusicCutProposal): void {
  const scene=project.scenes.find(s=>s.id===proposal.scene_id);
  if (!scene) throw new Error("Scene is no longer in the project.");
  scene.duration=structuredClone(proposal.new_duration);
  scene.status="draft";
  let cursor: Fraction=ZERO;
  for (const item of project.scenes) {
    item.start=asTime(cursor);
    cursor=add(cursor,fraction(item.duration));
  }
  if (project.production_design?.plan) project.production_design.plan.approval=null;
  // Important: absolute-time cues, markers and transcript segments remain
  // unchanged; the operator must manually resolve the disclosed impacts.
}


export interface MusicConformTarget {scene_id:string;beat_index:number}
export interface MusicConformSceneChange {
  scene_id:string;
  old_start:RationalTime;
  new_start:RationalTime;
  old_duration:RationalTime;
  new_duration:RationalTime;
  beat_index:number|null;
  changed:boolean;
}
export interface MusicConformReview {
  project_id:string;
  generation:string;
  base_revision:number;
  asset_id:string;
  source_sha256:string;
  targets:MusicConformTarget[];
  scenes:MusicConformSceneChange[];
  changed_scene_count:number;
  original_duration:RationalTime;
  conformed_duration:RationalTime;
  affected_cue_count:number;
  affected_marker_count:number;
  affected_transcript_count:number;
  approval_invalidated:boolean;
  anchors_need_acknowledgement:boolean;
}

/** Mirrors the trusted Rust review for the responsive Studio demo only.
 * The real service always re-evaluates this independently before CAS. */
export function inspectMusicConform(
  project:Project,targets:MusicConformTarget[],expectedDigest:string,
):MusicConformReview {
  if(targets.length<2||targets.length>16)
    throw new Error("Choose 2–16 explicit music cut boundaries.");
  if(!SHA.test(expectedDigest))throw new Error("Audio source SHA-256 is invalid.");
  const plan=project.production_design?.plan;
  if(!plan||plan.clock.kind!=="music")
    throw new Error("Save an author-marked production music clock first.");
  const clock=plan.clock;
  const source=project.assets.find(asset=>asset.id===clock.asset_id
    && asset.media_type.startsWith("audio/"));
  if(!source||source.content_sha256!==expectedDigest)
    throw new Error("Owner music source digest changed; review this clock again.");
  const indices=new Map(project.scenes.map((scene,index)=>[scene.id,index]));
  const selected=new Map<string,{beat:number;at:Fraction}>();
  let previous=-1;
  for(const target of targets){
    const pos=indices.get(target.scene_id);
    if(pos===undefined)throw new Error("Scene is not present in this project.");
    if(pos<=previous)throw new Error("Music targets must be unique in source scene order.");
    if(!Number.isInteger(target.beat_index)||target.beat_index<0||
       target.beat_index>=clock.beats.length)
      throw new Error("Music beat index is outside the saved source grid.");
    const at=fraction(clock.beats[target.beat_index]);
    if(compare(at,ZERO)<0||compare(at,MAX_DURATION)>0)
      throw new Error("Selected beat exceeds project timing bounds.");
    selected.set(target.scene_id,{beat:target.beat_index,at});
    previous=pos;
  }
  let cursor:Fraction=ZERO;
  let originalEnd:Fraction=ZERO;
  let earliest:Fraction|null=null;
  const changes:MusicConformSceneChange[]=[];
  for(const scene of project.scenes){
    const beforeStart=fraction(scene.start),beforeDuration=fraction(scene.duration);
    const oldEnd=add(beforeStart,beforeDuration);
    originalEnd=oldEnd;
    const selectedBoundary=selected.get(scene.id);
    const duration=selectedBoundary?subtract(selectedBoundary.at,cursor):beforeDuration;
    if(compare(duration,ZERO)<=0||compare(duration,MAX_DURATION)>0)
      throw new Error("Music conform requires positive bounded scene durations.");
    if(selectedBoundary) {
      if(scene.nodes.some(node=>node.keyframes.some(key=>
         compare(fraction(key.at),duration)>=0)))
        throw new Error("Music conform would strand a scene-local canvas keyframe.");
      if(scene.beats.some(beat=>
        compare(add(fraction(beat.start),fraction(beat.duration)),duration)>0))
        throw new Error("Music conform would strand an authored scene beat.");
    }
    const newEnd=add(cursor,duration);
    if(selectedBoundary&&compare(newEnd,selectedBoundary.at)!==0)
      throw new Error("Conformed scene end does not equal the selected beat.");
    if(selectedBoundary&&compare(newEnd,oldEnd)!==0){
      const boundary=compare(newEnd,oldEnd)<0?newEnd:oldEnd;
      if(!earliest||compare(boundary,earliest)<0)earliest=boundary;
    }
    const changed=compare(cursor,beforeStart)!==0||compare(duration,beforeDuration)!==0;
    changes.push({
      scene_id:scene.id,old_start:asTime(beforeStart),new_start:asTime(cursor),
      old_duration:asTime(beforeDuration),new_duration:asTime(duration),
      beat_index:selectedBoundary?.beat??null,changed,
    });
    cursor=newEnd;
  }
  const affected=changes.filter(change=>change.changed);
  if(!affected.length)throw new Error("All selected music cuts already match.");
  if(project.locks.some(lock=>lock.kind==="timing"&&(
    lock.resource==="project:"+project.id ||
    affected.some(scene=>lock.resource==="scene:"+scene.scene_id)
  )))throw new Error("Protected project or scene timing prevents the music conform.");
  if(project.deliverables.some(profile=>profile.timing_locked&&(
    !profile.included_scene_ids.length ||
    affected.some(scene=>profile.included_scene_ids.includes(scene.scene_id))
  )))throw new Error("A timing-locked delivery profile blocks the music conform.");
  if(!earliest)throw new Error("No selected boundary actually changed.");
  const threshold:Fraction=earliest;
  const cues=project.audio.cues.filter(cue=>
    compare(fraction(cue.at),threshold)>=0).length;
  const markers=project.markers.filter(marker=>
    compare(fraction(marker.at),threshold)>=0).length;
  const transcript=project.audio.transcript.filter(segment=>
    compare(fraction(segment.end),threshold)>0).length;
  return {
    project_id:project.id,generation:project.generation,base_revision:project.revision,
    asset_id:source.id,source_sha256:expectedDigest,
    targets:targets.map(target=>({...target})),scenes:changes,
    changed_scene_count:affected.length,
    original_duration:asTime(originalEnd),conformed_duration:asTime(cursor),
    affected_cue_count:cues,affected_marker_count:markers,
    affected_transcript_count:transcript,approval_invalidated:!!plan.approval,
    anchors_need_acknowledgement:cues+markers+transcript>0,
  };
}

export function applyMusicConformToProject(project:Project,review:MusicConformReview):void {
  if(project.id!==review.project_id||project.generation!==review.generation||
     project.revision!==review.base_revision)
    throw new Error("Music conform source project changed during review.");
  for(let i=0;i<project.scenes.length;i++){
    const scene=project.scenes[i],after=review.scenes[i];
    if(scene.id!==after?.scene_id)throw new Error("Conform scene order became stale.");
    scene.start=structuredClone(after.new_start);
    scene.duration=structuredClone(after.new_duration);
    if(after.changed)scene.status="draft";
  }
  if(project.production_design?.plan)project.production_design.plan.approval=null;
  // Recorded global audio cues/transcripts/markers NEVER move implicitly.
}
