import {test,expect} from "./support/selectedSyntheticProject";
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


async function openAudition(page:import("@playwright/test").Page){
  await page.goto("/");
  await page.getByRole("button",{name:"Audio",exact:true}).click();
  const desk=page.locator('details[aria-label="Original three-bus native PCM audition"]');
  await expect(desk).toBeVisible();
  await desk.locator(":scope > summary").click();
  return desk;
}
test("browser-only Audio cannot fabricate a mixed WAV or rights receipt",async({page})=>{
  const desk=await openAudition(page);
  await expect(desk).toContainText("Local PCM audition requires the desktop native service");
  await expect(desk.getByRole("button",{name:"Create local original PCM audition"})).toBeDisabled();
  await expect(desk.locator("audio")).toHaveCount(0);
});

test("explicit desktop audition reads a saved recipe and leaves that project revision unchanged",async({page})=>{
  const boot=structuredClone(fixtureBootstrap);
  boot.native_sdk.mode="tauri";
  const project=boot.project;
  const music="00000000-0000-4000-8000-000000000051";
  const sfx="00000000-0000-4000-8000-000000000052";
  const voiceId="00000000-0000-4000-8000-000000000053";
  const voiceAsset=project.assets.find(asset=>asset.media_type==="audio/wav")!;
  project.assets.push(
    {id:music,media_type:"audio/wav",content_sha256:"b".repeat(64),name:"Owner music",source_revision:"synthetic-ui-fixture"},
    {id:sfx,media_type:"audio/wav",content_sha256:"c".repeat(64),name:"Owner foley",source_revision:"synthetic-ui-fixture"}
  );
  project.audio.voice_tracks=[{
    id:voiceId,asset_id:voiceAsset.id,label:"Original recorded",
    sample_rate_hz:48000,channels:2,measured_duration:{num:"1",den:"1"},
    source_sha256:voiceAsset.content_sha256!,loudness_lufs:-22,true_peak_dbfs:-8,
  }];
  project.audio.active_voice_track_id=voiceId;
  project.deliverables[0].voice_track_id=voiceId;
  project.audio.transcript=[{
    id:"00000000-0000-4000-8000-000000000054",voice_track_id:voiceId,
    start:{num:"0",den:"1"},end:{num:"1",den:"1"},
    text:"Source words manually reviewed",speaker:null,alignment:{kind:"manual"},
  }];
  await installRecipeAuthoringFixture(page);
  await page.addInitScript(({boot,selectedMusic,selectedSfx})=>{
    const runtime=window as unknown as {
      __TAURI_INTERNALS__:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __MIX_TEST_CALLS__:Array<{name:string;request?:Record<string,unknown>}>;
    };
    const calls:Array<{name:string;request?:Record<string,unknown>}>=[];
    runtime.__MIX_TEST_CALLS__=calls;
    runtime.__TAURI_INTERNALS__={invoke:async(command,args)=>{
        // Authoring is counted separately from the native-only operations below.
        const recipeRequest = args?.request as RecipeFixtureRequest | undefined;
        if (command === "issue_effect_grant" && recipeRequest?.effect === "project_edit"
            && recipeRequest.project_id === boot.project.id && recipeRequest.generation === boot.project.generation
            && recipeRequest.revision === boot.project.revision) return { token: "synthetic-recipe-only" };
        if (command === "apply_change" && recipeRequest?.change?.type === "set_delivery_recipe") {
          const apply = (window as unknown as { __MW_RECIPE_FIXTURE_APPLY__: (source: RecipeFixtureProject, request: RecipeFixtureRequest) => Promise<RecipeFixtureProject> }).__MW_RECIPE_FIXTURE_APPLY__;
          boot.project = await apply(boot.project, recipeRequest);
          return structuredClone(boot.project);
        }

      if (command === "bootstrap") {
        const syntheticResponse = structuredClone(boot);
        const explicitlySelectedId = (args as { request?: { project_id?: string | null } } | undefined)?.request?.project_id;
        if (!syntheticResponse.project || explicitlySelectedId !== syntheticResponse.project.id) {
          throw new Error("Synthetic bootstrap requires the explicitly selected matching project identity.");
        }
        return { ...syntheticResponse, project_selection: { status: "selected", project_id: syntheticResponse.project.id } };
      }
      if(command==="production_runtime_status")return{
        status:"ready",reason:null,expected_version:"1.0.0",
        observed_version:"1.0.0",version_compatible:true,
        executable_sha256:"a".repeat(64),connection_identity:"b".repeat(64),
        pinned_revision:"c".repeat(40),
      };
      if(command==="native_original_mix_audition"){
        const request=args?.request as Record<string,unknown>|undefined;
        calls.push({name:command,request});
        if(request?.project_id!==boot.project.id || request?.revision!==boot.project.revision ||
           request?.generation!==boot.project.generation ||
           request?.music_asset_id!==selectedMusic || request?.sfx_asset_id!==selectedSfx ||
           request?.owner_attests_preview_rights!==true)
          throw new Error("Synthetic local PCM source scope not acknowledged.");
        // Synthetic transport fixture, not evidence of real audio mastering.
        const pcm=48_000*4;
        const bytes=new Uint8Array(44+pcm),view=new DataView(bytes.buffer);
        bytes.set([82,73,70,70],0);view.setUint32(4,36+pcm,true);
        bytes.set([87,65,86,69,102,109,116,32],8);
        view.setUint32(16,16,true);view.setUint16(20,1,true);
        view.setUint16(22,2,true);view.setUint32(24,48_000,true);
        view.setUint32(28,192_000,true);view.setUint16(32,4,true);
        view.setUint16(34,16,true);bytes.set([100,97,116,97],36);
        view.setUint32(40,pcm,true);
        return bytes;
      }
      calls.push({name:command});
      throw new Error("Unexpected synthetic desktop operation: "+command);
    }};
  },{boot,selectedMusic:music,selectedSfx:sfx});
  const desk=await openAudition(page);
  await desk.getByLabel("Delivery language / profile").selectOption(project.deliverables[0].id);
  await desk.getByLabel("Original music asset").selectOption(music);
  await desk.getByLabel("Original SFX asset").selectOption(sfx);
  await saveFixtureRecipe(page, "mix");
  const before=await page.locator(".revision-chip").innerText();
  const request=desk.getByRole("button",{name:"Create local original PCM audition"});
  await expect(request).toBeDisabled();
  await desk.getByLabel(/I confirm I have permission to use these exact original sources/).check();
  await expect(request).toBeEnabled();
  await request.click();
  await expect(desk.locator('audio[controls]')).toBeVisible();
  await expect(desk).toContainText("not a mastered deliverable");
  await expect(page.locator(".revision-chip")).toHaveText(before);
  const calls=await page.evaluate(()=>(window as unknown as {
    __MIX_TEST_CALLS__:Array<{name:string;request?:Record<string,unknown>}>
  }).__MIX_TEST_CALLS__);
  expect(calls.filter(call=>call.name==="native_original_mix_audition")).toHaveLength(1);
  // Recipe authoring was acknowledged above; the isolated audition call log
  // must still contain no editing, rendering, release or provider operation.
  expect(calls.some(call=>/apply_change|provider|render|payment|release/.test(call.name))).toBe(false);
  await desk.getByLabel("Original music asset").selectOption("");
  await expect(desk.getByLabel(/I confirm I have permission to use these exact original sources/)).not.toBeChecked();
  await expect(desk.locator("audio")).toHaveCount(0);
  await expect(request).toBeDisabled();
});
