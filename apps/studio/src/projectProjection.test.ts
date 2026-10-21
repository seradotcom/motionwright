import { afterEach, describe, expect, it, vi } from "vitest";
import { fixtureBootstrap } from "./fixture";
import {
  openSavedProjectView, ProjectProjectionReadError, readProjectionHead, readProjectionNodes, readProjectionScenes,
  type ProjectionHead, type ProjectionIdentity, type ProjectionNodePage, type ProjectionScenePage, type ProjectionSceneSummary,
} from "./projectProjection";
import type { Project } from "./types";

const uuid = (n: number) => "f1000000-0000-4000-8000-" + n.toString(16).padStart(12, "0");
const identity = (revision = "12"): ProjectionIdentity => ({ project_id: uuid(1), generation: uuid(2), revision });
const head = (scene_count = 1, revision = "12"): ProjectionHead => ({ identity: identity(revision), schema_version: 1, title: "Saved project", scene_count });
const summary = (ordinal = 0, node_count = 1): ProjectionSceneSummary => ({
  ordinal, id: uuid(100 + ordinal), name: "Scene " + (ordinal + 1), start: { num: String(ordinal * 4), den: "1" },
  duration: { num: "4", den: "1" }, renderer: "motion-canvas", status: "draft", beat_count: 0, node_count,
});
function scenes(source: ProjectionHead, start = 0, limit = 64): ProjectionScenePage {
  const end = Math.min(start + limit, source.scene_count);
  return { identity: source.identity, total_scenes: source.scene_count, start_ordinal: start,
    items: Array.from({ length: end - start }, (_, index) => summary(start + index)),
    next_cursor: end < source.scene_count ? { identity: source.identity, next_ordinal: end } : null };
}
function nodes(source = head(), scene = summary(), start = 0, limit = 128): ProjectionNodePage {
  const end = Math.min(start + limit, scene.node_count), ref = { ordinal: scene.ordinal, id: scene.id };
  return { identity: source.identity, scene: ref, total_nodes: scene.node_count, start_ordinal: start,
    items: Array.from({ length: end - start }, (_, index) => ({
      ...structuredClone(fixtureBootstrap.project.scenes[0].nodes[0]), id: uuid(1000 + start + index),
      parent_id: uuid(9999), relations: [{ id: uuid(8888 + index), kind: "follow" as const, target_id: uuid(9998) }],
    })),
    next_cursor: end < scene.node_count ? { identity: source.identity, scene: ref, next_ordinal: end } : null };
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("saved-page contract and exact custody", () => {
  it("preserves the largest native decimal revision through head and cursor", async () => {
    const source = head(65, "18446744073709551615");
    expect(await readProjectionHead(uuid(1), vi.fn(async () => source))).toEqual(source);
    const call = vi.fn(async () => scenes(source));
    const page = await readProjectionScenes(source, null, 64, call);
    expect(page.next_cursor?.identity.revision).toBe("18446744073709551615");
    expect(call).toHaveBeenCalledExactlyOnceWith("project_scene_page", { request: { identity: source.identity, cursor: null, limit: 64 } });
  });
  it.each(["01", "18446744073709551616", "1e3", "-1", 12])("refuses an invalid native revision: %j", async revision => {
    const value = { ...head(), identity: { ...identity(), revision } };
    await expect(readProjectionHead(uuid(1), vi.fn(async () => value))).rejects.toBeInstanceOf(ProjectProjectionReadError);
  });
  it("rejects a different head identity without issuing another read", async () => {
    const call = vi.fn(async () => ({ ...head(), identity: { ...identity(), project_id: uuid(3) } }));
    await expect(readProjectionHead(uuid(1), call)).rejects.toThrow("could not be verified");
    expect(call).toHaveBeenCalledOnce();
  });
  it("requires exact response keys instead of ignoring an unrecognized authority field", async () => {
    await expect(readProjectionHead(uuid(1), vi.fn(async () => ({ ...head(), current_branch: "unrequested" }))))
      .rejects.toBeInstanceOf(ProjectProjectionReadError);
  });
  it("loads only the requested second scene page with the original exact cursor", async () => {
    const source = head(130), cursor = { identity: source.identity, next_ordinal: 64 };
    const call = vi.fn(async () => scenes(source, 64));
    const result = await readProjectionScenes(source, cursor, 64, call);
    expect(call).toHaveBeenCalledOnce(); expect(result.items).toHaveLength(64);
    expect(result.items[0].ordinal).toBe(64); expect(result.next_cursor?.next_ordinal).toBe(128);
    expect(call.mock.calls[0]).toEqual(["project_scene_page", { request: { identity: source.identity, cursor, limit: 64 } }]);
  });
  it.each(["identity", "ordinal", "duplicate", "truncated", "next cursor", "generation"])("rejects a mismatched scene page: %s", async field => {
    const source = head(65), value = scenes(source);
    if (field === "identity") value.identity = { ...identity(), project_id: uuid(3) };
    if (field === "generation") value.identity = { ...identity(), generation: uuid(4) };
    if (field === "ordinal") value.items[0].ordinal = 1;
    if (field === "duplicate") value.items[1].id = value.items[0].id;
    if (field === "truncated") value.items.pop();
    if (field === "next cursor") value.next_cursor!.next_ordinal = 63;
    await expect(readProjectionScenes(source, null, 64, vi.fn(async () => value))).rejects.toThrow("could not be verified");
  });
  it("rejects a cursor from another revision before transport", async () => {
    const call = vi.fn(async () => scenes(head()));
    await expect(readProjectionScenes(head(), { identity: identity("13"), next_ordinal: 0 }, 64, call))
      .rejects.toBeInstanceOf(ProjectProjectionReadError);
    expect(call).not.toHaveBeenCalled();
  });
  it.each([0, 65, 1.5])("rejects an invalid scene limit before transport: %s", async limit => {
    const call = vi.fn(async () => scenes(head()));
    await expect(readProjectionScenes(head(), null, limit, call)).rejects.toBeInstanceOf(ProjectProjectionReadError);
    expect(call).not.toHaveBeenCalled();
  });
  it("retains every original node field and unresolved off-page relationship", async () => {
    const source = head(), scene = summary(0, 260), original = nodes(source, scene);
    const call = vi.fn(async () => original), result = await readProjectionNodes(source, scene, null, 128, call);
    expect(result).toBe(original); expect(result.items).toHaveLength(128);
    expect(result.items[0].parent_id).toBe(uuid(9999));
    expect(result.items[0].relations[0].target_id).toBe(uuid(9998));
    expect(result.items[0].style).toEqual(original.items[0].style);
    expect(call).toHaveBeenCalledExactlyOnceWith("project_scene_nodes", { request: {
      identity: source.identity, scene: { ordinal: 0, id: scene.id }, cursor: null, limit: 128,
    } });
  });
  it.each(["scene", "revision", "duplicate", "next scene", "truncated"])("rejects a wrong or incomplete node page: %s", async field => {
    const source = head(), scene = summary(0, 260), value = nodes(source, scene);
    if (field === "scene") value.scene = { ordinal: 0, id: uuid(101) };
    if (field === "revision") value.identity = identity("13");
    if (field === "duplicate") value.items[1] = { ...value.items[1], id: value.items[0].id };
    if (field === "next scene") value.next_cursor!.scene = { ordinal: 0, id: uuid(101) };
    if (field === "truncated") value.items.pop();
    await expect(readProjectionNodes(source, scene, null, 128, vi.fn(async () => value))).rejects.toBeInstanceOf(ProjectProjectionReadError);
  });
  it("rejects a node cursor for another scene before transport", async () => {
    const source = head(2), scene = summary(0, 260), call = vi.fn(async () => nodes(source, scene));
    await expect(readProjectionNodes(source, scene, { identity: source.identity, scene: { ordinal: 1, id: uuid(101) }, next_ordinal: 128 }, 128, call))
      .rejects.toBeInstanceOf(ProjectProjectionReadError);
    expect(call).not.toHaveBeenCalled();
  });
  it.each([
    { code: "invalid_request_or_content" }, { code: "not_found" }, { code: "generation_changed" }, { code: "unavailable" },
    { code: "too_large", limit: 1048576 }, { code: "stale_revision", expected: "9007199254740993", actual: "9007199254740994" },
  ])("retains a genuine structured read failure without retry: %j", async failure => {
    const call = vi.fn(async () => { throw failure; });
    try { await readProjectionHead(uuid(1), call); throw new Error("Expected failure"); }
    catch (reason) { expect(reason).toBeInstanceOf(ProjectProjectionReadError); expect((reason as ProjectProjectionReadError).failure).toBe(failure); }
    expect(call).toHaveBeenCalledOnce();
  });
  it.each([
    { code: "unavailable", secret: "DO_NOT_DISPLAY" }, { code: "stale_revision", expected: 12, actual: "13" },
    { code: "too_large", limit: -1 }, "DO_NOT_DISPLAY", new Error("DO_NOT_DISPLAY"),
  ])("does not parse or display an unknown failure shape: %j", async failure => {
    try { await readProjectionHead(uuid(1), async () => { throw failure; }); throw new Error("Expected failure"); }
    catch (reason) {
      expect(reason).toBeInstanceOf(ProjectProjectionReadError);
      expect((reason as ProjectProjectionReadError).failure).toBeNull();
      expect((reason as Error).message).not.toContain("DO_NOT_DISPLAY");
    }
  });
  it("fails the complete oversized page instead of truncating its contents", async () => {
    const call = vi.fn(async () => ({ ...head(), title: "x".repeat(1024 * 1024) }));
    await expect(readProjectionHead(uuid(1), call)).rejects.toBeInstanceOf(ProjectProjectionReadError);
    expect(call).toHaveBeenCalledOnce();
  });
});

describe("actual saved-open adapter routing with synthetic transport", () => {
  function transport(count: number, revision = "12", fail?: string) {
    const project: Project = { ...structuredClone(fixtureBootstrap.project), id: uuid(1), generation: uuid(2), revision: 12 };
    project.scenes = Array.from({ length: count }, (_, index) => ({
      ...structuredClone(fixtureBootstrap.project.scenes[0]), id: uuid(100 + index),
    }));
    const source: ProjectionHead = { ...head(count, revision), title: project.title, schema_version: project.schema_version };
    const invoke = vi.fn(async (command: string, _args?: unknown) => {
      if (command === fail) throw { code: "unavailable" };
      if (command === "bootstrap") return { project: null, project_selection: { status: "unselected" },
        native_sdk: { ...fixtureBootstrap.native_sdk, mode: "tauri" } };
      if (command === "project_projection_head") return source;
      if (command === "project_scene_page") return scenes(source);
      if (command === "open_project") return project;
      throw new Error("Unexpected synthetic command");
    });
    vi.stubGlobal("window", { __TAURI_INTERNALS__: { invoke } });
    return { invoke, project, source };
  }
  it("auto opens more than 64 scenes through shell, head and one scene page only", async () => {
    const { invoke } = transport(130, "18446744073709551615"), result = await openSavedProjectView(uuid(1));
    expect(result.kind).toBe("projection");
    expect(invoke.mock.calls.map(call => call[0])).toEqual(["bootstrap", "project_projection_head", "project_scene_page"]);
    expect(invoke.mock.calls[0][1]).toEqual({ request: { project_id: null } });
  });
  it("retains the complete editor at the 64-scene route boundary after its head", async () => {
    const { invoke, project } = transport(64), result = await openSavedProjectView(uuid(1));
    expect(result.kind).toBe("full"); if (result.kind === "full") expect(result.boot.project).toEqual(project);
    expect(invoke.mock.calls.map(call => call[0])).toEqual(["bootstrap", "project_projection_head", "open_project"]);
  });
  it("allows explicit pages for a small project", async () => {
    const { invoke } = transport(2);
    expect((await openSavedProjectView(uuid(1), "pages")).kind).toBe("projection");
    expect(invoke.mock.calls.map(call => call[0])).not.toContain("open_project");
  });
  it("allows an explicit complete editor request for a large project", async () => {
    const { invoke } = transport(65);
    expect((await openSavedProjectView(uuid(1), "editor")).kind).toBe("full");
    expect(invoke.mock.calls.map(call => call[0])).toEqual(["bootstrap", "project_projection_head", "open_project"]);
  });
  it.each(["project_projection_head", "project_scene_page"])("never falls back to a complete read after %s fails", async command => {
    const { invoke } = transport(130, "12", command);
    await expect(openSavedProjectView(uuid(1))).rejects.toBeInstanceOf(ProjectProjectionReadError);
    expect(invoke.mock.calls.map(call => call[0])).not.toContain("open_project");
    expect(invoke.mock.calls.filter(call => call[0] === command)).toHaveLength(1);
  });
  it("does not load an unsafe numeric revision into the complete editor", async () => {
    const { invoke } = transport(2, "9007199254740993");
    await expect(openSavedProjectView(uuid(1))).rejects.toThrow("cannot be represented");
    expect(invoke.mock.calls.map(call => call[0])).toEqual(["bootstrap", "project_projection_head"]);
  });
  it("refuses a complete response that changed after the observed head", async () => {
    const { invoke, project } = transport(2); project.revision++;
    await expect(openSavedProjectView(uuid(1))).rejects.toThrow("could not be verified");
    expect(invoke.mock.calls.filter(call => call[0] === "open_project")).toHaveLength(1);
  });
  it("retains verified shell information when the selected head is unavailable", async () => {
    transport(2, "12", "project_projection_head"); const shell = vi.fn();
    await expect(openSavedProjectView(uuid(1), "auto", shell)).rejects.toBeInstanceOf(ProjectProjectionReadError);
    expect(shell).toHaveBeenCalledOnce(); expect(shell.mock.calls[0][0].mode).toBe("tauri");
  });
  it("does not manufacture a saved view in the browser demo", async () => {
    vi.stubGlobal("window", {});
    await expect(openSavedProjectView(uuid(1), "pages")).rejects.toThrow("local desktop runtime");
  });
});
