import { describe, expect, it } from "vitest";
import goldenSource from "../../../fixtures/procedural-influence-v1.json";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { applyProceduralChange, defaultProceduralConfig, realizeProceduralField, validateProceduralConfig } from "./proceduralField";
import type { ProceduralConfig } from "./proceduralField";
import { proceduralGeneratorVersion } from "./proceduralInfluence";
import type { Project } from "./types";
import { rationalSeconds } from "./types";

const id = "00000000-0000-4000-8000-000000000095";
const golden = goldenSource as unknown as {
  cases: { name: string; config: ProceduralConfig; expected: number[][] }[];
  legacy: { typescript: { wire: { config_wire: string; nodes_wire: string } } };
};
function config(): ProceduralConfig {
  return { ...defaultProceduralConfig(), distribution: "grid", count: 3, columns: 3,
    origin_x: 300, origin_y: 300, area_width: 600, area_height: 200 };
}
const influenced = (): ProceduralConfig => ({ ...config(),
  influence: { center_x: 600, center_y: 400, radius: 150, target_size: 64 } });
function project(): Project {
  const value = structuredClone(fixtureProject);
  value.branch_workspaces = []; value.locks = []; value.production_design = emptyProductionDesign();
  value.reviews = []; value.markers = [];
  value.scenes = [{ ...value.scenes[0], nodes: [], beats: [], start: rationalSeconds(0),
    duration: rationalSeconds(6), renderer: "motion-canvas", status: "draft" }];
  return value;
}
const upsert = (value: Project, next: ProceduralConfig) => applyProceduralChange(value,
  { type: "upsert_procedural_field", instance_id: id, scene_id: value.scenes[0].id, config: next });

describe("bounded procedural influence on existing editable objects", () => {
  it("retains both historical config and complete generated-node bytes when influence is absent", async () => {
    expect(golden.legacy?.typescript?.wire).toBeTruthy();
    expect(JSON.stringify(defaultProceduralConfig())).toBe(golden.legacy.typescript.wire.config_wire);
    expect(JSON.stringify(await realizeProceduralField(id, defaultProceduralConfig())))
      .toBe(golden.legacy.typescript.wire.nodes_wire);
    expect(Object.hasOwn(defaultProceduralConfig(), "influence")).toBe(false);
    expect(proceduralGeneratorVersion(defaultProceduralConfig())).toBe(1);
  });

  it("matches eight independent exact distance and rounding vectors shared with Rust", async () => {
    expect(golden.cases).toHaveLength(8);
    for (const vector of golden.cases) {
      const nodes = await realizeProceduralField(id, vector.config);
      expect(nodes.map(node => [node.x, node.y, node.width]), vector.name).toEqual(vector.expected);
      expect(nodes.every(node => node.width === node.height), vector.name).toBe(true);
      const legacy = { ...vector.config }; delete legacy.influence;
      const before = await realizeProceduralField(id, legacy);
      expect(nodes.map(node => node.id), vector.name).toEqual(before.map(node => node.id));
      expect(nodes.map(node => [node.x + node.width / 2, node.y + node.height / 2]), vector.name)
        .toEqual(before.map(node => [node.x + node.width / 2, node.y + node.height / 2]));
    }
  });

  it("rejects incomplete, fractional, nonfinite, forged and out-of-range influence contracts", () => {
    const good = influenced().influence!;
    const invalid: unknown[] = [null, {}, { ...good, extra: 1 }, { ...good, radius: "1e" },
      { ...good, radius: 0 }, { ...good, radius: 1921 }, { ...good, radius: 2.5 },
      { ...good, center_x: 299 }, { ...good, center_x: 901 }, { ...good, center_y: 299 },
      { ...good, center_y: 501 }, { ...good, center_x: NaN }, { ...good, target_size: Infinity },
      { ...good, target_size: 3 }, { ...good, target_size: 129 }];
    for (const influence of invalid)
      expect(() => validateProceduralConfig({ ...config(), influence } as ProceduralConfig)).toThrow();
  });

  it("preserves the full input Project when influence would overflow authored geometry", async () => {
    const value = project(), before = structuredClone(value);
    const invalid = { ...config(), count: 1, columns: 1, origin_x: 0, origin_y: 0,
      area_width: 32, area_height: 32,
      influence: { center_x: 16, center_y: 16, radius: 1, target_size: 128 } };
    await expect(upsert(value, invalid)).rejects.toThrow(/exceeds the authored field/);
    expect(value).toEqual(before);
  });

  it("changes and removes influence without changing identities or a compatible human fill override", async () => {
    const value = project(); await upsert(value, config());
    const ids = value.scenes[0].nodes.map(node => node.id);
    value.scenes[0].nodes[0].style.fill = "#EECCAA";
    await upsert(value, influenced());
    expect(value.scenes[0].nodes.map(node => node.id)).toEqual(ids);
    expect(value.scenes[0].nodes.map(node => node.width)).toEqual([32, 64, 32]);
    expect(value.scenes[0].nodes[0].style.fill).toBe("#EECCAA");
    expect(value.production_design!.procedural_fields![0].generator_version).toBe(2);
    await upsert(value, { ...influenced(), influence: { ...influenced().influence!, target_size: 80 } });
    expect(value.scenes[0].nodes[1].width).toBe(80);
    await upsert(value, config());
    expect(value.production_design!.procedural_fields![0].generator_version).toBe(1);
    expect(Object.hasOwn(value.production_design!.procedural_fields![0].config, "influence")).toBe(false);
    expect(value.scenes[0].nodes.map(node => node.width)).toEqual([32, 32, 32]);
    expect(value.scenes[0].nodes[0].style.fill).toBe("#EECCAA");
    const kept = structuredClone(value.scenes[0].nodes);
    await applyProceduralChange(value, { type: "detach_procedural_field", instance_id: id });
    expect(value.scenes[0].nodes).toEqual(kept);
    expect(value.production_design!.procedural_fields).toEqual([]);
  });

  it("keeps complete human work on size conflicts, node locks and scope locks", async () => {
    for (const fault of ["override", "size", "position", "scope"] as const) {
      const value = project(); await upsert(value, config());
      if (fault === "override") value.scenes[0].nodes[1].width = 41;
      else if (fault === "scope") value.locks.push({
        id: crypto.randomUUID(), resource: "scene:" + value.scenes[0].id, kind: "position", note: "Approved framing" });
      else value.scenes[0].nodes[1].property_locks = [fault];
      const before = structuredClone(value);
      await expect(upsert(value, influenced())).rejects.toThrow(fault === "override" ? /override conflict/ : /lock/i);
      expect(value, fault).toEqual(before);
    }
  });

  it("retains the existing entrance clock and opacity grammar with influenced final Y", async () => {
    const original = { ...config(), reveal_step_frames: 4, reveal_duration_frames: 12 };
    const before = await realizeProceduralField(id, original);
    const after = await realizeProceduralField(id, { ...original, influence: influenced().influence });
    for (let index = 0; index < after.length; index++) {
      expect(after[index].keyframes.map(({ at, property, interpolation }) => ({ at, property, interpolation })))
        .toEqual(before[index].keyframes.map(({ at, property, interpolation }) => ({ at, property, interpolation })));
      expect(after[index].keyframes.filter(key => key.property === "opacity"))
        .toEqual(before[index].keyframes.filter(key => key.property === "opacity"));
      const y = after[index].keyframes.filter(key => key.property === "y");
      expect(y[0].value).toBe(after[index].y + 12);
      expect(y.at(-1)!.value).toBe(after[index].y);
    }
  });

  it("keeps all supported distributions and seed extremes bounded with64 stable editable nodes", async () => {
    for (const distribution of ["grid", "staggered", "scatter", "radial", "spiral"] as const)
      for (const seed of [0, 4294967295])
        for (const shape of ["square", "circle"] as const) {
          const next: ProceduralConfig = { ...defaultProceduralConfig(), distribution, seed, shape,
            count: 64, columns: 16, influence: { center_x: 630, center_y: 450, radius: 1920, target_size: 4 } };
          const nodes = await realizeProceduralField(id, next);
          expect(nodes).toHaveLength(64); expect(new Set(nodes.map(node => node.id)).size).toBe(64);
          expect(nodes.every(node => node.width >= 4 && node.width <= 32 && node.width === node.height
            && node.x >= next.origin_x && node.y >= next.origin_y
            && node.x + node.width <= next.origin_x + next.area_width
            && node.y + node.height <= next.origin_y + next.area_height)).toBe(true);
          expect(nodes).toEqual(await realizeProceduralField(id, next));
        }
  });

  it("refuses a forged version or baseline before editing live objects", async () => {
    for (const tamper of ["version", "baseline"] as const) {
      const value = project(); await upsert(value, influenced());
      const field = value.production_design!.procedural_fields![0];
      if (tamper === "version") field.generator_version = 1;
      else field.baseline[1].width = 33;
      const before = structuredClone(value);
      await expect(upsert(value, config())).rejects.toThrow(/baseline mismatch/);
      expect(value).toEqual(before);
    }
  });
});
