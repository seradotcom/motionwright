import { randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { expect, test as base, type Page } from "@playwright/test";
import type { Project } from "../src/types";
import { ScopedChangeService } from "./support/scopedChangeService";

// Actual desktop read/creation modules, grants and StudioService through the
// opt-in JSONL transport. Authored content and lost-browser-response injection
// are explicit test arrangements, not Tauri/native/human acceptance.
const PREFIX = "motionwright.project-creation.intent.v1:";
const SELECTION = "motionwright.project-library.selection.v1";
const test = base.extend<{ lifecycle: { service: ScopedChangeService; peers: ScopedChangeService[] } }>({
  lifecycle: async ({ page }, use, testInfo) => {
    const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3"),
      seedFixture: false, desktopInvokeFixture: true });
    const peers: ScopedChangeService[] = [];
    try { await service.attachPage(page, null); await use({ service, peers }); }
    finally {
      const oracles = [];
      const index = await service.invoke<{ items: { id: string }[]; truncated: boolean }>("list_projects", { request: { cursor: null, limit: 100 } });
      expect(index.truncated).toBe(false);
      for (const row of index.items) oracles.push(await service.observeStoredProject(await service.project(row.id)));
      const primary = oracles.at(0)?.project;
      try {
        if (primary) await service.finalizeClosedStore(testInfo.outputPath("closed-store-1"), primary, peers, [() => page.close()]);
        else {
          await page.close();
          const outcomes = await Promise.allSettled([service, ...peers].map(item => item.close()));
          await writeFile(testInfo.outputPath("empty-store-outcomes.json"), JSON.stringify(outcomes.map((item, n) => ({ status: item.status,
            ...(item.status === "rejected" ? { error: String(item.reason) } : {}), exits: [service, ...peers][n].processExits,
            taints: [service, ...peers][n].lifecycleTaints })), null, 2));
          for (const item of outcomes) if (item.status === "rejected") throw item.reason;
          await service.captureClosedStore(testInfo.outputPath("closed-store-1"), peers);
        }
      } finally {
        const evidence = { scope: "actual_service_sqlite_cas_and_browser_interaction", fixture: "synthetic_authored_test_content",
          excludes: ["installed_tauri", "native_realization", "human_acceptance"], status: testInfo.status,
          projectIndex: index, completeProjectOracles: oracles,
          services: [service, ...peers].map(item => ({ binary: item.binaryPath, transcript: item.transcript,
            exits: item.processExits, taints: item.lifecycleTaints, stderr: item.stderr })) };
        const path = testInfo.outputPath("project-lifecycle-evidence.json");
        await writeFile(path, JSON.stringify(evidence, null, 2));
        await testInfo.attach("project-lifecycle-evidence", { path, contentType: "application/json" });
      }
    }
  },
});
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "Build studio_review_session; no simulated service fallback.");
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const dialog = (page: Page) => page.getByRole("dialog", { name: "Project library", exact: true });
async function openLibrary(page: Page) {
  await page.locator(".app-shell, dialog[open]").first().waitFor();
  if (!await dialog(page).isVisible()) await button(page, "Open project library").click();
  await expect(dialog(page)).toBeVisible();
}
async function createFromUi(page: Page, title: string) {
  await openLibrary(page); await dialog(page).getByLabel("Project title", { exact: true }).fill(title);
  await dialog(page).getByRole("button", { name: "Create project", exact: true }).click();
  await expect(page.locator(".command-bar .revision-chip")).toBeVisible();
  if (await dialog(page).isVisible()) await button(page, "Close project library").click();
}
async function revision(page: Page, value: number) { await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + value); }
function calls(service: ScopedChangeService, command: string) { return service.transcript.filter(item => item.direction === "request" && "command" in item.payload && item.payload.command === command); }
async function creationCopies(page: Page) { return page.evaluate(prefix => Object.keys(localStorage).filter(key => key.startsWith(prefix)).map(key => ({ key, raw: localStorage.getItem(key)! })), PREFIX); }

async function createStored(service: ScopedChangeService, title: string) {
  const intent = { request_id: randomUUID(), title };
  const grant = await service.invoke<{ token: string }>("issue_project_creation_grant", { request: intent });
  const result = await service.invoke<{ project: Project }>("create_project", { request: { intent, effect_grant: grant.token } });
  return result.project;
}

test("empty library creates exact intent, edits Brief and Canvas through several revisions and reopens the same decisions", async ({ page, lifecycle }, testInfo) => {
  const { service } = lifecycle;
  await page.goto("/"); await openLibrary(page);
  await expect(dialog(page).getByText("No saved projects", { exact: true })).toBeVisible();
  expect(calls(service, "create_project")).toHaveLength(0);
  const title = "  Synthetic director study — Ámbito  ";
  await createFromUi(page, title); await revision(page, 0);
  let current = (await service.fixtureBootstrap()).project;
  expect(current.title).toBe(title); expect(current.scenes).toEqual([]); expect(current.assets).toEqual([]);
  await button(page, "Brief").click();
  await page.getByLabel("Production objective", { exact: true }).fill("Make the spatial comparison visible in an editable scene.");
  await page.getByLabel("Audience", { exact: true }).fill("Readers inspecting the authored canvas.");
  await button(page, "Commit brief").click(); await revision(page, 1);
  await button(page, "Add scene").click(); await page.getByLabel("New scene name").fill("Authored opening");
  await page.locator(".add-scene-form").getByRole("button", { name: "Add", exact: true }).click(); await revision(page, 2);
  await button(page, "Canvas").click(); await page.locator(".canvas-create-row").getByRole("button", { name: "text", exact: true }).click(); await revision(page, 3);
  await page.getByLabel("Canvas text", { exact: true }).fill("First authored decision"); await button(page, "Commit text").click(); await revision(page, 4);
  await page.getByLabel("Canvas text", { exact: true }).fill("Second reviewed decision"); await button(page, "Commit text").click(); await revision(page, 5);
  current = await service.project(current.id); expect(current.scenes[0].nodes[0].text).toBe("Second reviewed decision");
  expect(current.brief.objective).toBe("Make the spatial comparison visible in an editable scene.");
  await page.screenshot({ path: testInfo.outputPath("created-project-authored-canvas.png") });
  await service.close(); await service.start(); await page.reload(); await button(page, "Canvas").click();
  await revision(page, 5); await expect(page.getByLabel("Canvas text", { exact: true })).toHaveValue("Second reviewed decision");
  expect(await service.project(current.id)).toEqual(current);
  const second = await createStored(service, "Synthetic second independent project");
  await openLibrary(page); await dialog(page).getByRole("button", { name: "Open " + second.title, exact: true }).click(); await revision(page, 0);
  await openLibrary(page); await dialog(page).getByRole("button", { name: "Open " + title, exact: true }).click(); await revision(page, 5);
  expect(await service.project(current.id)).toEqual(current); expect(await service.project(second.id)).toEqual(second);
  expect(JSON.parse((await page.evaluate(key => localStorage.getItem(key), SELECTION))!).project_id).toBe(current.id);
  expect(await creationCopies(page)).toEqual([]);
});

test("lost creation response retains one original request and readonly recovery opens later saved revisions", async ({ page, lifecycle }) => {
  const { service, peers } = lifecycle;
  const original = service.invoke.bind(service); let interrupted = false, created: Project | undefined;
  service.invoke = async <T>(command: string, args: Record<string, unknown> = {}) => {
    const result = await original<T>(command, args);
    if (command === "create_project" && !interrupted) {
      interrupted = true; created = (result as { project: Project }).project;
      throw new Error("Injected browser response loss after the real creation committed");
    }
    return result;
  };
  try {
    await page.goto("/"); await openLibrary(page); await dialog(page).getByLabel("Project title").fill("Original exact creation title");
    await button(page, "Create project").click(); await expect(dialog(page).getByRole("alert")).toContainText("response loss");
    const retained = await creationCopies(page); expect(retained).toHaveLength(1); expect(created).toBeDefined();
    const peer = await service.peer(); peers.push(peer);
    let later = await peer.apply(created!, { type: "rename_project", title: "A later author's saved decision" });
    later = await peer.apply(later, { type: "add_scene", name: "Preserve this later scene", objective: "Keep later authority", duration_seconds: 4 });
    await page.reload(); await openLibrary(page); await button(page, "Check creation result").click(); await revision(page, later.revision);
    expect(await service.project(later.id)).toEqual(later);
    expect(calls(service, "create_project")).toHaveLength(1); expect(calls(service, "issue_project_creation_grant")).toHaveLength(1);
    expect(calls(service, "recover_project_creation")).toHaveLength(1); expect(await creationCopies(page)).toEqual([]);
    const originalReceipt = await service.invoke<{ project: Project }>("recover_project_creation", { request: JSON.parse(retained[0].raw).intent });
    expect(originalReceipt.project).toEqual(created); expect((await service.project(later.id)).revision).toBe(2);
  } finally { service.invoke = original; }
});

test("typing a second project title during first creation survives the empty-to-editor transition", async ({ page, lifecycle }) => {
  const { service } = lifecycle; const original = service.invoke.bind(service);
  const observed = Promise.withResolvers<void>(), release = Promise.withResolvers<void>(); let armed = true;
  service.invoke = async <T>(command: string, args: Record<string, unknown> = {}) => {
    const result = await original<T>(command, args);
    if (command === "create_project" && armed) { armed = false; observed.resolve(); await release.promise; }
    return result;
  };
  try {
    await page.goto("/"); await openLibrary(page); await dialog(page).getByLabel("Project title").fill("First project sent");
    await button(page, "Create project").click(); await observed.promise;
    await dialog(page).getByLabel("Project title").fill("The next title still being written");
    release.resolve(); await revision(page, 0);
    if (await dialog(page).isVisible()) await button(page, "Close project library").click();
    await openLibrary(page); await expect(dialog(page).getByLabel("Project title")).toHaveValue("The next title still being written");
    expect(calls(service, "create_project")).toHaveLength(1);
  } finally { release.resolve(); service.invoke = original; }
});

test("unknown retained creation is checked repeatedly without issuing a new key, grant or project", async ({ page, lifecycle }) => {
  const { service } = lifecycle; const intent = { request_id: randomUUID(), title: "An unconfirmed original action" };
  const key = PREFIX + intent.request_id, raw = JSON.stringify({ schema: "motionwright.project-creation-intent/1", intent, createdAt: Date.now(), phase: "unconfirmed", projectId: null });
  await page.addInitScript(({ key, raw }) => { if (!sessionStorage.getItem("unknown-arranged")) { localStorage.setItem(key, raw); sessionStorage.setItem("unknown-arranged", "yes"); } }, { key, raw });
  await page.goto("/"); await openLibrary(page);
  for (let check = 0; check < 3; check++) {
    await button(page, "Check creation result").click(); await expect(dialog(page).getByRole("status").filter({ hasText: "No creation receipt was found" })).toBeVisible();
    expect(await creationCopies(page)).toEqual([{ key, raw }]);
  }
  expect(calls(service, "recover_project_creation")).toHaveLength(3); expect(calls(service, "create_project")).toHaveLength(0); expect(calls(service, "issue_project_creation_grant")).toHaveLength(0);
  expect((await service.invoke<{ items: unknown[] }>("list_projects", { request: { cursor: null, limit: 20 } })).items).toEqual([]);
});

for (const changed of ["removed", "changed", "unreadable"] as const) {
  test(`a ${changed} local request during a real pending grant prevents creation`, async ({ page, lifecycle }) => {
    const { service } = lifecycle;
    const original = service.invoke.bind(service);
    const granted = Promise.withResolvers<void>(), release = Promise.withResolvers<void>();
    service.invoke = async <T>(command: string, args: Record<string, unknown> = {}) => {
      const result = await original<T>(command, args);
      if (command === "issue_project_creation_grant") { granted.resolve(); await release.promise; }
      return result;
    };
    try {
      await page.goto("/"); await openLibrary(page);
      await dialog(page).getByLabel("Project title").fill("The title remains editable after refusal");
      await button(page, "Create project").click(); await granted.promise;
      const [copy] = await creationCopies(page);
      expect(copy).toBeDefined();
      const altered = await page.evaluate(({ copy, changed }) => {
        if (changed === "removed") { localStorage.removeItem(copy.key); return null; }
        if (changed === "changed") {
          const value = JSON.parse(copy.raw); value.intent.title = "A different locally retained intention";
          const raw = JSON.stringify(value); localStorage.setItem(copy.key, raw); return raw;
        }
        const originalGet = Storage.prototype.getItem;
        (window as unknown as { restoreCreationRead: () => void }).restoreCreationRead = () => { Storage.prototype.getItem = originalGet; };
        Storage.prototype.getItem = function (key) {
          if (key === copy.key) throw new Error("Retained request unavailable during grant");
          return originalGet.call(this, key);
        };
        return copy.raw;
      }, { copy, changed });
      release.resolve();
      await expect(dialog(page).getByRole("alert").filter({ hasText: changed === "unreadable" ? "unavailable during grant" : "changed in another window" }).first()).toBeVisible();
      await expect(dialog(page).getByLabel("Project title")).toHaveValue("The title remains editable after refusal");
      expect(calls(service, "issue_project_creation_grant")).toHaveLength(1);
      expect(calls(service, "create_project")).toHaveLength(0);
      expect((await service.invoke<{ items: unknown[] }>("list_projects", { request: { cursor: null, limit: 20 } })).items).toEqual([]);
      if (changed === "unreadable") await page.evaluate(() => (window as unknown as { restoreCreationRead: () => void }).restoreCreationRead());
      expect(await creationCopies(page)).toEqual(altered === null ? [] : [{ key: copy.key, raw: altered }]);
    } finally { release.resolve(); service.invoke = original; }
  });
}

test("a missing remembered identity keeps the library available and never opens another project implicitly", async ({ page, lifecycle }) => {
  const { service } = lifecycle;
  const first = await createStored(service, "First healthy saved project");
  const second = await createStored(service, "Second healthy saved project");
  const missing = randomUUID();
  await page.addInitScript(({ key, projectId }) => localStorage.setItem(key, JSON.stringify({ schema: "motionwright.project-selection/1", project_id: projectId })), { key: SELECTION, projectId: missing });
  await page.goto("/"); await openLibrary(page);
  await expect(dialog(page).getByRole("button", { name: "Open " + first.title, exact: true })).toBeVisible();
  await expect(dialog(page).getByRole("button", { name: "Open " + second.title, exact: true })).toBeVisible();
  const bootstrap = calls(service, "bootstrap").at(-1)!.payload;
  if (!("command" in bootstrap)) throw new Error("Expected an observed bootstrap request");
  expect(bootstrap.args?.request).toEqual({ project_id: missing });
  expect(calls(service, "open_project")).toHaveLength(0);
  expect(calls(service, "create_project")).toHaveLength(2);
  await dialog(page).getByRole("button", { name: "Open " + first.title, exact: true }).click();
  await revision(page, 0);
  expect(await service.project(first.id)).toEqual(first); expect(await service.project(second.id)).toEqual(second);
  expect(JSON.parse((await page.evaluate(key => localStorage.getItem(key), SELECTION))!).project_id).toBe(first.id);
});

test("invalid UTF-8 length and unavailable continuity storage refuse creation while healthy projects still open", async ({ page, lifecycle }) => {
  const { service } = lifecycle;
  const saved = await createStored(service, "Healthy project during local quota failure");
  const writesBefore = calls(service, "create_project").length;
  await page.goto("/"); await openLibrary(page);
  await dialog(page).getByLabel("Project title").fill("🌘".repeat(51));
  await button(page, "Create project").click();
  await expect(dialog(page).getByRole("alert")).toContainText("This project title is too long.");
  expect(await creationCopies(page)).toEqual([]);
  await page.evaluate(prefix => {
    const originalSet = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith(prefix)) throw new DOMException("Controlled local quota failure", "QuotaExceededError");
      return originalSet.call(this, key, value);
    };
  }, PREFIX);
  await dialog(page).getByLabel("Project title").fill("Keep this unsent title");
  await button(page, "Create project").click();
  await expect(dialog(page).getByRole("alert")).toContainText("quota failure");
  await expect(dialog(page).getByLabel("Project title")).toHaveValue("Keep this unsent title");
  expect(calls(service, "create_project")).toHaveLength(writesBefore);
  expect(calls(service, "issue_project_creation_grant")).toHaveLength(writesBefore);
  expect(await creationCopies(page)).toEqual([]);
  await dialog(page).getByRole("button", { name: "Open " + saved.title, exact: true }).click(); await revision(page, 0);
  await openLibrary(page); await expect(dialog(page).getByLabel("Project title")).toHaveValue("Keep this unsent title");
  expect(await service.project(saved.id)).toEqual(saved);
});

test("a corrupt creation record can be downloaded intact and reviewed without blocking an existing project", async ({ page, lifecycle }, testInfo) => {
  const { service } = lifecycle;
  const saved = await createStored(service, "Continue editing with a damaged local request");
  const key = PREFIX + randomUUID(), raw = "{original damaged request\nkeep every byte — revisión";
  await page.addInitScript(({ key, raw }) => { if (!sessionStorage.getItem("corrupt-creation-arranged")) { localStorage.setItem(key, raw); sessionStorage.setItem("corrupt-creation-arranged", "yes"); } }, { key, raw });
  await page.goto("/"); await openLibrary(page);
  await dialog(page).getByLabel("Project title").fill("A later unsent title");
  await expect(button(page, "Create project")).toBeDisabled();
  const downloaded = page.waitForEvent("download"); await button(page, "Download request").click();
  const download = await downloaded, path = testInfo.outputPath("original-corrupt-request.json");
  await download.saveAs(path); expect(await readFile(path, "utf8")).toBe(raw);
  expect(await creationCopies(page)).toEqual([{ key, raw }]);
  await dialog(page).getByRole("button", { name: "Open " + saved.title, exact: true }).click(); await revision(page, 0);
  await page.reload(); await revision(page, 0); await openLibrary(page);
  expect(await creationCopies(page)).toEqual([{ key, raw }]);
  await button(page, "Remove local request").click();
  expect(await creationCopies(page)).toEqual([]);
  await dialog(page).getByLabel("Project title").fill("Ready for an explicit new creation");
  await expect(button(page, "Create project")).toBeEnabled();
  expect(await service.project(saved.id)).toEqual(saved);
  expect(calls(service, "create_project")).toHaveLength(1); expect(calls(service, "recover_project_creation")).toHaveLength(0);
});

test("real pagination preserves all identities and library keyboard focus survives repeated opens", async ({ page, lifecycle }, testInfo) => {
  const { service } = lifecycle, projects: Project[] = [];
  for (let index = 0; index < 25; index++) projects.push(await createStored(service, "Saved direction " + String(index + 1).padStart(2, "0")));
  await page.goto("/"); await openLibrary(page);
  const list = dialog(page).getByRole("list", { name: "Saved projects" });
  await expect(list.getByRole("listitem")).toHaveCount(20);
  await button(page, "Load more projects").focus(); await page.keyboard.press("Enter");
  await expect(list.getByRole("listitem")).toHaveCount(25);
  expect((await list.locator(".project-library-item-title strong").allTextContents()).sort()).toEqual(projects.map(p => p.title).sort());
  await expect(button(page, "Load more projects")).toHaveCount(0);
  const selected = projects[0];
  await dialog(page).getByRole("button", { name: "Open " + selected.title, exact: true }).focus(); await page.keyboard.press("Enter"); await revision(page, 0);
  const trigger = button(page, "Open project library");
  for (let attempt = 0; attempt < 2; attempt++) {
    await trigger.focus(); await page.keyboard.press("Enter"); await expect(dialog(page)).toBeVisible();
    await expect(dialog(page).getByRole("heading", { name: "Project library", exact: true })).toBeFocused();
    await page.keyboard.press("Shift+Tab"); await expect(dialog(page).getByRole("button", { name: "Close", exact: true })).toBeFocused();
    await page.keyboard.press("Tab"); await expect(button(page, "Close project library")).toBeFocused();
    await page.keyboard.press("Escape"); await expect(dialog(page)).not.toBeVisible(); await expect(trigger).toBeFocused();
  }
  await trigger.press("Enter"); await expect(dialog(page)).toBeVisible();
  await page.setViewportSize({ width: 1024, height: 768 });
  const bounds = await dialog(page).boundingBox(); expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0); expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(1024);
  expect(bounds!.y).toBeGreaterThanOrEqual(0); expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(768);
  await button(page, "Close project library").click({ trial: true });
  await page.screenshot({ path: testInfo.outputPath("library-25-projects-1024.png") });
  expect(calls(service, "create_project")).toHaveLength(25);
  for (const original of projects) expect(await service.project(original.id)).toEqual(original);
});
