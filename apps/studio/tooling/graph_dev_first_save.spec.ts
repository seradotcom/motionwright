/**
 * Explicit development-only first Native Graph Save and independent Store reopen.
 * This case is NOT run by ordinary Studio tests and cannot approve R74 or G2.
 * The genuine source-owned Native owner is required before any process starts:
 * no credential env flags, caller-created Ready, mock Host or copied Store.
 */
import { writeFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import { ScopedChangeService } from "../tests/support/scopedChangeService";
import { graphFrameTime, graphSourceSnapshot, type GraphProject } from "../src/semanticGraph";
import { decodeGraphContextFailure } from "../src/semanticGraphContextFailure";
import { acquireGraphDevelopmentOwner, assertOriginalGraphReadOnlyBootstrap } from "./graph_dev_native_owner_port";

const button = (page: Page, label: string) => page.getByRole("button", { name: label, exact: true });
const field = (page: Page, label: string) => page.getByLabel(label, { exact: true });
const CLOSED_FIRST_CAUSES = new Set([
  "GRAPH_DEV_REAL_SCENE_NOT_CREATED", "GRAPH_DEV_REAL_PROFILE_MISSING",
  "GRAPH_DEV_NATIVE_GRAPH_NOT_PERSISTED",
  "GRAPH_DEV_INDEPENDENT_STORE_REOPEN_NOT_PROVEN",
  "GRAPH_DEV_ORIGINAL_OWNER_PORTS_UNAVAILABLE",
  "GRAPH_DEV_ORIGINAL_OWNER_NOT_E_ONLY",
  "GRAPH_DEV_ORIGINAL_WRITER_IDENTITY_UNAVAILABLE",
  "GRAPH_DEV_ORIGINAL_PROJECT_OR_EMPTY_GRAPH_SOURCE_MISMATCH",
  "GRAPH_DEV_ORIGINAL_SCENE_OR_PROFILE_MISSING",
]);

test("Graph6 dev: original Native Host flow Save and independent same-Store read-only verification", async ({ page }, info) => {
  test.setTimeout(180_000);
  if (process.env.MOTIONWRIGHT_GRAPH_DEV_FIRST_SAVE !== "1" || process.env.MOTIONWRIGHT_GRAPH_NATIVE_CASE) {
    throw new Error("GRAPH_DEV_EXPLICIT_FIRST_SAVE_ONLY_NOT_RELEASE_PRODUCER");
  }
  // The source-owned provider is intentionally unavailable until the Native owner supplies
  // genuine Native/Host/Store owner code. Nothing from process.env proves it.
  const owner = await acquireGraphDevelopmentOwner();
  const options = owner.writerOptions;
  if (options.graphCustodyRequired !== true || options.seedFixture !== false
    || options.desktopInvokeFixture !== true || !options.processOwner
    || !options.rawStreamCapture || !options.evidenceCapture) {
    throw new Error("GRAPH_DEV_ORIGINAL_OWNER_PORTS_UNAVAILABLE");
  }
  if (process.platform === "win32" &&
      ![options.binaryPath, options.databasePath].every(path => /^[Ee]:[\\/]/.test(path))) {
    throw new Error("GRAPH_DEV_ORIGINAL_OWNER_NOT_E_ONLY");
  }
  let service: ScopedChangeService | undefined;
  let firstFailure: string | null = null;
  try {
    // These exact immutable options are issued by the original Native owner.
    // No user env marker can create a processOwner, evidence/original custody,
    // admitted callback or kernel Store file-limit witness.
    const original = await owner.openOriginalWriter();
    // The owner already launched and admitted this single real Native child.
    // The browser test must never spawn a second service or synthesize a replacement Store.
    service = original.service;
    if (!original.projectId || !original.sceneId || !original.profileId
      || service.binaryPath !== options.binaryPath
      || service.databasePath !== options.databasePath
      || service.processStreams.length !== 1 || service.processExits.length !== 0
      || service.lifecycleTaints.length !== 0 || !service.processStreams[0].process_id) {
      throw new Error("GRAPH_DEV_ORIGINAL_WRITER_IDENTITY_UNAVAILABLE");
    }
    const project = await service.project(original.projectId);
    if (project.id !== original.projectId || project.production_design?.semantic_graphs?.length) {
      throw new Error("GRAPH_DEV_ORIGINAL_PROJECT_OR_EMPTY_GRAPH_SOURCE_MISMATCH");
    }
    // The authenticated Native owner already created the actual source,
    // scene, renderer and Deliverable in this Store. Pre-save Service writes
    // would change that revision/authority before the first Native callback.
    const scene = project.scenes.find(item => item.id === original.sceneId);
    const profile = project.deliverables.find(item => item.id === original.profileId);
    if (!scene || !profile) throw new Error("GRAPH_DEV_ORIGINAL_SCENE_OR_PROFILE_MISSING");
    const source = structuredClone(project);
    await service.attachPage(page, project.id);
    await page.goto("/");
    await button(page, "Production").click();
    await field(page, "Production working scene").selectOption(scene.id);
    await button(page, "Graph").click();
    await expect(page.getByRole("heading", { name: "Semantic Graph", exact: true })).toBeVisible();
    await field(page, "Graph source").selectOption("new:system_flow");
    await field(page, "Graph Deliverable").selectOption(profile.id);
    await field(page, "Graph author label").fill("Native development test author");
    await field(page, "Graph rationale").fill("One real Native Save and independent Store reopen; not release acceptance.");
    await field(page, "Graph motion").selectOption("off");
    await button(page, "Add synthetic event").click();
    await page.locator("[data-graph-event] summary").first().click();
    await field(page, "Event 1 label").fill("Actual Native authorized transfer");

    const inspect = button(page, "Inspect current Graph support and sources");
    await inspect.click();
    await expect(inspect).toHaveAttribute("data-graph-inspection-phase",
      /^(GRAPH_CONTEXT_INSPECTION_RESOLVED|GRAPH_CONTEXT_INSPECTION_REJECTED|GRAPH_CONTEXT_INSPECTION_OBSOLETE)$/,
      { timeout: 30_000 });
    const phase = await inspect.getAttribute("data-graph-inspection-phase");
    const save = button(page, "Save semantic Graph");
    const gate = await save.getAttribute("data-graph-save-gate");
    if (phase !== "GRAPH_CONTEXT_INSPECTION_RESOLVED" || gate !== "GRAPH_SAVE_READY") {
      // One original failed read receives exactly one closed diagnostic.
      // No arbitrary RPC, Native error text or private paths enter this file.
      const phases = new Set(["GRAPH_CONTEXT_INSPECTION_RESOLVED",
        "GRAPH_CONTEXT_INSPECTION_REJECTED", "GRAPH_CONTEXT_INSPECTION_OBSOLETE"]);
      const gates = new Set(["GRAPH_SAVE_READY", "GRAPH_SAVE_BUSY", "GRAPH_SAVE_PENDING",
        "GRAPH_SAVE_CONTEXT_UNAVAILABLE", "GRAPH_SAVE_CONTEXT_BLOCKED",
        "GRAPH_SAVE_SOURCE_STALE", "GRAPH_SAVE_CONTEXT_INVALID", "GRAPH_SAVE_INPUT_INVALID",
        "GRAPH_SAVE_BACKUP_UNCONFIRMED", "GRAPH_SAVE_RECOVERY_UNCONFIRMED",
        "GRAPH_SAVE_PROJECT_CHANGED", "GRAPH_SAVE_LOCKED", "GRAPH_SAVE_IDENTITY_UNAVAILABLE",
        "GRAPH_SAVE_VARIANT_OCCUPIED", "GRAPH_SAVE_SAVED_IDENTITY_UNAVAILABLE"]);
      const parts = gate?.split(",") ?? [];
      const safePhase = phase && phases.has(phase) ? phase : "GRAPH_DEV_PHASE_UNRECOGNIZED";
      const safeGate = parts.length && parts.every(part => gates.has(part))
        ? parts.join(",") : "GRAPH_DEV_GATE_UNRECOGNIZED";
      const stage = await inspect.getAttribute("data-graph-context-failure-stage");
      const code = await inspect.getAttribute("data-graph-context-failure-code");
      const decoded = stage !== null && code !== null
        ? decodeGraphContextFailure({ schema: "motionwright.native.graph-context-failure/1",
          stage, error_code: code === "NONE" ? null : code }) : null;
      const diagnostic = {
        schema: "motionwright.graph-dev-first-context-failure/1",
        release_acceptance: false, mutation_dispatched: false,
        context_phase: safePhase, save_gates: safeGate,
        native_failure: decoded ? { stage: decoded.stage, error_code: decoded.error_code } : null,
        source_snapshot: graphSourceSnapshot(source),
        private_originals: "Custodied by the real Native owner, never copied to browser logs",
      };
      const evidence = info.outputPath("native-development-first-context-failure.json");
      await writeFile(evidence, JSON.stringify(diagnostic, null, 2));
      await info.attach("native-development-first-context-failure",
        { path: evidence, contentType: "application/json" });
      firstFailure = "GRAPH_DEV_FIRST_SAVE_NOT_READY: " + safePhase + " / " + safeGate
        + (decoded ? "\nGRAPH_CONTEXT_FAILURE: " + decoded.stage
          + " / " + (decoded.error_code ?? "NONE") : "");
      throw new Error(firstFailure);
    }
    await expect(save).toBeEnabled();
    await save.click();
    await expect.poll(async () => (await service!.project(source.id)).revision,
      { timeout: 30_000 }).toBe(source.revision + 1);
    const saved = await service.project(source.id) as GraphProject;
    const graph = saved.production_design?.semantic_graphs?.find(item =>
      item.scene_id === scene.id && item.variant_profile_ref === profile.id);
    if (!graph || graph.content.kind !== "system_flow") throw new Error("GRAPH_DEV_NATIVE_GRAPH_NOT_PERSISTED");
    expect(graph.authorship.source_snapshot).toEqual(graphSourceSnapshot(source));
    expect(graph.content.events).toHaveLength(1);
    expect(graph.content.events[0]).toMatchObject({
      edge_id: graph.content.edges[0].id,
      label: "Actual Native authorized transfer",
      at: graphFrameTime(2, profile.frame_rate),
      duration: graphFrameTime(1, profile.frame_rate),
    });
    const events = await service.history(source.id, source.revision);
    expect(events.map(item => item.change.type)).toEqual(["upsert_semantic_graph"]);
    const mutations = service.transcript.filter(item => item.direction === "request"
      && "command" in item.payload && item.payload.command === "apply_change"
      && (item.payload.args?.request as { change?: { type?: string } } | undefined)?.change?.type === "upsert_semantic_graph");
    expect(mutations).toHaveLength(1);

    // Close the original editorial page before its native writer is sealed.
    // An independent reader cannot reuse the old page's JSONL binding or
    // masquerade as a full desktop editor.
    await page.close();
    // The original writer closes once with real EOF/retained evidence BEFORE
    // the owner creates an independently source-bound READ-ONLY second process
    // on the identical physical SQLite/CAS. The former in-process reopen
    // cannot serve as proof of physical second-process reopening.
    const originalWriter = service;
    service = undefined;
    const readOnly = await owner.closeOriginalAndOpenIndependentReadOnly(originalWriter, saved, events);
    // Even a failed admission of this second process must reach the final
    // reader cleanup. No leaked client can keep the Store open on rejection.
    service = readOnly;
    // A newly returned service is not enough: its process must be distinct,
    // the original writer must have fully closed, and the exact SQLite path
    // must remain unchanged. The owner independently proves physical file
    // identity and source provenance; these checks cannot replace that proof.
    const writerClosed = originalWriter.processStreams.length > 0
      && originalWriter.processExits.length === originalWriter.processStreams.length
      && originalWriter.processStreams.every(stream =>
        stream.exit_index !== null && stream.stdout_ended && stream.stderr_ended && stream.jsonl_reader_closed)
      && originalWriter.processExits.every(exit => exit.code === 0 && exit.signal === null)
      && originalWriter.lifecycleTaints.length === 0;
    const separateReader = readOnly !== originalWriter && readOnly.isReadOnly
      && readOnly.databasePath === originalWriter.databasePath
      && readOnly.processStreams.length > 0
      && readOnly.processStreams.every(stream => stream.process_id !== null
        && !originalWriter.processStreams.some(old => old.process_id === stream.process_id));
    if (!writerClosed || !separateReader) {
      throw new Error("GRAPH_DEV_INDEPENDENT_STORE_REOPEN_NOT_PROVEN");
    }
    // The Rust read-only session exposes only five source-owned read
    // commands and reports browser-demo mode. A full App launch would demand
    // Tauri projection APIs it must never expose. Verify the actual original
    // read-only bootstrap and Store Project, without fabricating Tauri.
    const readerBootstrap = await service.invoke<unknown>("bootstrap",
      { request: { project_id: source.id } });
    assertOriginalGraphReadOnlyBootstrap(readerBootstrap, saved);
    const reopened = await service.project(source.id) as GraphProject;
    expect(reopened).toEqual(saved);
    expect((await service.history(source.id, source.revision)).map(item => item.change.type))
      .toEqual(["upsert_semantic_graph"]);

    const witness = {
      schema: "motionwright.graph-dev-first-save-observation/1",
      scope: "one original Native development Studio Save and separate same-Store source-built read-only Project verification",
      release_acceptance: false, R74_reviewed: false,
      original_source: graphSourceSnapshot(source),
      saved_revision: saved.revision, saved_graph_id: graph.id,
      event_ids: graph.content.events.map(item => item.id),
      graph_mutations: mutations.length, journal: events.map(item => item.change.type),
      independent_same_store_reopen: true,
      read_only_bootstrap_verified: true,
      full_editor_reopened: false,
    };
    const evidence = info.outputPath("native-development-first-save.json");
    await writeFile(evidence, JSON.stringify(witness, null, 2));
    await info.attach("native-development-first-save",
      { path: evidence, contentType: "application/json" });
  } catch (error) {
    // Private Native/Host errors belong to the owner originals. Emit only
    // finite source-owned first causes through the browser test reporter.
    // Never surface a Native/Host/owner string merely because its prefix
    // resembles a diagnostic. Only literals authored by this Studio probe or
    // its prior fully decoded context observation are public.
    firstFailure ??= error instanceof Error && CLOSED_FIRST_CAUSES.has(error.message)
      ? error.message : "GRAPH_DEV_NATIVE_OR_STORE_OPERATION_FAILED";
    throw new Error(firstFailure);
  } finally {
    const cleanupFailures: string[] = [];
    if (service) {
      try { await service.close(); } catch { cleanupFailures.push("GRAPH_DEV_READER_OR_WRITER_CLOSE_FAILED"); }
    }
    try { await owner.close(); } catch { cleanupFailures.push("GRAPH_DEV_NATIVE_OWNER_CLOSE_FAILED"); }
    if (cleanupFailures.length) {
      throw new Error((firstFailure ? firstFailure + " / " : "") + cleanupFailures.join(","));
    }
  }
});
