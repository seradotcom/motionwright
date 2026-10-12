import {expect,test} from "@playwright/test";

test("Deliver caption reading preflight is source-bound and opens the actual Audio editor",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Deliver",exact:true}).click();
  const revision=await page.locator(".revision-chip").first().innerText();
  const report=page.getByRole("region",{name:"Caption reading preflight"});
  await expect(report.locator("summary")).toContainText("Caption reading review");
  await report.locator("summary").click();
  await expect(report).toContainText("No transcript segments exist for caption export");
  await expect(report).toContainText("structural");
  await expect(report).toContainText("not a native pixel");
  expect(await page.locator(".revision-chip").first().innerText()).toBe(revision);
  await report.getByRole("button",{name:"Review transcript in Audio"}).click();
  await expect(page.getByRole("button",{name:"Audio",exact:true})).toHaveAttribute("aria-current","page");
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);
});

test("draft caption and burn-in changes update the advisory without committing",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Deliver",exact:true}).click();
  const revision=await page.locator(".revision-chip").first().innerText();
  const report=page.getByRole("region",{name:"Caption reading preflight"});
  await report.locator("summary").click();
  await expect(report.locator("summary")).toContainText("Needs correction");
  const exportPanel=page.getByRole("region",{name:"Caption sidecar export"});
  await exportPanel.getByLabel("Sidecar").uncheck();
  await expect(report.locator("summary")).toContainText("Disabled");
  await expect(report).toContainText("captions disabled");
  await exportPanel.getByLabel("Sidecar").check();
  await exportPanel.getByLabel("Burn-in").check();
  await expect(report).toContainText("Native burn-in needed");
  await expect(report.locator("summary")).toContainText("Needs correction");
  await expect(report).toContainText("Unsaved profile draft");
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);
});
