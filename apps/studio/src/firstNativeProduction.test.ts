import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import type { MotionCanvasProjectionPreflight, ProductionRuntimeCompatibility } from "./types";
import {
  evaluateFirstNativeRenderGate, firstRenderRuntimeScope,
  type ScopedRuntimeObservation,
} from "./firstNativeProduction";

const project = structuredClone(fixtureProject);
const profileId = project.deliverables[0].id;
const preflight: MotionCanvasProjectionPreflight = {
  project_resource: "project:" + project.id, generation: project.generation,
  revision: project.revision, deliverable_id: profileId,
  verdict: "projection_ready", reason: null, segment_count: 1, total_frames: 60,
  segments: [{ segment_id: "native-segment", scene_ids: [project.scenes[0].id], frame_count: 60 }],
};
const runtime: ProductionRuntimeCompatibility = {
  status: "ready", reason: null, expected_version: "1.0.0", observed_version: "1.0.0",
  version_compatible: true, executable_sha256: "a".repeat(64),
  connection_identity: "b".repeat(64), pinned_revision: "c".repeat(40),
};
const observed: ScopedRuntimeObservation = {
  scope: firstRenderRuntimeScope(project), value: runtime,
};

function gate(changes: Partial<Parameters<typeof evaluateFirstNativeRenderGate>[0]> = {}) {
  return evaluateFirstNativeRenderGate({
    project, profileId, desktopMode: true, profileDirty: false,
    sceneIntentComplete: true, nativePaletteReady: true,
    preflight, runtime: observed, ...changes,
  });
}
describe("v0.1 owner first native production gate", () => {
  it("never promotes a successful preflight to actual Broker or frame evidence", () => {
    const result = gate();
    expect(result).toMatchObject({
      code: "ELIGIBLE_FOR_AUTHORIZED_DISPATCH", may_request_native_render: true,
      native_render_verified: false, broker_health_attested: false,
    });
    expect(result.explanation).toContain("one-time grant");
  });
  it("browser mode always disables native dispatch despite spoofed ready data", () => {
    expect(gate({ desktopMode: false }).code).toBe("BROWSER_ONLY");
  });
  it("unsaved or dirty output cannot dispatch", () => {
    expect(gate({ profileDirty: true }).code).toBe("PROFILE_NOT_SAVED");
    expect(gate({ profileId: "unrelated" }).may_request_native_render).toBe(false);
  });
  it("incomplete intent and missing palette fail closed", () => {
    expect(gate({ sceneIntentComplete: false }).code).toBe("SCENE_INTENT_INCOMPLETE");
    expect(gate({ nativePaletteReady: false }).code).toBe("VISUAL_TOKENS_MISSING");
  });
  it("read-only Film evidence must be explicit and match project revision", () => {
    expect(gate({ preflight: null }).code).toBe("FILM_PREFLIGHT_REQUIRED");
    expect(gate({ preflight: { ...preflight, revision: project.revision + 1 } }).code)
      .toBe("FILM_PREFLIGHT_STALE");
    expect(gate({ preflight: { ...preflight, generation: crypto.randomUUID() } }).code)
      .toBe("FILM_PREFLIGHT_STALE");
    expect(gate({ preflight: { ...preflight, deliverable_id: crypto.randomUUID() } }).code)
      .toBe("FILM_PREFLIGHT_STALE");
  });
  it("never trusts false projection readiness or inconsistent frame counts", () => {
    expect(gate({ preflight: { ...preflight, verdict: "unsupported" } }).code)
      .toBe("FILM_PREFLIGHT_REJECTED");
    expect(gate({ preflight: { ...preflight, total_frames: 59 } }).may_request_native_render)
      .toBe(false);
    expect(gate({ preflight: { ...preflight, segment_count: 2 } }).may_request_native_render)
      .toBe(false);
    expect(gate({ preflight: { ...preflight, segments: [{...preflight.segments[0], frame_count: 0}] } })
      .may_request_native_render).toBe(false);
  });
  it("missing or old runtime observation cannot dispatch", () => {
    expect(gate({ runtime: null }).code).toBe("RUNTIME_NOT_CHECKED");
    expect(gate({ runtime: { ...observed, scope: "stale" } }).code).toBe("RUNTIME_OBSERVATION_STALE");
  });
  it("version-only claims, wrong version or incomplete binding cannot dispatch", () => {
    const mod = (change: Partial<ProductionRuntimeCompatibility>) =>
      gate({ runtime: { ...observed, value: { ...runtime, ...change } } });
    expect(mod({ status: "invalid" }).may_request_native_render).toBe(false);
    expect(mod({ status: "unavailable" }).may_request_native_render).toBe(false);
    expect(mod({ version_compatible: null }).may_request_native_render).toBe(false);
    expect(mod({ observed_version: "1.2.0" }).may_request_native_render).toBe(false);
    expect(mod({ executable_sha256: "unverified" }).may_request_native_render).toBe(false);
    expect(mod({ connection_identity: null }).may_request_native_render).toBe(false);
    expect(mod({ pinned_revision: "" }).may_request_native_render).toBe(false);
  });
  it("a different project generation/revision never accepts an old runtime scope", () => {
    const changed = { ...project, revision: project.revision + 1 };
    expect(gate({ project: changed }).may_request_native_render).toBe(false);
  });
});