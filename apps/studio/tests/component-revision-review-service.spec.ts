import { randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { defaultHeroConfig } from "../src/creativeProduction";
import { inspectComponentRevision } from "../src/componentRevisionPreview";
import type { HeroConfig } from "../src/creativeProduction";
import type { Change, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Real App + source-built StudioService + grants + SQLite. The initial author
// input is a controlled fixture; no saved response or native frame is fabricated.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A source-built StudioService runner is required; no simulated persistence fallback.");
test.setTimeout(120_000);
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
type Failure = { reason: unknown };
const detail = (reason: unknown) => reason instanceof Error
  ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) };
function commandCount(service: ScopedChangeService, command: string) {
  return service.transcript.filter(entry => entry.direction === "request"
    && "command" in entry.payload && entry.payload.command === command).length;
}
function projectEditGrantCount(service: ScopedChangeService) {
  return service.transcript.filter(entry => entry.direction === "request" && "command" in entry.payload
    && entry.payload.command === "issue_effect_grant"
    && (entry.payload.args?.request as { effect?: string } | undefined)?.effect === "project_edit").length;
}
async function open(page: Page, service: ScopedChangeService, project: Project, sceneId: string, attach = true) {
  if (attach) { await service.attachFixturePage(page, project.id); await page.goto("/"); }
  await button(page, "Production").click();
  await page.getByLabel("Production working scene").selectOption(sceneId);
  await expect(button(page, "Update component")).toBeVisible();
}
async function finish(page: Page, info: TestInfo, service: ScopedChangeService, final: Project | undefined,
  observations: Record<string, unknown>, primary?: Failure) {
  const failures: unknown[] = primary ? [primary.reason] : [];
  let custody: StoreFinalization | undefined;
  try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, [], [() => page.close()]); }
  catch (reason) { failures.push(reason); }
  try {
    const path = info.outputPath("component-revision-review-service-evidence.json");
    await writeFile(path, JSON.stringify({
      scope: "controlled authoring scenario against real StudioService + grants + SQLite + App DOM",
      exclusions: ["installed Tauri", "native renderer pixels", "version migration", "human acceptance"],
      observations, final, canonicalOracle: custody?.oracle,
      primaryFailure: primary ? detail(primary.reason) : null, failures: failures.map(detail),
      services: [{ databasePath: service.databasePath, binaryPath: service.binaryPath,
        transcript: service.transcript, processExits: service.processExits,
        lifecycleTaints: service.lifecycleTaints, stderr: service.stderr }],
    }, null, 2));
    await info.attach("component-revision-review-service-evidence", { path, contentType: "application/json" });
    await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
  } catch (reason) { failures.push(reason); }
  if (failures.length === 1) throw failures[0];
  if (failures.length > 1) throw new AggregateError(failures, "Component comparison and store finalization retained separate failures", { cause: primary?.reason });
}
async function arrange(service: ScopedChangeService) {
  let project = (await service.fixtureBootstrap()).project;
  for (const lock of [...project.locks]) project = await service.apply(project, { type: "remove_lock", lock_id: lock.id });
  const scene = project.scenes.find(value => value.renderer === "motion-canvas"
    && BigInt(value.duration.num) >= 2n * BigInt(value.duration.den)
    && !(project.production_design?.heroes ?? []).some(hero => hero.scene_id === value.id));
  if (!scene) throw new Error("A separate eligible Motion Canvas scene is required; existing components remain intact.");
  const instanceId = randomUUID();
  const config: HeroConfig = { ...defaultHeroConfig(), layout: "product_hero_reveal", eyebrow: "EDITORIAL / ORIGINAL STUDY",
    headline: "One authored idea", body: "A short authored explanation.", wordmark: "MW" };
  project = await service.apply(project, { type: "upsert_product_hero", scene_id: scene.id, instance_id: instanceId, config });
  const hero = project.production_design!.heroes.find(value => value.id === instanceId)!;
  const eyebrow = hero.baseline.find(node => node.name.endsWith(" / eyebrow"))!;
  const body = hero.baseline.find(node => node.name.endsWith(" / body"))!;
  project = await service.apply(project, { type: "update_canvas_text", scene_id: scene.id, node_id: eyebrow.id,
    text: "  An independent authored line\n " });
  project = await service.apply(project, { type: "update_canvas_style", scene_id: scene.id, node_id: body.id,
    style: { ...body.style, fill: "#FFE0A0" } });
  const unrelated = { ...structuredClone(body), id: randomUUID(), name: "Separate authored object",
    text: "Another object that must remain exact", parent_id: null, relations: [], property_locks: [], keyframes: [] };
  project = await service.apply(project, { type: "add_canvas_node", scene_id: scene.id, node: unrelated });
  return { project, sceneId: scene.id, instanceId, eyebrowId: eyebrow.id, bodyId: body.id, unrelated };
}
function expectedSaved(before: Project, nodes: Awaited<ReturnType<typeof inspectComponentRevision>>, receipt: Project) {
  const expected = structuredClone(before);
  expected.revision = before.revision + 1; expected.updated_at = receipt.updated_at;
  expect(Number.isFinite(Date.parse(receipt.updated_at))).toBe(true);
  expect(Date.parse(receipt.updated_at)).toBeGreaterThanOrEqual(Date.parse(before.updated_at));
  expected.branches = expected.branches.map(branch => branch.id === expected.active_branch
    ? { ...branch, head_revision: expected.revision } : branch);
  expected.reviews = expected.reviews.map(review => review.anchor.branch_id === expected.active_branch
    && review.anchor.revision < expected.revision && review.status === "resolved"
    ? { ...review, status: "needs_recheck" as const, resolution: null, resolved_at: null } : review);
  const scene = expected.scenes.find(value => value.id === nodes.sceneId)!;
  scene.nodes = structuredClone(nodes.after); scene.status = "draft";
  expected.production_design!.heroes = expected.production_design!.heroes.map(hero =>
    hero.id === nodes.instanceId ? structuredClone(nodes.afterInstance) : hero);
  return expected;
}

test("Component v1 review shows retained human objects before a real family change and preserves the full result across SQLite restart", async ({ page }, info) => {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const arranged = await arrange(service), initial = arranged.project;
    const { sceneId, instanceId, eyebrowId, bodyId, unrelated } = arranged;
    const hero = initial.production_design!.heroes.find(value => value.id === instanceId)!;
    const target: HeroConfig = { ...hero.config, layout: "split_explanation" };
    const comparison = await inspectComponentRevision(initial, sceneId, instanceId, target, 1);
    observations.initial = initial; observations.comparison = comparison;
    const applies = commandCount(service, "apply_change"), grants = projectEditGrantCount(service);
    await open(page, service, initial, sceneId);
    await page.getByLabel("Component layout", { exact: true }).selectOption("split_explanation");
    const review = page.getByRole("region", { name: "Component change review", exact: true });
    await expect(review.getByRole("heading", { name: "Saved scene", exact: true })).toBeVisible();
    await expect(review.getByRole("heading", { name: "After this change", exact: true })).toBeVisible();
    await expect(review).toContainText("An independent authored line");
    await expect(review).toContainText("#FFE0A0");
    await expect(review).toContainText("6 component objects retain their identities");
    await expect(button(page, "Update component")).toBeEnabled();
    expect(await service.project(initial.id)).toEqual(initial);
    expect(commandCount(service, "apply_change")).toBe(applies);
    expect(projectEditGrantCount(service)).toBe(grants);
    await page.reload(); await open(page, service, initial, sceneId, false);
    await expect(page.getByLabel("Component layout", { exact: true })).toHaveValue("split_explanation");
    await expect(review).toContainText("An independent authored line");
    await expect(button(page, "Update component")).toBeEnabled();
    expect(commandCount(service, "apply_change")).toBe(applies);
    expect(projectEditGrantCount(service)).toBe(grants);
    await page.screenshot({ path: info.outputPath("component-merged-review.png"), fullPage: true });
    await button(page, "Update component").click();
    await expect(page.locator(".revision-chip").first()).toHaveText("r" + (initial.revision + 1));
    final = await service.project(initial.id);
    expect(final).toEqual(expectedSaved(initial, comparison, final));
    const saved = final.scenes.find(value => value.id === sceneId)!;
    expect(saved.nodes.map(node => node.id)).toEqual(initial.scenes.find(value => value.id === sceneId)!.nodes.map(node => node.id));
    expect(saved.nodes.find(node => node.id === eyebrowId)?.text).toBe("  An independent authored line\n ");
    expect(saved.nodes.find(node => node.id === bodyId)?.style.fill).toBe("#FFE0A0");
    expect(saved.nodes.find(node => node.id === unrelated.id)).toEqual(unrelated);
    const events = await service.history(initial.id, initial.revision);
    expect(events.map(event => event.change)).toEqual([{ type: "upsert_product_hero", scene_id: sceneId, instance_id: instanceId, config: target }]);
    expect(commandCount(service, "apply_change")).toBe(applies + 1);
    expect(projectEditGrantCount(service)).toBe(grants + 1);
    await service.close(); await service.start();
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id, initial.revision)).toEqual(events);
    await page.reload(); await open(page, service, final, sceneId, false);
    await expect(review).toContainText("An independent authored line");
    observations.journal = events;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, primary); }
});

test("Component v1 review blocks a conflicting human edit without UI dispatch and the real service preserves Project and journal", async ({ page }, info) => {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const arranged = await arrange(service), { sceneId, instanceId, bodyId } = arranged;
    const initial = await service.apply(arranged.project, { type: "update_canvas_text", scene_id: sceneId, node_id: bodyId, text: "An authored explanation that must survive." });
    const hero = initial.production_design!.heroes.find(value => value.id === instanceId)!;
    observations.initial = initial;
    const applies = commandCount(service, "apply_change"), grants = projectEditGrantCount(service);
    await open(page, service, initial, sceneId);
    await page.getByLabel("Supporting copy", { exact: true }).fill("A conflicting replacement explanation.");
    const review = page.getByRole("region", { name: "Component change review", exact: true });
    await expect(review.getByRole("alert")).toContainText("override conflict");
    await expect(button(page, "Update component")).toBeDisabled();
    expect(commandCount(service, "apply_change")).toBe(applies);
    expect(projectEditGrantCount(service)).toBe(grants);
    expect(await service.project(initial.id)).toEqual(initial);
    await page.reload(); await open(page, service, initial, sceneId, false);
    await expect(page.getByLabel("Supporting copy", { exact: true })).toHaveValue("A conflicting replacement explanation.");
    await expect(review.getByRole("alert")).toContainText("override conflict");
    await expect(button(page, "Update component")).toBeDisabled();
    expect(commandCount(service, "apply_change")).toBe(applies);
    expect(projectEditGrantCount(service)).toBe(grants);
    const rejected: Change = { type: "upsert_product_hero", scene_id: sceneId, instance_id: instanceId,
      config: { ...hero.config, body: "A conflicting replacement explanation." } };
    await expect(service.apply(initial, rejected)).rejects.toThrow(/override conflict/);
    final = await service.project(initial.id); expect(final).toEqual(initial);
    expect(await service.history(initial.id, initial.revision)).toEqual([]);
    await page.screenshot({ path: info.outputPath("component-conflict-retained.png"), fullPage: true });
    await service.close(); await service.start();
    expect(await service.project(initial.id)).toEqual(initial);
    expect(await service.history(initial.id, initial.revision)).toEqual([]);
    observations.rejectedChange = rejected;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, primary); }
});
