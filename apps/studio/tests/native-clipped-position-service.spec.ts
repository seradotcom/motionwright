/** MW05-E01-02: source-bound masked position reveal with the pinned native
 * Settle primitive, a real static Clip parent and one atomic Studio UI Save.
 * The Service is real; no Native Host effect, installed Tauri or demo source.
 */
import { expect, test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A real StudioService test binary is required; there is no demo fallback");
test.describe.configure({ timeout: 90_000, retries: 0 });

test("Studio saves a frame-exact masked reveal under one real Clip and reopens the same Store",
  async ({ page }, info) => {
    const databasePath = info.outputPath("masked-reveal", "source.sqlite3");
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

      await page.locator(".canvas-create-row")
        .getByRole("button", { name: "clip group", exact: true }).click();
      await expect(revision).toHaveText("r" + (original.revision + 1));
      const withGroup = await service.project(original.id);
      const group = withGroup.scenes.find(item => item.id === scene!.id)!.nodes
        .find(item => item.kind === "clip_group");
      expect(group).toBeDefined();
      expect([group!.x, group!.y, group!.width, group!.height, group!.parent_id])
        .toEqual([640, 360, 640, 360, null]);

      await page.locator(".canvas-create-row")
        .getByRole("button", { name: "shape", exact: true }).click();
      await expect(revision).toHaveText("r" + (original.revision + 2));
      const afterChild = await service.project(original.id);
      const child = afterChild.scenes.find(item => item.id === scene!.id)!.nodes
        .find(item => item.parent_id === group!.id);
      expect(child).toBeDefined();
      expect([child!.x, child!.y, child!.width, child!.height])
        .toEqual([780, 430, 360, 220]);
      expect(child!.keyframes).toHaveLength(0);

      // One standard Canvas transform Save leaves half the source shape
      // outside its exact authored parent's x=1280 clipping boundary.
      const transform = page.locator(".canvas-draft-inspector").filter({
        has: page.locator("#canvas-draft-" + child!.id + "-x"),
      });
      await expect(transform).toHaveCount(1);
      await transform.getByLabel("Canvas X", { exact: true }).fill("1100");
      await transform.getByRole("button", { name: "Commit transform", exact: true }).click();
      await expect(revision).toHaveText("r" + (original.revision + 3));
      const positioned = await service.project(original.id);
      expect(positioned.scenes.find(item => item.id === scene!.id)?.nodes
        .find(item => item.id === child!.id)?.x).toBe(1100);
      await expect(page.getByRole("button", { name: "Canvas object " + child!.name }))
        .toHaveCSS("clip-path", "inset(0% 50% 0% 0%)");

      // The original source cannot be replaced by four unsaved in-memory keys.
      // One existing normal Studio command must save all four at once.
      await page.getByLabel("Native move start X", { exact: true }).fill("1400");
      await page.getByLabel("Native move start Y", { exact: true }).fill("430");
      await page.getByLabel("Native move end frame", { exact: true }).fill("30");
      await page.getByLabel("Native move easing", { exact: true }).selectOption("ease_out_cubic");
      const author = page.getByRole("button", {
        name: "Create native ease-out cubic move", exact: true,
      });
      await expect(author).toBeEnabled();
      await author.click();
      await expect(revision).toHaveText("r" + (original.revision + 4));

      const final = await service.project(original.id);
      const savedScene = final.scenes.find(item => item.id === scene!.id)!;
      const saved = savedScene.nodes.find(item => item.id === child!.id)!;
      expect(savedScene.nodes.find(item => item.id === group!.id)).toEqual(group);
      expect([saved.parent_id, saved.x, saved.y, saved.width, saved.height])
        .toEqual([group!.id, 1100, 430, 360, 220]);
      expect(saved.keyframes).toHaveLength(4);
      expect(saved.keyframes.map(key => [key.property, key.value, key.interpolation]))
        .toEqual([
          ["x", 1400, "linear"], ["y", 430, "linear"],
          ["x", 1100, "ease_out_cubic"], ["y", 430, "ease_out_cubic"],
        ]);
      expect(saved.keyframes[2].at).toEqual({ num: "1", den: "1" });
      expect(saved.keyframes[3].at).toEqual({ num: "1", den: "1" });
      expect(final.scenes.filter(item => item.id !== scene!.id))
        .toEqual(original.scenes.filter(item => item.id !== scene!.id));
      expect(final.assets).toEqual(original.assets);
      expect(final.audio).toEqual(original.audio);
      const changes = (await service.history(original.id, positioned.revision, 8))
        .map(event => event.change);
      expect(changes).toEqual([{
        type: "set_canvas_out_cubic_position_motion",
        scene_id: scene!.id, node_id: child!.id,
        deliverable_id: final.deliverables[0].id,
        start_x: 1400, start_y: 430, end_frame: 30,
      }]);

      // At t=0 the original child is entirely beyond the clipping plane.
      // At frame30 it re-enters, but still has half its width clipped.
      const sourceElement = page.getByRole("button", { name: "Canvas object " + child!.name });
      await expect(sourceElement).toHaveCSS("clip-path", "inset(0% 100% 0% 0%)");
      await page.locator(".motion-keyframe-jump").filter({ hasText: "1.000s" }).first().click();
      await expect(sourceElement).toHaveCSS("clip-path", "inset(0% 50% 0% 0%)");

      await page.reload();
      await page.getByRole("button", { name: "Canvas", exact: true }).click();
      await page.locator(".tree-row").filter({ hasText: scene!.name }).click();
      await page.locator(".canvas-tree-row").filter({ hasText: child!.name }).click();
      await expect(revision).toHaveText("r" + final.revision);
      await page.locator(".motion-keyframe-jump").filter({ hasText: "1.000s" }).first().click();
      await expect(page.getByRole("button", { name: "Canvas object " + child!.name }))
        .toHaveCSS("clip-path", "inset(0% 50% 0% 0%)");
      expect(await service.project(original.id)).toEqual(final);

      await page.close();
      const handoff = await service.rawRequest<{ read_only_handoff: boolean; journal_mode: string }>({
        command: "prepare_read_only_handoff", args: null,
      });
      expect(handoff).toMatchObject({ ok: true,
        result: { read_only_handoff: true, journal_mode: "delete" } });
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
        expect((await reader.history(original.id, positioned.revision, 8))
          .map(event => event.change)).toEqual(changes);
      } finally {
        await reader.close();
      }
    } finally {
      if (!page.isClosed()) await page.close();
      if (!closed) await service.close();
    }
  });
