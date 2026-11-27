import { randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import type { CanvasNode, Project } from "../src/types";
import { ScopedChangeService } from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "Requires the source-built StudioService.");
test.describe.configure({ timeout: 90_000, retries: 0 });

const blends = ["multiply", "screen", "add"] as const;

function tile(name: string, x: number, y: number, size: number, fill: string, opacity: number, z: number): CanvasNode {
  return {
    id: randomUUID(), name, kind: "rectangle", parent_id: null, x, y,
    width: size, height: size, rotation_deg: 0, opacity,
    text: null, coordinate_space: "project_pixels", z_index: z,
    style: {
      fill, stroke: null, stroke_width: 0, font_family: null, font_size: null,
      font_weight: null, line_height: null, blend_mode: "normal",
    },
    property_locks: [], keyframes: [], relations: [],
  };
}

for (const [label, opacity] of [["opaque", 1], ["half-opacity", 0.5]] as const) {
  test(label + " Canvas blends save and reopen in the original Store", async ({ page }, info) => {
    const databasePath = info.outputPath("blend.sqlite3");
    const service = await ScopedChangeService.create({ databasePath, seedFixture: false, desktopInvokeFixture: true });
    let closed = false;
    try {
      const intent = { request_id: randomUUID(), title: "Original native blend study" };
      const grant = await service.invoke<{ token: string }>("issue_project_creation_grant", { request: intent });
      let { project } = await service.invoke<{ project: Project }>("create_project", {
        request: { intent, effect_grant: grant.token },
      });
      project = await service.apply(project, {
        type: "add_scene", name: "Blend study", objective: "Compare three blended overlaps", duration_seconds: 2,
      });
      const sceneId = project.scenes[0].id;
      for (const [index, mode] of blends.entries()) {
        const x = 260 + index * 480;
        for (const node of [
          tile(mode + " backdrop", x, 320, 250, "#406080", 1, 1),
          tile(mode + " overlay", x + 80, 410, 220, "#80C040", opacity, 2),
        ]) {
          project = await service.apply(project, { type: "add_canvas_node", scene_id: sceneId, node });
        }
      }
      const before = project;
      const databaseIdentity = await service.openedStoreIdentityWitness();
      const beforeHistory = await service.history(before.id, 0, 64);
      await service.attachPage(page, before.id);
      await page.goto("/");
      const canvas = page.getByRole("button", { name: "Canvas", exact: true });
      await canvas.click();
      const revision = page.locator(".command-bar .revision-chip");
      const blend = page.getByLabel("Canvas blend mode", { exact: true });
      const overlay = (mode: string) => page.locator(".canvas-tree-row").filter({ hasText: mode + " overlay" });
      for (const [index, mode] of blends.entries()) {
        await overlay(mode).click();
        await expect(blend).toHaveValue("normal");
        await blend.selectOption(mode);
        const compatibility = page.getByLabel("Canvas blend compatibility", { exact: true });
        if (opacity === 0.5) {
          await expect(compatibility).toContainText("Native Film cannot preserve the saved static opacity.");
        } else {
          await expect(compatibility).not.toContainText("Native Film cannot preserve the saved static opacity.");
        }
        await page.getByRole("button", { name: "Commit style", exact: true }).click();
        await expect(revision).toHaveText("r" + (before.revision + index + 1));
      }
      const saved = await service.project(before.id);
      const history = await service.history(before.id, 0, 64);
      expect(history.slice(0, beforeHistory.length)).toEqual(beforeHistory);
      const newEvents = history.slice(beforeHistory.length);
      expect(newEvents).toHaveLength(blends.length);
      for (const [index, mode] of blends.entries()) {
        const original = before.scenes[0].nodes.find(node => node.name === mode + " overlay")!;
        const style = { ...original.style, blend_mode: mode };
        expect(newEvents[index].change).toEqual({
          type: "update_canvas_style", scene_id: sceneId, node_id: original.id, style,
        });
        expect(saved.scenes[0].nodes.find(node => node.id === original.id)).toEqual({ ...original, style });
      }
      await page.reload();
      await canvas.click();
      for (const mode of blends) {
        await overlay(mode).click();
        await expect(blend).toHaveValue(mode);
      }
      await page.close();
      expect(await service.rawRequest({ command: "prepare_read_only_handoff", args: null }))
        .toMatchObject({ ok: true, result: { read_only_handoff: true, journal_mode: "delete" } });
      await service.close();
      closed = true;
      expect(service.lifecycleTaints).toEqual([]);
      const reader = await ScopedChangeService.createReadOnly({
        binaryPath: service.binaryPath, databasePath, seedFixture: false,
      });
      try {
        expect(await reader.openedStoreIdentityWitness()).toBe(databaseIdentity);
        expect(await reader.project(saved.id)).toEqual(saved);
        expect(await reader.history(saved.id, 0, 64)).toEqual(history);
      } finally {
        await reader.close();
      }
      await writeFile(info.outputPath("blend-studio.original.json"), JSON.stringify({
        schema: "motionwright.native-blend-studio/1", before, before_history: beforeHistory,
        project: saved, history, database_identity_sha256: databaseIdentity, closed_store_handoff: true,
      }), { flag: "wx" });
    } finally {
      if (!page.isClosed()) await page.close();
      if (!closed) await service.close();
    }
  });
}
