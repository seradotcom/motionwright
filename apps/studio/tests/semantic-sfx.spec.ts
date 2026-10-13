import { expect,test } from "@playwright/test";

test("original semantic sound events require imported WAV, rights and exact bounded envelope",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Audio",exact:true}).click();
  const editor=page.getByRole("region",{name:"Semantic sound choreography"});
  await expect(editor).toBeVisible();
  await expect(editor.getByText(/native evidence pending/i).first()).toBeVisible();
  await expect(page.getByRole("button",{name:"Add original SFX event"})).toBeDisabled();
  await page.getByLabel("SFX source rights attestation")
    .fill("I authored this original stereo WAV for the UI focus event.");
  await page.getByLabel("SFX semantic event").selectOption("confirmation");
  await page.getByLabel("SFX Source frames").fill("0");
  await expect(editor.getByText(/Admission blocked:/)).toBeVisible();
  await expect(page.getByRole("button",{name:"Add original SFX event"})).toBeDisabled();
  await page.getByLabel("SFX Source frames").fill("30");
  await page.getByLabel("SFX Release frames").fill("30");
  await expect(page.getByRole("button",{name:"Add original SFX event"})).toBeDisabled();
  await page.screenshot({path:testInfo.outputPath("semantic-sfx-admission-bounded.png"),fullPage:true});
});
