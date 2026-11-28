import { mergeComponentNodes, PRODUCT_HERO_DEFINITIONS, realizeVersionedProductHero, sameValue } from "./creativeProduction";
import type { HeroConfig, ProductHeroInstance } from "./creativeProduction";
import type { CanvasNode, Project } from "./types";

/** A transient editorial comparison. It has no dispatch, receipt or native authority. */
export interface ComponentRevisionPreview {
  kind: "editorial_v1_component_change";
  source: Project;
  sceneId: string;
  instanceId: string;
  sequence: number;
  target: HeroConfig;
  beforeInstance: ProductHeroInstance;
  afterInstance: ProductHeroInstance;
  before: CanvasNode[];
  after: CanvasNode[];
  rows: ComponentRevisionRow[];
  unrelatedNodeIds: string[];
}

export interface ComponentRevisionRow {
  id: string;
  role: string;
  before: CanvasNode;
  after: CanvasNode;
  generated: CanvasNode;
  changedFields: string[];
  retainedFields: string[];
}

const clone = <T,>(value: T): T => structuredClone(value);
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

/** Arrays (including motion tracks) keep the existing merge's atomic semantics. */
function changedPaths(before: unknown, after: unknown, prefix = ""): string[] {
  if (sameValue(before, after)) return [];
  if (record(before) && record(after)) {
    return [...new Set([...Object.keys(before), ...Object.keys(after)])].sort().flatMap(key =>
      changedPaths(before[key], after[key], prefix ? prefix + "." + key : key));
  }
  return [prefix];
}

export function componentRevisionField(node: CanvasNode, path: string): unknown {
  let current: unknown = node;
  for (const part of path.split(".")) current = record(current) ? current[part] : undefined;
  return current;
}

/**
 * Compare a supported v1 family change against the complete saved scene, using
 * the same existing realization and three-way merge used by editorial updates.
 * The service remains authoritative when the existing upsert is submitted.
 */
export async function inspectComponentRevision(
  project: Project, sceneId: string, instanceId: string, config: HeroConfig, sequence: number, requestedVersion?: number,
): Promise<ComponentRevisionPreview> {
  if (!Number.isSafeInteger(sequence) || sequence < 0) throw new Error("The component edit sequence is invalid.");
  // Snapshot before the first digest await. Later edits must invalidate this view.
  const source = clone(project), target = clone(config);
  const instance = source.production_design?.heroes.find(value => value.id === instanceId);
  const scene = source.scenes.find(value => value.id === sceneId);
  if (!instance || !scene || instance.scene_id !== sceneId) throw new Error("The saved component is outside this scene.");
  if (!PRODUCT_HERO_DEFINITIONS.some(entry => entry.version === instance.component_version))
    throw new Error("This saved component version has no available realization for comparison.");
  const version = requestedVersion ?? instance.component_version;
  if (scene.renderer !== "motion-canvas") throw new Error("The saved component needs a Motion Canvas scene.");
  if (new Set(scene.nodes.map(node => node.id)).size !== scene.nodes.length
    || new Set(instance.baseline.map(node => node.id)).size !== instance.baseline.length) {
    throw new Error("The saved component has ambiguous object identities.");
  }
  // Canonical component source is authored in the existing 1920 x 1080 space.
  // Responsive studies remain a separate view and do not become the merge base.
  const storedDefinition = await realizeVersionedProductHero(instanceId, instance.config, 1920, 1080, instance.component_version);
  const heading = storedDefinition.find(node => node.name.endsWith(" / headline"));
  const storedSize = instance.baseline.find(node => node.id === heading?.id)?.style.font_size;
  if (typeof heading?.style.font_size !== "number" || typeof storedSize !== "number"
    || Math.abs(storedSize - heading.style.font_size) > 0.000_001)
    throw new Error("The saved component heading does not match its library version.");
  const generated = version === instance.component_version && sameValue(target, instance.config) ? storedDefinition
    : await realizeVersionedProductHero(instanceId, target, 1920, 1080, version);
  const after = mergeComponentNodes(instance.baseline, scene.nodes, generated);
  const governed = new Set(instance.baseline.map(node => node.id));
  const rows = instance.baseline.map((base, index): ComponentRevisionRow => {
    const before = scene.nodes.find(node => node.id === base.id)!;
    const next = after.find(node => node.id === base.id)!;
    const incoming = generated[index];
    return {
      id: base.id, role: base.name.split(" / ").at(-1) ?? base.name,
      before: clone(before), after: clone(next), generated: clone(incoming),
      changedFields: changedPaths(before, next),
      retainedFields: changedPaths(base, before).filter(path =>
        sameValue(componentRevisionField(before, path), componentRevisionField(next, path))
        && !sameValue(componentRevisionField(before, path), componentRevisionField(incoming, path))),
    };
  });
  return {
    kind: "editorial_v1_component_change", source, sceneId, instanceId, sequence, target,
    beforeInstance: clone(instance),
    afterInstance: { ...clone(instance), component_version: version, config: clone(target), baseline: clone(generated) },
    before: clone(scene.nodes), after, rows,
    unrelatedNodeIds: scene.nodes.filter(node => !governed.has(node.id)).map(node => node.id),
  };
}

/** Full saved state and edit sequence bind the comparison, including ABA edits. */
export function componentRevisionCurrent(
  preview: ComponentRevisionPreview | null, project: Project, sceneId: string,
  instanceId: string, config: HeroConfig, sequence: number, requestedVersion?: number,
): preview is ComponentRevisionPreview {
  return preview !== null && preview.kind === "editorial_v1_component_change"
    && preview.sceneId === sceneId && preview.instanceId === instanceId
    && preview.sequence === sequence && sameValue(preview.target, config)
    && preview.afterInstance.component_version === (requestedVersion ?? preview.beforeInstance.component_version)
    && sameValue(preview.source, project);
}
