import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import CompositionStudy from "./CompositionStudy";
import ExpandedStudy from "./ExpandedStudy";
import { inheritedTextInk } from "./canvasInheritedInk";
import { fixtureProject } from "./fixture";
import type { CanvasNode } from "./types";

const rootText = (): CanvasNode => {
  const node = fixtureProject.scenes[0].nodes.find(item => item.kind === "text" && !item.parent_id);
  if (!node) throw new Error("The test needs a canonical root text object.");
  return structuredClone(node);
};
const inkTag = (nodes: CanvasNode[], ink: string | null = null) => {
  const html = renderToStaticMarkup(<CompositionStudy nodes={nodes} width={1920} height={1080}
    time={0} background="#101820" ink={ink} label="Actual editorial SVG ink proof"/>);
  const text = html.match(/<text\b[^>]*>/)?.[0];
  if (!text) throw new Error("No SVG text in the editorial composition.");
  return { html, text };
};

describe("canonical palette influences actual editorial SVG ink", () => {
  it("inherits the first saved text/ink token with ASCII-case-insensitive name and order", () => {
    const palette = [
      { name: "INK", value: "#F7EAD7" },
      { name: "text", value: "#112233" },
    ];
    expect(inheritedTextInk(palette)).toBe("#F7EAD7");
    const node = rootText(); node.style.fill = null;
    const { text } = inkTag([node], inheritedTextInk(palette));
    expect(text).toContain('fill="#F7EAD7"');
    expect(text).not.toContain('fill="#F2F4F3"');
  });

  it("explicit authored object paint always wins over the inherited project palette", () => {
    const node = rootText(); node.style.fill = "#A1B2C3";
    expect(inkTag([node], "#F7EAD7").text).toContain('fill="#A1B2C3"');
  });

  it("rejects unsafe paint instead of injecting CSS/SVG or choosing a later token", () => {
    const node = rootText(); node.style.fill = null;
    const palette = [
      { name: "text", value: "url(javascript:evil)" },
      { name: "ink", value: "#AABBCC" },
    ];
    const { html, text } = inkTag([node], inheritedTextInk(palette));
    expect(text).toContain('fill="#F2F4F3"');
    expect(html).not.toContain("url(javascript:evil)");
  });

  it("does not paint non-text shapes through the text ink token", () => {
    const node = rootText(); node.kind = "shape"; node.style.fill = null;
    const html = renderToStaticMarkup(<CompositionStudy nodes={[node]} time={0}
      background="#101820" ink="#F7EAD7" label="Shape and text must have separate paint authority"/>);
    expect(html).toContain('fill="none"');
    expect(html).not.toContain('fill="#F7EAD7"');
  });

  it("uses the same inherited text ink in the expanded composition viewer", () => {
    const node = rootText(); node.style.fill = null;
    const html = renderToStaticMarkup(<ExpandedStudy nodes={[node]} width={1920} height={1080}
      time={0} duration={6} background="#101820" ink="#F7EAD7"
      onSeek={() => {}} onClose={() => {}}/>);
    expect(html).toContain('fill="#F7EAD7"');
    expect(html).toContain("Expanded editorial composition study");
  });

  it("unsupported explicit object paint fails safely instead of inheriting ink silently", () => {
    const node = rootText();
    node.style.fill = "url(javascript:untrusted)";
    const { text, html } = inkTag([node], "#F7EAD7");
    expect(text).toContain('fill="#F2F4F3"');
    expect(html).not.toContain("url(javascript:untrusted)");
    expect(text).not.toContain('fill="#F7EAD7"');
  });

  it("falls back to neutral only when no eligible token exists", () => {
    const node = rootText(); node.style.fill = null;
    expect(inheritedTextInk([{ name: "surface", value: "#AA5533" }])).toBeNull();
    expect(inkTag([node], null).text).toContain('fill="#F2F4F3"');
  });
});
