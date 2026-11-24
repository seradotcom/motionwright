/** Closed transport fixtures only; no Native execution or acceptance. */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import { BLENDER_RECIPE_MEDIA_TYPE, type BlenderSourceSnapshot } from "./blenderSourceApi";
import { createBlenderCycleIntent, prepareBlenderCycle, startBlenderCycle,
  BlenderCycleUnconfirmedError, type BlenderCycleIntent, type BlenderCyclePlan } from "./blenderCycleApi";
const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));
const digest = "a".repeat(64), asset = "dddddddd-4444-4444-8444-dddddddddddd",
  capsule = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb", planId = "11111111-2222-4333-8444-555555555555",
  token = "22222222-3333-4444-8555-666666666666";
function fixture() {
  const project = structuredClone(fixtureProject);
  project.scenes[0].renderer = "blender";
  project.assets.push({ id: asset, name: "product.json", media_type: BLENDER_RECIPE_MEDIA_TYPE,
    content_sha256: digest, source_revision: "transport-fixture" });
  project.production_design = { plan: null, heroes: [], capsules: [{
    id: capsule, scene_id: project.scenes[0].id, source_asset_id: asset, source_sha256: digest,
    label: "Product", editable_parameters: ["product.annotation.body"], native_editor_hint: "Blender",
    fidelity: { renderer: "Blender", renderer_version: "unobserved", visual: "unavailable",
      temporal: "unavailable", structural: "unavailable", editable: "unavailable", losses: ["Transport fixture"], evidence_sha256: null },
  }] };
  const source: BlenderSourceSnapshot = {
    schema: "motionwright.blender.product-source-snapshot/1", project: structuredClone(project),
    scene_id: project.scenes[0].id, capsule_id: capsule, current_source: { asset_id: asset, sha256: digest },
    original_recipe: { asset_id: asset, sha256: digest },
    effective_recipe: { version: 1, namespace: "CycleFixture", coating: "ocean", roughness: 0.28,
      metallic: 0.65, samples: 64, annotation: "ORIGINAL", camera_curve: "bezier_auto_clamped" },
    source_kind: "authored_recipe", lineage: null, attempt: null, source_cas_verified: true,
    source_acceptance_inherited: false, execution_grant_inherited: false, native_dispatch_performed: false,
    full_1200_frame_render: false, decoded_pixel_difference_verified: false, visual_acceptance: false, human_acceptance: false,
  };
  const intent = createBlenderCycleIntent(project, source, "EDITED\nSURFACE");
  return { project, source, intent, plan: plan(intent) };
}
function plan(intent: BlenderCycleIntent): BlenderCyclePlan {
  return { schema: "motionwright.blender.desktop-product-plan/1", plan_id: planId,
    source: structuredClone(intent.source), source_sha256: intent.source_sha256, request_id: intent.request_id,
    annotation_before: "ORIGINAL", annotation_after: intent.annotation_body,
    effect_subject: "blender-product|" + planId + "|" + "b".repeat(64), expires_in_seconds: 600,
    phases: ["create_original", "edit_original", "verify_edited"], native_dispatch_performed: false, execution_grant_inherited: false };
}
function grant(f: ReturnType<typeof fixture>) {
  return { token, effect: "render_local", project_id: f.project.id, generation: f.project.generation,
    revision: f.project.revision, one_time: true, expires_in_seconds: 120 };
}
beforeEach(() => { invokeMock.mockReset(); vi.stubGlobal("window", { __TAURI_INTERNALS__: {} }); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("prepares the exact retained intent without acquiring or dispatching an effect", async () => {
  const f = fixture(), barrier = vi.fn();
  invokeMock.mockResolvedValueOnce(f.plan);
  await expect(prepareBlenderCycle(f.project, f.intent, barrier)).resolves.toEqual(f.plan);
  expect(barrier).toHaveBeenCalledTimes(1);
  expect(invokeMock.mock.calls).toEqual([["prepare_blender_product_cycle", { request: f.intent }]]);
});
it("canceling connection selection leaves the original intent intact and sends no start", async () => {
  const f = fixture(), before = structuredClone(f.intent);
  invokeMock.mockResolvedValueOnce(null);
  await expect(prepareBlenderCycle(f.project, f.intent, () => {})).resolves.toBeNull();
  expect(f.intent).toEqual(before); expect(invokeMock).toHaveBeenCalledTimes(1);
});
it("refuses a substituted source or preparation before acquiring a real grant", async () => {
  const f = fixture(), changed = structuredClone(f.plan);
  changed.source_sha256 = "c".repeat(64);
  await expect(startBlenderCycle(f.project, f.intent, changed, () => {})).rejects.toThrow("does not match");
  f.project.revision++;
  await expect(startBlenderCycle(f.project, f.intent, f.plan, () => {})).rejects.toThrow("no longer matches");
  expect(invokeMock).not.toHaveBeenCalled();
});
it("stops a post-grant stale UI before dispatch and preserves the submitted identity", async () => {
  const f = fixture();
  invokeMock.mockResolvedValueOnce(grant(f));
  let phases = 0;
  await expect(startBlenderCycle(f.project, f.intent, f.plan, () => {
    if (++phases === 2) throw new Error("Project changed while granting");
  })).rejects.toThrow("Project changed");
  expect(invokeMock.mock.calls.map(row => row[0])).toEqual(["issue_effect_grant"]);
});
it("retains one original request after a lost native reply and never retries", async () => {
  const f = fixture();
  invokeMock.mockResolvedValueOnce(grant(f)).mockRejectedValueOnce(new Error("Lost original reply"));
  const error = await startBlenderCycle(f.project, f.intent, f.plan, () => {}).catch(value => value);
  expect(error).toBeInstanceOf(BlenderCycleUnconfirmedError);
  expect(error.original).toEqual(f.intent);
  expect(invokeMock.mock.calls.map(row => row[0])).toEqual(["issue_effect_grant", "start_blender_product_cycle"]);
  expect(invokeMock.mock.calls[1][1]).toEqual({ request: { source: f.intent.source, source_sha256: digest,
    request_id: f.intent.request_id, plan_id: planId, effect_grant: token } });
});
it("keeps a Native UNKNOWN terminal as an error outcome without inferring completion", async () => {
  const f = fixture();
  const response = { schema: "motionwright.blender.desktop-product-cycle/1", source: f.intent.source,
    original_request_id: f.intent.request_id, outcome: { Err: { code: "UNAVAILABLE", message: "Original outcome unknown", effect: "unknown" } },
    journal: { schema: "motionwright.blender.product-cycle-journal-summary/1", automatic_retry: false }, automatic_retry: false };
  invokeMock.mockResolvedValueOnce(grant(f)).mockResolvedValueOnce(response);
  await expect(startBlenderCycle(f.project, f.intent, f.plan, () => {})).resolves.toEqual(response);
  expect(invokeMock).toHaveBeenCalledTimes(2);
});
it("rejects malformed success and borrowed grants without a fallback start", async () => {
  const f = fixture();
  invokeMock.mockResolvedValueOnce({ ...grant(f), generation: capsule });
  await expect(startBlenderCycle(f.project, f.intent, f.plan, () => {})).rejects.toThrow("grant does not match");
  expect(invokeMock).toHaveBeenCalledTimes(1);
  invokeMock.mockReset();
  invokeMock.mockResolvedValueOnce(grant(f)).mockResolvedValueOnce({
    schema: "motionwright.blender.desktop-product-cycle/1", source: f.intent.source,
    original_request_id: f.intent.request_id, outcome: { Ok: {} },
    journal: { schema: "motionwright.blender.product-cycle-journal-summary/1", automatic_retry: false }, automatic_retry: false,
  });
  await expect(startBlenderCycle(f.project, f.intent, f.plan, () => {})).rejects.toBeInstanceOf(BlenderCycleUnconfirmedError);
  expect(invokeMock).toHaveBeenCalledTimes(2);
});
