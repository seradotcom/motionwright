import { describe, expect, it } from "vitest";
import { cameraLayerTransform, cameraPointerDelta, projectWorldPoint } from "./canvasCamera";
import type { CameraState } from "./types";

const center: CameraState = { center_x: 960, center_y: 540, zoom: 1, rotation_deg: 0, safe_margin: 0.05 };

describe("semantic camera viewport projection", () => {
  it("is identity at the canonical centered camera", () => {
    expect(cameraLayerTransform(center)).toContain("scale(1)");
    expect(projectWorldPoint(center, 160, 280)).toEqual({ x: 160, y: 280 });
    expect(cameraPointerDelta(center, 80, 54, 960, 540)).toEqual({ x: 160, y: 108 });
  });

  it("maintains world coordinates at a panned, zoomed and rotated camera", () => {
    const camera = { ...center, center_x: 1200, center_y: 630, zoom: 2, rotation_deg: 90 };
    expect(projectWorldPoint(camera, camera.center_x, camera.center_y)).toEqual({ x: 960, y: 540 });
    const world = cameraPointerDelta(camera, 100, 0, 960, 540);
    expect(world?.x).toBeCloseTo(0, 8);
    expect(world?.y).toBeCloseTo(100, 8);
    const origin = projectWorldPoint(camera, 1200, 630)!;
    const moved = projectWorldPoint(camera, 1200 + world!.x, 630 + world!.y)!;
    expect(moved.x - origin.x).toBeCloseTo(200, 8);
    expect(moved.y - origin.y).toBeCloseTo(0, 8);
  });

  it("safe margins remain a frame overlay independent of zoom", () => {
    const camera = { ...center, zoom: 3, safe_margin: 0.2 };
    expect(cameraLayerTransform(camera)).toContain("scale(3)");
    expect(projectWorldPoint(camera, 960 + 100, 540)?.x).toBeCloseTo(1260);
    expect(cameraPointerDelta(camera, 30, 0, 960, 540)?.x).toBeCloseTo(20);
  });

  it("fails closed for nonfinite values and zero stage dimensions", () => {
    expect(cameraPointerDelta(center, 10, 5, 0, 100)).toBeNull();
    expect(cameraPointerDelta({ ...center, zoom: 0 }, 10, 5, 500, 300)).toBeNull();
    expect(cameraPointerDelta({ ...center, rotation_deg: Number.NaN }, 10, 5, 500, 300)).toBeNull();
    expect(projectWorldPoint({ ...center, zoom: Infinity }, 5, 6)).toBeNull();
    expect(cameraLayerTransform({ ...center, zoom: Number.NaN })).toBe("none");
  });
});
