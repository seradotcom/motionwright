import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { fixtureProject } from "./fixture";
import type { DeliverableProfile } from "./types";
import ProgramDesignStudy, { hasEditableProgramDesign } from "./ProgramDesignStudy";

function source() {
  const project = structuredClone(fixtureProject);
  const scene = project.scenes.find(item => hasEditableProgramDesign(item));
  if (!scene) throw new Error("A source Canvas scene is required.");
  const text = scene.nodes.find(item => item.kind === "text");
  if (!text) throw new Error("A saved text object is required.");
  return { project, scene, text };
}
const render = (p: ReturnType<typeof source>, profile: DeliverableProfile | null = p.project.deliverables[0]) =>
  renderToStaticMarkup(<ProgramDesignStudy project={p.project} scene={p.scene} time={0} profile={profile}/>);

describe("saved source Canvas is the Timeline Program editorial representation", () => {
  it("shows actual authored object identity, text and source revision, not a generic diagram", () => {
    const p = source();
    const html = render(p);
    expect(html).toContain(p.text.text!);
    expect(html).toContain('data-canvas-node-id="' + p.text.id + '"');
    expect(html).toContain('data-source-revision="' + p.project.revision + '"');
    expect(html).toContain('aria-label="Program Canvas editorial study: not native output"');
    expect(html).toContain("DESIGN_PREVIEW");
    expect(html).toContain("EDITORIAL SOURCE CANVAS");
    expect(html).toContain("Native frames, crop, fonts and final AV require separate verification");
  });

  it("honors the saved ink palette and an explicit object fill with original IDs intact", () => {
    const p = source();
    p.project.visual_language.palette = [{name: "INK", value: "#F7EAD7"}];
    p.text.style.fill = null;
    expect(render(p)).toContain('fill="#F7EAD7"');
    p.text.style.fill = "#C17A3E";
    const html = render(p);
    expect(html).toContain('fill="#C17A3E"');
    expect(html).toContain('data-canvas-node-id="' + p.text.id + '"');
  });

  it("renders selected output text overrides without modifying original Project text", () => {
    const p = source(), original = p.text.text;
    const profile = structuredClone(p.project.deliverables[0]);
    profile.text_overrides[p.text.id] = "Localized editorial Program";
    const html = render(p,profile);
    expect(html).toContain("Localized editorial Program");
    expect(html).not.toContain(">" + original + "<");
    expect(html).toContain('data-text-origin="profile"');
    expect(html).toContain('data-selected-profile-id="' + profile.id + '"');
    expect(p.text.text).toEqual(original);
  });

  it("does not resurrect original source text when a profile explicitly contains an empty string", () => {
    const p = source(); p.text.text = "Protected source phrase";
    const profile = structuredClone(p.project.deliverables[0]);
    profile.text_overrides[p.text.id] = "";
    const html = render(p,profile);
    expect(html).not.toContain("Protected source phrase");
    expect(html).toContain('data-text-origin="profile"');
  });

  it("admits only drawable Motion Canvas sources, not unsupported engines or groups", () => {
    const p = source();
    expect(hasEditableProgramDesign(p.scene)).toBe(true);
    const otherEngine = {...p.scene, renderer:"blender" as typeof p.scene.renderer};
    expect(hasEditableProgramDesign(otherEngine)).toBe(false);
    expect(hasEditableProgramDesign({...p.scene,nodes:p.scene.nodes.map(node=>({...node,kind:"group"}))})).toBe(false);
    expect(hasEditableProgramDesign({...p.scene,nodes:[]})).toBe(false);
    expect(hasEditableProgramDesign(null)).toBe(false);
  });

  it("does not reclassify a source Canvas SVG as verified Film/AV media", () => {
    const p = source();
    const html = render(p,null);
    expect(html).toContain("not native output");
    expect(html).not.toContain("Verified native PNG");
    expect(html).not.toContain("Verified H.264/AAC master");
    expect(p.project.revision).toEqual(fixtureProject.revision);
  });
});
