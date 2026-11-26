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
