import { CANVAS_STYLE_FIELDS, canvasFieldIssue, canvasNumber } from "./canvasWorkspaceDraft";
import { formatBeatTime, parseBeatTime } from "./sceneBeatTiming";
import type { CanvasKeyframe, MotionInterpolation, MotionProperty, NodeStyle, RationalTime } from "./types";

export { CANVAS_STYLE_FIELDS };
export type ScopedStyleRaw = Record<keyof NodeStyle, string>;
export interface ScopedMotionRow { at: string; property: MotionProperty; value: string; interpolation: MotionInterpolation }
export const SCOPED_MOTION_PROPERTIES = ["x", "y", "width", "height", "rotation_deg", "opacity"] as const;
export const SCOPED_MOTION_CURVES = ["linear", "ease_in_out", "ease_out_cubic", "hold"] as const;
const decimal = /^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?$/i;
const optionalStyleNumbers = ["font_size", "font_weight", "line_height"] as const;
const styleStrings = ["fill", "stroke", "font_family"] as const;
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);

export function cleanScopedStyle(style: NodeStyle): NodeStyle {
  return { fill: style.fill, stroke: style.stroke, stroke_width: style.stroke_width, font_family: style.font_family,
    font_size: style.font_size, font_weight: style.font_weight, line_height: style.line_height, blend_mode: style.blend_mode };
}
export function scopedStyleRaw(style: NodeStyle): ScopedStyleRaw {
  return Object.fromEntries(CANVAS_STYLE_FIELDS.map(field => [field, String(style[field] ?? "")])) as ScopedStyleRaw;
}
export function scopedStyleIssues(raw: ScopedStyleRaw): Partial<Record<keyof NodeStyle, string>> {
  const errors: Partial<Record<keyof NodeStyle, string>> = {};
  for (const field of CANVAS_STYLE_FIELDS) {
    const numeric = field === "stroke_width" || (optionalStyleNumbers as readonly string[]).includes(field);
    const optionalEmpty = field !== "stroke_width" && raw[field].trim() === "";
    const issue = numeric && !optionalEmpty && !decimal.test(raw[field].trim())
      ? "Complete this value with a finite decimal number." : canvasFieldIssue(field, raw[field]);
    if (issue) errors[field] = issue;
  }
  return errors;
}
export function parseScopedStyle(raw: ScopedStyleRaw): NodeStyle {
  const errors = scopedStyleIssues(raw);
  if (Object.keys(errors).length) throw new Error("Complete the style fields before adding this proposal.");
  const result: Record<string, unknown> = {};
  for (const field of CANVAS_STYLE_FIELDS) {
    const text = raw[field].trim();
    result[field] = (styleStrings as readonly string[]).includes(field) ? text || null
      : (optionalStyleNumbers as readonly string[]).includes(field) ? text === "" ? null : canvasNumber(text)
        : field === "blend_mode" ? text : canvasNumber(text);
  }
  return result as unknown as NodeStyle;
}
export const validScopedStyleRaw = (value: unknown): value is ScopedStyleRaw => record(value) &&
  CANVAS_STYLE_FIELDS.every(field => typeof value[field] === "string");
export function validScopedStyle(value: unknown): value is NodeStyle {
  if (!record(value) || !styleStrings.every(field => value[field] === null || typeof value[field] === "string")
    || typeof value.stroke_width !== "number" || !optionalStyleNumbers.every(field => value[field] === null || typeof value[field] === "number")
    || typeof value.blend_mode !== "string") return false;
  return Object.keys(scopedStyleIssues(scopedStyleRaw(value as unknown as NodeStyle))).length === 0;
}
export function cleanScopedMotion(keys: CanvasKeyframe[]): CanvasKeyframe[] {
  return keys.map(key => ({ at: { num: key.at.num, den: key.at.den }, property: key.property,
    value: key.value, interpolation: key.interpolation }));
}
export function scopedMotionRows(keys: CanvasKeyframe[]): ScopedMotionRow[] {
  return keys.map(key => ({ at: formatBeatTime(key.at), property: key.property,
    value: String(key.value), interpolation: key.interpolation }));
}
export const validScopedMotionRows = (value: unknown): value is ScopedMotionRow[] => Array.isArray(value) && value.length <= 128 &&
  value.every(row => record(row) && typeof row.at === "string" && typeof row.value === "string" &&
    (SCOPED_MOTION_PROPERTIES as readonly unknown[]).includes(row.property) &&
    (SCOPED_MOTION_CURVES as readonly unknown[]).includes(row.interpolation));
export function parseScopedMotion(rows: ScopedMotionRow[], duration?: RationalTime): CanvasKeyframe[] {
  if (!validScopedMotionRows(rows)) throw new Error("A complete animation track contains at most 128 supported keyframes.");
  const slots = new Set<string>();
  const keys = rows.map((row, index) => {
    try {
      const at = parseBeatTime(row.at);
      if (BigInt(at.num) < 0n || (duration &&
        BigInt(at.num) * BigInt(duration.den) >= BigInt(duration.num) * BigInt(at.den))) {
        throw new Error("Time must be inside the scene, before its end.");
      }
      if (!decimal.test(row.value.trim())) throw new Error("Complete the value with a finite decimal number.");
      const value = canvasNumber(row.value);
      const issue = canvasFieldIssue(row.property, row.value);
      if (issue) throw new Error(issue);
      const slot = at.num + "/" + at.den + ":" + row.property;
      if (slots.has(slot)) throw new Error("This property already has a keyframe at the same time.");
      slots.add(slot);
      return { at, property: row.property, value, interpolation: row.interpolation };
    } catch (reason) {
      throw new Error("Keyframe " + (index + 1) + ": " + (reason instanceof Error ? reason.message : String(reason)));
    }
  });
  return keys.sort((a, b) => {
    const delta = BigInt(a.at.num) * BigInt(b.at.den) - BigInt(b.at.num) * BigInt(a.at.den);
    return delta < 0n ? -1 : delta > 0n ? 1 : SCOPED_MOTION_PROPERTIES.indexOf(a.property) - SCOPED_MOTION_PROPERTIES.indexOf(b.property);
  });
}
export function scopedMotionIssue(rows: ScopedMotionRow[], duration?: RationalTime): string | null {
  try { parseScopedMotion(rows, duration); return null; }
  catch (reason) { return reason instanceof Error ? reason.message : String(reason); }
}
export function validScopedMotion(value: unknown): value is CanvasKeyframe[] {
  if (!Array.isArray(value) || value.length > 128 || !value.every(key => record(key) && record(key.at) &&
    typeof key.at.num === "string" && /^\d+$/.test(key.at.num) &&
    typeof key.at.den === "string" && /^[1-9]\d*$/.test(key.at.den) &&
    typeof key.value === "number" && Number.isFinite(key.value) &&
    (SCOPED_MOTION_PROPERTIES as readonly unknown[]).includes(key.property) &&
    (SCOPED_MOTION_CURVES as readonly unknown[]).includes(key.interpolation))) return false;
  try { return scopedMotionIssue(scopedMotionRows(value as CanvasKeyframe[])) === null; }
  catch { return false; }
}
