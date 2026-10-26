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

test("structural removal denial is accessible and preserves authored hierarchy through repeated revisions and reopen", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3"),
    seedFixture: false, desktopInvokeFixture: true });
  const evidence: ScenarioEvidence = { checkpoints: [], observations: [] };
  const pageErrors: string[] = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  try {
    await service.attachPage(page, null);
    await page.goto("/");
    await expect(library(page)).toBeVisible();
    await library(page).getByLabel("Project title", { exact: true }).fill("Authored hierarchy survives a denied removal");
    await library(page).getByRole("button", { name: "Create project", exact: true }).click();
    await revision(page, 0);
    if (await library(page).isVisible()) await button(page, "Close project library").click();
    const initial = (await service.fixtureBootstrap()).project;
    evidence.initial = initial; evidence.final = initial;
    let at = 0;
    const saved = (label: string) => checkpoint(service, page, evidence, initial.id, ++at, label);
    await addScene(page, "Authored hierarchy");
    const scene = sceneNamed(await saved("scene created"), "Authored hierarchy");
    await openCanvas(page, scene.name);
    await page.locator(".canvas-create-row").getByRole("button", { name: "group", exact: true }).click();
    const group = nodeNamed(sceneNamed(await saved("group created"), scene.name), "Group 1");
    await page.locator(".canvas-create-row").getByRole("button", { name: "text", exact: true }).click();
    const child = nodeNamed(sceneNamed(await saved("editable child created"), scene.name), "Text 1");
    await selectNode(page, child.name);
    await input(page, "parent").selectOption(group.id);
    await input(page, "z order").fill("7");
    await button(page, "Commit hierarchy").click();
    await saved("child parent and order committed atomically");
    await input(page, "fill").fill("#38424d");
    await button(page, "Commit style").click();
    await saved("child ink explicitly authored");
    await input(page, "relation target").selectOption(group.id);
    await button(page, "Add canvas relation").click();
    const related = await saved("semantic relationship explicitly authored");
    const relationId = storedNode(related, scene.id, child.id).relations[0].id;
    await input(page, "text").fill("  Human-authored child copy  ");
    await button(page, "Commit text").click();
    await saved("child copy committed");
    await page.locator(".canvas-create-row").getByRole("button", { name: "text", exact: true }).click();
    const sibling = nodeNamed(sceneNamed(await saved("independent object created"), scene.name), "Text 2");
    await selectNode(page, sibling.name);
    await input(page, "text").fill("Independent copy before review");
    await button(page, "Commit text").click();
    const beforeDenial = await saved("independent object copy committed");
    const pendingCopy = "  Human decision retained after the structural denial\nStill editable  ";
    await input(page, "text").fill(pendingCopy);
    await input(page, "X").fill("411.25");
    const beforeJournal = await service.history(initial.id, 0);
    const beforeRequests = applyRequests(service).length;
    await selectNode(page, group.name);
    await button(page, "Remove object").focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("alert")).toContainText("children");
    await expect(button(page, "Remove object")).toBeFocused();
    await revision(page, at);
    expect(await service.project(initial.id)).toEqual(beforeDenial);
    expect(await service.history(initial.id, 0)).toEqual(beforeJournal);
    expect(applyRequests(service)).toHaveLength(beforeRequests + 1);
    expect(pageErrors).toEqual([]);
    evidence.observations.push({ label: "first structural denial changes no saved state", revision: at,
      rejectedApplyCount: 1, pageErrors: [...pageErrors] });
    await page.screenshot({ path: testInfo.outputPath("accessible-structural-denial-preserves-group-child-and-copy.png"), fullPage: true });

    await service.close(); await service.start(); await page.reload();
    await openCanvas(page, scene.name, sibling.name); await revision(page, at);
    await expect(input(page, "text")).toHaveValue(pendingCopy);
    await expect(input(page, "X")).toHaveValue("411.25");
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(await service.project(initial.id)).toEqual(beforeDenial);
    expect(await service.history(initial.id, 0)).toEqual(beforeJournal);
    expect(applyRequests(service)).toHaveLength(beforeRequests + 1);
    await button(page, "Commit text").click();
    await saved("recovered independent copy committed after restart");
    await button(page, "Commit transform").click();
    await saved("recovered independent geometry committed separately");
    await selectNode(page, child.name);
    await input(page, "text").fill("Revised child copy after a process restart");
    await button(page, "Commit text").click();
    await saved("child remains editable after denial and restart");
    await input(page, "fill").fill("#52756f");
    await button(page, "Commit style").click();
    const revised = await saved("second explicit ink revision");
    const revisedJournal = await service.history(initial.id, 0);
    const revisedRequests = applyRequests(service).length;
    await selectNode(page, group.name);
    await button(page, "Review Canvas draft on current revision").click();
    await button(page, "Remove object").focus();
    await page.keyboard.press("Space");
    await expect(page.getByRole("alert")).toContainText("children");
    await revision(page, at);
    expect(await service.project(initial.id)).toEqual(revised);
    expect(await service.history(initial.id, 0)).toEqual(revisedJournal);
    expect(applyRequests(service)).toHaveLength(revisedRequests + 1);
    expect(pageErrors).toEqual([]);

    await selectNode(page, child.name);
    await expect(page.getByRole("alert")).toHaveCount(0);
    await input(page, "parent").selectOption("");
    await button(page, "Commit hierarchy").click();
    await saved("author explicitly detaches child from group");
    await button(page, "Remove relation " + relationId).click();
    const detached = await saved("author explicitly removes semantic relationship");
    await selectNode(page, group.name);
    await button(page, "Review Canvas draft on current revision").click();
    await button(page, "Remove object").click();
    const final = await saved("now empty group is removed after explicit repairs");
    expect(sceneNamed(final, scene.name).nodes.some(node => node.id === group.id)).toBe(false);
    expect(storedNode(final, scene.id, child.id)).toEqual(storedNode(detached, scene.id, child.id));
    expect(storedNode(final, scene.id, child.id).text).toBe("Revised child copy after a process restart");
    expect(storedNode(final, scene.id, child.id).style.fill).toBe("#52756f");
    expect(storedNode(final, scene.id, child.id).parent_id).toBeNull();
    expect(storedNode(final, scene.id, child.id).relations).toEqual([]);
    expect(storedNode(final, scene.id, sibling.id)).toEqual({
      ...storedNode(beforeDenial, scene.id, sibling.id), text: pendingCopy, x: 411.25,
    });
    const finalJournal = await service.history(initial.id, 0);
    expect(finalJournal.map(event => event.revision)).toEqual(Array.from({ length: at }, (_, index) => index + 1));
    expect(finalJournal.filter(event => event.change.type === "remove_canvas_node")).toHaveLength(1);
    expect(at).toBe(16);
    await service.close(); await service.start(); await page.reload();
    await openCanvas(page, scene.name, child.name); await revision(page, at);
    await expect(input(page, "text")).toHaveValue("Revised child copy after a process restart");
    await selectNode(page, sibling.name);
    await expect(input(page, "text")).toHaveValue(pendingCopy);
    await expect(input(page, "X")).toHaveValue("411.25");
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id, 0)).toEqual(finalJournal);
    expect(pageErrors).toEqual([]);
    evidence.observations.push({ label: "sixteen revisions and two restarts preserve both authored objects",
      revision: at, deniedRemovals: 2, successfulRemovals: 1, pageErrors });
    await page.screenshot({ path: testInfo.outputPath("reopened-editable-work-after-explicit-hierarchy-repair.png"), fullPage: true });
  } finally { await retainEvidence(testInfo, [service], evidence, [() => page.close()]); }
});


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

test("authored Canvas style persists across two real revisions and reopen without losing uncommitted typography", async ({ page }, testInfo) => {
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

    await page.getByLabel("Canvas fill", { exact: true }).fill("#F7EAD7");
    await page.getByLabel("Canvas font size", { exact: true }).fill("1e");
    await expect(button(page, "Commit style")).toBeDisabled();
    await button(page, "Brief").click();
    await openCanvas(page, scene.name, target.name);
    await expect(page.getByLabel("Canvas fill", { exact: true })).toHaveValue("#F7EAD7");
    await expect(page.getByLabel("Canvas font size", { exact: true })).toHaveValue("1e");
    await page.reload(); await openCanvas(page, scene.name, target.name);
    await expect(page.getByLabel("Canvas fill", { exact: true })).toHaveValue("#F7EAD7");
    await expect(page.getByLabel("Canvas font size", { exact: true })).toHaveValue("1e");
    expect(applyRequests(service)).toHaveLength(0);
    expect(await service.project(initial.id)).toEqual(initial);

    await page.getByLabel("Canvas font size", { exact: true }).fill("64");
    await button(page, "Commit style").click();
    const first = await checkpoint(service, page, evidence, initial.id, initial.revision + 1, "saved original palette and typography");
    const firstNode = storedNode(first, scene.id, target.id);
    expect(firstNode.style.fill).toBe("#F7EAD7");
    expect(firstNode.style.font_size).toBe(64);
    expect(firstNode.style.stroke).toBe(target.style.stroke);
    expect(firstNode.text).toBe(target.text);
    expect(firstNode.id).toBe(target.id);
    expect((await service.history(initial.id,initial.revision)).map(event=>event.change.type)).toEqual(["update_canvas_style"]);

    await page.getByLabel("Canvas fill", { exact: true }).fill("#1A7D83");
    await page.getByLabel("Canvas line height", { exact: true }).fill("1e");
    await button(page, "Brief").click(); await openCanvas(page, scene.name, target.name);
    await expect(page.getByLabel("Canvas fill", { exact: true })).toHaveValue("#1A7D83");
    await expect(page.getByLabel("Canvas line height", { exact: true })).toHaveValue("1e");
    await page.getByLabel("Canvas line height", { exact: true }).fill("1.4");
    await button(page, "Commit style").click();
    const final = await checkpoint(service, page, evidence, initial.id, initial.revision + 2, "second style revision");
    expect(storedNode(final, scene.id, target.id).style).toEqual({
      ...firstNode.style, fill: "#1A7D83", line_height: 1.4,
    });
    const journal = await service.history(initial.id,initial.revision);
    expect(journal.map(event=>event.revision)).toEqual([initial.revision+1,initial.revision+2]);
    expect(journal.map(event=>event.change.type)).toEqual(["update_canvas_style","update_canvas_style"]);
    expect(applyRequests(service)).toHaveLength(2);
    await service.close(); await service.start(); await page.reload();
    await openCanvas(page, scene.name, target.name);
    await revision(page, final.revision);
    await expect(page.getByLabel("Canvas fill", { exact: true })).toHaveValue("#1A7D83");
    await expect(page.getByLabel("Canvas font size", { exact: true })).toHaveValue("64");
    await expect(page.getByLabel("Canvas line height", { exact: true })).toHaveValue("1.4");
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id,initial.revision)).toEqual(journal);
    evidence.observations.push({label:"style_two_revisions_exact_sqlite_reopen",saved:final.revision,nodeId:target.id,
      preservedText:target.text,applyCount:2});
  } finally {
    await retainEvidence(testInfo,[service],evidence,[()=>page.close()]);
  }
});

test("Canvas hierarchy retains parent and raw Z through two real revisions and a service restart", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({
    databasePath: testInfo.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true,
  });
  const evidence: ScenarioEvidence = { checkpoints: [], observations: [] };
  try {
    let initial = (await service.fixtureBootstrap()).project;
    const scene = sceneNamed(initial, "Independent synthetic scene"), target = scene.nodes[0];
    evidence.initial = initial; evidence.final = initial;
    await service.attachFixturePage(page, initial.id); await page.goto("/");
    await openCanvas(page, scene.name, target.name);
    // The bootstrap fixture has one object. Author the second through the
    // real Studio command, so both parent and child exist in SQLite.
    await page.locator(".canvas-create-row").getByRole("button", { name: "text", exact: true }).click();
    initial = await checkpoint(service, page, evidence, initial.id, initial.revision + 1, "authored second Canvas object");
    const other = sceneNamed(initial, scene.name).nodes[1];
    await selectNode(page, target.name);
    const beforeHierarchy = applyRequests(service).length;

    await page.getByLabel("Canvas parent", { exact: true }).selectOption({ value: other.id });
    await page.getByLabel("Canvas z order", { exact: true }).fill("1e");
    await expect(button(page, "Commit hierarchy")).toBeDisabled();
    await button(page, "Brief").click();
    await openCanvas(page, scene.name, target.name);
    await expect(page.getByLabel("Canvas parent", { exact: true })).toHaveValue(other.id);
    await expect(page.getByLabel("Canvas z order", { exact: true })).toHaveValue("1e");
    await page.reload(); await openCanvas(page, scene.name, target.name);
    await expect(page.getByLabel("Canvas parent", { exact: true })).toHaveValue(other.id);
    await expect(page.getByLabel("Canvas z order", { exact: true })).toHaveValue("1e");
    expect(applyRequests(service)).toHaveLength(beforeHierarchy);
    expect(await service.project(initial.id)).toEqual(initial);

    await page.getByLabel("Canvas z order", { exact: true }).fill("6");
    await button(page, "Commit hierarchy").click();
    const first = await checkpoint(service, page, evidence, initial.id, initial.revision + 1, "saved reparent and exact Z");
    expect(storedNode(first, scene.id, target.id)).toMatchObject({
      id: target.id, parent_id: other.id, z_index: 6, text: target.text, style: target.style,
    });
    expect((await service.history(initial.id,initial.revision)).map(event=>event.change.type))
      .toEqual(["reparent_canvas_node"]);

    await page.getByLabel("Canvas parent", { exact: true }).selectOption({ value: "" });
    await page.getByLabel("Canvas z order", { exact: true }).fill("9");
    await button(page, "Commit hierarchy").click();
    const final = await checkpoint(service, page, evidence, initial.id, initial.revision + 2, "saved scene root and later Z");
    expect(storedNode(final, scene.id, target.id)).toMatchObject({
      id: target.id, parent_id: null, z_index: 9, text: target.text, style: target.style,
    });
    const journal = await service.history(initial.id,initial.revision);
    expect(journal.map(event=>event.revision)).toEqual([initial.revision+1,initial.revision+2]);
    expect(journal.map(event=>event.change.type)).toEqual(["reparent_canvas_node","reparent_canvas_node"]);
    expect(applyRequests(service)).toHaveLength(beforeHierarchy + 2);
    await service.close(); await service.start(); await page.reload();
    await openCanvas(page, scene.name, target.name); await revision(page, final.revision);
    await expect(page.getByLabel("Canvas parent", { exact: true })).toHaveValue("");
    await expect(page.getByLabel("Canvas z order", { exact: true })).toHaveValue("9");
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id,initial.revision)).toEqual(journal);
    evidence.observations.push({ label: "hierarchy_two_revisions_full_reopen", finalRevision: final.revision,
      originalNodeId: target.id, originalText: target.text, applyCount: beforeHierarchy + 2 });
  } finally {
    await retainEvidence(testInfo,[service],evidence,[()=>page.close()]);
  }
});

test("Canvas relation add and remove preserve stable IDs and source intent through reopen", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({
    databasePath: testInfo.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true,
  });
  const evidence: ScenarioEvidence = { checkpoints: [], observations: [] };
  try {
    let baseline = (await service.fixtureBootstrap()).project;
    const scene = sceneNamed(baseline, "Independent synthetic scene"), target = scene.nodes[0];
    evidence.initial = baseline; evidence.final = baseline;
    await service.attachFixturePage(page, baseline.id); await page.goto("/");
    await openCanvas(page, scene.name, target.name);
    await page.locator(".canvas-create-row").getByRole("button", { name: "text", exact: true }).click();
    baseline = await checkpoint(service, page, evidence, baseline.id, baseline.revision + 1, "second object created for relation");
    const other = sceneNamed(baseline, scene.name).nodes[1];
    await selectNode(page, target.name);
    const preexistingApplyCount = applyRequests(service).length;

    await page.getByLabel("Canvas relation kind", { exact: true }).selectOption("align_right");
    await page.getByLabel("Canvas relation target", { exact: true }).selectOption(other.id);
    await button(page, "Brief").click();
    await openCanvas(page, scene.name, target.name);
    await expect(page.getByLabel("Canvas relation kind", { exact: true })).toHaveValue("align_right");
    await expect(page.getByLabel("Canvas relation target", { exact: true })).toHaveValue(other.id);
    await page.reload(); await openCanvas(page, scene.name, target.name);
    await expect(page.getByLabel("Canvas relation kind", { exact: true })).toHaveValue("align_right");
    await expect(page.getByLabel("Canvas relation target", { exact: true })).toHaveValue(other.id);
    expect(applyRequests(service)).toHaveLength(preexistingApplyCount);
    expect(await service.project(baseline.id)).toEqual(baseline);

    await button(page, "Add canvas relation").click();
    const linked = await checkpoint(service, page, evidence, baseline.id, baseline.revision + 1, "authored semantic relation");
    const savedRelation = storedNode(linked, scene.id, target.id).relations[0];
    expect(storedNode(linked, scene.id, target.id).relations).toHaveLength(1);
    expect(savedRelation).toMatchObject({ kind: "align_right", target_id: other.id });
    expect(savedRelation.id).toMatch(/^[0-9a-f-]{36}$/);
    expect(storedNode(linked, scene.id, target.id).text).toBe(target.text);
    expect((await service.history(baseline.id, baseline.revision)).map(e => e.change.type))
      .toEqual(["set_canvas_relations"]);

    await page.getByLabel("Canvas relation kind", { exact: true }).selectOption("align_right");
    await page.getByLabel("Canvas relation target", { exact: true }).selectOption(other.id);
    await expect(button(page, "Add canvas relation")).toBeDisabled();
    expect(applyRequests(service)).toHaveLength(preexistingApplyCount + 1);
    // Removing the existing ID is explicit. The duplicate composer choice
    // remains authored input and is not automatically submitted.
    await button(page, "Remove relation " + savedRelation.id).click();
    const unlinked = await checkpoint(service, page, evidence, baseline.id, baseline.revision + 2, "removed exact relation ID");
    expect(storedNode(unlinked, scene.id, target.id).relations).toEqual([]);
    expect(storedNode(unlinked, scene.id, target.id).text).toBe(target.text);
    const journal = await service.history(baseline.id, baseline.revision);
    expect(journal.map(event => event.revision)).toEqual([baseline.revision + 1, baseline.revision + 2]);
    expect(journal.map(event => event.change.type)).toEqual(["set_canvas_relations", "set_canvas_relations"]);
    expect(applyRequests(service)).toHaveLength(preexistingApplyCount + 2);
    await service.close(); await service.start(); await page.reload();
    await openCanvas(page, scene.name, target.name); await revision(page, unlinked.revision);
    expect(await service.project(baseline.id)).toEqual(unlinked);
    expect(await service.history(baseline.id, baseline.revision)).toEqual(journal);
    expect(applyRequests(service)).toHaveLength(preexistingApplyCount + 2);
    evidence.observations.push({
      label: "relations_two_exact_saved_revisions_and_reopen", id: savedRelation.id,
      originallyTargetedObject: other.id, originalTextPreserved: target.text,
      finalRevision: unlinked.revision, actionCount: 2,
    });
  } finally {
    await retainEvidence(testInfo, [service], evidence, [() => page.close()]);
  }
});

async function authoredRemovalFixture(page: Page, service: ScopedChangeService, evidence: ScenarioEvidence,
  targetKind: "group" | "text" = "text") {
  await service.attachPage(page, null); await page.goto("/");
  await expect(library(page)).toBeVisible();
  await library(page).getByLabel("Project title", { exact: true }).fill("Canvas removal recovery");
  await library(page).getByRole("button", { name: "Create project", exact: true }).click();
  await revision(page, 0);
  if (await library(page).isVisible()) await button(page, "Close project library").click();
  const initial = (await service.fixtureBootstrap()).project;
  evidence.initial = initial; evidence.final = initial;
  let at = initial.revision, current = initial;
  await addScene(page, "Removal recovery scene");
  current = await checkpoint(service, page, evidence, initial.id, ++at, "authored removal scene");
  let scene = sceneNamed(current, "Removal recovery scene");
  await openCanvas(page, scene.name);
  async function add(kind: "group" | "text") {
    const oldIds = new Set(scene.nodes.map(node => node.id));
    await page.locator(".canvas-create-row").getByRole("button", { name: kind, exact: true }).click();
    current = await checkpoint(service, page, evidence, initial.id, ++at, "authored removal " + kind);
    scene = sceneNamed(current, scene.name);
    const created = scene.nodes.filter(node => !oldIds.has(node.id));
    expect(created).toHaveLength(1);
    return created[0];
  }
  const target = await add(targetKind), survivor = await add("text");
  return { initial, project: current, scene, target, survivor };
}

async function retainedRemoval(page: Page, project: Project, sceneId: string, nodeId: string) {
  const scope = JSON.stringify([project.id, project.generation, project.active_branch, sceneId, nodeId]);
  return page.evaluate(scope => {
    const writer = sessionStorage.getItem("motionwright.canvas-session.v1:" + scope);
    for (const key of Object.keys(localStorage).filter(key => key.startsWith("motionwright.canvas-draft.v1:"))) {
      const raw = localStorage.getItem(key);
      if (raw === null) continue;
      const value = JSON.parse(raw) as import("../src/canvasWorkspaceDraft").CanvasRecovery;
      if (value.scope === scope && value.writerId === writer && value.draft?.awaiting?.group === "removal")
        return { key, raw, value };
    }
    throw new Error("The current window has no retained removal for the exact object scope");
  }, scope);
}

for (const guard of ["children", "incoming relations"] as const) {
  test("Canvas removal isolates the real " + guard + " denial and permits explicit repair after reopen", async ({ page }, testInfo) => {
    const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3"),
      seedFixture: false, desktopInvokeFixture: true });
    const evidence: ScenarioEvidence = { checkpoints: [], observations: [] }, pageErrors: string[] = [];
    page.on("pageerror", error => pageErrors.push(error.message));
    try {
      const authored = await authoredRemovalFixture(page, service, evidence, "group");
      const { initial, scene, target, survivor } = authored;
      await selectNode(page, survivor.name);
      if (guard === "children") {
        await input(page, "parent").selectOption(target.id); await button(page, "Commit hierarchy").click();
      } else {
        await input(page, "relation target").selectOption(target.id); await button(page, "Add canvas relation").click();
      }
      const protectedProject = await checkpoint(service, page, evidence, initial.id, authored.project.revision + 1, "isolated structural guard");
      const protectedScene = sceneNamed(protectedProject, scene.name);
      expect(protectedScene.nodes.some(node => node.parent_id === target.id)).toBe(guard === "children");
      expect(protectedScene.nodes.some(node => node.relations.some(relation => relation.target_id === target.id)))
        .toBe(guard === "incoming relations");
      const beforeHistory = await service.history(initial.id, 0), beforeCount = applyRequests(service).length;
      await selectNode(page, target.name); await button(page, "Remove object").focus(); await page.keyboard.press("Enter");
      await expect(inspector(page).getByRole("alert")).toContainText(guard);
      await expect(button(page, "Remove object")).toBeFocused();
      await expect(button(page, "Remove object")).toBeDisabled();
      expect(await service.project(initial.id)).toEqual(protectedProject);
      expect(await service.history(initial.id, 0)).toEqual(beforeHistory);
      expect(applyRequests(service)).toHaveLength(beforeCount + 1);
      const held = await retainedRemoval(page, protectedProject, scene.id, target.id);
      expect(held.value.draft?.awaiting?.revision).toBe(protectedProject.revision);
      await service.close(); await service.start(); await page.reload();
      await openCanvas(page, scene.name, target.name);
      await expect(button(page, "Remove object")).toBeDisabled();
      expect((await retainedRemoval(page, protectedProject, scene.id, target.id)).value.draft?.awaiting?.id)
        .toBe(held.value.draft?.awaiting?.id);
      await button(page, "Review Canvas draft on current revision").click();
      expect(applyRequests(service)).toHaveLength(beforeCount + 1);
      await selectNode(page, survivor.name);
      if (guard === "children") {
        await input(page, "parent").selectOption(""); await button(page, "Commit hierarchy").click();
      } else {
        const relation = storedNode(protectedProject, scene.id, survivor.id).relations[0];
        await button(page, "Remove relation " + relation.id).click();
      }
      const repaired = await checkpoint(service, page, evidence, initial.id, protectedProject.revision + 1, "explicit guard repair");
      await selectNode(page, target.name); await button(page, "Remove object").click();
      const removed = await checkpoint(service, page, evidence, initial.id, repaired.revision + 1, "removal after explicit review and repair");
      expect(sceneNamed(removed, scene.name).nodes.some(node => node.id === target.id)).toBe(false);
      expect(storedNode(removed, scene.id, survivor.id)).toEqual(storedNode(repaired, scene.id, survivor.id));
      expect((await service.history(initial.id, 0)).filter(event => event.change.type === "remove_canvas_node")).toHaveLength(1);
      expect(applyRequests(service)).toHaveLength(beforeCount + 3);
      expect(pageErrors).toEqual([]);
      evidence.observations.push({ label: "isolated service denial", guard, unchangedRevision: protectedProject.revision,
        deniedApplyCount: 1, committedRemovalCount: 1, priorLocalToken: held.value.draft?.awaiting?.id,
        localTokenIsServiceReceipt: false, pageErrors });
    } finally { await retainEvidence(testInfo, [service], evidence, [() => page.close()]); }
  });
}

for (const phase of ["before", "after"] as const) {
  test("Canvas removal stays pending through selection and remount " + phase + " the real commit response", async ({ page }, testInfo) => {
    const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3"),
      seedFixture: false, desktopInvokeFixture: true });
    const evidence: ScenarioEvidence = { checkpoints: [], observations: [] }, pageErrors: string[] = [];
    let held: ReturnType<typeof holdNextApply> | undefined;
    page.on("pageerror", error => pageErrors.push(error.message));
    try {
      const { initial, project, scene, target, survivor } = await authoredRemovalFixture(page, service, evidence);
      const copy = "  Unsent survivor copy during " + phase + " removal\n";
      await selectNode(page, survivor.name); await input(page, "text").fill(copy); await input(page, "X").fill("1e");
      await selectNode(page, target.name);
      const beforeCount = applyRequests(service).length;
      held = holdNextApply(service, "remove_canvas_node", phase);
      await button(page, "Remove object").click();
      const request = await held.entered;
      expect(request.request.change).toEqual({ type: "remove_canvas_node", scene_id: scene.id, node_id: target.id });
      expect(request.request.revision).toBe(project.revision);
      await expect(button(page, "Remove object")).toBeDisabled();
      await page.keyboard.press("Enter"); await page.keyboard.press("Space"); await paint(page);
      if (phase === "after") {
        const result = await held.observed;
        expect("project" in result).toBe(true);
      }
      expect(applyRequests(service)).toHaveLength(beforeCount + (phase === "after" ? 1 : 0));
      const retained = await retainedRemoval(page, project, scene.id, target.id);
      await selectNode(page, survivor.name);
      await expect(input(page, "text")).toHaveValue(copy); await expect(input(page, "X")).toHaveValue("1e");
      await selectNode(page, target.name);
      await expect(button(page, "Remove object")).toBeDisabled();
      await expect(button(page, "Review Canvas draft on current revision")).toHaveCount(0);
      await expect(button(page, "Discard Canvas object draft")).toBeDisabled();
      await expect(page.getByRole("region", { name: "Canvas removal recovery", exact: true })).toContainText("Removal pending");
      await button(page, "Remove object").focus(); await page.keyboard.press("Enter"); await paint(page);
      expect(applyRequests(service)).toHaveLength(beforeCount + (phase === "after" ? 1 : 0));
      held.release();
      const removed = await checkpoint(service, page, evidence, initial.id, project.revision + 1, "held removal settled");
      expect(sceneNamed(removed, scene.name).nodes.some(node => node.id === target.id)).toBe(false);
      await expect(page.getByRole("region", { name: "Canvas removal recovery", exact: true })).toContainText("absent in the saved project");
      await selectNode(page, survivor.name);
      await expect(input(page, "text")).toHaveValue(copy); await expect(input(page, "X")).toHaveValue("1e");
      expect(storedNode(removed, scene.id, survivor.id)).toEqual(storedNode(project, scene.id, survivor.id));
      expect(applyRequests(service)).toHaveLength(beforeCount + 1);
      await service.close(); await service.start(); await page.reload();
      await openCanvas(page, scene.name, survivor.name); await revision(page, removed.revision);
      await expect(input(page, "text")).toHaveValue(copy); await expect(input(page, "X")).toHaveValue("1e");
      expect(await service.project(initial.id)).toEqual(removed);
      expect(applyRequests(service)).toHaveLength(beforeCount + 1);
      expect((await service.history(initial.id, 0)).filter(event => event.change.type === "remove_canvas_node")).toHaveLength(1);
      expect(pageErrors).toEqual([]);
      evidence.observations.push({ label: "pending remount no replay", phase, actualRequestId: request.request.request_id,
        retainedLocalToken: retained.value.draft?.awaiting?.id, localTokenIsServiceReceipt: false,
        applyCount: 1, survivorDraft: copy, pageErrors });
    } finally {
      if (held) await held.dispose();
      await retainEvidence(testInfo, [service], evidence, [() => page.close()]);
    }
  });
}

test("Canvas removal committed with a lost reply stays unknown until real SQLite reopen and never replays", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3"),
    seedFixture: false, desktopInvokeFixture: true });
  const evidence: ScenarioEvidence = { checkpoints: [], observations: [] }, pageErrors: string[] = [];
  const original = service.invoke.bind(service);
  page.on("pageerror", error => pageErrors.push(error.message));
  try {
    const { initial, project, scene, target, survivor } = await authoredRemovalFixture(page, service, evidence);
    const copy = "  Authored survivor stays editable after an unknown removal\n";
    await selectNode(page, survivor.name); await input(page, "text").fill(copy); await input(page, "X").fill("1e");
    await selectNode(page, target.name);
    const beforeCount = applyRequests(service).length;
    let dropped = false;
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const result = await original<T>(command, args);
      const request = args.request as ApplyArgs["request"] | undefined;
      if (!dropped && command === "apply_change" && request?.change.type === "remove_canvas_node") {
        dropped = true;
        throw new Error("Synthetic removal reply loss after the real service committed");
      }
      return result;
    };
    await button(page, "Remove object").click();
    await expect(inspector(page).getByRole("alert")).toContainText("Synthetic removal reply loss");
    await expect(button(page, "Remove object")).toBeDisabled();
    const retained = await retainedRemoval(page, project, scene.id, target.id);
    await revision(page, project.revision);
    const saved = await service.project(initial.id); evidence.final = saved;
    expect(saved.revision).toBe(project.revision + 1);
    expect(sceneNamed(saved, scene.name).nodes.some(node => node.id === target.id)).toBe(false);
    const history = await service.history(initial.id, 0);
    expect(history.filter(event => event.change.type === "remove_canvas_node")).toHaveLength(1);
    expect(applyRequests(service)).toHaveLength(beforeCount + 1);
    await button(page, "Brief").click(); await openCanvas(page, scene.name, target.name);
    await expect(button(page, "Remove object")).toBeDisabled();
    expect(applyRequests(service)).toHaveLength(beforeCount + 1);
    await service.close(); await service.start(); await page.reload();
    await openCanvas(page, scene.name, survivor.name); await revision(page, saved.revision);
    await expect(page.locator(".canvas-tree-row").filter({ has: page.getByText(target.name, { exact: true }) })).toHaveCount(0);
    const recovery = page.getByRole("region", { name: "Canvas removal recovery", exact: true });
    await expect(recovery).toContainText("absent in the saved project");
    await expect(recovery).toContainText("does not identify which request");
    expect((await retainedRemoval(page, project, scene.id, target.id)).value.draft?.awaiting?.id)
      .toBe(retained.value.draft?.awaiting?.id);
    await expect(input(page, "text")).toHaveValue(copy); await expect(input(page, "X")).toHaveValue("1e");
    expect(await service.project(initial.id)).toEqual(saved);
    expect(await service.history(initial.id, 0)).toEqual(history);
    expect(applyRequests(service)).toHaveLength(beforeCount + 1);
    await button(page, "Commit text").click();
    const edited = await checkpoint(service, page, evidence, initial.id, saved.revision + 1, "survivor remains editable after reopen");
    expect(storedNode(edited, scene.id, survivor.id).text).toBe(copy);
    await expect(input(page, "X")).toHaveValue("1e");
    expect((await service.history(initial.id, 0)).filter(event => event.change.type === "remove_canvas_node")).toHaveLength(1);
    expect(applyRequests(service)).toHaveLength(beforeCount + 2);
    await recovery.getByRole("button", { name: "Discard local removal copy", exact: true }).click();
    await expect(recovery).toHaveCount(0);
    await expect(input(page, "X")).toHaveValue("1e");
    await expect(input(page, "text")).toHaveValue(copy);
    expect(await service.project(initial.id)).toEqual(edited);
    expect(applyRequests(service)).toHaveLength(beforeCount + 2);
    expect(pageErrors).toEqual([]);
    evidence.observations.push({ label: "real removal commit with transport reply loss and SQLite process reopen",
      committedRevision: saved.revision, reopenedRevision: saved.revision, retainedLocalToken: retained.value.draft?.awaiting?.id,
      localTokenIsServiceReceipt: false, removalApplyCount: 1, responseFailure: "reply_only", pageErrors });
  } finally {
    service.invoke = original;
    await retainEvidence(testInfo, [service], evidence, [() => page.close()]);
  }
});

for (const failingStore of ["record", "pointer"] as const) {
  test("Canvas removal sends nothing when its " + failingStore + " cannot be retained and verified", async ({ page }, testInfo) => {
    const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3"),
      seedFixture: false, desktopInvokeFixture: true });
    const evidence: ScenarioEvidence = { checkpoints: [], observations: [] };
    try {
      const { initial, project, scene, target } = await authoredRemovalFixture(page, service, evidence);
      await selectNode(page, target.name);
      const history = await service.history(initial.id, 0), beforeCount = applyRequests(service).length;
      await page.evaluate(store => {
        const original = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key: string, value: string) {
          if (store === "record" && this === window.localStorage && key.startsWith("motionwright.canvas-draft.v1:"))
            throw new Error("Synthetic removal recovery write failure");
          if (store === "pointer" && this === window.sessionStorage && key.startsWith("motionwright.canvas-session.v1:"))
            return; // Simulate a silent write loss; readback must block dispatch.
          return original.call(this, key, value);
        };
      }, failingStore);
      await button(page, "Remove object").click();
      await expect(inspector(page).getByRole("alert").filter({ hasText: "Nothing was sent" })).toBeVisible();
      await expect(page.getByRole("alert", { name: "Canvas draft backup warning", exact: true })).toBeVisible();
      expect(await service.project(initial.id)).toEqual(project);
      expect(await service.history(initial.id, 0)).toEqual(history);
      expect(applyRequests(service)).toHaveLength(beforeCount);
      evidence.observations.push({ label: "removal pre-dispatch recovery failure", failingStore, removalApplyCount: 0 });
    } finally { await retainEvidence(testInfo, [service], evidence, [() => page.close()]); }
  });
}

test("saved parented Canvas text stays visible in real editorial study after service reopen", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({
    databasePath: testInfo.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true,
  });
  const evidence: ScenarioEvidence = { checkpoints: [], observations: [] };
  try {
    let project = (await service.fixtureBootstrap()).project;
    const scene = sceneNamed(project, "Independent synthetic scene"), child = scene.nodes[0];
    evidence.initial = project; evidence.final = project;
    const initialJournal = await service.history(project.id, 0);
    const initialApplyCount = applyRequests(service).length;
    expect(child.coordinate_space).toBe("project_pixels");
    expect(child.parent_id).toBeNull();
    expect(child.keyframes).toEqual([]);
    await service.attachFixturePage(page, project.id); await page.goto("/");
    await openCanvas(page, scene.name, child.name);
    // Author the parent object through real Studio/SQLite rather than
    // fabricating an in-memory relationship in the browser.
    await page.locator(".canvas-create-row").getByRole("button", { name: "text", exact: true }).click();
    project = await checkpoint(service, page, evidence, project.id, project.revision + 1, "real second Canvas text parent");
    const parent = sceneNamed(project, scene.name).nodes[1];
    const childGeometry: CanvasTransform = {
      x: 316.25, y: 247.5, width: 484, height: 132, rotation_deg: 11, opacity: 0.8,
    };
    const firstParentGeometry: CanvasTransform = {
      x: 80, y: 60, width: 640, height: 240, rotation_deg: 30, opacity: 0.65,
    };
    const secondParentGeometry: CanvasTransform = {
      x: 1020, y: 540, width: 720, height: 200, rotation_deg: -25, opacity: 0.4,
    };
    const transformInputs = [["X", "x"], ["Y", "y"], ["W", "width"], ["H", "height"],
      ["Rotation", "rotation_deg"], ["Opacity", "opacity"] as const;
    const authorTransform = async (node: CanvasNode, geometry: CanvasTransform, label: string) => {
      await openCanvas(page, scene.name, node.name);
      for (const [label, field] of transformInputs) await input(page, label).fill(String(geometry[field]));
      await button(page, "Commit transform").click();
      project = await checkpoint(service, page, evidence, project.id, project.revision + 1, label);
      expect(storedNode(project, scene.id, node.id)).toEqual({ ...node, ...geometry });
    };
    await authorTransform(child, childGeometry, "explicit project-pixel child geometry saved before reparent");
    await authorTransform(parent, firstParentGeometry, "translated resized and rotated parent saved before reparent");
    await selectNode(page, child.name);
    await page.getByLabel("Canvas parent", { exact: true }).selectOption({ value: parent.id });
    await page.getByLabel("Canvas z order", { exact: true }).fill("8");
    await button(page, "Commit hierarchy").click();
    project = await checkpoint(service, page, evidence, project.id, project.revision + 1, "parented original text and project-pixel geometry saved");
    const parentedChild = storedNode(project, scene.id, child.id);
    expect(parentedChild).toEqual({ ...child, ...childGeometry, parent_id: parent.id, z_index: 8 });
    const inspect = async (parentGeometry: CanvasTransform, parentRotation: string) => {
      await button(page, "Production").click();
      await page.getByLabel("Production working scene", { exact: true })
        .selectOption({ value: scene.id });
      await button(page, "Scoped changes").click();
      const study = page.getByRole("img", { name: "Before scoped changes", exact: true });
      await expect(study).toBeVisible();
      const childSVG = study.locator('[data-canvas-node-id="' + child.id + '"]');
      const parentSVG = study.locator('[data-canvas-node-id="' + parent.id + '"]');
      await expect(childSVG).toHaveAttribute("data-parent-id", parent.id);
      await expect(childSVG).toBeVisible();
      await expect(childSVG.locator("text")).toBeVisible();
      // These are the child's own saved project coordinates, not an affine
      // composition with the parent or a claim about native Film geometry.
      await expect(childSVG).toHaveAttribute("transform", "rotate(11 558.25 313.5)");
      await expect(childSVG).toHaveAttribute("opacity", "0.8");
      await expect(childSVG.locator("text")).toHaveAttribute("x", "316.25");
      await expect(childSVG.locator("text")).toHaveAttribute("y", String(247.5 + (child.style.font_size ?? 30) * 0.88));
      await expect(childSVG.locator("text")).toContainText(child.text ?? "");
      await expect(parentSVG).toHaveAttribute("transform", parentRotation);
      await expect(parentSVG).toHaveAttribute("opacity", String(parentGeometry.opacity));
      await expect(parentSVG.locator("text")).toHaveAttribute("x", String(parentGeometry.x));
      expect(await study.locator(":scope > g[data-canvas-node-id]").evaluateAll(nodes =>
        nodes.map(node => node.getAttribute("data-canvas-node-id")))).toEqual([parent.id, child.id]);
      await expect(study).toContainText("Native Film");
      return childSVG.evaluate(group => {
        const text = group.querySelector("text");
        if (!text) throw new Error("The saved editable child lost its editorial text element");
        return {
          group: ["data-canvas-node-id", "data-parent-id", "transform", "opacity"]
            .map(name => [name, group.getAttribute(name)]),
          text: text.textContent,
          typographyAndPosition: ["x", "y", "fill", "font-family", "font-size", "font-weight"]
            .map(name => [name, text.getAttribute(name)]),
          lines: Array.from(text.querySelectorAll("tspan"), line => ({
            text: line.textContent, x: line.getAttribute("x"), dy: line.getAttribute("dy"),
          })),
        };
      });
    };
    const beforeParentChange = await inspect(firstParentGeometry, "rotate(30 400 180)");
    await authorTransform(storedNode(project, scene.id, parent.id), secondParentGeometry,
      "parent geometry changed without moving the project-pixel child");
    const saved = project;
    expect(storedNode(saved, scene.id, child.id)).toEqual(parentedChild);
    const afterParentChange = await inspect(secondParentGeometry, "rotate(-25 1380 640)");
    expect(afterParentChange).toEqual(beforeParentChange);
    const savedJournal = await service.history(saved.id, 0);
    expect(savedJournal.slice(0, initialJournal.length)).toEqual(initialJournal);
    expect(savedJournal.map(item => item.revision)).toEqual(
      Array.from({ length: saved.revision }, (_, index) => index + 1));
    expect(savedJournal.slice(initialJournal.length).map(item => item.change.type)).toEqual([
      "add_canvas_node", "transform_canvas_node", "transform_canvas_node", "reparent_canvas_node", "transform_canvas_node",
    ]);
    const applyCount = applyRequests(service).length;
    expect(applyCount - initialApplyCount).toBe(5);
    expect(applyRequests(service).slice(-4).map(item => item.request.change)).toEqual([
      { type: "transform_canvas_node", scene_id: scene.id, node_id: child.id, transform: childGeometry },
      { type: "transform_canvas_node", scene_id: scene.id, node_id: parent.id, transform: firstParentGeometry },
      { type: "reparent_canvas_node", scene_id: scene.id, node_id: child.id, parent_id: parent.id, z_index: 8 },
      { type: "transform_canvas_node", scene_id: scene.id, node_id: parent.id, transform: secondParentGeometry },
    ]);
    await service.close(); await service.start(); await page.reload();
    await revision(page, saved.revision);
    const afterReopen = await inspect(secondParentGeometry, "rotate(-25 1380 640)");
    expect(afterReopen).toEqual(beforeParentChange);
    await openCanvas(page, scene.name, child.name);
    for (const [label, field] of transformInputs) await expect(input(page, label)).toHaveValue(String(childGeometry[field]));
    await expect(page.getByLabel("Canvas parent", { exact: true })).toHaveValue(parent.id);
    await expect(page.getByLabel("Canvas z order", { exact: true })).toHaveValue("8");
    await expect(input(page, "text")).toHaveValue(child.text ?? "");
    expect(await service.project(saved.id)).toEqual(saved);
    expect(await service.history(saved.id, 0)).toEqual(savedJournal);
    expect(applyRequests(service)).toHaveLength(applyCount);
    evidence.observations.push({ label: "parented_subject_keeps_project_pixel_geometry_after_parent_transform_and_sqlite_reopen",
      childId: child.id, parentId: parent.id, savedRevision: saved.revision,
      coordinateSpace: "project_pixels", savedChild: parentedChild,
      firstParentGeometry, secondParentGeometry, beforeParentChange, afterParentChange, afterReopen,
      expectedSvgLayerZ: 8, exactStoredProjectAndFullJournal: true, appliedUiChanges: 5,
      nativeParentFidelity: "NOT_VERIFIED", localWorldConversion: "NOT_REQUESTED_OR_INFERRED" });
  } finally {
    await retainEvidence(testInfo, [service], evidence, [() => page.close()]);
  }
});

test("saved localized profile text overrides the editorial SVG across two revisions and reopening", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({
    databasePath: testInfo.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true,
  });
  const evidence: ScenarioEvidence = { checkpoints: [], observations: [] };
  try {
    let initial = (await service.fixtureBootstrap()).project;
    const scene = sceneNamed(initial, "Independent synthetic scene"), node = scene.nodes[0];
    expect(node.kind).toBe("text");
    evidence.initial = initial; evidence.final = initial;
    await service.attachFixturePage(page, initial.id); await page.goto("/");
    await button(page, "Deliver").click();
    const original = initial.deliverables[0];
    await button(page, "Derive selected").click();
    initial = await checkpoint(service, page, evidence, initial.id, initial.revision + 1, "derived profile from original");
    await page.getByLabel("Language / locale", { exact: true }).fill("es-MX");
    const localized = page.getByRole("region", { name: "Localized text", exact: true });
    await expect(localized).toBeVisible();
    const editors = localized.locator(".localized-text-ledger input");
    const editorCount = await editors.count();
    expect(editorCount).toBeGreaterThan(0);
    for (let index = 0; index < editorCount; index++) {
      await editors.nth(index).fill("Texto localizado original " + index);
    }
    await button(page, "Save profile").click();
    let saved = await checkpoint(service, page, evidence, initial.id, initial.revision + 1, "localized source-bound text committed");
    const variant = saved.deliverables.find(profile => profile.parent_profile_id === original.id);
    expect(variant).toBeDefined();
    if (!variant) throw new Error("A derived profile was not persisted.");
    const firstOverride = variant.text_overrides[node.id];
    expect(typeof firstOverride).toBe("string");
    expect(firstOverride).toMatch(/^Texto localizado original/);

    const profileIndex = page.getByRole("list", { name: "Delivery profile index", exact: true });
    async function compareSelectedProfileText(expected: string, origin: "profile" | "source") {
      await button(page, "Production").click();
      await page.getByLabel("Production working scene", { exact: true })
        .selectOption({ value: scene.id });
      await button(page, "Scoped changes").click();
      const svg = page.getByRole("img", { name: "Before scoped changes", exact: true });
      await expect(svg).toBeVisible();
      const originalNode = svg.locator('[data-canvas-node-id="' + node.id + '"] text');
      await expect(originalNode).toHaveAttribute("data-text-origin", origin);
      await expect(originalNode).toContainText(expected);
      // Camera reuses the same saved selected-profile text; no separate
      // localization cache or independent variant authority is allowed.
      await page.getByRole("navigation", { name: "Production tools" })
        .getByRole("button", { name: "Camera", exact: true }).click();
      const camera = page.getByRole("img", { name: /Planar camera at/ });
      await expect(camera).toBeVisible();
      const cameraNode = camera.locator('[data-canvas-node-id="' + node.id + '"] text');
      await expect(cameraNode).toHaveAttribute("data-text-origin", origin);
      await expect(cameraNode).toContainText(expected);
    }
    await page.getByRole("combobox", { name: "Editor preview timebase", exact: true }).selectOption(variant.id);
    await compareSelectedProfileText(firstOverride, "profile");
    await button(page, "Deliver").click();
    await profileIndex.getByText(original.name, { exact: true }).click();
    await page.getByRole("combobox", { name: "Editor preview timebase", exact: true }).selectOption(original.id);
    await expect(page.getByLabel("Profile name", { exact: true })).toHaveValue(original.name);
    await compareSelectedProfileText(node.text ?? "", "source");
    await button(page, "Deliver").click();
    await profileIndex.getByText(variant.name, { exact: true }).click();
    await page.getByRole("combobox", { name: "Editor preview timebase", exact: true }).selectOption(variant.id);
    await expect(page.getByLabel("Profile name", { exact: true })).toHaveValue(variant.name);
    const editorsNext = page.getByRole("region", { name: "Localized text", exact: true })
      .locator(".localized-text-ledger input");
    for (let index = 0; index < await editorsNext.count(); index++) {
      await editorsNext.nth(index).fill("Segunda decisión localizada " + index);
    }
    await button(page, "Save profile").click();
    saved = await checkpoint(service, page, evidence, initial.id, saved.revision + 1,
      "second localized author correction saved");
    const finalVariant = saved.deliverables.find(profile => profile.id === variant.id)!;
    const secondOverride = finalVariant.text_overrides[node.id];
    expect(secondOverride).toMatch(/^Segunda decisión localizada/);
    expect(secondOverride).not.toBe(firstOverride);
    await compareSelectedProfileText(secondOverride, "profile");

    expect(sceneNamed(saved, scene.name).nodes.find(item => item.id === node.id)?.text).toBe(node.text);
    expect(saved.deliverables.find(profile => profile.id === original.id)?.text_overrides[node.id]).toBeUndefined();
    const journal = await service.history(saved.id, evidence.initial.revision);
    expect(journal).toHaveLength(3);
    const applyCount = applyRequests(service).length;
    expect(applyCount).toBe(3);
    await service.close(); await service.start();
    await page.reload(); await revision(page, saved.revision);
    await button(page, "Deliver").click();
    await page.getByRole("list", { name: "Delivery profile index", exact: true })
      .getByText(finalVariant.name, { exact: true }).click();
    await page.getByRole("combobox", { name: "Editor preview timebase", exact: true }).selectOption(finalVariant.id);
    await compareSelectedProfileText(secondOverride, "profile");
    expect(await service.project(saved.id)).toEqual(saved);
    expect(await service.history(saved.id, evidence.initial.revision)).toEqual(journal);
    expect(applyRequests(service)).toHaveLength(applyCount);
    evidence.observations.push({
      label: "real_variant_text_controls_actual_svg_with_repeat_edit_reopen",
      originalProfileId: original.id, variantId: finalVariant.id, targetTextId: node.id,
      originalText: node.text, firstOverride, secondOverride, revision: saved.revision,
      journalCount: journal.length, nativeFrames: "NOT_VERIFIED",
    });
  } finally {
    await retainEvidence(testInfo, [service], evidence, [() => page.close()]);
  }
});
