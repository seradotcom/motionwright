import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import {expect,test} from "./support/selectedSyntheticProject";
import {fixtureBootstrap} from "../src/fixture";

// Component lifecycle coverage using synthetic media and a fixture transport.
// The separate Service journey proves SQLite/CAS; this does not attest Desktop.
test("original preview retains its source through React setup and isolates modal keyboard playback",async({page})=>{
  const path=process.env.MOTIONWRIGHT_REVIEW_MEDIA_FIXTURE;
  test.skip(!path,"An explicitly synthetic H.264/AAC fixture is required.");
  const bytes=await readFile(path!);
  const digest=createHash("sha256").update(bytes).digest("hex");
  const boot=structuredClone(fixtureBootstrap);
  boot.native_sdk.mode="tauri";
  const asset={id:"b530778e-e494-4521-967f-ef5b8032139e",name:"Component source.mp4",
    media_type:"video/mp4",content_sha256:digest,source_revision:"synthetic fixture"};
  boot.project.assets.push(asset);
  const ranges:number[]=[];
  await page.route("http://motionwright-asset.localhost/**",async route=>{
    const raw=route.request().headers()["range"];
    const selected=raw?.match(/^bytes=(\d+)-(\d*)$/);
    const start=selected?Number(selected[1]):0;
    const end=selected&&selected[2]?Math.min(Number(selected[2]),bytes.length-1):bytes.length-1;
    const body=bytes.subarray(start,end+1);
    ranges.push(body.length);
    const headers:Record<string,string>={"content-type":"video/mp4","content-length":String(body.length),
      "accept-ranges":"bytes","cache-control":"no-store"};
    if(selected)headers["content-range"]="bytes "+start+"-"+end+"/"+bytes.length;
    await route.fulfill({status:selected?206:200,headers,body});
  });
  await page.addInitScript(({boot,asset,size})=>{
    const runtime=window as unknown as {__TAURI_INTERNALS__:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __assetOpens:string[];__assetCloses:string[]};
    runtime.__assetOpens=[];runtime.__assetCloses=[];
    runtime.__TAURI_INTERNALS__={invoke:async(command,args)=>{
      if(command==="bootstrap"||command==="project_projection_head"||command==="open_project")
        return window.__SYNTHETIC_PROJECT_READ__(command,args,boot);
      if(command==="asset_preview_close"){
        runtime.__assetCloses.push((args?.request as {stream_token:string}).stream_token);return null;
      }
      if(command==="asset_preview_open"){
        const token="22222222-2222-4222-8222-"+String(runtime.__assetOpens.length+1).padStart(12,"0");
        runtime.__assetOpens.push(token);
        return {schema:"motionwright.original-asset-preview/1",project_id:boot.project.id,generation:boot.project.generation,
          revision:boot.project.revision,asset_id:asset.id,stream_token:token,url:"http://motionwright-asset.localhost/"+token,
          sha256:asset.content_sha256,size_bytes:size,max_range_bytes:4194304,media_type:"video/mp4"};
      }
      throw new Error("Unexpected component fixture command: "+command);
    }};
  },{boot,asset,size:bytes.length});
  await page.goto("/");
  await page.getByRole("button",{name:"Preview original "+asset.name,exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"Original imported media",exact:true});
  const video=dialog.locator("video");
  try {
    await expect.poll(()=>video.evaluate(v=>(v as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(2);
  } catch(error) {
    console.log("ORIGINAL_MEDIA_COMPONENT",await video.evaluate(v=>({src:v.getAttribute("src"),current:(v as HTMLVideoElement).currentSrc,
      error:(v as HTMLVideoElement).error?.message,ready:(v as HTMLVideoElement).readyState})),ranges);
    throw error;
  }
  await dialog.getByRole("heading",{name:"Original imported media"}).focus();
  await page.keyboard.press("Space");
  await expect(page.getByRole("button",{name:"Pause design preview",exact:true})).toHaveCount(0);
  await video.evaluate(async node=>{const v=node as HTMLVideoElement;v.muted=true;await v.play();});
  await expect.poll(()=>video.evaluate(v=>(v as HTMLVideoElement).currentTime)).toBeGreaterThan(.1);
  const handle=await video.elementHandle();
  const selected=new URL((await video.getAttribute("src"))!).pathname.slice(1);
  await dialog.getByRole("button",{name:"Close preview",exact:true}).click();
  await expect(dialog).toHaveCount(0);
  expect(await handle!.evaluate(v=>(v as HTMLVideoElement).paused)).toBe(true);
  await expect.poll(()=>page.evaluate(token=>(window as unknown as {__assetCloses:string[]}).__assetCloses.includes(token),selected)).toBe(true);
  expect(ranges.length).toBeGreaterThan(0);
});
