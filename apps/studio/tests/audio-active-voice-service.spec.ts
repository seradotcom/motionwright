import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { expect, test as base, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import type { AudioRecovery } from "../src/audioDraft";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Authored fixtures, real StudioService/grants/SQLite/CAS, browser interaction.
// Faults affect reply delivery or browser custody; no saved Project is simulated.
type VoiceFixture = {
  service: ScopedChangeService; peers: ScopedChangeService[]; initial: Project;
  first: string; second: string; paths: string[]; hashes: string[];
  observations: Record<string,unknown>; final: Project | undefined;
};
const test = base.extend<{ voice: VoiceFixture }>({
  voice: async ({page},use,info)=>{
    const service=await ScopedChangeService.create({databasePath:info.outputPath("service","studio.sqlite3"),desktopInvokeFixture:true});
    const peers:ScopedChangeService[]=[];const observations:Record<string,unknown>={};
    let initial:Project|undefined,fixture:VoiceFixture|undefined,primaryFailure:VoiceFixtureFailure|undefined;
    const paths:string[]=[],hashes:string[]=[];
    try {
      paths.push(info.outputPath("authored-original.wav"),info.outputPath("authored-revision.wav"));
      initial=(await service.fixtureBootstrap()).project;
      for(let i=0;i<2;i++) {
        const bytes=pcmWav(i?4096:8192);await writeFile(paths[i],bytes);
        hashes.push(createHash("sha256").update(bytes).digest("hex"));
        initial=await service.importVoice(initial,paths[i],i?"Voice continuity revision":"Voice continuity original");
      }
      const [a,b]=initial.audio.voice_tracks.slice(-2);
      expect(a.source_sha256).toBe(hashes[0]);expect(b.source_sha256).toBe(hashes[1]);
      expect(initial.audio.active_voice_track_id).toBe(b.id);
      await service.attachFixturePage(page,initial.id);await page.goto("/");await openAudio(page);
      fixture={service,peers,initial,first:a.id,second:b.id,paths,hashes,observations,final:undefined};
      await use(fixture);
    } catch(reason) { primaryFailure={reason}; }
    finally {
      await retainVoiceFixtureEvidence(page,info,service,peers,initial,fixture?.final,paths,hashes,observations,primaryFailure);
    }
  },
});
type VoiceFixtureFailure={reason:unknown};
function voiceFailureDetail(reason:unknown) {
  try {
    if(reason instanceof Error)return {name:reason.name,message:reason.message,stack:reason.stack};
    if(reason && typeof reason==="object" && "message" in reason) {
      const reported=reason as {message?:unknown;stack?:unknown;value?:unknown};
      return {message:String(reported.message),stack:typeof reported.stack==="string"?reported.stack:undefined,value:reported.value};
    }
    return {message:String(reason)};
  } catch {return {message:"An unprintable thrown value remains in the original fixture failure."};}
}
async function retainVoiceFixtureEvidence(page:Page,info:TestInfo,service:ScopedChangeService,peers:ScopedChangeService[],
  initial:Project|undefined,final:Project|undefined,paths:string[],hashes:string[],observations:Record<string,unknown>,
  primaryFailure?:VoiceFixtureFailure) {
  const testStatus=info.status,expectedStatus=info.expectedStatus,playwrightErrors=[...info.errors];
  const failures:unknown[]=primaryFailure?[primaryFailure.reason]:[];
  for(const reported of playwrightErrors)if(!failures.includes(reported))failures.push(reported);
  if((testStatus!=="passed" || expectedStatus!=="passed") && failures.length===0) {
    failures.push(new Error("Playwright fixture finished with status "+String(testStatus)+" (expected "+String(expectedStatus)+")."));
  }
  let finalization:StoreFinalization|undefined;
  try {
    finalization=await service.finalizeClosedStore(info.outputPath("closed-store"),final,peers,[()=>page.close()]);
  } catch(reason) {failures.push(reason);}
  try {
    const path=info.outputPath("active-voice-service-evidence.json");
    await writeFile(path,JSON.stringify({scope:"browser_real_service_sqlite_active_voice_continuity",
      excludes:["installed_tauri","native_mix","mastering","alignment_quality","human_acceptance"],
      testStatus,expectedStatus,playwrightErrors,primaryFailure:primaryFailure?voiceFailureDetail(primaryFailure.reason):null,
      failures:failures.map(voiceFailureDetail),initial,final,paths,hashes,observations,canonicalOracle:finalization?.oracle,
      services:[service,...peers].map(item=>({databasePath:item.databasePath,binaryPath:item.binaryPath,
        transcript:item.transcript,processExits:item.processExits,lifecycleTaints:item.lifecycleTaints,stderr:item.stderr})),
    },null,2));
  } catch(reason) {failures.push(reason);}
  for(const attachment of [
    {name:"active-voice-service-evidence",file:"active-voice-service-evidence.json"},
    {name:"complete-store-finalization",file:"closed-store-outcomes.json"},
  ]) {
    try {
      const path=info.outputPath(attachment.file);
      await info.attach(attachment.name,{path,contentType:"application/json"});
    } catch(reason) {failures.push(reason);}
  }
  if(failures.length===1)throw failures[0];
  if(failures.length>1)throw new AggregateError(failures,"Active voice scenario and evidence custody retained separate failures",{cause:failures[0]});
}
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"A source-built real StudioService is required; no persistence simulation fallback.");
test.setTimeout(120_000);
const button=(page:Page,name:string)=>page.getByRole("button",{name,exact:true});
const ledger=(page:Page)=>page.getByRole("complementary",{name:"Voice takes",exact:true});
const retained=(page:Page)=>page.getByRole("region",{name:"Retained voice selection",exact:true});
const originalButton=(page:Page)=>button(page,"Activate voice Voice continuity original");
const revisionButton=(page:Page)=>button(page,"Activate voice Voice continuity revision");
async function openAudio(page:Page){await button(page,"Audio").click();await expect(ledger(page)).toBeVisible();}
async function reloadAudio(page:Page){await page.reload();await openAudio(page);}
async function revision(page:Page,value:number){await expect(page.locator(".revision-chip").first()).toHaveText("r"+value);}
function pcmWav(amplitude:number) {
  const rate=48000,frames=rate*2,buffer=Buffer.alloc(44+frames*2);
  buffer.write("RIFF");buffer.writeUInt32LE(buffer.length-8,4);buffer.write("WAVEfmt ",8);buffer.writeUInt32LE(16,16);
  buffer.writeUInt16LE(1,20);buffer.writeUInt16LE(1,22);buffer.writeUInt32LE(rate,24);buffer.writeUInt32LE(rate*2,28);
  buffer.writeUInt16LE(2,32);buffer.writeUInt16LE(16,34);buffer.write("data",36);buffer.writeUInt32LE(frames*2,40);
  for(let i=0;i<frames;i++)buffer.writeInt16LE(i%2?amplitude:-amplitude,44+i*2);return buffer;
}
interface RequestArgs extends Record<string,unknown> {
  request:{project_id:string;generation:string;revision:number;request_id:string;effect_grant:string;change:Change};
}
function selectionRequests(service:ScopedChangeService) {
  return service.transcript.flatMap(entry=>{
    if(entry.direction!=="request" || !("command" in entry.payload) || entry.payload.command!=="apply_change")return [];
    const args=entry.payload.args as RequestArgs;
    return args.request.change.type==="set_active_voice_track"?[args.request]:[];
  });
}
async function ownRecord(page:Page,project:Project) {
  return page.evaluate(scope=>{
    const writer=window.sessionStorage.getItem("motionwright.audio-session.v1:"+scope);
    if(!writer)throw new Error("The current selection has no retained writer pointer.");
    const key="motionwright.audio-recovery.v1:"+encodeURIComponent(scope)+":"+writer;
    const raw=window.localStorage.getItem(key);if(raw===null)throw new Error("The selected writer has no retained bytes.");
    return {key,raw,value:JSON.parse(raw) as AudioRecovery};
  },JSON.stringify([project.id,project.generation,project.active_branch,{kind:"active-voice"}]));
}
const rawRecord=(page:Page,key:string)=>page.evaluate(key=>window.localStorage.getItem(key),key);
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done;});return {resolve,promise};}

test("active voice selection preserves both measured sources and canonical state through keyboard save and reopen",async({page,voice},info)=>{
  const {service,initial,first,paths,hashes,observations}=voice;
  await originalButton(page).focus();const timecode=await page.locator(".timeline-header .timecode").innerText();
  await page.keyboard.press("Space");await revision(page,initial.revision+1);
  await expect(originalButton(page)).toHaveAttribute("aria-pressed","true");
  await expect(retained(page)).toContainText("Acknowledged selection response");
  await expect(page.locator(".timeline-header .timecode")).toHaveText(timecode);
  const saved=await service.project(initial.id);
  expect(saved.audio.active_voice_track_id).toBe(first);expect(saved.audio.voice_tracks).toEqual(initial.audio.voice_tracks);
  expect(saved.audio.transcript).toEqual(initial.audio.transcript);expect(saved.audio.cues).toEqual(initial.audio.cues);
  expect(saved.assets).toEqual(initial.assets);expect(saved.scenes).toEqual(initial.scenes);
  for(let i=0;i<paths.length;i++)expect(createHash("sha256").update(await readFile(paths[i])).digest("hex")).toBe(hashes[i]);
  const requests=selectionRequests(service);expect(requests).toHaveLength(1);expect(requests[0].revision).toBe(initial.revision);
  const copy=await ownRecord(page,initial);expect(copy.value.draft.awaiting?.selection?.intentId).not.toBe(requests[0].request_id);
  expect(await service.reopen(saved.id)).toEqual(saved);await reloadAudio(page);
  await expect(originalButton(page)).toHaveAttribute("aria-pressed","true");expect(selectionRequests(service)).toHaveLength(1);
  voice.final=saved;observations.saved=saved;observations.selectionRecord=copy;await ledger(page).screenshot({path:info.outputPath("voice-selection-reopened.png")});
});

test("a held original selection reply blocks duplicate sends across workspace remount",async({page,voice})=>{
  const {service,initial,first,second,observations}=voice;const original=service.invoke.bind(service),entered=deferred<Project>(),release=deferred<void>();
  let armed=true;
  service.invoke=async<T=unknown>(command:string,args:Record<string,unknown>={}):Promise<T>=>{
    const request=args.request as RequestArgs["request"]|undefined;
    if(!armed || command!=="apply_change" || request?.change.type!=="set_active_voice_track")return original<T>(command,args);
    armed=false;const result=await original<Project>(command,args);entered.resolve(result);await release.promise;return result as T;
  };
  try {
    await originalButton(page).click();const saved=await entered.promise;expect(saved.audio.active_voice_track_id).toBe(first);
    await expect(originalButton(page)).toBeDisabled();await expect(revisionButton(page)).toBeDisabled();
    await button(page,"Brief").click();await openAudio(page);
    await expect(originalButton(page)).toBeDisabled();expect(selectionRequests(service)).toHaveLength(1);
    release.resolve();await revision(page,initial.revision+1);
    await expect(retained(page)).toContainText("Acknowledged selection response");
    await revisionButton(page).click();await revision(page,initial.revision+2);
    const final=await service.project(initial.id);expect(final.audio.active_voice_track_id).toBe(second);
    expect(selectionRequests(service)).toHaveLength(2);expect(await service.reopen(final.id)).toEqual(final);voice.final=final;observations.final=final;
  } finally {release.resolve();service.invoke=original;}
});

for(const scenario of [
  {kind:"unrelated" as const,title:"an unrelated r+1 leaves active voice UNKNOWN until separate saved-state review"},
  {kind:"committed" as const,title:"a committed active voice followed by r+2 remains UNKNOWN after lost reply and reload"},
])test(scenario.title,async({page,voice})=>{
  const {service,peers,initial,first,second,observations}=voice;
  const peer=await service.peer();peers.push(peer);const original=service.invoke.bind(service);let armed=true;
  const diagnostic="Original active voice response deliberately unavailable after real service observation";
  service.invoke=async<T=unknown>(command:string,args:Record<string,unknown>={}):Promise<T>=>{
    const request=args.request as RequestArgs["request"]|undefined;
    if(!armed || command!=="apply_change" || request?.change.type!=="set_active_voice_track")return original<T>(command,args);
    armed=false;
    if(scenario.kind==="unrelated")await peer.apply(await peer.project(initial.id),{type:"rename_project",title:"Independent before selection"});
    try {observations.originalServiceOutcome={status:"returned",project:await original<Project>(command,args)};}
    catch(error){observations.originalServiceOutcome={status:"rejected",diagnostic:error instanceof Error?error.message:String(error)};}
    if(scenario.kind==="committed")await peer.apply(await peer.project(initial.id),{type:"rename_project",title:"Independent after selection"});
    throw new Error(diagnostic);
  };
  try {
    await originalButton(page).click();await expect(retained(page)).toContainText(diagnostic);
    await expect(originalButton(page)).toBeDisabled();expect(selectionRequests(service)).toHaveLength(1);
    const saved=await peer.project(initial.id);expect(saved.revision).toBe(initial.revision+(scenario.kind==="unrelated"?1:2));
    expect(saved.audio.active_voice_track_id).toBe(scenario.kind==="unrelated"?second:first);
    const journal=await peer.history(initial.id,initial.revision);
    expect(journal.map(e=>e.change.type)).toEqual(scenario.kind==="unrelated"?["rename_project"]:["set_active_voice_track","rename_project"]);
    await reloadAudio(page);await revision(page,saved.revision);await expect(retained(page)).toContainText(diagnostic);
    expect(selectionRequests(service)).toHaveLength(1);const before=await ownRecord(page,initial);
    await button(page,"Read saved selection and history").click();
    const review=page.getByRole("region",{name:"Saved voice selection review",exact:true});
    await expect(review).toContainText("does not identify or acknowledge the earlier request");
    await expect(review).toContainText("Read saved revision r"+saved.revision);
    if(scenario.kind==="committed")await expect(review).toContainText("History contains matching values at r"+(initial.revision+1));
    else await expect(review).toContainText("No matching change values");
    await button(page,"Keep earlier record and choose another selection").click();
    expect(await rawRecord(page,before.key)).toBe(before.raw);expect(selectionRequests(service)).toHaveLength(1);
    expect(await peer.project(initial.id)).toEqual(saved);expect(await peer.history(initial.id,initial.revision)).toEqual(journal);
    await (scenario.kind==="unrelated"?originalButton(page):revisionButton(page)).click();await revision(page,saved.revision+1);
    const requests=selectionRequests(service);expect(requests).toHaveLength(2);expect(requests[1].request_id).not.toBe(requests[0].request_id);
    expect(await rawRecord(page,before.key)).toBe(before.raw);
    const after=await ownRecord(page,initial);expect(after.value.writer).not.toBe(before.value.writer);
    expect(after.value.draft.awaiting?.selection?.outcome).toBe("ACKNOWLEDGED");
    const final=await peer.project(initial.id);expect(final.audio.voice_tracks).toEqual(initial.audio.voice_tracks);
    expect(await service.reopen(final.id)).toEqual(final);observations.archivedUnknown=before;voice.final=final;observations.final=final;
  } finally {service.invoke=original;}
});

for(const kind of ["record","pointer"] as const)test("active voice "+kind+" retention failure is NOT_SENT and reload never submits it",async({page,voice})=>{
  const {service,initial,observations}=voice;
  await page.evaluate(kind=>{
    const original=Storage.prototype.setItem;
    Object.defineProperty(window,"__restoreVoiceStorage",{configurable:true,value:()=>{Storage.prototype.setItem=original;}});
    Storage.prototype.setItem=function(key:string,value:string) {
      const wanted=kind==="record"?this===window.localStorage&&key.startsWith("motionwright.audio-recovery.v1:"):this===window.sessionStorage&&key.startsWith("motionwright.audio-session.v1:");
      if(wanted && key.includes("active-voice"))throw new Error("Original "+kind+" custody failure");
      return original.call(this,key,value);
    };
  },kind);
  try {
    await originalButton(page).click();await expect(ledger(page)).toContainText("NOT_SENT:");
    await expect(ledger(page)).toContainText("Original "+kind+" custody failure");
    expect(selectionRequests(service)).toHaveLength(0);expect(await service.project(initial.id)).toEqual(initial);
    observations.retained=await page.evaluate(()=>Object.fromEntries(Object.entries(window.localStorage).filter(([key])=>key.startsWith("motionwright.audio-recovery.v1:")&&key.includes("active-voice"))));
  } finally {
    await page.evaluate(()=>{(window as unknown as {__restoreVoiceStorage:()=>void}).__restoreVoiceStorage();});
  }
  await reloadAudio(page);expect(selectionRequests(service)).toHaveLength(0);
  const final=await service.project(initial.id);expect(final).toEqual(initial);
  expect(await service.reopen(final.id)).toEqual(final);voice.final=final;
});
