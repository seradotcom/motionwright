import { describe, expect, it } from "vitest";
import { fixtureBootstrap } from "./fixture";
import { newSemanticGraph } from "./semanticGraph";
import { graphPreviewMissingCodepoints, graphPreviewText } from "./semanticGraphFonts";

function graph(kind: "causal_diagram" | "system_flow" = "causal_diagram") {
  const project = structuredClone(fixtureBootstrap.project);
  return newSemanticGraph(project, project.scenes[0], project.deliverables[0], kind);
}
describe("Graph preview's exact bundled glyph coverage", () => {
  it.each(["Instrument Sans Variable", "IBM Plex Mono"] as const)("keeps literal spaces, line breaks and Latin labels for %s", family => {
    const text = "  Niño · causal − effect\nÅngström  ";
    expect(graphPreviewMissingCodepoints(family, text)).toEqual([]);
    expect(text).toBe("  Niño · causal − effect\nÅngström  ");
  });
  it("keeps each family's actual supported scripts distinct", () => {
    expect(graphPreviewMissingCodepoints("IBM Plex Mono", "Пример")).toEqual([]);
    expect(graphPreviewMissingCodepoints("Instrument Sans Variable", "Пример").length).toBeGreaterThan(0);
  });
  it("reports unsupported Unicode scalars once without normalizing or replacing input", () => {
    const text = "漢字🙂漢";
    expect(graphPreviewMissingCodepoints("Instrument Sans Variable", text)).toEqual(["U+6F22", "U+5B57", "U+1F642"]);
    expect(text).toBe("漢字🙂漢");
  });
  it("checks visible source labels and units without treating hidden metadata as drawn text", () => {
    const value = graph(); value.content.vertices[0].label = "Value"; value.content.vertices[0].unit = { kind: "symbol", symbol: "kg" };
    value.content.vertices[0].meaning = "秘密"; const before = structuredClone(value);
    expect(graphPreviewText(value)).toContain("Value (kg)");
    expect(graphPreviewText(value)).not.toContain("秘密");
    expect(value).toEqual(before);
  });
  it("checks exactly the visible causal separator and negative influence sign", () => {
    const value = graph();
    if (value.content.kind !== "causal_diagram") throw new Error("Wrong fixture kind.");
    value.content.edges[0].label = "Reduces";
    value.content.edges[0].influence = "negative";
    const text = graphPreviewText(value);
    expect(text.split("\n")).toContain("Reduces \u00b7 \u2212");
    expect(graphPreviewMissingCodepoints(value.style.font_family, text)).toEqual([]);
  });
  it("includes future event labels before an unsupported glyph can appear during playback", () => {
    const value = graph("system_flow");
    if (value.content.kind !== "system_flow") throw new Error("Wrong fixture kind.");
    value.content.events.push({ id: crypto.randomUUID(), edge_id: value.content.edges[0].id, label: "Future 漢",
      at: { num: "1", den: "1" }, duration: { num: "1", den: "30" } });
    expect(graphPreviewMissingCodepoints(value.style.font_family, graphPreviewText(value))).toContain("U+6F22");
  });
});
