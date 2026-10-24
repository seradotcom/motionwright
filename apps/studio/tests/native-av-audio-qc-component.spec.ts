import { expect, test, type Locator, type Page } from "@playwright/test";
import type { DeliveryAudioQcReport } from "../src/types";
import type {
  AudioQcFixtureCall, AudioQcFixtureSelection,
} from "./fixtures/native-av-audio-qc";

// Actual NativeAvReview -> readNativeAvAudioQc -> public QC/2 reader.
// All IPC replies below are synthetic. These component regressions grant no
// native measurement, StudioService, SQLite, installed-app or human credit.
const privateMarker = "SYNTHETIC_PRIVATE_DIAGNOSTIC_MUST_NOT_APPEAR";
const pageErrors = new WeakMap<Page, string[]>();
const panel = (page: Page) => page.getByRole("region", { name: "Native numeric audio QC", exact: true });
const reread = (page: Page) => panel(page).getByRole("button", { name: "Read original audio QC again", exact: true });
const targetTable = (page: Page) => panel(page).getByRole("table", { name: "Saved targets and final decoded measurements", exact: true });
const rowValues = (table: Locator, label: string) =>
  table.getByRole("rowheader", { name: label, exact: true }).locator("..").getByRole("cell");
const calls = (page: Page) => page.evaluate(() => window.__NATIVE_AV_AUDIO_QC__.calls());
const reads = async (page: Page) => (await calls(page)).filter(call => call.command === "read_native_av_audio_qc");
const selection = (page: Page) => page.evaluate(() => window.__NATIVE_AV_AUDIO_QC__.selection());
const settle = (page: Page) => page.evaluate(() => window.__NATIVE_AV_AUDIO_QC__.settle());

function requestFor(value: AudioQcFixtureSelection) {
  return { project_id: value.projectId, generation: value.generation,
    revision: value.revision, export_token: value.exportToken };
}
async function readCount(page: Page, count: number): Promise<AudioQcFixtureCall[]> {
  await expect.poll(async () => (await reads(page)).length).toBe(count);
  return reads(page);
}
async function openPanel(page: Page, desktop = true) {
  await page.goto("/tests/fixtures/native-av-audio-qc.html" + (desktop ? "" : "?desktop=0"));
  await expect(page.getByRole("heading", { name: "Synthetic Audio QC component fixture", exact: true })).toBeVisible();
  await expect(panel(page)).toBeVisible();
  if (desktop) await readCount(page, 1);
}
async function resolve(page: Page, call: AudioQcFixtureCall, response: unknown) {
  await page.evaluate(({ id, value }) => window.__NATIVE_AV_AUDIO_QC__.resolve(id, value),
    { id: call.id, value: response });
}
async function reject(page: Page, call: AudioQcFixtureCall) {
  await page.evaluate(({ id, diagnostic }) => window.__NATIVE_AV_AUDIO_QC__.reject(id, diagnostic),
    { id: call.id, diagnostic: privateMarker });
}
async function setSelection(page: Page, value: AudioQcFixtureSelection) {
  await page.evaluate(value => window.__NATIVE_AV_AUDIO_QC__.setSelection(value), value);
}
async function details(page: Page) {
  await panel(page).getByText("Measurement values, actual interval and custody references", { exact: true }).click();
}
async function expectLoading(page: Page) {
  await expect(panel(page).getByRole("status")).toHaveText("Reading the original audio report\u2026");
  await expect(targetTable(page)).toHaveCount(0);
  await expect(reread(page)).toBeDisabled();
}
async function expectSafeFailure(page: Page) {
  await expect(panel(page).getByRole("alert")).toContainText("Audio QC unavailable.");
  await expect(panel(page).getByRole("alert")).toContainText("No render, measurement or automatic retry was requested.");
  await expect(panel(page)).not.toContainText(privateMarker);
  await expect(targetTable(page)).toHaveCount(0);
  await expect(reread(page)).toBeEnabled();
}
async function expectOutside(page: Page) {
  await expect(panel(page).getByRole("status")).toContainText("Outside numeric targets");
  await expect(panel(page).getByRole("status")).toContainText("outside_targets");
  await expect(panel(page).getByRole("alert")).toHaveCount(0);
}

// Synthetic public-wire data only, not another reader or measurement engine.
// Deliberately retains a 128-frame codec tail beyond a 96000-frame video span.
function report(masterSha256 = "1".repeat(64)): DeliveryAudioQcReport {
  return {
    schema: "motionwright.delivery-audio-qc-report/2",
    qc: {
      schema: "motionwright.delivery-audio-qc/1", status: "within_targets",
      targets: { target_lufs: -23.001, target_true_peak_dbtp: -1.001 },
      target_lufs_milli: -23001, target_true_peak_millidbtp: -1001,
      measured_integrated_lufs_milli: -23001, measured_true_peak_millidbtp: -1001,
      lufs_delta_milli: 0, lufs_tolerance_milli: 200,
      lufs_status: "within_targets", true_peak_status: "within_targets",
      evaluated_phase: "decoded_mlt_master", decoded_wav_sha256: "2".repeat(64),
      decoded_sample_frames: 96128,
      measurement_receipt_id: "00000000-0000-4000-8000-000000000001",
      measurement_proof_sha256: "3".repeat(64),
      evidence_scope: "final-decoded-pcm16-public-milli-numeric-qc-not-mastered-not-intelligibility-not-human-approved",
    },
    measurement_method: { engine: "libebur128", version: "1.2.6", producer_schema_version: 1 },
    decoded_input: {
      format: "wav_pcm_s16le", sha256: "2".repeat(64), sample_rate_hz: 48000,
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
      integrated_lufs_milli: -23001, momentary_lufs_milli: -23200,
      short_term_lufs_milli: null, loudness_range_milli: null,
      true_peak_millidbtp: -1001, sample_peak_millidbfs: -10000,
    },
    units: {
      integrated_lufs_milli: "LUFS", momentary_lufs_milli: "LUFS", short_term_lufs_milli: "LUFS",
      loudness_range_milli: "LU", true_peak_millidbtp: "dBTP", sample_peak_millidbfs: "dBFS",
      target_lufs_milli: "LUFS", target_true_peak_millidbtp: "dBTP",
      lufs_delta_milli: "LU", lufs_tolerance_milli: "LU",
    },
    published_resolution: { numerator: 1, denominator: 1000, rounding_authority: "identified_provider_integer_milli_output" },
    master_artifact_sha256: masterSha256,
    evidence_scope: "measured-final-pcm-numeric-report-not-normalization-not-intelligibility-not-human-acceptance",
  };
}
function outside(masterSha256 = "1".repeat(64)) {
  const value = report(masterSha256);
  // Matching display numbers must not cause Studio to replace a native verdict.
  value.qc.status = value.qc.lufs_status = "outside_targets";
  return { kind: "report", report: value };
}

test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  pageErrors.set(page, errors);
  page.on("pageerror", error => errors.push(error.message));
});
test.afterEach(async ({ page }, testInfo) => {
  const observed = await calls(page);
  await testInfo.attach("audio-qc-synthetic-bridge.json", {
    contentType: "application/json",
    body: JSON.stringify({
      schema: "motionwright.synthetic-audio-qc-component-trace/1",
      evidence_scope: "synthetic-transport-real-component-not-native-not-service-not-acceptance",
      test: testInfo.title, calls: observed, page_errors: pageErrors.get(page) ?? [],
    }, null, 2),
  });
  expect(pageErrors.get(page)).toEqual([]);
  for (const call of observed) {
    if (new URL(page.url()).searchParams.get("recovery") === "1") {
      expect(["native_av_audio_qc_receipt_inventory", "recover_native_av_audio_qc"]).toContain(call.command);
      expect(Object.keys(call.args ?? {})).toEqual(["request"]);
      const expected = call.command === "native_av_audio_qc_receipt_inventory"
        ? ["deliverable_id", "generation", "project_id", "revision"]
        : ["decoded_measurement_receipt_id", "deliverable_id", "expected_master_sha256", "generation", "project_id", "revision"];
      expect(Object.keys(call.args!.request as Record<string, unknown>).sort()).toEqual(expected);
      continue;
    }
    expect(["read_native_av_audio_qc", "review_native_av_master"]).toContain(call.command);
    expect(Object.keys(call.args ?? {})).toEqual(["request"]);
    expect(Object.keys(call.args!.request as Record<string, unknown>).sort())
      .toEqual(["export_token", "generation", "project_id", "revision"]);
  }
});

test("QC component reads once under Strict Mode and displays the exact public milli report", async ({ page }) => {
  await openPanel(page);
  const selected = await selection(page);
  await expectLoading(page);
  await settle(page);
  const [read] = await readCount(page, 1);
  expect(read.args).toEqual({ request: requestFor(selected) });
  expect((await calls(page)).filter(call => call.command === "review_native_av_master")).toHaveLength(1);
  await resolve(page, read, { kind: "report", report: report(selected.masterSha256) });
  await expect(panel(page).getByRole("status")).toContainText("Within numeric targets \u00b7 within_targets");
  await expect(rowValues(targetTable(page), "Integrated loudness")).toHaveText([
    "-23.001 LUFS", "-23.001 LUFS", "Within numeric targets",
  ]);
  await expect(rowValues(targetTable(page), "True peak ceiling")).toHaveText([
    "-1.001 dBTP", "-1.001 dBTP", "Within numeric targets",
  ]);
  await details(page);
  const values = panel(page).getByRole("table", { name: "Public measured values \u00b7 resolution 1/1000", exact: true });
  await expect(rowValues(values, "Momentary loudness at input end")).toHaveText(["-23.200", "LUFS"]);
  await expect(rowValues(values, "Short-term loudness at input end")).toHaveText(["Unavailable (null)", "LUFS"]);
  await expect(rowValues(values, "True peak")).toHaveText(["-1.001", "dBTP"]);
  await expect(rowValues(values, "Sample peak")).toHaveText(["-10.000", "dBFS"]);
  await expect(panel(page)).toContainText("Actual decoded interval: [0, 96128)");
  await expect(panel(page)).toContainText("This includes any codec tail");
  await expect(panel(page)).toContainText("Momentary window: 400 ms. Short-term window: 3000 ms.");
  await expect(panel(page)).toContainText("neither is a maximum over the whole master");
  await expect(panel(page)).toContainText("True peak oversampling: 4\u00d7.");
  await expect(panel(page)).toContainText(selected.masterSha256);
  await expect(panel(page)).toContainText("00000000-0000-4000-8000-000000000001");
  await expect(panel(page)).toContainText("motionwright.delivery-audio-qc-report/2");
  await expect(panel(page)).toContainText("They do not certify normalization, intelligibility, listening, caption quality or human approval.");
  await expect(page.getByRole("region", { name: "Native master playback review", exact: true }))
    .toContainText("publication blocked");
  expect(await reads(page)).toHaveLength(1);
});

test("QC component keeps null targets and unavailable measurements distinct from zero", async ({ page }) => {
  await openPanel(page);
  const value = report();
  value.qc.status = value.qc.lufs_status = "unavailable";
  value.qc.true_peak_status = "not_requested";
  value.qc.targets!.target_true_peak_dbtp = null;
  value.qc.target_true_peak_millidbtp = null;
  value.qc.measured_integrated_lufs_milli = value.qc.measured_true_peak_millidbtp = value.qc.lufs_delta_milli = null;
  value.measured_levels = { integrated_lufs_milli: null, momentary_lufs_milli: null,
    short_term_lufs_milli: null, loudness_range_milli: null,
    true_peak_millidbtp: null, sample_peak_millidbfs: null };
  await resolve(page, (await reads(page))[0], { kind: "report", report: value });
  await expect(rowValues(targetTable(page), "Integrated loudness")).toHaveText([
    "-23.001 LUFS", "Unavailable (null) LUFS", "Unavailable",
  ]);
  await expect(rowValues(targetTable(page), "True peak ceiling")).toHaveText([
    "Not requested (null)", "Unavailable (null) dBTP", "Not requested",
  ]);
  await details(page);
  await expect(panel(page)).toContainText("Saved target object: LUFS -23.001; true peak dBTP null");
  await expect(panel(page)).not.toContainText("0.000");
  await expect(panel(page)).not.toContainText("Within numeric targets");
  expect(await reads(page)).toHaveLength(1);
});

test("QC component distinguishes no requested report from a measured report", async ({ page }) => {
  await openPanel(page);
  await resolve(page, (await reads(page))[0], { kind: "not_requested" });
  await expect(panel(page).getByRole("status")).toHaveText("Audio QC not requested for this master. No numeric report is available.");
  await expect(targetTable(page)).toHaveCount(0);
  await expect(panel(page).getByRole("alert")).toHaveCount(0);
  await expect(reread(page)).toBeEnabled();
  await settle(page);
  expect(await reads(page)).toHaveLength(1);
});

test("QC component displays measured values when numeric targets were not requested", async ({ page }) => {
  await openPanel(page);
  const value = report();
  value.qc.status = value.qc.lufs_status = value.qc.true_peak_status = "not_requested";
  value.qc.targets = null;
  value.qc.target_lufs_milli = value.qc.target_true_peak_millidbtp = value.qc.lufs_delta_milli = null;
  await resolve(page, (await reads(page))[0], { kind: "report", report: value });
  await expect(rowValues(targetTable(page), "Integrated loudness")).toHaveText([
    "Not requested (null)", "-23.001 LUFS", "Not requested",
  ]);
  await details(page);
  await expect(panel(page)).toContainText("Saved target object: absent (null)");
  await expect(panel(page)).not.toContainText("No numeric report is available.");
});

test("QC component preserves the received outside outcome instead of calculating a local PASS", async ({ page }) => {
  await openPanel(page);
  await resolve(page, (await reads(page))[0], outside());
  await expectOutside(page);
  await expect(rowValues(targetTable(page), "Integrated loudness")).toHaveText([
    "-23.001 LUFS", "-23.001 LUFS", "Outside numeric targets",
  ]);
});

test("QC component refuses a null public envelope with a fixed safe error", async ({ page }) => {
  await openPanel(page);
  await resolve(page, (await reads(page))[0], null);
  await expectSafeFailure(page);
  await settle(page);
  expect(await reads(page)).toHaveLength(1);
});

test("QC component refuses private fields in a report without displaying the private payload", async ({ page }) => {
  await openPanel(page);
  await resolve(page, (await reads(page))[0], {
    kind: "report", report: { ...report(), private_proof: { diagnostic: privateMarker } },
  });
  await expectSafeFailure(page);
  expect(await reads(page)).toHaveLength(1);
});

test("QC component requires an explicit read after transport failure and retains the original selector", async ({ page }) => {
  await openPanel(page);
  const [original] = await reads(page);
  await reject(page, original);
  await expectSafeFailure(page);
  await settle(page);
  expect(await reads(page)).toHaveLength(1);
  await reread(page).click();
  const [, next] = await readCount(page, 2);
  expect(next.args).toEqual(original.args);
  await expectLoading(page);
  await resolve(page, next, outside());
  await expectOutside(page);
  expect((await calls(page)).filter(call => call.command === "review_native_av_master")).toHaveLength(1);
});

test("QC component rejects a report bound to a different master", async ({ page }) => {
  await openPanel(page);
  await resolve(page, (await reads(page))[0], { kind: "report", report: report("4".repeat(64)) });
  await expectSafeFailure(page);
  await expect(panel(page)).not.toContainText("4".repeat(64));
  expect(await reads(page)).toHaveLength(1);
});

test("QC component leaves an expired token unavailable without creating or replaying production", async ({ page }) => {
  await openPanel(page);
  const [original] = await reads(page);
  await resolve(page, original, { kind: "unavailable", reason: "session_unavailable" });
  await expect(panel(page).getByRole("status")).toContainText("original master session is unavailable or expired");
  await expect(targetTable(page)).toHaveCount(0);
  await settle(page);
  expect(await reads(page)).toHaveLength(1);
  await reread(page).click();
  const [, next] = await readCount(page, 2);
  expect(next.args).toEqual(original.args);
  await resolve(page, next, { kind: "unavailable", reason: "session_unavailable" });
  await expect(panel(page).getByRole("status")).toContainText("this read does not create one");
  expect(await reads(page)).toHaveLength(2);
});

const identityChanges = [
  { field: "projectId", value: "00000000-0000-4000-8000-000000000101" },
  { field: "generation", value: "00000000-0000-4000-8000-000000000102" },
  { field: "branch", value: "00000000-0000-4000-8000-000000000103" },
  { field: "revision", value: 9001 },
  { field: "exportToken", value: "synthetic-audio-master-session-B" },
  { field: "deliverableId", value: "00000000-0000-4000-8000-000000000104" },
  { field: "masterSha256", value: "4".repeat(64) },
] as const;
for (const change of identityChanges) {
  test("QC component rejects stale A replies after A-B-A " + change.field + " selection", async ({ page }) => {
    await openPanel(page);
    const a = await selection(page), [firstA] = await reads(page);
    const b = { ...a, [change.field]: change.value };
    await setSelection(page, b);
    const [, firstB] = await readCount(page, 2);
    await setSelection(page, a);
    const [, , secondA] = await readCount(page, 3);
    expect(secondA.id).not.toBe(firstA.id);
    expect(secondA.args).toEqual(firstA.args);
    await resolve(page, firstA, { kind: "report", report: report(a.masterSha256) });
    await expectLoading(page);
    await resolve(page, secondA, outside(a.masterSha256));
    await expectOutside(page);
    await resolve(page, firstB, { kind: "report", report: report(b.masterSha256) });
    await expectOutside(page);
    expect(await reads(page)).toHaveLength(3);
  });
}

test("QC component ignores an old read error after a newer selection has a report", async ({ page }) => {
  await openPanel(page);
  const a = await selection(page), [old] = await reads(page);
  await setSelection(page, { ...a, exportToken: "synthetic-audio-master-session-B" });
  const [, current] = await readCount(page, 2);
  await resolve(page, current, outside());
  await expectOutside(page);
  await reject(page, old);
  await expectOutside(page);
  await expect(panel(page)).not.toContainText(privateMarker);
  expect(await reads(page)).toHaveLength(2);
});

test("QC component ignores completion after unmount and rereads the same selector on remount", async ({ page }) => {
  await openPanel(page);
  const [old] = await reads(page);
  await page.evaluate(() => window.__NATIVE_AV_AUDIO_QC__.unmount());
  await expect(panel(page)).toHaveCount(0);
  await resolve(page, old, { kind: "report", report: report() });
  await expect(panel(page)).toHaveCount(0);
  await page.evaluate(() => window.__NATIVE_AV_AUDIO_QC__.mount());
  const [, fresh] = await readCount(page, 2);
  expect(fresh.args).toEqual(old.args);
  await expectLoading(page);
  await resolve(page, fresh, outside());
  await expectOutside(page);
});

test("QC component does not restore a previous report when the same token expires across remount", async ({ page }) => {
  await openPanel(page);
  const [first] = await reads(page);
  await resolve(page, first, { kind: "report", report: report() });
  await expect(targetTable(page)).toBeVisible();
  await page.evaluate(() => window.__NATIVE_AV_AUDIO_QC__.unmount());
  await page.evaluate(() => window.__NATIVE_AV_AUDIO_QC__.mount());
  const [, fresh] = await readCount(page, 2);
  expect(fresh.args).toEqual(first.args);
  await expectLoading(page);
  await resolve(page, fresh, { kind: "unavailable", reason: "session_unavailable" });
  await expect(panel(page).getByRole("status")).toContainText("unavailable or expired");
  await expect(targetTable(page)).toHaveCount(0);
  await expect(panel(page)).not.toContainText("Within numeric targets");
});

test("QC component removes a settled report during explicit reread without repeating the media read", async ({ page }) => {
  await openPanel(page);
  const [first] = await reads(page);
  await resolve(page, first, { kind: "report", report: report() });
  await expect(targetTable(page)).toBeVisible();
  await reread(page).click();
  const [, fresh] = await readCount(page, 2);
  expect(fresh.args).toEqual(first.args);
  await expectLoading(page);
  await reject(page, fresh);
  await expectSafeFailure(page);
  expect((await calls(page)).filter(call => call.command === "review_native_av_master")).toHaveLength(1);
});

test("QC component reports unsupported readback in a browser without a desktop bridge", async ({ page }) => {
  await openPanel(page, false);
  await expect(panel(page).getByRole("status")).toContainText("Audio measurement readback is not supported on this platform.");
  await expect(targetTable(page)).toHaveCount(0);
  await expect(reread(page)).toBeEnabled();
  await reread(page).click();
  await expect(panel(page).getByRole("status")).toContainText("not supported on this platform");
  await settle(page);
  expect(await calls(page)).toEqual([]);
});

const historyPanel = (page: Page) => page.getByRole("region", { name: "Saved audio measurement recovery", exact: true });
const findSaved = (page: Page) => historyPanel(page).getByRole("button", { name: "Find saved audio measurements", exact: true });
const inventoryCalls = async (page: Page) => (await calls(page)).filter(call => call.command === "native_av_audio_qc_receipt_inventory");
const recoveryCalls = async (page: Page) => (await calls(page)).filter(call => call.command === "recover_native_av_audio_qc");
const receiptA = "00000000-0000-4000-8000-000000000001";
const receiptB = "00000000-0000-4000-8000-000000000002";
function inventoryFor(source: AudioQcFixtureSelection, ids = [receiptA]) {
  return { kind: "inventory", inventory: {
    project_id: source.projectId, generation: source.generation, revision: source.revision,
    deliverable_id: source.deliverableId,
    items: ids.map(id => ({ decoded_measurement_receipt_id: id, master_sha256: source.masterSha256 })),
    legacy_unbound_count: 0, unavailable_count: 0,
  } };
}
async function openHistory(page: Page) {
  await page.goto("/tests/fixtures/native-av-audio-qc.html?recovery=1");
  await expect(historyPanel(page)).toBeVisible();
  await settle(page);
  expect(await calls(page)).toEqual([]);
}
async function discoverHistory(page: Page, ids = [receiptA]) {
  await findSaved(page).click();
  const pending = (await inventoryCalls(page)).at(-1)!;
  await resolve(page, pending, inventoryFor(await selection(page), ids));
}
async function chooseReceipt(page: Page, id = receiptA) {
  await historyPanel(page).getByRole("button", { name: "Read audio QC receipt " + id, exact: true }).click();
  await expect(panel(page)).toBeVisible();
  return (await recoveryCalls(page)).at(-1)!;
}

test("Persisted QC requires explicit discovery and exact selection without a session token", async ({ page }) => {
  await openHistory(page);
  await discoverHistory(page, [receiptA, receiptB]);
  expect(await recoveryCalls(page)).toEqual([]);
  expect(await reads(page)).toEqual([]);
  const read = await chooseReceipt(page, receiptB);
  const selected = await selection(page);
  expect(read.args).toEqual({ request: {
    project_id: selected.projectId, generation: selected.generation, revision: selected.revision,
    deliverable_id: selected.deliverableId, decoded_measurement_receipt_id: receiptB,
    expected_master_sha256: selected.masterSha256,
  } });
  const value = report(selected.masterSha256); value.qc.measurement_receipt_id = receiptB;
  await resolve(page, read, { kind: "report", report: value });
  await expect(targetTable(page)).toBeVisible();
  await settle(page);
  expect(await recoveryCalls(page)).toHaveLength(1);
  expect((await calls(page)).some(call => call.command === "review_native_av_master")).toBe(false);
});

test("Persisted QC distinguishes empty history, legacy metadata and incomplete observations", async ({ page }) => {
  await openHistory(page);
  await discoverHistory(page, []);
  await expect(historyPanel(page)).toContainText("No completed measurement reference");
  await expect(panel(page)).toHaveCount(0);
  await findSaved(page).click();
  const value = inventoryFor(await selection(page), []);
  value.inventory.legacy_unbound_count = 2; value.inventory.unavailable_count = 1;
  await resolve(page, (await inventoryCalls(page)).at(-1)!, value);
  await expect(historyPanel(page)).toContainText("2 current-source observations");
  await expect(historyPanel(page)).toContainText("1 original measurement chains");
  await expect(historyPanel(page)).not.toContainText("Audio QC not requested");
  expect(await recoveryCalls(page)).toEqual([]);
});

test("Persisted QC preserves UNKNOWN and never selects another receipt automatically", async ({ page }) => {
  await openHistory(page);
  await discoverHistory(page, [receiptA, receiptB]);
  const read = await chooseReceipt(page);
  await resolve(page, read, { kind: "unavailable", reason: "unresolved_receipt" });
  await expect(panel(page)).toContainText("The retained receipt chain could not be resolved.");
  await expect(panel(page)).toContainText("This read did not retry the original request.");
  await expect(targetTable(page)).toHaveCount(0);
  await settle(page);
  expect(await recoveryCalls(page)).toHaveLength(1);
});

test("Persisted QC rejects null inventory and sanitizes discovery transport failures", async ({ page }) => {
  await openHistory(page);
  await findSaved(page).click();
  await resolve(page, (await inventoryCalls(page)).at(-1)!, null);
  await expect(historyPanel(page).getByRole("alert")).toContainText("references are unavailable");
  await findSaved(page).click();
  await reject(page, (await inventoryCalls(page)).at(-1)!);
  await expect(historyPanel(page).getByRole("alert")).toContainText("No production or automatic retry");
  await expect(historyPanel(page)).not.toContainText(privateMarker);
  expect(await recoveryCalls(page)).toEqual([]);
});

test("Persisted QC rejects a report for another receipt or master", async ({ page }) => {
  await openHistory(page);
  await discoverHistory(page);
  const first = await chooseReceipt(page);
  const wrong = report(); wrong.qc.measurement_receipt_id = receiptB;
  await resolve(page, first, { kind: "report", report: wrong });
  await expectSafeFailure(page);
  await reread(page).click();
  await resolve(page, (await recoveryCalls(page)).at(-1)!, { kind: "report", report: report("f".repeat(64)) });
  await expectSafeFailure(page);
  expect(await recoveryCalls(page)).toHaveLength(2);
});

test("Persisted QC rejects late discovery after an A-B-A source change", async ({ page }) => {
  await openHistory(page);
  const a = await selection(page);
  await findSaved(page).click();
  const old = (await inventoryCalls(page))[0];
  await setSelection(page, { ...a, revision: a.revision + 1 });
  await setSelection(page, a);
  await findSaved(page).click();
  const current = (await inventoryCalls(page)).at(-1)!;
  await resolve(page, current, inventoryFor(a, [receiptB]));
  await resolve(page, old, inventoryFor(a, [receiptA]));
  await expect(historyPanel(page).getByRole("button", { name: "Read audio QC receipt " + receiptB, exact: true })).toBeVisible();
  await expect(historyPanel(page).getByRole("button", { name: "Read audio QC receipt " + receiptA, exact: true })).toHaveCount(0);
  expect(await recoveryCalls(page)).toEqual([]);
});

test("Persisted QC remount requires fresh discovery and does not retain an admitted report", async ({ page }) => {
  await openHistory(page);
  await discoverHistory(page);
  await resolve(page, await chooseReceipt(page), { kind: "report", report: report() });
  await expect(targetTable(page)).toBeVisible();
  await page.evaluate(() => window.__NATIVE_AV_AUDIO_QC__.unmount());
  await page.evaluate(() => window.__NATIVE_AV_AUDIO_QC__.mount());
  await expect(panel(page)).toHaveCount(0);
  expect(await recoveryCalls(page)).toHaveLength(1);
  await discoverHistory(page);
  await reject(page, await chooseReceipt(page));
  await expectSafeFailure(page);
  expect(await recoveryCalls(page)).toHaveLength(2);
});

test("Persisted QC source changes suppress a late recovered report", async ({ page }) => {
  await openHistory(page);
  await discoverHistory(page);
  const old = await chooseReceipt(page);
  const a = await selection(page);
  await setSelection(page, { ...a, revision: a.revision + 1 });
  await resolve(page, old, { kind: "report", report: report() });
  await expect(panel(page)).toHaveCount(0);
  await expect(historyPanel(page).getByRole("button", { name: "Find saved audio measurements", exact: true })).toBeEnabled();
  expect(await recoveryCalls(page)).toHaveLength(1);
});
