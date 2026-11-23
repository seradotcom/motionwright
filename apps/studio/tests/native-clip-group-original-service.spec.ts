/** The single exact-source original Studio authoring before Native Clip Host.
 * This path is deliberately separate from the synthetic browser-only control.
 * No Native owner is created until this writer completes and closes.
 */
import { createHash } from "node:crypto";
import { lstat, readFile, writeFile } from "node:fs/promises";
import { isAbsolute } from "node:path";
import { expect, test } from "@playwright/test";
import type { Project, ProjectEvent } from "../src/types";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(process.env.MOTIONWRIGHT_NATIVE_CLIP_E2E !== "1"
  || !process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Requires one source-exact original SQLite and the real StudioService test binary");
test.describe.configure({ timeout: 120_000, retries: 0 });
const composition = process.env.MOTIONWRIGHT_NATIVE_CLIP_COMPOSITION ?? "static";
if (!["static", "pose"].includes(composition)) throw new Error("Unknown original Clip composition");
const poseSelected = composition === "pose";
const expectedSaves = poseSelected ? 5 : 4;

const requiredPath = (name: string) => {
  const path = process.env[name];
  if (!path || !isAbsolute(path)) throw new Error("Native Clip original path is unavailable: " + name);
  return path;
};

test("user saves a real Clip group and a partially clipped child, then Studio reopens its original Store",
  async ({ page }) => {
    const databasePath = requiredPath("MOTIONWRIGHT_NATIVE_CLIP_DATABASE");
    const beforePath = requiredPath("MOTIONWRIGHT_NATIVE_CLIP_BEFORE");
    const observationPath = requiredPath("MOTIONWRIGHT_NATIVE_CLIP_OBSERVATION");
    const expectedSha = process.env.MOTIONWRIGHT_NATIVE_CLIP_BEFORE_SHA256;
    expect(expectedSha).toMatch(/^[a-f0-9]{64}$/);
    expect(process.env.GITHUB_SHA).toMatch(/^[a-f0-9]{40}$/);
    for (const filename of [databasePath, beforePath]) {
      const stat = await lstat(filename);
      expect(stat.isFile() && !stat.isSymbolicLink()).toBe(true);
    }
    const raw = await readFile(beforePath);
    expect(raw.length).toBeGreaterThan(0);
    expect(raw.length).toBeLessThan(4 * 1024 * 1024);
    expect(createHash("sha256").update(raw).digest("hex")).toBe(expectedSha);
    const receipt = JSON.parse(raw.toString("utf8")) as {
      schema: string; phase: string; composition_case: string; project: Project; history: ProjectEvent[];
    };
    expect(receipt.schema).toBe("motionwright.native-clip-group-source/1");
    expect(receipt.phase).toBe("before_ui");
    expect(receipt.composition_case).toBe(composition);
    const original = receipt.project;
    expect(original.scenes).toHaveLength(1);
    expect(original.scenes[0].nodes.some(node => node.kind === "clip_group")).toBe(false);
    const scene = original.scenes[0];
    const service = await ScopedChangeService.create({
      databasePath, seedFixture: false, desktopInvokeFixture: true,
    });
    let saveCount = 0, closed = false, completed = false;
    let final: Project | null = null;
    let history: ProjectEvent[] | null = null;
    try {
      expect(await service.project(original.id)).toEqual(original);
      expect(await service.history(original.id, 0, 96)).toEqual(receipt.history);
      await service.attachPage(page, original.id);
      await page.goto("/");
      await page.getByRole("button", { name: "Canvas", exact: true }).click();
      const revision = page.locator(".command-bar .revision-chip");
      await expect(revision).toHaveText("r" + original.revision);

      await page.locator(".canvas-create-row")
        .getByRole("button", { name: "clip group", exact: true }).click();
      await expect(revision).toHaveText("r" + (original.revision + ++saveCount));
      const created = await service.project(original.id);
      const groups = created.scenes[0].nodes.filter(node => node.kind === "clip_group");
      expect(groups).toHaveLength(1);
      const group = groups[0];
      expect([group.name, group.x, group.y, group.width, group.height, group.parent_id])
        .toEqual(["Clip group 1", 640, 360, 640, 360, null]);
      await expect(page.locator(".canvas-tree-row").filter({ hasText: group.name }))
        .toHaveClass(/selected/);

      await page.locator(".canvas-create-row").getByRole("button", { name: "shape", exact: true }).click();
      await expect(revision).toHaveText("r" + (original.revision + ++saveCount));
      const withChild = await service.project(original.id);
      const child = withChild.scenes[0].nodes.find(node => node.parent_id === group.id);
      expect(child).toBeDefined();
      expect([child!.name, child!.kind, child!.x, child!.y, child!.width, child!.height])
        .toEqual(["Shape 1", "shape", 780, 430, 360, 220]);
      expect(child!.style.fill).toBe("#1D242C");

      await page.getByLabel("Canvas fill", { exact: true }).fill("#E0A940");
      await page.getByRole("button", { name: "Commit style", exact: true }).click();
      await expect(revision).toHaveText("r" + (original.revision + ++saveCount));
      await page.getByLabel("Canvas X", { exact: true }).fill("1100");
      await page.getByRole("button", { name: "Commit transform", exact: true }).click();
      await expect(revision).toHaveText("r" + (original.revision + ++saveCount));
      if (poseSelected) {
        const pose = page.getByRole("region", { name: "Native pose animation" });
        for (const [label, value] of [
          ["Pose start x", "1200"], ["Pose start y", "410"], ["Pose start width", "280"],
          ["Pose start height", "160"], ["Pose start rotation", "-18"], ["Pose end frame", "30"],
        ]) await pose.getByLabel(label, { exact: true }).fill(value);
        await pose.getByLabel("Pose geometry curve", { exact: true }).selectOption("ease_out_cubic");
        await pose.getByLabel("Pose opacity", { exact: true }).selectOption("out");
        await pose.getByLabel("Pose fade curve", { exact: true }).selectOption("linear");
        await pose.getByRole("button", { name: "Create native pose animation", exact: true }).click();
        await expect(revision).toHaveText("r" + (original.revision + ++saveCount));
      }
      final = await service.project(original.id);
      history = await service.history(original.id, 0, 96);
      expect(final.revision).toBe(original.revision + expectedSaves);
      expect(final.id).toBe(original.id);
      expect(final.generation).toBe(original.generation);
      expect(final.scenes[0].nodes.slice(0, original.scenes[0].nodes.length))
        .toEqual(original.scenes[0].nodes);
      const updated = final.scenes[0].nodes.find(node => node.id === child!.id);
      expect(updated).toMatchObject({
        kind: "shape", parent_id: group.id,
        x: 1100, y: 430, width: 360, height: 220,
        style: { fill: "#E0A940" },
      });
      expect(final.assets).toEqual(original.assets);
      expect(final.audio).toEqual(original.audio);
      expect(updated!.keyframes).toHaveLength(poseSelected ? 12 : 0);
      expect(history).toHaveLength(receipt.history.length + expectedSaves);
      expect(history.slice(0, receipt.history.length)).toEqual(receipt.history);
      expect(history.slice(-expectedSaves).map(item => item.change.type)).toEqual([
        "add_canvas_node", "add_canvas_node", "update_canvas_style", "transform_canvas_node",
        ...(poseSelected ? ["set_canvas_motion_keyframes"] : []),
      ]);
      const requests = service.transcript.filter(entry => entry.direction === "request"
        && "command" in entry.payload && entry.payload.command === "apply_change");
      expect(requests).toHaveLength(expectedSaves);
      if (poseSelected) {
        await expect(page.getByRole("button", { name: "Canvas object " + child!.name })).toHaveCSS("clip-path", /^polygon\(/);
        await page.locator(".motion-keyframe-jump").filter({ hasText: "1.000s" }).first().click();
        await expect(page.getByRole("button", { name: "Canvas object " + child!.name })).toHaveCSS("opacity", "0");
      }
      await expect(page.getByRole("button", { name: "Canvas object " + child!.name }))
        .toHaveCSS("clip-path", "inset(0% 50% 0% 0%)");
      await page.reload();
      await page.getByRole("button", { name: "Canvas", exact: true }).click();
      await expect(revision).toHaveText("r" + final.revision);
      if (poseSelected) {
        await page.locator(".canvas-tree-row").filter({ hasText: child!.name }).click();
        await page.locator(".motion-keyframe-jump").filter({ hasText: "1.000s" }).first().click();
      }
      await expect(page.getByRole("button", { name: "Canvas object " + child!.name }))
        .toHaveCSS("clip-path", "inset(0% 50% 0% 0%)");
      expect(await service.project(original.id)).toEqual(final);
      await page.close();
      const handoff = await service.rawRequest<{read_only_handoff:boolean;journal_mode:string}>({
        command: "prepare_read_only_handoff", args: null,
      });
      expect(handoff).toMatchObject({
        ok: true, result: { read_only_handoff: true, journal_mode: "delete" },
      });
      await service.close();
      closed = true;
      expect(service.lifecycleTaints).toEqual([]);
      expect(service.processExits).toHaveLength(1);
      expect(service.processExits[0]).toMatchObject({ code: 0, signal: null });
      expect(createHash("sha256").update(await readFile(beforePath)).digest("hex")).toBe(expectedSha);
      completed = true;
    } finally {
      if (!page.isClosed()) await page.close();
      if (!closed) {
        try { await service.close(); closed = true; } catch { closed = false; }
      }
      // A private original observation. Never add it to public CI artifacts:
      // request IDs, source Project bytes and grants are not public evidence.
      await writeFile(observationPath, JSON.stringify({
        schema: "motionwright.native-clip-group-studio/1",
        status: completed ? (poseSelected ? "FIVE" : "FOUR") + "_ORIGINAL_UI_SAVES_AND_CLOSED_STORE" : "NOT_COMPLETED",
        composition_case: composition,
        source_sha: process.env.GITHUB_SHA,
        before_original_sha256: expectedSha,
        initial_project: original, project: final, history, ui_save_count: saveCount,
        closed_store_handoff: completed && closed,
        process_exits: service.processExits,
        lifecycle_taints: service.lifecycleTaints,
        original_transcript: service.transcript,
        automatic_retry: false, native_execution: false, installed_tauri: false, human_approval: false,
      }, null, 2) + "\n", { flag: "wx", mode: 0o600 });
    }
  });
