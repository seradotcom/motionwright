import { writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import type { BriefRecovery } from "../src/briefDraft";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Only transport delivery and browser storage are faulted. Project, grants,
// CAS, journal and closed-store observations come from the real StudioService.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A source-built real StudioService is required; no simulated persistence fallback.");
test.setTimeout(120_000);

const button = (page: Page, name: string) => page.getByRole("button", {name, exact:true});
const objective = (page: Page) => page.getByLabel("Production objective", {exact:true});
const unconfirmed = (page: Page) => page.getByRole("region", {name:"Unconfirmed brief submission", exact:true});
async function openBrief(page: Page) {
  await button(page,"Brief").click();
  await expect(page.getByRole("heading",{name:"Project brief",exact:true})).toBeVisible();
}
interface ApplyArgs extends Record<string, unknown> {
  request: {project_id:string; generation:string; revision:number; request_id:string; effect_grant:string; change:Change};
}
function requests(service: ScopedChangeService, command: string) {
  return service.transcript.filter(entry=>entry.direction==="request" && "command" in entry.payload && entry.payload.command===command);
}
function applies(service: ScopedChangeService): ApplyArgs[] {
  return requests(service,"apply_change").map(entry=>("args" in entry.payload ? entry.payload.args : undefined) as ApplyArgs);
}
async function ownRecovery(page: Page, project: Project) {
  return page.evaluate((scope:string) => {
    const writer=window.sessionStorage.getItem("motionwright.brief-session.v1:"+scope);
    if(!writer)throw new Error("This tab has no retained Brief writer.");
    const key="motionwright.brief-draft.v1:"+JSON.stringify([scope,writer]);
    const raw=window.localStorage.getItem(key);
    if(raw===null)throw new Error("The selected Brief writer has no retained bytes.");
    return {writer,key,raw,value:JSON.parse(raw) as BriefRecovery};
  },JSON.stringify([project.id,project.generation,project.active_branch]));
}
async function retainedRaw(page: Page, key: string) {
  return page.evaluate(key=>window.localStorage.getItem(key),key);
}
type ScenarioFailure = { reason: unknown };
function failureDetail(reason: unknown) {
  try { return reason instanceof Error ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) }; }
  catch { return { message: "An unprintable thrown value remains in the original test failure." }; }
}

async function finish(page: Page, info: TestInfo, service: ScopedChangeService, peers: ScopedChangeService[],
  final: Project | undefined, observations: Record<string,unknown>, prepare: (()=>Promise<unknown>)[] = [],
  primaryFailure?: ScenarioFailure) {
  let custody: StoreFinalization | undefined;
  const failures: unknown[] = primaryFailure ? [primaryFailure.reason] : [];
  try {
    custody=await service.finalizeClosedStore(info.outputPath("closed-store"),final,peers,[...prepare,()=>page.close()]);
  } catch (reason) { failures.push(reason); }
  const path=info.outputPath("brief-uncertain-outcome-evidence.json");
  try {
    await writeFile(path,JSON.stringify({
      scope:"synthetic authoring against real StudioService + grants + SQLite + DOM; excludes installed Tauri, native renders and human acceptance",
      observations,final,canonicalOracle:custody?.oracle,
      primaryFailure:primaryFailure ? failureDetail(primaryFailure.reason) : null,
      failures:failures.map(failureDetail),
      services:[service,...peers].map(item=>({
        databasePath:item.databasePath,binaryPath:item.binaryPath,transcript:item.transcript,
        processExits:item.processExits,lifecycleTaints:item.lifecycleTaints,stderr:item.stderr,
      })),
    },null,2));
  } catch (reason) { failures.push(reason); }
  for (const attachment of [
    {name:"brief-uncertain-outcome-evidence",path},
    {name:"complete-store-finalization",path:info.outputPath("closed-store-outcomes.json")},
  ]) {
    try { await info.attach(attachment.name,{path:attachment.path,contentType:"application/json"}); }
    catch (reason) { failures.push(reason); }
  }
  if (failures.length === 1) throw failures[0];
  if (failures.length > 1) throw new AggregateError(failures,
    "Brief scenario and evidence finalization retained separate failures", {cause:primaryFailure?.reason});
}

for (const scenario of [
  {mode:"unrelated" as const,title:"an unrelated r+1 is reviewed without acknowledging or replaying an uncertain Brief"},
  {mode:"committed" as const,title:"a committed Brief followed by r+2 preserves later input until separate review"},
]) test(scenario.title,async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","studio.sqlite3"),desktopInvokeFixture:true});
  const peers:ScopedChangeService[]=[];
  const observations:Record<string,unknown>={mode:scenario.mode};
  let final:Project|undefined;
  let primaryFailure:ScenarioFailure|undefined;
  const original=service.invoke.bind(service);
  try {
    const initial=(await service.fixtureBootstrap()).project;
    const peer=await service.peer();peers.push(peer);
    await service.attachFixturePage(page,initial.id);await page.goto("/");await openBrief(page);
    let armed=true;
    const diagnostic="Original Brief transport reply was lost after observing the real service outcome";
    service.invoke=async<T=unknown>(command:string,args:Record<string,unknown>={}):Promise<T>=>{
      const request=args.request as ApplyArgs["request"]|undefined;
      if(command!=="apply_change" || request?.change.type!=="set_brief" || !armed)return original<T>(command,args);
      armed=false;
      if(scenario.mode==="unrelated") {
        const current=await peer.project(initial.id);
        await peer.apply(current,{type:"rename_project",title:"Independent title before Brief request"});
      }
      try {
        const result=await original<Project>(command,args);
        observations.originalServiceOutcome={status:"returned",project:result};
      } catch(error) {
        observations.originalServiceOutcome={status:"rejected",message:error instanceof Error?error.message:String(error)};
      }
      if(scenario.mode==="committed") {
        const current=await peer.project(initial.id);
        await peer.apply(current,{type:"rename_project",title:"Independent title after Brief commit"});
      }
      throw new Error(diagnostic); // Lose only the reply, never fabricate a saved Project.
    };
    const submitted="Sent objective whose transport result is deliberately unavailable";
    const later="Later author correction\nkept exactly after the uncertain request\n";
    await objective(page).fill(submitted);
    await button(page,"Commit brief").click();
    await expect(unconfirmed(page)).toContainText(diagnostic);
    await expect(button(page,"Commit brief")).toBeDisabled();
    expect(applies(service)).toHaveLength(1);
    const saved=await peer.project(initial.id);
    expect(saved.revision).toBe(initial.revision+(scenario.mode==="unrelated"?1:2));
    expect(saved.brief.objective).toBe(scenario.mode==="unrelated"?initial.brief.objective:submitted);
    const priorJournal=await peer.history(initial.id,initial.revision);
    expect(priorJournal.map(event=>event.change.type)).toEqual(scenario.mode==="unrelated"
      ?["rename_project"]:["set_brief","rename_project"]);
    observations.savedBeforeReview=saved;observations.journalBeforeReview=priorJournal;
    await objective(page).fill(later);
    await page.reload();await openBrief(page);
    await expect(objective(page)).toHaveValue(later);
    await expect(unconfirmed(page)).toContainText(diagnostic);
    expect(applies(service)).toHaveLength(1);
    const historyReads=requests(service,"project_history").length;
    await button(page,"Read saved Brief and history").click();
    const review=page.getByRole("region",{name:"Saved Brief outcome review",exact:true});
    await expect(review).toContainText("does not identify or acknowledge the earlier request");
    await expect(review).toContainText("Read saved revision r"+saved.revision);
    expect(requests(service,"project_history").length).toBeGreaterThan(historyReads);
    if(scenario.mode==="committed")await expect(review).toContainText("History contains matching values at r"+(initial.revision+1));
    else await expect(review).toContainText("No matching change values");
    const before=await ownRecovery(page,initial);
    expect(before.value.draft?.pending?.outcome).toBe("UNKNOWN");
    await button(page,"Keep earlier attempt and continue editing").click();
    await expect(unconfirmed(page)).toHaveCount(0);
    await expect(objective(page)).toHaveValue(later);
    expect(applies(service)).toHaveLength(1);
    expect(await peer.project(initial.id)).toEqual(saved);
    expect(await peer.history(initial.id,initial.revision)).toEqual(priorJournal);
    const after=await ownRecovery(page,initial);
    expect(after.writer).not.toBe(before.writer);
    expect(after.value.draft?.pending).toBeUndefined();
    const archived=await retainedRaw(page,before.key);
    expect(archived).not.toBeNull();
    expect((JSON.parse(archived!) as BriefRecovery).draft?.pending).toEqual(before.value.draft?.pending);
    expect((JSON.parse(archived!) as BriefRecovery).draft?.fields.objective.raw).toBe(later);
    observations.archivedUnknown={key:before.key,raw:archived};
    if(scenario.mode==="committed") {
      await expect(page.getByRole("region",{name:"Brief conflicts",exact:true})).toBeVisible();
      await button(page,"Keep my draft Production objective").click();
    }
    await expect(button(page,"Commit brief")).toBeEnabled();
    await button(page,"Commit brief").click();
    await expect(page.locator(".revision-chip").first()).toHaveText("r"+(saved.revision+1));
    await expect(unconfirmed(page)).toHaveCount(0);
    final=await peer.project(initial.id);
    expect(final.brief.objective).toBe(later);
    expect(final.title).toBe(saved.title);
    expect(final.brief.claims).toEqual(initial.brief.claims);
    expect(applies(service)).toHaveLength(2);
    expect(applies(service)[1].request.revision).toBe(saved.revision);
    expect(applies(service)[1].request.request_id).not.toBe(applies(service)[0].request.request_id);
    const journal=await peer.history(initial.id,initial.revision);
    expect(journal).toHaveLength(priorJournal.length+1);
    expect(journal.at(-1)?.change).toMatchObject({type:"set_brief",objective:later});
    expect(await retainedRaw(page,before.key)).toBe(archived);
    await service.close();await service.start();await page.reload();await openBrief(page);
    await expect(objective(page)).toHaveValue(later);
    await expect(unconfirmed(page)).toHaveCount(0);
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id,initial.revision)).toEqual(journal);
    expect(applies(service)).toHaveLength(2);
  } catch (reason) { primaryFailure={reason}; }
  finally {
    service.invoke=original;
    await finish(page,info,service,peers,final,observations,[],primaryFailure);
  }
});

for(const fault of ["record","pointer"] as const) test(
  "Brief "+fault+" retention failure is NOT_SENT and retry backup permits one explicit save",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","studio.sqlite3"),desktopInvokeFixture:true});
  let final:Project|undefined;
  let primaryFailure:ScenarioFailure|undefined;
  const observations:Record<string,unknown>={fault};
  try {
    const initial=(await service.fixtureBootstrap()).project;
    await service.attachFixturePage(page,initial.id);await page.goto("/");await openBrief(page);
    const raw="Author input survives the "+fault+" preparation failure\n";
    await objective(page).fill(raw);
    await expect(button(page,"Commit brief")).toBeEnabled();
    const grantsBefore=requests(service,"issue_effect_grant").length;
    await page.evaluate(fault=>{
      const holder=window as unknown as {restoreBriefStorage?:()=>void};
      const original=Storage.prototype.setItem;let armed=true,staged=false;
      holder.restoreBriefStorage=()=>{Storage.prototype.setItem=original;};
      Storage.prototype.setItem=function(key:string,value:string) {
        if(this===window.localStorage && key.startsWith("motionwright.brief-draft.v1:")) {
          const record=JSON.parse(value) as BriefRecovery;
          if(record.draft?.pending?.outcome==="UNKNOWN") {
            staged=true;
            if(fault==="record"&&armed){armed=false;throw new Error("Controlled Brief record write failure before dispatch");}
          }
        }
        if(fault==="pointer"&&armed&&staged&&this===window.sessionStorage&&key.startsWith("motionwright.brief-session.v1:")) {
          armed=false;throw new Error("Controlled Brief pointer write failure before dispatch");
        }
        return original.call(this,key,value);
      };
    },fault);
    await button(page,"Commit brief").click();
    await expect(page.getByRole("status",{name:"Brief change not sent",exact:true})).toBeVisible();
    await expect(page.getByRole("alert",{name:"Brief draft backup warning",exact:true})).toBeVisible();
    await expect(unconfirmed(page)).toHaveCount(0);
    await expect(objective(page)).toHaveValue(raw);
    await expect(button(page,"Commit brief")).toBeDisabled();
    expect(applies(service)).toHaveLength(0);
    expect(requests(service,"issue_effect_grant")).toHaveLength(grantsBefore);
    expect(await service.project(initial.id)).toEqual(initial);
    expect(await service.history(initial.id,initial.revision)).toEqual([]);
    observations.beforeRetry={project:await service.project(initial.id),applyCount:applies(service).length};
    await page.evaluate(()=>{
      const holder=window as unknown as {restoreBriefStorage?:()=>void};
      holder.restoreBriefStorage?.();delete holder.restoreBriefStorage;
    });
    await button(page,"Retry brief draft backup").click();
    await expect(page.getByRole("alert",{name:"Brief draft backup warning",exact:true})).toHaveCount(0);
    await expect(button(page,"Commit brief")).toBeEnabled();
    const retained=await ownRecovery(page,initial);
    expect(retained.value.draft?.pending?.outcome).toBe("NOT_SENT");
    expect(applies(service)).toHaveLength(0);
    await button(page,"Commit brief").click();
    await expect(page.locator(".revision-chip").first()).toHaveText("r"+(initial.revision+1));
    await expect(unconfirmed(page)).toHaveCount(0);
    final=await service.project(initial.id);
    expect(final.brief.objective).toBe(raw);
    expect(applies(service)).toHaveLength(1);
    const journal=await service.history(initial.id,initial.revision);
    expect(journal).toHaveLength(1);
    expect(journal[0].change).toMatchObject({type:"set_brief",objective:raw});
    await service.close();await service.start();await page.reload();await openBrief(page);
    await expect(objective(page)).toHaveValue(raw);
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id,initial.revision)).toEqual(journal);
    expect(applies(service)).toHaveLength(1);
  } catch (reason) { primaryFailure={reason}; }
  finally {await finish(page,info,service,[],final,observations,[],primaryFailure);}
});

test("a late Brief reply cannot consume the pending state of a new recovery writer",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","studio.sqlite3"),desktopInvokeFixture:true});
  const original=service.invoke.bind(service);
  let release!:()=>void,entered!:()=>void,finished!:()=>void;
  const paused=new Promise<void>(resolve=>{release=resolve;});
  const dispatched=new Promise<void>(resolve=>{entered=resolve;});
  const completed=new Promise<void>(resolve=>{finished=resolve;});
  let held=false,armed=true,final:Project|undefined;
  let primaryFailure:ScenarioFailure|undefined;
  const observations:Record<string,unknown>={};
  try {
    const initial=(await service.fixtureBootstrap()).project;
    await service.attachFixturePage(page,initial.id);await page.goto("/");await openBrief(page);
    service.invoke=async<T=unknown>(command:string,args:Record<string,unknown>={}):Promise<T>=>{
      const request=args.request as ApplyArgs["request"]|undefined;
      if(command!=="apply_change"||request?.change.type!=="set_brief"||!armed)return original<T>(command,args);
      armed=false;held=true;entered();
      try {await paused;return await original<T>(command,args);}
      finally {finished();}
    };
    await objective(page).fill("Original held Brief value");
    await button(page,"Commit brief").click();await dispatched;
    await button(page,"Canvas").click();await openBrief(page);
    await expect(unconfirmed(page)).toBeVisible();
    await objective(page).fill("Later input belongs to the separate recovery writer\n");
    await button(page,"Read saved Brief and history").click();
    await expect(page.getByRole("region",{name:"Saved Brief outcome review",exact:true})).toContainText("0 consecutive history records");
    const earlier=await ownRecovery(page,initial);
    await button(page,"Keep earlier attempt and continue editing").click();
    await expect(unconfirmed(page)).toHaveCount(0);
    const next=await ownRecovery(page,initial);
    expect(next.writer).not.toBe(earlier.writer);
    const archived=await retainedRaw(page,earlier.key);
    await button(page,"Commit brief").click();
    // The same existing mutation coordinator rejects overlap locally. This
    // component has invoked it, so its new pending record stays conservative.
    await expect(unconfirmed(page)).toBeVisible();
    const newer=await ownRecovery(page,initial);
    expect(newer.value.draft?.pending?.outcome).toBe("UNKNOWN");
    expect(newer.value.draft?.pending?.intentId).not.toBe(earlier.value.draft?.pending?.intentId);
    const copies=page.getByRole("region",{name:"Brief draft recovery",exact:true});
    await copies.locator("summary").click();
    await expect(button(page,"Restore brief draft "+earlier.writer)).toBeVisible();
    await expect(button(page,"Restore brief draft "+earlier.writer)).toBeDisabled();
    expect(applies(service)).toHaveLength(0); // The one old transport call is still held before the real service.
    expect(await service.project(initial.id)).toEqual(initial);
    release();await completed;
    await expect(page.locator(".revision-chip").first()).toHaveText("r"+(initial.revision+1));
    await expect(unconfirmed(page)).toBeVisible();
    await expect(objective(page)).toHaveValue("Later input belongs to the separate recovery writer\n");
    expect(applies(service)).toHaveLength(1);
    const after=await ownRecovery(page,initial);
    expect(after.writer).toBe(newer.writer);
    expect(after.value.draft?.pending).toEqual(newer.value.draft?.pending);
    expect(await retainedRaw(page,earlier.key)).toBe(archived);
    final=await service.project(initial.id);
    expect(final.brief.objective).toBe("Original held Brief value");
    expect(await service.history(initial.id,initial.revision)).toHaveLength(1);
    observations.archivedUnknown={key:earlier.key,raw:archived};
    observations.newerUncertainAttempt=after.value.draft?.pending;
    await page.reload();await openBrief(page);
    await expect(unconfirmed(page)).toBeVisible();
    await expect(objective(page)).toHaveValue("Later input belongs to the separate recovery writer\n");
    expect(applies(service)).toHaveLength(1);
  } catch (reason) { primaryFailure={reason}; }
  finally {
    service.invoke=original;release();
    await finish(page,info,service,[],final,observations,[async()=>{if(held)await completed;}],primaryFailure);
  }
});

test("a failed recovery fork pointer keeps the earlier Brief UNKNOWN and sends nothing",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","studio.sqlite3"),desktopInvokeFixture:true});
  const original=service.invoke.bind(service);
  let final:Project|undefined;
  let primaryFailure:ScenarioFailure|undefined;
  const observations:Record<string,unknown>={};
  try {
    const initial=(await service.fixtureBootstrap()).project;
    await service.attachFixturePage(page,initial.id);await page.goto("/");await openBrief(page);
    let armed=true;
    service.invoke=async<T=unknown>(command:string,args:Record<string,unknown>={}):Promise<T>=>{
      const result=await original<T>(command,args);
      if(command==="apply_change"&&(args.request as ApplyArgs["request"]|undefined)?.change.type==="set_brief"&&armed) {
        armed=false;throw new Error("The original Brief service reply was lost after its real SQLite commit");
      }
      return result;
    };
    await objective(page).fill("Original uncertain Brief value");
    await button(page,"Commit brief").click();
    await expect(unconfirmed(page)).toContainText("The original Brief service reply was lost after its real SQLite commit");
    await objective(page).fill("Later raw input survives recovery pointer failure");
    await page.reload();await openBrief(page);
    await button(page,"Read saved Brief and history").click();
    await expect(page.getByRole("region",{name:"Saved Brief outcome review",exact:true})).toBeVisible();
    const before=await ownRecovery(page,initial);
    await page.evaluate(()=>{
      const original=Storage.prototype.setItem;
      Storage.prototype.setItem=function(key:string,value:string) {
        if(this===window.sessionStorage&&key.startsWith("motionwright.brief-session.v1:"))
          throw new Error("Controlled reviewed-writer pointer custody failure");
        return original.call(this,key,value);
      };
    });
    await button(page,"Keep earlier attempt and continue editing").click();
    await expect(unconfirmed(page)).toBeVisible();
    await expect(page.getByRole("alert",{name:"Brief draft backup warning",exact:true})).toContainText("pointer custody failure");
    await expect(button(page,"Commit brief")).toBeDisabled();
    await expect(objective(page)).toHaveValue("Later raw input survives recovery pointer failure");
    const after=await ownRecovery(page,initial);
    expect(after.writer).toBe(before.writer);
    expect(after.value.draft?.pending).toEqual(before.value.draft?.pending);
    expect(after.value.draft?.pending?.outcome).toBe("UNKNOWN");
    expect(applies(service)).toHaveLength(1);
    final=await service.project(initial.id);
    expect(final.revision).toBe(initial.revision+1);
    expect(final.brief.objective).toBe("Original uncertain Brief value");
    const journal=await service.history(initial.id,initial.revision);
    expect(journal).toHaveLength(1);
    observations.originalUnknown=after;
    observations.savedProject=final;observations.journal=journal;
  } catch (reason) { primaryFailure={reason}; }
  finally {
    service.invoke=original;
    await finish(page,info,service,[],final,observations,[],primaryFailure);
  }
});
