import { expect, test } from "@playwright/test";

test("one native source retains bytes across revisioned property fidelity classifications",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Native sources",exact:true}).click();
  const header=page.locator(".production-workspace-header p");
  const prior=Number((await header.innerText()).match(/revision (\d+)/)?.[1]);
  expect(Number.isFinite(prior)).toBe(true);

  await page.getByRole("combobox",{name:"Imported source asset"}).selectOption({label:"native-demo.glb"});
  await page.getByRole("button",{name:"Attach source to scene"}).click();
  await expect(header).toContainText("revision "+(prior+1));
  const panel=page.getByRole("region",{name:"Native per-property fidelity review"});
  await expect(panel.getByRole("heading",{name:"Property-level fidelity negotiation"})).toBeVisible();
  const beforeDigest=await panel.locator(".native-fidelity-source code").innerText();

  await panel.getByLabel("Native property path").fill("camera.focal_length");
  await panel.getByLabel("Native property fidelity").selectOption("baked");
  await panel.getByLabel("Native property loss explanation").fill(
    "The original GLB camera focal length cannot be edited in the target timeline."
  );
  await panel.getByRole("button",{name:"Record property classification"}).click();
  await expect(header).toContainText("revision "+(prior+2));
  const record=panel.getByLabel("Saved property fidelity classifications");
  await expect(record).toContainText("camera.focal_length");
  await expect(record).toContainText("baked");
  await expect(record).toContainText("original GLB camera");
  await expect(panel.locator(".native-fidelity-source code")).toHaveText(beforeDigest);

  await page.getByRole("button",{name:"Production plan",exact:true}).click();
  await page.getByRole("button",{name:"Native sources",exact:true}).click();
  await expect(panel.getByLabel("Saved property fidelity classifications")).toContainText("camera.focal_length");
  await expect(panel).toContainText("does not authorize execution");
  await page.screenshot({path:testInfo.outputPath("native-source-property-loss.png"),fullPage:true});
});

test("malformed property classification fails without changing source revision",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Native sources",exact:true}).click();
  await page.getByRole("combobox",{name:"Imported source asset"}).selectOption({label:"native-demo.glb"});
  await page.getByRole("button",{name:"Attach source to scene"}).click();
  const panel=page.getByRole("region",{name:"Native per-property fidelity review"});
  const snapshot=await page.locator(".production-workspace-header p").innerText();
  await panel.getByLabel("Native property path").fill("camera.__proto__");
  await panel.getByLabel("Native property fidelity").selectOption("native");
  await panel.getByRole("button",{name:"Record property classification"}).click();
  await expect(panel.getByRole("alert")).toContainText("invalid");
  await expect(page.locator(".production-workspace-header p")).toHaveText(snapshot);
  await expect(panel.getByLabel("Saved property fidelity classifications")).not.toContainText("camera.__proto__");
});

test("lossy native parameter notes cannot be blank or claim imported execution",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Native sources",exact:true}).click();
  await page.getByRole("combobox",{name:"Imported source asset"}).selectOption({label:"native-demo.glb"});
  await page.getByRole("button",{name:"Attach source to scene"}).click();
  const panel=page.getByRole("region",{name:"Native per-property fidelity review"});
  await panel.getByLabel("Native property path").fill("animation.keyframes");
  await panel.getByLabel("Native property fidelity").selectOption("unavailable");
  await panel.getByLabel("Native property loss explanation").fill("");
  await expect(panel.getByRole("button",{name:"Record property classification"})).toBeDisabled();
  await expect(panel).toContainText("Source bytes");
});
