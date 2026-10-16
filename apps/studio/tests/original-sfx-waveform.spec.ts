import {expect,test} from "@playwright/test";
import {fixtureBootstrap} from "../src/fixture";
import {originalSfxWaveform} from "../src/api";

const SHA="a".repeat(64);
const CUE_ID="00000000-0000-4000-8000-000000000411";
const ASSET_ID="00000000-0000-4000-8000-000000000412";

test("installed Studio inspects actual original stereo SFX extrema by scoped cue ID",async({page})=>{
  // This is synthetic WebView IPC acceptance; the Rust CAS test checks real
  // original WAV bytes, and this browser test MUST NOT claim audio playback.
  const boot=structuredClone(fixtureBootstrap);
  boot.native_sdk.mode="tauri";
  const p=boot.project;
  p.assets.push({id:ASSET_ID,name:"Owner original focus.wav",
    media_type:"audio/wav",content_sha256:SHA,source_revision:"owner"});
  p.audio.sfx_cues=[{
    schema:"motionwright.semantic-sfx/1",id:CUE_ID,
    scene_id:p.scenes[0].id,event:"ui_focus",anchor:{kind:"scene_start"},
    offset_frames:3,source_asset_id:ASSET_ID,source_sha256:SHA,
    source_frames:3,gain_milli_db:-12000,attack_frames:0,release_frames:1,
    original_source_attestation:"Owner original sound",
  }];
  const report={
    schema:"motionwright.original-sfx-waveform/1",
    project_id:p.id,generation:p.generation,revision:p.revision,
    cue_id:CUE_ID,asset_id:ASSET_ID,source_sha256:SHA,source_frames:3,
    sample_rate_hz:48000,channels:2,samples_per_peak:1600,
    left_min_pcm16:[-1200,-3000,-900],left_max_pcm16:[1200,3000,900],
    right_min_pcm16:[-600,-1500,-1800],right_max_pcm16:[600,1500,1800],
    sample_peak_pcm16:3000,measured_sha256:"b".repeat(64),
    evidence_scope:"owner-CAS-verified-original-PCM16-per-frame-extrema-NOT-listening-or-master",
  };
  await page.addInitScript(({initial,proof})=>{
    const app=window as unknown as {
      __TAURI_INTERNALS__:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __SFX_PEAK_REQUESTS__:Array<Record<string,unknown>>;
    };
    app.__SFX_PEAK_REQUESTS__=[];
    app.__TAURI_INTERNALS__={invoke:async(command,args)=>{
      if(command==="bootstrap")return structuredClone(initial);
      if(command==="production_runtime_status")return {
        status:"ready",reason:null,expected_version:"1.0.0",
        observed_version:"1.0.0",version_compatible:true,
        executable_sha256:"a".repeat(64),connection_identity:"b".repeat(64),
        pinned_revision:"c".repeat(40),
      };
      if(command==="production_jobs")return [];
      if(command==="original_sfx_waveform"){
        app.__SFX_PEAK_REQUESTS__.push(args?.request as Record<string,unknown>);
        return structuredClone(proof);
      }
      throw new Error("Unexpected mutating or native command: "+command);
    }};
  },{initial:boot,proof:report});
  await page.goto("/");
  await page.getByRole("button",{name:"Audio",exact:true}).click();
  const editor=page.getByRole("region",{name:"Semantic sound choreography"});
  await editor.getByRole("button",{name:"Inspect original SFX waveform "+CUE_ID}).click();
  const panel=editor.getByRole("region",{name:"Original SFX measured waveform"});
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("Measured original WAV");
  await expect(panel).toContainText("3 frames · 4800 samples/channel");
  await expect(panel).toContainText("measured peak 3,000");
  await expect(panel).toContainText("not playback, LUFS, true peak or creative approval");
  await expect(panel.getByRole("img",{name:/Left channel original PCM waveform/})).toBeVisible();
  await expect(panel.getByRole("img",{name:/Right channel original PCM waveform/})).toBeVisible();
  const slider=panel.getByLabel("Original SFX waveform source frame");
  await slider.focus();
  await slider.press("End");
  await expect(slider).toHaveValue("2");
  await expect(panel.getByRole("button",{
    name:"Seek project timeline to selected original SFX waveform frame",
  })).toContainText("Seek frame 5 · PCM sample 8,000");
  const requests=await page.evaluate(()=>(window as unknown as {
    __SFX_PEAK_REQUESTS__:Array<Record<string,unknown>>;
  }).__SFX_PEAK_REQUESTS__);
  expect(requests).toEqual([{
    project_id:p.id,generation:p.generation,revision:p.revision,cue_id:CUE_ID,
  }]);
  expect(Object.keys(requests[0])).not.toContain("path");
  expect(Object.keys(requests[0])).not.toContain("bytes");
});

test("non-Tauri API never fabricates owner WAV amplitude data",async()=>{
  // Test the exact exported Studio API in a runner with no installed Tauri.
  // The real browser's Audio panel may still show a saved cue, but never
  // manufactures a source waveform from that cue's metadata.
  const project={audio:{sfx_cues:[{id:CUE_ID}]}} as never;
  const result=await originalSfxWaveform(project,CUE_ID);
  expect(result).toBeNull();
});
