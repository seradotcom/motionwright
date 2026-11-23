/** Actual StudioService source/SQLite custody, not a Native pixel claim. */
import { expect, test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";
import type { Project } from "../src/types";
import type { PoseRecovery } from "../src/nativePoseRecovery";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "An exact-source StudioService binary is required");
test.describe.configure({ timeout: 120_000, retries: 0 });

test("a lost complete-pose reply retains original input and cannot replay after selection or reload", async ({ page }, info) => {
  const service = await ScopedChangeService.create({
    databasePath: info.outputPath("pose-recovery", "source.sqlite3"), desktopInvokeFixture: true,
  });
  const invoke = service.invoke.bind(service);
  let latest: Project | undefined, lost = false;
  const button = (name: string) => page.getByRole("button", { name, exact: true });
  const requests = () => service.transcript.filter(item => item.direction === "request"
    && "command" in item.payload && item.payload.command === "apply_change"
    && (item.payload.args?.request as {change?: {type?: string}} | undefined)?.change?.type === "set_canvas_motion_keyframes");
  try {
    const original = (await service.fixtureBootstrap()).project;
    const scene = original.scenes.find(item => item.name === "Independent synthetic scene")!;
    await service.attachFixturePage(page, original.id);
    await page.goto("/");
    await button("Canvas").click();
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
    const key = "motionwright.native-pose-recovery.v1:" + encodeURIComponent(JSON.stringify([
      original.id, original.generation, original.active_branch, scene.id, child.id,
    ]));
    const retained = () => page.evaluate(key => localStorage.getItem(key), key);
    const pose = page.getByRole("region", { name: "Native pose animation" });
    const input = pose.getByLabel("Pose start x", { exact: true });
    const select = async (name: string) => page.locator(".canvas-tree-row").filter({ hasText: name }).click();
    const reopen = async () => {
      await page.reload();
      await button("Canvas").click();
      await page.locator(".tree-row").filter({ hasText: scene.name }).click();
      await select(child.name);
    };
    await input.fill("-");
    await select(group.name);
    await select(child.name);
    await expect(input).toHaveValue("-");
    await reopen();
    await expect(input).toHaveValue("-");
    expect(requests()).toHaveLength(0);
    await input.fill("1000.000");
    await pose.getByLabel("Pose end frame", { exact: true }).fill("30");
    await pose.getByLabel("Pose opacity", { exact: true }).selectOption("out");
    service.invoke = async <T=unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const result = await invoke<T>(command, args);
      const change = (args.request as {change?: {type?: string}} | undefined)?.change;
      if (!lost && command === "apply_change" && change?.type === "set_canvas_motion_keyframes") {
        lost = true;
        throw new Error("Transport lost its reply after real SQLite pose commit");
      }
      return result;
    };
    await pose.getByRole("button", { name: "Create native pose animation", exact: true }).click();
    await expect(pose.getByText(/original pose save at revision .* unresolved result/)).toBeVisible();
    latest = await service.project(original.id);
    expect(latest.revision).toBe(created.revision + 1);
    const saved = latest.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === child.id)!;
    expect(saved.keyframes).toHaveLength(12);
    expect(requests()).toHaveLength(1);
    const unknownRaw = (await retained())!;
    const unknown = JSON.parse(unknownRaw) as PoseRecovery;
    expect(unknown.pending?.submitted.type).toBe("set_canvas_motion_keyframes");
    expect(unknown.raw.x).toBe("1000.000");
    expect(unknown.authority.revision).toBe(created.revision);
    await select(group.name); await select(child.name);
    await expect(input).toHaveValue("1000.000");
    await expect(input).toBeDisabled();
    await reopen();
    await expect(input).toHaveValue("1000.000");
    await expect(pose.getByRole("button", { name: "Save native pose animation", exact: true })).toBeDisabled();
    expect(await retained()).toBe(unknownRaw);
    await pose.getByRole("button", { name: "Read saved pose history", exact: true }).click();
    await expect(pose.getByText(/Readback found 1 matching change/)).toBeVisible();
    expect(await retained()).toBe(unknownRaw);
    expect(requests()).toHaveLength(1);
    await pose.getByRole("button", { name: "Retain unknown attempt and start a distinct edit", exact: true }).click();
    expect(await page.evaluate(key => localStorage.getItem(key), key + ":unknown:" + unknown.pending!.id)).toBe(unknownRaw);
    await expect(input).toBeEnabled();
    await input.fill("1000");
    await pose.getByRole("button", { name: "Save native pose animation", exact: true }).click();
    await expect(pose.getByText(/earlier unconfirmed pose cannot be resubmitted/)).toBeVisible();
    expect(requests()).toHaveLength(1);
    await input.fill("900");
    await pose.getByRole("button", { name: "Save native pose animation", exact: true }).click();
    await expect(revision).toHaveText("r" + (latest.revision + 1));
    latest = await service.project(original.id);
    expect(requests()).toHaveLength(2);
    expect(JSON.parse((await retained())!).pending).toBeNull();
    expect((await service.history(original.id, created.revision)).map(item => item.change.type))
      .toEqual(["set_canvas_motion_keyframes", "set_canvas_motion_keyframes"]);
    await reopen();
    await expect(input).toHaveValue("900");
    expect(await service.project(original.id)).toEqual(latest);
    expect(requests()).toHaveLength(2);
  } finally {
    service.invoke = invoke;
    await service.finalizeClosedStore(info.outputPath("closed-pose-store"), latest, [], [() => page.close()]);
  }
});
