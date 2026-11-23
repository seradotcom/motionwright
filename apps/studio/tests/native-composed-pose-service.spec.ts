/** One actual StudioService transaction carries every authored pose channel.
 * The browser edits and reopens its original SQLite/CAS. Native pixels are
 * covered separately by the selected original Host run, never by this test. */
import { expect, test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "An exact-source StudioService binary is required");
test.describe.configure({ timeout: 90_000, retries: 0 });

test("Studio saves and re-edits a complete clipped pose as one original Service change", async ({ page }, info) => {
  const databasePath = info.outputPath("composed-pose", "source.sqlite3");
  const service = await ScopedChangeService.create({ databasePath, desktopInvokeFixture: true });
  let closed = false;
  try {
    const original = (await service.fixtureBootstrap()).project;
    const scene = original.scenes.find(item => item.name === "Independent synthetic scene")!;
    expect(scene).toBeDefined();
    await service.attachFixturePage(page, original.id);
    await page.goto("/");
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await page.locator(".tree-row").filter({ hasText: scene.name }).click();
    const revision = page.locator(".command-bar .revision-chip");
    await page.locator(".canvas-create-row").getByRole("button", { name: "clip group", exact: true }).click();
    await expect(revision).toHaveText("r" + (original.revision + 1));
    const grouped = await service.project(original.id);
    const group = grouped.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.kind === "clip_group")!;
    await page.locator(".canvas-create-row").getByRole("button", { name: "shape", exact: true }).click();
    await expect(revision).toHaveText("r" + (original.revision + 2));
    const created = await service.project(original.id);
    const child = created.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.parent_id === group.id)!;
    await page.getByLabel("Canvas X", { exact: true }).fill("1100");
    await page.getByRole("button", { name: "Commit transform", exact: true }).click();
    await expect(revision).toHaveText("r" + (original.revision + 3));
    const positioned = await service.project(original.id);

    const pose = page.getByRole("region", { name: "Native pose animation" });
    for (const [label, value] of [
      ["Pose start x", "1200"], ["Pose start y", "410"], ["Pose start width", "280"],
      ["Pose start height", "160"], ["Pose start rotation", "-18"], ["Pose end frame", "30"],
    ]) await pose.getByLabel(label, { exact: true }).fill(value);
    await pose.getByLabel("Pose geometry curve", { exact: true }).selectOption("ease_out_cubic");
    await pose.getByLabel("Pose opacity", { exact: true }).selectOption("out");
    await pose.getByLabel("Pose fade curve", { exact: true }).selectOption("linear");
    await pose.getByRole("button", { name: "Create native pose animation", exact: true }).click();
    await expect(revision).toHaveText("r" + (positioned.revision + 1));
    const saved = await service.project(original.id);
    const savedNode = saved.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === child.id)!;
    expect(savedNode.keyframes).toHaveLength(12);
    expect(savedNode.parent_id).toBe(group.id);
    expect([savedNode.x, savedNode.y, savedNode.width, savedNode.height, savedNode.rotation_deg, savedNode.opacity])
      .toEqual([1100, 430, 360, 220, 0, 1]);
    expect(saved.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === group.id)).toEqual(group);
    const history = await service.history(original.id, positioned.revision, 8);
    expect(history).toHaveLength(1);
    expect(history[0].change).toMatchObject({ type: "set_canvas_motion_keyframes", scene_id: scene.id, node_id: child.id });
    expect((history[0].change as { keyframes: unknown[] }).keyframes).toHaveLength(12);
    const object = page.getByRole("button", { name: "Canvas object " + savedNode.name });
    await expect(object).toHaveCSS("opacity", "1");
    await expect(object).toHaveCSS("clip-path", /^polygon\(/);
    const motion = page.getByRole("region", { name: "Typed motion keyframes" });
    await motion.locator(".motion-keyframe-jump").filter({ hasText: "1.000s" }).first().click();
    await expect(object).toHaveCSS("clip-path", "inset(0% 50% 0% 0%)");
    await expect(object).toHaveCSS("opacity", "0");

    // Continue editing the same source after the bulk save.
    await motion.locator(".motion-keyframe-jump").filter({ hasText: "0.000s" }).first().click();
    await page.getByLabel("Motion property", { exact: true }).selectOption("rotation_deg");
    await page.getByLabel("Motion keyframe value", { exact: true }).fill("-9");
    await page.getByLabel("Motion interpolation", { exact: true }).selectOption("linear");
    await motion.getByRole("button", { name: "Add or replace keyframe at playhead", exact: true }).click();
    await expect(revision).toHaveText("r" + (saved.revision + 1));
    let final = await service.project(original.id);
    const finalNode = final.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === child.id)!;
    expect(finalNode.keyframes).toHaveLength(12);
    expect(finalNode.keyframes.find(key => key.property === "rotation_deg" && key.at.num === "0")?.value).toBe(-9);
    await page.reload();
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await page.locator(".tree-row").filter({ hasText: scene.name }).click();
    await page.locator(".canvas-tree-row").filter({ hasText: child.name }).click();
    await expect(revision).toHaveText("r" + final.revision);
    await expect(motion.locator(".motion-keyframe-row")).toHaveCount(12);
    await expect(object).toHaveCSS("clip-path", /^polygon\(/);
    await expect(pose.getByLabel("Pose start rotation", { exact: true })).toHaveValue("-9");
    await pose.getByLabel("Pose end frame", { exact: true }).fill("45");
    await pose.getByLabel("Pose geometry curve", { exact: true }).selectOption("hold");
    await pose.getByLabel("Pose opacity", { exact: true }).selectOption("none");
    await pose.getByRole("button", { name: "Save native pose animation", exact: true }).click();
    await expect(revision).toHaveText("r" + (final.revision + 1));
    final = await service.project(original.id);
    const retimedNode = final.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === child.id)!;
    expect(retimedNode.keyframes).toHaveLength(10);
    expect(retimedNode.keyframes.some(key => key.property === "opacity" || (key.at.num === "1" && key.at.den === "1"))).toBe(false);
    expect(retimedNode.keyframes.filter(key => key.at.num !== "0").every(key =>
      key.at.num === "3" && key.at.den === "2" && key.interpolation === "hold")).toBe(true);
    await page.reload();
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await page.locator(".tree-row").filter({ hasText: scene.name }).click();
    await page.locator(".canvas-tree-row").filter({ hasText: child.name }).click();
    await expect(pose.getByLabel("Pose end frame", { exact: true })).toHaveValue("45");
    await expect(pose.getByLabel("Pose geometry curve", { exact: true })).toHaveValue("hold");
    await expect(pose.getByLabel("Pose opacity", { exact: true })).toHaveValue("none");
    await motion.locator(".motion-keyframe-jump").filter({ hasText: "1.500s" }).first().click();
    await expect(object).toHaveCSS("opacity", "1");
    expect(final.scenes.filter(item => item.id !== scene.id)).toEqual(original.scenes.filter(item => item.id !== scene.id));
    expect(final.assets).toEqual(original.assets);
    expect(final.audio).toEqual(original.audio);

    await page.close();
    expect(await service.rawRequest({ command: "prepare_read_only_handoff", args: null }))
      .toMatchObject({ ok: true, result: { read_only_handoff: true, journal_mode: "delete" } });
    await service.close(); closed = true;
    expect(service.lifecycleTaints).toEqual([]);
    expect(service.processExits).toHaveLength(1);
    expect(service.processExits[0]).toMatchObject({ code: 0, signal: null });
    const reader = await ScopedChangeService.createReadOnly({ binaryPath: service.binaryPath, databasePath, seedFixture: false, desktopInvokeFixture: false });
    try {
      expect(await reader.project(original.id)).toEqual(final);
      expect((await reader.history(original.id, positioned.revision, 8)).map(event => event.change.type))
        .toEqual(["set_canvas_motion_keyframes", "set_canvas_keyframe", "set_canvas_motion_keyframes"]);
    } finally { await reader.close(); }
  } finally {
    if (!page.isClosed()) await page.close();
    if (!closed) await service.close();
  }
});
