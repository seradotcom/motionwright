import { describe, expect, it } from "vitest";
import {
  createNativeAvReadGuard, createNativeAvReviewSelectionStore, formatAudioMilli,
  nativeAvReadScope, nativeAvSelectionFromEvidence, readNativeAvAudioQcResponse, readNativeAudioQcReceiptInventoryResponse,
} from "./nativeAvAudioQc";
import type {
  DeliveryAudioQcReport, MltAvMasterEvidence, NativeMultisegmentAvMasterEvidence,
} from "./types";

// Synthetic public-wire fixtures for pure reader/state tests. They are not
// native measurements, app execution, SQLite evidence or listening approval.
const masterSha = "1".repeat(64), decodedSha = "2".repeat(64), proofSha = "3".repeat(64);
const source = { id: "unit-project", generation: "unit-generation", active_branch: "main", revision: 17 };
const profileId = "unit-deliverable";
const invalidMessage = "The native audio QC response is unavailable or invalid.";

function reportFixture(): DeliveryAudioQcReport {
  return {
    schema: "motionwright.delivery-audio-qc-report/2",
    qc: {
      schema: "motionwright.delivery-audio-qc/1", status: "within_targets",
      targets: { target_lufs: -23.001, target_true_peak_dbtp: -1.001 },
      target_lufs_milli: -23001, target_true_peak_millidbtp: -1001,
      measured_integrated_lufs_milli: -23001, measured_true_peak_millidbtp: -1001,
      lufs_delta_milli: 0, lufs_tolerance_milli: 200,
      lufs_status: "within_targets", true_peak_status: "within_targets",
      evaluated_phase: "decoded_mlt_master", decoded_wav_sha256: decodedSha,
      decoded_sample_frames: 96128,
      measurement_receipt_id: "00000000-0000-4000-8000-000000000001",
      measurement_proof_sha256: proofSha,
      evidence_scope: "final-decoded-pcm16-public-milli-numeric-qc-not-mastered-not-intelligibility-not-human-approved",
    },
    measurement_method: { engine: "libebur128", version: "1.2.6", producer_schema_version: 1 },
    decoded_input: {
      format: "wav_pcm_s16le", sha256: decodedSha, sample_rate_hz: 48000,
      channels: 2, channel_layout: "stereo",
      sample_interval: { start_sample_frame: 0, end_sample_frame_exclusive: 96128 },
    },
    measurement_windows: {
      integrated_loudness: "complete_decoded_input_with_provider_gating",
      momentary_window_ms: 400, short_term_window_ms: 3000,
      momentary_and_short_term_position: "ending_at_decoded_input_end",
      loudness_range: "complete_decoded_input_with_provider_gating",
      true_peak: "complete_decoded_input", sample_peak: "complete_decoded_input",
    },
    true_peak_oversample: 4,
    measured_levels: {
      integrated_lufs_milli: -23001, momentary_lufs_milli: -23200, short_term_lufs_milli: null,
      loudness_range_milli: null, true_peak_millidbtp: -1001, sample_peak_millidbfs: -10000,
    },
    units: {
      integrated_lufs_milli: "LUFS", momentary_lufs_milli: "LUFS", short_term_lufs_milli: "LUFS",
      loudness_range_milli: "LU", true_peak_millidbtp: "dBTP", sample_peak_millidbfs: "dBFS",
      target_lufs_milli: "LUFS", target_true_peak_millidbtp: "dBTP",
      lufs_delta_milli: "LU", lufs_tolerance_milli: "LU",
    },
    published_resolution: { numerator: 1, denominator: 1000, rounding_authority: "identified_provider_integer_milli_output" },
    master_artifact_sha256: masterSha,
    evidence_scope: "measured-final-pcm-numeric-report-not-normalization-not-intelligibility-not-human-acceptance",
  };
}

function readReport(value: DeliveryAudioQcReport, expectedMaster: string | null = masterSha) {
  const response = readNativeAvAudioQcResponse({ kind: "report", report: value }, expectedMaster);
  if (response.kind !== "report") throw new Error("Expected the synthetic unit report.");
  return response.report;
}

function replaced(path: readonly string[], value: unknown) {
  const report = reportFixture() as unknown as Record<string, unknown>;
  let item = report;
  for (const key of path.slice(0, -1)) item = item[key] as Record<string, unknown>;
  item[path[path.length - 1]] = value;
  return { kind: "report", report };
}

function multiEvidence(): NativeMultisegmentAvMasterEvidence {
  return {
    project_resource: "project:" + source.id, generation: source.generation,
    revision: source.revision, deliverable_id: profileId, frame_count: 60, video_segments: 2,
    video_codec: "h264", audio_codec: "aac", audio_sample_rate: 48000, audio_channels: 2,
    master_sha256: masterSha, export_token: "opaque-unit-session-token", review_only: true,
    provider_project_cleanup: "not_requested_requires_foreground_broker_consent",
    evidence_scope: "native-captioned-multisegment-av-review-only-not-decoded-pixel-approved",
    audio_qc: { kind: "report", report: reportFixture() },
  };
}

function singleEvidence(): MltAvMasterEvidence {
  return {
    project_resource: "project:" + source.id, generation: source.generation,
    revision: source.revision, deliverable_id: profileId, motion_segment_id: "unit-segment",
    export_token: "opaque-single-unit-token", frame_rate: { num: 30, den: 1 }, frame_count: 60,
    mezzanine: {}, source_audio: { relative_path: "synthetic-unit.wav", sha256: decodedSha, sample_rate: 48000, channels: 2 },
    master: { artifact: { sha256: masterSha } }, decoded_audio: {}, sync: null,
  };
}

function deferred() {
  let resolve!: (value: string) => void;
  const promise = new Promise<string>(done => { resolve = done; });
  return { promise, resolve };
}

describe("native audio QC public reader", () => {
  it("preserves exact milli targets, distinct peak units and the received report without mutation", () => {
    const original = reportFixture();
    const before = JSON.stringify(original);
    const actual = readReport(original);
    expect(actual).toEqual(original);
    expect(actual).not.toBe(original);
    expect(actual.qc.targets).toEqual({ target_lufs: -23.001, target_true_peak_dbtp: -1.001 });
    expect(actual.measured_levels.true_peak_millidbtp).toBe(-1001);
    expect(actual.measured_levels.sample_peak_millidbfs).toBe(-10000);
    expect(actual.units.true_peak_millidbtp).toBe("dBTP");
    expect(actual.units.sample_peak_millidbfs).toBe("dBFS");
    actual.qc.targets!.target_lufs = -14;
    expect(JSON.stringify(original)).toBe(before);
  });

  it("retains unavailable null measurements and a deliberately absent target without replacing either with zero", () => {
    const original = reportFixture();
    original.qc.status = "unavailable";
    original.qc.targets!.target_true_peak_dbtp = null;
    original.qc.target_true_peak_millidbtp = null;
    original.qc.measured_integrated_lufs_milli = null;
    original.qc.measured_true_peak_millidbtp = null;
    original.qc.lufs_delta_milli = null;
    original.qc.lufs_status = "unavailable";
    original.qc.true_peak_status = "not_requested";
    original.measured_levels = { integrated_lufs_milli: null, momentary_lufs_milli: null,
      short_term_lufs_milli: null, loudness_range_milli: null, true_peak_millidbtp: null, sample_peak_millidbfs: null };
    expect(readReport(original)).toEqual(original);
    expect(formatAudioMilli(null)).toBe("Unavailable (null)");
    expect(formatAudioMilli(0)).toBe("0.000");
    expect(formatAudioMilli(-1)).toBe("-0.001");
    expect(formatAudioMilli(-2147483648)).toBe("-2147483.648");
    expect(formatAudioMilli(4294967295)).toBe("4294967.295");
  });

  it("keeps the decoded codec tail and end windows instead of substituting video length or maxima", () => {
    const actual = readReport(reportFixture());
    expect(actual.decoded_input.sample_interval).toEqual({ start_sample_frame: 0, end_sample_frame_exclusive: 96128 });
    expect(actual.decoded_input.sample_interval.end_sample_frame_exclusive).not.toBe(60 / 30 * 48000);
    expect(actual.qc.decoded_sample_frames).toBe(96128);
    expect(actual.measurement_windows.momentary_and_short_term_position).toBe("ending_at_decoded_input_end");
    expect(actual.measurement_windows.momentary_window_ms).toBe(400);
    expect(actual.measurement_windows.short_term_window_ms).toBe(3000);
    expect(actual.measured_levels.short_term_lufs_milli).toBeNull();
  });

  it("retains native within and outside outcomes without recomputing a verdict from the displayed numbers", () => {
    const original = reportFixture();
    expect(readReport(original).qc.status).toBe("within_targets");
    // Deliberate projection-only control: the reader must not become a second
    // analyzer that rewrites a received native outcome from matching values.
    original.qc.status = "outside_targets";
    original.qc.lufs_status = "outside_targets";
    expect(readReport(original).qc.status).toBe("outside_targets");
    expect(readReport(original).qc.lufs_status).toBe("outside_targets");
    expect(readReport(original).qc.measured_integrated_lufs_milli).toBe(-23001);
  });

  it("distinguishes no requested report from a measured report whose numeric targets were not requested", () => {
    expect(readNativeAvAudioQcResponse({ kind: "not_requested" })).toEqual({ kind: "not_requested" });
    const original = reportFixture();
    original.qc.status = original.qc.lufs_status = original.qc.true_peak_status = "not_requested";
    original.qc.targets = null;
    original.qc.target_lufs_milli = original.qc.target_true_peak_millidbtp = original.qc.lufs_delta_milli = null;
    const actual = readReport(original);
    expect(actual.qc.status).toBe("not_requested");
    expect(actual.qc.targets).toBeNull();
    expect(actual.measured_levels.integrated_lufs_milli).toBe(-23001);
    expect(() => readNativeAvAudioQcResponse(undefined)).toThrow(invalidMessage);
  });

  it("requires the exact public envelope and refuses private fields without echoing their diagnostics", () => {
    const rejected = [
      null, [], {}, { kind: "report" }, { kind: "not_requested", report: reportFixture() },
      { kind: "unavailable", reason: "unresolved_receipt", diagnostic: "private-unit-diagnostic" },
      { kind: "report", report: reportFixture(), original_request_id: "private-unit-selector" },
      { kind: "unavailable", reason: "new_unrecognized_reason" },
      { kind: "passed", report: reportFixture() },
    ];
    for (const value of rejected) expect(() => readNativeAvAudioQcResponse(value)).toThrow(invalidMessage);
    expect(() => readNativeAvAudioQcResponse(replaced(["private_proof"], "private-unit-proof"))).toThrow(invalidMessage);
    expect(readNativeAvAudioQcResponse({ kind: "unavailable", reason: "unresolved_receipt" }))
      .toEqual({ kind: "unavailable", reason: "unresolved_receipt" });
    expect(readNativeAvAudioQcResponse({ kind: "unavailable", reason: "session_unavailable" }))
      .toEqual({ kind: "unavailable", reason: "session_unavailable" });
  });

  it("uses the saved target contract to reject incomplete pairs, sub-milli precision and out-of-range targets", () => {
    const rejected = [
      {}, { target_lufs: -23 }, { target_lufs: null, target_true_peak_dbtp: null },
      { target_lufs: -23.0001, target_true_peak_dbtp: null },
      { target_lufs: -2147483.649, target_true_peak_dbtp: null },
      { target_lufs: null, target_true_peak_dbtp: 2147483.648 },
      { target_lufs: NaN, target_true_peak_dbtp: null },
      { target_lufs: "-23", target_true_peak_dbtp: null },
      { target_lufs: -23, target_true_peak_dbtp: null, approved: true },
    ];
    for (const value of rejected) {
      expect(() => readNativeAvAudioQcResponse(replaced(["qc", "targets"], value))).toThrow(invalidMessage);
    }
  });

  it("rejects fractional, missing, nonfinite and out-of-range public measurement values", () => {
    const rejected: Array<[readonly string[], unknown]> = [
      [["measured_levels", "momentary_lufs_milli"], -23000.1],
      [["measured_levels", "momentary_lufs_milli"], -2147483649],
      [["measured_levels", "sample_peak_millidbfs"], Infinity],
      [["measured_levels", "short_term_lufs_milli"], undefined],
      [["measured_levels", "loudness_range_milli"], -1],
      [["measured_levels", "loudness_range_milli"], 4294967296],
      [["qc", "lufs_delta_milli"], 4294967296],
      [["qc", "lufs_tolerance_milli"], -1],
      [["decoded_input", "sample_interval", "end_sample_frame_exclusive"], Number.MAX_SAFE_INTEGER + 1],
      [["decoded_input", "sample_interval", "end_sample_frame_exclusive"], 0],
    ];
    for (const [path, value] of rejected) expect(() => readNativeAvAudioQcResponse(replaced(path, value))).toThrow(invalidMessage);
    const missing = reportFixture();
    delete (missing.measured_levels as unknown as Record<string, unknown>).short_term_lufs_milli;
    expect(() => readReport(missing)).toThrow(invalidMessage);
  });

  it("rejects mismatched redundant identities, interval, metric copies and advertised unit semantics", () => {
    const rejected: Array<[readonly string[], unknown]> = [
      [["qc", "decoded_wav_sha256"], "4".repeat(64)],
      [["qc", "decoded_sample_frames"], 96000],
      [["qc", "measured_true_peak_millidbtp"], -1002],
      [["qc", "measured_integrated_lufs_milli"], -23002],
      [["decoded_input", "sample_interval", "start_sample_frame"], 128],
      [["decoded_input", "sample_rate_hz"], 44100],
      [["units", "true_peak_millidbtp"], "dBFS"],
      [["measurement_windows", "momentary_and_short_term_position"], "maximum_over_master"],
      [["schema"], "motionwright.delivery-audio-qc-report/1"],
      [["published_resolution", "denominator"], 100],
      [["qc", "status"], "PASS"],
    ];
    for (const [path, value] of rejected) expect(() => readNativeAvAudioQcResponse(replaced(path, value))).toThrow(invalidMessage);
  });

  it("binds a report to the expected original master digest and rejects malformed or different expectations", () => {
    expect(readReport(reportFixture(), masterSha).master_artifact_sha256).toBe(masterSha);
    expect(() => readReport(reportFixture(), "4".repeat(64))).toThrow(invalidMessage);
    expect(() => readReport(reportFixture(), "not-a-digest")).toThrow(invalidMessage);
    expect(() => readNativeAvAudioQcResponse(replaced(["master_artifact_sha256"], "1".repeat(63)))).toThrow(invalidMessage);
  });
});

describe("source-bound native read lifecycle", () => {
  it("distinguishes project, generation, branch, revision, profile, token and public master identity", () => {
    const expected = nativeAvReadScope(source, "token-a", profileId, masterSha);
    const other = [
      nativeAvReadScope({ ...source, id: "another-project" }, "token-a", profileId, masterSha),
      nativeAvReadScope({ ...source, generation: "another-generation" }, "token-a", profileId, masterSha),
      nativeAvReadScope({ ...source, active_branch: "another-branch" }, "token-a", profileId, masterSha),
      nativeAvReadScope({ ...source, revision: source.revision + 1 }, "token-a", profileId, masterSha),
      nativeAvReadScope(source, "token-a", "another-profile", masterSha),
      nativeAvReadScope(source, "token-b", profileId, masterSha),
      nativeAvReadScope(source, "token-a", profileId, "4".repeat(64)),
    ];
    expect(new Set(other).size).toBe(7);
    for (const scope of other) expect(scope).not.toBe(expected);
  });

  it("hides a stored read immediately on a context change and never reuses the old key after A to B to A", () => {
    const guard = createNativeAvReadGuard();
    const first = guard.viewKey("source-a");
    expect(guard.viewKey("source-a")).toBe(first);
    const second = guard.viewKey("source-b");
    const returned = guard.viewKey("source-a");
    expect(second).not.toBe(first);
    expect(returned).not.toBe(first);
    const stored = { key: first, report: reportFixture() };
    expect(stored.key === returned).toBe(false);
  });

  it("refuses a late original response as soon as the visible context changes before the next effect begins", async () => {
    const guard = createNativeAvReadGuard();
    const first = guard.viewKey("source-a"), ticket = guard.begin(first);
    let visible = first;
    const pending = deferred();
    const published: string[] = [];
    const completed = pending.promise.then(value => { if (guard.isCurrent(ticket, visible)) published.push(value); });
    visible = guard.viewKey("source-b");
    pending.resolve("old original result");
    await completed;
    expect(published).toEqual([]);
  });

  it("admits only the fresh read after A to B to A even when the first A response resolves last", async () => {
    const guard = createNativeAvReadGuard();
    const oldKey = guard.viewKey("source-a"), oldTicket = guard.begin(oldKey);
    const oldRead = deferred(), freshRead = deferred();
    const published: string[] = [];
    let visible = oldKey;
    const oldCompleted = oldRead.promise.then(value => { if (guard.isCurrent(oldTicket, visible)) published.push(value); });
    const middle = guard.viewKey("source-b");
    guard.begin(middle);
    visible = guard.viewKey("source-a");
    const newTicket = guard.begin(visible);
    const freshCompleted = freshRead.promise.then(value => { if (guard.isCurrent(newTicket, visible)) published.push(value); });
    freshRead.resolve("fresh original read");
    await freshCompleted;
    oldRead.resolve("superseded original read");
    await oldCompleted;
    expect(published).toEqual(["fresh original read"]);
  });

  it("revokes a read on unmount and gives no settled-read authority to a new view with the same scope", async () => {
    const oldView = createNativeAvReadGuard();
    const key = oldView.viewKey("same-source"), ticket = oldView.begin(key);
    const pending = deferred();
    let published = false;
    const completed = pending.promise.then(() => { published = oldView.isCurrent(ticket, key); });
    oldView.cancel(ticket);
    const newView = createNativeAvReadGuard();
    const newKey = newView.viewKey("same-source");
    expect(newView.isCurrent(ticket, newKey)).toBe(false);
    pending.resolve("original read after unmount");
    await completed;
    expect(published).toBe(false);
    const fresh = newView.begin(newKey);
    expect(newView.isCurrent(fresh, newKey)).toBe(true);
  });

  it("supersedes overlapping reads at the same identity without an old cleanup cancelling the new read", () => {
    const guard = createNativeAvReadGuard();
    const key = guard.viewKey("same-source");
    const old = guard.begin(key), latest = guard.begin(key);
    guard.cancel(old);
    expect(guard.isCurrent(old, key)).toBe(false);
    expect(guard.isCurrent(latest, key)).toBe(true);
    guard.cancel(latest);
    expect(guard.isCurrent(latest, key)).toBe(false);
  });
});

describe("session-only original master selectors", () => {
  it("extracts only the exact public selector and retains caption review-only restrictions", () => {
    const evidence = multiEvidence();
    evidence.review_only = false; // The captioned evidence scope independently retains the restriction.
    const selection = nativeAvSelectionFromEvidence(source, profileId, evidence);
    expect(selection).toEqual({ exportToken: evidence.export_token, masterSha256: masterSha, reviewOnly: true });
    expect(Object.keys(selection!).sort()).toEqual(["exportToken", "masterSha256", "reviewOnly"]);
    expect(selection).not.toHaveProperty("audio_qc");
    expect(selection).not.toHaveProperty("report");
    expect(selection).not.toHaveProperty("export_grant");
  });

  it("rejects another project, generation, revision or deliverable before retaining a completed reply", () => {
    const mismatches: Partial<NativeMultisegmentAvMasterEvidence>[] = [
      { project_resource: "project:another" }, { generation: "another-generation" },
      { revision: source.revision + 1 }, { deliverable_id: "another-profile" },
      { export_token: "" }, { export_token: "x".repeat(4097) }, { master_sha256: "not-a-digest" },
    ];
    for (const changed of mismatches) {
      expect(nativeAvSelectionFromEvidence(source, profileId, { ...multiEvidence(), ...changed })).toBeNull();
    }
  });

  it("reads the original single-master artifact digest but does not repair a missing or malformed master", () => {
    const original = singleEvidence();
    expect(nativeAvSelectionFromEvidence(source, profileId, original)).toEqual({
      exportToken: original.export_token, masterSha256: masterSha, reviewOnly: false,
    });
    for (const master of [null, {}, [], { artifact: null }, { artifact: { sha256: "bad" } }]) {
      const malformed = { ...original, master } as unknown as MltAvMasterEvidence;
      expect(nativeAvSelectionFromEvidence(source, profileId, malformed)).toBeNull();
    }
    expect(nativeAvSelectionFromEvidence(source, profileId, null as unknown as MltAvMasterEvidence)).toBeNull();
  });

  it("remembers a late completed reply only for its original source and profile without caching QC", () => {
    const store = createNativeAvReviewSelectionStore();
    const evidence = multiEvidence();
    const selection = nativeAvSelectionFromEvidence(source, profileId, evidence)!;
    store.remember(source, profileId, selection);
    expect(store.read(source, profileId)).toEqual(selection);
    expect(store.read({ ...source, revision: source.revision + 1 }, profileId)).toBeNull();
    expect(store.read({ ...source, active_branch: "another-branch" }, profileId)).toBeNull();
    expect(store.read({ ...source, generation: "another-generation" }, profileId)).toBeNull();
    expect(store.read({ ...source, id: "another-project" }, profileId)).toBeNull();
    expect(store.read(source, "another-profile")).toBeNull();
    expect(store.read(source, profileId)).not.toHaveProperty("audio_qc");
    expect(createNativeAvReviewSelectionStore().read(source, profileId)).toBeNull();
  });

  it("copies only selector fields and cannot restore a prior report or export authority from extra data", () => {
    const store = createNativeAvReviewSelectionStore();
    const original = { exportToken: "token-a", masterSha256: masterSha, reviewOnly: true,
      audio_qc: { kind: "report", report: reportFixture() }, export_grant: "not-authority" };
    store.remember(source, profileId, original);
    original.exportToken = "mutated-input";
    const first = store.read(source, profileId)!;
    expect(first).toEqual({ exportToken: "token-a", masterSha256: masterSha, reviewOnly: true });
    first.exportToken = "mutated-read";
    expect(store.read(source, profileId)?.exportToken).toBe("token-a");
  });

  it("bounds retained session hints and removes evicted source selectors instead of inventing a fallback", () => {
    const store = createNativeAvReviewSelectionStore();
    for (let index = 0; index < 129; index += 1) {
      store.remember(source, "profile-" + index, { exportToken: "token-" + index, masterSha256: masterSha, reviewOnly: true });
    }
    expect(store.read(source, "profile-0")).toBeNull();
    expect(store.read(source, "profile-1")?.exportToken).toBe("token-1");
    expect(store.read(source, "profile-128")?.exportToken).toBe("token-128");
    expect(store.read(source, profileId)).toBeNull();
  });

  it("notifies only changed retained hints and removes a view subscription on unmount", () => {
    const store = createNativeAvReviewSelectionStore();
    const selection = { exportToken: "token-a", masterSha256: masterSha, reviewOnly: true };
    let notifications = 0;
    const unsubscribe = store.subscribe(() => { notifications += 1; });
    store.remember(source, profileId, selection);
    store.remember(source, profileId, { ...selection });
    expect(notifications).toBe(1);
    store.remember(source, profileId, { ...selection, exportToken: "token-b" });
    expect(notifications).toBe(2);
    unsubscribe();
    store.remember(source, profileId, { ...selection, exportToken: "token-c" });
    expect(notifications).toBe(2);
  });
});

describe("durable receipt discovery wire", () => {
  const current = { id: "10000000-0000-4000-8000-000000000001",
    generation: "10000000-0000-4000-8000-000000000002", revision: 0, active_branch: "main" };
  const profile = "10000000-0000-4000-8000-000000000003";
  const hint = { decoded_measurement_receipt_id: "10000000-0000-4000-8000-000000000004",
    master_sha256: "a".repeat(64) };
  const wire = () => ({ kind: "inventory", inventory: {
    project_id: current.id, generation: current.generation, revision: current.revision,
    deliverable_id: profile, items: [structuredClone(hint)], legacy_unbound_count: 0, unavailable_count: 0,
  } });
  it("keeps empty inventory distinct from legacy and unavailable observations", () => {
    const value = wire(); value.inventory.items = [];
    expect(readNativeAudioQcReceiptInventoryResponse(value, current, profile)).toEqual(value);
    value.inventory.legacy_unbound_count = 2; value.inventory.unavailable_count = 1;
    expect(readNativeAudioQcReceiptInventoryResponse(value, current, profile)).toEqual(value);
  });
  it("rejects boolean, nonfinite, negative and fractional counts", () => {
    for (const key of ["revision", "legacy_unbound_count", "unavailable_count"]) {
      for (const invalid of [false, true, NaN, Infinity, -1, 0.5]) {
        const value = wire();
        (value.inventory as unknown as Record<string, unknown>)[key] = invalid;
        expect(() => readNativeAudioQcReceiptInventoryResponse(value, current, profile)).toThrow(invalidMessage);
      }
    }
  });
  it("rejects a foreign source, generation, revision or profile", () => {
    for (const key of ["project_id", "generation", "revision", "deliverable_id"]) {
      const value = wire();
      (value.inventory as unknown as Record<string, unknown>)[key] = key === "revision" ? 1
        : "20000000-0000-4000-8000-000000000001";
      expect(() => readNativeAudioQcReceiptInventoryResponse(value, current, profile)).toThrow(invalidMessage);
    }
  });
  it("rejects duplicate receipt IDs even with another recorded digest", () => {
    const value = wire();
    value.inventory.items.push({ ...hint, master_sha256: "b".repeat(64) });
    expect(() => readNativeAudioQcReceiptInventoryResponse(value, current, profile)).toThrow(invalidMessage);
  });
  it("rejects over-budget inventory instead of retaining a prefix", () => {
    const value = wire();
    value.inventory.items = Array.from({ length: 129 }, (_, index) => ({
      ...hint, decoded_measurement_receipt_id: "10000000-0000-4000-8000-" + index.toString(16).padStart(12, "0"),
    }));
    expect(() => readNativeAudioQcReceiptInventoryResponse(value, current, profile)).toThrow(invalidMessage);
  });
  it("rejects raw receipt, private selectors and unexpected public fields", () => {
    for (const target of ["response", "inventory", "hint"]) {
      const value = wire();
      const object = target === "response" ? value : target === "inventory" ? value.inventory : value.inventory.items[0];
      (object as unknown as Record<string, unknown>).audio_reopen = { caller_request_id: "private" };
      expect(() => readNativeAudioQcReceiptInventoryResponse(value, current, profile)).toThrow(invalidMessage);
    }
  });
  it("rejects missing, null or malformed recorded identities", () => {
    for (const key of ["decoded_measurement_receipt_id", "master_sha256"]) {
      for (const invalid of [null, "", "UPPER", false]) {
        const value = wire();
        (value.inventory.items[0] as unknown as Record<string, unknown>)[key] = invalid;
        expect(() => readNativeAudioQcReceiptInventoryResponse(value, current, profile)).toThrow(invalidMessage);
      }
    }
  });
  it("preserves only closed unavailable reasons without inventing a report", () => {
    const value = { kind: "unavailable", reason: "unresolved_receipt" };
    expect(readNativeAudioQcReceiptInventoryResponse(value, current, profile)).toEqual(value);
    for (const invalid of [null, {}, { kind: "not_requested" }, { kind: "unavailable", reason: "private" }]) {
      expect(() => readNativeAudioQcReceiptInventoryResponse(invalid, current, profile)).toThrow(invalidMessage);
    }
  });
});
