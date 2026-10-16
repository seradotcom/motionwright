import {expect,test} from "@playwright/test";
import {readFileSync} from "node:fs";

const sourceLock = JSON.parse(readFileSync(new URL("../../../SOURCE_LOCK.json", import.meta.url), "utf8")) as {
  dependencies: {semwright: {revision: string}};
};
const pinnedRevision = sourceLock.dependencies.semwright.revision;
if (!/^[0-9a-f]{40}$/.test(pinnedRevision)) {
  throw new Error("Creative runtime doctor test requires one exact source-locked Semwright commit");
}

test("browser creative doctor provides real source counts without fabricating a Broker", async ({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Integrations",exact:true}).click();
  const panel=page.getByRole("region",{name:"Creative runtime doctor"});
  await expect(panel.getByRole("heading",{name:"Creative runtime doctor"})).toBeVisible();
  await expect(panel).toContainText("No diagnosis has been run");
  await page.getByRole("button",{name:"Run creative runtime doctor"}).click();
  await expect(panel).toContainText("BROWSER ONLY");
  await expect(panel).toContainText("Native runtime evidence");
  await expect(panel).toContainText("driver:blender");
  await expect(panel).toContainText("not tested");
  await expect(panel).toContainText("No");
  await expect(panel).not.toContainText("Broker session verified");
  await expect(panel).toContainText(pinnedRevision.slice(0, 12));
});

test("runtime doctor remains read-only and does not promote a browser demo",async({page})=>{
  await page.goto("/");
  const revision=await page.locator(".revision-chip").first().innerText();
  await page.getByRole("button",{name:"Integrations",exact:true}).click();
  const panel=page.getByRole("region",{name:"Creative runtime doctor"});
  await page.getByRole("button",{name:"Run creative runtime doctor"}).click();
  await expect(panel).toContainText("BROWSER ONLY");
  await expect(panel.getByRole("button",{name:"Run diagnosis again"})).toBeVisible();
  await panel.getByRole("button",{name:"Run diagnosis again"}).click();
  await expect(panel).toContainText("BROWSER ONLY");
  expect(await page.locator(".revision-chip").first().innerText()).toBe(revision);
  await expect(panel).toContainText("Installation sizing");
});
