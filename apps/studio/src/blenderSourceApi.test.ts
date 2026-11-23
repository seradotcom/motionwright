/** Frontend transport and read-state tests; no Blender, Broker or physical acceptance. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import {
  BLENDER_RECIPE_MEDIA_TYPE, readBlenderSource, readBlenderAttempt,
  validateBlenderSource, validateBlenderAttempt,
  type BlenderSourceSnapshot, type BlenderAttemptProjection, type BlenderLineage,
} from "./blenderSourceApi";
import type { Project } from "./types";

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

const capsuleId = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb";
const assetId = "dddddddd-4444-4444-8444-dddddddddddd";
const originalId = "cccccccc-3333-4333-8333-cccccccccccc";
const editedId = "eeeeeeee-5555-4555-8555-eeeeeeeeeeee";
const claim = "11111111-2222-4333-8444-555555555555";
const terminal = "22222222-3333-4444-8555-666666666666";
const request = "original-product-1";
const digest = "a".repeat(64);
const flags = { execution_grant_inherited: false, native_dispatch_performed: false,
  full_1200_frame_render: false, decoded_pixel_difference_verified: false,
  visual_acceptance: false, human_acceptance: false } as const;

function project(): Project {
  const p = structuredClone(fixtureProject); p.revision = 4;
  p.scenes[0].renderer = "blender";
  p.assets.push({ id: assetId, name: "product.json", media_type: BLENDER_RECIPE_MEDIA_TYPE,
    content_sha256: digest, source_revision: "frontend-test-fixture" });
  p.production_design = { ...(p.production_design ?? { plan: null, heroes: [], capsules: [] }), capsules: [{
    id: capsuleId, scene_id: p.scenes[0].id, source_asset_id: assetId, source_sha256: digest,
    label: "Saved product source", editable_parameters: ["product.annotation.body"], native_editor_hint: "Blender",
    fidelity: { renderer: "Blender", renderer_version: "unobserved", visual: "unavailable",
      temporal: "unavailable", structural: "unavailable", editable: "unavailable",
      losses: ["Frontend transport fixture"], evidence_sha256: null },
  }] };
  return p;
}
function source(p: Project): BlenderSourceSnapshot {
  return { schema: "motionwright.blender.product-source-snapshot/1", project: structuredClone(p),
    scene_id: p.scenes[0].id, capsule_id: capsuleId, current_source: { asset_id: assetId, sha256: digest },
    original_recipe: { asset_id: assetId, sha256: digest },
    effective_recipe: { version: 1, namespace: "SavedProduct", coating: "ocean", roughness: 0.28,
      metallic: 0.65, samples: 64, annotation: "EDITABLE\nSURFACE", camera_curve: "bezier_auto_clamped" },
    source_kind: "authored_recipe", lineage: null, attempt: null,
    source_cas_verified: true, source_acceptance_inherited: false, ...flags };
}
function stamp(p: Project, revision: number) {
  return { resource: "project:" + p.id, generation: p.generation, revision };
}
function unknownAttempt(p: Project): BlenderAttemptProjection {
  return { schema: "motionwright.blender.product-attempt-projection/1", project: structuredClone(p),
    scene_id: p.scenes[0].id, capsule_id: capsuleId, attempt: {
      original_request_id: request, expected_before: stamp(p, 2), claim_receipt_id: claim,
      terminal_receipt_id: null, state: "outcome_unknown", native_outcome_known: false, error_code: null,
    }, lineage: null, current_source_matches: false, source_cas_verified: false,
    effective_recipe: null, source_unavailable_reason: "original_attempt_has_no_completed_lineage",
    automatic_retry: false, ...flags };
}
function completed(p: Project): { saved: BlenderSourceSnapshot; attempt: BlenderAttemptProjection } {
  p.assets.push(
    { id: originalId, name: "original.blend", media_type: "application/x-blender", content_sha256: "b".repeat(64), source_revision: "fixture" },
    { id: editedId, name: "edited.blend", media_type: "application/x-blender", content_sha256: "c".repeat(64), source_revision: "fixture" },
  );
  const capsule = p.production_design!.capsules[0];
  capsule.source_asset_id = editedId; capsule.source_sha256 = "c".repeat(64);
  const saved = source(p);
  const chain: BlenderLineage = {
    original_request_id: request, claim_receipt_id: claim, terminal_receipt_id: terminal,
    expected_before: stamp(p, 2), original_attached: stamp(p, 3), expected_after: stamp(p, 4),
    recipe: { asset_id: assetId, sha256: digest },
    original: { asset_id: originalId, sha256: "b".repeat(64), bytes: 24 },
    edited: { asset_id: editedId, sha256: "c".repeat(64), bytes: 28 },
    annotation: { before: "EDITABLE\nSURFACE", after: "EDITED\nSURFACE" },
    original_surface_png_sha256: "d".repeat(64), edited_surface_png_sha256: "e".repeat(64),
  };
  saved.current_source = { asset_id: editedId, sha256: "c".repeat(64) };
  saved.source_kind = "edited_native_source"; saved.effective_recipe.annotation = chain.annotation.after;
  saved.lineage = chain;
  saved.attempt = { ...unknownAttempt(p).attempt, terminal_receipt_id: terminal,
    state: "completed", native_outcome_known: true };
  const attempt = { ...unknownAttempt(p), attempt: saved.attempt, lineage: chain,
    current_source_matches: true, source_cas_verified: true,
    effective_recipe: saved.effective_recipe, source_unavailable_reason: null };
  return { saved, attempt };
}
beforeEach(() => { invokeMock.mockReset(); vi.stubGlobal("window", { __TAURI_INTERNALS__: {} }); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("saved Blender source and original attempt reads", () => {
  it("reads the exact project/scene/capsule without issuing or inheriting an effect grant", async () => {
    const p = project(), reply = source(p);
    invokeMock.mockResolvedValueOnce(reply);
    await expect(readBlenderSource(p, p.scenes[0].id, capsuleId)).resolves.toEqual(reply);
    expect(invokeMock.mock.calls).toEqual([["native_blender_product_source", { request: {
      project_id: p.id, generation: p.generation, revision: p.revision, scene_id: p.scenes[0].id, capsule_id: capsuleId,
    } }]]);
    expect(reply.native_dispatch_performed).toBe(false);
  });
  it("shows the real original/edited lineage only when every current source binding matches", () => {
    const p = project(), value = completed(p);
    expect(validateBlenderSource(p, p.scenes[0].id, capsuleId, value.saved)).toEqual(value.saved);
    expect(validateBlenderAttempt(p, p.scenes[0].id, capsuleId, request, terminal, value.attempt)).toEqual(value.attempt);
    const wrong = structuredClone(value.saved); wrong.lineage!.edited.sha256 = "f".repeat(64);
    expect(() => validateBlenderSource(p, p.scenes[0].id, capsuleId, wrong)).toThrow("read-only contract");
  });
  for (const field of ["execution_grant_inherited", "native_dispatch_performed", "full_1200_frame_render",
    "decoded_pixel_difference_verified", "visual_acceptance", "human_acceptance", "source_acceptance_inherited"]) {
    it("rejects a source reply claiming " + field, () => {
      const p = project();
      expect(() => validateBlenderSource(p, p.scenes[0].id, capsuleId, { ...source(p), [field]: true }))
        .toThrow("read-only contract");
    });
  }
  for (const field of ["revision", "generation", "active_branch"] as const) {
    it("rejects a reply from a changed project " + field, () => {
      const p = project(), reply = source(p);
      if (field === "revision") reply.project.revision++;
      else reply.project[field] = editedId;
      expect(() => validateBlenderSource(p, p.scenes[0].id, capsuleId, reply)).toThrow("read-only contract");
    });
  }
  it("rejects a same-revision capsule or asset substitution before IPC", async () => {
    const p = project(); p.production_design!.capsules[0].source_sha256 = "f".repeat(64);
    await expect(readBlenderSource(p, p.scenes[0].id, capsuleId)).rejects.toThrow("read-only contract");
    expect(invokeMock).not.toHaveBeenCalled();
  });
  it("retains UNKNOWN as an original journal state and only queries that exact request", async () => {
    const p = project(), reply = unknownAttempt(p);
    invokeMock.mockResolvedValueOnce(reply);
    const result = await readBlenderAttempt(p, p.scenes[0].id, capsuleId, request, claim);
    expect(result.attempt.state).toBe("outcome_unknown");
    expect(invokeMock.mock.calls).toEqual([["native_blender_product_attempt", { request: {
      source: { project_id: p.id, generation: p.generation, revision: p.revision, scene_id: p.scenes[0].id, capsule_id: capsuleId },
      original_request_id: request, receipt_id: claim,
    } }]]);
    expect(result.automatic_retry).toBe(false);
  });
  it("keeps a recorded completion when current source verification is unavailable", () => {
    const p = project(), value = completed(p).attempt;
    value.source_cas_verified = false; value.effective_recipe = null;
    value.source_unavailable_reason = "current_source_cas_or_lineage_unavailable";
    const observed = validateBlenderAttempt(p, p.scenes[0].id, capsuleId, request, null, value);
    expect(observed.attempt.state).toBe("completed");
    expect(observed.source_cas_verified).toBe(false);
  });
  it("refuses another original request, receipt, an inherited retry or a false known outcome", () => {
    const p = project(), value = unknownAttempt(p);
    for (const changed of [
      { ...value, automatic_retry: true },
      { ...value, attempt: { ...value.attempt, native_outcome_known: true } },
      { ...value, attempt: { ...value.attempt, original_request_id: "foreign-original" } },
    ]) expect(() => validateBlenderAttempt(p, p.scenes[0].id, capsuleId, request, null, changed)).toThrow();
    expect(() => validateBlenderAttempt(p, p.scenes[0].id, capsuleId, request, terminal, value)).toThrow();
  });
  for (const identity of ["", "x".repeat(49), "same:attempt", "line\nbreak", "unicode-é"]) {
    it("refuses an unadmitted original request identity before IPC: " + JSON.stringify(identity), async () => {
      const p = project();
      await expect(readBlenderAttempt(p, p.scenes[0].id, capsuleId, identity)).rejects.toThrow();
      expect(invokeMock).not.toHaveBeenCalled();
    });
  }
  it("does not manufacture saved source observations in browser mode", async () => {
    vi.stubGlobal("window", {});
    const p = project();
    await expect(readBlenderSource(p, p.scenes[0].id, capsuleId)).rejects.toThrow("desktop project store");
    expect(invokeMock).not.toHaveBeenCalled();
  });
  it("does not retry a failed read or reinterpret it as a native attempt failure", async () => {
    const p = project(); invokeMock.mockRejectedValueOnce({ schema: "motionwright.blender.product-read-failure/1", code: "Unavailable" });
    await expect(readBlenderAttempt(p, p.scenes[0].id, capsuleId, request)).rejects.toThrow("original native attempt has not been changed");
    expect(invokeMock).toHaveBeenCalledTimes(1);
  });
});
