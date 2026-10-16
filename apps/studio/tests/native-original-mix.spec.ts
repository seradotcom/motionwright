import {test,expect} from "@playwright/test";
import {fixtureBootstrap} from "../src/fixture";

async function openAudition(page:import("@playwright/test").Page){
  await page.goto("/");
  await page.getByRole("button",{name:"Audio",exact:true}).click();
  const desk=page.locator('details[aria-label="Original three-bus native PCM audition"]');
  await expect(desk).toBeVisible();
  await desk.locator("summary").click();
  return desk;
}
test("browser-only Audio cannot fabricate a mixed WAV or rights receipt",async({page})=>{
  const desk=await openAudition(page);
  await expect(desk).toContainText("Desktop domain service is required");
  await expect(desk.getByRole("button",{name:"Create local original PCM audition"})).toBeDisabled();
  await expect(desk.locator("audio")).toHaveCount(0);
});

test("explicit desktop audition reads only owned voice/music/SFX, leaves project unchanged",async({page})=>{
  const boot=structuredClone(fixtureBootstrap);
  boot.native_sdk.mode="tauri";
  const project=boot.project;
  const music="00000000-0000-4000-8000-000000000051";
  const sfx="00000000-0000-4000-8000-000000000052";
  const voiceId="00000000-0000-4000-8000-000000000053";
  const voiceAsset=project.assets.find(asset=>asset.media_type==="audio/wav")!;
  project.assets.push(
    {id:music,media_type:"audio/wav",content_sha256:"b".repeat(64),name:"Owner music"},
    {id:sfx,media_type:"audio/wav",content_sha256:"c".repeat(64),name:"Owner foley"}
  );
  project.audio.voice_tracks=[{
    id:voiceId,asset_id:voiceAsset.id,label:"Original recorded",
    sample_rate_hz:48000,channels:2,measured_duration:{num:"1",den:"1"},
    source_sha256:voiceAsset.content_sha256!,loudness_lufs:-22,true_peak_dbfs:-8,
  }];
  project.audio.active_voice_track_id=voiceId;
  project.audio.transcript=[{
    id:"00000000-0000-4000-8000-000000000054",voice_track_id:voiceId,
    start:{num:"0",den:"1"},end:{num:"1",den:"1"},
    text:"Source words manually reviewed",speaker:null,alignment:{kind:"manual"},
  }];
  await page.addInitScript(({boot,selectedMusic,selectedSfx})=>{
    const runtime=window as unknown as {
      __TAURI_INTERNALS__:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __MIX_TEST_CALLS__:Array<{name:string;request?:Record<string,unknown>}>;
    };
    const calls:Array<{name:string;request?:Record<string,unknown>}>=[];
    runtime.__MIX_TEST_CALLS__=calls;
    runtime.__TAURI_INTERNALS__={invoke:async(command,args)=>{
      if(command==="bootstrap")return structuredClone(boot);
      if(command==="production_runtime_status")return{
        status:"ready",reason:null,expected_version:"1.0.0",
        observed_version:"1.0.0",version_compatible:true,
        executable_sha256:"a".repeat(64),connection_identity:"b".repeat(64),
        pinned_revision:"c".repeat(40),
      };
      if(command==="native_original_mix_audition"){
        const request=args?.request as Record<string,unknown>|undefined;
        calls.push({name:command,request});
        if(request?.project_id!==boot.project.id || request?.revision!==boot.project.revision ||
           request?.generation!==boot.project.generation ||
           request?.music_asset_id!==selectedMusic || request?.sfx_asset_id!==selectedSfx ||
           request?.owner_attests_preview_rights!==true)
          throw new Error("Synthetic local PCM source scope not acknowledged.");
        // Synthetic transport fixture, not evidence of real audio mastering.
        const pcm=48_000*4;
        const bytes=new Uint8Array(44+pcm),view=new DataView(bytes.buffer);
        bytes.set([82,73,70,70],0);view.setUint32(4,36+pcm,true);
        bytes.set([87,65,86,69,102,109,116,32],8);
        view.setUint32(16,16,true);view.setUint16(20,1,true);
        view.setUint16(22,2,true);view.setUint32(24,48_000,true);
        view.setUint32(28,192_000,true);view.setUint16(32,4,true);
        view.setUint16(34,16,true);bytes.set([100,97,116,97],36);
        view.setUint32(40,pcm,true);
        return bytes;
      }
      calls.push({name:command});
      throw new Error("Unexpected synthetic desktop operation: "+command);
    }};
  },{boot,selectedMusic:music,selectedSfx:sfx});
  const desk=await openAudition(page);
  const before=await page.locator(".revision-chip").innerText();
  await desk.getByLabel("Delivery language / profile").selectOption(project.deliverables[0].id);
  await desk.getByLabel("Original music asset").selectOption(music);
  await desk.getByLabel("Original SFX asset").selectOption(sfx);
  const request=desk.getByRole("button",{name:"Create local original PCM audition"});
  await expect(request).toBeDisabled();
  await desk.getByLabel(/I confirm I have permission to use these exact original sources/).check();
  await expect(request).toBeEnabled();
  await request.click();
  await expect(desk.locator('audio[controls]')).toBeVisible();
  await expect(desk).toContainText("not a mastered deliverable");
  await expect(page.locator(".revision-chip")).toHaveText(before);
  const calls=await page.evaluate(()=>(window as unknown as {
    __MIX_TEST_CALLS__:Array<{name:string;request?:Record<string,unknown>}>
  }).__MIX_TEST_CALLS__);
  expect(calls.filter(call=>call.name==="native_original_mix_audition")).toHaveLength(1);
  expect(calls.some(call=>/apply_change|provider|render|payment|release/.test(call.name))).toBe(false);
  await desk.getByLabel("Original music asset").selectOption("");
  await expect(desk.getByLabel(/I confirm I have permission to use these exact original sources/)).not.toBeChecked();
  await expect(desk.locator("audio")).toHaveCount(0);
  await expect(request).toBeDisabled();
});
