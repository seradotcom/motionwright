import { type ChildProcessWithoutNullStreams } from "node:child_process";
import { once } from "node:events";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect, test, type TestInfo } from "@playwright/test";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Faults below affect only the real executable's test transport. No Project,
// RPC response, SQLite mutation, process result, or media acceptance is mocked.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Build studio_review_session and set its absolute path; there is no simulated service fallback.");

function childOf(service: ScopedChangeService): ChildProcessWithoutNullStreams {
  // Deliberate test-only fault injection into the owned process's pipes/events.
  const child = (service as unknown as { child: ChildProcessWithoutNullStreams | null }).child;
  if (!child) throw new Error("The fault fixture requires a running real service");
  return child;
}
async function create(testInfo: TestInfo, timeoutMs = 10_000) {
  return ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3"), timeoutMs });
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

test("complete capture includes every file and a paginated full oracle after all known peers close", async ({}, testInfo) => {
  test.setTimeout(60_000);
  const service = await create(testInfo);
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
    const result = await service.finalizeClosedStore(testInfo.outputPath("closed-store-1"), final, [peer]);
    finalized = true;
    expect(result.errors).toEqual([]);
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
  const service = await create(testInfo);
  let peer: ScopedChangeService | undefined;
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
