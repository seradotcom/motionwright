import type { Project } from "./types";

export interface AssetReference {
  key: string;
  role: string;
  label: string;
  scene?: { id: string; name: string };
}

/** Saved editorial references keyed by asset identity. Graph and byte verification have their own authorities. */
export function assetReferences(project: Project): Map<string, AssetReference[]> {
  const references = new Map(project.assets.map(asset => [asset.id, [] as AssetReference[]]));
  const scenes = new Map(project.scenes.map(scene => [scene.id, { id: scene.id, name: scene.name }]));
  const add = (assetId: string | null, role: string, referenceId: string, label: string, sceneId?: string) => {
    if (assetId === null) return;
    references.get(assetId)?.push({
      key: role + ":" + referenceId, role, label, scene: sceneId ? scenes.get(sceneId) : undefined,
    });
  };

  for (const track of project.audio.voice_tracks)
    add(track.asset_id, "Voice", track.id, track.label);
  for (const claim of project.brief.claims)
    if (claim.source?.kind === "asset") add(claim.source.asset_id, "Claim source", claim.id, claim.text);
  for (const cue of project.audio.sfx_cues ?? [])
    add(cue.source_asset_id, "Sound cue", cue.id, cue.event.replaceAll("_", " "), cue.scene_id);

  const design = project.production_design;
  if (!design) return references;

  for (const shot of design.plan?.shots ?? [])
    for (const assetId of shot.asset_ids) add(assetId, "Planned shot", shot.scene_id, shot.purpose, shot.scene_id);
  if (design.plan?.clock.kind === "music")
    add(design.plan.clock.asset_id, "Music clock", design.plan.clock.asset_id, "Production plan");
  for (const capsule of design.capsules)
    add(capsule.source_asset_id, "Native capsule", capsule.id, capsule.label, capsule.scene_id);

  for (const boundary of design.scene_boundaries ?? []) {
    const { definition } = boundary;
    const status = definition.kind === "draft_incomplete" ? " draft" : "";
    for (const asset of definition.entry?.assets ?? [])
      add(asset.asset_id, "Scene entry" + status, boundary.id, "", boundary.scene_id);
    for (const asset of definition.exit?.assets ?? [])
      add(asset.asset_id, "Scene exit" + status, boundary.id, "", boundary.scene_id);
  }

  const tables = new Map((design.measured_tables ?? []).map(table => [table.id, table]));
  for (const table of tables.values())
    add(table.source_asset_id, "Measured data", table.id, table.label);
  for (const binding of design.measured_bindings ?? []) {
    const table = tables.get(binding.table_id);
    if (table) add(table.source_asset_id, "Measured scene data", binding.id, table.label, binding.scene_id);
  }

  for (const capture of design.captures ?? []) {
    add(capture.screenshot_asset_id, "Capture image", capture.id, capture.scenario_id);
    add(capture.manifest_asset_id, "Capture manifest", capture.id, capture.scenario_id);
    add(capture.fixture_state_asset_id, "Capture state", capture.id, capture.scenario_id);
  }

  for (const profile of project.deliverables) {
    const mix = design.delivery_recipes?.[profile.id]?.original_mix;
    if (!mix) continue;
    add(mix.music_asset_id, "Delivery music", profile.id, profile.name);
    add(mix.sfx_asset_id, "Delivery sound effects", profile.id, profile.name);
  }
  return references;
}
