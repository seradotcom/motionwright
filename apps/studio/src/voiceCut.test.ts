import { describe, expect, it } from "vitest";
import { fixtureBootstrap } from "./fixture";
import { deriveVoiceCut, requireCurrentVoiceCut, validateVoiceCutIntervals, voiceCutDigest } from "./voiceCut";
import { audioSampleToProject, buildAudioReviewClock, projectToAudioMedia } from "./audioReviewClock";
import { defaultOriginalMix, emptyDeliveryRecipe, editRecipeField, newRecipeDraft, proposedDeliveryRecipe, recipeDraftScope, recipeFields, validateDeliveryRecipe } from "./deliveryRecipe";
import { assertRecipeHistoryPayload, recipeFingerprint } from "./deliveryRecipeDraft";
import { applyProductionDesignChange, emptyProductionDesign } from "./creativeProduction";

function fixture() {
  const project = structuredClone(fixtureBootstrap.project), seed = structuredClone(project.scenes[0]);
  const sceneId = (i: number) => "00000000-0000-4000-8000-00000000010" + i;
  project.scenes = Array.from({length:3}, (_, i) => ({...structuredClone(seed), id:sceneId(i),
    start:{num:String(i*2),den:"1"},duration:{num:"2",den:"1"}}));
  const profile = project.deliverables[0];
  profile.id = "00000000-0000-4000-8000-000000000200";
  profile.audio_sample_rate_hz = 48000;
  profile.included_scene_ids = [sceneId(0), sceneId(2)];
  const track = {id:"00000000-0000-4000-8000-000000000300",asset_id:"00000000-0000-4000-8000-000000000400",
    label:"Original",source_sha256:"a".repeat(64),sample_rate_hz:48000,channels:2,
    measured_duration:{num:"6",den:"1"},loudness_lufs:null,true_peak_dbfs:null};
  project.audio.voice_tracks = [track]; project.audio.active_voice_track_id = track.id;
  project.audio.transcript = [];
  project.assets.push({id:track.asset_id,name:"original.wav",media_type:"audio/wav",content_sha256:track.source_sha256,source_revision:null});
  profile.voice_track_id = track.id;
  const music = project.assets.find(a=>a.media_type==="audio/wav" && a.id!==track.asset_id)!;
  const recipe = {...emptyDeliveryRecipe(), original_mix:{...defaultOriginalMix(),music_asset_id:music.id}};
  project.production_design = emptyProductionDesign();
  return {project,profile,recipe,track};
}

describe("explicit original voice montage binding", () => {
  it("hashes the exact ordered original samples using a shared closed wire", async () => {
    const {project,profile} = fixture(), cut = deriveVoiceCut(project,profile);
    expect(cut.sourceFrames).toBe(288000); expect(cut.outputFrames).toBe(192000);
    expect(cut.spans.map(s=>[s.sourceStart,s.sourceEnd,s.outputStart])).toEqual([[0,96000,0],[192000,288000,96000]]);
    const digest = await voiceCutDigest(cut.wire);
    expect(digest).toBe("e398be6acc88b7618f6f3bc6130f12fb1020901d48ed7157c675cc77feeee7f8");
    expect(await requireCurrentVoiceCut(project,profile,digest)).toEqual(cut);
    const prior = structuredClone(project);
    expect(project).toEqual(prior);
    profile.included_scene_ids.reverse();
    await expect(requireCurrentVoiceCut(project,profile,digest)).rejects.toThrow(/approved project scene order/);
    profile.included_scene_ids = [project.scenes[0].id, project.scenes[1].id];
    await expect(requireCurrentVoiceCut(project,profile,digest)).rejects.toThrow(/Review and save/);
    profile.included_scene_ids = [project.scenes[0].id, project.scenes[2].id];
    profile.id = "00000000-0000-4000-8000-000000000201";
    await expect(requireCurrentVoiceCut(project,profile,digest)).rejects.toThrow();
  });
  it("keeps an explicit binding through recipe history and draft save; rejects invalid new bindings", async () => {
    const {project,profile,recipe} = fixture();
    const digest = await voiceCutDigest(deriveVoiceCut(project,profile).wire);
    const draft = newRecipeDraft(recipeDraftScope(project,profile.id,"mix"),"mix",project.revision);
    const next = proposedDeliveryRecipe(recipe, editRecipeField(draft,"voice_cut_sha256",digest,recipeFields(recipe,"mix")));
    expect(next.original_mix?.voice_cut_sha256).toBe(digest);
    expect(recipeFingerprint(next)).not.toBe(recipeFingerprint(recipe));
    validateDeliveryRecipe(project,profile.id,next);
    assertRecipeHistoryPayload(next);
    await expect(applyProductionDesignChange(project,{type:"set_delivery_recipe",deliverable_profile_id:profile.id,
      recipe:{...next,original_mix:{...next.original_mix!,voice_cut_sha256:"b".repeat(64)}}})).rejects.toThrow();
    await applyProductionDesignChange(project,{type:"set_delivery_recipe",deliverable_profile_id:profile.id,recipe:next});
    expect(project.production_design?.delivery_recipes?.[profile.id]).toEqual(next);
    expect(proposedDeliveryRecipe(next,editRecipeField(draft,"voice_cut_sha256","",recipeFields(next,"mix")))).toEqual(recipe);
  });
  it("maps the cut playback to original project times without inventing omitted scene time", async () => {
    const {project,profile,recipe} = fixture(), digest=await voiceCutDigest(deriveVoiceCut(project,profile).wire);
    project.production_design = {...emptyProductionDesign(),delivery_recipes:{[profile.id]:{...recipe,original_mix:{...recipe.original_mix,voice_cut_sha256:digest}}}};
    const clock=buildAudioReviewClock(project,{kind:"mix",sourceId:profile.id});
    expect(clock.sampleCount).toBe(192000);
    expect(audioSampleToProject(clock,0)).toEqual({num:"0",den:"1"});
    expect(audioSampleToProject(clock,96000)).toEqual({num:"4",den:"1"});
    expect(audioSampleToProject(clock,192000)).toEqual({num:"6",den:"1"});
    expect(projectToAudioMedia(clock,5)).toBe(3);
    expect(projectToAudioMedia(clock,1)).toBe(1);
    expect(projectToAudioMedia(clock,3)).toBeNull();
  });
  it("admits only contained, non-overlapping reviewed intervals in the selected cut", () => {
    const {project,profile,track}=fixture(), cut=deriveVoiceCut(project,profile);
    const segment=(start:string,end:string)=>({id:crypto.randomUUID(),voice_track_id:track.id,
      start:{num:start,den:"4"},end:{num:end,den:"4"},text:"Synthetic",speaker:null,alignment:{kind:"manual" as const}});
    project.audio.transcript=[segment("1","2"),segment("9","10"),segment("17","19")];
    expect(()=>validateVoiceCutIntervals(project,profile,cut)).not.toThrow();
    project.audio.transcript=[segment("7","9")];
    expect(()=>validateVoiceCutIntervals(project,profile,cut)).toThrow(/crosses/);
    project.audio.transcript=[segment("9","10")];
    expect(()=>validateVoiceCutIntervals(project,profile,cut)).toThrow(/1 to 64/);
    project.audio.transcript=[segment("1","4"),segment("2","3")];
    expect(()=>validateVoiceCutIntervals(project,profile,cut)).toThrow(/overlap/);
  });
  it("refuses nonexact boundaries, source drift, duplicate and absent scene selections", async () => {
    for (const mutate of [
      (p:ReturnType<typeof fixture>)=>{p.project.scenes[0].duration={num:"1",den:"7"};},
      (p:ReturnType<typeof fixture>)=>{p.track.measured_duration={num:"5",den:"1"};},
      (p:ReturnType<typeof fixture>)=>{p.profile.included_scene_ids.push(p.profile.included_scene_ids[0]);},
      (p:ReturnType<typeof fixture>)=>{p.profile.included_scene_ids=["missing"];},
      (p:ReturnType<typeof fixture>)=>{p.track.source_sha256="b".repeat(64);},
    ]) { const data=fixture(); mutate(data); expect(()=>deriveVoiceCut(data.project,data.profile)).toThrow(); }
  });
});
