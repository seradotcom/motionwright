import {describe,expect,it} from "vitest";
import {fixtureProject} from "./fixture";
import {originalVoiceAuditionRequest,verifyOriginalVoiceAuditionBytes,voiceAuditionSource} from "./voiceAudition";
import type {Project} from "./types";
const trackId="11111111-1111-4111-8111-111111111111";
const assetId="22222222-2222-4222-8222-222222222222";
const sha="a".repeat(64);
function fixture(){
  const p:Project=structuredClone(fixtureProject);
  p.assets.push({id:assetId,name:"Synthetic take.wav",media_type:"audio/wav",content_sha256:sha,source_revision:null});
  p.audio.voice_tracks.push({id:trackId,asset_id:assetId,label:"Candidate",sample_rate_hz:8000,channels:1,
    measured_duration:{num:"1",den:"1"},source_sha256:sha,loudness_lufs:null,true_peak_dbfs:null});
  return p;
}
describe("original voice audition",()=>{
  it("binds one retained measured take without relying on the active take or writing project state",()=>{
    const p=fixture(),before=structuredClone(p);
    expect(originalVoiceAuditionRequest(p,trackId,true)).toEqual({
      project_id:p.id,generation:p.generation,revision:p.revision,track_id:trackId,
      source_sha256:sha,owner_attests_preview_rights:true,
    });
    expect(p).toEqual(before);
    expect(()=>originalVoiceAuditionRequest(p,trackId,false)).toThrow(/permission/);
  });
  it("rejects mismatched asset identity and fractional measured clocks",()=>{
    const p=fixture(),track=p.audio.voice_tracks.find(t=>t.id===trackId)!;
    p.assets.find(a=>a.id===assetId)!.content_sha256="b".repeat(64);
    expect(()=>voiceAuditionSource(p,trackId)).toThrow(/exact imported/);
    p.assets.find(a=>a.id===assetId)!.content_sha256=sha;
    track.measured_duration={num:"1",den:"7"};
    expect(()=>voiceAuditionSource(p,trackId)).toThrow(/exact sample clock/);
  });
  it("rejects unsupported channels, oversized and long sources",()=>{
    const p=fixture(),track=p.audio.voice_tracks.find(t=>t.id===trackId)!;
    track.channels=3;expect(()=>voiceAuditionSource(p,trackId)).toThrow(/mono\/stereo/);
    track.channels=2;track.sample_rate_hz=96000;track.measured_duration={num:"90",den:"1"};
    expect(()=>voiceAuditionSource(p,trackId)).toThrow(/16 MiB/);
    track.channels=1;track.sample_rate_hz=8000;track.measured_duration={num:"181",den:"1"};
    expect(()=>voiceAuditionSource(p,trackId)).toThrow(/180 seconds/);
  });
  it("accepts only the exact binary source digest and rejects a same-size tamper",async()=>{
    const wav=new Uint8Array(44),v=new DataView(wav.buffer);
    wav.set([82,73,70,70,0,0,0,0,87,65,86,69]);v.setUint32(4,36,true);
    const hash=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",wav.buffer)))
      .map(n=>n.toString(16).padStart(2,"0")).join("");
    expect(await verifyOriginalVoiceAuditionBytes(wav,hash)).toEqual(wav);
    wav[43]=1;await expect(verifyOriginalVoiceAuditionBytes(wav,hash)).rejects.toThrow(/digest differs/);
    await expect(verifyOriginalVoiceAuditionBytes(Array(44).fill(256),hash)).rejects.toThrow(/binary/);
  });
});
