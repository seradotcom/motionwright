import type { Beat, RationalTime } from "./types";

type Fraction = { n: bigint; d: bigint };

// These are authoring-input budgets, not a replacement for domain validation.
// Existing saved values remain exact and are never coerced through Number.
const MAX_INPUT_LENGTH = 96;
const MAX_EDIT_PART = 9_223_372_036_854_775_807n;
const MIN_SAVED_NUMERATOR = -9_223_372_036_854_775_808n;

const abs = (value: bigint) => value < 0n ? -value : value;

function gcd(left: bigint, right: bigint): bigint {
  let a = abs(left);
  let b = abs(right);
  while (b !== 0n) [a, b] = [b, a % b];
  return a || 1n;
}

function reduced(n: bigint, d: bigint): Fraction {
  if (d <= 0n) throw new Error("A time denominator must be positive.");
  const divisor = gcd(n, d);
  return { n: n / divisor, d: d / divisor };
}

function stored(time: RationalTime): Fraction {
  // Semwright Rational serializes reduced fractions as canonical i64 strings.
  // Validate that source shape rather than silently normalizing corrupt state.
  if (!/^-?(?:0|[1-9]\d{0,18})$/.test(time.num) || time.num === "-0"
    || !/^[1-9]\d{0,18}$/.test(time.den)) {
    throw new Error("The saved time is not a canonical exact fraction.");
  }
  const value = { n: BigInt(time.num), d: BigInt(time.den) };
  if (value.n < MIN_SAVED_NUMERATOR || value.n > MAX_EDIT_PART || value.d > MAX_EDIT_PART
    || gcd(value.n, value.d) !== 1n) {
    throw new Error("The saved time is outside the canonical exact range.");
  }
  return value;
}

const wire = ({ n, d }: Fraction): RationalTime => ({ num: n.toString(), den: d.toString() });
const compare = (left: Fraction, right: Fraction) => {
  const delta = left.n * right.d - right.n * left.d;
  return delta < 0n ? -1 : delta > 0n ? 1 : 0;
};
const add = (left: Fraction, right: Fraction) => reduced(left.n * right.d + right.n * left.d, left.d * right.d);
const subtract = (left: Fraction, right: Fraction) => reduced(left.n * right.d - right.n * left.d, left.d * right.d);

function checkedEnd(start: Fraction, duration: Fraction): Fraction {
  const end = add(start, duration);
  // Both fields can fit separately while their reduced sum cannot fit the
  // canonical domain's checked_add result. Do not offer an unsaveable interval.
  if (end.n < MIN_SAVED_NUMERATOR || end.n > MAX_EDIT_PART || end.d > MAX_EDIT_PART) {
    throw new Error("These times cannot form an exact interval within the saved time range. Use simpler fractions.");
  }
  return end;
}

function editable(value: Fraction): RationalTime {
  if (abs(value.n) > MAX_EDIT_PART || value.d > MAX_EDIT_PART) {
    throw new Error("This time exceeds the editor's exact input range. Use a simpler fraction.");
  }
  return wire(value);
}

/** Parse an authored decimal, exponent or fraction without IEEE-754 rounding. */
export function parseBeatTime(raw: string): RationalTime {
  const text = raw.trim();
  if (!text || text.length > MAX_INPUT_LENGTH) {
    throw new Error("Enter a complete time in seconds, such as 0.25 or 1001/30000.");
  }
  const fraction = /^([+-]?\d+)\/(\d+)$/.exec(text);
  if (fraction) return editable(reduced(BigInt(fraction[1]), BigInt(fraction[2])));

  const decimal = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/.exec(text);
  if (!decimal) throw new Error("Complete the time before saving; decimals and fractions are accepted.");
  const fractionDigits = decimal[3] ?? decimal[4] ?? "";
  const integerDigits = decimal[2] ?? "0";
  // Validate the bounded exponent before allocating powers of ten.
  const exponentText = decimal[5] ?? "0";
  if (!/^[+-]?\d{1,3}$/.test(exponentText)) {
    throw new Error("The time exponent exceeds the editor's exact input range.");
  }
  const exponent = Number(exponentText);
  if (Math.abs(exponent) > MAX_INPUT_LENGTH) {
    throw new Error("The time exponent exceeds the editor's exact input range.");
  }
  let numerator = BigInt(integerDigits + fractionDigits);
  if (decimal[1] === "-") numerator = -numerator;
  const scale = fractionDigits.length - exponent;
  return editable(scale >= 0
    ? reduced(numerator, 10n ** BigInt(scale))
    : reduced(numerator * 10n ** BigInt(-scale), 1n));
}

/** Show an exact finite decimal when available, otherwise an exact fraction. */
export function formatBeatTime(time: RationalTime): string {
  const value = stored(time);
  if (value.d === 1n) return value.n.toString();
  let rest = value.d;
  let twos = 0;
  let fives = 0;
  while (rest % 2n === 0n) { rest /= 2n; twos++; }
  while (rest % 5n === 0n) { rest /= 5n; fives++; }
  if (rest !== 1n || Math.max(twos, fives) > MAX_INPUT_LENGTH - 3) {
    return `${value.n}/${value.d}`;
  }
  const places = Math.max(twos, fives);
  const digits = (abs(value.n) * 10n ** BigInt(places) / value.d).toString().padStart(places + 1, "0");
  return `${value.n < 0n ? "-" : ""}${digits.slice(0, -places)}.${digits.slice(-places)}`;
}

export interface BeatTimingInput { start: string; duration: string }
export type BeatTimingResult =
  | { ok: true; start: RationalTime; duration: RationalTime; changed: boolean }
  | { ok: false; message: string };

function authored(raw: string, source: RationalTime): RationalTime {
  // A copy-only edit must retain the original pair, even for clocks that do
  // not have a finite decimal or whose values exceed JavaScript's safe integer.
  if (raw === formatBeatTime(source)) return { ...source };
  const parsed = parseBeatTime(raw);
  return compare(stored(parsed), stored(source)) === 0 ? { ...source } : parsed;
}

export function inspectBeatTiming(
  beat: Pick<Beat, "start" | "duration">,
  input: BeatTimingInput,
  sceneDuration: RationalTime,
): BeatTimingResult {
  try {
    const start = authored(input.start, beat.start);
    const duration = authored(input.duration, beat.duration);
    const startValue = stored(start);
    const durationValue = stored(duration);
    const sceneValue = stored(sceneDuration);
    if (startValue.n < 0n) throw new Error("Beat start must be zero or later.");
    if (durationValue.n <= 0n) throw new Error("Beat duration must be greater than zero.");
    if (sceneValue.n <= 0n || compare(checkedEnd(startValue, durationValue), sceneValue) > 0) {
      throw new Error("The whole beat must fit inside its scene.");
    }
    return {
      ok: true, start, duration,
      changed: compare(startValue, stored(beat.start)) !== 0 || compare(durationValue, stored(beat.duration)) !== 0,
    };
  } catch (error) {
    return { ok: false, message: error instanceof Error ? error.message : String(error) };
  }
}

export type BeatCoverage =
  | { ok: true; beats: Beat[]; exact: boolean; firstGap: { start: RationalTime; duration: RationalTime } | null }
  | { ok: false; message: string };

/** Shared half-open boundaries decide coverage and the next exact gap. */
export function inspectBeatCoverage(beats: readonly Beat[], sceneDuration: RationalTime): BeatCoverage {
  try {
    const scene = stored(sceneDuration);
    if (scene.n <= 0n) throw new Error("The saved scene duration must be positive.");
    const sorted = [...beats].sort((a, b) => compare(stored(a.start), stored(b.start)) || a.id.localeCompare(b.id));
    let cursor: Fraction = { n: 0n, d: 1n };
    let exact = true;
    let gap: { start: Fraction; duration: Fraction } | null = null;
    for (const beat of sorted) {
      const start = stored(beat.start);
      const duration = stored(beat.duration);
      if (start.n < 0n || duration.n <= 0n) throw new Error("A saved beat has an invalid range.");
      const end = checkedEnd(start, duration);
      if (compare(end, scene) > 0) throw new Error("A saved beat extends beyond its scene.");
      const delta = compare(start, cursor);
      if (delta > 0 && !gap) gap = { start: cursor, duration: subtract(start, cursor) };
      if (delta !== 0) exact = false;
      if (compare(end, cursor) > 0) cursor = end;
    }
    if (compare(cursor, scene) < 0 && !gap) gap = { start: cursor, duration: subtract(scene, cursor) };
    if (compare(cursor, scene) !== 0) exact = false;
    return { ok: true, beats: sorted, exact,
      firstGap: gap ? { start: editable(gap.start), duration: editable(gap.duration) } : null };
  } catch (error) {
    return { ok: false, message: error instanceof Error ? error.message : String(error) };
  }
}
