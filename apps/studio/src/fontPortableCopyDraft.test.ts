import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import type { Project } from "./types";
import {
  acknowledgeBrandDraft, brandUnknown, createBrandDraft, decodeBrandCopy, editBrandDraft,
  encodeBrandCopy, prepareBrandSubmission, reconcileBrandDraft,
} from "./brandAuthoringDraft";
import {
  changedFontCopies, fontCopyDeclarationShape, prepareFontCopyChange, savedFontCopyRaw,
} from "./fontPortableCopyDraft";

const assetId = "10000000-0000-4000-8000-000000000001";
const attempt = "10000000-0000-4000-8000-000000000002";
const writer = "10000000-0000-4000-8000-000000000003";
function project(): Project {
  const value = structuredClone(fixtureProject);
  value.schema_version = 2; value.locks = []; value.reviews = [];
  value.production_design = emptyProductionDesign();
  value.assets = [{ id: assetId, name: "Original font", media_type: "font/ttf",
    content_sha256: "ab".repeat(32), source_revision: "original-test-source" }];
  value.visual_language.fonts = [{ family: "Original face", source: "asset:" + assetId, rights_status: "cleared" }];
  return value;
}
function raw(value: Project) {
  return { ...savedFontCopyRaw(value, assetId), permission: "allowed" as const,
    origin: "Original author source", license: "Owner supplied license", note: "", confirmed: true };
}
async function pending(value: Project) {
  const draft = editBrandDraft(createBrandDraft(value, "font-copy"), raw(value));
  return prepareBrandSubmission(draft, value, attempt, "font-copy");
}
// Pure document/recovery checks; the separate original Service case verifies persistence.
function receipt(before: Project, change: Parameters<typeof changedFontCopies>[1]): Project {
  const after = structuredClone(before);
  after.revision++; after.schema_version = 2; after.updated_at = "2026-11-22T06:00:00Z";
  after.branches = after.branches.map(branch => branch.id === after.active_branch ? { ...branch, head_revision: after.revision } : branch);
  after.production_design!.font_portable_copies = changedFontCopies(before, change);
  if (!after.production_design!.font_portable_copies.length) delete after.production_design!.font_portable_copies;
  return after;
}
describe("original font copy declaration authoring", () => {
  it("binds an explicit owner declaration to the current original bytes", () => {
    const value = project();
    expect(prepareFontCopyChange(value, raw(value))).toEqual({
      type: "upsert_font_portable_copy", declaration: {
        version: 1, asset_id: assetId, content_sha256: "ab".repeat(32), permission: "allowed",
        origin: "Original author source", license: "Owner supplied license",
      },
    });
    expect(() => prepareFontCopyChange(value, { ...raw(value), confirmed: false })).toThrow(/Confirm/);
    expect(() => prepareFontCopyChange(value, { ...raw(value), contentSha256: "cd".repeat(32) })).toThrow(/exact saved/);
  });
  it("cannot carry an allowed declaration onto replacement bytes", () => {
    const value = project(), change = prepareFontCopyChange(value, raw(value));
    value.production_design!.font_portable_copies = changedFontCopies(value, change);
    value.assets[0].content_sha256 = "cd".repeat(32);
    expect(savedFontCopyRaw(value, assetId)).toMatchObject({ permission: "", origin: "", license: "", confirmed: false });
    expect(value.production_design!.font_portable_copies[0].content_sha256).toBe("ab".repeat(32));
  });
  it("requires cleared current use rights for an allowed copy but can record a prohibition", () => {
    const value = project();
    value.visual_language.fonts[0].rights_status = "unknown";
    expect(() => prepareFontCopyChange(value, raw(value))).toThrow(/cleared use rights/);
    const forbidden = prepareFontCopyChange(value, { ...raw(value), permission: "prohibited", origin: "", license: "" });
    expect(forbidden.type).toBe("upsert_font_portable_copy");
    value.visual_language.fonts[0].rights_status = "cleared";
    value.visual_language.fonts.push({ family: "Conflicting right", source: "asset:" + assetId, rights_status: "restricted" });
    expect(() => prepareFontCopyChange(value, raw(value))).toThrow(/cleared use rights/);
  });
  it.each(["content", "style"] as const)("honors the existing %s lock before a save or removal", kind => {
    const value = project(), change = prepareFontCopyChange(value, raw(value));
    value.production_design!.font_portable_copies = changedFontCopies(value, change);
    value.locks.push({ id: writer, resource: "project:" + value.id, kind, note: "Owner lock" });
    expect(() => prepareFontCopyChange(value, raw(value))).toThrow(/locked/);
    expect(() => prepareFontCopyChange(value, raw(value), assetId)).toThrow(/locked/);
  });
  it("enforces closed declarations, original byte limits and control-character bounds", () => {
    const value = project();
    expect(() => prepareFontCopyChange(value, { ...raw(value), license: "é".repeat(1025) })).toThrow(/bounded/);
    expect(() => prepareFontCopyChange(value, { ...raw(value), origin: "source\u0000text" })).toThrow(/bounded/);
    expect(() => prepareFontCopyChange(value, { ...raw(value), license: "" })).toThrow(/bounded/);
    const change = prepareFontCopyChange(value, raw(value));
    if (change.type !== "upsert_font_portable_copy") throw new Error("Expected copy declaration");
    expect(fontCopyDeclarationShape({ ...change.declaration, human_approved: true })).toBe(false);
    expect(fontCopyDeclarationShape({ ...change.declaration, permission: "approved_by_agent" })).toBe(false);
  });
  it("retains UNKNOWN across a recovery roundtrip and never resubmits from a later read", async () => {
    const value = project(), prepared = await pending(value);
    const recovered = decodeBrandCopy(encodeBrandCopy(prepared.draft, writer), prepared.draft.scope, writer).draft;
    expect(brandUnknown(recovered)).toBe(true);
    const later = receipt(value, prepared.change as Parameters<typeof changedFontCopies>[1]);
    expect(brandUnknown(reconcileBrandDraft(recovered, later))).toBe(true);
    await expect(prepareBrandSubmission(recovered, later, writer, "font-copy")).rejects.toThrow(/unconfirmed/);
  });
  it("acknowledges only the original exact save and resets explicit confirmation", async () => {
    const value = project(), prepared = await pending(value);
    const after = receipt(value, prepared.change as Parameters<typeof changedFontCopies>[1]);
    const accepted = acknowledgeBrandDraft(prepared.draft, attempt, after, value, prepared.draft.pending!);
    expect(accepted.pending).toBeNull();
    expect(accepted.raw).toMatchObject({ kind: "font-copy", confirmed: false });
    const forged = structuredClone(after);
    forged.assets[0].content_sha256 = "cd".repeat(32);
    expect(() => acknowledgeBrandDraft(prepared.draft, attempt, forged, value, prepared.draft.pending!)).toThrow(/unrelated/);
    const omitted = structuredClone(after);
    delete omitted.production_design!.font_portable_copies;
    expect(() => acknowledgeBrandDraft(prepared.draft, attempt, omitted, value, prepared.draft.pending!)).toThrow(/font response/);
  });
  it("removes a historical prohibition explicitly without changing its detached source or other design", async () => {
    const value = project(), change = prepareFontCopyChange(value, { ...raw(value), permission: "prohibited" });
    value.production_design!.font_portable_copies = changedFontCopies(value, change);
    value.assets = []; value.visual_language.fonts = [];
    const draft = createBrandDraft(value, "font-copy");
    const prepared = await prepareBrandSubmission(draft, value, attempt, { removeFont: assetId });
    expect(prepared.change).toEqual({ type: "remove_font_portable_copy", asset_id: assetId });
    const recovered = decodeBrandCopy(encodeBrandCopy(prepared.draft, writer), draft.scope, writer).draft;
    expect(brandUnknown(recovered)).toBe(true);
    const after = receipt(value, prepared.change as Parameters<typeof changedFontCopies>[1]);
    expect(acknowledgeBrandDraft(prepared.draft, attempt, after, value, prepared.draft.pending!).pending).toBeNull();
    expect(after.assets).toEqual([]);
    expect(() => prepareFontCopyChange(after, savedFontCopyRaw(after), assetId)).toThrow(/no longer present/);
  });
});
