import { invoke } from "@tauri-apps/api/core";
import { fixtureBootstrap } from "./fixture";
import type { Bootstrap, Change, LockKind, Project, ProjectEvent } from "./types";
import { rationalSeconds } from "./types";

const isTauri = () => "__TAURI_INTERNALS__" in window;
let browserState = structuredClone(fixtureBootstrap);
const browserEvents: ProjectEvent[] = [];

const projectResource = (project: Project) => "project:" + project.id;
const sceneResource = (sceneId: string) => "scene:" + sceneId;

function assertUnlocked(project: Project, resource: string, kinds: LockKind[]) {
  const hit = project.locks.find((lock) => lock.resource === resource && kinds.includes(lock.kind));
  if (hit) throw new Error(`resource is locked: ${resource} (${hit.kind})`);
}

export async function bootstrap(): Promise<Bootstrap> {
  if (isTauri()) return invoke<Bootstrap>("bootstrap");
  return structuredClone(browserState);
}

export async function projectHistory(
  project: Project,
  afterRevision = 0,
  limit = 100,
): Promise<ProjectEvent[]> {
  if (isTauri()) {
    return invoke<ProjectEvent[]>("project_history", {
      request: {
        project_id: project.id,
        after_revision: afterRevision,
        limit,
      },
    });
  }
  return structuredClone(
    browserEvents
      .filter((event) => event.revision > afterRevision)
      .slice(0, Math.max(1, Math.min(limit, 256))),
  );
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
      assertUnlocked(next, projectResource(next), ["content"]);
      next.title = change.title;
      break;
    case "set_brief":
      assertUnlocked(next, projectResource(next), ["content"]);
      next.brief = {
        ...next.brief,
        objective: change.objective,
        audience: change.audience,
        constraints: change.constraints,
        exclusions: change.exclusions,
      };
      break;
    case "set_narrative_premise":
      assertUnlocked(next, projectResource(next), ["content"]);
      next.narrative.premise = change.premise;
      break;
    case "add_scene": {
      assertUnlocked(next, projectResource(next), ["content", "timing"]);
      const start = next.scenes.reduce((total, scene) => total + Number(scene.duration.num) / Number(scene.duration.den), 0);
      next.scenes.push({
        id: crypto.randomUUID(),
        name: change.name,
        objective: change.objective,
        start: { num: String(start), den: "1" },
        duration: { num: String(change.duration_seconds), den: "1" },
        renderer: "motion-canvas",
        status: "draft",
        beats: [],
        nodes: [],
        camera: { center_x: 960, center_y: 540, zoom: 1, rotation_deg: 0, safe_margin: 0.05 },
      });
      break;
    }
    case "move_scene": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content", "timing"]);
      const from = next.scenes.findIndex((scene) => scene.id === change.scene_id);
      if (from >= 0 && change.to_index >= 0 && change.to_index < next.scenes.length) {
        const [scene] = next.scenes.splice(from, 1);
        next.scenes.splice(change.to_index, 0, scene);
        let cursor = 0;
        next.scenes.forEach((entry) => {
          entry.start = { num: String(cursor), den: "1" };
          cursor += Number(entry.duration.num) / Number(entry.duration.den);
        });
      }
      break;
    }
    case "update_scene_objective": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (scene) scene.objective = change.objective;
      break;
    }
    case "set_scene_renderer": {
      assertUnlocked(next, sceneResource(change.scene_id), ["renderer"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (scene) scene.renderer = change.renderer;
      break;
    }
    case "set_scene_status": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (scene) scene.status = change.status;
      break;
    }
    case "set_scene_duration": {
      assertUnlocked(next, sceneResource(change.scene_id), ["timing"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (!scene) throw new Error("resource not found: scene:" + change.scene_id);
      const duration = Number(change.duration.num) / Number(change.duration.den);
      if (!Number.isFinite(duration) || duration <= 0 || duration > 86_400) {
        throw new Error("scene duration is out of bounds");
      }
      scene.duration = structuredClone(change.duration);
      let cursor = 0;
      next.scenes.forEach((entry) => {
        entry.start = rationalSeconds(cursor);
        cursor += Number(entry.duration.num) / Number(entry.duration.den);
      });
      break;
    }
    case "add_canvas_node": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content", "position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (!scene) throw new Error("resource not found: scene:" + change.scene_id);
      if (!change.node.name.trim() || !change.node.kind.trim()) throw new Error("canvas node identity is invalid");
      if (scene.nodes.some((node) => node.id === change.node.id)) throw new Error("canvas node identity already exists in scene");
      scene.nodes.push(structuredClone(change.node));
      break;
    }
    case "remove_canvas_node": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content", "position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (!scene) throw new Error("resource not found: scene:" + change.scene_id);
      if (!scene.nodes.some((node) => node.id === change.node_id)) throw new Error("resource not found: node:" + change.node_id);
      if (scene.nodes.some((node) => node.parent_id === change.node_id)) throw new Error("canvas node still has children; reparent them before removal");
      if (scene.nodes.some((node) => node.relations.some((relation) => relation.target_id === change.node_id))) {
        throw new Error("canvas node still has incoming relations; remove them before removal");
      }
      scene.nodes = scene.nodes.filter((node) => node.id !== change.node_id);
      break;
    }
    case "transform_canvas_node": {
      assertUnlocked(next, sceneResource(change.scene_id), ["position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      const node = scene?.nodes.find((entry) => entry.id === change.node_id);
      if (!node) throw new Error("resource not found: node:" + change.node_id);
      const changesPosition = node.x !== change.transform.x || node.y !== change.transform.y;
      const changesSize = node.width !== change.transform.width || node.height !== change.transform.height;
      const changesRotation = node.rotation_deg !== change.transform.rotation_deg;
      const changesOpacity = node.opacity !== change.transform.opacity;
      if (
        (changesPosition && node.property_locks.includes("position")) ||
        (changesSize && node.property_locks.includes("size")) ||
        (changesRotation && node.property_locks.includes("rotation")) ||
        (changesOpacity && node.property_locks.includes("opacity"))
      ) throw new Error("resource is locked: node transform property");
      Object.assign(node, change.transform);
      break;
    }
    case "update_canvas_text": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content"]);
      const node = next.scenes.find((entry) => entry.id === change.scene_id)?.nodes.find((entry) => entry.id === change.node_id);
      if (!node) throw new Error("resource not found: node:" + change.node_id);
      if (node.property_locks.includes("text")) throw new Error("resource is locked: node text");
      node.text = change.text;
      break;
    }
    case "update_canvas_style": {
      assertUnlocked(next, sceneResource(change.scene_id), ["style"]);
      const node = next.scenes.find((entry) => entry.id === change.scene_id)?.nodes.find((entry) => entry.id === change.node_id);
      if (!node) throw new Error("resource not found: node:" + change.node_id);
      if (node.property_locks.includes("style")) throw new Error("resource is locked: node style");
      node.style = structuredClone(change.style);
      break;
    }
    case "reparent_canvas_node": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content", "position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (!scene) throw new Error("resource not found: scene:" + change.scene_id);
      if (change.parent_id && (change.parent_id === change.node_id || !scene.nodes.some((node) => node.id === change.parent_id))) {
        throw new Error("canvas parent must be another node in the scene");
      }
      const node = scene.nodes.find((entry) => entry.id === change.node_id);
      if (!node) throw new Error("resource not found: node:" + change.node_id);
      if (node.property_locks.includes("parent") || node.property_locks.includes("order")) throw new Error("resource is locked: node hierarchy");
      node.parent_id = change.parent_id;
      node.z_index = change.z_index;
      break;
    }
    case "set_canvas_relations": {
      assertUnlocked(next, sceneResource(change.scene_id), ["position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (!scene) throw new Error("resource not found: scene:" + change.scene_id);
      if (change.relations.length > 128) throw new Error("too many canvas relations");
      if (change.relations.some((relation) => relation.target_id === change.node_id || !scene.nodes.some((node) => node.id === relation.target_id))) {
        throw new Error("canvas relation target is invalid");
      }
      const node = scene.nodes.find((entry) => entry.id === change.node_id);
      if (!node) throw new Error("resource not found: node:" + change.node_id);
      if (node.property_locks.includes("position")) throw new Error("resource is locked: node relations");
      node.relations = structuredClone(change.relations);
      break;
    }
    case "set_node_property_lock": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content"]);
      const node = next.scenes.find((entry) => entry.id === change.scene_id)?.nodes.find((entry) => entry.id === change.node_id);
      if (!node) throw new Error("resource not found: node:" + change.node_id);
      if (change.locked && !node.property_locks.includes(change.property)) node.property_locks.push(change.property);
      if (!change.locked) node.property_locks = node.property_locks.filter((property) => property !== change.property);
      break;
    }
    case "set_camera": {
      assertUnlocked(next, sceneResource(change.scene_id), ["position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (!scene) throw new Error("resource not found: scene:" + change.scene_id);
      scene.camera = structuredClone(change.camera);
      break;
    }
    case "add_marker":
      assertUnlocked(next, projectResource(next), ["timing"]);
      next.markers.push({ id: crypto.randomUUID(), at: change.at, label: change.label });
      break;
    case "set_visual_language":
      assertUnlocked(next, projectResource(next), ["style"]);
      next.visual_language = structuredClone(change.visual_language);
      break;
    case "add_proposal_set":
      if (change.proposal_set.base_revision !== next.revision) {
        throw new Error("proposal set base does not match current project revision");
      }
      next.proposal_sets.push(structuredClone(change.proposal_set));
      break;
    case "select_proposal": {
      const set = next.proposal_sets.find((entry) => entry.id === change.proposal_set_id);
      if (!set) throw new Error("resource not found: proposal-set:" + change.proposal_set_id);
      if (set.base_revision + 1 !== next.revision) throw new Error("proposal set is stale for current project revision");
      if (!set.proposals.some((proposal) => proposal.id === change.proposal_id)) {
        throw new Error("resource not found: proposal:" + change.proposal_id);
      }
      set.selected = change.proposal_id;
      break;
    }
    case "set_lock":
      if (!next.locks.some((lock) => lock.resource === change.resource && lock.kind === change.kind)) {
        next.locks.push({ id: crypto.randomUUID(), resource: change.resource, kind: change.kind, note: change.note });
      }
      break;
    case "remove_lock":
      if (!next.locks.some((lock) => lock.id === change.lock_id)) throw new Error("resource not found: lock:" + change.lock_id);
      next.locks = next.locks.filter((lock) => lock.id !== change.lock_id);
      break;
  }
  next.revision += 1;
  next.updated_at = new Date().toISOString();
  browserEvents.push({
    revision: next.revision,
    change: structuredClone(change),
    created_at: next.updated_at,
  });
  browserState.project = structuredClone(next);
  return next;
}
