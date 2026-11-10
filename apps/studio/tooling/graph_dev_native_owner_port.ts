/**
 * Development-only source-owned Native Graph admission boundary.
 *
 * An environment flag, executable path/hash or JSON connection by itself is
 * not a Native/Host authority. An authorized owner implementation must bind
 * the real opened Store, original process and Stream/Evidence collectors.
 * There is intentionally NO fallback, synthetic owner, or release waiver.
 */
import { isDeepStrictEqual } from "node:util";
import type { ScopedChangeService, ScopedChangeServiceOptions } from "../tests/support/scopedChangeService";
import type { Project, ProjectEvent } from "../src/types";

export type OriginalGraphDevWriterOptions = ScopedChangeServiceOptions & {
  binaryPath: string;
  databasePath: string;
  processOwner: NonNullable<ScopedChangeServiceOptions["processOwner"]>;
  rawStreamCapture: NonNullable<ScopedChangeServiceOptions["rawStreamCapture"]>;
  evidenceCapture: NonNullable<ScopedChangeServiceOptions["evidenceCapture"]>;
  graphCustodyRequired: true;
  seedFixture: false;
  desktopInvokeFixture: true;
};

export interface OriginalGraphDevelopmentWriter {
  /** An already admitted first-party Native Host/Store process, not a client
   * that this Studio test is allowed to create or resume itself. */
  readonly service: ScopedChangeService;
  /** The exact Project, Scene and Deliverable selected by the real mounted
   * Native owner on this same Store. Studio may not manufacture another scene
   * or retarget this owner's profile to make a callback appear Ready. */
  readonly projectId: string;
  readonly sceneId: string;
  readonly profileId: string;
}

export interface OriginalGraphDevelopmentOwner {
  readonly writerOptions: OriginalGraphDevWriterOptions;
  /**
   * The Native owner owns the only original process launch and Native client admission;
   * Studio receives it after the actual owner verified its Store, provider,
   * callback budget and evidence collectors. No unreviewed second client.
   */
  openOriginalWriter(): Promise<OriginalGraphDevelopmentWriter>;
  /**
   * Exactly one closure of the original writer, its final EOF and protected
   * custody, followed by a SEPARATE source-built, READ-ONLY process observing
   * the identical physical SQLite/CAS. No copied DB, fake result or replay.
   * Use ScopedChangeService.createReadOnly with --read-only, no fixture or
   * desktop invoke flag; a second ordinary writer cannot pass the UI gate.
   * The implementation must independently verify Project/history and source,
   * then return a read-only Studio service for explicit read-only bootstrap
   * and Project verification. The full editor requires projection commands
   * and tauri mode, neither of which this reader may claim.
   */
  closeOriginalAndOpenIndependentReadOnly(
    original: ScopedChangeService,
    expectedProject: Project,
    expectedHistory: readonly ProjectEvent[],
  ): Promise<ScopedChangeService>;
  close(): Promise<void>;
}

/** The external reader intentionally declares browser-demo, not desktop
 * authority. It has no project projection APIs and cannot reopen the full
 * editing App. Verify its actual read-only bootstrap and complete original
 * Project directly; never fabricate Tauri or turn a read into another Save. */
export function assertOriginalGraphReadOnlyBootstrap(reply: unknown, expected: Project): void {
  const object = typeof reply === "object" && reply !== null && !Array.isArray(reply)
    ? reply as Record<string, unknown> : null;
  const sdk = object?.native_sdk as Record<string, unknown> | undefined;
  const selection = object?.project_selection as Record<string, unknown> | undefined;
  if (!object || object.test_transport !== "service-test-jsonl"
    || object.read_only !== true
    || object.evidence_scope !== "real_service_sqlite_cas_read_only; excludes_tauri_installer_native_render"
    || !sdk || sdk.mode !== "browser-demo"
    || !selection || selection.status !== "selected"
    || selection.project_id !== expected.id
    || !isDeepStrictEqual(object.project, expected)) {
    throw new Error("GRAPH_DEV_INDEPENDENT_READONLY_BOOTSTRAP_NOT_PROVEN");
  }
}

/** No implementation is shipped without the real Native owner/custody source.
 * Failing before any executable is launched is deliberate, not a test PASS. */
export async function acquireGraphDevelopmentOwner(): Promise<OriginalGraphDevelopmentOwner> {
  throw new Error("GRAPH_DEV_ORIGINAL_OWNER_PORT_UNAVAILABLE");
}
