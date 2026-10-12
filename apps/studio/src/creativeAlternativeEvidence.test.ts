import {describe,expect,it} from "vitest";
import {fixtureProject} from "./fixture";
import {inspectCreativeAlternative} from "./creativeAlternativeEvidence";
import type {Project} from "./types";

const current=()=>structuredClone(fixtureProject);
function setup() {
  const project=current();
  const other=project.branches.find(b=>b.id!==project.active_branch)!;
  const stored=project.branch_workspaces.find(w=>w.branch_id===other.id)!;
  return {project,other,stored};
}
const codes=(p:Project,other:string)=>
  inspectCreativeAlternative(p,other).differences.map(d=>d.kind);

describe("MW05-E05-04 author-reviewed positive/negative saved source alternatives",()=>{
  it("reads a genuine saved branch without mutating project or inventing pixels",()=>{
    const {project,other}=setup();
    const original=structuredClone(project);
    const r=inspectCreativeAlternative(project,other.id);
    expect(r.schema).toBe("motionwright.v05-creative-alternatives/1");
    expect(r.project_id).toBe(project.id);
    expect(r.generation).toBe(project.generation);
    expect(r.observed_revision).toBe(project.revision);
    expect(r.active_branch_id).toBe(project.active_branch);
    expect(r.candidate_branch_id).toBe(other.id);
    expect(r.verdict).toBe("DIFFERENCES_PRESENT");
    expect(r.differences).toEqual(expect.arrayContaining([
      expect.objectContaining({kind:"scene_copy",scene_id:project.scenes[1].id}),
    ]));
    expect(r.human_approval_recorded).toBe(false);
    expect(r.observed_rendered_pixels).toBe(false);
    expect(r.native_sdk_authority_changed).toBe(false);
    expect(project).toEqual(original);
  });
  it("reports both identical branches without attributing visual pixel equivalence",()=>{
    const {project,other,stored}=setup();
    stored.current_state.scenes[1].objective=project.scenes[1].objective;
    const result=inspectCreativeAlternative(project,other.id);
    expect(result.verdict).toBe("NO_AUTHORED_DIFFERENCE");
    expect(result.differences).toEqual([]);
    expect(result.observed_rendered_pixels).toBe(false);
  });
  it("rejects a reference to the current branch or nonexistent one",()=>{
    const {project}=setup();
    expect(()=>inspectCreativeAlternative(project,project.active_branch)).toThrow("different");
    expect(()=>inspectCreativeAlternative(project,crypto.randomUUID())).toThrow("different");
  });
  it("fails closed when saved comparison state has been removed",()=>{
    const {project,other}=setup();
    project.branch_workspaces=project.branch_workspaces.filter(x=>x.branch_id!==other.id);
    expect(()=>inspectCreativeAlternative(project,other.id)).toThrow("no preserved");
  });
  it("rejects a candidate source head ahead of observed revision",()=>{
    const {project,other}=setup();
    other.head_revision=project.revision+1;
    expect(()=>inspectCreativeAlternative(project,other.id)).toThrow("future revision");
  });
  it("protects against a stored base revision newer than this project",()=>{
    const {project,other,stored}=setup();
    stored.base_revision=project.revision+1;
    other.base_revision=stored.base_revision;
    expect(()=>inspectCreativeAlternative(project,other.id)).toThrow("future revision");
  });
  it("detects scene addition and removal by stable identity",()=>{
    const {project,other,stored}=setup();
    const deleted=project.scenes[0];
    stored.current_state.scenes=stored.current_state.scenes.filter(s=>s.id!==deleted.id);
    stored.current_state.scenes.push({
      ...structuredClone(project.scenes[2]),
      id:crypto.randomUUID(),
      nodes:project.scenes[2].nodes.map(node=>({...structuredClone(node),id:crypto.randomUUID()})),
    });
    const report=inspectCreativeAlternative(project,other.id);
    expect(report.differences.map(x=>x.kind)).toEqual(expect.arrayContaining(["scene_omitted","scene_added"]));
    expect(report.differences.find(x=>x.kind==="scene_omitted")?.scene_id).toBe(deleted.id);
  });
  it("detects authored cut timing and scene camera separately",()=>{
    const {project,other,stored}=setup();
    stored.current_state.scenes[0].duration={num:"1001",den:"30000"};
    stored.current_state.scenes[0].camera.zoom=1.3;
    expect(codes(project,other.id)).toEqual(expect.arrayContaining(["scene_timing","scene_camera"]));
  });
  it("detects differences in renderer, original beat and status",()=>{
    const {project,other,stored}=setup();
    const scene=stored.current_state.scenes[0];
    scene.renderer="blender";scene.status="draft";
    scene.beats[0].objective="Different intention";
    expect(codes(project,other.id)).toEqual(expect.arrayContaining(["scene_renderer","scene_beats"]));
  });
  it("compares original object copy, geometry, style and motion individually",()=>{
    const {project,other,stored}=setup();
    const node=stored.current_state.scenes[0].nodes[0];
    node.text="Alternate authored headline";
    node.x+=44;
    node.style.fill="#FF8800";
    node.keyframes=[{at:{num:"1",den:"1"},property:"x",value:220,interpolation:"linear"}];
    expect(codes(project,other.id)).toEqual(expect.arrayContaining([
      "node_copy","node_geometry","node_style","node_motion",
    ]));
  });
  it("detects changed graph relations or property locks without flattening source",()=>{
    const {project,other,stored}=setup();
    stored.current_state.scenes[0].nodes[0].property_locks=["position"];
    expect(codes(project,other.id)).toContain("node_structure");
  });
  it("reports removed and added stable Canvas objects separately",()=>{
    const {project,other,stored}=setup();
    const scene=stored.current_state.scenes[0];
    const missing=scene.nodes[0].id;
    scene.nodes.shift();
    scene.nodes.push({...structuredClone(project.scenes[1].nodes[0]),id:crypto.randomUUID()});
    const report=inspectCreativeAlternative(project,other.id);
    expect(report.differences).toEqual(expect.arrayContaining([
      expect.objectContaining({kind:"node_omitted",node_id:missing}),
      expect.objectContaining({kind:"node_added",scene_id:scene.id}),
    ]));
  });
  it("records source branch global authoring differences without displaying private content",()=>{
    const {project,other,stored}=setup();
    stored.current_state.brief.objective="Different authored film";
    stored.current_state.narrative.premise="New argument";
    stored.current_state.audio.mix.voice_gain_db=-3;
    stored.current_state.visual_language.name="Alternate visual language";
    expect(codes(project,other.id)).toEqual(expect.arrayContaining([
      "brief","narrative","audio","visual_language",
    ]));
  });
  it("stable key order changes do not invent semantic style differences",()=>{
    const {project,other,stored}=setup();
    const style=structuredClone(stored.current_state.scenes[0].nodes[0].style);
    const reordered=Object.fromEntries(Object.entries(style).reverse()) as typeof style;
    stored.current_state.scenes[0].nodes[0].style=reordered;
    expect(codes(project,other.id)).not.toContain("node_style");
  });
  it("fails closed on oversized scene sources rather than silently skipping diffs",()=>{
    const {project,other}=setup();
    project.scenes=Array.from({length:513},(_,i)=>({
      ...project.scenes[0],id:String(i),
    }));
    expect(()=>inspectCreativeAlternative(project,other.id)).toThrow("budget");
  });
  it("does not rely on a common ancestor merge policy or claim a safe merge",()=>{
    const {project,other,stored}=setup();
    stored.base_revision=0;
    other.base_revision=0;
    const report=inspectCreativeAlternative(project,other.id);
    expect(report.differences.length).toBeGreaterThan(0);
    expect(Object.keys(report)).not.toContain("merge_approved");
    expect(report.human_approval_recorded).toBe(false);
  });
  it("refuses saved branch metadata that no longer matches its base snapshot",()=>{
    const {project,other,stored}=setup();
    other.base_revision=stored.base_revision+1;
    expect(()=>inspectCreativeAlternative(project,other.id)).toThrow("base revision disagree");
  });
  it("refuses duplicate scene IDs in either branch rather than undercounting content",()=>{
    const {project,other,stored}=setup();
    stored.current_state.scenes[1].id=stored.current_state.scenes[0].id;
    expect(()=>inspectCreativeAlternative(project,other.id)).toThrow("Duplicate scene identity");
  });
  it("refuses duplicate Canvas object IDs rather than reporting an empty visual delta",()=>{
    const {project,other,stored}=setup();
    const duplicate=structuredClone(stored.current_state.scenes[0].nodes[0]);
    stored.current_state.scenes[0].nodes.push(duplicate);
    expect(()=>inspectCreativeAlternative(project,other.id)).toThrow("Duplicate editable object identity");
  });
  it("does not mutate original baseline, edit history, unrelated rights or source bytes",()=>{
    const {project,other}=setup();
    const prior=structuredClone(project);
    inspectCreativeAlternative(project,other.id);
    expect(project).toEqual(prior);
  });
});
