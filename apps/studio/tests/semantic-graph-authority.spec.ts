import { expect, test, type Page } from "@playwright/test";
import type {} from "./fixtures/semantic-graph-authority";
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const field = (page: Page, name: string) => page.getByLabel(name, { exact: true });
async function open(page: Page) { await page.goto("/tests/fixtures/semantic-graph-authority.html"); await expect(page.getByRole("heading", { name: "Semantic Graph", exact: true })).toBeVisible(); }
async function valid(page: Page) {
  await field(page, "Graph author label").fill("Component test author"); await field(page, "Graph rationale").fill("Explicitly synthetic component test.");
  await button(page, "Inspect current Graph support and sources").click(); await expect(button(page, "Save semantic Graph")).toBeEnabled();
}
const requestCount = (page: Page) => page.evaluate(() => window.__GRAPH_AUTHORITY__.requests().length);
async function saveFirst(page: Page) { await valid(page); await button(page, "Save semantic Graph").click(); await expect.poll(() => requestCount(page)).toBe(1); await page.evaluate(() => window.__GRAPH_AUTHORITY__.resolve(1)); await expect(button(page, "Save Graph changes")).toBeVisible(); }

test("Graph retains every incomplete geometry and rational byte across navigation and reload without sending", async ({ page }, testInfo) => {
  await open(page); await testInfo.attach("causal-editorial-component-fixture", { body: await page.screenshot(), contentType: "image/png" });
  await field(page, "Vertex 1 label").fill("Preserve my authored concept"); await field(page, "Vertex 1 box x").fill("1e"); await field(page, "Graph duration denominator").fill("");
  await expect(button(page, "Save semantic Graph")).toBeDisabled();
  await page.evaluate(async () => { await window.__GRAPH_AUTHORITY__.unmount(); await window.__GRAPH_AUTHORITY__.mount(); });
  await expect(field(page, "Vertex 1 label")).toHaveValue("Preserve my authored concept"); await expect(field(page, "Vertex 1 box x")).toHaveValue("1e");
  await page.reload(); await expect(field(page, "Vertex 1 box x")).toHaveValue("1e"); await expect(field(page, "Graph duration denominator")).toHaveValue(""); expect(await requestCount(page)).toBe(0);
});
test("Graph blocks a second source for an occupied scene and Deliverable before service submission", async ({ page }) => {
  await open(page); await saveFirst(page);
  const original = await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved());
  const savedGraph = original.production_design!.semantic_graphs![0];
  await field(page, "Graph source").selectOption("new:system_flow");
  await field(page, "Graph author label").fill("Different typed source, same occupied Deliverable");
  await field(page, "Graph rationale").fill("This must not replace an existing Graph implicitly.");
  await button(page, "Inspect current Graph support and sources").click();
  await expect(page.getByRole("status").filter({ hasText: "already owns" })).toBeVisible();
  await expect(button(page, "Save semantic Graph")).toBeDisabled();
  expect(await requestCount(page)).toBe(1);
  expect((await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved())).production_design!.semantic_graphs![0]).toEqual(savedGraph);
});
test("Graph new source retains distinct profile identities through selection and reload", async ({ page }) => {
  // Synthetic component fixture only: profile selection is the real Workbench
  // prop; these replies do not claim StudioService, Native, SQLite or Graph6.
  await open(page); await saveFirst(page);
  const firstProject = await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved());
  const firstGraph = firstProject.production_design!.semantic_graphs![0];
  const secondProfileId = await page.evaluate(async () => {
    const next = window.__GRAPH_AUTHORITY__.project();
    const second = { ...structuredClone(next.deliverables[0]), id: crypto.randomUUID() };
    // The fixture's displayProfile is deliverables[0], matching a global
    // Deliverable selection rather than retargeting the current Graph.
    next.deliverables = [second, ...next.deliverables]; next.revision++;
    await window.__GRAPH_AUTHORITY__.publish(next); return second.id;
  });
  await field(page, "Graph source").selectOption(firstGraph.id);
  await field(page, "Graph source").selectOption("new:causal_diagram");
  await expect(field(page, "Graph Deliverable")).toHaveValue(secondProfileId);
  await expect(button(page, "Save semantic Graph")).toBeVisible();
  await field(page, "Graph author label").fill("Second profile author");
  await field(page, "Graph rationale").fill("Independent editable Graph for the selected second profile.");
  await field(page, "Vertex 1 label").fill("Second profile concept");
  await button(page, "Inspect current Graph support and sources").click();
  await expect(button(page, "Save semantic Graph")).toBeEnabled();
  await button(page, "Save semantic Graph").click();
  await expect.poll(() => requestCount(page)).toBe(2);
  const request = await page.evaluate(() => window.__GRAPH_AUTHORITY__.requests()[1]);
  expect(request.change.type).toBe("upsert_semantic_graph");
  if (request.change.type !== "upsert_semantic_graph") throw new Error("Expected the second authored Graph.");
  expect(request.change.graph.id).not.toBe(firstGraph.id);
  expect(request.change.graph.variant_profile_ref).toBe(secondProfileId);
  const secondGraphId = request.change.graph.id;
  await page.evaluate(() => window.__GRAPH_AUTHORITY__.resolve(2));
  const secondProject = await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved());
  expect(secondProject.production_design!.semantic_graphs).toHaveLength(2);
  expect(secondProject.production_design!.semantic_graphs!.find(graph => graph.id === firstGraph.id)).toEqual(firstGraph);
  const secondGraph = secondProject.production_design!.semantic_graphs!.find(graph => graph.id === secondGraphId)!;
  expect(secondGraph.content.vertices[0].label).toBe("Second profile concept");
  await field(page, "Graph source").selectOption(firstGraph.id);
  await expect(field(page, "Graph Deliverable")).toHaveValue(firstGraph.variant_profile_ref);
  await expect(field(page, "Vertex 1 label")).toHaveValue(firstGraph.content.vertices[0].label);
  await field(page, "Graph source").selectOption(secondGraph.id);
  await expect(field(page, "Graph Deliverable")).toHaveValue(secondProfileId);
  await expect(field(page, "Vertex 1 label")).toHaveValue("Second profile concept");
  await page.reload();
  await expect(field(page, "Graph source")).toHaveValue(secondGraph.id);
  await expect(field(page, "Graph Deliverable")).toHaveValue(secondProfileId);
  await expect(field(page, "Vertex 1 label")).toHaveValue("Second profile concept");
  expect(await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved())).toEqual(secondProject);
  expect(await requestCount(page)).toBe(2);
});

/** Convert only this fixture's identity pointer; retained recovery bytes are untouched. */
async function exposeLegacyGraphIdentity(page: Page) {
  return page.evaluate(() => {
    const project = window.__GRAPH_AUTHORITY__.project(), sceneId = project.scenes[0].id;
    const profileId = project.deliverables[0].id, owner = [project.id, project.generation, project.active_branch, sceneId];
    const legacyKey = "motionwright.semantic-graph-new-id.v1:" + JSON.stringify([...owner, "causal_diagram"]);
    const profileKey = "motionwright.semantic-graph-new-id.v2:" + JSON.stringify([...owner, profileId, "causal_diagram"]);
    const graphId = sessionStorage.getItem(profileKey);
    if (!graphId) throw new Error("Fixture has no authored profile identity to migrate.");
    sessionStorage.setItem(legacyKey, graphId); sessionStorage.removeItem(profileKey);
    return { legacyKey, profileKey, graphId, profileId };
  });
}
test("Graph legacy draft migration preserves raw input and never lends its identity to another profile", async ({ page }) => {
  await open(page);
  await field(page, "Graph author label").fill("Legacy draft author");
  await field(page, "Graph rationale").fill("Preserve the incomplete original input during identity migration.");
  await field(page, "Vertex 1 label").fill("Legacy unsaved concept");
  await field(page, "Vertex 1 box x").fill("1e");
  const original = await currentGraphCopy(page), originalDraft = JSON.parse(original.raw).draft;
  const legacy = await exposeLegacyGraphIdentity(page);
  expect(legacy.graphId).toBe(originalDraft.graphId);
  await page.reload();
  await expect(field(page, "Vertex 1 label")).toHaveValue("Legacy unsaved concept");
  await expect(field(page, "Vertex 1 box x")).toHaveValue("1e");
  await expect(field(page, "Graph Deliverable")).toHaveValue(legacy.profileId);
  expect(JSON.parse((await currentGraphCopy(page)).raw).draft).toEqual(originalDraft);
  expect(await page.evaluate(key => localStorage.getItem(key), original.copyKey)).toBe(original.raw);
  expect(await page.evaluate(key => sessionStorage.getItem(key), legacy.legacyKey)).toBe(legacy.graphId);
  expect(await page.evaluate(key => sessionStorage.getItem(key), legacy.profileKey)).toBe(legacy.graphId);
  const other = await page.evaluate(async () => {
    const next = window.__GRAPH_AUTHORITY__.project();
    const second = { ...structuredClone(next.deliverables[0]), id: crypto.randomUUID() };
    next.deliverables = [second, ...next.deliverables]; next.revision++;
    await window.__GRAPH_AUTHORITY__.publish(next);
    return { profileId: second.id, key: "motionwright.semantic-graph-new-id.v2:" +
      JSON.stringify([next.id, next.generation, next.active_branch, next.scenes[0].id, second.id, "causal_diagram"]) };
  });
  await expect(field(page, "Graph Deliverable")).toHaveValue(other.profileId);
  await expect(field(page, "Vertex 1 label")).not.toHaveValue("Legacy unsaved concept");
  const secondId = await page.evaluate(key => sessionStorage.getItem(key), other.key);
  expect(secondId).toMatch(/^[0-9a-f-]{36}$/i); expect(secondId).not.toBe(legacy.graphId);
  await page.reload();
  await expect(field(page, "Graph Deliverable")).toHaveValue(other.profileId);
  expect(await page.evaluate(key => sessionStorage.getItem(key), other.key)).toBe(secondId);
  expect(await page.evaluate(key => localStorage.getItem(key), original.copyKey)).toBe(original.raw);
  await page.evaluate(async profileId => {
    const next = window.__GRAPH_AUTHORITY__.project();
    const first = next.deliverables.find(profile => profile.id === profileId);
    if (!first) throw new Error("Original profile missing.");
    next.deliverables = [first, ...next.deliverables.filter(profile => profile.id !== profileId)]; next.revision++;
    await window.__GRAPH_AUTHORITY__.publish(next);
  }, legacy.profileId);
  await expect(field(page, "Graph Deliverable")).toHaveValue(legacy.profileId);
  await expect(field(page, "Vertex 1 label")).toHaveValue("Legacy unsaved concept");
  await expect(field(page, "Vertex 1 box x")).toHaveValue("1e");
  await page.reload();
  await expect(field(page, "Vertex 1 box x")).toHaveValue("1e");
  await expect(button(page, "Save semantic Graph")).toBeDisabled();
  expect(await requestCount(page)).toBe(0);
  expect(await page.evaluate(key => localStorage.getItem(key), original.copyKey)).toBe(original.raw);
  expect(await page.evaluate(key => sessionStorage.getItem(key), legacy.legacyKey)).toBe(legacy.graphId);
});
test("Graph legacy saved identity survives new-source selection without replacing another profile", async ({ page }) => {
  await open(page); await saveFirst(page);
  const firstProject = await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved());
  const firstGraph = firstProject.production_design!.semantic_graphs![0];
  const legacy = await exposeLegacyGraphIdentity(page);
  expect(legacy.graphId).toBe(firstGraph.id);
  await page.reload();
  await field(page, "Graph source").selectOption("new:causal_diagram");
  await expect(button(page, "Save Graph changes")).toBeVisible();
  await expect(field(page, "Graph Deliverable")).toHaveValue(legacy.profileId);
  expect(await page.evaluate(key => sessionStorage.getItem(key), legacy.profileKey)).toBe(firstGraph.id);
  expect(await page.evaluate(key => sessionStorage.getItem(key), legacy.legacyKey)).toBe(firstGraph.id);
  const other = await page.evaluate(async () => {
    const next = window.__GRAPH_AUTHORITY__.project();
    const second = { ...structuredClone(next.deliverables[0]), id: crypto.randomUUID() };
    next.deliverables = [second, ...next.deliverables]; next.revision++;
    await window.__GRAPH_AUTHORITY__.publish(next);
    return { profileId: second.id, key: "motionwright.semantic-graph-new-id.v2:" +
      JSON.stringify([next.id, next.generation, next.active_branch, next.scenes[0].id, second.id, "causal_diagram"]) };
  });
  await field(page, "Graph source").selectOption(firstGraph.id);
  await field(page, "Graph source").selectOption("new:causal_diagram");
  await expect(field(page, "Graph Deliverable")).toHaveValue(other.profileId);
  await expect(button(page, "Save semantic Graph")).toBeVisible();
  expect(await page.evaluate(key => sessionStorage.getItem(key), other.key)).not.toBe(firstGraph.id);
  await field(page, "Graph author label").fill("Independent migrated profile");
  await field(page, "Graph rationale").fill("The old profile retains its original saved source.");
  await field(page, "Vertex 1 label").fill("Second Graph beside the migrated original");
  await button(page, "Inspect current Graph support and sources").click();
  await expect(button(page, "Save semantic Graph")).toBeEnabled();
  await button(page, "Save semantic Graph").click(); await expect.poll(() => requestCount(page)).toBe(2);
  const request = await page.evaluate(() => window.__GRAPH_AUTHORITY__.requests()[1]);
  if (request.change.type !== "upsert_semantic_graph") throw new Error("Expected the second Graph mutation.");
  const secondGraphId = request.change.graph.id;
  expect(secondGraphId).not.toBe(firstGraph.id);
  expect(request.change.graph.variant_profile_ref).toBe(other.profileId);
  await page.evaluate(() => window.__GRAPH_AUTHORITY__.resolve(2));
  const saved = await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved());
  expect(saved.production_design!.semantic_graphs).toHaveLength(2);
  expect(saved.production_design!.semantic_graphs!.find(graph => graph.id === firstGraph.id)).toEqual(firstGraph);
  await page.reload();
  await expect(field(page, "Graph source")).toHaveValue(secondGraphId);
  await expect(field(page, "Vertex 1 label")).toHaveValue("Second Graph beside the migrated original");
  await field(page, "Graph source").selectOption(firstGraph.id);
  await expect(field(page, "Graph Deliverable")).toHaveValue(firstGraph.variant_profile_ref);
  await expect(field(page, "Vertex 1 label")).toHaveValue(firstGraph.content.vertices[0].label);
  expect(await page.evaluate(key => sessionStorage.getItem(key), legacy.legacyKey)).toBe(firstGraph.id);
  expect(await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved())).toEqual(saved);
  expect(await requestCount(page)).toBe(2);
});

test("Graph migrated draft retarget keeps its identity without aliasing another profile", async ({ page }) => {
  await open(page);
  await field(page, "Graph author label").fill("Original migration author");
  await field(page, "Graph rationale").fill("An explicit profile retarget preserves this draft identity.");
  await field(page, "Vertex 1 label").fill("Explicitly retargeted legacy concept");
  const legacy = await exposeLegacyGraphIdentity(page);
  await page.reload();
  expect(await page.evaluate(key => sessionStorage.getItem(key), legacy.profileKey)).toBe(legacy.graphId);
  const other = await page.evaluate(async () => {
    const next = window.__GRAPH_AUTHORITY__.project();
    const second = { ...structuredClone(next.deliverables[0]), id: crypto.randomUUID() };
    next.deliverables.push(second); next.revision++;
    await window.__GRAPH_AUTHORITY__.publish(next);
    return { profileId: second.id, key: "motionwright.semantic-graph-new-id.v2:" +
      JSON.stringify([next.id, next.generation, next.active_branch, next.scenes[0].id, second.id, "causal_diagram"]) };
  });
  // This is an explicit author edit of the existing draft, unlike selecting
  // another displayProfile and requesting a separate New Graph source.
  await field(page, "Graph Deliverable").selectOption(other.profileId);
  await expect(field(page, "Graph Deliverable")).toHaveValue(other.profileId);
  const retargeted = await currentGraphCopy(page), retargetedDraft = JSON.parse(retargeted.raw).draft;
  expect(retargetedDraft.graphId).toBe(legacy.graphId);
  expect(retargetedDraft.input.variant_profile_ref).toBe(other.profileId);
  await page.evaluate(async profileId => {
    const next = window.__GRAPH_AUTHORITY__.project();
    const selected = next.deliverables.find(profile => profile.id === profileId)!;
    next.deliverables = [selected, ...next.deliverables.filter(profile => profile.id !== profileId)]; next.revision++;
    await window.__GRAPH_AUTHORITY__.publish(next);
  }, other.profileId);
  await field(page, "Graph source").selectOption("new:causal_diagram");
  await expect(field(page, "Graph Deliverable")).toHaveValue(other.profileId);
  const secondId = await page.evaluate(key => sessionStorage.getItem(key), other.key);
  expect(secondId).toMatch(/^[0-9a-f-]{36}$/i); expect(secondId).not.toBe(legacy.graphId);
  await expect(field(page, "Vertex 1 label")).not.toHaveValue("Explicitly retargeted legacy concept");
  expect(await page.evaluate(key => sessionStorage.getItem(key), legacy.profileKey)).toBe(legacy.graphId);
  expect(await page.evaluate(key => sessionStorage.getItem(key), legacy.legacyKey)).toBe(legacy.graphId);
  expect(await page.evaluate(key => localStorage.getItem(key), retargeted.copyKey)).toBe(retargeted.raw);
  await page.reload();
  expect(await page.evaluate(key => sessionStorage.getItem(key), other.key)).toBe(secondId);
  await page.evaluate(async profileId => {
    const next = window.__GRAPH_AUTHORITY__.project();
    const selected = next.deliverables.find(profile => profile.id === profileId)!;
    next.deliverables = [selected, ...next.deliverables.filter(profile => profile.id !== profileId)]; next.revision++;
    await window.__GRAPH_AUTHORITY__.publish(next);
  }, legacy.profileId);
  await expect(field(page, "Vertex 1 label")).toHaveValue("Explicitly retargeted legacy concept");
  await expect(field(page, "Graph Deliverable")).toHaveValue(other.profileId);
  const retainedGraphId = await page.evaluate(pointerKey => {
    const writer = sessionStorage.getItem(pointerKey);
    if (!writer) throw new Error("The original draft recovery pointer is missing.");
    const key = "motionwright.semantic-graph-recovery.v1:" + pointerKey.slice("motionwright.semantic-graph-writer.v1:".length) + ":" + writer;
    const raw = localStorage.getItem(key);
    if (!raw) throw new Error("The original scoped draft recovery copy is missing.");
    return JSON.parse(raw).draft.graphId;
  }, retargeted.pointerKey);
  expect(retainedGraphId).toBe(legacy.graphId);
  expect(await page.evaluate(key => localStorage.getItem(key), retargeted.copyKey)).toBe(retargeted.raw);
  expect(await requestCount(page)).toBe(0);
});

test("Graph original ACK retains later typing and return-to-base intent", async ({ page }) => {
  await open(page); await valid(page); const base = await field(page, "Vertex 1 label").inputValue(); await field(page, "Vertex 1 label").fill("Sent concept");
  await button(page, "Save semantic Graph").click(); await expect.poll(() => requestCount(page)).toBe(1);
  await field(page, "Vertex 1 label").fill(base); await field(page, "Vertex 1 meaning").fill("Meaning written after the request.");
  await page.evaluate(() => window.__GRAPH_AUTHORITY__.resolve(1)); await expect(field(page, "Vertex 1 label")).toHaveValue(base);
  await expect(field(page, "Vertex 1 meaning")).toHaveValue("Meaning written after the request."); await expect(page.getByText(/Later author input remains pending/)).toBeVisible();
  const saved = await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved()); expect(saved.production_design!.semantic_graphs![0].content.vertices[0].label).toBe("Sent concept");
  await page.reload(); await expect(field(page, "Vertex 1 label")).toHaveValue(base); expect(await requestCount(page)).toBe(1);
});
test("Matching props are not an ACK and do not clean the original uncertain Graph copy", async ({ page }) => {
  await open(page); await valid(page); await button(page, "Save semantic Graph").click(); await expect.poll(() => requestCount(page)).toBe(1);
  await page.evaluate(() => window.__GRAPH_AUTHORITY__.observeOriginal(1)); await expect(page.getByRole("region", { name: "Unconfirmed Graph submission" })).toBeVisible();
  await expect(button(page, "Save Graph changes")).toBeDisabled(); await page.evaluate(() => window.__GRAPH_AUTHORITY__.reject(1, "Original reply was lost"));
  await expect(page.getByRole("region", { name: "Unconfirmed Graph submission" })).toBeVisible(); expect(await requestCount(page)).toBe(1);
  const copies = await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith("motionwright.semantic-graph-recovery.v1:")).map(key => JSON.parse(localStorage.getItem(key)!)));
  expect(copies.some(copy => copy.draft.pending?.outcome === "UNKNOWN")).toBe(true);
});
test("Lost Graph reply survives reload and requires fresh explicit review before a new intention", async ({ page }) => {
  await open(page); await valid(page); await field(page, "Vertex 1 label").fill("Sent first"); await button(page, "Save semantic Graph").click(); await expect.poll(() => requestCount(page)).toBe(1);
  await field(page, "Vertex 1 label").fill("Later authored successor"); await page.evaluate(() => window.__GRAPH_AUTHORITY__.reject(1, "Synthetic lost reply after commit", true));
  await page.reload(); await expect(field(page, "Vertex 1 label")).toHaveValue("Later authored successor"); await expect(page.getByRole("region", { name: "Unconfirmed Graph submission" })).toBeVisible();
  expect(await requestCount(page)).toBe(1); await button(page, "Refresh saved Project").click(); await button(page, "Inspect saved Graph outcome").click();
  const pending = page.getByRole("region", { name: "Unconfirmed Graph submission" });
  await expect(pending).toContainText("The original operation has a recorded saved result");
  await button(page, "Continue Graph as a new reviewed intention").click(); await expect(page.getByRole("region", { name: "Unconfirmed Graph submission" })).toHaveCount(0);
  await expect(field(page, "Vertex 1 label")).toHaveValue("Later authored successor"); expect(await requestCount(page)).toBe(1);
  await button(page, "Inspect current Graph support and sources").click(); await expect(button(page, "Save Graph changes")).toBeEnabled();
  await button(page, "Save Graph changes").click(); await expect.poll(() => requestCount(page)).toBe(2);
  const request = await page.evaluate(() => window.__GRAPH_AUTHORITY__.requests()[1]); expect(request.change.type).toBe("upsert_semantic_graph");
  if (request.change.type === "upsert_semantic_graph") expect(request.change.graph.content.vertices[0].label).toBe("Later authored successor");
});
test("A real third-value source conflict remains explicit while a peer edge is preserved", async ({ page }) => {
  await open(page); await saveFirst(page); await button(page, "Inspect current Graph support and sources").click(); await field(page, "Vertex 1 label").fill("Local concept");
  await button(page, "Save Graph changes").click(); await expect.poll(() => requestCount(page)).toBe(2);
  await page.evaluate(async () => {
    const next = window.__GRAPH_AUTHORITY__.project(); next.revision++; const graph = next.production_design!.semantic_graphs![0]; graph.content.vertices[0].label = "Peer concept"; graph.content.edges[0].label = "Peer relation";
    await window.__GRAPH_AUTHORITY__.publish(next); await window.__GRAPH_AUTHORITY__.rejectKnown(2);
  });
  await expect(page.getByRole("region", { name: "Unconfirmed Graph submission" })).toContainText("Graph change was rejected");
  await button(page, "Inspect saved Graph outcome").click(); await button(page, "Continue Graph as a new reviewed intention").click();
  await expect(page.getByRole("region", { name: "Graph source reconciliation" })).toBeVisible(); await expect(field(page, "Vertex 1 label")).toHaveValue("Local concept");
  const conflict = page.getByLabel(/^Resolve Graph \/content\/vertices\/.*\/label$/); await expect(conflict).toHaveCount(1); await conflict.selectOption("input");
  await button(page, "Review current Graph and keep unrelated changes").click(); await page.locator("[data-graph-edge] summary").first().click(); await expect(field(page, "Edge 1 label")).toHaveValue("Peer relation");
  expect(await requestCount(page)).toBe(2);
});
test("Silent local storage failure blocks Graph callback and keeps current input", async ({ page }) => {
  await open(page); await valid(page);
  await page.evaluate(() => { const set = Storage.prototype.setItem; Storage.prototype.setItem = function (key, value) { if (this === localStorage && key.startsWith("motionwright.semantic-graph-recovery.v1:")) return; set.call(this, key, value); }; window.__GRAPH_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = set; }; });
  await field(page, "Vertex 1 label").fill("Retained without confirmed disk copy"); await expect(button(page, "Save semantic Graph")).toBeDisabled(); expect(await requestCount(page)).toBe(0);
  await expect(page.getByRole("region", { name: "Graph recovery needs attention" })).toBeVisible(); await expect(field(page, "Vertex 1 label")).toHaveValue("Retained without confirmed disk copy");
  await page.evaluate(() => window.__GRAPH_RESTORE_STORAGE__?.()); await button(page, "Verify Graph recovery again").click(); await expect(button(page, "Save semantic Graph")).toBeEnabled(); expect(await requestCount(page)).toBe(0);
});
test("A recovered Graph ACK cannot make a later original rejection UNKNOWN", async ({ page }) => {
  // Synthetic UI fixture only: keep this runtime alive so id1's retained ACK
  // is still present when the original Promise for id2 rejects.
  await open(page); await valid(page); await button(page, "Save semantic Graph").click();
  await expect.poll(() => requestCount(page)).toBe(1);
  await page.evaluate(() => {
    const set = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (this === localStorage && key.startsWith("motionwright.semantic-graph-recovery.v1:")) return;
      set.call(this, key, value);
    };
    window.__GRAPH_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = set; };
  });
  await page.evaluate(() => window.__GRAPH_AUTHORITY__.resolve(1));
  const pending = page.getByRole("region", { name: "Unconfirmed Graph submission" });
  await expect(pending).toBeVisible(); await expect(button(page, "Save Graph changes")).toBeDisabled();
  const savedFirst = await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved());
  await page.evaluate(() => window.__GRAPH_RESTORE_STORAGE__?.());
  await button(page, "Verify Graph recovery again").click();
  await button(page, "Inspect saved Graph outcome").click();
  await expect(pending).toContainText("The original operation has a recorded saved result");
  await button(page, "Continue Graph as a new reviewed intention").click();
  await expect(pending).toHaveCount(0); expect(await requestCount(page)).toBe(1);
  await field(page, "Graph rationale").fill("Second intention awaiting rejection");
  await button(page, "Inspect current Graph support and sources").click();
  await expect(button(page, "Save Graph changes")).toBeEnabled();
  await button(page, "Save Graph changes").click(); await expect.poll(() => requestCount(page)).toBe(2);
  const requests = await page.evaluate(() => window.__GRAPH_AUTHORITY__.requests());
  expect(requests[1].requestId).not.toBe(requests[0].requestId);
  await page.evaluate(() => window.__GRAPH_AUTHORITY__.rejectKnown(2));
  await expect(pending).toContainText("Graph change was rejected");
  await expect(page.getByRole("region", { name: "Graph recovery needs attention" })).toHaveCount(0);
  const retained = JSON.parse((await currentGraphCopy(page)).raw).draft;
  expect(retained.pending).toMatchObject({ id: requests[1].requestId, outcome: "REJECTED" });
  await expect(field(page, "Graph rationale")).toHaveValue("Second intention awaiting rejection");
  expect(await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved())).toEqual(savedFirst);
  await button(page, "Inspect saved Graph outcome").click();
  await button(page, "Continue Graph as a new reviewed intention").click();
  await expect(pending).toHaveCount(0);
  await expect(field(page, "Graph rationale")).toHaveValue("Second intention awaiting rejection");
  expect(await requestCount(page)).toBe(2);
  expect(await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved())).toEqual(savedFirst);
});

test("An observed original ACK with failed custody never enables another save", async ({ page }) => {
  await open(page); await valid(page); await button(page, "Save semantic Graph").click(); await expect.poll(() => requestCount(page)).toBe(1);
  await page.evaluate(() => { const set = Storage.prototype.setItem; Storage.prototype.setItem = function (key, value) { if (this === localStorage && key.startsWith("motionwright.semantic-graph-recovery.v1:")) return; set.call(this, key, value); }; window.__GRAPH_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = set; }; });
  await page.evaluate(() => window.__GRAPH_AUTHORITY__.resolve(1)); await expect(page.getByRole("region", { name: "Unconfirmed Graph submission" })).toBeVisible(); await expect(button(page, "Save Graph changes")).toBeDisabled();
  expect(await requestCount(page)).toBe(1); await page.evaluate(() => window.__GRAPH_RESTORE_STORAGE__?.());
});
test("Late dependency inspection cannot enable changed Graph input", async ({ page }) => {
  await open(page); await field(page, "Graph author label").fill("Test"); await field(page, "Graph rationale").fill("Deferred context test");
  await page.evaluate(() => window.__GRAPH_AUTHORITY__.deferContext(true)); await button(page, "Inspect current Graph support and sources").click();
  await field(page, "Vertex 1 label").fill("Changed while inspecting"); await page.evaluate(() => window.__GRAPH_AUTHORITY__.resolveContext());
  await expect(button(page, "Save semantic Graph")).toBeDisabled(); expect(await requestCount(page)).toBe(0);
  await page.evaluate(() => window.__GRAPH_AUTHORITY__.deferContext(false)); await button(page, "Inspect current Graph support and sources").click(); await expect(button(page, "Save semantic Graph")).toBeEnabled();
});
test("Unavailable matching consumer blocks saving without destroying the draft", async ({ page }) => {
  await open(page); await valid(page); await field(page, "Vertex 1 label").fill("A concept awaiting native support"); await page.evaluate(() => window.__GRAPH_AUTHORITY__.blockContext(true));
  await button(page, "Inspect current Graph support and sources").click(); await expect(button(page, "Save semantic Graph")).toBeDisabled(); await expect(field(page, "Vertex 1 label")).toHaveValue("A concept awaiting native support");
  await expect(page.getByText(/native_graph_profile_unavailable/).first()).toBeVisible(); expect(await requestCount(page)).toBe(0);
});
test("Vertex removal cannot silently remove edges, and flow exposes actual synthetic event controls", async ({ page }, testInfo) => {
  await open(page); await expect(button(page, "Remove vertex 1")).toBeDisabled(); await field(page, "Graph source").selectOption("new:system_flow");
  await expect(field(page, "Synthetic flow disclosure")).toHaveValue(/Synthetic system flow/); await button(page, "Add synthetic event").click(); await page.locator("[data-graph-event] summary").first().click();
  await field(page, "Event 1 label").fill("Illustrated transfer"); await field(page, "Graph editorial view").selectOption("playhead");
  await page.evaluate(async () => { const source = window.__GRAPH_AUTHORITY__.project(); await window.__GRAPH_AUTHORITY__.setPlayhead(Number(source.scenes[0].start.num) / Number(source.scenes[0].start.den) + 2.5 / 30); });
  const label = page.getByRole("img", { name: "Editorial semantic Graph vector study" }).locator("[data-graph-label]").filter({ hasText: "Illustrated transfer" });
  await expect(label).toHaveCount(1); await expect(label).toContainText("Transfer · data");
  await testInfo.attach("flow-active-event-editorial-component-fixture", { body: await page.screenshot(), contentType: "image/png" });
  await page.evaluate(async () => { const source = window.__GRAPH_AUTHORITY__.project(); await window.__GRAPH_AUTHORITY__.setPlayhead(Number(source.scenes[0].start.num) / Number(source.scenes[0].start.den) + 5); });
  await expect(page.getByText("Outside the authored Graph interval", { exact: true })).toBeVisible();
  await expect(page.locator("[data-graph-active-interval]")).toHaveAttribute("opacity", "0");
  await field(page, "Event 1 start numerator").fill("1e"); await page.reload();
  await field(page, "Graph source").selectOption("new:system_flow"); await page.locator("[data-graph-event] summary").first().click(); await expect(field(page, "Event 1 label")).toHaveValue("Illustrated transfer"); await expect(field(page, "Event 1 start numerator")).toHaveValue("1e"); expect(await requestCount(page)).toBe(0);
});
test("Review callback names the saved Graph identity, profile and exact source revision", async ({ page }) => {
  await open(page); await saveFirst(page); await button(page, "Open review of saved Graph").click();
  const value = await page.evaluate(() => ({ project: window.__GRAPH_AUTHORITY__.project(), reviews: window.__GRAPH_AUTHORITY__.reviews() }));
  const graph = value.project.production_design!.semantic_graphs![0]; expect(value.reviews).toHaveLength(1); expect(value.reviews[0]).toMatchObject({ resource: "graph:" + graph.id, branch_id: value.project.active_branch, revision: value.project.revision, profile_id: graph.variant_profile_ref });
  // This fixture proves only callback scope. Persistent review/recheck is the real-service oracle.
});


/** Identity faults are limited to this synthetic component fixture, never a Native result. */
for (const phase of ["get", "set", "readback"] as const) test("Graph identity retries a transient " + phase + " fault without replacing the draft or sending", async ({ page }) => {
  await page.addInitScript(phase => {
    const get = Storage.prototype.getItem, set = Storage.prototype.setItem;
    const marker = "TEST-ONLY.graph-identity-transient-" + phase;
    let wroteIdentity = false;
    Storage.prototype.getItem = function (key: string) {
      if (this === sessionStorage && key.startsWith("motionwright.semantic-graph-new-id.v2:") && get.call(sessionStorage, marker) !== "failed"
        && (phase === "get" || (phase === "readback" && wroteIdentity))) {
        set.call(sessionStorage, marker, "failed");
        if (phase === "readback") return null;
        throw new Error("Synthetic transient Graph identity read failure");
      }
      return get.call(this, key);
    };
    Storage.prototype.setItem = function (key: string, value: string) {
      const targeted = this === sessionStorage && key.startsWith("motionwright.semantic-graph-new-id.v2:");
      if (targeted && phase === "set" && get.call(sessionStorage, marker) !== "failed") {
        set.call(sessionStorage, marker, "failed"); throw new Error("Synthetic transient Graph identity write failure");
      }
      set.call(this, key, value); if (targeted) wroteIdentity = true;
    };
  }, phase);
  await open(page);
  const attention = page.getByRole("region", { name: "Graph identity needs attention" });
  await expect(attention).toBeVisible();
  const fontSize = await field(page, "Graph font_size").inputValue();
  await field(page, "Graph author label").fill("Retained identity author");
  await field(page, "Graph rationale").fill("Keep the same Graph after a temporary identity fault");
  await field(page, "Graph font_size").fill("1e");
  const original = await currentGraphCopy(page), originalDraft = JSON.parse(original.raw).draft;
  const identity = await page.evaluate(() => {
    const project = window.__GRAPH_AUTHORITY__.project();
    const key = "motionwright.semantic-graph-new-id.v2:" + JSON.stringify([project.id, project.generation, project.active_branch, project.scenes[0].id, project.deliverables[0].id, "causal_diagram"]);
    return { key, raw: sessionStorage.getItem(key) };
  });
  expect(identity.raw).toBe(phase === "readback" ? originalDraft.graphId : null);
  await expect(page.getByRole("region", { name: "Graph recovery needs attention" })).toHaveCount(0);
  await expect(button(page, "Save semantic Graph")).toBeDisabled(); expect(await requestCount(page)).toBe(0);
  if (phase !== "readback") {
    const competing = await page.evaluate(({ key, phase }) => {
      const value = phase === "get" ? "unreadable Graph identity bytes" : crypto.randomUUID();
      sessionStorage.setItem(key, value); return value;
    }, { key: identity.key, phase });
    await button(page, "Verify Graph identity again").click();
    await expect(attention).toContainText(phase === "get" ? "identity is unreadable" : "identity changed");
    expect(await page.evaluate(key => sessionStorage.getItem(key), identity.key)).toBe(competing);
    await expect(field(page, "Graph font_size")).toHaveValue("1e");
    await expect(field(page, "Graph author label")).toHaveValue("Retained identity author");
    expect(JSON.parse((await currentGraphCopy(page)).raw).draft).toEqual(originalDraft);
    await expect(button(page, "Save semantic Graph")).toBeDisabled(); expect(await requestCount(page)).toBe(0);
    // End the injected conflicting-storage condition; the UI must never overwrite it itself.
    await page.evaluate(({ key, raw }) => { if (raw === null) sessionStorage.removeItem(key); else sessionStorage.setItem(key, raw); }, identity);
  }
  await button(page, "Verify Graph identity again").click(); await expect(attention).toHaveCount(0);
  expect(await page.evaluate(key => sessionStorage.getItem(key), identity.key)).toBe(originalDraft.graphId);
  expect(JSON.parse((await currentGraphCopy(page)).raw).draft).toEqual(originalDraft);
  await expect(field(page, "Graph font_size")).toHaveValue("1e"); expect(await requestCount(page)).toBe(0);
  await page.reload();
  await expect(field(page, "Graph font_size")).toHaveValue("1e");
  await expect(field(page, "Graph author label")).toHaveValue("Retained identity author");
  await expect(field(page, "Graph rationale")).toHaveValue("Keep the same Graph after a temporary identity fault");
  expect(JSON.parse((await currentGraphCopy(page)).raw).draft.graphId).toBe(originalDraft.graphId);
  expect(await requestCount(page)).toBe(0);
  await field(page, "Graph font_size").fill(fontSize);
  await button(page, "Inspect current Graph support and sources").click(); await expect(button(page, "Save semantic Graph")).toBeEnabled();
  await button(page, "Save semantic Graph").click(); await expect.poll(() => requestCount(page)).toBe(1);
  const request = await page.evaluate(() => window.__GRAPH_AUTHORITY__.requests()[0]); expect(request.change.type).toBe("upsert_semantic_graph");
  if (request.change.type === "upsert_semantic_graph") {
    expect(request.change.graph.id).toBe(originalDraft.graphId);
    expect(request.change.graph.authorship.author_label).toBe("Retained identity author");
    expect(request.change.graph.authorship.rationale).toBe("Keep the same Graph after a temporary identity fault");
  }
});

/** Faults affect only recovery reads in this explicitly synthetic component fixture. */
async function failFirstRecoveryRead(page: Page, area: "pointer" | "data", marker: string) {
  await page.addInitScript(({ area, marker }) => {
    const get = Storage.prototype.getItem, set = Storage.prototype.setItem;
    Storage.prototype.getItem = function (key: string) {
      const targeted = area === "pointer" ? this === sessionStorage && key.startsWith("motionwright.semantic-graph-writer.v1:")
        : this === localStorage && key.startsWith("motionwright.semantic-graph-recovery.v1:");
      if (targeted && get.call(sessionStorage, marker) !== "failed") {
        set.call(sessionStorage, marker, "failed"); throw new Error("Synthetic first Graph recovery " + area + " read failure");
      }
      return get.call(this, key);
    };
  }, { area, marker });
}
async function currentGraphCopy(page: Page) {
  return page.evaluate(() => {
    const pointerKey = Object.keys(sessionStorage).find(key => key.startsWith("motionwright.semantic-graph-writer.v1:"))!;
    const writer = sessionStorage.getItem(pointerKey)!;
    const copyKey = "motionwright.semantic-graph-recovery.v1:" + pointerKey.slice("motionwright.semantic-graph-writer.v1:".length) + ":" + writer;
    return { pointerKey, writer, copyKey, raw: localStorage.getItem(copyKey)! };
  });
}
for (const mode of ["incomplete", "empty", "ABA"] as const) test("Graph first pointer fault then confirmed null retains " + mode + " input through Retry and reload", async ({ page }) => {
  await failFirstRecoveryRead(page, "pointer", "TEST-ONLY.graph-first-pointer-" + mode); await open(page);
  await expect(page.getByRole("region", { name: "Graph recovery needs attention" })).toBeVisible();
  const input = field(page, "Graph font_size"), baseline = await input.inputValue();
  await input.fill("intermediate"); const wanted = mode === "incomplete" ? "1e" : mode === "empty" ? "" : baseline; await input.fill(wanted);
  await field(page, "Graph rationale").fill("Author input after unreadable pointer");
  expect(await requestCount(page)).toBe(0); await button(page, "Verify Graph recovery again").click();
  await expect(page.getByRole("region", { name: "Graph recovery needs attention" })).toHaveCount(0);
  await expect(input).toHaveValue(wanted); expect(await requestCount(page)).toBe(0);
  await page.reload(); await expect(field(page, "Graph font_size")).toHaveValue(wanted);
  await expect(field(page, "Graph rationale")).toHaveValue("Author input after unreadable pointer"); expect(await requestCount(page)).toBe(0);
});
test("Graph known W with unreadable data refuses W2 without discarding UNKNOWN or later text", async ({ page }) => {
  await open(page); await valid(page); await button(page, "Save semantic Graph").click(); await expect.poll(() => requestCount(page)).toBe(1);
  await page.evaluate(() => window.__GRAPH_AUTHORITY__.reject(1, "Synthetic original reply lost", true));
  const original = await currentGraphCopy(page); expect(JSON.parse(original.raw).draft.pending.outcome).toBe("UNKNOWN");
  await failFirstRecoveryRead(page, "data", "TEST-ONLY.graph-first-copy-known-w"); await page.reload();
  await expect(page.getByRole("region", { name: "Graph recovery needs attention" })).toBeVisible();
  await field(page, "Graph rationale").fill("Later input after original read failed");
  const peer = await page.evaluate(original => {
    const writer = crypto.randomUUID(), key = original.copyKey.slice(0, -original.writer.length) + writer;
    const copy = JSON.parse(original.raw); copy.writerId = writer; copy.draft.pending = null; copy.updatedAt++;
    const raw = JSON.stringify(copy); localStorage.setItem(key, raw); sessionStorage.setItem(original.pointerKey, writer); return { writer, key, raw };
  }, original);
  await button(page, "Verify Graph recovery again").click();
  await expect(page.getByRole("region", { name: "Graph recovery needs attention" })).toContainText(/changed the earlier Graph pointer/);
  await expect(field(page, "Graph rationale")).toHaveValue("Later input after original read failed"); expect(await requestCount(page)).toBe(1);
  const storage = await page.evaluate(({ original, peer }) => ({ old: localStorage.getItem(original.copyKey), peer: localStorage.getItem(peer.key), pointer: sessionStorage.getItem(original.pointerKey) }), { original, peer });
  expect(storage).toEqual({ old: original.raw, peer: peer.raw, pointer: peer.writer });
  await expect(button(page, "Save semantic Graph")).toBeDisabled();
});
test("Graph original UNKNOWN first data read failure merges only explicit later fields and retains its original copy", async ({ page }) => {
  await open(page); await saveFirst(page); await button(page, "Inspect current Graph support and sources").click();
  await field(page, "Graph rationale").fill("Original recovered rationale"); await field(page, "Graph author label").fill("Original recovered author");
  await button(page, "Save Graph changes").click(); await expect.poll(() => requestCount(page)).toBe(2);
  await page.evaluate(() => window.__GRAPH_AUTHORITY__.reject(2, "Synthetic second reply lost", true));
  const original = await currentGraphCopy(page); expect(JSON.parse(original.raw).draft.pending.outcome).toBe("UNKNOWN");
  await failFirstRecoveryRead(page, "data", "TEST-ONLY.graph-first-copy-merge"); await page.reload();
  await expect(page.getByRole("region", { name: "Graph recovery needs attention" })).toBeVisible();
  await field(page, "Graph author label").fill("Intermediate later text"); await field(page, "Graph author label").fill("");
  await field(page, "Graph font_size").fill("1e"); await button(page, "Verify Graph recovery again").click();
  await expect(page.getByRole("region", { name: "Unconfirmed Graph submission" })).toBeVisible();
  await expect(field(page, "Graph rationale")).toHaveValue("Original recovered rationale"); await expect(field(page, "Graph author label")).toHaveValue("");
  await expect(field(page, "Graph font_size")).toHaveValue("1e"); expect(await requestCount(page)).toBe(2);
  expect(await page.evaluate(key => localStorage.getItem(key), original.copyKey)).toBe(original.raw);
  await page.reload(); await expect(field(page, "Graph rationale")).toHaveValue("Original recovered rationale"); await expect(field(page, "Graph author label")).toHaveValue("");
  await expect(field(page, "Graph font_size")).toHaveValue("1e"); await expect(page.getByRole("region", { name: "Unconfirmed Graph submission" })).toBeVisible();
  expect(await page.evaluate(key => localStorage.getItem(key), original.copyKey)).toBe(original.raw); expect(await requestCount(page)).toBe(2);
});


test("Graph recovery keeps a final qualitative unit after symbol typing and return without replay", async ({ page }) => {
  await open(page); await saveFirst(page); const original = await currentGraphCopy(page);
  await failFirstRecoveryRead(page, "data", "TEST-ONLY.graph-first-copy-unit-return"); await page.reload();
  await expect(page.getByRole("region", { name: "Graph recovery needs attention" })).toBeVisible();
  await field(page, "Vertex 1 unit type").selectOption("symbol"); await field(page, "Vertex 1 unit symbol").fill("kg");
  await field(page, "Vertex 1 unit type").selectOption("qualitative"); await button(page, "Verify Graph recovery again").click();
  await expect(page.getByRole("region", { name: "Graph recovery needs attention" })).toHaveCount(0);
  await expect(field(page, "Vertex 1 unit type")).toHaveValue("qualitative"); await expect(field(page, "Vertex 1 unit symbol")).toHaveCount(0);
  expect(await requestCount(page)).toBe(1); expect(await page.evaluate(key => localStorage.getItem(key), original.copyKey)).toBe(original.raw);
  await page.reload(); await expect(field(page, "Vertex 1 unit type")).toHaveValue("qualitative"); expect(await requestCount(page)).toBe(1);
  expect(await page.evaluate(key => localStorage.getItem(key), original.copyKey)).toBe(original.raw);
});


test("Graph UNKNOWN observed by the copy list cannot be replaced under the same W before Retry", async ({ page }) => {
  await open(page); await valid(page); await button(page, "Save semantic Graph").click(); await expect.poll(() => requestCount(page)).toBe(1);
  await page.evaluate(() => window.__GRAPH_AUTHORITY__.reject(1, "Synthetic original reply lost", true));
  const original = await currentGraphCopy(page); expect(JSON.parse(original.raw).draft.pending.outcome).toBe("UNKNOWN");
  await failFirstRecoveryRead(page, "data", "TEST-ONLY.graph-first-copy-same-w-replacement"); await page.reload();
  await expect(button(page, "Download observed original Graph copy")).toBeVisible();
  await field(page, "Graph rationale").fill("Later input must remain separate");
  const replacement = await page.evaluate(original => {
    const value = JSON.parse(original.raw); value.draft.pending = null; value.updatedAt++;
    const raw = JSON.stringify(value); localStorage.setItem(original.copyKey, raw); return raw;
  }, original);
  await button(page, "Verify Graph recovery again").click();
  await expect(page.getByRole("region", { name: "Graph recovery needs attention" })).toContainText(/recovery bytes changed/);
  await expect(field(page, "Graph rationale")).toHaveValue("Later input must remain separate"); await expect(button(page, "Save semantic Graph")).toBeDisabled();
  expect(await requestCount(page)).toBe(1);
  expect(await page.evaluate(original => ({ raw: localStorage.getItem(original.copyKey), pointer: sessionStorage.getItem(original.pointerKey) }), original)).toEqual({ raw: replacement, pointer: original.writer });
  const downloaded = page.waitForEvent("download"); await button(page, "Download observed original Graph copy").click();
  const file = await downloaded, stream = await file.createReadStream(); expect(stream).not.toBeNull(); let raw = "";
  if (stream) for await (const chunk of stream) raw += chunk.toString("utf8");
  expect(raw).toBe(original.raw); expect(JSON.parse(raw).draft.pending.outcome).toBe("UNKNOWN"); expect(await requestCount(page)).toBe(1);
});


test("Graph v1 preserves literal empty lines at fixed editorial baselines through reload", async ({ page }) => {
  await open(page);
  await field(page, "Graph font_size").fill("20");
  for (const [key, value] of Object.entries({ x: "100", y: "200", width: "300", height: "72" })) await field(page, "Vertex 1 box " + key).fill(value);
  const input = field(page, "Vertex 1 label"); await expect(input).toHaveJSProperty("tagName", "TEXTAREA");
  await input.fill("Alpha\n\nOmega");
  const label = page.getByRole("img", { name: "Editorial semantic Graph vector study" }).locator("[data-graph-label]").filter({ hasText: "Alpha" });
  const lines = label.locator("text"); await expect(lines).toHaveCount(3);
  expect(await lines.allTextContents()).toEqual(["Alpha", "", "Omega"]);
  for (const [index, baseline] of [219, 243, 267].entries()) {
    await expect(lines.nth(index)).toHaveAttribute("x", "250");
    await expect(lines.nth(index)).toHaveAttribute("y", String(baseline));
    await expect(lines.nth(index)).toHaveAttribute("text-anchor", "middle");
    await expect(lines.nth(index)).toHaveAttribute("dominant-baseline", "alphabetic");
  }
  await page.reload(); await expect(field(page, "Vertex 1 label")).toHaveValue("Alpha\n\nOmega");
  expect(await requestCount(page)).toBe(0);
});

test("Graph v1 editorial overflow retains every space without wrapping clipping or text scaling", async ({ page }) => {
  await open(page); await field(page, "Graph font_size").fill("20");
  await field(page, "Vertex 1 box width").fill("20");
  const literal = "  Wide literal label  "; await field(page, "Vertex 1 label").fill(literal);
  const label = page.getByRole("img", { name: "Editorial semantic Graph vector study" }).locator("[data-graph-label]").filter({ hasText: "Wide literal label" });
  const lines = label.locator("text"); await expect(lines).toHaveCount(1); expect(await lines.allTextContents()).toEqual([literal]);
  await expect(lines.first()).toHaveCSS("white-space", "pre");
  const geometry = await lines.first().evaluate(node => ({ width: (node as SVGGraphicsElement).getBBox().width,
    textLength: node.getAttribute("textLength"), lengthAdjust: node.getAttribute("lengthAdjust"),
    transform: node.getAttribute("transform"), clipPath: node.getAttribute("clip-path"), space: node.getAttribute("xml:space") }));
  expect(geometry.width).toBeGreaterThan(20);
  expect({ ...geometry, width: undefined }).toEqual({ width: undefined, textLength: null, lengthAdjust: null, transform: null, clipPath: null, space: "preserve" });
  await page.reload(); await expect(field(page, "Vertex 1 label")).toHaveValue(literal); expect(await requestCount(page)).toBe(0);
  // This component fixture demonstrates retention and vector coordinates; native face/overflow admission is a separate real-consumer oracle.
});

test("Graph same-revision UNKNOWN remains blocked until an explicit newer-source review preserves its original copy", async ({ page }) => {
  await open(page); await valid(page);
  const initial = await page.evaluate(() => window.__GRAPH_AUTHORITY__.project());
  await field(page, "Vertex 1 label").fill("Original Graph concept");
  await button(page, "Save semantic Graph").click(); await expect.poll(() => requestCount(page)).toBe(1);
  await page.evaluate(() => window.__GRAPH_AUTHORITY__.reject(1, "Synthetic uncertain delivery without an observed commit"));
  await field(page, "Vertex 1 label").fill("Later Graph concept retained");
  const retained = await currentGraphCopy(page), pending = page.getByRole("region", { name: "Unconfirmed Graph submission" });
  expect(JSON.parse(retained.raw).draft.pending.outcome).toBe("UNKNOWN");
  await button(page, "Inspect saved Graph outcome").click();
  const continuation = button(page, "Continue Graph as a new reviewed intention");
  await expect(pending).toContainText("Read current source at r" + initial.revision + ". 0 history entries inspected");
  await expect(continuation, "GRAPH_UNCHANGED_BASE_REMAINS_UNKNOWN").toBeDisabled();
  await expect(pending.getByRole("status")).toContainText("No recorded result is available for the original operation");
  await expect(button(page, "Save semantic Graph")).toBeDisabled(); expect(await requestCount(page)).toBe(1);
  expect(await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved())).toEqual(initial);
  expect(await page.evaluate(key => localStorage.getItem(key), retained.copyKey)).toBe(retained.raw);
  // The original backend result is now observed independently of its lost
  // Promise; no second author dispatch or invented ACK occurs.
  await page.evaluate(() => window.__GRAPH_AUTHORITY__.recordLateOriginal(1));
  const historical = await page.evaluate(() => window.__GRAPH_AUTHORITY__.project());
  expect(historical.revision).toBe(initial.revision + 1);
  // This explicitly synthetic peer then advances another revision. A newer
  // head alone did not resolve UNKNOWN; only original recorded recovery may.
  await page.evaluate(async () => {
    const peer = window.__GRAPH_AUTHORITY__.project(); peer.revision++; peer.title = "Independent component-fixture Graph revision";
    const branch = peer.branches.find(item => item.id === peer.active_branch);
    if (!branch) throw new Error("The component peer source has no active branch");
    branch.head_revision = peer.revision;
    await window.__GRAPH_AUTHORITY__.publish(peer);
  });
  const advanced = await page.evaluate(() => window.__GRAPH_AUTHORITY__.project());
  expect(advanced.revision).toBe(initial.revision + 2);
  expect(advanced.branches.find(item => item.id === advanced.active_branch)?.head_revision).toBe(advanced.revision);
  await button(page, "Inspect saved Graph outcome").click();
  await expect(pending).toContainText("Read current source at r" + advanced.revision + ".");
  await expect(pending.getByRole("status")).toContainText("recorded saved result at r" + historical.revision);
  await expect(continuation).toBeEnabled();
  await continuation.click(); await expect(pending).toHaveCount(0);
  await expect(field(page, "Vertex 1 label")).toHaveValue("Later Graph concept retained");
  expect(await page.evaluate(key => localStorage.getItem(key), retained.copyKey)).toBe(retained.raw);
  expect(await requestCount(page)).toBe(1); expect(await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved())).toEqual(advanced);
  await expect(button(page, "Save Graph changes")).toBeDisabled();
  await button(page, "Review current Graph and keep unrelated changes").click();
  await button(page, "Inspect current Graph support and sources").click();
  await expect(button(page, "Save Graph changes")).toBeEnabled(); expect(await requestCount(page)).toBe(1);
  expect(await page.evaluate(key => localStorage.getItem(key), retained.copyKey)).toBe(retained.raw);
  await page.reload(); await expect(field(page, "Vertex 1 label")).toHaveValue("Later Graph concept retained");
  await expect(pending).toHaveCount(0); expect(await requestCount(page)).toBe(1);
  expect(await page.evaluate(key => localStorage.getItem(key), retained.copyKey)).toBe(retained.raw);
});

for (const recovered of [false, true]) {
  test("Graph known NOT_SENT " + (recovered ? "becomes UNKNOWN after reload and cannot release the same base" : "can explicitly continue on its same base in the live caller"), async ({ page }) => {
    await open(page); await valid(page);
    const initial = await page.evaluate(() => window.__GRAPH_AUTHORITY__.project());
    await page.evaluate(() => {
      const set = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if (this === localStorage && key.startsWith("motionwright.semantic-graph-recovery.v1:") && JSON.parse(value).draft.pending) return;
        set.call(this, key, value);
      };
      window.__GRAPH_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = set; };
    });
    await button(page, "Save semantic Graph").click();
    const pending = page.getByRole("region", { name: "Unconfirmed Graph submission" });
    await expect(pending).toContainText("Graph change was not sent"); expect(await requestCount(page)).toBe(0);
    await page.evaluate(() => window.__GRAPH_RESTORE_STORAGE__?.());
    await button(page, "Verify Graph recovery again").click();
    await expect(page.getByRole("region", { name: "Graph recovery needs attention" })).toHaveCount(0);
    const original = await currentGraphCopy(page); expect(JSON.parse(original.raw).draft.pending.outcome).toBe("NOT_SENT");
    if (recovered) {
      await page.reload(); await expect(pending).toContainText("Graph save outcome is unconfirmed");
      expect(JSON.parse((await currentGraphCopy(page)).raw).draft.pending.outcome).toBe("UNKNOWN");
    }
    await button(page, "Inspect saved Graph outcome").click();
    await expect(pending).toContainText("Read current source at r" + initial.revision + ". 0 history entries inspected");
    const continuation = button(page, "Continue Graph as a new reviewed intention");
    if (recovered) {
      await expect(continuation, "RECOVERED_GRAPH_NOT_SENT_REMAINS_UNKNOWN").toBeDisabled();
      await expect(pending.getByRole("status")).toContainText("No recorded result is available for the original operation");
      await expect(button(page, "Save semantic Graph")).toBeDisabled();
    } else {
      await expect(continuation).toBeEnabled(); await continuation.click(); await expect(pending).toHaveCount(0);
      await expect(button(page, "Save semantic Graph")).toBeEnabled();
    }
    expect(await requestCount(page)).toBe(0); expect(await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved())).toEqual(initial);
    expect(await page.evaluate(key => localStorage.getItem(key), original.copyKey)).toBe(original.raw);
  });
}
