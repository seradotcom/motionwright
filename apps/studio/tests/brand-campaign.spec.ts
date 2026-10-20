import {expect,test,type Page} from "./support/selectedSyntheticProject";
import {fixtureBootstrap} from "../src/fixture";
import {defaultHeroConfig,emptyProductionDesign} from "../src/creativeProduction";
import type {BrandCampaignRollout} from "../src/brandCampaign";

async function syntheticBrand(page:Page,failSecond=false){
  const boot=structuredClone(fixtureBootstrap);
  boot.native_sdk.mode="tauri";
  const project=boot.project;
  project.production_design=emptyProductionDesign();
  project.locks=[];project.reviews=[];project.scenes.forEach(s=>s.status="draft");
  project.production_design.heroes=project.scenes.slice(0,2).map((scene,index)=>({
    id:"00000000-0000-4000-8000-00000000007"+index,
    scene_id:scene.id,component_version:1,baseline:[],config:defaultHeroConfig(),
  }));
  project.production_design.brand_profile={
    id:"00000000-0000-4000-8000-000000000070",
    label:"Original product campaign",version:1,rules:[
      {kind:"allowed_accents",id:"00000000-0000-4000-8000-000000000071",colors:["#A5C8DF","#F5B84A"]},
      {kind:"required_wordmark",id:"00000000-0000-4000-8000-000000000072",text:"Mw"},
    ]
  };
  await page.addInitScript(({boot,fail})=>{
    const rt=window as unknown as {
      __TAURI_INTERNALS__:{invoke:(n:string,a?:Record<string,unknown>)=>Promise<unknown>};
      __BRAND_CALLS__:Array<{name:string;kind?:string}>;
    };
    let project=structuredClone(boot.project);
    const calls:Array<{name:string;kind?:string}>=[];
    rt.__BRAND_CALLS__=calls;
    rt.__TAURI_INTERNALS__={invoke:async(name,args)=>{
      const request=args?.request as Record<string,unknown> | undefined;
      if (name === "bootstrap") {
        const syntheticResponse = {...boot,project:structuredClone(project)};
        const requestedId = (args as {request?: {project_id?:string|null}}|undefined)?.request?.project_id;
        if (!syntheticResponse.project || requestedId !== syntheticResponse.project.id) throw new Error("Synthetic mock requires selected project identity.");
        return {...syntheticResponse, project_selection: {status:"selected",project_id:syntheticResponse.project.id}};
      }
      if(name==="production_runtime_status")return {
        status:"ready",reason:null,expected_version:"1.0.0",observed_version:"1.0.0",
        version_compatible:true,executable_sha256:"a".repeat(64),
        connection_identity:"b".repeat(64),pinned_revision:"c".repeat(40),
      };
      if(name==="issue_effect_grant")return {token:"synthetic-only-test-effect-grant"};
      if(name==="apply_change"){
        const source=request as {project_id:string;generation:string;revision:number;
          change:{type:string;rollout:BrandCampaignRollout}};
        calls.push({name,kind:source.change.type});
        if(source.project_id!==project.id||source.generation!==project.generation||
          source.revision!==project.revision)throw new Error("Synthetic CAS refused stale brand source");
        if(source.change.type!=="apply_brand_campaign")throw new Error("Unexpected synthetic mutation");
        if(fail)throw new Error("Synthetic late style-lock failure: no partial project update");
        const proposal=source.change.rollout;
        for(const sceneId of proposal.scene_ids){
          const hero=project.production_design!.heroes.find(h=>h.scene_id===sceneId);
          if(!hero)throw new Error("Unbound original scene");
          const from=hero.config.accent;
          if(proposal.accent!==null)hero.config.accent=proposal.accent;
          if(proposal.wordmark!==null)hero.config.wordmark=proposal.wordmark;
          project.production_design!.project_decisions!.push({
            id:crypto.randomUUID(),scene_id:sceneId,author:proposal.reviewer_declaration,
            decision:"Campaign "+proposal.campaign+": brand accent "+from+" to "+hero.config.accent,
            rationale:proposal.rationale,
          });
        }
        project.revision++;
        return structuredClone(project);
      }
      calls.push({name});
      throw new Error("Unexpected synthetic desktop operation: "+name);
    }};
  },{boot,fail:failSecond});
  return boot;
}
async function openBrand(page:Page){
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Brand & taste",exact:true}).click();
  await expect(page.getByRole("region",{name:"Campaign brand rollout review"})).toBeVisible();
}
async function fillOwnerReview(page:Page){
  await page.getByLabel("Brand rollout campaign").fill("Original launch batch");
  await page.getByLabel("Brand rollout owner").fill("I authored the original campaign");
  await page.getByLabel("Brand rollout accent").selectOption("#F5B84A");
  await page.getByLabel("Brand rollout rationale").fill("Original semantic components receive the saved palette accent");
  await page.getByLabel(/I understand existing Hero components will be rebuilt/).check();
  await page.getByRole("group",{name:"Select up to eight existing editable scenes"})
    .locator('input[type="checkbox"]').first().check();
  await page.getByRole("group",{name:"Select up to eight existing editable scenes"})
    .locator('input[type="checkbox"]').nth(1).check();
}
test("browser with no canonical BrandProfile cannot claim campaign admission",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Brand & taste",exact:true}).click();
  await expect(page.getByText(/Save a versioned BrandProfile above/)).toBeVisible();
  await expect(page.getByRole("button",{name:"Apply owner-reviewed campaign via project CAS"})).toHaveCount(0);
});
test("two-scene source-bound brand rollout is reviewed before one canonical mutation",async({page})=>{
  const boot=await syntheticBrand(page);
  await openBrand(page);
  const revision=await page.locator(".revision-chip").innerText();
  await fillOwnerReview(page);
  await page.getByRole("button",{name:"Review per-scene brand changes"}).click();
  const region=page.getByRole("region",{name:"Proposed brand campaign changes"});
  await expect(region).toContainText("2 component(s) would change");
  await expect(region).toContainText("#A5C8DF");
  await expect(region).toContainText("#F5B84A");
  await expect(page.locator(".revision-chip")).toHaveText(revision);
  const button=page.getByRole("button",{name:"Apply owner-reviewed campaign via project CAS"});
  await expect(button).toBeDisabled();
  await page.getByLabel(/I reviewed the exact rows, campaign, brand digest/).check();
  await expect(button).toBeEnabled();
  const before=await page.evaluate(()=>(window as unknown as {__BRAND_CALLS__:Array<{name:string}>}).__BRAND_CALLS__);
  expect(before.filter(call=>call.name==="apply_change")).toHaveLength(0);
  await button.click();
  await expect(page.locator(".revision-chip")).not.toHaveText(revision);
  const after=await page.evaluate(()=>(window as unknown as {__BRAND_CALLS__:Array<{name:string;kind?:string}>}).__BRAND_CALLS__);
  expect(after.filter(call=>call.name==="apply_change")).toHaveLength(1);
  expect(after.filter(call=>call.kind==="apply_brand_campaign")).toHaveLength(1);
  expect(after.some(call=>/render|provider|payment|delete|cleanup/.test(call.name))).toBe(false);
  expect(boot.project.production_design?.heroes).toHaveLength(2);
});
test("failed synthetic late CAS never claims a successful campaign revision",async({page})=>{
  await syntheticBrand(page,true);
  await openBrand(page);
  await fillOwnerReview(page);
  await page.getByRole("button",{name:"Review per-scene brand changes"}).click();
  await page.getByLabel(/I reviewed the exact rows, campaign, brand digest/).check();
  const revision=await page.locator(".revision-chip").innerText();
  await page.getByRole("button",{name:"Apply owner-reviewed campaign via project CAS"}).click();
  await expect(page.locator(".revision-chip")).toHaveText(revision);
  await expect(page.getByText(/Owner rollout requested. Confirm a new project revision/)).toBeVisible();
});
