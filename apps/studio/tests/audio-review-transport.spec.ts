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
