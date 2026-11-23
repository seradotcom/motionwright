import type { CanvasKeyframe, CanvasNode, CanvasTransform, MotionInterpolation, Project, Scene } from "./types";

export type NativePoseDraft = {
  start: Omit<CanvasTransform, "opacity">;
  endFrame: number;
  curve: "linear" | "ease_out_cubic" | "hold";
  fade: "none" | "in" | "out";
  fadeCurve: "linear" | "ease_out_cubic";
};

export type NativePoseEdit = { keys: CanvasKeyframe[]; issue: null } | { keys: null; issue: string };

const geometryProperties = ["x", "y", "width", "height", "rotation_deg"] as const;

export function defaultNativePoseDraft(node: CanvasNode): NativePoseDraft {
  return {
    start: { x: node.x - 60, y: node.y, width: node.width * 0.8, height: node.height * 0.8, rotation_deg: -15 },
    endFrame: 30, curve: "ease_out_cubic", fade: "none", fadeCurve: "ease_out_cubic",
  };
}

/** Only hydrate a losslessly representable complete pose. Other saved motion
 * stays in the existing typed inspector and is never replaced implicitly. */
export function savedNativePoseDraft(
  node: CanvasNode, profile: Project["deliverables"][number] | null,
): NativePoseDraft | null {
  if (!profile || ![10, 12].includes(node.keyframes.length)) return null;
  const draft = defaultNativePoseDraft(node);
  let terminal: CanvasKeyframe | undefined;
  for (const property of geometryProperties) {
    const keys = node.keyframes.filter(key => key.property === property);
    const first = keys.find(key => key.at.num === "0" && key.at.den === "1");
    const last = keys.find(key => key !== first);
    if (keys.length !== 2 || !first || !last || first.interpolation !== "linear"
      || !["linear", "ease_out_cubic", "hold"].includes(last.interpolation)
      || last.value !== node[property]) return null;
    if (terminal && (last.at.num !== terminal.at.num || last.at.den !== terminal.at.den
      || last.interpolation !== terminal.interpolation)) return null;
    terminal = last;
    draft.start[property] = first.value;
    draft.curve = last.interpolation as NativePoseDraft["curve"];
  }
  if (!terminal) return null;
  try {
    const numerator = BigInt(terminal.at.num) * BigInt(profile.frame_rate.num);
    const denominator = BigInt(terminal.at.den) * BigInt(profile.frame_rate.den);
    if (numerator <= 0n || denominator <= 0n || numerator % denominator !== 0n
      || numerator / denominator > 36000n) return null;
    draft.endFrame = Number(numerator / denominator);
  } catch { return null; }
  const alpha = node.keyframes.filter(key => key.property === "opacity");
  if (node.keyframes.length === 12) {
    const first = alpha.find(key => key.at.num === "0" && key.at.den === "1");
    const last = alpha.find(key => key !== first);
    if (alpha.length !== 2 || !first || !last || first.interpolation !== "linear"
      || !["linear", "ease_out_cubic"].includes(last.interpolation)
      || last.at.num !== terminal.at.num || last.at.den !== terminal.at.den) return null;
    if (first.value === 0 && last.value === 1) draft.fade = "in";
    else if (first.value === 1 && last.value === 0) draft.fade = "out";
    else return null;
    draft.fadeCurve = last.interpolation as NativePoseDraft["fadeCurve"];
  } else if (alpha.length) return null;
  return draft;
}

/** A read-only authoring check; the real Service independently validates the
 * submitted Change and Native preflight verifies its renderer/profile. */
export function nativePoseEdit(
  project: Project, scene: Scene, node: CanvasNode,
  profile: Project["deliverables"][number] | null, draft: NativePoseDraft,
): NativePoseEdit {
  const fail = (issue: string): NativePoseEdit => ({ keys: null, issue });
  if (!profile) return fail("Select a saved delivery profile.");
  if (scene.renderer !== "motion-canvas" || scene.beats.length) return fail("Use a Motion Canvas scene with a continuous timeline.");
  if (!["shape", "rectangle", "circle"].includes(node.kind)
    || node.coordinate_space !== "project_pixels" || node.relations.length
    || node.style.blend_mode !== "normal" || node.rotation_deg !== 0 || node.opacity !== 1
    || scene.nodes.some(candidate => candidate.parent_id === node.id)) {
    return fail("Select a plain shape with base rotation 0 and opacity 1.");
  }
  if (node.parent_id !== null) {
    const parent = scene.nodes.find(candidate => candidate.id === node.parent_id);
    if (!parent || parent.kind !== "clip_group" || parent.parent_id !== null
      || parent.keyframes.length || parent.relations.length || parent.rotation_deg !== 0
      || parent.opacity !== 1 || parent.coordinate_space !== "project_pixels") {
      return fail("The shape needs an unchanged root Clip group.");
    }
  }
  if (node.keyframes.length && !savedNativePoseDraft(node, profile)) {
    return fail("This motion has independent or additional keys. Edit them in the Motion inspector.");
  }
  const affectsAlpha = draft.fade !== "none" || node.keyframes.some(key => key.property === "opacity");
  const locks = ["position", "size", "rotation", ...(affectsAlpha ? ["opacity"] : [])];
  if (project.locks.some(lock => lock.resource === "scene:" + scene.id && lock.kind === "position")
    || node.property_locks.some(lock => locks.includes(lock))) return fail("Unlock the affected motion properties.");
  const { start } = draft;
  if (Object.values(start).some(value => !Number.isFinite(value))
    || start.width <= 0 || start.height <= 0 || start.width > 16384 || start.height > 16384
    || Math.abs(start.rotation_deg) > 360) return fail("Enter finite geometry with positive dimensions and rotation between -360 and 360.");
  if (!["linear", "ease_out_cubic", "hold"].includes(draft.curve)
    || !["linear", "ease_out_cubic"].includes(draft.fadeCurve)
    || !["none", "in", "out"].includes(draft.fade)) return fail("Choose a supported native curve.");
  if (!Number.isInteger(draft.endFrame) || draft.endFrame < 1 || draft.endFrame > 36000) return fail("Enter an end frame from 1 to 36000.");
  let numerator: bigint, denominator: bigint;
  try {
    const rateNum = BigInt(profile.frame_rate.num), rateDen = BigInt(profile.frame_rate.den);
    numerator = BigInt(draft.endFrame) * rateDen; denominator = rateNum;
    const durationNum = BigInt(scene.duration.num), durationDen = BigInt(scene.duration.den);
    if (rateNum <= 0n || rateDen <= 0n || durationDen <= 0n
      || numerator * durationDen >= durationNum * denominator) return fail("The end frame must be inside the scene.");
    // A held terminal pose occupies an actual complete output frame.
    if (draft.curve === "hold"
      && (numerator + rateDen) * durationDen > durationNum * denominator) return fail("Leave one complete frame for the held destination.");
    let a = numerator, b = denominator;
    while (b) [a, b] = [b, a % b];
    numerator /= a; denominator /= a;
  } catch { return fail("The saved output frame rate is invalid."); }
  const properties = geometryProperties;
  if (draft.fade === "none" && properties.every(property => start[property] === node[property])) return fail("Change at least one starting property.");
  const first = { num: "0", den: "1" };
  const last = { num: numerator.toString(), den: denominator.toString() };
  const keys: CanvasKeyframe[] = [];
  for (const property of properties) {
    keys.push({ at: first, property, value: start[property], interpolation: "linear" });
    keys.push({ at: last, property, value: node[property], interpolation: draft.curve });
  }
  if (draft.fade !== "none") {
    keys.push({ at: first, property: "opacity", value: draft.fade === "in" ? 0 : 1, interpolation: "linear" });
    keys.push({ at: last, property: "opacity", value: draft.fade === "in" ? 1 : 0, interpolation: draft.fadeCurve as MotionInterpolation });
  }
  return { keys, issue: null };
}
