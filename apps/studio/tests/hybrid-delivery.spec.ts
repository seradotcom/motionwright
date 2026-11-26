import {expect,test,type Page} from "@playwright/test";
import type {} from "./fixtures/hybrid-delivery";

// Real DeliveryProfiles + API + draft barriers, synthetic IPC responses.
// No Native render, original media playback, file copy or external grant is attested.
const panel=(page:Page)=>page.getByRole("region",{name:"Native hybrid master",exact:true});
const commands=(page:Page)=>page.evaluate(()=>window.__HYBRID_DELIVERY__.calls());
async function ready(page:Page){
  await panel(page).getByRole("button",{name:"Check hybrid sources",exact:true}).click();
  await expect(panel(page).getByRole("button",{name:"Create hybrid master",exact:true})).toBeEnabled();
}
test.beforeEach(async({page})=>{
  await page.goto("/tests/fixtures/hybrid-delivery.html");
  await expect(panel(page)).toBeVisible();
});
test("hybrid product routes source check, one render, original review and a fresh delivery permission",async({page})=>{
  await expect(panel(page).getByRole("button",{name:"Create hybrid master",exact:true})).toBeDisabled();
  await expect(page.getByRole("button",{name:"Render native segments",exact:true})).toBeDisabled();
  await expect(page.getByRole("button",{name:"Assemble native AV master",exact:true})).toHaveCount(0);
  await ready(page);
  expect((await commands(page)).filter(call=>call.command==="issue_effect_grant")).toHaveLength(0);
  await panel(page).getByRole("button",{name:"Create hybrid master",exact:true}).click();
  await expect(panel(page).getByLabel("Completed original hybrid master")).toContainText("c".repeat(64));
  await expect(panel(page).getByRole("button",{name:"Create hybrid master",exact:true})).toBeDisabled();
  await panel(page).getByRole("button",{name:"Review completed hybrid original",exact:true}).click();
  await expect.poll(async()=>(await commands(page)).filter(call=>call.command==="review_native_av_master").length).toBe(1);
  const delivery=panel(page).getByRole("region",{name:"Deliver saved original MP4",exact:true});
  await delivery.getByRole("textbox",{name:"Saved MP4 export path",exact:true}).fill("/synthetic-output/hybrid.mp4");
  await delivery.getByRole("button",{name:"Export saved original MP4",exact:true}).click();
  await expect.poll(async()=>(await commands(page)).filter(call=>call.command==="export_saved_native_av_master").length).toBe(1);
  const observed=await commands(page);
  expect(observed.filter(call=>call.command==="render_native_hybrid_master")).toHaveLength(1);
  expect(observed.filter(call=>call.command==="issue_effect_grant").map(call=>call.request.effect)).toEqual(["render_local","deliver_local"]);
});
test("a two-source cut checks and creates a master with the normal render permission",async({page})=>{
  await page.goto("/tests/fixtures/hybrid-delivery.html?layout=manim-first");
  await ready(page);
  await expect(panel(page).getByRole("status")).toContainText("2 sources checked");
  await panel(page).getByRole("button",{name:"Create hybrid master",exact:true}).click();
  await expect(panel(page).getByLabel("Completed original hybrid master")).toContainText("c".repeat(64));
  const observed=await commands(page);
  expect(observed.filter(call=>call.command==="render_native_hybrid_master")).toHaveLength(1);
  expect(observed.filter(call=>call.command==="issue_effect_grant").map(call=>call.request.effect)).toEqual(["render_local"]);
});
test("a project revision arriving during the normal grant dialog prevents native dispatch",async({page})=>{
  await ready(page);
  await page.evaluate(()=>window.__HYBRID_DELIVERY__.holdGrant());
  await panel(page).getByRole("button",{name:"Create hybrid master",exact:true}).click();
  await expect.poll(async()=>(await commands(page)).filter(call=>call.command==="issue_effect_grant").length).toBe(1);
  await page.evaluate(()=>window.__HYBRID_DELIVERY__.changeRevision());
  await page.evaluate(()=>window.__HYBRID_DELIVERY__.resolveGrant());
  await expect(panel(page).getByRole("button",{name:"Check hybrid sources",exact:true})).toBeEnabled();
  expect((await commands(page)).filter(call=>call.command==="render_native_hybrid_master")).toHaveLength(0);
});
test("UNKNOWN remains blocked after a new read-only source check and never requests another render",async({page})=>{
  await ready(page);
  await page.evaluate(()=>window.__HYBRID_DELIVERY__.unknownMaster());
  await panel(page).getByRole("button",{name:"Create hybrid master",exact:true}).click();
  await expect(panel(page).getByRole("alert")).toContainText("Original outcome is unknown");
  await panel(page).getByRole("button",{name:"Check hybrid sources",exact:true}).click();
  await expect(panel(page).getByRole("button",{name:"Check hybrid sources",exact:true})).toBeEnabled();
  await expect(panel(page).getByRole("button",{name:"Create hybrid master",exact:true})).toBeDisabled();
  expect((await commands(page)).filter(call=>call.command==="render_native_hybrid_master")).toHaveLength(1);
  expect((await commands(page)).filter(call=>call.command==="issue_effect_grant")).toHaveLength(1);
});
test("late completion remains with its original revision and is not adopted by the new view",async({page})=>{
  await ready(page);
  await page.evaluate(()=>window.__HYBRID_DELIVERY__.holdMaster());
  await panel(page).getByRole("button",{name:"Create hybrid master",exact:true}).click();
  await expect.poll(async()=>(await commands(page)).filter(call=>call.command==="render_native_hybrid_master").length).toBe(1);
  await page.evaluate(()=>window.__HYBRID_DELIVERY__.changeRevision());
  await page.evaluate(()=>window.__HYBRID_DELIVERY__.resolveMaster());
  await expect(page.getByText("The native action returned for its original inputs.",{exact:false})).toBeVisible();
  await expect(panel(page).getByLabel("Completed original hybrid master")).toHaveCount(0);
  expect((await commands(page)).filter(call=>call.command==="render_native_hybrid_master")).toHaveLength(1);
});
