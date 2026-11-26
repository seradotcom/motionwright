import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";

// Component/browser coverage only. This test does not mint a real delivery,
// execute Native, attest caption pixels or replace the Rust owner-boundary tests.
test("original MP4 above the binary IPC limit decodes and seeks through bounded ranges",async({page})=>{
  const path=process.env.MOTIONWRIGHT_REVIEW_MEDIA_FIXTURE;
  test.skip(!path,"Requires an explicitly synthetic four-second H.264/AAC fixture; no Native fallback.");
  const original=await readFile(path!);
  expect(original.subarray(4,8).toString()).toBe("ftyp");
  // A legal trailing ISO BMFF free box makes the file larger than the old IPC
  // cap without altering the original media sample offsets or decoded clocks.
  const free=Buffer.alloc(17*1024*1024);
  free.writeUInt32BE(free.length,0);free.write("free",4,"ascii");
  const bytes=Buffer.concat([original,free]);
  const token="f58bcf30-ae92-4cee-b718-7cec5736e640";
  const url="http://motionwright-media.localhost/"+token;
  const digest=createHash("sha256").update(bytes).digest("hex");
  const requests:{range:string|null;length:number}[]=[];
  await page.route(url,async route=>{
    const raw=route.request().headers()["range"]??null;
    const selected=raw?.match(/^bytes=(\d+)-(\d*)$/);
    if(!selected){requests.push({range:raw,length:0});await route.fulfill({status:416,headers:{"Content-Range":"bytes */"+bytes.length},body:""});return;}
    const start=Number(selected[1]);
    const end=Math.min(selected[2]?Number(selected[2]):bytes.length-1,start+4*1024*1024-1,bytes.length-1);
    expect(start).toBeLessThan(bytes.length);expect(end).toBeGreaterThanOrEqual(start);
    const body=bytes.subarray(start,end+1);
    requests.push({range:raw,length:body.length});
    await route.fulfill({status:206,headers:{
      "Content-Type":"video/mp4","Accept-Ranges":"bytes","Cache-Control":"no-store",
      "Content-Length":String(body.length),"Content-Range":"bytes "+start+"-"+end+"/"+bytes.length,
    },body});
  });
  await page.addInitScript(({url,token,digest,size})=>{
    type Request={project_id:string;generation:string;revision:number;export_token:string;stream:boolean};
    type Runtime={__TAURI_INTERNALS__:{invoke:(command:string,args:{request:Request})=>Promise<unknown>};__mediaCommands:string[]};
    const runtime=window as unknown as Runtime;
    runtime.__mediaCommands=[];
    let original:Request|null=null;
    runtime.__TAURI_INTERNALS__={invoke:async(command,args)=>{
      runtime.__mediaCommands.push(command);
      if(command!=="review_native_av_master"||args.request.stream!==true)throw new Error("Unexpected synthetic media operation");
      // A revision change deliberately returns the old source so the view must
      // discard it. A new matching descriptor cannot be inferred from a token.
      original??=args.request;
      return {schema:"motionwright.native-av-review/1",
        project_id:original.project_id,generation:original.generation,revision:original.revision,
        export_token:original.export_token,deliverable_id:"f289d1de-15e5-47f7-8877-9cd04c8d3e27",
        stream_token:token,url,sha256:digest,size_bytes:size,max_range_bytes:4*1024*1024,media_type:"video/mp4"};
    }};
  },{url,token,digest,size:bytes.length});
  await page.goto("/tests/fixtures/audio-review.html");
  await page.getByRole("button",{name:"Open synthetic MP4 component"}).click();
  const video=page.getByLabel("Verified native MP4 review player",{exact:true});
  await expect(video).toHaveAttribute("src",url);
  await expect.poll(()=>video.evaluate(node=>(node as HTMLVideoElement).duration)).toBeCloseTo(4,1);
  await video.evaluate(async node=>{const v=node as HTMLVideoElement;v.muted=true;v.currentTime=.5;await v.play();});
  await expect.poll(async()=>Number(await page.getByLabel("Observed project playhead").textContent())).toBeGreaterThan(.5);
  await page.getByRole("button",{name:"Seek project to three seconds"}).click();
  await expect.poll(()=>video.evaluate(node=>(node as HTMLVideoElement).paused)).toBe(true);
  await expect.poll(()=>video.evaluate(node=>(node as HTMLVideoElement).currentTime)).toBeCloseTo(3,3);
  const old=await video.elementHandle();
  await page.getByRole("button",{name:"Change saved revision"}).click();
  await expect(video).toHaveCount(0);
  expect(await old!.evaluate(node=>(node as HTMLVideoElement).paused)).toBe(true);
  await expect(page.getByRole("region",{name:"Native master playback review"})).toContainText("Native MP4 review unavailable");
  expect(requests.length).toBeGreaterThan(0);
  expect(requests.every(request=>request.range!==null&&request.length>0&&request.length<=4*1024*1024)).toBe(true);
  // Browsers may prefetch or revisit the full file. The contract bounds each
  // transfer and backend allocation, not total bytes over the playback lifetime.
  expect(requests.length).toBeGreaterThan(1);
  const commands=await page.evaluate(()=>(window as unknown as {__mediaCommands:string[]}).__mediaCommands);
  expect(commands.length).toBeGreaterThanOrEqual(2);
  expect(commands.every(command=>command==="review_native_av_master")).toBe(true);
});
