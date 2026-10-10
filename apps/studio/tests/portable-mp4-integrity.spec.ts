import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("local portable MP4 verification is explicit, read-only and supports an independent hash", async ({ page }) => {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const digest = "a".repeat(64);
  await page.addInitScript(({ initial, sha256 }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __MEDIA_CHECK__: {
        calls: Array<{ manifest_path: string; trusted_sha256: string | null }>;
        otherOperations: string[];
      };
    };
    runtime.__MEDIA_CHECK__ = { calls: [], otherOperations: [] };
    runtime.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "bootstrap") return structuredClone(initial);
        if (command === "verify_local_media_integrity") {
          const request = args?.request as { manifest_path: string; trusted_sha256: string | null };
          runtime.__MEDIA_CHECK__.calls.push(request);
          if (request.manifest_path.includes("tampered")) {
            throw new Error("Local MP4 SHA-256 does not match its integrity receipt.");
          }
          return {
            status: "sha256-content-verified",
            filename: "final.mp4",
            size_bytes: 12345,
            sha256,
            source_revision: "44",
            signed_authenticity: false,
            human_acceptance: false,
            trusted_anchor_matched: Boolean(request.trusted_sha256),
          };
        }
        runtime.__MEDIA_CHECK__.otherOperations.push(command);
        throw new Error("Synthetic verification must not dispatch " + command);
      },
    };
  }, { initial: boot, sha256: digest });

  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  const panel = page.getByRole("region", { name: "Verify portable MP4 integrity" });
  const action = panel.getByRole("button", { name: "Verify local MP4" });
  const path = panel.getByRole("textbox", { name: "Portable MP4 receipt path" });
  const anchor = panel.getByRole("textbox", { name: "Independent trusted MP4 SHA-256" });
  await expect(action).toBeDisabled();

  await path.fill("/home/test/final.mp4.motionwright-integrity.json");
  await expect(action).toBeEnabled();
  const revisionBefore = await page.locator(".revision-chip").first().innerText();
  await action.click();
  const result = panel.getByLabel("Portable MP4 integrity result");
  await expect(result).toContainText("SHA-256 CONTENT MATCH");
  await expect(result).toContainText("NOT PROVIDED");
  await expect(result).toContainText("r44 · self-declared");
  await expect(result).not.toContainText("PUBLISHER VERIFIED");

  await anchor.fill("ABC123");
  await expect(result).toHaveCount(0);
  await action.click();
  await expect(page.getByRole("alert")).toContainText("64 lowercase hexadecimal characters");
  const noDispatchedAnchor = await page.evaluate(() => (
    window as unknown as { __MEDIA_CHECK__: { calls: unknown[] } }
  ).__MEDIA_CHECK__.calls.length);
  expect(noDispatchedAnchor).toBe(1, "Invalid external anchors must not reach the native command");

  await anchor.fill(digest);
  await action.click();
  await expect(result).toContainText("MATCHED");
  const calls = await page.evaluate(() => (
    window as unknown as {
      __MEDIA_CHECK__: { calls: Array<Record<string, unknown>>; otherOperations: string[] };
    }
  ).__MEDIA_CHECK__);
  expect(calls.calls).toEqual([
    { manifest_path: "/home/test/final.mp4.motionwright-integrity.json", trusted_sha256: null },
    { manifest_path: "/home/test/final.mp4.motionwright-integrity.json", trusted_sha256: digest },
  ]);
  expect(calls.otherOperations).toHaveLength(0);
  await expect(page.locator(".revision-chip").first()).toHaveText(revisionBefore);

  await path.fill("/home/test/tampered.mp4.motionwright-integrity.json");
  await expect(result).toHaveCount(0);
  await action.click();
  await expect(page.getByRole("alert")).toContainText("does not match");
  await expect(result).toHaveCount(0);
  await expect(panel).toContainText("does not authenticate the publisher");
});

test("browser preview cannot claim to verify local files", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  const panel = page.getByRole("region", { name: "Verify portable MP4 integrity" });
  await expect(panel.getByRole("textbox", { name: "Portable MP4 receipt path" })).toBeDisabled();
  await expect(panel.getByRole("button", { name: "Verify local MP4" })).toBeDisabled();
  await expect(panel.getByLabel("Portable MP4 integrity result")).toHaveCount(0);
});
