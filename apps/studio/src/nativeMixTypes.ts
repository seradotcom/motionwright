import type { OriginalMixRecipe } from "./deliveryRecipe";

/** Desktop mixer policy; it is deliberately not an editable recipe field. */
export const ORIGINAL_MIX_SAMPLE_PEAK_GUARD_DBFS = -1;

export interface OriginalMixPreviewOptions extends Pick<OriginalMixRecipe, "sfx_gain_db" | "duck_attenuation_db" | "duck_attack_samples" | "duck_release_samples"> {
  deliverable_profile_id:string;
  music_asset_id:string;
  sfx_asset_id:string | null;
  /** Explicit for this audition only. Never written into the saved recipe. */
  owner_attests_preview_rights:boolean;
}
