import { describe,expect,it } from "vitest";
import { fixtureProject } from "./fixture";
import { originalSfxWaveformFrame, originalSfxWaveformMatches } from "./originalSfxWaveform";
import type { OriginalSfxWaveform } from "./originalSfxWaveform";
import type { Project } from "./types";
import type { SemanticSfxCue } from "./semanticSfx";

const SHA="a".repeat(64);
const OBSERVED="b".repeat(64);
const ASSET_ID="00000000-0000-4000-8000-000000000301";
const CUE_ID="00000000-0000-4000-8000-000000000302";
function source():{project:Project;cue:SemanticSfxCue;report:OriginalSfxWaveform} {
  const project=structuredClone(fixtureProject);
  const scene=project.scenes[0];
  project.assets.push({id:ASSET_ID,name:"Owner original click.wav",
    media_type:"audio/wav",content_sha256:SHA,source_revision:"owned"});
  const cue:SemanticSfxCue={
    schema:"motionwright.semantic-sfx/1",id:CUE_ID,
    scene_id:scene.id,event:"ui_focus",anchor:{kind:"scene_start"},
    offset_frames:3,source_asset_id:ASSET_ID,source_sha256:SHA,
    source_frames:3,gain_milli_db:-12000,attack_frames:0,release_frames:1,
    original_source_attestation:"Owner original numeric source",
  };
  project.audio.sfx_cues=[cue];
  const report:OriginalSfxWaveform={
    schema:"motionwright.original-sfx-waveform/1",
    project_id:project.id,generation:project.generation,
    revision:project.revision,cue_id:CUE_ID,asset_id:ASSET_ID,
    source_sha256:SHA,source_frames:3,
    sample_rate_hz:48000,channels:2,samples_per_peak:1600,
    left_min_pcm16:[-1200,-3000,-900],
    left_max_pcm16:[1200,3000,900],
    right_min_pcm16:[-600,-1500,-1800],
    right_max_pcm16:[600,1500,1800],
    sample_peak_pcm16:3000,
    measured_sha256:OBSERVED,
    evidence_scope:"owner-CAS-verified-original-PCM16-per-frame-extrema-NOT-listening-or-master",
  };
  return {project,cue,report};
}
describe("v0.5 original source WAV stereo waveform readback",()=>{
  it("reads measured per-frame extrema without changing the creative model",()=>{
    const {project,cue,report}=source();
    const before=structuredClone(project);
    expect(originalSfxWaveformMatches(project,cue,report)).toBe(true);
    expect(originalSfxWaveformFrame(3,0))
      .toEqual({frame:3,sample:4800,seconds:.1});
    expect(originalSfxWaveformFrame(3,2))
      .toEqual({frame:5,sample:8000,seconds:5/30});
    expect(project).toEqual(before);
  });
  it("refuses stale generation, revision, wrong cue and foreign source digest",()=>{
    const {project,cue,report}=source();
    for(const variant of [
      {...report,generation:"foreign"},
      {...report,revision:project.revision+1},
      {...report,cue_id:"foreign"},
      {...report,asset_id:"foreign"},
      {...report,source_sha256:"c".repeat(64)},
      {...report,evidence_scope:"creative-PASS"},
      {...report,measured_sha256:"invalid"},
    ]){
      expect(originalSfxWaveformMatches(project,cue,variant)).toBe(false);
    }
    expect(originalSfxWaveformMatches({...project,revision:project.revision+1},cue,report))
      .toBe(false);
  });
  it("rejects fabricated, incomplete, oversized and contradictory PCM peaks",()=>{
    const {project,cue,report}=source();
    for(const variant of [
      {...report,left_min_pcm16:[-1200,-3000]},
      {...report,right_max_pcm16:[600,1500,99999]},
      {...report,left_min_pcm16:[-1200,4000,-900]},
      {...report,source_frames:181},
      {...report,channels:1},
      {...report,sample_rate_hz:44100},
      {...report,samples_per_peak:1000},
      {...report,sample_peak_pcm16:3001},
    ]){
      expect(originalSfxWaveformMatches(project,cue,variant)).toBe(false);
    }
  });
  it("never guesses frame offsets from negative or fractional clocks",()=>{
    for(const [a,b] of [[-1,0],[0,-1],[3.5,0],[1,1.5],[36000,1]]){
      expect(originalSfxWaveformFrame(a,b)).toBeNull();
    }
  });
});
