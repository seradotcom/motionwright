import { expect,test } from "@playwright/test";
import { proposalButton as button,readStudioProject } from "./support/scopedPaintMotionJourney";
import { beatTimelineEditor,commitBeatOperation,createBeatTimelineScene,prepareBeatOperation,reopenBeatTimeline } from "./support/sceneBeatTimelineJourney";

// Product UI with synthetic browser storage. Native and actual Service acceptance
// belong to the companion; this journey never claims a physical renderer result.
test("Scene beat timing completes split, reorder, ripple, trim, move and conditional undo through actual controls",async({page},info)=>{
  test.setTimeout(150_000);await page.goto("/");
  const context=await createBeatTimelineScene(page),base=context.base,editor=beatTimelineEditor(page);
  await editor.getByLabel("Beat operation start",{exact:true}).fill("1/");
  await editor.getByLabel("Beat timing rationale",{exact:true}).fill("Separate the opening claim from its support.");
  await expect(editor.getByRole("button",{name:"Preview beat timing",exact:true})).toBeDisabled();
  expect(await readStudioProject(page)).toEqual(base);
  await button(page,"Back to storyboard").click();await button(page,"Edit beat timeline").click();
  await expect(editor.getByLabel("Beat operation start",{exact:true})).toHaveValue("1/");
  await prepareBeatOperation(page,context.beatId,"split",{start:"2"},"Separate the opening claim from its support.");
  expect(await readStudioProject(page)).toEqual(base);
  for(const width of [1440,1024]) {
    await page.setViewportSize({width,height:940});
    const controls=editor.locator(".beat-timing-fields"),bounds=await controls.boundingBox();expect(bounds).not.toBeNull();
    for(const field of await controls.locator("input,select").all()){
      const box=await field.boundingBox();expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(bounds!.x);expect(box!.x+box!.width).toBeLessThanOrEqual(bounds!.x+bounds!.width+1);
    }
    await editor.screenshot({path:info.outputPath("scene-beat-timing-"+width+".png")});
  }
  let saved=await commitBeatOperation(page,base.revision);
  const parts=()=>saved.scenes.find(scene=>scene.id===context.sceneId)!.beats;
  expect(parts().map(beat=>[beat.start,beat.duration])).toEqual([[{num:"0",den:"1"},{num:"2",den:"1"}],[{num:"2",den:"1"},{num:"4",den:"1"}]]);
  expect(parts()[0].id).toBe(context.beatId);
  const right=parts()[1].id;
  await prepareBeatOperation(page,right,"split",{start:"4"},"Separate the supporting detail from the conclusion.");
  saved=await commitBeatOperation(page,saved.revision);
  expect(parts()).toHaveLength(3);
  const last=parts()[2].id;
  await prepareBeatOperation(page,last,"reorder",{position:"1"},"Open with the conclusion before the evidence.");
  saved=await commitBeatOperation(page,saved.revision);
  expect(parts().map(beat=>beat.id)).toEqual([last,context.beatId,right]);
  await prepareBeatOperation(page,last,"ripple",{duration:"1"},"Tighten the opening while preserving later beat lengths.");
  saved=await commitBeatOperation(page,saved.revision);
  expect(parts().map(beat=>beat.start)).toEqual([{num:"0",den:"1"},{num:"1",den:"1"},{num:"3",den:"1"}]);
  await prepareBeatOperation(page,right,"trim",{start:"7/2",end:"5"},"Retain the central supporting interval.");
  saved=await commitBeatOperation(page,saved.revision);
  expect(parts()[2].duration).toEqual({num:"3",den:"2"});
  await prepareBeatOperation(page,right,"move",{start:"4"},"Give the preceding claim a deliberate gap.");
  const beforeMove=saved;
  saved=await commitBeatOperation(page,saved.revision);
  expect(parts()[2].start).toEqual({num:"4",den:"1"});
  expect(saved.scenes.find(scene=>scene.id===context.sceneId)!.nodes).toEqual(base.scenes.find(scene=>scene.id===context.sceneId)!.nodes);
  expect(saved.scenes.filter(scene=>scene.id!==context.sceneId)).toEqual(base.scenes.filter(scene=>scene.id!==context.sceneId));
  const history=editor.getByRole("region",{name:"Saved beat timing history",exact:true});
  await history.getByRole("button",{name:"Review inverse r"+saved.revision,exact:true}).click();
  await editor.getByRole("button",{name:"Preview beat timing",exact:true}).click();
  saved=await commitBeatOperation(page,saved.revision);
  expect(parts()).toEqual(beforeMove.scenes.find(scene=>scene.id===context.sceneId)!.beats);
  await history.getByRole("button",{name:"Review inverse r"+(base.revision+1),exact:true}).click();
  await expect(editor.getByRole("button",{name:"Preview beat timing",exact:true})).toBeDisabled();
  await expect(editor.getByRole("status").filter({hasText:"conditional source"})).toBeVisible();
  expect(await readStudioProject(page)).toEqual(saved);
});

test("Beat timing retains incomplete raw input across a page reload without fabricating fixture project persistence",async({page})=>{
  await page.goto("/");
  const base=await readStudioProject(page),scene=base.scenes.find(scene=>scene.beats.length>0)!;
  await reopenBeatTimeline(page,scene.name);
  const editor=beatTimelineEditor(page);
  await editor.getByLabel("Timeline beat",{exact:true}).selectOption(scene.beats[0].id);
  await editor.getByLabel("Beat timing operation",{exact:true}).selectOption("trim");
  await editor.getByLabel("Beat operation start",{exact:true}).fill("1/");
  await editor.getByLabel("Beat operation end",{exact:true}).fill("3e");
  await editor.getByLabel("Beat timing rationale",{exact:true}).fill("  Retain this exact unfinished timing intent.  ");
  await expect(editor.getByRole("button",{name:"Preview beat timing",exact:true})).toBeDisabled();
  await page.reload();await reopenBeatTimeline(page,scene.name);
  await expect(editor.getByLabel("Beat operation start",{exact:true})).toHaveValue("1/");
  await expect(editor.getByLabel("Beat operation end",{exact:true})).toHaveValue("3e");
  await expect(editor.getByLabel("Beat timing rationale",{exact:true})).toHaveValue("  Retain this exact unfinished timing intent.  ");
  // This demo constructs only fixture timestamps on each page load. Compare
  // every source field and revision; actual SQLite restart equality is separate.
  const sourceFields=(value:unknown)=>JSON.parse(JSON.stringify(value,(key,entry)=>["created_at","updated_at"].includes(key)?"fixture-clock":entry));
  expect(sourceFields(await readStudioProject(page))).toEqual(sourceFields(base));
});
