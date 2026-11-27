import {expect,test} from "@playwright/test";
import {proposalButton as button,readStudioProject} from "./support/scopedPaintMotionJourney";

test("caption and sound craft entries focus their original audio controls without saving",async({page})=>{
  await page.goto("/");
  await button(page,"Add scene").click();
  await page.getByLabel("New scene name",{exact:true}).fill("Craft sound source");
  await button(page,"Add").click();
  const original=await readStudioProject(page);
  const soundScene=original.scenes.find(scene=>scene.name==="Craft sound source")!;
  for(const [definition,target] of [
    ["Caption emphasis","transcript"],["Focus hit","sound-events"],["Transition tail","sound-events"],
    ["Energy bed","music-source"],["Silence release","silence-release"],
  ] as const){
    await button(page,"Production").click();
    await button(page,"Craft library").click();
    const library=page.getByRole("region",{name:"Craft component library",exact:true});
    await library.getByRole("button",{name:new RegExp(definition)}).click();
    await library.getByRole("button",{name:"Open authoring editor",exact:true}).press("Enter");
    const audio=page.getByRole("region",{name:"Audio workspace",exact:true});
    const selected=audio.locator("[data-craft-audio-target="+target+"]");
    await expect(selected).toBeVisible();
    await expect(selected).toBeFocused();
    if(definition==="Focus hit"){
      await expect(audio.getByLabel("SFX semantic event")).toHaveValue("scene_reveal");
      await expect(button(page,"Apply Focus hit to draft")).toBeDisabled();
      await audio.getByLabel("SFX source scene").selectOption(soundScene.id);
      await audio.getByLabel("SFX Offset frames").fill("5");
      await button(page,"Apply Focus hit to draft").click();
      await expect(audio.getByLabel("SFX semantic event")).toHaveValue("ui_focus");
      await expect(audio.getByLabel("SFX Tail policy")).toHaveValue("within_scene");
    }
    if(definition==="Transition tail"){
      await expect(audio.getByLabel("SFX semantic event")).toHaveValue("ui_focus");
      await button(page,"Apply Transition tail to draft").click();
      await expect(audio.getByLabel("SFX semantic event")).toHaveValue("transition");
      await expect(audio.getByLabel("SFX Tail policy")).toHaveValue("continue_across_cuts");
      await expect(audio.getByLabel("SFX Offset frames")).toHaveValue("5");
      await expect(audio.getByLabel("SFX imported WAV")).toHaveValue("");
    }
  }
  expect((await readStudioProject(page)).revision).toBe(original.revision);
});
