import { readFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";

test("actual media clock drives the shared playhead and exact seeks pause playback",async({page})=>{
  await page.goto("/tests/fixtures/audio-review.html");
  const audio=page.getByLabel("Timeline audio playback",{exact:true});
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).duration)).toBe(4);
  await audio.evaluate(async node=>{const el=node as HTMLAudioElement;el.muted=true;el.currentTime=.5;await el.play();});
  await expect.poll(async()=>Number(await page.getByLabel("Observed project playhead").textContent())).toBeGreaterThan(.5);
  await page.getByRole("button",{name:"Seek project to three seconds"}).click();
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).paused)).toBe(true);
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).currentTime)).toBeCloseTo(3,4);
  await expect(page.getByLabel("Observed project playhead")).toHaveText("3.000000");
  await audio.evaluate(async node=>{const el=node as HTMLAudioElement;el.currentTime=1.25;await el.play();});
  await expect.poll(async()=>Number(await page.getByLabel("Observed project playhead").textContent())).toBeGreaterThan(1.25);
  await page.getByRole("button",{name:"Start design preview fixture"}).click();
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).paused)).toBe(true);
  await expect(page.getByLabel("Observed design playback")).toHaveText("true");
});

test("out-of-range project seeks stay explicit and one local reviewer owns playback",async({page})=>{
  await page.goto("/tests/fixtures/audio-review.html");
  const audio=page.getByLabel("Timeline audio playback",{exact:true});
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).duration)).toBe(4);
  await page.getByRole("button",{name:"Seek outside audio range"}).click();
  await expect(page.getByRole("region",{name:"Timeline audio review"})).toContainText("outside this reviewed audio range");
  await audio.evaluate(async node=>{const el=node as HTMLAudioElement;el.muted=true;await el.play();});
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).paused)).toBe(true);
  await expect(page.getByLabel("Observed project playhead")).toHaveText("9.000000");
  await page.getByRole("button",{name:"Seek reviewed audio start"}).click();
  await expect(page.getByLabel("Observed project playhead")).toHaveText("0.000000");
  await audio.evaluate(async node=>{const el=node as HTMLAudioElement;el.muted=true;await el.play();});
  const other=page.getByLabel("Independent source playback",{exact:true});
  await other.evaluate(async node=>{const el=node as HTMLAudioElement;el.muted=true;await el.play();});
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).paused)).toBe(true);
  await expect.poll(()=>other.evaluate(node=>(node as HTMLAudioElement).paused)).toBe(false);
});

test("changing revision and closing release the actual media; mismatched decoded clocks cannot play",async({page})=>{
  await page.goto("/tests/fixtures/audio-review.html");
  const audio=page.getByLabel("Timeline audio playback",{exact:true});
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).duration)).toBe(4);
  const element=await audio.elementHandle();
  await audio.evaluate(async node=>{const el=node as HTMLAudioElement;el.muted=true;await el.play();});
  await page.getByRole("button",{name:"Change saved revision"}).click();
  await expect(audio).toHaveCount(0);
  expect(await element!.evaluate(node=>(node as HTMLAudioElement).paused)).toBe(true);
  await expect(page.getByRole("alert")).toContainText("saved project or source revision changed");
  await page.getByRole("button",{name:"Use mismatched decoded duration"}).click();
  await expect(page.getByRole("alert")).toContainText("decoded WAV duration differs");
  await audio.evaluate(async node=>{const el=node as HTMLAudioElement;el.muted=true;await el.play();});
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).paused)).toBe(true);
  await page.getByRole("button",{name:"Close timeline audio review"}).click();
  await expect(audio).toHaveCount(0);
});

test("actual MP4 and original WAV share playback ownership, seeks and unmount cleanup",async({page})=>{
  const source=process.env.MOTIONWRIGHT_REVIEW_MEDIA_FIXTURE;
  test.skip(!source,"Provide an explicitly synthetic four-second H.264/AAC fixture; no Native render fallback.");
  const bytes=[...await readFile(source!)];
  await page.addInitScript(bytes=>{
    const runtime=window as unknown as {__TAURI_INTERNALS__:{invoke:(command:string)=>Promise<Uint8Array>}};
    runtime.__TAURI_INTERNALS__={invoke:async(command)=>{
      if(command!=="review_native_av_master")throw new Error("Unexpected synthetic media read: "+command);
      return Uint8Array.from(bytes);
    }};
  },bytes);
  await page.goto("/tests/fixtures/audio-review.html");
  const audio=page.getByLabel("Timeline audio playback",{exact:true});
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).duration)).toBe(4);
  await audio.evaluate(async node=>{const el=node as HTMLAudioElement;el.muted=true;await el.play();});
  await page.getByRole("button",{name:"Open synthetic MP4 component"}).click();
  const video=page.getByLabel("Verified native MP4 review player",{exact:true});
  await expect.poll(()=>video.evaluate(node=>(node as HTMLVideoElement).duration)).toBeCloseTo(4,1);
  await video.evaluate(async node=>{const el=node as HTMLVideoElement;el.muted=true;el.currentTime=.5;await el.play();});
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).paused)).toBe(true);
  await expect.poll(()=>video.evaluate(node=>(node as HTMLVideoElement).paused)).toBe(false);
  await expect.poll(async()=>Number(await page.getByLabel("Observed project playhead").textContent())).toBeGreaterThan(.5);
  await page.getByRole("button",{name:"Seek project to three seconds"}).click();
  await expect.poll(()=>video.evaluate(node=>(node as HTMLVideoElement).paused)).toBe(true);
  await expect.poll(()=>video.evaluate(node=>(node as HTMLVideoElement).currentTime)).toBeCloseTo(3,3);
  await video.evaluate(async node=>{const el=node as HTMLVideoElement;el.currentTime=1;await el.play();});
  await audio.evaluate(async node=>{const el=node as HTMLAudioElement;el.currentTime=1;await el.play();});
  await expect.poll(()=>video.evaluate(node=>(node as HTMLVideoElement).paused)).toBe(true);
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).paused)).toBe(false);
  // Seeking in paused native controls deliberately takes the timeline back.
  await video.evaluate(node=>{(node as HTMLVideoElement).currentTime=2.25;});
  await expect.poll(()=>audio.evaluate(node=>(node as HTMLAudioElement).paused)).toBe(true);
  await expect(page.getByLabel("Observed project playhead")).toHaveText("2.250000");
  await video.evaluate(async node=>{await (node as HTMLVideoElement).play();});
  await page.getByRole("button",{name:"Start design preview fixture"}).click();
  await expect.poll(()=>video.evaluate(node=>(node as HTMLVideoElement).paused)).toBe(true);
  await video.evaluate(async node=>{await (node as HTMLVideoElement).play();});
  const old=await video.elementHandle();
  await page.getByRole("button",{name:"Close synthetic MP4 component"}).click();
  await expect(video).toHaveCount(0);
  expect(await old!.evaluate(node=>(node as HTMLVideoElement).paused)).toBe(true);
});
