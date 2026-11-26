import {createHash} from "node:crypto";
import {copyFile,mkdir,readFile,unlink,writeFile} from "node:fs/promises";
import {expect,test} from "@playwright/test";
import {ScopedChangeService} from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN||!process.env.MOTIONWRIGHT_ASSET_PREVIEW_MP4,
  "An exact-source StudioService and a real synthetic MP4 fixture are required.");
test.describe.configure({timeout:120_000,retries:0});
const sha=(bytes:Buffer)=>createHash("sha256").update(bytes).digest("hex");
function wav():Buffer {
  const rate=48_000,data=Buffer.alloc(44+rate*2);
  data.write("RIFF",0);data.writeUInt32LE(data.length-8,4);data.write("WAVEfmt ",8);
  data.writeUInt32LE(16,16);data.writeUInt16LE(1,20);data.writeUInt16LE(1,22);
  data.writeUInt32LE(rate,24);data.writeUInt32LE(rate*2,28);data.writeUInt16LE(2,32);data.writeUInt16LE(16,34);
  data.write("data",36);data.writeUInt32LE(rate*2,40);
  for(let sample=0;sample<rate;sample++)data.writeInt16LE(Math.round(Math.sin(sample*2*Math.PI*440/rate)*4000),44+sample*2);
  return data;
}

test("Assets plays original MP4 and WAV from real CAS after input deletion and Store restart, then closes private readers",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","assets.sqlite3"),desktopInvokeFixture:true});
  let finalized=false;
  try{
    let project=(await service.fixtureBootstrap()).project;
    await mkdir(info.outputPath("originals"),{recursive:true});
    const videoPath=info.outputPath("originals","original.mp4"),audioPath=info.outputPath("originals","original.wav");
    await copyFile(process.env.MOTIONWRIGHT_ASSET_PREVIEW_MP4!,videoPath);
    const video=await readFile(videoPath),audio=wav();
    await writeFile(audioPath,audio,{flag:"wx"});
    project=await service.importAsset(project,videoPath,"Editorial source.mp4","video/mp4");
    project=await service.importAsset(project,audioPath,"Voice source.wav","audio/wav");
    await unlink(videoPath);await unlink(audioPath);
    const saved=project;
    // Transport adapter only: the shared Rust registry resolves every range
    // from the real retained SQLite/CAS source. No response media is mocked.
    await page.route("http://motionwright-asset.localhost/**",async route=>{
      const request=route.request(),token=new URL(request.url()).pathname.slice(1);
      const response=await service.invoke<{status:number;media_type:string;content_length:number;content_range:string|null;body:number[]}>(
        "asset_preview_test_range",{request:{stream_token:token,method:request.method(),range:request.headers()["range"]??null}});
      const headers:Record<string,string>={"content-type":response.media_type,"content-length":String(response.content_length),
        "accept-ranges":"bytes","cache-control":"no-store","x-content-type-options":"nosniff"};
      if(response.content_range)headers["content-range"]=response.content_range;
      await route.fulfill({status:response.status,headers,body:Buffer.from(response.body)});
    });
    await service.attachFixturePage(page,project.id);await page.goto("/");
    const inspect=async(name:string,kind:"video"|"audio",original:Buffer,duration:number)=>{
      await page.getByRole("button",{name:"Preview original "+name,exact:true}).click();
      const dialog=page.getByRole("dialog",{name:"Original imported media",exact:true});
      await expect(dialog).toBeVisible();
      const player=dialog.locator(kind);
      await expect.poll(()=>player.evaluate((element:HTMLMediaElement)=>element.readyState)).toBeGreaterThanOrEqual(2);
      await expect.poll(()=>player.evaluate((element:HTMLMediaElement)=>element.duration)).toBeCloseTo(duration,1);
      if(kind==="video")expect(await player.evaluate((element:HTMLVideoElement)=>element.videoWidth>0&&element.videoHeight>0)).toBe(true);
      await player.evaluate(async(element:HTMLMediaElement)=>{element.muted=true;await element.play();});
      await expect.poll(()=>player.evaluate((element:HTMLMediaElement)=>element.currentTime)).toBeGreaterThan(0.1);
      await player.evaluate((element:HTMLMediaElement)=>{element.pause();element.currentTime=element.duration*.7;});
      await expect.poll(()=>player.evaluate((element:HTMLMediaElement)=>element.currentTime)).toBeCloseTo(duration*.7,1);
      await expect.poll(()=>player.evaluate((element:HTMLMediaElement)=>!element.seeking&&element.readyState>=2)).toBe(true);
      const token=new URL((await player.getAttribute("src"))!).pathname.slice(1);
      const originalReply=await service.invoke<{status:number;body:number[]}>("asset_preview_test_range",
        {request:{stream_token:token,method:"GET",range:null}});
      expect(originalReply.status).toBe(200);expect(sha(Buffer.from(originalReply.body))).toBe(sha(original));
      await dialog.getByRole("button",{name:"Close preview",exact:true}).click();
      await expect(dialog).toBeHidden();
      await expect.poll(async()=> (await service.invoke<{status:number}>("asset_preview_test_range",
        {request:{stream_token:token,method:"HEAD",range:null}})).status).toBe(410);
    };
    await inspect("Editorial source.mp4","video",video,4);
    await inspect("Voice source.wav","audio",audio,1);
    expect(await service.project(project.id)).toEqual(saved);
    await service.close();await service.start();await page.reload();
    await inspect("Editorial source.mp4","video",video,4);
    expect(await service.project(project.id)).toEqual(saved);
    const closed=await service.finalizeClosedStore(info.outputPath("closed-store"),saved,[],[async()=>{await page.close();}]);
    finalized=true;expect(closed.oracle?.project).toEqual(saved);expect(service.lifecycleTaints).toEqual([]);
  }finally{
    if(!page.isClosed())await page.close();
    if(!finalized)await service.close();
  }
});
