import { expect, test } from "./support/selectedSyntheticProject";
import { fixtureBootstrap } from "../src/fixture";

function inventoryFixture(project: typeof fixtureBootstrap.project) {
  return {
    schema: "motionwright.v01-mlt-cleanup-inventory/1",
    project_id: project.id, generation: project.generation,
    revision: project.revision, source_receipts_scanned: 5,
    confirmed_native_creations_current_revision: 1,
    confirmed_native_creations_historical: 0,
    unconfirmed_or_invalid_creation_requests: 1,
    observed_close_request_identities: 0,
    disposition: "creation_outcome_not_fully_proven",
    complete_source_receipt_snapshot: true,
    native_provider_liveness_verified: false,
    foreground_cleanup_consent_obtained: false,
    verified_native_project_cleanup: false,
    automatic_destructive_cleanup_allowed: false,
  };
}

test("installed Jobs can inspect MLT creation receipts without driver dispatch or consent", async ({page})=>{
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  await page.addInitScript(({initial, inventory})=>{
    const local = window as unknown as {
      __TAURI_INTERNALS__:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __MLT_INSPECTION__:{reads:number;forbidden:string[];scopes:Array<Record<string,unknown>>};
    };
    local.__MLT_INSPECTION__={reads:0,forbidden:[],scopes:[]};
    local.__TAURI_INTERNALS__={invoke:async(command,args)=>{
      if (command === "bootstrap" || command === "project_projection_head" || command === "open_project") {
        return window.__SYNTHETIC_PROJECT_READ__(command, args, structuredClone(initial));
      }
      if (command==="production_jobs") return [];
      if (command==="mlt_cleanup_inventory") {
        local.__MLT_INSPECTION__.reads++;
        const request = args?.request as Record<string,unknown>;
        local.__MLT_INSPECTION__.scopes.push(request);
        return structuredClone(inventory);
      }
      local.__MLT_INSPECTION__.forbidden.push(command);
      throw Error("An unexpected native command was attempted");
    }};
  },{initial:boot,inventory:inventoryFixture(boot.project)});
  await page.goto("/");
  await page.getByRole("button",{name:"Jobs",exact:true}).click();
  const button = page.getByRole("button",{name:"Inspect MLT receipt inventory"});
  await expect(button).toBeEnabled();
  const region=page.getByRole("region",{name:"Native MLT inventory"});
  await expect(region).toHaveCount(0);
  await button.click();
  await expect(region).toContainText("1 current creations");
  await expect(region).toContainText("Native cleanup remains unverified");
  const calls=await page.evaluate(()=>(
    window as unknown as {__MLT_INSPECTION__:{
      reads:number;forbidden:string[];scopes:Array<Record<string,unknown>>
    }}
  ).__MLT_INSPECTION__);
  expect(calls.reads).toBe(1);
  expect(calls.forbidden).toEqual([]);
  expect(calls.scopes).toEqual([{
    project_id:boot.project.id,
    generation:boot.project.generation,
    revision:boot.project.revision,
  }]);
});

test("foreign source receipt inventory cannot be displayed as current project",async({page})=>{
  const boot=structuredClone(fixtureBootstrap);
  boot.native_sdk.mode="tauri";
  await page.addInitScript(({initial,report})=>{
    const app=window as unknown as {
      __TAURI_INTERNALS__:{invoke:(command:string)=>Promise<unknown>};
    };
    app.__TAURI_INTERNALS__={invoke:async(command,args)=>{
      if (command === "bootstrap" || command === "project_projection_head" || command === "open_project") {
        return window.__SYNTHETIC_PROJECT_READ__(command, args, structuredClone(initial));
      }
      if(command==="production_jobs")return [];
      if(command==="mlt_cleanup_inventory")return structuredClone(report);
      throw Error("No native operation authorized");
    }};
  },{initial:boot,report:{...inventoryFixture(boot.project),generation:"other-generation"}});
  await page.goto("/");
  await page.getByRole("button",{name:"Jobs",exact:true}).click();
  await page.getByRole("button",{name:"Inspect MLT receipt inventory"}).click();
  await expect(page.getByRole("alert",{name:"MLT inventory unavailable"})).toBeVisible();
  await expect(page.getByRole("region",{name:"Native MLT inventory"})).toHaveCount(0);
});

test("browser preview cannot inspect installed MLT receipts or invent cleanup",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Jobs",exact:true}).click();
  await expect(page.getByRole("button",{name:"Inspect MLT receipt inventory"})).toBeDisabled();
  await expect(page.getByRole("region",{name:"Native MLT inventory"})).toHaveCount(0);
});
