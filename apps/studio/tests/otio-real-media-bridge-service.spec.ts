/**
 * MW05-E11-03: original-source OTIO export -> one test-owned, actual H.264
 * media source -> verified first-party bridge -> real Studio UI owner review
 * -> one real StudioService change -> SQLite/CAS and independent closed Store.
 *
 * The ffmpeg-created media is explicitly synthetic and authorized only by this
 * test. The full bridge runs without mocked probes. No installed NLE/Tauri,
 * publisher authorization or external media rights are inferred.
 */
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import { expect, test, type Locator, type Page } from "@playwright/test";
import type { Project } from "../src/types";
import { ScopedChangeService } from "./support/scopedChangeService";

const run = promisify(execFile);
const kdenlivePair = process.env.MOTIONWRIGHT_OTIO_INPUT_PROFILE === "kdenlive";
if (process.env.MOTIONWRIGHT_OTIO_INPUT_PROFILE
    && process.env.MOTIONWRIGHT_OTIO_INPUT_PROFILE !== "kdenlive") {
  throw new Error("Unrecognized source-exact OTIO input profile");
}
const expectedMediaRefs = kdenlivePair ? 2 : 1;
const flowTitle = kdenlivePair
  ? "original Kdenlive-shaped OTIO pair is verified in Studio, saved once and independently reopened"
  : "real verified media OTIO cut is reviewed in Studio, saved once and independently reopened";

test.skip(
  !process.env.MOTIONWRIGHT_SERVICE_TEST_BIN ||
  process.env.MOTIONWRIGHT_OTIO_REAL_MEDIA_OWNER !== "1",
  "This source-exact flow requires the real service binary and one explicitly selected real-media bridge test.",
);
// Each bridge process has a 70-second production deadline. The observer must
// retain its terminal result instead of closing the Service after a 5-second
// UI wait or the helper's ordinary 10-second RPC timeout. Never retry effects.
test.setTimeout(240_000);
test.describe.configure({ retries: 0 });

async function runBounded(binary: string, args: string[], cwd: string) {
  const result = await run(binary, args, {
    cwd, timeout: 55_000, maxBuffer: 2 * 1024 * 1024,
    env: { ...process.env, PYTHONNOUSERSITE: "1" },
  });
  if (result.stderr.length > 32_000) {
    throw new Error("Original bridge process emitted an oversized diagnostic");
  }
  return result.stdout;
}

function sha(data: Buffer | string): string {
  return createHash("sha256").update(data).digest("hex");
}

async function prepareOriginalMedia(root: string): Promise<string> {
  const original = join(root, "source-original.mp4");
  await runBounded("ffmpeg", [
    "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
    "-frames:v", "288", "-c:v", "libx264", "-pix_fmt", "yuv420p",
    "-preset", "ultrafast", "-crf", "35", "-an", "-movflags", "+faststart",
    original,
  ], root);
  const bytes = await readFile(original);
  expect(bytes.length).toBeGreaterThan(1024);
  expect(bytes.length).toBeLessThan(32 * 1024 * 1024);
  return original;
}

async function showOtio(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await expect(page.getByRole("region", { name: "OTIO owner cut review" })).toBeVisible();
}

async function expectOtioTerminal(page: Page, success: Locator) {
  const refusal = page.locator(".otio-cut-review [role=alert]");
  // Observe either terminal UI outcome from the one original invocation.
  // Polling these locators never issues another grant or media operation.
  await expect(success.or(refusal).first()).toBeVisible({ timeout: 85_000 });
  expect(await refusal.allTextContents(), "Original OTIO refusal").toEqual([]);
  await expect(success).toBeVisible();
}

test(flowTitle, async ({ page }, info) => {
  const databasePath = info.outputPath("service", "studio.sqlite3");
  const owner = info.outputPath("otio-owner");
  const exported = info.outputPath("verified-otio-bundle");
  await mkdir(owner, { recursive: true });
  const service = await ScopedChangeService.create({
    databasePath, desktopInvokeFixture: true, timeoutMs: 90_000,
  });
  let final: Project | undefined;
  let closed = false;
  const record: Record<string, unknown> = {
    inputProfile: kdenlivePair ? "kdenlive-otio-pair" : "metadata-preserving-otio",
    syntheticNativeData: kdenlivePair,
    actualOwnerMediaBytes: false,
    actualBridgeMediaProbe: false,
    bridgeSourceBoundProposal: false,
    uiCommitCount: 0,
    independentStoreOpen: false,
    installedTauri: false, installedNle: false,
    humanCreativeApproval: false, releaseAuthorized: false,
  };
  try {
    const initial = (await service.fixtureBootstrap()).project;
    expect(initial.scenes).toHaveLength(2);
    expect(initial.scenes[0].duration).toEqual({ num: "6", den: "1" });
    expect(initial.scenes[1].duration).toEqual({ num: "4", den: "1" });

    const media = await prepareOriginalMedia(owner);
    const sourceBytes = await readFile(media);
    const mediaSha = sha(sourceBytes);
    const originalMediaProject = await service.importAsset(
      initial, media, "Owned original OTIO cut video", "video/mp4",
    );
    const mediaAsset = originalMediaProject.assets.find(asset =>
      asset.media_type === "video/mp4" && asset.content_sha256 === mediaSha);
    expect(mediaAsset).toBeDefined();
    const current = await service.project(initial.id);
    expect(current).toEqual(originalMediaProject);
    record.actualOwnerMediaBytes = true;
    record.mediaOriginalSha256 = mediaSha;
    record.originalProjectRevision = current.revision;

    // Studio initiates export through the exact Desktop otio_bridge module
    // and one use-limited grant via the real Service test transport.
    await service.attachPage(page, current.id);
    await showOtio(page);
    await page.getByText("Create the original media-bound OTIO bundle", { exact: true }).click();
    await page.getByLabel("New OTIO media bundle destination").fill(exported);
    await page.getByLabel("Original OTIO source reviewer").fill("Test-only original source author");
    await page.getByLabel("Original OTIO media owner").fill("Test fixture's original creator");
    await page.getByLabel("Original OTIO media license").fill("Synthetic test-owned original");
    await page.getByLabel("Original OTIO video asset 1").selectOption(mediaAsset!.id);
    await page.getByLabel("Original OTIO MP4 file path 1").fill(media);
    if (kdenlivePair) {
      await page.getByRole("button", { name: "Bind another original scene", exact: true }).click();
      await page.getByLabel("Original OTIO video asset 2").selectOption(mediaAsset!.id);
      await page.getByLabel("Original OTIO MP4 file path 2").fill(media);
      await page.getByLabel("Original OTIO source start frame 2").fill("144");
    }
    await page.getByLabel(/I confirm I have permission to use, copy and redistribute/).check();
    await page.getByLabel(/Explicitly permit synthetic or test-origin video inputs/).check();
    await page.getByRole("button", { name: "Export original media-bound OTIO bundle" }).click();
    await expectOtioTerminal(page,
      page.getByText(/Original verified OTIO media bundle exported to/));
    const originalExportCalls = service.transcript.filter(item =>
      item.direction === "request" && "command" in item.payload &&
      item.payload.command === "export_portable_otio_media_bundle");
    expect(originalExportCalls).toHaveLength(1);
    record.desktopModuleOriginalBundleExportCalls = originalExportCalls.length;
    const sourceSnapshotSha = sha(await readFile(join(exported, "source-project.json")));
    expect(JSON.parse(await readFile(join(exported, "timeline.otio"), "utf8")).OTIO_SCHEMA)
      .toBe("Timeline.1");
    const manifest = JSON.parse(await readFile(join(exported, "bridge-manifest.json"), "utf8"));
    expect(manifest.bound_media).toHaveLength(expectedMediaRefs);
    expect(manifest.project_sha256).toBe(sourceSnapshotSha);
    expect(manifest.bound_media[0].source_sha256).toBe(mediaSha);
    expect(manifest.bound_media[0].actual_media_frames).toBe(288);
    expect(manifest.nle_import_verified).toBe(false);
    record.actualBridgeMediaProbe = true;

    const originalTimeline = JSON.parse(await readFile(join(exported, "timeline.otio"), "utf8"));
    const originalClips = originalTimeline.tracks.children[0].children;
    expect(originalClips).toHaveLength(2);
    expect(originalClips[0].media_reference.OTIO_SCHEMA).toBe("ExternalReference.1");
    expect(originalClips[1].media_reference.OTIO_SCHEMA)
      .toBe(kdenlivePair ? "ExternalReference.1" : "MissingReference.1");
    // This variant tests the actual installed reader on deliberately synthetic,
    // metadata-stripped Kdenlive-shaped bytes. It does not run or authenticate
    // a new NLE process, and never replaces the retained physical receiver.
    const receivedTimeline = kdenlivePair ? {
      OTIO_SCHEMA: "Timeline.1", name: "Test-owned receiver cut",
      metadata: { kdenlive: { version: "26.08.3" } },
      tracks: { OTIO_SCHEMA: "Stack.1", children: [{
        OTIO_SCHEMA: "Track.1", kind: "Video",
        children: originalClips.map((clip: {
          name: string; source_range: unknown;
          media_reference: { target_url: string; available_range: unknown };
        }) => ({
          OTIO_SCHEMA: "Clip.2", name: clip.name, enabled: true,
          metadata: {}, effects: [], markers: [], source_range: clip.source_range,
          active_media_reference_key: "DEFAULT_MEDIA",
          media_references: { DEFAULT_MEDIA: {
            OTIO_SCHEMA: "ExternalReference.1",
            target_url: clip.media_reference.target_url,
            available_range: clip.media_reference.available_range, metadata: {},
          } },
        })),
      }] },
    } : originalTimeline;
    const receivedBytes = JSON.stringify(receivedTimeline);
    if (kdenlivePair) {
      await writeFile(join(exported, "received.otio"), receivedBytes, { flag: "wx" });
      record.initialReceiverOriginalSha256 = sha(receivedBytes);
    }
    const editedTimeline = structuredClone(receivedTimeline);
    const editedClips = editedTimeline.tracks.children[0].children;
    editedClips[0].source_range.duration.value = 120; // 5 seconds at the original 24 fps
    editedTimeline.tracks.children[0].children = [editedClips[1], editedClips[0]];
    const editedBytes = JSON.stringify(editedTimeline);
    await writeFile(join(exported, "edited.otio"), editedBytes, { flag: "wx" });
    record.editedCutOriginalSha256 = sha(editedBytes);
    // Same live Studio session now imports the edited cut via the compiled
    // bridge. The original export already installed its exact bundle path and
    // suggested filename; no navigation or second injected transport.
    await expect(page.getByLabel("Original OTIO media bundle folder")).toHaveValue(exported);
    await expect(page.getByLabel("Edited OTIO filename in bundle")).toHaveValue("edited.otio");
    if (kdenlivePair) {
      await page.getByLabel("OTIO cut origin").selectOption("kdenlive");
      await expect(page.getByLabel("Initial Kdenlive OTIO filename in bundle"))
        .toHaveValue("received.otio");
    }
    await page.getByRole("button", { name: "Preview edited OTIO with verified media" }).click();
    const station = page.getByRole("region", { name: "OTIO proposed revision" });
    await expectOtioTerminal(page, station);
    await expect(page.getByRole("button", { name: "Preview edited OTIO with verified media" }))
      .toBeDisabled();
    const rawProposal = await page.getByLabel("OTIO imported cut preview JSON").inputValue();
    const preview = JSON.parse(rawProposal);
    expect(preview.schema).toBe("motionwright.otio-edit-preview/1");
    expect(preview.source_project_sha256).toBe(sourceSnapshotSha);
    expect(preview.project_id).toBe(current.id);
    expect(preview.expected_project_revision).toBe(current.revision);
    expect(preview.native_change_proposals).toEqual([
      { type: "move_scene", scene_id: current.scenes[1].id, to_index: 0 },
      { type: "set_scene_duration", scene_id: current.scenes[0].id,
        duration: { num: "5", den: "1" } },
    ]);
    expect(preview.media_refs_verified).toBe(expectedMediaRefs);
    expect(preview.missing_references_retained).toBe(kdenlivePair ? 0 : 1);
    if (kdenlivePair) {
      expect(preview.loss_report.join("\n")).toContain(record.initialReceiverOriginalSha256);
      expect(preview.loss_report.join("\n")).toContain(record.editedCutOriginalSha256);
      expect(preview.nle_receiver_acceptance).toBe("NOT_VERIFIED");
      expect(await readFile(join(exported, "received.otio"), "utf8")).toBe(receivedBytes);
      expect(await readFile(join(exported, "edited.otio"), "utf8")).toBe(editedBytes);
    }
    expect(preview.edit_imported).toBe(false);
    record.bridgeSourceBoundProposal = true;
    record.originalBridgeProposalSha256 = sha(Buffer.from(rawProposal));
    const bridgeCalls = service.transcript.filter(item =>
      item.direction === "request" && "command" in item.payload &&
      item.payload.command === "preview_otio_media_cut");
    expect(bridgeCalls).toHaveLength(1);
    record.desktopModuleBridgePreviewCalls = bridgeCalls.length;
    await expect(station).toBeVisible();
    await expect(station).toContainText(expectedMediaRefs + " bridge-declared verified source");
    await expect(page.getByLabel("Exact supported OTIO edits")).toContainText("End-trim");
    const commit = page.getByRole("button", { name: /Commit reviewed OTIO cut/ });
    await expect(commit).toBeDisabled();

    expect(await service.project(current.id)).toEqual(current);
    await page.getByLabel("OTIO reviewer declaration").fill("Owner test-only identity");
    await page.getByLabel("OTIO review rationale").fill(
      "Keep the original author video and scene objects; accept only reordering and end trim",
    );
    await page.getByLabel(/I reviewed and accept the listed property losses/).check();
    const absoluteAudio = page.getByLabel(/I acknowledge existing audio, transcript or marker timestamps/);
    if (await absoluteAudio.count()) await absoluteAudio.check();
    await expect(commit).toBeEnabled();
    await commit.click();
    await expect(page.locator(".revision-chip")).toHaveText("r" + (current.revision + 1));

    const after = await service.project(current.id);
    expect(after.revision).toBe(current.revision + 1);
    expect(after.scenes.map(scene => scene.id)).toEqual(
      [current.scenes[1].id, current.scenes[0].id],
    );
    expect(after.scenes[1].duration).toEqual({ num: "5", den: "1" });
    expect(after.scenes[0].nodes).toEqual(current.scenes[1].nodes);
    expect(after.scenes[1].nodes).toEqual(current.scenes[0].nodes);
    expect(after.assets).toEqual(current.assets);
    expect(after.audio).toEqual(current.audio);
    expect(await readFile(media)).toEqual(sourceBytes);
    const actualMutations = service.transcript.filter(item =>
      item.direction === "request" && "command" in item.payload &&
      item.payload.command === "apply_change");
    // Fixture setup uses import_asset_file, not apply_change. The owner
    // review is one and only one canonical Studio project mutation.
    expect(actualMutations).toHaveLength(1);
    record.uiCommitCount = actualMutations.length;

    // The already-read proposal cannot authorize another mutation on the
    // next revision. Only validate the retained JSON; no second media probe.
    await page.getByLabel("OTIO imported cut preview JSON").fill(rawProposal);
    await page.getByRole("button", { name: "Review bounded changes" }).click();
    await expect(page.locator(".otio-cut-review [role=alert]")).toContainText(
      /stale|foreign|revision/i,
    );
    await expect(page.getByRole("button", { name: /Commit reviewed OTIO cut/ }))
      .toHaveCount(0);
    expect(await service.project(current.id)).toEqual(after);

    const reopened = await service.reopen(current.id);
    expect(reopened).toEqual(after);
    expect(await service.project(current.id)).toEqual(after);
    const journal = await service.history(current.id, 0, 100);
    expect(journal.at(-1)?.revision).toBe(after.revision);
    final = after;

    // Actual writer process is fully closed. The normal finalizer inventories
    // original SQLite/CAS bytes; no fake success or an in-memory readback.
    if (process.env.MOTIONWRIGHT_R3_READ_ONLY_HANDOFF !== "1") {
      // Same legitimate SQLite writer handoff as desktop and R3. This is
      // read-only admission, not a second import, Save or grant.
      const handoff = await service.rawRequest<{
        read_only_handoff: boolean; journal_mode: string;
      }>({ command: "prepare_read_only_handoff", args: null });
      expect(handoff.ok).toBe(true);
      if (!handoff.ok) throw new Error("Original writer refused the read-only handoff");
      expect(handoff.result).toEqual({ read_only_handoff: true, journal_mode: "delete" });
      record.writerReadOnlyHandoff = true;
    }
    const closedStore = await service.finalizeClosedStore(
      info.outputPath("otio-closed-original"), final, [], [() => page.close()],
    );
    expect(closedStore.errors).toEqual([]);
    expect(closedStore.capture).not.toBeNull();
    expect(closedStore.oracle?.project).toEqual(final);
    closed = true;

    // A NEW process opens the unchanged *original* SQLite in explicit
    // StudioServiceReader mode. No writer, grant or fixture initializer is
    // present. This is stronger than merely reading the captured copy.
    const independent = await ScopedChangeService.createReadOnly({
      binaryPath: service.binaryPath,
      databasePath,
      seedFixture: false,
      desktopInvokeFixture: false,
    });
    try {
      expect(await independent.project(final.id)).toEqual(final);
      const history = await independent.history(final.id, 0, 100);
      expect(history.at(-1)?.revision).toBe(final.revision);
      record.independentStoreOpen = true;
    } finally {
      await independent.close();
    }
    await info.attach("otio-real-media-owner-cut", {
      body: JSON.stringify({
        schema: "motionwright.otio-real-media-studio-service/1",
        project_id: final.id, project_revision: final.revision,
        original_media_sha256: mediaSha, original_project_snapshot_sha256: sourceSnapshotSha,
        original_bridge_proposal_sha256: record.originalBridgeProposalSha256,
        bridge_media_refs_verified: expectedMediaRefs, physical_original_store_closed: true,
        observations: record,
      }, null, 2),
      contentType: "application/json",
    });
  } finally {
    const uiRefusals = await page.locator(".otio-cut-review [role=alert]")
      .allTextContents().catch(() => []);
    try {
      if (!closed) {
        // Single cleanup; errors from a known process are never converted to PASS.
        await service.close();
      }
    } finally {
      const commands = new Set([
        "export_portable_otio_media_bundle", "preview_otio_media_cut", "apply_change",
        "prepare_read_only_handoff",
      ]);
      const requestIds = new Set(service.transcript.flatMap(entry =>
        entry.direction === "request" && "command" in entry.payload
          && commands.has(entry.payload.command) ? [entry.payload.id] : []));
      // Retain the original request/response pair even on failure. These are
      // this test's synthetic owner inputs, never user media or Store files.
      await info.attach("otio-original-terminal-observation", {
        body: JSON.stringify({
          schema: "motionwright.otio-original-terminal-observation/1",
          observations: record, ui_refusals: uiRefusals,
          original_effect_transcript: service.transcript.filter(entry =>
            requestIds.has(entry.payload.id)),
        }, null, 2),
        contentType: "application/json",
      });
    }
  }
});
