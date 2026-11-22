/** Source transport tests, not actual media, Tauri, NLE or Store acceptance. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { previewInstalledOtioMediaCut } from "./api";
import { reviewableOtioProject, sampleOtioPreview } from "./otioCutReviewTestFixtures";

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));
const bundle = "/owner/original-bundle";
const token = "cccccccc-3333-4333-8333-cccccccccccc";
function response(native = true) {
  const project = reviewableOtioProject();
  const preview = sampleOtioPreview(project);
  return {
    preview, source_project_sha256: preview.source_project_sha256,
    media_refs_verified: preview.media_refs_verified,
    input_profile: native ? "kdenlive-otio-pair" : "metadata-preserving-otio",
    received_otio_sha256: native ? "a".repeat(64) : null,
    edited_otio_sha256: "b".repeat(64),
    runtime: "test transport only", source_media_relinked: false,
    editor_launched: false, project_modified: false,
  };
}
beforeEach(() => {
  invokeMock.mockReset();
  vi.stubGlobal("window", { __TAURI_INTERNALS__: {} });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("original Kdenlive export pair", () => {
  it("binds both original filenames and the current revision into one owner grant", async () => {
    const project = reviewableOtioProject();
    const receipt = response();
    invokeMock.mockResolvedValueOnce({ token }).mockResolvedValueOnce(receipt);
    await expect(previewInstalledOtioMediaCut(project, " " + bundle + " ",
      " edited.otio ", " received.otio ")).resolves.toEqual(receipt);
    expect(invokeMock.mock.calls).toEqual([
      ["issue_effect_grant", { request: {
        effect: "import_local", project_id: project.id,
        generation: project.generation, revision: project.revision,
        subject: JSON.stringify(["otio-kdenlive-pair-preview/1", bundle,
          "received.otio", "edited.otio"]),
      } }],
      ["preview_otio_media_cut", { request: {
        project_id: project.id, generation: project.generation, revision: project.revision,
        bundle_path: bundle, edited_file: "edited.otio",
        received_file: "received.otio", effect_grant: token,
      } }],
    ]);
  });

  it("preserves the original metadata-bearing grant without inventing a received file", async () => {
    const project = reviewableOtioProject();
    invokeMock.mockResolvedValueOnce({ token }).mockResolvedValueOnce(response(false));
    await previewInstalledOtioMediaCut(project, bundle, "edited.otio");
    expect(invokeMock.mock.calls[0][1].request.subject)
      .toBe(JSON.stringify(["otio-bridge-preview/1", bundle, "edited.otio"]));
    expect(invokeMock.mock.calls[1][1].request).not.toHaveProperty("received_file");
  });

  for (const initial of ["", "  ", "../received.otio", "a/b.otio",
    "C:\\received.otio", "edited.otio", "timeline.otio"]) {
    it("refuses unbound initial selection " + JSON.stringify(initial) + " before requesting a grant", async () => {
      await expect(previewInstalledOtioMediaCut(reviewableOtioProject(),
        bundle, "edited.otio", initial)).rejects.toThrow("distinct original");
      expect(invokeMock).not.toHaveBeenCalled();
    });
  }

  for (const substitution of ["profile", "initial-hash", "edited-hash", "revision", "effect"] as const) {
    it("rejects a substituted " + substitution + " response without replay", async () => {
      const receipt = response();
      if (substitution === "profile") receipt.input_profile = "metadata-preserving-otio";
      if (substitution === "initial-hash") receipt.received_otio_sha256 = null;
      if (substitution === "edited-hash") receipt.edited_otio_sha256 = "not-an-original";
      if (substitution === "revision") receipt.preview.expected_project_revision++;
      if (substitution === "effect") receipt.editor_launched = true;
      invokeMock.mockResolvedValueOnce({ token }).mockResolvedValueOnce(receipt);
      await expect(previewInstalledOtioMediaCut(reviewableOtioProject(),
        bundle, "edited.otio", "received.otio")).rejects.toThrow("different source");
      expect(invokeMock).toHaveBeenCalledTimes(2);
    });
  }

  it("does not downgrade, regrant or retry an unknown native inspection", async () => {
    const unknown = new Error("Original media operation UNKNOWN");
    invokeMock.mockResolvedValueOnce({ token }).mockRejectedValueOnce(unknown);
    await expect(previewInstalledOtioMediaCut(reviewableOtioProject(),
      bundle, "edited.otio", "received.otio")).rejects.toBe(unknown);
    expect(invokeMock.mock.calls.map(row => row[0]))
      .toEqual(["issue_effect_grant", "preview_otio_media_cut"]);
  });

  it("keeps browser-only review from requesting a native effect", async () => {
    vi.stubGlobal("window", {});
    await expect(previewInstalledOtioMediaCut(reviewableOtioProject(),
      bundle, "edited.otio", "received.otio")).rejects.toThrow("requires Motionwright desktop");
    expect(invokeMock).not.toHaveBeenCalled();
  });
});
