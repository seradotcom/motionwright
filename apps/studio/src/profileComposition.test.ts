import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { defaultHeroConfig, emptyProductionDesign } from "./creativeProduction";
import { defaultProceduralConfig, proceduralGridDimensionsForOutput, realizeProceduralField, realizeProceduralFieldForOutput } from "./proceduralField";
import { reflowSceneForProfile, sampleProfileLayout } from "./profileComposition";
import { rationalSeconds } from "./types";
import type { DeliverableProfile, Project } from "./types";

const ID = "00000000-0000-4000-8000-000000000095";
function base(): Project {
  const p = structuredClone(fixtureProject);
  p.locks = []; p.reviews = []; p.branch_workspaces = []; p.production_design = emptyProductionDesign();
  p.scenes = [{ ...p.scenes[0], nodes: [], beats: [], start: rationalSeconds(0), duration: rationalSeconds(6),
    camera: { ...p.scenes[0].camera, center_x: 960, center_y: 540, zoom: 1, rotation_deg: 0 }, renderer: "motion-canvas", status: "draft" }];
  return p;
}
const config = () => ({ ...defaultProceduralConfig(), distribution: "adaptive_grid" as const, count: 24 });
const profile = (p: Project, width = 1080, height = 1920): DeliverableProfile => ({
  ...p.deliverables[0], width, height, framing_strategy: "replan",
});
async function saved(p = base(), sequence = false) {
  return applyChange(p, { type: "upsert_procedural_field", scene_id: p.scenes[0].id, instance_id: ID,
    config: { ...config(), reveal_step_frames: sequence ? 3 : 0 } });
}

describe("responsive procedural composition in the existing profile path", () => {
  it("reflows 24 original objects across wide, square and portrait without changing their identities or source", async () => {
    const c = config(), source = structuredClone(c), original = await realizeProceduralField(ID, c);
    for (const [width, height, columns, rows] of [[1920, 1080, 6, 4], [1080, 1080, 5, 5], [1080, 1920, 4, 6]]) {
      expect(proceduralGridDimensionsForOutput(c, width, height)).toEqual({ columns, rows });
      const nodes = await realizeProceduralFieldForOutput(ID, c, width, height);
      expect(nodes.map(node => node.id)).toEqual(original.map(node => node.id));
      expect(nodes.every(node => node.width === c.size && node.height === c.size)).toBe(true);
      expect(new Set(nodes.map(node => node.x)).size).toBe(columns);
      expect(new Set(nodes.map(node => node.y)).size).toBe(rows);
      if (width === 1920) expect(nodes).toEqual(original);
    }
    expect(c).toEqual(source);
  });
  it("retains manual distributions and Crop source layout instead of implying a different authoring choice", async () => {
    for (const distribution of ["grid", "staggered", "scatter", "radial", "spiral"] as const) {
      const c = { ...config(), distribution };
      expect(await realizeProceduralFieldForOutput(ID, c, 1080, 1920)).toEqual(await realizeProceduralField(ID, c));
    }
    const p = await saved(), target = { ...profile(p), framing_strategy: "crop" as const, crop_approved: true };
    expect(await reflowSceneForProfile(p, p.scenes[0], target)).toEqual(p.scenes[0]);
  });
  it("preserves individual paint, entrance timing and the saved baseline through reflow and serialization", async () => {
    let p = await saved(base(), true);
    const first = p.scenes[0].nodes[0];
    p = await applyChange(p, { type: "update_canvas_style", scene_id: p.scenes[0].id, node_id: first.id,
      style: { ...first.style, fill: "#FFAA22" } });
    const source = structuredClone(p), target = profile(p);
    const output = await reflowSceneForProfile(p, p.scenes[0], target);
    expect(output.nodes[0].style.fill).toBe("#FFAA22");
    expect(output.nodes.map(node => node.id)).toEqual(p.scenes[0].nodes.map(node => node.id));
    for (const node of output.nodes) {
      const original = p.scenes[0].nodes.find(value => value.id === node.id)!;
      expect(node.keyframes.map(key => [key.at, key.property, key.interpolation])).toEqual(original.keyframes.map(key => [key.at, key.property, key.interpolation]));
    }
    expect(output.nodes[0].x).not.toBe(p.scenes[0].nodes[0].x);
    expect(p).toEqual(source);
    const reopened: Project = JSON.parse(JSON.stringify(p));
    expect(await reflowSceneForProfile(reopened, reopened.scenes[0], target)).toEqual(output);
  });
  it("refuses an ambiguous human position override without rewriting it", async () => {
    const p = await saved(), first = p.scenes[0].nodes[0];
    first.x += 5;
    const original = structuredClone(p);
    await expect(reflowSceneForProfile(p, p.scenes[0], profile(p))).rejects.toThrow(/override conflict/);
    expect(p).toEqual(original);
  });
  it("reflows a Hero and the adaptive field together while preserving other scene objects", async () => {
    let p = base();
    p = await applyChange(p, { type: "upsert_product_hero", scene_id: p.scenes[0].id,
      instance_id: "00000000-0000-4000-8000-000000000005", config: { ...defaultHeroConfig(), motion: false } });
    p = await saved(p);
    const output = await reflowSceneForProfile(p, p.scenes[0], profile(p));
    const heroIds = new Set(p.production_design!.heroes[0].baseline.map(node => node.id));
    expect(output.nodes).toHaveLength(p.scenes[0].nodes.length);
    expect(output.nodes.filter(node => heroIds.has(node.id))).toHaveLength(heroIds.size);
    expect(output.nodes.map(node => node.id).sort()).toEqual(p.scenes[0].nodes.map(node => node.id).sort());
    expect(new Set(output.nodes.filter(node => !heroIds.has(node.id)).map(node => node.x)).size).toBe(4);
  });
  it("samples the same saved pan and full delayed entrances before exact output projection", async () => {
    const p = await saved(base(), true), scene = p.scenes[0], target = profile(p);
    scene.camera.center_x = 980;
    p.production_design!.camera_reframes = [{ id: "00000000-0000-4000-8000-000000000072", scene_id: scene.id,
      component_version: 1, start_center_x: 960, start_center_y: 540, end_center_x: 980, end_center_y: 540, duration_frames: 60 }];
    const output = await reflowSceneForProfile(p, scene, target), sampled = sampleProfileLayout(p, output, target, 1);
    expect(sampled.nodes[0]).toMatchObject({ x: 149.625, y: 375, width: 18, height: 18, opacity: 1 });
    expect(sampled.nodes.at(-1)?.opacity).toBe(0);
    expect(sampled.nodes.every(node => !node.keyframes.length && !node.relations.length)).toBe(true);
    expect(sampled.boundaryIssues).toEqual([]);
  });
  it("exposes unavailable camera and safe-area conflicts without clamping source coordinates", async () => {
    const p = await saved(), target = profile(p);
    p.scenes[0].camera.zoom = 2;
    expect(() => sampleProfileLayout(p, p.scenes[0], target, 0)).toThrow("planar camera");
    p.scenes[0].camera.zoom = 1;
    p.scenes[0].nodes[0].x = -20;
    const source = structuredClone(p);
    const sample = sampleProfileLayout(p, p.scenes[0], target, 0);
    expect(sample.boundaryIssues.some(issue => issue.startsWith(p.scenes[0].nodes[0].name))).toBe(true);
    expect(sample.nodes[0].x).toBeLessThan(0);
    expect(p).toEqual(source);
  });
  it("checks profile and generation budgets before realization", async () => {
    for (const [width, height] of [[319, 1080], [1080, 7681], [NaN, 1080], [1080.5, 1920]])
      await expect(realizeProceduralFieldForOutput(ID, config(), width, height)).rejects.toThrow("dimensions");
    await expect(realizeProceduralFieldForOutput(ID, { ...config(), count: 65 }, 1080, 1920)).rejects.toThrow("budget");
  });
});

it("validates retained group motion before profile sampling can erase its tracks", () => {
  const p=base(),scene=p.scenes[0];
  const node=structuredClone(fixtureProject.scenes[0].nodes[0]);
  Object.assign(node,{kind:"group",parent_id:null,keyframes:[{at:{num:"1",den:"1"},property:"x",value:25,interpolation:"linear"}],
    relations:[],rotation_deg:0,opacity:1});
  scene.nodes=[node];
  const original=structuredClone(scene);
  expect(()=>sampleProfileLayout(p,scene,profile(p),0)).toThrow("hierarchy is unresolved");
  expect(scene).toEqual(original);
});
