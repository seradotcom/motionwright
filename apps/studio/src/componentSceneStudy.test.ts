import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { defaultHeroConfig, emptyProductionDesign } from "./creativeProduction";
import { buildComponentSceneStudy } from "./componentSceneStudy";
import { sampleProfileLayout } from "./profileComposition";
import { defaultProceduralConfig } from "./proceduralField";
import { rationalSeconds } from "./types";

const ID = "00000000-0000-4000-8000-000000000005";
const config = () => ({ ...defaultHeroConfig(), layout: "typography_focus" as const,
  eyebrow: "AUTHOR / TYPE", headline: "An authored idea", body: "A supporting thought.", wordmark: "MW" });
async function source() {
  const p = structuredClone(fixtureProject);
  p.locks = []; p.reviews = []; p.branch_workspaces = []; p.production_design = emptyProductionDesign();
  p.scenes = [{ ...p.scenes[0], nodes: [], beats: [], start: rationalSeconds(0), duration: rationalSeconds(6),
    camera: { ...p.scenes[0].camera, center_x: 960, center_y: 540, zoom: 1, rotation_deg: 0 }, renderer: "motion-canvas" as const }];
  return applyChange(p, { type: "upsert_product_hero", instance_id: ID, scene_id: p.scenes[0].id, config: config() });
}
describe("complete component scene studies", () => {
  it("shows the merged human style, other source objects and sampled camera in the main and expanded source", async () => {
    let p = await source();
    const heading = p.scenes[0].nodes[1], sid = p.scenes[0].id;
    p = await applyChange(p, { type: "update_canvas_style", scene_id: sid, node_id: heading.id, style: { ...heading.style, fill: "#FFAA22" } });
    const shape = { ...structuredClone(p.scenes[0].nodes[4]), id: "00000000-0000-4000-8000-000000000077",
      name: "Separate original object", x: 500, y: 600, width: 40, height: 40, keyframes: [] };
    p = await applyChange(p, { type: "add_canvas_node", scene_id: sid, node: shape });
    p.scenes[0].camera.center_x = 950;
    const original = structuredClone(p), target = { ...config(), headline: "The revised idea" };
    const study = await buildComponentSceneStudy(p, sid, ID, target, 3, 1920, 1080, p.deliverables[0]);
    expect(study.scene.nodes).toHaveLength(7);
    expect(study.scene.nodes.find(node => node.id === heading.id)?.style.fill).toBe("#FFAA22");
    expect(study.scene.nodes.find(node => node.id === shape.id)).toEqual(shape);
    const sampled = sampleProfileLayout(study.project, study.scene, study.profile, 2);
    expect(sampled.nodes.find(node => node.id === shape.id)?.x).toBe(510);
    expect(sampled.nodes.find(node => node.id === heading.id)?.text).toBe("The revised idea");
    expect(p).toEqual(original);
    expect(study.project.revision).toBe(p.revision);
    expect(study.project.deliverables).toEqual(p.deliverables);
  });
  it("uses both generators for output reflow and keeps all original objects without mutating the saved profile", async () => {
    let p = await source();
    p = await applyChange(p, { type: "upsert_procedural_field", scene_id: p.scenes[0].id,
      instance_id: "00000000-0000-4000-8000-000000000095", config: { ...defaultProceduralConfig(), distribution: "adaptive_grid", count: 24 } });
    const original = structuredClone(p);
    const study = await buildComponentSceneStudy(p, p.scenes[0].id, ID, config(), 1, 1080, 1920, p.deliverables[0]);
    expect(study.scene.nodes).toHaveLength(30);
    const fieldIds = new Set(p.production_design!.procedural_fields![0].baseline.map(node => node.id));
    expect(new Set(study.scene.nodes.filter(node => fieldIds.has(node.id)).map(node => node.x)).size).toBe(4);
    expect(study.profile).toMatchObject({ width: 1080, height: 1920 });
    expect(study.project.deliverables[0]).toEqual(p.deliverables[0]);
    expect(p).toEqual(original);
  });
  it("refuses an unresolved profile or incompatible override without drawing a generated replacement", async () => {
    const p = await source(), sid = p.scenes[0].id;
    await expect(buildComponentSceneStudy(p, sid, ID, config(), 1, 1080, 1920, { ...p.deliverables[0], name: "From another revision" }))
      .rejects.toThrow("source revision");
    p.scenes[0].nodes[1].y += 5;
    const original = structuredClone(p);
    await expect(buildComponentSceneStudy(p, sid, ID, config(), 2, 1080, 1920)).rejects.toThrow("override conflict");
    expect(p).toEqual(original);
  });
  it("captures input before digest awaits so later edits cannot change a prepared visual result", async () => {
    const p = await source(), target = config(), originalHeadline = target.headline;
    const pending = buildComponentSceneStudy(p, p.scenes[0].id, ID, target, 1, 1920, 1080);
    target.headline = "A later unsubmitted idea";
    p.scenes[0].nodes[1].style.fill = "#AA0000";
    const prepared = await pending;
    expect(prepared.scene.nodes[1].text).toBe(originalHeadline);
    expect(prepared.scene.nodes[1].style.fill).not.toBe("#AA0000");
  });
});
