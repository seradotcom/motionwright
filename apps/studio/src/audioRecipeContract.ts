import type { DeliveryRecipe, OriginalMixRecipe } from "./deliveryRecipe";
import type { OriginalMixPreviewOptions } from "./nativeMixTypes";
import type { Project, Scene } from "./types";
import type { SemanticSfxCue } from "./semanticSfx";

/** Exact optional fields admitted by the shared domain audio contract. */
export interface OriginalMixBusEnvelope {
  muted: boolean;
  fade_in_samples: number;
  fade_out_samples: number;
}
export interface DeliveryAudioTargets {
  target_lufs: number | null;
  target_true_peak_dbtp: number | null;
}
export const originalMixEnvelopeKeys = ["voice_envelope", "music_envelope", "sfx_envelope"] as const;
export const originalMixRequestKeys = [
  "music_asset_id", "sfx_asset_id", "sfx_gain_db",
  "duck_attenuation_db", "duck_attack_samples", "duck_release_samples",
] as const;
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
function closed(value: unknown, keys: readonly string[], label: string): Record<string, unknown> {
  if (!record(value) || Object.keys(value).length !== keys.length
      || keys.some(key => !Object.hasOwn(value, key)) || Object.keys(value).some(key => !keys.includes(key))) {
    throw new Error("Unknown or missing " + label + " fields.");
  }
  return value;
}
export function assertDeliveryAudioTargets(value: unknown): asserts value is DeliveryAudioTargets {
  const targets = closed(value, ["target_lufs", "target_true_peak_dbtp"], "delivery audio targets");
  if (targets.target_lufs === null && targets.target_true_peak_dbtp === null) {
    throw new Error("Choose at least one delivery audio target.");
  }
  for (const field of Object.values(targets)) {
    if (field === null) continue;
    if (typeof field !== "number" || !Number.isFinite(field)) throw new Error("Audio targets must be finite exact milli units.");
    const milli = Math.round(field * 1000);
    if (!Number.isSafeInteger(milli) || milli < -2147483648 || milli > 2147483647 || milli / 1000 !== field) {
      throw new Error("Audio targets require exact 0.001 units within the signed 32-bit milli range.");
    }
  }
}
export function assertOriginalMixBusEnvelope(value: unknown): asserts value is OriginalMixBusEnvelope {
  const envelope = closed(value, ["muted", "fade_in_samples", "fade_out_samples"], "original mix bus envelope");
  if (typeof envelope.muted !== "boolean") throw new Error("A bus mute choice must be boolean.");
  for (const key of ["fade_in_samples", "fade_out_samples"]) {
    const samples = envelope[key];
    if (typeof samples !== "number" || !Number.isInteger(samples) || samples < 0 || samples > 4294967295) {
      throw new Error("Bus fades must be whole unsigned 32-bit sample counts.");
    }
  }
}
/** Presence matters: outer null/undefined is not the optional-field contract. */
export function assertAudioRecipeExtensions(recipe: { original_mix: unknown; audio_targets?: unknown }): void {
  if (Object.hasOwn(recipe, "audio_targets")) assertDeliveryAudioTargets(recipe.audio_targets);
  if (recipe.original_mix === null) return;
  if (!record(recipe.original_mix)) throw new Error("Invalid original mix.");
  for (const key of originalMixEnvelopeKeys) {
    if (Object.hasOwn(recipe.original_mix, key)) assertOriginalMixBusEnvelope(recipe.original_mix[key]);
  }
}
function selectedScenes(project: Project, profileId: string): Scene[] {
  const profile = project.deliverables.find(profile => profile.id === profileId);
  if (!profile) throw new Error("The delivery profile no longer exists.");
  if (!profile.included_scene_ids.length) return project.scenes;
  const seen = new Set<string>();
  return profile.included_scene_ids.map(id => {
    if (seen.has(id)) throw new Error("The selected cut contains a duplicate scene.");
    seen.add(id);
    const scene = project.scenes.find(scene => scene.id === id);
    if (!scene) throw new Error("The selected cut contains an unavailable scene.");
    return scene;
  });
}
function exactOutputSamples(scenes: Scene[]): bigint {
  let numerator = 0n, denominator = 1n;
  const maximum = (1n << 63n) - 1n;
  for (const scene of scenes) {
    if (!/^\d{1,19}$/.test(scene.duration.num) || !/^\d{1,19}$/.test(scene.duration.den)) {
      throw new Error("Mix fades require a valid saved cut duration.");
    }
    const n = BigInt(scene.duration.num), d = BigInt(scene.duration.den);
    if (d <= 0n || n > maximum || d > maximum) throw new Error("Mix duration is outside the exact saved clock.");
    numerator = numerator * d + n * denominator;
    denominator *= d;
    let a = numerator, b = denominator;
    while (b !== 0n) { const rest = a % b; a = b; b = rest; }
    numerator /= a || 1n; denominator /= a || 1n;
    if (numerator > maximum || denominator > maximum) throw new Error("Mix output duration exceeds the saved rational clock.");
  }
  const scaled = numerator * 48000n;
  if (scaled % denominator !== 0n) throw new Error("Mix fades require an exact 48-kHz output clock; no implicit snap.");
  const samples = scaled / denominator;
  if (samples > (1n << 64n) - 1n) throw new Error("Mix output sample count is too large.");
  return samples;
}
/** UI feedback only. The service and native decoder repeat canonical admission. */
export function validateAudioRecipeCut(project: Project, profileId: string, recipe: DeliveryRecipe): void {
  assertAudioRecipeExtensions(recipe);
  const envelopes = originalMixEnvelopeKeys.flatMap(key => recipe.original_mix?.[key] ? [recipe.original_mix[key]!] : []);
  if (!envelopes.some(envelope => envelope.fade_in_samples || envelope.fade_out_samples)) return;
  const samples = exactOutputSamples(selectedScenes(project, profileId));
  for (const envelope of envelopes) {
    if (BigInt(envelope.fade_in_samples) + BigInt(envelope.fade_out_samples) > samples) {
      throw new Error("Original mix fades overlap or exceed the exact selected-cut sample range.");
    }
  }
}
/** Stable comparison projection. Never writes or normalizes the saved recipe. */
export function canonicalOriginalMix(mix: OriginalMixRecipe | null): Record<string, unknown> | null {
  if (mix === null) return null;
  assertAudioRecipeExtensions({ original_mix: mix });
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(mix).sort()) {
    if ((originalMixEnvelopeKeys as readonly string[]).includes(key)) {
      const envelope = mix[key as typeof originalMixEnvelopeKeys[number]]!;
      // Domain serialization omits a neutral envelope. The authored copy remains untouched.
      if (envelope.muted || envelope.fade_in_samples || envelope.fade_out_samples) {
        result[key] = { muted: envelope.muted, fade_in_samples: envelope.fade_in_samples, fade_out_samples: envelope.fade_out_samples };
      }
    } else result[key] = mix[key as keyof OriginalMixRecipe];
  }
  return result;
}
export function canonicalAudioTargets(recipe: DeliveryRecipe): { audio_targets?: DeliveryAudioTargets } {
  if (!Object.hasOwn(recipe, "audio_targets")) return {};
  assertDeliveryAudioTargets(recipe.audio_targets);
  return { audio_targets: {
    target_lufs: recipe.audio_targets.target_lufs, target_true_peak_dbtp: recipe.audio_targets.target_true_peak_dbtp,
  } };
}
/** Selected cue identity only; scheduling, source bytes and tails stay canonical in Rust. */
export function selectedOriginalMixCues(project: Project, profileId: string): SemanticSfxCue[] {
  const ids = new Set(selectedScenes(project, profileId).map(scene => scene.id));
  return (project.audio.sfx_cues ?? []).filter(cue => ids.has(cue.scene_id));
}
export function requireOriginalMixSfx(project: Project, profileId: string, mix: OriginalMixRecipe): void {
  const selected = selectedOriginalMixCues(project, profileId);
  if (mix.sfx_asset_id === null && selected.length === 0) {
    throw new Error("Save an imported SFX base or authored SFX cues in this delivery cut; an empty bus is not invented.");
  }
}
/** Closed Desktop request: envelopes/targets are read from the same saved revision. */
export function savedOriginalMixAuditionRequest(project: Project, options: OriginalMixPreviewOptions, saved: OriginalMixRecipe) {
  if (!options.owner_attests_preview_rights) throw new Error("Confirm permission for these exact sources before auditioning.");
  for (const key of originalMixRequestKeys) {
    if (saved[key] !== options[key]) throw new Error("Audition choices differ from the saved original mix recipe.");
  }
  requireOriginalMixSfx(project, options.deliverable_profile_id, saved);
  return {
    project_id: project.id, generation: project.generation, revision: project.revision,
    deliverable_profile_id: options.deliverable_profile_id,
    music_asset_id: options.music_asset_id, sfx_asset_id: options.sfx_asset_id,
    sfx_gain_db: saved.sfx_gain_db, duck_attenuation_db: saved.duck_attenuation_db,
    duck_attack_samples: saved.duck_attack_samples, duck_release_samples: saved.duck_release_samples,
    owner_attests_preview_rights: true,
  };
}
