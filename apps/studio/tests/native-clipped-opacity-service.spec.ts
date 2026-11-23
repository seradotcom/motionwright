/** MW05-E01-01 / E01-02: the real Studio Service persists a clipped FadeOut.
 * Group and child are created by ordinary Canvas Save. Source FadeIn keys are
 * staged through the same SQLite Service; TWO subsequent key edits must be UI
 * Save operations. This test has no Native effect and no demo fallback.
 */
import { expect, test } from "@playwright/test";
import type { CanvasKeyframe } from "../src/types";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Actual StudioService binary is required; no demo or synthetic Native render");
test.describe.configure({ timeout: 90_000, retries: 0 });

test("Studio saves an editable FadeOut inside an actual Clip group and independently reopens its Store",
  async ({ page }, info) => {
    const databasePath = info.outputPath("clip-opacity", "studio.sqlite3");
    const service = await ScopedChangeService.create({ databasePath, desktopInvokeFixture: true });
    let closed = false;
    try {
      const original = (await service.fixtureBootstrap()).project;
      const scene = original.scenes.find(item => item.name === "Independent synthetic scene");
      expect(scene).toBeDefined();
      await service.attachFixturePage(page, original.id);
      await page.goto("/");
      await page.getByRole("button", { name: "Canvas", exact: true }).click();
      await page.locator(".tree-row").filter({ hasText: scene!.name }).click();
      const revision = page.locator(".command-bar .revision-chip");
      await expect(revision).toHaveText("r" + original.revision);

      // Two genuine Studio UI additions establish the saved parent-child
      // hierarchy before any opacity authoring takes place.
      await page.locator(".canvas-create-row")
        .getByRole("button", { name: "clip group", exact: true }).click();
      await expect(revision).toHaveText("r" + (original.revision + 1));
      const groupProject = await service.project(original.id);
      const group = groupProject.scenes.find(item => item.id === scene!.id)!.nodes
        .find(item => item.kind === "clip_group");
      expect(group).toBeDefined();
      await page.locator(".canvas-create-row").getByRole("button", { name: "shape", exact: true }).click();
      await expect(revision).toHaveText("r" + (original.revision + 2));
      const childProject = await service.project(original.id);
      const child = childProject.scenes.find(item => item.id === scene!.id)!.nodes
        .find(item => item.parent_id === group!.id);
      expect(child).toBeDefined();
      expect(child!.keyframes).toHaveLength(0);
      expect(child!.opacity).toBe(1);

      // An ordinary Studio transform puts half of the actual saved child
      // outside the clipping parent, so opacity and crop must coexist.
      const transform = page.locator(".canvas-draft-inspector").filter({
        has: page.locator("#canvas-draft-" + child!.id + "-x"),
      });
      await expect(transform).toHaveCount(1);
      await transform.getByLabel("Canvas X", { exact: true }).fill("1100");
      await transform.getByRole("button", { name: "Commit transform", exact: true }).click();
      await expect(revision).toHaveText("r" + (original.revision + 3));
      const positioned = await service.project(original.id);
      const initial: CanvasKeyframe = {
        at: { num: "0", den: "1" }, property: "opacity", value: 0,
        interpolation: "linear",
      };
      const end: CanvasKeyframe = {
        at: { num: "1", den: "1" }, property: "opacity", value: 1,
        interpolation: "linear",
      };
      const staged0 = await service.apply(positioned, {
        type: "set_canvas_keyframe", scene_id: scene!.id, node_id: child!.id, keyframe: initial,
      });
      const staged = await service.apply(staged0, {
        type: "set_canvas_keyframe", scene_id: scene!.id, node_id: child!.id, keyframe: end,
      });

      // The editor reloads the exact original source. Only the TWO later
      // value changes are UI-authored, revisioned and independently journaled.
      await page.reload();
      await page.getByRole("button", { name: "Canvas", exact: true }).click();
      await page.locator(".tree-row").filter({ hasText: scene!.name }).click();
      await page.locator(".canvas-tree-row").filter({ hasText: child!.name }).click();
      const editor = page.getByRole("region", { name: "Typed motion keyframes" });
      await expect(revision).toHaveText("r" + staged.revision);
      await expect(editor.locator(".motion-keyframe-row")).toHaveCount(2);

      const edit = async (at: string, value: string, expectedRevision: number) => {
        await editor.locator(".motion-keyframe-jump").filter({ hasText: at }).click();
        await page.getByLabel("Motion property", { exact: true }).selectOption("opacity");
        await page.getByLabel("Motion keyframe value", { exact: true }).fill(value);
        await page.getByLabel("Motion interpolation", { exact: true })
          .selectOption("linear");
        await editor.getByRole("button", {
          name: "Add or replace keyframe at playhead", exact: true,
        }).click();
        await expect(revision).toHaveText("r" + expectedRevision);
      };
      await edit("0.000s", "1", staged.revision + 1);
      await edit("1.000s", "0", staged.revision + 2);
      const final = await service.project(original.id);
      const saved = final.scenes.find(item => item.id === scene!.id)!.nodes
        .find(item => item.id === child!.id)!;
      expect(final.revision).toBe(staged.revision + 2);
      expect(saved.parent_id).toBe(group!.id);
      expect(saved.x).toBe(1100);
      expect(saved.opacity).toBe(1);
      expect(saved.keyframes).toEqual(expect.arrayContaining([
        { ...initial, value: 1 },
        { ...end, value: 0 },
      ]));
      expect(final.scenes.filter(item => item.id !== scene!.id))
        .toEqual(original.scenes.filter(item => item.id !== scene!.id));
      expect(final.assets).toEqual(original.assets);

      const changes = (await service.history(original.id, staged.revision, 6))
        .map(event => event.change);
      expect(changes).toEqual([
        { type: "set_canvas_keyframe", scene_id: scene!.id,
          node_id: child!.id, keyframe: { ...initial, value: 1 } },
        { type: "set_canvas_keyframe", scene_id: scene!.id,
          node_id: child!.id, keyframe: { ...end, value: 0 } },
      ]);
      await expect(page.getByRole("button", { name: "Canvas object " + saved.name }))
        .toHaveCSS("clip-path", "inset(0% 50% 0% 0%)");
      await expect(page.getByRole("button", { name: "Canvas object " + saved.name }))
        .toHaveCSS("opacity", "0");

      await page.reload();
      await page.getByRole("button", { name: "Canvas", exact: true }).click();
      await page.locator(".tree-row").filter({ hasText: scene!.name }).click();
      await page.locator(".canvas-tree-row").filter({ hasText: child!.name }).click();
      await editor.locator(".motion-keyframe-jump").filter({ hasText: "1.000s" }).click();
      await expect(revision).toHaveText("r" + final.revision);
      await expect(page.getByRole("button", { name: "Canvas object " + saved.name }))
        .toHaveCSS("opacity", "0");
      await expect(page.getByRole("button", { name: "Canvas object " + saved.name }))
        .toHaveCSS("clip-path", "inset(0% 50% 0% 0%)");
      expect(await service.project(original.id)).toEqual(final);

      await page.close();
      const handoff = await service.rawRequest<{ read_only_handoff: boolean; journal_mode: string }>({
        command: "prepare_read_only_handoff", args: null,
      });
      expect(handoff).toMatchObject({ ok: true, result: {
        read_only_handoff: true, journal_mode: "delete",
      } });
      await service.close();
      closed = true;
      expect(service.lifecycleTaints).toEqual([]);
      expect(service.processExits).toHaveLength(1);
      expect(service.processExits[0]).toMatchObject({ code: 0, signal: null });
      const reader = await ScopedChangeService.createReadOnly({
        binaryPath: service.binaryPath, databasePath,
        seedFixture: false, desktopInvokeFixture: false,
      });
      try {
        expect(await reader.project(original.id)).toEqual(final);
        expect((await reader.history(original.id, staged.revision, 6))
          .map(event => event.change)).toEqual(changes);
      } finally {
        await reader.close();
      }
    } finally {
      if (!page.isClosed()) await page.close();
      if (!closed) await service.close();
    }
  });
