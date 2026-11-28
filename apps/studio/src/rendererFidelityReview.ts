import type { Project, RendererKind, Scene } from "./types";

export function sceneHasAttachedNativeCapsule(project: Project, sceneId: string): boolean {
  return project.production_design?.capsules?.some(capsule => capsule.scene_id === sceneId) ?? false;
}

export function requiresRendererFidelityReview(project: Project, scene: Scene, renderer: RendererKind): boolean {
  const { camera } = scene;
  return scene.renderer !== renderer
    && (scene.renderer === "manim-community" || renderer === "manim-community")
    && (scene.nodes.length > 0 || scene.beats.length > 0 || camera.center_x !== 960
      || camera.center_y !== 540 || camera.zoom !== 1 || camera.rotation_deg !== 0 || camera.safe_margin !== 0.05
      || sceneHasAttachedNativeCapsule(project, scene.id));
}
