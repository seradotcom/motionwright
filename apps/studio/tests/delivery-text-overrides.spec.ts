import { expect, test, type Page } from "@playwright/test";
import type { ProfileFixtureRequest } from "./fixtures/delivery-profile-authority";

// Actual DeliveryProfiles and its retained draft coordinator; fixture replies
// and reopen are synthetic. No SQLite, native renderer or acceptance claim.
const button=(page:Page,name:string)=>page.getByRole("button",{name,exact:true});
const requests=(page:Page)=>page.evaluate(()=>window.__PROFILE_AUTHORITY__.requests());
async function submit(page:Page):Promise<ProfileFixtureRequest> {
  const count=(await requests(page)).length;
  await button(page,"Save profile").click();
  await expect.poll(async()=>(await requests(page)).length).toBe(count+1);
  return (await requests(page))[count];
}
async function confirm(page:Page,request:ProfileFixtureRequest) {
  await page.evaluate(id=>window.__PROFILE_AUTHORITY__.resolve(id),request.id);
  await expect(button(page,"Save profile")).toBeDisabled();
}
test.beforeEach(async({page})=>{
  await page.goto("/tests/fixtures/delivery-profile-authority.html");
  await expect(page.getByRole("textbox",{name:"Profile name",exact:true})).toBeVisible();
});

test("first same-language text override preserves lines, survives reopen and can return to the current source",async({page})=>{
  const original=await page.evaluate(()=>window.__PROFILE_AUTHORITY__.project());
  const scene=original.scenes.find(row=>row.nodes.some(node=>node.kind==="text"))!;
  const node=scene.nodes.find(row=>row.kind==="text")!;
  const profile=original.deliverables[0];
  expect(profile.text_overrides).toEqual({});
  const editor=page.getByRole("textbox",{name:"Delivery text for "+node.name+" in "+scene.name,exact:true});
  const useSource=button(page,"Use source text for "+node.name+" in "+scene.name);
  await expect(page.getByRole("region",{name:"Delivery text overrides",exact:true})).toBeVisible();
  await expect(editor).toHaveValue("");
  await expect(useSource).toBeDisabled();
  await button(page,"Customize source text for "+node.name+" in "+scene.name).click();
  await expect(editor).toHaveValue(node.text!);
  const wording="  A distinct delivery headline\nSecond authored line\n";
  await editor.fill(wording);
  await page.evaluate(()=>window.__PROFILE_AUTHORITY__.unmount());
  await page.evaluate(()=>window.__PROFILE_AUTHORITY__.mount());
  await expect(editor).toHaveValue(wording);
  const request=await submit(page);
  expect(request.change.type).toBe("upsert_deliverable");
  if(request.change.type!=="upsert_deliverable") throw new Error("Expected the existing output profile change");
  expect(request.change.profile.text_overrides[node.id]).toBe(wording);
  await confirm(page,request);
  let saved=await page.evaluate(()=>window.__PROFILE_AUTHORITY__.project());
  expect(saved.revision).toBe(original.revision+1);
  expect(saved.scenes).toEqual(original.scenes);
  expect(saved.deliverables.filter(row=>row.id!==profile.id)).toEqual(original.deliverables.filter(row=>row.id!==profile.id));
  await page.reload();
  await expect(editor).toHaveValue(wording);
  expect(await requests(page)).toEqual([]);

  const newBase="Current base headline\nSource continuation";
  await page.evaluate(({sceneId,nodeId,text})=>window.__PROFILE_AUTHORITY__.externalChange({
    type:"update_canvas_text",scene_id:sceneId,node_id:nodeId,text,
  }),{sceneId:scene.id,nodeId:node.id,text:newBase});
  await expect(editor).toHaveValue(wording);
  await expect(editor.locator("..").locator(".delivery-source-text")).toHaveText(newBase);
  await useSource.click();
  await expect(editor).toHaveValue("");
  await confirm(page,await submit(page));
  saved=await page.evaluate(()=>window.__PROFILE_AUTHORITY__.project());
  expect(saved.deliverables.find(row=>row.id===profile.id)!.text_overrides[node.id]).toBeUndefined();
  expect(saved.scenes.find(row=>row.id===scene.id)!.nodes.find(row=>row.id===node.id)!.text).toBe(newBase);
  await page.reload();
  await expect(editor).toHaveValue("");
  await expect(page.getByRole("region",{name:"Delivery text overrides",exact:true})).toBeVisible();
  await expect(useSource).toBeDisabled();
});

test("locale text retains Unicode and line breaks without offering base-language inheritance",async({page})=>{
  const original=await page.evaluate(()=>window.__PROFILE_AUTHORITY__.project());
  const parent=original.deliverables[0];
  const scene=original.scenes.find(row=>row.nodes.some(node=>node.kind==="text"))!;
  const node=scene.nodes.find(row=>row.kind==="text")!;
  const variant={...structuredClone(parent),id:"70000000-0000-4000-8000-000000000007",
    name:"German delivery",language:"de",parent_profile_id:parent.id,source_revision:original.revision,
    adaptation_notes:["Review explicit localized wording and line breaks for this delivery."],
    voice_track_id:null,timing_locked:false,
    text_overrides:Object.fromEntries(original.scenes.flatMap(row=>row.nodes.filter(item=>item.kind==="text")
      .map(item=>[item.id,"Übersetzung für "+item.name])))};
  await page.evaluate(profile=>window.__PROFILE_AUTHORITY__.externalChange({type:"upsert_deliverable",profile}),variant);
  await page.locator('.delivery-profile-row[data-profile-id="'+variant.id+'"]').click();
  const region=page.getByRole("region",{name:"Localized text",exact:true});
  const editor=region.getByRole("textbox",{name:"Delivery text for "+node.name+" in "+scene.name,exact:true});
  const wording="  Grüße aus Köln\nZweite Zeile — 日本語\n";
  await editor.fill(wording);
  await expect(region.getByRole("button",{name:/Use source text/})).toHaveCount(0);
  await page.reload();
  await page.locator('.delivery-profile-row[data-profile-id="'+variant.id+'"]').click();
  await expect(editor).toHaveValue(wording);
  const request=await submit(page);
  if(request.change.type!=="upsert_deliverable") throw new Error("Expected output profile change");
  expect(request.change.profile.text_overrides).toEqual({...variant.text_overrides,[node.id]:wording});
  await confirm(page,request);
  const saved=await page.evaluate(()=>window.__PROFILE_AUTHORITY__.project());
  expect(saved.deliverables.find(row=>row.id===parent.id)).toEqual(parent);
  expect(saved.scenes).toEqual(original.scenes);
  expect(saved.deliverables.find(row=>row.id===variant.id)!.text_overrides[node.id]).toBe(wording);
});
