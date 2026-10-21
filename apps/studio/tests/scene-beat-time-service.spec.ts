import { randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { Beat, Project, RationalTime } from "../src/types";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A selected studio_review_session Rust binary and real SQLite are required.");
test.setTimeout(120_000);

const r = (num: string, den = "1"): RationalTime => ({ num, den });
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const revision = (page: Page, value: number) =>
  expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + value);
const applied = (service: ScopedChangeService) => service.transcript.filter(entry =>
  entry.direction === "request" && "command" in entry.payload && entry.payload.command === "apply_change");

function savedBeat(project: Project, sceneId: string, beatId: string): Beat {
  const value = project.scenes.find(scene => scene.id === sceneId)?.beats.find(beat => beat.id === beatId);
  if (!value) throw new Error("The actual saved scene beat is missing.");
  return value;
}

async function openScene(page: Page, name: string) {
  await button(page, "Canvas").click();
  await page.locator(".tree-row").filter({ has: page.getByText(name, { exact: true }) }).click();
  await expect(page.getByRole("region", { name: "Scene beats", exact: true })).toBeVisible();
}

interface BeatCase {
  service: ScopedChangeService;
  baseline: Project;
  sceneId: string;
  sceneName: string;
  beat: Beat;
  applyCount: number;
  observe: (label: string) => Promise<Project>;
}

/** Test arrangement only. Every setup and UI change uses the compiled service. */
async function withSavedBeat(
  page: Page,
  info: TestInfo,
  seed: { start: RationalTime; duration: RationalTime },
  run: (context: BeatCase) => Promise<void>,
) {
  const service = await ScopedChangeService.create({
    databasePath: info.outputPath("studio", "source-project.sqlite3"), desktopInvokeFixture: true,
  });
  let final: Project | undefined, projectId: string | null = null;
  const evidencePath = info.outputPath("scene-beat-source-evidence.json");
  let evidenceWritten = false;
  try {
    const initial = (await service.fixtureBootstrap()).project;
    projectId = initial.id; final = initial;
    const sceneName = "Exact authored beat timing";
    const expanded = await service.apply(initial, {
      type: "add_scene", name: sceneName, objective: "Keep copy edits independent of authored time", duration_seconds: 2,
    });
    final = expanded;
    const scene = expanded.scenes.find(item => !initial.scenes.some(prior => prior.id === item.id));
    if (!scene) throw new Error("The real service did not create the arranged scene.");
    const beat: Beat = {
      id: randomUUID(), label: "Authored fractional beat", objective: "Original exact timing", ...seed,
    };
    const baseline = await service.apply(expanded, { type: "upsert_scene_beat", scene_id: scene.id, beat });
    final = baseline;
    expect(savedBeat(baseline, scene.id, beat.id)).toEqual(beat);
    const checkpoints: { label: string; project: Project; journal: Awaited<ReturnType<typeof service.history>> }[] = [];
    const observe = async (label: string): Promise<Project> => {
      const current = await service.project(baseline.id);
      final = current;
      checkpoints.push({ label, project: current, journal: await service.history(baseline.id, baseline.revision) });
      // Write the actual observation before assertions, retaining the baseline
      // failure as evidence instead of replacing rounded values with expectations.
      await writeFile(evidencePath, JSON.stringify({
        schemaVersion: 1,
        source: "actual App and api.ts -> compiled StudioService -> SQLite -> Project and journal",
        fixture: "explicit synthetic test arrangement; commands and persistence are real",
        excludes: ["installed Tauri", "native render frames", "audio sample boundaries", "human acceptance"],
        resultAuthority: "The original Playwright result decides assertions; snapshots alone do not establish PASS.",
        sourceScene: scene.id, sourceBeat: beat.id, baseline, checkpoints,
      }, null, 2));
      evidenceWritten = true;
      return current;
    };
    await service.attachFixturePage(page, baseline.id);
    await page.goto("/");
    await revision(page, baseline.revision);
    await openScene(page, sceneName);
    const applyCount = applied(service).length;
    await observe("real service baseline before UI edits");
    await run({ service, baseline, sceneId: scene.id, sceneName, beat, applyCount, observe });
  } finally {
    if (projectId) {
      try { final = await service.project(projectId); } catch { /* finalization records any inaccessible store */ }
    }
    await service.finalizeClosedStore(info.outputPath("beat-closed-store"), final, [], [() => page.close()]);
    if (evidenceWritten) {
      await info.attach("scene-beat-source-evidence", { path: evidencePath, contentType: "application/json" });
    }
  }
}

test("copy-only scene beat edits retain exact fractional frame times in SQLite and after process restart", async ({ page }, info) => {
  await withSavedBeat(page, info, { start: r("1001", "30000"), duration: r("1001", "30000") },
    async ({ service, baseline, sceneId, sceneName, beat, applyCount, observe }) => {
      const row = page.locator('.scene-beat-row[data-beat-id="' + beat.id + '"]');
      const objective = "Human copy correction with the original fractional frame timing";
      await row.getByLabel("Beat objective", { exact: true }).fill(objective);
      await row.getByRole("button", { name: "Save beat", exact: true }).click();
      await revision(page, baseline.revision + 1);
      let saved = await observe("objective-only UI commit");
      expect(savedBeat(saved, sceneId, beat.id)).toEqual({ ...beat, objective });
      expect(applied(service)).toHaveLength(applyCount + 1);

      const label = "Fractional beat copy corrected";
      await row.getByLabel("Beat label", { exact: true }).fill(label);
      await row.getByRole("button", { name: "Save beat", exact: true }).click();
      await revision(page, baseline.revision + 2);
      saved = await observe("label-only UI commit");
      expect(savedBeat(saved, sceneId, beat.id)).toEqual({ ...beat, objective, label });
      const journal = await service.history(baseline.id, baseline.revision);
      expect(journal.map(event => event.change)).toEqual([
        { type: "upsert_scene_beat", scene_id: sceneId, beat: { ...beat, objective } },
        { type: "upsert_scene_beat", scene_id: sceneId, beat: { ...beat, objective, label } },
      ]);
      expect(applied(service)).toHaveLength(applyCount + 2);
      await button(page, "Timeline").click();
      await expect(page.getByRole("button", { name: "Beat: " + label, exact: true })).toBeVisible();

      await service.close(); await service.start(); await page.reload();
      await revision(page, saved.revision);
      await openScene(page, sceneName);
      await expect(row.getByLabel("Beat label", { exact: true })).toHaveValue(label);
      await expect(row.getByLabel("Beat objective", { exact: true })).toHaveValue(objective);
      await expect(row.getByLabel("Beat start seconds", { exact: true })).toHaveValue("1001/30000");
      await expect(row.getByLabel("Beat duration seconds", { exact: true })).toHaveValue("1001/30000");
      expect(await observe("new service process and actual App reload")).toEqual(saved);
      expect(await service.history(baseline.id, baseline.revision)).toEqual(journal);
      expect(applied(service)).toHaveLength(applyCount + 2);
    });
});

test("authored decimal and sample times save exactly while incomplete and out-of-scene inputs cannot commit", async ({ page }, info) => {
  await withSavedBeat(page, info, { start: r("0"), duration: r("1") },
    async ({ service, baseline, sceneId, sceneName, beat, applyCount, observe }) => {
      const row = page.locator('.scene-beat-row[data-beat-id="' + beat.id + '"]');
      const start = row.getByLabel("Beat start seconds", { exact: true });
      const duration = row.getByLabel("Beat duration seconds", { exact: true });
      const save = row.getByRole("button", { name: "Save beat", exact: true });
      await start.fill("0.01001"); await duration.fill("0.1234567"); await save.click();
      await revision(page, baseline.revision + 1);
      const decimal = await observe("exact authored decimal UI commit");
      expect(savedBeat(decimal, sceneId, beat.id)).toEqual({
        ...beat, start: r("1001", "100000"), duration: r("1234567", "10000000"),
      });
      expect(applied(service)).toHaveLength(applyCount + 1);

      await start.fill("1e");
      await expect(start).toHaveValue("1e"); await expect(save).toBeDisabled();
      await expect(row.getByRole("alert", { name: "Beat timing validation" })).toContainText("Complete");
      expect(await observe("incomplete input retained without a write")).toEqual(decimal);
      expect(applied(service)).toHaveLength(applyCount + 1);
      await start.fill("1.9999999"); await duration.fill("0.0000002");
      await expect(save).toBeDisabled();
      await expect(row.getByRole("alert", { name: "Beat timing validation" })).toContainText("fit inside");
      expect(await observe("exact out-of-scene interval rejected without a write")).toEqual(decimal);
      expect(applied(service)).toHaveLength(applyCount + 1);

      await start.fill("1/48000"); await duration.fill("1001/30000"); await save.click();
      await revision(page, baseline.revision + 2);
      const saved = await observe("explicit sample and fractional frame UI commit");
      expect(savedBeat(saved, sceneId, beat.id)).toEqual({
        ...beat, start: r("1", "48000"), duration: r("1001", "30000"),
      });
      const journal = await service.history(baseline.id, baseline.revision);
      expect(journal.map(event => event.change.type)).toEqual(["upsert_scene_beat", "upsert_scene_beat"]);
      expect(applied(service)).toHaveLength(applyCount + 2);
      await service.close(); await service.start(); await page.reload();
      await revision(page, saved.revision); await openScene(page, sceneName);
      await expect(start).toHaveValue("1/48000"); await expect(duration).toHaveValue("1001/30000");
      expect(await observe("authored rational values after process restart")).toEqual(saved);
      expect(await service.history(baseline.id, baseline.revision)).toEqual(journal);
      expect(applied(service)).toHaveLength(applyCount + 2);
    });
});

test("Fill timing gap commits the exact shared frame boundary and preserves both beats after restart", async ({ page }, info) => {
  await withSavedBeat(page, info, { start: r("1001", "30000"), duration: r("58999", "30000") },
    async ({ service, baseline, sceneId, sceneName, beat, applyCount, observe }) => {
      const editor = page.getByRole("region", { name: "Scene beats", exact: true });
      await expect(editor.locator(".beat-coverage")).toHaveText("timing gap");
      await editor.getByRole("button", { name: "Fill timing gap", exact: true }).click();
      await revision(page, baseline.revision + 1);
      const saved = await observe("exact first-gap UI commit");
      const beats = saved.scenes.find(scene => scene.id === sceneId)!.beats;
      expect(beats).toHaveLength(2);
      expect(beats[1]).toEqual(beat);
      expect(beats[0]).toEqual({
        id: expect.any(String), label: "New beat", objective: "", start: r("0"), duration: r("1001", "30000"),
      });
      expect(beats[0].id).not.toBe(beat.id);
      await expect(editor.locator(".beat-coverage")).toHaveText("timing covered");
      await expect(editor.getByRole("button", { name: "Fill timing gap", exact: true })).toBeDisabled();
      const journal = await service.history(baseline.id, baseline.revision);
      expect(journal.map(event => event.change)).toEqual([
        { type: "upsert_scene_beat", scene_id: sceneId, beat: beats[0] },
      ]);
      expect(applied(service)).toHaveLength(applyCount + 1);
      await service.close(); await service.start(); await page.reload();
      await revision(page, saved.revision); await openScene(page, sceneName);
      await expect(editor.locator(".scene-beat-row")).toHaveCount(2);
      await expect(editor.locator(".beat-coverage")).toHaveText("timing covered");
      expect(await observe("exact adjacent beats after process restart")).toEqual(saved);
      expect(await service.history(baseline.id, baseline.revision)).toEqual(journal);
      expect(applied(service)).toHaveLength(applyCount + 1);
    });
});

test("a saved sub-microsecond gap remains editable and cannot display exact coverage until filled", async ({ page }, info) => {
  await withSavedBeat(page, info, { start: r("0"), duration: r("19999999", "10000000") },
    async ({ service, baseline, sceneId, sceneName, beat, applyCount, observe }) => {
      const editor = page.getByRole("region", { name: "Scene beats", exact: true });
      await expect(editor.locator(".beat-coverage")).toHaveText("timing gap");
      const fill = editor.getByRole("button", { name: "Fill timing gap", exact: true });
      await expect(fill).toBeEnabled(); await fill.click();
      await revision(page, baseline.revision + 1);
      const saved = await observe("sub-microsecond gap filled exactly through UI");
      const beats = saved.scenes.find(scene => scene.id === sceneId)!.beats;
      expect(beats).toHaveLength(2);
      expect(beats[0]).toEqual(beat);
      expect(beats[1]).toEqual({
        id: expect.any(String), label: "New beat", objective: "",
        start: r("19999999", "10000000"), duration: r("1", "10000000"),
      });
      await expect(editor.locator(".beat-coverage")).toHaveText("timing covered");
      await expect(fill).toBeDisabled();
      const journal = await service.history(baseline.id, baseline.revision);
      expect(journal.map(event => event.change)).toEqual([
        { type: "upsert_scene_beat", scene_id: sceneId, beat: beats[1] },
      ]);
      expect(applied(service)).toHaveLength(applyCount + 1);
      await service.close(); await service.start(); await page.reload();
      await revision(page, saved.revision); await openScene(page, sceneName);
      await expect(editor.locator(".beat-coverage")).toHaveText("timing covered");
      expect(await observe("sub-microsecond shared boundary after process restart")).toEqual(saved);
      expect(await service.history(baseline.id, baseline.revision)).toEqual(journal);
      expect(applied(service)).toHaveLength(applyCount + 1);
    });
});
