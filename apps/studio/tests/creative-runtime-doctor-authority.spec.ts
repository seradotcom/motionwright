import {expect, test} from "@playwright/test";
const fixture = "/tests/fixtures/creative-runtime-doctor-authority.html";
test.beforeEach(async ({page}) => { await page.goto(fixture); });
const panelName = "Creative runtime doctor";

test("current authenticated observations retain native readiness boundaries", async ({page}) => {
  const panel = page.getByRole("region", {name: panelName});
  await panel.getByRole("button", {name: "Run creative runtime doctor", exact:true}).click();
  await page.evaluate(() => window.__DOCTOR_AUTHORITY__.resolve(1, "CURRENT-DIAGNOSIS"));
  await expect(panel).toContainText("CURRENT-DIAGNOSIS");
  await expect(panel).toContainText("observed");
  await expect(panel).toContainText("Authenticated read-only Broker observations are included");
  await expect(panel).toContainText("Ready for native production: No");
  expect(await page.evaluate(() => window.__DOCTOR_AUTHORITY__.requests().map(r => r.command)))
    .toEqual(["creative_runtime_doctor"]);
});

test("late success cannot restore a previous project diagnosis", async ({page}) => {
  const panel = page.getByRole("region", {name: panelName});
  await panel.getByRole("button", {name: "Run creative runtime doctor", exact:true}).click();
  await page.evaluate(async () => {
    const h = window.__DOCTOR_AUTHORITY__; const s = h.scope();
    await h.setScope({revision:s.revision + 1});
    await h.resolve(1, "OLD-PROJECT-RESULT");
  });
  await expect(panel).not.toContainText("OLD-PROJECT-RESULT");
  await expect(panel).toContainText("No diagnosis has been run");
  await expect(panel.getByRole("button", {name:"Run creative runtime doctor",exact:true})).toBeEnabled();
});

test("late error cannot cross the current connection scope", async ({page}) => {
  const panel = page.getByRole("region", {name: panelName});
  await panel.getByRole("button", {name: "Run creative runtime doctor", exact:true}).click();
  await page.evaluate(async () => {
    const h = window.__DOCTOR_AUTHORITY__;
    await h.setScope({connection_identity:"connection-b"});
    await h.reject(1, "OLD-CONNECTION-ERROR");
  });
  await expect(panel).not.toContainText("OLD-CONNECTION-ERROR");
  await expect(panel).toContainText("No diagnosis has been run");
});

test("returning to the same project does not revive an earlier request", async ({page}) => {
  const panel = page.getByRole("region", {name: panelName});
  await panel.getByRole("button", {name: "Run creative runtime doctor", exact:true}).click();
  await page.evaluate(async () => {
    const h = window.__DOCTOR_AUTHORITY__; const initial = h.scope();
    await h.setScope({revision:initial.revision + 1});
    await h.setScope(initial);
    await h.resolve(1, "OLD-ABA-RESULT");
  });
  await expect(panel).not.toContainText("OLD-ABA-RESULT");
  await expect(panel).toContainText("No diagnosis has been run");
});

test("an obsolete finally cannot enable controls during a newer request", async ({page}) => {
  const panel = page.getByRole("region", {name: panelName});
  await panel.getByRole("button", {name:"Run creative runtime doctor",exact:true}).click();
  await page.evaluate(() => window.__DOCTOR_AUTHORITY__.setScope({connection_identity:"connection-b"}));
  const run = panel.getByRole("button", {name:"Run creative runtime doctor",exact:true});
  await expect(run).toBeEnabled();
  await run.click();
  await page.evaluate(() => window.__DOCTOR_AUTHORITY__.reject(1, "OLD-FINALLY"));
  await expect(panel.getByRole("button", {name:"Checking creative environment",exact:true})).toBeDisabled();
  await expect(panel).not.toContainText("OLD-FINALLY");
  await page.evaluate(() => window.__DOCTOR_AUTHORITY__.resolve(2, "CURRENT-SECOND"));
  await expect(panel).toContainText("CURRENT-SECOND");
});

test("SDK source changes invalidate old success even when the project is unchanged", async ({page}) => {
  const panel = page.getByRole("region", {name: panelName});
  await panel.getByRole("button", {name:"Run creative runtime doctor",exact:true}).click();
  await page.evaluate(async () => {
    const h = window.__DOCTOR_AUTHORITY__;
    await h.setScope({sdk_pin:"b".repeat(40)});
    await h.resolve(1, "OLD-SDK-RESULT");
  });
  await expect(panel).not.toContainText("OLD-SDK-RESULT");
  await expect(panel).toContainText("No diagnosis has been run");
});

test("a response with a foreign source pin is rejected in the active request", async ({page}) => {
  const panel = page.getByRole("region", {name: panelName});
  await panel.getByRole("button", {name:"Run creative runtime doctor",exact:true}).click();
  await page.evaluate(() => window.__DOCTOR_AUTHORITY__.resolve(1,"FOREIGN-SOURCE",
    {semwright_native_sdk_source_pin:"f".repeat(40)}));
  await expect(panel).not.toContainText("FOREIGN-SOURCE");
  await expect(panel.getByRole("alert")).toContainText("stale");
});

test("unmount and remount preserve ownership of the new diagnosis", async ({page}) => {
  const panel = page.getByRole("region", {name: panelName});
  await panel.getByRole("button", {name:"Run creative runtime doctor",exact:true}).click();
  await page.evaluate(async () => {
    const h = window.__DOCTOR_AUTHORITY__; await h.unmount(); await h.mount();
  });
  await panel.getByRole("button", {name:"Run creative runtime doctor",exact:true}).click();
  await page.evaluate(() => window.__DOCTOR_AUTHORITY__.resolve(1, "UNMOUNTED-RESULT"));
  await expect(panel).not.toContainText("UNMOUNTED-RESULT");
  await expect(panel.getByRole("button", {name:"Checking creative environment",exact:true})).toBeDisabled();
  await page.evaluate(() => window.__DOCTOR_AUTHORITY__.resolve(2,"REMOUNTED-CURRENT"));
  await expect(panel).toContainText("REMOUNTED-CURRENT");
});
