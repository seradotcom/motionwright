import type { FontPortableCopyDeclaration } from "./creativeProduction";
import type { Asset, Change, Project } from "./types";
import { projectionUuid } from "./projectMutation";

export interface FontCopyRaw {
  kind: "font-copy"; assetId: string; contentSha256: string;
  permission: "" | "allowed" | "prohibited"; origin: string; license: string; note: string;
  confirmed: boolean;
}
export type FontCopyChange = Extract<Change, {
  type: "upsert_font_portable_copy" | "remove_font_portable_copy"
}>;
const digest = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const text = (value: unknown, limit: number, required: boolean): value is string =>
  typeof value === "string" && value.trim() === value && (!required || value.length > 0)
  && !/[\u0000-\u001f\u007f-\u009f]/.test(value) && new TextEncoder().encode(value).length <= limit;
export function fontCopyDeclarationShape(value: unknown): value is FontPortableCopyDeclaration {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>, keys = ["version", "asset_id", "content_sha256", "permission", "origin", "license"];
  return keys.every(key => Object.hasOwn(row, key)) && Object.keys(row).every(key => keys.includes(key) || key === "note")
    && row.version === 1 && projectionUuid(row.asset_id) && digest(row.content_sha256)
    && (row.permission === "allowed" || row.permission === "prohibited")
    && text(row.origin, 2048, row.permission === "allowed") && text(row.license, 2048, row.permission === "allowed")
    && (row.note === undefined || row.note === null || text(row.note, 4096, true));
}
export function fontCopyRawShape(value: unknown): value is FontCopyRaw {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  const keys = ["kind", "assetId", "contentSha256", "permission", "origin", "license", "note", "confirmed"];
  return Object.keys(row).length === keys.length && keys.every(key => Object.hasOwn(row, key))
    && row.kind === "font-copy" && ["assetId", "contentSha256", "origin", "license", "note"].every(key => typeof row[key] === "string")
    && typeof row.permission === "string" && ["", "allowed", "prohibited"].includes(row.permission) && typeof row.confirmed === "boolean";
}
export function fontCopyCandidates(project: Project): Asset[] {
  return project.assets.filter(asset => /^font\//i.test(asset.media_type)
    || /^application\/(?:font-|x-font-|vnd\.ms-(?:opentype|fontobject))/i.test(asset.media_type)
    || project.visual_language.fonts.some(right => right.source === "asset:" + asset.id)
    || project.production_design?.font_portable_copies?.some(copy => copy.asset_id === asset.id));
}
export function savedFontCopyRaw(project: Project, assetId = ""): FontCopyRaw {
  const asset = project.assets.find(value => value.id === assetId);
  const saved = project.production_design?.font_portable_copies?.find(value =>
    value.asset_id === assetId && value.content_sha256 === asset?.content_sha256);
  return { kind: "font-copy", assetId, contentSha256: asset?.content_sha256 ?? "",
    permission: saved?.permission ?? "", origin: saved?.origin ?? "", license: saved?.license ?? "",
    note: saved?.note ?? "", confirmed: false };
}
/** Authoring admission only. Storage still inspects and hashes the original font. */
export function prepareFontCopyChange(project: Project, raw: FontCopyRaw, removeAssetId?: string): FontCopyChange {
  if (project.locks.some(lock => lock.resource === "project:" + project.id && ["content", "style"].includes(lock.kind)))
    throw new Error("The project is content/style locked. The font declaration was not sent.");
  const saved = project.production_design?.font_portable_copies ?? [];
  if (removeAssetId !== undefined) {
    if (!projectionUuid(removeAssetId) || saved.filter(value => value.asset_id === removeAssetId).length !== 1)
      throw new Error("The selected saved font declaration is no longer present.");
    return { type: "remove_font_portable_copy", asset_id: removeAssetId };
  }
  const source = fontCopyCandidates(project).filter(asset => asset.id === raw.assetId);
  if (source.length !== 1 || !digest(raw.contentSha256) || source[0].content_sha256 !== raw.contentSha256)
    throw new Error("Choose the current original font and its exact saved SHA-256.");
  if (!raw.confirmed) throw new Error("Confirm your original font-copy declaration before saving.");
  const declaration = { version: 1, asset_id: raw.assetId, content_sha256: raw.contentSha256,
    permission: raw.permission, origin: raw.origin.trim(), license: raw.license.trim(),
    ...(raw.note.trim() ? { note: raw.note.trim() } : {}) };
  if (!fontCopyDeclarationShape(declaration))
    throw new Error("Choose permission and provide bounded origin/license text for an allowed copy.");
  if (saved.length >= 64 && !saved.some(value => value.asset_id === raw.assetId))
    throw new Error("The project already has 64 font-copy declarations.");
  if (declaration.permission === "allowed") {
    const rights = project.visual_language.fonts.filter(right => right.source === "asset:" + raw.assetId);
    if (!rights.length || rights.some(right => right.rights_status !== "cleared"))
      throw new Error("Record cleared use rights for this original asset in Visual language before allowing its portable copy.");
  }
  return { type: "upsert_font_portable_copy", declaration };
}
export function changedFontCopies(project: Project, change: FontCopyChange): FontPortableCopyDeclaration[] {
  const copies = structuredClone(project.production_design?.font_portable_copies ?? []);
  if (change.type === "remove_font_portable_copy") return copies.filter(value => value.asset_id !== change.asset_id);
  const index = copies.findIndex(value => value.asset_id === change.declaration.asset_id);
  if (index < 0) copies.push(structuredClone(change.declaration));
  else copies[index] = structuredClone(change.declaration);
  return copies;
}
