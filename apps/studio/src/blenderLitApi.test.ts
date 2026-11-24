/** Transport fixtures only: no Blender execution, pixels or acceptance. */
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import { createBlenderLitIntent, inspectBlenderLitSource, prepareBlenderLitStill, startBlenderLitStill,
  readBlenderLitAttempt, BlenderLitUnconfirmedError, type BlenderLitPlan, type BlenderLitSource } from "./blenderLitApi";
const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));
const planId = "11111111-2222-4333-8444-555555555555", token = "22222222-3333-4444-8555-666666666666";
function fixture() {
  const project = structuredClone(fixtureProject); project.scenes[0].renderer = "blender";
  const source: BlenderLitSource = {
    schema: "motionwright.blender.desktop-lit-source/1",
    source: { project_id: project.id, generation: project.generation, revision: project.revision, scene_id: project.scenes[0].id },
    source_sha256: "a".repeat(64), fingerprint: "b".repeat(64), attempt_key: "blender-lit-" + "b".repeat(64),
    attempted: false, width: 1920, height: 1080, object_count: 1,
    profile: "planar-project-cycles-lit-still-v1", native_dispatch_performed: false,
  };
  const intent = createBlenderLitIntent(project, source);
  const plan: BlenderLitPlan = {
    schema: "motionwright.blender.desktop-lit-plan/1", plan_id: planId, intent, capsule_id: token,
    effect_subject: "blender-lit|" + planId + "|" + "c".repeat(64), attempt_key: source.attempt_key,
    expires_in_seconds: 600, native_dispatch_performed: false, execution_grant_inherited: false,
  };
  const grant = { token, effect: "render_local", project_id: project.id, generation: project.generation, revision: project.revision, one_time: true };
  const unknown = {
    schema: "motionwright.blender.desktop-lit-attempt/1", attempt_key: source.attempt_key,
    original_request_id: intent.request_id, source: intent.source, source_sha256: intent.source_sha256, fingerprint: intent.fingerprint,
    state: "outcome_unknown", source_current: true, native_outcome: { Err: { outcome_known: false, message: "Original native reply unknown" } },
    saved_assets: null, journal: { phase: "lit-create", native_call_count: 1 }, receipt_id: token,
    automatic_retry: false, execution_grant_inherited: false, visual_acceptance: false,
  };
  return { project, source, intent, plan, grant, unknown };
}
beforeEach(() => { invokeMock.mockReset(); vi.stubGlobal("window", { __TAURI_INTERNALS__: {} }); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it("inspects the current saved source and prepares one owner plan without starting", async () => {
  const f = fixture(), barrier = vi.fn();
  invokeMock.mockResolvedValueOnce(f.source).mockResolvedValueOnce(f.plan);
  await expect(inspectBlenderLitSource(f.project, f.source.source.scene_id)).resolves.toEqual(f.source);
  await expect(prepareBlenderLitStill(f.project, f.intent, barrier)).resolves.toEqual(f.plan);
  expect(barrier).toHaveBeenCalledTimes(1);
  expect(invokeMock.mock.calls.map(row => row[0])).toEqual(["native_blender_lit_source", "prepare_blender_lit_still"]);
});
it("does not prepare another identity for a previously attempted composition", () => {
  const f = fixture(); f.source.attempted = true;
  expect(() => createBlenderLitIntent(f.project, f.source)).toThrow("original Blender still attempt");
  expect(invokeMock).not.toHaveBeenCalled();
});
it("rejects stale source and a substituted preparation before a grant", async () => {
  const f = fixture(), substituted = structuredClone(f.plan); substituted.intent.source_sha256 = "d".repeat(64);
  await expect(startBlenderLitStill(f.project, f.intent, substituted, () => {})).rejects.toThrow("preparation");
  f.project.revision++;
  await expect(prepareBlenderLitStill(f.project, f.intent, () => {})).rejects.toThrow("no longer matches");
  expect(invokeMock).not.toHaveBeenCalled();
});
it("preserves cancellation and stops post-grant UI changes before Native dispatch", async () => {
  const f = fixture(); invokeMock.mockResolvedValueOnce(null);
  await expect(prepareBlenderLitStill(f.project, f.intent, () => {})).resolves.toBeNull();
  invokeMock.mockReset(); invokeMock.mockResolvedValueOnce(f.grant);
  let phases = 0;
  await expect(startBlenderLitStill(f.project, f.intent, f.plan, () => { if (++phases === 2) throw new Error("Scene changed"); })).rejects.toThrow("Scene changed");
  expect(invokeMock.mock.calls.map(row => row[0])).toEqual(["issue_effect_grant"]);
});
it("retains the original identity after one lost native reply and performs only readonly recovery", async () => {
  const f = fixture(); invokeMock.mockResolvedValueOnce(f.grant).mockRejectedValueOnce(new Error("Reply lost"));
  const error = await startBlenderLitStill(f.project, f.intent, f.plan, () => {}).catch(e => e);
  expect(error).toBeInstanceOf(BlenderLitUnconfirmedError); expect(error.original).toEqual(f.intent);
  invokeMock.mockResolvedValueOnce(f.unknown);
  await expect(readBlenderLitAttempt(f.project, f.source.source.scene_id, f.source.attempt_key)).resolves.toEqual(f.unknown);
  expect(invokeMock.mock.calls.map(row => row[0])).toEqual(["issue_effect_grant", "start_blender_lit_still", "read_original_blender_lit_still"]);
});
it("an absent receipt remains absence without any automatic render", async () => {
  const f = fixture(); invokeMock.mockResolvedValueOnce(null);
  await expect(readBlenderLitAttempt(f.project, f.source.source.scene_id, f.source.attempt_key)).resolves.toBeNull();
  expect(invokeMock.mock.calls.map(row => row[0])).toEqual(["read_original_blender_lit_still"]);
});
it("keeps Native UNKNOWN and rejects a malformed claimed completion", async () => {
  const f = fixture(); invokeMock.mockResolvedValueOnce(f.grant).mockResolvedValueOnce(f.unknown);
  await expect(startBlenderLitStill(f.project, f.intent, f.plan, () => {})).resolves.toEqual(f.unknown);
  invokeMock.mockReset(); invokeMock.mockResolvedValueOnce(f.grant).mockResolvedValueOnce({ ...f.unknown, state: "completed", native_outcome: { Ok: {} } });
  await expect(startBlenderLitStill(f.project, f.intent, f.plan, () => {})).rejects.toBeInstanceOf(BlenderLitUnconfirmedError);
  expect(invokeMock).toHaveBeenCalledTimes(2);
});
it("rejects a foreign grant and browser-only dispatch", async () => {
  const f = fixture(); invokeMock.mockResolvedValueOnce({ ...f.grant, generation: planId });
  await expect(startBlenderLitStill(f.project, f.intent, f.plan, () => {})).rejects.toThrow("grant does not match");
  expect(invokeMock).toHaveBeenCalledTimes(1);
  invokeMock.mockReset(); vi.stubGlobal("window", {});
  await expect(prepareBlenderLitStill(f.project, f.intent, () => {})).rejects.toThrow("desktop");
  expect(invokeMock).not.toHaveBeenCalled();
});
