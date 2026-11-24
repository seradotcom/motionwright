import {expect,test} from "@playwright/test";
import {readStudioProject} from "./support/scopedPaintMotionJourney";

// The explicitly labelled browser demo has only one GLB metadata asset.
// Its UI can reject a missing alternative, but cannot prove CAS retention.
test("Source relink never substitutes an incompatible browser-demo asset",async({page})=>{
  await page.goto("/");
  await expect(page.locator(".sdk-chip")).toContainText("browser demo");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Native sources",exact:true}).click();
  await page.getByRole("combobox",{name:"Imported source asset",exact:true}).selectOption({label:"native-demo.glb"});
  await page.getByRole("button",{name:"Attach source to scene",exact:true}).click();
  const panel=page.getByRole("region",{name:"Relink saved source",exact:true});
  await expect(panel).toContainText("Current source: native-demo.glb");
  const saved=await readStudioProject(page);
  await expect(panel.getByLabel("Replacement retained source",{exact:true}).locator("option")).toHaveCount(1);
  await expect(panel).toContainText("No other retained asset of this media type is available.");
  await expect(panel.getByRole("button",{name:"Relink saved source",exact:true})).toBeDisabled();
  await page.getByRole("button",{name:"Production plan",exact:true}).click();
  await page.getByRole("button",{name:"Native sources",exact:true}).click();
  await expect(panel.getByRole("button",{name:"Relink saved source",exact:true})).toBeDisabled();
  expect(await readStudioProject(page)).toEqual(saved);
});
