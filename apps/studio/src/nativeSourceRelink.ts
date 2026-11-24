import type { NativeCapsule } from "./creativeProduction";
import type { Asset, Change, Project } from "./types";

export type NativeSourceRelink = Extract<Change, { type: "relink_native_capsule_source" }>["relink"];
export interface ProductionRelinkInput {
  kind: "relink"; sourceAssetId: string; sourceSha256: string; assetId: string; assetSha256: string;
}
const digest = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const identity = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 128;
export function nativeRelinkInput(capsule: NativeCapsule | null): ProductionRelinkInput {
  return { kind: "relink", sourceAssetId: capsule?.source_asset_id ?? "", sourceSha256: capsule?.source_sha256 ?? "", assetId: "", assetSha256: "" };
}
export function nativeRelinkInputShape(value: unknown): value is ProductionRelinkInput {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const v = value as Record<string, unknown>;
  return Object.keys(v).length === 5 && ["kind", "sourceAssetId", "sourceSha256", "assetId", "assetSha256"].every(key => Object.hasOwn(v,key))
    && v.kind === "relink" && ((identity(v.sourceAssetId) && digest(v.sourceSha256)) || (v.sourceAssetId === "" && v.sourceSha256 === ""))
    && ((identity(v.assetId) && digest(v.assetSha256)) || (v.assetId === "" && v.assetSha256 === ""));
}
export function relinkRequest(capsuleId: string | null, sceneId: string | null, input: ProductionRelinkInput): NativeSourceRelink {
  if (!identity(capsuleId) || !identity(sceneId) || !nativeRelinkInputShape(input) || !input.sourceAssetId || !input.assetId
    || input.assetId === input.sourceAssetId) throw new Error("Choose a different retained source asset.");
  return { capsule_id: capsuleId, scene_id: sceneId, expected_source_asset_id: input.sourceAssetId,
    expected_source_sha256: input.sourceSha256, source_asset_id: input.assetId, source_sha256: input.assetSha256 };
}
export function relinkedCapsuleValue(current: NativeCapsule, relink: NativeSourceRelink): NativeCapsule {
  if (current.id !== relink.capsule_id || current.scene_id !== relink.scene_id
    || current.source_asset_id !== relink.expected_source_asset_id || current.source_sha256 !== relink.expected_source_sha256
    || !digest(relink.source_sha256) || !identity(relink.source_asset_id) || relink.source_asset_id === current.source_asset_id)
    throw new Error("Native capsule source changed; review the saved binding.");
  const capsule: NativeCapsule = { ...structuredClone(current), source_asset_id: relink.source_asset_id, source_sha256: relink.source_sha256,
    fidelity: { renderer: "source-relink", renderer_version: "unverified", visual: "unavailable", temporal: "unavailable",
      structural: "unavailable", editable: "unavailable", evidence_sha256: null,
      losses: ["Relinked source has no verified native realization or fidelity evidence. Review its editable parameters again."] },
    editable_parameters: [] };
  delete capsule.property_fidelity;
  return capsule;
}
function exactAsset(project: Project, id: string, sha: string): Asset {
  const matches = project.assets.filter(asset => asset.id === id);
  if (matches.length !== 1 || !digest(sha) || matches[0].content_sha256 !== sha) throw new Error("Retained source asset identity or digest differs.");
  return matches[0];
}
export function proposeNativeSourceRelink(project: Project, relink: NativeSourceRelink): NativeCapsule {
  const capsules = project.production_design?.capsules.filter(c => c.id === relink.capsule_id) ?? [];
  if (capsules.length !== 1 || project.scenes.filter(scene => scene.id === relink.scene_id).length !== 1)
    throw new Error("The selected native capsule or scene is unavailable or ambiguous.");
  const next = relinkedCapsuleValue(capsules[0], relink);
  const previous = exactAsset(project, relink.expected_source_asset_id, relink.expected_source_sha256);
  const replacement = exactAsset(project, relink.source_asset_id, relink.source_sha256);
  if (previous.media_type !== replacement.media_type) throw new Error("Choose a retained source with the same media type.");
  return next;
}
export function nativeRelinkCandidates(project: Project, capsule: NativeCapsule | null): Asset[] {
  if (!capsule) return [];
  const matches = project.assets.filter(asset => asset.id === capsule.source_asset_id);
  if (matches.length !== 1 || matches[0].content_sha256 !== capsule.source_sha256) return [];
  return project.assets.filter(asset => asset.id !== capsule.source_asset_id && asset.media_type === matches[0].media_type
    && digest(asset.content_sha256) && project.assets.filter(other => other.id === asset.id).length === 1);
}
