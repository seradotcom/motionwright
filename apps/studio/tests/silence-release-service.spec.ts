import {createHash,randomUUID} from "node:crypto";
import {mkdir,writeFile,unlink} from "node:fs/promises";
import {expect,test,type Page,type TestInfo} from "@playwright/test";
import {defaultOriginalMix,type DeliveryRecipe} from "../src/deliveryRecipe";
import {ScopedChangeService} from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"Requires the exact-source StudioService binary, not a demo fallback.");
test.describe.configure({timeout:180_000,retries:0});
const button=(page:Page,name:string)=>page.getByRole("button",{name,exact:true});
function stereoWav(frames:number,sample:number):Buffer{
  const data=Buffer.alloc(44+frames*4);
  data.write("RIFF");data.writeUInt32LE(data.length-8,4);data.write("WAVEfmt ",8);
  data.writeUInt32LE(16,16);data.writeUInt16LE(1,20);data.writeUInt16LE(2,22);
  data.writeUInt32LE(48000,24);data.writeUInt32LE(192000,28);
  data.writeUInt16LE(4,32);data.writeUInt16LE(16,34);data.write("data",36);
  data.writeUInt32LE(frames*4,40);
  for(let i=44;i<data.length;i+=2)data.writeInt16LE(sample,i);
  return data;
}
async function setup(service:ScopedChangeService,info:TestInfo){
  let project=(await service.fixtureBootstrap()).project;
  for(const lock of [...project.locks])project=await service.apply(project,{type:"remove_lock",lock_id:lock.id});
  const samples=project.scenes.reduce((sum,scene)=>{
    const n=BigInt(scene.duration.num)*48000n,d=BigInt(scene.duration.den);
    if(d<=0n||n%d)throw new Error("The exact-service source fixture needs integral sample boundaries.");
    return sum+Number(n/d);
  },0);
  expect(samples).toBeGreaterThanOrEqual(96000);expect(samples).toBeLessThanOrEqual(720000);
  const directory=info.outputPath("original-sources");await mkdir(directory,{recursive:true});
  const originals=[];
  for(const [label,amplitude] of [["voice",2000],["music",10000],["sfx",1000]] as const){
    const bytes=stereoWav(samples,amplitude),path=info.outputPath("original-sources",label+".wav");
    await writeFile(path,bytes,{flag:"wx"});
    const oldAssets=project.assets;
    project=label==="voice"?await service.importVoice(project,path,"Synthetic original narration proxy"):
      await service.importAsset(project,path,"Synthetic original "+label+".wav","audio/wav");
    const asset=project.assets.find(item=>!oldAssets.some(old=>old.id===item.id))!;
    expect(asset.content_sha256).toBe(createHash("sha256").update(bytes).digest("hex"));
    originals.push(asset);
    await unlink(path);
  }
  const track=project.audio.voice_tracks.find(item=>item.asset_id===originals[0].id)!;
  const profile={...project.deliverables[0],included_scene_ids:[],voice_track_id:track.id,audio_sample_rate_hz:48000,language:"en"};
  project=await service.apply(project,{type:"upsert_deliverable",profile});
  project=await service.apply(project,{type:"upsert_transcript_segment",segment:{
    id:randomUUID(),voice_track_id:track.id,start:{num:"1",den:"2"},end:{num:"1",den:"1"},
    text:"Synthetic authored timing, not speech.",speaker:null,alignment:{kind:"manual"},emphasis:[],
  }});
  const recipe:DeliveryRecipe={schema:"motionwright.delivery-recipe/1",scene_intents:[],
    original_mix:{...defaultOriginalMix(),music_asset_id:originals[1].id,sfx_asset_id:originals[2].id}};
  project=await service.apply(project,{type:"set_delivery_recipe",deliverable_profile_id:profile.id,recipe});
  return {project,profileId:profile.id,samples,recipe};
}
async function openAudio(page:Page,profileId:string){
  await button(page,"Audio").click();
  const region=page.getByRole("region",{name:"Original three-bus native PCM audition",exact:true});
  const toggle=region.getByRole("button",{name:/original source mix/});
  if(await toggle.getAttribute("aria-expanded")==="false")await toggle.click();
  await region.getByRole("combobox",{name:"Delivery language / profile",exact:true}).selectOption(profileId);
  const panel=region.getByRole("region",{name:"Original mix silence release",exact:true});
  await expect(panel).toBeVisible();
  return panel;
}

test("Silence release authoring keeps original timing, saves a reviewed terminal fade and reopens the real Store",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","silence-release.sqlite3"),desktopInvokeFixture:true});
  let finalized=false;
  try{
    const {project:base,profileId,samples,recipe}=await setup(service,info);
    await service.attachFixturePage(page,base.id);await page.goto("/");
    let panel=await openAudio(page,profileId);
    let fade=panel.getByRole("textbox",{name:"Silence release fade samples",exact:true});
    let hold=panel.getByRole("textbox",{name:"Terminal silence samples",exact:true});
    const save=button(page,"Save original mix recipe");
    await expect(fade).toHaveValue("");await expect(hold).toHaveValue("");
    await fade.fill("12000");await expect(save).toBeDisabled();
    await hold.fill("12000");
    await fade.fill(String(samples-12000-24000));
    await expect(page.getByText(/Silence release must follow every selected reviewed narration interval/)).toBeVisible();
    await expect(save).toBeDisabled();
    expect(await service.project(base.id)).toEqual(base);
    await fade.fill("12000");
    await expect(save).toBeEnabled();await save.click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(base.revision+1));
    const saved=await service.project(base.id);
    const expected=structuredClone(recipe);expected.original_mix!.silence_release={fade_samples:12000,silence_samples:12000};
    expect(saved.production_design?.delivery_recipes?.[profileId]).toEqual(expected);
    expect(saved.scenes).toEqual(base.scenes);expect(saved.audio).toEqual(base.audio);expect(saved.assets).toEqual(base.assets);
    await service.close();await service.start();await page.reload();
    panel=await openAudio(page,profileId);
    fade=panel.getByRole("textbox",{name:"Silence release fade samples",exact:true});
    hold=panel.getByRole("textbox",{name:"Terminal silence samples",exact:true});
    await expect(fade).toHaveValue("12000");await expect(hold).toHaveValue("12000");
    expect(await service.project(base.id)).toEqual(saved);
    await fade.fill("");await expect(save).toBeDisabled();
    await hold.fill("");await expect(save).toBeEnabled();await save.click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(saved.revision+1));
    const final=await service.project(base.id);
    expect(final.production_design?.delivery_recipes?.[profileId]).toEqual(recipe);
    expect(final.scenes).toEqual(base.scenes);expect(final.audio).toEqual(base.audio);expect(final.assets).toEqual(base.assets);
    expect((await service.history(base.id,base.revision,8)).map(event=>event.change.type)).toEqual(["set_delivery_recipe","set_delivery_recipe"]);
    const closure=await service.finalizeClosedStore(info.outputPath("closed-store"),final,[],[async()=>{await page.close();}]);
    finalized=true;expect(closure.oracle?.project).toEqual(final);expect(service.lifecycleTaints).toEqual([]);
  }finally{
    if(!page.isClosed())await page.close();
    if(!finalized)await service.close();
  }
});
