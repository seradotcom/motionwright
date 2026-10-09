import type { CameraState } from "./types";

export const PROJECT_CANVAS_WIDTH = 1920;
export const PROJECT_CANVAS_HEIGHT = 1080;

/** The semantic stage uses project-pixel coordinates. Camera transforms must
 * change pixels on screen without mutating the underlying object positions.
 */
export function cameraLayerTransform(camera: CameraState): string {
  const tx = (-100 * camera.center_x) / PROJECT_CANVAS_WIDTH;
  const ty = (-100 * camera.center_y) / PROJECT_CANVAS_HEIGHT;
  if (![tx, ty, camera.rotation_deg, camera.zoom].every(Number.isFinite)
    || camera.zoom <= 0) {
    return "none";
  }
  return [
    "translate(50%, 50%)",
    `rotate(${-camera.rotation_deg}deg)`,
    `scale(${camera.zoom})`,
    `translate(${tx}%, ${ty}%)`,
  ].join(" ");
}

/** Undo zoom/camera rotation on a screen pointer delta, preserving authored
 * world-coordinate movement (including when the camera is not axis aligned).
 */
export function cameraPointerDelta(
  camera: CameraState,
  pixelsX: number,
  pixelsY: number,
  stageWidth: number,
  stageHeight: number,
): { x: number; y: number } | null {
  if (![pixelsX, pixelsY, stageWidth, stageHeight,
    camera.zoom, camera.rotation_deg].every(Number.isFinite)
    || stageWidth <= 0 || stageHeight <= 0 || camera.zoom <= 0) {
    return null;
  }
  const deltaX = pixelsX * PROJECT_CANVAS_WIDTH / stageWidth / camera.zoom;
  const deltaY = pixelsY * PROJECT_CANVAS_HEIGHT / stageHeight / camera.zoom;
  const rad = camera.rotation_deg * Math.PI / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const x = cos * deltaX - sin * deltaY;
  const y = sin * deltaX + cos * deltaY;
  return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
}

/** Pure semantic camera projection, used to cross-check camera transforms.
 * This is not evidence that an external renderer produced matching pixels.
 */
export function projectWorldPoint(camera: CameraState, x: number, y: number): { x: number; y: number } | null {
  const inputs = [camera.center_x, camera.center_y, camera.zoom, camera.rotation_deg, x, y];
  if (!inputs.every(Number.isFinite) || camera.zoom <= 0) return null;
  const dx = x - camera.center_x;
  const dy = y - camera.center_y;
  const rad = camera.rotation_deg * Math.PI / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const output = {
    x: PROJECT_CANVAS_WIDTH / 2 + camera.zoom * (cos * dx + sin * dy),
    y: PROJECT_CANVAS_HEIGHT / 2 + camera.zoom * (-sin * dx + cos * dy),
  };
  return [output.x, output.y].every(Number.isFinite) ? output : null;
}
