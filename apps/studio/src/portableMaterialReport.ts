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
  kind: "unbound_asset" | "external_font" | "font_copy_permission";
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

  for (const font of project.visual_language.fonts) {
    const isAssetSource = font.source.startsWith("asset:");
    const referenced = isAssetSource ? originalAssets.get(font.source.slice(6)) : undefined;
    const included = referenced !== undefined && sha256(referenced.content_sha256);
    if (!included) {
      issues.push({
        id: font.family + "\u0000" + font.source,
        kind: "external_font",
        label: font.family,
        reason: "This typeface is a referenced external source, not a portable original in the saved CAS. Reinstall or relink it after import.",
        blocks_export: false,
      });
    } else if (font.rights_status !== "cleared") {
      issues.push({
        id: font.family + "\u0000" + font.source,
        kind: "font_copy_permission",
        label: font.family,
        reason: "Font rights are not cleared. The Store requires original source-bound copy permission before exporting protected font bytes.",
        blocks_export: true,
      });
    }
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
