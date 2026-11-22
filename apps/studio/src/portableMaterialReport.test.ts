import { describe, expect, it } from "vitest";
import { fixtureBootstrap } from "./fixture";
import type { Project } from "./types";
import { assertPortableExportReceipt, inspectPortableMaterials } from "./portableMaterialReport";

const owner = (): Project => structuredClone(fixtureBootstrap.project);
const one = "018f0000-0000-7000-8000-000000000501";
const two = "018f0000-0000-7000-8000-000000000502";

describe("MW05-E11-04 original portable material coverage", () => {
  it("counts unique original CAS blobs, not asset aliases, and retains saved lock/data counts", () => {
    const project = owner();
    project.assets = [
      { id: one, name: "Original video", media_type: "video/mp4", content_sha256: "a".repeat(64), source_revision: "v1" },
      { id: two, name: "Same original as alternate label", media_type: "video/mp4", content_sha256: "a".repeat(64), source_revision: null },
    ];
    project.visual_language.fonts = [];
    const report = inspectPortableMaterials(project);
    expect(report.expected_unique_blob_count).toBe(1);
    expect(report.asset_count).toBe(2);
    expect(report.saved_lock_count).toBe(project.locks.length);
    expect(report.saved_measured_table_count)
      .toBe(project.production_design?.measured_tables?.length ?? 0);
    expect(report.blocking).toEqual([]);
    expect(report.external).toEqual([]);
    expect(() => assertPortableExportReceipt(report, {
      destination: "E:\\original-bundle", blob_count: 1, total_blob_bytes: "1024",
    })).not.toThrow();
  });

  it("refuses export readiness when any source asset has no digest, preserving labels without inventing bytes", () => {
    const project = owner();
    project.assets = [
      { id: one, name: "Descriptive external file", media_type: "video/mp4", content_sha256: null, source_revision: null },
      { id: two, name: "Recorded original", media_type: "video/mp4", content_sha256: "a".repeat(64), source_revision: null },
    ];
    project.visual_language.fonts = [];
    const report = inspectPortableMaterials(project);
    expect(report.blocking).toEqual([{
      id: one, kind: "unbound_asset", label: "Descriptive external file", blocks_export: true,
      reason: expect.stringMatching(/refuses a portable bundle/),
    }]);
    expect(report.expected_unique_blob_count).toBe(1);
    expect(() => assertPortableExportReceipt(report, {
      destination: "E:\\original-bundle", blob_count: 1, total_blob_bytes: "1024",
    })).toThrow(/Inspect the original destination/);
  });

  it("distinguishes external typefaces from digest-bound source fonts requiring rights", () => {
    const project = owner();
    project.assets = [
      { id: one, name: "Original font", media_type: "font/ttf", content_sha256: "b".repeat(64), source_revision: null },
    ];
    project.visual_language.fonts = [
      { family: "Bundled original", source: "asset:" + one, rights_status: "cleared" },
      { family: "Licensed system typeface", source: "installed:system", rights_status: "cleared" },
      { family: "Restricted original", source: "asset:" + one, rights_status: "restricted" },
    ];
    const report = inspectPortableMaterials(project);
    expect(report.expected_unique_blob_count).toBe(1);
    expect(report.external.map(issue => issue.label)).toEqual(["Licensed system typeface"]);
    expect(report.blocking.map(issue => issue.kind)).toEqual(["font_copy_permission"]);
    expect(report.blocking[0].label).toBe("Restricted original");
    expect(report.external[0].reason).toMatch(/not a portable original/);
  });

  it("never accepts a misleading receipt or an invalid digest as verified content", () => {
    const project = owner();
    project.assets = [{
      id: one, name: "Available original", media_type: "image/png",
      content_sha256: "c".repeat(64), source_revision: null,
    }];
    project.visual_language.fonts = [];
    const report = inspectPortableMaterials(project);
    for (const receipt of [
      { destination: "E:\\original-bundle", blob_count: 0, total_blob_bytes: "100" },
      { destination: "E:\\original-bundle", blob_count: 2, total_blob_bytes: "100" },
      { destination: "E:\\original-bundle", blob_count: 1, total_blob_bytes: "-1" },
      { destination: "", blob_count: 1, total_blob_bytes: "100" },
    ]) {
      expect(() => assertPortableExportReceipt(report, receipt)).toThrow(/did not match/);
    }
    project.assets[0].content_sha256 = "NON_SHA";
    expect(inspectPortableMaterials(project).blocking.map(issue => issue.id)).toEqual([one]);
  });
});
