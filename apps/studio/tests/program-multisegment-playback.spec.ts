import {readFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import {test,expect} from "@playwright/test";

test("one original MP4 continues across Program segment handoff and releases on source change",async({page})=>{
  const source=process.env.MOTIONWRIGHT_REVIEW_MEDIA_FIXTURE;
  test.skip(!source,"Requires the existing synthetic four-second H.264/AAC fixture; never renders a replacement.");
  const bytes=await readFile(source!);
  await page.addInitScript(({bytes,sha})=>{
    const app=window as unknown as {
      __PROGRAM_CLIP_SHA__:string;__PROGRAM_ORIGINAL_READS__:unknown[];
      __TAURI_INTERNALS__:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<Uint8Array>};
    };
    app.__PROGRAM_CLIP_SHA__=sha;app.__PROGRAM_ORIGINAL_READS__=[];
    app.__TAURI_INTERNALS__={invoke:async(command,args)=>{
      if(command!=="review_native_av_master")throw new Error("Unexpected synthetic operation: "+command);
      app.__PROGRAM_ORIGINAL_READS__.push(args?.request);
      return Uint8Array.from(bytes);
    }};
  },{bytes:[...bytes],sha:createHash("sha256").update(bytes).digest("hex")});
  await page.goto("/tests/fixtures/program-multisegment.html");
  const program=page.getByRole("region",{name:"Program preview"});
  await program.getByRole("button",{name:"Review final AV"}).click();
  const video=program.locator("video");
  await expect.poll(()=>video.evaluate(el=>(el as HTMLVideoElement).duration)).toBe(4);
  const original=await video.elementHandle();
  await video.evaluate(async el=>{const media=el as HTMLVideoElement;media.muted=true;media.currentTime=1.8;await media.play();});
  await expect(page.getByLabel("Observed source scene")).toHaveText("Second original segment");
  await expect.poll(()=>video.evaluate(el=>(el as HTMLVideoElement).currentTime)).toBeGreaterThan(2);
  expect(await original!.evaluate(node=>node===document.querySelector('[aria-label="Program preview"] video'))).toBe(true);
  await video.evaluate(el=>{(el as HTMLVideoElement).playbackRate=.125;});
  await page.getByRole("button",{name:"Seek five milliseconds ahead"}).click();
  await expect.poll(()=>video.evaluate(el=>(el as HTMLVideoElement).paused),{timeout:2000}).toBe(true);
  const nearby=Number(await page.getByLabel("Requested nearby editorial seek").innerText());
  await expect.poll(()=>video.evaluate(el=>(el as HTMLVideoElement).currentTime)).toBeCloseTo(nearby,4);
  await page.getByRole("button",{name:"Seek second original to three seconds"}).click();
  await expect.poll(()=>video.evaluate(el=>(el as HTMLVideoElement).paused)).toBe(true);
  await expect.poll(()=>video.evaluate(el=>(el as HTMLVideoElement).currentTime)).toBeCloseTo(3,4);
  await expect(page.getByLabel("Observed Program playhead")).toHaveText("3.000000");
  await video.evaluate(async el=>{const media=el as HTMLVideoElement;media.currentTime=.5;await media.play();});
  await page.getByRole("button",{name:"Change saved revision"}).click();
  await expect(video).toHaveCount(0);
  expect(await original!.evaluate(node=>(node as HTMLVideoElement).paused)).toBe(true);
  await expect(program.getByRole("button",{name:"Review final AV"})).toHaveCount(0);
  const reads=await page.evaluate(()=>(window as unknown as {__PROGRAM_ORIGINAL_READS__:unknown[]}).__PROGRAM_ORIGINAL_READS__);
  expect(reads).toHaveLength(1);
  expect(reads[0]).toMatchObject({export_token:"77777777-7777-4777-8777-777777777777",stream:true});
  expect(Object.keys(reads[0] as object).sort()).toEqual(["project_id","generation","revision","export_token","stream"].sort());
});
