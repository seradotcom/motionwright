import { invoke } from "@tauri-apps/api/core";
import { fixtureBootstrap } from "./fixture";
import type { Bootstrap, Change, Project } from "./types";

const isTauri = () => "__TAURI_INTERNALS__" in window;
let browserState = structuredClone(fixtureBootstrap);

export async function bootstrap(): Promise<Bootstrap> {
  if (isTauri()) return invoke<Bootstrap>("bootstrap");
  return structuredClone(browserState);
}

export async function applyChange(project: Project, change: Change): Promise<Project> {
  const requestId = crypto.randomUUID();
  if (isTauri()) {
    return invoke<Project>("apply_change", {
      request: {
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        request_id: requestId,
        change
      }
    });
  }

  const next = structuredClone(project);
  switch (change.type) {
    case "rename_project":
      next.title = change.title;
      break;
    case "add_scene": {
      const start = next.scenes.reduce((total, scene) => total + scene.duration.num / scene.duration.den, 0);
      next.scenes.push({
        id: crypto.randomUUID(),
        name: change.name,
        objective: change.objective,
        start: { num: start, den: 1 },
        duration: { num: change.duration_seconds, den: 1 },
        renderer: "motion-canvas",
        status: "draft",
        beats: [],
        nodes: []
      });
      break;
    }
    case "move_scene": {
      const from = next.scenes.findIndex((scene) => scene.id === change.scene_id);
      if (from >= 0 && change.to_index >= 0 && change.to_index < next.scenes.length) {
        const [scene] = next.scenes.splice(from, 1);
        next.scenes.splice(change.to_index, 0, scene);
        let cursor = 0;
        next.scenes.forEach((entry) => {
          entry.start = { num: cursor, den: 1 };
          cursor += entry.duration.num / entry.duration.den;
        });
      }
      break;
    }
    case "update_scene_objective": {
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (scene) scene.objective = change.objective;
      break;
    }
    case "set_scene_renderer": {
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (scene) scene.renderer = change.renderer;
      break;
    }
    case "add_marker":
      next.markers.push({ id: crypto.randomUUID(), at: change.at, label: change.label });
      break;
    case "set_lock":
      next.locks.push({ id: crypto.randomUUID(), resource: change.resource, kind: change.kind, note: change.note });
      break;
    case "remove_lock":
      next.locks = next.locks.filter((lock) => lock.id !== change.lock_id);
      break;
  }
  next.revision += 1;
  next.updated_at = new Date().toISOString();
  browserState.project = structuredClone(next);
  return next;
}
