/** Original Studio edits in the existing Native geometry Store, before Host start.
 * Every Save crosses the real Service. This case cannot render or grant Native effects.
 * The original Native runner later captures the closed Store, renders once and reads it again.
 */
import { createHash } from "node:crypto";
import { lstat, readFile, writeFile } from "node:fs/promises";
import { isAbsolute } from "node:path";
import { expect, test } from "@playwright/test";
import type { Project, ProjectEvent } from "../src/types";
import { ScopedChangeService } from "./support/scopedChangeService";

test.describe.configure({ timeout: 120_000, retries: 0 });
test.skip(process.env.MOTIONWRIGHT_NATIVE_GEOMETRY_PROFILE !== "hold",
  "Selected only by the existing exact-source, one-effect Native geometry runner.");

function requiredPath(name: string): string {
  const value = process.env[name];
  if (!value || !isAbsolute(value)) throw new Error("The original geometry path is missing: " + name);
  return value;
}

test("Studio saves five original held geometry destinations and closes their original Store", async ({ page }) => {
  // Fixed phase names are the only UI diagnostics the parent may print publicly.
  // Full responses and browser diagnostics stay in the private original.
  let lastPhase = "validate_source", uiSaveCount = 0;
  function phase(name: string) {
    lastPhase = name;
    console.log("MOTIONWRIGHT_HOLD_STUDIO_PHASE=" + name);
  }
  phase("validate_source");
  const databasePath = requiredPath("MOTIONWRIGHT_GEOMETRY_UI_DATABASE");
  const sourcePath = requiredPath("MOTIONWRIGHT_GEOMETRY_UI_SOURCE");
  const observationPath = requiredPath("MOTIONWRIGHT_GEOMETRY_UI_OBSERVATION");
  const sourceSha = process.env.MOTIONWRIGHT_GEOMETRY_UI_SOURCE_SHA256;
  expect(sourceSha).toMatch(/^[0-9a-f]{64}$/);
  expect(process.env.GITHUB_SHA).toMatch(/^[0-9a-f]{40}$/);
  for (const path of [sourcePath, databasePath]) {
    const entry = await lstat(path);
    expect(entry.isFile() && !entry.isSymbolicLink()).toBe(true);
  }
  expect((await lstat(sourcePath)).size).toBeLessThanOrEqual(4 * 1024 * 1024);
  const bytes = await readFile(sourcePath);
  expect(createHash("sha256").update(bytes).digest("hex")).toBe(sourceSha);
  const original = JSON.parse(bytes.toString("utf8")) as {
    schema: string; identity: { easing: string; project_id: string; generation: string; revision: number };
    project: Project; history: ProjectEvent[];
  };
  expect(original.schema).toBe("motionwright.native-geometry-source/1");
  expect(original.identity.easing).toBe("ease_out_cubic");
  const initial = original.project, scene = initial.scenes[0], node = scene.nodes[0];
  expect(original.identity).toMatchObject({ project_id: initial.id, generation: initial.generation, revision: initial.revision });
  expect(initial.scenes).toHaveLength(1);
  expect(scene.nodes).toHaveLength(1);
  expect(node.name).toBe("Native geometry source-bound rectangle");
  expect(node.keyframes).toHaveLength(10);
  const destinations = node.keyframes.filter(key => key.at.num === "1" && key.at.den === "1");
  expect(destinations).toHaveLength(5);
  expect(destinations.every(key => key.interpolation === "ease_out_cubic")).toBe(true);

  phase("start_service");
  const service = await ScopedChangeService.create({ databasePath, seedFixture: false });
  let final: Project | null = null, history: ProjectEvent[] | null = null;
  let completed = false, closed = false, handoff: unknown = null;
  try {
    phase("verify_initial_store");
    expect(await service.project(initial.id)).toEqual(initial);
    expect(await service.history(initial.id, 0, 16)).toEqual(original.history);
    await service.attachPage(page, initial.id);
    phase("open_studio");
    await page.goto("/");
    phase("open_canvas");
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    phase("select_node");
    await page.getByRole("complementary", { name: "Canvas object tree" })
      .getByRole("button", { name: /Native geometry source-bound rectangle/ }).click();
    phase("inspect_keys");
    const motion = page.getByRole("region", { name: "Typed motion keyframes" });
    const revision = page.locator(".command-bar .revision-chip");
    await expect(revision).toHaveText("r" + initial.revision);
    await expect(motion.locator(".motion-keyframe-row")).toHaveCount(10);
    for (const key of destinations) {
      // The existing jump button seeks the exact authored one-second/frame-30 clock.
      phase("seek_destination");
      await motion.locator(".motion-keyframe-jump").filter({ hasText: "1.000s" }).first().click();
      await expect(motion.locator(".motion-editor-heading")).toContainText("1.000s");
      phase("select_property");
      await page.getByLabel("Motion property", { exact: true }).selectOption(key.property);
      phase("enter_value");
      await page.getByLabel("Motion keyframe value", { exact: true }).fill(String(key.value));
      phase("select_hold");
      await page.getByLabel("Motion interpolation", { exact: true }).selectOption("hold");
      phase("save_key");
      await motion.getByRole("button", { name: "Add or replace keyframe at playhead", exact: true }).click();
      uiSaveCount++;
      phase("observe_save");
      await expect(revision).toHaveText("r" + (initial.revision + uiSaveCount));
      await expect(motion.locator(".motion-keyframe-row").filter({ hasText: "hold" })).toHaveCount(uiSaveCount);
    }
    phase("verify_final_store");
    final = await service.project(initial.id);
    history = await service.history(initial.id, 0, 16);
    expect(final.revision).toBe(initial.revision + 5);
    expect(final.id).toBe(initial.id);
    expect(final.generation).toBe(initial.generation);
    expect(history).toHaveLength(original.history.length + 5);
    expect(history.slice(0, original.history.length)).toEqual(original.history);
    expect(history.at(-1)?.revision).toBe(final.revision);
    const expectedKeys = node.keyframes.map(key => ({
      ...key, interpolation: key.at.num === "1" ? "hold" : key.interpolation,
    }));
    const keys = final.scenes[0].nodes[0].keyframes;
    expect(keys).toHaveLength(10);
    expect(keys).toEqual(expect.arrayContaining(expectedKeys));
    expect({ ...final.scenes[0].nodes[0], keyframes: [] }).toEqual({ ...node, keyframes: [] });
    expect(final.scenes.map(value => ({ ...value, nodes: [] })))
      .toEqual(initial.scenes.map(value => ({ ...value, nodes: [] })));
    expect(final.assets).toEqual(initial.assets);
    expect(final.audio).toEqual(initial.audio);
    expect(final.production_design).toEqual(initial.production_design);
    expect(final.deliverables).toEqual(initial.deliverables);
    expect(final.visual_language).toEqual(initial.visual_language);

    const requests = service.transcript.filter(entry => entry.direction === "request"
      && "command" in entry.payload && entry.payload.command === "apply_change").map(entry =>
      entry.payload as { id: string; args: { request: {
        project_id: string; generation: string; revision: number;
        change: { type: string; scene_id: string; node_id: string; keyframe: unknown };
      } } });
    expect(requests).toHaveLength(5);
    expect(new Set(requests.map(request => request.id)).size).toBe(5);
    requests.forEach((request, index) => {
      expect(request.args.request).toMatchObject({
        project_id: initial.id, generation: initial.generation, revision: initial.revision + index,
        change: { type: "set_canvas_keyframe", scene_id: scene.id, node_id: node.id,
          keyframe: { ...destinations[index], interpolation: "hold" } },
      });
      const replies = service.transcript.filter(entry => entry.direction === "response" && entry.payload.id === request.id);
      expect(replies).toHaveLength(1);
      expect(replies[0].payload).toMatchObject({ ok: true });
    });
    const nativeCommands = service.transcript.filter(entry => entry.direction === "request"
      && "command" in entry.payload && /render|native_job|production_execute/.test(entry.payload.command));
    expect(nativeCommands).toHaveLength(0);
    // A fresh UI load must show the saved held keys before the writer closes.
    phase("reload_studio");
    await page.reload();
    phase("reopen_canvas");
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + final.revision);
    await expect(page.locator(".motion-keyframe-row").filter({ hasText: "hold" })).toHaveCount(5);
    expect(await service.project(initial.id)).toEqual(final);
    phase("close_page");
    await page.close();
    phase("prepare_handoff");
    const response = await service.rawRequest<{ read_only_handoff: boolean; journal_mode: string }>({
      command: "prepare_read_only_handoff", args: null,
    });
    handoff = response;
    expect(response.ok).toBe(true);
    if (!response.ok) throw new Error("The original geometry writer refused its handoff");
    expect(response.result).toEqual({ read_only_handoff: true, journal_mode: "delete" });
    phase("close_writer");
    await service.close();
    closed = true;
    phase("verify_closed_store");
    expect(service.processExits).toHaveLength(1);
    expect(service.processExits[0]).toMatchObject({ code: 0, signal: null });
    expect(service.lifecycleTaints).toEqual([]);
    expect(createHash("sha256").update(await readFile(sourcePath)).digest("hex")).toBe(sourceSha);
    completed = true;
    phase("completed");
  } finally {
    if (!page.isClosed()) await page.close();
    if (!closed) {
      try { await service.close(); closed = true; }
      catch { closed = false; }
    }
    // Private original only: no grant tokens or raw service replies enter the
    // public Native artifact. Rust and the parent join this exact receipt hash.
    await writeFile(observationPath, JSON.stringify({
      schema: "motionwright.native-geometry-studio-hold/1",
      status: completed ? "FIVE_ORIGINAL_UI_SAVES_AND_CLOSED_STORE" : "NOT_COMPLETED",
      source_sha: process.env.GITHUB_SHA, seed_source_sha256: sourceSha, last_phase: lastPhase,
      initial_project: initial, project: final, history, ui_save_count: uiSaveCount,
      closed_store_handoff: completed && closed, original_handoff: handoff,
      process_exits: service.processExits, lifecycle_taints: service.lifecycleTaints,
      original_transcript: service.transcript,
      automatic_retry: false, native_execution: false, installed_tauri: false, human_approval: false,
    }, null, 2) + "\n", { flag: "wx", mode: 0o600 });
  }
});