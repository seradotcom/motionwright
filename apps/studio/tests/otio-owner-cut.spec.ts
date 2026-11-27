import { expect,test,type Page } from "./support/selectedSyntheticProject";
import { fixtureBootstrap } from "../src/fixture";
import { reviewableOtioProject,sampleOtioPreview } from "../src/otioCutReviewTestFixtures";

const project=reviewableOtioProject();
const proposal=sampleOtioPreview(project);

async function openStation(page:Page){
  await page.goto("/");
  await page.getByRole("button",{name:"Deliver",exact:true}).click();
  await expect(page.getByRole("region",{name:"OTIO owner cut review"})).toBeVisible();
}

test("browser demo cannot admit a forged cut or mutate the active project",async({page})=>{
  await openStation(page);
  const source=page.getByLabel("OTIO imported cut preview JSON");
  await source.fill(JSON.stringify(proposal));
  await page.getByRole("button",{name:"Review bounded changes"}).click();
  await expect(page.locator(".otio-cut-review [role=alert]")).toContainText("Resolve existing creative review");
  await expect(page.getByRole("button",{name:/Commit reviewed OTIO cut/})).toHaveCount(0);
  await expect(page.getByText(/Browser demo: proposals can be inspected, never committed/)).toBeVisible();
});

test("owner reviews explicit native changes and accepts one project transaction",async({page},info)=>{
  const boot=structuredClone(fixtureBootstrap);
  boot.project=structuredClone(project);
  boot.native_sdk.mode="tauri";
  await page.addInitScript(({boot})=>{
    const runtime=window as unknown as {
      __TAURI_INTERNALS__:{invoke:(name:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __OTIO_ACCEPTANCE_CALLS__:Array<{name:string;args?:Record<string,unknown>}>;
    };
    let snapshot=structuredClone(boot.project);
    const calls:Array<{name:string;args?:Record<string,unknown>}>=[];
    runtime.__OTIO_ACCEPTANCE_CALLS__=calls;
    runtime.__TAURI_INTERNALS__={invoke:async(name,args)=>{
      calls.push({name,args});
      if (name === "bootstrap" || name === "project_projection_head" || name === "open_project") {
        return window.__SYNTHETIC_PROJECT_READ__(name, args, {...boot,project:structuredClone(snapshot)});
      }
      if(name==="issue_effect_grant")return {token:"test-only-non-authoritative-grant"};
      if(name==="apply_change"){
        const request=args?.request as {
          project_id:string;generation:string;revision:number;
          change:{type:string;review:{preview:{
            proposed_scene_order:string[];
            native_change_proposals:Array<{type:string;scene_id:string;duration?:{num:string;den:string}}>;
          }}}
        };
        if(request.project_id!==snapshot.id ||
           request.generation!==snapshot.generation || request.revision!==snapshot.revision)
          throw new Error("Synthetic source revision changed");
        if(request.change.type!=="apply_otio_owner_cut_review")
          throw new Error("Unadmitted change type");
        const p=request.change.review.preview;
        snapshot.scenes=p.proposed_scene_order.map(id=>snapshot.scenes.find(scene=>scene.id===id)!);
        for(const edit of p.native_change_proposals){
          if(edit.type==="set_scene_duration"){
            const scene=snapshot.scenes.find(scene=>scene.id===edit.scene_id)!;
            scene.duration=edit.duration!;
          }
        }
        snapshot.revision++;
        return structuredClone(snapshot);
      }
      throw new Error("Synthetic desktop did not implement "+name);
    }};
  },{boot});
  await openStation(page);
  const rev=await page.locator(".revision-chip").innerText();
  await page.getByLabel("OTIO imported cut preview JSON").fill(JSON.stringify(proposal));
  const change=page.getByRole("button",{name:/Commit reviewed OTIO cut/});
  await expect(change).toHaveCount(0);
  await page.getByRole("button",{name:"Review bounded changes"}).click();
  await expect(page.getByRole("region",{name:"OTIO proposed revision"})).toBeVisible();
  await expect(page.getByLabel("Exact supported OTIO edits")).toContainText("End-trim");
  await expect(page.locator(".revision-chip")).toHaveText(rev);
  await expect(change).toBeDisabled();
  await page.getByLabel("OTIO reviewer declaration").fill("Owner test-only identity");
  await page.getByLabel("OTIO review rationale").fill("Accept original reordering and tail trim, never NLE media or effects");
  const acceptedLosses = page.getByLabel(/I reviewed and accept the listed property losses/);
  await expect(acceptedLosses).toBeVisible();
  expect((await acceptedLosses.boundingBox())?.width).toBeLessThanOrEqual(44);
  await acceptedLosses.check();
  await expect(change).toBeDisabled(); // Existing absolute markers and voice are still present.
  await page.getByLabel(/I acknowledge existing audio, transcript or marker timestamps/).check();
  await expect(change).toBeEnabled();
  const before=await page.evaluate(()=>(window as unknown as {__OTIO_ACCEPTANCE_CALLS__:Array<{name:string}>}).__OTIO_ACCEPTANCE_CALLS__);
  expect(before.filter(call=>call.name==="apply_change")).toHaveLength(0);
  await page.screenshot({path:info.outputPath("otio-owner-review-dark.png"),fullPage:true});
  await change.click();
  await expect(page.locator(".revision-chip")).not.toHaveText(rev);
  const calls=await page.evaluate(()=>(window as unknown as {
    __OTIO_ACCEPTANCE_CALLS__:Array<{name:string;args?:Record<string,unknown>}>
  }).__OTIO_ACCEPTANCE_CALLS__);
  expect(calls.filter(call=>call.name==="apply_change")).toHaveLength(1);
  expect(calls.filter(call=>call.name==="issue_effect_grant")).toHaveLength(1);
  expect(calls.some(call=>/render|provider|payment|relink|publish/.test(call.name))).toBe(false);
});

test("explicit loss confirmation does not permit a tampered OTIO scene extension",async({page})=>{
  await openStation(page);
  const forged={...proposal,native_change_proposals:[
    proposal.native_change_proposals[0],
    {type:"set_scene_duration",scene_id:project.scenes[2].id,duration:{num:"20",den:"1"}},
  ]};
  await page.getByLabel("OTIO imported cut preview JSON").fill(JSON.stringify(forged));
  await page.getByRole("button",{name:"Review bounded changes"}).click();
  await expect(page.locator(".otio-cut-review [role=alert]")).toBeVisible();
  await expect(page.getByRole("button",{name:/Commit reviewed OTIO cut/})).toHaveCount(0);
});

test("runtime checks and folder choices preserve the scoped OTIO inspection rules", async ({page}) => {
  const boot = {...structuredClone(fixtureBootstrap), project: structuredClone(project)};
  boot.native_sdk.mode = "tauri";
  await page.addInitScript(({boot}) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: {invoke: (name: string, args?: Record<string, unknown>) => Promise<unknown>};
      __OTIO_INSPECTIONS__: number;
      __OTIO_FOLDER_CHOICES__: number;
      __OTIO_RUNTIME_CHECKS__: number;
      __OTIO_PREVIEW_GRANTS__: number;
    };
    runtime.__OTIO_INSPECTIONS__ = 0;
    runtime.__OTIO_FOLDER_CHOICES__ = 0;
    runtime.__OTIO_RUNTIME_CHECKS__ = 0;
    runtime.__OTIO_PREVIEW_GRANTS__ = 0;
    runtime.__TAURI_INTERNALS__ = {invoke: async (name, args) => {
      if (name === "bootstrap" || name === "project_projection_head" || name === "open_project") {
        return window.__SYNTHETIC_PROJECT_READ__(name, args, boot);
      }
      if (name === "check_otio_media_runtime") {
        runtime.__OTIO_RUNTIME_CHECKS__++;
        if (runtime.__OTIO_RUNTIME_CHECKS__ === 1) {
          throw "OTIO bridge preflight unavailable: Install protected Python and ffprobe.";
        }
        return null;
      }
      if (name === "choose_bundle_export_destination") {
        const request = args?.request as {project_id: string; generation: string; revision: number};
        if (args?.kind !== "otio_media" || request.project_id !== boot.project.id
            || request.generation !== boot.project.generation || request.revision !== boot.project.revision) {
          throw new Error("Expected the current project and OTIO destination mode");
        }
        return "/Users/owner/Documents/Edición final.otio-bundle";
      }
      if (name === "choose_otio_media_bundle_folder") {
        const request = args?.request as {project_id: string; generation: string; revision: number};
        if (request.project_id !== boot.project.id || request.generation !== boot.project.generation
            || request.revision !== boot.project.revision) {
          throw new Error("Folder choice used a different project revision");
        }
        runtime.__OTIO_FOLDER_CHOICES__++;
        return runtime.__OTIO_FOLDER_CHOICES__ === 1 ? null : "E:/Original cuts/Edición final";
      }
      if (name === "issue_effect_grant") {
        runtime.__OTIO_PREVIEW_GRANTS__++;
        return {token: "synthetic-grant"};
      }
      if (name === "preview_otio_media_cut") {
        runtime.__OTIO_INSPECTIONS__++;
        if (runtime.__OTIO_INSPECTIONS__ === 1) {
          throw "OTIO bridge preflight unavailable: Python 3 or ffprobe is missing.";
        }
        throw new Error("The original inspection outcome is unknown.");
      }
      throw new Error("Synthetic desktop did not implement " + name);
    }};
  }, {boot});
  await openStation(page);
  const check = page.getByRole("button", {name: "Check OTIO runtime files"});
  await check.click();
  await expect(page.getByRole("alert").filter({hasText: "Install protected Python and ffprobe."})).toBeVisible();
  await check.click();
  await expect(page.getByText("Protected Python and ffprobe files passed the runtime check.")).toBeVisible();
  expect(await page.evaluate(() => Reflect.get(window, "__OTIO_INSPECTIONS__"))).toBe(0);
  expect(await page.evaluate(() => Reflect.get(window, "__OTIO_PREVIEW_GRANTS__"))).toBe(0);
  await page.getByText("Create the original media-bound OTIO bundle", {exact: true}).click();
  await page.getByRole("button", {name: "Choose OTIO export destination", exact: true}).click();
  await expect(page.getByLabel("New OTIO media bundle destination")).toHaveValue("/Users/owner/Documents/Edición final.otio-bundle");
  await expect(page.getByRole("button", {name: "Export original media-bound OTIO bundle"})).toBeDisabled();
  expect(await page.evaluate(() => Reflect.get(window, "__OTIO_PREVIEW_GRANTS__"))).toBe(0);
  const folder = page.getByLabel("Original OTIO media bundle folder");
  await folder.fill("/owner/original-bundle");
  const chooser = page.getByRole("button", {name: "Choose OTIO bundle folder"});
  await chooser.click();
  await expect(chooser).toBeEnabled();
  await expect(folder).toHaveValue("/owner/original-bundle");
  await chooser.click();
  await expect(folder).toHaveValue("E:/Original cuts/Edición final");
  expect(await page.evaluate(() => Reflect.get(window, "__OTIO_PREVIEW_GRANTS__"))).toBe(0);
  const inspect = page.getByRole("button", {name: "Preview edited OTIO with verified media"});
  const error = page.locator(".otio-cut-review [role=alert]");
  await inspect.click();
  await expect(error).toContainText("Python 3 or ffprobe is missing.");
  await expect(inspect).toBeEnabled();
  await inspect.click();
  await expect(error).toContainText("The original inspection outcome is unknown.");
  await expect(inspect).toBeDisabled();
  expect(await page.evaluate(() => Reflect.get(window, "__OTIO_INSPECTIONS__"))).toBe(2);
});
