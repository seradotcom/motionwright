import { expect,test,type Page } from "@playwright/test";
import {fixtureBootstrap} from "../src/fixture";
import type { Page as RecipeFixturePage } from "@playwright/test";
import { applyChange as applySyntheticFixtureChange } from "../src/api";
import type { Change as RecipeFixtureChange, Project as RecipeFixtureProject } from "../src/types";

interface RecipeFixtureRequest {
  effect?: string; project_id: string; generation: string; revision: number;
  effect_grant: string; change?: RecipeFixtureChange;
}

// Explicit synthetic authoring fixture. The Node-side API uses its existing
// simulator branch, not SQLite, Tauri, native rendering or owner authority.
// The real UI still issues ProjectEdit/apply_change and observes the returned
// Project revision; no saved recipe is injected directly into the component.
async function installRecipeAuthoringFixture(page: RecipeFixturePage) {
  await page.exposeFunction("__MW_RECIPE_FIXTURE_APPLY__", async (source: RecipeFixtureProject, request: RecipeFixtureRequest) => {
    if (request.project_id !== source.id || request.generation !== source.generation || request.revision !== source.revision
        || request.effect_grant !== "synthetic-recipe-only" || request.change?.type !== "set_delivery_recipe") {
      throw new Error("Synthetic recipe fixture rejected a stale or unsupported authoring request.");
    }
    return applySyntheticFixtureChange(source, request.change);
  });
}

async function saveFixtureRecipe(page: RecipeFixturePage, group: "film" | "mix" = "film") {
  const revision = Number((await page.locator(".command-bar .revision-chip").innerText()).slice(1));
  const label = group === "film" ? "Save scene intent" : "Save original mix recipe";
  await page.getByRole("button", { name: label, exact: true }).click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + (revision + 1));
  await expect(page.getByRole("region", { name: (group === "film" ? "Scene intent" : "Original mix") + " draft review", exact: true }).getByRole("status").first())
    .toContainText("Showing saved recipe values at revision r" + (revision + 1));
  return revision + 1;
}


async function installSyntheticSource(page:Page,invalidHeader=false){
  const boot=structuredClone(fixtureBootstrap);
  boot.native_sdk.mode="tauri";
  const first=boot.project.scenes[0],second=boot.project.scenes[1];
  const profile=boot.project.deliverables[0];
  const receipt={
    project_resource:"project:"+boot.project.id,generation:boot.project.generation,
    revision:boot.project.revision,deliverable_id:profile.id,
    frame_rate:{num:30,den:1},
    segments:[{
      segment_id:"synthetic-original-pixel-contrast",
      scene_ids:[first.id,second.id],frame_count:450,
      plan_ref:"synthetic-only",fingerprint:"synthetic-only",
      job_ref:"synthetic-only",artifact:{},verification:{},
    }],
    preview:[{token:"synthetic-pixel-contrast-token",
      segment_id:"synthetic-original-pixel-contrast",
      scene_ids:[first.id,second.id],frame_count:450}],
  };
  await installRecipeAuthoringFixture(page);
  await page.addInitScript(({initialBoot,receipt,width,height,bad})=>{
    const runtime=window as unknown as {
      __TAURI_INTERNALS__:{invoke:(name:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __PIXEL_TEST_CALLS__:string[];
    };
    const calls:string[]=[];runtime.__PIXEL_TEST_CALLS__=calls;
    runtime.__TAURI_INTERNALS__={invoke:async(name,args)=>{
        // Authoring is counted separately from the native-only operations below.
        const recipeRequest = args?.request as RecipeFixtureRequest | undefined;
        if (name === "issue_effect_grant" && recipeRequest?.effect === "project_edit"
            && recipeRequest.project_id === initialBoot.project.id && recipeRequest.generation === initialBoot.project.generation
            && recipeRequest.revision === initialBoot.project.revision) return { token: "synthetic-recipe-only" };
        if (name === "apply_change" && recipeRequest?.change?.type === "set_delivery_recipe") {
          const apply = (window as unknown as { __MW_RECIPE_FIXTURE_APPLY__: (source: RecipeFixtureProject, request: RecipeFixtureRequest) => Promise<RecipeFixtureProject> }).__MW_RECIPE_FIXTURE_APPLY__;
          initialBoot.project = await apply(initialBoot.project, recipeRequest);
          receipt.revision = initialBoot.project.revision; // Future synthetic native reply uses the saved source.
          return structuredClone(initialBoot.project);
        }

      calls.push(name);
      if(name==="production_runtime_status")return{
        status:"ready",reason:null,expected_version:"1.0.0",
        observed_version:"1.0.0",version_compatible:true,
        executable_sha256:"a".repeat(64),connection_identity:"b".repeat(64),
        pinned_revision:"c".repeat(40),
      };
      if(name==="motion_canvas_preflight"){
        const scope=args?.request as {project_id:string;generation:string;revision:number;deliverable_id:string};
        return {project_resource:"project:"+scope.project_id,
          generation:scope.generation,revision:scope.revision,
          deliverable_id:scope.deliverable_id,verdict:"projection_ready",
          reason:null,segment_count:1,total_frames:60,
          segments:[{segment_id:"synthetic-film",scene_ids:[],frame_count:60}]};
      }
      if(name==="bootstrap")return structuredClone(initialBoot);
      if(name==="issue_effect_grant")return {token:"synthetic-unauthenticated-grant"};
      if(name==="render_motion_canvas")return structuredClone(receipt);
      if(name==="preview_native_frame"){
        const request=args?.request as {token:string};
        if(request.token!=="synthetic-pixel-contrast-token")throw new Error("Unexpected source token");
        const canvas=document.createElement("canvas");
        canvas.width=width;canvas.height=height;
        const context=canvas.getContext("2d");
        if(!context)throw new Error("Synthetic pixel canvas is unavailable");
        context.fillStyle="#000000";context.fillRect(0,0,width/2,height);
        context.fillStyle="#ffffff";context.fillRect(width/2,0,width/2,height);
        const encoded=canvas.toDataURL("image/png").split(",")[1];
        const result=Uint8Array.from(atob(encoded),char=>char.charCodeAt(0));
        if(bad)result[0]=0;
        return result;
      }
      throw new Error("Unexpected synthetic native request: "+name);
    }};
  },{initialBoot:boot,receipt,width:profile.width,height:profile.height,bad:invalidHeader});
  return {boot,first,second,profile};
}
async function syntheticNativeRender(page:Page,first:string,second:string){
  await page.goto("/");
  await page.getByRole("button",{name:"Deliver",exact:true}).click();
  await page.getByLabel(first+" narrative role").selectOption("hook");
  await page.getByLabel(first+" motion archetype").selectOption("statement");
  await page.getByLabel(second+" narrative role").selectOption("problem");
  await page.getByLabel(second+" motion archetype").selectOption("diagram_build");
  await saveFixtureRecipe(page);
  await page.getByRole("button",{name:"Check Film projection"}).click();
  await expect(page.getByLabel("First native production readiness")).toContainText("ELIGIBLE FOR AUTHORIZED DISPATCH");
  await page.getByRole("button",{name:"Render native segments"}).click();
  await expect(page.getByLabel("Native render evidence")).toContainText("NATIVE · PASS");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Pixel contrast",exact:true}).click();
}
test("browser demo without native source never fabricates visual metrics",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Pixel contrast",exact:true}).click();
  await expect(page.getByRole("heading",{name:"No current native frame grant"})).toBeVisible();
  await expect(page.getByLabel("Measured source-pixel contrast")).toHaveCount(0);
  await expect(page.getByRole("button",{name:"Measure source-pixel contrast"})).toHaveCount(0);
});
test("exact native PNG and two manually chosen opaque colors measure 21:1, never change the project",async({page},info)=>{
  const {first,second,boot,profile}=await installSyntheticSource(page);
  await syntheticNativeRender(page,first.name,second.name);
  const rev=await page.locator(".revision-chip").innerText();
  await page.getByRole("button",{name:"Read exact native source frame"}).click();
  await expect(page.getByRole("button",{name:"Choose foreground pixel point on native source frame"})).toBeVisible();
  await expect(page.getByText(/PNG SHA-256 [0-9a-f]{16}/)).toBeVisible();
  const measure=page.getByRole("button",{name:"Measure source-pixel contrast"});
  await expect(measure).toBeDisabled();
  await page.getByLabel("Foreground X percent").fill("25");
  await page.getByLabel("Foreground Y percent").fill("50");
  await page.getByLabel("Background X percent").fill("75");
  await page.getByLabel("Background Y percent").fill("50");
  await expect(measure).toBeEnabled();
  await measure.click();
  const result=page.getByLabel("Measured source-pixel contrast");
  await expect(result).toContainText("21.00");
  await expect(result).toContainText("Foreground rgb(0, 0, 0)");
  await expect(result).toContainText("Background rgb(255, 255, 255)");
  await expect(result).toContainText("not WCAG compliance");
  await expect(page.locator(".revision-chip")).toHaveText(rev);
  const calls=await page.evaluate(()=>(window as unknown as {__PIXEL_TEST_CALLS__:string[]}).__PIXEL_TEST_CALLS__);
  expect(calls.filter(command=>command==="preview_native_frame")).toHaveLength(1);
  expect(calls).not.toContain("apply_change");
  expect(calls).not.toContain("render_mlt_av_master");
  await page.screenshot({path:info.outputPath("native-source-contrast-dark.png"),fullPage:true});
  await page.getByLabel("Editor preview timebase").selectOption(boot.project.deliverables[1].id);
  await expect(page.getByRole("heading",{name:"No current native frame grant"})).toBeVisible();
  await expect(page.getByLabel("Measured source-pixel contrast")).toHaveCount(0);
  await page.getByLabel("Editor preview timebase").selectOption(profile.id);
  await expect(page.getByLabel("Measured source-pixel contrast")).toHaveCount(0);
});
test("tampered synthetic PNG is refused before any contrast metric exists",async({page})=>{
  const {first,second}=await installSyntheticSource(page,true);
  await syntheticNativeRender(page,first.name,second.name);
  await page.getByRole("button",{name:"Read exact native source frame"}).click();
  // Existing desktop API rejects a corrupted PNG signature before the downstream IHDR check.
  await expect(page.locator(".native-contrast [role=alert]")).toContainText("Native preview did not return valid PNG bytes");
  await expect(page.getByLabel("Measured source-pixel contrast")).toHaveCount(0);
});
