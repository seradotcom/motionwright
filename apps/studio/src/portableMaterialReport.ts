import type { PortableBundleExport, Project } from "./types";

/**
 * Preflight for the existing SQLite/CAS portable project export.
 *
 * This is deliberately a saved-document inventory, not a file inspection:
 * only the Store can establish whether original CAS bytes and cleared font
 * copy permissions pass admission. Never turn a declared source into an
 * assertion that the original bytes were exported.
 */
export interface PortableMaterialIssue {
  id: string;
  kind: "unbound_asset" | "external_font" | "font_copy_permission" |
    "external_runtime" | "external_editor" | "external_extension" | "external_integration";
  label: string;
  reason: string;
  blocks_export: boolean;
}

export interface PortableMaterialReport {
  project_id: string;
  generation: string;
  revision: number;
  asset_count: number;
  expected_unique_blob_count: number;
  saved_lock_count: number;
  saved_measured_table_count: number;
  saved_native_capsule_count: number;
  blocking: PortableMaterialIssue[];
  external: PortableMaterialIssue[];
}

const sha256 = (value: unknown): value is string =>
  typeof value === "string" && /^[0-9a-f]{64}$/.test(value);

/** Sort by canonical identifier rather than user-provided display order. */
const order = (left: PortableMaterialIssue, right: PortableMaterialIssue) =>
  left.kind.localeCompare(right.kind) || left.id.localeCompare(right.id);

export function inspectPortableMaterials(project: Project): PortableMaterialReport {
  const issues: PortableMaterialIssue[] = [];
  const originalAssets = new Map(project.assets.map(asset => [asset.id, asset]));
  const digests = new Set<string>();

  for (const asset of project.assets) {
    if (!sha256(asset.content_sha256)) {
      issues.push({
        id: asset.id,
        kind: "unbound_asset",
        label: asset.name,
        reason: "No verified content-addressed original is attached. The Store refuses a portable bundle containing this asset.",
        blocks_export: true,
      });
    } else {
      digests.add(asset.content_sha256);
    }
  }

  const sourceRights = project.visual_language.fonts;
  const declarations = project.production_design?.font_portable_copies ?? [];
  const fontDigests = new Set<string>();

  // Storage classifies fonts from *all* aliases, recorded permissions and
  // original byte signatures. The UI cannot inspect CAS magic bytes; this
  // catches the source-visible subset without claiming final admission.
  const fontMime = (value: string): boolean => {
    const mime = value.toLowerCase();
    return mime.startsWith("font/") || [
      "application/font-sfnt", "application/font-woff",
      "application/x-font-ttf", "application/x-font-opentype",
      "application/x-font-woff", "application/vnd.ms-opentype",
      "application/vnd.ms-fontobject",
    ].includes(mime);
  };
  for (const asset of project.assets) {
    if (!sha256(asset.content_sha256)) continue;
    if (fontMime(asset.media_type) ||
        sourceRights.some(font => font.source === "asset:" + asset.id) ||
        declarations.some(copy => copy.content_sha256 === asset.content_sha256)) {
      fontDigests.add(asset.content_sha256);
    }
  }

  for (const font of sourceRights) {
    const referenced = font.source.startsWith("asset:")
      ? originalAssets.get(font.source.slice(6)) : undefined;
    if (!referenced || !sha256(referenced.content_sha256)) {
      issues.push({
        id: font.family + "\u0000" + font.source,
        kind: "external_font",
        label: font.family,
        reason: "This typeface is a referenced external source, not a portable original in the saved CAS. Reinstall or relink it after import.",
        blocks_export: false,
      });
    }
  }

  for (const digest of fontDigests) {
    const aliases = project.assets.filter(asset => asset.content_sha256 === digest);
    const attachedRights = sourceRights.filter(font =>
      aliases.some(asset => font.source === "asset:" + asset.id));
    const forbidden = attachedRights.find(right => right.rights_status !== "cleared");
    const prohibited = declarations.some(copy =>
      copy.content_sha256 === digest && copy.permission === "prohibited");
    const currentCopies = declarations.filter(copy =>
      copy.content_sha256 === digest && aliases.some(asset => asset.id === copy.asset_id));
    // Storage validates every current declaration. A second valid alias must
    // not hide a recorded copy whose own source lacks cleared use rights.
    const permitted = currentCopies.length > 0 && currentCopies.every(copy =>
      copy.version === 1 && copy.permission === "allowed" &&
      !!copy.origin.trim() && !!copy.license.trim() &&
      attachedRights.some(right => right.source === "asset:" + copy.asset_id &&
        right.rights_status === "cleared"));
    if (forbidden || prohibited || !permitted) {
      issues.push({
        id: digest,
        kind: "font_copy_permission",
        label: forbidden?.family ?? attachedRights[0]?.family ??
          aliases[0]?.name ?? "Original font",
        reason: forbidden
          ? "This original font has restricted or unknown use rights. A portable copy cannot be authorized by another alias."
          : prohibited
            ? "An original source-bound font copy is explicitly prohibited."
            : "A current font-copy declaration is missing or invalid. Every recorded declaration for an attached original must allow copying and match cleared use rights, origin and license.",
        blocks_export: true,
      });
    }
  }

  // A portable Project records its chosen renderer, but does not bundle the
  // installed renderer/Driver Host, native editor or execution permission.
  for (const renderer of [...new Set(project.scenes.map(scene => scene.renderer))].sort()) {
    issues.push({
      id: renderer,
      kind: "external_runtime",
      label: renderer,
      reason: "The project stores scene renderer intent. To re-render after import, a compatible, separately admitted renderer runtime is required; executable bytes and permissions are not part of this bundle.",
      blocks_export: false,
    });
  }

  // Native capsule source bytes ARE saved as CAS originals, but the external
  // editor named by the author is still not included with those bytes.
  const editors = new Map<string, string>();
  for (const capsule of project.production_design?.capsules ?? []) {
    const name = capsule.native_editor_hint.trim();
    if (name) editors.set(name.toLowerCase(), name);
  }
  for (const [id, label] of [...editors.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    issues.push({
      id, kind: "external_editor", label,
      reason: "This is an author-declared native editor hint, not evidence that the editor is installed. The native source is a saved asset; editor software is not included.",
      blocks_export: false,
    });
  }

  for (const extension of project.extensions.filter(item => item.enabled)) {
    issues.push({
      id: extension.id,
      kind: "external_extension",
      label: extension.name,
      reason: "The enabled extension " + extension.kind + " at version " +
        extension.package_version + " must be installed and authorized separately. Executable bytes are not included, and this bundle grants no permission to run it or access the network.",
      blocks_export: false,
    });
  }

  for (const system of [...new Set(project.handoffs.map(handoff => handoff.system))].sort()) {
    issues.push({
      id: system,
      kind: "external_integration",
      label: system,
      reason: "The project retains external handoff references, but remote resources, connections and credentials must be resolved and authorized separately after import.",
      blocks_export: false,
    });
  }

  const sorted = issues.sort(order);
  return {
    project_id: project.id,
    generation: project.generation,
    revision: project.revision,
    asset_count: project.assets.length,
    expected_unique_blob_count: digests.size,
    saved_lock_count: project.locks.length,
    saved_measured_table_count: project.production_design?.measured_tables?.length ?? 0,
    saved_native_capsule_count: project.production_design?.capsules?.length ?? 0,
    blocking: sorted.filter(issue => issue.blocks_export),
    external: sorted.filter(issue => !issue.blocks_export),
  };
}

/**
 * The backend owns the Store snapshot, original blob verification, and bundle
 * publication. A frontend receipt must not claim success if its blob count
 * disagrees with this source-bound preflight.
 */
export function assertPortableExportReceipt(
  report: PortableMaterialReport,
  receipt: PortableBundleExport,
): void {
  if (report.blocking.length !== 0 ||
      !receipt ||
      !Number.isSafeInteger(receipt.blob_count) ||
      receipt.blob_count !== report.expected_unique_blob_count ||
      typeof receipt.total_blob_bytes !== "string" ||
      !/^(0|[1-9][0-9]*)$/.test(receipt.total_blob_bytes) ||
      typeof receipt.destination !== "string" ||
      !receipt.destination.trim()) {
    throw new Error("The exported portable bundle did not match the saved asset inventory. Inspect the original destination; do not repeat an uncertain export automatically.");
  }
}
