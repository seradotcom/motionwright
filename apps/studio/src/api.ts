import { invoke } from "@tauri-apps/api/core";
import { fixtureBootstrap } from "./fixture";
import type {
  Bootstrap,
  BranchState,
  CaptionExportResult,
  Change,
  LockKind,
  PortableBundleExport,
  PortableBundlePlan,
  Project,
  ProjectEvent,
} from "./types";
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

function captureBranchState(project: Project): BranchState {
  return structuredClone({
    scenes: project.scenes,
    markers: project.markers,
    locks: project.locks,
    deliverables: project.deliverables,
    brief: project.brief,
    narrative: project.narrative,
    audio: project.audio,
    visual_language: project.visual_language,
    proposal_sets: project.proposal_sets,
    model_invocations: project.model_invocations,
  });
}

function restoreBranchState(project: Project, state: BranchState) {
  project.scenes = structuredClone(state.scenes);
  project.markers = structuredClone(state.markers);
  project.locks = structuredClone(state.locks);
  project.deliverables = structuredClone(state.deliverables);
  project.brief = structuredClone(state.brief);
  project.narrative = structuredClone(state.narrative);
  project.audio = structuredClone(state.audio);
  project.visual_language = structuredClone(state.visual_language);
  project.proposal_sets = structuredClone(state.proposal_sets);
  project.model_invocations = structuredClone(state.model_invocations);
}

function saveActiveWorkspace(project: Project) {
  const state = captureBranchState(project);
  const branch = project.branches.find((candidate) => candidate.id === project.active_branch);
  if (!branch) throw new Error("active branch is missing");
  const existing = project.branch_workspaces.find((workspace) => workspace.branch_id === branch.id);
  if (existing) {
    existing.current_state = state;
  } else {
    project.branch_workspaces.push({
      branch_id: branch.id,
      base_revision: branch.base_revision,
      base_state: structuredClone(state),
      current_state: state,
    });
  }
}

function branchStateEqual(left: unknown, right: unknown) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function mergeBranchState(base: BranchState, target: BranchState, source: BranchState): BranchState {
  const fields: Array<keyof BranchState> = [
    "scenes", "markers", "locks", "deliverables", "brief", "narrative",
    "audio", "visual_language", "proposal_sets", "model_invocations",
  ];
  const result = structuredClone(target);
  const conflicts: string[] = [];
  for (const field of fields) {
    const baseValue = base[field];
    const targetValue = target[field];
    const sourceValue = source[field];
    if (branchStateEqual(sourceValue, baseValue) || branchStateEqual(sourceValue, targetValue)) continue;
    if (branchStateEqual(targetValue, baseValue)) {
      Object.assign(result, { [field]: structuredClone(sourceValue) });
    } else {
      conflicts.push(field);
    }
  }
  if (conflicts.length) throw new Error("semantic merge conflict in: " + conflicts.join(", "));
  return result;
}

export async function bootstrap(): Promise<Bootstrap> {
  if (isTauri()) return invoke<Bootstrap>("bootstrap");
  return structuredClone(browserState);
}

export async function importAssetFile(
  project: Project,
  path: string,
  name?: string,
  mediaType?: string,
): Promise<Project> {
  if (!isTauri()) {
    throw new Error("Local asset import requires the Motionwright desktop runtime.");
  }
  return invoke<Project>("import_asset_file", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      path,
      name: name ?? null,
      media_type: mediaType ?? null,
    },
  });
}

export async function exportProjectBundle(
  project: Project,
  destination: string,
): Promise<PortableBundleExport> {
  if (!isTauri()) {
    throw new Error("Portable project bundles require the Motionwright desktop runtime.");
  }
  return invoke<PortableBundleExport>("export_project_bundle", {
    request: {
      project_id: project.id,
      path: destination,
    },
  });
}

export async function inspectProjectBundle(path: string): Promise<PortableBundlePlan> {
  if (!isTauri()) {
    throw new Error("Portable project bundles require the Motionwright desktop runtime.");
  }
  return invoke<PortableBundlePlan>("inspect_project_bundle", {
    request: { path },
  });
}

export async function importProjectBundle(path: string): Promise<Project> {
  if (!isTauri()) {
    throw new Error("Portable project bundles require the Motionwright desktop runtime.");
  }
  return invoke<Project>("import_project_bundle", {
    request: { path },
  });
}

export async function exportCaptionSidecar(
  project: Project,
  profileId: string,
  path: string,
): Promise<CaptionExportResult> {
  if (!isTauri()) {
    throw new Error("Caption sidecar export requires the Motionwright desktop runtime.");
  }
  return invoke<CaptionExportResult>("export_caption_sidecar", {
    request: {
      project_id: project.id,
      profile_id: profileId,
      path,
    },
  });
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
  next.branch_workspaces ??= [];
  next.reviews ??= [];
  next.merges ??= [];
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
    case "add_asset":
      assertUnlocked(next, projectResource(next), ["content"]);
      if (next.assets.some((asset) => asset.id === change.asset.id)) {
        throw new Error("duplicate asset id");
      }
      next.assets.push(structuredClone(change.asset));
      break;
    case "remove_asset": {
      assertUnlocked(next, projectResource(next), ["content"]);
      if (
        next.audio.voice_tracks.some((track) => track.asset_id === change.asset_id) ||
        next.brief.claims.some((claim) => claim.source?.kind === "asset" && claim.source.asset_id === change.asset_id)
      ) {
        throw new Error("asset is still referenced by project state");
      }
      if (!next.assets.some((asset) => asset.id === change.asset_id)) {
        throw new Error("resource not found: asset:" + change.asset_id);
      }
      next.assets = next.assets.filter((asset) => asset.id !== change.asset_id);
      break;
    }
    case "upsert_deliverable": {
      assertUnlocked(next, projectResource(next), ["content"]);
      const profile = structuredClone(change.profile);
      if (
        !profile.name.trim() || profile.name.length > 160 ||
        !profile.language.trim() || profile.language.length > 64 ||
        !Number.isInteger(profile.width) || !Number.isInteger(profile.height) ||
        profile.width <= 0 || profile.height <= 0 || profile.width > 16384 || profile.height > 16384 ||
        ![44100, 48000, 96000].includes(profile.audio_sample_rate_hz)
      ) throw new Error("deliverable profile is invalid");
      const duplicate = next.deliverables.find(
        (candidate) => candidate.id !== profile.id && candidate.name.trim().toLowerCase() === profile.name.trim().toLowerCase(),
      );
      if (duplicate) throw new Error("deliverable profile name is already used");
      const index = next.deliverables.findIndex((candidate) => candidate.id === profile.id);
      if (index >= 0) next.deliverables[index] = profile;
      else {
        if (next.deliverables.length >= 128) throw new Error("too many deliverable profiles");
        next.deliverables.push(profile);
      }
      break;
    }
    case "remove_deliverable": {
      assertUnlocked(next, projectResource(next), ["content"]);
      if (next.deliverables.length <= 1) throw new Error("a project must keep at least one deliverable profile");
      if (next.reviews.some((review) => review.anchor.profile_id === change.profile_id)) {
        throw new Error("deliverable profile is referenced by review history");
      }
      if (!next.deliverables.some((profile) => profile.id === change.profile_id)) {
        throw new Error("resource not found: deliverable:" + change.profile_id);
      }
      next.deliverables = next.deliverables.filter((profile) => profile.id !== change.profile_id);
      break;
    }
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
    case "create_branch": {
      const name = change.name.trim();
      if (!name || name.length > 120 || next.branches.some((branch) => branch.name.toLowerCase() === name.toLowerCase())) {
        throw new Error("branch name is invalid or already used");
      }
      saveActiveWorkspace(next);
      const branchId = crypto.randomUUID();
      const state = captureBranchState(next);
      next.branches.push({
        id: branchId,
        name,
        parent_branch: next.active_branch,
        base_revision: next.revision,
        head_revision: next.revision,
        protected: false,
        created_at: new Date().toISOString(),
      });
      next.branch_workspaces.push({
        branch_id: branchId,
        base_revision: next.revision,
        base_state: structuredClone(state),
        current_state: state,
      });
      break;
    }
    case "checkout_branch": {
      if (change.branch_id === next.active_branch) break;
      saveActiveWorkspace(next);
      const workspace = next.branch_workspaces.find((entry) => entry.branch_id === change.branch_id);
      if (!workspace) throw new Error("resource not found: branch:" + change.branch_id);
      restoreBranchState(next, workspace.current_state);
      next.active_branch = change.branch_id;
      break;
    }
    case "merge_branch": {
      if (change.source_branch_id === next.active_branch) throw new Error("cannot merge a branch into itself");
      saveActiveWorkspace(next);
      const sourceBranch = next.branches.find((branch) => branch.id === change.source_branch_id);
      if (!sourceBranch) throw new Error("resource not found: branch:" + change.source_branch_id);
      if (sourceBranch.parent_branch !== next.active_branch) {
        throw new Error("first-party merge currently requires the source branch to descend directly from the active target");
      }
      const sourceWorkspace = next.branch_workspaces.find((entry) => entry.branch_id === change.source_branch_id);
      if (!sourceWorkspace) throw new Error("resource not found: branch:" + change.source_branch_id);
      const merged = mergeBranchState(
        sourceWorkspace.base_state,
        captureBranchState(next),
        sourceWorkspace.current_state,
      );
      restoreBranchState(next, merged);
      next.merges.push({
        id: crypto.randomUUID(),
        source_branch: change.source_branch_id,
        target_branch: next.active_branch,
        base_revision: sourceWorkspace.base_revision,
        committed_revision: null,
        merged_at: new Date().toISOString(),
      });
      break;
    }
    case "add_review":
      next.reviews.push({
        id: crypto.randomUUID(),
        kind: change.kind,
        anchor: {
          resource: change.resource,
          branch_id: next.active_branch,
          revision: next.revision,
          start: structuredClone(change.start),
          end: structuredClone(change.end),
          locale: change.locale,
          profile_id: change.profile_id,
        },
        body: change.body,
        status: "open",
        resolution: null,
        created_at: new Date().toISOString(),
        resolved_at: null,
      });
      break;
    case "resolve_review": {
      const review = next.reviews.find((entry) => entry.id === change.review_id);
      if (!review) throw new Error("resource not found: review:" + change.review_id);
      if (!change.resolution.trim()) throw new Error("review resolution is out of bounds");
      review.status = "resolved";
      review.resolution = change.resolution;
      review.resolved_at = new Date().toISOString();
      break;
    }
    case "reopen_review": {
      const review = next.reviews.find((entry) => entry.id === change.review_id);
      if (!review) throw new Error("resource not found: review:" + change.review_id);
      review.status = "needs_recheck";
      review.resolution = null;
      review.resolved_at = null;
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
  const activeBranch = next.branches.find((branch) => branch.id === next.active_branch);
  if (!activeBranch) throw new Error("active branch is missing");
  activeBranch.head_revision = next.revision;

  if (change.type === "merge_branch") {
    const pending = [...next.merges].reverse().find(
      (merge) => merge.target_branch === next.active_branch && merge.committed_revision === null,
    );
    if (pending) pending.committed_revision = next.revision;
  }

  const reviewPreservingChanges = new Set<Change["type"]>([
    "create_branch",
    "checkout_branch",
    "add_review",
    "resolve_review",
    "reopen_review",
    "set_lock",
    "remove_lock",
  ]);
  if (!reviewPreservingChanges.has(change.type)) {
    next.reviews.forEach((review) => {
      if (
        review.anchor.branch_id === next.active_branch &&
        review.anchor.revision < next.revision &&
        review.status === "resolved"
      ) {
        review.status = "needs_recheck";
        review.resolution = null;
        review.resolved_at = null;
      }
    });
  }

  next.updated_at = new Date().toISOString();
  browserEvents.push({
    revision: next.revision,
    change: structuredClone(change),
    created_at: next.updated_at,
  });
  browserState.project = structuredClone(next);
  return next;
}
