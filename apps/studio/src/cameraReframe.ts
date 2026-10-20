/** Closed, source-owned planar camera intent. Rust Project/StudioService is
 * authoritative; an editorial sample is never native render evidence. */
import type { CameraState, Change, DeliverableProfile, Project, RendererKind, Scene } from "./types";
import { emptyProductionDesign } from "./creativeProduction";
import { seconds } from "./types";

/** Legacy records stay V1. V2 is the closed Manim Community pair. */
export const CAMERA_REFRAME_VERSION = 1;
export const CAMERA_REFRAME_MANIM_VERSION = 2;
export const CAMERA_REFRAME_FPS = 30;
export const CAMERA_REFRAME_MAX = 16;
export interface CameraReframe {
  id: string; scene_id: string; component_version: number;
  start_center_x: number; start_center_y: number;
  end_center_x: number; end_center_y: number;
  duration_frames: number;
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const KEYS = ["id", "scene_id", "component_version", "start_center_x", "start_center_y",
  "end_center_x", "end_center_y", "duration_frames"];
export const CAMERA_DRAFT_FIELDS = ["start_center_x", "start_center_y", "end_center_x",
  "end_center_y", "duration_frames"] as const;
export type CameraDraftField = typeof CAMERA_DRAFT_FIELDS[number];
function invalid(message: string): never { throw new Error("CameraReframe: " + message); }
function exactKeys(value: object, keys: readonly string[]): boolean {
  return Object.keys(value).sort().join("|") === [...keys].sort().join("|");
}
export function sameCameraReframe(left: CameraReframe, right: CameraReframe): boolean {
  return KEYS.every(key => left[key as keyof CameraReframe] === right[key as keyof CameraReframe]);
}
export function cameraReframeVersion(renderer: RendererKind): number | null {
  return renderer === "motion-canvas" ? CAMERA_REFRAME_VERSION :
    renderer === "manim-community" ? CAMERA_REFRAME_MANIM_VERSION : null;
}
export function validateCameraReframe(source: CameraReframe, scene: Scene): void {
  if (!source || typeof source !== "object" || Array.isArray(source) || !exactKeys(source, KEYS))
    invalid("unknown or missing source parameters");
  if (!UUID.test(source.id) || !UUID.test(source.scene_id) || source.scene_id !== scene.id ||
    ![CAMERA_REFRAME_VERSION, CAMERA_REFRAME_MANIM_VERSION].includes(source.component_version))
    invalid("invalid identity, scene or version");
  for (const [label, value, min, max] of [
    ["start x", source.start_center_x, 720, 1200], ["end x", source.end_center_x, 720, 1200],
    ["start y", source.start_center_y, 320, 760], ["end y", source.end_center_y, 320, 760],
  ] as const) {
    if (!Number.isFinite(value) || value < min || value > max)
      invalid(label + " is outside the admitted source canvas");
  }
  if (source.component_version !== cameraReframeVersion(scene.renderer))
    invalid("version 1 requires Motion Canvas; version 2 requires Manim Community");
  if (scene.nodes.length < 1 || scene.nodes.length > 32 || scene.beats.length ||
    scene.nodes.some(node => node.keyframes.length || node.parent_id !== null || node.kind === "group"))
    invalid("an original, ungrouped and motion-free Canvas shot with at most 32 objects is required");
  if (scene.camera.zoom !== 1 || scene.camera.rotation_deg !== 0)
    invalid("this planar reframe cannot represent optical zoom or 3D rotation");
  if (!Number.isFinite(scene.camera.safe_margin)) invalid("the camera safe margin must be finite");
  if (source.component_version === CAMERA_REFRAME_MANIM_VERSION) {
    if (scene.camera.safe_margin < .05) invalid("Manim camera requires a safe margin of at least 5%");
    for (const node of scene.nodes) {
      const style = node.style;
      if (!["rectangle", "circle"].includes(node.kind) || node.coordinate_space !== "project_pixels" ||
        node.relations.length || node.opacity !== 1 || node.rotation_deg !== 0 || node.text !== null ||
        style.stroke !== null || style.stroke_width !== 0 || style.blend_mode !== "normal" ||
        style.font_family !== null || style.font_size !== null || style.font_weight !== null ||
        style.line_height !== null || (node.kind === "circle" && node.width !== node.height))
        invalid("Manim camera cannot preserve the authored shape semantics of object " + node.name +
          "; use unrotated, opaque rectangles or circles without text, strokes, relations or font styling");
      if (typeof style.fill !== "string" || !/^#[0-9a-f]{6}$/i.test(style.fill))
        invalid("Manim camera requires an explicit opaque #RRGGBB fill for object " + node.name);
    }
  }
  const dx = source.end_center_x - source.start_center_x;
  const dy = source.end_center_y - source.start_center_y;
  if (Math.abs(dx) > 80 || Math.abs(dy) > 80 || (Math.abs(dx) < .001 && Math.abs(dy) < .001))
    invalid("camera movement must be visible and at most 80 source pixels per axis");
  // Compare exact rational spans, including a one-sample-short authored scene.
  let fits = false;
  try {
    const numerator = BigInt(scene.duration.num), denominator = BigInt(scene.duration.den);
    fits = denominator > 0n && numerator >= 0n && Number.isInteger(source.duration_frames) &&
      BigInt(source.duration_frames) * denominator <= numerator * BigInt(CAMERA_REFRAME_FPS);
  } catch { /* Invalid clocks fail closed before creating a source intent. */ }
  if (!Number.isInteger(source.duration_frames) || source.duration_frames < 12 ||
    source.duration_frames > 180 || !fits)
    invalid("camera duration must fit 12–180 exact frames inside the authored scene");
  if (scene.camera.center_x !== source.end_center_x || scene.camera.center_y !== source.end_center_y)
    invalid("terminal camera center must match the authored Scene camera");
  const marginX = 1920 * Math.max(.05, scene.camera.safe_margin);
  const marginY = 1080 * Math.max(.05, scene.camera.safe_margin);
  for (const node of scene.nodes) {
    if (![node.x, node.y, node.width, node.height].every(Number.isFinite) || node.width <= 0 || node.height <= 0)
      invalid("original Canvas object dimensions must be finite and positive");
    for (const [cx, cy] of [[source.start_center_x, source.start_center_y], [source.end_center_x, source.end_center_y]]) {
      const x = node.x + 960 - cx, y = node.y + 540 - cy;
      if (x < marginX || y < marginY || x + node.width > 1920 - marginX || y + node.height > 1080 - marginY)
        invalid("original Canvas node " + node.id + " is cropped by this pan");
    }
  }
}
export function validateCameraReframes(project: Project): void {
  const moves = project.production_design?.camera_reframes ?? [];
  if (moves.length > CAMERA_REFRAME_MAX) invalid("project move count exceeds 16");
  const ids = new Set<string>(), scenes = new Set<string>();
  for (const move of moves) {
    if (ids.has(move.id) || scenes.has(move.scene_id)) invalid("duplicate camera or scene identity");
    ids.add(move.id); scenes.add(move.scene_id);
    const scene = project.scenes.find(scene => scene.id === move.scene_id);
    if (!scene) invalid("camera references an absent scene");
    validateCameraReframe(move, scene);
  }
}
type CameraChange = Extract<Change, { type: "upsert_camera_reframe" | "detach_camera_reframe" }>;
export function applyCameraReframeChange(project: Project, change: CameraChange): void {
  const design = structuredClone(project.production_design ?? emptyProductionDesign());
  const moves = design.camera_reframes ?? [];
  if (change.type === "upsert_camera_reframe") {
    const { reframe } = change;
    const scene = project.scenes.find(scene => scene.id === reframe.scene_id);
    if (!scene) invalid("scene not found");
    if (project.locks.some(lock => ["project:" + project.id, "scene:" + scene.id].includes(lock.resource) &&
      ["content", "timing", "position", "renderer"].includes(lock.kind)))
      invalid("source scene or project is locked");
    const index = moves.findIndex(existing => existing.id === reframe.id);
    if (index >= 0 && moves[index].scene_id !== scene.id) invalid("camera identity cannot move to another scene");
    if (index < 0 && (moves.length >= CAMERA_REFRAME_MAX || moves.some(existing => existing.scene_id === scene.id)))
      invalid("only one camera move per scene or up to 16 per project");
    const camera = { ...scene.camera, center_x: reframe.end_center_x, center_y: reframe.end_center_y };
    validateCameraReframe(reframe, { ...scene, camera });
    if (index >= 0) moves[index] = structuredClone(reframe);
    else moves.push(structuredClone(reframe));
    // Publish only after all checks, matching the atomic domain change.
    scene.camera = camera;
    scene.status = "draft";
    design.camera_reframes = moves;
  } else {
    const found = moves.find(move => move.id === change.id);
    if (!found) invalid("source camera move not found");
    if (project.locks.some(lock => ["project:" + project.id, "scene:" + found.scene_id].includes(lock.resource) &&
      ["content", "timing"].includes(lock.kind))) invalid("source camera move is locked");
    design.camera_reframes = moves.filter(move => move.id !== change.id);
  }
  project.production_design = design;
}

/** An editorial sample at i/30 has progress i/D. D is the endpoint, held
 * afterward; it is not an extra media frame when scene length equals D. */
export function cameraReframePose(move: CameraReframe, timeSeconds: number, safeMargin = .05): CameraState | null {
  if (![timeSeconds, safeMargin, move.start_center_x, move.start_center_y, move.end_center_x,
    move.end_center_y, move.duration_frames].every(Number.isFinite) || move.duration_frames <= 0 ||
    !Number.isInteger(move.duration_frames) || ![1, 2].includes(move.component_version)) return null;
  const progress = Math.max(0, Math.min(1, timeSeconds * CAMERA_REFRAME_FPS / move.duration_frames));
  return { center_x: move.start_center_x + (move.end_center_x - move.start_center_x) * progress,
    center_y: move.start_center_y + (move.end_center_y - move.start_center_y) * progress,
    zoom: 1, rotation_deg: 0, safe_margin: safeMargin };
}
export function cameraPreviewOffset(move: CameraReframe, timeSeconds: number): { x: number; y: number } {
  const camera = cameraReframePose(move, timeSeconds);
  if (!camera) invalid("cannot preview a non-finite or unsupported camera sample");
  return { x: 960 - camera.center_x, y: 540 - camera.center_y };
}
export function cameraSceneFrameCount(scene: Scene): number | null {
  try {
    const n = BigInt(scene.duration.num) * BigInt(CAMERA_REFRAME_FPS), d = BigInt(scene.duration.den);
    if (n <= 0n || d <= 0n || n % d !== 0n || n / d > BigInt(Number.MAX_SAFE_INTEGER)) return null;
    return Number(n / d);
  } catch { return null; }
}
/** These are source constraints, not an admission receipt or runtime status. */
export function manimCameraRenderNotes(project: Project, scene: Scene, profile?: DeliverableProfile | null): string[] {
  if (scene.renderer !== "manim-community") return [];
  const notes: string[] = [];
  if (!profile || profile.width !== 640 || profile.height !== 360 || profile.frame_rate.num !== "30" ||
    profile.frame_rate.den !== "1" || profile.container !== "mp4" || profile.video_codec !== "h264" ||
    profile.color_space !== "rec709") notes.push("Select a saved 640 × 360, 30 fps, Rec.709 H.264 MP4 profile for this Manim contribution.");
  if (profile?.included_scene_ids.length && !profile.included_scene_ids.includes(scene.id))
    notes.push("The selected profile excludes this scene.");
  const palette = project.visual_language.palette;
  if ((palette.find(row => row.name === "background") ?? palette.find(row => row.name === "surface"))?.value !== "#000000")
    notes.push("The Manim contribution requires an authored opaque black background (#000000).");
  const count = cameraSceneFrameCount(scene);
  let exactStart = false;
  try { exactStart = BigInt(scene.start.den) > 0n && BigInt(scene.start.num) * 30n % BigInt(scene.start.den) === 0n; } catch { /* fail closed */ }
  if (!exactStart || count === null || count > 180) notes.push("The Manim contribution requires an exact 30 fps scene start and 1–180 scene frames.");
  const design = project.production_design;
  if ((project.audio.sfx_cues?.length ?? 0) || (design?.measured_bindings?.length ?? 0) ||
    [design?.heroes, design?.stages, design?.procedural_fields].some(rows => rows?.some(row => row.scene_id === scene.id)))
    notes.push("This Manim contribution has no mapping for the additional authored audio or creative components.");
  if (project.audio.cues.some(cue => seconds(cue.at) >= seconds(scene.start) && seconds(cue.at) < seconds(scene.start) + seconds(scene.duration)))
    notes.push("This Manim scene contribution does not realize its authored voice or caption cues.");
  return notes;
}

/** Local editor continuity only. This envelope never enters Project or Change. */
export interface CameraDraftRecord {
  schema: "motionwright.camera-draft/1";
  project_id: string; generation: string; scene_id: string; active_branch: string;
  base_revision: number; source_signature: string; base: CameraReframe;
  fields: Record<CameraDraftField, string>;
}
export function cameraDraftKey(project: Project, scene: Scene): string {
  return `motionwright.camera-draft/1:${project.id}:${project.generation}:${scene.id}:${encodeURIComponent(project.active_branch)}`;
}
export function cameraSourceSignature(project: Project, scene: Scene, reframe: CameraReframe | null =
  project.production_design?.camera_reframes?.find(move => move.scene_id === scene.id) ?? null): string {
  return JSON.stringify({ renderer: scene.renderer, camera: scene.camera, start: scene.start,
    duration: scene.duration, beats: scene.beats, nodes: scene.nodes,
    reframe: reframe ? KEYS.map(key => reframe[key as keyof CameraReframe]) : null });
}
export function newCameraReframe(scene: Scene, id: string = crypto.randomUUID()): CameraReframe {
  const { center_x: x, center_y: y } = scene.camera;
  return { id, scene_id: scene.id, component_version: cameraReframeVersion(scene.renderer) ?? CAMERA_REFRAME_VERSION,
    start_center_x: x, start_center_y: y, end_center_x: x + (x <= 1188 ? 12 : -12),
    end_center_y: y + (y <= 756 ? 4 : -4),
    duration_frames: Math.max(12, Math.min(90, Math.floor(seconds(scene.duration) * 30))) };
}
export function createCameraDraft(project: Project, scene: Scene, id?: string): CameraDraftRecord {
  const base = structuredClone(project.production_design?.camera_reframes?.find(move => move.scene_id === scene.id) ?? newCameraReframe(scene, id));
  return { schema: "motionwright.camera-draft/1", project_id: project.id, generation: project.generation,
    scene_id: scene.id, active_branch: project.active_branch, base_revision: project.revision, source_signature: cameraSourceSignature(project, scene), base,
    fields: Object.fromEntries(CAMERA_DRAFT_FIELDS.map(key => [key, String(base[key])])) as CameraDraftRecord["fields"] };
}
export function cameraDraftMove(record: CameraDraftRecord): CameraReframe {
  const move = { ...record.base };
  for (const key of CAMERA_DRAFT_FIELDS) {
    const input = record.fields[key];
    if (!input.trim() || !/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(input.trim()) || !Number.isFinite(Number(input)))
      invalid("Enter a finite number for " + key.replaceAll("_", " ") + "; the draft input is retained.");
    move[key] = Number(input);
  }
  return move;
}
export function cameraDraftEdited(record: CameraDraftRecord): boolean {
  try {
    const move = cameraDraftMove(record);
    return CAMERA_DRAFT_FIELDS.some(key => move[key] !== record.base[key]);
  } catch { return true; }
}
export function decodeCameraDraft(raw: string, project: Project, scene: Scene): CameraDraftRecord {
  if (raw.length > 1_000_000) invalid("retained camera draft is too large");
  const record: CameraDraftRecord = JSON.parse(raw);
  if (!record || typeof record !== "object" || !exactKeys(record, ["schema", "project_id", "generation", "scene_id", "active_branch",
    "base_revision", "source_signature", "base", "fields"]) || record.schema !== "motionwright.camera-draft/1" ||
    record.project_id !== project.id || record.generation !== project.generation || record.scene_id !== scene.id || record.active_branch !== project.active_branch ||
    !Number.isSafeInteger(record.base_revision) || record.base_revision < 0 || typeof record.source_signature !== "string" ||
    !record.base || typeof record.base !== "object" || !exactKeys(record.base, KEYS) ||
    !UUID.test(record.base.id) || record.base.scene_id !== scene.id || ![1, 2].includes(record.base.component_version) ||
    !CAMERA_DRAFT_FIELDS.every(key => Number.isFinite(record.base[key])) || !record.fields || typeof record.fields !== "object" ||
    !exactKeys(record.fields, CAMERA_DRAFT_FIELDS) || CAMERA_DRAFT_FIELDS.some(key => typeof record.fields[key] !== "string" || record.fields[key].length > 64))
    invalid("retained camera draft does not match this project, generation or scene");
  return record;
}
export function reconcileCameraDraft(record: CameraDraftRecord, project: Project, scene: Scene,
  acknowledged?: { move: CameraReframe; source_signature: string }): CameraDraftRecord {
  if (record.project_id !== project.id || record.generation !== project.generation || record.scene_id !== scene.id || record.active_branch !== project.active_branch)
    invalid("draft identity changed; load the corresponding scene draft");
  const signature = cameraSourceSignature(project, scene);
  if (signature === record.source_signature) return record.base_revision === project.revision ? record : { ...record, base_revision: project.revision };
  const saved = project.production_design?.camera_reframes?.find(move => move.scene_id === scene.id);
  if (acknowledged && saved && sameCameraReframe(saved, acknowledged.move) && signature === acknowledged.source_signature)
    return { ...record, base_revision: project.revision, source_signature: signature, base: structuredClone(saved) };
  if (!cameraDraftEdited(record)) return createCameraDraft(project, scene, record.base.id);
  return record;
}
/** Explicit conflict resolution preserves entered values, using the identity
 * of the current saved camera if another writer attached it in this scene. */
export function rebaseCameraDraft(record: CameraDraftRecord, project: Project, scene: Scene): CameraDraftRecord {
  const current = createCameraDraft(project, scene, record.base.id);
  return { ...current, fields: { ...record.fields } };
}
