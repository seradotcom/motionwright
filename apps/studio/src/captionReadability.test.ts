import {describe,expect,it} from "vitest";
import {fixtureBootstrap} from "./fixture";
import {inspectCaptionReadability} from "./captionReadability";
import type {DeliverableProfile,Project,TranscriptSegment,VoiceTrack} from "./types";

function fixture() {
  const project:Project=structuredClone(fixtureBootstrap.project);
  const profile:DeliverableProfile=project.deliverables[0];
  const voice:VoiceTrack={
    id:"00000000-0000-0000-0000-0000000000b1",
    asset_id:"00000000-0000-0000-0000-000000000031",
    label:"Review voice",
    sample_rate_hz:48000,channels:2,source_sha256:"b".repeat(64),
    measured_duration:{num:"36",den:"1"},
    loudness_lufs:null,true_peak_dbfs:null,
  };
  project.audio.voice_tracks.push(voice);
  profile.voice_track_id=voice.id;
  function segment(id:string,start:number,end:number,text:string):TranscriptSegment {
    return {
      id,voice_track_id:voice.id,
      start:{num:String(start),den:"1"},end:{num:String(end),den:"1"},
      text,speaker:null,alignment:{kind:"manual"},
    };
  }
  return {project,profile,voice,segment};
}
function codes(project:Project,profile:DeliverableProfile) {
  return inspectCaptionReadability(project,profile).findings.map(f=>f.code);
}
describe("source-bound caption legibility preflight",()=>{
  it("does not invent a transcript for a saved caption profile",()=>{
    const {project,profile}=fixture();
    const result=inspectCaptionReadability(project,profile);
    expect(result.verdict).toBe("STRUCTURAL_BLOCKERS");
    expect(result.findings.some(f=>f.code==="VOICE_TRANSCRIPT_MISSING")).toBe(true);
    expect(result).toMatchObject({
      schema:"motionwright.caption-readability/1",
      projectId:project.id,generation:project.generation,
      revision:project.revision,profileId:profile.id,
    });
  });
  it("does not assess a profile with captions disabled",()=>{
    const {project,profile}=fixture();
    profile.captions=false;
    profile.burn_in_captions=true;
    const result=inspectCaptionReadability(project,profile);
    expect(result).toMatchObject({verdict:"CAPTIONS_DISABLED",blocks:0,reviews:0});
  });
  it("retains a simple manual source cue without claiming audio or visual approval",()=>{
    const {project,profile,segment}=fixture();
    project.audio.transcript.push(segment("one",1,4,"A clear short caption."));
    const before=structuredClone(project);
    const result=inspectCaptionReadability(project,profile);
    expect(result.verdict).toBe("NO_AUTOMATED_FLAGS");
    expect(result.selectedSegments).toBe(1);
    expect(result.inspectedSegments).toBe(1);
    expect(result.findings).toEqual([]);
    expect(result.guidance).toContain("No source speech match");
    expect(project).toEqual(before);
  });
  it("flags high reading density without blocking trusted source export",()=>{
    const {project,profile,segment}=fixture();
    project.audio.transcript.push(segment("fast",1,2,"Long captions can exceed the chosen rate, and must be checked by an editor."));
    const result=inspectCaptionReadability(project,profile);
    expect(result.verdict).toBe("EDITOR_REVIEW");
    expect(codes(project,profile)).toContain("FAST_READING");
    expect(result.blocks).toBe(0);
  });
  it("flags overly long explicit lines and three authored lines",()=>{
    const {project,profile,segment}=fixture();
    project.audio.transcript.push(segment("long",1,5,
      "The longest single line of text exceeds the explicit forty two code point threshold\nLine two\nLine three"));
    const flagged=codes(project,profile);
    expect(flagged).toContain("LONG_LINE");
    expect(flagged).toContain("MANY_LINES");
  });
  it("flags brief and long cues without changing their timing",()=>{
    const {project,profile,segment}=fixture();
    project.audio.transcript.push(segment("brief",1,2,"Brief"));
    project.audio.transcript[0].end={num:"1499",den:"1000"};
    project.audio.transcript.push(segment("long",10,18,"Long"));
    const flagged=codes(project,profile);
    expect(flagged).toContain("BRIEF_DISPLAY");
    expect(flagged).toContain("LONG_DISPLAY");
  });
  it("detects exact rational overlap across differently represented clocks",()=>{
    const {project,profile,segment}=fixture();
    const a=segment("first",1,3,"First cue");
    const b=segment("second",2,4,"Second cue");
    a.start={num:"1000000000",den:"1000000000"};
    b.start={num:"3",den:"2"};
    project.audio.transcript.push(b,a);
    expect(codes(project,profile)).toContain("OVERLAPPING_CAPTIONS");
  });
  it("treats exact adjacent end/start boundaries as nonoverlapping",()=>{
    const {project,profile,segment}=fixture();
    const a=segment("first",1,2,"First cue");
    const b=segment("second",2,3,"Next cue");
    b.start={num:"2000000000",den:"1000000000"};
    project.audio.transcript.push(b,a);
    expect(codes(project,profile)).not.toContain("OVERLAPPING_CAPTIONS");
  });
  it("refuses UNKNOWN timing, independent of readable-looking text",()=>{
    const {project,profile,segment}=fixture();
    const a=segment("unknown",1,4,"A normal caption");
    a.alignment={kind:"unknown"};
    project.audio.transcript.push(a);
    expect(codes(project,profile)).toContain("UNKNOWN_ALIGNMENT");
    expect(inspectCaptionReadability(project,profile).verdict).toBe("STRUCTURAL_BLOCKERS");
  });
  it("rejects stale measured alignment after the selected voice source changes",()=>{
    const {project,profile,segment}=fixture();
    const a=segment("measured",1,3,"Measured caption");
    a.alignment={kind:"measured",engine:"original-align",source_sha256:"0".repeat(64),
      confidence_millis:900};
    project.audio.transcript.push(a);
    expect(codes(project,profile)).toContain("STALE_MEASURED_SOURCE");
  });
  it("accepts a measured alignment bound to the current source digest as source metadata only",()=>{
    const {project,profile,segment,voice}=fixture();
    const a=segment("measured",1,3,"Current source");
    a.alignment={kind:"measured",engine:"original-align",
      source_sha256:voice.source_sha256,confidence_millis:900};
    project.audio.transcript.push(a);
    expect(codes(project,profile)).not.toContain("STALE_MEASURED_SOURCE");
    expect(inspectCaptionReadability(project,profile).guidance).toContain("No source speech match");
  });
  it("blocks ambiguous transcript tracks when the profile has no selected voice",()=>{
    const {project,profile,voice,segment}=fixture();
    profile.voice_track_id=null;
    project.audio.transcript.push(segment("one",1,3,"From voice one"));
    project.audio.transcript.push({...segment("two",3,5,"From voice two"),voice_track_id:"other"});
    expect(codes(project,profile)).toContain("SELECT_VOICE");
    expect(inspectCaptionReadability(project,profile).voiceTrackId).toBeNull();
    expect(voice.id).not.toBe("other");
  });
  it("refuses a caption that crosses a retained scene boundary in a custom cut",()=>{
    const {project,profile,segment}=fixture();
    profile.included_scene_ids=[project.scenes[1].id,project.scenes[3].id];
    project.audio.transcript.push(segment("cross",6,8,"Crosses into the next shot"));
    expect(codes(project,profile)).toContain("CROSSES_CUT");
  });
  it("counts omitted caption segments without treating them as delivered text",()=>{
    const {project,profile,segment}=fixture();
    profile.included_scene_ids=[project.scenes[1].id];
    project.audio.transcript.push(segment("excluded",1,3,"Never in selected output"));
    project.audio.transcript.push(segment("kept",8,10,"Retained caption"));
    const result=inspectCaptionReadability(project,profile);
    expect(result.omittedSegments).toBe(1);
    expect(result.inspectedSegments).toBe(1);
    expect(result.selectedSegments).toBe(2);
    expect(result.verdict).toBe("NO_AUTOMATED_FLAGS");
  });
  it("does not overlap distant source segments that become adjacent after scene rebase",()=>{
    const {project,profile,segment}=fixture();
    profile.included_scene_ids=[project.scenes[1].id,project.scenes[3].id];
    project.audio.transcript.push(segment("left",8,10,"First retained scene"));
    project.audio.transcript.push(segment("right",29,31,"Second retained scene"));
    const result=inspectCaptionReadability(project,profile);
    expect(result.omittedSegments).toBe(0);
    expect(codes(project,profile)).not.toContain("OVERLAPPING_CAPTIONS");
  });
  it("rejects duplicate/reordered/unrecognized cut identities without mutating them",()=>{
    const {project,profile,segment}=fixture();
    project.audio.transcript.push(segment("one",8,10,"A"));
    profile.included_scene_ids=[
      project.scenes[2].id,project.scenes[1].id,
      project.scenes[1].id,"no-such-scene",
    ];
    const original=[...profile.included_scene_ids];
    const flagged=codes(project,profile);
    expect(flagged).toContain("REORDERED_CUT");
    expect(flagged).toContain("DUPLICATE_CUT_SCENE");
    expect(flagged).toContain("UNKNOWN_CUT_SCENE");
    expect(profile.included_scene_ids).toEqual(original);
  });
  it("refuses zero, negative and backwards source timestamps",()=>{
    const {project,profile,segment}=fixture();
    project.audio.transcript.push(segment("zero",2,2,"Zero"));
    project.audio.transcript.push(segment("before",-1,1,"Negative"));
    project.audio.transcript.push(segment("reverse",5,4,"Reversed"));
    const result=inspectCaptionReadability(project,profile);
    expect(result.blocks).toBeGreaterThanOrEqual(3);
    expect(result.findings.filter(f=>f.code==="INVALID_TIMING")).toHaveLength(3);
    expect(result.inspectedSegments).toBe(0);
  });
  it("flags burn-in separately from sidecar structural timing checks",()=>{
    const {project,profile,segment}=fixture();
    profile.burn_in_captions=true;
    project.audio.transcript.push(segment("one",1,4,"A proper source caption"));
    expect(codes(project,profile)).toContain("BURN_IN_NOT_REVIEWED");
    expect(inspectCaptionReadability(project,profile).verdict).toBe("EDITOR_REVIEW");
  });
  it("fails boundedly for adversarial transcript size rather than sampling it",()=>{
    const {project,profile,segment}=fixture();
    project.audio.transcript=Array.from({length:769},(_,i)=>
      segment(String(i),1,2,"A"));
    expect(()=>inspectCaptionReadability(project,profile)).toThrow("inspection budget");
  });
  it("rejects maliciously huge rational denominators instead of misreporting seconds",()=>{
    const {project,profile,segment}=fixture();
    const a=segment("over",1,2,"A");
    a.start.den="9".repeat(190);
    project.audio.transcript.push(a);
    expect(()=>inspectCaptionReadability(project,profile)).toThrow("Invalid exact caption time");
  });
  it("refuses blank text while keeping source timing and review metadata",()=>{
    const {project,profile,segment}=fixture();
    project.audio.transcript.push(segment("blank",1,3,"  "));
    expect(codes(project,profile)).toContain("EMPTY_CAPTION");
  });
});
