import { expect, test } from "@playwright/test";
import { defaultOriginalMix } from "../src/deliveryRecipe";
import { fixtureProject } from "../src/fixture";

test("the actual mix editor compares exact saved and draft source windows", async ({ page }) => {
  const project = structuredClone(fixtureProject);
  project.schema_version = 2;
  project.locks = [];
  const profile = project.deliverables[0];
  profile.audio_sample_rate_hz = 48000;
  profile.included_scene_ids = [];
  const original = "aaaabbbb-cccc-4000-8000-000000000091";
  const replacement = "aaaabbbb-cccc-4000-8000-000000000092";
  const base = "aaaabbbb-cccc-4000-8000-000000000093";
  project.assets.push(...[
    { id: original, name: "Retained music", content_sha256: "a".repeat(64) },
    { id: replacement, name: "Proposed music", content_sha256: "b".repeat(64) },
    { id: base, name: "Original foley", content_sha256: "c".repeat(64) },
  ].map(asset => ({ ...asset, media_type: "audio/wav", source_revision: null })));
  project.production_design = {
    plan: null, heroes: [], capsules: [],
    delivery_recipes: { [profile.id]: {
      schema: "motionwright.delivery-recipe/1", scene_intents: [],
      original_mix: { ...defaultOriginalMix(), music_asset_id: original, sfx_asset_id: base, music_source_start_sample: 24000 },
    } },
  };
  await page.route("**/src/fixture.ts", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, body: (await response.text()) + "\nfixtureBootstrap.project = " + JSON.stringify(project) + ";\n" });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Audio", exact: true }).click();
  await page.getByRole("button", { name: /Voice \/ music \/ SFX · original source mix/ }).click();
  const review = page.getByRole("region", { name: "Original mix dependency comparison" });
  await expect(review).toHaveCount(0);

  await page.getByLabel("Original music asset", { exact: true }).selectOption(replacement);
  const start = page.getByLabel("Music source start sample", { exact: true });
  await start.fill("48000");
  const row = review.getByRole("row", { name: /^Original music / });
  await expect(review).toContainText("Saved revision " + project.revision);
  await expect(row.getByRole("cell").first()).toContainText(/Retained music.*Source \[24000,/);
  await expect(row.getByRole("cell").last()).toContainText(/Proposed music.*Source \[48000,/);
  await expect(review.getByRole("row", { name: /Original SFX base/ })).toContainText("Original foley");
  const save = page.getByRole("button", { name: "Save original mix recipe", exact: true });
  await start.fill("48000.1");
  await expect(review).toHaveCount(0);
  await expect(save).toBeDisabled();
  await start.fill("48000");
  await expect(review).toBeVisible();
  await save.click();
  await expect(review).toHaveCount(0);
  await expect(page.getByText("Saved music starts at source sample 48000", { exact: false })).toBeVisible();
  await expect(page.getByLabel("Original music asset", { exact: true })).toHaveValue(replacement);
});
