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
    await window.__GRAPH_AUTHORITY__.publish(next); await window.__GRAPH_AUTHORITY__.reject(2, "Synthetic stale CAS");
  });
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
  await expect(pending.getByRole("status")).toContainText("original Graph request can still commit");
  await expect(button(page, "Save semantic Graph")).toBeDisabled(); expect(await requestCount(page)).toBe(1);
  expect(await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved())).toEqual(initial);
  expect(await page.evaluate(key => localStorage.getItem(key), retained.copyKey)).toBe(retained.raw);
  // This existing component fixture explicitly publishes synthetic props. It
  // does not stand in for Graph service, SQLite, renderer or native evidence.
  await page.evaluate(async () => {
    const peer = window.__GRAPH_AUTHORITY__.project(); peer.revision++; peer.title = "Independent component-fixture Graph revision";
    const branch = peer.branches.find(item => item.id === peer.active_branch);
    if (!branch) throw new Error("The component peer source has no active branch");
    branch.head_revision = peer.revision;
    await window.__GRAPH_AUTHORITY__.publish(peer);
  });
  const advanced = await page.evaluate(() => window.__GRAPH_AUTHORITY__.project());
  expect(advanced.revision).toBe(initial.revision + 1);
  expect(advanced.branches.find(item => item.id === advanced.active_branch)?.head_revision).toBe(advanced.revision);
  await button(page, "Inspect saved Graph outcome").click();
  await expect(pending).toContainText("Read current source at r" + advanced.revision + "."); await expect(continuation).toBeEnabled();
  await continuation.click(); await expect(pending).toHaveCount(0);
  await expect(field(page, "Vertex 1 label")).toHaveValue("Later Graph concept retained");
  expect(await page.evaluate(key => localStorage.getItem(key), retained.copyKey)).toBe(retained.raw);
  expect(await requestCount(page)).toBe(1); expect(await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved())).toEqual(advanced);
  await expect(button(page, "Save semantic Graph")).toBeDisabled();
  await button(page, "Review current Graph and keep unrelated changes").click();
  await button(page, "Inspect current Graph support and sources").click();
  await expect(button(page, "Save semantic Graph")).toBeEnabled(); expect(await requestCount(page)).toBe(1);
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
      await expect(pending.getByRole("status")).toContainText("original Graph request can still commit");
      await expect(button(page, "Save semantic Graph")).toBeDisabled();
    } else {
      await expect(continuation).toBeEnabled(); await continuation.click(); await expect(pending).toHaveCount(0);
      await expect(button(page, "Save semantic Graph")).toBeEnabled();
    }
    expect(await requestCount(page)).toBe(0); expect(await page.evaluate(() => window.__GRAPH_AUTHORITY__.saved())).toEqual(initial);
    expect(await page.evaluate(key => localStorage.getItem(key), original.copyKey)).toBe(original.raw);
  });
}
