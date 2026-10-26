/** App + real StudioService/SQLite oracles. This file has no browser Graph adapter.
 * Missing DTO/consumer admission fails the selected test, never becomes a mock PASS. */
import { writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";
import type { Project } from "../src/types";
import type { GraphProject } from "../src/semanticGraph";
import type { DeliveryRecipe } from "../src/deliveryRecipe";

test.setTimeout(120_000);
test.beforeAll(() => {
  if (!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN) throw new Error("Semantic Graph service oracles require the real executable built from this exact composed Rust source; no skipped or synthetic substitute is accepted.");
});
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const field = (page: Page, name: string) => page.getByLabel(name, { exact: true });
const graphRequests = (service: ScopedChangeService) => service.transcript.filter(entry => entry.direction === "request" && "command" in entry.payload
  && entry.payload.command === "apply_change" && ["upsert_semantic_graph", "remove_semantic_graph"].includes(String((entry.payload.args?.request as { change?: { type?: string } } | undefined)?.change?.type)));
async function arrange(service: ScopedChangeService) {
  let project = (await service.fixtureBootstrap()).project;
  const before = new Set(project.scenes.map(scene => scene.id));
  project = await service.apply(project, { type: "add_scene", name: "Synthetic semantic Graph service fixture", objective: "Editable typed explanation with explicitly synthetic content", duration_seconds: 5 });
  const scene = project.scenes.find(item => !before.has(item.id)); if (!scene) throw new Error("The actual add_scene operation did not retain a new identity.");
  project = await service.apply(project, { type: "set_scene_renderer", scene_id: scene.id, renderer: "manim-community" });
  const profile = project.deliverables[0]; if (!profile) throw new Error("The actual Project has no Deliverable profile.");
  const recipe: DeliveryRecipe = {
    schema: "motionwright.delivery-recipe/1", scene_intents: [], original_mix: null,
  };
  project = await service.apply(project, { type: "set_delivery_recipe", deliverable_profile_id: profile.id, recipe });
  return { project, sceneId: scene.id, profileId: profile.id };
}
async function open(page: Page, sceneId: string) {
  await button(page, "Production").click(); await field(page, "Production working scene").selectOption(sceneId); await button(page, "Graph").click();
  await expect(page.getByRole("heading", { name: "Semantic Graph", exact: true })).toBeVisible();
}
async function prepare(page: Page, kind: "causal_diagram" | "system_flow") {
  await field(page, "Graph source").selectOption("new:" + kind);
  await field(page, "Graph author label").fill("Real-service synthetic fixture author");
  await field(page, "Graph rationale").fill("Editable source and persistence regression; this is not customer or human craft evidence.");
  await button(page, "Inspect current Graph support and sources").click();
  await expect(button(page, "Save semantic Graph"), "The actual service DTO and matching native consumer guards must admit this source; a synthetic context is not injected.").toBeEnabled();
}

test("Graph creates, edits, reopens SQLite, persists an actual review and rechecks it after editing", async ({ page }, info) => {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("store", "semantic-graph.sqlite3"), desktopInvokeFixture: true }); let latest: Project | undefined;
  try {
    const setup = await arrange(service), initial = setup.project; await service.attachFixturePage(page, initial.id); await page.goto("/"); await open(page, setup.sceneId);
    await prepare(page, "causal_diagram"); await field(page, "Vertex 1 label").fill("Declared intervention"); await field(page, "Vertex 1 box x").fill("1e");
    await expect(button(page, "Save semantic Graph")).toBeDisabled(); await page.reload(); await open(page, setup.sceneId); await expect(field(page, "Vertex 1 box x")).toHaveValue("1e");
    expect(graphRequests(service)).toHaveLength(0); await field(page, "Vertex 1 box x").fill("120"); await button(page, "Inspect current Graph support and sources").click();
    await button(page, "Save semantic Graph").click(); await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + (initial.revision + 1));
    latest = await service.project(initial.id); const first = (latest as GraphProject).production_design!.semantic_graphs!.find(graph => graph.scene_id === setup.sceneId)!;
    expect(first).toBeTruthy(); expect(first.content.kind).toBe("causal_diagram"); expect(first.content.vertices[0].label).toBe("Declared intervention"); expect(first.content.vertices[0].box.x).toBe(120);
    expect(first.style.font_family).toBe("Instrument Sans Variable"); expect(first.time_range.duration).toEqual({ num: "5", den: "1" }); expect(first.authorship.source_snapshot.revision).toBe(initial.revision);
    await button(page, "Open review of saved Graph").click(); await field(page, "Review comment").fill("Technical source review: check the declared causal basis and retained geometry.");
    await button(page, "Add anchored review").click(); latest = await service.project(initial.id);
    await expect.poll(async () => (await service.project(initial.id)).reviews.some(review => review.anchor.resource === "graph:" + first.id)).toBe(true);
    latest = await service.project(initial.id); const review = latest.reviews.find(item => item.anchor.resource === "graph:" + first.id)!;
    expect(review.anchor.profile_id).toBe(setup.profileId); expect(review.anchor.revision).toBe(initial.revision + 1);
    const resolution = field(page, "Resolution for review " + review.id); await resolution.fill("Technical source inspection only; native fidelity and human acceptance remain separate.");
    await resolution.locator("..").getByRole("button", { name: "Resolve", exact: true }).click(); await expect.poll(async () => (await service.project(initial.id)).reviews.find(item => item.id === review.id)?.status).toBe("resolved");
    await open(page, setup.sceneId); await field(page, "Vertex 1 label").fill("Revised declared intervention"); await button(page, "Inspect current Graph support and sources").click();
    await button(page, "Save Graph changes").click(); await expect.poll(async () => (await service.project(initial.id) as GraphProject).production_design!.semantic_graphs!.find(graph => graph.id === first.id)?.content.vertices[0].label).toBe("Revised declared intervention");
    latest = await service.project(initial.id); const final = (latest as GraphProject).production_design!.semantic_graphs!.find(graph => graph.id === first.id)!;
    expect(final.content.vertices.map(v => v.id)).toEqual(first.content.vertices.map(v => v.id)); expect(final.content.edges.map(e => e.id)).toEqual(first.content.edges.map(e => e.id));
    expect(latest.reviews.find(item => item.id === review.id)?.status).toBe("needs_recheck"); expect(graphRequests(service)).toHaveLength(2);
    const history = await service.history(initial.id, initial.revision); expect(history.map(event => event.change.type)).toEqual(["upsert_semantic_graph", "add_review", "resolve_review", "upsert_semantic_graph"]);
    await service.close(); await service.start(); await page.reload(); await open(page, setup.sceneId); await expect(field(page, "Vertex 1 label")).toHaveValue("Revised declared intervention");
    expect(await service.project(initial.id)).toEqual(latest); expect(await service.history(initial.id, initial.revision)).toEqual(history); expect(graphRequests(service)).toHaveLength(2);
    const path = info.outputPath("graph-real-source-review-reopen.json"); await writeFile(path, JSON.stringify({ scope: "Actual App + StudioService + SQLite only", initialRevision: initial.revision,
      finalRevision: latest.revision, graph: final, review: latest.reviews.find(item => item.id === review.id), events: history.map(event => event.change.type),
      excludes: ["native rendered output", "provider parity", "installed app", "human craft approval"] }, null, 2)); await info.attach("graph-real-source-review-reopen", { path, contentType: "application/json" });
  } finally { await service.finalizeClosedStore(info.outputPath("closed-graph-store"), latest, [], [() => page.close()]); }
});

test("Synthetic flow stores exact event times and rejects a real stale CAS without a new event", async ({ page }, info) => {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("store", "semantic-flow.sqlite3"), desktopInvokeFixture: true }); let latest: Project | undefined;
  try {
    const setup = await arrange(service), initial = setup.project; await service.attachFixturePage(page, initial.id); await page.goto("/"); await open(page, setup.sceneId); await prepare(page, "system_flow");
    await field(page, "Graph motion").selectOption("off"); await button(page, "Add synthetic event").click(); await page.locator("[data-graph-event] summary").first().click(); await field(page, "Event 1 label").fill("Illustrated message transfer");
    await button(page, "Save semantic Graph").click(); await expect.poll(async () => (await service.project(initial.id)).revision).toBe(initial.revision + 1); latest = await service.project(initial.id);
    const graph = (latest as GraphProject).production_design!.semantic_graphs!.find(item => item.scene_id === setup.sceneId)!; expect(graph.motion).toBe("off");
    if (graph.content.kind !== "system_flow") throw new Error("Actual saved source is not a typed system flow.");
    expect(graph.content.source.kind).toBe("synthetic"); expect(graph.content.events[0]).toMatchObject({ label: "Illustrated message transfer", edge_id: graph.content.edges[0].id, at: { num: "1", den: "15" }, duration: { num: "1", den: "30" } });
    const beforePeer = structuredClone(latest), requestId = randomUUID(), grant = await service.grant("project_edit", beforePeer, requestId);
    latest = await service.apply(beforePeer, { type: "rename_project", title: "Concurrent real-service writer" }); const before = structuredClone(latest), journal = await service.history(initial.id, initial.revision);
    const intended = structuredClone(graph); intended.authorship.source_snapshot = { project_id: beforePeer.id, generation: beforePeer.generation, branch_id: beforePeer.active_branch, revision: beforePeer.revision };
    intended.content.vertices[0].label = "Stale Graph writer";
    await expect(service.invoke("apply_change", { request: { project_id: beforePeer.id, generation: beforePeer.generation, revision: beforePeer.revision, request_id: requestId, effect_grant: grant.token,
      change: { type: "upsert_semantic_graph", graph: intended } } })).rejects.toThrow();
    expect(await service.project(initial.id)).toEqual(before); expect(await service.history(initial.id, initial.revision)).toEqual(journal);
    await service.close(); await service.start(); expect(await service.project(initial.id)).toEqual(before); latest = before;
  } finally { await service.finalizeClosedStore(info.outputPath("closed-flow-store"), latest, [], [() => page.close()]); }
});

test("A lost Graph reply cannot replay a committed SQLite mutation after reload", async ({ page }, info) => {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("store", "semantic-graph-lost-reply.sqlite3"), desktopInvokeFixture: true }); let latest: Project | undefined;
  const original = service.invoke.bind(service); let lost = false;
  try {
    const setup = await arrange(service), initial = setup.project; await service.attachFixturePage(page, initial.id); await page.goto("/"); await open(page, setup.sceneId); await prepare(page, "causal_diagram");
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const result = await original<T>(command, args), change = (args.request as { change?: { type?: string } } | undefined)?.change;
      if (!lost && command === "apply_change" && change?.type === "upsert_semantic_graph") { lost = true; throw new Error("Transport lost the original reply after actual SQLite Graph commit."); } return result;
    };
    await field(page, "Vertex 1 label").fill("Committed once"); await button(page, "Save semantic Graph").click(); await expect(page.getByRole("region", { name: "Unconfirmed Graph submission" })).toBeVisible();
    latest = await service.project(initial.id); expect(latest.revision).toBe(initial.revision + 1); expect(graphRequests(service)).toHaveLength(1);
    await field(page, "Vertex 1 label").fill("New human intention after lost reply"); await page.reload(); await open(page, setup.sceneId);
    await expect(field(page, "Vertex 1 label")).toHaveValue("New human intention after lost reply"); await expect(button(page, "Save Graph changes")).toBeDisabled(); expect(graphRequests(service)).toHaveLength(1);
    await button(page, "Inspect saved Graph outcome").click(); await button(page, "Continue Graph as a new reviewed intention").click(); expect(graphRequests(service)).toHaveLength(1);
    await button(page, "Inspect current Graph support and sources").click(); await button(page, "Save Graph changes").click();
    await expect.poll(async () => (await service.project(initial.id)).revision).toBe(initial.revision + 2); latest = await service.project(initial.id);
    expect((latest as GraphProject).production_design!.semantic_graphs!.find(graph => graph.scene_id === setup.sceneId)!.content.vertices[0].label).toBe("New human intention after lost reply");
    const journal = await service.history(initial.id, initial.revision); expect(journal.map(event => event.change.type)).toEqual(["upsert_semantic_graph", "upsert_semantic_graph"]); expect(graphRequests(service)).toHaveLength(2);
    await service.close(); await service.start(); expect(await service.project(initial.id)).toEqual(latest); expect(await service.history(initial.id, initial.revision)).toEqual(journal);
  } finally { await service.finalizeClosedStore(info.outputPath("closed-graph-lost-reply-store"), latest, [], [() => page.close()]); }
});

for (const kind of ["causal_diagram", "system_flow"] as const) {
  test(`Graph ${kind} follows the edge midpoint through real saves and restores manual placement after SQLite restart`, async ({ page }, info) => {
    const service = await ScopedChangeService.create({
      databasePath: info.outputPath("store", "semantic-graph-label-" + kind + ".sqlite3"), desktopInvokeFixture: true,
    });
    let latest: Project | undefined, initial: Project | undefined;
    let bodyFailed = false, primaryFailure: unknown;
    const observations: Record<string, unknown>[] = [];
    const errors = (reason: unknown) => reason instanceof Error
      ? { name: reason.name, message: reason.message, stack: reason.stack ?? null }
      : { name: "ThrownValue", message: String(reason), stack: null };
    try {
      const setup = await arrange(service); initial = setup.project; latest = initial;
      const initialOracle = await service.observeStoredProject(initial);
      const profile = initial.deliverables.find(item => item.id === setup.profileId);
      expect(profile).toMatchObject({ width: 1920, height: 1080 });
      const initialGraphs = (initial as GraphProject).production_design?.semantic_graphs ?? [];
      const unrelatedDesign = (project: Project) => Object.fromEntries(Object.entries(project.production_design ?? {})
        .filter(([key]) => key !== "semantic_graphs"));
      const assertSourcesPreserved = (project: Project, graphId: string) => {
        expect([project.id, project.generation, project.active_branch]).toEqual([initial!.id, initial!.generation, initial!.active_branch]);
        expect(project.scenes).toEqual(initial!.scenes);
        expect(project.assets).toEqual(initial!.assets);
        expect(project.deliverables).toEqual(initial!.deliverables);
        expect(project.brief).toEqual(initial!.brief);
        expect(project.narrative).toEqual(initial!.narrative);
        expect(project.audio).toEqual(initial!.audio);
        expect(project.visual_language).toEqual(initial!.visual_language);
        expect(project.locks).toEqual(initial!.locks);
        expect(unrelatedDesign(project)).toEqual(unrelatedDesign(initial!));
        expect(((project as GraphProject).production_design?.semantic_graphs ?? []).filter(graph => graph.id !== graphId)).toEqual(initialGraphs);
      };
      await service.attachFixturePage(page, initial.id); await page.goto("/"); await open(page, setup.sceneId); await prepare(page, kind);
      await expect(field(page, "Graph Deliverable")).toHaveValue(setup.profileId);
      await field(page, "Graph motion").selectOption("off");
      await field(page, "Graph font_size").fill("20");
      const vertices = page.locator("[data-graph-vertex]");
      const edgeDetails = page.locator("[data-graph-edge]").first();
      await vertices.nth(1).locator("summary").click();
      await edgeDetails.locator("summary").click();
      for (const [name, value] of [
        ["Vertex 1 label", "Source"], ["Vertex 1 box x", "120"], ["Vertex 1 box y", "180"],
        ["Vertex 1 box width", "160"], ["Vertex 1 box height", "80"],
        ["Vertex 2 label", "Target"], ["Vertex 2 box x", "1000"], ["Vertex 2 box y", "500"],
        ["Vertex 2 box width", "160"], ["Vertex 2 box height", "80"],
        ["Edge 1 label", "Route"], ["Edge 1 label box x", "700"], ["Edge 1 label box y", "700"],
        ["Edge 1 label box width", "240"], ["Edge 1 label box height", "72"],
      ] as const) await field(page, name).fill(value);
      await field(page, "Edge 1 from port").selectOption("right");
      await field(page, "Edge 1 to port").selectOption("left");
      await button(page, "Add edge 1 waypoint").click();
      await field(page, "Edge 1 waypoint 1 x").fill("600"); await field(page, "Edge 1 waypoint 1 y").fill("220");
      await button(page, "Add edge 1 waypoint").click();
      await field(page, "Edge 1 waypoint 2 x").fill("600"); await field(page, "Edge 1 waypoint 2 y").fill("540");
      if (kind === "system_flow") {
        await button(page, "Add synthetic event").click(); await page.locator("[data-graph-event] summary").first().click();
        await field(page, "Event 1 label").fill("Retained synthetic transfer");
      }
      await expect(field(page, "Edge 1 label placement")).toHaveValue("manual");
      await field(page, "Edge 1 label placement").selectOption("polyline_midpoint");
      await field(page, "Edge 1 label offset Y").fill("-16");
      await field(page, "Edge 1 label offset X").fill("1e");
      await expect(button(page, "Save semantic Graph")).toBeDisabled();
      const retained = await page.evaluate(() => Object.keys(localStorage)
        .filter(key => key.startsWith("motionwright.semantic-graph-recovery.v1:"))
        .flatMap(key => {
          const raw = localStorage.getItem(key); if (raw === null) return [];
          const value = JSON.parse(raw);
          return value.draft?.input?.content?.edges?.[0]?.label_placement?.offset?.x === "1e" ? [{ key, raw }] : [];
        }));
      expect(retained).toHaveLength(1);
      expect(graphRequests(service)).toHaveLength(0);
      expect(await service.project(initial.id)).toEqual(initial);
      await page.reload(); await open(page, setup.sceneId);
      // A new, unsaved flow is reopened by its explicit source kind; this
      // selection reads its existing recovery and does not create a saved Graph.
      await field(page, "Graph source").selectOption("new:" + kind);
      await edgeDetails.locator("summary").click();
      await expect(field(page, "Edge 1 label placement")).toHaveValue("polyline_midpoint");
      await expect(field(page, "Edge 1 label offset X")).toHaveValue("1e");
      await expect(field(page, "Edge 1 label offset Y")).toHaveValue("-16");
      await expect(field(page, "Edge 1 waypoint 1 x")).toHaveValue("600");
      await expect(field(page, "Edge 1 waypoint 2 y")).toHaveValue("540");
      await expect(button(page, "Save semantic Graph")).toBeDisabled();
      expect(await page.evaluate(key => localStorage.getItem(key), retained[0].key)).toBe(retained[0].raw);
      const invalidOracle = await service.observeStoredProject(initial);
      expect(invalidOracle.project).toEqual(initialOracle.project);
      expect(invalidOracle.journal).toEqual(initialOracle.journal);
      expect(graphRequests(service)).toHaveLength(0);
      observations.push({ stage: "incomplete_offset_reloaded_without_mutation", recovery: retained[0],
        project: invalidOracle.project, journal: invalidOracle.journal });

      await field(page, "Edge 1 label offset X").fill("24");
      await button(page, "Inspect current Graph support and sources").click();
      await expect(button(page, "Save semantic Graph")).toBeEnabled(); await button(page, "Save semantic Graph").click();
      await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + (initial.revision + 1));
      latest = await service.project(initial.id); const saved = latest;
      const first = (saved as GraphProject).production_design!.semantic_graphs!.find(graph => graph.scene_id === setup.sceneId)!;
      expect(first).toBeTruthy(); expect(first.content.kind).toBe(kind); expect(first.content.edges).toHaveLength(1);
      expect(first.variant_profile_ref).toBe(setup.profileId); expect(first.reference_frame).toEqual({ width: 1920, height: 1080 });
      expect(first.authorship.source_snapshot).toEqual({ project_id: initial.id, generation: initial.generation,
        branch_id: initial.active_branch, revision: initial.revision });
      expect(first.content.vertices.map(vertex => vertex.box)).toEqual([
        { x: 120, y: 180, width: 160, height: 80 }, { x: 1000, y: 500, width: 160, height: 80 },
      ]);
      const edge = first.content.edges[0], manualBox = { x: 700, y: 700, width: 240, height: 72 };
      const waypoints = [{ x: 600, y: 220 }, { x: 600, y: 540 }];
      expect(edge.label_box).toEqual(manualBox); expect(edge.waypoints).toEqual(waypoints);
      expect(edge.label_placement).toEqual({ kind: "polyline_midpoint", offset: { x: 24, y: -16 } });
      expect(first.content.vertices.map(vertex => vertex.target)).toEqual([null, null]);
      if (first.content.kind === "system_flow") {
        expect(first.content.events).toHaveLength(1);
        expect(first.content.events[0]).toMatchObject({ edge_id: edge.id, label: "Retained synthetic transfer",
          at: { num: "1", den: "15" }, duration: { num: "1", den: "30" } });
      }
      assertSourcesPreserved(saved, first.id);
      // These are independent authored coordinates, not calls to the resolver
      // under test: lengths 320+320+400 give midpoint (600,420).
      const preview = page.getByRole("img", { name: "Editorial semantic Graph vector study", exact: true });
      const edgeText = preview.locator(`[data-graph-label="${edge.id}-label"] [data-graph-label-line="0"]`);
      const edgeLine = edgeText.locator("xpath=../..").locator(":scope > polyline");
      const assertFlowInterval = async (stage: string, expectedProject: Project, x: number, singleLineY: number) => {
        if (kind !== "system_flow") return;
        // These authored fixtures use 30 fps: the retained event occupies frame
        // two through the exclusive boundary at frame three. Only actual App
        // transport controls move the playhead; no Graph adapter supplies time.
        expect(Number(profile!.frame_rate.num) / Number(profile!.frame_rate.den)).toBe(30);
        const beforeSeek = await service.observeStoredProject(expectedProject);
        const mutationCount = graphRequests(service).length;
        await field(page, "Editor preview timebase").selectOption(setup.profileId);
        await field(page, "Production working scene").selectOption(setup.sceneId);
        const sceneStart = expectedProject.scenes.find(scene => scene.id === setup.sceneId)!.start;
        const startNumerator = BigInt(sceneStart.num) * 30n, startDenominator = BigInt(sceneStart.den);
        expect(startNumerator % startDenominator).toBe(0n);
        const firstFrame = Number(startNumerator / startDenominator);
        expect(Number.isSafeInteger(firstFrame)).toBe(true);
        const readout = page.locator(".timeline-transport .timecode");
        await expect(readout).toHaveAttribute("title", "30 fps NDF · display only");
        const observedClock: { relativeFrame: number; timecode: string }[] = [];
        const assertClock = async (relativeFrame: number) => {
          let observed = "";
          await expect.poll(async () => {
            observed = (await readout.innerText()).trim();
            const parts = /^(?:(\d{2,}):)?(\d{2}):(\d{2}):(\d{2})$/.exec(observed);
            if (!parts) return NaN;
            const [, hours, minutes, seconds, frames] = parts;
            if (Number(minutes) >= 60 || Number(seconds) >= 60 || Number(frames) >= 30) return NaN;
            return ((Number(hours ?? 0) * 60 + Number(minutes)) * 60 + Number(seconds)) * 30 + Number(frames);
          }).toBe(firstFrame + relativeFrame);
          observedClock.push({ relativeFrame, timecode: observed });
        };
        await assertClock(0);
        await field(page, "Graph editorial view").selectOption("playhead");
        const label = preview.locator('[data-graph-label="' + edge.id + '-label"]');
        const lines = label.locator("[data-graph-label-line]");
        await button(page, "Step forward one frame").click();
        await assertClock(1);
        await expect(lines).toHaveCount(1);
        await expect(lines.nth(0)).toHaveText("Route \u00b7 data");
        await expect(lines.nth(0)).toHaveAttribute("x", String(x));
        await expect(lines.nth(0)).toHaveAttribute("y", String(singleLineY));
        await button(page, "Step forward one frame").click();
        await assertClock(2);
        await expect(lines).toHaveCount(2);
        await expect(lines.nth(0)).toHaveText("Route \u00b7 data");
        await expect(lines.nth(1)).toHaveText("Retained synthetic transfer");
        // Fixed 20px type with a 24px baseline interval: a two-line label
        // stays centered around the same independently authored box.
        await expect(lines.nth(0)).toHaveAttribute("x", String(x));
        await expect(lines.nth(0)).toHaveAttribute("y", String(singleLineY - 12));
        await expect(lines.nth(1)).toHaveAttribute("x", String(x));
        await expect(lines.nth(1)).toHaveAttribute("y", String(singleLineY + 12));
        await expect(lines.nth(1)).toBeVisible();
        await button(page, "Step forward one frame").click();
        await assertClock(3);
        await expect(lines).toHaveCount(1);
        await expect(lines.nth(0)).toHaveText("Route \u00b7 data");
        await expect(lines.nth(0)).toHaveAttribute("x", String(x));
        await expect(lines.nth(0)).toHaveAttribute("y", String(singleLineY));
        const afterSeek = await service.observeStoredProject(expectedProject);
        expect(afterSeek.project).toEqual(beforeSeek.project);
        expect(afterSeek.journal).toEqual(beforeSeek.journal);
        expect(graphRequests(service)).toHaveLength(mutationCount);
        observations.push({ stage, eventId: first.content.kind === "system_flow" ? first.content.events[0].id : null,
          observedClock, beforeAndAfter: { x, y: singleLineY },
          activeLines: [{ x, y: singleLineY - 12 }, { x, y: singleLineY + 12 }],
          sourceUnchanged: true, graphMutations: mutationCount, editorialOnly: true });
        await field(page, "Graph editorial view").selectOption("complete");
      };
      await expect(edgeText).toBeVisible(); await expect(edgeText).toHaveAttribute("x", "624"); await expect(edgeText).toHaveAttribute("y", "411");
      await expect(edgeLine).toHaveAttribute("points", "280,220 600,220 600,540 1000,540");
      expect(graphRequests(service)).toHaveLength(1);
      observations.push({ stage: "automatic_saved", project: saved, graph: first, independentMidpoint: { x: 600, y: 420 },
        resolvedBox: { x: 504, y: 368, width: 240, height: 72 }, svgText: { x: 624, y: 411 }, editorialOnly: true });
      await assertFlowInterval("automatic_saved_active_interval", saved, 624, 411);

      await vertices.nth(1).locator("summary").click(); await field(page, "Vertex 2 box x").fill("1200");
      // Lengths 320+320+600 move the midpoint to (600,520), not the
      // arithmetic average of the four points and not the manual label box.
      await expect(edgeText).toHaveAttribute("x", "624"); await expect(edgeText).toHaveAttribute("y", "511");
      await expect(edgeLine).toHaveAttribute("points", "280,220 600,220 600,540 1200,540");
      expect(await service.project(initial.id)).toEqual(saved);
      await button(page, "Inspect current Graph support and sources").click();
      await expect(button(page, "Save Graph changes")).toBeEnabled(); await button(page, "Save Graph changes").click();
      await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + (initial.revision + 2));
      latest = await service.project(initial.id); const moved = latest;
      const movedGraph = (moved as GraphProject).production_design!.semantic_graphs!.find(graph => graph.id === first.id)!;
      const expectedMoved = structuredClone(first);
      expectedMoved.content.vertices[1].box.x = 1200;
      expectedMoved.authorship.source_snapshot = { project_id: saved.id, generation: saved.generation, branch_id: saved.active_branch, revision: saved.revision };
      expect(movedGraph).toEqual(expectedMoved);
      assertSourcesPreserved(moved, first.id);
      const movedOracle = await service.observeStoredProject(moved);
      expect(movedOracle.journal.slice(0, initialOracle.journal.length)).toEqual(initialOracle.journal);
      expect(movedOracle.journal.slice(initialOracle.journal.length).map(event => event.change.type)).toEqual(["upsert_semantic_graph", "upsert_semantic_graph"]);
      expect(graphRequests(service)).toHaveLength(2);

      // Reopen while the optional placement still exists, before testing its
      // explicit deletion. Both saved forms must survive actual SQLite restart.
      await service.close(); await service.start(); await page.reload(); await open(page, setup.sceneId);
      await edgeDetails.locator("summary").click();
      await expect(field(page, "Edge 1 label placement")).toHaveValue("polyline_midpoint");
      await expect(field(page, "Edge 1 label offset X")).toHaveValue("24");
      await expect(field(page, "Edge 1 label offset Y")).toHaveValue("-16");
      await expect(edgeText).toHaveAttribute("x", "624"); await expect(edgeText).toHaveAttribute("y", "511");
      const automaticReopened = await service.observeStoredProject(moved);
      expect(automaticReopened.project).toEqual(movedOracle.project);
      expect(automaticReopened.journal).toEqual(movedOracle.journal);
      expect(graphRequests(service)).toHaveLength(2);
      observations.push({ stage: "automatic_reopened", oracle: automaticReopened,
        independentMidpoint: { x: 600, y: 520 }, resolvedBox: { x: 504, y: 468, width: 240, height: 72 }, svgText: { x: 624, y: 511 } });
      await assertFlowInterval("automatic_reopened_active_interval", moved, 624, 511);

      await field(page, "Edge 1 label placement").selectOption("manual");
      await expect(field(page, "Edge 1 label offset X")).toHaveCount(0);
      for (const [key, value] of Object.entries(manualBox)) await expect(field(page, "Edge 1 label box " + key)).toHaveValue(String(value));
      await expect(edgeText).toHaveAttribute("x", "820"); await expect(edgeText).toHaveAttribute("y", "743");
      await expect(edgeLine).toHaveAttribute("points", "280,220 600,220 600,540 1200,540");
      expect(await service.project(initial.id)).toEqual(moved);
      await button(page, "Inspect current Graph support and sources").click();
      await expect(button(page, "Save Graph changes")).toBeEnabled(); await button(page, "Save Graph changes").click();
      await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + (initial.revision + 3));
      latest = await service.project(initial.id); const manual = latest;
      const manualGraph = (manual as GraphProject).production_design!.semantic_graphs!.find(graph => graph.id === first.id)!;
      const expectedManual = structuredClone(movedGraph);
      delete expectedManual.content.edges[0].label_placement;
      expectedManual.authorship.source_snapshot = { project_id: moved.id, generation: moved.generation, branch_id: moved.active_branch, revision: moved.revision };
      expect(manualGraph).toEqual(expectedManual);
      expect(Object.hasOwn(manualGraph.content.edges[0], "label_placement")).toBe(false);
      assertSourcesPreserved(manual, first.id);
      const manualOracle = await service.observeStoredProject(manual);
      expect(manualOracle.journal.slice(0, movedOracle.journal.length)).toEqual(movedOracle.journal);
      expect(manualOracle.journal.slice(movedOracle.journal.length).map(event => event.change.type)).toEqual(["upsert_semantic_graph"]);
      expect(graphRequests(service)).toHaveLength(3);
      await service.close(); await service.start(); await page.reload(); await open(page, setup.sceneId);
      await edgeDetails.locator("summary").click();
      await expect(field(page, "Edge 1 label placement")).toHaveValue("manual");
      await expect(field(page, "Edge 1 label offset X")).toHaveCount(0);
      await expect(edgeText).toHaveAttribute("x", "820"); await expect(edgeText).toHaveAttribute("y", "743");
      const manualReopened = await service.observeStoredProject(manual);
      expect(manualReopened.project).toEqual(manualOracle.project);
      expect(manualReopened.journal).toEqual(manualOracle.journal);
      expect(graphRequests(service)).toHaveLength(3);
      observations.push({ stage: "manual_reopened", oracle: manualReopened, initialInvalidRecoverySnapshot: retained[0],
        manualBox, waypoints, graphId: first.id, vertexIds: first.content.vertices.map(vertex => vertex.id), edgeId: edge.id,
        svgText: { x: 820, y: 743 }, graphMutations: 3, sqliteRestarts: 2 });
      await assertFlowInterval("manual_reopened_active_interval", manual, 820, 743);
    } catch (error) {
      bodyFailed = true; primaryFailure = error; throw error;
    } finally {
      const cleanupErrors: unknown[] = [];
      let finalization: Awaited<ReturnType<ScopedChangeService["finalizeClosedStore"]>> | null = null;
      const destination = info.outputPath("closed-graph-label-store");
      try { finalization = await service.finalizeClosedStore(destination, latest, [], [() => page.close()]); }
      catch (error) { cleanupErrors.push(error); }
      try {
        await info.attach("closed-store-outcomes", { path: destination + "-outcomes.json", contentType: "application/json" });
      } catch (error) { cleanupErrors.push(error); }
      try {
        const path = info.outputPath("graph-label-placement-service-evidence.json");
        await writeFile(path, JSON.stringify({
          scope: "Actual App controls + StudioService + SQLite, with editorial SVG coordinates",
          kind, testBodyCompleted: !bodyFailed, primaryFailure: bodyFailed ? errors(primaryFailure) : null,
          cleanupErrors: cleanupErrors.map(errors), initial: initial ?? null, final: latest ?? null,
          observations, closedStoreOracle: finalization?.oracle ?? null, finalization,
          service: { binaryPath: service.binaryPath, databasePath: service.databasePath, transcript: service.transcript,
            processExits: service.processExits, lifecycleTaints: service.lifecycleTaints, stderr: service.stderr },
          excludes: ["native rendered pixels", "all native flow-event label intervals", "installed Tauri", "external archived readback", "human creative acceptance"],
        }, null, 2), { flag: "wx" });
        await info.attach("graph-label-placement-service-evidence", { path, contentType: "application/json" });
      } catch (error) { cleanupErrors.push(error); }
      if (cleanupErrors.length) throw new AggregateError(bodyFailed ? [primaryFailure, ...cleanupErrors] : cleanupErrors,
        "Graph label service evidence or closure failed; the original test failure and close outcomes are retained");
    }
  });
}
