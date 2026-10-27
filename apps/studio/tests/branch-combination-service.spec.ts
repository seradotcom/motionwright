import { writeFile } from "node:fs/promises";
import { expect,test,type Page,type TestInfo } from "@playwright/test";
import type { Change,Project } from "../src/types";
import { ScopedChangeService,type StoreFinalization } from "./support/scopedChangeService";

// Real App -> agreed readonly preview -> original ProjectEdit grant -> actual
// StudioService/SQLite. Run only against the admitted source-built executable.
// Fixture authorship and response delivery timing are controlled; domain,
// preview, grants, journal and persistence responses are never fabricated.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN || process.env.MOTIONWRIGHT_BRANCH_COMBINATION_ADMITTED!=="1",
  "Requires the exact source-built service with canonical branch-combination admission and UI route.");
test.setTimeout(120_000);
const button=(page:Page,name:string)=>page.getByRole("button",{name,exact:true});
const region=(page:Page)=>page.getByRole("region",{name:"Combine editable alternatives",exact:true});
interface ApplyArgs {request:{change:Change;request_id:string;effect_grant:string}}
function combinationRequests(service:ScopedChangeService){return service.transcript.filter(entry=>entry.direction==="request"
  && "command" in entry.payload && entry.payload.command==="apply_change"
  && (entry.payload.args as unknown as ApplyArgs)?.request?.change.type==="combine_creative_branches");}
function deferred<T>(){let resolve!:(value:T)=>void,reject!:(reason:unknown)=>void;
  const promise=new Promise<T>((done,fail)=>{resolve=done;reject=fail;});return {promise,resolve,reject};}
async function revision(page:Page,value:number){await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+value);}
async function review(page:Page){await button(page,"Review").click();await expect(region(page)).toBeVisible();}
const errorDetail=(reason:unknown)=>reason instanceof Error?{name:reason.name,message:reason.message,stack:reason.stack}:{message:String(reason)};
interface Scenario {
  service:ScopedChangeService;source:Project;donorId:string;sceneId:string;nodeId:string;nodeName:string;
  observations:Record<string,unknown>;cleanups:(()=>Promise<unknown>)[];
  observe(label:string):Promise<Project>;
  peer():Promise<ScopedChangeService>;
}
async function withCombination(page:Page,info:TestInfo,run:(context:Scenario)=>Promise<void>){
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","studio.sqlite3"),desktopInvokeFixture:true});
  const peers:ScopedChangeService[]=[],cleanups:(()=>Promise<unknown>)[]=[],failures:unknown[]=[],observations:Record<string,unknown>={};
  let final:Project|undefined,primary:unknown,custody:StoreFinalization|undefined;
  try{
    let source=(await service.fixtureBootstrap()).project;const activeId=source.active_branch;
    const scene=source.scenes[0],node=scene.nodes[0];
    source=await service.apply(source,{type:"add_review",kind:"creative",resource:"project:"+source.id,
      body:"Synthetic historical review of the original strategy; not human or native acceptance.",start:null,end:null,locale:null,profile_id:null});
    source=await service.apply(source,{type:"resolve_review",review_id:source.reviews.at(-1)!.id,
      resolution:"Synthetic history fixture; preserve its original branch/revision anchor."});
    source=await service.apply(source,{type:"create_branch",name:"Donor motion source"});const donorId=source.branches.at(-1)!.id;
    source=await service.apply(source,{type:"checkout_branch",branch_id:donorId});
    source=await service.apply(source,{type:"transform_canvas_node",scene_id:scene.id,node_id:node.id,
      transform:{x:node.x+100,y:node.y,width:node.width,height:node.height,rotation_deg:node.rotation_deg,opacity:node.opacity}});
    source=await service.apply(source,{type:"set_canvas_keyframe",scene_id:scene.id,node_id:node.id,
      keyframe:{at:{num:"1",den:"1"},property:"x",value:node.x+300,interpolation:"linear"}});
    source=await service.apply(source,{type:"checkout_branch",branch_id:activeId});final=source;observations.source=source;
    const checkpoints:{label:string;project:Project;journal:Awaited<ReturnType<typeof service.history>>}[]=[];observations.checkpoints=checkpoints;
    const observe=async(label:string)=>{final=await service.project(source.id);checkpoints.push({label,project:final,journal:await service.history(source.id,source.revision)});return final;};
    await service.attachFixturePage(page,source.id);await page.goto("/");
    // Document load precedes the saved-project editor. Enter through the real
    // visible navigation action, then keep the same exact revision assertion.
    await review(page);await revision(page,source.revision);
    await run({service,source,donorId,sceneId:scene.id,nodeId:node.id,nodeName:node.name,observations,cleanups,observe,
      peer:async()=>{
        const peer=await service.peer();peers.push(peer);
        await peer.project(source.id);
        const primaryPid=service.transcript.at(-1)?.processId,peerPid=peer.transcript.at(-1)?.processId;
        expect(primaryPid).toBeGreaterThan(0);expect(peerPid).toBeGreaterThan(0);expect(peerPid).not.toBe(primaryPid);
        expect(peer.databasePath).toBe(service.databasePath);
        observations.independentProcesses={primaryPid,peerPid,databasePath:service.databasePath};
        return peer;
      }});
  }catch(reason){primary=reason;failures.push(reason);}
  finally{
    for(const cleanup of cleanups){try{await cleanup();}catch(reason){failures.push(reason);}}
    if(final){try{final=await service.project(final.id);}catch(reason){failures.push(reason);}}
    try{custody=await service.finalizeClosedStore(info.outputPath("closed-store"),final,peers,[()=>page.close()]);}
    catch(reason){failures.push(reason);}
    try{
      const path=info.outputPath("branch-combination-evidence.json");
      await writeFile(path,JSON.stringify({scope:"actual App/canonical preview/ProjectEdit grants/StudioService/SQLite/journal",
        arrangement:"synthetic original strategies and historical review, actual independent service processes; selected response timing only",
        exclusions:["installed Tauri","native render","subjective strategy quality","human acceptance"],observations,final,
        oracle:custody?.oracle,primaryFailure:primary?errorDetail(primary):null,failures:failures.map(errorDetail),
        service:{databasePath:service.databasePath,binaryPath:service.binaryPath,transcript:service.transcript,
          processExits:service.processExits,lifecycleTaints:service.lifecycleTaints,stderr:service.stderr},
        peers:peers.map(peer=>({databasePath:peer.databasePath,binaryPath:peer.binaryPath,transcript:peer.transcript,processExits:peer.processExits,lifecycleTaints:peer.lifecycleTaints,stderr:peer.stderr}))},null,2));
      await info.attach("branch-combination-evidence",{path,contentType:"application/json"});
      await info.attach("closed-store-outcomes",{path:info.outputPath("closed-store-outcomes.json"),contentType:"application/json"});
    }catch(reason){failures.push(reason);}
    if(failures.length===1)throw failures[0];if(failures.length>1)throw new AggregateError(failures,"Combination scenario and closure failures remain separate",{cause:primary});
  }
}
async function author(page:Page,value:Scenario,name:string){
  await region(page).getByRole("button",{name:"Start a combination draft",exact:true}).click();
  await page.getByLabel("Combination donor branch",{exact:true}).selectOption(value.donorId);
  await page.getByLabel("Combination scene",{exact:true}).selectOption(value.sceneId);
  await page.getByLabel("Combination candidate name",{exact:true}).fill(name);
  await page.getByLabel("Combination reason",{exact:true}).fill("Keep the base composition and transfer the authored motion without replacing either strategy.");
  await page.getByLabel("Use donor motion for "+value.nodeName,{exact:true}).check();
}
async function preview(page:Page){
  const read=button(page,"Read canonical combination preview");await expect(read).toBeEnabled();await read.click();
  await expect(page.getByRole("region",{name:"Canonical combination preview",exact:true})).toBeVisible();
  await expect(button(page,"Create inactive combined candidate")).toBeEnabled();
}
async function recoveryBytes(page:Page,source:Project){return page.evaluate(scope=>{
  const pointer=sessionStorage.getItem("motionwright.branch-writer.v1:"+encodeURIComponent(scope));
  const key="motionwright.branch-recovery.v1:"+encodeURIComponent(scope)+":"+pointer,raw=localStorage.getItem(key);
  if(!raw)throw new Error("Original combination copy is missing");return {key,raw,value:JSON.parse(raw)};
},JSON.stringify([source.id,source.generation,source.active_branch]));}

test("two real strategies produce a persisted inactive candidate, then editable undo preserves peer style and historical reviews",async({page},info)=>{
  await withCombination(page,info,async value=>{
    const {service,source,sceneId,nodeId}=value;await author(page,value,"Combined editable strategy");
    await button(page,"Brief").click();await review(page);
    await expect(page.getByLabel("Combination candidate name")).toHaveValue("Combined editable strategy");
    await page.reload();await revision(page,source.revision);await review(page);
    await expect(page.getByLabel("Combination reason")).toHaveValue(/Keep the base composition/);
    await expect(button(page,"Create inactive combined candidate")).toBeDisabled();
    expect(combinationRequests(service)).toHaveLength(0);expect(await value.observe("raw draft and reload remain uncommitted")).toEqual(source);
    await preview(page);expect(await service.project(source.id)).toEqual(source);
    await button(page,"Create inactive combined candidate").click();await revision(page,source.revision+1);
    const saved=await value.observe("one canonical derivation committed"),created=saved.branches.find(b=>!source.branches.some(prior=>prior.id===b.id))!;
    expect(saved.active_branch).toBe(source.active_branch);expect(saved.scenes).toEqual(source.scenes);expect(saved.audio).toEqual(source.audio);
    expect(saved.reviews).toEqual(source.reviews);expect(saved.reviews.some(review=>review.anchor.branch_id===created.id)).toBe(false);
    expect(combinationRequests(service)).toHaveLength(1);
    const candidate=saved.branch_workspaces.find(w=>w.branch_id===created.id)!;
    expect(candidate.current_state.scenes.find(s=>s.id===sceneId)!.nodes.find(n=>n.id===nodeId)!.keyframes).toHaveLength(1);
    await service.close();await service.start();await page.reload();await revision(page,saved.revision);await review(page);
    expect(await value.observe("new service process reopens identical candidate and complete history")).toEqual(saved);
    await button(page,"Open saved combined candidate").click();await revision(page,saved.revision+1);
    const opened=await value.observe("explicit candidate checkout"),peer=await value.peer(),currentNode=opened.scenes.find(s=>s.id===sceneId)!.nodes.find(n=>n.id===nodeId)!;
    expect(opened.active_branch).toBe(created.id);
    const peerStyled=await peer.apply(await peer.project(source.id),{type:"update_canvas_style",scene_id:sceneId,node_id:nodeId,style:{...currentNode.style,fill:"#336699"}});
    await page.reload();await revision(page,peerStyled.revision);await button(page,"Production").click();
    await page.getByLabel("Production working scene").selectOption(sceneId);await button(page,"Scoped changes").click();
    await button(page,"Preview undo").click();await button(page,"Apply undo as new revision").click();await revision(page,peerStyled.revision+1);
    const undone=await value.observe("existing undo is a new revision retaining independent peer style"),node=undone.scenes.find(s=>s.id===sceneId)!.nodes.find(n=>n.id===nodeId)!;
    expect(node.id).toBe(nodeId);expect(node.keyframes).toEqual(source.scenes.find(s=>s.id===sceneId)!.nodes.find(n=>n.id===nodeId)!.keyframes);
    expect(node.style.fill).toBe("#336699");expect(undone.reviews).toEqual(source.reviews);
    expect((await service.history(source.id,source.revision)).map(event=>event.change.type)).toEqual([
      "combine_creative_branches","checkout_branch","update_canvas_style","undo_creative_patch"]);
    const screenshot=info.outputPath("combined-candidate-reopened-undo.png");await page.screenshot({path:screenshot,fullPage:true});
    await info.attach("editable-candidate-reopened",{path:screenshot,contentType:"image/png"});
  });
});

test("lost original reply preserves late choices and UNKNOWN through reload, exact read and explicit separate intent",async({page},info)=>{
  await withCombination(page,info,async value=>{
    const {service,source}=value;await author(page,value,"Original uncertain candidate");await preview(page);
    const original=service.invoke.bind(service),observed=deferred<Project>(),release=deferred<void>(),finished=deferred<void>();
    let armed=true,matched=false;void observed.promise.catch(()=>undefined);
    service.invoke=async<T=unknown>(command:string,args:Record<string,unknown>={}):Promise<T>=>{
      const request=(args as unknown as ApplyArgs).request;
      if(!armed||command!=="apply_change"||request?.change.type!=="combine_creative_branches")return original<T>(command,args);
      armed=false;matched=true;
      try{const saved=await original<Project>(command,args);observed.resolve(saved);await release.promise;
        throw new Error("Controlled delivery loss after real combination commit");}
      catch(reason){observed.reject(reason);throw reason;}finally{finished.resolve();}
    };
    value.cleanups.push(async()=>{service.invoke=original;release.resolve();if(matched)await finished.promise;});
    await button(page,"Create inactive combined candidate").click();await observed.promise;
    await page.getByLabel("Combination candidate name").fill("  Later distinct candidate  ");
    await page.getByLabel("Combination reason").fill("Later edited reason stays independent of the unconfirmed original.");
    release.resolve();await expect(region(page).getByRole("alert").filter({hasText:"delivery loss"})).toBeVisible();
    await page.reload();await review(page);await expect(page.getByLabel("Combination candidate name")).toHaveValue("  Later distinct candidate  ");
    await expect(button(page,"Create inactive combined candidate")).toBeDisabled();expect(combinationRequests(service)).toHaveLength(1);
    const retained=await recoveryBytes(page,source);expect(retained.value.draft.pending.outcome).toBe("UNKNOWN");
    await button(page,"Read saved branches and history").click();
    await expect(page.getByRole("region",{name:"Saved branch outcome"})).toContainText("do not identify or acknowledge");
    const before=await value.observe("original applied but still unacknowledged after actual history read");
    await button(page,"Keep original attempt and start another combination").click();
    expect(await page.evaluate(key=>localStorage.getItem(key),retained.key)).toBe(retained.raw);
    expect(await service.project(source.id)).toEqual(before);expect(combinationRequests(service)).toHaveLength(1);
    await expect(page.getByLabel("Combination reason")).toHaveValue("Later edited reason stays independent of the unconfirmed original.");
    await preview(page);await button(page,"Create inactive combined candidate").click();await revision(page,before.revision+1);
    const after=await value.observe("a distinct explicit intent creates a second candidate"),requests=combinationRequests(service);
    expect(requests).toHaveLength(2);expect(after.branches.filter(b=>["Original uncertain candidate","  Later distinct candidate  "].includes(b.name))).toHaveLength(2);
    expect(await page.evaluate(key=>localStorage.getItem(key),retained.key)).toBe(retained.raw);
    value.observations.originalRecovery=retained;
  });
});

test("an independent peer wins CAS after preview; rejected combination retains raw and never adds a phantom candidate",async({page},info)=>{
  await withCombination(page,info,async value=>{
    const {service,source}=value,peer=await value.peer();await author(page,value,"Peer-conflicted candidate");await preview(page);
    const original=service.invoke.bind(service);let armed=true;
    service.invoke=async<T=unknown>(command:string,args:Record<string,unknown>={}):Promise<T>=>{
      if(armed&&command==="apply_change"&&(args as unknown as ApplyArgs).request?.change.type==="combine_creative_branches"){
        armed=false;await peer.apply(await peer.project(source.id),{type:"rename_project",title:"Independent peer advances the exact source"});
      }
      return original<T>(command,args);
    };
    value.cleanups.push(async()=>{service.invoke=original;});
    await button(page,"Create inactive combined candidate").click();
    await expect(region(page).getByRole("alert").filter({hasText:/revision|conflict|stale/i})).toBeVisible();
    await expect(page.getByLabel("Combination candidate name")).toHaveValue("Peer-conflicted candidate");
    const saved=await value.observe("peer committed and original combination rejected by actual authority");
    expect(saved.branches.map(b=>b.id)).toEqual(source.branches.map(b=>b.id));expect(saved.scenes).toEqual(source.scenes);
    expect((await service.history(source.id,source.revision)).map(event=>event.change.type)).toEqual(["rename_project"]);
    expect(combinationRequests(service)).toHaveLength(1);
    await page.reload();await review(page);await expect(button(page,"Create inactive combined candidate")).toBeDisabled();
    await expect(page.getByLabel("Combination candidate name")).toHaveValue("Peer-conflicted candidate");
    expect((await recoveryBytes(page,source)).value.draft.pending.outcome).toBe("UNKNOWN");
  });
});
