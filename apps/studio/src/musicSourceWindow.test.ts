import {describe,expect,it} from "vitest";
import {fixtureProject} from "./fixture";
import {defaultOriginalMix,emptyDeliveryRecipe,editRecipeField,parseMusicSourceStartSample,
  newRecipeDraft,proposedDeliveryRecipe,recipeDraftScope,recipeFields,validateDeliveryRecipe} from "./deliveryRecipe";
import {assertRecipeHistoryPayload,recipeFingerprint} from "./deliveryRecipeDraft";
import {assertAudioRecipeExtensions} from "./audioRecipeContract";
import type {DeliveryRecipe} from "./deliveryRecipe";

function fixture() {
  const project=structuredClone(fixtureProject);
  const profile=project.deliverables[0];
  const music=project.assets.find(a=>a.media_type==="audio/wav")!;
  const recipe:DeliveryRecipe={...emptyDeliveryRecipe(),original_mix:{...defaultOriginalMix(),music_asset_id:music.id}};
  const draft=newRecipeDraft(recipeDraftScope(project,profile.id,"mix"),"mix",project.revision);
  return {project,profile,music,recipe,draft};
}
describe("explicit music source window",()=>{
  it("distinguishes absence and an explicit zero; never widens or rounds the sample clock",()=>{
    expect(parseMusicSourceStartSample("")).toBeUndefined();
    expect(parseMusicSourceStartSample("0000")).toBe(0);
    expect(parseMusicSourceStartSample("48000")).toBe(48000);
    for(const raw of ["-1","1.5","1e3","Infinity","8640000","9".repeat(128)])
      expect(()=>parseMusicSourceStartSample(raw)).toThrow();
  });
  it("retains legacy serialized shape and changes the exact recipe fingerprint only on explicit selection",()=>{
    const {project,profile,recipe,draft}=fixture();
    expect(Object.hasOwn(recipe.original_mix!,"music_source_start_sample")).toBe(false);
    const changed=editRecipeField(draft,"music_source_start_sample","0",recipeFields(recipe,"mix"));
    const candidate=proposedDeliveryRecipe(recipe,changed);
    expect(candidate.original_mix!.music_source_start_sample).toBe(0);
    expect(recipeFingerprint(candidate)).not.toBe(recipeFingerprint(recipe));
    expect(()=>validateDeliveryRecipe(project,profile.id,candidate)).not.toThrow();
    expect(()=>assertRecipeHistoryPayload(candidate)).not.toThrow();
    const clear=editRecipeField(draft,"music_source_start_sample","",recipeFields(candidate,"mix"));
    expect(proposedDeliveryRecipe(candidate,clear)).toEqual(recipe);
  });
  it("rejects fractional, null, absent source, or excessive explicit sample windows",()=>{
    const {project,profile,recipe}=fixture();
    for(const invalid of [null,-1,0.5,8640000,"0",undefined]) {
      const candidate={...recipe,original_mix:{...recipe.original_mix!,music_source_start_sample:invalid}};
      expect(()=>assertAudioRecipeExtensions(candidate)).toThrow();
    }
    recipe.original_mix!.music_source_start_sample=0;
    recipe.original_mix!.music_asset_id=null;
    expect(()=>validateDeliveryRecipe(project,profile.id,recipe)).toThrow("selected audio asset");
  });
  it("requires the full exact 48k cut and the existing decode budget",()=>{
    const {project,profile,recipe}=fixture();
    recipe.original_mix!.music_source_start_sample=48000;
    expect(()=>validateDeliveryRecipe(project,profile.id,recipe)).not.toThrow();
    profile.included_scene_ids=[project.scenes[0].id];
    expect(()=>validateDeliveryRecipe(project,profile.id,recipe)).toThrow("full original cut");
    profile.included_scene_ids=project.scenes.map(s=>s.id);
    expect(()=>validateDeliveryRecipe(project,profile.id,recipe)).not.toThrow();
    profile.audio_sample_rate_hz=44100;
    expect(()=>validateDeliveryRecipe(project,profile.id,recipe)).toThrow("48 kHz");
    profile.audio_sample_rate_hz=48000;
    recipe.original_mix!.music_source_start_sample=8639999;
    expect(()=>validateDeliveryRecipe(project,profile.id,recipe)).toThrow("180-second");
    recipe.original_mix!.music_source_start_sample=0;
    project.scenes[0].duration={num:"1",den:"7"};
    expect(()=>validateDeliveryRecipe(project,profile.id,recipe)).toThrow("exact 48");
  });
  it("retains a saved music window on unrelated edits and refuses silent transfer to another asset",()=>{
    const {recipe,draft}=fixture();
    recipe.original_mix!.music_source_start_sample=48000;
    const fields=recipeFields(recipe,"mix");
    expect(proposedDeliveryRecipe(recipe,editRecipeField(draft,"sfx_gain_db","-11",fields)).original_mix!.music_source_start_sample).toBe(48000);
    const changed=editRecipeField(draft,"music_asset_id","12345678-1234-4567-89ab-123456789001",fields);
    expect(()=>proposedDeliveryRecipe(recipe,changed)).toThrow("explicitly revise");
    const explicit=editRecipeField(changed,"music_source_start_sample","0",fields);
    expect(proposedDeliveryRecipe(recipe,explicit).original_mix!.music_source_start_sample).toBe(0);
  });
});
