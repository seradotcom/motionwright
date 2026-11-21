/** Source-scoped Native capsule export transport only; no grant authority, CAS
 * operation, installed Tauri, native editor or physical output is claimed. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import { exportNativeCapsuleSource } from "./api";

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

const project = () => structuredClone(fixtureProject);
const sceneId = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa";
const capsuleId = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb";
const grant = "cccccccc-3333-4333-8333-cccccccccccc";
const destination = "E:\\NativeSources\\reopen.blend";

beforeEach(() => {
  invokeMock.mockReset();
  vi.stubGlobal("window", { __TAURI_INTERNALS__: {} });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("one original native source export intent", () => {
  it("issues one source-scoped local deliver grant and dispatches its exact capsule once", async () => {
    const p = project();
    const receipt = {
      path: destination, capsule_id: capsuleId, source_asset_id: p.assets[0]?.id ?? capsuleId,
      source_sha256: "a".repeat(64), size_bytes: "24", editor_launched: false,
    };
    invokeMock.mockResolvedValueOnce({ token: grant }).mockResolvedValueOnce(receipt);
    await expect(exportNativeCapsuleSource(p, sceneId, capsuleId, "  " + destination + "  "))
      .resolves.toEqual(receipt);
    expect(invokeMock.mock.calls).toEqual([
      ["issue_effect_grant", { request: {
        effect: "deliver_local", project_id: p.id,
        generation: p.generation, revision: p.revision,
        subject: "native-source|" + capsuleId + "|" + destination,
      } }],
      ["export_native_capsule_source", { request: {
        project_id: p.id, generation: p.generation, revision: p.revision,
        scene_id: sceneId, capsule_id: capsuleId,
        path: destination, effect_grant: grant,
      } }],
    ]);
  });

  it("does not request any grant for a blank destination", async () => {
    await expect(exportNativeCapsuleSource(project(), sceneId, capsuleId, "  "))
      .rejects.toThrow("Choose an absolute export destination");
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("does not dispatch or retry if the original grant is denied", async () => {
    const refusal = new Error("Explicit local delivery refused");
    invokeMock.mockRejectedValueOnce(refusal);
    await expect(exportNativeCapsuleSource(project(), sceneId, capsuleId, destination))
      .rejects.toBe(refusal);
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock.mock.calls[0][0]).toBe("issue_effect_grant");
  });
});
