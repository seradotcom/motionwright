/** Browser interactions use synthetic IPC. Actual SQLite/filesystem coverage is in Rust. */
import { expect,test } from "@playwright/test";
type Fixture={calls():{command:string;request:Record<string,unknown>}[];loseNext():void;holdGrant():void;releaseGrant():void;revision(value:number):void};
declare global{interface Window{__runtimeProfileFixture:Fixture;}}
test("reviewed package, lost acknowledgement, reopen, original read and explicit rollback",async({page})=>{
  await page.goto("/tests/fixtures/runtime-profiles.html");
  const panel=page.getByRole("region",{name:"Offline runtime profiles"});
  await panel.getByRole("button",{name:"Choose offline runtime package"}).click();
  const reviewed=panel.getByRole("article",{name:"Reviewed runtime installation"});
  await expect(reviewed.getByText("Motion Canvas 3.17.2",{exact:false})).toBeVisible();
  await expect(reviewed.getByText("7 files · 2.00 KiB · 2,048 bytes",{exact:true})).toBeVisible();
  await expect(reviewed.getByText("/fixture/runtime-profiles-v1/aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",{exact:true})).toBeVisible();
  await page.evaluate(()=>window.__runtimeProfileFixture.loseNext());
  await reviewed.getByRole("button",{name:"Install reviewed local copy"}).click();
  await expect(panel.getByRole("alert")).toContainText("acknowledgement lost");
  await expect(panel.getByRole("button",{name:"Choose offline runtime package"})).toBeDisabled();
  await page.reload();
  await panel.getByRole("button",{name:"Read original runtime outcome"}).click();
  await expect(panel.getByText("Installed · original files verified",{exact:true})).toBeVisible();
  await panel.getByText("Paths for the separately authorized provider",{exact:true}).click();
  await expect(panel.getByRole("button",{name:"Copy Runtime lock SHA-256",exact:true})).toBeVisible();
  await panel.getByRole("button",{name:"Roll back and keep files"}).click();
  await expect(panel.getByText("Rolled back · files preserved",{exact:true})).toBeVisible();
  await expect(panel.getByRole("button",{name:"Roll back and keep files"})).toBeDisabled();
  const calls=await page.evaluate(()=>window.__runtimeProfileFixture.calls());
  expect(calls.filter(row=>row.command==="install_runtime_profile")).toHaveLength(1);
  expect(calls.filter(row=>row.command==="rollback_runtime_profile")).toHaveLength(1);
  expect(calls.filter(row=>row.command==="issue_effect_grant")).toHaveLength(2);
  expect(calls.filter(row=>row.command==="read_original_runtime_profile")).toHaveLength(1);
});
test("an old runtime permission cannot dispatch after revision ABA",async({page})=>{
  await page.goto("/tests/fixtures/runtime-profiles.html");
  const panel=page.getByRole("region",{name:"Offline runtime profiles"});
  await panel.getByRole("button",{name:"Choose offline runtime package"}).click();
  await page.evaluate(()=>window.__runtimeProfileFixture.holdGrant());
  await panel.getByRole("button",{name:"Install reviewed local copy"}).click();
  await expect.poll(()=>page.evaluate(()=>window.__runtimeProfileFixture.calls().filter(row=>row.command==="issue_effect_grant").length)).toBe(1);
  await page.evaluate(()=>window.__runtimeProfileFixture.revision(5));
  await page.evaluate(()=>window.__runtimeProfileFixture.revision(4));
  await page.evaluate(()=>window.__runtimeProfileFixture.releaseGrant());
  await panel.getByRole("button",{name:"Read installed profile inventory"}).click();
  await expect(panel.getByRole("button",{name:"Choose offline runtime package"})).toBeEnabled();
  expect(await page.evaluate(()=>window.__runtimeProfileFixture.calls().filter(row=>row.command==="install_runtime_profile").length)).toBe(0);
});
