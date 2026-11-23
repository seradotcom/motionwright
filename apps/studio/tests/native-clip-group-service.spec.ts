import { expect, test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Actual StudioService test binary is required; the browser-demo shell is not a Save.");
test.describe.configure({ timeout: 90_000, retries: 0 });

test("Studio creates an editable rectangular Clip group, clips its child and reopens the same Store", async ({ page }, info) => {
  const databasePath = info.outputPath("clip-group", "project.sqlite3");
  const service = await ScopedChangeService.create({ databasePath, desktopInvokeFixture: true });
  let closed = false;
  try {
    const original = (await service.fixtureBootstrap()).project;
    const scene = original.scenes.find(scene => scene.name === "Independent synthetic scene");
    expect(scene).toBeDefined();
    await service.attachFixturePage(page, original.id);
    await page.goto("/");
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await page.locator(".tree-row").filter({ hasText: scene!.name }).click();

    const revision = page.locator(".command-bar .revision-chip");
    await expect(revision).toHaveText("r" + original.revision);
    await page.locator(".canvas-create-row").getByRole("button", { name: "clip group", exact: true }).click();
    await expect(revision).toHaveText("r" + (original.revision + 1));
    const created = await service.project(original.id);
    const clips = created.scenes.find(item => item.id === scene!.id)!.nodes.filter(node => node.kind === "clip_group");
    expect(clips).toHaveLength(1);
    const group = clips[0];
    expect([group.x, group.y, group.width, group.height, group.parent_id])
      .toEqual([640, 360, 640, 360, null]);
    expect(group.keyframes).toHaveLength(0);
    await expect(page.locator(".canvas-tree-row").filter({ hasText: group.name })).toHaveClass(/selected/);

    // A new shape is authored INSIDE the selected saved clip group.
    await page.locator(".canvas-create-row").getByRole("button", { name: "shape", exact: true }).click();
    await expect(revision).toHaveText("r" + (original.revision + 2));
    const withChild = await service.project(original.id);
    const sceneAfter = withChild.scenes.find(item => item.id === scene!.id)!;
    const newChild = sceneAfter.nodes.find(node => node.parent_id === group.id);
    expect(newChild).toBeDefined();
    expect([newChild!.x, newChild!.y, newChild!.width, newChild!.height])
      .toEqual([780, 430, 360, 220]);
    expect(newChild!.kind).toBe("shape");
    expect(sceneAfter.nodes.find(node => node.id === group.id)).toEqual(group);

    // Explicit Save pushes the child halfway outside the saved parent clip.
    const transform = page.locator(".canvas-draft-inspector").filter({
        has: page.locator("#canvas-draft-" + newChild!.id + "-x"),
      });
      await expect(transform).toHaveCount(1);
      await transform.getByLabel("Canvas X", { exact: true }).fill("1100");
    await transform.getByRole("button", { name: "Commit transform", exact: true }).click();
    await expect(revision).toHaveText("r" + (original.revision + 3));
    const final = await service.project(original.id);
    const saved = final.scenes.find(item => item.id === scene!.id)!.nodes.find(node => node.id === newChild!.id)!;
    expect(saved.parent_id).toBe(group.id);
    expect(saved.x).toBe(1100);
    expect(saved.style).toEqual(newChild!.style);
    expect(final.scenes.filter(item => item.id !== scene!.id))
      .toEqual(original.scenes.filter(item => item.id !== scene!.id));
    expect(final.assets).toEqual(original.assets);
    await expect(page.getByRole("button", { name: "Canvas object " + saved.name }))
      .toHaveCSS("clip-path", "inset(0% 50% 0% 0%)");

    const changes = (await service.history(original.id, original.revision, 8)).map(item => item.change);
    expect(changes.map(item => item.type))
      .toEqual(["add_canvas_node", "add_canvas_node", "transform_canvas_node"]);
    expect(changes[1]).toMatchObject({ type: "add_canvas_node", scene_id: scene!.id,
      node: { id: saved.id, parent_id: group.id, kind: "shape" } });
    const requests = service.transcript.filter(entry => entry.direction === "request"
      && "command" in entry.payload && entry.payload.command === "apply_change");
    expect(requests).toHaveLength(3);

    await page.reload();
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await page.locator(".tree-row").filter({ hasText: scene!.name }).click();
    await expect(revision).toHaveText("r" + final.revision);
    await expect(page.getByRole("button", { name: "Canvas object " + saved.name }))
      .toHaveCSS("clip-path", "inset(0% 50% 0% 0%)");
    expect(await service.project(original.id)).toEqual(final);

    await page.close();
    const handoff = await service.rawRequest<{ read_only_handoff: boolean; journal_mode: string }>({
      command: "prepare_read_only_handoff", args: null,
    });
    expect(handoff).toMatchObject({ ok: true, result: { read_only_handoff: true, journal_mode: "delete" } });
    await service.close();
    closed = true;
    const reader = await ScopedChangeService.createReadOnly({
      binaryPath: service.binaryPath, databasePath, seedFixture: false, desktopInvokeFixture: false,
    });
    try {
      expect(await reader.project(original.id)).toEqual(final);
      expect((await reader.history(original.id, original.revision, 8)).map(item => item.change)).toEqual(changes);
    } finally {
      await reader.close();
    }
  } finally {
    if (!page.isClosed()) await page.close();
    if (!closed) await service.close();
  }
});
