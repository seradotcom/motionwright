import { projectionUuid } from "./projectMutation";
import type { Project, Scene } from "./types";

export const canvasReviewResource = (nodeId: string) => "canvas-node:" + nodeId;
export const isCanvasReviewResource = (resource: string) => resource.startsWith("canvas-node:");

export function reviewResourceScene(project: Project, resource: string): Scene | null {
  if (resource.startsWith("scene:")) return project.scenes.find(scene => "scene:" + scene.id === resource) ?? null;
  if (!isCanvasReviewResource(resource)) return null;
  const id = resource.slice("canvas-node:".length);
  if (!projectionUuid(id)) return null;
  const scenes = project.scenes.filter(scene => scene.nodes.some(node => node.id === id));
  return scenes.length === 1 ? scenes[0] : null;
}
