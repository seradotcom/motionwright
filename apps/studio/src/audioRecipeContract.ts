import type { DeliveryRecipe, OriginalMixRecipe } from "./deliveryRecipe";
import type { OriginalMixPreviewOptions } from "./nativeMixTypes";
import type { Project, Scene } from "./types";
import type { SemanticSfxCue } from "./semanticSfx";
import { deriveVoiceCut, isOriginalVoiceMontage } from "./voiceCut";

/** Exact optional fields admitted by the shared domain audio contract. */
export interface OriginalMixBusEnvelope {
  muted: boolean;
  fade_in_samples: number;
  fade_out_samples: number;
}
export interface OriginalMixSilenceRelease {
  fade_samples:number;
  silence_samples:number;
}
export interface DeliveryAudioTargets {
  target_lufs: number | null;
  target_true_peak_dbtp: number | null;
}
export const originalMixEnvelopeKeys = ["voice_envelope", "music_envelope", "sfx_envelope"] as const;
export const originalMixOptionalKeys = [...originalMixEnvelopeKeys, "music_source_start_sample", "voice_cut_sha256", "silence_release"] as const;
export const MAX_MUSIC_SOURCE_SAMPLE_FRAMES = 48_000 * 180;
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
export function assertOriginalMixSilenceRelease(value:unknown): asserts value is OriginalMixSilenceRelease {
  const release=closed(value,["fade_samples","silence_samples"],"original mix silence release");
  if(typeof release.fade_samples!=="number"||typeof release.silence_samples!=="number"||
    !Number.isSafeInteger(release.fade_samples)||!Number.isSafeInteger(release.silence_samples)||
    release.fade_samples<2||release.silence_samples<1||
    release.fade_samples+release.silence_samples>=MAX_MUSIC_SOURCE_SAMPLE_FRAMES)
    throw new Error("Silence release requires a gradual fade and positive silent tail inside the exact original cut.");
}

/** Presence matters: outer null/undefined is not the optional-field contract. */
export function assertAudioRecipeExtensions(recipe: { original_mix: unknown; audio_targets?: unknown; voice_only_cut_sha256?: unknown }): void {
  if (Object.hasOwn(recipe, "voice_only_cut_sha256") &&
    (recipe.original_mix !== null || typeof recipe.voice_only_cut_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(recipe.voice_only_cut_sha256)))
    throw new Error("Voice-only cut requires an exact SHA-256 binding and no original mix.");
  if (Object.hasOwn(recipe, "audio_targets")) assertDeliveryAudioTargets(recipe.audio_targets);
  if (recipe.original_mix === null) return;
  if (!record(recipe.original_mix)) throw new Error("Invalid original mix.");
  if (Object.hasOwn(recipe.original_mix, "music_source_start_sample")) {
    const start = recipe.original_mix.music_source_start_sample;
    if (typeof start !== "number" || !Number.isInteger(start) || start < 0
      || start >= MAX_MUSIC_SOURCE_SAMPLE_FRAMES || typeof recipe.original_mix.music_asset_id !== "string")
      throw new Error("Music source window requires a selected audio asset and a bounded exact start sample.");
  }
  if (Object.hasOwn(recipe.original_mix, "voice_cut_sha256") &&
      (typeof recipe.original_mix.voice_cut_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(recipe.original_mix.voice_cut_sha256)))
    throw new Error("Voice cut requires a lower-case SHA-256 binding.");
  for (const key of originalMixEnvelopeKeys) {
    if (Object.hasOwn(recipe.original_mix, key)) assertOriginalMixBusEnvelope(recipe.original_mix[key]);
  }
  if(Object.hasOwn(recipe.original_mix,"silence_release"))
    assertOriginalMixSilenceRelease(recipe.original_mix.silence_release);
}
export function selectedAudioScenes(project: Project, profileId: string): Scene[] {
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
/** A saved release resolves the exact selected original clock. The service
 * independently recomputes the source-bound voice-cut digest and admission. */
export function validateSilenceReleaseCut(project:Project,profileId:string,mix:OriginalMixRecipe):void{
  const release=mix.silence_release;
  if(!release)return;
  assertOriginalMixSilenceRelease(release);
  const profile=project.deliverables.find(item=>item.id===profileId);
  if(!profile||profile.audio_sample_rate_hz!==48000)
    throw new Error("Silence release requires the exact original 48-kHz output clock.");
  const frames=exactOutputSamples(selectedAudioScenes(project,profileId));
  const length=BigInt(release.fade_samples)+BigInt(release.silence_samples);
  if(frames<48000n||frames>BigInt(MAX_MUSIC_SOURCE_SAMPLE_FRAMES)||length>=frames)
    throw new Error("Silence release requires a gradual fade and positive silent tail inside the exact original cut.");
  const fadeStart=Number(frames-length);
  const track=project.audio.voice_tracks.find(item=>item.id===profile.voice_track_id);
  if(!track||track.sample_rate_hz!==48000||track.channels!==2||!project.assets.some(asset=>
    asset.id===track.asset_id&&asset.content_sha256===track.source_sha256&&asset.media_type.startsWith("audio/")))
    throw new Error("Silence release requires the explicitly bound original stereo 48-kHz voice.");
  const sample=(time:{num:string;den:string})=>{
    if(!/^(0|[1-9][0-9]{0,18})$/.test(time.num)||!/^[1-9][0-9]{0,18}$/.test(time.den))
      throw new Error("Silence release requires exact 48-kHz sample boundaries.");
    const n=BigInt(time.num)*48000n,d=BigInt(time.den);
    if(n%d||n/d>BigInt(MAX_MUSIC_SOURCE_SAMPLE_FRAMES))
      throw new Error("Silence release requires exact 48-kHz sample boundaries.");
    return Number(n/d);
  };
  const sourceFrames=sample(track.measured_duration);
  const montage=isOriginalVoiceMontage(project,profile);
  if(montage!==!!mix.voice_cut_sha256)
    throw new Error("Review the exact selected original voice cut before authoring silence release.");
  const cut=montage?deriveVoiceCut(project,profile):null;
  if((cut?.outputFrames??sourceFrames)!==Number(frames))
    throw new Error("Silence release voice and output cut sample counts differ.");
  const windows=project.audio.transcript.filter(segment=>segment.voice_track_id===track.id).map(segment=>{
    const reviewed=segment.alignment.kind==="manual"||(segment.alignment.kind==="measured"&&
      segment.alignment.source_sha256===track.source_sha256&&(segment.alignment.confidence_millis??0)>=800);
    const start=sample(segment.start),end=sample(segment.end);
    if(!reviewed||start>=end||end>sourceFrames)
      throw new Error("Silence release requires source-bound reviewed voice timing.");
    return {start,end};
  });
  const selected=cut?cut.spans.flatMap(span=>windows.filter(window=>window.start<span.sourceEnd&&window.end>span.sourceStart).map(window=>{
    if(window.start<span.sourceStart||window.end>span.sourceEnd)
      throw new Error("Silence release cannot cross a selected reviewed voice boundary.");
    return {start:span.outputStart+window.start-span.sourceStart,end:span.outputStart+window.end-span.sourceStart};
  })):windows;
  selected.sort((a,b)=>a.start-b.start);
  if(!selected.length||selected.length>64||selected.some((window,index)=>window.end>fadeStart||
    (index>0&&selected[index-1].end>window.start)))
    throw new Error("Silence release must follow every selected reviewed narration interval.");
}

/** UI feedback only. The service and native decoder repeat canonical admission. */
export function validateAudioRecipeCut(project: Project, profileId: string, recipe: DeliveryRecipe): void {
  assertAudioRecipeExtensions(recipe);
  if(recipe.original_mix?.silence_release)validateSilenceReleaseCut(project,profileId,recipe.original_mix);
  const envelopes = originalMixEnvelopeKeys.flatMap(key => recipe.original_mix?.[key] ? [recipe.original_mix[key]!] : []);
  const start = recipe.original_mix?.music_source_start_sample;
  if (start === undefined && !envelopes.some(envelope => envelope.fade_in_samples || envelope.fade_out_samples)) return;
  const scenes = selectedAudioScenes(project, profileId);
  const samples = exactOutputSamples(scenes);
  if (start !== undefined) {
    const profile = project.deliverables.find(value => value.id === profileId)!;
    if (profile.audio_sample_rate_hz !== 48000 || (recipe.original_mix?.voice_cut_sha256 === undefined &&
      (scenes.length !== project.scenes.length || scenes.some((scene, index) => scene.id !== project.scenes[index].id))))
      throw new Error("Music source windows require the full original cut at 48 kHz or a saved voice cut binding.");
    if (samples < 48000n || BigInt(start) + samples > BigInt(MAX_MUSIC_SOURCE_SAMPLE_FRAMES))
      throw new Error("Music source window must fit the existing 180-second decode budget and a cut of at least one second.");
  }
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
    } else if(key==="silence_release"){
      const release=mix.silence_release!;
      result[key]={fade_samples:release.fade_samples,silence_samples:release.silence_samples};
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
  const ids = new Set(selectedAudioScenes(project, profileId).map(scene => scene.id));
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
