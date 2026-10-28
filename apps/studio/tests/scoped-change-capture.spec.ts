import { type ChildProcessWithoutNullStreams } from "node:child_process";
import { once } from "node:events";
import { createHash } from "node:crypto";
import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import type { Project } from "../src/types";
import { expect, test, type TestInfo } from "@playwright/test";
import { ScopedChangeService, type ServiceClosureBarrier, type ServiceClosureReceipt, type ServiceLaunchContext, type ServiceProcessOwner, type ServiceRawStreamCapture, type StoreFinalization } from "./support/scopedChangeService";

// Transport faults and deliberately damaged copies of real SQLite stores are
// explicit negative fixtures. RPC responses and process results are real.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Build studio_review_session and set its absolute path; there is no simulated service fallback.");

function childOf(service: ScopedChangeService): ChildProcessWithoutNullStreams {
  // Deliberate test-only fault injection into the owned process's pipes/events.
  const child = (service as unknown as { child: ChildProcessWithoutNullStreams | null }).child;
  if (!child) throw new Error("The fault fixture requires a running real service");
  return child;
}
async function create(testInfo: TestInfo, timeoutMs = 10_000, processOwner?: ServiceProcessOwner, rawStreamCapture?: ServiceRawStreamCapture) {
  return ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3"), timeoutMs, processOwner, rawStreamCapture });
}
async function record(testInfo: TestInfo, services: ScopedChangeService[], observed?: unknown) {
  const path = testInfo.outputPath("capture-lifecycle-negative.json");
  await writeFile(path, JSON.stringify({
    scope: "real_service_transport_fault_injection; not_a_clean_store_certificate",
    observed,
    services: services.map(service => ({ databasePath: service.databasePath,
      exits: service.processExits, taints: service.lifecycleTaints, transcript: service.transcript, stderr: service.stderr })),
  }, null, 2));
  await testInfo.attach("capture-lifecycle-negative", { path, contentType: "application/json" });
}
async function rejectedCapture(service: ScopedChangeService, testInfo: TestInfo, name: string) {
  await expect(service.captureClosedStore(testInfo.outputPath(name))).rejects.toThrow(/cleanly.*lifecycle taint/);
}

function withDatabase<T>(path: string, action: (database: DatabaseSync) => T): T {
  const database = new DatabaseSync(path);
  try { return action(database); }
  finally { database.close(); }
}


/** Deliberate corruption of our isolated v6 fixture, never a production writer grant. */
function withCorruptibleV6Database<T>(path: string, action: (database: DatabaseSync) => T): T {
  return withDatabase(path, database => {
    expect(database.prepare("PRAGMA user_version").get()?.user_version).toBe(6);
    database.function("motionwright_writer_epoch_guard_v6", { deterministic: true }, () => 6n);
    return action(database);
  });
}

/** Existing portable restore creates its own managed CAS root and fresh authority. */
async function copyProjectThroughBundle(source: ScopedChangeService, target: ScopedChangeService, project: Project, path: string): Promise<Project> {
  const before = await source.observeStoredProject(project);
  await source.exportBundle(project, path);
  const plan = await target.inspectBundle(path);
  const restored = await target.importBundle(path, plan.fingerprint);
  expect(restored.generation).not.toBe(project.generation);
  // Core restore deliberately renews these two instance metadata fields.
  expect(Number.isFinite(Date.parse(restored.updated_at))).toBe(true);
  expect({ ...restored, generation: project.generation, updated_at: project.updated_at }).toEqual(project);
  const after = await target.observeStoredProject(restored);
  expect(after.journal).toEqual(before.journal);
  return restored;
}

function authorityRows(database: DatabaseSync) {
  return Object.fromEntries(["projects", "events", "receipts", "production_receipts"].map(table =>
    [table, database.prepare(`SELECT * FROM ${table} ORDER BY rowid`).all()]));
}

for (const damage of ["middle", "tail", "all"] as const) {
  test(`a ${damage} journal loss in an isolated SQLite copy cannot receive a complete oracle`, async ({}, testInfo) => {
    const original = await create(testInfo);
    let damaged: ScopedChangeService | undefined;
    try {
      let final = (await original.fixtureBootstrap()).project;
      for (let n = 0; n < 3; n++) final = await original.apply(final, { type: "rename_project", title: `Journal continuity ${n}` });
      damaged = await ScopedChangeService.create({ databasePath: testInfo.outputPath("damaged-copy", "studio.sqlite3"), seedFixture: false });
      const copiedProject = await copyProjectThroughBundle(original, damaged, final, testInfo.outputPath("journal-source-bundle"));
      await original.close();
      await original.captureClosedStore(testInfo.outputPath("original-closed-store"));
      const originalBytes = await readFile(original.databasePath);
      await damaged.close();
      const deleted = withCorruptibleV6Database(damaged.databasePath, database => {
        const revision = damage === "middle" ? 2 : final.revision;
        return damage === "all"
          ? database.prepare("DELETE FROM events WHERE project_id=?").run(final.id).changes
          : database.prepare("DELETE FROM events WHERE project_id=? AND revision=?").run(final.id, revision).changes;
      });
      expect(Number(deleted)).toBe(damage === "all" ? final.revision : 1);
      await damaged.start();
      expect(await damaged.project(copiedProject.id)).toEqual(copiedProject);
      await expect(damaged.observeStoredProject(copiedProject)).rejects.toThrow(damage === "middle" ? /journal has a gap/ : /journal is truncated/);
      const destination = testInfo.outputPath("rejected-damaged-finalization");
      await expect(damaged.finalizeClosedStore(destination, copiedProject)).rejects.toThrow("Store finalization is incomplete");
      const outcome = JSON.parse(await readFile(destination + "-outcomes.json", "utf8")) as StoreFinalization;
      expect(outcome.oracle).toBeNull(); expect(outcome.capture).toBeNull();
      expect(outcome.closures.every(item => item.status === "fulfilled")).toBe(true);
      expect(await readFile(original.databasePath)).toEqual(originalBytes);
    } finally {
      const services = [original, ...(damaged ? [damaged] : [])];
      await Promise.allSettled(services.map(item => item.close()));
      await record(testInfo, services, { fixture: "deliberate_journal_loss_in_separate_sqlite_copy", damage });
    }
  });
}

for (const damage of ["foreign_identity", "invalid_json"] as const) {
  test(`a ${damage} project row cannot redirect reads or mutations and healthy projects remain usable`, async ({ page }, testInfo) => {
    const original = await create(testInfo);
    let damaged: ScopedChangeService | undefined;
    try {
      let first = (await original.fixtureBootstrap()).project;
      const intent = { request_id: crypto.randomUUID(), title: "Healthy independent project" };
      const creationGrant = await original.invoke<{ token: string }>("issue_project_creation_grant", { request: intent });
      let second = (await original.invoke<{ project: typeof first }>("create_project", { request: { intent, effect_grant: creationGrant.token } })).project;
      damaged = await ScopedChangeService.create({ databasePath: testInfo.outputPath("damaged-copy", "studio.sqlite3"), seedFixture: false, desktopInvokeFixture: true });
      first = await copyProjectThroughBundle(original, damaged, first, testInfo.outputPath("first-project-bundle"));
      second = await copyProjectThroughBundle(original, damaged, second, testInfo.outputPath("second-project-bundle"));
      await original.close();
      await original.captureClosedStore(testInfo.outputPath("original-closed-store"));
      const originalBytes = await readFile(original.databasePath);
      const copiedDatabase = damaged.databasePath;
      const requestId = crypto.randomUUID();
      const grant = await damaged.grant("project_edit", first, requestId);
      // The real grant predates a separate committed write to the isolated
      // copy. This exercises an association changing before the apply RPC.
      const expectedRows = withCorruptibleV6Database(copiedDatabase, database => {
        const document = damage === "foreign_identity" ? JSON.stringify(second) : "{ damaged document";
        expect(database.prepare("UPDATE projects SET document_json=? WHERE id=?").run(document, first.id).changes).toBe(1);
        return authorityRows(database);
      });
      const rejected = damage === "foreign_identity" ? /stored project authority columns differ from the document/ : /could not be opened/;
      await expect(damaged.project(first.id)).rejects.toThrow(rejected);
      await expect(damaged.grant("project_edit", first, crypto.randomUUID())).rejects.toThrow(rejected);
      await expect(damaged.invoke("apply_change", { request: {
        project_id: first.id, generation: first.generation, revision: first.revision,
        request_id: requestId, effect_grant: grant.token, change: { type: "rename_project", title: "Must never be applied" },
      } })).rejects.toThrow(rejected);
      await damaged.attachPage(page, null); await page.goto("/");
      const library = page.getByRole("dialog", { name: "Project library", exact: true });
      await expect(library).toBeVisible();
      await library.getByRole("button", { name: "Open " + first.title, exact: true }).click();
      await expect(library.getByRole("alert")).toContainText("The saved page could not be read because its request or content is invalid.");
      await library.getByRole("button", { name: "Open " + second.title, exact: true }).click();
      await expect(page.locator(".command-bar .revision-chip")).toHaveText("r0");
      expect(await damaged.project(second.id)).toEqual(second);
      await expect(damaged.reopen(first.id)).rejects.toThrow(rejected);
      await page.close(); await damaged.close();
      expect(withDatabase(copiedDatabase, authorityRows)).toEqual(expectedRows);
      expect(await readFile(original.databasePath)).toEqual(originalBytes);
    } finally {
      await page.close();
      const services = [original, ...(damaged ? [damaged] : [])];
      await Promise.allSettled(services.map(item => item.close()));
      await record(testInfo, services, { fixture: "deliberately_damaged_project_row_in_separate_sqlite_copy", damage });
    }
  });
}

test("complete capture includes every file and a paginated full oracle after all known peers close", async ({}, testInfo) => {
  test.setTimeout(60_000);
  let admissionsSealed = false, externalClosed = false, clientCount = 0;
  let serviceReceipt: ServiceClosureReceipt | null = null;
  const clientContexts: Readonly<ServiceLaunchContext>[] = [];
  // This owner fixture checks the actual JSONL barrier, not Host termination.
  const owner: ServiceProcessOwner = {
    async environmentForNewClient(context) {
      if (admissionsSealed) throw new Error("Client admissions sealed");
      expect(Object.isFrozen(context)).toBe(true);
      clientContexts.push(context); clientCount++; return {};
    },
    sealClientAdmissions() { admissionsSealed = true; },
    async closeAfterClients(barrier) {
      expect(barrier.closed).toBe(true);
      if (!barrier.receipt_path) throw new Error("Missing actual service closure receipt");
      const original = await readFile(barrier.receipt_path);
      expect(createHash("sha256").update(original).digest("hex")).toBe(barrier.receipt_sha256);
      serviceReceipt = JSON.parse(original.toString("utf8")) as ServiceClosureReceipt;
      expect(serviceReceipt.status).toBe("CLOSED"); externalClosed = true;
    },
    assertClosed() { if (!externalClosed) throw new Error("External callback did not finish"); },
  };
  const service = await create(testInfo, 10_000, owner);
  let peer: ScopedChangeService | undefined;
  let finalized = false;
  try {
    const initial = (await service.fixtureBootstrap()).project;
    const initialHistory = await service.observeStoredProject(initial);
    peer = await service.peer();
    await rejectedCapture(service, testInfo, "denied-live-peer");
    let final = initial;
    // Cross the transport's 100-event page size, using real grants and commits.
    for (let index = 0; index < 101; index++) {
      final = await service.apply(final, { type: "rename_project", title: `Capture pagination fixture ${index + 1}` });
    }
    // Opaque auxiliary files exercise the unfiltered tree copier, including
    // sidecar-shaped names. They are not fabricated SQLite WAL/SHM for this DB.
    await writeFile(join(service.directory, "opaque.sqlite3-wal"), Buffer.from([0, 255, 1, 2, 3]));
    await writeFile(join(service.directory, "opaque.sqlite3-shm"), Buffer.from([9, 0, 8, 7]));
    await service.close(); await service.start(); // Actual new launch and reader lifetime.
    const result = await service.finalizeClosedStore(testInfo.outputPath("closed-store-1"), final, [peer]);
    finalized = true;
    expect(result.errors).toEqual([]);
    expect(clientCount).toBe(3); expect(admissionsSealed).toBe(true); expect(externalClosed).toBe(true);
    expect(clientContexts).toEqual([
      { helper_id: service.helperId, launch_index: 0, binary_path: service.binaryPath, database_path: service.databasePath },
      { helper_id: peer.helperId, launch_index: 0, binary_path: peer.binaryPath, database_path: peer.databasePath },
      { helper_id: service.helperId, launch_index: 1, binary_path: service.binaryPath, database_path: service.databasePath },
    ]);
    const receipt = serviceReceipt as ServiceClosureReceipt | null;
    if (!receipt) throw new Error("Owner did not receive the actual service barrier");
    expect(receipt.schema).toBe("motionwright.service-closure/1");
    expect(receipt.sealed_helper_ids).toEqual(receipt.participants.map(item => item.helper_id).sort());
    expect(receipt.participants.map(item => item.launch_count).sort()).toEqual([1, 2]);
    expect(receipt.phases).toEqual({ preparation: [], oracle: "fulfilled", owner_admission_seal: ["fulfilled"] });
    for (const item of receipt.participants) for (const stream of item.process_streams) {
      expect(clientContexts.filter(context => context.helper_id === item.helper_id
        && context.launch_index === stream.launch_index)).toEqual([{
          helper_id: item.helper_id, launch_index: stream.launch_index,
          binary_path: item.binary_path, database_path: item.database_path,
        }]);
      expect(stream).toMatchObject({ stdout_ended: true, stderr_ended: true, jsonl_reader_closed: true });
      expect(item.process_exits[stream.exit_index!]).toMatchObject({ process_id: stream.process_id, code: 0, signal: null });
    }
    await expect(service.peer()).rejects.toThrow(/admissions are sealed/);
    await expect(service.start()).rejects.toThrow(/admissions are sealed/);
    await expect(ScopedChangeService.create({ databasePath: service.databasePath, binaryPath: service.binaryPath })).rejects.toThrow(/admissions are sealed/);
    expect(clientCount).toBe(3);
    expect(result.closures).toHaveLength(2);
    expect(result.closures.every(outcome => outcome.status === "fulfilled" && outcome.taints.length === 0 && outcome.exits.every(exit => exit.code === 0 && exit.signal === null))).toBe(true);
    expect(result.oracle?.project).toEqual(final);
    expect(result.oracle?.journalComplete).toBe(true);
    expect(result.oracle?.journal).toHaveLength(initialHistory.journal.length + 101);
    expect(result.oracle?.journal.at(-1)?.revision).toBe(final.revision);
    const capture = result.capture!;
    expect(capture.sourceBeforeSha256).toBe(capture.snapshotSha256);
    expect(capture.sourceAfterSha256).toBe(capture.snapshotSha256);
    expect(capture.entries.some(entry => entry.path === "opaque.sqlite3-wal")).toBe(true);
    expect(capture.entries.some(entry => entry.path === "opaque.sqlite3-shm")).toBe(true);
    expect(await readFile(join(capture.snapshotDirectory, "opaque.sqlite3-wal"))).toEqual(Buffer.from([0, 255, 1, 2, 3]));
    expect(await readFile(join(capture.snapshotDirectory, "opaque.sqlite3-shm"))).toEqual(Buffer.from([9, 0, 8, 7]));
    expect(JSON.parse(await readFile(testInfo.outputPath("closed-store-1", "oracle.json"), "utf8"))).toEqual(result.oracle);
    await expect(service.captureClosedStore(testInfo.outputPath("closed-store-1"), [peer])).rejects.toThrow();
  } finally {
    if (!finalized) {
      const closed = await Promise.allSettled([service.close(), ...(peer ? [peer.close()] : [])]);
      await record(testInfo, peer ? [service, peer] : [service], closed.map(outcome => outcome.status));
    }
  }
});

test("pending at close remains tainted despite exit zero and a later clean restart", async ({}, testInfo) => {
  const service = await create(testInfo);
  try {
    const initial = (await service.fixtureBootstrap()).project;
    // rawRequest registers its pending entry synchronously before close sends EOF.
    const requested = service.project(initial.id).then(
      value => ({ status: "fulfilled", value }), error => ({ status: "rejected", error: String(error) }));
    await expect(service.close()).rejects.toThrow("lifecycle is tainted");
    expect((await requested).status).toBe("rejected");
    expect(service.lifecycleTaints.some(taint => taint.kind === "pending_at_close")).toBe(true);
    expect(service.processExits.at(-1)).toMatchObject({ code: 0, signal: null });
    await rejectedCapture(service, testInfo, "denied-pending-close");
    const taints = structuredClone(service.lifecycleTaints);
    await service.start();
    expect(await service.project(initial.id)).toEqual(initial);
    await expect(service.close()).rejects.toThrow("lifecycle is tainted");
    expect(service.processExits).toHaveLength(2);
    expect(service.processExits.every(exit => exit.code === 0 && exit.signal === null)).toBe(true);
    expect(service.lifecycleTaints).toEqual(taints);
    await rejectedCapture(service, testInfo, "denied-after-restart");
  } finally {
    await Promise.allSettled([service.close()]);
    await record(testInfo, [service]);
  }
});

test("a timed out real response followed by exit zero never becomes clean capture evidence", async ({}, testInfo) => {
  const service = await create(testInfo, 500);
  try {
    const initial = (await service.fixtureBootstrap()).project;
    const child = childOf(service);
    child.stdout.pause();
    try { await expect(service.project(initial.id)).rejects.toThrow("timed out"); }
    finally { child.stdout.resume(); }
    // Observe the subsequent real response after the delayed one is drained.
    expect(await service.project(initial.id)).toEqual(initial);
    expect(service.lifecycleTaints.some(taint => taint.kind === "request_timeout")).toBe(true);
    await expect(service.close()).rejects.toThrow("lifecycle is tainted");
    expect(service.processExits.at(-1)).toMatchObject({ code: 0, signal: null });
    await rejectedCapture(service, testInfo, "denied-timeout-zero");
  } finally {
    await Promise.allSettled([service.close()]);
    await record(testInfo, [service]);
  }
});

test("a real stdin transport error is permanent even when EOF lets the process exit zero", async ({}, testInfo) => {
  const service = await create(testInfo);
  try {
    const child = childOf(service);
    const pipeError = once(child.stdin, "error");
    child.stdin.destroy(new Error("Injected owned-pipe failure for capture admission"));
    await pipeError;
    await expect(service.close()).rejects.toThrow("lifecycle is tainted");
    expect(service.processExits.at(-1)).toMatchObject({ code: 0, signal: null });
    expect(service.lifecycleTaints.some(taint => taint.kind === "transport_failure")).toBe(true);
    await rejectedCapture(service, testInfo, "denied-transport-zero");
  } finally {
    await Promise.allSettled([service.close()]);
    await record(testInfo, [service]);
  }
});

test("a broken response pipe records the real nonzero exit and vetoes capture", async ({}, testInfo) => {
  const service = await create(testInfo, 1_000);
  try {
    const initial = (await service.fixtureBootstrap()).project;
    const child = childOf(service);
    // The Rust writer returns an error when its actual output pipe is closed.
    child.stdout.destroy();
    await expect(service.project(initial.id)).rejects.toThrow();
    await expect(service.close()).rejects.toThrow("did not close cleanly");
    expect(typeof service.processExits.at(-1)?.code).toBe("number");
    expect(service.processExits.at(-1)?.code).not.toBe(0);
    expect(service.lifecycleTaints.some(taint => taint.kind === "unclean_exit")).toBe(true);
    await rejectedCapture(service, testInfo, "denied-nonzero");
  } finally {
    await Promise.allSettled([service.close()]);
    await record(testInfo, [service]);
  }
});

test("an unobserved process close cannot be promoted to proof by a later timeout", async ({}, testInfo) => {
  const service = await create(testInfo);
  const child = childOf(service);
  // Remove only this test process's helper observation; do not fabricate a
  // replacement exit in the helper. The outside observer records reality.
  // The helper adds its observer after spawn hooks; preserve any earlier
  // runner instrumentation. The empty processExits assertion below verifies
  // that this fault actually removed the helper's observation.
  const observer = child.listeners("close").at(-1);
  if (!observer) throw new Error("No helper close observer was installed");
  child.removeListener("close", observer as (...args: unknown[]) => void);
  const actualClose = once(child, "close");
  const closing = service.close();
  const observed = await actualClose;
  await expect(closing).rejects.toThrow("no observed exit");
  expect(observed).toEqual([0, null]);
  expect(service.processExits).toEqual([]);
  expect(service.lifecycleTaints.some(taint => taint.kind === "unobserved_exit")).toBe(true);
  await rejectedCapture(service, testInfo, "denied-unobserved-exit");
  await record(testInfo, [service], { externalClose: observed, helperCloseMissing: true });
});

test("finalization attempts every peer and retains every outcome when preparation and one close fail", async ({}, testInfo) => {
  let peer: ScopedChangeService | undefined;
  let externalClosed = false, externalCloseCalls = 0, clientEnvironmentCalls = 0, admissionsSealed = false;
  // This callback fixture tests cleanup ordering only. It creates no Host,
  // authority, Graph context or native success; both service processes are real.
  const owner: ServiceProcessOwner = {
    async environmentForNewClient() { if (admissionsSealed) throw new Error("Client admissions sealed"); clientEnvironmentCalls += 1; return {}; },
    sealClientAdmissions() { admissionsSealed = true; },
    async closeAfterClients() {
      externalCloseCalls += 1;
      if (!service.processExits.length || !peer?.processExits.length)
        throw new Error("External close ran before every real client exit was observed");
      externalClosed = true;
    },
    assertClosed() { if (!externalClosed) throw new Error("External closure was not observed"); },
  };
  const service = await create(testInfo, 10_000, owner);
  let attemptedSecondPreparation = false;
  try {
    peer = await service.peer();
    const final = (await service.fixtureBootstrap()).project;
    const child = childOf(peer);
    const pipeError = once(child.stdin, "error");
    child.stdin.destroy(new Error("Injected peer pipe failure"));
    await pipeError;
    await expect(peer.close()).rejects.toThrow("lifecycle is tainted");
    const destination = testInfo.outputPath("denied-finalization");
    await expect(service.finalizeClosedStore(destination, final, [peer], [
      async () => { throw new Error("Injected preparation failure"); },
      async () => { attemptedSecondPreparation = true; },
    ])).rejects.toThrow("all peer close outcomes were retained");
    const result = JSON.parse(await readFile(destination + "-outcomes.json", "utf8")) as StoreFinalization;
    expect(attemptedSecondPreparation).toBe(true);
    expect(result.preparation.map(outcome => outcome.status)).toEqual(["rejected", "fulfilled"]);
    expect(clientEnvironmentCalls).toBe(2);
    expect(externalCloseCalls).toBe(1); expect(externalClosed).toBe(true);
    expect(result.externalClosures).toEqual([{ index: 0, status: "fulfilled" }]);
    expect(result.oracle?.project).toEqual(final);
    expect(result.oracle?.journalComplete).toBe(true);
    expect(result.closures.map(outcome => outcome.status)).toEqual(["fulfilled", "rejected"]);
    expect(result.closures.every(outcome => outcome.exits.some(exit => exit.code === 0 && exit.signal === null))).toBe(true);
    expect(result.capture).toBeNull();
    expect(result.errors.some(error => error.includes("preparation failure"))).toBe(true);
    expect(result.errors.some(error => error.includes("lifecycle is tainted"))).toBe(true);
    await rejectedCapture(service, testInfo, "denied-tainted-peer");
  } finally {
    await Promise.allSettled([service.close(), ...(peer ? [peer.close()] : [])]);
    await record(testInfo, peer ? [service, peer] : [service]);
  }
});

for (const phase of ["preparation", "client", "external"] as const) {
  test(`finalization rejects an empty-message ${phase} failure after actual service closure`, async ({}, testInfo) => {
    let externalClosed = false, externalCalls = 0, admissionsSealed = false;
    // Only the rejection is injected; the service process and its close are real.
    const owner: ServiceProcessOwner = {
      async environmentForNewClient() { if (admissionsSealed) throw new Error("Client admissions sealed"); return {}; },
      sealClientAdmissions() { admissionsSealed = true; },
      async closeAfterClients() { externalCalls++; externalClosed = true; if (phase === "external") throw ""; },
      assertClosed() { if (!externalClosed) throw new Error("External callback did not finish"); },
    };
    const service = await create(testInfo, 10_000, owner), originalClose = service.close.bind(service);
    try {
      const final = (await service.fixtureBootstrap()).project;
      if (phase === "client") service.close = async () => { await originalClose(); throw ""; };
      const destination = testInfo.outputPath("empty-rejection-finalization");
      await expect(service.finalizeClosedStore(destination, final, [], phase === "preparation" ? [async () => { throw ""; }] : []))
        .rejects.toThrow("all peer close outcomes were retained");
      const result = JSON.parse(await readFile(destination + "-outcomes.json", "utf8")) as StoreFinalization;
      const selected = phase === "preparation" ? result.preparation : phase === "client" ? result.closures : result.externalClosures;
      expect(selected).toHaveLength(1); expect(selected[0]).toMatchObject({ status: "rejected", error: "" });
      expect(result.errors).toContain(""); expect(result.capture).toBeNull();
      expect(service.processExits).toHaveLength(1); expect(service.processExits[0]).toMatchObject({ code: 0, signal: null });
      expect(service.lifecycleTaints).toEqual([]); expect(externalClosed).toBe(true); expect(externalCalls).toBe(1);
      await expect(readFile(join(destination, "manifest.json"))).rejects.toMatchObject({ code: "ENOENT" });
    } finally {
      service.close = originalClose;
      await service.close(); await record(testInfo, [service], { phase, injectedRejection: "" });
    }
  });
}

for (const failure of ["missing EOF observation", "receipt already exists"] as const) {
  test(`service closure preserves UNKNOWN after ${failure} and still closes its owner`, async ({}, testInfo) => {
    let sealed = false, externalClosed = false, calls = 0, observed: ServiceClosureBarrier | undefined;
    const owner: ServiceProcessOwner = {
      async environmentForNewClient() { if (sealed) throw new Error("Client admissions sealed"); return {}; },
      sealClientAdmissions() { sealed = true; },
      async closeAfterClients(barrier) { observed = structuredClone(barrier); calls++; externalClosed = true; },
      assertClosed() { if (!externalClosed) throw new Error("External callback did not finish"); },
    };
    const service = await create(testInfo, 10_000, owner);
    try {
      const final = (await service.fixtureBootstrap()).project, destination = testInfo.outputPath("denied-service-barrier");
      let actualEnd: Promise<unknown> | undefined;
      if (failure === "missing EOF observation") {
        const child = childOf(service), observer = child.stdout.listeners("end").find(listener => listener.name === "observeStdoutEnd");
        if (!observer) throw new Error("Actual helper EOF observer was not installed");
        child.stdout.removeListener("end", observer as (...args: unknown[]) => void);
        actualEnd = once(child.stdout, "end");
      } else await writeFile(destination + "-service-closure.json", "Original conflicting receipt retained", { flag: "wx" });
      await expect(service.finalizeClosedStore(destination, final)).rejects.toThrow("all peer close outcomes were retained");
      if (actualEnd) await actualEnd;
      expect(calls).toBe(1); expect(externalClosed).toBe(true); expect(observed?.closed).toBe(false);
      expect(service.processExits).toHaveLength(1); expect(service.processExits[0]).toMatchObject({ code: 0, signal: null });
      if (failure === "receipt already exists") {
        expect(observed).toEqual({ closed: false, receipt_path: null, receipt_sha256: null });
        expect(await readFile(destination + "-service-closure.json", "utf8")).toBe("Original conflicting receipt retained");
      } else {
        if (!observed?.receipt_path) throw new Error("Missing failed closure receipt");
        const receipt = JSON.parse(await readFile(observed.receipt_path, "utf8")) as ServiceClosureReceipt;
        expect(receipt.status).toBe("UNKNOWN");
        expect(receipt.participants[0].process_streams[0]).toMatchObject({ stdout_ended: false, stderr_ended: true, jsonl_reader_closed: true });
      }
      const outcome = JSON.parse(await readFile(destination + "-outcomes.json", "utf8")) as StoreFinalization;
      expect(outcome.capture).toBeNull(); expect(outcome.errors.length).toBeGreaterThan(0);
      await expect(readFile(join(destination, "manifest.json"))).rejects.toMatchObject({ code: "ENOENT" });
    } finally { await service.close(); await record(testInfo, [service], { failure, observed }); }
  });
}
test("service closure prevents a reserved client from spawning after admissions seal", async ({}, testInfo) => {
  let sealed = false, count = 0, externalClosed = false;
  let resolveEnvironment!: (env: NodeJS.ProcessEnv) => void, waiting!: () => void;
  const clientWaiting = new Promise<void>(resolve => { waiting = resolve; });
  let barrier: ServiceClosureBarrier | undefined;
  const owner: ServiceProcessOwner = {
    async environmentForNewClient() {
      if (sealed) throw new Error("Client admissions sealed");
      if (++count === 1) return {};
      return new Promise<NodeJS.ProcessEnv>(resolve => { resolveEnvironment = resolve; waiting(); });
    },
    sealClientAdmissions() { sealed = true; resolveEnvironment({}); },
    async closeAfterClients(value) { barrier = structuredClone(value); externalClosed = true; },
    assertClosed() { if (!externalClosed) throw new Error("External callback did not finish"); },
  };
  const service = await create(testInfo, 10_000, owner);
  try {
    const final = (await service.fixtureBootstrap()).project;
    const peerResult = service.peer().then(peer => ({ peer, error: null }), (error: unknown) => ({ peer: null, error }));
    await clientWaiting;
    const destination = testInfo.outputPath("denied-late-spawn");
    await expect(service.finalizeClosedStore(destination, final)).rejects.toThrow("all peer close outcomes were retained");
    const peer = await peerResult; expect(peer.peer).toBeNull();
    expect(peer.error).toBeInstanceOf(AggregateError);
    expect((peer.error as AggregateError).errors.map(String)).toEqual(expect.arrayContaining([
      expect.stringMatching(/sealed before the process launch/),
      expect.stringMatching(/lifecycle is tainted/),
    ]));
    expect(count).toBe(2); expect(externalClosed).toBe(true); expect(barrier?.closed).toBe(false);
    if (!barrier?.receipt_path) throw new Error("Missing reserved-client closure receipt");
    const receipt = JSON.parse(await readFile(barrier.receipt_path, "utf8")) as ServiceClosureReceipt;
    expect(receipt.status).toBe("UNKNOWN"); expect(receipt.participants.map(item => item.launch_count).sort()).toEqual([0, 1]);
    expect(receipt.participants.flatMap(item => item.process_exits)).toHaveLength(1);
    expect(receipt.participants.every(item => item.active_child_count === 0 && item.starting_count === 0 && item.closing_count === 0 && item.pending_count === 0)).toBe(true);
  } finally { await service.close(); await record(testInfo, [service], { barrier }); }
});

test("service closure seals ordinary clients after real reopen and captures a separate verified copy", async ({}, testInfo) => {
  const service = await create(testInfo);
  try {
    const initial = (await service.fixtureBootstrap()).project;
    const saved = await service.apply(initial, { type: "rename_project", title: "Ordinary closure and real reopen" });
    const beforeRestart = await service.observeStoredProject(saved);
    await service.close(); await service.start();
    const reopened = await service.observeStoredProject(saved);
    expect(reopened.project).toEqual(beforeRestart.project); expect(reopened.journal).toEqual(beforeRestart.journal);
    const final = await service.apply(reopened.project, { type: "rename_project", title: "Editable after real restart" });
    const destination = testInfo.outputPath("ordinary-closed-store");
    const result = await service.finalizeClosedStore(destination, final);
    expect(result.errors).toEqual([]); expect(result.serviceClosure?.closed).toBe(true);
    const receipt = JSON.parse(await readFile(result.serviceClosure!.receipt_path!, "utf8")) as ServiceClosureReceipt;
    expect(receipt.status).toBe("CLOSED"); expect(receipt.phases.owner_admission_seal).toEqual([]);
    expect(receipt.sealed_helper_ids).toEqual(receipt.participants.map(item => item.helper_id).sort());
    await expect(service.start()).rejects.toThrow(/admissions are sealed/);
    await expect(service.peer()).rejects.toThrow(/admissions are sealed/);
    await expect(ScopedChangeService.create({ databasePath: service.databasePath, binaryPath: service.binaryPath })).rejects.toThrow(/admissions are sealed/);
    expect(service.processExits).toHaveLength(2); expect(service.processStreams).toHaveLength(2);
    const copiedDirectory = testInfo.outputPath("ordinary-reopen-copy"), capture = result.capture!;
    await cp(capture.snapshotDirectory, copiedDirectory, { recursive: true, errorOnExist: true, force: false });
    // A physical archive preserves CAS control identities as evidence. Inspect a
    // separate copy read-only; writable restore uses the inspected bundle API.
    const database = new DatabaseSync(join(copiedDirectory, capture.databaseRelativePath), { readOnly: true });
    try {
      expect(database.prepare("PRAGMA integrity_check").all()).toEqual([{ integrity_check: "ok" }]);
      expect(database.prepare("PRAGMA foreign_key_check").all()).toEqual([]);
      const row = database.prepare("SELECT document_json FROM projects WHERE id=?").get(final.id);
      expect(JSON.parse(String(row?.document_json))).toEqual(final);
      expect(database.prepare("SELECT count(*) AS count FROM events WHERE project_id=?").get(final.id)?.count).toBe(result.oracle!.journal.length);
    } finally { database.close(); }
    await writeFile(testInfo.outputPath("ordinary-reopen-original.json"), JSON.stringify({
      scope: "real_service_restart_then_edit; immutable_capture_inspected_readonly_on_separate_copy",
      service_closure: result.serviceClosure, beforeRestart, reopened, final, process_exits: service.processExits,
    }, null, 2), { flag: "wx" });
  } finally { await service.close(); }
});

test("an owner client reserved for a failed restart cannot reuse an earlier clean launch for capture", async ({}, testInfo) => {
  const reservations: Readonly<ServiceLaunchContext>[] = [];
  let sealed = false, ownerClosed = false, observed: ServiceClosureReceipt | null = null;
  const owner: ServiceProcessOwner = {
    async environmentForNewClient(context) {
      if (sealed) throw new Error("Client admissions sealed");
      reservations.push(context);
      if (reservations.length === 3) throw new Error("Original owner client response unavailable before restart spawn");
      return {};
    },
    sealClientAdmissions() { sealed = true; },
    async closeAfterClients(barrier) {
      expect(barrier.closed).toBe(false);
      if (barrier.receipt_path === null) throw new Error("The failed restart must retain its actual closure receipt");
      const bytes = await readFile(barrier.receipt_path);
      expect(createHash("sha256").update(bytes).digest("hex")).toBe(barrier.receipt_sha256);
      observed = JSON.parse(bytes.toString("utf8")) as ServiceClosureReceipt;
      ownerClosed = true;
    },
    assertClosed() { if (!ownerClosed) throw new Error("External cleanup did not finish"); },
  };
  const service = await create(testInfo, 10_000, owner);
  let peer: ScopedChangeService | undefined;
  try {
    const project = (await service.fixtureBootstrap()).project;
    peer = await service.peer();
    await service.close();
    expect(service.processExits).toHaveLength(1);
    expect(service.processExits[0]).toMatchObject({ code: 0, signal: null });
    await expect(service.start()).rejects.toThrow("Original owner client response unavailable before restart spawn");
    expect(reservations.map(context => [context.helper_id, context.launch_index])).toEqual([
      [service.helperId, 0], [peer.helperId, 0], [service.helperId, 1],
    ]);
    expect(service.processStreams).toHaveLength(1);
    expect(service.lifecycleTaints.some(taint => taint.kind === "transport_failure")).toBe(true);
    await expect(peer.finalizeClosedStore(testInfo.outputPath("failed-restart-store"), project, [service]))
      .rejects.toThrow("Store finalization is incomplete");
    expect(sealed).toBe(true); expect(ownerClosed).toBe(true);
    const receipt = observed as ServiceClosureReceipt | null;
    if (!receipt) throw new Error("External owner did not receive failed restart observations");
    expect(receipt.status).toBe("UNKNOWN");
    expect(receipt.sealed_helper_ids).toEqual([service.helperId, peer.helperId].sort());
    const failed = receipt.participants.find(item => item.helper_id === service.helperId)!;
    expect(failed.launch_count).toBe(1);
    expect(failed.process_exits).toHaveLength(1);
    expect(failed.close_status).toBe("rejected");
    expect(failed.taints).toHaveLength(1);
    expect(failed.taints[0]).toMatchObject({ kind: "transport_failure", process_id: null });
    await expect(service.start()).rejects.toThrow(/admissions are sealed/);
    expect(reservations).toHaveLength(3);
    await expect(readFile(testInfo.outputPath("failed-restart-store", "manifest.json"))).rejects.toThrow();
  } finally {
    await Promise.allSettled([service.close(), ...(peer ? [peer.close()] : [])]);
    await record(testInfo, peer ? [service, peer] : [service], { fixture: "real_service_failed_restart_reservation; excludes_Native_Host" });
  }
});

interface RawSinkObservation {
  context: Readonly<ServiceLaunchContext>;
  processId: number | null;
  stdout: Buffer[];
  stderr: Buffer[];
  nonBufferChunks: number;
  ended: boolean;
  settled: boolean;
}

/** Synthetic sink controls over real process bytes; never the Native shared pool. */
function rawSinkFixture(afterEnd: (row: RawSinkObservation) => Promise<void> = async () => undefined) {
  const rows: RawSinkObservation[] = [];
  const capture: ServiceRawStreamCapture = {
    bind(context, streams) {
      expect(Object.isFrozen(context)).toBe(true);
      expect(Object.isFrozen(streams)).toBe(true);
      const row: RawSinkObservation = { context, processId: streams.process_id,
        stdout: [], stderr: [], nonBufferChunks: 0, ended: false, settled: false };
      rows.push(row);
      for (const name of ["stdout", "stderr"] as const) streams[name].on("data", (chunk: unknown) => {
        if (Buffer.isBuffer(chunk)) row[name].push(Buffer.from(chunk));
        else row.nonBufferChunks++;
      });
      const settled = Promise.all([once(streams.stdout, "end"), once(streams.stderr, "end")]).then(async () => {
        row.ended = true;
        if (row.nonBufferChunks) throw new Error("Raw fixture received decoded chunks");
        await afterEnd(row);
        row.settled = true;
      });
      return { settled };
    },
  };
  return { rows, capture };
}

test("one raw capture port covers the ordinary initializer, owner-admitted peers and every restart", async ({}, testInfo) => {
  const raw = rawSinkFixture();
  const initializer = await create(testInfo, 10_000, undefined, raw.capture);
  const admissions: Readonly<ServiceLaunchContext>[] = [];
  let ownerClosed = false;
  let receipt: ServiceClosureReceipt | undefined;
  const owner: ServiceProcessOwner = {
    async environmentForNewClient(context) { admissions.push(context); return {}; },
    sealClientAdmissions() {},
    async closeAfterClients(barrier) {
      expect(raw.rows.every(row => row.ended && row.settled)).toBe(true);
      expect(barrier.closed).toBe(true);
      if (!barrier.receipt_path) throw new Error("Missing service closure receipt");
      receipt = JSON.parse(await readFile(barrier.receipt_path, "utf8")) as ServiceClosureReceipt;
      ownerClosed = true;
    },
    assertClosed() { if (!ownerClosed) throw new Error("Owner fixture was not closed"); },
  };
  let service: ScopedChangeService | undefined, peer: ScopedChangeService | undefined;
  try {
    const project = (await initializer.fixtureBootstrap()).project;
    await initializer.close();
    service = await ScopedChangeService.create({ databasePath: initializer.databasePath, seedFixture: false,
      processOwner: owner, rawStreamCapture: raw.capture });
    peer = await service.peer();
    await service.close(); await service.start();
    expect(await service.project(project.id)).toEqual(project);
    await service.finalizeClosedStore(testInfo.outputPath("raw-complete-store"), project, [peer]);
    expect(raw.rows).toHaveLength(4);
    expect(admissions).toHaveLength(3);
    expect(admissions.some(context => context.helper_id === initializer.helperId)).toBe(false);
    expect(raw.rows.map(row => [row.context.helper_id, row.context.launch_index])).toEqual([
      [initializer.helperId, 0], [service.helperId, 0], [peer.helperId, 0], [service.helperId, 1],
    ]);
    expect(receipt?.participants.map(item => item.helper_id).sort()).toEqual([initializer.helperId, service.helperId, peer.helperId].sort());
    for (const row of raw.rows) {
      const helper = [initializer, service, peer].find(item => item.helperId === row.context.helper_id)!;
      expect(row.context.binary_path).toBe(helper.binaryPath);
      expect(row.context.database_path).toBe(helper.databasePath);
      expect(row.processId).toBe(helper.processStreams[row.context.launch_index].process_id);
      expect(row.nonBufferChunks).toBe(0);
      const wire = Buffer.concat(row.stdout).toString("utf8").trim().split("\n").map(line => JSON.parse(line));
      expect(wire).toEqual(helper.transcript.filter(entry => entry.processId === row.processId && entry.direction === "response").map(entry => entry.payload));
    }
  } finally {
    await Promise.allSettled([initializer.close(), ...(service ? [service.close()] : []), ...(peer ? [peer.close()] : [])]);
  }
});

test("service closure waits for raw sink flush before presenting a CLOSED barrier to its owner", async ({}, testInfo) => {
  let releaseFlush!: () => void;
  const flush = new Promise<void>(resolve => { releaseFlush = resolve; });
  const raw = rawSinkFixture(async () => flush);
  let ownerClosed = false;
  const owner: ServiceProcessOwner = {
    async environmentForNewClient() { return {}; }, sealClientAdmissions() {},
    async closeAfterClients(barrier) {
      expect(raw.rows.every(row => row.settled)).toBe(true);
      expect(barrier.closed).toBe(true); ownerClosed = true;
    },
    assertClosed() { if (!ownerClosed) throw new Error("Owner fixture was not closed"); },
  };
  const service = await create(testInfo, 10_000, owner, raw.capture);
  let finalized: Promise<StoreFinalization> | undefined;
  try {
    const project = (await service.fixtureBootstrap()).project;
    finalized = service.finalizeClosedStore(testInfo.outputPath("raw-flush-store"), project);
    // Retain an early rejection without allowing an unhandled test promise.
    void finalized.catch(() => undefined);
    await expect.poll(() => service.processExits.length).toBe(1);
    await expect.poll(() => raw.rows[0].ended).toBe(true);
    expect(raw.rows[0].settled).toBe(false);
    expect(ownerClosed).toBe(false);
    await expect(readFile(testInfo.outputPath("raw-flush-store-service-closure.json"))).rejects.toThrow();
    releaseFlush();
    expect((await finalized).capture).not.toBeNull();
    expect(ownerClosed).toBe(true);
  } finally {
    releaseFlush(); await Promise.allSettled([service.close(), ...(finalized ? [finalized] : [])]);
  }
});

test("direct capture cannot bypass an exited process whose raw sinks are still pending", async ({}, testInfo) => {
  let releaseFlush!: () => void;
  const flush = new Promise<void>(resolve => { releaseFlush = resolve; });
  const raw = rawSinkFixture(async () => flush);
  const service = await create(testInfo, 10_000, undefined, raw.capture);
  try {
    await service.fixtureBootstrap();
    childOf(service).stdin.end();
    await expect.poll(() => service.processExits.length).toBe(1);
    await expect.poll(() => raw.rows[0].ended).toBe(true);
    expect(service.processExits[0]).toMatchObject({ code: 0, signal: null });
    expect(service.lifecycleTaints).toHaveLength(0);
    await rejectedCapture(service, testInfo, "raw-pending-direct-capture");
    releaseFlush(); await service.close();
    expect(raw.rows[0].settled).toBe(true);
    expect((await service.captureClosedStore(testInfo.outputPath("raw-settled-direct-capture"))).entries.length).toBeGreaterThan(0);
  } finally { releaseFlush(); await Promise.allSettled([service.close()]); }
});

test("a raw binding exception after spawn still drains and closes the actual child", async ({}, testInfo) => {
  let context: Readonly<ServiceLaunchContext> | undefined;
  const raw: ServiceRawStreamCapture = {
    bind(observed) { context = observed; throw new Error("Injected raw binding failure after spawn"); },
  };
  await expect(create(testInfo, 10_000, undefined, raw)).rejects.toThrow("Service creation and its cleanup failed");
  if (!context) throw new Error("The raw binding was not reached");
  // Inspect the failed helper retained by the existing cleanup registry.
  const instances = (ScopedChangeService as unknown as { instances: Set<ScopedChangeService> }).instances;
  const service = [...instances].find(item => item.helperId === context!.helper_id)!;
  expect(service).toBeDefined();
  expect(service.processExits).toHaveLength(1);
  expect(service.processExits[0]).toMatchObject({ code: 0, signal: null });
  expect(service.processStreams[0]).toMatchObject({ stdout_ended: true, stderr_ended: true, jsonl_reader_closed: true });
  expect(service.lifecycleTaints.some(taint => taint.message.includes("Injected raw binding failure"))).toBe(true);
  await rejectedCapture(service, testInfo, "raw-binding-failed-capture");
  await record(testInfo, [service], { fixture: "synthetic_raw_binding_throw_after_real_spawn; excludes_Native_Host" });
});

for (const failure of ["rejection", "deadline"] as const) {
  test(`raw sink ${failure} permanently vetoes capture while external owner cleanup still runs`, async ({}, testInfo) => {
    test.setTimeout(45_000);
    let releaseLate!: () => void;
    const late = new Promise<void>(resolve => { releaseLate = resolve; });
    const raw = rawSinkFixture(async () => {
      if (failure === "rejection") throw new Error("Injected raw sink flush failure");
      await late;
    });
    let cleanupCalls = 0;
    const owner: ServiceProcessOwner = {
      async environmentForNewClient() { return {}; }, sealClientAdmissions() {},
      async closeAfterClients(barrier) { expect(barrier.closed).toBe(false); cleanupCalls++; },
      assertClosed() { if (cleanupCalls !== 1) throw new Error("Owner cleanup was not observed exactly once"); },
    };
    const service = await create(testInfo, 10_000, owner, raw.capture);
    try {
      const project = (await service.fixtureBootstrap()).project;
      const destination = testInfo.outputPath("raw-failed-store");
      await expect(service.finalizeClosedStore(destination, project)).rejects.toThrow("Store finalization is incomplete");
      const receipt = JSON.parse(await readFile(destination + "-service-closure.json", "utf8")) as ServiceClosureReceipt;
      expect(receipt.status).toBe("UNKNOWN");
      expect(cleanupCalls).toBe(1);
      expect(service.processExits[0]).toMatchObject({ code: 0, signal: null });
      expect(service.lifecycleTaints.some(taint => taint.kind === "transport_failure" && /raw stream capture/.test(taint.message))).toBe(true);
      releaseLate();
      if (failure === "deadline") await expect.poll(() => raw.rows[0].settled).toBe(true);
      await expect(service.close()).rejects.toThrow(/lifecycle is tainted/);
      await rejectedCapture(service, testInfo, "raw-failed-direct-capture");
      await expect(readFile(join(destination, "manifest.json"))).rejects.toThrow();
    } finally {
      releaseLate(); await Promise.allSettled([service.close()]);
      await record(testInfo, [service], { fixture: "synthetic_raw_sink_failure_on_real_service; excludes_Native_Host", failure });
    }
  });
}

test("real stderr stays in Buffer mode when SQLite rejects a directory path", async ({}, testInfo) => {
  const raw = rawSinkFixture();
  const databasePath = testInfo.outputPath("service", "studio.sqlite3");
  // The actual Rust startup fails opening a directory as SQLite and writes stderr.
  await mkdir(databasePath, { recursive: true });
  await expect(ScopedChangeService.create({ databasePath, rawStreamCapture: raw.capture }))
    .rejects.toThrow("Service creation and its cleanup failed");
  expect(raw.rows).toHaveLength(1);
  const instances = (ScopedChangeService as unknown as { instances: Set<ScopedChangeService> }).instances;
  const service = [...instances].find(item => item.helperId === raw.rows[0].context.helper_id)!;
  expect(service).toBeDefined();
  expect(service.processExits).toHaveLength(1);
  expect(Number.isSafeInteger(service.processExits[0].code)).toBe(true);
  expect(service.processExits[0].code).not.toBe(0);
  expect(service.processExits[0].signal).toBeNull();
  expect(raw.rows[0].ended).toBe(true);
  expect(raw.rows[0].settled).toBe(true);
  expect(raw.rows[0].nonBufferChunks).toBe(0);
  const stderr = Buffer.concat(raw.rows[0].stderr);
  expect(stderr.length).toBeGreaterThan(0);
  expect(stderr.toString("utf8")).toBe(service.stderr);
  await rejectedCapture(service, testInfo, "raw-stderr-failed-capture");
  await record(testInfo, [service], { fixture: "SQLite_directory_path_for_original_stderr_byte_control; excludes_Native_Host" });
});
