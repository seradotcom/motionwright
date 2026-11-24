import { expect, type Page } from "@playwright/test";
import { proposalButton as button } from "./scopedPaintMotionJourney";

export async function openTimelineAnimation(page: Page,nodeId: string) {
  const entry=page.locator('.timeline-object-track [data-source-id="'+nodeId+'"]');
  await expect(entry).toHaveCount(1);
  await entry.click();
  await button(page,"Edit animation").click();
  await expect(page.getByRole("region",{name:"Canvas animation workspace",exact:true})).toBeVisible();
  await expect(page.getByLabel("Patch target object",{exact:true})).toHaveValue(nodeId);
  await expect(page.getByLabel("Patch edit kind",{exact:true})).toHaveValue("motion");
}
export async function leaveMotionRangeInput(page: Page) {
  await page.locator(".scoped-motion-range > summary").click();
  await page.getByLabel("Animation range operation",{exact:true}).selectOption("fit");
  await page.getByLabel("Animation range start",{exact:true}).fill("1/");
  await page.getByLabel("Animation range end",{exact:true}).fill("3");
  await expect(button(page,"Apply range to animation draft")).toBeDisabled();
}
export async function applyMotionRangeProposal(page: Page,nodeId:string) {
  await expect(page.getByLabel("Animation range start",{exact:true})).toHaveValue("1/");
  await page.getByLabel("Animation range start",{exact:true}).fill("0");
  await button(page,"Apply range to animation draft").click();
  await expect(page.locator(".scoped-motion-row")).toHaveCount(10);
  await expect(page.getByLabel("Keyframe 6 time",{exact:true})).toHaveValue("3");
  await button(page,"Add to proposal").click();
  await page.getByLabel("Patch rationale",{exact:true}).fill("Lengthen the complete entrance while preserving the camera and all authored curves.");
  await button(page,"Preview scoped changes").click();
  await expect(page.getByText("Editorial diff prepared",{exact:true})).toBeVisible();
  await page.getByLabel("Comparison playhead",{exact:true}).fill("1");
  const before=page.getByRole("img",{name:"Before scoped changes",exact:true});
  const after=page.getByRole("img",{name:"After scoped changes",exact:true});
  await expect(before.locator('[data-canvas-node-id="'+nodeId+'"] rect')).toHaveAttribute("x","480");
  await expect.poll(async()=>Number(await after.locator('[data-canvas-node-id="'+nodeId+'"] rect').getAttribute("x"))).toBeCloseTo(468.148148148,6);
  await expect(after).toHaveAttribute("data-camera-center-x",await before.getAttribute("data-camera-center-x")??"");
}
