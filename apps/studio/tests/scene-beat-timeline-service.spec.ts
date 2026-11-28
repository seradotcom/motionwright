import { expect,test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";
import { beatTimelineEditor,commitBeatOperation,createBeatTimelineScene,prepareBeatOperation,reopenBeatTimeline } from "./support/sceneBeatTimelineJourney";
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"An exact-source StudioService binary is required.");
test.describe.configure({timeout:180_000,retries:0});
test("Beat split uses real Service authority, one SQLite history change and a conditional inverse after closing and reopening",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","beat-timeline.sqlite3"),desktopInvokeFixture:true});
  let finalized=false;
  try {
    const boot=await service.fixtureBootstrap();await service.attachFixturePage(page,boot.project.id);await page.goto("/");
    const context=await createBeatTimelineScene(page),base=await service.project(boot.project.id),editor=beatTimelineEditor(page);
    await editor.getByLabel("Beat operation start",{exact:true}).fill("1/");
    await page.reload();await reopenBeatTimeline(page,context.name);
    await expect(editor.getByLabel("Beat operation start",{exact:true})).toHaveValue("1/");
    await prepareBeatOperation(page,context.beatId,"split",{start:"2"},"Keep the original editable identity and split the support.");
    expect(await service.project(base.id)).toEqual(base);expect(await service.history(base.id,base.revision,8)).toEqual([]);
    await commitBeatOperation(page,base.revision);
    const saved=await service.project(base.id),scene=saved.scenes.find(scene=>scene.id===context.sceneId)!;
    expect(scene.beats).toHaveLength(2);expect(scene.beats[0].id).toBe(context.beatId);
    expect(scene.beats[0].duration).toEqual({num:"2",den:"1"});expect(scene.beats[1].duration).toEqual({num:"4",den:"1"});
    expect(scene.nodes).toEqual(base.scenes.find(scene=>scene.id===context.sceneId)!.nodes);
    const events=await service.history(base.id,base.revision,8);expect(events).toHaveLength(1);
    expect(events[0].change.type).toBe("edit_scene_beat_timeline");
    await service.close();await service.start();await page.reload();await reopenBeatTimeline(page,context.name);
    expect(await service.project(base.id)).toEqual(saved);
    await editor.getByRole("region",{name:"Saved beat timing history",exact:true}).getByRole("button",{name:"Review inverse r"+saved.revision,exact:true}).click();
    await editor.getByRole("button",{name:"Preview beat timing",exact:true}).click();
    await commitBeatOperation(page,saved.revision);
    const restored=await service.project(base.id);expect(restored.scenes).toEqual(base.scenes);
    expect((await service.history(base.id,base.revision,8)).map(event=>event.change.type)).toEqual(["edit_scene_beat_timeline","edit_scene_beat_timeline"]);
    const closure=await service.finalizeClosedStore(info.outputPath("closed-store"),restored,[],[async()=>{await page.close();}]);finalized=true;
    expect(closure.oracle?.project).toEqual(restored);expect(service.lifecycleTaints).toEqual([]);
  }finally{if(!page.isClosed())await page.close();if(!finalized)await service.close();}
});

test("a scene split keeps its exact saved cut and recovers a lost reply without another write", async ({ page }, info) => {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "scene-split.sqlite3"), desktopInvokeFixture: true });
  let finalized = false;
  try {
    const boot = await service.fixtureBootstrap();
    const name = "Editable scene split";
    const base = await service.apply(boot.project, { type: "add_scene", name, objective: "Preserve editorial timing", duration_seconds: 6 });
    const scene = base.scenes.at(-1)!;
    const invoke = service.invoke.bind(service);
    let sent = 0;
    let originalId: string | undefined;
    let observedId: string | undefined;
    service.invoke = async <T>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as { request_id?: string; change?: { type?: string } } | undefined;
      if (command === "observe_scene_split_receipt") observedId = request?.request_id;
      if (command !== "apply_change" || request?.change?.type !== "split_scene") return invoke<T>(command, args);
      originalId = request.request_id;
      sent++;
      await invoke<T>(command, args);
      throw new Error("The original split reply was lost.");
    };
    await service.attachFixturePage(page, base.id);
    await page.goto("/");
    await page.locator(".tree-row").filter({ has: page.getByText(name, { exact: true }) }).click();
    const start = Number(scene.start.num) / Number(scene.start.den);
    await page.getByTitle("Go to timecode", { exact: true }).click();
    await page.getByLabel("Go to timecode", { exact: true }).fill(String(start + 2) + "s");
    await page.getByRole("button", { name: "Go", exact: true }).click();
    await page.getByRole("button", { name: "Split scene", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Split scene", exact: true });
    await expect(dialog).toContainText("2.000 s before");
    expect(await service.history(base.id, base.revision, 8)).toEqual([]);
    await dialog.getByRole("button", { name: "Save scene split", exact: true }).click();
    await expect(dialog).toContainText("The original split reply was lost.");
    const saved = await service.project(base.id);
    const right = saved.scenes.at(-1)!;
    expect(saved.revision).toBe(base.revision + 1);
    expect(right.name).toBe(name + " (continued)");
    expect(saved.scenes.at(-2)?.duration).toEqual({ num: "2", den: "1" });
    expect(right.duration).toEqual({ num: "4", den: "1" });
    expect(await service.history(base.id, base.revision, 8)).toHaveLength(1);
    await service.close();
    await service.start();
    await page.reload();
    await page.getByRole("button", { name: "Check scene split", exact: true }).click();
    await dialog.getByRole("button", { name: "Check original split", exact: true }).click();
    await expect(page.getByText("The original split was confirmed.", { exact: true })).toBeVisible();
    expect(observedId).toBe(originalId);
    expect(sent).toBe(1);
    const closure = await service.finalizeClosedStore(info.outputPath("closed-store"), saved, [], [async () => { await page.close(); }]);
    finalized = true;
    expect(closure.oracle?.project).toEqual(saved);
  } finally {
    if (!page.isClosed()) await page.close();
    if (!finalized) await service.close();
  }
});
