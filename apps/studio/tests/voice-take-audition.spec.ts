import {createHash} from "node:crypto";
import {test,expect} from "./support/selectedSyntheticProject";
import {fixtureBootstrap} from "../src/fixture";

function originalWav(rate:number,channels:number,level:number){
  const data=rate*channels*2,bytes=Buffer.alloc(44+data);
  bytes.write("RIFF",0);bytes.writeUInt32LE(36+data,4);bytes.write("WAVEfmt ",8);
  bytes.writeUInt32LE(16,16);bytes.writeUInt16LE(1,20);bytes.writeUInt16LE(channels,22);
  bytes.writeUInt32LE(rate,24);bytes.writeUInt32LE(rate*channels*2,28);
  bytes.writeUInt16LE(channels*2,32);bytes.writeUInt16LE(16,34);bytes.write("data",36);
  bytes.writeUInt32LE(data,40);
  for(let at=44;at<bytes.length;at+=2)bytes.writeInt16LE(level+(at%19),at);
  return Array.from(bytes);
}
test("review either retained original voice take, verify its bytes and never change the active take",async({page})=>{
  // Synthetic transport only: the native test independently covers actual CAS.
  const boot=structuredClone(fixtureBootstrap);boot.native_sdk.mode="tauri";
  const p=boot.project;
  const waves=[originalWav(8000,1,1000),originalWav(48000,2,-1500)];
  const ids=["11111111-1111-4111-8111-111111111111","22222222-2222-4222-8222-222222222222"];
  const assetIds=["33333333-3333-4333-8333-333333333333","44444444-4444-4444-8444-444444444444"];
  p.audio.voice_tracks=waves.map((bytes,i)=>{
    const digest=createHash("sha256").update(Buffer.from(bytes)).digest("hex");
    p.assets.push({id:assetIds[i],name:"Synthetic original "+i+".wav",media_type:"audio/wav",
      content_sha256:digest,source_revision:null});
    return {id:ids[i],asset_id:assetIds[i],label:i===0?"Original active mono":"Alternate stereo take",
      source_sha256:digest,sample_rate_hz:i===0?8000:48000,channels:i===0?1:2,
      measured_duration:{num:"1",den:"1"},loudness_lufs:null,true_peak_dbfs:null};
  });
  p.audio.active_voice_track_id=ids[0];p.audio.transcript=[];p.audio.cues=[];
  await page.addInitScript(({boot,waves,ids})=>{
    const runtime=window as unknown as {__TAURI_INTERNALS__:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __VOICE_TAKE_CALLS__:unknown[];__VOICE_TAKE_TAMPER__:boolean};
    runtime.__VOICE_TAKE_CALLS__=[];runtime.__VOICE_TAKE_TAMPER__=false;
    runtime.__TAURI_INTERNALS__={invoke:async(command,args)=>{
      if(command==="bootstrap"||command==="project_projection_head"||command==="open_project")
        return window.__SYNTHETIC_PROJECT_READ__(command,args,structuredClone(boot));
      if(command==="production_runtime_status")return{status:"ready",reason:null,expected_version:"1.0.0",observed_version:"1.0.0",version_compatible:true,
        executable_sha256:"a".repeat(64),connection_identity:"b".repeat(64),pinned_revision:"c".repeat(40)};
      if(command==="read_original_voice_audition"){
        const request=args?.request as Record<string,unknown>;
        const index=ids.indexOf(request.track_id as string);
        if(index<0||request.project_id!==boot.project.id||request.generation!==boot.project.generation||
           request.revision!==boot.project.revision||request.source_sha256!==boot.project.audio.voice_tracks[index].source_sha256||
           request.owner_attests_preview_rights!==true)throw new Error("Wrong synthetic original source scope");
        runtime.__VOICE_TAKE_CALLS__.push(request);
        const bytes=waves[index].slice();if(runtime.__VOICE_TAKE_TAMPER__)bytes[bytes.length-1]^=1;
        return bytes;
      }
      if(command==="waveform_page")throw new Error("Synthetic fixture has no native waveform evidence");
      throw new Error("Unexpected command in original take fixture: "+command);
    }};
  },{boot,waves,ids});
  await page.goto("/");
  await page.getByRole("button",{name:"Audio",exact:true}).click();
  const panel=page.getByRole("region",{name:"Original voice take listening",exact:true});
  await panel.getByRole("button",{name:"Listen to an original voice take"}).click();
  const select=panel.getByRole("combobox",{name:"Take to listen to",exact:true});
  const rights=panel.getByRole("checkbox",{name:"Confirm original take listening rights"});
  const load=panel.getByRole("button",{name:"Load original take for playback"});
  const revision=await page.locator(".command-bar .revision-chip").innerText();
  await expect(select).toHaveValue(ids[0]);await expect(load).toBeDisabled();
  await rights.check();await load.click();
  const audio=panel.getByLabel("Original take playback",{exact:true});
  await expect(audio).toHaveCount(1);
  await expect(audio).toHaveAttribute("src",/^blob:/);
  await audio.evaluate(node=>{const el=node as HTMLAudioElement;el.preload="metadata";el.load();});
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).duration)).toBe(1);
  await panel.getByRole("button",{name:"Review active take on project timeline"}).click();
  const linked=page.getByLabel("Timeline audio playback",{exact:true});
  await expect.poll(()=>linked.evaluate(node=>(node as HTMLAudioElement).duration)).toBe(1);
  const linkedUrl=await linked.getAttribute("src");
  await linked.evaluate(async node=>{const el=node as HTMLAudioElement;el.muted=true;el.currentTime=.4;await el.play();});
  await expect(page.locator(".timeline-transport .timecode")).not.toHaveText("00:00:00");
  await page.getByRole("button",{name:"Canvas",exact:true}).click();
  await expect(linked).toHaveAttribute("src",linkedUrl!);
  await expect.poll(()=>linked.evaluate(node=>(node as HTMLAudioElement).currentTime)).toBeGreaterThan(.4);
  await page.getByRole("button",{name:"Audio",exact:true}).click();
  await panel.getByRole("button",{name:"Listen to an original voice take"}).click();
  // Reopening the local source controls never silently approves another read.
  await expect(rights).not.toBeChecked();
  await select.selectOption(ids[1]);
  await expect(linked).toHaveCount(0);
  await expect(audio).toHaveCount(0);await expect(rights).not.toBeChecked();await expect(load).toBeDisabled();
  await rights.check();await load.click();await expect(audio).toHaveCount(1);
  await audio.evaluate(node=>{const el=node as HTMLAudioElement;el.preload="metadata";el.load();});
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).duration)).toBe(1);
  await expect(page.locator(".command-bar .revision-chip")).toHaveText(revision);
  await expect(page.getByRole("region",{name:"Audio measurements"})).toContainText("Original active mono");
  await page.evaluate(()=>{(window as unknown as {__VOICE_TAKE_TAMPER__:boolean}).__VOICE_TAKE_TAMPER__=true;});
  await load.click();
  await expect(panel.getByRole("alert")).toContainText("digest differs");
  await expect(audio).toHaveCount(0);
  expect(await page.evaluate(()=>(window as unknown as {__VOICE_TAKE_CALLS__:unknown[]}).__VOICE_TAKE_CALLS__.length)).toBe(3);
  await expect(page.locator(".command-bar .revision-chip")).toHaveText(revision);
});
