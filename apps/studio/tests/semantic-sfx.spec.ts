import { expect,test } from "@playwright/test";

test("unselected SFX source keeps submission blocked while raw timing input is preserved",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Audio",exact:true}).click();
  const editor=page.getByRole("region",{name:"Sound events",exact:true});
  await expect(editor).toBeVisible();
  await editor.getByRole("button",{name:"New sound event",exact:true}).click();
  const add=editor.getByRole("button",{name:"Add sound event",exact:true});
  const sourceError=editor.getByRole("status").filter({
    hasText:"The imported WAV and its original digest must still match this draft.",
  });
  const revision=page.locator(".command-bar .revision-chip");
  await expect(revision).toHaveText(/^r\d+$/);
  const originalRevision=await revision.innerText();
  // The fixture first scene is timing-locked; isolate this source-selection control.
  await editor.getByLabel("SFX source scene",{exact:true}).selectOption("018f0000-0000-7000-8000-000000000012");
  await expect(editor.getByLabel("SFX Source asset ID",{exact:true})).toHaveValue("");
  await expect(editor.getByLabel("SFX Source SHA-256",{exact:true})).toHaveValue("");
  await expect(sourceError).toBeVisible();
  await expect(add).toBeDisabled();
  await editor.getByLabel("SFX Original source attestation",{exact:true})
    .fill("Fixture-only source declaration; no WAV is selected.");
  await editor.getByLabel("SFX semantic event",{exact:true}).selectOption("confirmation");
  await editor.getByLabel("SFX Source frames",{exact:true}).fill("0");
  await expect(editor.getByLabel("SFX Source frames",{exact:true})).toHaveValue("0");
  await expect(sourceError).toBeVisible();
  await expect(add).toBeDisabled();
  await editor.getByLabel("SFX Source frames",{exact:true}).fill("30");
  await editor.getByLabel("SFX Release frames",{exact:true}).fill("30");
  await page.getByRole("button",{name:"Brief",exact:true}).click();
  await page.getByRole("button",{name:"Audio",exact:true}).click();
  await expect(editor.getByLabel("SFX Source frames",{exact:true})).toHaveValue("30");
  await expect(editor.getByLabel("SFX Release frames",{exact:true})).toHaveValue("30");
  await expect(editor.getByLabel("SFX Source asset ID",{exact:true})).toHaveValue("");
  await expect(editor.getByLabel("SFX Source SHA-256",{exact:true})).toHaveValue("");
  await expect(sourceError).toBeVisible();
  await expect(add).toBeDisabled();
  await expect(revision).toHaveText(originalRevision);
  await expect(editor.getByText("0 saved sound events",{exact:true})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("semantic-sfx-unselected-source-raw.png"),fullPage:true});
});
