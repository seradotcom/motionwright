import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import CompositionStudy from "./CompositionStudy";
import SceneCompositionStudy from "./SceneCompositionStudy";
import ProgramDesignStudy from "./ProgramDesignStudy";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { sampledSceneCamera } from "./cameraReframe";
import type { CameraReframe } from "./cameraReframe";
import { cameraSvgTransform, projectWorldPoint } from "./canvasCamera";
import type { CanvasNode, Scene } from "./types";

const sceneId = "00000000-0000-4000-8000-000000000071";
function shot(): Scene {
  return { ...structuredClone(fixtureProject.scenes[0]), id: sceneId, renderer: "motion-canvas",
    start: { num: "8", den: "1" }, duration: { num: "6", den: "1" }, nodes: [], beats: [],
    camera: { center_x: 980, center_y: 540, zoom: 1, rotation_deg: 0, safe_margin: 0.05 } };
}
const move = (): CameraReframe => ({ id: "00000000-0000-4000-8000-000000000072", scene_id: sceneId,
  component_version: 1, start_center_x: 900, start_center_y: 500, end_center_x: 980, end_center_y: 540, duration_frames: 60 });
const box = (): CanvasNode => ({
  ...structuredClone(fixtureProject.scenes[0].nodes[0]), id: "00000000-0000-4000-8000-000000000073",
  kind: "rectangle", parent_id: null, coordinate_space: "project_pixels", name: "Camera source box",
  x: 450, y: 350, width: 100, height: 80, rotation_deg: 20, opacity: 1, text: null,
  relations: [], keyframes: [], property_locks: [], z_index: 1,
});

describe("one saved camera across scene-relative authoring and review", () => {
  it("samples exact pan time, holds its endpoints and leaves the terminal source unchanged", () => {
    const scene = shot(), source = move(), before = structuredClone({ scene, source });
    for (const [time, x, y] of [[-1, 900, 500], [0, 900, 500], [1 / 30, 900 + 80 / 60, 500 + 40 / 60],
      [1, 940, 520], [2, 980, 540], [5, 980, 540]]) {
      const sample = sampledSceneCamera(scene, [source], time);
      expect(sample.issue).toBeNull(); expect(sample.sourceId).toBe(source.id);
      expect(sample.camera?.center_x).toBeCloseTo(x, 10); expect(sample.camera?.center_y).toBeCloseTo(y, 10);
    }
    expect({ scene, source }).toEqual(before);
    expect(sampledSceneCamera(scene, [], 0).camera?.center_x).toBe(980);
  });
  it("uses the semantic camera's rotation and zoom in SVG without moving source objects", () => {
    const camera = { ...shot().camera, center_x: 100, center_y: 200, zoom: 2, rotation_deg: 90 };
    const sampled = sampledSceneCamera({ ...shot(), camera }, undefined, 0);
    expect(sampled.camera).toEqual(camera); expect(sampled.camera).not.toBe(camera);
    expect(cameraSvgTransform(camera)).toBe("translate(960 540) rotate(-90) scale(2) translate(-100 -200)");
    const projected = projectWorldPoint(camera, 150, 200)!;
    expect(projected.x).toBeCloseTo(960); expect(projected.y).toBeCloseTo(440);
  });
  it("refuses stale, ambiguous or invalid camera sources instead of showing the stored endpoint", () => {
    const scene = shot(), source = move();
    for (const [candidate, moves, time] of [
      [{ ...scene, camera: { ...scene.camera, center_x: 960 } }, [source], 0],
      [scene, [source, { ...source, id: "00000000-0000-4000-8000-000000000074" }], 0],
      [scene, [{ ...source, duration_frames: 0 }], 0],
      [scene, [{ ...source, component_version: 2 }], 0],
      [{ ...scene, camera: { ...scene.camera, zoom: 2 } }, [source], 0],
      [scene, [source], Number.NaN],
    ] as const) {
      const result = sampledSceneCamera(candidate, moves, time);
      expect(result.camera).toBeNull(); expect(result.issue).not.toBeNull();
    }
    const invalid = renderToStaticMarkup(<SceneCompositionStudy scene={{ ...scene, camera: { ...scene.camera, center_x: 960 } }}
      cameraReframes={[source]} time={0} background="#000000" label="Invalid source"/>);
    expect(invalid).toContain('role="alert"'); expect(invalid).not.toContain("<svg");
    const malformed = renderToStaticMarkup(<CompositionStudy nodes={[box()]} camera={{ ...scene.camera, zoom: 0 }}
      time={0} background="#000000" label="Invalid static camera"/>);
    expect(malformed).not.toContain("<svg");
  });
  it("moves nested world-space clipping and animated geometry under one outer camera transform", () => {
    const scene = shot();
    const clip = { ...box(), id: "00000000-0000-4000-8000-000000000075", kind: "clip_group",
      x: 300, y: 300, width: 500, height: 300, rotation_deg: 0, z_index: 0 };
    const child = { ...box(), parent_id: clip.id, keyframes: [
      { at: { num: "0", den: "1" }, property: "x" as const, value: 350, interpolation: "linear" as const },
      { at: { num: "1", den: "1" }, property: "x" as const, value: 450, interpolation: "linear" as const },
      { at: { num: "0", den: "1" }, property: "opacity" as const, value: 0, interpolation: "linear" as const },
      { at: { num: "1", den: "1" }, property: "opacity" as const, value: 1, interpolation: "linear" as const },
    ] };
    scene.nodes = [clip, child];
    const original = structuredClone(scene);
    const markup = renderToStaticMarkup(<SceneCompositionStudy scene={scene} cameraReframes={[move()]}
      time={0.5} background="#000000" label="Camera and clip"/>);
    expect(markup).toContain('data-camera-center-x="920"');
    expect(markup).toContain('transform="translate(960 540) rotate(0) scale(1) translate(-920 -510)"');
    expect(markup.match(/data-camera-layer="scene"/g)).toHaveLength(1);
    expect(markup).toContain('<rect x="300" y="300" width="500" height="300"');
    expect(markup).toContain('opacity="0.5" transform="rotate(20 450 390)"');
    expect(markup).toContain('<rect x="400" y="350" width="100" height="80"');
    expect(scene).toEqual(original);
  });
  it("the saved Program monitor samples the same camera and preserves profile text selection", () => {
    const p = structuredClone(fixtureProject), scene = shot();
    scene.nodes = [box()]; p.scenes = [scene];
    p.production_design = { ...emptyProductionDesign(), camera_reframes: [move()] };
    const markup = renderToStaticMarkup(<ProgramDesignStudy project={p} scene={scene} playhead={9} profile={p.deliverables[0]}/>);
    expect(markup).toContain('data-camera-center-x="940"');
    expect(markup).toContain('data-source-revision="' + p.revision + '"');
    expect(markup).toContain("Saved scene camera sampled at 1.000 s");
    expect(p.scenes[0].camera.center_x).toBe(980);
  });
});
