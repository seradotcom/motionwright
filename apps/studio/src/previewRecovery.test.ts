import { describe, expect, it } from "vitest";
import {
  captureDecodedPreview, MAX_RECOVERY_FRAME_BYTES, previewRecoveryScopeKey,
  sealPreviewRecovery, verifyPreviewRecovery, type PreviewRecoveryScope,
} from "./previewRecovery";

// Pure storage-envelope checks. Browser decode, IndexedDB, quota and reopen are
// exercised separately by real Chromium; this file is not native evidence.
const png = Uint8Array.from(atob(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4XyX4HwAGkAKKXzfbLwAAAABJRU5ErkJggg=="), c => c.charCodeAt(0));
const owner = "e199c218-b9d9-4b40-bb65-dd3f081f5728";
const scope: PreviewRecoveryScope = { projectId: "project-one", generation: "generation-one", branchId: "main",
  sceneId: "scene-one", profileId: "profile-one" };
const capture = () => captureDecodedPreview(scope, 7, "Saved scene", {
  bytes: png, frameIndex: 2, frameCount: 120, pixelWidth: 1, pixelHeight: 1,
}, owner);

describe("decoded preview recovery envelope", () => {
  it("preserves bytes independently and hashes scope, source revision and metadata as well as PNG", async () => {
    const frame = capture(), sealed = await sealPreviewRecovery(frame);
    expect(await verifyPreviewRecovery(sealed, scope, 8)).toEqual(sealed);
    expect(sealed.sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(new Uint8Array(await sealed.blob.arrayBuffer())).toEqual(png);
    expect(Object.keys(sealed).sort()).not.toContain("token");
    expect(Object.keys(sealed).sort()).not.toContain("path");
    expect(await sealPreviewRecovery({ ...frame, sourceRevision: 8 })).not.toHaveProperty("sha256", sealed.sha256);
  });
  it.each(["projectId", "generation", "branchId", "sceneId", "profileId"] as const)(
    "rejects a different %s and does not mutate its input", async field => {
      const sealed = await sealPreviewRecovery(capture()), before = structuredClone(sealed);
      await expect(verifyPreviewRecovery(sealed, { ...scope, [field]: "other" }, 7)).rejects.toThrow("scope changed");
      expect(sealed).toEqual(before);
    },
  );
  it("rejects future revisions and incomplete source metadata", async () => {
    const sealed = await sealPreviewRecovery(capture());
    await expect(verifyPreviewRecovery(sealed, scope, 6)).rejects.toThrow("later revision");
    await expect(verifyPreviewRecovery({ ...sealed, sourceRevision: "7" }, scope, 7)).rejects.toThrow("incomplete or corrupt");
    expect(() => previewRecoveryScopeKey({ ...scope, branchId: "" })).toThrow("exact project");
  });
  it("rejects changed pixels with an otherwise intact envelope", async () => {
    const sealed = await sealPreviewRecovery(capture()), bytes = new Uint8Array(png);
    bytes[40] ^= 1;
    await expect(verifyPreviewRecovery({ ...sealed, blob: new Blob([bytes], { type: "image/png" }) }, scope, 7))
      .rejects.toThrow("SHA-256 check");
  });
  it.each(["sceneName", "sourceRevision", "frameIndex", "pixelWidth", "capturedAt"] as const)(
    "rejects metadata corruption in %s", async field => {
      const sealed = await sealPreviewRecovery(capture());
      const changed = { ...sealed, [field]: field === "sceneName" ? "Changed scene" : Number(sealed[field]) + 1 };
      await expect(verifyPreviewRecovery(changed, scope, 20)).rejects.toThrow("SHA-256 check");
    },
  );
  it("rejects authority fields instead of serializing a token or authorized path", async () => {
    const sealed = await sealPreviewRecovery(capture());
    await expect(verifyPreviewRecovery({ ...sealed, token: "old-native-grant" }, scope, 7)).rejects.toThrow("incomplete or corrupt");
    await expect(verifyPreviewRecovery({ ...sealed, path: "old-authorized-path" }, scope, 7)).rejects.toThrow("incomplete or corrupt");
  });
  it("bounds raw bytes, dimensions, frame count and numeric entry", () => {
    const base = { bytes: png, frameIndex: 0, frameCount: 1, pixelWidth: 1, pixelHeight: 1 };
    for (const bad of [{ ...base, frameIndex: -1 }, { ...base, frameCount: 72_001 },
      { ...base, pixelWidth: 0 }, { ...base, pixelHeight: Infinity }, { ...base, frameIndex: 1e99 }]) {
      expect(() => captureDecodedPreview(scope, 7, "Saved scene", bad, owner)).toThrow();
    }
    const large = new Uint8Array(MAX_RECOVERY_FRAME_BYTES + 1); large.set(png);
    expect(() => captureDecodedPreview(scope, 7, "Saved scene", { ...base, bytes: large }, owner)).toThrow("recovery bounds");
  });
});
