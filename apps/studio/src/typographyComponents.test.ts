import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { defaultHeroConfig, emptyProductionDesign, heroNodeId, realizeProductHero } from "./creativeProduction";
import type { HeroConfig, HeroLayout } from "./creativeProduction";
import { previewTransform } from "./canvasMotion";
import { acknowledgeProductionDraft, createProductionDraft, decodeProductionCopy, editProductionDraft,
  encodeProductionCopy, prepareProductionSubmission, productionUnconfirmed } from "./productionDesignDraft";
import { rationalSeconds, seconds } from "./types";

const ID = "00000000-0000-4000-8000-000000000005";
const request = "00000000-0000-4000-8000-000000000006", writer = "00000000-0000-4000-8000-000000000007";
const layouts: HeroLayout[] = ["typography_focus", "typography_contrast"];
const copy = [
  ["One clear idea", "Space for the supporting thought."],
  ["Una idea clara", "Espacio para lo que importa."],
  ["Une idée claire", "Un espace pour le sens du message."],
];
const config = (layout: HeroLayout): HeroConfig => ({ ...defaultHeroConfig(), layout,
  eyebrow: "MOTIONWRIGHT / TYPE", headline: copy[0][0], body: copy[0][1], wordmark: "MW" });
function source() {
  const p = structuredClone(fixtureProject);
  p.locks = []; p.reviews = []; p.branch_workspaces = []; p.production_design = emptyProductionDesign();
  p.scenes = [{ ...p.scenes[0], nodes: [], beats: [], start: rationalSeconds(0), duration: rationalSeconds(6), renderer: "motion-canvas" as const }];
  return p;
}
const words = (value: string) => value.trim().split(/\s+/).join(" ");

describe("original editable headline and contrast typography", () => {
  it("keeps complete copy, stable IDs and glyph-box room across three languages and aspects", async () => {
    const ids = (await realizeProductHero(ID, defaultHeroConfig())).map(node => node.id);
    for (const layout of layouts) for (const [headline, body] of copy) for (const [w, h] of [[1920, 1080], [1080, 1080], [1080, 1920]]) {
      const nodes = await realizeProductHero(ID, { ...config(layout), headline, body }, w, h);
      expect(nodes.map(node => node.id)).toEqual(ids);
      expect(nodes.filter(node => node.kind === "text")).toHaveLength(4);
      expect(nodes.filter(node => node.kind === "rectangle")).toHaveLength(2);
      expect(words(nodes[1].text!)).toBe(words(headline)); expect(words(nodes[2].text!)).toBe(words(body));
      for (const node of nodes) {
        expect(node.x).toBeGreaterThanOrEqual(w * .05);
        expect(node.x + node.width).toBeLessThanOrEqual(w * .96);
        expect(node.y + node.height + 18 * Math.min(w, h) / 1080).toBeLessThanOrEqual(h * .96);
        if (node.kind === "text") {
          const font = node.style.font_size!, lines = node.text!.split("\n");
          expect(font * ((lines.length - 1) * 1.12 + 1.4)).toBeLessThanOrEqual(node.height * .96);
        }
      }
    }
  });
  it("groups the focus entrance and gives the supporting contrast idea a distinct later interval", async () => {
    const focus = await realizeProductHero(ID, config("typography_focus"));
    const alpha = focus.map(node => previewTransform(node, .4).opacity);
    expect(new Set(alpha).size).toBe(1); expect(alpha[0]).toBeGreaterThan(0); expect(alpha[0]).toBeLessThan(1);
    expect(focus.every(node => node.keyframes.every(key => Number.isInteger(seconds(key.at) * 30)))).toBe(true);
    const contrast = await realizeProductHero(ID, config("typography_contrast"));
    expect(previewTransform(contrast[1], .3).opacity).toBeGreaterThan(0);
    expect(previewTransform(contrast[2], .3).opacity).toBe(0);
    expect(contrast[1].style.font_size!).toBeGreaterThan(contrast[2].style.font_size!);
    expect(contrast[2].x).toBeGreaterThan(contrast[1].x + contrast[1].width);
    const portrait = await realizeProductHero(ID, config("typography_contrast"), 1080, 1920);
    expect(portrait[2].y).toBeGreaterThan(portrait[1].y + portrait[1].height);
  });
  it("keeps authored static equivalents and refuses unbreakable, blank or over-budget lines", async () => {
    for (const layout of layouts) {
      expect((await realizeProductHero(ID, { ...config(layout), motion: false })).every(node => !node.keyframes.length)).toBe(true);
      await expect(realizeProductHero(ID, { ...config(layout), headline: "X".repeat(60) }, 1080, 1920)).rejects.toThrow(/word wider/);
      await expect(realizeProductHero(ID, { ...config(layout), headline: "A\n\nB" })).rejects.toThrow(/text box/);
      await expect(realizeProductHero(ID, { ...config(layout), body: "A ".repeat(75).trim() }, 1080, 1080)).rejects.toThrow(/line budget|text box/);
    }
  });
  it("preserves independent human appearance across family updates and reopen while refusing text conflicts", async () => {
    let p = source(), scene_id = p.scenes[0].id;
    p = await applyChange(p, { type: "upsert_product_hero", instance_id: ID, scene_id, config: config("typography_focus") });
    const headline = p.scenes[0].nodes[1];
    p = await applyChange(p, { type: "update_canvas_style", scene_id, node_id: headline.id, style: { ...headline.style, fill: "#FFAA22" } });
    p = await applyChange(p, { type: "upsert_product_hero", instance_id: ID, scene_id, config: config("typography_contrast") });
    expect(p.scenes[0].nodes[1].style.fill).toBe("#FFAA22");
    expect(p.scenes[0].nodes.map(node => node.id)).toEqual(p.production_design!.heroes[0].baseline.map(node => node.id));
    const reopened = JSON.parse(JSON.stringify(p));
    expect(reopened).toEqual(p);
    p = await applyChange(p, { type: "update_canvas_text", scene_id, node_id: headline.id, text: "The human direction" });
    const before = structuredClone(p);
    await expect(applyChange(p, { type: "upsert_product_hero", instance_id: ID, scene_id,
      config: { ...config("typography_contrast"), headline: "A conflicting idea" } })).rejects.toThrow(/override conflict/);
    expect(p).toEqual(before);
    const detached = await applyChange(p, { type: "detach_product_hero", instance_id: ID });
    expect(detached.scenes[0].nodes).toEqual(p.scenes[0].nodes);
    expect(detached.production_design!.heroes).toEqual([]);
  });
  it("preserves incomplete typography input through recovery and an original late save reply", async () => {
    const p = source();
    const draft = editProductionDraft(createProductionDraft(p, { kind: "hero", sceneId: p.scenes[0].id }),
      { kind: "hero", config: config("typography_focus") });
    const prepared = await prepareProductionSubmission(draft, p, request);
    expect(productionUnconfirmed(prepared.draft)).toBe(true);
    const later = editProductionDraft(prepared.draft, { kind: "hero", config: { ...config("typography_contrast"), headline: "" } });
    const raw = encodeProductionCopy({ schema: "motionwright.production-recovery/1", scope: later.scope, writerId: writer, updatedAt: 1, draft: later });
    const recovered = decodeProductionCopy(raw, later.scope, writer).draft;
    expect(recovered.input).toEqual(later.input);
    const saved = await applyChange(p, prepared.change), acknowledged = acknowledgeProductionDraft(recovered, request, saved);
    expect(acknowledged.input).toEqual(later.input); expect(acknowledged.pending).toBeNull();
    expect(saved.production_design!.heroes[0].config.layout).toBe("typography_focus");
    expect(await heroNodeId(saved.production_design!.heroes[0].id, "headline")).toBe(saved.scenes[0].nodes[1].id);
  });
});
