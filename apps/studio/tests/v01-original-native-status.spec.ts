import { expect, test } from "./support/selectedSyntheticProject";
import { fixtureBootstrap } from "../src/fixture";

const jobFixture = (boot: typeof fixtureBootstrap) => ({
  job_ref: "original-status-test-job",
  provider: "driver:motion-canvas",
  root_request_id: "original-start-request",
  generation: boot.project.generation,
  revision: boot.project.revision,
  state: "outcome_unknown",
  cancellation_requested: false,
  applicability: "current",
  last_command: "driver.motion-canvas.render.status",
  created_at: "2026-10-12T07:00:00Z",
  last_observed_at: "2026-10-12T07:01:00Z",
  local_observations: 2,
  provider_generation: null,
  progress: null,
  artifact_available: false,
  result_available: false,
  last_observation: "outcome_unknown",
});

test("owner explicitly observes only original native job; no render restart or forged media success", async ({page}) => {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const job = jobFixture(boot);
  await page.addInitScript(({initial, job}) => {
    const app = window as unknown as {
      __TAURI_INTERNALS__: {invoke: (command:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __OWNER_STATUS__: {reads:number;forbidden:string[];requests:Array<Record<string,unknown>>};
    };
    app.__OWNER_STATUS__={reads:0,forbidden:[],requests:[]};
    app.__TAURI_INTERNALS__={invoke:async(command,args)=>{
      if (command === "bootstrap") {
        const syntheticResponse = structuredClone(initial);
        const explicitlySelectedId = (args as { request?: { project_id?: string | null } } | undefined)?.request?.project_id;
        if (!syntheticResponse.project || explicitlySelectedId !== syntheticResponse.project.id) {
          throw new Error("Synthetic bootstrap requires the explicitly selected matching project identity.");
        }
        return { ...syntheticResponse, project_selection: { status: "selected", project_id: syntheticResponse.project.id } };
      }
      if(command==="production_jobs")return [structuredClone(job)];
      if(command==="read_original_native_job_status"){
        app.__OWNER_STATUS__.reads++;
        const request=args?.request as Record<string,unknown>;
        app.__OWNER_STATUS__.requests.push(request);
        if(request.job_ref!==job.job_ref || request.project_id!==initial.project.id)
          throw Error("Unknown owner job");
        return {
          schema:"motionwright.v01-owner-native-status/1",
          job_ref:job.job_ref,
          provider_state:"succeeded_unverified",
          diagnostic:"original_native_job_status_read_only",
          provider_generation_matches_original_start:true,
          source_authority_checked:true,
          broker_observed:true,
          read_only:true,
          native_result_verified:false,
          output_bytes_verified:false,
          automatic_retry_permitted:false,
        };
      }
      app.__OWNER_STATUS__.forbidden.push(command);
      throw Error("No mutation or other command in owner status inspection");
    }};
  }, {initial:boot,job});
  await page.goto("/");
  await page.getByRole("button",{name:"Jobs",exact:true}).click();
  const row=page.getByRole("row").filter({hasText:job.job_ref});
  await expect(row).toContainText("OUTCOME UNKNOWN");
  const button=row.getByRole("button",{name:"Check original native job status"});
  await expect(button).toBeEnabled();
  const before=await page.locator(".revision-chip").first().innerText();
  const originalStatus=row.getByRole("status",{name:"Original native job status observation"});
  await expect(originalStatus).toHaveCount(0);
  await button.click();
  await expect(originalStatus).toContainText("succeeded unverified");
  await expect(originalStatus).toContainText("no result, verified pixels");
  await expect(row).toContainText("OUTCOME UNKNOWN");
  await expect(page.locator(".revision-chip").first()).toHaveText(before);
  const info=await page.evaluate(()=>(
    window as unknown as {__OWNER_STATUS__:{reads:number;forbidden:string[];requests:Array<Record<string,unknown>>}}
  ).__OWNER_STATUS__);
  expect(info.reads).toBe(1);
  expect(info.forbidden).toEqual([]);
  expect(info.requests).toEqual([{
    project_id:boot.project.id,generation:boot.project.generation,
    revision:boot.project.revision,job_ref:job.job_ref,
  }]);
});

test("foreign response with fake verified media is rejected, never becomes success",async({page})=>{
  const boot=structuredClone(fixtureBootstrap);
  boot.native_sdk.mode="tauri";
  const job=jobFixture(boot);
  await page.addInitScript(({initial,job})=>{
    const app=window as unknown as {
      __TAURI_INTERNALS__:{invoke:(command:string)=>Promise<unknown>};
    };
    app.__TAURI_INTERNALS__={invoke:async(command,args)=>{
      if (command === "bootstrap") {
        const syntheticResponse = structuredClone(initial);
        const explicitlySelectedId = (args as { request?: { project_id?: string | null } } | undefined)?.request?.project_id;
        if (!syntheticResponse.project || explicitlySelectedId !== syntheticResponse.project.id) {
          throw new Error("Synthetic bootstrap requires the explicitly selected matching project identity.");
        }
        return { ...syntheticResponse, project_selection: { status: "selected", project_id: syntheticResponse.project.id } };
      }
      if(command==="production_jobs")return [structuredClone(job)];
      if(command==="read_original_native_job_status")return {
        schema:"motionwright.v01-owner-native-status/1",
        job_ref:"foreign-native-job", provider_state:"succeeded_unverified",
        source_authority_checked:true, broker_observed:true, read_only:true,
        native_result_verified:true, output_bytes_verified:true,
        automatic_retry_permitted:true,
      };
      throw Error("Unknown command");
    }};
  },{initial:boot,job});
  await page.goto("/");
  await page.getByRole("button",{name:"Jobs",exact:true}).click();
  await page.getByRole("button",{name:"Check original native job status"}).click();
  await expect(page.getByLabel("Original native status read not confirmed")).toContainText(
    "Original read-only status was not confirmed"
  );
  await expect(page.getByRole("table",{name:"Canonical production job receipts"})).toContainText("OUTCOME UNKNOWN");
  await expect(page.getByRole("status",{name:"Original native job status observation"})).toHaveCount(0);
});

test("historical, other-provider and browser-only jobs cannot trigger original status read",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Jobs",exact:true}).click();
  await expect(page.getByRole("button",{name:"Check original native job status"})).toHaveCount(0);
});
