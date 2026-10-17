import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect, test, type BrowserContext, type Locator, type Page, type TestInfo } from "@playwright/test";
import type { Project } from "../src/types";
import { ScopedChangeService } from "./support/scopedChangeService";

// Real persisted-service coverage with explicitly synthetic source content and
// author decisions. An approval entered by this test is a fixture record, not
// human acceptance. No model, Film, renderer or installed desktop is exercised.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Build studio_review_session and set MOTIONWRIGHT_SERVICE_TEST_BIN; no simulated service is substituted.");

const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const field = (page: Page, name: string) => page.getByLabel(name, { exact: true });
// Use the accessible textbox name for Plan's wrapping labels: seeded textarea
// content must not become part of an exact label-text selector.
const planField = (panel: Locator, name: string | RegExp) => panel.getByRole("textbox", { name, exact: true });

async function openBrief(page: Page) {
  await button(page, "Brief").click();
  await expect(page.getByRole("heading", { name: "Project brief", exact: true })).toBeVisible();
}

async function openPlan(page: Page) {
  await button(page, "Production").click();
  await button(page, "Production plan").click();
  await expect(page.getByRole("heading", { name: "Production plan", exact: true })).toBeVisible();
  return page.locator(".production-plan");
}

async function revision(page: Page, expected: number) {
  await expect(page.locator(".revision-chip").first()).toHaveText(`r${expected}`);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.locator(".error-banner")).toHaveCount(0);
  await expect(page.locator(".document-view .production-error[role='alert']")).toHaveCount(0);
}

function protectedWork(project: Project) {
  return {
    id: project.id, generation: project.generation, activeBranch: project.active_branch,
    scenes: project.scenes, markers: project.markers, assets: project.assets,
    locks: project.locks, deliverables: project.deliverables, claims: project.brief.claims,
    narrative: project.narrative, audio: project.audio, visualLanguage: project.visual_language,
    productionDesign: project.production_design, reviews: project.reviews,
    proposals: project.proposal_sets, modelInvocations: project.model_invocations,
  };
}

async function sourceBytes(service: ScopedChangeService, project: Project) {
  const result = [];
  for (const asset of project.assets) {
    if (!asset.content_sha256) continue;
    const digest = asset.content_sha256;
    expect(digest).toMatch(/^[0-9a-f]{64}$/);
    // Store::open places actual content-addressed blobs next to the SQLite DB.
    const bytes = await readFile(join(service.directory, "blobs", "sha256", digest.slice(0, 2), digest));
    const actual = createHash("sha256").update(bytes).digest("hex");
    expect(actual).toBe(digest);
    result.push({ assetId: asset.id, sha256: actual, sizeBytes: bytes.length });
  }
  expect(result.length).toBeGreaterThan(0);
  return result.sort((a, b) => a.assetId.localeCompare(b.assetId));
}

async function evidence(testInfo: TestInfo, services: ScopedChangeService[], snapshots: Record<string, unknown>) {
  const path = testInfo.outputPath("brief-preservation-evidence.json");
  await writeFile(path, JSON.stringify({
    fixture: "synthetic_test_fixture",
    evidenceScope: "real_service_sqlite_cas_and_ui_consumption; excludes_model_dispatch_film_native_render_tauri_and_human_acceptance",
    ...snapshots,
    services: services.map(service => ({ databasePath: service.databasePath, binaryPath: service.binaryPath,
      stderr: service.stderr, transcript: service.transcript })),
  }, null, 2));
  await testInfo.attach("brief-preservation-evidence", { path, contentType: "application/json" });
}

test("six real revisions preserve an authored plan, pending text, source bytes and full service reopen", async ({ page, browser }, testInfo) => {
  test.setTimeout(60_000);
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3") });
  let peer: ScopedChangeService | undefined;
  let freshContext: BrowserContext | undefined;
  const snapshots: Record<string, unknown> = {};
  try {
    const seed = (await service.bootstrap()).project;
    const claimText = "Synthetic source note for an author-controlled shot plan.";
    const withClaim = await service.apply(seed, { type: "add_claim", text: claimText,
      source: { kind: "manual_note", label: "Synthetic preservation test source" },
      context: "Test fixture only; no observed product behavior is asserted.", source_revision: null,
    });
    await service.attachPage(page);
    await page.goto("/");
    const plan = await openPlan(page);
    await planField(plan, "Objective").fill("Retain the author's saved production direction.");
    await planField(plan, "Audience").fill("The audience recorded in the saved plan.");
    await planField(plan, "Creative concept").fill("An independently authored concept with deliberate shot purposes.");
    await planField(plan, /^Reference-derived constraints(?:\s|$)/).fill("Preserve the chosen source classification.");
    await planField(plan, /^Exclusions(?:\s|$)/).fill("Do not replace this plan when its intake brief changes.");
    await plan.locator(".production-shot").first().getByLabel(claimText, { exact: true }).check();
    await button(page, "Save production plan").click();
    await revision(page, withClaim.revision + 1);
    await planField(plan, "Reviewer").fill("Automated authoring fixture");
    await planField(plan, "Approval note").fill("Synthetic approval record for preservation testing; not human or renderer acceptance.");
    await button(page, "Approve this concept digest").click();
    await revision(page, withClaim.revision + 2);
    const baseline = await service.project(seed.id);
    expect(baseline.production_design?.plan?.approval?.reviewer).toBe("Automated authoring fixture");
    expect(baseline.production_design?.plan?.approval?.content_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(baseline.production_design?.plan?.shots[0].claim_ids).toEqual([withClaim.brief.claims.at(-1)!.id]);
    const authoredPlan = structuredClone(baseline.production_design!.plan!);
    const blobsBefore = await sourceBytes(service, baseline);
    snapshots.baseline = baseline;
    snapshots.sourceBytesBefore = blobsBefore;

    await openBrief(page);
    const pendingObjective = "  An unfinished brief objective kept through later revisions.\nIts second line remains authored.  ";
    await field(page, "Production objective").fill(pendingObjective);
    await field(page, "Project title").fill("Preserved project title one");
    await button(page, "Save title").click();
    await revision(page, baseline.revision + 1);
    await expect(field(page, "Production objective")).toHaveValue(pendingObjective);
    const first = await service.project(seed.id);
    expect(first.brief).toEqual(baseline.brief);
    const savedPlan = await openPlan(page);
    await expect(planField(savedPlan, "Objective")).toHaveValue(authoredPlan.objective);
    await expect(planField(savedPlan, "Creative concept")).toHaveValue(authoredPlan.concept);
    expect(await service.project(seed.id)).toEqual(first);
    await openBrief(page);
    await expect(field(page, "Production objective")).toHaveValue(pendingObjective);

    await field(page, "Project title").fill("Preserved project title two");
    await button(page, "Save title").click();
    await revision(page, baseline.revision + 2);
    const second = await service.project(seed.id);
    await service.close();
    await service.start();
    await page.reload();
    await openBrief(page);
    expect(await service.project(seed.id)).toEqual(second);
    await expect(field(page, "Production objective")).toHaveValue(pendingObjective);

    // An actual independent author changes a scene, not the brief or plan.
    peer = await service.peer();
    const otherScene = baseline.scenes[1];
    const otherObjective = "An independent scene decision committed by the second process.";
    const concurrent = await peer.apply(await peer.project(seed.id), {
      type: "update_scene_objective", scene_id: otherScene.id, objective: otherObjective,
    });
    expect(concurrent.revision).toBe(baseline.revision + 3);
    await page.reload();
    await openBrief(page);
    await expect(field(page, "Production objective")).toHaveValue(pendingObjective);
    await expect(button(page, "Keep saved Production objective")).toHaveCount(0);
    await field(page, "Project title").fill("Preserved project title three");
    await button(page, "Save title").click();
    await revision(page, baseline.revision + 4);

    const pendingTitle = "  A later title remains outside all six revisions  ";
    await field(page, "Project title").fill(pendingTitle);
    await field(page, "Audience").fill("The revised brief audience, distinct from the saved plan.");
    await field(page, "Constraints").fill("  Keep scene timing and source bytes  \n\n  Keep the plan's shot decisions  ");
    await button(page, "Commit brief").click();
    await revision(page, baseline.revision + 5);
    await expect(field(page, "Project title")).toHaveValue(pendingTitle);
    await field(page, "Exclusions").fill("  Unrequested reinterpretation of the approved concept  \n\n");
    await button(page, "Commit brief").click();
    await revision(page, baseline.revision + 6);
    const final = await service.project(seed.id);
    expect(final.title).toBe("Preserved project title three");
    expect(final.brief).toEqual({ ...baseline.brief, objective: pendingObjective,
      audience: "The revised brief audience, distinct from the saved plan.",
      constraints: ["Keep scene timing and source bytes", "Keep the plan's shot decisions"],
      exclusions: ["Unrequested reinterpretation of the approved concept"],
    });
    expect(final.production_design?.plan).toEqual(authoredPlan);
    const expectedWork = structuredClone(baseline);
    expectedWork.scenes.find(scene => scene.id === otherScene.id)!.objective = otherObjective;
    expect(protectedWork(final)).toEqual(protectedWork(expectedWork));
    const journal = await service.history(seed.id, baseline.revision);
    expect(journal.map(event => [event.revision, event.change.type])).toEqual([
      [baseline.revision + 1, "rename_project"], [baseline.revision + 2, "rename_project"],
      [baseline.revision + 3, "update_scene_objective"], [baseline.revision + 4, "rename_project"],
      [baseline.revision + 5, "set_brief"], [baseline.revision + 6, "set_brief"],
    ]);
    snapshots.final = final;
    snapshots.journal = journal;
    await page.screenshot({ path: testInfo.outputPath("six-revisions-keep-pending-title.png"), fullPage: true });

    await peer.close();
    await service.close();
    await service.start();
    expect(await service.project(seed.id)).toEqual(final);
    expect(await service.history(seed.id, baseline.revision)).toEqual(journal);
    snapshots.sourceBytesAfter = await sourceBytes(service, final);
    expect(snapshots.sourceBytesAfter).toEqual(blobsBefore);
    await page.reload();
    await openBrief(page);
    await expect(field(page, "Project title")).toHaveValue(pendingTitle);
    const reopenedPlan = await openPlan(page);
    await expect(planField(reopenedPlan, "Objective")).toHaveValue(authoredPlan.objective);
    await expect(planField(reopenedPlan, "Audience")).toHaveValue(authoredPlan.audience);
    await expect(planField(reopenedPlan, "Creative concept")).toHaveValue(authoredPlan.concept);
    await expect(reopenedPlan).toContainText("Automated authoring fixture");
    await page.screenshot({ path: testInfo.outputPath("saved-plan-and-approval-survive-brief-revisions.png"), fullPage: true });

    // A fresh browser context has no recovery copy. It sees only canonical
    // SQLite state; the pending title must not masquerade as a saved project.
    freshContext = await browser.newContext();
    const freshPage = await freshContext.newPage();
    await service.attachPage(freshPage);
    await freshPage.goto(page.url());
    await openBrief(freshPage);
    await expect(field(freshPage, "Project title")).toHaveValue(final.title);
    await expect(field(freshPage, "Production objective")).toHaveValue(final.brief.objective);
    await expect(button(freshPage, "Save title")).toBeDisabled();
    expect(await service.project(seed.id)).toEqual(final);
    expect(await service.history(seed.id, baseline.revision)).toEqual(journal);
  } finally {
    if (freshContext) await freshContext.close();
    if (peer) await peer.close();
    await service.close();
    await evidence(testInfo, peer ? [service, peer] : [service], snapshots);
  }
});

test("a newly saved brief seeds the real initial plan without overwriting a later authored plan", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3") });
  const snapshots: Record<string, unknown> = {};
  try {
    const initial = (await service.bootstrap()).project;
    expect(initial.production_design?.plan).toBeNull();
    const baseline = await service.apply(initial, { type: "set_narrative_premise",
      premise: "An existing narrative premise that the author can develop into a plan.",
    });
    snapshots.baseline = baseline;
    await service.attachPage(page);
    await page.goto("/");
    await openBrief(page);
    await field(page, "Production objective").fill("The newly committed brief objective.");
    await field(page, "Audience").fill("The newly committed brief audience.");
    await field(page, "Constraints").fill("  Keep the original scene structure  ");
    await field(page, "Exclusions").fill("  Invented source evidence  ");
    await button(page, "Commit brief").click();
    await revision(page, baseline.revision + 1);
    const briefSaved = await service.project(initial.id);
    expect(briefSaved.production_design?.plan).toBeNull();
    expect(briefSaved.brief.constraints).toEqual(["Keep the original scene structure"]);
    expect(briefSaved.brief.exclusions).toEqual(["Invented source evidence"]);

    const plan = await openPlan(page);
    await expect(planField(plan, "Objective")).toHaveValue(briefSaved.brief.objective);
    await expect(planField(plan, "Audience")).toHaveValue(briefSaved.brief.audience);
    await expect(planField(plan, "Creative concept")).toHaveValue(baseline.narrative.premise);
    expect(await service.project(initial.id)).toEqual(briefSaved);
    expect(await service.history(initial.id, baseline.revision)).toHaveLength(1);
    await page.screenshot({ path: testInfo.outputPath("new-brief-feeds-initial-plan-without-saving-it.png"), fullPage: true });

    await planField(plan, "Creative concept").fill("The author narrows the concept before committing this plan.");
    await button(page, "Save production plan").click();
    await revision(page, baseline.revision + 2);
    const planned = await service.project(initial.id);
    const savedPlan = structuredClone(planned.production_design!.plan!);
    expect(savedPlan.objective).toBe(briefSaved.brief.objective);
    expect(savedPlan.audience).toBe(briefSaved.brief.audience);
    expect(savedPlan.concept).toBe("The author narrows the concept before committing this plan.");
    expect(savedPlan.shots.map(shot => shot.scene_id)).toEqual(baseline.scenes.map(scene => scene.id));
    expect(savedPlan.approval).toBeNull();
    snapshots.initialPlan = planned;

    await openBrief(page);
    const nextObjective = "A later brief decision that must not rewrite the authored plan.";
    await field(page, "Production objective").fill(nextObjective);
    await openPlan(page);
    await expect(planField(page.locator(".production-plan"), "Objective")).toHaveValue(savedPlan.objective);
    expect(await service.project(initial.id)).toEqual(planned);
    await openBrief(page);
    await expect(field(page, "Production objective")).toHaveValue(nextObjective);
    await button(page, "Commit brief").click();
    await revision(page, baseline.revision + 3);
    const final = await service.project(initial.id);
    expect(final.brief).toEqual({ ...briefSaved.brief, objective: nextObjective });
    expect(final.production_design?.plan).toEqual(savedPlan);
    expect(final.scenes).toEqual(baseline.scenes);
    expect(final.assets).toEqual(baseline.assets);
    expect(final.narrative).toEqual(baseline.narrative);
    expect(final.model_invocations).toEqual(baseline.model_invocations);
    expect((await service.history(initial.id, baseline.revision)).map(event => event.change.type)).toEqual([
      "set_brief", "set_production_plan", "set_brief",
    ]);
    snapshots.final = final;
    await service.close();
    await service.start();
    expect(await service.project(initial.id)).toEqual(final);
    await page.reload();
    const reopened = await openPlan(page);
    await expect(planField(reopened, "Objective")).toHaveValue(savedPlan.objective);
    await expect(planField(reopened, "Creative concept")).toHaveValue(savedPlan.concept);
    expect(await service.project(initial.id)).toEqual(final);
  } finally {
    await service.close();
    await evidence(testInfo, [service], snapshots);
  }
});
