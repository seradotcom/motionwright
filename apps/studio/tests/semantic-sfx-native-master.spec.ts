import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("one-use desktop semantic SFX master uses existing preview and owner-scoped native AV authority", async ({page}) => {
  // This checks WebView IPC shape, not an actual native render or WAV bytes.
  // The pinned GitHub native AV workflow proves the latter independently.
  const boot=structuredClone(fixtureBootstrap);
  boot.native_sdk.mode="tauri";
  const p=boot.project;
  p.scenes=[p.scenes[0]];
  p.scenes[0].renderer="motion-canvas";
  p.scenes[0].start={num:"0",den:"1"};
  p.scenes[0].duration={num:"2",den:"1"};
  p.scenes[0].beats=[];
  const profile=p.deliverables[0];
  const sourceId="b755c8d5-6440-4059-9d99-a21233263fd1";
  const sourceSHA="c".repeat(64);
  p.assets.push({id:sourceId,name:"Original source-bound WAV",
    media_type:"audio/wav",content_sha256:sourceSHA,source_revision:"original-author"});
  p.audio.sfx_cues=[{
    schema:"motionwright.semantic-sfx/1",
    id:"b755c8d5-6440-4059-9d99-a21233263fd2",
    scene_id:p.scenes[0].id,event:"ui_focus",anchor:{kind:"scene_start"},
    offset_frames:6,source_asset_id:sourceId,source_sha256:sourceSHA,
    source_frames:24,gain_milli_db:-12000,attack_frames:3,release_frames:6,
    original_source_attestation:"Owner original WAV",
  }];
  const native={
    project_resource:"project:"+p.id,
    generation:p.generation,revision:p.revision,deliverable_id:profile.id,
    frame_rate:{num:30,den:1},
    segments:[{segment_id:"source-bound-single",scene_ids:[p.scenes[0].id],
      frame_count:60,plan_ref:"pinned-plan",fingerprint:"native-fingerprint",
      job_ref:"original-owner-job",artifact:{},verification:{}}],
    preview:[{token:"owner-issued-source-preview",
      segment_id:"source-bound-single",scene_ids:[p.scenes[0].id],frame_count:60}],
  };
  const mastered={
    export_token:"owner-issued-export-token",
    project_resource:native.project_resource,
    generation:native.generation,revision:native.revision,
    deliverable_id:profile.id,motion_segment_id:"source-bound-single",
    frame_rate:native.frame_rate,frame_count:60,
    mezzanine:{profile:"ffv1"},
    source_audio:{relative_path:"private-owner-digest.wav",
      sha256:sourceSHA,sample_rate:48000,channels:2},
    master:{profile:"h264-aac-mp4",artifact:{sha256:"d".repeat(64)}},
    decoded_audio:{kind:"synthetic-ipc-only"},sync:null,
    semantic_sfx_source_proof:{
      project_id:p.id,generation:p.generation,revision:p.revision,
      deliverable_id:profile.id,master_frames:60,
      original_asset_sha256:[sourceSHA],exact_pcm_sha256:sourceSHA,
      sample_peak_pcm16:2477,
    },
    semantic_sfx_cue_count:1,
    semantic_sfx_human_creative_approval:"REQUIRED",
  };
  await page.addInitScript(({initial,rendered,masteredResult})=>{
    const app=window as unknown as {
      __TAURI_INTERNALS__:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __SFX_IPC__:{requests:Array<Record<string,unknown>>; effects:Array<Record<string,unknown>>};
    };
    app.__SFX_IPC__={requests:[],effects:[]};
    app.__TAURI_INTERNALS__={invoke:async(command,args)=>{
      if(command==="bootstrap")return structuredClone(initial);
      if(command==="production_runtime_status")return {
        status:"ready",reason:null,expected_version:"1.0.0",observed_version:"1.0.0",
        version_compatible:true,executable_sha256:"a".repeat(64),
        connection_identity:"b".repeat(64),pinned_revision:"c".repeat(40),
      };
      if(command==="motion_canvas_preflight"){
        const req=args?.request as Record<string,unknown>;
        return {project_resource:"project:"+req.project_id,
          generation:req.generation,revision:req.revision,
          deliverable_id:req.deliverable_id,verdict:"projection_ready",
          reason:null,segment_count:1,total_frames:60,
          segments:[{segment_id:"source-bound-single",
            scene_ids:[initial.project.scenes[0].id],frame_count:60}]};
      }
      if(command==="issue_effect_grant"){
        app.__SFX_IPC__.effects.push(args?.request as Record<string,unknown>);
        return {token:"owner-only-ephemeral-effect-grant"};
      }
      if(command==="render_motion_canvas")return structuredClone(rendered);
      if(command==="assemble_semantic_sfx_av_master"){
        app.__SFX_IPC__.requests.push(args?.request as Record<string,unknown>);
        return structuredClone(masteredResult);
      }
      // Read-only screens may request other data; reject mutating native calls.
      if(command==="production_jobs")return [];
      throw new Error("Unexpected operation "+command);
    }};
  },{initial:boot,rendered:native,masteredResult:mastered});
  await page.goto("/");
  await page.getByRole("button",{name:"Deliver",exact:true}).click();
  const sfx=page.getByRole("region",{name:"Original semantic SFX native master"});
  const assemble=sfx.getByRole("button",{name:"Assemble original semantic SFX native MP4"});
  await expect(assemble).toBeDisabled();
  await expect(sfx).toContainText("registered");
  await page.getByLabel(p.scenes[0].name+" narrative role").selectOption("hook");
  await page.getByLabel(p.scenes[0].name+" motion archetype").selectOption("statement");
  await page.getByRole("button",{name:"Check Film projection"}).click();
  await expect(page.getByLabel("First native production readiness"))
    .toContainText("ELIGIBLE FOR AUTHORIZED DISPATCH");
  await page.getByRole("button",{name:"Render native segments"}).click();
  await expect(assemble).toBeEnabled();
  await assemble.click();
  await expect(page.getByLabel("Native AV master evidence")).toContainText("CURRENT · NATIVE");
  await expect(page.getByText(/Original PCM and native media were verified/)).toBeVisible();
  const sourceProof=page.getByRole("status",{name:"Original semantic SFX source verification"});
  await expect(sourceProof).toContainText("Original scene cues");
  await expect(sourceProof).toContainText("Original WAV assets");
  await expect(sourceProof).toContainText("2477 / 32767 PCM16");
  await expect(sourceProof).toContainText("REQUIRED");
  const audit=await page.evaluate(()=>(window as unknown as {
    __SFX_IPC__:{requests:Array<Record<string,unknown>>;effects:Array<Record<string,unknown>>};
  }).__SFX_IPC__);
  expect(audit.requests).toHaveLength(1);
  expect(audit.requests[0]).toMatchObject({
    project_id:p.id,generation:p.generation,revision:p.revision,
    deliverable_id:profile.id,preview_token:"owner-issued-source-preview",
    effect_grant:"owner-only-ephemeral-effect-grant",
  });
  for(const forbidden of ["voice_track_id","source_path","audio_path","file","path","audio","motion","sha256"]){
    expect(audit.requests[0]).not.toHaveProperty(forbidden);
  }
  expect(audit.effects.length).toBe(2);
});

test("browser demo has no native SFX master authority even with editable sound",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Deliver",exact:true}).click();
  await expect(page.getByRole("button",{name:"Assemble native AV master"})).toBeDisabled();
  await expect(page.getByRole("button",{name:"Assemble original semantic SFX native MP4"})).toHaveCount(0);
});


test("desktop delivery explicitly blocks voice-only master when authored SFX would be dropped",async({page})=>{
  const boot=structuredClone(fixtureBootstrap);
  boot.native_sdk.mode="tauri";
  const project=boot.project,profile=project.deliverables[0];
  const originalAssetId="40000000-0000-4000-8000-000000000001";
  const voiceAssetId="40000000-0000-4000-8000-000000000002";
  const voiceId="40000000-0000-4000-8000-000000000003";
  const sourceSHA="b".repeat(64),voiceSHA="c".repeat(64);
  project.assets.push(
    {id:originalAssetId,name:"Original sound.wav",media_type:"audio/wav",
      content_sha256:sourceSHA,source_revision:"owned"},
    {id:voiceAssetId,name:"Measured voice.wav",media_type:"audio/wav",
      content_sha256:voiceSHA,source_revision:"owned"},
  );
  project.audio.voice_tracks.push({
    id:voiceId,asset_id:voiceAssetId,label:"Measured original narration",
    sample_rate_hz:48000,channels:2,measured_duration:{num:"16",den:"1"},
    source_sha256:voiceSHA,loudness_lufs:null,true_peak_dbfs:null,
  });
  profile.voice_track_id=voiceId;
  project.audio.sfx_cues=[{
    schema:"motionwright.semantic-sfx/1",
    id:"40000000-0000-4000-8000-000000000004",
    scene_id:project.scenes[0].id,
    event:"ui_focus",anchor:{kind:"scene_start"},offset_frames:6,
    source_asset_id:originalAssetId,source_sha256:sourceSHA,
    source_frames:24,gain_milli_db:-12000,
    attack_frames:3,release_frames:6,
    original_source_attestation:"Owned numerical tone",
  }];
  await page.addInitScript(({initial})=>{
    const app=window as unknown as {
      __TAURI_INTERNALS__:{invoke:(command:string)=>Promise<unknown>};
    };
    app.__TAURI_INTERNALS__={invoke:async(command)=>{
      if(command==="bootstrap")return structuredClone(initial);
      if(command==="production_runtime_status")return {
        status:"ready",expected_version:"1.0.0",observed_version:"1.0.0",
        version_compatible:true,executable_sha256:"a".repeat(64),
        connection_identity:"b".repeat(64),pinned_revision:"c".repeat(40),
      };
      if(command==="production_jobs")return [];
      throw new Error("Blocked combined master must not request a native mutation: "+command);
    }};
  },{initial:boot});
  await page.goto("/");
  await page.getByRole("button",{name:"Deliver",exact:true}).click();
  const panel=page.getByRole("region",{name:"Canonical audiovisual mastering"});
  await expect(panel).toContainText("A voice-only master would omit the authored effects");
  await expect(panel.getByRole("button",{name:"Assemble native AV master"})).toBeDisabled();
  await expect(panel.getByRole("button",{name:"Assemble original semantic SFX native MP4"}))
    .toBeDisabled();
  await expect(panel).toContainText("cannot combine narration or music");
});
