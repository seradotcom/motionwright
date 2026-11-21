/** Native source export API transport checks only. This is not a real Tauri
 * grant, CAS readback, editor launch, or physical acceptance. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import { exportNativeCapsuleSource } from "./api";

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

const sceneId = fixtureProject.scenes[0].id;
const capsuleId = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb";
const assetId = "dddddddd-4444-4444-8444-dddddddddddd";
const digest = "a".repeat(64);
const grant = "cccccccc-3333-4333-8333-cccccccccccc";
const destination = "E:\\NativeSources\\reopen.blend";

function project() {
  const p = structuredClone(fixtureProject);
  p.assets.push({
    id: assetId, name: "reopen.blend", media_type: "application/x-blender",
    content_sha256: digest, source_revision: "native-capsule-source-import",
  });
  p.production_design = {
    ...(p.production_design ?? { plan: null, heroes: [], capsules: [] }),
    capsules: [...(p.production_design?.capsules ?? []), {
      id: capsuleId, scene_id: sceneId, source_asset_id: assetId,
      source_sha256: digest, label: "Editable original",
      editable_parameters: ["headline"], native_editor_hint: "Blender",
      fidelity: {
        renderer: "opaque", renderer_version: "unverified", visual: "unavailable",
        temporal: "unavailable", structural: "unavailable", editable: "unavailable",
        losses: ["No native fidelity claim"], evidence_sha256: null,
      },
    }],
  };
  return p;
}

const sourceArgs = (p: ReturnType<typeof project>, output = destination) =>
  [p, sceneId, capsuleId, assetId, digest, output] as const;

beforeEach(() => {
  invokeMock.mockReset();
  vi.stubGlobal("window", { __TAURI_INTERNALS__: {} });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("one original native source export intent", () => {
  it("issues one exact branch/scene/capsule/asset/digest/destination grant and dispatches once", async () => {
    const p = project();
    const receipt = {
      path: destination, capsule_id: capsuleId, source_asset_id: assetId,
      source_sha256: digest, size_bytes: "24", editor_launched: false,
    };
    invokeMock.mockResolvedValueOnce({ token: grant }).mockResolvedValueOnce(receipt);
    await expect(exportNativeCapsuleSource(p, sceneId, capsuleId, assetId, digest, "  " + destination + "  "))
      .resolves.toEqual(receipt);
    expect(invokeMock.mock.calls).toEqual([
      ["issue_effect_grant", { request: {
        effect: "deliver_local", project_id: p.id,
        generation: p.generation, revision: p.revision,
        subject: ["native-source", p.active_branch, sceneId, capsuleId, assetId, digest, destination].join("|"),
      } }],
      ["export_native_capsule_source", { request: {
        project_id: p.id, generation: p.generation, revision: p.revision,
        scene_id: sceneId, capsule_id: capsuleId, expected_active_branch: p.active_branch,
        expected_source_asset_id: assetId, expected_source_sha256: digest,
        path: destination, effect_grant: grant,
      } }],
    ]);
  });

  it("does not request any grant for a blank destination", async () => {
    await expect(exportNativeCapsuleSource(...sourceArgs(project(), "  ")))
      .rejects.toThrow("Choose an absolute export destination");
    expect(invokeMock).not.toHaveBeenCalled();
  });

  for (const substituted of ["asset", "digest", "capsule", "scene", "uppercase_digest"] as const) {
    it("refuses a changed same-revision " + substituted + " before any local grant", async () => {
      const p = project();
      const other = "eeeeeeee-5555-4555-8555-eeeeeeeeeeee";
      const changed = {
        asset: [p, sceneId, capsuleId, other, digest, destination],
        digest: [p, sceneId, capsuleId, assetId, "b".repeat(64), destination],
        capsule: [p, sceneId, other, assetId, digest, destination],
        scene: [p, other, capsuleId, assetId, digest, destination],
        uppercase_digest: [p, sceneId, capsuleId, assetId, "A".repeat(64), destination],
      }[substituted] as Parameters<typeof exportNativeCapsuleSource>;
      await expect(exportNativeCapsuleSource(...changed)).rejects.toThrow("no longer match");
      expect(invokeMock).not.toHaveBeenCalled();
      expect(p.revision).toBe(fixtureProject.revision);
    });
  }

  it("refuses a UTF-8 grant subject longer than the backend limit before requesting authority", async () => {
    const longPath = "E:\\" + "🙂".repeat(500) + ".blend";
    await expect(exportNativeCapsuleSource(...sourceArgs(project(), longPath)))
      .rejects.toThrow("too long for one exact delivery grant");
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("does not dispatch or retry when the original grant is denied", async () => {
    const refusal = new Error("Explicit local delivery refused");
    invokeMock.mockRejectedValueOnce(refusal);
    await expect(exportNativeCapsuleSource(...sourceArgs(project()))).rejects.toBe(refusal);
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock.mock.calls[0][0]).toBe("issue_effect_grant");
  });

  it("does not replay an uncertain native export response", async () => {
    const uncertain = new Error("Original output status unknown");
    invokeMock.mockResolvedValueOnce({ token: grant }).mockRejectedValueOnce(uncertain);
    await expect(exportNativeCapsuleSource(...sourceArgs(project()))).rejects.toBe(uncertain);
    expect(invokeMock.mock.calls.map(row => row[0]))
      .toEqual(["issue_effect_grant", "export_native_capsule_source"]);
  });
});
