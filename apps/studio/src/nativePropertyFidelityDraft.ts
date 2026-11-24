import type { Fidelity, NativeCapsule, NativePropertyFidelity } from "./creativeProduction";
import { sameValue, validateNativePropertyFidelity } from "./creativeProduction";
import type { Project } from "./types";

/** Local author input, bound to one preserved source. Never renderer evidence or execution authority. */
export interface ProductionFidelityInput {
  kind: "fidelity"; sourceAssetId: string; sourceSha256: string; entries: NativePropertyFidelity[];
  path: string; fidelity: Fidelity; note: string; editingPath: string | null;
}
export const nativePropertyStatuses: readonly Fidelity[] = ["native", "translated", "baked", "approximate", "unavailable"];
const clone = <T,>(value: T): T => structuredClone(value);
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: readonly string[], optional: readonly string[] = []) =>
  keys.every(key => Object.hasOwn(value, key)) && Object.keys(value).every(key => keys.includes(key) || optional.includes(key));
const bytes = (value: string) => new TextEncoder().encode(value).length;
const digest = (value: unknown) => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const status = (value: unknown): value is Fidelity => nativePropertyStatuses.includes(value as Fidelity);
const text = (value: unknown, maximum: number) => typeof value === "string" && !!value.trim()
  && bytes(value) <= maximum && !/[\u0000-\u0009\u000b-\u001f\u007f]/u.test(value);

export function nativeFidelityInput(capsule: NativeCapsule | null): ProductionFidelityInput {
  return { kind: "fidelity", sourceAssetId: capsule?.source_asset_id ?? "", sourceSha256: capsule?.source_sha256 ?? "",
    entries: clone(capsule?.property_fidelity ?? []), path: "", fidelity: "unavailable", note: "", editingPath: null };
}
export const nativePropertyEditorHasInput = (input: ProductionFidelityInput) =>
  input.path !== "" || input.note !== "" || input.fidelity !== "unavailable" || input.editingPath !== null;
export function clearNativePropertyEditor(input: ProductionFidelityInput): ProductionFidelityInput {
  return { ...input, path: "", fidelity: "unavailable", note: "", editingPath: null };
}
export function editNativePropertyReview(input: ProductionFidelityInput, path: string): ProductionFidelityInput {
  if (nativePropertyEditorHasInput(input)) throw new Error("Add the current property to the review or clear its fields before editing another property.");
  const entry = input.entries.find(item => item.path === path);
  if (!entry) throw new Error("This property is no longer in the review.");
  return { ...clone(input), editingPath: path, path, fidelity: entry.fidelity, note: entry.loss_note ?? "" };
}
export function stageNativePropertyReview(input: ProductionFidelityInput): ProductionFidelityInput {
  const entry: NativePropertyFidelity = { path: input.path.trim(), fidelity: input.fidelity, loss_note: input.note.trim() || null };
  validateNativePropertyFidelity([entry]);
  if (input.editingPath !== null && !input.entries.some(item => item.path === input.editingPath))
    throw new Error("The property being edited is no longer in this review.");
  if (input.entries.some(item => item.path === entry.path && item.path !== input.editingPath))
    throw new Error("This property already has a classification. Edit its review row explicitly.");
  const entries = input.editingPath === null ? [...clone(input.entries), entry]
    : input.entries.map(item => item.path === input.editingPath ? entry : clone(item));
  validateNativePropertyFidelity(entries);
  return clearNativePropertyEditor({ ...input, entries });
}
export function removeNativePropertyReview(input: ProductionFidelityInput, path: string): ProductionFidelityInput {
  if (!input.entries.some(item => item.path === path)) throw new Error("This property is no longer in the review.");
  if (input.editingPath === path && nativePropertyEditorHasInput(input))
    throw new Error("Add the current edit to the review or clear its fields before removing that property.");
  return { ...clone(input), entries: input.entries.filter(item => item.path !== path).map(clone) };
}
export function assertNativeFidelitySource(project: Project, capsule: NativeCapsule | null, input: ProductionFidelityInput): asserts capsule is NativeCapsule {
  if (!capsule) throw new Error("The selected native capsule is unavailable. The earlier review remains recoverable.");
  if (capsule.source_asset_id !== input.sourceAssetId || capsule.source_sha256 !== input.sourceSha256)
    throw new Error("The preserved source identity changed. Keep the earlier review copy and use the saved source values before starting another review.");
  const assets = project.assets.filter(asset => asset.id === input.sourceAssetId);
  if (assets.length !== 1 || !digest(input.sourceSha256) || assets[0].content_sha256 !== input.sourceSha256)
    throw new Error("The imported asset is unavailable or its SHA-256 no longer matches this capsule. The review cannot be saved.");
  if (project.scenes.filter(scene => scene.id === capsule.scene_id).length !== 1)
    throw new Error("The selected native source scene is unavailable or ambiguous.");
}
/** Preserve all current capsule metadata; the review can only replace its property classifications. */
export function nativeFidelityValue(capsule: NativeCapsule, input: ProductionFidelityInput): NativeCapsule {
  validateNativePropertyFidelity(input.entries);
  const value = clone(capsule);
  if (input.entries.length) value.property_fidelity = clone(input.entries);
  else delete value.property_fidelity;
  return value;
}
export function proposeNativeFidelity(project: Project, capsule: NativeCapsule | null, input: ProductionFidelityInput): NativeCapsule {
  assertNativeFidelitySource(project, capsule, input);
  if (nativePropertyEditorHasInput(input)) throw new Error("Add the current property to the review or clear its fields before saving the complete review.");
  if (sameValue(capsule.property_fidelity ?? [], input.entries)) throw new Error("The property review has no changes to save.");
  return nativeFidelityValue(capsule, input);
}
function propertiesShape(value: unknown): value is NativePropertyFidelity[] {
  if (!Array.isArray(value) || !value.every(entry => record(entry) && exact(entry, ["path", "fidelity", "loss_note"])
    && typeof entry.path === "string" && status(entry.fidelity) && (entry.loss_note === null || typeof entry.loss_note === "string"))) return false;
  try { validateNativePropertyFidelity(value as NativePropertyFidelity[]); return true; } catch { return false; }
}
export function nativeFidelityInputShape(value: unknown): value is ProductionFidelityInput {
  return record(value) && exact(value, ["kind", "sourceAssetId", "sourceSha256", "entries", "path", "fidelity", "note", "editingPath"])
    && value.kind === "fidelity" && typeof value.sourceAssetId === "string" && bytes(value.sourceAssetId) <= 128
    && (value.sourceSha256 === "" || digest(value.sourceSha256)) && propertiesShape(value.entries)
    && typeof value.path === "string" && bytes(value.path) <= 512 && status(value.fidelity)
    && typeof value.note === "string" && bytes(value.note) <= 2000
    && (value.editingPath === null || typeof value.editingPath === "string" && value.entries.some(entry => entry.path === value.editingPath));
}
/** Closed comparison-only snapshot. Restoring this shape never imports a capsule or grants. */
export function nativeFidelityCapsuleShape(value: unknown): value is NativeCapsule {
  if (!record(value) || !exact(value, ["id", "scene_id", "source_asset_id", "source_sha256", "label", "fidelity", "editable_parameters", "native_editor_hint"], ["property_fidelity"])
    || typeof value.id !== "string" || !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value.id)
    || !text(value.scene_id, 128) || !text(value.source_asset_id, 128) || !digest(value.source_sha256)
    || !text(value.label, 256) || !text(value.native_editor_hint, 256)
    || !Array.isArray(value.editable_parameters) || value.editable_parameters.length > 64 || !value.editable_parameters.every(item => text(item, 2000))
    || value.property_fidelity !== undefined && !propertiesShape(value.property_fidelity)) return false;
  const fidelity = value.fidelity;
  return record(fidelity) && exact(fidelity, ["renderer", "renderer_version", "visual", "temporal", "structural", "editable", "losses", "evidence_sha256"])
    && text(fidelity.renderer, 128) && text(fidelity.renderer_version, 128)
    && [fidelity.visual, fidelity.temporal, fidelity.structural, fidelity.editable].every(status)
    && Array.isArray(fidelity.losses) && fidelity.losses.length <= 128 && fidelity.losses.every(item => text(item, 2000))
    && ([fidelity.visual, fidelity.temporal, fidelity.structural, fidelity.editable].every(item => item === "native" || item === "translated") || fidelity.losses.length > 0)
    && (fidelity.evidence_sha256 === null || digest(fidelity.evidence_sha256));
}
