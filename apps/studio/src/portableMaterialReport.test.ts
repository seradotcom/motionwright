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
    expect(report.external.filter(issue => issue.kind === "external_font")).toEqual([]);
    expect(report.external.filter(issue => issue.kind === "external_runtime").map(issue => issue.label))
      .toEqual([...new Set(project.scenes.map(scene => scene.renderer))].sort());
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
    const externalFonts = report.external.filter(issue => issue.kind === "external_font");
    expect(externalFonts.map(issue => issue.label)).toEqual(["Licensed system typeface"]);
    expect(report.blocking.map(issue => issue.kind)).toEqual(["font_copy_permission"]);
    expect(report.blocking[0].label).toBe("Restricted original");
    expect(externalFonts[0].reason).toMatch(/not a portable original/);
  });

  it("requires the exact original allowed font-copy declaration even when use rights are cleared", () => {
    const project = owner();
    project.assets = [{
      id: one, name: "Exact font original", media_type: "font/woff2",
      content_sha256: "d".repeat(64), source_revision: "owner-v1",
    }];
    project.visual_language.fonts = [{
      family: "Exact brand font", source: "asset:" + one, rights_status: "cleared",
    }];
    project.production_design ??= { plan: null, heroes: [], capsules: [] };
    const noCopy = inspectPortableMaterials(project);
    expect(noCopy.blocking.map(issue => issue.label)).toEqual(["Exact brand font"]);
    expect(noCopy.blocking[0].reason).toMatch(/missing or invalid/);

    project.production_design.font_portable_copies = [{
      version: 1, asset_id: one, content_sha256: "d".repeat(64),
      permission: "allowed", origin: "Author's original font",
      license: "Original license permits local project archives",
    }];
    expect(inspectPortableMaterials(project).blocking).toEqual([]);

    project.production_design.font_portable_copies[0].content_sha256 = "e".repeat(64);
    expect(inspectPortableMaterials(project).blocking.map(issue => issue.kind))
      .toEqual(["font_copy_permission"]);
    project.production_design.font_portable_copies[0].content_sha256 = "d".repeat(64);
    project.production_design.font_portable_copies[0].permission = "prohibited";
    expect(inspectPortableMaterials(project).blocking[0].reason).toMatch(/explicitly prohibited/);
  });

  it("refuses one restricted alias even when another cleared alias has a matching copy declaration", () => {
    const project = owner();
    project.assets = [
      { id: one, name: "Original file", media_type: "font/ttf",
        content_sha256: "d".repeat(64), source_revision: null },
      { id: two, name: "Same source alias", media_type: "application/octet-stream",
        content_sha256: "d".repeat(64), source_revision: null },
    ];
    project.visual_language.fonts = [
      { family: "Permitted face", source: "asset:" + one, rights_status: "cleared" },
      { family: "Restricted alias", source: "asset:" + two, rights_status: "restricted" },
    ];
    project.production_design ??= { plan: null, heroes: [], capsules: [] };
    project.production_design.font_portable_copies = [{
      version: 1, asset_id: one, content_sha256: "d".repeat(64),
      permission: "allowed", origin: "Original author", license: "Approved for copy",
    }];
    expect(inspectPortableMaterials(project).blocking.map(issue => issue.label))
      .toEqual(["Restricted alias"]);
  });

  it("checks every current copy declaration instead of letting one valid alias hide an uncleared source", () => {
    const project = owner();
    project.assets = [
      { id: one, name: "Cleared original", media_type: "font/ttf",
        content_sha256: "d".repeat(64), source_revision: null },
      { id: two, name: "Second attached original", media_type: "application/octet-stream",
        content_sha256: "d".repeat(64), source_revision: null },
    ];
    project.visual_language.fonts = [{
      family: "Cleared face", source: "asset:" + one, rights_status: "cleared",
    }];
    project.production_design ??= { plan: null, heroes: [], capsules: [] };
    project.production_design.font_portable_copies = [one, two].map(asset_id => ({
      version: 1, asset_id, content_sha256: "d".repeat(64), permission: "allowed",
      origin: "Original author", license: "Permitted local copy",
    }));
    expect(inspectPortableMaterials(project).blocking.map(issue => issue.kind))
      .toEqual(["font_copy_permission"]);
    project.visual_language.fonts.push({
      family: "Second cleared face", source: "asset:" + two, rights_status: "cleared",
    });
    expect(inspectPortableMaterials(project).blocking).toEqual([]);
  });

  it("reports installed renderers, native editors, extensions and handoffs without claiming to bundle them", () => {
    const project = owner();
    project.assets = [{
      id: one, name: "Original native scene", media_type: "application/octet-stream",
      content_sha256: "f".repeat(64), source_revision: null,
    }];
    project.visual_language.fonts = [];
    project.production_design ??= { plan: null, heroes: [], capsules: [] };
    project.production_design.capsules = [{
      id: two, scene_id: project.scenes[0].id, source_asset_id: one,
      source_sha256: "f".repeat(64), label: "Owned animation",
      native_editor_hint: "Blender 5", editable_parameters: [],
      fidelity: {
        renderer: "blender", renderer_version: "5", visual: "native",
        temporal: "native", structural: "native", editable: "native",
        losses: [], evidence_sha256: null,
      },
    }];
    project.extensions = [{
      id: one, name: "External permitted plugin", kind: "catalog-package",
      package_version: "2.1.0", digest_sha256: "c".repeat(64),
      license: "owner permission", source: "catalog:local", rights_status: "cleared",
      enabled: true, permissions: ["read_project"],
    }];
    project.handoffs = [{
      id: two, system: "launchwright", direction: "context_output",
      external_kind: "context", external_id: "source-stub", external_revision: null,
      local_resource: "project:" + project.id, artifact_sha256: null,
    }];
    const report = inspectPortableMaterials(project);
    expect(report.blocking).toEqual([]);
    expect(report.external.filter(issue => issue.kind === "external_runtime").map(issue => issue.label))
      .toEqual([...new Set(project.scenes.map(scene => scene.renderer))].sort());
    expect(report.external.filter(issue => issue.kind === "external_editor").map(issue => issue.label))
      .toEqual(["Blender 5"]);
    expect(report.external.filter(issue => issue.kind === "external_extension").map(issue => issue.label))
      .toEqual(["External permitted plugin"]);
    expect(report.external.filter(issue => issue.kind === "external_integration").map(issue => issue.label))
      .toEqual(["launchwright"]);
    expect(report.external.every(issue => !issue.blocks_export)).toBe(true);
    expect(report.expected_unique_blob_count).toBe(1);
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
