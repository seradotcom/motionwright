import { randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";
import type { Change, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Actual App -> canonical projection adapter -> source-built StudioService ->
// SQLite. Arrangement uses real grants and changes. No persistence fallback.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A source-built studio_review_session with the projection transport is required.");
test.setTimeout(180_000);

const SELECTION = "motionwright.project-library.selection.v1";
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const heading = (page: Page, name: string) => page.getByRole("heading", { name, exact: true });
type RequestEntry = { id: number; command: string; args: Record<string, unknown> };
function requests(service: ScopedChangeService, start = 0, end = service.transcript.length): RequestEntry[] {
  return service.transcript.slice(start, end).filter(entry => entry.direction === "request" && "command" in entry.payload)
    .map(entry => entry.payload as RequestEntry);
}
function fullReads(entries: RequestEntry[]) {
  return entries.filter(entry => ["get_project", "open_project", "reopen"].includes(entry.command)
    || (entry.command === "bootstrap" && (entry.args.request as { project_id?: unknown })?.project_id !== null));
}
function detail(reason: unknown) {
  return reason instanceof Error ? { name: reason.name, message: reason.message, stack: reason.stack } : { value: reason };
}
interface ProjectionCase {
  service: ScopedChangeService;
  baseline: Project;
  expected: Project;
  peers: ScopedChangeService[];
  observations: Record<string, unknown>;
  cleanup: (() => Promise<unknown>)[];
  bounded(label: string, action: () => Promise<void>): Promise<void>;
}
async function createStored(service: ScopedChangeService, title: string) {
  const intent = { request_id: randomUUID(), title };
  const grant = await service.invoke<{ token: string }>("issue_project_creation_grant", { request: intent });
  return (await service.invoke<{ project: Project }>("create_project", { request: { intent, effect_grant: grant.token } })).project;
}
async function withProjection(page: Page, info: TestInfo, sceneCount: number, run: (value: ProjectionCase) => Promise<void>) {
  const service = await ScopedChangeService.create({
    databasePath: info.outputPath("service", "studio.sqlite3"), seedFixture: false, desktopInvokeFixture: true,
  });
  const peers: ScopedChangeService[] = [], cleanup: (() => Promise<unknown>)[] = [], failures: unknown[] = [];
  const observations: Record<string, unknown> = {}, measured: Record<string, unknown>[] = [];
  observations.boundedPhases = measured;
  let context: ProjectionCase | undefined, final: Project | undefined, custody: StoreFinalization | undefined;
  let primary: { reason: unknown } | undefined;
  page.on("dialog", dialog => dialog.accept());
  try {
    let baseline = await createStored(service, "Saved projection source");
    final = baseline;
    for (let index = 0; index < sceneCount; index++) {
      baseline = await service.apply(baseline, { type: "add_scene", name: "Paged scene " + (index + 1),
        objective: "Synthetic arrangement saved through the actual service", duration_seconds: 4 });
      final = baseline;
    }
    observations.baseline = baseline;
    context = {
      service, baseline, expected: baseline, peers, observations, cleanup,
      async bounded(label, action) {
        const start = service.transcript.length;
        let succeeded = false;
        try { await action(); succeeded = true; }
        finally {
          const end = service.transcript.length, entries = requests(service, start, end);
          measured.push({ label, startTranscriptIndex: start, endTranscriptIndexExclusive: end, succeeded,
            requestIds: entries.map(entry => entry.id), commands: entries.map(entry => entry.command),
            fullReadRequests: fullReads(entries) });
          if (succeeded) {
            expect(fullReads(entries), label + ": bounded phase issued a complete-project read").toEqual([]);
            expect(entries.filter(entry => !["bootstrap", "project_projection_head", "project_scene_page", "project_scene_nodes", "list_projects"].includes(entry.command)),
              label + ": bounded observation issued an effect or unrelated command").toEqual([]);
          }
        }
      },
    };
    await service.attachPage(page, baseline.id);
    await run(context);
  } catch (reason) { primary = { reason }; failures.push(reason); }
  finally {
    for (const action of cleanup) { try { await action(); } catch (reason) { failures.push(reason); } }
    if (context) {
      try {
        final = await service.project(context.expected.id);
        expect(final).toEqual(context.expected);
        observations.finalReadOutsideBoundedPhase = final;
      } catch (reason) { failures.push(reason); }
    }
    try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, peers, [() => page.close()]); }
    catch (reason) { failures.push(reason); }
    try {
      const path = info.outputPath("project-projection-service-evidence.json");
      await writeFile(path, JSON.stringify({
        scope: "actual App and source-built canonical projection transport with real SQLite and closed-store custody",
        arrangement: "synthetic documents authored through real creation grants and service changes",
        measuredScope: "bounded phases cover UI read requests only; arrangement, peer writes, explicit complete editor opens and final oracles are recorded separately",
        exclusions: ["installed Tauri", "native renderer execution", "human acceptance", "bounded editing", "performance acceptance"],
        observations, final, canonicalOracle: custody?.oracle, primaryFailure: primary ? detail(primary.reason) : null,
        failures: failures.map(detail),
        services: [service, ...peers].map(item => ({ databasePath: item.databasePath, binaryPath: item.binaryPath,
          transcript: item.transcript, processExits: item.processExits, lifecycleTaints: item.lifecycleTaints, stderr: item.stderr })),
      }, null, 2), { flag: "wx" });
      await info.attach("project-projection-service-evidence", { path, contentType: "application/json" });
      await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
    } catch (reason) { failures.push(reason); }
    if (failures.length === 1) throw failures[0];
    if (failures.length > 1) throw new AggregateError(failures, "Projection scenario and closed-store finalization retained separate failures", { cause: primary?.reason });
  }
}

test("remembered 65-scene App opening and process restart use bounded native pages without complete reads", async ({ page }, info) => {
  await withProjection(page, info, 65, async value => {
    const { service, baseline, bounded, observations } = value;
    await bounded("remembered large opening and second scene page", async () => {
      await page.goto("/");
      await expect(heading(page, baseline.title)).toBeVisible();
      await expect(heading(page, "Scenes 1–64 of 65")).toBeVisible();
      await expect(page.getByRole("list", { name: "Loaded scene summaries" }).getByRole("listitem")).toHaveCount(64);
      await button(page, "Next scenes").click();
      await expect(heading(page, "Scenes 65–65 of 65")).toBeVisible();
      await expect(page.getByRole("list", { name: "Loaded scene summaries" }).getByRole("listitem")).toHaveCount(1);
      await button(page, "View nodes of Paged scene 65").click();
      await expect(heading(page, "Nodes 0–0 of 0")).toBeVisible();
    });
    const headRequest = requests(service).find(entry => entry.command === "project_projection_head");
    expect((headRequest!.args.request as { project_id: string }).project_id).toBe(baseline.id);
    const replies = service.transcript.filter(entry => entry.direction === "response" && entry.payload.id === headRequest!.id);
    observations.originalHeadReplies = replies;
    await service.close(); await service.start();
    await bounded("new process and actual App reload", async () => {
      await page.reload(); await expect(heading(page, "Scenes 1–64 of 65")).toBeVisible();
      await expect(page.getByText("Revision " + baseline.revision, { exact: true })).toBeVisible();
    });
  });
});

test("explicit small-project browsing pages 129 native nodes and preserves off-page references", async ({ page }, info) => {
  await withProjection(page, info, 1, async value => {
    const { service, baseline, bounded, observations } = value;
    const sceneId = baseline.scenes[0].id, ids = Array.from({ length: 129 }, () => randomUUID());
    let arranged = baseline;
    for (let index = 0; index < ids.length; index++) {
      arranged = await service.apply(arranged, { type: "add_canvas_node", scene_id: sceneId, node: {
        ...structuredClone(fixtureBootstrap.project.scenes[0].nodes[0]),
        id: ids[index], name: "Stored node " + (index + 1), parent_id: null,
        relations: [], keyframes: [], property_locks: [],
      } });
      value.expected = arranged;
    }
    arranged = await service.apply(arranged, { type: "reparent_canvas_node", scene_id: sceneId,
      node_id: ids[0], parent_id: ids[128], z_index: 0 }); value.expected = arranged;
    arranged = await service.apply(arranged, { type: "set_canvas_relations", scene_id: sceneId,
      node_id: ids[1], relations: [{ id: randomUUID(), kind: "follow", target_id: ids[128] }] }); value.expected = arranged;
    observations.nodeArrangement = arranged;
    await page.goto("/"); await expect(page.getByRole("navigation", { name: "Workspaces" })).toBeVisible();
    await button(page, "Open project library").click();
    await bounded("explicit small saved-page choice and node boundary", async () => {
      await button(page, "Browse pages of " + baseline.title).click();
      await button(page, "View nodes of Paged scene 1").click();
      const nodes = page.getByRole("list", { name: "Loaded saved nodes" });
      await expect(nodes.getByRole("listitem")).toHaveCount(128);
      await expect(nodes).toContainText("Parent: " + ids[128] + " — not loaded on this page");
      await expect(nodes).toContainText("follow: " + ids[128] + " — not loaded on this page");
      await button(page, "Next nodes").click(); await expect(heading(page, "Nodes 129–129 of 129")).toBeVisible();
      await expect(nodes.getByRole("listitem")).toHaveCount(1);
      await button(page, "Previous nodes").click(); await expect(heading(page, "Nodes 1–128 of 129")).toBeVisible();
    });
  });
});

test("a real peer revision returns typed stale-page failure and preserves the view until explicit reload", async ({ page }, info) => {
  await withProjection(page, info, 65, async value => {
    const { service, baseline, bounded, observations, peers } = value;
    await bounded("initial large view", async () => { await page.goto("/"); await expect(heading(page, "Scenes 1–64 of 65")).toBeVisible(); });
    const preference = await page.evaluate(key => localStorage.getItem(key), SELECTION);
    const peer = await service.peer(); peers.push(peer);
    value.expected = await peer.apply(baseline, { type: "rename_project", title: "Peer saved a later title" });
    observations.peerSavedProject = value.expected;
    await bounded("stale page refusal and explicit reload", async () => {
      const before = service.transcript.length;
      await button(page, "Next scenes").click();
      await expect(page.getByRole("alert")).toContainText("saved project changed");
      await expect(heading(page, "Scenes 1–64 of 65")).toBeVisible();
      await expect(heading(page, baseline.title)).toBeVisible();
      expect(await page.evaluate(key => localStorage.getItem(key), SELECTION)).toBe(preference);
      const request = requests(service, before).find(entry => entry.command === "project_scene_page")!;
      const response = service.transcript.find(entry => entry.direction === "response" && entry.payload.id === request.id)!;
      expect(response.payload).toMatchObject({ id: request.id, ok: false,
        error: { code: "stale_revision", expected: String(baseline.revision), actual: String(value.expected.revision) } });
      observations.originalTypedStaleResponse = response;
      await button(page, "Reload saved pages").click(); await expect(heading(page, value.expected.title)).toBeVisible();
      await button(page, "Next scenes").click(); await expect(heading(page, "Scenes 65–65 of 65")).toBeVisible();
    });
  });
});

test("a lost genuine node-page delivery is reported safely without fallback, replay or lost prior page", async ({ page }, info) => {
  await withProjection(page, info, 65, async value => {
    const { service, bounded, observations, cleanup } = value;
    await bounded("initial saved view", async () => { await page.goto("/"); await expect(heading(page, "Scenes 1–64 of 65")).toBeVisible(); });
    const original = service.invoke.bind(service); let armed = true;
    cleanup.push(async () => { service.invoke = original; });
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const result = await original<T>(command, args);
      if (armed && command === "project_scene_nodes") {
        armed = false; observations.originalReadReplyBeforeDeliveryFault = result;
        throw { code: "unavailable", unrecognized_detail: "DO_NOT_DISPLAY" };
      }
      return result;
    };
    await bounded("unknown delivery failure and explicit second read", async () => {
      const before = requests(service).filter(entry => entry.command === "project_scene_nodes").length;
      await button(page, "View nodes of Paged scene 1").click();
      await expect(page.getByRole("alert")).toContainText("could not be verified");
      await expect(page.locator("body")).not.toContainText("DO_NOT_DISPLAY");
      await expect(heading(page, "Scenes 1–64 of 65")).toBeVisible();
      expect(requests(service).filter(entry => entry.command === "project_scene_nodes")).toHaveLength(before + 1);
      service.invoke = original;
      await button(page, "View nodes of Paged scene 1").click(); await expect(heading(page, "Nodes 0–0 of 0")).toBeVisible();
      expect(requests(service).filter(entry => entry.command === "project_scene_nodes")).toHaveLength(before + 2);
    });
  });
});

test("bounded observations retain an original committed Review UNKNOWN and later authored text without replay", async ({ page }, info) => {
  await withProjection(page, info, 1, async value => {
    const { service, baseline, bounded, observations, cleanup } = value;
    await page.goto("/"); await expect(page.getByRole("navigation", { name: "Workspaces" })).toBeVisible();
    await button(page, "Review").click();
    const original = service.invoke.bind(service); let armed = true;
    cleanup.push(async () => { service.invoke = original; });
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const result = await original<T>(command, args);
      const change = (args.request as { change?: Change } | undefined)?.change;
      if (armed && command === "apply_change" && change?.type === "add_review") {
        armed = false; value.expected = result as Project; observations.originalCommittedReviewReply = result;
        throw new Error("The original Review reply was lost after the actual SQLite commit");
      }
      return result;
    };
    await page.getByLabel("Review comment", { exact: true }).fill("Original saved uncertain review");
    await button(page, "Add anchored review").click();
    await expect(page.getByRole("status").filter({ hasText: "Original review outcome UNKNOWN" })).toHaveCount(1);
    await page.getByLabel("Review comment", { exact: true }).fill("  Later authored comment\n  kept verbatim  ");
    const ownCopy = () => page.evaluate(scope => {
      const encoded = encodeURIComponent(scope), writer = sessionStorage.getItem("motionwright.review-writer.v1:" + encoded);
      if (!writer) throw new Error("Missing original Review writer");
      const key = "motionwright.review-recovery.v1:" + encoded + ":" + writer, raw = localStorage.getItem(key);
      if (raw === null) throw new Error("Missing original Review bytes");
      return { key, raw, value: JSON.parse(raw) };
    }, JSON.stringify([baseline.id, baseline.generation, baseline.active_branch, "comment", "scene:" + baseline.scenes[0].id]));
    const before = await ownCopy();
    expect(before.value.draft.pending.outcome).toBe("UNKNOWN");
    const applyCount = requests(service).filter(entry => entry.command === "apply_change").length;
    await button(page, "Open project library").click();
    await bounded("same-project pages observe saved result without acknowledgement", async () => {
      await button(page, "Browse pages of " + baseline.title).click();
      await expect(heading(page, baseline.title)).toBeVisible();
      await button(page, "View nodes of Paged scene 1").click(); await expect(heading(page, "Nodes 0–0 of 0")).toBeVisible();
      expect((await ownCopy()).value.draft).toEqual(before.value.draft);
    });
    // This explicit complete open is outside the bounded phase and is retained
    // in the original transcript. It must not acknowledge the earlier request.
    await button(page, "Open editor").click(); await button(page, "Review").click();
    await expect(page.getByLabel("Review comment", { exact: true })).toHaveValue("  Later authored comment\n  kept verbatim  ");
    await expect(button(page, "Add anchored review")).toBeDisabled();
    const after = await ownCopy();
    expect(after.value.draft.pending).toEqual(before.value.draft.pending);
    expect(after.value.draft.pending.outcome).toBe("UNKNOWN");
    expect(requests(service).filter(entry => entry.command === "apply_change")).toHaveLength(applyCount);
    observations.originalUnknownCopy = before; observations.afterExplicitEditorCopy = after;
  });
});

test("a genuinely missing selected head preserves preference and allows an explicit healthy page choice", async ({ page }, info) => {
  await withProjection(page, info, 1, async value => {
    const { service, baseline, bounded, observations } = value, missing = randomUUID();
    await page.addInitScript(({ key, projectId }) => {
      if (!sessionStorage.getItem("projection-missing-arranged")) {
        localStorage.setItem(key, JSON.stringify({ schema: "motionwright.project-selection/1", project_id: projectId }));
        sessionStorage.setItem("projection-missing-arranged", "yes");
      }
    }, { key: SELECTION, projectId: missing });
    await bounded("missing head and explicit healthy choice", async () => {
      await page.goto("/");
      await expect(page.getByRole("alert", { name: "Project open error", exact: true })).toContainText("unavailable");
      expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key)!).project_id, SELECTION)).toBe(missing);
      const request = requests(service).find(entry => entry.command === "project_projection_head"
        && (entry.args.request as { project_id: string }).project_id === missing)!;
      const response = service.transcript.find(entry => entry.direction === "response" && entry.payload.id === request.id)!;
      expect(response.payload).toMatchObject({ id: request.id, ok: false, error: { code: "not_found" } });
      observations.originalMissingHeadResponse = response;
      await button(page, "Open project library").click(); await button(page, "Browse pages of " + baseline.title).click();
      await expect(heading(page, baseline.title)).toBeVisible();
      expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key)!).project_id, SELECTION)).toBe(baseline.id);
    });
  });
});
