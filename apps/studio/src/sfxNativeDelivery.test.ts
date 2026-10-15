import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { originalSfxAvReadiness, voiceOnlyMasterMayExcludeSfx } from "./sfxNativeDelivery";
import { SFX_CUE_SCHEMA } from "./semanticSfx";
import type { Project } from "./types";

const SHA = "a".repeat(64);
const sceneId = "00000000-0000-4000-8000-0000000000f0";
const assetId = "00000000-0000-4000-8000-0000000000f1";

function source():Project {
  const p = structuredClone(fixtureProject);
  p.scenes = [p.scenes[0]];
  p.scenes[0].id = sceneId;
  p.scenes[0].renderer = "motion-canvas";
  p.scenes[0].start = {num:"0",den:"1"};
  p.scenes[0].duration = {num:"6",den:"1"};
  p.scenes[0].beats = [];
  p.assets.push({id:assetId,name:"Original numerical PCM",
    media_type:"audio/wav",content_sha256:SHA,source_revision:"owner-original"});
  p.audio.sfx_cues = [{
    schema:SFX_CUE_SCHEMA,
    id:"00000000-0000-4000-8000-0000000000f2",
    scene_id:sceneId,event:"ui_focus",anchor:{kind:"scene_start"},
    offset_frames:5,source_asset_id:assetId,source_sha256:SHA,
    source_frames:30,gain_milli_db:-12000,attack_frames:3,release_frames:8,
    original_source_attestation:"Original owner-authored WAV",
  }];
  return p;
}
const check = (p:Project, frames:number|null=180, registered=true, desktop=true, dirty=false)=>
  originalSfxAvReadiness(p,p.deliverables[0],frames,registered,desktop,dirty);

describe("MW05-E08 Studio native semantic SFX delivery readiness",()=>{
  it("checks a real preview against exact original scene timing without claiming WAV bytes",()=>{
    const p=source();
    const r=check(p);
    expect(r.ready).toBe(true);
    expect(r.frames).toBe(180);
    expect(r.cueCount).toBe(1);
    expect(r.sourceAuthority).toBe("editorial_only");
    expect(r.reason).toMatch(/Native CAS byte verification/);
  });
  it("prevents silent omission of voice/music or native preview authority",()=>{
    const p=source();
    expect(check(p,null).ready).toBe(false);
    expect(check(p,180,false).ready).toBe(false);
    expect(check(p,180,true,false).ready).toBe(false);
    expect(check(p,180,true,true,true).ready).toBe(false);
    p.deliverables[0].voice_track_id="00000000-0000-4000-8000-0000000000ab";
    expect(check(p).reason).toMatch(/cannot combine narration/);
  });
  it("refuses alternate codecs, frame rates and manually restricted scenes",()=>{
    const p=source(),d=p.deliverables[0];
    d.frame_rate={num:"30000",den:"1001"};
    expect(check(p).ready).toBe(false);
    d.frame_rate={num:"30",den:"1"}; d.audio_sample_rate_hz=44100;
    expect(check(p).ready).toBe(false);
    d.audio_sample_rate_hz=48000;d.included_scene_ids=[sceneId];
    expect(check(p).ready).toBe(false);
    d.included_scene_ids=[];d.container="mov";
    expect(check(p).ready).toBe(false);
  });
  it("never silently promotes an old voice-only master while original SFX exist",()=>{
    const original=source();
    expect(voiceOnlyMasterMayExcludeSfx(original)).toBe(false);
    expect(voiceOnlyMasterMayExcludeSfx(structuredClone(fixtureProject))).toBe(true);
    original.deliverables[0].voice_track_id="00000000-0000-4000-8000-0000000000ab";
    expect(voiceOnlyMasterMayExcludeSfx(original)).toBe(false);
    expect(check(original).ready).toBe(false);
    original.audio.sfx_cues=[];
    expect(voiceOnlyMasterMayExcludeSfx(original)).toBe(true);
  });
  it("guards exact scene cuts, SFX source and 900-frame native audio cap",()=>{
    const p=source();
    expect(check(p,179).ready).toBe(false);
    expect(check(p,901).ready).toBe(false);
    p.audio.sfx_cues![0].source_sha256="b".repeat(64);
    expect(check(p).ready).toBe(false);
    p.audio.sfx_cues![0].source_sha256=SHA;
    p.scenes[0].duration={num:"12",den:"5"}; // exact 72 frames, but preview is stale.
    expect(check(p).ready).toBe(false);
  });
  it("accepts contiguous multi-scene editorial placements within one real Film run",()=>{
    const p=source();
    const first=structuredClone(p.scenes[0]);
    first.duration={num:"2",den:"1"};
    const second=structuredClone(first);
    second.id="00000000-0000-4000-8000-0000000000f3";
    second.name="Following shot";
    second.start={num:"2",den:"1"};
    second.duration={num:"4",den:"1"};
    p.scenes=[first,second];
    p.audio.sfx_cues![0].scene_id=second.id;
    p.audio.sfx_cues![0].offset_frames=2;
    expect(check(p).ready).toBe(true);
    second.start={num:"21",den:"10"}; // noncontiguous and non-frame-exact.
    expect(check(p).ready).toBe(false);
  });
});
