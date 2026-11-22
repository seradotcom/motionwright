/** Real Studio authoring of exact FadeOut on an existing saved Canvas node.
 * The only UI-authorized effects here are two normal source changes; this
 * transport excludes installed Tauri and does not invoke the Native Host.
 */
import { expect, test } from "@playwright/test";
import type { CanvasKeyframe } from "../src/types";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "The original StudioService test executable is required; no demo fallback.");
test.describe.configure({ timeout: 90_000, retries: 0 });

test("Studio converts an existing opacity entrance to a saved native-compatible exit and reopens SQLite", async ({ page }, info) => {
  const databasePath = info.outputPath("fade-out", "studio.sqlite3");
  const service = await ScopedChangeService.create({ databasePath, desktopInvokeFixture: true });
  let closed = false;
  try {
    const original = (await service.fixtureBootstrap()).project;
    const scene = original.scenes.find(scene => scene.name === "Independent synthetic scene");
    expect(scene).toBeDefined();
    const node = scene!.nodes[0];
    expect(node.keyframes).toHaveLength(0);
    expect(node.opacity).toBe(1);
    expect(node.property_locks).toEqual([]);

    // Stage real, saved, editable opacity keys as the user's existing source.
    // The following two changes are made exclusively through the Studio UI.
    const first: CanvasKeyframe = { at: { num: "0", den: "1" },
      property: "opacity", value: 0, interpolation: "linear" };
    const second: CanvasKeyframe = { at: { num: "1", den: "1" },
      property: "opacity", value: 1, interpolation: "linear" };
    const source0 = await service.apply(original, {
      type: "set_canvas_keyframe", scene_id: scene!.id, node_id: node.id, keyframe: first,
    });
    const source = await service.apply(source0, {
      type: "set_canvas_keyframe", scene_id: scene!.id, node_id: node.id, keyframe: second,
    });
    await service.attachFixturePage(page, source.id);
    await page.goto("/");
    await page.locator(".tree-row").filter({ hasText: scene!.name }).click();
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await page.locator(".canvas-tree-row").filter({ hasText: node.name }).click();
    const editor = page.getByRole("region", { name: "Typed motion keyframes" });
    const revision = page.locator(".command-bar .revision-chip");
    await expect(revision).toHaveText("r" + source.revision);
    await expect(editor.locator(".motion-keyframe-row")).toHaveCount(2);

    const edit = async (at: string, value: string, expectedRevision: number) => {
      await editor.locator(".motion-keyframe-jump").filter({ hasText: at }).click();
      await expect(editor.locator(".motion-editor-heading")).toContainText(at);
      await page.getByLabel("Motion property", { exact: true }).selectOption("opacity");
      await page.getByLabel("Motion keyframe value", { exact: true }).fill(value);
      await page.getByLabel("Motion interpolation", { exact: true }).selectOption("linear");
      await editor.getByRole("button", {
        name: "Add or replace keyframe at playhead", exact: true,
      }).click();
      await expect(revision).toHaveText("r" + expectedRevision);
    };
    await edit("0.000s", "1", source.revision + 1);
    await edit("1.000s", "0", source.revision + 2);

    const final = await service.project(source.id);
    expect(final.revision).toBe(source.revision + 2);
    expect(final.scenes.find(scene => scene.id === scene!.id)?.nodes.find(candidate => candidate.id === node.id)?.keyframes)
      .toEqual(expect.arrayContaining([{ ...first, value: 1 }, { ...second, value: 0 }]));
    expect(final.scenes.filter(item => item.id !== scene!.id))
      .toEqual(source.scenes.filter(item => item.id !== scene!.id));
    expect(final.assets).toEqual(source.assets);
    const changed = (await service.history(source.id, source.revision, 8)).map(event => event.change);
    expect(changed).toEqual([
      { type: "set_canvas_keyframe", scene_id: scene!.id, node_id: node.id, keyframe: { ...first, value: 1 } },
      { type: "set_canvas_keyframe", scene_id: scene!.id, node_id: node.id, keyframe: { ...second, value: 0 } },
    ]);
    const requests = service.transcript.filter(entry => entry.direction === "request"
      && "command" in entry.payload && entry.payload.command === "apply_change");
    expect(requests).toHaveLength(4);
    const replies = service.transcript.filter(entry => entry.direction === "response");
    for (const request of requests.slice(-2)) {
      const match = replies.filter(reply => reply.payload.id === request.payload.id);
      expect(match).toHaveLength(1);
      expect(match[0].payload).toMatchObject({ ok: true });
    }

    await page.reload();
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + final.revision);
    await page.locator(".canvas-tree-row").filter({ hasText: node.name }).click();
    const reopenedEditor = page.getByRole("region", { name: "Typed motion keyframes" });
    await reopenedEditor.locator(".motion-keyframe-jump").filter({ hasText: "1.000s" }).click();
    await expect(page.getByRole("button", { name: "Canvas object " + node.name }))
      .toHaveCSS("opacity", "0");

    await page.close();
    const handoff = await service.rawRequest<{ read_only_handoff: boolean; journal_mode: string }>({
      command: "prepare_read_only_handoff", args: null,
    });
    expect(handoff).toMatchObject({ ok: true, result: { read_only_handoff: true, journal_mode: "delete" } });
    await service.close();
    closed = true;
    expect(service.lifecycleTaints).toEqual([]);
    expect(service.processExits).toHaveLength(1);
    expect(service.processExits[0]).toMatchObject({ code: 0, signal: null });
    const reader = await ScopedChangeService.createReadOnly({
      binaryPath: service.binaryPath, databasePath, seedFixture: false, desktopInvokeFixture: false,
    });
    try {
      expect(await reader.project(final.id)).toEqual(final);
      expect((await reader.history(final.id, source.revision, 8)).map(event => event.change)).toEqual(changed);
    } finally {
      await reader.close();
    }
  } finally {
    if (!page.isClosed()) await page.close();
    if (!closed) await service.close();
  }
});
