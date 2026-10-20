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

test("a real saved partial cut excludes an editable Program scene until an explicit full-sequence revision", async ({page}, info) => {
  const service = await ScopedChangeService.create({
    databasePath:info.outputPath("studio","cut-disclosure.sqlite3"),desktopInvokeFixture:true,
  });
  let final: Project | undefined, projectId: string | null = null;
  const evidence: {label:string;revision:number;project:Project}[] = [];
  try {
    const initial = (await service.fixtureBootstrap()).project;
    projectId = initial.id; final = initial;
    const originalScene = initial.scenes.find(scene => scene.renderer === "motion-canvas"
      && scene.nodes.some(node => node.kind === "text"));
    if (!originalScene) throw new Error("A real source text scene is required");
    const textNode = originalScene.nodes.find(node => node.kind === "text")!;
    const profile = initial.deliverables[0];
    if (!profile) throw new Error("A real output profile is required");
    await service.attachFixturePage(page,initial.id); await page.goto("/");
    await button(page,"Canvas").click();
    await button(page,"Add scene").click();
    await page.getByLabel("New scene name",{exact:true}).fill("A3 explicitly included cut scene");
    await page.locator(".add-scene-form").getByRole("button",{name:"Add",exact:true}).click();
    await revision(page,initial.revision+1);
    const twoScene = await service.project(initial.id);
    final = twoScene;
    evidence.push({label:"new scene authored through Studio",revision:twoScene.revision,project:twoScene});
    expect(twoScene.scenes.length).toBeGreaterThan(initial.scenes.length);
    const originalIndex = twoScene.scenes.findIndex(scene => scene.id === originalScene.id);
    expect(originalIndex).toBeGreaterThanOrEqual(0);

    await button(page,"Deliver").click();
    await button(page,"Customize cut").click();
    const scope = page.getByRole("region",{name:"Variant content",exact:true});
    const include = scope.getByRole("checkbox",{name:"Include",exact:true});
    await expect(include).toHaveCount(twoScene.scenes.length);
    await include.nth(originalIndex).uncheck();
    await button(page,"Save profile").click();
    await revision(page,twoScene.revision+1);
    let saved = await service.project(initial.id);
    final = saved;
    evidence.push({label:"real partial output cut excludes original scene",revision:saved.revision,project:saved});
    const savedProfile = saved.deliverables.find(item => item.id === profile.id);
    expect(savedProfile).toBeDefined();
    expect(savedProfile!.included_scene_ids.length).toBeGreaterThan(0);
    expect(savedProfile!.included_scene_ids).not.toContain(originalScene.id);
    expect(savedProfile!.included_scene_ids).toContain(twoScene.scenes.at(-1)!.id);
    await button(page,"Canvas").click();
    await page.locator(".tree-row").filter({has:page.getByText(originalScene.name,{exact:true})}).click();
    await button(page,"Timeline").click();
    const monitor = page.getByLabel("Saved Canvas editorial design monitor",{exact:true});
    await expect(monitor).toHaveAttribute("data-output-cut-scope","excluded");
    await expect(monitor.getByRole("status")).toContainText("SCENE EXCLUDED FROM SELECTED OUTPUT CUT");
    await expect(monitor).toContainText("This editable source scene will not be exported in this profile");
    await expect(readProgramText(page,textNode.id)).toContainText(textNode.text ?? "");
    await expect(page.getByRole("button",{name:"Review final AV"})).toHaveCount(0);
    const applyCount = applied(service).length;
    expect(applyCount).toBe(2);
    await service.close(); await service.start(); await page.reload();
    await revision(page,saved.revision);
    await button(page,"Timeline").click();
    await expect(monitor).toHaveAttribute("data-output-cut-scope","excluded");
    await expect(readProgramText(page,textNode.id)).toContainText(textNode.text ?? "");
    expect(await service.project(initial.id)).toEqual(saved);
    expect(applied(service)).toHaveLength(applyCount);

    await button(page,"Deliver").click();
    await button(page,"Use full sequence").click();
    await button(page,"Save profile").click();
    await revision(page,saved.revision+1);
    saved=await service.project(initial.id);
    final=saved;
    evidence.push({label:"explicit return to full output sequence",revision:saved.revision,project:saved});
    expect(saved.deliverables.find(item => item.id === profile.id)?.included_scene_ids).toEqual([]);
    await button(page,"Timeline").click();
    await expect(monitor).toHaveAttribute("data-output-cut-scope","full");
    await expect(monitor.getByRole("status")).toHaveCount(0);
    await expect(readProgramText(page,textNode.id)).toContainText(textNode.text ?? "");
    const journal=await service.history(initial.id,initial.revision);
    expect(journal.map(item => item.change.type)).toEqual([
      "add_scene","upsert_deliverable","upsert_deliverable",
    ]);
    expect(applied(service)).toHaveLength(applyCount+1);
    expect(await service.project(initial.id)).toEqual(saved);
    const proof=info.outputPath("program-cut-source-oracle.json");
    await writeFile(proof,JSON.stringify({
      source:"real authored Studio UI -> Rust StudioService -> SQLite -> Project + journal + browser DOM",
      exact_profile:profile.id,editable_scene:originalScene.id,
      excludes:["native Film frames","actual exported AV","installed Tauri","human acceptance"],
      checkpoints:evidence,serialized_cut_sequence:["full","excluded","full"],journal,
    },null,2));
    await info.attach("program-cut-source-oracle",{path:proof,contentType:"application/json"});
  } finally {
    if(projectId){try{final=await service.project(projectId)}catch{/* finalization reports */}}
    await service.finalizeClosedStore(info.outputPath("program-cut-closed-store"),
      final,[],[()=>page.close()]);
  }
});

test("real SQLite profile cut with stale localized text shows only the excluded original source after reopen", async ({page},info) => {
  const service = await ScopedChangeService.create({
    databasePath:info.outputPath("studio","excluded-locale.sqlite3"),desktopInvokeFixture:true,
  });
  let final: Project | undefined, projectId: string | null = null;
  const peers: Awaited<ReturnType<typeof service.peer>>[]=[];
  try {
    const initial=(await service.fixtureBootstrap()).project;
    final=initial;projectId=initial.id;
    const scene=initial.scenes.find(item=>item.renderer==="motion-canvas" && item.nodes.some(node=>node.kind==="text"));
    if(!scene) throw new Error("A saved source Canvas text scene is required");
    const node=scene.nodes.find(item=>item.kind==="text")!;
    const profile=initial.deliverables[0];
    await service.attachFixturePage(page,initial.id);await page.goto("/");
    await button(page,"Canvas").click();
    await button(page,"Add scene").click();
    await page.getByLabel("New scene name",{exact:true}).fill("Other saved output scene");
    await page.locator(".add-scene-form").getByRole("button",{name:"Add",exact:true}).click();
    await revision(page,initial.revision+1);
    const expanded=await service.project(initial.id);
    final=expanded;
    const other=expanded.scenes.find(item=>!initial.scenes.some(old=>old.id===item.id));
    if(!other)throw new Error("A real second output scene was not created");
    const peer=await service.peer();peers.push(peer);
    const current=await peer.project(initial.id);
    const updated=await peer.apply(current,{
      type:"upsert_deliverable",
      profile:{...current.deliverables.find(item=>item.id===profile.id)!,
        included_scene_ids:[other.id],
        text_overrides:{[node.id]:"Stale localized text for an excluded scene"},
      },
    });
    final=updated;
    expect(updated.revision).toBe(expanded.revision+1);
    expect(updated.deliverables.find(item=>item.id===profile.id)?.included_scene_ids).toEqual([other.id]);
    expect(updated.deliverables.find(item=>item.id===profile.id)?.text_overrides[node.id])
      .toBe("Stale localized text for an excluded scene");
    await page.reload();await revision(page,updated.revision);
    await button(page,"Canvas").click();
    await page.locator(".tree-row").filter({has:page.getByText(scene.name,{exact:true})}).click();
    await button(page,"Timeline").click();
    const monitor=page.getByLabel("Saved Canvas editorial design monitor",{exact:true});
    await expect(monitor).toHaveAttribute("data-output-cut-scope","excluded");
    await expect(monitor.getByRole("status")).toContainText("SCENE EXCLUDED FROM SELECTED OUTPUT CUT");
    await expect(readProgramText(page,node.id)).toHaveAttribute("data-text-origin","source");
    await expect(readProgramText(page,node.id)).toContainText(node.text ?? "");
    await expect(readProgramText(page,node.id)).not.toContainText("Stale localized text for an excluded scene");
    const canonical=await service.project(initial.id);
    const journal=await service.history(initial.id,initial.revision);
    expect(journal.map(entry=>entry.change.type)).toEqual(["add_scene","upsert_deliverable"]);
    const applyCount=applied(service).length;
    expect(applyCount).toBe(1);
    await service.close();await service.start();await page.reload();await revision(page,canonical.revision);
    await button(page,"Timeline").click();
    await expect(monitor).toHaveAttribute("data-output-cut-scope","excluded");
    await expect(readProgramText(page,node.id)).toHaveAttribute("data-text-origin","source");
    await expect(readProgramText(page,node.id)).toContainText(node.text ?? "");
    expect(await service.project(initial.id)).toEqual(canonical);
    expect(await service.history(initial.id,initial.revision)).toEqual(journal);
    expect(applied(service)).toHaveLength(applyCount);
    final=canonical;
  }finally{
    if(projectId){try{final=await service.project(projectId)}catch{/* preserve original failure */}}
    await service.finalizeClosedStore(info.outputPath("excluded-locale-closed-store"),
      final,peers,[()=>page.close()]);
  }
});
