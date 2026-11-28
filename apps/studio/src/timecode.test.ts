import { describe, expect, it } from "vitest";
import {
  DEFAULT_EDITOR_RATE,
  editorFps,
  formatEditorTimecode,
  formatFrameTimecode,
  parseEditorSeekTime,
  projectFrameIndex,
  rulerTime,
  stepEditorialFrame,
  supportsDropFrame,
  timebaseLabel,
  validEditorRate,
} from "./timecode";
import type { RationalTime } from "./types";

const r = (num: number, den: number): RationalTime => ({ num: String(num), den: String(den) });

describe("editorial display timebase", () => {
  it("never treats frame-rate fractions as rounded decimal authority", () => {
    expect(editorFps(r(30_000, 1_001))).toBeCloseTo(29.9700299700, 8);
    expect(validEditorRate(DEFAULT_EDITOR_RATE)).toBe(true);
    expect(validEditorRate(r(0, 1))).toBe(false);
    expect(validEditorRate(r(30, 0))).toBe(false);
    expect(validEditorRate(r(125, 1))).toBe(false);
    expect(supportsDropFrame(r(24_000, 1_001))).toBe(false);
    expect(supportsDropFrame(r(30_000, 1_001))).toBe(true);
    expect(supportsDropFrame(r(60_000, 1_001))).toBe(true);
  });

  it("labels the first frames correctly at different delivery rates", () => {
    expect(formatEditorTimecode(0, r(25, 1))).toBe("00:00:00");
    expect(formatEditorTimecode(1 / 25, r(25, 1))).toBe("00:00:01");
    expect(formatEditorTimecode(1, r(30, 1))).toBe("00:01:00");
    expect(formatFrameTimecode(25 * 60 * 60, r(25, 1))).toBe("01:00:00:00");
    expect(rulerTime(3601)).toBe("01:00:01");
  });

  it("supports explicit SMPTE drop-frame numbering without assuming DF from rate alone", () => {
    const ntsc = r(30_000, 1_001);
    expect(formatFrameTimecode(1798, ntsc, "df")).toBe("00:59;28");
    expect(formatFrameTimecode(1800, ntsc, "df")).toBe("01:00;02");
    expect(formatFrameTimecode(17_982, ntsc, "df")).toBe("10:00;00");
    expect(formatFrameTimecode(107_892, ntsc, "df")).toBe("01:00:00;00");
    expect(formatFrameTimecode(1800, ntsc, "ndf")).toBe("01:00:00");
    expect(timebaseLabel(ntsc, "df")).toBe("29.97 fps DF");
    expect(timebaseLabel(ntsc, "ndf")).toBe("29.97 fps NDF");
    expect(formatFrameTimecode(3_600, r(60_000, 1_001), "df")).toBe("01:00;04");
  });

  it("parses typed seconds and frame addresses as exact rational positions", () => {
    expect(parseEditorSeekTime("2.125s", DEFAULT_EDITOR_RATE, "ndf")).toEqual(r(17, 8));
    expect(parseEditorSeekTime("60f", r(30_000, 1_001), "ndf")).toEqual(r(1_001, 500));
    expect(parseEditorSeekTime("01:00:00:119", r(120, 1), "ndf")).toEqual(r(432_119, 120));
    expect(() => parseEditorSeekTime("00:00:24", DEFAULT_EDITOR_RATE, "ndf")).toThrow("frame clock");
  });

  it("accepts valid drop-frame addresses and rejects skipped labels", () => {
    const ntsc = r(30_000, 1_001);
    expect(parseEditorSeekTime("01:00;02", ntsc, "df")).toEqual(r(3_003, 50));
    expect(parseEditorSeekTime("10:00;00", ntsc, "df")).toEqual(r(2_999_997, 5_000));
    expect(() => parseEditorSeekTime("01:00;00", ntsc, "df")).toThrow("skipped");
    expect(() => parseEditorSeekTime("01:00:02", ntsc, "df")).toThrow("semicolon");
  });

  it("steps precisely by the selected profile frame duration and respects bounds", () => {
    expect(stepEditorialFrame(0, r(25, 1), 1, 3)).toBeCloseTo(0.04, 12);
    expect(stepEditorialFrame(0, r(30, 1), 1, 3)).toBeCloseTo(1 / 30, 12);
    const ntsc = r(30_000, 1_001);
    const oneFrame = stepEditorialFrame(0, ntsc, 1, 3);
    expect(oneFrame).toBeCloseTo(1001 / 30_000, 12);
    expect(projectFrameIndex(oneFrame, ntsc)).toBe(1);
    expect(stepEditorialFrame(oneFrame, ntsc, -1, 3)).toBe(0);
    expect(stepEditorialFrame(3, ntsc, 1, 3)).toBe(3);
    expect(() => formatFrameTimecode(-1, ntsc)).toThrow("nonnegative safe integer");
    expect(() => stepEditorialFrame(0, ntsc, 1, -2)).toThrow("invalid project duration");
  });
});
