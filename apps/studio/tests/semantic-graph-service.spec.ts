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
  const recipe: DeliveryRecipe & { font_selection: { primary_family: string; mono_family: string } } = {
    schema: "motionwright.delivery-recipe/1", scene_intents: [], original_mix: null,
    font_selection: { primary_family: "Instrument Sans Variable", mono_family: "IBM Plex Mono" },
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
