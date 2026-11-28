import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import {test,expect,type Page} from "@playwright/test";
import type {AudioQcFixtureSelection} from "./fixtures/native-av-audio-qc";

// Real components and decoder with explicit synthetic IPC. SQLite reopening is
// verified separately by Native tests; this fixture grants no production proof.
const receipt="11111111-1111-4111-8111-111111111111";
const stream="22222222-2222-4222-8222-222222222222";
const token="33333333-3333-4333-8333-333333333333";
const calls=(page:Page)=>page.evaluate(()=>window.__NATIVE_AV_AUDIO_QC__.calls());
const selection=(page:Page)=>page.evaluate(()=>window.__NATIVE_AV_AUDIO_QC__.selection());
const find=(page:Page)=>page.getByRole("button",{name:"Find saved MP4s",exact:true});
const choose=(page:Page)=>page.getByRole("button",{name:"Review saved original "+receipt,exact:true});
async function resolve(page:Page,id:number,value:unknown){
  await page.evaluate(({id,value})=>window.__NATIVE_AV_AUDIO_QC__.resolve(id,value),{id,value});
}
function inventory(source:AudioQcFixtureSelection,sha="1".repeat(64)){
  return {schema:"motionwright.saved-master-inventory/1",project_id:source.projectId,generation:source.generation,
    revision:source.revision,deliverable_id:source.deliverableId,truncated:false,
    items:[{mux_receipt_id:receipt,master_sha256:sha,frame_count:120,fps_num:30,fps_den:1}]};
}
function descriptor(source:AudioQcFixtureSelection,sha:string,size:number){
  return {schema:"motionwright.native-av-review/1",project_id:source.projectId,generation:source.generation,
    revision:source.revision,deliverable_id:source.deliverableId,export_token:token,stream_token:stream,
    url:"http://motionwright-media.localhost/"+stream,sha256:sha,size_bytes:size,max_range_bytes:4*1024*1024,media_type:"video/mp4"};
}
async function open(page:Page){
  await page.goto("/tests/fixtures/native-av-audio-qc.html?saved=1&playback=1");
  await expect(page.getByRole("region",{name:"Saved original MP4s",exact:true})).toBeVisible();
  expect(await calls(page)).toEqual([]);
}

test("a saved original is discovered and decoded without an audio measurement or production request",async({page})=>{
  const source=process.env.MOTIONWRIGHT_REVIEW_MEDIA_FIXTURE;
  test.skip(!source,"Requires the existing synthetic MP4; this test never renders one.");
  const bytes=await readFile(source!);const sha=createHash("sha256").update(bytes).digest("hex");
  await page.route("http://motionwright-media.localhost/**",route=>route.fulfill({contentType:"video/mp4",body:bytes}));
  await open(page);const current=await selection(page);
  await find(page).click();await expect.poll(async()=>(await calls(page)).length).toBe(1);
  await resolve(page,1,inventory(current,sha));await choose(page).click();
  await expect.poll(async()=>(await calls(page)).length).toBe(2);
  const request=(await calls(page))[1].args!.request;
  expect(request).toEqual({project_id:current.projectId,generation:current.generation,revision:current.revision,
    deliverable_id:current.deliverableId,mux_receipt_id:receipt,expected_master_sha256:sha});
  await resolve(page,2,descriptor(current,sha,bytes.length));
  const video=page.locator("video");
  await expect.poll(()=>video.evaluate(el=>(el as HTMLVideoElement).duration)).toBe(4);
  await expect(page.getByText("Read-only recovery; export permissions from an earlier session are not restored.")).toBeVisible();
  const original=await video.elementHandle();
  await video.evaluate(async el=>{const media=el as HTMLVideoElement;media.muted=true;await media.play();});
  await expect.poll(()=>video.evaluate(el=>(el as HTMLVideoElement).currentTime)).toBeGreaterThan(.1);
  await page.evaluate(value=>window.__NATIVE_AV_AUDIO_QC__.setSelection(value),{...current,revision:current.revision+1});
  await expect(video).toHaveCount(0);
  expect(await original!.evaluate(el=>(el as HTMLVideoElement).paused)).toBe(true);
  await expect(choose(page)).toHaveCount(0);
  expect((await calls(page)).map(call=>call.command)).toEqual(["native_av_saved_master_inventory","review_native_av_master"]);
  await page.reload();await expect(find(page)).toBeVisible();
  expect(await calls(page)).toEqual([]);
  await expect(page.locator("video")).toHaveCount(0);
});

test("late and foreign saved-media discovery cannot populate a changed source",async({page})=>{
  await open(page);const current=await selection(page);
  await find(page).click();await expect.poll(async()=>(await calls(page)).length).toBe(1);
  await page.evaluate(value=>window.__NATIVE_AV_AUDIO_QC__.setSelection(value),{...current,revision:current.revision+1});
  await resolve(page,1,inventory(current));
  await expect(choose(page)).toHaveCount(0);await expect(find(page)).toBeEnabled();
  await find(page).click();await expect.poll(async()=>(await calls(page)).length).toBe(2);
  await resolve(page,2,inventory(current));
  await expect(page.getByRole("alert")).toHaveText("Saved originals are unavailable for this source and owner connection. Your project is unchanged.");
  await expect(choose(page)).toHaveCount(0);
  expect((await calls(page)).map(call=>call.command)).toEqual(["native_av_saved_master_inventory","native_av_saved_master_inventory"]);
});

test("a saved original with a mismatched returned hash stays closed without fallback or QC",async({page})=>{
  await open(page);const current=await selection(page);
  await find(page).click();await expect.poll(async()=>(await calls(page)).length).toBe(1);
  await resolve(page,1,inventory(current));await choose(page).click();
  await expect.poll(async()=>(await calls(page)).length).toBe(2);
  await resolve(page,2,descriptor(current,"2".repeat(64),1000));
  await expect(page.getByRole("alert")).toContainText("Native MP4 review unavailable");
  await expect(page.locator("video")).toHaveCount(0);
  await expect(page.getByRole("button",{name:"Retry readback"})).toBeEnabled();
  expect((await calls(page)).map(call=>call.command)).toEqual(["native_av_saved_master_inventory","review_native_av_master"]);
});


test("reopening after reload shows the original audio timing without another render or a delivery grant",async({page})=>{
  await page.route("http://motionwright-media.localhost/**",route=>route.abort());
  await page.goto("/tests/fixtures/native-av-audio-qc.html?saved=1&export=1&playback=1");
  await page.reload();
  const current=await selection(page), sha="1".repeat(64);
  await find(page).click();await expect.poll(async()=>(await calls(page)).length).toBe(1);
  const available=inventory(current,sha);
  available.items[0].frame_count=31;
  await resolve(page,1,available);
  await choose(page).click();
  await expect.poll(async()=>(await calls(page)).length).toBe(2);
  const delivery=page.getByRole("region",{name:"Deliver saved original MP4",exact:true});
  await expect(delivery).toHaveCount(0);
  await resolve(page,2,{...descriptor(current,sha,1000),schema:"motionwright.native-av-review/2",
    saved_hybrid_timing:{status:"observed",clock:{exact_video_clock:true,audio_start_sample:0,
      presented_audio_samples:49_584,authored_audio_samples:49_600}}});
  const review=page.getByRole("region",{name:"Native master playback review",exact:true});
  const notice=review.getByRole("status").filter({hasText:"Review only."});
  await expect(notice).toContainText(/49\D?584 samples/);
  await expect(notice).toContainText(/requires 49\D?600/);
  await expect(review.getByRole("alert")).toContainText("This WebView could not decode the recovered original MP4.");
  await expect(page.getByRole("region",{name:"Deliver saved original MP4",exact:true})).toHaveCount(0);
  await review.getByRole("button",{name:"Retry readback",exact:true}).click();
  await expect.poll(async()=>(await calls(page)).length).toBe(3);
  await expect(notice).toContainText(/requires 49\D?600/);
  await expect(page.getByRole("region",{name:"Deliver saved original MP4",exact:true})).toHaveCount(0);
  expect((await calls(page)).map(call=>call.command)).toEqual([
    "native_av_saved_master_inventory","review_native_av_master","review_native_av_master",
  ]);
});

async function selectForExport(page:Page){
  await page.route("http://motionwright-media.localhost/**",route=>route.abort());
  await page.goto("/tests/fixtures/native-av-audio-qc.html?saved=1&export=1&playback=1");
  const current=await selection(page);
  await find(page).click();await expect.poll(async()=>(await calls(page)).length).toBe(1);
  await resolve(page,1,inventory(current));await choose(page).click();
  await expect.poll(async()=>(await calls(page)).length).toBe(2);
  await resolve(page,2,descriptor(current,"1".repeat(64),1024));
  await expect(page.getByRole("region",{name:"Deliver saved original MP4",exact:true})).toBeVisible();
  await page.getByRole("textbox",{name:"Saved MP4 export path",exact:true}).fill("E:\\Exports\\original.mp4");
  return current;
}
test("saved delivery requests fresh destination permission and copies only the selected original",async({page})=>{
  const current=await selectForExport(page);
  await page.getByRole("checkbox",{name:"Write saved MP4 integrity receipt"}).check();
  await page.getByRole("button",{name:"Export saved original MP4",exact:true}).click();
  await expect.poll(async()=>(await calls(page)).filter(c=>c.command==="issue_effect_grant").length).toBe(1);
  const grant=(await calls(page)).find(c=>c.command==="issue_effect_grant")!;
  expect(grant.args!.request).toEqual({effect:"deliver_local",project_id:current.projectId,
    generation:current.generation,revision:current.revision,subject:"E:\\Exports\\original.mp4"});
  await resolve(page,grant.id,{token});
  await expect.poll(async()=>(await calls(page)).filter(c=>c.command==="export_saved_native_av_master").length).toBe(1);
  const copy=(await calls(page)).find(c=>c.command==="export_saved_native_av_master")!;
  expect(copy.args!.request).toEqual({project_id:current.projectId,generation:current.generation,revision:current.revision,
    deliverable_id:current.deliverableId,mux_receipt_id:receipt,expected_master_sha256:"1".repeat(64),
    effect_grant:token,destination:"E:\\Exports\\original.mp4",include_integrity_manifest:true});
  await resolve(page,copy.id,{destination:"E:\\Exports\\original.mp4",size_bytes:104557,sha256:"1".repeat(64),
    revision:current.revision,deliverable_id:current.deliverableId,source_current:true,
    integrity_manifest_path:"E:\\Exports\\original.mp4.motionwright-integrity.json"});
  await expect(page.getByRole("status",{name:"Saved MP4 delivery receipt"})).toContainText("Exported and verified");
  expect((await calls(page)).some(c=>/assemble|render|measure/.test(c.command))).toBe(false);
});
test("saved delivery stops before copying when project identity changes during permission",async({page})=>{
  const current=await selectForExport(page);
  await page.getByRole("button",{name:"Export saved original MP4",exact:true}).click();
  await expect.poll(async()=>(await calls(page)).filter(c=>c.command==="issue_effect_grant").length).toBe(1);
  const grant=(await calls(page)).find(c=>c.command==="issue_effect_grant")!;
  await page.evaluate(value=>window.__NATIVE_AV_AUDIO_QC__.setSelection(value),{...current,revision:current.revision+1});
  await resolve(page,grant.id,{token});
  expect((await calls(page)).filter(c=>c.command==="export_saved_native_av_master")).toHaveLength(0);
  await expect(page.getByRole("region",{name:"Deliver saved original MP4",exact:true})).toHaveCount(0);
});
test("saved delivery rejects destination ABA while a fresh permission is pending",async({page})=>{
  await selectForExport(page);
  await page.getByRole("button",{name:"Export saved original MP4",exact:true}).click();
  await expect.poll(async()=>(await calls(page)).filter(c=>c.command==="issue_effect_grant").length).toBe(1);
  const grant=(await calls(page)).find(c=>c.command==="issue_effect_grant")!;
  const input=page.getByRole("textbox",{name:"Saved MP4 export path",exact:true});
  await input.fill("E:\\Exports\\other.mp4");await input.fill("E:\\Exports\\original.mp4");
  await resolve(page,grant.id,{token});
  expect((await calls(page)).filter(c=>c.command==="export_saved_native_av_master")).toHaveLength(0);
  await expect(page.getByRole("button",{name:"Export saved original MP4",exact:true})).toBeEnabled();
});
test("an unresolved saved delivery is shown once and is never retried automatically",async({page})=>{
  await selectForExport(page);
  await page.getByRole("button",{name:"Export saved original MP4",exact:true}).click();
  await expect.poll(async()=>(await calls(page)).filter(c=>c.command==="issue_effect_grant").length).toBe(1);
  const grant=(await calls(page)).find(c=>c.command==="issue_effect_grant")!;
  await resolve(page,grant.id,{token});
  await expect.poll(async()=>(await calls(page)).filter(c=>c.command==="export_saved_native_av_master").length).toBe(1);
  const copy=(await calls(page)).find(c=>c.command==="export_saved_native_av_master")!;
  await page.evaluate(id=>window.__NATIVE_AV_AUDIO_QC__.reject(id,"Original delivery unavailable; inspect the destination."),copy.id);
  await expect(page.getByRole("region",{name:"Deliver saved original MP4"}).getByRole("alert")).toContainText("inspect the destination");
  await expect(page.getByRole("status",{name:"Saved MP4 delivery receipt"})).toHaveCount(0);
  await page.evaluate(()=>window.__NATIVE_AV_AUDIO_QC__.settle());
  expect((await calls(page)).filter(c=>c.command==="export_saved_native_av_master")).toHaveLength(1);
});

test("private delivery approves the exact plan and inspects the slot after an unknown reply", async ({page}) => {
  await page.goto("/tests/fixtures/native-av-audio-qc.html?saved=1&private-delivery=1");
  const current = await selection(page);
  const slot = page.getByRole("region", {name: "Private delivery slot"});
  const prepare = slot.getByRole("button", {name: "Prepare private delivery"});
  const approve = slot.getByRole("button", {name: "Approve and commit privately"});
  const inspect = slot.getByRole("button", {name: "Inspect private slot"});
  const sha256 = "1".repeat(64);
  const priorCommitId = "55555555-5555-4555-8555-555555555555";
  const prepared = {
    subject: "private-delivery:" + "b".repeat(64),
    plan: {
      project_id: current.projectId, generation: current.generation, revision: current.revision,
      deliverable_id: current.deliverableId, mux_receipt_id: receipt, previous_commit_id: priorCommitId,
      artifact: {sha256, size_bytes: 1024},
    },
  };
  const manifest = {commit_id: "44444444-4444-4444-8444-444444444444", plan: prepared.plan};
  await find(page).click();
  await resolve(page, 1, inventory(current));
  await choose(page).click();
  await inspect.click();
  const priorSha256 = "2".repeat(64);
  await resolve(page, 3, {
    commit_id: priorCommitId,
    plan: {...manifest.plan, previous_commit_id: null, artifact: {sha256: priorSha256, size_bytes: 2048}},
  });
  await expect(slot.getByRole("status")).toContainText(priorSha256);
  await prepare.click();
  await expect.poll(async () => (await calls(page)).at(-1)?.command).toBe("prepare_private_delivery");
  await resolve(page, 4, prepared);
  await expect(slot.getByText(`Expected slot commit ${priorCommitId}.`)).toBeVisible();
  expect((await calls(page)).map(call => call.command)).toEqual([
    "native_av_saved_master_inventory", "review_native_av_master", "inspect_private_delivery", "prepare_private_delivery",
  ]);
  await approve.click();
  await expect.poll(async () => (await calls(page)).at(-1)?.args?.request).toMatchObject({
    effect: "promote_private_delivery", subject: prepared.subject,
  });
  await resolve(page, 5, {token});
  await expect.poll(async () => (await calls(page)).at(-1)?.args?.request).toEqual({
    plan: prepared.plan, effect_grant: token,
  });
  await page.evaluate(() => window.__NATIVE_AV_AUDIO_QC__.reject(6, "Connection closed before acknowledgement."));
  await expect(slot.getByRole("alert")).toContainText("Inspect the slot");
  await expect(prepare).toBeDisabled();
  await expect(approve).toBeDisabled();
  await expect(slot.getByRole("status")).toHaveCount(0);
  await inspect.click();
  await resolve(page, 7, manifest);
  await expect(slot.getByRole("status")).toContainText(sha256);
  await expect(approve).toHaveCount(0);
  expect((await calls(page)).filter(call => call.command === "commit_private_delivery")).toHaveLength(1);
});
