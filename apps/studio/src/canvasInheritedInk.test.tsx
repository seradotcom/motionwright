import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import CompositionStudy from "./CompositionStudy";
import ExpandedStudy from "./ExpandedStudy";
import CameraReframeWorkbench from "./CameraReframeWorkbench";
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

describe("parented Canvas editorial visibility", () => {
  it("renders a saved child text even when its group parent has no SVG glyph", () => {
    const group = rootText();
    group.id = "018f0000-0000-7000-8000-0000000003a1";
    group.kind = "group"; group.parent_id = null;
    group.text = null; group.z_index = 1;
    const child = rootText();
    child.id = "018f0000-0000-7000-8000-0000000003a2";
    child.parent_id = group.id;
    child.text = "Nested original author line";
    child.z_index = 5; child.style.fill = null;
    const other = rootText();
    other.id = "018f0000-0000-7000-8000-0000000003a3";
    other.parent_id = null; other.text = "Lower global layer"; other.z_index = 2;
    const html = renderToStaticMarkup(<CompositionStudy nodes={[child, other, group]} time={0}
      background="#101820" ink="#F7EAD7" label="Parented editorial visual continuity"/>);
    expect(html).toContain("Nested original author line");
    expect(html).toContain('data-parent-id="' + group.id + '"');
    expect(html).toContain('data-canvas-node-id="' + child.id + '"');
    expect(html).toContain('fill="#F7EAD7"');
    expect(html.indexOf("Lower global layer")).toBeLessThan(html.indexOf("Nested original author line"));
    expect(html).toContain("Native Film");
  });
});

describe("selected profile controls editorial text without changing canonical objects", () => {
  it("shows the explicit selected profile replacement and its origin", () => {
    const node = rootText(); node.text = "Original saved English source";
    const markup = renderToStaticMarkup(<CompositionStudy nodes={[node]} time={0}
      background="#101820" label="Variant text preview" textOverrides={{[node.id]: "Versión editorial traducida"}} />);
    expect(markup).toContain("Versión editorial traducida");
    expect(markup).not.toContain("Original saved English source");
    expect(markup).toContain('data-text-origin="profile"');
  });

  it("does not silently resurrect source text when a selected override is explicitly empty", () => {
    const node = rootText(); node.text = "Source must not reappear";
    const markup = renderToStaticMarkup(<CompositionStudy nodes={[node]} time={0}
      background="#101820" label="Empty locale draft cannot claim completed text" textOverrides={{[node.id]: ""}} />);
    expect(markup).not.toContain("Source must not reappear");
    expect(markup).toContain('data-text-origin="profile"');
  });

  it("keeps original text when no active profile override exists for that ID", () => {
    const node = rootText(); node.text = "Unchanged authored English copy";
    const markup = renderToStaticMarkup(<CompositionStudy nodes={[node]} time={0}
      background="#101820" label="Source profile text" textOverrides={{"another-node": "Unrelated locale"}} />);
    expect(markup).toContain("Unchanged authored English copy");
    expect(markup).not.toContain("Unrelated locale");
    expect(markup).toContain('data-text-origin="source"');
  });
});

describe("profile text flows through expanded and camera studies", () => {
  it("renders selected profile text in the enlarged editorial study", () => {
    const node = rootText(); node.text = "Base text from source";
    const html = renderToStaticMarkup(<ExpandedStudy nodes={[node]} width={1920} height={1080}
      time={0} duration={6} background="#101820" textOverrides={{[node.id]:"Perfil de salida guardado"}}
      onSeek={() => {}} onClose={() => {}}/>);
    expect(html).toContain("Perfil de salida guardado");
    expect(html).not.toContain("Base text from source");
    expect(html).toContain('data-text-origin="profile"');
  });

  it("the camera study follows explicit selected profile without altering source copy", () => {
    const project = structuredClone(fixtureProject), scene = project.scenes[0];
    const node = scene.nodes.find(item => item.kind === "text")!;
    node.text = "Untranslated source sentence";
    const profile = {...project.deliverables[0], text_overrides: {[node.id]: "Vista localizada por cámara"}};
    const html = renderToStaticMarkup(<CameraReframeWorkbench project={project} scene={scene}
      busy={false} commit={async () => {}} playhead={0} onSeek={() => {}}
      onOpenCanvas={() => {}} displayProfile={profile}/>);
    expect(html).toContain("Vista localizada por cámara");
    expect(html).not.toContain("Untranslated source sentence");
    expect(html).toContain('data-text-origin="profile"');
  });
});
