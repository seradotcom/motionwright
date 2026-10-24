import { describe, expect, it } from "vitest";
import { defaultHeroConfig, emptyProductionDesign, realizeProductHero } from "./creativeProduction";
import type { HeroConfig, HeroLayout } from "./creativeProduction";
import { fixtureProject } from "./fixture";
import { rationalSeconds } from "./types";
import type { Project } from "./types";
import { componentRevisionCurrent, inspectComponentRevision } from "./componentRevisionPreview";

const instanceId = "00000000-0000-4000-8000-000000000005";
const unrelatedId = "00000000-0000-4000-8000-000000000006";
const layouts: HeroLayout[] = ["product_hero_reveal", "split_explanation", "metric_evidence", "step_sequence", "contrast_comparison", "editorial_quote"];
function config(layout: HeroLayout = "product_hero_reveal"): HeroConfig {
  return { ...defaultHeroConfig(), layout, eyebrow: "EDITORIAL / ORIGINAL STUDY", headline: "One authored idea",
    body: "An original explanation with an explicit source", wordmark: "MW" };
}
async function seeded() {
  const project: Project = structuredClone(fixtureProject), original = config();
  const baseline = await realizeProductHero(instanceId, original);
  project.schema_version = 2; project.locks = []; project.reviews = []; project.branch_workspaces = [];
  project.scenes = [{ ...project.scenes[0], renderer: "motion-canvas", nodes: structuredClone(baseline),
    duration: rationalSeconds(6), start: rationalSeconds(0), status: "draft" }];
  project.production_design = { ...emptyProductionDesign(), heroes: [{ id: instanceId,
    scene_id: project.scenes[0].id, component_version: 1, config: original, baseline }] };
  return project;
}

describe("existing v1 component revision comparison", () => {
  it.each(layouts)("compares the real %s realization without manufacturing another component version", async layout => {
    const project = await seeded(), before = structuredClone(project), target = config(layout);
    const preview = await inspectComponentRevision(project, project.scenes[0].id, instanceId, target, 3);
    expect(preview.kind).toBe("editorial_v1_component_change");
    expect(preview.beforeInstance.component_version).toBe(1);
    expect(preview.afterInstance.component_version).toBe(1);
    expect(preview.afterInstance.baseline).toEqual(await realizeProductHero(instanceId, target));
    expect(preview.after.map(node => node.id)).toEqual(project.scenes[0].nodes.map(node => node.id));
    expect(preview.after).toEqual(preview.afterInstance.baseline);
    expect(preview.before).toEqual(project.scenes[0].nodes);
    expect(project).toEqual(before);
  });

  it("shows the preserved human text and style in the merged scene instead of the generated baseline", async () => {
    const project = await seeded(), scene = project.scenes[0];
    const eyebrow = scene.nodes[0], body = scene.nodes[2];
    eyebrow.text = "  An exact human line\n\n "; body.style.fill = "#FFE0A0";
    const before = structuredClone(project), target = config("split_explanation");
    const preview = await inspectComponentRevision(project, scene.id, instanceId, target, 4);
    const eyebrowRow = preview.rows.find(row => row.id === eyebrow.id)!;
    const bodyRow = preview.rows.find(row => row.id === body.id)!;
    expect(eyebrowRow.after.text).toBe(eyebrow.text);
    expect(eyebrowRow.generated.text).not.toBe(eyebrow.text);
    expect(eyebrowRow.retainedFields).toContain("text");
    expect(bodyRow.after.style.fill).toBe("#FFE0A0");
    expect(bodyRow.retainedFields).toContain("style.fill");
    expect(bodyRow.changedFields.length).toBeGreaterThan(0);
    expect(preview.after).not.toEqual(preview.afterInstance.baseline);
    expect(project).toEqual(before);
  });

  it("keeps unrelated objects, references, camera and the complete saved Project intact", async () => {
    const project = await seeded(), scene = project.scenes[0];
    const extra = { ...structuredClone(scene.nodes[0]), id: unrelatedId, name: "A separate authored object",
      text: "Retain this exact object", parent_id: scene.nodes[2].id };
    scene.nodes.splice(2, 0, extra);
    const before = structuredClone(project);
    const preview = await inspectComponentRevision(project, scene.id, instanceId, config("editorial_quote"), 1);
    expect(preview.after.find(node => node.id === unrelatedId)).toEqual(extra);
    expect(preview.after.findIndex(node => node.id === unrelatedId)).toBe(2);
    expect(preview.unrelatedNodeIds).toEqual([unrelatedId]);
    expect(preview.source).toEqual(before);
    expect(project).toEqual(before);
  });

  it("preserves authored motion and editing locks when the proposed fields do not overlap them", async () => {
    const project = await seeded(), node = project.scenes[0].nodes[0];
    node.property_locks = ["text"];
    node.text = "A locked authored line";
    node.keyframes = [...node.keyframes, { at: rationalSeconds(3), property: "opacity", value: 0.7, interpolation: "hold" }];
    const before = structuredClone(project);
    const preview = await inspectComponentRevision(project, project.scenes[0].id, instanceId,
      { ...config(), foreground: "#FFEEDD" }, 2);
    const row = preview.rows.find(value => value.id === node.id)!;
    expect(row.after.keyframes).toEqual(node.keyframes);
    expect(row.after.property_locks).toEqual(node.property_locks);
    expect(row.after.text).toBe(node.text);
    expect(row.retainedFields).toEqual(expect.arrayContaining(["text", "keyframes", "property_locks"]));
    expect(project).toEqual(before);
  });

  it("refuses an overlapping text edit and retains every current object", async () => {
    const project = await seeded(), scene = project.scenes[0];
    scene.nodes[2].text = "A human explanation";
    const before = structuredClone(project);
    await expect(inspectComponentRevision(project, scene.id, instanceId,
      { ...config(), body: "A different incoming explanation" }, 2)).rejects.toThrow("override conflict");
    expect(project).toEqual(before);
  });

  it("keeps a deleted component object deleted and does not invent a restoration", async () => {
    const project = await seeded(), scene = project.scenes[0]; scene.nodes.splice(2, 1);
    const before = structuredClone(project);
    await expect(inspectComponentRevision(project, scene.id, instanceId, config("split_explanation"), 2)).rejects.toThrow("deleted");
    expect(project).toEqual(before);
  });

  it("refuses a locked incoming style change without mutating saved source", async () => {
    const project = await seeded(); project.scenes[0].nodes.forEach(node => { node.property_locks = ["style"]; });
    const before = structuredClone(project);
    await expect(inspectComponentRevision(project, project.scenes[0].id, instanceId,
      { ...config(), foreground: "#FFEEDD" }, 2)).rejects.toThrow("locked");
    expect(project).toEqual(before);
  });

  it("keeps topology and identity rejection from the existing merge", async () => {
    for (const mutation of ["missing-baseline", "wrong-identity", "duplicate-object"] as const) {
      const project = await seeded();
      if (mutation === "missing-baseline") project.production_design!.heroes[0].baseline.pop();
      else if (mutation === "wrong-identity") project.production_design!.heroes[0].baseline[0].id = unrelatedId;
      else project.scenes[0].nodes.push(structuredClone(project.scenes[0].nodes[0]));
      const before = structuredClone(project);
      await expect(inspectComponentRevision(project, project.scenes[0].id, instanceId, config(), 2)).rejects.toThrow();
      expect(project).toEqual(before);
    }
  });

  it.each([0, 2, 99])("refuses unsupported stored component version %s instead of calling it migrated", async version => {
    const project = await seeded(); project.production_design!.heroes[0].component_version = version;
    const before = structuredClone(project);
    await expect(inspectComponentRevision(project, project.scenes[0].id, instanceId, config(), 2)).rejects.toThrow("no available realization");
    expect(project).toEqual(before);
  });

  it("takes its complete source and target before the first asynchronous realization", async () => {
    const project = await seeded(), before = structuredClone(project), target = config("split_explanation"), sent = structuredClone(target);
    const work = inspectComponentRevision(project, project.scenes[0].id, instanceId, target, 1);
    target.headline = "Later input"; project.audio.mix.voice_gain_db -= 1;
    const preview = await work;
    expect(preview.source).toEqual(before); expect(preview.target).toEqual(sent);
    expect(componentRevisionCurrent(preview, project, preview.sceneId, instanceId, target, 1)).toBe(false);
  });

  it.each(["deliverables", "audio", "production_design"] as const)("invalidates a comparison when %s changes at the same stamp", async field => {
    const project = await seeded(), target = config("split_explanation");
    const preview = await inspectComponentRevision(project, project.scenes[0].id, instanceId, target, 2);
    expect(componentRevisionCurrent(preview, structuredClone(project), preview.sceneId, instanceId, target, 2)).toBe(true);
    const changed = structuredClone(project);
    if (field === "deliverables") changed.deliverables[0].width++;
    else if (field === "audio") changed.audio.mix.voice_gain_db -= 1;
    else changed.production_design!.heroes[0].config.wordmark = "LATER";
    expect(componentRevisionCurrent(preview, changed, preview.sceneId, instanceId, target, 2)).toBe(false);
  });

  it("invalidates later clear and return-to-base gestures through sequence even when raw values match", async () => {
    const project = await seeded(), target = config(), preview = await inspectComponentRevision(project, project.scenes[0].id, instanceId, target, 2);
    expect(componentRevisionCurrent(preview, project, preview.sceneId, instanceId, target, 3)).toBe(false);
    expect(componentRevisionCurrent(preview, project, preview.sceneId, instanceId, target, 4)).toBe(false);
    expect(componentRevisionCurrent(preview, project, preview.sceneId, unrelatedId, target, 2)).toBe(false);
    expect(componentRevisionCurrent(preview, { ...project, generation: unrelatedId }, preview.sceneId, instanceId, target, 2)).toBe(false);
    expect(componentRevisionCurrent(preview, { ...project, active_branch: unrelatedId }, preview.sceneId, instanceId, target, 2)).toBe(false);
  });

  it("supports the actual legacy default layout without rewriting the saved instance", async () => {
    const project = await seeded(); delete project.production_design!.heroes[0].config.layout;
    const target = config(); delete target.layout; const before = structuredClone(project);
    const preview = await inspectComponentRevision(project, project.scenes[0].id, instanceId, target, 0);
    expect(preview.rows.every(row => row.changedFields.length === 0)).toBe(true);
    expect(project).toEqual(before); expect(preview.beforeInstance.config.layout).toBeUndefined();
  });
});
