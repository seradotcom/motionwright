import { describe, expect, it } from "vitest";
import { fixtureBootstrap } from "./fixture";
import { evaluateTypeSafety } from "./typographySafety";
import type { CanvasNode, Project } from "./types";

function setup() {
  const project:Project=structuredClone(fixtureBootstrap.project);
  const scene=project.scenes[0];
  const profile=project.deliverables[0];
  const node=scene.nodes.find(n=>n.kind==="text")!;
  return {project,scene,profile,node};
}
function classify(update:(node:CanvasNode)=>void) {
  const {project,scene,profile,node}=setup();
  update(node);
  return evaluateTypeSafety(project,scene,profile,1);
}

describe("source-bound typography framing preflight",()=>{
  it("reports that in-frame geometry is sampled but does not claim native legibility",()=>{
    const {project,scene,profile,node}=setup();
    const before=structuredClone(project);
    const report=evaluateTypeSafety(project,scene,profile,2);
    expect(report.schema).toBe("motionwright.typography-safearea-preflight/1");
    expect(report).toMatchObject({projectId:project.id,generation:project.generation,
      revision:project.revision,sceneId:scene.id,profileId:profile.id,
      sourceFrame:{width:1920,height:1080},formatCompatible:true});
    expect(report.findings[0].nodeId).toBe(node.id);
    expect(report.findings[0].status).toBe("sampled_inside");
    expect(report.findings[0].reason).toContain("unverified");
    expect(report.warningCount).toBe(0);
    expect(project).toEqual(before);
  });
  it("flags text extending past the physical source frame",()=>{
    const report=classify(node=>{node.x=1800;node.width=300;});
    expect(report.findings[0].status).toBe("frame_edge");
    expect(report.findings[0].firstRiskTime).toBe(0);
  });
  it("flags a margin breach without claiming the image is outside the frame",()=>{
    const report=classify(node=>{node.x=20;node.width=200;});
    expect(report.findings[0].status).toBe("safe_area");
    expect(report.warningCount).toBe(1);
  });
  it("detects a declared multi-line text box that exceeds its own height",()=>{
    const report=classify(node=>{
      node.text="FIRST\nSECOND";node.height=70;
      node.style.font_size=64;node.style.line_height=1.1;
    });
    expect(report.findings[0].status).toBe("declared_lines");
  });
  it("samples frame clocks and detects a late motion framing failure",()=>{
    const {project,scene,profile,node}=setup();
    node.keyframes.push({
      at:{num:"3",den:"1"},property:"x",value:1880,interpolation:"linear",
    });
    const finding=evaluateTypeSafety(project,scene,profile,0).findings[0];
    expect(finding.status).toBe("frame_edge");
    expect(finding.sampledTimes).toBeGreaterThan(3);
    expect(finding.firstRiskTime).toBeGreaterThan(0);
  });
  it("projects the actual semantic camera instead of ignoring zoom",()=>{
    const {project,scene,profile}=setup();
    scene.camera.zoom=3;
    expect(evaluateTypeSafety(project,scene,profile,0).findings[0].status).toBe("frame_edge");
  });
  it("fails closed for portrait and square adaptation without invented reflow",()=>{
    const {project,scene}=setup();
    for(const profile of project.deliverables.slice(1)) {
      const result=evaluateTypeSafety(project,scene,profile,0);
      expect(result.formatCompatible).toBe(false);
      expect(result.findings[0].status).toBe("unresolved");
      expect(result.findings[0].reason).toContain("changes aspect ratio");
    }
  });
  it("does not silently include scenes excluded by a variant",()=>{
    const {project,scene,profile}=setup();
    profile.included_scene_ids=[project.scenes[1].id];
    expect(evaluateTypeSafety(project,scene,profile,0).findings[0].status).toBe("unresolved");
  });
  it("fails closed for nested, related or unsupported coordinate spaces",()=>{
    for(const mutate of [
      (n:CanvasNode)=>{n.parent_id="00000000-0000-0000-0000-000000000000";},
      (n:CanvasNode)=>{n.coordinate_space="scene_local";},
      (n:CanvasNode)=>{n.relations=[{id:"00000000-0000-0000-0000-000000000000",
        kind:"align_left",target_id:"00000000-0000-0000-0000-000000000001"}];},
    ]) {
      expect(classify(mutate).findings[0].status).toBe("unresolved");
    }
  });
  it("does not report a PASS when font metadata is unknown or copy empty",()=>{
    expect(classify(n=>{n.style.font_size=null;}).findings[0].status).toBe("unresolved");
    expect(classify(n=>{n.text="";}).findings[0].status).toBe("unresolved");
  });
  it("refuses invalid margin, NaN pose or unbounded animations",()=>{
    const {project,scene,profile,node}=setup();
    scene.camera.safe_margin=0.6;
    expect(evaluateTypeSafety(project,scene,profile,0).findings[0].status).toBe("unresolved");
    scene.camera.safe_margin=0.05;
    node.rotation_deg=Number.NaN;
    expect(evaluateTypeSafety(project,scene,profile,0).findings[0].status).toBe("unresolved");
    node.rotation_deg=0;
    node.keyframes=Array.from({length:49},(_,i)=>({
      at:{num:String(i),den:"100"},property:"x" as const,
      value:node.x+i,interpolation:"linear" as const,
    }));
    expect(evaluateTypeSafety(project,scene,profile,0).findings[0].status).toBe("unresolved");
  });
  it("has no text findings when a scene contains only non-text nodes",()=>{
    const {project,scene,profile}=setup();
    scene.nodes=scene.nodes.filter(n=>n.kind!=="text");
    const result=evaluateTypeSafety(project,scene,profile,0);
    expect(result.findings).toEqual([]);
    expect(result.warningCount).toBe(0);
  });
});


describe("variant-specific typography provenance",()=>{
  it("does not reuse source text geometry when a profile replaces the copy",()=>{
    const {project,scene,profile,node}=setup();
    profile.text_overrides[node.id]="A much longer translated phrase, not independently laid out";
    const result=evaluateTypeSafety(project,scene,profile,1);
    expect(result.findings[0].status).toBe("unresolved");
    expect(result.findings[0].reason).toContain("profile-specific text overrides");
    expect(result.warningCount).toBe(0);
    expect(result.unresolvedCount).toBe(1);
  });
  it("treats source-aspect crop strategy as unknown instead of safe",()=>{
    const {project,scene,profile}=setup();
    profile.framing_strategy="crop";
    profile.crop_approved=true;
    const result=evaluateTypeSafety(project,scene,profile,1);
    expect(result.findings[0].status).toBe("unresolved");
    expect(result.findings[0].reason).toContain("crop strategy");
  });
});
