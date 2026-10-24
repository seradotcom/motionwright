import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { applyProceduralChange, defaultProceduralConfig } from "./proceduralField";
import { rawProceduralInfluence } from "./proceduralInfluence";
import type { ProceduralInfluenceRaw } from "./proceduralInfluence";
import type { Project, ProjectEvent } from "./types";
import {
  acknowledgeWorkbenchDraft, createWorkbenchDraft, decodeWorkbenchCopy, editWorkbenchDraft,
  encodeWorkbenchCopy, inspectWorkbenchOutcome, prepareWorkbenchSubmission, proceduralConfig,
  reconcileWorkbenchDraft, releaseWorkbenchOutcome, workbenchConflict, workbenchCopyKey,
  workbenchUnconfirmed, writeWorkbenchCopy,
} from "./creativeWorkbenchDraft";
import type { ProceduralRaw, WorkbenchCustody, WorkbenchDraft, WorkbenchInput, WorkbenchStorage } from "./creativeWorkbenchDraft";

const requestId = "00000000-0000-4000-8000-000000000091";
const instanceId = "00000000-0000-4000-8000-000000000092";
const writerId = "00000000-0000-4000-8000-000000000093";
const influence = { center_x: 600, center_y: 400, radius: 150, target_size: 64 };
const config = { ...defaultProceduralConfig(), distribution: "grid" as const, count: 3, columns: 3,
  origin_x: 300, origin_y: 300, area_width: 600, area_height: 200, size: 32 };
type Input = Extract<WorkbenchInput, { kind: "procedural" }>;
function source(): Project {
  const project = structuredClone(fixtureProject);
  project.locks = []; project.branch_workspaces = []; project.production_design = emptyProductionDesign();
  return project;
}
function create(project: Project): WorkbenchDraft {
  return createWorkbenchDraft(project, { kind: "procedural", sceneId: project.scenes[0].id });
}
function authored(project = source(), value: ProceduralInfluenceRaw = rawProceduralInfluence(influence)) {
  const draft = create(project), input = draft.input as Input;
  const raw: ProceduralRaw = { ...input.config, distribution: "grid", count: "3", columns: "3",
    origin_x: "300", origin_y: "300", area_width: "600", area_height: "200", size: "32", influence: value };
  return editWorkbenchDraft(draft, { ...input, config: raw });
}
function later(draft: WorkbenchDraft, patch: Partial<ProceduralInfluenceRaw>) {
  const input = draft.input as Input;
  return editWorkbenchDraft(draft, { ...input, config: { ...input.config,
    influence: { ...input.config.influence!, ...patch } } });
}
function rawCopy(draft: WorkbenchDraft) {
  return encodeWorkbenchCopy({ schema: "motionwright.creative-workbench-recovery/1",
    scope: draft.scope, writerId, updatedAt: 1, draft });
}
async function saveBase(project = source()) {
  return applyChange(project, { type: "upsert_procedural_field", instance_id: instanceId,
    scene_id: project.scenes[0].id, config });
}
class Storage implements WorkbenchStorage {
  values = new Map<string, string>();
  get length() { return this.values.size; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}
describe("Influence Field joins the existing Workbench author buffer", () => {
  it("reads a legacy copy without adding influence or changing its submitted config", () => {
    const draft = create(source()), raw = rawCopy(draft), copy = decodeWorkbenchCopy(raw, draft.scope, writerId);
    expect(copy.draft).toEqual(draft);
    expect((copy.draft.input as Input).config).not.toHaveProperty("influence");
    expect(proceduralConfig((copy.draft.input as Input).config)).not.toHaveProperty("influence");
  });
  it("loads saved numeric version two as independent raw strings", async () => {
    const project = await applyChange(source(), { type: "upsert_procedural_field", instance_id: instanceId,
      scene_id: source().scenes[0].id, config: { ...config, influence } });
    expect(project.production_design!.procedural_fields![0].generator_version).toBe(2);
    const draft = create(project), raw = (draft.input as Input).config.influence;
    expect(raw).toEqual(rawProceduralInfluence(influence));
    expect(decodeWorkbenchCopy(rawCopy(draft), draft.scope, writerId).draft).toEqual(draft);
  });
  it("retains whitespace and leading zeroes while dispatching complete integers", async () => {
    const project = source(), draft = authored(project, { enabled: true, center_x: " 0600 ",
      center_y: "00400", radius: " 00150 ", target_size: "0064" });
    expect(decodeWorkbenchCopy(rawCopy(draft), draft.scope, writerId).draft.input).toEqual(draft.input);
    const prepared = await prepareWorkbenchSubmission(draft, project, requestId, "save");
    expect(prepared.change.type).toBe("upsert_procedural_field");
    if (prepared.change.type !== "upsert_procedural_field") throw new Error("Wrong change");
    expect(prepared.change.config.influence).toEqual(influence);
    expect(prepared.draft.pending!.input).toEqual(draft.input);
  });
  for (const key of ["center_x", "center_y", "radius", "target_size"] as const) {
    it("preserves incomplete " + key + " and blocks preparation", async () => {
      const project = source(), draft = later(authored(project), { [key]: "1e" });
      const recovered = decodeWorkbenchCopy(rawCopy(draft), draft.scope, writerId).draft;
      expect((recovered.input as Input).config.influence![key]).toBe("1e");
      await expect(prepareWorkbenchSubmission(recovered, project, requestId, "save")).rejects.toThrow("complete integer");
      expect(recovered.pending).toBeNull();
    });
  }
  for (const malformed of [
    { ...rawProceduralInfluence(influence), effect_grant: "foreign" },
    { enabled: true, center_x: "600", center_y: "400", radius: "150" },
    { ...rawProceduralInfluence(influence), radius: 150 },
    null,
  ]) {
    it("rejects a malformed influence recovery shape " + JSON.stringify(malformed), () => {
      const draft = authored(), copy = JSON.parse(rawCopy(draft));
      copy.draft.input.config.influence = malformed;
      expect(() => decodeWorkbenchCopy(JSON.stringify(copy), draft.scope, writerId)).toThrow();
    });
  }
  it("disabled incomplete parameters survive ACK and a later foreign revision", async () => {
    const project = source(), draft = authored(project, { ...rawProceduralInfluence(influence), enabled: false, radius: "1e" });
    const prepared = await prepareWorkbenchSubmission(draft, project, requestId, "save");
    expect(prepared.change).toHaveProperty("type", "upsert_procedural_field");
    if (prepared.change.type !== "upsert_procedural_field") throw new Error("Wrong change");
    expect(prepared.change.config).not.toHaveProperty("influence");
    const receipt = await applyChange(project, prepared.change);
    const ack = acknowledgeWorkbenchDraft(prepared.draft, requestId, receipt);
    expect(ack.input).toEqual(draft.input); expect(ack.intent).toBe(true); expect(ack.pending).toBeNull();
    expect(receipt.production_design!.procedural_fields![0].generator_version).toBe(1);
    const changed = { ...receipt, revision: receipt.revision + 1 };
    expect(reconcileWorkbenchDraft(ack, changed)).toBe(ack);
    expect(workbenchConflict(ack, changed)).toBe(true);
  });
  it("retains a numeric influence in UNKNOWN comparison data without granting execution", async () => {
    const project = source(), prepared = await prepareWorkbenchSubmission(authored(project), project, requestId, "save");
    const raw = rawCopy(prepared.draft), recovered = decodeWorkbenchCopy(raw, prepared.draft.scope, writerId).draft;
    expect(recovered.pending?.submitted).toEqual(prepared.change);
    expect(recovered.pending?.outcome).toBe("UNKNOWN");
    expect(raw).not.toContain("effect_grant"); expect(raw).not.toContain("capability_token");
  });
  it("rejects an original ACK with a mismatched generator version", async () => {
    const project = source(), prepared = await prepareWorkbenchSubmission(authored(project), project, requestId, "save");
    const receipt = await applyChange(project, prepared.change);
    receipt.production_design!.procedural_fields![0].generator_version = 1;
    expect(() => acknowledgeWorkbenchDraft(prepared.draft, requestId, receipt)).toThrow("exact submitted");
    expect(workbenchUnconfirmed(prepared.draft)).toBe(true);
  });
  it("original ACK preserves influence text edited after dispatch", async () => {
    const project = source(), prepared = await prepareWorkbenchSubmission(authored(project), project, requestId, "save");
    const edited = later(prepared.draft, { radius: "1e", center_x: " 0601 " });
    const receipt = await applyChange(project, prepared.change), ack = acknowledgeWorkbenchDraft(edited, requestId, receipt);
    expect(ack.input).toEqual(edited.input); expect(ack.intent).toBe(true); expect(ack.pending).toBeNull();
    expect((ack.base as Input).config.influence).toEqual(rawProceduralInfluence(influence));
  });
  it("observed matching values leave UNKNOWN until a separate reviewed intent", async () => {
    const project = source(), prepared = await prepareWorkbenchSubmission(authored(project), project, requestId, "save");
    const receipt = await applyChange(project, prepared.change), edited = later(prepared.draft, { target_size: " 96 " });
    expect(reconcileWorkbenchDraft(edited, receipt)).toBe(edited);
    const events: ProjectEvent[] = [{ revision: receipt.revision, change: prepared.change, created_at: "2026-10-23T03:50:00Z" }];
    const review = await inspectWorkbenchOutcome(edited, receipt, { readProject: async () => receipt, readHistory: async () => events });
    expect(review.matchingValueRevisions).toEqual([receipt.revision]);
    expect(workbenchUnconfirmed(edited)).toBe(true);
    const next = releaseWorkbenchOutcome(edited, receipt, review);
    expect(next.input).toEqual(edited.input); expect(next.pending).toBeNull(); expect(next.intent).toBe(true);
  });
  it("preparation rejects a deleted editable object before any dispatch", async () => {
    const project = await saveBase(), deleted = project.production_design!.procedural_fields![0].baseline[1].id;
    project.scenes[0].nodes = project.scenes[0].nodes.filter(node => node.id !== deleted);
    const before = structuredClone(project);
    await expect(prepareWorkbenchSubmission(authored(project), project, requestId, "save")).rejects.toThrow("human deleted");
    expect(project).toEqual(before);
  });
  it("preparation rejects a locked affected property before any dispatch", async () => {
    const project = await saveBase(), middle = project.production_design!.procedural_fields![0].baseline[1].id;
    project.scenes[0].nodes.find(node => node.id === middle)!.property_locks = ["size"];
    const before = structuredClone(project);
    await expect(prepareWorkbenchSubmission(authored(project), project, requestId, "save")).rejects.toThrow("locked");
    expect(project).toEqual(before);
  });
  it("draft preview merges influence with a compatible human style override", async () => {
    const project = await saveBase(), middle = project.production_design!.procedural_fields![0].baseline[1].id;
    project.scenes[0].nodes.find(node => node.id === middle)!.style.fill = "#eebb44";
    const before = structuredClone(project), candidate = structuredClone(project);
    await applyProceduralChange(candidate, { type: "upsert_procedural_field", instance_id: instanceId,
      scene_id: project.scenes[0].id, config: { ...config, influence } });
    const node = candidate.scenes[0].nodes.find(value => value.id === middle)!;
    expect([node.x, node.y, node.width, node.height]).toEqual([568, 368, 64, 64]);
    expect(node.style.fill).toBe("#eebb44");
    expect(project).toEqual(before);
  });
  it("an unconfirmed local write blocks armed influence custody without altering the pointer", async () => {
    const project = source(), prepared = await prepareWorkbenchSubmission(authored(project), project, requestId, "save");
    const local = new Storage(), session = new Storage(), custody: WorkbenchCustody = { writerId, expectedRaw: null, expectedPointer: null };
    local.setItem = () => undefined;
    expect(() => writeWorkbenchCopy(local, session, prepared.draft, custody)).toThrow();
    expect(session.length).toBe(0); expect(custody.expectedRaw).toBeNull();
    expect(local.getItem(workbenchCopyKey(prepared.draft.scope, writerId))).toBeNull();
    expect(prepared.draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("influence detach requires the original receipt to preserve all generated and human objects", async () => {
    const project = source(), prepared = await prepareWorkbenchSubmission(authored(project), project, requestId, "save");
    const saved = await applyChange(project, prepared.change), detached = await prepareWorkbenchSubmission(create(saved), saved, writerId, "detach");
    const receipt = await applyChange(saved, detached.change);
    expect(receipt.scenes[0].nodes).toEqual(saved.scenes[0].nodes);
    expect(acknowledgeWorkbenchDraft(detached.draft, writerId, receipt).pending).toBeNull();
    const corrupt = structuredClone(receipt); corrupt.scenes[0].nodes.pop();
    expect(() => acknowledgeWorkbenchDraft(detached.draft, writerId, corrupt)).toThrow("preserve");
  });
});
