import type { CanvasKeyframe, CanvasNode, RationalTime, Scene } from "./types";

export const MAX_NATIVE_OPACITY_KEYS = 8;
export type CanvasOpacitySpan = {
  start: RationalTime; end: RationalTime; from: number; to: number;
  curve: "linear" | "ease_out_cubic" | "ease_in_out";
};

/** Exact read-only counterpart of Domain opacity negotiation. Same-value
 * keys hold visibility; actual transitions use literal native fade curves. */
export function exactCanvasOpacitySpans(node: CanvasNode, scene: Pick<Scene, "duration">,
  fps: RationalTime): CanvasOpacitySpan[] | null {
  const alpha = node.keyframes.filter(key => key.property === "opacity");
  if (!alpha.length) return null;
  const fail = (): never => {
    throw new Error("Use 2 to 8 frame-aligned opacity keys with values 0 or 1; partial alpha and held jumps are unsupported.");
  };
  if (alpha.length < 2 || alpha.length > MAX_NATIVE_OPACITY_KEYS || node.opacity !== 1) return fail();
  const rational = (at: RationalTime): [bigint, bigint] => {
    if (!/^(0|[1-9][0-9]{0,18})$/.test(at.num) || !/^[1-9][0-9]{0,18}$/.test(at.den)) return fail();
    const num = BigInt(at.num), den = BigInt(at.den), limit = 9223372036854775807n;
    if (num > limit || den > limit) return fail();
    let a = num, b = den;
    while (b) [a, b] = [b, a % b];
    if (a !== 1n) return fail();
    return [num, den];
  };
  const compare = (a: CanvasKeyframe, b: CanvasKeyframe) => {
    const delta = BigInt(a.at.num) * BigInt(b.at.den) - BigInt(b.at.num) * BigInt(a.at.den);
    return delta < 0n ? -1 : delta > 0n ? 1 : 0;
  };
  let keys: CanvasKeyframe[];
  try {
    const [num, den] = rational(fps), [durationNum, durationDen] = rational(scene.duration);
    if (num <= 0n || durationNum <= 0n) return fail();
    for (const key of alpha) rational(key.at);
    keys = [...alpha].sort(compare);
    if (keys[0].at.num !== "0" || keys[0].at.den !== "1" || keys[0].interpolation !== "linear") return fail();
    for (let index = 0; index < keys.length; index++) {
      const key = keys[index], [atNum, atDen] = rational(key.at);
      const numerator = atNum * num, denominator = atDen * den;
      if (numerator % denominator !== 0n || numerator / denominator > 36000n ||
          atNum * durationDen >= durationNum * atDen || ![0, 1].includes(key.value) ||
          !["linear", "ease_out_cubic", "ease_in_out", "hold"].includes(key.interpolation) ||
          (index > 0 && compare(keys[index - 1], key) >= 0)) return fail();
    }
  } catch { return fail(); }
  const spans: CanvasOpacitySpan[] = [];
  for (let index = 1; index < keys.length; index++) {
    const from = keys[index - 1], to = keys[index];
    if (from.value === to.value) continue;
    if (to.interpolation === "hold") return fail();
    spans.push({ start: from.at, end: to.at, from: from.value, to: to.value,
      curve: to.interpolation as CanvasOpacitySpan["curve"] });
  }
  return spans;
}
