/** Source-bound Studio layer promotion against one real original SQLite/CAS.
 * A browser test transport is not Tauri installation or Native render permission.
 * The parent launches the Native Host only after this original writer closes.
 */
import { createHash } from "node:crypto";
import { lstat, readFile, writeFile } from "node:fs/promises";
import { isAbsolute } from "node:path";
import { expect, test } from "@playwright/test";
import type { Project, ProjectEvent } from "../src/types";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(process.env.MOTIONWRIGHT_NATIVE_LAYER_ORDER_E2E !== "1",
  "Selected only for the existing exact-source one-project Native layer path.");
test.describe.configure({ timeout: 120_000, retries: 0 });

function selectedFile(name: string): string {
  const value = process.env[name];
  if (!value || !isAbsolute(value)) throw new Error("The source-bound layer path is unavailable: " + name);
  return value;
}

test("user promotes an overlapping Canvas layer and the exact Store can be reopened", async ({ page }) => {
  const databasePath = selectedFile("MOTIONWRIGHT_NATIVE_LAYER_DATABASE");
  const beforePath = selectedFile("MOTIONWRIGHT_NATIVE_LAYER_BEFORE");
  const observationPath = selectedFile("MOTIONWRIGHT_NATIVE_LAYER_OBSERVATION");
  const expectedSha = process.env.MOTIONWRIGHT_NATIVE_LAYER_BEFORE_SHA256;
  expect(expectedSha).toMatch(/^[a-f0-9]{64}$/);
  expect(process.env.GITHUB_SHA).toMatch(/^[a-f0-9]{40}$/);
  for (const file of [databasePath, beforePath]) {
    const entry = await lstat(file);
    expect(entry.isFile() && !entry.isSymbolicLink()).toBe(true);
  }
  const bytes = await readFile(beforePath);
  expect(bytes.length).toBeGreaterThan(0);
  expect(bytes.length).toBeLessThan(4 * 1024 * 1024);
  expect(createHash("sha256").update(bytes).digest("hex")).toBe(expectedSha);
  const before = JSON.parse(bytes.toString("utf8")) as {
    schema: string; phase: string; project: Project; history: ProjectEvent[];
  };
  expect(before.schema).toBe("motionwright.native-layer-order-source/1");
  expect(before.phase).toBe("before_ui");
  const source = before.project;
  const scene = source.scenes[0];
  expect(source.scenes).toHaveLength(1);
  const front = scene.nodes.filter(node => node.name === "Native layer order amber foreground");
  const back = scene.nodes.filter(node => node.name === "Native layer order blue background");
  expect(front).toHaveLength(1);
  expect(back).toHaveLength(1);
  const promotedZ = 9;
  expect({ z: front[0].z_index, parent: front[0].parent_id, color: front[0].style.fill,
    x: front[0].x, y: front[0].y }).toEqual({ z: 7, parent: null, color: "#E0A940", x: 405, y: 820 });
  expect({ z: back[0].z_index, parent: back[0].parent_id, color: back[0].style.fill })
    .toEqual({ z: 8, parent: null, color: "#2569B8" });
  const service = await ScopedChangeService.create({
    databasePath, seedFixture: false, desktopInvokeFixture: true,
  });
  let final: Project | null = null;
  let history: ProjectEvent[] | null = null;
  let completed = false;
  let closed = false;
  let saveCount = 0;
  try {
    expect(await service.project(source.id)).toEqual(source);
    expect(await service.history(source.id, 0, 64)).toEqual(before.history);
    await service.attachPage(page, source.id);
    await page.goto("/");
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + source.revision);
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await page.locator(".canvas-tree-row").filter({ hasText: front[0].name }).click();
    const edit = page.getByLabel("Canvas z order", { exact: true });
    await expect(edit).toHaveValue("7");
    await edit.fill(String(promotedZ));
    const save = page.getByRole("button", { name: "Commit hierarchy", exact: true });
    await expect(save).toBeEnabled();
    await save.click();
    saveCount++;
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + (source.revision + 1));
    final = await service.project(source.id);
    history = await service.history(source.id, 0, 64);
    expect(final.revision).toBe(source.revision + 1);
    expect(final.id).toBe(source.id);
    expect(final.generation).toBe(source.generation);
    expect(history.slice(0, before.history.length)).toEqual(before.history);
    expect(history).toHaveLength(before.history.length + 1);
    const changed = history.at(-1)?.change;
    expect(changed).toMatchObject({
      type: "reparent_canvas_node", scene_id: scene.id,
      node_id: front[0].id, parent_id: null, z_index: promotedZ,
    });
    const resulting = final.scenes[0].nodes;
    expect(resulting.find(node => node.id === front[0].id))
      .toEqual({ ...front[0], z_index: promotedZ });
    expect(resulting.filter(node => node.id !== front[0].id))
      .toEqual(scene.nodes.filter(node => node.id !== front[0].id));
    expect(final.assets).toEqual(source.assets);
    expect(final.deliverables).toEqual(source.deliverables);
    expect(final.audio).toEqual(source.audio);
    const requests = service.transcript.filter(entry => entry.direction === "request"
      && "command" in entry.payload && entry.payload.command === "apply_change");
    expect(requests).toHaveLength(1);
    const reply = service.transcript.filter(entry => entry.direction === "response"
      && entry.payload.id === requests[0].payload.id);
    expect(reply).toHaveLength(1);
    expect(reply[0].payload).toMatchObject({ ok: true });
    const nativeRequests = service.transcript.filter(entry => entry.direction === "request"
      && "command" in entry.payload && /native_job|render|production_execute/.test(entry.payload.command));
    expect(nativeRequests).toHaveLength(0);

    await page.reload();
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + final.revision);
    const order = await page.locator(".canvas-tree-row").allTextContents();
    expect(order.findIndex(label => label.includes(front[0].name)))
      .toBeLessThan(order.findIndex(label => label.includes(back[0].name)));
    await page.locator(".canvas-tree-row").filter({ hasText: front[0].name }).click();
    await expect(page.getByLabel("Canvas z order", { exact: true })).toHaveValue(String(promotedZ));
    expect(await service.project(source.id)).toEqual(final);
    await page.close();
    const handoff = await service.rawRequest<{read_only_handoff:boolean;journal_mode:string}>({
      command: "prepare_read_only_handoff", args: null,
    });
    expect(handoff).toMatchObject({
      ok: true, result: { read_only_handoff: true, journal_mode: "delete" },
    });
    await service.close();
    closed = true;
    expect(service.processExits).toHaveLength(1);
    expect(service.processExits[0]).toMatchObject({ code: 0, signal: null });
    expect(service.lifecycleTaints).toEqual([]);
    expect(createHash("sha256").update(await readFile(beforePath)).digest("hex")).toBe(expectedSha);
    completed = true;
  } finally {
    if (!page.isClosed()) await page.close();
    if (!closed) {
      try { await service.close(); closed = true; } catch { closed = false; }
    }
    // This is private evidence; raw original request IDs, grants and Project
    // bytes must never be uploaded by the Native artifact publisher.
    await writeFile(observationPath, JSON.stringify({
      schema: "motionwright.native-layer-order-studio/1",
      status: completed ? "ONE_SAVED_UI_LAYER_PROMOTION_AND_CLOSED_STORE" : "NOT_COMPLETED",
      source_sha: process.env.GITHUB_SHA, before_original_sha256: expectedSha,
      initial_project: source, project: final, history, ui_save_count: saveCount,
      closed_store_handoff: completed && closed,
      process_exits: service.processExits, lifecycle_taints: service.lifecycleTaints,
      original_transcript: service.transcript,
      automatic_retry: false, native_execution: false, installed_tauri: false, human_approval: false,
    }, null, 2) + "\n", { flag: "wx", mode: 0o600 });
  }
});
