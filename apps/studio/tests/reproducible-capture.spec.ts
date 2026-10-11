import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fixtureBootstrap } from "../src/fixture";

const CLAIM_ID = "00000000-0000-4000-8000-0000000000e7";
const MASK_COLOR = "#0B0B0B";
const ORIGIN = "http://127.0.0.1:1420";
const sha256 = (value: Uint8Array | string): string =>
  createHash("sha256").update(value).digest("hex");

test("pinned first-party browser scenario yields a repeatable redacted screenshot linked to a source claim", async ({ page, context, browserName }, testInfo) => {
  test.skip(browserName !== "chromium", "capture source is pinned to a known browser provider");
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const claim = {
    id: CLAIM_ID,
    text: "The authored Production workstation is visible in this first-party browser scenario.",
    source: { kind: "project_revision" as const, revision: boot.project.revision },
    context: "synthetic-first-party-capture-validation",
    source_revision: "fixture-" + boot.project.revision,
  };
  boot.project.brief.claims = [claim];
  await page.setViewportSize({ width: 1440, height: 940 });
  await context.clearCookies();
  const blocked: string[] = [];
  await page.route("**/*", async route => {
    const requestUrl = new URL(route.request().url());
    if (requestUrl.origin !== ORIGIN) {
      blocked.push(requestUrl.origin);
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
  // The test-only local fixture is injected into the ACTUAL Studio application.
  // It is not evidence of a user's private account or third-party product.
  await page.addInitScript(({ initial }) => {
    (window as unknown as {
      __TAURI_INTERNALS__: {
        invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown>
      };
    }).__TAURI_INTERNALS__ = {
      invoke: async command => {
        if (command === "bootstrap") return structuredClone(initial);
        throw new Error("Capture fixture cannot invoke unreviewed desktop effects: " + command);
      },
    };
  }, { initial: boot });

  async function reproduceScenario() {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Production", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Production", exact: true }).click();
    await expect(page.getByRole("heading", { name: "ProductHeroReveal", exact: true })).toBeVisible();
    await expect(page.getByRole("img", { name: /ProductHeroReveal 16:9/ })).toBeVisible();
    await expect(page.locator(".revision-chip")).toBeVisible();
    await page.addStyleTag({
      content: "* { animation: none !important; transition: none !important; caret-color: transparent !important; }",
    });
    await page.evaluate(async () => { await document.fonts.ready; });
    expect(await page.locator("input[type=password]:visible").count()).toBe(0);
    expect(await page.evaluate(() => window.devicePixelRatio)).toBe(1);
  }
  await reproduceScenario();
  const revisionChip = page.locator(".revision-chip");
  const bbox = await revisionChip.boundingBox();
  expect(bbox).not.toBeNull();
  const x = Math.floor(bbox!.x);
  const y = Math.floor(bbox!.y);
  const right = Math.ceil(bbox!.x + bbox!.width);
  const bottom = Math.ceil(bbox!.y + bbox!.height);
  const redaction = {
    selector: ".revision-chip",
    x, y, width: right - x, height: bottom - y,
    mask_color: MASK_COLOR,
  };
  const folder = testInfo.outputPath("source-capture");
  mkdirSync(folder, { recursive: true });
  const screenshotPath = path.join(folder, "capture.png");
  const capture = await page.screenshot({
    path: screenshotPath,
    fullPage: false,
    animations: "disabled",
    caret: "hide",
    mask: [revisionChip],
    maskColor: MASK_COLOR,
  });
  await reproduceScenario();
  const second = await page.screenshot({
    fullPage: false,
    animations: "disabled",
    caret: "hide",
    mask: [page.locator(".revision-chip")],
    maskColor: MASK_COLOR,
  });
  expect(sha256(capture)).toBe(sha256(second));
  expect(blocked).toEqual([]);

  const data = {
    id: boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision,
    claims: boot.project.brief.claims,
  };
  const statePath = path.join(folder, "fixture-state.json");
  writeFileSync(statePath, JSON.stringify(data, null, 2) + "\n", "utf8");
  const sourceRevision = (process.env.GITHUB_SHA ||
    execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim()).toLowerCase();
  expect(sourceRevision).toMatch(/^[0-9a-f]{40}$/);
  const version = JSON.parse(readFileSync("package.json", "utf8")).version as string;
  const receipt = {
    schema: "motionwright.first-party-capture-evidence/1",
    app: {
      id: "motionwright-studio",
      version,
      source_revision: sourceRevision,
    },
    scenario: {
      id: "production-original-ui-local-v1",
      origin: ORIGIN,
      route: "/",
      actions: ["open:/", "click:Production"],
      viewport: { width: 1440, height: 940, device_scale_factor: 1 },
      browser: "chromium",
      fixture_kind: "synthetic-first-party-data",
      auth_mode: "none",
      external_requests: blocked.length,
      credential_fields: 0,
    },
    claim_id: CLAIM_ID,
    fixture_state_sha256: sha256(readFileSync(statePath)),
    redactions: [redaction],
    capture: {
      path: "capture.png",
      sha256: sha256(capture),
      repeated_sha256: sha256(second),
      pixel_equal: capture.equals(second),
      redacted: true,
      raw_unredacted_saved: false,
      width: 1440,
      height: 940,
    },
    review_status: "technical-evidence-only-human-review-required",
  };
  writeFileSync(path.join(folder, "capture-evidence.json"),
    JSON.stringify(receipt, null, 2) + "\n", "utf8");
  await testInfo.attach("first-party-redacted-capture", {
    body: capture, contentType: "image/png",
  });
});
