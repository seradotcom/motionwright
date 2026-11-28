import { expect, test } from "@playwright/test";

const voiceId = "11111111-1111-4111-8111-111111111111", segmentId = "22222222-2222-4222-8222-222222222222";
// Browser-fixture interaction only. Store reopening and sidecar effects are
// covered separately by the opt-in real Service test, never simulated here.
test("caption word selection follows editing, shared playback and explicit format negotiation", async ({ page }, info) => {
  await page.route("**/src/fixture.ts*", async route => {
    const response = await route.fetch(), body = await response.text();
    const setup = {
      voice_tracks: [{ id: voiceId, asset_id: "00000000-0000-7000-8000-000000000031", label: "Synthetic UI caption fixture",
        sample_rate_hz: 48000, channels: 1, measured_duration: { num: "2", den: "1" },
        source_sha256: "5f".repeat(32), loudness_lufs: null, true_peak_dbfs: null }],
      active_voice_track_id: voiceId,
      transcript: [{ id: segmentId, voice_track_id: voiceId, start: { num: "11", den: "30" }, end: { num: "5", den: "3" },
        text: "Una decisión clara", speaker: "Authored fixture", alignment: { kind: "manual" } }],
      cues: [],
    };
    await route.fulfill({ response, body: body + "\nObject.assign(fixtureProject.audio," + JSON.stringify(setup) + ");"
      + "\nfixtureProject.audio.voice_tracks[0].asset_id=fixtureProject.assets[0].id;"
      + "\nfixtureProject.locks=[];fixtureProject.branch_workspaces=[];"
      + "\nfixtureProject.deliverables[0].voice_track_id=" + JSON.stringify(voiceId) + ";"
      + "\nfixtureProject.scenes[0].start={num:'1',den:'10'};fixtureProject.scenes[0].duration={num:'69',den:'10'};"
      + "\nfixtureBootstrap.project=structuredClone(fixtureProject);\n" });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Audio", exact: true }).click();
  const input = page.getByLabel("Transcript " + segmentId, { exact: true });
  const row = page.locator(".audio-transcript-row").filter({ has: input });
  await expect(row).toHaveCount(1);
  const editor = row.getByRole("group", { name: "Caption emphasis audio-transcript-" + segmentId + "-emphasis", exact: true });
  const save = row.getByTitle("Save transcript segment as explicit manual alignment", { exact: true });
  const word = editor.getByRole("button", { name: "Emphasis word 2: decisión", exact: true });
  const revision = page.locator(".command-bar .revision-chip");
  const initialRevision = Number((await revision.innerText()).replace("r", ""));
  await word.click(); await expect(word).toHaveAttribute("aria-pressed", "true");
  await expect(editor.getByLabel("Caption cue style preview").locator("strong")).toHaveText("decisión");
  await page.getByRole("button", { name: "Brief", exact: true }).click();
  await page.getByRole("button", { name: "Audio", exact: true }).click();
  await expect(word).toHaveAttribute("aria-pressed", "true"); await expect(save).toBeEnabled();
  await save.click(); await expect(revision).toHaveText("r" + (initialRevision + 1));
  await row.getByTitle("Seek project timeline to transcript start", { exact: true }).click();
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  const program = page.getByLabel("Program caption source study", { exact: true });
  await expect(program).toHaveAttribute("data-caption-id", segmentId);
  await expect(program.locator("strong")).toHaveText("decisión");
  await page.getByRole("button", { name: "Step back one frame", exact: true }).click();
  await expect(program).toHaveAttribute("data-caption-id", "");
  await page.getByRole("button", { name: "Step forward one frame", exact: true }).click();
  await expect(program).toHaveAttribute("data-caption-id", segmentId);
  await expect(revision).toHaveText("r" + (initialRevision + 1));
  await page.getByRole("button", { name: "Audio", exact: true }).click();
  await input.fill("Otra decisión clara");
  await expect(editor.getByRole("alert", { name: "Caption emphasis source conflict" })).toContainText("matching");
  await expect(save).toBeDisabled();
  await editor.getByRole("button", { name: "Clear caption emphasis", exact: true }).click();
  await word.click(); await save.click(); await expect(revision).toHaveText("r" + (initialRevision + 2));
  await expect(editor.getByLabel("Caption cue style preview")).toHaveText("Otra decisión clara");
  await row.scrollIntoViewIfNeeded();
  const controlsFit = () => row.evaluate(element => {
    const bounds = element.getBoundingClientRect();
    return element.scrollWidth <= element.clientWidth && [...element.querySelectorAll("input, textarea, button, fieldset")].every(control => {
      const box = control.getBoundingClientRect(); return box.left >= bounds.left - 1 && box.right <= bounds.right + 1;
    });
  });
  expect(await controlsFit()).toBe(true);
  await page.screenshot({ path: info.outputPath("caption-word-emphasis-editable.png") });
  await page.setViewportSize({ width: 1024, height: 940 });
  expect(await controlsFit()).toBe(true);
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  const sidecar = page.getByRole("region", { name: "Caption sidecar export", exact: true });
  await sidecar.getByRole("combobox", { name: "Format", exact: true }).selectOption("srt");
  await expect(sidecar.getByLabel("Caption style format conflict")).toContainText("Choose WebVTT");
  await expect(sidecar.getByRole("button", { name: "Export sidecar", exact: true })).toBeDisabled();
  await sidecar.getByRole("combobox", { name: "Format", exact: true }).selectOption("web_vtt");
  await expect(sidecar.getByLabel("Caption style format conflict")).toHaveCount(0);
  await expect(revision).toHaveText("r" + (initialRevision + 2));
});
