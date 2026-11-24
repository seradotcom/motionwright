/** Real Studio edits and original SQLite reopen; no Native render or pixel claim. */
import { expect, test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "An exact-source StudioService binary is required");
test.describe.configure({ timeout: 90_000, retries: 0 });

test("Studio retains, saves and replaces a clipped waypoint track with independent fade in the original Store", async ({ page }, info) => {
  const databasePath = info.outputPath("waypoint-pose", "source.sqlite3");
  const service = await ScopedChangeService.create({ databasePath, desktopInvokeFixture: true });
  let closed = false;
  try {
    const original = (await service.fixtureBootstrap()).project;
    const scene = original.scenes.find(item => item.name === "Independent synthetic scene")!;
    expect(scene).toBeDefined();
    await service.attachFixturePage(page, original.id);
    const selectScene = async () => {
      await page.getByRole("button", { name: "Canvas", exact: true }).click();
      await page.locator(".tree-row").filter({ hasText: scene.name }).click();
    };
    await page.goto("/");
    await selectScene();
    const revision = page.locator(".command-bar .revision-chip");
    await page.locator(".canvas-create-row").getByRole("button", { name: "clip group", exact: true }).click();
    await expect(revision).toHaveText("r" + (original.revision + 1));
    const grouped = await service.project(original.id);
    const group = grouped.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.kind === "clip_group")!;
    await page.locator(".canvas-create-row").getByRole("button", { name: "shape", exact: true }).click();
    await expect(revision).toHaveText("r" + (original.revision + 2));
    const created = await service.project(original.id);
    const child = created.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.parent_id === group.id)!;
    const transform = page.locator(".canvas-draft-inspector").filter({
      has: page.locator("#canvas-draft-" + child.id + "-x"),
    });
    await expect(transform).toHaveCount(1);
    await transform.getByLabel("Canvas X", { exact: true }).fill("1100");
    await transform.getByRole("button", { name: "Commit transform", exact: true }).click();
    await expect(revision).toHaveText("r" + (original.revision + 3));
    const positioned = await service.project(original.id);
    const originalNode = positioned.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === child.id)!;
    const pose = page.getByRole("region", { name: "Native pose animation" });
    for (const [label, value] of [
      ["Pose start x", "1000"], ["Pose start y", "410"], ["Pose start width", "280"],
      ["Pose start height", "160"], ["Pose start rotation", "-18"], ["Pose end frame", "30"],
    ]) await pose.getByLabel(label, { exact: true }).fill(value);
    await pose.getByLabel("Pose geometry curve", { exact: true }).selectOption("ease_out_cubic");
    await pose.getByLabel("Pose opacity", { exact: true }).selectOption("out");
    await pose.getByLabel("Pose fade curve", { exact: true }).selectOption("linear");
    await pose.getByLabel("Pose fade end frame", { exact: true }).fill("15");
    for (const [index, values, curve] of [
      [1, { frame: "10", x: "1020", y: "425", width: "310", height: "170", rotation: "-5" }, "hold"],
      [2, { frame: "20", x: "1060", y: "440", width: "330", height: "200", rotation: "8" }, "linear"],
    ] as const) {
      await pose.getByRole("button", { name: "Add intermediate pose", exact: true }).click();
      for (const [field, value] of Object.entries(values))
        await pose.getByLabel("Intermediate pose " + index + " " + field, { exact: true }).fill(value);
      await pose.getByLabel("Intermediate pose " + index + " curve", { exact: true }).selectOption(curve);
    }
    const create = pose.getByRole("button", { name: "Create native pose animation", exact: true });
    await pose.getByLabel("Intermediate pose 2 frame", { exact: true }).fill("10");
    await expect(create).toBeDisabled();
    expect((await service.project(original.id)).revision).toBe(positioned.revision);
    await pose.getByLabel("Intermediate pose 2 frame", { exact: true }).fill("20");

    // Draft reload preserves the new optional fields without sending a change.
    await page.reload();
    await selectScene();
    await page.locator(".canvas-tree-row").filter({ hasText: child.name }).click();
    await expect(pose.getByLabel("Intermediate pose 1 frame", { exact: true })).toHaveValue("10");
    await expect(pose.getByLabel("Intermediate pose 1 curve", { exact: true })).toHaveValue("hold");
    await expect(pose.getByLabel("Intermediate pose 2 rotation", { exact: true })).toHaveValue("8");
    await expect(pose.getByLabel("Pose fade end frame", { exact: true })).toHaveValue("15");
    await expect(revision).toHaveText("r" + positioned.revision);
    await expect(create).toBeEnabled();
    await create.click();
    await expect(revision).toHaveText("r" + (positioned.revision + 1));
    const saved = await service.project(original.id);
    const savedNode = saved.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === child.id)!;
    expect(savedNode.keyframes).toHaveLength(22);
    expect({ ...savedNode, keyframes: [] }).toEqual(originalNode);
    for (const property of ["x", "y", "width", "height", "rotation_deg"]) {
      const channel = savedNode.keyframes.filter(key => key.property === property)
        .sort((a, b) => Number(a.at.num) / Number(a.at.den) - Number(b.at.num) / Number(b.at.den));
      expect(channel.map(key => [key.at.num, key.at.den, key.interpolation])).toEqual([
        ["0", "1", "linear"], ["1", "3", "hold"], ["2", "3", "linear"], ["1", "1", "ease_out_cubic"],
      ]);
    }
    expect(savedNode.keyframes.filter(key => key.property === "opacity")).toEqual(expect.arrayContaining([
      expect.objectContaining({ at: { num: "0", den: "1" }, value: 1 }),
      expect.objectContaining({ at: { num: "1", den: "2" }, value: 0, interpolation: "linear" }),
    ]));
    const firstHistory = await service.history(original.id, positioned.revision, 8);
    expect(firstHistory).toHaveLength(1);
    expect(firstHistory[0].change, "WAYPOINT_ORIGINAL_CHANGE_IDENTITY").toMatchObject({
      type: "set_canvas_motion_keyframes", scene_id: scene.id, node_id: child.id,
    });
    // The journal preserves submitted order; the saved node sorts time/property.
    // Compare the complete unique key set without imposing either serialization order.
    const journalKeys = (firstHistory[0].change as { keyframes: unknown[] }).keyframes;
    expect(journalKeys, "WAYPOINT_ORIGINAL_CHANGE_KEY_COUNT").toHaveLength(22);
    expect(journalKeys, "WAYPOINT_ORIGINAL_CHANGE_KEY_VALUES").toEqual(expect.arrayContaining(savedNode.keyframes));

    // A subsequent edit replaces the whole original track, including old times.
    await page.reload();
    await selectScene();
    await page.locator(".canvas-tree-row").filter({ hasText: child.name }).click();
    await expect(pose.getByLabel("Intermediate pose 2 frame", { exact: true })).toHaveValue("20");
    await pose.getByLabel("Intermediate pose 1 frame", { exact: true }).fill("12");
    await pose.getByLabel("Intermediate pose 1 x", { exact: true }).fill("1040");
    await pose.getByLabel("Pose fade end frame", { exact: true }).fill("18");
    await pose.getByRole("button", { name: "Save native pose animation", exact: true }).click();
    await expect(revision).toHaveText("r" + (saved.revision + 1));
    let final = await service.project(original.id);
    const retimedScene = final.scenes.find(item => item.id === scene.id)!;
    const retimedNode = retimedScene.nodes.find(item => item.id === child.id)!;
    expect(retimedNode.keyframes).toHaveLength(22);
    expect(retimedNode.keyframes.some(key => key.at.num === "1" && ["2", "3"].includes(key.at.den))).toBe(false);
    expect(retimedNode.keyframes.find(key => key.property === "x" && key.at.num === "2" && key.at.den === "5"))
      .toMatchObject({ value: 1040, interpolation: "hold" });
    expect(retimedNode.keyframes.find(key => key.property === "opacity" && key.at.num === "3" && key.at.den === "5"))
      .toMatchObject({ value: 0, interpolation: "linear" });
    expect(retimedScene.nodes.find(item => item.id === group.id)).toEqual(group);

    await page.getByRole("button", { name: "Production", exact: true }).click();
    await page.getByLabel("Production working scene", { exact: true }).selectOption(scene.id);
    await page.getByRole("button", { name: "Camera", exact: true }).click();
    await page.getByLabel("End center X", { exact: true }).fill(String(retimedScene.camera.center_x + 12));
    await page.getByLabel("Camera duration frames", { exact: true }).fill("30");
    await page.getByRole("button", { name: "Attach camera reframe", exact: true }).click();
    await expect(revision).toHaveText("r" + (final.revision + 1));
    final = await service.project(original.id);
    expect(final.scenes.find(item => item.id === scene.id)!.nodes).toEqual(retimedScene.nodes);
    expect(final.production_design?.camera_reframes?.find(item => item.scene_id === scene.id))
      .toMatchObject({ end_center_x: retimedScene.camera.center_x + 12, duration_frames: 30 });
    await page.reload();
    await selectScene();
    await page.locator(".canvas-tree-row").filter({ hasText: child.name }).click();
    await expect(pose.getByLabel("Intermediate pose 1 frame", { exact: true })).toHaveValue("12");
    await expect(pose.getByLabel("Intermediate pose 1 x", { exact: true })).toHaveValue("1040");
    await expect(pose.getByLabel("Intermediate pose 2 frame", { exact: true })).toHaveValue("20");
    await expect(pose.getByLabel("Pose fade end frame", { exact: true })).toHaveValue("18");
    await expect(page.getByRole("region", { name: "Typed motion keyframes" }).locator(".motion-keyframe-row")).toHaveCount(22);
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
    const reader = await ScopedChangeService.createReadOnly({
      binaryPath: service.binaryPath, databasePath, seedFixture: false, desktopInvokeFixture: false,
    });
    try {
      expect(await reader.project(original.id)).toEqual(final);
      expect((await reader.history(original.id, positioned.revision, 8)).map(event => event.change.type))
        .toEqual(["set_canvas_motion_keyframes", "set_canvas_motion_keyframes", "upsert_camera_reframe"]);
    } finally { await reader.close(); }
  } finally {
    if (!page.isClosed()) await page.close();
    if (!closed) await service.close();
  }
});
