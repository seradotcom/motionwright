/** Browser interactions use synthetic IPC. Actual SQLite/filesystem coverage is in Rust. */
import { expect,test } from "@playwright/test";
type Fixture={calls():{command:string;request:Record<string,unknown>}[];loseNext():void;cancelPick():void;holdGrant():void;releaseGrant():void;revision(value:number):void};
declare global{interface Window{__runtimeProfileFixture:Fixture;}}
for(const platform of ["linux-x86_64","macos-aarch64"]){
test(platform+" reviewed package, lost acknowledgement, reopen, original read and explicit rollback",async({page})=>{
  await page.goto("/tests/fixtures/runtime-profiles.html?platform="+platform);
  const panel=page.getByRole("region",{name:"Offline runtime profiles"});
  await panel.getByRole("button",{name:"Choose offline runtime package"}).click();
  const reviewed=panel.getByRole("article",{name:"Reviewed runtime installation"});
  if(platform==="macos-aarch64")await expect(panel.getByText("Use a Motion Canvas package prepared for Apple Silicon.",{exact:false})).toBeVisible();
  await expect(reviewed).toContainText(platform);
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
}
test("an original installed profile remains readable with damaged browser history", async ({page}) => {
  await page.goto("/tests/fixtures/runtime-profiles.html?platform=macos-aarch64");
  const panel = page.getByRole("region", {name: "Offline runtime profiles"});
  await panel.getByRole("button", {name: "Choose offline runtime package"}).click();
  await panel.getByRole("button", {name: "Install reviewed local copy"}).click();
  await expect(panel.getByText("Installed · original files verified", {exact: true})).toBeVisible();
  const retainedKey = await page.evaluate(() => {
    const key = Object.keys(localStorage).find(name => name.startsWith("motionwright:runtime-profile:v1:"));
    if (!key) throw new Error("The fixture did not retain its runtime intent.");
    localStorage.setItem(key, "damaged runtime history");
    return key;
  });
  await page.reload();
  await panel.getByRole("button", {name: "Read original files"}).click();
  await expect(panel.getByText("Installed · original files verified", {exact: true})).toBeVisible();
  await expect(panel.getByText("The browser’s runtime history could not be read.", {exact: false})).toBeVisible();
  await expect(panel.getByRole("button", {name: "Choose offline runtime package"})).toBeDisabled();
  await expect(panel.getByRole("button", {name: "Roll back and keep files"})).toBeDisabled();
  expect(await page.evaluate(key => localStorage.getItem(key), retainedKey)).toBe("damaged runtime history");
  const commands = await page.evaluate(() => window.__runtimeProfileFixture.calls().map(call => call.command));
  expect(commands.filter(command => command === "issue_effect_grant")).toHaveLength(1);
  expect(commands.filter(command => command === "install_runtime_profile")).toHaveLength(1);
  expect(commands).not.toContain("rollback_runtime_profile");
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

test("a cancelled new selection clears the old review without requesting a permission",async({page})=>{
  await page.goto("/tests/fixtures/runtime-profiles.html?platform=macos-aarch64");
  const panel=page.getByRole("region",{name:"Offline runtime profiles"});
  await panel.getByRole("button",{name:"Choose offline runtime package"}).click();
  await expect(panel.getByRole("article",{name:"Reviewed runtime installation"})).toBeVisible();
  await page.evaluate(()=>window.__runtimeProfileFixture.cancelPick());
  await panel.getByRole("button",{name:"Choose offline runtime package"}).click();
  await expect(panel.getByRole("article",{name:"Reviewed runtime installation"})).toHaveCount(0);
  await expect(panel.getByText("Package selection was cancelled.",{exact:false})).toBeVisible();
  const calls=await page.evaluate(()=>window.__runtimeProfileFixture.calls());
  expect(calls.filter(row=>row.command==="issue_effect_grant"||row.command==="install_runtime_profile")).toHaveLength(0);
  await panel.getByRole("button",{name:"Choose offline runtime package"}).click();
  await expect(panel.getByRole("button",{name:"Install reviewed local copy"})).toBeEnabled();
});
