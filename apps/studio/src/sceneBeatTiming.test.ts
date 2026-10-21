import { describe, expect, it } from "vitest";
import type { Beat, RationalTime } from "./types";
import { formatBeatTime, inspectBeatCoverage, inspectBeatTiming, parseBeatTime } from "./sceneBeatTiming";

const r = (num: string, den = "1"): RationalTime => ({ num, den });
const beat = (id: string, start: RationalTime, duration: RationalTime): Beat => ({
  id, label: id, objective: "Preserve authored time", start, duration,
});

describe("exact scene beat authoring", () => {
  it("preserves the original fractional frame pairs when only copy changes", () => {
    const source = beat("frame", r("1001", "30000"), r("1001", "30000"));
    expect(inspectBeatTiming(source, {
      start: formatBeatTime(source.start), duration: formatBeatTime(source.duration),
    }, r("2"))).toEqual({ ok: true, start: source.start, duration: source.duration, changed: false });
    expect(formatBeatTime(source.start)).toBe("1001/30000");
    expect(source.start).toEqual(r("1001", "30000"));
  });

  it("does not route stored integer parts through IEEE-754", () => {
    const source = beat("large-parts", r("9007199254740993", "18014398509481987"), r("1", "2"));
    expect(formatBeatTime(source.start)).toBe("9007199254740993/18014398509481987");
    expect(inspectBeatTiming(source, {
      start: "9007199254740993/18014398509481987", duration: "0.5",
    }, r("1"))).toEqual({ ok: true, start: source.start, duration: source.duration, changed: false });
  });

  it("retains the original wire pair for an equivalent spelling", () => {
    const source = beat("wire", r("1", "5"), r("1", "30"));
    expect(inspectBeatTiming(source, { start: " 0.200 ", duration: "1000/30000" }, r("1")))
      .toEqual({ ok: true, start: r("1", "5"), duration: r("1", "30"), changed: false });
  });

  it("parses complete authored decimals, fractions and exponents exactly", () => {
    expect(parseBeatTime("0.01001")).toEqual(r("1001", "100000"));
    expect(parseBeatTime("0.1234567")).toEqual(r("1234567", "10000000"));
    expect(parseBeatTime("1.001e-3")).toEqual(r("1001", "1000000"));
    expect(parseBeatTime("+.25")).toEqual(r("1", "4"));
    expect(parseBeatTime("5.e-1")).toEqual(r("1", "2"));
    expect(parseBeatTime("1/48000")).toEqual(r("1", "48000"));
    expect(parseBeatTime("-0")).toEqual(r("0"));
  });

  it("formats exact finite decimals and repeating fractions without rounding", () => {
    expect(formatBeatTime(r("1", "8"))).toBe("0.125");
    expect(formatBeatTime(r("1", "40"))).toBe("0.025");
    expect(formatBeatTime(r("-1", "8"))).toBe("-0.125");
    expect(formatBeatTime(r("1", "48000"))).toBe("1/48000");
    expect(formatBeatTime(r("0"))).toBe("0");
  });

  it("rejects incomplete and invalid input without converting it to zero", () => {
    for (const input of ["", " ", "1e", "1e-", ".", "-", "1/", "1/0", "1/-2", "Infinity", "NaN", "0x10", "1,25"]) {
      expect(() => parseBeatTime(input), input).toThrow();
    }
    const source = beat("partial", r("1", "4"), r("1", "4"));
    const input = { start: "1e", duration: "0.25" };
    expect(inspectBeatTiming(source, input, r("1")).ok).toBe(false);
    expect(input.start).toBe("1e");
    expect(source.start).toEqual(r("1", "4"));
  });

  it("bounds new rational parts and exponent work before returning a change", () => {
    expect(parseBeatTime("9223372036854775807")).toEqual(r("9223372036854775807"));
    expect(parseBeatTime("1/9223372036854775807")).toEqual(r("1", "9223372036854775807"));
    expect(() => parseBeatTime("9223372036854775808")).toThrow("exact input range");
    expect(() => parseBeatTime("1/9223372036854775808")).toThrow("exact input range");
    expect(() => parseBeatTime("1e1000000")).toThrow("exact input range");
    expect(() => parseBeatTime("1e97")).toThrow("exact input range");
    expect(() => parseBeatTime("1".repeat(97))).toThrow();
    expect(parseBeatTime("9223372036854775808/9223372036854775808")).toEqual(r("1"));
  });

  it("detects a changed sub-millisecond value and validates exact scene bounds", () => {
    const source = beat("edited", r("0"), r("1", "2"));
    expect(inspectBeatTiming(source, { start: "0.0000001", duration: "0.5" }, r("1")))
      .toEqual({ ok: true, start: r("1", "10000000"), duration: r("1", "2"), changed: true });
    expect(inspectBeatTiming(source, { start: "-0.01", duration: "0.5" }, r("1")).ok).toBe(false);
    expect(inspectBeatTiming(source, { start: "0", duration: "0" }, r("1")).ok).toBe(false);
    expect(inspectBeatTiming(source, { start: "1/3", duration: "2/3" }, r("1")).ok).toBe(true);
    expect(inspectBeatTiming(source, {
      start: "1/3", duration: "20000000000000001/30000000000000000",
    }, r("1")).ok).toBe(false);
  });

  it("matches the domain when individually valid parts have an unrepresentable sum", () => {
    const source = beat("sum", r("0"), r("1"));
    const result = inspectBeatTiming(source, {
      start: "1/9223372036854775807", duration: "1/9223372036854775806",
    }, r("2"));
    expect(result).toEqual({ ok: false,
      message: "These times cannot form an exact interval within the saved time range. Use simpler fractions." });
  });

  it("rejects a noncanonical saved pair instead of normalizing source state silently", () => {
    for (const source of [r("2", "10"), r("01"), r("-0"), r("1", "0"), r("1", "9223372036854775808")]) {
      expect(() => formatBeatTime(source)).toThrow();
    }
  });
});

describe("exact scene beat coverage", () => {
  it("returns the first shared-boundary gap without quantizing it to milliseconds", () => {
    const tail = beat("tail", r("1001", "30000"), r("58999", "30000"));
    const before = inspectBeatCoverage([tail], r("2"));
    expect(before).toEqual({ ok: true, beats: [tail], exact: false,
      firstGap: { start: r("0"), duration: r("1001", "30000") } });
    const head = beat("head", r("0"), r("1001", "30000"));
    expect(inspectBeatCoverage([tail, head], r("2")))
      .toEqual({ ok: true, beats: [head, tail], exact: true, firstGap: null });
  });

  it("keeps a real sub-microsecond uncovered interval visible", () => {
    const head = beat("head", r("0"), r("19999999", "10000000"));
    expect(inspectBeatCoverage([head], r("2"))).toEqual({
      ok: true, beats: [head], exact: false,
      firstGap: { start: r("19999999", "10000000"), duration: r("1", "10000000") },
    });
  });

  it("does not certify an overlap even when the union covers the full scene", () => {
    const head = beat("head", r("0"), r("1"));
    const tail = beat("tail", r("9999999", "10000000"), r("10000001", "10000000"));
    expect(inspectBeatCoverage([tail, head], r("2")))
      .toEqual({ ok: true, beats: [head, tail], exact: false, firstGap: null });
  });

  it("uses the full scene as the first gap when no authored beats exist", () => {
    expect(inspectBeatCoverage([], r("1001", "30000"))).toEqual({
      ok: true, beats: [], exact: false,
      firstGap: { start: r("0"), duration: r("1001", "30000") },
    });
  });

  it("rejects invalid persisted intervals rather than displaying ready coverage", () => {
    expect(inspectBeatCoverage([beat("outside", r("1"), r("10000001", "10000000"))], r("2")).ok).toBe(false);
    expect(inspectBeatCoverage([beat("negative", r("-1"), r("1"))], r("2")).ok).toBe(false);
    expect(inspectBeatCoverage([beat("zero", r("0"), r("0"))], r("2")).ok).toBe(false);
    expect(inspectBeatCoverage([], r("1", "0")).ok).toBe(false);
  });
});
