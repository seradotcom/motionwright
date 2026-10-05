export type ProjectState = "current" | "stale" | "unknown";
export type SceneStatus = "draft" | "review" | "approved" | "needs_work";
export type RendererKind =
  | "motion-canvas"
  | "mlt"
  | "blender"
  | "manim-community"
  | "remotion"
  | "manim-gl";
export type LockKind = "content" | "timing" | "position" | "style" | "renderer";

export interface RationalTime { num: string; den: string; }
export interface Beat {
  id: string; label: string; objective: string;
  start: RationalTime; duration: RationalTime;
}
export interface CanvasNode {
  id: string; name: string; kind: string; parent_id: string | null;
  x: number; y: number; width: number; height: number;
  rotation_deg: number; opacity: number; text: string | null;
}
export interface Scene {
  id: string; name: string; objective: string;
  start: RationalTime; duration: RationalTime;
  renderer: RendererKind; status: SceneStatus;
  beats: Beat[]; nodes: CanvasNode[];
}
export interface Marker { id: string; at: RationalTime; label: string; }
export interface ProjectLock {
  id: string; resource: string; kind: LockKind; note: string;
}
export interface DeliverableProfile {
  id: string; name: string; width: number; height: number;
  language: string; captions: boolean;
}
export interface Branch {
  id: string; name: string; base_revision: number; created_at: string;
}
export interface Project {
  schema_version: number; id: string; generation: string; revision: number;
  title: string; state: ProjectState; active_branch: string;
  branches: Branch[]; scenes: Scene[]; markers: Marker[];
  assets: Array<{
    id: string; name: string; media_type: string;
    content_sha256: string | null; source_revision: string | null;
  }>;
  locks: ProjectLock[]; deliverables: DeliverableProfile[]; updated_at: string;
}
export type Change =
  | { type: "rename_project"; title: string }
  | { type: "add_scene"; name: string; objective: string; duration_seconds: number }
  | { type: "move_scene"; scene_id: string; to_index: number }
  | { type: "update_scene_objective"; scene_id: string; objective: string }
  | { type: "set_scene_renderer"; scene_id: string; renderer: RendererKind }
  | { type: "add_marker"; at: RationalTime; label: string }
  | { type: "set_lock"; resource: string; kind: LockKind; note: string }
  | { type: "remove_lock"; lock_id: string };
export interface Bootstrap {
  project: Project;
  native_sdk: {
    application: string;
    pinned_revision: string;
    mode: "tauri" | "browser-demo";
  };
}
export const seconds = (time: RationalTime): number => Number(time.num) / Number(time.den);
