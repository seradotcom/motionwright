import { writeFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import type { Project } from "../src/types";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A real source-built StudioService runner is required. No simulated persistence fallback.");
test.setTimeout(120_000);

const button = (page: Page, label: string) => page.getByRole("button",{name:label,exact:true});
const objective = (page: Page) => page.getByLabel("Production objective",{exact:true});
async function openBrief(page: Page) {
  await button(page,"Brief").click();
  await expect(page.getByRole("heading",{name:"Project brief",exact:true})).toBeVisible();
}
function applies(service: ScopedChangeService) {
  return service.transcript.filter(item=>item.direction==="request" && "command" in item.payload
    && item.payload.command==="apply_change");
}

test("lost brief session writer pointer stops service grant; explicit Restore saves once and survives reopen",
  async ({page}, testInfo) => {
  const service = await ScopedChangeService.create({
    databasePath:testInfo.outputPath("service","studio.sqlite3"),desktopInvokeFixture:true,
  });
  let final: Project | undefined;
  try {
    const initial=(await service.fixtureBootstrap()).project;
    await service.attachFixturePage(page,initial.id); await page.goto("/");
    await openBrief(page);
    const raw="Original author direction kept while browser storage pointer silently fails";
    await page.evaluate(() => {
      const original=Storage.prototype.setItem;
      Storage.prototype.setItem=function(key:string,value:string) {
        if(this===window.sessionStorage && key.startsWith("motionwright.brief-session.v1:"))
          return; // This is a browser-only storage fault, NOT a simulated project save.
        return original.call(this,key,value);
      };
    });
    await objective(page).fill(raw);
    await expect(page.getByRole("alert",{name:"Brief draft backup warning",exact:true}))
      .toContainText("writer pointer was not confirmed");
    await expect(button(page,"Commit brief")).toBeDisabled();
    await expect(objective(page)).toHaveValue(raw);
    const found=await page.evaluate((expected:string) => {
      for (let index=0;index<window.localStorage.length;index++) {
        const key=window.localStorage.key(index);
        if(!key?.startsWith("motionwright.brief-draft.v1:")) continue;
        const copy=window.localStorage.getItem(key);
        if(!copy) continue;
        const value=JSON.parse(copy) as {
          sessionId:string; draft: null | {fields:{objective:{raw:string}}}
        };
        if(value.draft?.fields.objective.raw===expected)
          return {writer:value.sessionId,key,raw:copy};
      }
      return null;
    },raw);
    expect(found).not.toBeNull();
    if(!found)throw new Error("The durable brief authoring bytes were not recoverable.");
    expect(await service.project(initial.id)).toEqual(initial);
    expect(await service.history(initial.id,initial.revision)).toEqual([]);
    expect(applies(service)).toHaveLength(0);

    await page.reload(); await openBrief(page);
    // The previous writer pointer was never verified. Do NOT silently adopt a
    // copy from another browser window; require the explicit user Restore.
    await expect(objective(page)).not.toHaveValue(raw);
    const region=page.getByRole("region",{name:"Brief draft recovery",exact:true});
    await expect(region).toBeVisible();
    await region.locator("summary").click();
    await button(page,"Restore brief draft "+found.writer).click();
    await expect(objective(page)).toHaveValue(raw);
    await expect(button(page,"Commit brief")).toBeEnabled();
    expect(applies(service)).toHaveLength(0);
    await button(page,"Commit brief").click();
    await expect(page.locator(".revision-chip").first()).toHaveText("r"+(initial.revision+1));
    final=await service.project(initial.id);
    expect(final.brief.objective).toBe(raw);
    expect(final.title).toBe(initial.title);
    expect(final.brief.claims).toEqual(initial.brief.claims);
    const journal=await service.history(initial.id,initial.revision);
    expect(journal).toHaveLength(1);
    expect(journal[0].change.type).toBe("set_brief");
    expect(applies(service)).toHaveLength(1);
    await service.close(); await service.start(); await page.reload();
    await openBrief(page);
    await expect(objective(page)).toHaveValue(raw);
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id,initial.revision)).toEqual(journal);
    expect(applies(service)).toHaveLength(1);
    const evidencePath=testInfo.outputPath("brief-writer-admission-evidence.json");
    await writeFile(evidencePath,JSON.stringify({
      source:"real StudioService + SQLite + DOM + actual browser recovery bytes",
      exclusion:["installed Tauri","native Film frame pixels","human approval"],
      pointerWrite:"silently dropped, tested readback",
      writerId:found.writer,
      savedRevision:final.revision,
      changeCount:1,projectScope:final.id,
      previousProject:initial,
      finalProject:final,
      exactJournal:journal,
    },null,2));
    await testInfo.attach("brief-writer-admission-evidence",{path:evidencePath,contentType:"application/json"});
  } finally {
    await service.finalizeClosedStore(testInfo.outputPath("closed-store"),
      final,[],[()=>page.close()]);
  }
});
