import { randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import type { Change, Project } from "../src/types";
import { ScopedChangeService, type ServiceRpcRequest } from "./support/scopedChangeService";

// Actual StudioService, SQLite, CAS and grants through the opt-in JSONL bridge.
// This does not exercise installed Tauri, native rendering or human acceptance.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Build studio_review_session; no simulated service fallback is available.");
test.setTimeout(120_000);

const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const requests = (service: ScopedChangeService, command: string): ServiceRpcRequest[] =>
  service.transcript.flatMap(entry => entry.direction === "request" && "command" in entry.payload
    && entry.payload.command === command ? [entry.payload] : []);
const detail = (error: unknown) => error instanceof Error
  ? { name: error.name, message: error.message, stack: error.stack } : { message: String(error) };

test("an imported bundle rejects source generation and grants while a fresh Studio edit survives reopen", async ({ page }, info) => {
  let source: ScopedChangeService | undefined, target: ScopedChangeService | undefined;
  let sourceFinal: Project | undefined, targetFinal: Project | undefined;
  const failures: unknown[] = [];
  const observations: Record<string, unknown> = {};
  try {
    source = await ScopedChangeService.create({ databasePath: info.outputPath("source", "studio.sqlite3"), desktopInvokeFixture: true });
    const boot = await source.fixtureBootstrap();
    const initial = sourceFinal = boot.project;
    const scene = initial.scenes.find(item => item.id === boot.fixture.scene_id)!;
    const node = scene.nodes.find(item => item.id === boot.fixture.node_id)!;
    expect(node.kind).toBe("text");
    const sourceOracle = await source.observeStoredProject(initial);
    const requestId = randomUUID();
    const originalGrant = await source.grant("project_edit", initial, requestId);
    const forbiddenChange: Change = { type: "update_canvas_text", scene_id: scene.id, node_id: node.id,
      text: "Authority from the source must not edit the imported project." };
    const originalRequest = { project_id: initial.id, generation: initial.generation, revision: initial.revision,
      request_id: requestId, effect_grant: originalGrant.token, change: forbiddenChange };
    const bundlePath = info.outputPath("portable-bundle");
    await source.exportBundle(initial, bundlePath);

    target = await ScopedChangeService.create({ databasePath: info.outputPath("target", "studio.sqlite3"),
      seedFixture: false, desktopInvokeFixture: true });
    const inspected = await target.inspectBundle(bundlePath);
    expect(inspected.rotates_generation).toBe(true);
    expect(inspected.source_generation).toBe(initial.generation);
    const imported = targetFinal = await target.importBundle(bundlePath, inspected.fingerprint);
    expect(imported.generation).toBeTruthy();
    expect(imported.generation).not.toBe(initial.generation);
    expect(imported).toEqual({ ...initial, generation: imported.generation, updated_at: imported.updated_at });
    const importedOracle = await target.observeStoredProject(imported);
    expect(importedOracle.journal).toEqual(sourceOracle.journal);
    expect(source.transcript.at(-1)?.processId).toBeGreaterThan(0);
    expect(target.transcript.at(-1)?.processId).toBeGreaterThan(0);
    expect(target.transcript.at(-1)?.processId).not.toBe(source.transcript.at(-1)?.processId);

    // Generation is checked before the grant registry. Neither rejected request
    // may edit the project or append a journal event.
    const oldGeneration = await target.rawRequest({ command: "apply_change", args: { request: originalRequest } });
    expect(oldGeneration.ok).toBe(false);
    if (oldGeneration.ok) throw new Error("The imported project accepted the source generation.");
    expect(String(oldGeneration.error)).toMatch(/generation/i);
    expect((await target.observeStoredProject(imported)).journal).toEqual(importedOracle.journal);
    const oldGrant = await target.rawRequest({ command: "apply_change", args: {
      request: { ...originalRequest, generation: imported.generation },
    } });
    expect(oldGrant.ok).toBe(false);
    if (oldGrant.ok) throw new Error("The imported project accepted the source process grant.");
    expect(String(oldGrant.error)).toMatch(/grant/i);
    expect((await target.observeStoredProject(imported)).journal).toEqual(importedOracle.journal);
    expect(requests(target, "issue_effect_grant").filter(call =>
      (call.args?.request as { effect?: string } | undefined)?.effect === "project_edit")).toEqual([]);
    observations.rejections = { oldGeneration, oldGrant };
    observations.imported = { project: imported, journal: importedOracle.journal, fingerprint: inspected.fingerprint };

    // Explicit selection is required: the destination has no synthetic seed.
    await target.attachPage(page, imported.id);
    await page.goto("/");
    await button(page, "Production").click();
    await page.getByLabel("Production working scene").selectOption(scene.id);
    await button(page, "Scoped changes").click();
    const text = "An editable decision authorized in the imported generation.";
    const rationale = "Revise only the imported text using the current project authority.";
    await page.getByLabel("Patch target object").selectOption(node.id);
    await page.getByLabel("Patch edit kind").selectOption("text");
    await page.getByLabel("Patch replacement text").fill(text);
    await button(page, "Add to proposal").click();
    await page.getByLabel("Patch rationale").fill(rationale);
    await button(page, "Preview scoped changes").click();
    await expect(page.getByText("Editorial diff prepared", { exact: true })).toBeVisible();
    expect((await target.observeStoredProject(imported)).journal).toEqual(importedOracle.journal);
    await button(page, "Apply as one revision").click();
    await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${imported.revision + 1}`);
    const edited = targetFinal = await target.project(imported.id);
    expect(edited.revision).toBe(imported.revision + 1);
    expect(edited.generation).toBe(imported.generation);
    const editedScene = edited.scenes.find(item => item.id === scene.id)!;
    expect(editedScene.nodes.find(item => item.id === node.id)?.text).toBe(text);
    expect(editedScene.nodes.filter(item => item.id !== node.id)).toEqual(scene.nodes.filter(item => item.id !== node.id));
    expect(edited.scenes.filter(item => item.id !== scene.id)).toEqual(imported.scenes.filter(item => item.id !== scene.id));
    expect(edited.assets).toEqual(imported.assets);
    const editedOracle = await target.observeStoredProject(edited);
    expect(editedOracle.journal.slice(0, -1)).toEqual(importedOracle.journal);
    const expectedChange = { type: "apply_creative_patch", patch: {
      scene_id: scene.id, base_revision: imported.revision, rationale,
      edits: [{ kind: "text", node_id: node.id, text }],
    } };
    expect(editedOracle.journal.at(-1)?.change).toEqual(expectedChange);
    const applies = requests(target, "apply_change");
    expect(applies).toHaveLength(3);
    const uiRequest = applies[2].args?.request as {
      project_id: string; generation: Project["generation"]; revision: number;
      request_id: string; effect_grant: string; change: Change;
    };
    expect(uiRequest).toMatchObject({ project_id: imported.id, generation: imported.generation,
      revision: imported.revision, change: expectedChange });
    expect(uiRequest.request_id).toBeTruthy();
    expect(uiRequest.request_id).not.toBe(requestId);
    expect(uiRequest.effect_grant).toBeTruthy();
    expect(uiRequest.effect_grant).not.toBe(originalGrant.token);
    const freshGrants = requests(target, "issue_effect_grant").filter(call =>
      (call.args?.request as { effect?: string } | undefined)?.effect === "project_edit");
    expect(freshGrants).toHaveLength(1);
    expect(freshGrants[0].args?.request).toMatchObject({ effect: "project_edit", project_id: imported.id,
      generation: imported.generation, revision: imported.revision, subject: uiRequest.request_id });
    const freshReply = target.transcript.find(entry => entry.direction === "response"
      && entry.payload.id === freshGrants[0].id)?.payload;
    expect(freshReply).toMatchObject({ ok: true, result: { token: uiRequest.effect_grant } });

    await target.close();
    await target.start();
    expect((await target.observeStoredProject(edited)).journal).toEqual(editedOracle.journal);
    await page.reload();
    await button(page, "Production").click();
    await page.getByLabel("Production working scene").selectOption(scene.id);
    await button(page, "Scoped changes").click();
    await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${edited.revision}`);
    await expect(button(page, "Apply as one revision")).toBeDisabled();
    await expect(page.getByRole("region", { name: "Proposed edits", exact: true })).toContainText("No edits queued");
    expect((await target.observeStoredProject(edited)).journal).toEqual(editedOracle.journal);
    expect(requests(target, "apply_change")).toHaveLength(3);
    expect((await source.observeStoredProject(initial)).journal).toEqual(sourceOracle.journal);
    expect(requests(source, "apply_change")).toEqual([]);
    observations.final = { source: sourceOracle, imported: editedOracle };
  } catch (error) { failures.push(error); }
  finally {
    const stores = [
      ...(source ? [{ name: "source", service: source, expected: sourceFinal }] : []),
      ...(target ? [{ name: "target", service: target, expected: targetFinal }] : []),
    ];
    const [pageClosed] = await Promise.allSettled([page.close()]);
    if (pageClosed.status === "rejected") failures.push(pageClosed.reason);
    // Separate databases require separate complete-store oracles and captures.
    const finalized = await Promise.allSettled(stores.map(store => store.service.finalizeClosedStore(
      info.outputPath(`closed-store-${store.name}`), store.expected, [], [async () => {
        if (pageClosed.status === "rejected") throw pageClosed.reason;
      }],
    )));
    const closed = await Promise.allSettled(stores.map(store => store.service.close()));
    for (const result of [...finalized, ...closed]) if (result.status === "rejected") failures.push(result.reason);
    const transcripts = await Promise.allSettled(stores.map(async (store, index) => {
      const path = info.outputPath(`${store.name}-service-transcript.json`);
      const finalizedStore = finalized[index];
      await writeFile(path, JSON.stringify({
        evidenceScope: "real_service_sqlite_cas; excludes_tauri_installer_native_render",
        fixture: "synthetic_test_fixture", databasePath: store.service.databasePath, expected: store.expected,
        transcript: store.service.transcript, processExits: store.service.processExits,
        lifecycleTaints: store.service.lifecycleTaints, stderr: store.service.stderr,
        finalization: finalizedStore.status === "fulfilled" ? finalizedStore.value : { error: detail(finalizedStore.reason) },
        close: closed[index].status,
      }, null, 2), { flag: "wx" });
      await info.attach(`${store.name}-service-transcript`, { path, contentType: "application/json" });
    }));
    for (const result of transcripts) if (result.status === "rejected") failures.push(result.reason);
    const path = info.outputPath("bundle-generation-authority-evidence.json");
    try {
      await writeFile(path, JSON.stringify({ observations, failures: failures.map(detail),
        evidenceScope: "real_service_sqlite_cas_and_browser_edit; excludes_installed_tauri_native_and_human_acceptance",
      }, null, 2), { flag: "wx" });
      await info.attach("bundle-generation-authority-evidence", { path, contentType: "application/json" });
    } catch (error) { failures.push(error); }
  }
  if (failures.length === 1) throw failures[0];
  if (failures.length) throw new AggregateError(failures, "Bundle generation authority test or store finalization failed");
});
