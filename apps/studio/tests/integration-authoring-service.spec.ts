import {test,expect,type Page,type TestInfo} from "@playwright/test";
import {randomUUID} from "node:crypto";
import {readFile,writeFile} from "node:fs/promises";
import {ScopedChangeService,type StoreFinalization} from "./support/scopedChangeService";
import type {Change,Project,ExtensionProfile} from "../src/types";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"The exact source-built Rust/SQLite service is required.");
test.setTimeout(120_000);
const button=(page:Page,name:string)=>page.getByRole("button",{name,exact:true});
const mutations=(service:ScopedChangeService)=>service.transcript.filter(entry=>
  entry.direction==="request"&&"command" in entry.payload&&entry.payload.command==="apply_change");
const grants=(service:ScopedChangeService)=>service.transcript.filter(entry=>
  entry.direction==="request"&&"command" in entry.payload&&entry.payload.command==="issue_effect_grant");
const unknown=(page:Page)=>page.getByRole("region",{name:"Unconfirmed integration submission",exact:true});
const recovery=(page:Page)=>page.getByRole("region",{name:"Integration input recovery",exact:true});
async function open(page:Page,descriptor=false){
  await button(page,"Integrations").click();
  await expect(page.getByRole("heading",{name:"Integrations",exact:true})).toBeVisible();
  if(descriptor&&await page.getByRole("region",{name:"Extension descriptor editor",exact:true}).count()===0)
    await button(page,"Register descriptor").click();
}
async function fillDescriptor(page:Page,name="Original integration descriptor",rights:"unknown"|"cleared"="unknown"){
  await page.getByLabel("Extension display name",{exact:true}).fill(name);
  await page.getByLabel("Extension version",{exact:true}).fill("1.0.0");
  await page.getByLabel("Extension license",{exact:true}).fill("MIT");
  await page.getByLabel("Extension source",{exact:true}).fill("https://example.invalid/original-synthetic-package");
  await page.getByLabel("Extension digest",{exact:true}).fill("ab".repeat(32));
  await page.getByLabel("Extension rights state",{exact:true}).selectOption(rights);
}
async function fillBinding(page:Page,id="original-public-release"){
  await page.getByLabel("External resource ID",{exact:true}).fill(id);
  await page.getByLabel("External resource revision",{exact:true}).fill("r17");
}
/** Only delivery of an actual already-applied response is delayed/lost.
 * Grants, CAS, domain validation, reads, journal and storage are the real service. */
async function responseGate(page:Page,type:Change["type"],mode:"hold"|"lose"){
  await page.evaluate(({type,mode})=>{
    type Runtime={__TAURI_INTERNALS__:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __INTEGRATION_GATE__?:{reached:boolean;release:()=>void}};
    const runtime=window as unknown as Runtime,original=runtime.__TAURI_INTERNALS__.invoke;
    let remaining=true;runtime.__INTEGRATION_GATE__={reached:false,release:()=>{}};
    runtime.__TAURI_INTERNALS__.invoke=async(command,args)=>{
      const result=await original(command,args),request=args?.request as {change?:Change}|undefined;
      if(remaining&&command==="apply_change"&&request?.change?.type===type){
        remaining=false;runtime.__INTEGRATION_GATE__!.reached=true;
        if(mode==="lose")throw new Error("Injected response loss after real integration service commit");
        await new Promise<void>(resolve=>{runtime.__INTEGRATION_GATE__!.release=resolve;});
      }
      return result;
    };
  },{type,mode});
}
async function reached(page:Page){
  await expect.poll(()=>page.evaluate(()=>(window as unknown as {
    __INTEGRATION_GATE__?:{reached:boolean}}).__INTEGRATION_GATE__?.reached)).toBe(true);
}
async function release(page:Page){
  await page.evaluate(()=>(window as unknown as {__INTEGRATION_GATE__?:{release:()=>void}}).__INTEGRATION_GATE__?.release());
}
async function originals(page:Page){return page.evaluate(()=>Object.fromEntries(Object.keys(localStorage)
  .filter(key=>key.startsWith("motionwright.integration-recovery.v1:")).map(key=>[key,localStorage.getItem(key)])));}
async function unchangedCopies(page:Page,observed:Record<string,string|null>){
  expect(await page.evaluate(observed=>Object.entries(observed).every(([key,raw])=>localStorage.getItem(key)===raw),observed)).toBe(true);
}
async function inspectAndContinue(page:Page,service:ScopedChangeService,projectId:string){
  const before=await service.project(projectId),journal=await service.history(projectId,0),count=mutations(service).length,grantCount=grants(service).length;
  await button(page,"Read saved integration outcome").click();
  await expect(button(page,"Keep integration input and continue separately")).toBeVisible();
  expect(await service.project(projectId)).toEqual(before);expect(await service.history(projectId,0)).toEqual(journal);
  await button(page,"Keep integration input and continue separately").click();await expect(unknown(page)).toHaveCount(0);
  expect(mutations(service)).toHaveLength(count);expect(grants(service)).toHaveLength(grantCount);
}
async function restart(page:Page,service:ScopedChangeService,expected:Project,descriptor=false){
  const journal=await service.history(expected.id,0);
  await service.close();await service.start();await page.reload();await open(page,descriptor);
  expect(await service.project(expected.id)).toEqual(expected);expect(await service.history(expected.id,0)).toEqual(journal);
}
async function preserve(service:ScopedChangeService,page:Page,info:TestInfo,projectId:string|undefined,primaryFailure:unknown,peers:ScopedChangeService[]=[]){
  const observations:string[]=[];let latest:Project|undefined,result:StoreFinalization|undefined;
  try{if(projectId)latest=await service.project(projectId);}catch(error){observations.push(String(error));}
  try{
    result=await service.finalizeClosedStore(info.outputPath("closed-store"),latest,peers,
      [async()=>{if(!page.isClosed()){await release(page);await page.close();}}]);
  }catch(error){
    observations.push(String(error));
    try{result=JSON.parse(await readFile(info.outputPath("closed-store-outcomes.json"),"utf8")) as StoreFinalization;}
    catch(readError){observations.push("Retained finalization outcome unavailable: "+String(readError));}
  }
  const failures=[...observations,...(result?.errors??[])];
  await writeFile(info.outputPath("integration-finalization.json"),JSON.stringify({
    primaryFailure:primaryFailure===undefined?null:String(primaryFailure),observations,outcome:result??null},null,2));
  await writeFile(info.outputPath("integration-transcript.json"),JSON.stringify({
    transcript:service.transcript,exits:service.processExits,taints:service.lifecycleTaints,
  },null,2));
  const evidencePath=info.outputPath("integration-authoring-service-evidence.json");
  await writeFile(evidencePath,JSON.stringify({
    schema:"motionwright.integration-authoring-custody/1",
    primaryFailure:primaryFailure===undefined?null:String(primaryFailure),failures,
    final:result?.oracle?.project??latest,closedStoreOracle:result?.oracle??null,
    service:{binaryPath:service.binaryPath,lifecycleTaints:service.lifecycleTaints,processExits:service.processExits},
    acceptanceScope:"The Playwright verdict controls whether this exact closed store supports the UI/service result.",
    nativeRender:false,installedTauri:false,humanCreativeApproval:false,
  },null,2));
  if(result)await info.attach("closed-store-outcomes",{path:info.outputPath("closed-store-outcomes.json"),contentType:"application/json"});
  await info.attach("integration-authoring-service-evidence",{path:evidencePath,contentType:"application/json"});
  if(failures.length)throw new AggregateError(failures.map(message=>new Error(message)),"Integration evidence finalization failed");
}
async function finish(service:ScopedChangeService,page:Page,info:TestInfo,projectId:string|undefined,primaryFailure:unknown,peers:ScopedChangeService[]=[]){
  try{await preserve(service,page,info,projectId,primaryFailure,peers);}
  catch(error){
    // Keep the first causal test failure, while recording custody failures separately.
    console.error("INTEGRATION_FINALIZATION_FAILURE",String(error));
    try{await info.attach("integration-finalization-failure",{body:Buffer.from(JSON.stringify({
      primaryFailure:primaryFailure===undefined?null:String(primaryFailure),finalizationFailure:String(error),
    })),contentType:"application/json"});}catch(attachmentError){console.error("INTEGRATION_ATTACHMENT_FAILURE",String(attachmentError));}
    if(primaryFailure===undefined)throw error;
  }
}

test("extension input survives navigation and reload, verified recovery precedes its real save",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("store","integration.sqlite3"),desktopInvokeFixture:true});
  let projectId:string|undefined,primaryFailure:unknown;
  try{
    const source=(await service.fixtureBootstrap()).project;projectId=source.id;
    await service.attachFixturePage(page,source.id);await page.goto("/");await open(page,true);
    const arranged=mutations(service).length,grantCount=grants(service).length;
    await fillDescriptor(page);await page.getByLabel("Extension digest",{exact:true}).fill("1e");
    await button(page,"Brief").click();await open(page,true);
    await expect(page.getByLabel("Extension digest",{exact:true}),"INTEGRATION_EXTENSION_RAW_PRESERVATION").toHaveValue("1e");
    await page.reload();await open(page,true);
    await expect(page.getByLabel("Extension digest",{exact:true})).toHaveValue("1e");
    const unnormalized=" AB"+"CD".repeat(32)+" ";
    await page.getByLabel("Extension digest",{exact:true}).fill(unnormalized);
    await expect(page.getByLabel("Extension digest",{exact:true})).toHaveValue(unnormalized);
    await expect(button(page,"Register versioned descriptor")).toBeDisabled();
    expect(mutations(service)).toHaveLength(arranged);expect(grants(service)).toHaveLength(grantCount);
    await page.evaluate(()=>{
      const original=Storage.prototype.setItem;
      (window as unknown as {__INTEGRATION_RESTORE_STORAGE__?:()=>void}).__INTEGRATION_RESTORE_STORAGE__=()=>{Storage.prototype.setItem=original;};
      Storage.prototype.setItem=function(key,value){if(this===localStorage&&key.startsWith("motionwright.integration-recovery.v1:"))return;return original.call(this,key,value);};
    });
    await page.getByLabel("Extension version",{exact:true}).fill("1.2.3");
    await page.getByLabel("Extension digest",{exact:true}).fill("ab".repeat(32));
    await expect(recovery(page).getByRole("alert").first()).toBeVisible();
    await expect(button(page,"Register versioned descriptor")).toBeDisabled();
    expect(mutations(service)).toHaveLength(arranged);expect(grants(service)).toHaveLength(grantCount);
    await page.evaluate(()=>(window as unknown as {__INTEGRATION_RESTORE_STORAGE__?:()=>void}).__INTEGRATION_RESTORE_STORAGE__?.());
    await button(page,"Retry integration recovery").click();
    await expect(button(page,"Register versioned descriptor")).toBeEnabled();
    await responseGate(page,"upsert_extension","hold");await button(page,"Register versioned descriptor").click();await reached(page);
    await page.getByLabel("Extension display name",{exact:true}).fill("Later unsaved descriptor name");
    await page.getByLabel("External resource ID",{exact:true}).fill("Later independently authored binding");
    await release(page);await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(source.revision+1));
    const latest=await service.project(source.id),extension=latest.extensions.find(value=>value.name==="Original integration descriptor")!;
    expect(extension).toBeDefined();expect(extension.package_version).toBe("1.2.3");expect(extension.enabled).toBe(false);
    expect(extension.rights_status).toBe("unknown");expect(extension.permissions).toEqual(["read_project"]);
    expect(latest.scenes).toEqual(source.scenes);expect(latest.handoffs).toEqual(source.handoffs);
    await expect(page.getByLabel("Extension display name",{exact:true})).toHaveValue("Later unsaved descriptor name");
    await expect(page.getByLabel("External resource ID",{exact:true})).toHaveValue("Later independently authored binding");
    expect(mutations(service)).toHaveLength(arranged+1);
    await restart(page,service,latest,true);
    await expect(page.getByLabel("Extension display name",{exact:true})).toHaveValue("Later unsaved descriptor name");
    await expect(page.getByLabel("External resource ID",{exact:true})).toHaveValue("Later independently authored binding");
    expect(mutations(service)).toHaveLength(arranged+1);
    await page.screenshot({path:info.outputPath("extension-reopened.png"),fullPage:true});
  }catch(error){primaryFailure=error;throw error;}finally{await finish(service,page,info,projectId,primaryFailure);}
});
test("handoff raw input and identity survive navigation, reload and later edits",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("store","integration.sqlite3"),desktopInvokeFixture:true});
  let projectId:string|undefined,primaryFailure:unknown;
  try{
    const source=(await service.fixtureBootstrap()).project;projectId=source.id;
    await service.attachFixturePage(page,source.id);await page.goto("/");await open(page);
    const arranged=mutations(service).length;
    await fillBinding(page);await page.getByLabel("Handoff direction",{exact:true}).selectOption("artifact_output");
    await page.getByLabel("External resource kind",{exact:true}).selectOption("artifact");
    await page.getByLabel("Handoff artifact digest",{exact:true}).fill("1e");
    await button(page,"Brief").click();await open(page);
    await expect(page.getByLabel("External resource ID",{exact:true}),"INTEGRATION_HANDOFF_RAW_PRESERVATION").toHaveValue("original-public-release");
    await expect(page.getByLabel("Handoff artifact digest",{exact:true})).toHaveValue("1e");
    await page.reload();await open(page);
    await expect(page.getByLabel("Handoff artifact digest",{exact:true})).toHaveValue("1e");
    await page.getByLabel("External resource revision",{exact:true}).fill("   ");
    await expect(page.getByLabel("External resource revision",{exact:true})).toHaveValue("   ");
    await expect(button(page,"Record binding")).toBeDisabled();
    const raw=" CD"+"AB".repeat(32)+" ";
    await page.getByLabel("Handoff artifact digest",{exact:true}).fill(raw);
    await page.getByLabel("Handoff direction",{exact:true}).selectOption("context_output");
    await expect(page.getByLabel("Handoff artifact digest",{exact:true})).toHaveCount(0);
    await page.getByLabel("Handoff direction",{exact:true}).selectOption("artifact_output");
    await expect(page.getByLabel("Handoff artifact digest",{exact:true})).toHaveValue(raw);
    await expect(page.getByLabel("External resource kind",{exact:true})).toHaveValue("artifact");
    await page.getByLabel("Handoff artifact digest",{exact:true}).fill("cd".repeat(32));
    await page.getByLabel("External resource revision",{exact:true}).fill(" exact-r17 ");
    await responseGate(page,"upsert_handoff","hold");await button(page,"Record binding").click();await reached(page);
    await page.getByLabel("External resource revision",{exact:true}).fill(" later unsubmitted revision ");
    await button(page,"Register descriptor").click();
    await page.getByLabel("Extension display name",{exact:true}).fill("Independent descriptor draft");
    await release(page);await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(source.revision+1));
    const latest=await service.project(source.id),binding=latest.handoffs.find(value=>value.external_id==="original-public-release")!;
    expect(binding).toBeDefined();expect(binding.external_revision).toBe(" exact-r17 ");expect(binding.artifact_sha256).toBe("cd".repeat(32));
    expect(binding.local_resource).toBe("project:"+source.id);expect(binding.external_kind).toBe("artifact");
    expect(latest.extensions).toEqual(source.extensions);expect(latest.scenes).toEqual(source.scenes);
    await restart(page,service,latest,true);
    await expect(page.getByLabel("External resource revision",{exact:true})).toHaveValue(" later unsubmitted revision ");
    await expect(page.getByLabel("Extension display name",{exact:true})).toHaveValue("Independent descriptor draft");
    expect(mutations(service)).toHaveLength(arranged+1);
    await page.screenshot({path:info.outputPath("binding-reopened.png"),fullPage:true});
  }catch(error){primaryFailure=error;throw error;}finally{await finish(service,page,info,projectId,primaryFailure);}
});
test("lost descriptor registration response retains UNKNOWN and requires separate reviewed intent",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("store","integration.sqlite3"),desktopInvokeFixture:true});
  let projectId:string|undefined,primaryFailure:unknown;
  try{
    const source=(await service.fixtureBootstrap()).project;projectId=source.id;
    await service.attachFixturePage(page,source.id);await page.goto("/");await open(page,true);
    const arranged=mutations(service).length;await fillDescriptor(page,"Descriptor with lost response","cleared");
    await responseGate(page,"upsert_extension","lose");await button(page,"Register versioned descriptor").click();await reached(page);
    await expect(unknown(page)).toBeVisible();
    let latest=await service.project(source.id);const extension=latest.extensions.find(value=>value.name==="Descriptor with lost response")!;
    expect(latest.revision).toBe(source.revision+1);expect(extension.enabled).toBe(false);
    await page.getByLabel("Extension source",{exact:true}).fill("https://example.invalid/separate-later-source");
    await page.reload();await open(page,true);await expect(unknown(page)).toBeVisible();
    await expect(button(page,"Register versioned descriptor")).toBeDisabled();await expect(button(page,"Enable")).toBeDisabled();
    await expect(page.getByRole("button",{name:"Remove extension Descriptor with lost response",exact:true})).toBeDisabled();
    const copies=await originals(page);expect(mutations(service)).toHaveLength(arranged+1);
    await inspectAndContinue(page,service,source.id);await unchangedCopies(page,copies);
    await expect(page.getByLabel("Extension source",{exact:true})).toHaveValue("https://example.invalid/separate-later-source");
    await button(page,"Register versioned descriptor").click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(source.revision+2));
    latest=await service.project(source.id);const edited=latest.extensions.find(value=>value.id===extension.id)!;
    expect(edited.source).toBe("https://example.invalid/separate-later-source");expect(edited.enabled).toBe(false);
    expect(latest.extensions).toHaveLength(source.extensions.length+1);expect(latest.scenes).toEqual(source.scenes);
    await restart(page,service,latest,true);await unchangedCopies(page,copies);
    expect(mutations(service)).toHaveLength(arranged+2);
    await page.screenshot({path:info.outputPath("descriptor-unknown-reviewed.png"),fullPage:true});
  }catch(error){primaryFailure=error;throw error;}finally{await finish(service,page,info,projectId,primaryFailure);}
});
test("lost binding response is inspected without replay before a separate canonical edit",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("store","integration.sqlite3"),desktopInvokeFixture:true});
  let projectId:string|undefined,primaryFailure:unknown;
  try{
    const source=(await service.fixtureBootstrap()).project;projectId=source.id;
    await service.attachFixturePage(page,source.id);await page.goto("/");await open(page);
    const arranged=mutations(service).length;await fillBinding(page,"binding-with-lost-response");
    await responseGate(page,"upsert_handoff","lose");await button(page,"Record binding").click();await reached(page);
    await expect(unknown(page)).toBeVisible();
    let latest=await service.project(source.id);const binding=latest.handoffs.find(value=>value.external_id==="binding-with-lost-response")!;
    expect(latest.revision).toBe(source.revision+1);
    await page.getByLabel("External resource ID",{exact:true}).fill("separate-later-public-reference");
    await page.getByLabel("External resource revision",{exact:true}).fill(" exact-later-revision ");
    await page.reload();await open(page);await expect(unknown(page)).toBeVisible();
    await expect(button(page,"Record binding")).toBeDisabled();
    await expect(page.getByRole("button",{name:"Remove handoff binding-with-lost-response",exact:true})).toBeDisabled();
    const copies=await originals(page);await inspectAndContinue(page,service,source.id);
    await unchangedCopies(page,copies);expect(mutations(service)).toHaveLength(arranged+1);
    await button(page,"Record binding").click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(source.revision+2));
    latest=await service.project(source.id);const edited=latest.handoffs.find(value=>value.id===binding.id)!;
    expect(edited.external_id).toBe("separate-later-public-reference");expect(edited.external_revision).toBe(" exact-later-revision ");
    expect(edited.artifact_sha256).toBeNull();expect(edited.local_resource).toBe("project:"+source.id);
    expect(latest.handoffs).toHaveLength(source.handoffs.length+1);expect(latest.scenes).toEqual(source.scenes);
    await restart(page,service,latest);await unchangedCopies(page,copies);
    await expect(page.getByLabel("External resource ID",{exact:true})).toHaveValue("separate-later-public-reference");
    expect(mutations(service)).toHaveLength(arranged+2);
    await page.screenshot({path:info.outputPath("binding-unknown-reviewed.png"),fullPage:true});
  }catch(error){primaryFailure=error;throw error;}finally{await finish(service,page,info,projectId,primaryFailure);}
});
test("lost toggle and removal responses retain their own UNKNOWN outcomes",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("store","integration.sqlite3"),desktopInvokeFixture:true});
  let projectId:string|undefined,primaryFailure:unknown;
  try{
    const source=(await service.fixtureBootstrap()).project;projectId=source.id;
    await service.attachFixturePage(page,source.id);await page.goto("/");await open(page,true);
    const arranged=mutations(service).length;await fillDescriptor(page,"Explicit optional descriptor","cleared");
    await button(page,"Register versioned descriptor").click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(source.revision+1));
    const initial=await service.project(source.id),extension=initial.extensions.find(value=>value.name==="Explicit optional descriptor")!;
    await responseGate(page,"upsert_extension","lose");await button(page,"Enable").click();await reached(page);
    await expect(unknown(page)).toBeVisible();
    let latest=await service.project(source.id);expect(latest.extensions.find(value=>value.id===extension.id)?.enabled).toBe(true);
    await page.reload();await open(page,true);await expect(unknown(page)).toBeVisible();
    await expect(button(page,"Disable")).toBeDisabled();
    const copies=await originals(page);await inspectAndContinue(page,service,source.id);await unchangedCopies(page,copies);
    expect(mutations(service)).toHaveLength(arranged+2);
    await responseGate(page,"remove_extension","lose");
    await page.getByRole("button",{name:"Remove extension Explicit optional descriptor",exact:true}).click();await reached(page);
    await expect(unknown(page)).toBeVisible();latest=await service.project(source.id);
    expect(latest.extensions.some(value=>value.id===extension.id)).toBe(false);expect(latest.revision).toBe(source.revision+3);
    await page.reload();await open(page,true);await expect(unknown(page)).toBeVisible();
    expect(mutations(service)).toHaveLength(arranged+3);
    const removedCopies=await originals(page);await inspectAndContinue(page,service,source.id);
    await unchangedCopies(page,copies);await unchangedCopies(page,removedCopies);
    await restart(page,service,latest,true);
    expect(mutations(service)).toHaveLength(arranged+3);expect(latest.scenes).toEqual(source.scenes);
    await page.screenshot({path:info.outputPath("toggle-remove-unknown-reviewed.png"),fullPage:true});
  }catch(error){primaryFailure=error;throw error;}finally{await finish(service,page,info,projectId,primaryFailure);}
});
test("foreign revision and unreadable recovery keep authored input separate without grants",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("store","integration.sqlite3"),desktopInvokeFixture:true});
  let projectId:string|undefined,primaryFailure:unknown,peer:ScopedChangeService|undefined;
  try{
    let source=(await service.fixtureBootstrap()).project;projectId=source.id;
    const extension:ExtensionProfile={id:randomUUID(),name:"Existing reviewed descriptor",kind:"catalog-package",package_version:"1.0.0",
      digest_sha256:"ab".repeat(32),license:"MIT",source:"https://example.invalid/original-reviewed-source",
      rights_status:"unknown",enabled:false,permissions:["read_project"]};
    source=await service.apply(source,{type:"upsert_extension",extension});
    await service.attachFixturePage(page,source.id);await page.goto("/");await open(page);
    await button(page,"Edit descriptor Existing reviewed descriptor").click();
    await page.getByLabel("Extension display name",{exact:true}).fill("My unsaved descriptor name");
    await page.getByLabel("Extension digest",{exact:true}).fill("1e");
    const arranged=mutations(service).length,grantCount=grants(service).length;
    peer=await service.peer();const peerSource=await peer.project(source.id);
    const changed={...extension,source:"https://example.invalid/peer-reviewed-source",permissions:["read_project","network"] as ExtensionProfile["permissions"]};
    const latest=await peer.apply(peerSource,{type:"upsert_extension",extension:changed});
    await page.reload();await open(page,true);
    await expect(page.getByRole("region",{name:"Integration source conflict",exact:true})).toBeVisible();
    await expect(page.getByLabel("Extension display name",{exact:true})).toHaveValue("My unsaved descriptor name");
    await expect(page.getByLabel("Extension digest",{exact:true})).toHaveValue("1e");
    await expect(button(page,"Register versioned descriptor")).toBeDisabled();
    expect(await service.project(source.id)).toEqual(latest);
    const beforeRecovery=await service.observeStoredProject(latest);
    const peerMutationCount=mutations(peer).length,peerGrantCount=grants(peer).length;
    const retained=await page.evaluate(()=>{
      const pointer=Object.keys(sessionStorage).find(key=>key.startsWith("motionwright.integration-writer.v1:"));
      if(!pointer)throw new Error("Missing exact integration pointer");
      const writer=sessionStorage.getItem(pointer)!;
      const key=Object.keys(localStorage).find(key=>key.startsWith("motionwright.integration-recovery.v1:")&&key.endsWith(":"+writer));
      if(!key)throw new Error("Missing exact integration data");
      const raw=localStorage.getItem(key)!,corrupt="{damaged original recovery";localStorage.setItem(key,corrupt);
      return {key,raw,pointer,writer,corrupt};
    });
    await page.reload();await open(page,true);await expect(recovery(page).getByRole("alert").first()).toBeVisible();
    await page.getByLabel("Extension display name",{exact:true}).fill("Later name while recovery was unreadable");
    await expect(button(page,"Register versioned descriptor")).toBeDisabled();
    expect(mutations(service)).toHaveLength(arranged);expect(grants(service)).toHaveLength(grantCount);
    await page.evaluate(({key,raw})=>localStorage.setItem(key,raw),retained);
    await button(page,"Retry integration recovery").click();
    // Replacing corruption is not a read retry: the first observed bytes must
    // remain the source. The original implementation silently adopted raw here.
    await expect(recovery(page).getByRole("alert").filter({hasText:"original Integration recovery bytes changed"}),
      "INTEGRATION_CORRUPT_SAME_WRITER_REPLACEMENT_REFUSED").toBeVisible();
    await expect(page.getByLabel("Extension display name",{exact:true})).toHaveValue("Later name while recovery was unreadable");
    await expect(page.getByLabel("Extension digest",{exact:true})).toHaveValue("");
    await expect(page.getByLabel("Extension source",{exact:true})).toHaveValue("");
    await expect(button(page,"Register versioned descriptor")).toBeDisabled();
    await recovery(page).getByText(/^Local integration copies \(/).click();
    const downloaded=page.waitForEvent("download");await button(page,"Download copy "+retained.writer.slice(0,8)).click();
    const downloadedPath=await (await downloaded).path();expect(downloadedPath).not.toBeNull();
    const originalDownload=JSON.parse(await readFile(downloadedPath!,"utf8"));
    expect(originalDownload).toEqual({schema:"motionwright.local-input-copy/1",storage_key:retained.key,raw:retained.corrupt});
    await expect(button(page,"Discard copy "+retained.writer.slice(0,8))).toBeDisabled();
    expect(await page.evaluate(({key})=>localStorage.getItem(key),retained)).toBe(retained.raw);
    expect(mutations(service)).toHaveLength(arranged);expect(grants(service)).toHaveLength(grantCount);
    expect(mutations(peer)).toHaveLength(peerMutationCount);expect(grants(peer)).toHaveLength(peerGrantCount);
    const afterRecovery=await service.observeStoredProject(latest);
    expect(afterRecovery.project).toEqual(beforeRecovery.project);expect(afterRecovery.journal).toEqual(beforeRecovery.journal);
    const evidence=info.outputPath("integration-peer-and-recovery.json");
    await writeFile(evidence,JSON.stringify({source,peerProject:latest,peerTranscript:peer.transcript,
      originalCopy:retained,originalDownload,beforeRecovery,afterRecovery,
      uiProjectMutations:0,uiEffectGrants:0,excludes:["remote product import","native renderer admission","human approval"]},null,2));
    await info.attach("integration-peer-and-recovery",{path:evidence,contentType:"application/json"});
    await page.screenshot({path:info.outputPath("integration-peer-preserved.png"),fullPage:true});
  }catch(error){primaryFailure=error;throw error;}finally{await finish(service,page,info,projectId,primaryFailure,peer?[peer]:[]);}
});


test("a real UNKNOWN survives transient first read and refuses later same-writer replacement without replay",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("store","integration.sqlite3"),desktopInvokeFixture:true});
  let projectId:string|undefined,primaryFailure:unknown;
  try{
    const source=(await service.fixtureBootstrap()).project;projectId=source.id;
    await service.attachFixturePage(page,source.id);await page.goto("/");await open(page,true);
    const arranged=mutations(service).length;
    await fillDescriptor(page,"Descriptor actually committed before recovery read failure");
    await responseGate(page,"upsert_extension","lose");await button(page,"Register versioned descriptor").click();await reached(page);
    await expect(unknown(page)).toBeVisible();
    const committed=await service.project(source.id),beforeRecovery=await service.observeStoredProject(committed);
    expect(committed.revision).toBe(source.revision+1);
    expect(committed.extensions.some(value=>value.name==="Descriptor actually committed before recovery read failure")).toBe(true);
    const mutationCount=mutations(service).length,grantCount=grants(service).length;
    expect(mutationCount).toBe(arranged+1);
    await page.getByLabel("External resource ID",{exact:true}).fill("Independent binding authored before reload");
    const ownCopy=async()=>page.evaluate(()=>{
      const pointer=Object.keys(sessionStorage).find(key=>key.startsWith("motionwright.integration-writer.v1:"));
      if(!pointer)throw new Error("Missing exact Integration pointer");
      const writer=sessionStorage.getItem(pointer)!;
      const key=Object.keys(localStorage).find(key=>key.startsWith("motionwright.integration-recovery.v1:")&&key.endsWith(":"+writer));
      if(!key)throw new Error("Missing exact Integration recovery copy");
      const raw=localStorage.getItem(key)!;return {pointer,writer,key,raw,copy:JSON.parse(raw)};
    });
    const original=await ownCopy();
    expect(original.copy.draft.pending?.outcome).toBe("UNKNOWN");
    const pending=structuredClone(original.copy.draft.pending);
    expect(pending.change.type).toBe("upsert_extension");
    expect(committed.extensions.find(value=>value.id===pending.change.extension.id)).toEqual(pending.change.extension);
    expect(beforeRecovery.journal.at(-1)?.change).toEqual(pending.change);
    // The original submission and pending bytes are produced by the actual
    // service send above. This fault affects one local read, never the service.
    await page.addInitScript(()=>{
      const marker="motionwright.integration-test-first-read",key=sessionStorage.getItem(marker);
      if(!key)return;sessionStorage.removeItem(marker);
      const get=Storage.prototype.getItem;let failed=false;
      Storage.prototype.getItem=function(name){
        if(this===localStorage&&name===key&&!failed){failed=true;throw new Error("Initial Integration copy read temporarily unavailable");}
        return get.call(this,name);
      };
    });
    await page.evaluate(key=>sessionStorage.setItem("motionwright.integration-test-first-read",key),original.key);
    await page.reload();await open(page,true);
    await expect(recovery(page).getByRole("alert").first()).toBeVisible();
    await page.getByLabel("Extension display name",{exact:true}).fill("Later words after failed read");
    await page.getByLabel("Extension display name",{exact:true}).fill("Reviewed optional package");
    await page.getByLabel("Extension version",{exact:true}).fill("later version");
    await page.getByLabel("Extension version",{exact:true}).fill("");
    await expect(button(page,"Register versioned descriptor")).toBeDisabled();
    // Positive retry uses the same exact original bytes. A later blank and a
    // return to the new editor's base are both authored input, not defaults.
    await button(page,"Retry integration recovery").click();await expect(unknown(page)).toBeVisible();
    await expect(page.getByLabel("Extension display name",{exact:true})).toHaveValue("Reviewed optional package");
    await expect(page.getByLabel("Extension version",{exact:true})).toHaveValue("");
    await expect(page.getByLabel("External resource ID",{exact:true})).toHaveValue("Independent binding authored before reload");
    await expect(button(page,"Register versioned descriptor")).toBeDisabled();
    const recovered=await ownCopy();expect(recovered.copy.draft.pending).toEqual(pending);
    expect(await page.evaluate(key=>localStorage.getItem(key),original.key)).toBe(original.raw);
    await button(page,"Brief").click();await open(page,true);await expect(unknown(page)).toBeVisible();
    await expect(page.getByLabel("Extension version",{exact:true})).toHaveValue("");
    await button(page,"Read saved integration outcome").click();
    await expect(button(page,"Keep integration input and continue separately")).toBeVisible();
    expect((await ownCopy()).copy.draft.pending).toEqual(pending);
    expect(mutations(service)).toHaveLength(mutationCount);expect(grants(service)).toHaveLength(grantCount);
    // A second first-read failure observes the real UNKNOWN in its subsequent
    // listing. Replacing that same writer by a valid copy without pending must
    // not turn the failed read into permission to lose the original attempt.
    const beforeReplacement=await ownCopy();
    await page.evaluate(key=>sessionStorage.setItem("motionwright.integration-test-first-read",key),beforeReplacement.key);
    await page.reload();await open(page,true);
    await expect(recovery(page).getByRole("alert").first()).toBeVisible();
    await page.getByLabel("Extension display name",{exact:true}).fill("Later input after second failed read");
    const replacement=await page.evaluate(({key,raw})=>{
      const value=JSON.parse(raw);value.draft.pending=null;
      const next=JSON.stringify(value);localStorage.setItem(key,next);return next;
    },beforeReplacement);
    await button(page,"Retry integration recovery").click();
    await expect(recovery(page).getByRole("alert").filter({hasText:"original Integration recovery bytes changed"}),
      "INTEGRATION_FIRST_READ_SAME_WRITER_UNKNOWN_REPLACEMENT_REFUSED").toBeVisible();
    await expect(page.getByLabel("Extension display name",{exact:true})).toHaveValue("Later input after second failed read");
    await expect(button(page,"Register versioned descriptor")).toBeDisabled();
    await recovery(page).getByText(/^Local integration copies \(/).click();
    const downloaded=page.waitForEvent("download");await button(page,"Download copy "+beforeReplacement.writer.slice(0,8)).click();
    const downloadedPath=await (await downloaded).path();expect(downloadedPath).not.toBeNull();
    const originalDownload=JSON.parse(await readFile(downloadedPath!,"utf8"));
    expect(originalDownload).toEqual({schema:"motionwright.local-input-copy/1",storage_key:beforeReplacement.key,raw:beforeReplacement.raw});
    expect(JSON.parse(originalDownload.raw).draft.pending).toEqual(pending);
    await expect(button(page,"Discard copy "+beforeReplacement.writer.slice(0,8))).toBeDisabled();
    expect(await page.evaluate(key=>localStorage.getItem(key),beforeReplacement.key)).toBe(replacement);
    const afterRecovery=await service.observeStoredProject(committed);
    expect(afterRecovery.project).toEqual(beforeRecovery.project);expect(afterRecovery.journal).toEqual(beforeRecovery.journal);
    expect(mutations(service)).toHaveLength(mutationCount);expect(grants(service)).toHaveLength(grantCount);
    await service.close();await service.start();
    expect(await service.project(source.id)).toEqual(committed);expect(await service.history(source.id,0)).toEqual(beforeRecovery.journal);
    const evidence=info.outputPath("integration-first-read-source-custody.json");
    await writeFile(evidence,JSON.stringify({original,recovered,beforeReplacement,replacement,originalDownload,pending,
      beforeRecovery,afterRecovery,mutationCount,grantCount,additionalProjectMutations:0,additionalEffectGrants:0,
      sameStoreRestart:"Separate from the independent copied-store finalizer",nativeRender:false,installedTauri:false},null,2));
    await info.attach("integration-first-read-source-custody",{path:evidence,contentType:"application/json"});
    await page.screenshot({path:info.outputPath("integration-first-read-source-preserved.png"),fullPage:true});
  }catch(error){primaryFailure=error;throw error;}finally{await finish(service,page,info,projectId,primaryFailure);}
});
