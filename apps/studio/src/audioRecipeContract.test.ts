import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import {
  defaultOriginalMix, editRecipeField, newRecipeDraft, proposedDeliveryRecipe,
  recipeDraftScope, recipeFields, validateDeliveryRecipe,
} from "./deliveryRecipe";
import {
  assertRecipeHistoryPayload, createRecipeAuthorState, editRecipeAuthorState,
  prepareRecipeSubmission, recipeFingerprint,
} from "./deliveryRecipeDraft";
import type { DeliveryRecipe } from "./deliveryRecipe";

// Pure typed-reader/author-state controls. These are not native or SQLite evidence.
const id = (n: number) => "abcdef00-0000-4000-8000-" + n.toString(16).padStart(12, "0");
function sample() {
  const project = structuredClone(fixtureProject);
  project.locks = [];
  project.revision = 20;
  const profileId = project.deliverables[0].id;
  const recipe = {
    schema: "motionwright.delivery-recipe/1",
    scene_intents: [],
    original_mix: {
      ...defaultOriginalMix(),
      voice_envelope: { muted: false, fade_in_samples: 480, fade_out_samples: 960 },
      music_envelope: { muted: true, fade_in_samples: 0, fade_out_samples: 0 },
      sfx_envelope: { muted: false, fade_in_samples: 0, fade_out_samples: 480 },
    },
    audio_targets: { target_lufs: -23.001, target_true_peak_dbtp: -1 },
  } as DeliveryRecipe;
  project.production_design = { plan: null, heroes: [], capsules: [], ...project.production_design, delivery_recipes: { [profileId]: recipe } };
  return { project, profileId, recipe };
}
describe("audio recipe consumer regressions", () => {
  it("reads the exact optional targets and nonneutral envelope wire without changing it", () => {
    const { project, profileId, recipe } = sample();
    const before = JSON.stringify(recipe);
    expect(() => validateDeliveryRecipe(project, profileId, recipe)).not.toThrow();
    expect(JSON.stringify(recipe)).toBe(before);
  });
  it("keeps saved bus envelopes and targets when editing an existing scalar mix field", () => {
    const { project, profileId, recipe } = sample();
    const draft = editRecipeField(newRecipeDraft(recipeDraftScope(project, profileId, "mix"), "mix", project.revision),
      "sfx_gain_db", "-11", recipeFields(recipe, "mix"));
    const proposed = proposedDeliveryRecipe(recipe, draft);
    const expected = structuredClone(recipe);
    expected.original_mix!.sfx_gain_db = -11;
    expect(proposed).toEqual(expected);
    expect(recipe.original_mix!.sfx_gain_db).toBe(-8);
  });
  it("keeps a targets-only saved recipe in its exact comparison identity", () => {
    const { recipe } = sample();
    recipe.original_mix = null;
    expect(recipeFingerprint(recipe)).not.toBe("null");
  });
  it("distinguishes a target-only edit even when film and original mix are absent", () => {
    const { recipe } = sample();
    recipe.original_mix = null;
    const changed = structuredClone(recipe) as DeliveryRecipe & { audio_targets: { target_lufs: number; target_true_peak_dbtp: number } };
    changed.audio_targets.target_lufs = -14;
    expect(recipeFingerprint(changed)).not.toBe(recipeFingerprint(recipe));
  });
  it("reads extended historical recipes without consulting today's removed asset/scene membership", () => {
    const { recipe } = sample();
    recipe.scene_intents.push({ scene_id: id(91), role: "hook", archetype: "statement" });
    recipe.original_mix!.music_asset_id = id(92);
    recipe.original_mix!.sfx_asset_id = id(93);
    expect(() => assertRecipeHistoryPayload(recipe)).not.toThrow();
  });
  it("retains all extended intent through the existing prepared SetDeliveryRecipe command", () => {
    const { project, profileId, recipe } = sample();
    const state = editRecipeAuthorState(createRecipeAuthorState(project, profileId, "mix"),
      "sfx_gain_db", "-12", recipeFields(recipe, "mix"));
    const prepared = prepareRecipeSubmission(state, project, id(94));
    const expected = structuredClone(recipe);
    expected.original_mix!.sfx_gain_db = -12;
    expect(prepared.change.recipe).toEqual(expected);
    expect(prepared.state.pending?.fingerprint).toBe(recipeFingerprint(expected));
  });
});

import {
  assertDeliveryAudioTargets, assertOriginalMixBusEnvelope, canonicalOriginalMix,
  requireOriginalMixSfx, savedOriginalMixAuditionRequest, selectedOriginalMixCues,
} from "./audioRecipeContract";
import type { OriginalMixPreviewOptions } from "./nativeMixTypes";
import type { SemanticSfxCue } from "./semanticSfx";

function cue(sceneId: string): SemanticSfxCue {
  return { schema: "motionwright.semantic-sfx/1", id: id(300), scene_id: sceneId,
    event: "scene_reveal", anchor: { kind: "scene_start" }, offset_frames: 0,
    source_asset_id: id(301), source_sha256: "b".repeat(64), source_frames: 30,
    gain_milli_db: -8000, attack_frames: 0, release_frames: 0,
    original_source_attestation: "Synthetic unit source, no rights attestation." };
}
function auditionSample() {
  const { project, profileId, recipe } = sample();
  recipe.original_mix!.music_asset_id = id(201);
  recipe.original_mix!.sfx_asset_id = null;
  project.deliverables[0].included_scene_ids = [project.scenes[0].id];
  project.audio.sfx_cues = [cue(project.scenes[0].id)];
  const options: OriginalMixPreviewOptions = { ...recipe.original_mix!,
    music_asset_id: id(201), sfx_asset_id: null, deliverable_profile_id: profileId, owner_attests_preview_rights: true };
  return { project, profileId, recipe, options };
}
describe("exact audio targets and envelope boundaries", () => {
  it.each([-2147483.648, -23.001, -0.001, 0, 0.001, 2147483.647])("accepts exact signed milli target %s", value => {
    expect(() => assertDeliveryAudioTargets({ target_lufs: value, target_true_peak_dbtp: null })).not.toThrow();
    expect(() => assertDeliveryAudioTargets({ target_lufs: null, target_true_peak_dbtp: value })).not.toThrow();
  });
  it.each([-2147483.649, 2147483.648, 0.0001, -23.0001, NaN, Infinity, -Infinity, "-23"])("rejects invalid target %s", value => {
    expect(() => assertDeliveryAudioTargets({ target_lufs: value, target_true_peak_dbtp: null })).toThrow();
  });
  it.each([null, undefined, [], {}, { target_lufs: -23 }, { target_lufs: null, target_true_peak_dbtp: null },
    { target_lufs: -23, target_true_peak_dbtp: null, approved: true }])("rejects incomplete or widened target wire %#", value => {
    expect(() => assertDeliveryAudioTargets(value)).toThrow();
  });
  it.each([null, undefined, {}, { muted: "false", fade_in_samples: 0, fade_out_samples: 0 },
    { muted: false, fade_in_samples: -1, fade_out_samples: 0 },
    { muted: false, fade_in_samples: 0.5, fade_out_samples: 0 },
    { muted: false, fade_in_samples: 4294967296, fade_out_samples: 0 },
    { muted: false, fade_in_samples: 0, fade_out_samples: 0, approved: true }])("rejects invalid envelope wire %#", value => {
    expect(() => assertOriginalMixBusEnvelope(value)).toThrow();
  });
  it("accepts u32 envelope wire in history but checks current selected-cut duration without mutation", () => {
    const { project, profileId, recipe } = sample();
    recipe.original_mix!.voice_envelope = { muted: true, fade_in_samples: 4294967295, fade_out_samples: 0 };
    expect(() => assertRecipeHistoryPayload(recipe)).not.toThrow();
    const before = JSON.stringify(recipe);
    expect(() => validateDeliveryRecipe(project, profileId, recipe)).toThrow(/fade/);
    expect(JSON.stringify(recipe)).toBe(before);
  });
  it("allows touching fades and refuses one extra sample on a shorter selected cut even when muted", () => {
    const { project, profileId, recipe } = sample();
    project.deliverables[0].included_scene_ids = [project.scenes[0].id];
    project.scenes[0].duration = { num: "1", den: "1" };
    recipe.original_mix!.voice_envelope = { muted: true, fade_in_samples: 24000, fade_out_samples: 24000 };
    expect(() => validateDeliveryRecipe(project, profileId, recipe)).not.toThrow();
    recipe.original_mix!.voice_envelope.fade_out_samples++;
    expect(() => validateDeliveryRecipe(project, profileId, recipe)).toThrow(/fade/);
    expect(recipe.original_mix!.voice_envelope.fade_out_samples).toBe(24001);
  });
  it("refuses a fractional sample clock without silently snapping its saved fades", () => {
    const { project, profileId, recipe } = sample();
    project.deliverables[0].included_scene_ids = [project.scenes[0].id];
    project.scenes[0].duration = { num: "1", den: "7" };
    expect(() => validateDeliveryRecipe(project, profileId, recipe)).toThrow(/exact 48/);
  });
  it("compares absent and neutral envelopes canonically without changing their input bytes", () => {
    const mix = defaultOriginalMix();
    const explicit = { ...mix, voice_envelope: { muted: false, fade_in_samples: 0, fade_out_samples: 0 } };
    Object.freeze(explicit.voice_envelope); Object.freeze(explicit);
    const before = JSON.stringify(explicit);
    expect(canonicalOriginalMix(explicit)).toEqual(canonicalOriginalMix(mix));
    expect(JSON.stringify(explicit)).toBe(before);
    expect(canonicalOriginalMix({ ...mix, voice_envelope: { muted: true, fade_in_samples: 0, fade_out_samples: 0 } }))
      .not.toEqual(canonicalOriginalMix(mix));
  });
  it("keeps targets when explicitly clearing the mix or changing a film scene", () => {
    const { project, profileId, recipe } = sample();
    const cleared = proposedDeliveryRecipe(recipe, editRecipeField(
      newRecipeDraft(recipeDraftScope(project, profileId, "mix"), "mix", project.revision),
      "enabled", "false", recipeFields(recipe, "mix")));
    expect(cleared.original_mix).toBeNull();
    expect(cleared.audio_targets).toEqual(recipe.audio_targets);
    expect(recipeFingerprint(cleared)).not.toBe("null");
    const film = proposedDeliveryRecipe(recipe, editRecipeField(
      newRecipeDraft(recipeDraftScope(project, profileId, "film"), "film", project.revision),
      project.scenes[0].id + ":role", "evidence", recipeFields(recipe, "film")));
    expect(film.original_mix).toEqual(recipe.original_mix);
    expect(film.audio_targets).toEqual(recipe.audio_targets);
  });
  it("includes a target-only change and nested envelope-only change in comparison identity", () => {
    const { recipe } = sample();
    const target = structuredClone(recipe); target.audio_targets!.target_lufs = 0;
    const envelope = structuredClone(recipe); envelope.original_mix!.voice_envelope!.muted = true;
    expect(recipeFingerprint(target)).not.toBe(recipeFingerprint(recipe));
    expect(recipeFingerprint(envelope)).not.toBe(recipeFingerprint(recipe));
  });
});
describe("closed source-bound audition consumer", () => {
  it("serializes exactly the Desktop scalar request while preserving saved envelopes and targets", () => {
    const { project, recipe, options } = auditionSample();
    const before = JSON.stringify(project);
    const request = savedOriginalMixAuditionRequest(project, options, recipe.original_mix!);
    expect(Object.keys(request).sort()).toEqual([
      "project_id", "generation", "revision", "deliverable_profile_id", "music_asset_id", "sfx_asset_id",
      "sfx_gain_db", "duck_attenuation_db", "duck_attack_samples", "duck_release_samples", "owner_attests_preview_rights",
    ].sort());
    expect(request).toMatchObject({ project_id: project.id, generation: project.generation, revision: project.revision,
      sfx_asset_id: null, owner_attests_preview_rights: true });
    expect(JSON.stringify(project)).toBe(before);
  });
  it("rejects changed scalar choices and a missing explicit rights confirmation", () => {
    const { project, recipe, options } = auditionSample();
    expect(() => savedOriginalMixAuditionRequest(project, { ...options, sfx_gain_db: -10 }, recipe.original_mix!)).toThrow(/differ/);
    expect(() => savedOriginalMixAuditionRequest(project, { ...options, sfx_asset_id: id(202) }, recipe.original_mix!)).toThrow(/differ/);
    expect(() => savedOriginalMixAuditionRequest(project, { ...options, owner_attests_preview_rights: false }, recipe.original_mix!)).toThrow(/permission/);
  });
  it("does not let a cue in an excluded scene supply the selected cut's missing SFX bus", () => {
    const { project, profileId, recipe } = auditionSample();
    project.audio.sfx_cues = [cue(project.scenes[1].id)];
    expect(selectedOriginalMixCues(project, profileId)).toEqual([]);
    expect(() => requireOriginalMixSfx(project, profileId, recipe.original_mix!)).toThrow(/this delivery cut/);
    recipe.original_mix!.sfx_asset_id = id(202);
    expect(() => requireOriginalMixSfx(project, profileId, recipe.original_mix!)).not.toThrow();
  });
  it("accepts applicable cues with no imported base and retains excluded cues without a global blocker", () => {
    const { project, profileId, recipe } = auditionSample();
    const selected = project.audio.sfx_cues![0];
    project.audio.sfx_cues!.push({ ...cue(project.scenes[1].id), id: id(302) });
    expect(selectedOriginalMixCues(project, profileId)).toEqual([selected]);
    expect(() => requireOriginalMixSfx(project, profileId, recipe.original_mix!)).not.toThrow();
    expect(project.audio.sfx_cues).toHaveLength(2);
  });
  it("refuses an unavailable selected cut even if an imported SFX base exists", () => {
    const { project, recipe } = auditionSample();
    recipe.original_mix!.sfx_asset_id = id(202);
    expect(() => requireOriginalMixSfx(project, id(404), recipe.original_mix!)).toThrow(/profile/);
    project.deliverables[0].included_scene_ids = [id(404)];
    expect(() => requireOriginalMixSfx(project, project.deliverables[0].id, recipe.original_mix!)).toThrow(/scene/);
  });
});
