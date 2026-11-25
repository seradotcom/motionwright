import { expect, test } from "@playwright/test";
import { readStudioProject, proposalButton as button } from "./support/scopedPaintMotionJourney";
import { fixtureBootstrap } from "../src/fixture";
import { deriveVoiceCut } from "../src/voiceCut";
import { createHash } from "node:crypto";

// Authoring-only browser fixture. The original sources are synthetic metadata;
// no native dispatch, generated media, or real Store proof is supplied.
test("review and save a reordered original voice cut without replacing source assets", async ({page}) => {
  const seed = structuredClone(fixtureBootstrap.project);
  const trackId = "00000000-0000-4000-8000-000000000801";
  const assetId = "00000000-0000-4000-8000-000000000802";
  const digest = "a".repeat(64);
  seed.assets.push({id:assetId,name:"Synthetic voice metadata",media_type:"audio/wav",content_sha256:digest,source_revision:null});
  seed.audio.voice_tracks = [{id:trackId,asset_id:assetId,label:"Synthetic voice",source_sha256:digest,
    sample_rate_hz:48000,channels:2,measured_duration:{num:"36",den:"1"},loudness_lufs:null,true_peak_dbfs:null}];
  const profile = seed.deliverables[0];
  profile.voice_track_id = trackId;
  profile.included_scene_ids = [seed.scenes[3].id,seed.scenes[0].id];
  const cut = deriveVoiceCut(seed,profile);
  const binding = createHash("sha256").update(cut.wire).digest("hex");
  const music = seed.assets.find(a=>a.id!==assetId && a.media_type==="audio/wav")!;
  // Supply only the fixture data to Vite's ordinary browser-demo module.
  await page.route(/\/src\/fixture\.ts(?:\?|$)/,async route=>{
    const response=await route.fetch();
    expect(response.ok()).toBeTruthy();
    await route.fulfill({response,body:(await response.text())+"\nObject.assign(fixtureBootstrap.project,"+JSON.stringify(seed)+");\n"});
  });
  await page.goto("/");
  const before = await readStudioProject(page);
  expect(before.deliverables[0].included_scene_ids).toEqual(profile.included_scene_ids);
  const panel=page.getByRole("region",{name:"Original three-bus native PCM audition",exact:true});
  const open=async()=>{
    await button(page,"Audio").click();
    const expand=panel.getByRole("button",{name:/original source mix/});
    if(await expand.getAttribute("aria-expanded")!=="true")await expand.click();
    await panel.getByLabel("Delivery language / profile",{exact:true}).selectOption(profile.id);
  };
  await open();
  await panel.getByLabel("Enable original mix recipe",{exact:true}).check();
  await panel.getByLabel("Original music asset",{exact:true}).selectOption(music.id);
  const review=panel.getByRole("region",{name:"Original voice cut review",exact:true});
  await expect(review).toContainText("Review required");
  await expect(review.locator("tbody tr")).toHaveCount(2);
  await expect(review.locator("tbody tr").first()).toContainText(seed.scenes[3].name);
  await review.getByRole("button",{name:"Bind reviewed original voice cut",exact:true}).click();
  await expect(review).toContainText("Exact cut binding selected.");
  const save=panel.getByRole("button",{name:"Save original mix recipe",exact:true});
  await expect(save).toBeEnabled();await save.click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(before.revision+1));
  const after=await readStudioProject(page);
  expect(after.production_design?.delivery_recipes?.[profile.id]?.original_mix?.voice_cut_sha256).toBe(binding);
  expect(after.assets).toEqual(before.assets);expect(after.audio).toEqual(before.audio);expect(after.scenes).toEqual(before.scenes);
  await button(page,"Canvas").click();await open();
  await expect(review).toContainText("Exact cut binding selected.");
  await expect(panel.getByRole("button",{name:"Create local original PCM audition",exact:true})).toBeDisabled();
  await review.getByRole("button",{name:"Clear original voice cut binding",exact:true}).click();
  await save.click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(after.revision+1));
  const cleared=await readStudioProject(page);
  expect(cleared.production_design?.delivery_recipes?.[profile.id]?.original_mix?.voice_cut_sha256).toBeUndefined();
  expect(cleared.assets).toEqual(before.assets);
});
