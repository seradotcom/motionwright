import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import {
  acceptNativeWindowReceipt, createNativeWindowIntent, nativeWindowCutRange,
  nativeWindowFrameAtPlayhead, nativeWindowIntentMatches, nativeWindowReuseToken,
  nativeWindowSceneBounds, parseNativeWindowRange,
  clearConfirmedNativeWindow, markNativeWindowUncertain, readNativeWindowUncertainties,
  type NativeWindowIntent,
} from "./nativeWindowPreview";
import type { MotionCanvasFilmOptions, NativeWindowRenderResponse, RationalTime } from "./types";

// Synthetic DTO/selection fixtures only. No SDK job, source PNG, Store or native
// quality observation is represented by these values. The mapping oracle comes
// from the build_motion_canvas_window and variant_scene_sequence contract.
const token = "cb060ec7-a16b-4f38-bae2-66c151058428";
const otherToken = "0237d243-26d0-4fc3-91e8-f0059d9f3fb5";
const time = (num: number | string, den: number | string = 1): RationalTime => ({ num: String(num), den: String(den) });

function selected() {
  const project = structuredClone(fixtureProject);
  project.scenes = project.scenes.slice(0, 2);
  const [scene, preceding] = project.scenes;
  scene.start = time(0); scene.duration = time(3); scene.beats = [];
  preceding.start = time(20); preceding.duration = time(2); preceding.beats = [];
  scene.renderer = preceding.renderer = "motion-canvas";
  const profile = project.deliverables[0];
  profile.frame_rate = time(30);
  profile.included_scene_ids = [preceding.id, scene.id];
  const options: MotionCanvasFilmOptions = {
    frame_rate: { num: 30, den: 1 }, font_family: "Instrument Sans Variable", mono_font_family: "IBM Plex Mono",
    scene_intents: [{ scene_id: scene.id, role: "hook", archetype: "statement" },
      { scene_id: preceding.id, role: "problem", archetype: "diagram_build" }],
  };
  const range = { start: 1, end: 89 };
  const intent = createNativeWindowIntent(project, scene, profile, range, options, "synthetic-window-intent-1");
  return { project, scene, preceding, profile, options, range, intent };
}

function reply(intent: NativeWindowIntent, segmentStart = 61): NativeWindowRenderResponse {
  const count = intent.window.source_window.end - intent.window.source_window.start;
  const end = segmentStart + count;
  const warm = intent.reuse_token !== null;
  return {
    window: {
      generation: intent.generation, revision: intent.revision,
      deliverable_id: intent.window.deliverable_id, source_scene_id: intent.window.source_scene_id,
      source_window: structuredClone(intent.window.source_window), segment_window: { start: segmentStart, end },
      original_prefix: { start: 0, end }, frame_rate: structuredClone(intent.window.options.frame_rate),
      preview_only: true, master_eligible: false,
      validation: { verdict: "UNKNOWN", native_frame_coverage: "UNKNOWN", pass_checks: 3, unknown_checks: 1, unknown_findings: 0 },
      cost: { operation: warm ? "explicit_original_reuse" : "native_prefix", original_prefix_pngs: end,
        selected_pngs: count, reused_original_pngs: warm ? count : 0, start_calls_observed: warm ? 0 : 1,
        cached_png_bytes_returned: warm ? count * 70 : 0, cache_payload_bytes_admitted: warm ? 0 : count * 70,
        cache_micros: 20, internal_simulation_steps: null, internal_simulation_observation: "not_observed" },
    },
    preview: { token: intent.reuse_token ?? token, segment_id: "synthetic-realized-segment",
      scene_ids: [intent.window.source_scene_id], frame_count: count },
  };
}

describe("native Window authority uses source frames and the original bounded response", () => {
  it("keeps original source, reordered cut, realized segment and token-local clocks distinct", () => {
    const f = selected();
    const before = structuredClone(f.project);
    const receipt = acceptNativeWindowReceipt(f.intent, reply(f.intent));
    expect(nativeWindowCutRange(f.project, f.scene, f.profile, f.range, f.options.frame_rate)).toEqual({
      start: time(61, 30), end: time(149, 30),
    });
    expect(nativeWindowFrameAtPlayhead(f.project, f.scene, f.profile, receipt, 1 / 30)).toEqual({
      token, segmentId: "synthetic-realized-segment", frameIndex: 0, frameCount: 88,
      sourceFrame: 1, segmentFrame: 61, cutTime: time(61, 30),
    });
    expect(nativeWindowFrameAtPlayhead(f.project, f.scene, f.profile, receipt, 88 / 30)?.frameIndex).toBe(87);
    for (const uncovered of [0, 0.5 / 30, 89 / 30, 90 / 30, 10_000, Number.NaN, Infinity]) {
      expect(nativeWindowFrameAtPlayhead(f.project, f.scene, f.profile, receipt, uncovered)).toBeNull();
    }
    expect(f.project).toEqual(before);
  });

  it("keeps cut time exact across another renderer without deriving that renderer's duration as segment offset", () => {
    const f = selected();
    f.preceding.renderer = "manim-community";
    f.preceding.duration = time(1, 7);
    f.options.scene_intents = f.options.scene_intents.filter(value => value.scene_id === f.scene.id);
    const intent = createNativeWindowIntent(f.project, f.scene, f.profile, f.range, f.options, "synthetic-other-renderer");
    // The Film realization starts a separate Motion Canvas run after the preceding renderer. Its
    // returned segment starts at 1, while the cut still includes the preceding 1/7s.
    const receipt = acceptNativeWindowReceipt(intent, reply(intent, 1));
    const frame = nativeWindowFrameAtPlayhead(f.project, f.scene, f.profile, receipt, 1 / 30);
    expect(frame).toMatchObject({ sourceFrame: 1, segmentFrame: 1, frameIndex: 0, cutTime: time(37, 210) });
  });

  it("uses exact rational scene boundaries at 30000/1001 and never rounds a fractional source boundary", () => {
    const f = selected();
    const rate = { num: 30_000, den: 1001 };
    f.scene.start = time(1001, 30_000); f.scene.duration = time(2002, 30_000);
    f.preceding.start = time(1001, 1000); f.preceding.duration = time(1001, 1000);
    f.profile.frame_rate = time(30_000, 1001); f.options.frame_rate = rate;
    f.profile.included_scene_ids = [f.scene.id, f.preceding.id];
    expect(nativeWindowSceneBounds(f.scene, rate)).toEqual({ start: 1, end: 3 });
    expect(parseNativeWindowRange(f.scene, rate, "1", "3")).toEqual({ start: 1, end: 3 });
    const intent = createNativeWindowIntent(f.project, f.scene, f.profile, { start: 1, end: 3 }, f.options, "synthetic-rational");
    const receipt = acceptNativeWindowReceipt(intent, reply(intent, 0));
    expect(nativeWindowFrameAtPlayhead(f.project, f.scene, f.profile, receipt, 1001 / 30_000)?.frameIndex).toBe(0);
    expect(nativeWindowFrameAtPlayhead(f.project, f.scene, f.profile, receipt, 3003 / 30_000)).toBeNull();
    f.scene.start = time(1, 30);
    expect(() => nativeWindowSceneBounds(f.scene, rate)).toThrow();
    expect(() => parseNativeWindowRange(f.scene, rate, "1", "2")).toThrow();
  });

  it("retains separate source, selected-count and prefix limits, including a late source in a short cut", () => {
    const f = selected();
    for (const raw of ["", "1e", "1.5", "-1", "Infinity"]) {
      expect(() => parseNativeWindowRange(f.scene, f.options.frame_rate, raw, "89")).toThrow();
    }
    for (const range of [["1", "1"], ["2", "1"], ["89", "91"]]) {
      expect(() => parseNativeWindowRange(f.scene, f.options.frame_rate, range[0], range[1])).toThrow();
    }
    f.scene.start = time(3000); f.scene.duration = time(3);
    f.profile.included_scene_ids = [f.scene.id];
    f.options.scene_intents = f.options.scene_intents.filter(value => value.scene_id === f.scene.id);
    const range = parseNativeWindowRange(f.scene, f.options.frame_rate, "90001", "90003");
    const intent = createNativeWindowIntent(f.project, f.scene, f.profile, range, f.options, "synthetic-late-source");
    const receipt = acceptNativeWindowReceipt(intent, reply(intent, 1));
    expect(nativeWindowFrameAtPlayhead(f.project, f.scene, f.profile, receipt, 3000 + 1 / 30))
      .toMatchObject({ frameIndex: 0, sourceFrame: 90_001, segmentFrame: 1 });
    const unboundedPrefix = reply(intent, 72_000);
    expect(() => acceptNativeWindowReceipt(intent, unboundedPrefix)).toThrow();
    f.scene.start = time(0); f.scene.duration = time(40_000);
    expect(parseNativeWindowRange(f.scene, f.options.frame_rate, "0", "4096")).toEqual({ start: 0, end: 4096 });
    expect(() => parseNativeWindowRange(f.scene, f.options.frame_rate, "0", "4097")).toThrow();
    expect(() => parseNativeWindowRange(f.scene, f.options.frame_rate, "1079999", "1080001")).toThrow();
  });

  it("invalidates old scope, selected profile, scene and intent while owning copies of the original request and reply", () => {
    const f = selected();
    const wire = reply(f.intent);
    const receipt = acceptNativeWindowReceipt(f.intent, wire);
    expect(nativeWindowIntentMatches(f.intent, f.project, f.scene, f.profile, f.range, f.options)).toBe(true);
    for (const project of [
      { ...f.project, id: "another-project" }, { ...f.project, generation: "another-generation" },
      { ...f.project, revision: f.project.revision + 1 }, { ...f.project, active_branch: "another-branch" },
    ]) {
      expect(nativeWindowIntentMatches(f.intent, project, f.scene, f.profile, f.range, f.options)).toBe(false);
      expect(nativeWindowFrameAtPlayhead(project, f.scene, f.profile, receipt, 1 / 30)).toBeNull();
    }
    expect(nativeWindowIntentMatches(f.intent, f.project, f.preceding, f.profile, f.range, f.options)).toBe(false);
    expect(nativeWindowIntentMatches(f.intent, f.project, f.scene, f.project.deliverables[1], f.range, f.options)).toBe(false);
    expect(nativeWindowIntentMatches(f.intent, f.project, f.scene, f.profile, { start: 2, end: 89 }, f.options)).toBe(false);
    f.options.font_family = "Changed after the original intent";
    expect(nativeWindowIntentMatches(f.intent, f.project, f.scene, f.profile, f.range, f.options)).toBe(false);
    expect(f.intent.window.options.font_family).toBe("Instrument Sans Variable");
    wire.preview.token = otherToken;
    expect(receipt.response.preview.token).toBe(token);
  });

  it("rejects foreign, unbounded or promoted replies without manufacturing a Full receipt", () => {
    const f = selected();
    const changes: Array<(value: NativeWindowRenderResponse) => void> = [
      value => { value.window.generation = "foreign-generation"; },
      value => { value.window.revision++; },
      value => { value.window.deliverable_id = f.project.deliverables[1].id; },
      value => { value.window.source_scene_id = f.preceding.id; },
      value => { value.window.source_window.start++; },
      value => { value.window.frame_rate.num = 24; },
      value => { value.window.segment_window.end++; },
      value => { value.window.original_prefix.start = 1; },
      value => { value.preview.frame_count--; },
      value => { value.preview.scene_ids.push(f.preceding.id); },
      value => { value.preview.token = ""; },
      value => { Reflect.set(value.window, "master_eligible", true); },
      value => { Reflect.set(value.window, "preview_only", false); },
      value => { Reflect.set(value.window.validation, "verdict", "FAIL"); },
      value => { value.window.validation.verdict = "PASS"; },
      value => { value.window.validation.native_frame_coverage = "PASS"; value.window.validation.pass_checks = 0; },
      value => { value.window.cost.selected_pngs++; },
      value => { value.window.cost.cache_payload_bytes_admitted = 0; },
      value => { Reflect.set(value.window.cost, "internal_simulation_steps", 1); },
    ];
    for (const change of changes) {
      const value = reply(f.intent); change(value);
      expect(() => acceptNativeWindowReceipt(f.intent, value)).toThrow();
    }
    const accepted = acceptNativeWindowReceipt(f.intent, reply(f.intent));
    expect(accepted.kind).toBe("window");
    expect(accepted.response).not.toHaveProperty("segments");
    expect(accepted.response.window.validation.verdict).toBe("UNKNOWN");
    expect(accepted.response.window.master_eligible).toBe(false);
  });

  it("preserves an observed PASS or stored receipt replay while keeping Window authority preview-only", () => {
    const f = selected();
    // This window reaches the end of the150-frame B,A run. The selected PNGs
    // remain only source[1,90), even though its prefix covers the complete Film.
    const intent = createNativeWindowIntent(f.project, f.scene, f.profile, { start: 1, end: 90 }, f.options,
      "synthetic-complete-prefix-still-window");
    const value = reply(intent);
    value.window.validation = { verdict: "PASS", native_frame_coverage: "PASS", pass_checks: 4, unknown_checks: 0, unknown_findings: 0 };
    value.window.cost.start_calls_observed = 0;
    const receipt = acceptNativeWindowReceipt(intent, value);
    expect(receipt.kind).toBe("window");
    expect(receipt.response.window).toMatchObject({ preview_only: true, master_eligible: false,
      validation: { verdict: "PASS" }, cost: { operation: "native_prefix", start_calls_observed: 0 } });
    expect(receipt.response).not.toHaveProperty("project_resource");
    expect(receipt.response).not.toHaveProperty("segments");
    // The otherwise valid PASS receipt isolates enum types from count checks:
    // String(["PASS"]) is not evidence that the backend returned a PASS string.
    for (const key of ["verdict", "native_frame_coverage"] as const) {
      const arrayEnum = structuredClone(value);
      Reflect.set(arrayEnum.window.validation, key, ["PASS"]);
      expect(() => acceptNativeWindowReceipt(intent, arrayEnum)).toThrow();
    }
  });

  it("requires explicit warm identity and the same original token, with no cold operation hidden in reuse", () => {
    const f = selected();
    const original = acceptNativeWindowReceipt(f.intent, reply(f.intent));
    const next = createNativeWindowIntent(f.project, f.scene, f.profile, f.range, f.options, "synthetic-new-explicit-request");
    expect(next.request_id).not.toBe(f.intent.request_id);
    expect(nativeWindowReuseToken(next, original)).toBe(token);
    const warm = createNativeWindowIntent(f.project, f.scene, f.profile, f.range, f.options, next.request_id, token);
    expect(acceptNativeWindowReceipt(warm, reply(warm)).response.window.cost).toMatchObject({
      operation: "explicit_original_reuse", start_calls_observed: 0, reused_original_pngs: 88, cache_payload_bytes_admitted: 0,
    });
    const wrongToken = reply(warm); wrongToken.preview.token = otherToken;
    const coldFallback = reply(warm); coldFallback.window.cost.operation = "native_prefix";
    const newStart = reply(warm); newStart.window.cost.start_calls_observed = 1;
    const payloadWrite = reply(warm); payloadWrite.window.cost.cache_payload_bytes_admitted = 1;
    const absentCachedBytes = reply(warm); absentCachedBytes.window.cost.cached_png_bytes_returned = 0;
    for (const value of [wrongToken, coldFallback, newStart, payloadWrite, absentCachedBytes]) {
      expect(() => acceptNativeWindowReceipt(warm, value)).toThrow();
    }
    const changed = createNativeWindowIntent(f.project, f.scene, f.profile, { start: 2, end: 89 }, f.options, "synthetic-changed-window");
    expect(nativeWindowReuseToken(changed, original)).toBeNull();
    expect(nativeWindowReuseToken(next, null)).toBeNull();
    expect(f.intent.reuse_token).toBeNull();
    expect(original.response.preview.token).toBe(token);
  });

  it("retains independent unresolved attempts and refuses peer races, unconfirmed writes or changed original bytes", () => {
    const f = selected();
    const peer = createNativeWindowIntent(f.project, f.scene, f.profile, f.range, f.options, "synthetic-peer-request");
    const memory = () => {
      const values = new Map<string, string>();
      const surface = {
        get length() { return values.size; },
        key(index: number) { return [...values.keys()][index] ?? null; },
        getItem(key: string) { return values.get(key) ?? null; },
        setItem(key: string, raw: string) { values.set(key, raw); },
        removeItem(key: string) { values.delete(key); },
      };
      return { values, surface };
    };
    const ownStore = memory(), peerStore = memory();
    const own = markNativeWindowUncertain(ownStore.surface, f.intent);
    const peerCopy = markNativeWindowUncertain(peerStore.surface, peer);
    expect(own.key).not.toBe(peerCopy.key);
    const interleaved = memory();
    const write = interleaved.surface.setItem;
    interleaved.surface.setItem = (key, raw) => {
      write(key, raw);
      // A different document publishes its own already-serialized intent after
      // this writer starts. Neither document owns the other's marker bytes.
      if (key === own.key) interleaved.values.set(peerCopy.key, peerCopy.raw);
    };
    expect(() => markNativeWindowUncertain(interleaved.surface, f.intent)).toThrow();
    // The rejected writer is known NOT_SENT: it may retire only its own exact
    // marker, leaving the already-published peer's unresolved bytes untouched.
    expect([...interleaved.values]).toEqual([[peerCopy.key, peerCopy.raw]]);
    expect(readNativeWindowUncertainties(interleaved.surface, f.project)).toEqual([peer]);
    expect(interleaved.surface.getItem(own.key)).toBeNull();
    expect(interleaved.surface.getItem(peerCopy.key)).toBe(peerCopy.raw);
    clearConfirmedNativeWindow(interleaved.surface, peerCopy);
    expect(readNativeWindowUncertainties(interleaved.surface, f.project)).toEqual([]);

    ownStore.values.set(peerCopy.key, peerCopy.raw);
    ownStore.values.set(own.key, own.raw + " ");
    expect(() => clearConfirmedNativeWindow(ownStore.surface, own)).toThrow();
    expect(ownStore.surface.getItem(own.key)).toBe(own.raw + " ");
    expect(ownStore.surface.getItem(peerCopy.key)).toBe(peerCopy.raw);
    ownStore.values.set(own.key, own.raw);
    clearConfirmedNativeWindow(ownStore.surface, own);
    expect(readNativeWindowUncertainties(ownStore.surface, f.project)).toEqual([peer]);
    expect([...ownStore.values]).toEqual([[peerCopy.key, peerCopy.raw]]);
    const advanced = structuredClone(f.project);
    advanced.revision++;
    advanced.branches.find(branch => branch.id === advanced.active_branch)!.head_revision = advanced.revision;
    expect(readNativeWindowUncertainties(ownStore.surface, advanced)).toEqual([]);
    expect(ownStore.surface.getItem(peerCopy.key)).toBe(peerCopy.raw);

    const changedBeforeConfirmation = memory();
    changedBeforeConfirmation.surface.setItem = (key, raw) => { changedBeforeConfirmation.values.set(key, raw + " "); };
    expect(() => markNativeWindowUncertain(changedBeforeConfirmation.surface, f.intent)).toThrow();
    expect(changedBeforeConfirmation.surface.getItem(own.key)).toBe(own.raw + " ");

    const silent = memory();
    silent.surface.setItem = () => { /* Explicitly synthetic lost local write. */ };
    expect(() => markNativeWindowUncertain(silent.surface, f.intent)).toThrow();
    expect(silent.values.size).toBe(0);
    const quota = memory();
    quota.surface.setItem = () => { throw new DOMException("Synthetic quota", "QuotaExceededError"); };
    expect(() => markNativeWindowUncertain(quota.surface, f.intent)).toThrow();
    expect(quota.values.size).toBe(0);
    peerStore.values.set(peerCopy.key, "{incomplete preserved original");
    expect(() => readNativeWindowUncertainties(peerStore.surface, f.project)).toThrow();
    expect(peerStore.surface.getItem(peerCopy.key)).toBe("{incomplete preserved original");
  });
});
