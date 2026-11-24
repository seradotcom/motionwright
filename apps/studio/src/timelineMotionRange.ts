import { formatBeatTime, parseBeatTime } from "./sceneBeatTiming";
import { parseScopedMotion, scopedMotionRows } from "./scopedPaintMotion";
import type { ScopedMotionRow } from "./scopedPaintMotion";
import type { CanvasKeyframe, RationalTime } from "./types";

export interface MotionRangeInput { mode: "move" | "fit"; start: string; end: string }
type Fraction = { n: bigint; d: bigint };
const fraction = (time: RationalTime): Fraction => {
  formatBeatTime(time);
  return { n: BigInt(time.num), d: BigInt(time.den) };
};
const sub = (a: Fraction, b: Fraction): Fraction => ({ n: a.n * b.d - b.n * a.d, d: a.d * b.d });
const add = (a: Fraction, b: Fraction): Fraction => ({ n: a.n * b.d + b.n * a.d, d: a.d * b.d });
const canonical = (a: Fraction): RationalTime => {
  let n=a.n<0n?-a.n:a.n,d=a.d;
  while(d) [n,d]=[d,n%d];
  const divisor=n||1n;
  return parseBeatTime(a.n/divisor + "/" + a.d/divisor);
};
const compare = (a: RationalTime, b: RationalTime) => {
  const delta = BigInt(a.num) * BigInt(b.den) - BigInt(b.num) * BigInt(a.den);
  return delta < 0n ? -1 : delta > 0n ? 1 : 0;
};
export function motionKeyRange(keys: readonly CanvasKeyframe[]): { start: RationalTime; end: RationalTime } | null {
  if (!keys.length) return null;
  const times = keys.map(key => key.at).sort(compare);
  return { start: { ...times[0] }, end: { ...times.at(-1)! } };
}
export function validMotionRangeInput(value: unknown): value is MotionRangeInput {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const input = value as Record<string, unknown>;
  return (input.mode === "move" || input.mode === "fit") && typeof input.start === "string" && input.start.length <= 128
    && typeof input.end === "string" && input.end.length <= 128;
}
export function defaultMotionRangeInput(rows: ScopedMotionRow[]): MotionRangeInput {
  try {
    const range = motionKeyRange(parseScopedMotion(rows));
    return { mode: "move", start: range ? formatBeatTime(range.start) : "", end: range ? formatBeatTime(range.end) : "" };
  } catch { return { mode: "move", start: "", end: "" }; }
}
/** Exact affine time transform, never resampling values or replacing curves.
 * Source and destination are local seconds; output still uses the existing
 * complete-track Change and canonical reduced i64 RationalTime strings. */
export function retimeMotionRows(rows: ScopedMotionRow[], input: MotionRangeInput, duration: RationalTime): ScopedMotionRow[] {
  if (!validMotionRangeInput(input)) throw new Error("Complete the bounded animation timing fields.");
  const keys = parseScopedMotion(rows, duration), range = motionKeyRange(keys);
  if (!range) throw new Error("Add keyframes before moving or fitting the animation range.");
  const oldStart = fraction(range.start), oldSpan = sub(fraction(range.end), oldStart);
  const start = fraction(parseBeatTime(input.start));
  const nextSpan = input.mode === "fit" ? sub(fraction(parseBeatTime(input.end)), start) : oldSpan;
  if (input.mode === "fit" && (oldSpan.n <= 0n || nextSpan.n <= 0n))
    throw new Error("Fitting requires two distinct source times and an end after the new start.");
  const shifted = keys.map(key => {
    const offset = sub(fraction(key.at), oldStart);
    const mapped = input.mode === "move" ? offset : {
      n: offset.n * nextSpan.n * oldSpan.d,
      d: offset.d * nextSpan.d * oldSpan.n,
    };
    return { ...key, at: canonical(add(start, mapped)) };
  });
  // This also checks the half-open scene, duplicate slots and canonical bounds.
  const result = scopedMotionRows(parseScopedMotion(scopedMotionRows(shifted), duration));
  if (JSON.stringify(result) === JSON.stringify(scopedMotionRows(keys)))
    throw new Error("This range already matches the animation draft.");
  return result;
}
