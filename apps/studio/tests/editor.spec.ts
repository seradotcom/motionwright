import { expect, test } from "@playwright/test";

test("editor exposes real workspaces and browser-demo mutations", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Motionwright", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Timeline", exact: true })).toHaveAttribute("class", /active/);
  await expect(page.getByText("Reasoning is solved", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Brief", exact: true }).click();
  const title = page.getByLabel("Project title");
  await title.fill("Semwright Native Film");
  await page.getByRole("button", { name: "Save title" }).click();
  await expect(page.getByText("Semwright Native Film", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("r13", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Storyboard", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Storyboard" })).toBeVisible();
  await expect(page.locator(".story-card").filter({ hasText: "Pixels are brittle" })).toBeVisible();

  await page.screenshot({ path: "test-results/motionwright-editor.png", fullPage: true });
});

test("renderer and locks mutate the same visible project revision", async ({ page }) => {
  await page.goto("/");
  const renderer = page.getByLabel("Renderer", { exact: true });
  await expect(renderer).toHaveValue("motion-canvas");
  await renderer.selectOption("blender");
  await expect(page.getByText("r13", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Content", exact: true }).click();
  await expect(page.getByText("content", { exact: true })).toBeVisible();
  await expect(page.getByText("r14", { exact: true }).first()).toBeVisible();
});

test("keyboard focus is visible and workspace navigation remains operable", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  const active = page.locator(":focus");
  await expect(active).toBeVisible();

  await page.getByRole("button", { name: "Dependencies", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Dependencies" })).toBeVisible();
  await expect(page.getByText("NOT ADMITTED").first()).toBeVisible();
});


test("theme preference persists without mutating the project revision", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => window.localStorage.removeItem("motionwright.theme"));
  await page.reload();

  const revision = await page.locator(".revision-chip").first().innerText();
  const html = page.locator("html");
  const initialTheme = await html.getAttribute("data-theme");
  expect(initialTheme === "dark" || initialTheme === "light").toBeTruthy();

  const targetTheme = initialTheme === "dark" ? "light" : "dark";
  await page.getByRole("button", {
    name: initialTheme === "dark" ? "Use light theme" : "Use dark theme",
  }).click();

  await expect(html).toHaveAttribute("data-theme", targetTheme);
  await expect.poll(async () => page.evaluate(() => window.localStorage.getItem("motionwright.theme"))).toBe(targetTheme);
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", targetTheme);
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);
});

test("scene selection and timeline seeking share one playhead context", async ({ page }) => {
  await page.goto("/");
  const timelineTimecode = page.locator(".timeline-header .timecode");

  await page.getByRole("button", { name: "Storyboard", exact: true }).click();
  await page.locator(".story-card").filter({ hasText: "Pixels are brittle" }).click();
  await expect(timelineTimecode).toHaveText("00:07:00");

  await page.locator(".tree-row").filter({ hasText: "Semwright acts natively" }).click();
  await expect(timelineTimecode).toHaveText("00:16:00");

  const trackArea = page.locator(".track-area");
  const box = await trackArea.boundingBox();
  expect(box).not.toBeNull();
  if (!box) throw new Error("timeline track area is not measurable");
  await page.mouse.click(box.x + box.width * 0.25, box.y + 10);

  await expect(page.locator(".tree-row").filter({ hasText: "Pixels are brittle" })).toHaveClass(/selected/);
});

test("beat and cue selection share scene and playhead without mutating project state", async ({ page }) => {
  await page.goto("/");
  const timelineTimecode = page.locator(".timeline-header .timecode");
  const revision = await page.locator(".revision-chip").first().innerText();

  await page.getByRole("button", { name: "Beat: Pixel failure", exact: true }).click();
  await expect(timelineTimecode).toHaveText("00:08:00");
  await expect(page.locator(".tree-row").filter({ hasText: "Pixels are brittle" })).toHaveClass(/selected/);
  await expect(page.locator(".timeline-selection-context")).toContainText("Pixel failure");
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);

  await page.getByRole("button", { name: "Cue: Broker handoff", exact: true }).click();
  await expect(timelineTimecode).toHaveText("00:18:00");
  await expect(page.locator(".tree-row").filter({ hasText: "Semwright acts natively" })).toHaveClass(/selected/);
  await expect(page.locator(".timeline-selection-context")).toContainText("Broker handoff");
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);
});

test("design preview transport steps and plays without claiming a project edit", async ({ page }) => {
  await page.goto("/");
  const timelineTimecode = page.locator(".timeline-header .timecode");
  const revision = await page.locator(".revision-chip").first().innerText();

  await page.getByRole("button", { name: "Step forward one frame", exact: true }).click();
  await expect(timelineTimecode).toHaveText("00:00:01");

  await page.getByRole("button", { name: "Play design preview", exact: true }).click();
  await expect.poll(async () => timelineTimecode.innerText()).not.toBe("00:00:01");
  await page.getByRole("button", { name: "Pause design preview", exact: true }).click();

  await expect(page.locator(".revision-chip").first()).toHaveText(revision);
});

test("scene beat locks are enforced while unlocked beats stay synchronized with the timeline", async ({ page }) => {
  await page.goto("/");

  const lockedBeat = page.locator(".scene-beat-row").first();
  await expect(lockedBeat.getByRole("textbox", { name: "Beat label" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Fill timing gap", exact: true })).toBeDisabled();

  await page.locator(".tree-row").filter({ hasText: "Pixels are brittle" }).click();
  const revision = await page.locator(".revision-chip").first().innerText();
  const firstBeat = page.locator(".scene-beat-row").first();
  await expect(firstBeat.getByRole("textbox", { name: "Beat label" })).toBeEnabled();

  await firstBeat.getByRole("textbox", { name: "Beat label" }).fill("Pixel failure revised");
  await firstBeat.getByRole("button", { name: "Save beat", exact: true }).click();

  await expect(page.getByRole("button", { name: "Beat: Pixel failure revised", exact: true })).toBeVisible();
  await expect(page.locator(".revision-chip").first()).not.toHaveText(revision);

  const afterEdit = await page.locator(".revision-chip").first().innerText();
  await page.getByRole("button", { name: "Fill timing gap", exact: true }).click();
  await expect(page.locator(".scene-beat-row")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Beat: New beat", exact: true })).toBeVisible();
  await expect(page.locator(".revision-chip").first()).not.toHaveText(afterEdit);

  await page.getByRole("button", { name: "Remove beat New beat", exact: true }).click();
  await expect(page.locator(".scene-beat-row")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Beat: New beat", exact: true })).toHaveCount(0);
});

test("offline state is explicit while local editing remains available", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => window.dispatchEvent(new Event("offline")));
  await expect(page.getByText("OFFLINE · LOCAL", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Brief", exact: true })).toBeEnabled();

  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.getByText("OFFLINE · LOCAL", { exact: true })).toHaveCount(0);
});

test("workflow intelligence never fabricates canonical evidence in browser demo", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Workflows", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Workflow intelligence" })).toBeVisible();
  await expect(page.getByText("BROWSER DEMO", { exact: true })).toBeVisible();
  await expect(page.getByText("No live workflow evidence is being presented.", { exact: true })).toBeVisible();
  await expect(page.getByText("No proposal is currently ready from the connected evidence.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /promote/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /replay/i })).toHaveCount(0);
});

test("model preflight exposes minimal context without dispatch or silent fallback", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Alternatives", exact: true }).click();

  await expect(page.getByRole("region", { name: "Model request boundary" })).toBeVisible();
  await expect(page.getByText("NETWORK OFF", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Inspect request boundary", exact: true }).click();

  const result = page.getByRole("region", { name: "Model request preflight result" });
  await expect(result).toBeVisible();
  await expect(result).toContainText("fallback: none");
  await expect(result).toContainText("network dispatched: no");
  await expect(result).toContainText("UNTRUSTED DATA");
  await expect(result).toContainText("exact base");
});

test("canvas edits and stored alternatives use project revisions rather than local-only UI state", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("button", { name: "Storyboard", exact: true }).click();
  await page.locator(".story-card").filter({ hasText: "Pixels are brittle" }).click();
  await page.getByRole("button", { name: "Alternatives", exact: true }).click();
  await expect(page.getByText("Candidate budget")).toBeVisible();
  await expect(page.getByText("Contrast cut", { exact: true })).toBeVisible();

  const beforeSelection = await page.locator(".revision-chip").first().innerText();
  await page.getByRole("button", { name: "Select for review" }).nth(1).click();
  await expect(page.getByRole("button", { name: "Selected", exact: true })).toBeVisible();
  await expect.poll(async () => page.locator(".revision-chip").first().innerText()).not.toBe(beforeSelection);

  const beforeTransform = await page.locator(".revision-chip").first().innerText();
  await page.getByRole("button", { name: "Canvas", exact: true }).click();
  await expect(page.getByRole("region", { name: "Semantic canvas" })).toBeVisible();

  const x = page.getByLabel("Canvas X");
  await x.fill("796");
  await page.getByRole("button", { name: "Commit transform" }).click();
  await expect.poll(async () => page.locator(".revision-chip").first().innerText()).not.toBe(beforeTransform);

  await page.getByRole("button", { name: "Changes", exact: true }).click();
  await expect(page.getByRole("table", { name: "Project event journal" })).toBeVisible();
  await expect(page.getByText("select proposal", { exact: true })).toBeVisible();
  await expect(page.getByText("transform canvas node", { exact: true })).toBeVisible();
});

test("scene duration ripple reflows the shared timeline and journals one change", async ({ page }) => {
  await page.goto("/");

  await page.locator(".tree-row").filter({ hasText: "Pixels are brittle" }).click();

  const secondClip = page.locator(".timeline-clip").nth(1);
  const before = await secondClip.getAttribute("style");

  const duration = page.getByLabel("Scene duration seconds");
  await expect(duration).toBeEnabled();
  await duration.fill("8");
  await page.getByRole("button", { name: "Ripple", exact: true }).click();

  await expect.poll(async () => secondClip.getAttribute("style")).not.toBe(before);

  await page.getByRole("button", { name: "Changes", exact: true }).click();
  await expect(page.getByText("set scene duration", { exact: true })).toBeVisible();
});

test("visual system commits a new version and project style lock is enforceable", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Canvas", exact: true }).click();

  const systemName = page.getByLabel("Visual system name");
  await expect(systemName).toHaveValue("Cut Room Ledger");
  await systemName.fill("Cut Room Ledger Review");
  await page.getByRole("button", { name: "Commit visual system", exact: true }).click();

  await expect(page.getByText("v2", { exact: true })).toBeVisible();
  await expect(systemName).toHaveValue("Cut Room Ledger Review");

  await page.getByRole("button", { name: "Protect style", exact: true }).click();
  await expect(page.getByRole("button", { name: "Unlock style", exact: true })).toBeVisible();
  await expect(systemName).toBeDisabled();

  await page.getByRole("button", { name: "Unlock style", exact: true }).click();
  await expect(page.getByRole("button", { name: "Protect style", exact: true })).toBeVisible();
  await expect(systemName).toBeEnabled();
});

test("canvas hierarchy style relations and safe removal are versioned", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Canvas", exact: true }).click();

  await page.getByRole("button", { name: "group", exact: true }).click();
  await expect(page.locator(".canvas-tree-row").filter({ hasText: "Group 1" })).toBeVisible();

  await page.getByRole("button", { name: "shape", exact: true }).click();
  await expect(page.locator(".canvas-tree-row").filter({ hasText: "Shape 2" })).toBeVisible();

  await page.getByLabel("Canvas parent").selectOption({ label: "Group 1" });
  await page.getByLabel("Canvas z order").fill("7");
  await page.getByRole("button", { name: "Commit hierarchy", exact: true }).click();

  await page.getByLabel("Canvas fill").fill("#38424d");
  await page.getByRole("button", { name: "Commit style", exact: true }).click();

  await page.getByLabel("Canvas relation target").selectOption({ label: "Group 1" });
  await page.getByRole("button", { name: "Add canvas relation", exact: true }).click();
  await expect(page.locator(".relation-row").filter({ hasText: "Group 1" })).toBeVisible();

  await page.locator(".canvas-tree-row").filter({ hasText: "Group 1" }).click();
  await page.getByRole("button", { name: "Remove object", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("children");

  await page.locator(".canvas-tree-row").filter({ hasText: "Shape 2" }).click();
  await page.getByRole("button", { name: "Remove object", exact: true }).click();
  await expect(page.locator(".canvas-tree-row").filter({ hasText: "Shape 2" })).toHaveCount(0);

  await page.locator(".canvas-tree-row").filter({ hasText: "Group 1" }).click();
  await page.getByRole("button", { name: "Remove object", exact: true }).click();
  await expect(page.locator(".canvas-tree-row").filter({ hasText: "Group 1" })).toHaveCount(0);
});

test("production jobs workspace never fabricates runtime evidence in browser demo mode", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Jobs", exact: true }).click();

  await expect(page.getByRole("region", { name: "Production jobs" })).toBeVisible();
  await expect(page.getByText("No canonical render job receipts yet.", { exact: true })).toBeVisible();
  await expect(page.getByText(/Browser demo mode intentionally does not fabricate runtime jobs/)).toBeVisible();
  await expect(page.getByText(/A cancellation request stays unconfirmed until/)).toBeVisible();
});

test("portable project delivery is truthful in browser demo mode", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Import local asset" })).toBeDisabled();
  await expect(page.getByText("Local asset import is available in the desktop runtime.")).toBeVisible();

  await page.getByRole("button", { name: "Deliver", exact: true }).click();

  await expect(page.getByRole("region", { name: "Portable project" })).toBeVisible();
  await expect(page.getByLabel("Portable export path")).toBeDisabled();
  await expect(page.getByLabel("Portable import path")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Export bundle", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Inspect bundle", exact: true })).toBeDisabled();
  await expect(page.getByText(/Desktop filesystem capability is required/).last()).toBeVisible();
});


test("delivery profiles are versioned and filesystem claims remain truthful", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();

  await expect(page.getByRole("region", { name: "Delivery profiles" })).toBeVisible();
  await expect(page.getByRole("button", { name: "New profile", exact: true })).toBeVisible();
  await expect(page.getByLabel("Video codec")).toHaveValue("h264");
  await expect(page.getByLabel("Audio codec")).toHaveValue("aac");

  const before = await page.locator(".revision-chip").first().innerText();
  const name = page.getByLabel("Profile name");
  await name.fill("Master review");
  await page.getByRole("button", { name: "Save profile", exact: true }).click();
  await expect.poll(async () => page.locator(".revision-chip").first().innerText()).not.toBe(before);
  await expect(name).toHaveValue("Master review");

  await expect(page.getByLabel("Destination · absolute path")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Export sidecar", exact: true })).toBeDisabled();
  await expect(page.getByText("Desktop filesystem capability is required.").first()).toBeVisible();
});


test("delivery variants keep lineage, replan alternate aspect ratios and require explicit crop approval", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();

  await page.getByRole("button", { name: "Derive selected", exact: true }).click();
  await expect(page.locator(".delivery-truth-note").filter({ hasText: "Derived from Master 16:9" })).toBeVisible();
  await expect(page.getByLabel("Framing", { exact: true })).toHaveValue("replan");
  await expect(page.getByLabel("Frame rate", { exact: true })).toHaveValue("30/1");

  await page.getByLabel("Profile name").fill("Portrait campaign");
  await page.getByLabel("Width").fill("1080");
  await page.getByLabel("Height").fill("1920");
  await page.getByLabel("Cut label").fill("social-vertical");
  await page.getByRole("button", { name: "Save profile", exact: true }).click();

  await expect(page.locator(".delivery-profile-row").filter({ hasText: "Portrait campaign" })).toBeVisible();
  await expect(page.getByText(/original remains independently inspectable/)).toBeVisible();

  await page.getByLabel("Framing", { exact: true }).selectOption("crop");
  await expect(page.getByText("Approve crop")).toBeVisible();
  await page.getByRole("button", { name: "Save profile", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("crop framing requires explicit approval");

  await page.locator("label.delivery-inline-check").filter({ hasText: "Approve crop" }).locator("input").check();
  await page.getByRole("button", { name: "Save profile", exact: true }).click();
  await expect(page.getByText(/explicitly approved crop/)).toBeVisible();
});

test("audio workspace keeps measurement claims honest and versions mix intent", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Audio", exact: true }).click();

  await expect(page.getByRole("region", { name: "Audio workspace" })).toBeVisible();
  await expect(page.getByText("No measured take", { exact: true })).toBeVisible();
  await expect(page.getByText("Desktop runtime required for measured file import.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Import + measure", exact: true })).toBeDisabled();
  const waveform = page.getByRole("region", { name: "Measured sample peak waveform" });
  await expect(waveform).toContainText("No active measured take. No waveform is synthesized.");
  await expect(waveform.getByRole("img")).toHaveCount(0);

  const before = await page.locator(".revision-chip").first().innerText();
  await page.getByLabel("Voice gain dB").fill("-2");
  await page.getByLabel("Music gain dB").fill("-16");
  await page.getByRole("button", { name: "Save mix intent", exact: true }).click();
  await expect.poll(async () => page.locator(".revision-chip").first().innerText()).not.toBe(before);
  await expect(page.getByLabel("Voice gain dB")).toHaveValue("-2");
});

test("optional renderers stay closed until a reviewed project extension is explicitly enabled", async ({ page }) => {
  await page.goto("/");

  const renderer = page.getByLabel("Renderer", { exact: true });
  await expect(renderer.locator('option[value="remotion"]')).toBeDisabled();
  await expect(renderer.locator('option[value="manim-gl"]')).toBeDisabled();

  await page.getByRole("button", { name: "Integrations", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Integrations" })).toBeVisible();
  await expect(page.getByText("UPSTREAM GATE", { exact: true })).toBeVisible();
  await expect(page.getByText("CONTRACT ONLY", { exact: true })).toBeVisible();
  await expect(page.getByText("Remotion closed", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Register descriptor", exact: true }).click();
  await page.getByLabel("Extension display name").fill("Reviewed Remotion bridge");
  await page.getByLabel("Extension version").fill("1.2.3");
  await page.getByLabel("Extension license").fill("MIT");
  await page.getByLabel("Extension source").fill("https://example.invalid/remotion-bridge");
  await page.getByLabel("Extension digest").fill("ab".repeat(32));
  await page.getByLabel("Extension rights state").selectOption("cleared");
  await page.getByLabel("Explicitly enable").check();
  await page.getByRole("button", { name: "Register versioned descriptor", exact: true }).click();

  await expect(page.getByText("Remotion enabled", { exact: true })).toBeVisible();
  await expect(page.getByText("OPTED IN", { exact: true })).toBeVisible();
  await expect(renderer.locator('option[value="remotion"]')).toBeEnabled();
  await expect(renderer.locator('option[value="manim-gl"]')).toBeDisabled();

  await renderer.selectOption("remotion");
  await expect(renderer).toHaveValue("remotion");

  await page.getByRole("button", { name: "Disable", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("renderer is in use");
  await expect(renderer).toHaveValue("remotion");
});

test("Launchwright handoffs use public refs and exact output digests without remote mutation claims", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Integrations", exact: true }).click();

  const section = page.getByRole("region", { name: "Launchwright public handoff bindings" });
  await expect(section.getByText("No external handoff bindings.", { exact: true })).toBeVisible();

  await section.getByLabel("External resource ID").fill("release-public-42");
  await section.getByLabel("External resource revision").fill("r9");
  await section.getByRole("button", { name: "Record binding", exact: true }).click();
  await expect(section.getByText("release-public-42", { exact: true })).toBeVisible();
  await expect(section.getByText("context input", { exact: true })).toBeVisible();

  await section.getByLabel("Handoff direction").selectOption("artifact_output");
  await section.getByLabel("External resource ID").fill("artifact-public-84");
  await expect(section.getByRole("button", { name: "Record binding", exact: true })).toBeDisabled();
  await section.getByLabel("Handoff artifact digest").fill("cd".repeat(32));
  await expect(section.getByRole("button", { name: "Record binding", exact: true })).toBeEnabled();
  await section.getByRole("button", { name: "Record binding", exact: true }).click();

  await expect(section.getByText("artifact-public-84", { exact: true })).toBeVisible();
  await expect(section.getByText("artifact output", { exact: true })).toBeVisible();
  await expect(section.getByText("No remote mutation is performed by creating this binding.", { exact: true })).toBeVisible();
});


test("OpenTimelineIO export stays filesystem-gated and never implies lossless interchange in browser mode", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();

  const section = page.getByRole("region", { name: "OpenTimelineIO export" });
  await expect(section.getByText("LOSS-AWARE", { exact: true })).toBeVisible();
  await expect(section.getByLabel("OpenTimelineIO export path")).toBeDisabled();
  await expect(section.getByRole("button", { name: "Export .otio", exact: true })).toBeDisabled();
  await expect(section.getByText(/browser demo does not fabricate an OTIO file/)).toBeVisible();
  await expect(section.getByText(/Unsupported Motionwright semantics are returned as a loss report/)).toBeVisible();
});
