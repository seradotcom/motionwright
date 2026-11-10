/**
 * Narrow TypeScript view of the source-authenticated Graph owner factory.
 *
 * The actual implementation is factory.mjs. This declaration does not invent
 * an approval, capability, credential, Store, Host, or a reader: the factory
 * can only be constructed by a separately authenticated formal or development
 * original-producer admission. Development carries no R74 approval.
 *
 * Keep the launch/custody interfaces linked to the same source-defined
 * ScopedChangeService types, so a caller cannot substitute a plain object.
 */
import type {
  ScopedChangeService,
  ScopedChangeServiceOptions,
  ServiceClosureBarrier,
  ServiceEvidenceCapture,
  ServiceProcessOwner,
  ServiceRawStreamCapture,
} from "../scopedChangeService";
import type { Project } from "../../../src/types";

export interface AuthenticatedGraphOwnerSpec {
  schema: "motionwright.graph-browser-owner-spec/6";
  case_id: string;
  /** The authenticated original producer's exact source identity. */
  source: AuthenticatedGraphCaseSource;
  paths: {
    private_root: string;
    owner_home: string;
    owner_runtime: string;
    owner_state: string;
    owner_socket: string;
    manim_work: string;
  };
  binaries: AuthenticatedGraphSourceAdmission["binaries"];
  runtime: AuthenticatedGraphSourceAdmission["runtime"];
  store: { database_path: string; project_id: string };
  limits: {
    deadline_unix_ms: number;
    max_clients: number;
    max_processes: number;
    max_stream_bytes: number;
    cli_timeout_ms: number;
    close_timeout_ms: number;
  };
}

export interface AuthenticatedGraphCaseSource {
  repository_root: string;
  sha: string;
  tree: string;
  sdk_root: string;
  sdk_sha: string;
  locks: Record<string, string>;
  run_id: string;
  run_attempt: number;
}

export interface AuthenticatedGraphExecutable {
  path: string;
  sha256: string;
  size_bytes: number;
}

export interface AuthenticatedGraphSourceAdmission {
  source: AuthenticatedGraphCaseSource;
  approval?: { context: string; status_id: number };
  development?: {
    kind: "EXPLICIT_DEVELOPMENT_ORIGINAL";
    context: string;
    source_sha: string;
    run_id: string;
    index_sha256: string;
    request_sha256: string;
    actor_id: number;
    formal_acceptance: false;
  };
  nativeService: AuthenticatedGraphExecutable;
  ordinaryService: AuthenticatedGraphExecutable;
  binaries: Record<string, AuthenticatedGraphExecutable>;
  runtime: {
    root: string;
    manifest: string;
    manifest_sha256: string;
    intake_receipt: string;
    intake_receipt_sha256: string;
    fontconfig_root: string;
  };
}

/** Real original owner and all its stream/metadata ports share one instance. */
export interface AuthenticatedGraphCaseFactory
  extends ServiceProcessOwner, ServiceRawStreamCapture, ServiceEvidenceCapture {
  readonly originalProduction: boolean;
  readonly admission: AuthenticatedGraphSourceAdmission;
  readonly root: string;
  readonly deadline: number;
  readonly options: Readonly<{
    databasePath: string;
    caseId: string;
    privateRoot: string;
  }>;
  readonly ownerStart: Promise<unknown> | null;
  /** Quota for the sole source-built reader reserved before any Native Save. */
  reserveDevelopmentReadOnlyOriginals(): boolean;
  /** Actual original writer close, original Host seal, live independent reader. */
  openIndependentlyCustodiedReadOnlyService(
    writer: ScopedChangeService,
    expectedProject: Project,
    expectedHistory: readonly import("../../../src/types").ProjectEvent[],
  ): Promise<ScopedChangeService>;
  assertDevelopmentReadOnlyClosed(): void;
  createNativeService(
    Service: typeof import("../scopedChangeService").ScopedChangeService,
    options: ScopedChangeServiceOptions,
    makeSpec: (project: Project) => Promise<AuthenticatedGraphOwnerSpec>,
  ): Promise<ScopedChangeService>;
  checkOpen(): void;
  closeAfterClients(barrier: ServiceClosureBarrier): Promise<void>;
  assertClosed(): void;
  taint(error: unknown): void;
}

export interface GraphCaseOriginalAdmissionInput {
  privateRoot: string;
  databasePath: string;
  caseId: string;
  deadlineUnixMs: number;
  producerOriginalPath: string;
}

/** Performs genuine source, R74, executable and physical owner admission. */
export function createGraphCaseFactory(
  options: GraphCaseOriginalAdmissionInput,
): Promise<AuthenticatedGraphCaseFactory>;

/** Authenticates one explicit development original, without formal R74 credit. */
export function createGraphDevelopmentCaseFactory(
  options: GraphCaseOriginalAdmissionInput,
): Promise<AuthenticatedGraphCaseFactory>;
