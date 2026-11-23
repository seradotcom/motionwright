/** Contract tests only: mocked IPC grants are not native permissions or physical acceptance. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import { exportHyperframesSource, readHyperframesSource, type HyperframesSourceSnapshot } from "./hyperframesSourceApi";
const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));
const grant = "cccccccc-3333-4333-8333-cccccccccccc";
const path = "E:\\NativeSources\\index.html";
const project = () => structuredClone(fixtureProject);
function snapshot(p = project()): HyperframesSourceSnapshot {
  const profile = p.deliverables[0];
  return { schema: "motionwright.hyperframes-source/1", project: structuredClone(p), deliverable_id: profile.id,
    fingerprint: "c".repeat(64), source_sha256: "a".repeat(64), source_bytes: "300",
    html_sha256: "b".repeat(64), html_bytes: "600", compiler: "semwright-hyperframes-rect-clips-v3",
    hyperframes_version: "0.8.181", width: profile.width, height: profile.height, frame_count: 60,
    project_modified: false, editor_launched: false, native_execution_started: false };
}
const binding = (p = project()) => ({ project_id: p.id, generation: p.generation, revision: p.revision,
  active_branch: p.active_branch, deliverable_id: p.deliverables[0].id });
const receipt = (p = project()) => ({ schema: "motionwright.hyperframes-source-export/1", source: binding(p),
  fingerprint: "c".repeat(64), format: "html", path, content_sha256: "b".repeat(64), size_bytes: "600",
  project_modified: false, editor_launched: false, native_execution_started: false });
beforeEach(() => { invokeMock.mockReset(); vi.stubGlobal("window", { __TAURI_INTERNALS__: {} }); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe("saved HyperFrames source authority", () => {
  it("inspects only the exact saved project, branch and profile without a grant", async () => {
    const p = project(); invokeMock.mockResolvedValue(snapshot(p));
    await expect(readHyperframesSource(p, p.deliverables[0].id)).resolves.toEqual(snapshot(p));
    expect(invokeMock.mock.calls).toEqual([["native_hyperframes_source", { request: binding(p) }]]);
  });
  for (const change of ["revision", "same_revision_content", "profile", "digest", "unknown_field", "native_effect", "dimensions"] as const) {
    it("rejects a mismatched source: " + change, async () => {
      const p = project(), s = snapshot(p);
      const value = structuredClone(s) as unknown as Record<string, unknown>;
      if (change === "revision") (value.project as typeof p).revision++;
      if (change === "same_revision_content") (value.project as typeof p).title = "Foreign saved content";
      if (change === "profile") value.deliverable_id = grant;
      if (change === "digest") value.html_sha256 = "B".repeat(64);
      if (change === "unknown_field") value.install_runtime = true;
      if (change === "native_effect") value.native_execution_started = true;
      if (change === "dimensions") value.width = 2000;
      invokeMock.mockResolvedValue(value);
      await expect(readHyperframesSource(p, p.deliverables[0].id)).rejects.toThrow();
      expect(invokeMock).toHaveBeenCalledTimes(1);
    });
  }
  it("binds one delivery grant to format, source fingerprint and exact destination", async () => {
    const p = project(), s = snapshot(p), check = vi.fn();
    invokeMock.mockResolvedValueOnce({ token: grant }).mockResolvedValueOnce(receipt(p));
    await expect(exportHyperframesSource(p, s, "html", " " + path + " ", check)).resolves.toEqual(receipt(p));
    expect(check).toHaveBeenCalledTimes(2);
    expect(invokeMock.mock.calls).toEqual([
      ["issue_effect_grant", { request: { effect: "deliver_local", project_id: p.id,
        generation: p.generation, revision: p.revision,
        subject: ["hyperframes-source", p.active_branch, s.deliverable_id, s.fingerprint, "html", path].join("|") } }],
      ["export_hyperframes_source_file", { request: { source: binding(p), expected_fingerprint: s.fingerprint,
        format: "html", path, effect_grant: grant } }],
    ]);
  });
  it("does not export after selection changes while permission is outstanding", async () => {
    const p = project(); let active = true;
    invokeMock.mockImplementationOnce(async () => { active = false; return { token: grant }; });
    await expect(exportHyperframesSource(p, snapshot(p), "html", path, () => {
      if (!active) throw new Error("Original selection retired");
    })).rejects.toThrow("retired");
    expect(invokeMock).toHaveBeenCalledTimes(1);
  });
  it("rejects a stale source before requesting permission", async () => {
    const p = project(), s = snapshot(p); p.revision++;
    await expect(exportHyperframesSource(p, s, "html", path, () => {})).rejects.toThrow();
    expect(invokeMock).not.toHaveBeenCalled();
  });
  it("does not request permission for an incorrect native filename or relative destination", async () => {
    const p = project();
    for (const wrong of ["index.html", "E:\\NativeSources\\foreign.html", "E:\\NativeSources\\index.html\nx"]) {
      await expect(exportHyperframesSource(p, snapshot(p), "html", wrong, () => {})).rejects.toThrow();
    }
    expect(invokeMock).not.toHaveBeenCalled();
  });
  it("retains an uncertain export as an error without retrying or reissuing permission", async () => {
    const p = project();
    invokeMock.mockResolvedValueOnce({ token: grant }).mockRejectedValueOnce(new Error("Transport ended before original acknowledgement"));
    await expect(exportHyperframesSource(p, snapshot(p), "html", path, () => {})).rejects.toThrow("acknowledgement");
    expect(invokeMock).toHaveBeenCalledTimes(2);
  });
  it("rejects a receipt for different bytes without another export", async () => {
    const p = project();
    invokeMock.mockResolvedValueOnce({ token: grant }).mockResolvedValueOnce({ ...receipt(p), content_sha256: "f".repeat(64) });
    await expect(exportHyperframesSource(p, snapshot(p), "html", path, () => {})).rejects.toThrow("acknowledgement differs");
    expect(invokeMock).toHaveBeenCalledTimes(2);
  });
  it("has no browser fallback that writes or grants an export", async () => {
    vi.stubGlobal("window", {}); const p = project();
    await expect(readHyperframesSource(p, p.deliverables[0].id)).rejects.toThrow("Desktop");
    await expect(exportHyperframesSource(p, snapshot(p), "html", path, () => {})).rejects.toThrow("Desktop");
    expect(invokeMock).not.toHaveBeenCalled();
  });
});
