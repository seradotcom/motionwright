import { expect, test } from "@playwright/test";
import { proposalButton as button, readStudioProject } from "./support/scopedPaintMotionJourney";

// Authoring contract only: the browser demo does not dispatch HyperFrames.
test("saved derived cut retains original scene IDs and leaves master unchanged", async ({page}) => {
  await page.goto("/");
  await button(page, "Deliver").click();
  const base = await readStudioProject(page);
  const master = base.deliverables[0];
  expect(base.scenes.length).toBeGreaterThanOrEqual(3);
  const select = (id: string) => page.locator('[data-profile-id="' + id + '"]').click();
  await select(master.id);
  await button(page, "Derive selected").click();
  await expect(page.locator(".revision-chip").first()).toHaveText("r" + (base.revision + 1));
  const derived = await readStudioProject(page);
  const cut = derived.deliverables.find(p => p.parent_profile_id === master.id)!;
  expect(cut).toBeDefined();
  await select(cut.id);
  await button(page, "Customize cut").click();
  const selected = [base.scenes[0], base.scenes[base.scenes.length - 1]];
  for (let i = 1; i < base.scenes.length - 1; i++) {
    await page.locator(".variant-scene-row").nth(i).getByRole("checkbox", {name:"Include", exact:true}).uncheck();
  }
  await page.locator(".variant-scene-row").last().getByRole("checkbox", {name:"Protect", exact:true}).check();
  await expect(page.getByRole("region", {name:"Narrative cut continuity review"})).toContainText("2/" + base.scenes.length);
  await button(page, "Save profile").click();
  await expect(page.locator(".revision-chip").first()).toHaveText("r" + (derived.revision + 1));
  const saved = await readStudioProject(page);
  expect(saved.scenes).toEqual(base.scenes);
  expect(saved.deliverables.find(p => p.id === master.id)).toEqual(master);
  expect(saved.deliverables.find(p => p.id === cut.id)?.included_scene_ids).toEqual(selected.map(s => s.id));
  expect(saved.deliverables.find(p => p.id === cut.id)?.protected_scene_ids).toContain(selected[1].id);
  await select(master.id);
  await select(cut.id);
  await expect(page.locator(".variant-scene-row").nth(1).getByRole("checkbox", {name:"Include", exact:true})).not.toBeChecked();
  await expect(page.locator(".variant-scene-row").last().getByRole("checkbox", {name:"Protect", exact:true})).toBeChecked();
});
