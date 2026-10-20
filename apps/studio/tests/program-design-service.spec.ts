import { writeFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";
import type { Project } from "../src/types";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A selected studio_review_session Rust binary and real SQLite are required.");
test.setTimeout(120_000);
const button = (page: Page, text: string) => page.getByRole("button",{name:text,exact:true});
const revision = async (page: Page, value: number) =>
  expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+value);
const program = (page: Page) => page.getByRole("img",
  {name:"Program Canvas editorial study: not native output",exact:true});
const applied = (service: ScopedChangeService) => service.transcript.filter(entry =>
  entry.direction === "request" && "command" in entry.payload
    && entry.payload.command === "apply_change");
const readProgramText = (page: Page, id: string) =>
  program(page).locator('[data-canvas-node-id="'+id+'"] text');

test("Timeline Program renders actual saved Canvas text, style and selected locale through revisions and restart",
  async ({page}, info) => {
  const service = await ScopedChangeService.create({
    databasePath:info.outputPath("studio","source-project.sqlite3"),desktopInvokeFixture:true,
  });
  let final: Project | undefined;
  let projectId: string | null = null;
  const checkpoints: {revision:number;label:string;project:Project}[]=[];
  try {
    const initial=(await service.fixtureBootstrap()).project;
    projectId=initial.id;
    final=initial;
    const scene=initial.scenes.find(item=>item.renderer==="motion-canvas" &&
      item.nodes.some(node=>node.kind==="text"));
    if(!scene) throw new Error("The real persisted fixture needs a drawable Motion Canvas scene.");
    const node=scene.nodes.find(item=>item.kind==="text")!;
    if(!node.text) throw new Error("The saved source text must not be empty.");
    const original=initial.deliverables[0];
    await service.attachFixturePage(page,initial.id); await page.goto("/");
    await button(page,"Timeline").click();
    await expect(program(page)).toBeVisible();
    await expect(readProgramText(page,node.id)).toContainText(node.text);
    await expect(page.getByLabel("Semantic composition representation")).toHaveCount(0);
    await expect(page.getByLabel("Saved Canvas editorial design monitor")).toContainText("Native frames, crop, fonts and final AV require separate verification");
    await expect(page.getByRole("button",{name:"Review final AV"})).toHaveCount(0);

    await button(page,"Canvas").click();
    await page.locator(".tree-row").filter({has:page.getByText(scene.name,{exact:true})}).click();
    await page.locator(".canvas-tree-row").filter({has:page.getByText(node.name,{exact:true})}).click();
    const corrected="Saved Canvas timeline source, human correction r1";
    await page.getByLabel("Canvas text",{exact:true}).fill(corrected);
    await button(page,"Commit text").click();
    await revision(page,initial.revision+1);
    let saved=await service.project(initial.id);
    checkpoints.push({revision:saved.revision,label:"authored original text",project:saved});
    expect(saved.scenes.find(item=>item.id===scene.id)?.nodes.find(item=>item.id===node.id)?.text).toBe(corrected);

    await page.getByLabel("Canvas fill",{exact:true}).fill("#F7EAD7");
    await button(page,"Commit style").click();
    await revision(page,initial.revision+2);
    saved=await service.project(initial.id);
    checkpoints.push({revision:saved.revision,label:"authored object paint",project:saved});
    expect(saved.scenes.find(item=>item.id===scene.id)?.nodes.find(item=>item.id===node.id)?.style.fill).toBe("#F7EAD7");

    await button(page,"Timeline").click();
    const sourceSvg=readProgramText(page,node.id);
    await expect(sourceSvg).toContainText(corrected);
    await expect(sourceSvg).toHaveAttribute("fill","#F7EAD7");
    await expect(sourceSvg).toHaveAttribute("data-text-origin","source");
    await expect(page.getByLabel("Saved Canvas editorial design monitor"))
      .toHaveAttribute("data-source-revision",String(saved.revision));

    await button(page,"Deliver").click();
    await button(page,"Derive selected").click();
    await revision(page,initial.revision+3);
    await page.getByLabel("Language / locale",{exact:true}).fill("es-MX");
    const locale=page.getByRole("region",{name:"Localized text",exact:true});
    await expect(locale).toBeVisible();
    const editors=locale.locator(".localized-text-ledger input");
    expect(await editors.count()).toBeGreaterThan(0);
    for(let i=0;i<await editors.count();i++) await editors.nth(i).fill("Variante visual localizada "+i);
    await button(page,"Save profile").click();
    await revision(page,initial.revision+4);
    saved=await service.project(initial.id);
    final=saved;
    checkpoints.push({revision:saved.revision,label:"persistent Spanish output variant",project:saved});
    const variant=saved.deliverables.find(profile=>profile.parent_profile_id===original.id);
    expect(variant).toBeDefined();
    if(!variant) throw new Error("The saved locale derivative is missing.");
    const localized=variant.text_overrides[node.id];
    expect(localized).toMatch(/^Variante visual localizada/);

    const selectProfile=page.getByRole("combobox",{name:"Editor preview timebase",exact:true});
    await selectProfile.selectOption(variant.id);
    await button(page,"Timeline").click();
    await expect(readProgramText(page,node.id)).toHaveAttribute("data-text-origin","profile");
    await expect(readProgramText(page,node.id)).toContainText(localized);
    await expect(readProgramText(page,node.id)).toHaveAttribute("fill","#F7EAD7");
    const snapshot=await service.project(initial.id);
    const count=applied(service).length;
    await selectProfile.selectOption(original.id);
    await expect(readProgramText(page,node.id)).toContainText(corrected);
    await expect(readProgramText(page,node.id)).toHaveAttribute("data-text-origin","source");
    expect(await service.project(initial.id)).toEqual(snapshot);
    expect(applied(service)).toHaveLength(count);

    const journal=await service.history(initial.id,initial.revision);
    expect(journal.map(item=>item.change.type)).toEqual([
      "update_canvas_text","update_canvas_style","upsert_deliverable","upsert_deliverable",
    ]);
    await service.close(); await service.start(); await page.reload();
    await revision(page,final.revision);
    await selectProfile.selectOption(variant.id);
    await button(page,"Timeline").click();
    await expect(readProgramText(page,node.id)).toContainText(localized);
    await expect(readProgramText(page,node.id)).toHaveAttribute("data-text-origin","profile");
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id,initial.revision)).toEqual(journal);
    expect(applied(service)).toHaveLength(count);
    const proof=info.outputPath("program-design-editorial-source-evidence.json");
    await writeFile(proof,JSON.stringify({
      source:"saved Project + actual DOM/SVG + SQLite + Rust StudioService",
      excludes:["native Film pixels","installed font metrics","human approval"],
      base_revision:initial.revision,final_revision:final.revision,
      source_node_id:node.id,profile_id:variant.id,
      authored_text:corrected,localized_text:localized,
      journal_types:journal.map(item=>item.change.type),checkpoints,
    },null,2));
    await info.attach("program-design-editorial-source-evidence",
      {path:proof,contentType:"application/json"});
  } finally {
    // Preserve the primary assertion error and still supply a complete
    // real final Project oracle for the mandatory closed-store capture.
    if (projectId) {
      try { final=await service.project(projectId); } catch { /* finalization reports any inaccessible store */ }
    }
    await service.finalizeClosedStore(info.outputPath("program-closed-store"),
      final,[],[()=>page.close()]);
  }
});
