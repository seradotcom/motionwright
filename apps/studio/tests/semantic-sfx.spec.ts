import { expect,test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("original semantic sound events require imported WAV, rights and exact bounded envelope",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Audio",exact:true}).click();
  const editor=page.getByRole("region",{name:"Semantic sound choreography"});
  await expect(editor).toBeVisible();
  await expect(editor.getByText(/native evidence pending/i).first()).toBeVisible();
  await expect(page.getByRole("button",{name:"Add original SFX event"})).toBeDisabled();
  await page.getByLabel("SFX source rights attestation")
    .fill("I authored this original stereo WAV for the UI focus event.");
  await page.getByLabel("SFX semantic event").selectOption("confirmation");
  await page.getByLabel("SFX Source frames").fill("0");
  await expect(editor.getByText(/Admission blocked:/)).toBeVisible();
  await expect(page.getByRole("button",{name:"Add original SFX event"})).toBeDisabled();
  await page.getByLabel("SFX Source frames").fill("30");
  await page.getByLabel("SFX Release frames").fill("30");
  await expect(page.getByRole("button",{name:"Add original SFX event"})).toBeDisabled();
  await page.screenshot({path:testInfo.outputPath("semantic-sfx-admission-bounded.png"),fullPage:true});
});

test("native SFX editor changes an existing cue in place without recreating source identity",async({page})=>{
  // Synthetic desktop transport only; the real CAS upsert is covered by Rust
  // and TS domain tests. This verifies editor intent, not native audio output.
  const initial=structuredClone(fixtureBootstrap);
  initial.native_sdk.mode="tauri";
  const p=initial.project;
  p.scenes=[p.scenes[0]];
  p.scenes[0].start={num:"0",den:"1"};
  p.scenes[0].duration={num:"6",den:"1"};
  p.scenes[0].beats=[];
  p.locks=[];
  const cueId="30000000-0000-4000-8000-0000000000f1";
  const assetId="30000000-0000-4000-8000-0000000000f2";
  const sha="a".repeat(64);
  p.assets.push({id:assetId,name:"Owner original focus.wav",
    media_type:"audio/wav",content_sha256:sha,source_revision:"owner"});
  p.audio.sfx_cues=[{
    schema:"motionwright.semantic-sfx/1",id:cueId,
    scene_id:p.scenes[0].id,event:"ui_focus",
    anchor:{kind:"scene_start"},offset_frames:5,
    source_asset_id:assetId,source_sha256:sha,
    source_frames:30,gain_milli_db:-12000,attack_frames:3,release_frames:8,
    original_source_attestation:"Owner-created original WAV",
  }];
  const reviewId="30000000-0000-4000-8000-0000000000f3";
  p.reviews.push({
    id:reviewId,kind:"creative",status:"open",
    resolution:null,resolved_at:null,created_at:"2026-10-15T00:00:00Z",
    anchor:{
      resource:"scene:"+p.scenes[0].id,branch_id:p.active_branch,
      revision:p.revision,start:{num:"1",den:"6"},end:{num:"7",den:"6"},
      locale:null,profile_id:p.deliverables[0].id,
    },
    body:JSON.stringify({
      schema:"motionwright.manual-native-sfx-listening-issue/1",
      verdict:"needs_adjustment",
      interpretation:"human_annotated_after_player_play_event_not_creative_approval",
      observation:"The owner-authored focus effect is arriving too late.",
      source:{
        project_id:p.id,generation:p.generation,revision:p.revision,
        deliverable_id:p.deliverables[0].id,scene_id:p.scenes[0].id,
        cue_id:cueId,start_frame:5,end_frame_exclusive:35,
        absolute_pcm_sample:8000,sample_rate_hz:48000,video_frame_rate:"30/1",
        original_wav_sha256:sha,exact_mix_pcm_sha256:sha,
        native_mp4_sha256:"b".repeat(64),
      },
    }),
  });
  await page.addInitScript(({boot})=>{
    const app=window as unknown as {
      __TAURI_INTERNALS__:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __SFX_EDIT__:{changes:Array<Record<string,unknown>>};
    };
    let project=structuredClone(boot.project);
    app.__SFX_EDIT__={changes:[]};
    app.__TAURI_INTERNALS__={invoke:async(command,args)=>{
      if(command==="bootstrap")return {...boot,project:structuredClone(project)};
      if(command==="production_runtime_status")return {
        status:"ready",expected_version:"1.0.0",observed_version:"1.0.0",
        version_compatible:true,executable_sha256:"a".repeat(64),
        connection_identity:"b".repeat(64),pinned_revision:"c".repeat(40),
      };
      if(command==="production_jobs")return [];
      if(command==="issue_effect_grant")return {token:"owner-edit-effect"};
      if(command==="apply_change"){
        const request=args?.request as {change:{
          type:string;cue:{id:string;scene_id:string;gain_milli_db:number}
        }};
        if(request.change.type!=="upsert_semantic_sfx")
          throw new Error("Only scoped semantic SFX edit may mutate the fixture");
        app.__SFX_EDIT__.changes.push(request.change as Record<string,unknown>);
        const current=project.audio.sfx_cues??[];
        const index=current.findIndex(row=>row.id===request.change.cue.id);
        if(index<0 || current[index].scene_id!==request.change.cue.scene_id)
          throw new Error("Original SFX identity moved");
        current[index]={...current[index],...request.change.cue};
        project={...project,revision:project.revision+1,
          audio:{...project.audio,sfx_cues:current}};
        return structuredClone(project);
      }
      throw new Error("Unexpected native UI operation: "+command);
    }};
  },{boot:initial});
  await page.goto("/");
  await page.getByRole("button",{name:"Audio",exact:true}).click();
  const editor=page.getByRole("region",{name:"Semantic sound choreography"});
  const handoff=editor.getByRole("region",{name:"Open original SFX listening issues"});
  await expect(handoff).toContainText("arriving too late");
  await expect(handoff).toContainText("source r"+p.revision);
  const edit=editor.getByRole("button",{name:"Edit original SFX "+cueId});
  await expect(edit).toBeEnabled();
  await handoff.getByRole("button",{
    name:"Edit SFX cue from listening review "+reviewId,
  }).click();
  await expect(editor.getByRole("status",{name:"Editing original semantic SFX cue"})).toContainText(cueId);
  await expect(editor.getByLabel("SFX source scene")).toBeDisabled();
  await expect(editor.getByLabel("SFX Offset frames")).toHaveValue("5");
  await expect(editor.getByLabel("SFX Gain milli-dB")).toHaveValue("-12000");
  await editor.getByLabel("SFX Offset frames").fill("9");
  await editor.getByLabel("SFX Gain milli-dB").fill("-18000");
  await expect(editor.getByText(/Starts at frame 9; PCM sample 14400/)).toBeVisible();
  const save=editor.getByRole("button",{name:"Save original SFX changes"});
  await expect(save).toBeEnabled();
  await save.click();
  await expect(editor.getByRole("status",{name:"Editing original semantic SFX cue"})).toHaveCount(0);
  await expect(editor.getByRole("button",{name:"Edit original SFX "+cueId})).toHaveCount(1);
  await expect(editor).toContainText("-18 dB");
  const writes=await page.evaluate(()=>(window as unknown as {
    __SFX_EDIT__:{changes:Array<Record<string,unknown>>};
  }).__SFX_EDIT__.changes);
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({
    type:"upsert_semantic_sfx",
    cue:{id:cueId,scene_id:p.scenes[0].id,source_asset_id:assetId,
      source_sha256:sha,offset_frames:9,gain_milli_db:-18000},
  });
});
