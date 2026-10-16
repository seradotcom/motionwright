import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { openOriginalSfxReviewHandoffs } from "./sfxReviewHandoff";
import type { Project } from "./types";

const SHA="a".repeat(64);
const cueId="10000000-0000-4000-8000-0000000000a1";
const sceneId="10000000-0000-4000-8000-0000000000a2";
const reviewId="10000000-0000-4000-8000-0000000000a3";

function fixture():Project{
  const project=structuredClone(fixtureProject);
  project.scenes=[project.scenes[0]];
  project.scenes[0].id=sceneId;
  project.audio.sfx_cues=[{
    schema:"motionwright.semantic-sfx/1",id:cueId,scene_id:sceneId,
    event:"ui_focus",anchor:{kind:"scene_start"},offset_frames:5,
    source_asset_id:"10000000-0000-4000-8000-0000000000a4",
    source_sha256:SHA,source_frames:30,gain_milli_db:-12000,
    attack_frames:3,release_frames:8,
    original_source_attestation:"Owner original sample",
  }];
  const profile=project.deliverables[0].id;
  project.reviews.push({
    id:reviewId,kind:"creative",status:"open",resolution:null,
    created_at:"2026-10-15T00:00:00Z",resolved_at:null,
    anchor:{
      resource:"scene:"+sceneId,branch_id:project.active_branch,
      revision:project.revision,start:{num:"1",den:"6"},
      end:{num:"7",den:"6"},locale:null,profile_id:profile,
    },
    body:JSON.stringify({
      schema:"motionwright.manual-native-sfx-listening-issue/1",
      interpretation:"human_annotated_after_player_play_event_not_creative_approval",
      verdict:"needs_adjustment",
      observation:"The original UI focus effect comes a little too late.",
      source:{
        project_id:project.id,generation:project.generation,revision:project.revision,
        deliverable_id:profile,scene_id:sceneId,cue_id:cueId,
        start_frame:5,end_frame_exclusive:35,absolute_pcm_sample:8000,
        sample_rate_hz:48000,video_frame_rate:"30/1",
        original_wav_sha256:SHA,exact_mix_pcm_sha256:SHA,
        native_mp4_sha256:"b".repeat(64),
      },
    }),
  });
  return project;
}
describe("v0.5 human SFX issue → same original cue edit handoff",()=>{
  it("finds an open scene-bound review without inventing a quality approval",()=>{
    const p=fixture(),before=structuredClone(p);
    const rows=openOriginalSfxReviewHandoffs(p);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      reviewId,cueId,sceneId,sceneName:p.scenes[0].name,
      profileId:p.deliverables[0].id,reviewRevision:p.revision,
      fromFrame:5,toFrame:35,sourceSha256:SHA,
      sameOriginalWav:true,status:"open",
    });
    expect(rows[0].observation).toContain("too late");
    expect(p).toEqual(before);
  });
  it("keeps open review historical after project revision and source replacement",()=>{
    const p=fixture();
    p.revision+=2;
    expect(openOriginalSfxReviewHandoffs(p)).toHaveLength(1);
    p.audio.sfx_cues![0].source_sha256="c".repeat(64);
    expect(openOriginalSfxReviewHandoffs(p)[0].sameOriginalWav).toBe(false);
    p.reviews[0].status="resolved";
    expect(openOriginalSfxReviewHandoffs(p)).toEqual([]);
    p.reviews[0].status="needs_recheck";
    expect(openOriginalSfxReviewHandoffs(p)[0].status).toBe("needs_recheck");
  });
  it("rejects foreign branches and review source-identity forgery",()=>{
    const p=fixture();
    p.reviews[0].anchor.branch_id="10000000-0000-4000-8000-0000000000ab";
    expect(openOriginalSfxReviewHandoffs(p)).toEqual([]);
    p.reviews[0].anchor.branch_id=p.active_branch;
    const body=JSON.parse(p.reviews[0].body);
    for(const pair of [
      ["project_id","foreign-project"],["cue_id","foreign-cue"],
      ["original_wav_sha256","invalid"],["revision",p.revision+1],
      ["start_frame",6],["generation","foreign-generation"],
      ["sample_rate_hz",44100],["absolute_pcm_sample",9000],
      ["video_frame_rate","29.97"],["native_mp4_sha256","not-immutable"],
    ] as const){
      const broken=structuredClone(body);
      broken.source[pair[0]]=pair[1];
      p.reviews[0].body=JSON.stringify(broken);
      expect(openOriginalSfxReviewHandoffs(p)).toEqual([]);
    }
  });
  it("does not let untrusted JSON or a changed anchor impersonate a native finding",()=>{
    const p=fixture(),correct=p.reviews[0].body;
    for(const body of ["garbage", "", "{}", JSON.stringify({schema:"x"})]){
      p.reviews[0].body=body;
      expect(openOriginalSfxReviewHandoffs(p)).toEqual([]);
    }
    p.reviews[0].body=correct;
    p.reviews[0].anchor.start={num:"2",den:"3"};
    expect(openOriginalSfxReviewHandoffs(p)).toEqual([]);
    p.reviews[0].anchor.start={num:"1",den:"6"};
    p.reviews[0].kind="technical";
    expect(openOriginalSfxReviewHandoffs(p)).toEqual([]);
  });
});
