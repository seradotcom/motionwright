import { expect, test, type Page } from "@playwright/test";
import { fixtureProject } from "../src/fixture";
import type { Project } from "../src/types";
import type { ReviewAuthorityHarness } from "./fixtures/review-authority";

// Isolated UI observations of preserved source snapshots. The existing Review
// fixture schedules replies; these tests do not claim SQLite or native evidence.
const fixture="/tests/fixtures/review-authority.html";
const panel=(page:Page)=>page.getByRole("region",{name:"Creative alternatives evidence"});
const differences=(page:Page)=>panel(page).getByLabel("Source alternative differences");
const pagination=(page:Page)=>panel(page).getByRole("group",{name:"Alternative differences pagination"});
const scenario=(count=65)=>{
  const project=structuredClone(fixtureProject);
  project.locks=[];
  const template=structuredClone(project.scenes[0]);
  project.scenes=Array.from({length:Math.max(1,count)},(_,index)=>({
    ...structuredClone(template),id:(index+1).toString(16).padStart(8,"0")+"-0000-4000-8000-000000000001",
    name:"Comparison scene "+(index+1),objective:"Original scene "+(index+1),
    start:{num:String(index),den:"1"},duration:{num:"1",den:"1"},nodes:[],beats:[],
  }));
  for(const workspace of project.branch_workspaces){
    workspace.current_state.scenes=structuredClone(project.scenes);
    if(workspace.branch_id!==project.active_branch&&count)
      workspace.current_state.scenes.forEach((scene,index)=>{scene.objective="Alternative scene "+(index+1);});
  }
  return project;
};
async function open(page:Page,project:Project){
  await page.addInitScript(value=>{
    sessionStorage.setItem("TEST-ONLY.review.synthetic-props.v1",JSON.stringify({project:value,sceneId:value.scenes[0].id}));
  },project);
  await page.goto(fixture);
  await expect(panel(page).getByRole("heading",{name:"Compare editable alternatives"})).toBeVisible();
}
const observed=(page:Page)=>page.evaluate(()=>{
  const harness=(window as Window&{__REVIEW_AUTHORITY__:ReviewAuthorityHarness}).__REVIEW_AUTHORITY__;
  return {project:harness.project(),requests:harness.requests()};
});

test("every source difference is inspectable in bounded pages without losing navigation focus or mutating the project",async({page})=>{
  const source=scenario();await open(page,source);
  const before=await observed(page), identities:string[]=[];
  const next=pagination(page).getByRole("button",{name:"Next changes",exact:true});
  await expect(differences(page).locator("article")).toHaveCount(30);
  await expect(pagination(page).getByRole("status")).toHaveText("Showing changes 1\u201330 of 65. Page 1 of 3.");
  identities.push(...await differences(page).locator("article .mono").allTextContents());
  await next.focus();await next.press("Enter");
  await expect(pagination(page).getByRole("status")).toHaveText("Showing changes 31\u201360 of 65. Page 2 of 3.");
  await expect(next).toBeFocused();await expect(differences(page).locator("article")).toHaveCount(30);
  identities.push(...await differences(page).locator("article .mono").allTextContents());
  await next.press("Enter");
  await expect(pagination(page).getByRole("status")).toHaveText("Showing changes 61\u201365 of 65. Page 3 of 3.");
  await expect(next).toBeFocused();await expect(next).toHaveAttribute("aria-disabled","true");
  await expect(differences(page).locator("article")).toHaveCount(5);
  identities.push(...await differences(page).locator("article .mono").allTextContents());
  expect(identities).toEqual(source.scenes.map(scene=>"scene:"+scene.id.slice(0,8)));
  expect(new Set(identities).size).toBe(65);
  await next.press("Enter");
  await expect(differences(page).locator("article")).toHaveCount(5);
  const previous=pagination(page).getByRole("button",{name:"Previous changes",exact:true});
  await previous.click();await expect(previous).toBeFocused();
  await expect(pagination(page).getByRole("status")).toContainText("Page 2 of 3");
  expect(await observed(page)).toEqual(before);
});

test("a new observed source starts at its first page and preserves authored review context",async({page})=>{
  const source=scenario();await open(page,source);
  await panel(page).getByLabel("Alternative review rationale").fill("The authored reason remains while another source revision is inspected.");
  const record=panel(page).getByRole("button",{name:"Record manual positive observation",exact:true});
  await expect(record).toBeEnabled();
  const next=pagination(page).getByRole("button",{name:"Next changes",exact:true});
  await next.click();await next.click();
  await expect(pagination(page).getByRole("status")).toContainText("Page 3 of 3");
  await page.evaluate(async()=>{
    const harness=(window as Window&{__REVIEW_AUTHORITY__:ReviewAuthorityHarness}).__REVIEW_AUTHORITY__;
    const project=harness.project();
    await harness.externalChange({type:"update_scene_objective",scene_id:project.scenes[0].id,objective:"A newly observed source objective"});
  });
  await expect(pagination(page).getByRole("status")).toHaveText("Showing changes 1\u201330 of 65. Page 1 of 3.");
  await expect(differences(page).locator("article")).toHaveCount(30);
  await expect(panel(page).getByLabel("Alternative review rationale")).toHaveValue("The authored reason remains while another source revision is inspected.");
  await expect(record).toBeDisabled();
  await panel(page).getByRole("button",{name:"Review alternative reason on current revision",exact:true}).click();
  await expect(record).toBeEnabled();
  expect((await observed(page)).requests).toEqual([]);
});

test("returning to a prior source identity never revives its old comparison page",async({page})=>{
  const source=scenario();await open(page,source);
  const next=pagination(page).getByRole("button",{name:"Next changes",exact:true});
  await next.click();await next.click();
  await expect(pagination(page).getByRole("status")).toContainText("Page 3 of 3");
  await page.evaluate(async()=>{
    const harness=(window as Window&{__REVIEW_AUTHORITY__:ReviewAuthorityHarness}).__REVIEW_AUTHORITY__;
    await harness.setIdentity("generation","a0000001-0000-4000-8000-000000000001");
  });
  await expect(pagination(page).getByRole("status")).toHaveText("Showing changes 1\u201330 of 65. Page 1 of 3.");
  await page.evaluate(async generation=>{
    const harness=(window as Window&{__REVIEW_AUTHORITY__:ReviewAuthorityHarness}).__REVIEW_AUTHORITY__;
    await harness.setIdentity("generation",generation);
  },source.generation);
  await expect(pagination(page).getByRole("status")).toHaveText("Showing changes 1\u201330 of 65. Page 1 of 3.");
  await expect(differences(page).locator("article")).toHaveCount(30);
  const after=await observed(page);expect(after.project).toEqual(source);expect(after.requests).toEqual([]);
});

test("same-copy claim source changes remain identifiable and both branch contexts enter only an explicit open review",async({page})=>{
  const source=scenario(0),candidate=source.branches.find(branch=>branch.id!==source.active_branch)!;
  const active=source.branches.find(branch=>branch.id===source.active_branch)!;
  const claim={id:"e0000001-0000-4000-8000-000000000001",text:"Identical authored claim text",
    source:{kind:"project_revision" as const,revision:3},context:"A synthetic reference fixture",source_revision:"fixture-source-r3"};
  source.brief.claims=[structuredClone(claim)];
  for(const workspace of source.branch_workspaces)workspace.current_state.brief.claims=[structuredClone(claim)];
  const alternative=source.branch_workspaces.find(workspace=>workspace.branch_id===candidate.id)!.current_state.brief.claims[0];
  alternative.source={kind:"project_revision",revision:7};alternative.source_revision="fixture-source-r7";
  await open(page,source);
  await expect(differences(page).locator("article")).toHaveCount(1);
  await expect(differences(page)).toContainText("claim:"+claim.id);
  await expect(differences(page)).toContainText("Changed fields: source, source revision.");
  const sourceComparison=differences(page).getByLabel("Claim source comparison "+claim.id);
  await expect(sourceComparison).toContainText("Project revision r3");
  await expect(sourceComparison).toContainText("Project revision r7");
  await expect(sourceComparison).toContainText("fixture-source-r3");
  await expect(sourceComparison).toContainText("fixture-source-r7");
  await expect(panel(page)).toContainText("Active base/head: r"+active.base_revision+"/r"+active.head_revision);
  await expect(panel(page)).toContainText("Alternative base/head: r"+candidate.base_revision+"/r"+candidate.head_revision);
  expect((await observed(page)).project).toEqual(source);expect((await observed(page)).requests).toEqual([]);
  await panel(page).getByLabel("Alternative review rationale").fill("The claim source changed while the exact statement stayed the same.");
  await panel(page).getByRole("button",{name:"Record manual positive observation",exact:true}).click();
  await expect.poll(async()=>(await observed(page)).requests.length).toBe(1);
  const request=(await observed(page)).requests[0];
  expect(request.change.type).toBe("add_review");
  if(request.change.type!=="add_review")throw new Error("Unexpected mutation in the isolated review fixture.");
  expect(request.change).toMatchObject({resource:"project:"+source.id,kind:"creative",start:null,end:null,locale:null,profile_id:null});
  expect(request.change.body).toContain("Active base/head: r"+active.base_revision+"/r"+active.head_revision);
  expect(request.change.body).toContain("Alternative base/head: r"+candidate.base_revision+"/r"+candidate.head_revision);
  expect(request.change.body).not.toContain("fixture-source-r3");expect(request.change.body).not.toContain("fixture-source-r7");
  expect(request.change.body).not.toContain(claim.text);
  expect(request.change.body).toContain("No automatic preference, source quality, native render or reusable component distillation is claimed.");
  expect((await observed(page)).project).toEqual(source);
});
