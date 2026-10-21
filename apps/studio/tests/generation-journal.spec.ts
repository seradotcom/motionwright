import { expect, test, type Page } from "./support/selectedSyntheticProject";
import { fixtureBootstrap } from "../src/fixture";
import type { PaidGenerationJob, PaidGenerationSpec } from "../src/paidGenerationTypes";

async function installSyntheticDesktop(page: Page, delayed = false) {
  await page.addInitScript(({ boot, hold }) => {
    // Explicit test transport, NOT a paid provider, financial receipt, or real SDK host.
    let project=structuredClone(boot.project);
    project.paid_generation={jobs:[]};
    const calls:string[]=[];
    const runtime=window as unknown as {
      __TAURI_INTERNALS__: {invoke:(name:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __GENERATION_TEST_CALLS__:string[];
      __RELEASE_GENERATION_TEST__?:()=>void;
    };
    runtime.__GENERATION_TEST_CALLS__=calls;
    const source=()=>({project_id:project.id,generation:project.generation,revision:project.revision});
    const budget=(extra=0)=>({schema:"motionwright.paid-generation-budget-preview/1",project_reserved_microusd:project.paid_generation!.jobs.reduce((n,j)=>n+j.reserved_microusd,extra),provider_reported_cost_microusd:0,unresolved_reserved_microusd:project.paid_generation!.jobs.reduce((n,j)=>n+j.reserved_microusd,extra),currency:"USD_micro",cost_reports_are_provider_verified:false,actual_payment_settled:false,may_retry_paid_submission:false});
    const journal=()=>({schema:"motionwright.paid-generation-ledger-observation/1",...source(),budget:budget(),read_only:true,remote_api_called:false,execution_authority:"NONE_GRANTED",rows:project.paid_generation!.jobs.map(job=>({job_id:job.spec.id,kind:job.spec.kind,scope:job.spec.scope,state:job.state,history_sha256:"e".repeat(64),provider_id:job.spec.provider_id,model_id:job.spec.model_id,model_version:job.spec.model_version,variant_index:job.spec.variant_index,max_charge_microusd:job.spec.max_charge_microusd,source_sha256:job.spec.logical_idempotency_sha256,known_task_id:job.task_id,reconciliation_query:null,reported_spend_microusd:null,owner_approval_is_authenticated:false,provider_success_is_authenticated:false}))});
    runtime.__TAURI_INTERNALS__={invoke:async(name,args)=>{
      calls.push(name);
      const request=args?.request as Record<string,unknown> | undefined;
      if (name === "bootstrap" || name === "project_projection_head" || name === "open_project") {
        return window.__SYNTHETIC_PROJECT_READ__(name, args, {...boot,project:structuredClone(project),native_sdk:{...boot.native_sdk,mode:"tauri"}});
      }
      if(name==="native_paid_generation_journal")return structuredClone(journal());
      if(name==="native_paid_generation_preflight") {
        const spec={...(request!.specification as PaidGenerationSpec),logical_idempotency_sha256:"d".repeat(64)};
        const reply={schema:"motionwright.paid-generation-draft-preview/1",...source(),specification:spec,normalized_edit:{kind:"create",spec},budget:budget(),provider_capability_signature_verified:false,owner_execution_grant:false,provider_called:false,payment_charged:false,committed:false};
        if(hold)await new Promise<void>(resolve=>{runtime.__RELEASE_GENERATION_TEST__=resolve;});
        return structuredClone(reply);
      }
      if(name==="native_paid_generation_reserve_preflight") {
        const job=project.paid_generation!.jobs.find(j=>j.spec.id===request!.job_id)!;
        return {schema:"motionwright.paid-generation-budget-preflight/1",...source(),normalized_edit:{kind:"append",job_id:job.spec.id,expected_history_sha256:"e".repeat(64),event:{id:"33333333-3333-4333-8333-333333333333",kind:{kind:"reserve",maximum_charge_microusd:job.spec.max_charge_microusd}}},budget:budget(job.spec.max_charge_microusd),committed:false,provider_called:false,payment_charged:false,owner_execution_grant:false};
      }
      if(name==="issue_effect_grant")return {token:"44444444-4444-4444-8444-444444444444"};
      if(name==="apply_change") {
        if(request!.project_id!==project.id || request!.generation!==project.generation || request!.revision!==project.revision)throw new Error("Synthetic stale mutation");
        const change=request!.change as {type:string;edit:{kind:string;spec?:PaidGenerationSpec;job_id?:string;event?:PaidGenerationJob['events'][number]}};
        if(change.type!=="edit_paid_generation")throw new Error("Unexpected synthetic project edit");
        if(change.edit.kind==="create") {
          project.paid_generation!.jobs.push({spec:change.edit.spec!,events:[],state:"draft",reserved_microusd:0,provider_reported_spend_microusd:null,task_id:null,reported_output_sha256:null,admitted_output_asset_id:null});
        } else {
          const job=project.paid_generation!.jobs.find(j=>j.spec.id===change.edit.job_id)!;
          job.events.push(change.edit.event!);job.state="reserved";job.reserved_microusd=job.spec.max_charge_microusd;
        }
        project.revision++;
        return structuredClone(project);
      }
      if(name==="project_history" || name==="project_history_recent")return [];
      throw new Error("Unimplemented synthetic desktop operation: "+name);
    }};
  },{boot:structuredClone(fixtureBootstrap),hold:delayed});
}
async function openJournal(page:Page) {
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Generation journal",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Generation budget journal",exact:true})).toBeVisible();
}
async function fillSource(page:Page) {
  await page.getByLabel("Provider capability source SHA-256").fill("a".repeat(64));
  await page.getByLabel("Usage policy source SHA-256").fill("b".repeat(64));
  await page.getByLabel("Maximum recorded attempt USD").fill("1.500001");
}

test("desktop preflight, intent commit, and exact reservation are separate user actions",async({page},info)=>{
  await installSyntheticDesktop(page);
  await openJournal(page);
  await fillSource(page);
  const revision=await page.locator(".revision-chip").innerText();
  await page.getByRole("button",{name:"Preflight original pilot (no spend)",exact:true}).click();
  const confirm=page.getByRole("button",{name:"Commit this project ledger record only",exact:true});
  await expect(confirm).toBeVisible();
  await expect(page.locator(".revision-chip")).toHaveText(revision);
  expect(await page.evaluate(()=>(window as unknown as {__GENERATION_TEST_CALLS__:string[]}).__GENERATION_TEST_CALLS__.filter(n=>n==="apply_change").length)).toBe(0);
  await confirm.click();
  await expect(page.getByRole("button",{name:"Preflight ledger budget reservation (NOT a payment)",exact:true})).toBeVisible();
  const after=await page.locator(".revision-chip").innerText();
  await page.getByRole("button",{name:"Preflight ledger budget reservation (NOT a payment)",exact:true}).click();
  await expect(confirm).toBeVisible();
  await expect(page.locator(".revision-chip")).toHaveText(after);
  await confirm.click();
  await expect(page.getByRole("button",{name:"Preflight ledger budget reservation (NOT a payment)",exact:true})).toHaveCount(0);
  await expect(page.getByText(/image · reserved/)).toBeVisible();
  await expect(page.getByText("$1.500001 USD (declared, unverified)",{exact:true}).first()).toBeVisible();
  const calls=await page.evaluate(()=>(window as unknown as {__GENERATION_TEST_CALLS__:string[]}).__GENERATION_TEST_CALLS__);
  expect(calls.filter(n=>n==="apply_change")).toHaveLength(2);
  expect(calls.filter(n=>n==="issue_effect_grant")).toHaveLength(2);
  expect(calls.some(n=>/charge|submit|retry|render_motion_canvas/.test(n))).toBe(false);
  await page.screenshot({path:info.outputPath("generation-budget-desktop-dark.png"),fullPage:true});
});

test("late preflight cannot become a confirmation for another working scene",async({page})=>{
  await installSyntheticDesktop(page,true);
  await openJournal(page);await fillSource(page);
  await page.getByRole("button",{name:"Preflight original pilot (no spend)",exact:true}).click();
  await page.waitForFunction(()=>typeof (window as unknown as {__RELEASE_GENERATION_TEST__?:()=>void}).__RELEASE_GENERATION_TEST__==="function");
  await page.getByLabel("Production working scene").selectOption(fixtureBootstrap.project.scenes[1].id);
  await page.evaluate(()=>(window as unknown as {__RELEASE_GENERATION_TEST__:()=>void}).__RELEASE_GENERATION_TEST__());
  await expect(page.getByRole("button",{name:"Commit this project ledger record only",exact:true})).toHaveCount(0);
  expect(await page.evaluate(()=>(window as unknown as {__GENERATION_TEST_CALLS__:string[]}).__GENERATION_TEST_CALLS__.includes("apply_change"))).toBe(false);
});

test("browser demo never simulates provider jobs or spending",async({page})=>{
  await openJournal(page);
  await expect(page.getByText(/Browser demo — read-only/)).toBeVisible();
  await expect(page.getByRole("button",{name:"Preflight original pilot (no spend)",exact:true})).toBeDisabled();
  await expect(page.getByRole("button",{name:"Commit this project ledger record only",exact:true})).toHaveCount(0);
});
