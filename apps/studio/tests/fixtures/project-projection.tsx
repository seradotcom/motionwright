import { createRoot } from "react-dom/client";
import App from "../../src/App";
import { fixtureBootstrap } from "../../src/fixture";
import type { Project, Scene } from "../../src/types";
import "../../src/styles.css";

export interface ProjectionFixtureHarness {
  requests(): Array<{ id: number; command: string; args: Record<string, unknown> }>;
  failure(command: string, value: unknown | null): void;
  advance(which: "small" | "large", generation?: boolean): void;
  ids: { small: string; large: string };
}
declare global { interface Window { __PROJECTION_FIXTURE__: ProjectionFixtureHarness } }

// This fixture schedules synthetic invoke replies. It is not SQLite, Tauri,
// real-service execution, a performance sample, or saved-project acceptance.
const parameters = new URLSearchParams(location.search);
const uuid = (ordinal: number) => "f1000000-0000-4000-8000-" + ordinal.toString(16).padStart(12, "0");
const small: Project = { ...structuredClone(fixtureBootstrap.project), id: uuid(1), generation: uuid(2), title: "Small saved project" };
const large: Project = { ...structuredClone(fixtureBootstrap.project), id: uuid(3), generation: uuid(4), title: "Large saved project" };
small.scenes = small.scenes.slice(0, 2);
large.scenes = Array.from({ length: 130 }, (_, ordinal): Scene => ({
  ...structuredClone(small.scenes[0]), id: uuid(100 + ordinal), name: "Saved scene " + (ordinal + 1),
  start: { num: String(ordinal * 4), den: "1" }, duration: { num: "4", den: "1" },
  nodes: Array.from({ length: ordinal === 0 ? 260 : 3 }, (_, nodeIndex) => ({
    ...structuredClone(small.scenes[0].nodes[0]), id: uuid(10000 + ordinal * 300 + nodeIndex),
    name: "Saved node " + (nodeIndex + 1),
    parent_id: ordinal === 0 && nodeIndex === 0 ? uuid(10000 + 200) : null,
    relations: ordinal === 0 && nodeIndex === 0 ? [{ id: uuid(90001), kind: "follow" as const, target_id: uuid(10000 + 201) }] : [],
  })),
}));
const nativeSdk = { ...fixtureBootstrap.native_sdk, mode: "tauri" as const };
const requests: Array<{ id: number; command: string; args: Record<string, unknown> }> = [];
const failures = new Map<string, unknown>();
const identity = (project: Project) => ({ project_id: project.id, generation: project.generation,
  revision: parameters.has("exact") && project.id === large.id ? "18446744073709551615" : String(project.revision) });
const stored = (id: unknown) => {
  const project = [small, large].find(item => item.id === id);
  if (!project) throw { code: "not_found" };
  return project;
};
const check = (input: Record<string, unknown>) => {
  const observed = input.identity as ReturnType<typeof identity>;
  const project = stored(observed.project_id), actual = identity(project);
  if (actual.generation !== observed.generation) throw { code: "generation_changed" };
  if (actual.revision !== observed.revision) throw { code: "stale_revision", expected: observed.revision, actual: actual.revision };
  return project;
};
const invoke = async (command: string, args: Record<string, unknown> = {}): Promise<unknown> => {
  requests.push({ id: requests.length + 1, command, args: structuredClone(args) });
  if (failures.has(command)) throw structuredClone(failures.get(command));
  const request = (args.request ?? {}) as Record<string, unknown>;
  switch (command) {
    case "bootstrap": {
      const selected = request.project_id;
      return { project: selected ? structuredClone(stored(selected)) : null, native_sdk: nativeSdk,
        project_selection: selected ? { status: "selected", project_id: selected } : { status: "unselected" } };
    }
    case "open_project": return structuredClone(stored(request.project_id));
    case "list_projects": return { items: [small, large].map(project => ({
      id: project.id, generation: project.generation, revision: String(project.revision), title: project.title,
      updated_at: "2026-10-21T00:00:00Z",
    })), next_cursor: null, truncated: false };
    case "project_projection_head": {
      const project = stored(request.project_id);
      return { identity: identity(project), schema_version: project.schema_version, title: project.title, scene_count: project.scenes.length };
    }
    case "project_scene_page": {
      const project = check(request), cursor = request.cursor as { next_ordinal: number } | null;
      const start = cursor?.next_ordinal ?? 0, end = Math.min(start + Number(request.limit), project.scenes.length);
      return { identity: identity(project), total_scenes: project.scenes.length, start_ordinal: start,
        items: project.scenes.slice(start, end).map((scene, index) => ({
          ordinal: start + index, id: scene.id, name: scene.name, start: scene.start, duration: scene.duration,
          renderer: scene.renderer, status: scene.status, beat_count: scene.beats.length, node_count: scene.nodes.length,
        })), next_cursor: end < project.scenes.length ? { identity: identity(project), next_ordinal: end } : null };
    }
    case "project_scene_nodes": {
      const project = check(request), ref = request.scene as { ordinal: number; id: string };
      const scene = project.scenes[ref.ordinal];
      if (!scene || scene.id !== ref.id) throw { code: "invalid_request_or_content" };
      const cursor = request.cursor as { next_ordinal: number } | null;
      const start = cursor?.next_ordinal ?? 0, end = Math.min(start + Number(request.limit), scene.nodes.length);
      return { identity: identity(project), scene: ref, total_nodes: scene.nodes.length, start_ordinal: start,
        items: structuredClone(scene.nodes.slice(start, end)),
        next_cursor: end < scene.nodes.length ? { identity: identity(project), scene: ref, next_ordinal: end } : null };
    }
    default: throw new Error("Unsupported synthetic projection fixture command: " + command);
  }
};
window.__PROJECTION_FIXTURE__ = {
  requests: () => structuredClone(requests),
  failure(command, value) { if (value === null) failures.delete(command); else failures.set(command, structuredClone(value)); },
  advance(which, generation = false) { const project = which === "small" ? small : large; project.revision++;
    if (generation) project.generation = uuid(which === "small" ? 50001 : 50002); },
  ids: { small: small.id, large: large.id },
};
if (!parameters.has("demo")) {
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = { invoke };
  const key = "TEST-ONLY.projection-initial:" + location.search;
  if (!sessionStorage.getItem(key)) {
    const selected = parameters.get("initial") === "none" ? null : parameters.get("initial") === "small" ? small.id : large.id;
    if (selected) localStorage.setItem("motionwright.project-library.selection.v1", JSON.stringify({
      schema: "motionwright.project-selection/1", project_id: selected,
    }));
    else localStorage.removeItem("motionwright.project-library.selection.v1");
    sessionStorage.setItem(key, "arranged");
  }
}
const host = document.getElementById("projection-root");
if (!host) throw new Error("Projection fixture root is missing");
createRoot(host).render(<App />);
