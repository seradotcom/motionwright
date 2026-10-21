import { test, expect, type Page } from "./support/selectedSyntheticProject";
import { applyChange as applySyntheticFixtureChange } from "../src/api";
import { defaultOriginalMix } from "../src/deliveryRecipe";
import { fixtureBootstrap } from "../src/fixture";

type VoiceBinding = "absent" | "different" | "matching";
const activeVoiceId = "00000000-0000-4000-8000-000000000153";
const otherVoiceId = "00000000-0000-4000-8000-000000000154";
const musicId = "00000000-0000-4000-8000-000000000151";
const sfxId = "00000000-0000-4000-8000-000000000152";
const missingBinding = "Explicitly bind this delivery profile to the active measured voice take before auditioning.";
const differentBinding = "The delivery profile is bound to a different voice take than the active Audio take.";
const permissionLabel = /I confirm I have permission to use these exact original sources/;

// Reuses the selected-project and browser authoring fixtures. Bootstrap and
// runtime status are simulated; read-only waveform requests remain unavailable.
// No native PCM, SQLite, media, grant or receipt is supplied. The real component's
// saved-state blockers decide eligibility.
async function openBindingFixture(page: Page, binding: VoiceBinding) {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const project = boot.project;
  const voiceAsset = project.assets.find(asset => asset.media_type === "audio/wav");
  if (!voiceAsset?.content_sha256) throw new Error("The synthetic voice fixture needs a hashed WAV asset.");
  project.assets.push(
    { id: musicId, media_type: "audio/wav", content_sha256: "b".repeat(64),
      name: "Synthetic source music", source_revision: "synthetic-ui-fixture" },
    { id: sfxId, media_type: "audio/wav", content_sha256: "c".repeat(64),
      name: "Synthetic source effects", source_revision: "synthetic-ui-fixture" },
  );
  project.audio.voice_tracks = [activeVoiceId, otherVoiceId].map((id, index) => ({
    id, asset_id: voiceAsset.id, label: index === 0 ? "Active measured take" : "Other measured take",
    sample_rate_hz: 48_000, channels: 2, measured_duration: { num: "1", den: "1" },
    source_sha256: voiceAsset.content_sha256!, loudness_lufs: -22, true_peak_dbfs: -8,
  }));
  project.audio.active_voice_track_id = activeVoiceId;
  project.audio.transcript = [{
    id: "00000000-0000-4000-8000-000000000155", voice_track_id: activeVoiceId,
    start: { num: "0", den: "1" }, end: { num: "1", den: "1" },
    text: "Synthetic manually aligned source words", speaker: null, alignment: { kind: "manual" },
  }];
  project.audio.sfx_cues = [];
  const profileId = project.deliverables[0].id;
  project.deliverables[0].voice_track_id =
    binding === "absent" ? null : binding === "different" ? otherVoiceId : activeVoiceId;
  boot.project = await applySyntheticFixtureChange(project, {
    type: "set_delivery_recipe", deliverable_profile_id: profileId,
    recipe: { schema: "motionwright.delivery-recipe/1", scene_intents: [],
      original_mix: { ...defaultOriginalMix(), music_asset_id: musicId, sfx_asset_id: sfxId } },
  });

  await page.addInitScript(({ boot }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: {
        invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
      };
      __MIX_VOICE_BINDING_CALLS__: { reads: string[]; auditions: string[]; other: string[] };
    };
    const calls = { reads: [] as string[], auditions: [] as string[], other: [] as string[] };
    runtime.__MIX_VOICE_BINDING_CALLS__ = calls;
    runtime.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
      if (["bootstrap", "project_projection_head", "open_project", "production_runtime_status", "waveform_page"].includes(command)) {
        calls.reads.push(command);
      } else if (command === "native_original_mix_audition") {
        calls.auditions.push(command);
      } else {
        calls.other.push(command);
      }
      if (command === "bootstrap" || command === "project_projection_head" || command === "open_project") {
        return window.__SYNTHETIC_PROJECT_READ__(command, args, structuredClone(boot));
      }
      if (command === "production_runtime_status") return {
        status: "ready", reason: null, expected_version: "1.0.0", observed_version: "1.0.0",
        version_compatible: true, executable_sha256: "a".repeat(64),
        connection_identity: "b".repeat(64), pinned_revision: boot.native_sdk.pinned_revision,
      };
      if (command === "waveform_page") {
        throw new Error("Waveform reads are unavailable in this synthetic voice-binding fixture.");
      }
      throw new Error("No write or native operation is authorized by this synthetic fixture: " + command);
    } };
  }, { boot });

  await page.goto("/");
  await page.getByRole("button", { name: "Audio", exact: true }).click();
  const desk = page.locator('details[aria-label="Original three-bus native PCM audition"]');
  await expect(desk).toBeVisible();
  await desk.locator(":scope > summary").click();
  await expect(desk.getByLabel("Delivery language / profile")).toHaveValue(profileId);
  await expect(desk.getByLabel("Original music asset")).toHaveValue(musicId);
  await expect(desk.getByLabel("Original SFX asset")).toHaveValue(sfxId);
  await expect(desk.getByRole("region", { name: "Original mix draft review", exact: true })
    .getByRole("status").first()).toContainText("Showing saved recipe values at revision r" + boot.project.revision);
  return { desk, revision: boot.project.revision };
}

async function expectNoNativeOrWrite(page: Page, revision: number) {
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + revision);
  await expect(page.locator('details[aria-label="Original three-bus native PCM audition"] audio')).toHaveCount(0);
  const calls = await page.evaluate(() => (window as unknown as {
    __MIX_VOICE_BINDING_CALLS__: { reads: string[]; auditions: string[]; other: string[] };
  }).__MIX_VOICE_BINDING_CALLS__);
  await test.info().attach("synthetic-ipc-calls", {
    body: JSON.stringify(calls), contentType: "application/json",
  });
  expect(calls.reads).toContain("bootstrap");
  expect(calls.reads).toContain("project_projection_head");
  expect(calls.reads).toContain("open_project");
  expect(calls.auditions).toEqual([]);
  expect(calls.other).toEqual([]);
}

test("an unbound saved profile blocks audition before permission or a native request", async ({ page }) => {
  const { desk, revision } = await openBindingFixture(page, "absent");
  await expect(desk.getByRole("region", { name: "Original mix audition requirements" }))
    .toContainText(missingBinding);
  await expect(desk).toContainText("The saved profile must explicitly bind that active voice.");
  await expect(desk).not.toContainText("An unbound profile uses that active voice.");
  await expect(desk.getByLabel(permissionLabel)).toBeDisabled();
  await expect(desk.getByLabel(permissionLabel)).not.toBeChecked();
  await expect(desk.getByRole("button", { name: "Create local original PCM audition" })).toBeDisabled();
  await expectNoNativeOrWrite(page, revision);
});

test("a different saved voice binding remains blocked without a native request", async ({ page }) => {
  const { desk, revision } = await openBindingFixture(page, "different");
  const requirements = desk.getByRole("region", { name: "Original mix audition requirements" });
  await expect(requirements).toContainText(differentBinding);
  await expect(requirements).not.toContainText(missingBinding);
  await expect(desk.getByLabel(permissionLabel)).toBeDisabled();
  await expect(desk.getByLabel(permissionLabel)).not.toBeChecked();
  await expect(desk.getByRole("button", { name: "Create local original PCM audition" })).toBeDisabled();
  await expectNoNativeOrWrite(page, revision);
});

test("a matching saved voice binding enables local confirmation without dispatch", async ({ page }) => {
  const { desk, revision } = await openBindingFixture(page, "matching");
  await expect(desk.getByRole("region", { name: "Original mix audition requirements" })).toHaveCount(0);
  const permission = desk.getByLabel(permissionLabel);
  const audition = desk.getByRole("button", { name: "Create local original PCM audition" });
  await expect(permission).toBeEnabled();
  await expect(permission).not.toBeChecked();
  await expect(audition).toBeDisabled();
  await permission.check();
  await expect(permission).toBeChecked();
  await expect(audition).toBeEnabled();
  // Deliberately stop before invoking the native-only operation.
  await expectNoNativeOrWrite(page, revision);
  await permission.uncheck();
  await expect(audition).toBeDisabled();
  await expectNoNativeOrWrite(page, revision);
});
