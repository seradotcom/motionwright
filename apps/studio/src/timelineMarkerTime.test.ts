import { describe, expect, it } from "vitest";
import { timelineMarkerTime } from "./timelineMarkerTime";
import { editorFps, projectFrameIndex, stepEditorialFrame } from "./timecode";
import { seconds } from "./types";
import type { RationalTime } from "./types";

const r = (num: string, den = "1"): RationalTime => ({ num, den });
const frameCases = [
  { label: "24 fps frame 2", rate: r("24"), frame: 2, exact: r("1", "12"), continuous: r("83", "1000") },
  { label: "24 fps frame 5", rate: r("24"), frame: 5, exact: r("5", "24"), continuous: r("26", "125") },
  { label: "30 fps frame 1", rate: r("30"), frame: 1, exact: r("1", "30"), continuous: r("33", "1000") },
  { label: "30 fps frame 4", rate: r("30"), frame: 4, exact: r("2", "15"), continuous: r("133", "1000") },
  { label: "30000/1001 fps frame 1", rate: r("30000", "1001"), frame: 1, exact: r("1001", "30000"), continuous: r("33", "1000") },
  { label: "30000/1001 fps frame 3", rate: r("30000", "1001"), frame: 3, exact: r("1001", "10000"), continuous: r("1", "10") },
  { label: "30000/1001 fps frame 9", rate: r("30000", "1001"), frame: 9, exact: r("3003", "10000"), continuous: r("3", "10") },
];

// Adjacent positive IEEE-754 values, without an arbitrary decimal epsilon.
function neighbor(value: number, direction: -1 | 1): number {
  const bytes = new DataView(new ArrayBuffer(8));
  bytes.setFloat64(0, value);
  bytes.setBigUint64(0, bytes.getBigUint64(0) + BigInt(direction));
  return bytes.getFloat64(0);
}

describe("timeline marker frame time", () => {
  it.each(frameCases)("preserves $label through steps, stored time and selection", ({ rate, frame, exact }) => {
    let playhead = 0;
    for (let index = 0; index < frame; index++) playhead = stepEditorialFrame(playhead, rate, 1, 10);
    const markerTime = timelineMarkerTime(playhead, rate);
    // These rational oracles are literals, independent of graphFrameTime.
    expect(markerTime).toStrictEqual(exact);
    const selectedPlayhead = seconds(markerTime);
    expect(projectFrameIndex(selectedPlayhead, rate)).toBe(frame);
    expect(timelineMarkerTime(selectedPlayhead, rate)).toStrictEqual(exact);
    expect(projectFrameIndex(stepEditorialFrame(selectedPlayhead, rate, 1, 10), rate)).toBe(frame + 1);
    expect(projectFrameIndex(stepEditorialFrame(selectedPlayhead, rate, -1, 10), rate)).toBe(frame - 1);
  });

  it.each(frameCases)("does not snap representable neighbors of $label", ({ rate, frame, exact, continuous }) => {
    const fromStep = frame / editorFps(rate);
    const fromStoredTime = seconds(exact);
    // Both clock projections are legitimate. Inspect the immediately adjacent
    // values outside their interval, including when the projections differ.
    const before = neighbor(Math.min(fromStep, fromStoredTime), -1);
    const after = neighbor(Math.max(fromStep, fromStoredTime), 1);
    for (const value of [before, after]) {
      expect(value).not.toBe(fromStep);
      expect(value).not.toBe(fromStoredTime);
      expect(timelineMarkerTime(value, rate)).toStrictEqual(continuous);
      expect(timelineMarkerTime(value, rate)).not.toStrictEqual(exact);
    }
  });

  it("keeps the existing millisecond conversion for continuous seeks", () => {
    for (const rate of [r("24"), r("30"), r("30000", "1001")]) {
      expect(timelineMarkerTime(0.123456, rate)).toStrictEqual(r("123", "1000"));
    }
  });

  it("does not turn a non-frame project-duration cap into the next frame", () => {
    const rate = r("30");
    const capped = stepEditorialFrame(0.1, rate, 1, 0.123456);
    expect(capped).toBe(0.123456);
    expect(timelineMarkerTime(capped, rate)).toStrictEqual(r("123", "1000"));
    expect(timelineMarkerTime(capped, rate)).not.toStrictEqual(r("2", "15"));
  });

  it("preserves zero and the established negative-time conversion", () => {
    expect(timelineMarkerTime(0, r("30"))).toStrictEqual(r("0"));
    expect(timelineMarkerTime(-0.125, r("30"))).toStrictEqual(r("-1", "8"));
  });

  it.each([
    r("0"), r("30", "0"), r("bad"), r("121"), r("1", "2"),
    r("60", "2"), r("030"),
  ])("keeps the old conversion for an unsupported or noncanonical rate %j", rate => {
    expect(timelineMarkerTime(1 / 30, rate)).toStrictEqual(r("33", "1000"));
  });

  it.each([NaN, Infinity, -Infinity])("retains the existing nonfinite error for %s", playhead => {
    expect(() => timelineMarkerTime(playhead, r("30"))).toThrow("time must be finite");
  });
});
