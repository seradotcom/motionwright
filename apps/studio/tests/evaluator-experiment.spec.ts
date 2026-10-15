import {expect,test,type Page} from "@playwright/test";
import {fixtureBootstrap} from "../src/fixture";
import type {CreativeEvaluatorBundle} from "../src/creativeEvaluator";

async function openEvaluator(page:Page){
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Evaluators",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Creative evaluation"})).toBeVisible();
}
async function prepareExperiment(page:Page){
  await page.getByLabel("Evaluator author declaration").fill("I am the original author of this data-only evaluator.");
  await page.getByRole("button",{name:"Register data-only evaluator"}).click();
  await expect(page.getByLabel("Evaluator source version").locator("option")).toHaveCount(2);
  await page.getByRole("button",{name:/Prepare a proposed v2 manifest/}).click();
  const jsonField=page.getByLabel("Evaluator experimental candidate JSON");
  const body=JSON.parse(await jsonField.inputValue()) as CreativeEvaluatorBundle;
  const minimum=body.criteria.find(item=>item.kind==="minimum_nodes");
  if(!minimum||minimum.kind!=="minimum_nodes")throw Error("Original evaluator lacks bounded minimum_nodes");
  minimum.minimum=3;
  await jsonField.fill(JSON.stringify(body));
  await page.getByRole("button",{name:"Run disposable technical comparison"}).click();
  await expect(page.getByRole("region",{name:"Source-bound evaluator experiment"})).toBeVisible();
  await expect(page.getByLabel("Technical outcome counts by scene")).toBeVisible();
}
test("a browser can compare original data but cannot claim trusted candidate admission",async({page})=>{
  await openEvaluator(page);
  await prepareExperiment(page);
  const result=page.getByRole("region",{name:"Source-bound evaluator experiment"});
  await expect(result).toContainText("Technical comparison");
  await expect(result).toContainText("Human questions always remain not assessed");
  const admit=page.getByRole("button",{name:/Register reviewed v2 via project CAS/});
  await expect(admit).toBeDisabled();
  await page.getByLabel("Experiment owner review rationale").fill("Reviewed source tests and abstentions, this candidate is data-only.");
  await page.getByLabel(/I have permission to register this original/).check();
  await page.getByLabel(/I reviewed the per-scene technical changes/).check();
  await expect(admit).toBeDisabled();
  await expect(page.getByText(/Browser demo may compare local data/)).toBeVisible();
  await expect(page.getByRole("region",{name:"Source-bound evaluator experiment"})).not.toContainText("Approved");
});
test("re-editing a candidate or selecting a different scene invalidates the previous comparison",async({page})=>{
  await openEvaluator(page);
  await prepareExperiment(page);
  const candidate=page.getByLabel("Evaluator experimental candidate JSON");
  await candidate.fill((await candidate.inputValue()).replace('"minimum":3','"minimum":4'));
  await expect(page.getByRole("region",{name:"Source-bound evaluator experiment"})).toHaveCount(0);
  await page.getByRole("button",{name:"Run disposable technical comparison"}).click();
  await expect(page.getByRole("region",{name:"Source-bound evaluator experiment"})).toBeVisible();
  await page.getByLabel("Production working scene").selectOption(fixtureBootstrap.project.scenes[1].id);
  await expect(page.getByRole("region",{name:"Source-bound evaluator experiment"})).toHaveCount(0);
});
test("an approved owner registration sends one scoped canonical evaluator change, never an executable skill",async({page})=>{
  const boot=structuredClone(fixtureBootstrap);
  boot.native_sdk.mode="tauri";
  await page.addInitScript((initial)=>{
    const runtime=window as unknown as {
      __TAURI_INTERNALS__:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>};
      __EXPERIMENT_TEST_CALLS__:Array<{command:string;kind?:string}>;
      __EXPERIMENT_REJECT_NEXT__?:boolean;
    };
    let project=structuredClone(initial.project);
    const calls:Array<{command:string;kind?:string}>=[];
    runtime.__EXPERIMENT_TEST_CALLS__=calls;
    runtime.__TAURI_INTERNALS__={invoke:async(command,args)=>{
      const request=args?.request as Record<string,unknown>|undefined;
      if(command==="bootstrap")return {...initial,project:structuredClone(project)};
      if(command==="production_runtime_status")return {
        status:"ready",reason:null,expected_version:"1.0.0",observed_version:"1.0.0",
        version_compatible:true,executable_sha256:"a".repeat(64),
        connection_identity:"b".repeat(64),pinned_revision:"c".repeat(40),
      };
      if(command==="issue_effect_grant")return {token:"synthetic-test-only-effect-grant"};
      if(command==="apply_change"){
        const scope=request as {
          project_id:string;generation:string;revision:number;
          change:{type:string;bundle:CreativeEvaluatorBundle};
        };
        calls.push({command,kind:scope.change?.type});
        if(scope.project_id!==project.id || scope.generation!==project.generation ||
            scope.revision!==project.revision)throw new Error("Synthetic CAS refused stale evaluator");
        if(scope.change.type!=="register_creative_evaluator")throw new Error("Unexpected mutation");
        if(scope.change.bundle.version===2 && runtime.__EXPERIMENT_REJECT_NEXT__){
          runtime.__EXPERIMENT_REJECT_NEXT__=false;
          throw new Error("Synthetic StudioService CAS rejected the candidate");
        }
        // The pre-v0.5 fixture has no production_design; the real service creates it on first successful write.
        project.production_design ??= {plan:null,heroes:[],capsules:[],evaluator_bundles:[],evaluation_reports:[]};
        (project.production_design.evaluator_bundles ??= []).push(structuredClone(scope.change.bundle));
        project.revision++;
        return structuredClone(project);
      }
      calls.push({command});
      throw new Error("Unexpected synthetic native operation: "+command);
    }};
  },boot);
  await openEvaluator(page);
  await prepareExperiment(page);
  const rev=await page.locator(".revision-chip").innerText();
  const admit=page.getByRole("button",{name:/Register reviewed v2 via project CAS/});
  await expect(admit).toBeDisabled();
  await page.getByLabel("Experiment owner review rationale").fill("I reviewed technical regressions and abstentions for original source.");
  await page.getByLabel(/I have permission to register this original/).check();
  await page.getByLabel(/I reviewed the per-scene technical changes/).check();
  await expect(admit).toBeEnabled();
  await expect(page.locator(".revision-chip")).toHaveText(rev);
  // The outer App.commit resolves void even when its service call is refused.
  // Reject the first attempted candidate and require the canonical version list
  // to remain unchanged; a resolved promise must never be labelled committed.
  await page.evaluate(()=>(window as unknown as {
    __EXPERIMENT_REJECT_NEXT__:boolean
  }).__EXPERIMENT_REJECT_NEXT__=true);
  await admit.click();
  await expect(page.getByLabel("Evaluator source version").locator("option")).toHaveCount(2);
  await expect(page.getByText(/Registration requested. Confirm the candidate/)).toBeVisible();
  await expect(admit).toBeEnabled();
  await admit.click();
  await expect(page.getByLabel("Evaluator source version").locator("option")).toHaveCount(3);
  const calls=await page.evaluate(()=>(window as unknown as {
    __EXPERIMENT_TEST_CALLS__:Array<{command:string;kind?:string}>
  }).__EXPERIMENT_TEST_CALLS__);
  expect(calls.filter(call=>call.command==="apply_change")).toHaveLength(3);
  expect(calls.filter(call=>call.kind==="register_creative_evaluator")).toHaveLength(3);
  expect(calls.some(call=>/provider|render|skill\.execute|code/.test(call.command))).toBe(false);
});
