/**
 * Playwright transport to the real, opt-in StudioService JSONL test executable.
 * SQLite, CAS, grants, changes and journal responses all come from that process.
 * The injected invoke seam is test transport; it does not certify Tauri, an
 * installer, a native renderer, or creative acceptance. No command is mocked.
 */
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { createReadStream } from "node:fs";
import { access, cp, lstat, mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { createInterface } from "node:readline";
import { isDeepStrictEqual } from "node:util";
import type { Page } from "@playwright/test";
import type { FileImportInput, FileImportReceipt, PreparedFileImport } from "../../src/fileImport";
import type {
  StudioBootstrap, Change, EffectGrantReceipt, EffectKind, PortableBundleExport,
  PortableBundlePlan, Project, ProjectEvent, WaveformPage,
} from "../../src/types";

type RpcId = string | number;
type InvokeArgs = Record<string, unknown>;

export interface ServiceRpcRequest {
  id?: RpcId;
  command: string;
  args?: InvokeArgs;
}

export type ServiceProjectionFailure =
  | { code: "invalid_request_or_content" | "not_found" | "generation_changed" | "unavailable" }
  | { code: "too_large"; limit: number }
  | { code: "stale_revision"; expected: string; actual: string };

const projectionCommands = new Set(["project_projection_head", "project_scene_page", "project_scene_nodes"]);

function projectionFailure(command: string, value: unknown): value is ServiceProjectionFailure {
  if (!projectionCommands.has(command) || typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const failure = value as Record<string, unknown>;
  const fields = (...names: string[]) => Object.keys(failure).length === names.length && names.every(name => Object.hasOwn(failure, name));
  const revision = (input: unknown) => typeof input === "string" && /^(0|[1-9][0-9]{0,19})$/.test(input)
    && (input.length < 20 || input <= "18446744073709551615");
  switch (failure.code) {
    case "invalid_request_or_content": case "not_found": case "generation_changed": case "unavailable":
      return fields("code");
    case "too_large":
      return fields("code", "limit") && typeof failure.limit === "number" && Number.isSafeInteger(failure.limit) && failure.limit > 0;
    case "stale_revision":
      return fields("code", "expected", "actual") && revision(failure.expected) && revision(failure.actual);
    default: return false;
  }
}

export type ServiceRpcResponse<T = unknown> =
  | { id: RpcId; ok: true; result: T }
  | { id: RpcId; ok: false; error: string | ServiceProjectionFailure };

export interface ServiceStartup extends StudioBootstrap {
  test_transport: "service-test-jsonl";
  evidence_scope: string;
  /** Explicit browser fixture routing, never a claim of installed Tauri. */
  desktop_invoke_fixture: boolean;
  fixture: {
    label: "synthetic_test_fixture";
    scene_id: string | null;
    node_id: string | null;
    other_scene_id: string | null;
    other_node_id: string | null;
    hero_instance_id: string | null;
    asset_sha256: string | null;
  };
}

export interface ServiceBootstrap extends ServiceStartup { project: Project }

export interface ScopedChangeServiceOptions {
  /** Existing or new absolute test SQLite path; never deleted by this helper. */
  databasePath?: string;
  timeoutMs?: number;
  /** A peer/reopen should not create a new fixture in an empty database. */
  seedFixture?: boolean;
  /** Opt into local import/bundle UI routes via JSONL; no native render authority. */
  desktopInvokeFixture?: boolean;
}

export interface ServiceTranscriptEntry {
  at: string;
  processId: number | undefined;
  direction: "request" | "response";
  payload: ServiceRpcRequest | ServiceRpcResponse;
}

interface PendingRequest {
  command: string;
  resolve: (response: ServiceRpcResponse) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

export interface ServiceProcessExit {
  processId: number | undefined;
  code: number | null;
  signal: NodeJS.Signals | null;
  observedAt: string;
}

export interface ServiceLifecycleTaint {
  kind: "pending_at_close" | "request_timeout" | "transport_failure" | "unclean_exit" | "close_deadline" | "unobserved_exit";
  processId: number | undefined;
  observedAt: string;
  message: string;
  pendingCommands: string[];
}

export interface StoredProjectOracle {
  schemaVersion: 1;
  observedAt: string;
  project: Project;
  journal: ProjectEvent[];
  journalAfterRevisionExclusive: 0;
  journalComplete: true;
}

export interface StoreFinalization {
  schemaVersion: 1;
  oracle: StoredProjectOracle | null;
  preparation: { index: number; status: "fulfilled" | "rejected"; error?: string }[];
  closures: { databasePath: string; status: "fulfilled" | "rejected"; error?: string; exits: ServiceProcessExit[]; taints: ServiceLifecycleTaint[] }[];
  errors: string[];
  capture: ClosedStoreCapture | null;
}

interface StoreEntry {
  path: string;
  kind: "directory" | "file";
  sizeBytes?: number;
  sha256?: string;
}

/** Every entry is included; unsupported/symlink entries fail instead of disappearing. */
async function storeTree(directory: string): Promise<StoreEntry[]> {
  const entries: StoreEntry[] = [];
  const visit = async (path: string, name: string): Promise<void> => {
    const info = await lstat(path);
    if (info.isSymbolicLink()) throw new Error(`Closed store capture refuses a symlink: ${name || "."}`);
    if (info.isDirectory()) {
      entries.push({ path: name, kind: "directory" });
      const children = (await readdir(path)).sort();
      for (const child of children) await visit(join(path, child), name ? `${name}/${child}` : child);
    } else if (info.isFile()) {
      const digest = createHash("sha256");
      let sizeBytes = 0;
      for await (const chunk of createReadStream(path)) { digest.update(chunk); sizeBytes += chunk.length; }
      if (sizeBytes !== info.size) throw new Error(`Store file changed size during capture: ${name}`);
      entries.push({ path: name, kind: "file", sizeBytes, sha256: digest.digest("hex") });
    } else throw new Error(`Closed store capture refuses a non-regular entry: ${name || "."}`);
  };
  await visit(directory, "");
  return entries;
}

export interface ClosedStoreCapture {
  schemaVersion: 1;
  capturedAt: string;
  sourceDirectory: string;
  snapshotDirectory: string;
  databaseRelativePath: string;
  entries: StoreEntry[];
  sourceBeforeSha256: string;
  sourceAfterSha256: string;
  snapshotSha256: string;
  lifecycle: { databasePath: string; exits: ServiceProcessExit[]; taints: ServiceLifecycleTaint[] }[];
  evidenceScope: string;
}

export class ScopedChangeService {
  private static readonly instances = new Set<ScopedChangeService>();
  readonly databasePath: string;
  readonly directory: string;
  readonly binaryPath: string;
  readonly transcript: ServiceTranscriptEntry[] = [];
  readonly processExits: ServiceProcessExit[] = [];
  /** Permanent for this helper, including across close()/start() lifetimes. */
  readonly lifecycleTaints: ServiceLifecycleTaint[] = [];
  private readonly timeoutMs: number;
  private readonly seedFixture: boolean;
  private readonly desktopInvokeFixture: boolean;
  private readonly ownsDirectory: boolean;
  private child: ChildProcessWithoutNullStreams | null = null;
  private starting: Promise<this> | null = null;
  private closing: Promise<void> | null = null;
  private exited: Promise<ServiceProcessExit> | null = null;
  private stderrText = "";
  private lastExit = "Process has not started";
  private sequence = 0;
  private readonly sessionId = randomUUID();
  private readonly pending = new Map<string, PendingRequest>();
  private readonly usedIds = new Set<string>();
  private readonly timedOutIds = new Set<string>();

  private constructor(binaryPath: string, databasePath: string, ownsDirectory: boolean, options: ScopedChangeServiceOptions) {
    this.binaryPath = binaryPath;
    this.databasePath = databasePath;
    this.directory = dirname(databasePath);
    this.ownsDirectory = ownsDirectory;
    this.timeoutMs = options.timeoutMs ?? 10_000;
    this.seedFixture = options.seedFixture ?? true;
    this.desktopInvokeFixture = options.desktopInvokeFixture ?? false;
    if (!Number.isFinite(this.timeoutMs) || this.timeoutMs <= 0) {
      throw new Error("Service RPC timeoutMs must be a positive finite number");
    }
    ScopedChangeService.instances.add(this);
  }

  static async create(options: ScopedChangeServiceOptions = {}): Promise<ScopedChangeService> {
    const configured = process.env.MOTIONWRIGHT_SERVICE_TEST_BIN;
    if (!configured?.trim() || !isAbsolute(configured)) {
      throw new Error("Set MOTIONWRIGHT_SERVICE_TEST_BIN to the absolute path of the built studio_review_session executable; no service fixture fallback is available");
    }
    if (options.databasePath && !isAbsolute(options.databasePath)) {
      throw new Error("databasePath must identify an absolute, isolated test SQLite file");
    }
    let databasePath = options.databasePath;
    if (!databasePath) {
      const parent = resolve(process.env.MOTIONWRIGHT_SERVICE_TEST_TMPDIR ?? tmpdir());
      await mkdir(parent, { recursive: true });
      const directory = await mkdtemp(join(parent, "motionwright-scoped-review-"));
      databasePath = join(directory, "studio.sqlite3");
    }
    const service = new ScopedChangeService(configured, databasePath, !options.databasePath, options);
    try {
      await service.start();
      // A real response confirms both successful process startup and the
      // transport/database contract, without implicitly selecting a project.
      await service.bootstrap(null);
      return service;
    } catch (error) {
      const [closed] = await Promise.allSettled([service.close()]);
      // Failed startup/transport stores remain available for diagnosis. Only
      // an explicit dispose() of a clean helper removes its owned directory.
      if (closed.status === "rejected") throw new AggregateError([error, closed.reason], "Service creation and its cleanup failed");
      throw error;
    }
  }

  get stderr(): string { return this.stderrText; }

  private taint(kind: ServiceLifecycleTaint["kind"], message: string, child = this.child): void {
    this.lifecycleTaints.push({ kind, processId: child?.pid, observedAt: new Date().toISOString(),
      message, pendingCommands: [...this.pending.values()].map(request => request.command) });
  }

  private failure(message: string): Error {
    const tail = this.stderrText.trim();
    return new Error(`${message}${tail ? `\nService stderr:\n${tail}` : ""}`);
  }

  private rejectPending(error: Error, ignoreLateResponses = false): void {
    for (const [id, pending] of this.pending) {
      if (ignoreLateResponses) this.timedOutIds.add(id);
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  private receive(child: ChildProcessWithoutNullStreams, line: string): void {
    if (this.child !== child) return;
    try {
      const parsed: unknown = JSON.parse(line);
      if (typeof parsed !== "object" || parsed === null) throw new Error("Response must be an object");
      const response = parsed as ServiceRpcResponse;
      if (!(typeof response.id === "string" || typeof response.id === "number") || typeof response.ok !== "boolean") {
        throw new Error("Response lacks a correlation id or boolean ok field");
      }
      const key = JSON.stringify(response.id);
      const pending = this.pending.get(key);
      // Structured failures require this exact still-pending projection command.
      // Legacy strings, late legacy responses and lifecycle custody keep their
      // existing behavior; unrecognized objects are terminal protocol failures.
      if (response.ok ? !("result" in response)
        : !(typeof response.error === "string" || (pending && projectionFailure(pending.command, response.error)))) {
        throw new Error("Response lacks its declared result/error payload");
      }
      this.transcript.push({ at: new Date().toISOString(), processId: child.pid, direction: "response", payload: response });
      if (this.timedOutIds.delete(key)) return;
      if (!pending) throw new Error(`Unexpected response id ${key}`);
      this.pending.delete(key);
      clearTimeout(pending.timer);
      pending.resolve(response);
    } catch (error) {
      this.lastExit = `Invalid service JSONL response: ${String(error)}`;
      this.taint("transport_failure", this.lastExit, child);
      this.rejectPending(this.failure(this.lastExit));
      child.kill();
    }
  }

  async start(): Promise<this> {
    if (this.starting) return this.starting;
    this.starting = this.launch();
    try { return await this.starting; }
    finally { this.starting = null; }
  }

  private async launch(): Promise<this> {
    if (this.closing) await this.closing;
    if (this.child) return this;
    await access(this.binaryPath);
    await mkdir(this.directory, { recursive: true });
    this.stderrText = "";
    this.usedIds.clear();
    this.timedOutIds.clear();
    const args = ["--database", this.databasePath];
    if (this.seedFixture) args.push("--synthetic-fixture");
    if (this.desktopInvokeFixture) args.push("--desktop-invoke-fixture");
    const child = spawn(this.binaryPath, args, { stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
    this.child = child;
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk: string) => { this.stderrText = (this.stderrText + chunk).slice(-16_384); });
    const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
    lines.on("line", line => this.receive(child, line));
    this.exited = new Promise<ServiceProcessExit>(resolveExit => {
      child.once("close", (code, signal) => {
        const exit = { processId: child.pid, code, signal, observedAt: new Date().toISOString() };
        this.processExits.push(exit);
        if (code !== 0 || signal !== null) this.taint("unclean_exit", `Process closed with code ${code}, signal ${signal ?? "none"}`, child);
        if (this.pending.size) this.taint("transport_failure", "Process closed before all RPC responses were observed", child);
        lines.close();
        if (this.child === child) {
          this.child = null;
          this.lastExit = `StudioService test process exited (code ${code}, signal ${signal ?? "none"})`;
          this.rejectPending(this.failure(this.lastExit));
        }
        resolveExit(exit);
      });
    });
    child.on("error", error => {
      this.lastExit = `Cannot run StudioService test process: ${error.message}`;
      this.taint("transport_failure", this.lastExit, child);
      this.rejectPending(this.failure(this.lastExit));
    });
    child.stdin.on("error", error => {
      this.taint("transport_failure", `StudioService stdin failed: ${error.message}`, child);
      this.rejectPending(this.failure(`StudioService stdin failed: ${error.message}`));
    });
    child.stdout.on("error", error => {
      this.taint("transport_failure", `StudioService stdout failed: ${error.message}`, child);
      this.rejectPending(this.failure(`StudioService stdout failed: ${error.message}`));
    });
    lines.on("error", error => {
      this.taint("transport_failure", `StudioService JSONL reader failed: ${error.message}`, child);
      this.rejectPending(this.failure(`StudioService JSONL reader failed: ${error.message}`));
    });
    await new Promise<void>((resolveSpawn, rejectSpawn) => {
      child.once("spawn", resolveSpawn);
      child.once("error", error => rejectSpawn(this.failure(`StudioService process launch failed: ${error.message}`)));
    });
    this.lastExit = "Process is running";
    return this;
  }

  async rawRequest<T = unknown>(request: ServiceRpcRequest): Promise<ServiceRpcResponse<T>> {
    const child = this.child;
    if (!child || this.closing) {
      this.taint("transport_failure", `RPC ${request.command} was requested outside a running session`);
      throw this.failure(`StudioService is not running: ${this.lastExit}`);
    }
    const id = request.id ?? `${this.sessionId}:${++this.sequence}`;
    if (!((typeof id === "string" && id.length > 0) || (typeof id === "number" && Number.isSafeInteger(id)))) {
      throw new Error("RPC id must be a nonempty string or safe integer");
    }
    if (typeof request.command !== "string" || !request.command.trim()) throw new Error("RPC command must be a nonempty string");
    const key = JSON.stringify(id);
    if (this.usedIds.has(key)) throw new Error(`RPC id ${key} was already used by this process; use another correlation id`);
    const wire = JSON.stringify({ id, command: request.command, args: request.args ?? {} });
    this.usedIds.add(key);
    this.transcript.push({ at: new Date().toISOString(), processId: child.pid, direction: "request", payload: JSON.parse(wire) as ServiceRpcRequest });
    return new Promise<ServiceRpcResponse<T>>((resolveResponse, rejectResponse) => {
      const timer = setTimeout(() => {
        this.taint("request_timeout", `StudioService ${request.command} (id ${key}) timed out after ${this.timeoutMs} ms; its outcome is unconfirmed`, child);
        this.pending.delete(key);
        this.timedOutIds.add(key);
        rejectResponse(this.failure(`StudioService ${request.command} (id ${key}) timed out after ${this.timeoutMs} ms; the request was not retried and may already have committed`));
      }, this.timeoutMs);
      this.pending.set(key, {
        command: request.command,
        resolve: response => resolveResponse(response as ServiceRpcResponse<T>),
        reject: rejectResponse,
        timer,
      });
      child.stdin.write(wire + "\n", error => {
        if (!error) return;
        this.taint("transport_failure", `Could not write StudioService ${request.command} (id ${key}): ${error.message}`, child);
        const pending = this.pending.get(key);
        if (!pending) return;
        clearTimeout(pending.timer);
        this.pending.delete(key);
        pending.reject(this.failure(`Could not write StudioService ${request.command} (id ${key}): ${error.message}`));
      });
    });
  }

  async invoke<T = unknown>(command: string, args: InvokeArgs = {}): Promise<T> {
    const response = await this.rawRequest<T>({ command, args });
    if (!response.ok) {
      if (typeof response.error === "string") throw new Error(`${command}: ${response.error}`);
      throw response.error;
    }
    return response.result;
  }

  async bootstrap(projectId: string | null): Promise<ServiceStartup> {
    const response = await this.invoke<ServiceStartup>("bootstrap", { request: { project_id: projectId } });
    if (response.test_transport !== "service-test-jsonl" || response.fixture?.label !== "synthetic_test_fixture") {
      this.taint("transport_failure", "The executable did not identify the required real-service test transport");
      throw new Error("The executable did not identify the real-service synthetic test transport");
    }
    if (this.desktopInvokeFixture && (!response.desktop_invoke_fixture || response.native_sdk.mode !== "tauri")) {
      this.taint("transport_failure", "The executable did not identify the requested desktop-invoke fixture");
      throw new Error("The test executable did not enable the explicitly requested browser desktop-invoke fixture");
    }
    return response;
  }

  /** Explicit test arrangement only: ambiguity is an error, never latest-wins. */
  async fixtureBootstrap(projectId?: string): Promise<ServiceBootstrap> {
    if (!projectId) {
      const page = await this.invoke<{ items: { id: string }[]; truncated: boolean }>(
        "list_projects", { request: { cursor: null, limit: 2 } });
      if (page.truncated || page.items.length !== 1) {
        throw new Error("Choose the explicit fixture project identity; the real index is empty or ambiguous");
      }
      projectId = page.items[0].id;
    }
    const response = await this.bootstrap(projectId);
    if (!response.project || response.project.id !== projectId ||
        response.project_selection.status !== "selected" || response.project_selection.project_id !== projectId) {
      throw new Error("The explicitly chosen fixture project could not be opened");
    }
    return response as ServiceBootstrap;
  }

  project(projectId: string): Promise<Project> {
    return this.invoke<Project>("get_project", { request: { project_id: projectId } });
  }

  /** Reopen SQLite and invalidate grants inside the same process. */
  reopen(projectId: string): Promise<Project> {
    return this.invoke<Project>("reopen", { request: { project_id: projectId } });
  }

  history(projectId: string, afterRevision = 0, limit = 100): Promise<ProjectEvent[]> {
    return this.invoke<ProjectEvent[]>("project_history", { request: { project_id: projectId, after_revision: afterRevision, limit } });
  }

  /** Complete canonical comparison, collected before any final store closure. */
  async observeStoredProject(expected: Project): Promise<StoredProjectOracle> {
    const project = await this.project(expected.id);
    if (!isDeepStrictEqual(project, expected)) throw new Error("The current stored Project differs from the final Project observed by the test");
    const journal: ProjectEvent[] = [];
    let afterRevision = 0;
    for (;;) {
      const page = await this.history(project.id, afterRevision, 100);
      if (!page.length) break;
      for (const event of page) {
        if (!Number.isSafeInteger(event.revision) || event.revision !== afterRevision + 1 || event.revision > project.revision) {
          throw new Error("The full journal has a gap, is unordered, repeated, or ahead of the observed Project");
        }
        afterRevision = event.revision;
        journal.push(event);
      }
    }
    if (afterRevision !== project.revision) {
      throw new Error("The full journal is truncated before the observed Project revision");
    }
    if (!isDeepStrictEqual(await this.project(expected.id), project)) throw new Error("The stored Project changed while collecting its full journal");
    return { schemaVersion: 1, observedAt: new Date().toISOString(), project, journal,
      journalAfterRevisionExclusive: 0, journalComplete: true };
  }

  async apply(project: Project, change: Change): Promise<Project> {
    const requestId = randomUUID();
    const scope = { project_id: project.id, generation: project.generation, revision: project.revision };
    const grant = await this.invoke<EffectGrantReceipt>("issue_effect_grant", { request: { effect: "project_edit", ...scope, subject: requestId } });
    return this.invoke<Project>("apply_change", { request: { ...scope, request_id: requestId, effect_grant: grant.token, change } });
  }

  /** Fixture setup uses the same real grant and invoke payloads as api.ts. */
  grant(effect: EffectKind, project: Project | null, subject: string): Promise<EffectGrantReceipt> {
    return this.invoke<EffectGrantReceipt>("issue_effect_grant", { request: {
      effect, project_id: project?.id ?? null, generation: project?.generation ?? null,
      revision: project?.revision ?? null, subject: subject.trim(),
    } });
  }

  async importAsset(project: Project, path: string, name?: string, mediaType?: string): Promise<Project> {
    return this.importFile(project, { kind: "asset", path, name: name ?? null,
      media_type: mediaType ?? null, label: null });
  }

  async importVoice(project: Project, path: string, label?: string): Promise<Project> {
    return this.importFile(project, { kind: "voice", path, name: null,
      media_type: null, label: label ?? null });
  }

  /** Fixture arrangement crosses the actual preparation, grant and receipt APIs. */
  private async importFile(project: Project, input: FileImportInput): Promise<Project> {
    const scope = { project_id: project.id, generation: project.generation, revision: project.revision };
    const prepared = await this.invoke<PreparedFileImport>("prepare_file_import", { request: { ...scope, ...input } });
    if (prepared.schema !== "motionwright.file-import-preparation/1" || prepared.project_id !== project.id
      || prepared.generation !== project.generation || prepared.revision !== project.revision
      || prepared.intent.kind !== input.kind || prepared.path !== input.path.trim()) {
      throw new Error("The real service import preparation differs from its fixture request");
    }
    const requestId = randomUUID();
    const grant = await this.grant("import_local", project, prepared.path);
    const receipt = await this.invoke<FileImportReceipt>(`import_${input.kind}_file`, { request: {
      ...scope, request_id: requestId, effect_grant: grant.token,
      path: prepared.path, intent: prepared.intent,
    } });
    if (receipt.schema !== "motionwright.file-import-receipt/1" || receipt.request_id !== requestId
      || !isDeepStrictEqual(receipt.intent, prepared.intent) || typeof receipt.replayed !== "boolean"
      || receipt.project.id !== project.id || receipt.project.generation !== project.generation
      || receipt.project.revision !== project.revision + 1) {
      throw new Error("The real service import receipt differs from the original fixture request");
    }
    return receipt.project;
  }

  waveform(projectId: string, trackId: string, pageIndex = 0, pageSize = 192): Promise<WaveformPage> {
    return this.invoke<WaveformPage>("waveform_page", { request: {
      project_id: projectId, track_id: trackId, page_index: pageIndex, page_size: pageSize,
    } });
  }

  async exportBundle(project: Project, path: string): Promise<PortableBundleExport> {
    const grant = await this.grant("deliver_local", project, path);
    return this.invoke<PortableBundleExport>("export_project_bundle", { request: {
      project_id: project.id, generation: project.generation, revision: project.revision,
      effect_grant: grant.token, path,
    } });
  }

  inspectBundle(path: string): Promise<PortableBundlePlan> {
    return this.invoke<PortableBundlePlan>("inspect_project_bundle", { request: { path } });
  }

  async importBundle(path: string): Promise<Project> {
    const grant = await this.grant("import_local", null, path);
    return this.invoke<Project>("import_project_bundle", { request: { path, effect_grant: grant.token } });
  }

  /** An independent process and grant registry, sharing only the SQLite/CAS. */
  peer(): Promise<ScopedChangeService> {
    return ScopedChangeService.create({ databasePath: this.databasePath, timeoutMs: this.timeoutMs,
      seedFixture: false, desktopInvokeFixture: this.desktopInvokeFixture });
  }

  /** Install before page.goto; every invoke crosses into the real process. */
  async attachPage(page: Page, projectId: string | null): Promise<void> {
    const binding = `__motionwright_service_rpc_${randomUUID().replaceAll("-", "")}`;
    await page.exposeBinding(binding, async (_source, command: string, args?: InvokeArgs) => this.invoke(command, args));
    await page.addInitScript(({ name, selected }) => {
      // One explicit setup per page attachment. A reload must observe the
      // user's changed/removed/corrupt preference, not reinstall the fixture.
      const setupKey = "motionwright.test.fixture-selection:" + name;
      if (selected !== null && sessionStorage.getItem(setupKey) !== "arranged") {
        if (localStorage.getItem("motionwright.project-library.selection.v1") === null) {
          localStorage.setItem("motionwright.project-library.selection.v1", JSON.stringify({
            schema: "motionwright.project-selection/1", project_id: selected,
          }));
        }
        sessionStorage.setItem(setupKey, "arranged");
      }
      const runtime = window as unknown as Record<string, unknown>;
      runtime.__MOTIONWRIGHT_SERVICE_TEST_TRANSPORT__ = {
        kind: "service-test-jsonl", fixture: "synthetic_test_fixture",
        evidenceScope: "real_service_sqlite_cas; excludes_tauri_installer_native_render",
      };
      runtime.__TAURI_INTERNALS__ = {
        invoke: (command: string, args: Record<string, unknown> = {}) => {
          const binding = runtime[name] as (command: string, args: Record<string, unknown>) => Promise<unknown>;
          return binding(command, args);
        },
      };
    }, { name: binding, selected: projectId });
  }

  /** Opt-in fixture arrangement; new/empty-library tests call attachPage(null). */
  async attachFixturePage(page: Page, projectId?: string): Promise<void> {
    const fixture = await this.fixtureBootstrap(projectId);
    await this.attachPage(page, fixture.project.id);
  }

  /** Stop the complete process while retaining SQLite/CAS for start()/evidence. */
  async close(): Promise<void> {
    if (this.closing) return this.closing;
    this.closing = this.stop();
    try { await this.closing; }
    finally { this.closing = null; }
  }

  private async stop(): Promise<void> {
    if (this.starting) await this.starting.catch(() => undefined);
    const child = this.child;
    const exited = this.exited;
    if (!exited) {
      if (this.lifecycleTaints.length) throw this.failure("StudioService lifecycle is tainted and has no observed exit");
      return;
    }
    const requireCleanExit = (exit: ServiceProcessExit): void => {
      if (exit.code !== 0 || exit.signal !== null) {
        throw this.failure(`StudioService did not close cleanly (pid ${exit.processId}, code ${exit.code}, signal ${exit.signal ?? "none"})`);
      }
      if (this.lifecycleTaints.length) throw this.failure(`StudioService lifecycle is tainted: ${this.lifecycleTaints.map(taint => taint.kind + ": " + taint.message).join("; ")}`);
    };
    if (!child) { requireCleanExit(await exited); return; }
    if (this.pending.size) this.taint("pending_at_close", "Close was requested with unresolved RPCs", child);
    this.rejectPending(this.failure("StudioService test session closed before pending RPCs finished"), true);
    try { child.stdin.end(); }
    catch (error) { this.taint("transport_failure", `Could not end StudioService stdin: ${String(error)}`, child); }
    let forceStop: ReturnType<typeof setTimeout> | undefined;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    const missingExit = new Promise<never>((_resolve, reject) => {
      forceStop = setTimeout(() => {
        this.taint("close_deadline", "Graceful close exceeded 2000 ms; forced termination was attempted", child);
        try { child.kill("SIGKILL"); }
        catch (error) { this.taint("transport_failure", `Forced termination failed: ${String(error)}`, child); }
        deadline = setTimeout(() => {
          this.taint("unobserved_exit", "No close event was observed after the termination grace period", child);
          reject(this.failure("StudioService close has no observed exit; capture is forbidden"));
        }, 2_000);
      }, 2_000);
    });
    try { requireCleanExit(await Promise.race([exited, missingExit])); }
    finally { clearTimeout(forceStop); clearTimeout(deadline); }
  }

  private storeParticipants(peers: ScopedChangeService[]): ScopedChangeService[] {
    const identity = (directory: string) => process.platform === "win32" ? resolve(directory).toLowerCase() : resolve(directory);
    const source = identity(this.directory);
    return [...new Set([this, ...peers, ...[...ScopedChangeService.instances].filter(service => identity(service.directory) === source)])];
  }

  /**
   * One finalization per store: quiesce browser/gates, observe the full oracle,
   * attempt every peer's close, retain every outcome, then capture only if clean.
   * The caller's runner still seals the complete capture before returning.
   */
  async finalizeClosedStore(destination: string, expected: Project | undefined, peers: ScopedChangeService[] = [], prepare: (() => Promise<unknown>)[] = []): Promise<StoreFinalization> {
    const participants = this.storeParticipants(peers);
    const result: StoreFinalization = { schemaVersion: 1, oracle: null, preparation: [], closures: [], errors: [], capture: null };
    const prepared = await Promise.allSettled(prepare.map(action => Promise.resolve().then(action)));
    result.preparation = prepared.map((outcome, index) => outcome.status === "fulfilled"
      ? { index, status: "fulfilled" } : { index, status: "rejected", error: String(outcome.reason) });
    result.errors.push(...result.preparation.flatMap(outcome => outcome.error ? [outcome.error] : []));
    try {
      if (!expected) throw new Error("No final Project oracle was observed by the test before closure");
      // A final read drains each running peer's earlier JSONL requests; closed
      // peers are not restarted just to make evidence appear complete.
      for (const service of participants) if (service !== this && service.child) {
        if (!isDeepStrictEqual(await service.project(expected.id), expected)) throw new Error("A peer's final stored Project differs from the expected Project");
      }
      result.oracle = await this.observeStoredProject(expected);
    } catch (error) { result.errors.push(String(error)); }
    const closed = await Promise.allSettled(participants.map(service => service.close()));
    result.closures = closed.map((outcome, index) => ({ databasePath: participants[index].databasePath,
      status: outcome.status, ...(outcome.status === "rejected" ? { error: String(outcome.reason) } : {}),
      exits: structuredClone(participants[index].processExits), taints: structuredClone(participants[index].lifecycleTaints) }));
    result.errors.push(...result.closures.flatMap(outcome => outcome.error ? [outcome.error] : []));
    await mkdir(dirname(destination), { recursive: true });
    const outcomePath = destination + "-outcomes.json";
    await writeFile(outcomePath, JSON.stringify(result, null, 2), { flag: "wx" });
    if (result.errors.length) throw new AggregateError(result.errors.map(error => new Error(error)), "Store finalization is incomplete; all peer close outcomes were retained");
    try {
      result.capture = await this.captureClosedStore(destination, participants);
      await writeFile(join(destination, "oracle.json"), JSON.stringify(result.oracle, null, 2), { flag: "wx" });
    } catch (error) {
      result.errors.push(String(error));
      throw error;
    } finally { await writeFile(outcomePath, JSON.stringify(result, null, 2)); }
    return result;
  }

  /**
   * Capture a quiescent, complete test store in a new exclusive directory.
   * All supplied peers and every known helper sharing this store directory must
   * have clean exits. Hashes detect any source change during copying. This does
   * not claim to discover unrelated external processes or open SQLite here.
   * WAL/SHM, when present, are copied and hashed like every other regular file.
   * The runner must archive this capture before its execution returns, then
   * verify/extract/reopen a separate copy; never normalize the original store.
   */
  async captureClosedStore(destination: string, peers: ScopedChangeService[] = []): Promise<ClosedStoreCapture> {
    if (!isAbsolute(destination)) throw new Error("Closed store capture needs an absolute destination");
    const sourceDirectory = resolve(this.directory);
    const target = resolve(destination);
    const relation = relative(sourceDirectory, target);
    const outside = relation === ".." || relation.startsWith("../") || relation.startsWith("..\\") || isAbsolute(relation);
    if (!outside) {
      throw new Error("Closed store capture destination must be outside its source directory");
    }
    const participants = this.storeParticipants(peers);
    for (const service of participants) {
      if (service.lifecycleTaints.length || service.pending.size || service.child || service.starting || service.closing || service.processExits.length === 0 ||
          service.processExits.some(exit => exit.code !== 0 || exit.signal !== null)) {
        throw new Error(`Close every service peer cleanly and without lifecycle taint before capturing ${service.databasePath}`);
      }
    }
    const before = await storeTree(sourceDirectory);
    await mkdir(dirname(target), { recursive: true });
    await mkdir(target); // Fails if anything already occupies this destination.
    const snapshotDirectory = join(target, "store");
    await cp(sourceDirectory, snapshotDirectory, {
      recursive: true, force: false, errorOnExist: true, dereference: false, preserveTimestamps: true,
    });
    const copied = await storeTree(snapshotDirectory);
    const after = await storeTree(sourceDirectory);
    const beforeJson = JSON.stringify(before);
    const afterJson = JSON.stringify(after);
    const copiedJson = JSON.stringify(copied);
    if (beforeJson !== afterJson || beforeJson !== copiedJson) {
      throw new Error("Closed store capture differs from its source, or the source changed; evidence is incomplete");
    }
    const digest = (text: string) => createHash("sha256").update(text).digest("hex");
    const capture: ClosedStoreCapture = {
      schemaVersion: 1, capturedAt: new Date().toISOString(), sourceDirectory, snapshotDirectory,
      databaseRelativePath: relative(sourceDirectory, this.databasePath).split("\\").join("/"),
      entries: before, sourceBeforeSha256: digest(beforeJson), sourceAfterSha256: digest(afterJson),
      snapshotSha256: digest(copiedJson),
      lifecycle: [...participants].map(service => ({ databasePath: service.databasePath,
        exits: service.processExits.map(exit => ({ ...exit })), taints: structuredClone(service.lifecycleTaints) })),
      evidenceScope: "complete_closed_store_copy_with_verified_helper_exits; unopened_snapshot; later_archive_reopen_verification_required",
    };
    await writeFile(join(target, "manifest.json"), JSON.stringify(capture, null, 2), { flag: "wx" });
    return capture;
  }

  /** Call after peers close; only helper-created temporary directories are removed. */
  async dispose(): Promise<void> {
    await this.close();
    if (this.ownsDirectory) await rm(this.directory, { recursive: true, force: true });
  }
}
