import { inspectComponentRevision } from "./componentRevisionPreview";
import { sameValue } from "./creativeProduction";
import { reflowSceneForProfile } from "./profileComposition";
import type { HeroConfig } from "./creativeProduction";
import type { DeliverableProfile, Project, Scene } from "./types";

export interface ComponentSceneStudy { project: Project; scene: Scene; profile: DeliverableProfile }
/** Full proposed source scene for the component's existing aspect study. The
 * temporary dimensions are preview parameters, never a saved delivery profile. */
export async function buildComponentSceneStudy(project: Project, sceneId: string, instanceId: string,
  config: HeroConfig, sequence: number, width: number, height: number, displayProfile: DeliverableProfile | null = null): Promise<ComponentSceneStudy> {
  const selectedProfile = displayProfile ? structuredClone(displayProfile) : null;
  if (selectedProfile && !project.deliverables.some(value => sameValue(value, selectedProfile)))
    throw new Error("The selected delivery profile does not belong to this source revision.");
  const review = await inspectComponentRevision(project, sceneId, instanceId, config, sequence);
  const proposed = structuredClone(review.source);
  const scene = proposed.scenes.find(value => value.id === sceneId)!;
  scene.nodes = structuredClone(review.after);
  proposed.production_design!.heroes = proposed.production_design!.heroes.map(value =>
    value.id === instanceId ? structuredClone(review.afterInstance) : value);
  const sourceProfile = selectedProfile ?? proposed.deliverables.find(value => value.width === width && value.height === height) ?? proposed.deliverables[0];
  if (!sourceProfile) throw new Error("Choose a saved delivery profile before inspecting a complete component scene.");
  const profile: DeliverableProfile = { ...structuredClone(sourceProfile), width, height, framing_strategy: "replan" };
  return { project: proposed, scene: await reflowSceneForProfile(proposed, scene, profile), profile };
}
