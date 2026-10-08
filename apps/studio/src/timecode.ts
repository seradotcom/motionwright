import type { RationalTime } from "./types";

/**
 * Editorial timecode is a display projection only. Project timestamps and
 * renderer frame ranges remain rational and are owned by the native domain.
 */
export type TimecodeMode = "ndf" | "df";
export const DEFAULT_EDITOR_RATE: RationalTime = { num: "24", den: "1" };

export function validEditorRate(rate: RationalTime | null | undefined): rate is RationalTime {
  if (!rate || !/^\d+$/.test(rate.num) || !/^\d+$/.test(rate.den)) return false;
  const n = Number(rate.num);
  const d = Number(rate.den);
  return Number.isSafeInteger(n) && Number.isSafeInteger(d)
    && n > 0 && d > 0 && n / d >= 1 && n / d <= 120;
}

export function editorFps(rate: RationalTime): number {
  if (!validEditorRate(rate)) throw new Error("unsupported editorial frame rate");
  return Number(rate.num) / Number(rate.den);
}

export function supportsDropFrame(rate: RationalTime): boolean {
  if (!validEditorRate(rate)) return false;
  const n = BigInt(rate.num);
  const d = BigInt(rate.den);
  return n * 1001n === 30_000n * d || n * 1001n === 60_000n * d;
}

export function timebaseLabel(rate: RationalTime, mode: TimecodeMode): string {
  const fps = editorFps(rate);
  const display = String(Number(fps.toFixed(3)));
  return display + " fps " + (supportsDropFrame(rate) && mode === "df" ? "DF" : "NDF");
}

export function projectFrameIndex(seconds: number, rate: RationalTime): number {
  if (!Number.isFinite(seconds)) throw new Error("editorial time must be finite");
  return Math.max(0, Math.floor(Math.max(0, seconds) * editorFps(rate) + 1e-7));
}

export function stepEditorialFrame(
  seconds: number,
  rate: RationalTime,
  direction: -1 | 1,
  duration: number,
): number {
  if (!Number.isFinite(duration) || duration < 0) throw new Error("invalid project duration");
  const rateValue = editorFps(rate);
  const nextFrame = Math.max(0, projectFrameIndex(seconds, rate) + direction);
  return Math.min(duration, nextFrame / rateValue);
}

export function formatFrameTimecode(
  frameNumber: number,
  rate: RationalTime,
  mode: TimecodeMode = "ndf",
): string {
  if (!Number.isSafeInteger(frameNumber) || frameNumber < 0) {
    throw new Error("frame index must be a nonnegative safe integer");
  }
  const nominal = Math.round(editorFps(rate));
  const df = supportsDropFrame(rate) && mode === "df";
  let labelFrame = frameNumber;
  if (df) {
    // Drop *labels*, never video frames. Tenth minutes preserve every label.
    const droppedPerMinute = nominal === 60 ? 4 : 2;
    const framesPerTenMinutes = nominal * 600 - droppedPerMinute * 9;
    const framesPerMinute = nominal * 60 - droppedPerMinute;
    const blocks = Math.floor(frameNumber / framesPerTenMinutes);
    const remainder = frameNumber % framesPerTenMinutes;
    const minutesWithinBlock = Math.max(0, Math.floor((remainder - droppedPerMinute) / framesPerMinute));
    labelFrame += droppedPerMinute * (blocks * 9 + minutesWithinBlock);
  }
  const pad = (value: number) => String(value).padStart(2, "0");
  const frame = labelFrame % nominal;
  const totalSeconds = Math.floor(labelFrame / nominal);
  const second = totalSeconds % 60;
  const minute = Math.floor(totalSeconds / 60) % 60;
  const hour = Math.floor(totalSeconds / 3600);
  const separator = df ? ";" : ":";
  const prefix = hour ? pad(hour) + ":" : "";
  return prefix + pad(minute) + ":" + pad(second) + separator + pad(frame);
}

export function formatEditorTimecode(
  seconds: number,
  rate: RationalTime,
  mode: TimecodeMode = "ndf",
): string {
  return formatFrameTimecode(projectFrameIndex(seconds, rate), rate, mode);
}

export function rulerTime(seconds: number): string {
  const safe = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  const pad = (v: number) => String(v).padStart(2, "0");
  const hh = Math.floor(safe / 3600);
  const mm = Math.floor((safe % 3600) / 60);
  const ss = safe % 60;
  return hh ? pad(hh) + ":" + pad(mm) + ":" + pad(ss) : pad(mm) + ":" + pad(ss);
}
