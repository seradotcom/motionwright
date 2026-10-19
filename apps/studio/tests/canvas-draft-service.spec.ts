import { writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { CanvasNode, CanvasTransform, Change, Project, Scene } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Synthetic authored content, with every project command crossing the real
// Rust service, grants and SQLite journal. Delays below only schedule transport
// replies; they never replace a Project or simulate a successful mutation.
// These tests exclude installed Tauri, Film/native realization and human review.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Build studio_review_session and set MOTIONWRIGHT_SERVICE_TEST_BIN; no simulated service fallback.");
test.setTimeout(90_000);

const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const input = (page: Page, name: string) => page.getByLabel("Canvas " + name, { exact: true });
const inspector = (page: Page) => page.locator("section[aria-label='Canvas object draft']");
const library = (page: Page) => page.getByRole("dialog", { name: "Project library", exact: true });

interface ApplyArgs extends Record<string, unknown> {
  request: {
    project_id: string; generation: string; revision: number;
    request_id: string; effect_grant: string; change: Change;
  };
}

interface ScenarioEvidence {
  initial?: Project;
  final?: Project;
  checkpoints: { label: string; project: Project }[];
  observations: Record<string, unknown>[];
}

function applyRequests(service: ScopedChangeService): ApplyArgs[] {
  return service.transcript.flatMap(entry => entry.direction === "request" &&
    "command" in entry.payload && entry.payload.command === "apply_change"
    ? [entry.payload.args as ApplyArgs] : []);
}

function transform(node: CanvasNode): CanvasTransform {
  return { x: node.x, y: node.y, width: node.width, height: node.height,
    rotation_deg: node.rotation_deg, opacity: node.opacity };
}

function sceneNamed(project: Project, name: string): Scene {
  const scene = project.scenes.find(item => item.name === name);
  if (!scene) throw new Error("Expected the authored scene: " + name);
  return scene;
}

function nodeNamed(scene: Scene, name: string): CanvasNode {
  const node = scene.nodes.find(item => item.name === name);
  if (!node) throw new Error("Expected the authored object: " + name);
  return node;
}

function storedNode(project: Project, sceneId: string, nodeId: string): CanvasNode {
  const node = project.scenes.find(scene => scene.id === sceneId)?.nodes.find(item => item.id === nodeId);
  if (!node) throw new Error("A previously authored Canvas object disappeared from the stored Project");
  return node;
}

async function paint(page: Page) {
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}

async function revision(page: Page, expected: number) {
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + expected);
  await paint(page);
}

async function selectScene(page: Page, name: string) {
  const row = page.locator(".tree-row").filter({ has: page.getByText(name, { exact: true }) });
  await row.click();
  await expect(row).toHaveClass(/selected/);
}

async function selectNode(page: Page, name: string) {
  const row = page.locator(".canvas-tree-row").filter({ has: page.getByText(name, { exact: true }) });
  await row.click();
  await expect(row).toHaveClass(/selected/);
  await expect(page.locator(".canvas-selection-title strong")).toHaveText(name);
  await expect(input(page, "text")).toBeVisible();
}

async function openCanvas(page: Page, scene?: string, node?: string) {
  await button(page, "Canvas").click();
  await expect(page.getByLabel("Canvas object tree", { exact: true })).toBeVisible();
  if (scene) await selectScene(page, scene);
  if (node) await selectNode(page, node);
}

async function addScene(page: Page, name: string) {
  await button(page, "Add scene").click();
  await page.getByLabel("New scene name", { exact: true }).fill(name);
  await page.locator(".add-scene-form").getByRole("button", { name: "Add", exact: true }).click();
}

async function checkpoint(service: ScopedChangeService, page: Page, evidence: ScenarioEvidence,
  projectId: string, expected: number, label: string): Promise<Project> {
  await revision(page, expected);
  const project = await service.project(projectId);
  evidence.final = project;
  evidence.checkpoints.push({ label, project });
  expect(project.revision).toBe(expected);
  return project;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(complete => { resolve = complete; });
  return { promise, resolve };
}

/** A real grant and a real command; only their browser scheduling is held. */
function holdNextApply(service: ScopedChangeService, change: Change["type"], phase: "before" | "after") {
  const original = service.invoke.bind(service);
  const entered = deferred<ApplyArgs>();
  const observed = deferred<{ project: Project } | { error: unknown }>();
  const released = deferred<void>();
  const finished = deferred<void>();
  let armed = true, matched = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyArgs["request"] | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== change) return original<T>(command, args);
    armed = false; matched = true;
    entered.resolve(structuredClone(args) as ApplyArgs);
    try {
      if (phase === "before") await released.promise;
      try {
        const project = await original<Project>(command, args);
        observed.resolve({ project });
        if (phase === "after") await released.promise;
        return project as T;
      } catch (error) {
        observed.resolve({ error });
        if (phase === "after") await released.promise;
        throw error;
      }
    } finally { finished.resolve(); }
  };
  return {
    entered: entered.promise, observed: observed.promise,
    release: () => released.resolve(),
    async dispose() {
      service.invoke = original;
      released.resolve();
      if (matched) await finished.promise;
    },
  };
}

async function retainEvidence(testInfo: TestInfo, services: ScopedChangeService[], evidence: ScenarioEvidence,
  prepare: (() => Promise<unknown>)[]) {
  let finalization: StoreFinalization | undefined;
  try {
    // The helper attempts every peer close even when an assertion, final
    // oracle, browser shutdown or outstanding transport gate has failed.
    finalization = await services[0].finalizeClosedStore(testInfo.outputPath("closed-store-1"),
      evidence.final, services.slice(1), prepare);
  } finally {
    const path = testInfo.outputPath("canvas-draft-real-service-evidence.json");
    await writeFile(path, JSON.stringify({
      fixture: "synthetic_authored_content",
      evidenceScope: "browser_interaction_real_service_sqlite_cas_and_local_draft_recovery",
      excludes: ["installed_tauri", "native_realization", "film_render", "human_acceptance"],
      testStatus: testInfo.status, expectedStatus: testInfo.expectedStatus,
      ...evidence, canonicalOracle: finalization?.oracle,
      finalizationPath: testInfo.outputPath("closed-store-1-outcomes.json"),
      services: services.map(service => ({ databasePath: service.databasePath, binaryPath: service.binaryPath,
        stderr: service.stderr, transcript: service.transcript,
        processExits: service.processExits, lifecycleTaints: service.lifecycleTaints })),
    }, null, 2));
    await testInfo.attach("canvas-draft-real-service-evidence", { path, contentType: "application/json" });
    await testInfo.attach("complete-store-finalization", {
      path: testInfo.outputPath("closed-store-1-outcomes.json"), contentType: "application/json",
    });
  }
}

test("an empty library reaches thirteen authored revisions while object, scene and workspace drafts survive a process restart", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3"),
    seedFixture: false, desktopInvokeFixture: true });
  const evidence: ScenarioEvidence = { checkpoints: [], observations: [] };
  try {
    await service.attachPage(page, null);
    await page.goto("/");
    await expect(library(page)).toBeVisible();
    await expect(library(page).getByText("No saved projects", { exact: true })).toBeVisible();
    await library(page).getByLabel("Project title", { exact: true }).fill("Synthetic Canvas continuity");
    await library(page).getByRole("button", { name: "Create project", exact: true }).click();
    await revision(page, 0);
    if (await library(page).isVisible()) await button(page, "Close project library").click();
    const initial = (await service.fixtureBootstrap()).project;
    evidence.initial = initial; evidence.final = initial;
    evidence.checkpoints.push({ label: "empty authored project", project: initial });
    expect(initial.scenes).toEqual([]);
    expect(initial.assets).toEqual([]);

    await button(page, "Brief").click();
    await page.getByLabel("Production objective", { exact: true }).fill("Preserve deliberate spatial and copy decisions across independent scenes.");
    await page.getByLabel("Audience", { exact: true }).fill("An author reopening and revising the same editable project.");
    await button(page, "Commit brief").click();
    await checkpoint(service, page, evidence, initial.id, 1, "brief committed");
    await addScene(page, "Scene A");
    const withA = await checkpoint(service, page, evidence, initial.id, 2, "first scene created");
    const sceneA = sceneNamed(withA, "Scene A");
    await openCanvas(page, sceneA.name);
    await page.locator(".canvas-create-row").getByRole("button", { name: "text", exact: true }).click();
    const withA1 = await checkpoint(service, page, evidence, initial.id, 3, "first editable text object created");
    const nodeA1 = nodeNamed(sceneNamed(withA1, sceneA.name), "Text 1");
    await selectNode(page, nodeA1.name);
    await input(page, "text").fill("First authored A1");
    await button(page, "Commit text").click();
    await checkpoint(service, page, evidence, initial.id, 4, "first object copy committed");
    await page.locator(".canvas-create-row").getByRole("button", { name: "text", exact: true }).click();
    const withA2 = await checkpoint(service, page, evidence, initial.id, 5, "second editable text object created");
    const nodeA2 = nodeNamed(sceneNamed(withA2, sceneA.name), "Text 2");
    await selectNode(page, nodeA2.name);
    await input(page, "text").fill("First authored A2");
    await button(page, "Commit text").click();
    await checkpoint(service, page, evidence, initial.id, 6, "second object copy committed");
    await addScene(page, "Scene B");
    const withB = await checkpoint(service, page, evidence, initial.id, 7, "independent scene created");
    const sceneB = sceneNamed(withB, "Scene B");
    await selectScene(page, sceneB.name);
    await page.locator(".canvas-create-row").getByRole("button", { name: "text", exact: true }).click();
    const withB1 = await checkpoint(service, page, evidence, initial.id, 8, "independent scene object created");
    const nodeB1 = nodeNamed(sceneNamed(withB1, sceneB.name), "Text 1");
    await selectNode(page, nodeB1.name);
    await input(page, "text").fill("First authored B1");
    await button(page, "Commit text").click();
    const baseline = await checkpoint(service, page, evidence, initial.id, 9, "three saved objects before uncommitted input");
    const journalAtNine = await service.history(initial.id, 0);
    const requestsAtNine = applyRequests(service).length;
    expect(requestsAtNine).toBe(9);

    const copyA1 = "  Second A1 decision\nStill authored  ";
    const copyA2 = "  Second A2 decision  ";
    const copyB1 = "  Unsent B1 decision\n";
    await selectScene(page, sceneA.name); await selectNode(page, nodeA1.name);
    await input(page, "text").fill(copyA1);
    await input(page, "X").fill("411.25"); await input(page, "Y").fill("287");
    await selectNode(page, nodeA2.name);
    await input(page, "text").fill(copyA2); await input(page, "H").fill("");
    await expect(input(page, "H")).toHaveAttribute("aria-invalid", "true");
    await expect(button(page, "Commit transform")).toBeDisabled();
    await selectNode(page, nodeA1.name);
    await expect(input(page, "text")).toHaveValue(copyA1);
    await expect(input(page, "X")).toHaveValue("411.25");
    await expect(input(page, "Y")).toHaveValue("287");
    await selectScene(page, sceneB.name); await selectNode(page, nodeB1.name);
    await input(page, "text").fill(copyB1);
    await selectScene(page, sceneA.name); await selectNode(page, nodeA1.name);
    await button(page, "Brief").click();
    await openCanvas(page, sceneA.name, nodeA1.name);
    await expect(input(page, "text")).toHaveValue(copyA1);
    await expect(input(page, "X")).toHaveValue("411.25");
    await paint(page); await page.reload();
    await openCanvas(page, sceneA.name, nodeA1.name);
    await revision(page, 9);
    await expect(input(page, "text")).toHaveValue(copyA1);
    await expect(input(page, "X")).toHaveValue("411.25");
    await expect(input(page, "Y")).toHaveValue("287");
    await selectNode(page, nodeA2.name);
    await expect(input(page, "text")).toHaveValue(copyA2);
    await expect(input(page, "H")).toHaveValue("");
    await expect(button(page, "Commit transform")).toBeDisabled();
    await selectScene(page, sceneB.name); await selectNode(page, nodeB1.name);
    await expect(input(page, "text")).toHaveValue(copyB1);
    expect(await service.project(initial.id)).toEqual(baseline);
    expect(await service.history(initial.id, 0)).toEqual(journalAtNine);
    expect(applyRequests(service)).toHaveLength(requestsAtNine);
    evidence.observations.push({ label: "object_scene_workspace_reload_are_readonly", revision: 9,
      applyCount: requestsAtNine, objectIds: [nodeA1.id, nodeA2.id, nodeB1.id] });

    await selectScene(page, sceneA.name); await selectNode(page, nodeA1.name);
    const playheadBeforeKeyboardCommit = await page.locator(".timeline-header .timecode").innerText();
    await expect(button(page, "Play design preview")).toBeVisible();
    await button(page, "Commit text").focus(); await page.keyboard.press("Space");
    const copiedA1 = await checkpoint(service, page, evidence, initial.id, 10, "A1 copy saved independently of its pending transform");
    await expect(button(page, "Play design preview")).toBeVisible();
    await expect(button(page, "Pause design preview")).toHaveCount(0);
    await expect(page.locator(".timeline-header .timecode")).toHaveText(playheadBeforeKeyboardCommit);
    expect(applyRequests(service)).toHaveLength(requestsAtNine + 1);
    expect(await service.history(initial.id, 9)).toHaveLength(1);
    expect(storedNode(copiedA1, sceneA.id, nodeA1.id)).toEqual({ ...storedNode(baseline, sceneA.id, nodeA1.id), text: copyA1 });
    await expect(input(page, "X")).toHaveValue("411.25");
    await expect(input(page, "Y")).toHaveValue("287");
    await expect(button(page, "Commit transform")).toBeEnabled();
    await expect(page.getByRole("button", { name: "Canvas object " + nodeA1.name, exact: true })).toContainText(copyA1.trim());
    await button(page, "Commit transform").click();
    const movedA1 = await checkpoint(service, page, evidence, initial.id, 11, "A1 transform saved without changing another object");
    expect(storedNode(movedA1, sceneA.id, nodeA1.id)).toEqual({ ...storedNode(baseline, sceneA.id, nodeA1.id), text: copyA1, x: 411.25, y: 287 });
    expect(storedNode(movedA1, sceneA.id, nodeA2.id)).toEqual(storedNode(baseline, sceneA.id, nodeA2.id));
    await selectNode(page, nodeA2.name);
    await expect(input(page, "text")).toHaveValue(copyA2);
    await expect(input(page, "H")).toHaveValue("");
    await expect(button(page, "Commit text")).toBeEnabled();
    await button(page, "Commit text").click();
    const copiedA2 = await checkpoint(service, page, evidence, initial.id, 12, "A2 copy saved while its incomplete height remains local");
    expect(storedNode(copiedA2, sceneA.id, nodeA2.id)).toEqual({ ...storedNode(baseline, sceneA.id, nodeA2.id), text: copyA2 });
    await expect(input(page, "H")).toHaveValue("");
    await expect(button(page, "Commit transform")).toBeDisabled();
    await input(page, "H").fill("280"); await button(page, "Commit transform").click();
    const final = await checkpoint(service, page, evidence, initial.id, 13, "A2 corrected height explicitly committed");
    expect(storedNode(final, sceneA.id, nodeA2.id)).toEqual({ ...storedNode(baseline, sceneA.id, nodeA2.id), text: copyA2, height: 280 });
    expect(storedNode(final, sceneA.id, nodeA1.id)).toEqual(storedNode(movedA1, sceneA.id, nodeA1.id));
    expect(storedNode(final, sceneB.id, nodeB1.id)).toEqual(storedNode(baseline, sceneB.id, nodeB1.id));
    expect(final.brief).toEqual(baseline.brief);
    expect([final.id, final.generation, final.active_branch]).toEqual([initial.id, initial.generation, initial.active_branch]);
    const journal = await service.history(initial.id, 0);
    expect(journal.map(event => event.revision)).toEqual(Array.from({ length: 13 }, (_, index) => index + 1));
    expect(journal.map(event => event.change.type)).toEqual([
      "set_brief", "add_scene", "add_canvas_node", "update_canvas_text", "add_canvas_node", "update_canvas_text",
      "add_scene", "add_canvas_node", "update_canvas_text", "update_canvas_text", "transform_canvas_node",
      "update_canvas_text", "transform_canvas_node",
    ]);
    expect(applyRequests(service)).toHaveLength(13);
    const lastFour = applyRequests(service).slice(-4).map(request => request.request.change);
    expect(lastFour).toEqual([
      { type: "update_canvas_text", scene_id: sceneA.id, node_id: nodeA1.id, text: copyA1 },
      { type: "transform_canvas_node", scene_id: sceneA.id, node_id: nodeA1.id,
        transform: { ...transform(nodeA1), x: 411.25, y: 287 } },
      { type: "update_canvas_text", scene_id: sceneA.id, node_id: nodeA2.id, text: copyA2 },
      { type: "transform_canvas_node", scene_id: sceneA.id, node_id: nodeA2.id,
        transform: { ...transform(nodeA2), height: 280 } },
    ]);
    await page.screenshot({ path: testInfo.outputPath("thirteen-real-revisions-authored-canvas.png"), fullPage: true });

    await service.close(); await service.start(); await page.reload();
    await openCanvas(page, sceneA.name, nodeA1.name); await revision(page, 13);
    await expect(input(page, "text")).toHaveValue(copyA1);
    await expect(input(page, "X")).toHaveValue("411.25");
    await selectNode(page, nodeA2.name);
    await expect(input(page, "text")).toHaveValue(copyA2);
    await expect(input(page, "H")).toHaveValue("280");
    await selectScene(page, sceneB.name); await selectNode(page, nodeB1.name);
    await expect(input(page, "text")).toHaveValue(copyB1);
    await expect(button(page, "Commit text")).toBeEnabled();
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id, 0)).toEqual(journal);
    expect(applyRequests(service)).toHaveLength(13);
    await page.screenshot({ path: testInfo.outputPath("reopened-saved-project-and-uncommitted-object-copy.png"), fullPage: true });
  } finally { await retainEvidence(testInfo, [service], evidence, [() => page.close()]); }
});

test("an independent peer wins overlapping copy while disjoint geometry and locked drafts are preserved", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const evidence: ScenarioEvidence = { checkpoints: [], observations: [] };
  let peer: ScopedChangeService | undefined;
  let gate: ReturnType<typeof holdNextApply> | undefined;
  try {
    const initial = (await service.fixtureBootstrap()).project;
    evidence.initial = initial; evidence.final = initial;
    const scene = sceneNamed(initial, "Independent synthetic scene");
    const target = scene.nodes[0];
    expect(target).toBeDefined();
    expect(target.property_locks).toEqual([]);
    peer = await service.peer();
    await service.attachFixturePage(page, initial.id); await page.goto("/");
    await openCanvas(page, scene.name, target.name);
    const myText = "Client A pending correction";
    const savedText = "Client B saved correction";
    const myX = target.x + 37, peerY = target.y + 19;
    await input(page, "text").fill(myText); await input(page, "X").fill(String(myX));
    gate = holdNextApply(service, "update_canvas_text", "before");
    await button(page, "Commit text").click();
    const submitted = await gate.entered;
    expect(submitted.request.revision).toBe(initial.revision);
    expect(submitted.request.effect_grant).toBeTruthy();
    expect(submitted.request.change).toEqual({ type: "update_canvas_text", scene_id: scene.id, node_id: target.id, text: myText });
    const copied = await peer.apply(initial, { type: "update_canvas_text", scene_id: scene.id, node_id: target.id, text: savedText });
    evidence.final = copied; evidence.checkpoints.push({ label: "peer saved overlapping copy", project: copied });
    const winner = await peer.apply(copied, { type: "transform_canvas_node", scene_id: scene.id, node_id: target.id,
      transform: { ...transform(storedNode(copied, scene.id, target.id)), y: peerY } });
    evidence.final = winner; evidence.checkpoints.push({ label: "peer saved disjoint Y geometry", project: winner });
    gate.release();
    const refused = await gate.observed;
    expect("error" in refused).toBe(true);
    await expect(inspector(page).getByRole("alert").filter({ hasText: "stale" })).toBeVisible();
    await expect(input(page, "text")).toHaveValue(myText);
    await expect(input(page, "X")).toHaveValue(String(myX));
    expect(await service.project(initial.id)).toEqual(winner);
    const winningJournal = await service.history(initial.id, initial.revision);
    expect(winningJournal.map(event => event.change.type)).toEqual(["update_canvas_text", "transform_canvas_node"]);
    await gate.dispose();

    // checked_project rejects the stale authority before Store.apply. This is
    // an inter-process stale-stamp negative, not a claimed SQLite commit race.
    await page.reload(); await openCanvas(page, scene.name, target.name);
    await revision(page, initial.revision + 2);
    const conflicts = page.getByRole("region", { name: "Canvas conflicts", exact: true });
    const textConflict = conflicts.locator("section[aria-label='Text conflict']");
    await expect(textConflict).toContainText(target.text!);
    await expect(textConflict).toContainText(savedText);
    await expect(textConflict).toContainText(myText);
    await expect(conflicts.locator("section[aria-label='X conflict']")).toHaveCount(0);
    await expect(conflicts.locator("section[aria-label='Y conflict']")).toHaveCount(0);
    await expect(input(page, "X")).toHaveValue(String(myX));
    await expect(input(page, "Y")).toHaveValue(String(peerY));
    await expect(button(page, "Commit text")).toBeDisabled();
    await expect(button(page, "Commit transform")).toBeEnabled();
    await conflicts.screenshot({ path: testInfo.outputPath("real-peer-canvas-conflict-three-values.png") });
    await button(page, "Keep saved Text").click();
    await expect(input(page, "text")).toHaveValue(savedText);
    await expect(input(page, "text")).toBeFocused();
    await expect(conflicts).toHaveCount(0);
    expect(await service.project(initial.id)).toEqual(winner);
    expect(await service.history(initial.id, initial.revision)).toEqual(winningJournal);
    await button(page, "Commit transform").click();
    const merged = await checkpoint(service, page, evidence, initial.id, initial.revision + 3, "A explicitly saved only disjoint X");
    expect(storedNode(merged, scene.id, target.id)).toEqual({ ...target, text: savedText, x: myX, y: peerY });
    expect(merged.scenes.filter(item => item.id !== scene.id)).toEqual(initial.scenes.filter(item => item.id !== scene.id));
    expect(await service.history(initial.id, initial.revision)).toHaveLength(3);
    const request = applyRequests(service).at(-1)!;
    expect(request.request.change).toEqual({ type: "transform_canvas_node", scene_id: scene.id, node_id: target.id,
      transform: { ...transform(target), x: myX, y: peerY } });

    const lockedText = "  Pending text retained under a saved lock\n";
    const lockedX = String(myX + 91.25);
    await input(page, "text").fill(lockedText); await input(page, "X").fill(lockedX);
    let locked = await peer.apply(merged, { type: "set_node_property_lock", scene_id: scene.id, node_id: target.id, property: "text", locked: true });
    evidence.final = locked;
    locked = await peer.apply(locked, { type: "set_node_property_lock", scene_id: scene.id, node_id: target.id, property: "position", locked: true });
    evidence.final = locked; evidence.checkpoints.push({ label: "peer saved text and position locks", project: locked });
    await page.reload(); await openCanvas(page, scene.name, target.name);
    await revision(page, initial.revision + 5);
    await expect(input(page, "text")).toHaveValue(lockedText);
    await expect(input(page, "X")).toHaveValue(lockedX);
    await expect(input(page, "text")).toBeDisabled(); await expect(input(page, "X")).toBeDisabled();
    await expect(button(page, "Commit text")).toBeDisabled(); await expect(button(page, "Commit transform")).toBeDisabled();
    await expect(page.locator(".property-locks").getByRole("button", { name: "text", exact: true })).toHaveClass(/active/);
    await expect(page.locator(".property-locks").getByRole("button", { name: "position", exact: true })).toHaveClass(/active/);
    const journalBeforeRejections = await service.history(initial.id, 0);
    const forbidden: Change[] = [
      { type: "update_canvas_text", scene_id: scene.id, node_id: target.id, text: "Forbidden saved-lock overwrite" },
      { type: "transform_canvas_node", scene_id: scene.id, node_id: target.id,
        transform: { ...transform(storedNode(locked, scene.id, target.id)), x: myX + 200 } },
    ];
    for (const change of forbidden) {
      await expect(service.apply(locked, change)).rejects.toThrow(/locked/);
      expect(await service.project(initial.id)).toEqual(locked);
      expect(await service.history(initial.id, 0)).toEqual(journalBeforeRejections);
    }
    expect(await peer.reopen(initial.id)).toEqual(locked);
    await page.reload(); await openCanvas(page, scene.name, target.name);
    await expect(input(page, "text")).toHaveValue(lockedText);
    await expect(input(page, "X")).toHaveValue(lockedX);
    await expect(input(page, "text")).toBeDisabled(); await expect(input(page, "X")).toBeDisabled();
    expect(await service.project(initial.id)).toEqual(locked);
    expect(await service.history(initial.id, 0)).toEqual(journalBeforeRejections);
    const processA = service.transcript.find(entry => entry.direction === "request")?.processId;
    const processB = peer.transcript.find(entry => entry.direction === "request")?.processId;
    expect(processA).toBeDefined(); expect(processB).toBeDefined(); expect(processA).not.toBe(processB);
    evidence.observations.push({ label: "stale_authority_and_current_stamp_lock_rejections", processA, processB,
      staleRequest: submitted.request, rejectedLockedChanges: forbidden,
      journalLengthBeforeAndAfterLocksRejected: journalBeforeRejections.length });
    await page.screenshot({ path: testInfo.outputPath("locked-saved-object-keeps-uncommitted-fields.png"), fullPage: true });
  } finally {
    await retainEvidence(testInfo, peer ? [service, peer] : [service], evidence,
      [...(gate ? [() => gate!.dispose()] : []), () => page.close()]);
  }
});

test("a late real transform acknowledgement preserves newer invalid raw input without replay after reload", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const evidence: ScenarioEvidence = { checkpoints: [], observations: [] };
  let gate: ReturnType<typeof holdNextApply> | undefined;
  try {
    const initial = (await service.fixtureBootstrap()).project;
    evidence.initial = initial; evidence.final = initial;
    const scene = sceneNamed(initial, "Independent synthetic scene");
    const target = scene.nodes[0];
    expect(target).toBeDefined();
    await service.attachFixturePage(page, initial.id); await page.goto("/");
    await openCanvas(page, scene.name, target.name);
    const sentTransform = { ...transform(target), x: target.x + 83, y: target.y + 41 };
    await input(page, "X").fill(String(sentTransform.x)); await input(page, "Y").fill(String(sentTransform.y));
    gate = holdNextApply(service, "transform_canvas_node", "after");
    await button(page, "Commit transform").click();
    const submitted = await gate.entered;
    expect(submitted.request.change).toEqual({ type: "transform_canvas_node", scene_id: scene.id, node_id: target.id, transform: sentTransform });
    const reply = await gate.observed;
    expect("project" in reply).toBe(true);
    if (!("project" in reply)) throw new Error("The actual transform service command was rejected");
    const committed = reply.project;
    evidence.final = committed; evidence.checkpoints.push({ label: "real transform saved before its browser acknowledgement", project: committed });
    expect(committed.revision).toBe(initial.revision + 1);
    expect(await service.project(initial.id)).toEqual(committed);
    expect(storedNode(committed, scene.id, target.id)).toEqual({ ...target, ...sentTransform });
    await revision(page, initial.revision);
    const laterText = "  A later object copy, outside the submitted transform\n";
    await input(page, "X").fill("1e"); await input(page, "text").fill(laterText);
    await expect(input(page, "X")).toHaveValue("1e");
    await expect(input(page, "X")).toHaveAttribute("aria-invalid", "true");
    await paint(page);
    await page.screenshot({ path: testInfo.outputPath("real-transform-saved-reply-held-new-invalid-input.png"), fullPage: true });
    // The saved result is still withheld. Returning to the object mounts a new
    // inspector over the shared input session and its unconfirmed snapshot.
    await button(page, "Brief").click(); await openCanvas(page, scene.name, target.name);
    await expect(input(page, "X")).toHaveValue("1e");
    await expect(input(page, "text")).toHaveValue(laterText);
    await expect(page.getByRole("region", { name: "Unconfirmed Canvas submission", exact: true })).toBeVisible();
    expect(applyRequests(service)).toHaveLength(1);
    gate.release();
    await revision(page, initial.revision + 1);
    await expect(input(page, "X")).toHaveValue("1e");
    await expect(input(page, "Y")).toHaveValue(String(sentTransform.y));
    await expect(input(page, "text")).toHaveValue(laterText);
    await expect(button(page, "Commit transform")).toBeDisabled();
    await expect(button(page, "Commit text")).toBeEnabled();
    await gate.dispose();

    const requestsAfterAcknowledgement = applyRequests(service).length;
    expect(requestsAfterAcknowledgement).toBe(1);
    const journalAfterAcknowledgement = await service.history(initial.id, initial.revision);
    expect(journalAfterAcknowledgement.map(event => event.change)).toEqual([submitted.request.change]);
    await selectScene(page, initial.scenes[0].name);
    await selectScene(page, scene.name); await selectNode(page, target.name);
    await expect(input(page, "X")).toHaveValue("1e");
    await button(page, "Brief").click(); await openCanvas(page, scene.name, target.name);
    await expect(input(page, "X")).toHaveValue("1e");
    await paint(page); await page.reload(); await openCanvas(page, scene.name, target.name);
    await revision(page, initial.revision + 1);
    await expect(input(page, "X")).toHaveValue("1e");
    await expect(input(page, "X")).toHaveAttribute("aria-invalid", "true");
    await expect(input(page, "text")).toHaveValue(laterText);
    await expect(button(page, "Commit transform")).toBeDisabled();
    await expect(button(page, "Commit text")).toBeEnabled();
    expect(await service.project(initial.id)).toEqual(committed);
    expect(await service.history(initial.id, initial.revision)).toEqual(journalAfterAcknowledgement);
    expect(applyRequests(service)).toHaveLength(requestsAfterAcknowledgement);

    await input(page, "X").fill("777.125");
    await button(page, "Commit transform").click();
    const final = await checkpoint(service, page, evidence, initial.id, initial.revision + 2, "later numeric input corrected and explicitly saved");
    expect(storedNode(final, scene.id, target.id)).toEqual({ ...target, ...sentTransform, x: 777.125 });
    expect(final.scenes.filter(item => item.id !== scene.id)).toEqual(initial.scenes.filter(item => item.id !== scene.id));
    await expect(input(page, "text")).toHaveValue(laterText);
    await expect(button(page, "Commit text")).toBeEnabled();
    expect(applyRequests(service)).toHaveLength(2);
    expect(applyRequests(service).map(item => item.request.change)).toEqual([
      submitted.request.change,
      { type: "transform_canvas_node", scene_id: scene.id, node_id: target.id,
        transform: { ...sentTransform, x: 777.125 } },
    ]);
    const journal = await service.history(initial.id, initial.revision);
    expect(journal.map(event => event.revision)).toEqual([initial.revision + 1, initial.revision + 2]);
    expect(journal.every(event => event.change.type === "transform_canvas_node")).toBe(true);
    expect(await service.reopen(initial.id)).toEqual(final);
    await page.reload(); await openCanvas(page, scene.name, target.name);
    await expect(input(page, "X")).toHaveValue("777.125");
    await expect(input(page, "text")).toHaveValue(laterText);
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id, initial.revision)).toEqual(journal);
    expect(applyRequests(service)).toHaveLength(2);
    evidence.observations.push({ label: "late_ack_kept_newer_raw_and_did_not_replay", submitted: submitted.request,
      invalidRaw: "1e", laterUncommittedText: laterText, actualApplyCount: 2 });
    await page.screenshot({ path: testInfo.outputPath("late-input-corrected-and-other-copy-still-uncommitted.png"), fullPage: true });
  } finally {
    await retainEvidence(testInfo, [service], evidence,
      [...(gate ? [() => gate!.dispose()] : []), () => page.close()]);
  }
});

test("another tab restores foreign input explicitly and changed recovery bytes cannot be overwritten or discarded from an older view", async ({ page, context }, testInfo) => {
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const evidence: ScenarioEvidence = { checkpoints: [], observations: [] };
  let peer: ScopedChangeService | undefined;
  let second: Page | undefined;
  const prefix = "motionwright.canvas-draft.v1:";
  const sessionPrefix = "motionwright.canvas-session.v1:";
  const backupWarning = (target: Page) => target.getByRole("alert", { name: "Canvas draft backup warning", exact: true });
  async function activeCopy(target: Page, scope: string) {
    return target.evaluate(({ prefix, sessionPrefix, scope }) => {
      const writerId = sessionStorage.getItem(sessionPrefix + scope);
      if (!writerId) throw new Error("The active Canvas writer has no session pointer");
      for (const key of Object.keys(localStorage).filter(key => key.startsWith(prefix))) {
        const raw = localStorage.getItem(key);
        if (raw === null) continue;
        const value = JSON.parse(raw);
        if (value.scope === scope && value.writerId === writerId) return { key, raw, value };
      }
      throw new Error("The active Canvas writer has no retained recovery copy");
    }, { prefix, sessionPrefix, scope });
  }
  try {
    const initial = (await service.fixtureBootstrap()).project;
    evidence.initial = initial; evidence.final = initial;
    const scene = sceneNamed(initial, "Independent synthetic scene");
    const target = scene.nodes[0];
    expect(target).toBeDefined();
    const scope = JSON.stringify([initial.id, initial.generation, initial.active_branch, scene.id, target.id]);
    const foreignText = "  First tab's uncommitted authored copy\n";
    await service.attachFixturePage(page, initial.id); await page.goto("/");
    await openCanvas(page, scene.name, target.name);
    await input(page, "text").fill(foreignText); await paint(page);
    const firstCopy = await activeCopy(page, scope);
    expect(firstCopy.value.draft.scope).toBe(scope);
    expect(firstCopy.value.draft.fields.text.raw).toBe(foreignText);
    expect(firstCopy.value.draft.awaiting).toBeNull();

    // context.newPage shares localStorage, but unlike an opener-created tab it
    // has no inherited sessionStorage pointer to the first tab's writer.
    peer = await service.peer(); second = await context.newPage();
    await peer.attachFixturePage(second, initial.id); await second.goto("/");
    expect(await second.evaluate(key => sessionStorage.getItem(key), sessionPrefix + scope)).toBeNull();
    await openCanvas(second, scene.name, target.name);
    await expect(input(second, "text")).toHaveValue(target.text ?? "");
    await expect(button(second, "Commit text")).toBeDisabled();
    expect(await service.project(initial.id)).toEqual(initial);
    expect(await service.history(initial.id, initial.revision)).toEqual([]);
    expect(applyRequests(service)).toHaveLength(0); expect(applyRequests(peer)).toHaveLength(0);
    const summary = inspector(second).locator("summary").filter({ hasText: "Local Canvas recovery copies" });
    await expect(summary).toBeVisible(); await summary.click();
    const restore = second.getByRole("button", { name: "Restore Canvas draft " + firstCopy.value.writerId, exact: true });
    await expect(restore).toBeVisible(); await restore.click();
    await expect(input(second, "text")).toHaveValue(foreignText);
    await expect(button(second, "Commit text")).toBeEnabled();
    const restoredCopy = await activeCopy(second, scope);
    expect(restoredCopy.value.writerId).not.toBe(firstCopy.value.writerId);
    expect(restoredCopy.value.draft.fields.text.raw).toBe(foreignText);
    expect(await page.evaluate(key => localStorage.getItem(key), firstCopy.key)).toBe(firstCopy.raw);
    expect(await service.project(initial.id)).toEqual(initial);
    expect(await service.history(initial.id, initial.revision)).toEqual([]);
    await second.screenshot({ path: testInfo.outputPath("foreign-canvas-copy-restored-only-after-explicit-choice.png"), fullPage: true });

    // The second tab still displays the original first-writer entry. Mutating
    // localStorage from this same tab deliberately emits no storage event to
    // that tab, giving its later discard action a genuinely outdated view.
    const discard = second.getByRole("button", { name: "Discard Canvas draft " + firstCopy.value.writerId, exact: true });
    await expect(discard).toBeVisible();
    const externallyAuthoredText = "  A valid externally updated recovery copy\nKeep these exact bytes  ";
    const externalRaw = await second.evaluate(({ key, expectedRaw, text }) => {
      if (localStorage.getItem(key) !== expectedRaw) throw new Error("The arranged recovery source changed before this negative case");
      const value = JSON.parse(expectedRaw);
      if (!value.draft || value.draft.awaiting !== null || !Number.isSafeInteger(value.draft.fields.text.sequence)) {
        throw new Error("This case requires a valid idle Canvas draft");
      }
      value.draft.fields.text.raw = text;
      value.draft.fields.text.sequence += 1;
      value.updatedAt += 1;
      const changed = JSON.stringify(value);
      localStorage.setItem(key, changed);
      if (localStorage.getItem(key) !== changed) throw new Error("The valid external edit could not be arranged");
      return changed;
    }, { key: firstCopy.key, expectedRaw: firstCopy.raw, text: externallyAuthoredText });
    expect(externalRaw).not.toBe(firstCopy.raw);
    await expect(input(page, "text")).toHaveValue(foreignText);
    const laterFirstInput = "  The first tab's later edit stays in memory\n";
    await input(page, "text").fill(laterFirstInput);
    await expect(backupWarning(page)).toBeVisible();
    await expect(backupWarning(page)).toContainText("changed in another window");
    await expect(input(page, "text")).toHaveValue(laterFirstInput);
    expect(await page.evaluate(key => localStorage.getItem(key), firstCopy.key)).toBe(externalRaw);
    await button(page, "Commit text").click();
    await expect(inspector(page).getByRole("alert").filter({ hasText: "Nothing was sent" })).toBeVisible();
    await expect(backupWarning(page)).toBeVisible();
    await expect(input(page, "text")).toHaveValue(laterFirstInput);
    expect(await page.evaluate(key => localStorage.getItem(key), firstCopy.key)).toBe(externalRaw);
    expect(applyRequests(service)).toHaveLength(0);
    expect(await service.project(initial.id)).toEqual(initial);
    expect(await service.history(initial.id, initial.revision)).toEqual([]);

    await discard.click();
    await expect(backupWarning(second)).toBeVisible();
    await expect(backupWarning(second)).toContainText("changed after it was shown");
    expect(await second.evaluate(key => localStorage.getItem(key), firstCopy.key)).toBe(externalRaw);
    expect(await service.project(initial.id)).toEqual(initial);
    expect(await service.history(initial.id, initial.revision)).toEqual([]);
    await expect(input(second, "text")).toHaveValue(foreignText);
    await page.screenshot({ path: testInfo.outputPath("changed-active-recovery-copy-refuses-overwrite-and-keeps-input.png"), fullPage: true });

    // Explicit restoration created its own writer. That independently reviewed
    // copy remains usable even though overwriting/discarding the changed source
    // was refused. The actual service performs this one deliberate commit.
    await button(second, "Commit text").click();
    const final = await checkpoint(peer, second, evidence, initial.id, initial.revision + 1, "explicitly restored foreign copy committed by its new writer");
    expect(storedNode(final, scene.id, target.id)).toEqual({ ...target, text: foreignText });
    expect(final.scenes.filter(item => item.id !== scene.id)).toEqual(initial.scenes.filter(item => item.id !== scene.id));
    expect(await service.project(initial.id)).toEqual(final);
    expect(applyRequests(service)).toHaveLength(0); expect(applyRequests(peer)).toHaveLength(1);
    const journal = await service.history(initial.id, initial.revision);
    expect(journal.map(event => event.change)).toEqual([
      { type: "update_canvas_text", scene_id: scene.id, node_id: target.id, text: foreignText },
    ]);
    expect(await second.evaluate(key => localStorage.getItem(key), firstCopy.key)).toBe(externalRaw);
    await expect(input(page, "text")).toHaveValue(laterFirstInput);
    expect(await peer.reopen(initial.id)).toEqual(final);

    // A refused overwrite must have a usable recovery path. Fork the current
    // in-memory input without acquiring authority over the changed old copy.
    await button(page, "Keep Canvas input in a new recovery copy").click();
    await expect(backupWarning(page)).toHaveCount(0);
    const forkedCopy = await activeCopy(page, scope);
    expect(forkedCopy.value.writerId).not.toBe(firstCopy.value.writerId);
    expect(forkedCopy.value.writerId).not.toBe(restoredCopy.value.writerId);
    expect(forkedCopy.value.draft.fields.text.raw).toBe(laterFirstInput);
    expect(forkedCopy.value.draft.awaiting).toBeNull();
    expect(await page.evaluate(key => localStorage.getItem(key), firstCopy.key)).toBe(externalRaw);
    await page.reload(); await openCanvas(page, scene.name, target.name);
    await revision(page, initial.revision + 1);
    await expect(input(page, "text")).toHaveValue(laterFirstInput);
    const recoveredConflict = page.getByRole("region", { name: "Canvas conflicts", exact: true })
      .locator("section[aria-label='Text conflict']");
    await expect(recoveredConflict).toContainText(laterFirstInput);
    await expect(recoveredConflict).toContainText(foreignText);
    await expect(button(page, "Commit text")).toBeDisabled();
    await expect(backupWarning(page)).toHaveCount(0);
    expect(await page.evaluate(key => localStorage.getItem(key), firstCopy.key)).toBe(externalRaw);
    expect(applyRequests(service)).toHaveLength(0); expect(applyRequests(peer)).toHaveLength(1);
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id, initial.revision)).toEqual(journal);
    evidence.observations.push({ label: "refused_recovery_can_fork_and_reopen_without_replaying", scope,
      newWriterId: forkedCopy.value.writerId, restoredInput: laterFirstInput,
      protectedOriginalRaw: externalRaw, firstTabApplyCount: 0, secondTabApplyCount: 1 });

    evidence.observations.push({ label: "foreign_copy_requires_choice_and_valid_changed_bytes_are_preserved", scope,
      originalWriterId: firstCopy.value.writerId, restoredWriterId: restoredCopy.value.writerId,
      originalRaw: firstCopy.raw, externallyChangedRaw: externalRaw,
      blockedFirstTabApplyCount: 0, deliberateRestoredCopyApplyCount: 1,
      firstTabInMemoryInput: laterFirstInput });
  } finally {
    await retainEvidence(testInfo, peer ? [service, peer] : [service], evidence,
      [() => page.close(), ...(second ? [() => second!.close()] : [])]);
  }
});

test("lost reply after real Canvas commit cannot replay the already saved operation", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({
    databasePath: testInfo.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true,
  });
  const evidence: ScenarioEvidence = { checkpoints: [], observations: [] };
  const original = service.invoke.bind(service);
  let swallowed = false;
  try {
    const initial = (await service.fixtureBootstrap()).project;
    evidence.initial = initial; evidence.final = initial;
    const scene = sceneNamed(initial, "Independent synthetic scene"), target = scene.nodes[0];
    await service.attachFixturePage(page, initial.id); await page.goto("/");
    await openCanvas(page, scene.name, target.name);
    const savedText = "  First committed text despite lost transport acknowledgment  ";
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const result = await original<T>(command, args);
      const request = args.request as ApplyArgs["request"] | undefined;
      if (command === "apply_change" && request?.change.type === "update_canvas_text" && !swallowed) {
        swallowed = true;
        throw new Error("synthetic transport lost its reply after real SQLite commit");
      }
      return result;
    };
    await input(page, "text").fill(savedText);
    await button(page, "Commit text").click();
    await expect(page.getByRole("region", { name: "Unconfirmed Canvas submission", exact: true })).toBeVisible();
    await expect(button(page, "Commit text")).toBeDisabled();
    const actuallySaved = await service.project(initial.id);
    evidence.final = actuallySaved;
    evidence.checkpoints.push({ label: "write persisted before the lost reply", project: actuallySaved });
    expect(actuallySaved.revision).toBe(initial.revision + 1);
    expect(storedNode(actuallySaved, scene.id, target.id).text).toBe(savedText);
    const storedJournal = await service.history(initial.id, initial.revision);
    expect(storedJournal).toHaveLength(1);
    expect(storedJournal[0].change.type).toBe("update_canvas_text");
    expect(applyRequests(service)).toHaveLength(1);
    await button(page, "Brief").click();
    await openCanvas(page, scene.name, target.name);
    await expect(page.getByRole("region", { name: "Unconfirmed Canvas submission", exact: true })).toBeVisible();
    expect(applyRequests(service)).toHaveLength(1);
    await page.reload();
    await openCanvas(page, scene.name, target.name);
    await revision(page, initial.revision + 1);
    await expect(input(page, "text")).toHaveValue(savedText);
    await expect(button(page, "Commit text")).toBeDisabled();
    expect(await service.project(initial.id)).toEqual(actuallySaved);
    expect(await service.history(initial.id, initial.revision)).toEqual(storedJournal);
    expect(applyRequests(service)).toHaveLength(1);
    evidence.observations.push({ label: "real_commit_unknown_reply_readback_no_replay", projectId: initial.id,
      savedRevision: actuallySaved.revision, applyCount: 1, simulatedFailure: "reply_only" });
  } finally {
    service.invoke = original;
    await retainEvidence(testInfo, [service], evidence, [() => page.close()]);
  }
});

test("Canvas pointer readback is required before real-service commit and explicit reload restores draft", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({
    databasePath: testInfo.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true,
  });
  const evidence: ScenarioEvidence = { checkpoints: [], observations: [] };
  try {
    const initial = (await service.fixtureBootstrap()).project;
    const scene = sceneNamed(initial, "Independent synthetic scene"), target = scene.nodes[0];
    evidence.initial = initial; evidence.final = initial;
    await service.attachFixturePage(page, initial.id); await page.goto("/");
    await openCanvas(page, scene.name, target.name);
    await page.evaluate(() => {
      const state = window as Window & { __denyCanvasPointer?: boolean };
      state.__denyCanvasPointer = true;
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key: string, raw: string) {
        if (this === window.sessionStorage && key.startsWith("motionwright.canvas-session.v1:") && state.__denyCanvasPointer)
          return;
        return original.call(this, key, raw);
      };
    });
    const authored = "  Draft surviving a silent session pointer failure  ";
    await input(page, "text").fill(authored);
    await expect(page.getByRole("alert", { name: "Canvas draft backup warning", exact: true }))
      .toContainText("session pointer could not be verified");
    await button(page, "Commit text").click();
    expect(applyRequests(service)).toHaveLength(0);
    expect(await service.project(initial.id)).toEqual(initial);
    expect(await service.history(initial.id, initial.revision)).toEqual([]);
    const copy = await page.evaluate((text) => {
      for (let index = 0; index < localStorage.length; index++) {
        const key = localStorage.key(index);
        if (!key?.startsWith("motionwright.canvas-draft.v1:")) continue;
        const raw = localStorage.getItem(key);
        if (!raw) continue;
        const value = JSON.parse(raw) as { writerId: string; draft: { fields: { text: { raw: string } } } | null };
        if (value.draft?.fields.text.raw === text) return { writerId: value.writerId, key, raw };
      }
      return null;
    }, authored);
    expect(copy).not.toBeNull();
    if (!copy) throw new Error("The local Canvas author copy was not retained.");
    await page.reload();
    await openCanvas(page, scene.name, target.name);
    await expect(input(page, "text")).not.toHaveValue(authored);
    await page.getByText("Local Canvas recovery copies", { exact: true }).click();
    await button(page, "Restore Canvas draft " + copy.writerId).click();
    await expect(input(page, "text")).toHaveValue(authored);
    expect(applyRequests(service)).toHaveLength(0);
    await button(page, "Commit text").click();
    const final = await checkpoint(service, page, evidence, initial.id, initial.revision + 1,
      "Restored local author input deliberately committed");
    expect(storedNode(final, scene.id, target.id).text).toBe(authored);
    const journal = await service.history(initial.id, initial.revision);
    expect(journal).toHaveLength(1);
    expect(journal[0].change.type).toBe("update_canvas_text");
    expect(applyRequests(service)).toHaveLength(1);
    await service.close(); await service.start();
    await page.reload(); await openCanvas(page, scene.name, target.name);
    await revision(page, final.revision);
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id, initial.revision)).toEqual(journal);
    expect(applyRequests(service)).toHaveLength(1);
    evidence.observations.push({
      label: "silent_pointer_failure_did_not_apply_and_explicit_restore_committed_once",
      retainedWriter: copy.writerId, restoredText: authored, revision: final.revision,
      originalApplyCount: 0, finalApplyCount: 1,
    });
  } finally {
    await retainEvidence(testInfo, [service], evidence, [() => page.close()]);
  }
});
