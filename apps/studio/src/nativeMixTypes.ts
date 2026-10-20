import type { OriginalMixRecipe } from "./deliveryRecipe";

/** Desktop mixer policy; it is deliberately not an editable recipe field. */
export const ORIGINAL_MIX_SAMPLE_PEAK_GUARD_DBFS = -1;

export interface OriginalMixPreviewOptions extends Omit<OriginalMixRecipe, "music_asset_id" | "sfx_asset_id"> {
  deliverable_profile_id:string;
  music_asset_id:string;
  sfx_asset_id:string;
  /** Explicit for this audition only. Never written into the saved recipe. */
  owner_attests_preview_rights:boolean;
}
