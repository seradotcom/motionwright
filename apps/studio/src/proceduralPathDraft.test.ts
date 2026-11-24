import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { defaultProceduralConfig, proceduralGridDimensions, realizeProceduralField } from "./proceduralField";
import type { ProceduralConfig } from "./proceduralField";
import {
  acknowledgeWorkbenchDraft, createWorkbenchDraft, decodeWorkbenchCopy, editWorkbenchDraft, encodeWorkbenchCopy,
  prepareWorkbenchSubmission, rawProceduralPath, retainWorkbenchFailure, workbenchUnconfirmed,
} from "./creativeWorkbenchDraft";
import type { WorkbenchDraft } from "./creativeWorkbenchDraft";
import { rationalSeconds } from "./types";

const ID = "00000000-0000-4000-8000-000000000095", request = "00000000-0000-4000-8000-000000000096";
const writer = "00000000-0000-4000-8000-000000000097";
const pathConfig = (): ProceduralConfig => ({
  ...defaultProceduralConfig(), distribution: "path", origin_x: 100, origin_y: 100,
  area_width: 420, area_height: 220, size: 20, count: 4,
  path: { closed: false, points: [{ x_percent: 0, y_percent: 0 }, { x_percent: 100, y_percent: 0 }, { x_percent: 100, y_percent: 100 }] },
});
function source() {
  const p = structuredClone(fixtureProject);
  p.branch_workspaces = []; p.locks = []; p.production_design = emptyProductionDesign(); p.reviews = []; p.markers = [];
  p.scenes = [{ ...p.scenes[0], nodes: [], beats: [], start: rationalSeconds(0), duration: rationalSeconds(6), renderer: "motion-canvas", status: "draft" }];
  return p;
}
const coordinates = (nodes: Awaited<ReturnType<typeof realizeProceduralField>>) => nodes.map(node => [node.x, node.y]);
const saved = (config = pathConfig()) => {
  const p = source();
  return applyChange(p, { type: "upsert_procedural_field", instance_id: ID, scene_id: p.scenes[0].id, config });
};

describe("editable polyline and count-responsive procedural composition", () => {
  it("spaces by path distance across unequal segments, including reverse traversal and endpoints", async () => {
    const config = pathConfig(), nodes = await realizeProceduralField(ID, config);
    expect(coordinates(nodes)).toEqual([[100, 100], [300, 100], [500, 100], [500, 300]]);
    const reverse = { ...config, path: { ...config.path!, points: [...config.path!.points].reverse() } };
    const backwards = await realizeProceduralField(ID, reverse);
    expect(coordinates(backwards)).toEqual([[500, 300], [500, 100], [300, 100], [100, 100]]);
    expect(backwards.map(node => node.id)).toEqual(nodes.map(node => node.id));
    expect(coordinates(await realizeProceduralField(ID, { ...config, count: 1 }))).toEqual([[100, 100]]);
  });
  it("closes a loop without duplicating the first object and keeps source topology editable", async () => {
    const config: ProceduralConfig = { ...pathConfig(), area_width: 220, path: { closed: true, points: [
      { x_percent: 0, y_percent: 0 }, { x_percent: 100, y_percent: 0 },
      { x_percent: 100, y_percent: 100 }, { x_percent: 0, y_percent: 100 },
    ] } };
    expect(coordinates(await realizeProceduralField(ID, config))).toEqual([[100, 100], [300, 100], [300, 300], [100, 300]]);
    const p = await saved(config), reopened = JSON.parse(JSON.stringify(p));
    expect(reopened.production_design.procedural_fields[0].config.path).toEqual(config.path);
    expect(reopened.scenes[0].nodes).toEqual(p.scenes[0].nodes);
  });
  it("rejects missing, degenerate, oversized, fractional or hidden path declarations", async () => {
    const config = pathConfig();
    for (const bad of [
      { ...config, path: undefined }, { ...config, path: { closed: false, points: [{ x_percent: 0, y_percent: 0 }] } },
      { ...config, path: { closed: true, points: config.path!.points.slice(0, 2) } },
      { ...config, path: { closed: false, points: Array(17).fill({ x_percent: 0, y_percent: 0 }) } },
      { ...config, path: { closed: false, points: [{ x_percent: 0, y_percent: 0 }, { x_percent: 0, y_percent: 0 }] } },
      { ...config, path: { closed: false, points: [{ x_percent: 0, y_percent: 0 }, { x_percent: 101, y_percent: 0 }] } },
      { ...config, path: { closed: false, points: [{ x_percent: 0.5, y_percent: 0 }, { x_percent: 100, y_percent: 0 }] } },
      { ...config, area_width: 21, area_height: 21, path: { closed: false, points: [{ x_percent: 0, y_percent: 0 }, { x_percent: 1, y_percent: 1 }] } },
      { ...config, distribution: "grid" as const },
    ]) await expect(realizeProceduralField(ID, bad)).rejects.toThrow();
  });
  it("reflows both grid dimensions from quantity and field aspect without scaling the objects", async () => {
    const config: ProceduralConfig = { ...defaultProceduralConfig(), distribution: "adaptive_grid" };
    expect(proceduralGridDimensions(config)).toEqual({ columns: 4, rows: 3 });
    expect(proceduralGridDimensions({ ...config, count: 20 })).toEqual({ columns: 5, rows: 4 });
    const portrait = { ...config, area_width: 200, columns: 16 };
    expect(proceduralGridDimensions(portrait)).toEqual({ columns: 2, rows: 6 });
    const nodes = await realizeProceduralField(ID, portrait);
    expect(nodes.every(node => node.width === 32 && node.height === 32)).toBe(true);
    await expect(realizeProceduralField(ID, { ...config, count: 2, area_width: 20, area_height: 20, size: 20 })).rejects.toThrow("fit");
  });
  it("reflows the original identities while retaining human appearance and refusing destructive shrink", async () => {
    let p = await saved(), first = p.scenes[0].nodes[0], last = p.scenes[0].nodes[3];
    p = await applyChange(p, { type: "update_canvas_style", scene_id: p.scenes[0].id, node_id: first.id, style: { ...first.style, fill: "#FFAA22" } });
    p = await applyChange(p, { type: "update_canvas_style", scene_id: p.scenes[0].id, node_id: last.id, style: { ...last.style, fill: "#CC3344" } });
    const ids = p.scenes[0].nodes.map(node => node.id);
    p = await applyChange(p, { type: "upsert_procedural_field", instance_id: ID, scene_id: p.scenes[0].id,
      config: { ...pathConfig(), distribution: "adaptive_grid", path: undefined } });
    expect(p.scenes[0].nodes.map(node => node.id)).toEqual(ids);
    expect(p.scenes[0].nodes[0].style.fill).toBe("#FFAA22");
    const before = structuredClone(p);
    await expect(applyChange(p, { type: "upsert_procedural_field", instance_id: ID, scene_id: p.scenes[0].id,
      config: { ...pathConfig(), distribution: "adaptive_grid", path: undefined, count: 3 } })).rejects.toThrow("human edits");
    expect(p).toEqual(before);
  });
  it("keeps the complete path and existing entrance grammar with an oriented influence", async () => {
    const config: ProceduralConfig = { ...pathConfig(), reveal_step_frames: 3, influence: {
      shape: "horizontal", falloff: "linear", center_x: 300, center_y: 200, radius: 800,
      offset_x: 10, offset_y: 5, scale_percent: 125, rotation_deg: 30, fill: "#DDAA88",
    } };
    const p = await saved(config);
    expect(p.scenes[0].nodes.every(node => node.rotation_deg > 0 && node.keyframes.some(key => key.property === "opacity" && key.value === 1))).toBe(true);
    expect(p.production_design!.procedural_fields![0].config.path).toEqual(config.path);
  });
});

function editPath(draft: WorkbenchDraft, x: string): WorkbenchDraft {
  if (draft.input.kind !== "procedural") throw new Error("Expected procedural input.");
  const path = draft.input.config.path ?? rawProceduralPath();
  return editWorkbenchDraft(draft, { ...draft.input, config: { ...draft.input.config,
    path: { ...path, points: path.points.map((point, index) => index === 1 ? { ...point, x_percent: x } : point) } } });
}
describe("polyline source custody through existing workbench saves", () => {
  it("keeps an incomplete point across recovery and a late original ACK", async () => {
    const p = await saved();
    const draft = editPath(createWorkbenchDraft(p, { kind: "procedural", sceneId: p.scenes[0].id }), "80");
    const prepared = await prepareWorkbenchSubmission(draft, p, request, "save");
    const pendingCopy = encodeWorkbenchCopy({ schema: "motionwright.creative-workbench-recovery/1",
      scope: prepared.draft.scope, writerId: writer, updatedAt: 1, draft: prepared.draft });
    const recovered = decodeWorkbenchCopy(pendingCopy, prepared.draft.scope, writer).draft;
    expect(recovered.pending?.submitted).toEqual(prepared.change);
    expect(workbenchUnconfirmed(recovered)).toBe(true);
    const poisoned = JSON.parse(pendingCopy);
    poisoned.draft.pending.submitted.config.path.execute = "not an admitted field";
    expect(() => decodeWorkbenchCopy(JSON.stringify(poisoned), prepared.draft.scope, writer)).toThrow("comparison snapshot");
    const late = editPath(prepared.draft, "-"), receipt = await applyChange(p, prepared.change);
    const ack = acknowledgeWorkbenchDraft(late, request, receipt);
    expect(ack.intent).toBe(true); expect(ack.pending).toBeNull();
    const copy = encodeWorkbenchCopy({ schema: "motionwright.creative-workbench-recovery/1", scope: ack.scope, writerId: writer, updatedAt: 1, draft: ack });
    expect(decodeWorkbenchCopy(copy, ack.scope, writer).draft.input).toEqual(late.input);
    await expect(prepareWorkbenchSubmission(ack, receipt, crypto.randomUUID(), "save")).rejects.toThrow("complete integer");
    expect(receipt.production_design!.procedural_fields![0].config.path!.points[1].x_percent).toBe(80);
  });
  it("retains inactive path input after a grid save and UNKNOWN never retries", async () => {
    const p = await saved();
    let draft = createWorkbenchDraft(p, { kind: "procedural", sceneId: p.scenes[0].id });
    if (draft.input.kind !== "procedural") throw new Error("Expected procedural input.");
    draft = editWorkbenchDraft(draft, { ...draft.input, config: { ...draft.input.config, distribution: "adaptive_grid" } });
    const prepared = await prepareWorkbenchSubmission(draft, p, request, "save"), receipt = await applyChange(p, prepared.change);
    const ack = acknowledgeWorkbenchDraft(prepared.draft, request, receipt);
    expect(ack.intent).toBe(true);
    expect(receipt.production_design!.procedural_fields![0].config.path).toBeUndefined();
    if (ack.input.kind !== "procedural") throw new Error("Expected procedural input.");
    expect(ack.input.config.path).toEqual(rawProceduralPath(pathConfig().path));
    const unknown = retainWorkbenchFailure(prepared.draft, request, "Reply unavailable");
    expect(workbenchUnconfirmed(unknown)).toBe(true);
    await expect(prepareWorkbenchSubmission(unknown, p, crypto.randomUUID(), "save")).rejects.toThrow();
  });
});
