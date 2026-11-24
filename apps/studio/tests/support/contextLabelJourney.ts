import { expect, type Page } from "@playwright/test";

export async function createContextLabelJourney(page: Page) {
  const revision=page.locator(".command-bar .revision-chip");
  let expected=Number((await revision.innerText()).replace("r",""));
  const saved=async()=>{expected+=1;await expect(revision).toHaveText("r"+expected);};
  const create=page.getByLabel("Create canvas object",{exact:true});
  await create.getByRole("button",{name:"shape",exact:true}).click();await saved();
  let inspector=page.locator(".canvas-draft-inspector");
  await expect(inspector).toHaveCount(1);
  const sourceId=(await inspector.getByLabel("Canvas X",{exact:true}).getAttribute("id"))!.slice("canvas-draft-".length,-2);
  const sourceName=await page.locator(".canvas-tree-row.selected span:not(.canvas-kind)").innerText();
  for(const [field,value] of [["Canvas X","720"],["Canvas Y","400"],["Canvas W","240"],["Canvas H","160"]])
    await inspector.getByLabel(field,{exact:true}).fill(value);
  await inspector.getByRole("button",{name:"Commit transform",exact:true}).click();await saved();
  const pose=page.getByRole("region",{name:"Native pose animation",exact:true});
  for(const [field,value] of [["Pose start x","600"],["Pose start y","400"],["Pose start width","240"],
    ["Pose start height","160"],["Pose start rotation","0"],["Pose end frame","45"]])
    await pose.getByLabel(field,{exact:true}).fill(value);
  await pose.getByLabel("Pose geometry curve",{exact:true}).selectOption("hold");
  await pose.getByRole("button",{name:"Add intermediate pose",exact:true}).click();
  for(const [field,value] of [["frame","15"],["x","660"],["y","400"],["width","240"],["height","160"],["rotation","0"]])
    await pose.getByLabel("Intermediate pose 1 "+field,{exact:true}).fill(value);
  await pose.getByLabel("Intermediate pose 1 curve",{exact:true}).selectOption("ease_out_cubic");
  await pose.getByRole("button",{name:"Create native pose animation",exact:true}).click();await saved();
  await expect(page.getByRole("region",{name:"Typed motion keyframes",exact:true}).locator(".motion-keyframe-row")).toHaveCount(15);
  await create.getByRole("button",{name:"context label",exact:true}).click();await saved();
  inspector=page.locator(".canvas-draft-inspector");
  await expect(inspector).toHaveCount(1);
  const labelId=(await inspector.getByLabel("Canvas X",{exact:true}).getAttribute("id"))!.slice("canvas-draft-".length,-2);
  const labelName=await page.locator(".canvas-tree-row.selected span:not(.canvas-kind)").innerText();
  await expect(inspector.getByLabel("Canvas text",{exact:true})).toHaveValue(sourceName);
  await inspector.getByLabel("Canvas text",{exact:true}).fill("Measured result");
  await inspector.getByRole("button",{name:"Commit text",exact:true}).click();await saved();
  const motion=page.getByRole("region",{name:"Typed motion keyframes",exact:true});
  await page.getByLabel("Motion property",{exact:true}).selectOption("opacity");
  await page.getByLabel("Motion interpolation",{exact:true}).selectOption("linear");
  for(const [time,value] of [["0","0"],["1","1"]]){
    await page.getByLabel("Motion playhead",{exact:true}).fill(time);
    await page.getByLabel("Motion keyframe value",{exact:true}).fill(value);
    await motion.getByRole("button",{name:"Add or replace keyframe at playhead",exact:true}).click();await saved();
  }
  await expect(motion.locator(".motion-keyframe-row")).toHaveCount(2);
  return {sourceId,sourceName,labelId,labelName,revision:expected};
}

export async function verifyContextLabelJourney(page:Page,labelName:string,sourceName:string){
  const label=page.getByRole("button",{name:"Canvas object "+labelName,exact:true});
  const source=page.getByRole("button",{name:"Canvas object "+sourceName,exact:true});
  const x=(element:typeof label)=>element.evaluate(node=>parseFloat((node as HTMLElement).style.left)*1920/100);
  for(const [time,expectedX] of [[0,560],[.25,612.5],[.5,620],[1.499,620],[1.5,680]]){
    await page.getByLabel("Motion playhead",{exact:true}).fill(String(time));
    await expect(label).toHaveAttribute("data-follow-resolved","true");
    await expect.poll(async()=>Math.abs(await x(label)-expectedX)).toBeLessThan(.02);
    await expect.poll(async()=>Math.abs(await x(label)-await x(source)+40)).toBeLessThan(.02);
    await expect.poll(async()=>Number(await label.evaluate(node=>getComputedStyle(node).opacity))).toBeCloseTo(Math.min(time,1),3);
  }
  await page.getByLabel("Motion playhead",{exact:true}).fill("0.25");
}
