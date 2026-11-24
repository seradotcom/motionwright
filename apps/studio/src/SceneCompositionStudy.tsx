import type { ComponentProps } from "react";
import CompositionStudy from "./CompositionStudy";
import { sampledSceneCamera } from "./cameraReframe";
import type { CameraReframe } from "./cameraReframe";
import type { CanvasNode, Scene } from "./types";

type Props = Omit<ComponentProps<typeof CompositionStudy>, "nodes" | "width" | "height" | "camera"> & {
  scene: Scene; nodes?: CanvasNode[]; cameraReframes?: readonly CameraReframe[];
};
/** Complete scene-source view. Generated component studies use their own layout
 * coordinates; delivery crop/replan and native pixel evidence remain separate. */
export default function SceneCompositionStudy({ scene, nodes, cameraReframes, ...study }: Props) {
  const sampled = sampledSceneCamera(scene, cameraReframes, study.time);
  if (!sampled.camera) return <p className="production-error" role="alert" aria-label={study.label + " camera unavailable"}>
    Camera preview unavailable: {sampled.issue}
  </p>;
  return <CompositionStudy {...study} nodes={nodes ?? scene.nodes} width={1920} height={1080} camera={sampled.camera}/>;
}
