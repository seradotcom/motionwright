/**
 * Playwright transport to the real, opt-in StudioService JSONL test executable.
 * SQLite, CAS, grants, changes and journal responses all come from that process.
 * The injected invoke seam is test transport; it does not certify Tauri, an
 * installer, a native renderer, or creative acceptance. No command is mocked.
 */
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { access, mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import type { Page } from "@playwright/test";
import type { Bootstrap, Change, EffectGrantReceipt, Project, ProjectEvent } from "../../src/types";

type RpcId = string | number;
type InvokeArgs = Record<string, unknown>;

export interface ServiceRpcRequest {
  id?: RpcId;
  command: string;
  args?: InvokeArgs;
}

export type ServiceRpcResponse<T = unknown> =
  | { id: RpcId; ok: true; result: T }
  | { id: RpcId; ok: false; error: string };

export interface ServiceBootstrap extends Bootstrap {
  test_transport: "service-test-jsonl";
  evidence_scope: string;
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

export interface ScopedChangeServiceOptions {
  /** Existing or new absolute test SQLite path; never deleted by this helper. */
  databasePath?: string;
  timeoutMs?: number;
  /** A peer/reopen should not create a new fixture in an empty database. */
  seedFixture?: boolean;
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

export class ScopedChangeService {
  readonly databasePath: string;
  readonly directory: string;
  readonly binaryPath: string;
  readonly transcript: ServiceTranscriptEntry[] = [];
  private readonly timeoutMs: number;
  private readonly seedFixture: boolean;
  private readonly ownsDirectory: boolean;
  private child: ChildProcessWithoutNullStreams | null = null;
  private starting: Promise<this> | null = null;
  private closing: Promise<void> | null = null;
  private exited: Promise<void> | null = null;
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
    if (!Number.isFinite(this.timeoutMs) || this.timeoutMs <= 0) {
      throw new Error("Service RPC timeoutMs must be a positive finite number");
    }
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
      // fixture/database contract before the browser attaches.
      await service.bootstrap();
      return service;
    } catch (error) {
      await service.close();
      if (service.ownsDirectory) await rm(service.directory, { recursive: true, force: true });
      throw error;
    }
  }

  get stderr(): string { return this.stderrText; }

  private failure(message: string): Error {
    const tail = this.stderrText.trim();
    return new Error(`${message}${tail ? `\nService stderr:\n${tail}` : ""}`);
  }

  private rejectPending(error: Error): void {
    for (const pending of this.pending.values()) {
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
      if (response.ok ? !("result" in response) : typeof response.error !== "string") {
        throw new Error("Response lacks its declared result/error payload");
      }
      this.transcript.push({ at: new Date().toISOString(), processId: child.pid, direction: "response", payload: response });
      const key = JSON.stringify(response.id);
      if (this.timedOutIds.delete(key)) return;
      const pending = this.pending.get(key);
      if (!pending) throw new Error(`Unexpected response id ${key}`);
      this.pending.delete(key);
      clearTimeout(pending.timer);
      pending.resolve(response);
    } catch (error) {
      this.lastExit = `Invalid service JSONL response: ${String(error)}`;
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
    const child = spawn(this.binaryPath, args, { stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
    this.child = child;
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk: string) => { this.stderrText = (this.stderrText + chunk).slice(-16_384); });
    const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
    lines.on("line", line => this.receive(child, line));
    this.exited = new Promise<void>(resolveExit => {
      child.once("close", (code, signal) => {
        lines.close();
        if (this.child === child) {
          this.child = null;
          this.lastExit = `StudioService test process exited (code ${code}, signal ${signal ?? "none"})`;
          this.rejectPending(this.failure(this.lastExit));
        }
        resolveExit();
      });
    });
    child.on("error", error => {
      this.lastExit = `Cannot run StudioService test process: ${error.message}`;
      this.rejectPending(this.failure(this.lastExit));
    });
    child.stdin.on("error", error => {
      this.rejectPending(this.failure(`StudioService stdin failed: ${error.message}`));
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
    if (!child || this.closing) throw this.failure(`StudioService is not running: ${this.lastExit}`);
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
    if (!response.ok) throw new Error(`${command}: ${response.error}`);
    return response.result;
  }

  async bootstrap(): Promise<ServiceBootstrap> {
    const response = await this.invoke<ServiceBootstrap>("bootstrap");
    if (response.test_transport !== "service-test-jsonl" || response.fixture?.label !== "synthetic_test_fixture") {
      throw new Error("The executable did not identify the real-service synthetic test transport");
    }
    return response;
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

  async apply(project: Project, change: Change): Promise<Project> {
    const requestId = randomUUID();
    const scope = { project_id: project.id, generation: project.generation, revision: project.revision };
    const grant = await this.invoke<EffectGrantReceipt>("issue_effect_grant", { request: { effect: "project_edit", ...scope, subject: requestId } });
    return this.invoke<Project>("apply_change", { request: { ...scope, request_id: requestId, effect_grant: grant.token, change } });
  }

  /** An independent process and grant registry, sharing only the SQLite/CAS. */
  peer(): Promise<ScopedChangeService> {
    return ScopedChangeService.create({ databasePath: this.databasePath, timeoutMs: this.timeoutMs, seedFixture: false });
  }

  /** Install before page.goto; every invoke crosses into the real process. */
  async attachPage(page: Page): Promise<void> {
    const binding = `__motionwright_service_rpc_${randomUUID().replaceAll("-", "")}`;
    await page.exposeBinding(binding, async (_source, command: string, args?: InvokeArgs) => this.invoke(command, args));
    await page.addInitScript(({ name }) => {
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
    }, { name: binding });
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
    if (!child || !exited) return;
    this.rejectPending(this.failure("StudioService test session closed before pending RPCs finished"));
    child.stdin.end();
    const forceStop = setTimeout(() => child.kill("SIGKILL"), 2_000);
    try { await exited; }
    finally { clearTimeout(forceStop); }
  }

  /** Call after peers close; only helper-created temporary directories are removed. */
  async dispose(): Promise<void> {
    await this.close();
    if (this.ownsDirectory) await rm(this.directory, { recursive: true, force: true });
  }
}
