/**
 * Development-only source-owned Native Graph admission boundary.
 *
 * An environment flag, executable path/hash or JSON connection by itself is
 * not a Native/Host authority. An authorized owner implementation must bind
 * the real opened Store, original process and Stream/Evidence collectors.
 * There is intentionally NO fallback, synthetic owner, or release waiver.
 */
import { isDeepStrictEqual } from "node:util";
import { isAbsolute, join } from "node:path";
import { lstatSync } from "node:fs";
import { createGraphDevelopmentCaseFactory } from "../tests/support/graph-owner/factory.mjs";
import { ScopedChangeService, type ScopedChangeServiceOptions } from "../tests/support/scopedChangeService";
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

/**
 * Authenticated development Host/Store owner. The separate development
 * factory verifies the explicit original dispatch against the current HTTPS
 * run, then the original build/source/runtime records and exact callback plan.
 * Its different index schema cannot enter formal R74 admission.
 * The real SharedPool, kernel Store bound and Native Host custody remain required.
 */
type AdmittedGraphCaseFactory = Awaited<ReturnType<typeof createGraphDevelopmentCaseFactory>>;

/** Authenticated first-party factory capability to close the original
 * Writer and Host and return a NEW live read-only Service with its own
 * original streams. A stored oracle or copied SQLite file cannot provide
 * this capability. It still requires a genuine original producer admission. */
type LiveIndependentReaderPort = {
  openIndependentlyCustodiedReadOnlyService?: (
    writer: ScopedChangeService,
    expectedProject: Project,
    expectedHistory: readonly ProjectEvent[],
  ) => Promise<ScopedChangeService>;
};

class SourceBoundGraphDevelopmentOwner implements OriginalGraphDevelopmentOwner {
  readonly writerOptions: OriginalGraphDevWriterOptions;
  private attempted = false;
  private original: OriginalGraphDevelopmentWriter | undefined;
  private reader: ScopedChangeService | undefined;
  private closed = false;

  constructor(private readonly factory: AdmittedGraphCaseFactory) {
    if (!factory.originalProduction || !factory.admission?.development
      || factory.admission?.approval || !factory.admission?.nativeService || !factory.admission?.source
      || !factory.admission?.runtime) {
      throw new Error("GRAPH_DEV_AUTHENTIC_NATIVE_HOST_SOURCE_NOT_ADMITTED");
    }
    // The original index supplies BOTH paths; neither a user flag nor Studio
    // can select a Store or substitute another source-built Native binary.
    const binaryPath = factory.admission.nativeService.path as string;
    const databasePath = factory.options.databasePath as string;
    if (!isAbsolute(binaryPath) || !isAbsolute(databasePath)
      || (process.platform === "win32"
        && ![binaryPath, databasePath].every(value => /^[Ee]:[\\/]/.test(value)))) {
      throw new Error("GRAPH_DEV_ORIGINAL_OWNER_NOT_E_ONLY");
    }
    this.writerOptions = Object.freeze({
      binaryPath, databasePath,
      processOwner: factory,
      rawStreamCapture: factory,
      evidenceCapture: factory,
      graphCustodyRequired: true as const,
      seedFixture: false as const,
      desktopInvokeFixture: true as const,
      timeoutMs: 50_000,
    });
    // Pre-charge a single source-owned live reader BEFORE the initializer or
    // any Native grant can be consumed. Failure here is a safe no-effect DENY.
    factory.reserveDevelopmentReadOnlyOriginals();
  }

  async openOriginalWriter(): Promise<OriginalGraphDevelopmentWriter> {
    // First-party source now exports a LIVE independent reader port, instead
    // of returning only a completed private reopen receipt. Its reservation
    // is owned by the same authenticated factory and was precharged before
    // initializer/Native effects; never substitute an uncustodied reader.
    // An older factory lacking this port still denies BEFORE any Save.
    const readerPort = this.factory as LiveIndependentReaderPort;
    if (typeof readerPort.openIndependentlyCustodiedReadOnlyService !== "function") {
      throw new Error("GRAPH_DEV_LIVE_INDEPENDENT_READONLY_OWNER_CONTRACT_UNAVAILABLE");
    }
    // The one-shot intent is consumed BEFORE initializer or Native launch.
    if (this.attempted || this.closed) {
      throw new Error("GRAPH_DEV_ORIGINAL_WRITER_ALREADY_ATTEMPTED");
    }
    this.attempted = true;
    // Mutable cell: the initializer callback is invoked by the real
    // source-owned factory, not synchronously in this method body.
    const selected: { project?: Project } = {};
    let started: ScopedChangeService | undefined;
    try {
      const factory = this.factory;
      started = await factory.createNativeService(
        ScopedChangeService, this.writerOptions,
        async (project: Project) => {
          // These IDs are read from the REAL ordinary initializer's SQLite;
          // this callback grants no ability to manufacture a second project.
          if (!project?.id || !project.scenes?.[0]?.id
            || !project.deliverables?.[0]?.id
            || project.production_design?.semantic_graphs?.length) {
            throw new Error("GRAPH_DEV_REAL_INITIALIZER_SOURCE_UNAVAILABLE");
          }
          selected.project = structuredClone(project);
          const root = join(factory.root, "owner");
          return {
            schema: "motionwright.graph-browser-owner-spec/6",
            case_id: "graph-flow",
            source: factory.admission.source,
            paths: {
              private_root: root,
              owner_home: join(root, "home"),
              owner_runtime: join(root, "runtime"),
              owner_state: join(root, "state"),
              owner_socket: join(root, "runtime", "broker.sock"),
              manim_work: join(root, "work"),
            },
            binaries: factory.admission.binaries,
            runtime: factory.admission.runtime,
            store: {
              database_path: factory.options.databasePath,
              project_id: project.id,
            },
            limits: {
              deadline_unix_ms: factory.deadline,
              max_clients: 8,
              max_processes: 128,
              max_stream_bytes: 1_048_576,
              cli_timeout_ms: 20_000,
              close_timeout_ms: 30_000,
            },
          };
        },
      );
      if (!selected.project) throw new Error("GRAPH_DEV_ORIGINAL_PROJECT_NOT_ISSUED");
      // Do not narrow a mutable outer variable through the asynchronous
      // initializer. The exact real Native service must have returned first.
      if (!started) throw new Error("GRAPH_DEV_ORIGINAL_NATIVE_WRITER_NOT_STARTED");
      const source: Project = selected.project;
      const nativeWriter: ScopedChangeService = started;
      // Native must read precisely the initializer's original Store. Neither
      // URI equality nor a previously parsed fixture can replace this read.
      const current = await nativeWriter.project(source.id);
      if (!isDeepStrictEqual(current, source)
        || current.scenes?.[0]?.renderer !== "manim-community"
        || nativeWriter.binaryPath !== this.writerOptions.binaryPath
        || nativeWriter.databasePath !== this.writerOptions.databasePath
        || nativeWriter.processStreams.length !== 1
        || nativeWriter.processExits.length !== 0
        || nativeWriter.lifecycleTaints.length !== 0
        || !nativeWriter.processStreams[0].process_id) {
        throw new Error("GRAPH_DEV_ORIGINAL_NATIVE_WRITER_SOURCE_NOT_PROVEN");
      }
      const originalWriter: OriginalGraphDevelopmentWriter = Object.freeze({
        service: nativeWriter,
        projectId: current.id,
        sceneId: current.scenes[0].id,
        profileId: current.deliverables[0].id,
      });
      this.original = originalWriter;
      return originalWriter;
    } catch (error) {
      // An uncertain launch remains consumed. Keep the source-owned original
      // evidence and first failure; never re-open this Store as a substitute.
      this.factory.taint(error);
      if (started) {
        try { await started.close(); } catch (cleanup) { this.factory.taint(cleanup); }
      }
      throw error;
    }
  }

  async closeOriginalAndOpenIndependentReadOnly(
    original: ScopedChangeService, expectedProject: Project,
    expectedHistory: readonly ProjectEvent[],
  ): Promise<ScopedChangeService> {
    if (!this.original || original !== this.original.service || this.reader) {
      throw new Error("GRAPH_DEV_ORIGINAL_WRITER_CUSTODY_IDENTITY_MISMATCH");
    }
    const readerPort = this.factory as LiveIndependentReaderPort;
    const reopen = readerPort.openIndependentlyCustodiedReadOnlyService;
    if (typeof reopen !== "function") {
      throw new Error("GRAPH_DEV_LIVE_INDEPENDENT_READONLY_OWNER_CONTRACT_UNAVAILABLE");
    }
    // Real original opened pager request, before the factory closes its
    // original writer. No pathname, caller-provided digest or copied DB is an
    // acceptable replacement for either live process observation.
    const writerWitness = await original.openedStoreIdentityWitness();
    const reader = await reopen.call(this.factory, original, expectedProject, expectedHistory);
    this.reader = reader; // Retain even if verification fails: cleanup owns it.
    const writerStreams = original.processStreams;
    const writerClosed = writerStreams.length === original.processExits.length
      && writerStreams.length > 0
      && writerStreams.every(stream => stream.process_id !== null
        && stream.exit_index !== null && stream.stdout_ended
        && stream.stderr_ended && stream.jsonl_reader_closed)
      && original.processExits.every(exit => exit.code === 0 && exit.signal === null)
      && original.lifecycleTaints.length === 0;
    const sourceReader = reader !== original && reader.isReadOnly
      && reader.binaryPath === this.factory.admission.ordinaryService.path
      && reader.databasePath === this.writerOptions.databasePath
      && reader.processStreams.length === 1
      && reader.processStreams[0].process_id !== null
      && writerStreams.every(stream => stream.process_id !== reader.processStreams[0].process_id);
    if (!writerClosed || !sourceReader) {
      throw new Error("GRAPH_DEV_INDEPENDENT_SOURCE_BUILT_READONLY_NOT_PROVEN");
    }
    const readerWitness = await reader.openedStoreIdentityWitness();
    if (readerWitness !== writerWitness
      || !isDeepStrictEqual(await reader.project(expectedProject.id), expectedProject)
      || !isDeepStrictEqual(
        await reader.history(expectedProject.id, expectedProject.revision - expectedHistory.length),
        expectedHistory)) {
      throw new Error("GRAPH_DEV_INDEPENDENT_SQLITE_CAS_IDENTITY_OR_HISTORY_CHANGED");
    }
    return reader;
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    if (this.reader) {
      await this.reader.close();
      // A source-owner-supplied live reader is valid only if its real parent
      // completed the original custody before returning this reader.
      this.factory.assertDevelopmentReadOnlyClosed();
      return;
    }
    if (this.original) {
      try { await this.original.service.close(); }
      catch (error) { this.factory.taint(error); }
    }
    // Without the trusted service-closure barrier this is deliberately an
    // INCOMPLETE original. It still asks the existing source-owned process to
    // terminate and retain evidence; never upgrades an uncertain case to PASS.
    if (this.factory.ownerStart) {
      await this.factory.closeAfterClients({
        closed: false, receipt_path: null, receipt_sha256: null,
      });
    }
  }
}

/**
 * Acquire only the actual independently authenticated Graph Host producer.
 *
 * The locator is untrusted. createGraphDevelopmentCaseFactory() authenticates
 * the current explicit development dispatch and original source-built producer.
 * The Native callback capability still comes from the real scoped Host grant.
 */
export async function acquireGraphDevelopmentOwner(): Promise<OriginalGraphDevelopmentOwner> {
  if (process.env.MOTIONWRIGHT_GRAPH_DEV_FIRST_SAVE !== "1"
    || process.env.MOTIONWRIGHT_GRAPH_NATIVE_CASE) {
    throw new Error("GRAPH_DEV_ORIGINAL_DEVELOPMENT_SELECTION_INVALID");
  }
  // The existing kernel/file-limit and original-process guard is Linux-only.
  // Windows local source review cannot impersonate a Linux GitHub original.
  if (process.platform !== "linux") {
    throw new Error("GRAPH_DEV_ORIGINAL_OWNER_REQUIRES_LINUX_PROCESS_CUSTODY");
  }
  const original = process.env.MOTIONWRIGHT_GRAPH_PRODUCER_ORIGINAL;
  if (!original || !isAbsolute(original)) {
    throw new Error("GRAPH_DEV_AUTHENTIC_ORIGINAL_PRODUCER_PATH_REQUIRED");
  }
  let metadata: ReturnType<typeof lstatSync>;
  try { metadata = lstatSync(original); }
  catch { throw new Error("GRAPH_DEV_AUTHENTIC_ORIGINAL_PRODUCER_MISSING"); }
  if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.nlink !== 1) {
    throw new Error("GRAPH_DEV_AUTHENTIC_ORIGINAL_PRODUCER_FILE_INVALID");
  }
  const provisional = join(process.cwd(), ".graph-dev-source-candidate");
  // The provisional path is NOT authority or the actual Store path. The
  // original authenticated producer index defines the g-slot and exact mount.
  const factory = await createGraphDevelopmentCaseFactory({
    privateRoot: provisional,
    databasePath: join(provisional, "store", "motionwright.sqlite3"),
    caseId: "graph-flow",
    deadlineUnixMs: Date.now() + 900_000,
    producerOriginalPath: original,
  });
  return new SourceBoundGraphDevelopmentOwner(factory);
}
