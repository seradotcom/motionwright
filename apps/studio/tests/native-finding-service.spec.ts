import { writeFile } from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { deflateSync } from "node:zlib";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// App, ProjectEdit grants, StudioService, journal and SQLite are real.
// ONLY render_local grants, runtime/preflight/render replies and PNG readback
// are synthetic native IPC. This is not renderer, installed Tauri or craft acceptance.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"Requires source-pinned StudioService; no simulated Project persistence fallback.");
test.setTimeout(60_000);
const button=(page:Page,name:string)=>page.getByRole("button",{name,exact:true});
interface Request extends Record<string,unknown> {project_id?:string;generation?:string;revision?:number;effect?:string;subject?:string;request_id?:string;effect_grant?:string;deliverable_id?:string;token?:string;frame_index?:number;change?:Change}
const requests=(service:ScopedChangeService,command:string)=>service.transcript.filter(entry=>entry.direction==="request"&&"command" in entry.payload&&entry.payload.command===command);
const applies=(service:ScopedChangeService)=>requests(service,"apply_change");
const grants=(service:ScopedChangeService)=>requests(service,"issue_effect_grant");
function syntheticPng(width:number,height:number):Buffer {
  const stride=width*4+1,raw=Buffer.alloc(stride*height);
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){const i=y*stride+1+x*4;raw[i]=40;raw[i+1]=52;raw[i+2]=65;raw[i+3]=255;}
  const table=Array.from({length:256},(_,value)=>{let crc=value;for(let i=0;i<8;i++)crc=crc&1?(crc>>>1)^0xedb88320:crc>>>1;return crc>>>0;});
  const chunk=(name:string,data:Buffer)=>{const type=Buffer.from(name),joined=Buffer.concat([type,data]);let crc=0xffffffff;
    for(const byte of joined)crc=(crc>>>8)^table[(crc^byte)&255];const out=Buffer.alloc(data.length+12);out.writeUInt32BE(data.length);type.copy(out,4);data.copy(out,8);out.writeUInt32BE((crc^0xffffffff)>>>0,out.length-4);return out;};
  const ihdr=Buffer.alloc(13);ihdr.writeUInt32BE(width);ihdr.writeUInt32BE(height,4);ihdr[8]=8;ihdr[9]=6;
  return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk("IHDR",ihdr),chunk("IDAT",deflateSync(raw)),chunk("IEND",Buffer.alloc(0))]);
}
function installSyntheticNativeIpc(service:ScopedChangeService,projectId:string) {
  const original:ScopedChangeService["invoke"]=service.invoke.bind(service),calls:{command:string;request:Request}[]=[];
  const issued=new Map<string,{project:Project;subject:string}>();
  const previews=new Map<string,{project:Project;profileId:string;frameCount:number;png:Buffer;sha256:string}>();
  service.invoke=async<T=unknown>(command:string,args:Record<string,unknown>={}):Promise<T>=>{
    const request=(args.request??{}) as Request;
    const synthetic=command==="production_runtime_status"||command==="motion_canvas_preflight"||command==="render_motion_canvas"
      ||command==="preview_native_frame"||command==="issue_effect_grant"&&request.effect==="render_local";
    if(!synthetic)return original<T>(command,args);
    calls.push({command,request:structuredClone(request)});
    if(command==="production_runtime_status")return {status:"ready",reason:"Explicit synthetic native IPC fixture; no renderer execution",
      expected_version:"1.0.0",observed_version:"1.0.0",version_compatible:true,
      executable_sha256:"a".repeat(64),connection_identity:"b".repeat(64),pinned_revision:"c".repeat(40)} as T;
    const project=await original<Project>("get_project",{request:{project_id:projectId}});
    if(request.project_id!==project.id||request.generation!==project.generation||request.revision!==project.revision)
      throw new Error("Synthetic native IPC refused a stale source; real Project state was not changed.");
    if(command==="issue_effect_grant"){
      if(!request.subject)throw new Error("Synthetic native grant requires an explicit request subject.");
      const token="synthetic-render-only:"+randomUUID();issued.set(token,{project,subject:request.subject});return {token} as T;
    }
    const profile=project.deliverables.find(value=>value.id===request.deliverable_id);
    if(command==="motion_canvas_preflight"){
      if(!profile)throw new Error("Synthetic preflight profile missing.");
      return {project_resource:"project:"+project.id,generation:project.generation,revision:project.revision,deliverable_id:profile.id,
        verdict:"projection_ready",reason:"Synthetic native boundary only",segment_count:1,total_frames:3600,
        segments:[{segment_id:"synthetic-finding-only",scene_ids:project.scenes.map(scene=>scene.id),frame_count:3600}]} as T;
    }
    if(command==="render_motion_canvas"){
      const grant=issued.get(request.effect_grant??"");
      if(!profile||!grant||grant.subject!==request.request_id||grant.project.revision!==project.revision)throw new Error("Synthetic native render grant mismatch.");
      issued.delete(request.effect_grant!);
      const token="synthetic-frame-only:"+randomUUID(),png=syntheticPng(profile.width,profile.height),sha256=createHash("sha256").update(png).digest("hex");
      previews.set(token,{project,profileId:profile.id,frameCount:3600,png,sha256});
      return {project_resource:"project:"+project.id,generation:project.generation,revision:project.revision,deliverable_id:profile.id,frame_rate:{num:30,den:1},
        segments:[{segment_id:"synthetic-finding-only",scene_ids:project.scenes.map(scene=>scene.id),frame_count:3600,
          plan_ref:"synthetic-only",fingerprint:"synthetic-only",job_ref:"synthetic-only",artifact:{},verification:{}}],
        preview:[{token,segment_id:"synthetic-finding-only",scene_ids:project.scenes.map(scene=>scene.id),frame_count:3600}]} as T;
    }
    const preview=previews.get(request.token??"");
    if(!preview||preview.project.revision!==project.revision||!Number.isSafeInteger(request.frame_index)||request.frame_index!<0||request.frame_index!>=preview.frameCount)
      throw new Error("Synthetic frame selection has no matching current native fixture reference.");
    return Array.from(preview.png) as T;
  };
  return {calls,origins:()=>[...previews.values()].map(value=>({synthetic:true,projectId:value.project.id,generation:value.project.generation,
    revision:value.project.revision,profileId:value.profileId,pngSha256:value.sha256,bytes:value.png.length})),
    restore:async()=>{service.invoke=original;}};
}
function deferred<T>(){let resolve!:(value:T)=>void,reject!:(error:unknown)=>void;const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
async function originalResponse<T>(promise:Promise<T>):Promise<T> {
  let timer:ReturnType<typeof setTimeout>|undefined;
  try{return await Promise.race([promise,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error("The original review request was not observed within 10 seconds.")),10_000);})]);}
  finally{if(timer!==undefined)clearTimeout(timer);}
}
function holdReviewReply(service:ScopedChangeService,lost:boolean) {
  const original:ScopedChangeService["invoke"]=service.invoke.bind(service),gate=deferred<void>(),observed=deferred<Project>(),done=deferred<void>();let armed=true,matched=false;
  void observed.promise.catch(()=>undefined);
  service.invoke=async<T=unknown>(command:string,args:Record<string,unknown>={}):Promise<T>=>{
    const request=args.request as Request|undefined;
    if(!armed||command!=="apply_change"||request?.change?.type!=="add_review")return original<T>(command,args);
    armed=false;matched=true;
    try{const saved=await original<Project>(command,args);observed.resolve(saved);await gate.promise;if(lost)throw new Error("Original finding response lost after the real SQLite commit.");return saved as T;}
    catch(error){observed.reject(error);throw error;}finally{done.resolve();}
  };
  return {observed:observed.promise,release:()=>gate.resolve(),dispose:async()=>{service.invoke=original;gate.resolve();if(matched)await done.promise;}};
}
type Failure={reason:unknown};
const detail=(reason:unknown)=>reason instanceof Error?{name:reason.name,message:reason.message,stack:reason.stack}:{message:String(reason)};
async function finish(page:Page,info:TestInfo,service:ScopedChangeService,final:Project|undefined,observations:Record<string,unknown>,prepare:(()=>Promise<unknown>)[],primary?:Failure) {
  const failures:unknown[]=primary?[primary.reason]:[];let custody:StoreFinalization|undefined;
  try{custody=await service.finalizeClosedStore(info.outputPath("closed-store"),final,[],[...prepare,()=>page.close()]);}catch(error){failures.push(error);}
  try{const path=info.outputPath("native-finding-service-evidence.json");await writeFile(path,JSON.stringify({
    scope:"Actual App, ProjectEdit grants, source-pinned StudioService, review journal and closed SQLite custody",
    syntheticNativeBoundary:"render_local grant, runtime/preflight/render metadata and generated PNG only; no native renderer execution",
    exclusions:["installed Tauri","native visual fidelity","human creative acceptance","A/B visual approval"],observations,final,canonicalOracle:custody?.oracle,
    primaryFailure:primary?detail(primary.reason):null,failures:failures.map(detail),services:[{databasePath:service.databasePath,binaryPath:service.binaryPath,
      transcript:service.transcript,processExits:service.processExits,lifecycleTaints:service.lifecycleTaints,stderr:service.stderr}]},null,2));
    await info.attach("native-finding-service-evidence",{path,contentType:"application/json"});
    const outcome=info.outputPath("closed-store-outcomes.json");await info.attach("closed-store-outcomes",{path:outcome,contentType:"application/json"});
  }catch(error){failures.push(error);}
  if(failures.length===1)throw failures[0];if(failures.length>1)throw new AggregateError(failures,"Finding scenario and finalization retain separate failures",{cause:primary?.reason});
}
const setup=(info:TestInfo)=>ScopedChangeService.create({databasePath:info.outputPath("service","studio.sqlite3"),desktopInvokeFixture:true});
const target=(project:Project)=>{const scene=project.scenes.find(value=>value.renderer==="motion-canvas");if(!scene)throw new Error("No actual Motion Canvas fixture scene.");return scene;};
async function open(page:Page,sceneId:string){await button(page,"Production").click();await button(page,"Native inspection").click();await page.getByLabel("Production working scene",{exact:true}).selectOption(sceneId);}
async function attach(page:Page,service:ScopedChangeService,project:Project){await service.attachFixturePage(page,project.id);await page.goto("/");}
async function revision(page:Page,value:number){await expect(page.locator(".revision-chip").first()).toHaveText("r"+value);}
async function saveSceneRecipe(page:Page,project:Project) {
  await button(page,"Deliver").click();
  for(let index=0;index<project.scenes.length;index++){
    const scene=project.scenes[index];
    await page.getByLabel(scene.name+" narrative role",{exact:true}).selectOption(index===0?"hook":"problem");
    await page.getByLabel(scene.name+" motion archetype",{exact:true}).selectOption(index===0?"statement":"diagram_build");
  }
  await button(page,"Save scene intent").click();await revision(page,project.revision+1);
}
async function inspectSyntheticNative(page:Page,sceneId:string) {
  await button(page,"Deliver").click();await button(page,"Check Film projection").click();
  await expect(page.getByLabel("First native production readiness",{exact:true})).toContainText("ELIGIBLE FOR AUTHORIZED DISPATCH");
  await button(page,"Render native segments").click();
  await expect(page.getByLabel("Native render evidence",{exact:true})).toContainText("NATIVE");
  await open(page,sceneId);await button(page,"Inspect current native frames").click();
  await expect(page.getByRole("button",{name:/^Inspect native frame /}).first()).toBeVisible();
}
async function author(page:Page,nodeId:string|null) {
  await page.getByRole("combobox",{name:"Severity",exact:true}).selectOption("blocking");await page.getByRole("combobox",{name:"Confidence",exact:true}).selectOption("uncertain");
  await page.getByRole("combobox",{name:"Affected object",exact:true}).selectOption(nodeId??"");
  await page.getByLabel("Violated constraint",{exact:true}).fill("  Preserve authored text position and legibility.  ");
  await page.getByLabel("What is visible in this frame",{exact:true}).fill("Synthetic transport fixture observation.\nExact unfinished 1e");
  await page.getByLabel("Proposed localized repair",{exact:true}).fill("Review a small move of only the selected object.");
}
async function rawPreserved(page:Page) {
  await expect(page.getByRole("combobox",{name:"Severity",exact:true})).toHaveValue("blocking");await expect(page.getByRole("combobox",{name:"Confidence",exact:true})).toHaveValue("uncertain");
  await expect(page.getByLabel("Violated constraint",{exact:true})).toHaveValue("  Preserve authored text position and legibility.  ");
  await expect(page.getByLabel("What is visible in this frame",{exact:true})).toHaveValue("Synthetic transport fixture observation.\nExact unfinished 1e");
  await expect(page.getByLabel("Proposed localized repair",{exact:true})).toHaveValue("Review a small move of only the selected object.");
}
const copies=(page:Page)=>page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith("motionwright.native-finding-recovery.v1:")));
function authorPayloadUnchanged(saved:Project,initial:Project) {
  for(const key of ["id","generation","active_branch","title","scenes","brief","narrative","audio","assets","markers","locks","deliverables","visual_language",
    "proposal_sets","model_invocations","extensions","handoffs","production_design"] as const)expect(saved[key]).toEqual(initial[key]);
}
function newReview(saved:Project,initial:Project) {
  const review=saved.reviews.filter(value=>!initial.reviews.some(before=>before.id===value.id));expect(review).toHaveLength(1);
  expect(review[0].kind).toBe("creative");expect(review[0].status).toBe("open");expect(review[0].anchor.revision).toBe(initial.revision);
  expect(review[0].resolution).toBeNull();const body=JSON.parse(review[0].body);
  expect(body.schema).toBe("motionwright.manual-native-frame-finding/1");expect(body.interpretation).toBe("manual_review_not_automated_critic_verdict");
  expect(body.source.revision).toBe(initial.revision);expect(body.observation).toBeDefined();expect(body).not.toHaveProperty("effect_grant");
  return {review:review[0],body};
}

test("finding input without a current native grant stays editable across App navigation and reload without dispatch",async({page},info)=>{
  const service=await setup(info),observations:Record<string,unknown>={};let final:Project|undefined,primary:Failure|undefined;
  try{const initial=(await service.fixtureBootstrap()).project,scene=target(initial);observations.initial=initial;await attach(page,service,initial);await open(page,scene.id);
    await author(page,scene.nodes[0]?.id??null);const writes=applies(service).length,permissions=grants(service).length;
    await expect(button(page,"Record source-anchored review")).toBeDisabled();await button(page,"Timeline").click();await open(page,scene.id);await rawPreserved(page);
    await page.reload();await open(page,scene.id);await rawPreserved(page);await expect(button(page,"Record source-anchored review")).toBeDisabled();
    expect(applies(service)).toHaveLength(writes);expect(grants(service)).toHaveLength(permissions);final=await service.project(initial.id);expect(final).toEqual(initial);
    observations.copies=await copies(page);await page.screenshot({path:info.outputPath("no-current-native-source.png"),fullPage:true});
  }catch(error){primary={reason:error};try{final=(await service.fixtureBootstrap()).project;}catch{/* Finalizer retains failure. */}}
  await finish(page,info,service,final,observations,[],primary);
});
test("six retained finding fields become one real source-anchored review and survive full SQLite reopening",async({page},info)=>{
  const service=await setup(info),boot=(await service.fixtureBootstrap()).project,native=installSyntheticNativeIpc(service,boot.id),observations:Record<string,unknown>={};let final:Project|undefined,primary:Failure|undefined;
  try{await attach(page,service,boot);await saveSceneRecipe(page,boot);const initial=await service.project(boot.id),scene=target(initial);observations.initial=initial;
    await inspectSyntheticNative(page,scene.id);await author(page,scene.nodes[0]?.id??null);const object=await page.getByRole("combobox",{name:"Affected object",exact:true}).inputValue();
    await button(page,"Timeline").click();await open(page,scene.id);await rawPreserved(page);await page.reload();await open(page,scene.id);await rawPreserved(page);
    await expect(button(page,"Record source-anchored review")).toBeDisabled();const writes=applies(service).length,permissions=grants(service).length;
    await inspectSyntheticNative(page,scene.id);await rawPreserved(page);await expect(page.getByRole("combobox",{name:"Affected object",exact:true})).toHaveValue(object);
    await button(page,"Record source-anchored review").click();await revision(page,initial.revision+1);
    await expect(page.getByRole("status").filter({hasText:"Saved source-anchored review"})).toBeVisible();
    final=await service.project(boot.id);const recorded=newReview(final,initial);expect(native.origins().some(value=>value.pngSha256===recorded.body.source.png_sha256)).toBe(true);
    authorPayloadUnchanged(final,initial);expect(applies(service)).toHaveLength(writes+1);expect(grants(service)).toHaveLength(permissions+1);await rawPreserved(page);
    await service.close();await service.start();expect(await service.project(final.id)).toEqual(final);
    await page.reload();await open(page,scene.id);await rawPreserved(page);
    observations.recorded=recorded;observations.copies=await copies(page);await page.screenshot({path:info.outputPath("saved-finding-retained.png"),fullPage:true});
  }catch(error){primary={reason:error};try{final=await service.project(boot.id);}catch{/* Finalizer records unavailable oracle. */}}
  observations.syntheticNativeCalls=native.calls;observations.syntheticNativeOrigins=native.origins();await finish(page,info,service,final,observations,[native.restore],primary);
});
test("the original real review response preserves later finding input after App navigation",async({page},info)=>{
  const service=await setup(info),boot=(await service.fixtureBootstrap()).project,native=installSyntheticNativeIpc(service,boot.id),observations:Record<string,unknown>={};let held:ReturnType<typeof holdReviewReply>|undefined,final:Project|undefined,primary:Failure|undefined;
  try{await attach(page,service,boot);await saveSceneRecipe(page,boot);const initial=await service.project(boot.id),scene=target(initial);observations.initial=initial;
    await inspectSyntheticNative(page,scene.id);await author(page,scene.nodes[0]?.id??null);held=holdReviewReply(service,false);
    await button(page,"Record source-anchored review").click();const saved=await originalResponse(held.observed);
    await page.getByLabel("What is visible in this frame",{exact:true}).fill("Later exact input typed while the original response was delayed.");
    await button(page,"Timeline").click();held.release();await revision(page,saved.revision);await open(page,scene.id);
    await expect(page.getByLabel("What is visible in this frame",{exact:true})).toHaveValue("Later exact input typed while the original response was delayed.");
    await expect(page.getByRole("status").filter({hasText:"Saved source-anchored review"})).toBeVisible();newReview(saved,initial);authorPayloadUnchanged(saved,initial);
    await page.reload();await open(page,scene.id);await expect(page.getByLabel("What is visible in this frame",{exact:true})).toHaveValue("Later exact input typed while the original response was delayed.");
    final=await service.project(boot.id);expect(final).toEqual(saved);observations.copies=await copies(page);
  }catch(error){primary={reason:error};try{final=await service.project(boot.id);}catch{/* Finalizer records. */}}
  observations.syntheticNativeCalls=native.calls;observations.syntheticNativeOrigins=native.origins();
  await finish(page,info,service,final,observations,[async()=>held?.dispose(),native.restore],primary);
});
test("a lost real review response remains UNKNOWN through fresh history review before a separate authored save",async({page},info)=>{
  const service=await setup(info),boot=(await service.fixtureBootstrap()).project,native=installSyntheticNativeIpc(service,boot.id),observations:Record<string,unknown>={};let held:ReturnType<typeof holdReviewReply>|undefined,final:Project|undefined,primary:Failure|undefined;
  try{await attach(page,service,boot);await saveSceneRecipe(page,boot);const initial=await service.project(boot.id),scene=target(initial);observations.initial=initial;
    await inspectSyntheticNative(page,scene.id);await author(page,scene.nodes[0]?.id??null);held=holdReviewReply(service,true);
    await button(page,"Record source-anchored review").click();const saved=await originalResponse(held.observed);
    await page.getByLabel("What is visible in this frame",{exact:true}).fill("Later observation remains separate from the unknown original.");
    held.release();await expect(page.getByRole("region",{name:"Unconfirmed native finding",exact:true})).toBeVisible();await held.dispose();held=undefined;
    const writes=applies(service).length,permissions=grants(service).length;
    await page.reload();await open(page,scene.id);await expect(page.getByLabel("What is visible in this frame",{exact:true})).toHaveValue("Later observation remains separate from the unknown original.");
    const originals=await copies(page);await button(page,"Read saved review outcome").click();
    await expect(page.getByRole("status").filter({hasText:"1 history changes match"})).toBeVisible();
    expect(applies(service)).toHaveLength(writes);expect(grants(service)).toHaveLength(permissions);
    await button(page,"Keep unknown copy and author a separate finding").click();expect(await service.project(boot.id)).toEqual(saved);
    const retained=await copies(page);for(const entry of originals)expect(retained).toContainEqual(entry);
    await inspectSyntheticNative(page,scene.id);await expect(button(page,"Record source-anchored review")).toBeDisabled();
    await button(page,"Use current frame for this finding").click();await button(page,"Record source-anchored review").click();await revision(page,saved.revision+1);
    final=await service.project(boot.id);const second=newReview(final,saved);expect(second.body.observation).toBe("Later observation remains separate from the unknown original.");
    expect(final.reviews.filter(value=>!initial.reviews.some(before=>before.id===value.id))).toHaveLength(2);authorPayloadUnchanged(final,initial);
    expect(applies(service)).toHaveLength(writes+1);expect(grants(service)).toHaveLength(permissions+1);observations.originalCopies=originals;observations.finalCopies=await copies(page);
  }catch(error){primary={reason:error};try{final=await service.project(boot.id);}catch{/* Finalizer records. */}}
  observations.syntheticNativeCalls=native.calls;observations.syntheticNativeOrigins=native.origins();await finish(page,info,service,final,observations,[async()=>held?.dispose(),native.restore],primary);
});
test("a foreign revision and corrupt local bytes cannot silently rebind or dispatch an authored finding",async({page},info)=>{
  const service=await setup(info),boot=(await service.fixtureBootstrap()).project,native=installSyntheticNativeIpc(service,boot.id),observations:Record<string,unknown>={};let final:Project|undefined,primary:Failure|undefined;
  try{await attach(page,service,boot);await saveSceneRecipe(page,boot);const initial=await service.project(boot.id),scene=target(initial);
    await inspectSyntheticNative(page,scene.id);await author(page,scene.nodes[0]?.id??null);
    final=await service.apply(initial,{type:"rename_project",title:initial.title+" observed elsewhere"});const writes=applies(service).length,permissions=grants(service).length;
    await page.reload();await open(page,scene.id);await rawPreserved(page);await expect(button(page,"Record source-anchored review")).toBeDisabled();
    expect(applies(service)).toHaveLength(writes);expect(grants(service)).toHaveLength(permissions);
    const corrupt=await page.evaluate(()=>{const key=Object.keys(localStorage).find(value=>value.startsWith("motionwright.native-finding-recovery.v1:"))!;const raw='{"retained unreadable finding":';localStorage.setItem(key,raw);return {key,raw};});
    await page.reload();await open(page,scene.id);await expect(button(page,"Retry finding recovery")).toBeVisible();
    await page.getByLabel("What is visible in this frame",{exact:true}).fill("New visible input cannot overwrite the unreadable original.");
    await expect(button(page,"Record source-anchored review")).toBeDisabled();expect(await page.evaluate(key=>localStorage.getItem(key),corrupt.key)).toBe(corrupt.raw);
    expect(await service.project(boot.id)).toEqual(final);expect(applies(service)).toHaveLength(writes);expect(grants(service)).toHaveLength(permissions);
    observations.corruptOriginal=corrupt;observations.retainedCopies=await copies(page);
  }catch(error){primary={reason:error};try{final=await service.project(boot.id);}catch{/* Finalizer records. */}}
  observations.syntheticNativeCalls=native.calls;observations.syntheticNativeOrigins=native.origins();await finish(page,info,service,final,observations,[native.restore],primary);
});
for(const kind of ["data","pointer"] as const)test("a silent finding "+kind+" backup failure grants nothing until explicit local recovery",async({page},info)=>{
  const service=await setup(info),boot=(await service.fixtureBootstrap()).project,native=installSyntheticNativeIpc(service,boot.id),observations:Record<string,unknown>={};let final:Project|undefined,primary:Failure|undefined;
  try{
    await page.addInitScript(fault=>{const original=Storage.prototype.setItem;Object.assign(window,{__FINDING_BACKUP_FAULT__:true});
      Storage.prototype.setItem=function(key:string,value:string){if((window as unknown as {__FINDING_BACKUP_FAULT__:boolean}).__FINDING_BACKUP_FAULT__
        &&(fault==="data"&&this===localStorage&&key.startsWith("motionwright.native-finding-recovery.v1:")||fault==="pointer"&&this===sessionStorage&&key.startsWith("motionwright.native-finding-writer.v1:")))return;return original.call(this,key,value);};},kind);
    await attach(page,service,boot);await saveSceneRecipe(page,boot);const initial=await service.project(boot.id),scene=target(initial);await inspectSyntheticNative(page,scene.id);await author(page,scene.nodes[0]?.id??null);
    const writes=applies(service).length,permissions=grants(service).length;
    await expect(button(page,"Record source-anchored review")).toBeDisabled();await rawPreserved(page);expect(await service.project(boot.id)).toEqual(initial);
    expect(applies(service)).toHaveLength(writes);expect(grants(service)).toHaveLength(permissions);
    await page.evaluate(()=>{(window as unknown as {__FINDING_BACKUP_FAULT__:boolean}).__FINDING_BACKUP_FAULT__=false;});await button(page,"Retry finding recovery").click();
    expect(applies(service)).toHaveLength(writes);expect(grants(service)).toHaveLength(permissions);
    await button(page,"Record source-anchored review").click();await revision(page,initial.revision+1);
    final=await service.project(boot.id);newReview(final,initial);authorPayloadUnchanged(final,initial);expect(applies(service)).toHaveLength(writes+1);expect(grants(service)).toHaveLength(permissions+1);
    observations.retainedCopies=await copies(page);
  }catch(error){primary={reason:error};try{final=await service.project(boot.id);}catch{/* Finalizer records. */}}
  observations.syntheticNativeCalls=native.calls;observations.syntheticNativeOrigins=native.origins();await finish(page,info,service,final,observations,[native.restore],primary);
});
