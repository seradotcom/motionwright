import type { CanvasKeyframe, CanvasNode, CanvasTransform, MotionInterpolation, Project, RationalTime, Scene } from "./types";

export type PoseGeometry = Omit<CanvasTransform, "opacity">;
export type PoseCurve = "linear" | "ease_out_cubic" | "hold";
export type NativePoseWaypoint = { frame: number; pose: PoseGeometry; curve: PoseCurve };
export const MAX_POSE_WAYPOINTS = 4;
export type NativePoseDraft = {
  start: PoseGeometry;
  endFrame: number;
  curve: PoseCurve;
  fade: "none" | "in" | "out";
  fadeCurve: "linear" | "ease_out_cubic";
  /** Absent means the fade follows the geometry duration. Legacy drafts stay valid. */
  fadeEndFrame?: number;
  waypoints?: NativePoseWaypoint[];
};

export type NativePoseEdit = { keys: CanvasKeyframe[]; issue: null } | { keys: null; issue: string };
type TimingProfile = Pick<Project["deliverables"][number], "frame_rate">;
export const POSE_GEOMETRY = ["x", "y", "width", "height", "rotation_deg"] as const;
const curves = ["linear", "ease_out_cubic", "hold"] as const;
const validCurve = (value: string): value is PoseCurve => (curves as readonly string[]).includes(value);
const validFrame = (value: number) => Number.isInteger(value) && value >= 1 && value <= 36000;
const validGeometry = (pose: PoseGeometry) =>
  POSE_GEOMETRY.every(property => Number.isFinite(pose[property])) &&
  pose.width > 0 && pose.height > 0 && pose.width <= 16384 && pose.height <= 16384 &&
  Math.abs(pose.rotation_deg) <= 360;
const geometry = (node: CanvasNode): PoseGeometry => ({
  x: node.x, y: node.y, width: node.width, height: node.height, rotation_deg: node.rotation_deg,
});

export function defaultNativePoseDraft(node: CanvasNode): NativePoseDraft {
  return {
    start: { x: node.x - 60, y: node.y, width: node.width * 0.8, height: node.height * 0.8, rotation_deg: -15 },
    endFrame: 30, curve: "ease_out_cubic", fade: "none", fadeCurve: "ease_out_cubic",
  };
}

function frameOf(at: RationalTime, profile: TimingProfile): number | null {
  try {
    const numerator = BigInt(at.num) * BigInt(profile.frame_rate.num);
    const denominator = BigInt(at.den) * BigInt(profile.frame_rate.den);
    if (numerator < 0n || denominator <= 0n || numerator % denominator !== 0n ||
        numerator / denominator > 36000n) return null;
    return Number(numerator / denominator);
  } catch { return null; }
}
function timeOf(frame: number, profile: TimingProfile, scene: Scene, held = false): RationalTime {
  if (!validFrame(frame)) throw new Error("Enter a frame from 1 to 36000.");
  const rateNum = BigInt(profile.frame_rate.num), rateDen = BigInt(profile.frame_rate.den);
  let numerator = BigInt(frame) * rateDen, denominator = rateNum;
  const durationNum = BigInt(scene.duration.num), durationDen = BigInt(scene.duration.den);
  if (rateNum <= 0n || rateDen <= 0n || durationDen <= 0n) throw new Error("The saved output frame rate is invalid.");
  if (numerator * durationDen >= durationNum * denominator) throw new Error("Every end or intermediate frame must be inside the scene.");
  if (held && (numerator + rateDen) * durationDen > durationNum * denominator)
    throw new Error("Leave one complete frame for the held destination.");
  let a = numerator, b = denominator;
  while (b) [a, b] = [b, a % b];
  numerator /= a; denominator /= a;
  return { num: numerator.toString(), den: denominator.toString() };
}

/** Hydrate only losslessly representable complete pose tracks. Other motion
 * stays in the typed inspector. All geometry channels share each exact pose
 * timestamp and its incoming curve; opacity is an independent exact fade. */
export function savedNativePoseDraft(node: CanvasNode, profile: TimingProfile | null): NativePoseDraft | null {
  if (!profile) return null;
  const draft = defaultNativePoseDraft(node);
  const slots = new Map<number, { at: RationalTime; pose: Partial<PoseGeometry>; curve: PoseCurve }>();
  const geometryKeys = node.keyframes.filter(key => key.property !== "opacity");
  for (const key of geometryKeys) {
    const property = key.property as keyof PoseGeometry, frame = frameOf(key.at, profile);
    if (frame === null || !POSE_GEOMETRY.includes(property) || !validCurve(key.interpolation) ||
        !Number.isFinite(key.value)) return null;
    let slot = slots.get(frame);
    if (!slot) {
      slot = { at: key.at, pose: {}, curve: key.interpolation };
      slots.set(frame, slot);
    }
    if (slot.at.num !== key.at.num || slot.at.den !== key.at.den ||
        slot.curve !== key.interpolation || Object.hasOwn(slot.pose, property)) return null;
    slot.pose[property] = key.value;
  }
  const ordered = [...slots.entries()].sort(([a], [b]) => a - b);
  if (ordered.length < 2 || ordered.length > MAX_POSE_WAYPOINTS + 2 ||
      ordered[0][0] !== 0 || ordered[0][1].at.num !== "0" || ordered[0][1].at.den !== "1" ||
      ordered[0][1].curve !== "linear" ||
      ordered.some(([, slot]) => !validGeometry(slot.pose as PoseGeometry))) return null;
  const [endFrame, terminal] = ordered.at(-1)!;
  if (POSE_GEOMETRY.some(property => terminal.pose[property] !== node[property])) return null;
  draft.start = ordered[0][1].pose as PoseGeometry;
  draft.endFrame = endFrame; draft.curve = terminal.curve;
  if (ordered.length > 2) draft.waypoints = ordered.slice(1, -1).map(([frame, slot]) =>
    ({ frame, pose: slot.pose as PoseGeometry, curve: slot.curve }));
  const alpha = node.keyframes.filter(key => key.property === "opacity");
  if (alpha.length) {
    const first = alpha.find(key => key.at.num === "0" && key.at.den === "1");
    const last = alpha.find(key => key !== first);
    if (alpha.length !== 2 || !first || !last || first.interpolation !== "linear" ||
        !["linear", "ease_out_cubic"].includes(last.interpolation)) return null;
    const fadeFrame = frameOf(last.at, profile);
    if (fadeFrame === null || !validFrame(fadeFrame)) return null;
    if (first.value === 0 && last.value === 1) draft.fade = "in";
    else if (first.value === 1 && last.value === 0) draft.fade = "out";
    else return null;
    draft.fadeCurve = last.interpolation as NativePoseDraft["fadeCurve"];
    if (fadeFrame !== endFrame) draft.fadeEndFrame = fadeFrame;
  }
  return draft;
}

/** A read-only authoring check; Service independently validates the submitted
 * Change and Native preflight checks the actual selected profile and bounds. */
export function nativePoseEdit(project: Project, scene: Scene, node: CanvasNode,
  profile: Project["deliverables"][number] | null, draft: NativePoseDraft): NativePoseEdit {
  const fail = (issue: string): NativePoseEdit => ({ keys: null, issue });
  if (!profile) return fail("Select a saved delivery profile.");
  if (scene.renderer !== "motion-canvas" || scene.beats.length) return fail("Use a Motion Canvas scene with a continuous timeline.");
  if (!["shape", "rectangle", "circle"].includes(node.kind) ||
      node.coordinate_space !== "project_pixels" || node.relations.length ||
      node.style.blend_mode !== "normal" || node.rotation_deg !== 0 || node.opacity !== 1 ||
      scene.nodes.some(candidate => candidate.parent_id === node.id)) {
    return fail("Select a plain shape with base rotation 0 and opacity 1.");
  }
  if (node.parent_id !== null) {
    const parent = scene.nodes.find(candidate => candidate.id === node.parent_id);
    if (!parent || parent.kind !== "clip_group" || parent.parent_id !== null ||
        parent.keyframes.length || parent.relations.length || parent.rotation_deg !== 0 ||
        parent.opacity !== 1 || parent.coordinate_space !== "project_pixels" ||
        parent.style.blend_mode !== "normal") return fail("The shape needs an unchanged root Clip group.");
  }
  if (node.keyframes.length && !savedNativePoseDraft(node, profile)) {
    return fail("This motion has independent or additional keys. Edit them in the Motion inspector.");
  }
  const affectsAlpha = draft.fade !== "none" || node.keyframes.some(key => key.property === "opacity");
  const locks = ["position", "size", "rotation", ...(affectsAlpha ? ["opacity"] : [])];
  if (project.locks.some(lock => lock.resource === "scene:" + scene.id && lock.kind === "position") ||
      node.property_locks.some(lock => locks.includes(lock))) return fail("Unlock the affected motion properties.");
  const waypoints = draft.waypoints ?? [];
  if (!Array.isArray(waypoints) || waypoints.length > MAX_POSE_WAYPOINTS) return fail("Use at most four intermediate poses.");
  if (![draft.start, ...waypoints.map(waypoint => waypoint.pose), geometry(node)].every(validGeometry))
    return fail("Enter finite geometry with positive dimensions and rotation between -360 and 360.");
  if (!validCurve(draft.curve) || !waypoints.every(waypoint => validCurve(waypoint.curve)) ||
      !["linear", "ease_out_cubic"].includes(draft.fadeCurve) || !["none", "in", "out"].includes(draft.fade))
    return fail("Choose a supported native curve.");
  let previousFrame = 0;
  for (const frame of [...waypoints.map(waypoint => waypoint.frame), draft.endFrame]) {
    if (!validFrame(frame) || frame <= previousFrame) return fail("Pose frames must be strictly increasing inside the scene.");
    previousFrame = frame;
  }
  if (draft.fade === "none" && [draft.start, ...waypoints.map(waypoint => waypoint.pose)]
      .every(pose => POSE_GEOMETRY.every(property => pose[property] === node[property]))) return fail("Change at least one starting or intermediate property.");
  const first = { num: "0", den: "1" };
  const keys: CanvasKeyframe[] = POSE_GEOMETRY.map(property =>
    ({ at: first, property, value: draft.start[property], interpolation: "linear" }));
  try {
    for (const waypoint of [...waypoints, { frame: draft.endFrame, pose: geometry(node), curve: draft.curve }]) {
      const at = timeOf(waypoint.frame, profile, scene, waypoint.curve === "hold");
      for (const property of POSE_GEOMETRY) keys.push({ at, property, value: waypoint.pose[property], interpolation: waypoint.curve });
    }
    if (draft.fade !== "none") {
      const at = timeOf(draft.fadeEndFrame ?? draft.endFrame, profile, scene);
      keys.push({ at: first, property: "opacity", value: draft.fade === "in" ? 0 : 1, interpolation: "linear" });
      keys.push({ at, property: "opacity", value: draft.fade === "in" ? 1 : 0, interpolation: draft.fadeCurve as MotionInterpolation });
    }
  } catch (reason) { return fail(reason instanceof Error ? reason.message : "Invalid rational pose timing."); }
  return { keys, issue: null };
}
