import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import type { DistillationFixtureRequest } from "./fixtures/component-distillation-authority";

// Actual component + applyChange + ProjectMutationGate. Source authoring and
// reply scheduling belong to this labelled regression fixture, not SQLite,
// native pixel evidence, rights verification or human admission.
const candidateA="00000000-0000-4000-8000-000000000093",candidateB="00000000-0000-4000-8000-000000000094";
const button=(page:Page,name:string)=>page.getByRole("button",{name,exact:true});
const label=(page:Page)=>page.getByLabel("Distillation label",{exact:true});
const note=(page:Page)=>page.getByLabel("Distillation review rationale",{exact:true});
const requests=(page:Page)=>page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.requests());
async function open(page:Page){await button(page,"Distillation").click();await expect(label(page)).toBeVisible();}
async function author(page:Page){
  const project=await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project());
  const positives=project.scenes.filter(scene=>scene.renderer==="motion-canvas");
  await page.getByLabel("Distillation source A").selectOption(positives[0].id);await page.getByLabel("Distillation source B").selectOption(positives[1].id);
  await page.getByLabel("Distillation negative scene").selectOption(project.scenes.find(scene=>scene.renderer!=="motion-canvas")!.id);
  await label(page).fill("New authored candidate");await page.getByLabel("Distillation author", { exact: true }).fill("Original candidate author");
  await page.getByLabel("Distillation rights").fill("Original source roles authored for this controlled fixture.");
  await page.getByLabel("Distillation requirements").fill("Preserve editable source roles\nKeep independent review explicit");
}
async function reviewer(page:Page,id=candidateA){
  await page.getByLabel("Distillation review candidate").selectOption(id);
  await page.getByLabel("Distillation reviewer").fill("Independent test reviewer");await note(page).fill("Compatible source proposal; no executable admission.");
}
async function submit(page:Page,name:string):Promise<DistillationFixtureRequest>{
  const count=(await requests(page)).length;await button(page,name).click();await expect.poll(async()=>(await requests(page)).length).toBe(count+1);return(await requests(page))[count];
}
test.beforeEach(async({page})=>{
  await page.goto("/tests/fixtures/component-distillation-authority.html");
  await expect(page.getByLabel("Headline",{exact:true})).toBeVisible();await open(page);
});

test("Distillation author source choices and exact raw declarations survive navigation, remount and reload without writes",async({page})=>{
  const initial=await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project());await author(page);
  const raw="  A retained candidate  ",requirements="  First authored requirement  \n\n  Second requirement \n";
  await label(page).fill(raw);await page.getByLabel("Distillation author", { exact: true }).fill("  Author A  ");await page.getByLabel("Distillation rights").fill("  Retained original declaration  ");
  await page.getByLabel("Distillation requirements").fill(requirements);await button(page,"Production plan").click();await open(page);await expect(label(page)).toHaveValue(raw);
  await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.unmount());await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.mount());await open(page);
  await expect(label(page)).toHaveValue(raw);await page.reload();await open(page);await expect(label(page)).toHaveValue(raw);
  await expect(page.getByLabel("Distillation author", { exact: true })).toHaveValue("  Author A  ");await expect(page.getByLabel("Distillation rights")).toHaveValue("  Retained original declaration  ");
  await expect(page.getByLabel("Distillation requirements")).toHaveValue(requirements);await expect(page.getByLabel("Distillation source A")).toHaveValue(initial.scenes[0].id);
  expect(await requests(page)).toEqual([]);expect(await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project())).toEqual(initial);
});

test("Distillation review input belongs to each proposal and survives selection return and reload",async({page})=>{
  const initial=await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project());await reviewer(page,candidateA);
  await page.getByLabel("Distillation reviewer").fill("  Reviewer A  ");await note(page).fill("  Rationale A\nRetain spacing  ");await page.getByLabel("Distillation verdict").selectOption("rejected");
  await page.getByLabel("Distillation review candidate").selectOption(candidateB);await expect(note(page)).not.toHaveValue("  Rationale A\nRetain spacing  ");
  await reviewer(page,candidateB);await note(page).fill("  Rationale B  ");await page.getByLabel("Distillation review candidate").selectOption(candidateA);
  await expect(note(page)).toHaveValue("  Rationale A\nRetain spacing  ");await expect(page.getByLabel("Distillation reviewer")).toHaveValue("  Reviewer A  ");
  await expect(page.getByLabel("Distillation verdict")).toHaveValue("rejected");await page.reload();await open(page);
  await expect(page.getByLabel("Distillation review candidate")).toHaveValue(candidateA);await expect(note(page)).toHaveValue("  Rationale A\nRetain spacing  ");
  await page.getByLabel("Distillation review candidate").selectOption(candidateB);await expect(note(page)).toHaveValue("  Rationale B  ");
  expect(await requests(page)).toEqual([]);expect(await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project())).toEqual(initial);
});

for(const identity of ["id","generation","active_branch"]as const){
  test(`Distillation author and review drafts cannot leak across ${identity}`,async({page})=>{
    const initial=await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project());await author(page);await label(page).fill("Owner A candidate");await reviewer(page);await note(page).fill("Owner A private review draft");
    await page.evaluate(({identity,value})=>window.__DISTILLATION_AUTHORITY__.setIdentity(identity,value),{identity,value:initial[identity]+"-other"});
    await expect(label(page)).not.toHaveValue("Owner A candidate");await expect(note(page)).not.toHaveValue("Owner A private review draft");
    await label(page).fill("Owner B candidate");await reviewer(page);await note(page).fill("Owner B review draft");
    await page.evaluate(({identity,value})=>window.__DISTILLATION_AUTHORITY__.setIdentity(identity,value),{identity,value:initial[identity]});
    await expect(label(page)).toHaveValue("Owner A candidate");await expect(page.getByLabel("Distillation review candidate")).toHaveValue(candidateA);await expect(note(page)).toHaveValue("Owner A private review draft");
    expect(await requests(page)).toEqual([]);
  });
}

test("Distillation original proposal ACK retains later author input and the exact frozen source samples",async({page})=>{
  await author(page);const initial=await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project());const request=await submit(page,"Propose original component");
  await label(page).fill("  Later candidate label  ");await page.getByLabel("Distillation requirements").fill("  Later source requirement\n ");
  await page.evaluate(id=>window.__DISTILLATION_AUTHORITY__.resolve(id),request.id);await expect(label(page)).toHaveValue("  Later candidate label  ");
  await expect(page.getByLabel("Distillation requirements")).toHaveValue("  Later source requirement\n ");
  const saved=await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project());expect(saved.revision).toBe(initial.revision+1);expect(saved.scenes).toEqual(initial.scenes);
  expect(saved.production_design?.distillations?.slice(0,2)).toEqual(initial.production_design?.distillations);
  const added=saved.production_design!.distillations!.at(-1)!;expect(added.input.label).toBe("New authored candidate");expect(added.review).toBeNull();
  expect(added.samples.map(sample=>sample.nodes)).toEqual(added.input.positives.map(selector=>selector.node_ids.map(id=>initial.scenes.find(scene=>scene.id===selector.scene_id)!.nodes.find(node=>node.id===id))));
  expect((await requests(page)).length).toBe(1);
});

test("Distillation original review ACK cannot erase another proposal's later reviewer or rationale",async({page})=>{
  await reviewer(page,candidateA);const request=await submit(page,"Record review only");
  await reviewer(page,candidateB);await page.getByLabel("Distillation reviewer").fill("Reviewer B remains local");await note(page).fill("Later B rationale remains editable");
  await page.evaluate(id=>window.__DISTILLATION_AUTHORITY__.resolve(id),request.id);
  await expect(page.getByLabel("Distillation review candidate")).toHaveValue(candidateB);await expect(note(page)).toHaveValue("Later B rationale remains editable");
  await expect(page.getByLabel("Distillation reviewer")).toHaveValue("Reviewer B remains local");
  const saved=await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project());expect(saved.production_design!.distillations!.find(value=>value.input.id===candidateA)!.review?.reviewer).toBe("Independent test reviewer");
  expect(saved.production_design!.distillations!.find(value=>value.input.id===candidateB)!.review).toBeNull();expect((await requests(page)).length).toBe(1);
});

test("Distillation later rationale remains available after an immutable review ACK without rewriting that review",async({page})=>{
  await reviewer(page);const request=await submit(page,"Record review only");await note(page).fill("  Later local reconsideration  ");
  await page.evaluate(id=>window.__DISTILLATION_AUTHORITY__.resolve(id),request.id);await expect(note(page)).toHaveValue("  Later local reconsideration  ");
  await expect(button(page,"Record review only")).toBeDisabled();const saved=await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project());
  expect(saved.production_design!.distillations!.find(value=>value.input.id===candidateA)!.review?.rationale).toBe("Compatible source proposal; no executable admission.");
  await page.reload();await open(page);await expect(note(page)).toHaveValue("  Later local reconsideration  ");expect(await requests(page)).toEqual([]);
  expect(await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project())).toEqual(saved);
});

for(const target of ["author","review"]as const){
  test(`Distillation ${target} matching observed values cannot acknowledge a lost original reply or replay on reload`,async({page})=>{
    if(target==="author")await author(page);else await reviewer(page);
    const request=await submit(page,target==="author"?"Propose original component":"Record review only");
    const field=target==="author"?label(page):note(page);await field.fill("Later local input survives uncertainty");
    await page.evaluate(id=>window.__DISTILLATION_AUTHORITY__.observeOriginal(id),request.id);
    await expect(page.getByRole("region",{name:"Unconfirmed Distillation "+target,exact:true})).toBeVisible();
    await page.evaluate(id=>window.__DISTILLATION_AUTHORITY__.reject(id,"Original Distillation response unavailable"),request.id);
    const saved=await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project());await page.reload();await open(page);
    await expect(field).toHaveValue("Later local input survives uncertainty");await expect(page.getByRole("region",{name:"Unconfirmed Distillation "+target,exact:true})).toBeVisible();
    await expect(button(page,target==="author"?"Propose original component":"Record review only")).toBeDisabled();
    expect(await requests(page)).toEqual([]);expect(await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project())).toEqual(saved);
  });
  test(`Distillation ${target} reply without its submitted operation remains UNKNOWN`,async({page})=>{
    if(target==="author")await author(page);else await reviewer(page);
    const request=await submit(page,target==="author"?"Propose original component":"Record review only");
    await page.evaluate(id=>window.__DISTILLATION_AUTHORITY__.resolveWithoutOperation(id),request.id);
    await expect(page.getByRole("region",{name:"Unconfirmed Distillation "+target,exact:true})).toBeVisible();expect((await requests(page)).length).toBe(1);
  });
  for(const fault of ["data","pointer"]as const){
    test(`Distillation ${target} silent ${fault} recovery failure blocks dispatch and preserves input`,async({page})=>{
      if(target==="author")await author(page);else await reviewer(page);const initial=await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project());
      await page.evaluate(fault=>{
        const original=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){
          if(fault==="data"&&this===localStorage&&key.startsWith("motionwright.component-distillation-recovery.v1:"))return;
          if(fault==="pointer"&&this===sessionStorage&&key.startsWith("motionwright.component-distillation-writer.v1:")){this.removeItem(key);return;}
          return original.call(this,key,value);
        };window.__DISTILLATION_RESTORE_STORAGE__=()=>{Storage.prototype.setItem=original;};
      },fault);
      await button(page,target==="author"?"Propose original component":"Record review only").click();
      await expect(page.getByRole("alert",{name:"Distillation "+target+" backup warning",exact:true})).toBeVisible();
      expect(await requests(page)).toEqual([]);expect(await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project())).toEqual(initial);
      await page.evaluate(()=>window.__DISTILLATION_RESTORE_STORAGE__?.());
    });
  }
}

test("Distillation explicit raw JSON download remains available when all local recovery writes fail",async({page})=>{
  const initial=await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project());await author(page);
  await page.evaluate(()=>{const original=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){if(this===localStorage)throw new Error("Local storage unavailable");return original.call(this,key,value);};});
  await label(page).fill("  Downloaded author input  ");await page.getByLabel("Distillation requirements").fill("  One raw requirement\n\n ");
  const downloadPromise=page.waitForEvent("download");await button(page,"Download Distillation author draft").click();const download=await downloadPromise;
  const path=await download.path();if(!path)throw new Error("No downloaded draft file.");const parsed=JSON.parse(await readFile(path,"utf8"));
  expect(parsed.draft.raw.label).toBe("  Downloaded author input  ");expect(parsed.draft.raw.requirements).toBe("  One raw requirement\n\n ");
  expect(parsed).not.toHaveProperty("effect_grant");expect(parsed.draft).not.toHaveProperty("effect_grant");
  expect(await requests(page)).toEqual([]);expect(await page.evaluate(()=>window.__DISTILLATION_AUTHORITY__.project())).toEqual(initial);
});
