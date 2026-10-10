import type { CanvasNode, CanvasTransform, MotionInterpolation, MotionProperty } from "./types";
import { seconds } from "./types";

export const motionProperties: MotionProperty[] = ["x", "y", "width", "height", "rotation_deg", "opacity"];
export function easedProgress(progress: number, interpolation: MotionInterpolation): number {
  if (interpolation === "hold") return 0;
  if (interpolation === "ease_in_out") return progress * progress * (3 - 2 * progress);
  if (interpolation === "ease_out_cubic") return 1 - Math.pow(1 - progress, 3);
  return progress;
}
/** Editorial sampling; a native frame is authoritative for font/raster appearance. */
export function previewTransform(node: CanvasNode, playhead: number): CanvasTransform {
  const output: CanvasTransform = { x:node.x,y:node.y,width:node.width,height:node.height,rotation_deg:node.rotation_deg,opacity:node.opacity };
  for (const property of motionProperties) {
    const keys = node.keyframes.filter(key => key.property === property).sort((a,b) => seconds(a.at)-seconds(b.at));
    let fromValue = output[property], fromTime = 0;
    for (const key of keys) {
      const keyTime = seconds(key.at);
      if (playhead < keyTime) {
        const span = keyTime - fromTime;
        if (span <= 0) break;
        const progress = Math.max(0,Math.min(1,(playhead-fromTime)/span));
        output[property] = fromValue + (key.value-fromValue)*easedProgress(progress,key.interpolation);
        break;
      }
      fromValue = key.value; fromTime = keyTime; output[property] = key.value;
    }
  }
  return output;
}
