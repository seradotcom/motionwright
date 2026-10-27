import { describe, expect, it } from "vitest";
import { applyChange, bootstrap, previewCreativePatch, sceneBoundaryAuthoringContext } from "./api";
import { defaultHeroConfig, heroNodeId, mergeComponentNodes, planContentDigest, realizeProductHero,
  parseSceneBoundary, parseSceneBoundaryAuthoringContext, parseSceneBoundaryContextRequest,
  parseSceneBoundaryEndpoint, parseSceneBoundaryFilmOptions, parseSceneBoundaryJson } from "./creativeProduction";
import type { CreativePatch, ProductionPlan } from "./creativeProduction";
import { easedProgress, previewTransform } from "./canvasMotion";
import { fixtureProject } from "./fixture";
import { rationalSeconds, seconds } from "./types";
import type { Project, SceneBoundary, SceneBoundaryAuthoringContext, SceneBoundaryContextRequest,
  SceneBoundaryEndpoint, SceneBoundaryFilmOptions, SceneBoundarySource } from "./types";

const instance = "00000000-0000-4000-8000-000000000005";
function source(): Project {
  const p = structuredClone(fixtureProject);
  p.locks=[]; p.branch_workspaces=[]; p.production_design={plan:null,heroes:[],capsules:[]};
  p.scenes=[{...p.scenes[0],nodes:[],beats:[],start:rationalSeconds(0),duration:rationalSeconds(6),renderer:"motion-canvas",status:"draft"}];
  p.reviews=[]; p.markers=[]; return p;
}
async function seeded() {
  const p=source();
  return applyChange(p,{type:"upsert_product_hero",instance_id:instance,scene_id:p.scenes[0].id,config:defaultHeroConfig()});
}

describe("first-party creative production",()=>{
  it("realizes stable component identities and exact bounded responsive output",async()=>{
    const landscape=await realizeProductHero(instance,defaultHeroConfig());
    expect(landscape).toHaveLength(6);
    expect(landscape.map(n=>n.name.split(" / ")[1])).toEqual(["eyebrow","headline","body","wordmark","rule","disclosure"]);
    for(const [w,h] of [[1920,1080],[1080,1920],[1080,1080]]){
      const nodes=await realizeProductHero(instance,defaultHeroConfig(),w,h);
      expect(nodes.map(n=>n.id)).toEqual(landscape.map(n=>n.id));
      for(const n of nodes){expect(n.x).toBeGreaterThanOrEqual(0);expect(n.y).toBeGreaterThanOrEqual(0);expect(n.x+n.width).toBeLessThanOrEqual(w);expect(n.y+n.height).toBeLessThanOrEqual(h);}
    }
    expect(landscape[0].id).toBe(await heroNodeId(instance,"eyebrow"));
    await expect(realizeProductHero(instance,{...defaultHeroConfig(),headline:"X".repeat(25)},1080,1920)).rejects.toThrow("wider");
  });
  it("reflows SplitExplanation with stable IDs and closes unsafe variation cases", async()=>{
    const original=await realizeProductHero(instance,defaultHeroConfig());
    const config={...defaultHeroConfig(), layout:"split_explanation" as const,
      headline:"First, the problem",body:"Then, explain the solution with original copy that can be edited.",wordmark:"DETAIL"};
    for(const [width,height] of [[1920,1080],[1080,1920],[1080,1080]]){
      const nodes=await realizeProductHero(instance,config,width,height);
      expect(nodes.map(node=>node.id)).toEqual(original.map(node=>node.id));
      expect(nodes).toHaveLength(6);
      expect(nodes.every(node=>node.name.startsWith("SplitExplanation / "))).toBe(true);
      nodes.forEach(node=>{
        expect(node.x).toBeGreaterThanOrEqual(0); expect(node.y).toBeGreaterThanOrEqual(0);
        expect(node.x+node.width).toBeLessThanOrEqual(width); expect(node.y+node.height).toBeLessThanOrEqual(height);
      });
      const rule=nodes.find(node=>node.name.endsWith("rule"))!;
      expect(width>height?rule.height>rule.width:rule.width>rule.height).toBe(true);
      expect(nodes.at(-1)!.text).toContain("NOT A CAPTURE");
    }
    const legacy=structuredClone(defaultHeroConfig());
    delete legacy.layout;
    expect(await realizeProductHero(instance,legacy)).toEqual(original);
    await expect(realizeProductHero(instance,{...config,layout:"unknown" as never})).rejects.toThrow("Unsupported semantic");
  });
  it("switches families without resetting independent human text, rejects overlapping edits",async()=>{
    let project=await seeded();const scene_id=project.scenes[0].id;
    const eyebrow=await heroNodeId(instance,"eyebrow");
    const body=await heroNodeId(instance,"body");
    project=await applyChange(project,{type:"update_canvas_text",scene_id,node_id:eyebrow,text:"Edited by a human"});
    const before=project.revision;
    project=await applyChange(project,{type:"upsert_product_hero",instance_id:instance,scene_id,config:{...defaultHeroConfig(),layout:"split_explanation",wordmark:"DETAIL"}});
    expect(project.revision).toBe(before+1);
    expect(project.scenes[0].nodes.find(node=>node.id===eyebrow)?.text).toBe("Edited by a human");
    expect(project.scenes[0].nodes.find(node=>node.id===body)?.name).toBe("SplitExplanation / body");
    project=await applyChange(project,{type:"update_canvas_text",scene_id,node_id:body,text:"An independent human explanation"});
    const prior=structuredClone(project);
    await expect(applyChange(project,{type:"upsert_product_hero",instance_id:instance,scene_id,
      config:{...defaultHeroConfig(),layout:"split_explanation",wordmark:"DETAIL",body:"An incompatible new claim"}})).rejects.toThrow("override conflict");
    expect(project).toEqual(prior);
  });
  it("samples hold and exact out-cubic entrances without changing old smoothstep semantics",async()=>{
    expect(easedProgress(.25,"ease_out_cubic")).toBe(.578125);
    expect(easedProgress(.25,"ease_in_out")).toBe(.15625);
    const nodes=await realizeProductHero(instance,defaultHeroConfig());
    const mark=nodes.find(n=>n.name.endsWith("wordmark"))!;
    expect(previewTransform(mark,0).opacity).toBe(0);
    expect(previewTransform(mark,.16).rotation_deg).toBe(-5);
    expect(previewTransform(mark,.81).opacity).toBeCloseTo(.875,12);
    expect(previewTransform(mark,.81).rotation_deg).toBeCloseTo(-.625,12);
    expect(previewTransform(mark,2)).toMatchObject({y:mark.y,rotation_deg:0,opacity:1});
  });
  it("preserves a human override through ten agent-style revisions and serialization",async()=>{
    let p=await seeded(); const sceneId=p.scenes[0].id, body=await heroNodeId(instance,"body");
    expect(p.scenes[0].nodes[0]).not.toBe(p.production_design!.heroes[0].baseline[0]);
    p=await applyChange(p,{type:"update_canvas_text",scene_id:sceneId,node_id:body,text:"A deliberate human line."});
    for(let revision=1;revision<=10;revision++){
      p=await applyChange(p,{type:"upsert_product_hero",instance_id:instance,scene_id:sceneId,config:{...defaultHeroConfig(),eyebrow:`CREATIVE PRODUCTION / REVISION ${revision}`}});
      p=JSON.parse(JSON.stringify(p)) as Project;
      expect(p.scenes[0].nodes.find(n=>n.id===body)?.text).toBe("A deliberate human line.");
      expect(p.production_design?.heroes[0].id).toBe(instance);
      expect(p.scenes[0].nodes).toHaveLength(6);
    }
  });
  it("rejects conflicts and locks atomically rather than erasing a human's work",async()=>{
    let p=await seeded(); const body=await heroNodeId(instance,"body");
    p=await applyChange(p,{type:"update_canvas_text",scene_id:p.scenes[0].id,node_id:body,text:"Human copy"});
    const before=structuredClone(p);
    await expect(applyChange(p,{type:"upsert_product_hero",instance_id:instance,scene_id:p.scenes[0].id,config:{...defaultHeroConfig(),body:"Agent copy"}})).rejects.toThrow("override conflict");
    expect(p).toEqual(before);
    const mark=await heroNodeId(instance,"wordmark");
    p=await applyChange(p,{type:"set_node_property_lock",scene_id:p.scenes[0].id,node_id:mark,property:"style",locked:true});
    await expect(applyChange(p,{type:"upsert_product_hero",instance_id:instance,scene_id:p.scenes[0].id,config:{...defaultHeroConfig(),accent:"#AABBCC"}})).rejects.toThrow("locked");
  });
  it("does not mutate browser storage, current project or revision during patch inspection",async()=>{
    const p=await seeded(), body=await heroNodeId(instance,"body"), before=await bootstrap();
    const patch:CreativePatch={scene_id:p.scenes[0].id,base_revision:p.revision,rationale:"Shorten the line without touching the rest of the composition",edits:[{kind:"text",node_id:body,text:"A shorter line."}]};
    const preview=await previewCreativePatch(p,patch);
    expect(await bootstrap()).toEqual(before);
    expect(preview.before[0].text).not.toBe(preview.after[0].text);
    expect(preview.after[0].text).toBe("A shorter line.");
    expect(seconds(preview.dirty_end)).toBe(6);
    expect(preview.kind).toContain("not_pixel_verification");
    const applied=await applyChange(p,{type:"apply_creative_patch",patch});
    expect(applied.revision).toBe(p.revision+1);
    expect(applied.scenes[0].nodes.filter(n=>n.id!==body)).toEqual(p.scenes[0].nodes.filter(n=>n.id!==body));
    await expect(applyChange(applied,{type:"apply_creative_patch",patch})).rejects.toThrow("stale");
  });
  it("keeps multiple patch edits atomic and enforces per-property locks",async()=>{
    let p=await seeded();const scene_id=p.scenes[0].id,body=await heroNodeId(instance,"body"),mark=await heroNodeId(instance,"wordmark");
    p=await applyChange(p,{type:"set_node_property_lock",scene_id,node_id:mark,property:"text",locked:true});
    const before=await bootstrap();
    const patch:CreativePatch={scene_id,base_revision:p.revision,rationale:"A proposal that must fail as a unit",edits:[{kind:"text",node_id:body,text:"Should never be stored"},{kind:"text",node_id:mark,text:"Locked"}]};
    await expect(applyChange(p,{type:"apply_creative_patch",patch})).rejects.toThrow("locked");
    expect(await bootstrap()).toEqual(before);
    await expect(previewCreativePatch(p,{...patch,edits:[{kind:"text",node_id:crypto.randomUUID(),text:"Outside scope"}]})).rejects.toThrow("outside");
  });
  it("preserves node deletion and offers explicit detach without deleting authored objects",async()=>{
    let p=await seeded();const baseline=p.production_design!.heroes[0].baseline;
    expect(()=>mergeComponentNodes(baseline,p.scenes[0].nodes.slice(1),baseline)).toThrow("deleted");
    const nodes=structuredClone(p.scenes[0].nodes);
    p=await applyChange(p,{type:"detach_product_hero",instance_id:instance});
    expect(p.production_design?.heroes).toHaveLength(0);expect(p.scenes[0].nodes).toEqual(nodes);
  });
  it("keeps plan approval tied to its current content and requires actual source assets for capture claims",async()=>{
    let p=await seeded();const plan:ProductionPlan={objective:"Explain the workflow",audience:"Creative teams",concept:"Typography carries a truthful argument",reference_constraints:[],exclusions:["No fake product screenshots"],shots:[{scene_id:p.scenes[0].id,purpose:"Open the argument",claim_ids:[],asset_ids:[],evidence_kind:"graphic_study"}],clock:{kind:"timeline"},approval:null};
    const digest=await planContentDigest(plan);
    p=await applyChange(p,{type:"set_production_plan",plan:{...plan,approval:{reviewer:"Owner",note:"Approved concept, not a fidelity pass",content_sha256:digest}}});
    await expect(applyChange(p,{type:"set_production_plan",plan:{...p.production_design!.plan!,concept:"Changed concept"}})).rejects.toThrow("stale");
    await expect(applyChange(p,{type:"set_production_plan",plan:{...plan,shots:[{...plan.shots[0],evidence_kind:"real_capture"}]}})).rejects.toThrow("digest-bound");
  });
});


describe("MetricEvidence as a third versioned composition family", () => {
  const config = {
    ...defaultHeroConfig(), layout:"metric_evidence" as const,
    eyebrow:"USER AUTHORED / NOT VERIFIED",
    headline:"42% growth",
    body:"A reported figure needs a source, date and independently checked evidence.",
    wordmark:"DATA",accent:"#D9A46E",
  };

  it("reflows in three aspects with stable semantic IDs and a persistent unverified disclosure", async () => {
    const original=await realizeProductHero(instance,defaultHeroConfig());
    for(const [width,height] of [[1920,1080],[1080,1920],[1080,1080]]) {
      const nodes=await realizeProductHero(instance,config,width,height);
      expect(nodes).toHaveLength(6);
      expect(nodes.map(n=>n.id)).toEqual(original.map(n=>n.id));
      for(const node of nodes) {
        expect(node.name.startsWith("MetricEvidence / ")).toBe(true);
        expect(node.x).toBeGreaterThanOrEqual(0);
        expect(node.y).toBeGreaterThanOrEqual(0);
        expect(node.x+node.width).toBeLessThanOrEqual(width);
        expect(node.y+node.height).toBeLessThanOrEqual(height);
      }
      const rule=nodes.find(n=>n.name.endsWith("rule"))!;
      expect(width>height?rule.height>rule.width:rule.width>rule.height).toBe(true);
      expect(nodes.find(n=>n.name.endsWith("headline"))).toMatchObject({text:"42% growth",style:{fill:"#D9A46E"}});
      expect(nodes.at(-1)?.text).toBe("METRIC EVIDENCE / EDITORIAL STUDY · SOURCE NOT VERIFIED");
    }
    const staticNodes=await realizeProductHero(instance,{...config,motion:false});
    expect(staticNodes.every(n=>n.keyframes.length===0)).toBe(true);
  });

  it("protects human source and explanations while switching families", async () => {
    let p=await seeded();const scene_id=p.scenes[0].id;
    const eyebrow=await heroNodeId(instance,"eyebrow");
    const body=await heroNodeId(instance,"body");
    p=await applyChange(p,{type:"update_canvas_text",scene_id,node_id:eyebrow,text:"Human-authored source context"});
    // Keep the baseline generated eyebrow while moving to the new family.
    // Changing the eyebrow *and* preserving a human edit would be a real conflict.
    const compatibleConfig={...config,eyebrow:defaultHeroConfig().eyebrow};
    p=await applyChange(p,{type:"upsert_product_hero",instance_id:instance,scene_id,config:compatibleConfig});
    expect(p.scenes[0].nodes.find(n=>n.id===eyebrow)?.text).toBe("Human-authored source context");
    expect(p.scenes[0].nodes.find(n=>n.id===body)?.name).toBe("MetricEvidence / body");
    p=await applyChange(p,{type:"update_canvas_text",scene_id,node_id:body,text:"Human-controlled explanation"});
    const before=structuredClone(p);
    await expect(applyChange(p,{type:"upsert_product_hero",instance_id:instance,scene_id,
      config:{...compatibleConfig,body:"An incompatible regenerated metric claim"}})).rejects.toThrow("override conflict");
    expect(p).toEqual(before);
  });
});

describe("StepSequence as a fourth source-preserving native composition family",()=>{
  const config = {
    ...defaultHeroConfig(),layout:"step_sequence" as const,
    eyebrow:"WORKFLOW / AUTHOR MARKED STEP",
    headline:"Inspect the intended result",
    body:"Read the native project state and confirm the actual change before approving the next action.",
    wordmark:"02 / 04",accent:"#D9A46E",
  };
  it("uses six stable editable semantic identities, safe boundaries and a mandatory disclaimer",async()=>{
    const canonical=await realizeProductHero(instance,defaultHeroConfig());
    for(const [width,height] of [[1920,1080],[1080,1920],[1080,1080]]) {
      const nodes=await realizeProductHero(instance,config,width,height);
      expect(nodes).toHaveLength(6);
      expect(nodes.map(node=>node.id)).toEqual(canonical.map(node=>node.id));
      expect(nodes.every(node=>node.name.startsWith("StepSequence / "))).toBe(true);
      for(const node of nodes){
        expect(node.x).toBeGreaterThanOrEqual(0);
        expect(node.y).toBeGreaterThanOrEqual(0);
        expect(node.x+node.width).toBeLessThanOrEqual(width);
        expect(node.y+node.height).toBeLessThanOrEqual(height);
      }
      expect(nodes.find(node=>node.name.endsWith("wordmark"))?.text).toBe("02 / 04");
      expect(nodes.find(node=>node.name.endsWith("rule"))?.style.fill).toBe("#D9A46E");
      expect(nodes.find(node=>node.name.endsWith("disclosure"))?.text)
        .toBe("STEP SEQUENCE / EDITORIAL STUDY · NOT AN OBSERVED WORKFLOW");
      const rule=nodes.find(node=>node.name.endsWith("rule"))!;
      expect(width>height?rule.height>rule.width:rule.width>rule.height).toBe(true);
      expect(nodes.filter(node=>node.kind==="text")).toHaveLength(5);
    }
  });
  it("declares a static equivalent without faking motion",async()=>{
    const nodes=await realizeProductHero(instance,{...config,motion:false});
    expect(nodes.every(node=>node.keyframes.length===0)).toBe(true);
    expect(nodes.find(node=>node.name.endsWith("disclosure"))?.text).toContain("NOT AN OBSERVED");
  });
  it("preserves precise stepped cubic entrance timings on source nodes",async()=>{
    const nodes=await realizeProductHero(instance,config);
    const mark=nodes.find(node=>node.name.endsWith("wordmark"))!;
    const statement=nodes.find(node=>node.name.endsWith("headline"))!;
    const disclosure=nodes.find(node=>node.name.endsWith("disclosure"))!;
    expect(previewTransform(mark,0).opacity).toBe(0);
    expect(previewTransform(mark,2).opacity).toBe(1);
    expect(previewTransform(statement,2).opacity).toBe(1);
    expect(previewTransform(disclosure,2).opacity).toBe(1);
    expect(mark.keyframes.every(k=>k.property!=="rotation_deg")).toBe(true);
    expect(disclosure.keyframes.at(-1)?.interpolation).toBe("ease_out_cubic");
  });
  it("stores a new editable StepSequence in one ordinary service revision",async()=>{
    const p=source();
    const after=await applyChange(p,{
      type:"upsert_product_hero",instance_id:instance,
      scene_id:p.scenes[0].id,config,
    });
    expect(after.revision).toBe(p.revision+1);
    expect(after.scenes[0].nodes).toHaveLength(6);
    expect(after.scenes[0].status).toBe("draft");
    expect(after.production_design?.heroes[0].config.layout).toBe("step_sequence");
    const restored=JSON.parse(JSON.stringify(after)) as Project;
    expect(restored.production_design?.heroes[0].baseline)
      .toEqual(await realizeProductHero(instance,config,1920,1080));
    expect(p.production_design?.heroes).toHaveLength(0);
  });
  it("does not replace independent human copy during layout transition",async()=>{
    let p=await seeded();
    const scene_id=p.scenes[0].id;
    const eyebrow=await heroNodeId(instance,"eyebrow");
    const body=await heroNodeId(instance,"body");
    p=await applyChange(p,{
      type:"update_canvas_text",scene_id,node_id:eyebrow,
      text:"Author-validated scope: Step 02",
    });
    // Keep authored eyebrow baseline constant, so there is no overlapping
    // generated change to the same property.
    p=await applyChange(p,{
      type:"upsert_product_hero",instance_id:instance,scene_id,
      config:{...config,eyebrow:defaultHeroConfig().eyebrow},
    });
    expect(p.scenes[0].nodes.find(n=>n.id===eyebrow)?.text).toBe("Author-validated scope: Step 02");
    expect(p.scenes[0].nodes.find(n=>n.id===body)?.name).toBe("StepSequence / body");
    p=await applyChange(p,{
      type:"update_canvas_text",scene_id,node_id:body,
      text:"An editor-owned explanation that must survive revisions.",
    });
    const prior=structuredClone(p);
    await expect(applyChange(p,{
      type:"upsert_product_hero",instance_id:instance,scene_id,
      config:{...config,eyebrow:defaultHeroConfig().eyebrow,
        body:"An incompatible generated step explanation"},
    })).rejects.toThrow("override conflict");
    expect(p).toEqual(prior);
  });
  it("rejects over-budget source words before a native render or mutation",async()=>{
    await expect(realizeProductHero(instance,{
      ...config,headline:"X".repeat(20),
    },1920,1080)).rejects.toThrow("wider");
  });
});

describe("attached MetricEvidence source notice cannot be rewritten into a verification claim",()=>{
  it("rejects source-disclosure text edits and node deletion without changing the project",async()=>{
    const sourceProject=source();
    let p=await applyChange(sourceProject,{
      type:"upsert_product_hero",instance_id:instance,scene_id:sourceProject.scenes[0].id,
      config:{...defaultHeroConfig(),layout:"metric_evidence",headline:"42% growth",
        body:"Original user-authored data, pending source verification.",wordmark:"DATA"}
    });
    const scene_id=p.scenes[0].id;
    const node_id=await heroNodeId(instance,"disclosure");
    expect(p.scenes[0].nodes.find(n=>n.id===node_id)?.text)
      .toBe("METRIC EVIDENCE / EDITORIAL STUDY · SOURCE NOT VERIFIED");
    const prior=structuredClone(p);
    await expect(applyChange(p,{
      type:"update_canvas_text",scene_id,node_id,text:"VERIFIED BY US"
    })).rejects.toThrow("disclosure");
    await expect(applyChange(p,{
      type:"remove_canvas_node",scene_id,node_id
    })).rejects.toThrow("disclosure");
    const disclosure=p.scenes[0].nodes.find(n=>n.id===node_id)!;
    await expect(applyChange(p,{
      type:"transform_canvas_node",scene_id,node_id,
      transform:{x:disclosure.x,y:disclosure.y,width:disclosure.width,height:disclosure.height,
        rotation_deg:disclosure.rotation_deg,opacity:0}
    })).rejects.toThrow("disclosure");
    expect(p).toEqual(prior);
    p=await applyChange(p,{type:"detach_product_hero",instance_id:instance});
    expect(p.production_design?.heroes).toHaveLength(0);
    expect(p.scenes[0].nodes.find(n=>n.id===node_id)?.text).toContain("SOURCE NOT VERIFIED");
  });
});

// Unit fixtures exercise the public wire contract. They do not attest service
// persistence, dependency digests, predecessor freshness or native execution.
const boundaryIds = {
  project: "10000000-0000-4000-8000-000000000001",
  generation: "10000000-0000-4000-8000-000000000002",
  branch: "10000000-0000-4000-8000-000000000003",
  scene: "10000000-0000-4000-8000-000000000004",
  profile: "10000000-0000-4000-8000-000000000005",
  boundary: "10000000-0000-4000-8000-000000000006",
  predecessor: "10000000-0000-4000-8000-000000000007",
  priorScene: "10000000-0000-4000-8000-000000000008",
  node: "10000000-0000-4000-8000-000000000009",
  other: "10000000-0000-4000-8000-000000000010",
};
function boundaryOptions(): SceneBoundaryFilmOptions {
  return { frame_rate: { num: 30_000, den: 1_001 }, font_family: "Instrument Sans Variable",
    mono_font_family: "IBM Plex Mono", role: "mechanism", archetype: "diagram_build" };
}
function boundarySource(): SceneBoundarySource {
  return { project_id: boundaryIds.project, generation: boundaryIds.generation, branch_id: boundaryIds.branch,
    authored_revision: 7, deliverable_profile_id: boundaryIds.profile,
    film_options: boundaryOptions(), dependencies_sha256: "a".repeat(64) };
}
function boundaryEndpoint(): SceneBoundaryEndpoint {
  return { nodes: [], camera: { center_x: 960, center_y: 540, zoom: 1, rotation_deg: 0, safe_margin: 0.05 }, assets: [] };
}
function explicitBoundary(): SceneBoundary {
  return { version: 1, id: boundaryIds.boundary, scene_id: boundaryIds.scene, input: { kind: "self_contained" },
    definition: { kind: "explicit", source: boundarySource(), entry: boundaryEndpoint(), exit: boundaryEndpoint() } };
}
function boundaryRequest(predecessor_boundary_id: string | null = null): SceneBoundaryContextRequest {
  return { project_id: boundaryIds.project, expected: { resource: `project:${boundaryIds.project}`,
    generation: boundaryIds.generation, revision: 7 }, branch_id: boundaryIds.branch,
    scene_id: boundaryIds.scene, deliverable_profile_id: boundaryIds.profile,
    authored_film_options: boundaryOptions(), predecessor_boundary_id };
}
function boundaryContext(predecessor: SceneBoundaryAuthoringContext["predecessor"] = null): SceneBoundaryAuthoringContext {
  return { schema: "motionwright.scene-boundary-authoring-context/1", scene_id: boundaryIds.scene,
    source: boundarySource(), predecessor };
}

describe("SceneBoundary authored wire decoding", () => {
  it("retains incomplete declarations and applies only published optional-field defaults", () => {
    const raw = { version: 1, id: boundaryIds.boundary, scene_id: boundaryIds.scene,
      input: { kind: "predecessor" }, definition: { kind: "draft_incomplete" } };
    expect(parseSceneBoundary(raw)).toEqual({ ...raw,
      input: { kind: "predecessor", scene_id: null, boundary_id: null, exit_sha256: null, appearances: [] },
      definition: { kind: "draft_incomplete", source: null, entry: null, exit: null } });
    expect(parseSceneBoundaryEndpoint({})).toEqual({ nodes: [], camera: null, assets: [] });
    expect(raw).toEqual({ version: 1, id: boundaryIds.boundary, scene_id: boundaryIds.scene,
      input: { kind: "predecessor" }, definition: { kind: "draft_incomplete" } });
    expect(() => parseSceneBoundaryEndpoint({ nodes: null })).toThrow("endpoint.nodes");
    expect(() => parseSceneBoundaryEndpoint({ nodes: new Array(1) })).toThrow("endpoint.nodes[0]");
    expect(() => parseSceneBoundaryEndpoint({ camera: undefined })).toThrow("endpoint.camera");
  });

  it("round-trips a complete declaration without sharing mutable source or endpoint objects", () => {
    const original = explicitBoundary();
    const parsed = parseSceneBoundaryJson(JSON.stringify(original));
    expect(parsed).toEqual(original);
    expect(parsed.definition).not.toBe(original.definition);
    if (parsed.definition.kind !== "explicit" || original.definition.kind !== "explicit") throw new Error("fixture");
    parsed.definition.source.film_options.font_family = "An independently authored font";
    parsed.definition.entry.camera!.zoom = 2;
    expect(original.definition.source.film_options.font_family).toBe("Instrument Sans Variable");
    expect(original.definition.entry.camera!.zoom).toBe(1);
    expect(() => parseSceneBoundaryJson('{"version":1e}')).toThrow("invalid JSON");
  });

  it("does not promote missing source, camera or predecessor selectors into an Explicit declaration", () => {
    const boundary = explicitBoundary();
    expect(() => parseSceneBoundary({ ...boundary, definition: { kind: "explicit", entry: {}, exit: {} } })).toThrow("missing source");
    expect(() => parseSceneBoundary({ ...boundary, definition: { kind: "explicit", source: boundarySource(), entry: {}, exit: boundaryEndpoint() } })).toThrow("require a camera");
    expect(() => parseSceneBoundary({ ...boundary, input: { kind: "predecessor" } })).toThrow("Explicit predecessor selectors");
    const predecessor = { kind: "predecessor", scene_id: boundaryIds.priorScene, boundary_id: boundaryIds.predecessor,
      exit_sha256: "b".repeat(64), appearances: [] };
    expect(parseSceneBoundary({ ...boundary, input: predecessor }).input).toEqual(predecessor);
  });

  it("rejects undeclared authority and unknown fields at every declaration boundary", () => {
    const valid = explicitBoundary();
    const invalid = [
      { ...valid, grant: "forged" },
      { ...valid, input: { kind: "self_contained", boundary_id: boundaryIds.predecessor } },
      { ...valid, definition: { ...valid.definition, native_ready: true } },
      { ...valid, definition: { kind: "explicit", source: { ...boundarySource(), command: {} }, entry: boundaryEndpoint(), exit: boundaryEndpoint() } },
      { ...valid, definition: { kind: "explicit", source: boundarySource(), entry: { ...boundaryEndpoint(), resolved: true }, exit: boundaryEndpoint() } },
    ];
    for (const candidate of invalid) expect(() => parseSceneBoundary(candidate)).toThrow("unknown field");
    for (const candidate of [null, [], "explicit", { ...valid, version: 2 }]) expect(() => parseSceneBoundary(candidate)).toThrow("Scene boundary");
  });

  it("accepts only authored Rate numbers and closed Film options without inventing defaults", () => {
    expect(parseSceneBoundaryFilmOptions(boundaryOptions())).toEqual(boundaryOptions());
    for (const frame_rate of [{ num: "30", den: "1" }, { num: 30, den: 0 }, { num: 60, den: 2 },
      { num: 4_294_967_296, den: 1 }, { num: 29.97, den: 1 }, { num: 30, den: 1, fps: 30 }]) {
      expect(() => parseSceneBoundaryFilmOptions({ ...boundaryOptions(), frame_rate })).toThrow("frame_rate");
    }
    const { mono_font_family: omitted, ...missing } = boundaryOptions();
    expect(omitted).toBe("IBM Plex Mono");
    expect(() => parseSceneBoundaryFilmOptions(missing)).toThrow("missing mono_font_family");
    for (const override of [{ font_family: " " }, { mono_font_family: "Mono\u0085" },
      { role: "automatic" }, { archetype: "auto" }]) {
      expect(() => parseSceneBoundaryFilmOptions({ ...boundaryOptions(), ...override })).toThrow("film options");
    }
  });

  it("validates nested endpoint values and preserves canonical Canvas defaults", () => {
    const rawNode = { id: boundaryIds.node, name: "An authored mark", kind: "rectangle",
      x: 10, y: 20, width: 100, height: 80, rotation_deg: 0, opacity: 1 };
    const endpoint = parseSceneBoundaryEndpoint({ nodes: [rawNode] });
    expect(endpoint.nodes[0]).toEqual({ ...rawNode, parent_id: null, text: null,
      coordinate_space: "project_pixels", z_index: 0, style: { fill: null, stroke: null, stroke_width: 0,
        font_family: null, font_size: null, font_weight: null, line_height: null, blend_mode: "normal" },
      relations: [], property_locks: [], keyframes: [] });
    for (const override of [{ x: NaN }, { opacity: 2 }, { width: -1 }, { style: { stroke_width: 0, blend_mode: "normal", native_ready: true } },
      { keyframes: [{ at: { num: "1", den: "2" }, property: "opacity", value: 0.5, interpolation: "linear", grant: "forged" }] }]) {
      expect(() => parseSceneBoundaryEndpoint({ nodes: [{ ...rawNode, ...override }] })).toThrow("endpoint.nodes[0]");
    }
    expect(() => parseSceneBoundaryEndpoint({ nodes: [rawNode, rawNode] })).toThrow("duplicate node identity");
    expect(() => parseSceneBoundaryEndpoint({ camera: { ...boundaryEndpoint().camera, zoom: 0 } })).toThrow("zoom");
    expect(() => parseSceneBoundaryEndpoint({ assets: [{ asset_id: boundaryIds.other, content_sha256: "A".repeat(64) }] })).toThrow("lowercase");
  });

  it("rejects ambiguous appearance mappings and unsafe revision values without calculating freshness", () => {
    const boundary = explicitBoundary();
    const appearance = { predecessor_node_id: boundaryIds.other, successor_node_id: boundaryIds.node };
    expect(() => parseSceneBoundary({ ...boundary, input: { kind: "predecessor", appearances: [appearance, appearance] } })).toThrow("only once");
    const invalidSource = { ...boundarySource(), authored_revision: Number.MAX_SAFE_INTEGER + 1 };
    expect(() => parseSceneBoundary({ ...boundary, definition: { kind: "explicit", source: invalidSource,
      entry: boundaryEndpoint(), exit: boundaryEndpoint() } })).toThrow("exact integer");
    // A previously bound source may persist after later project revisions.
    // Parsing never certifies current dependency or execution admission.
    const persisted = { ...boundary, definition: { kind: "draft_incomplete", source: { ...boundarySource(), authored_revision: 2 } } };
    expect(parseSceneBoundary(persisted).definition).toMatchObject({ kind: "draft_incomplete", source: { authored_revision: 2 } });
  });
});

describe("SceneBoundary authoring context identity decoding", () => {
  it("requires an explicit nullable predecessor selector and the exact project stamp shape", () => {
    const request = boundaryRequest();
    expect(parseSceneBoundaryContextRequest(request)).toEqual(request);
    const { predecessor_boundary_id: omitted, ...missing } = request;
    expect(omitted).toBeNull();
    expect(() => parseSceneBoundaryContextRequest(missing)).toThrow("missing predecessor_boundary_id");
    expect(() => parseSceneBoundaryContextRequest({ ...request, expected: { ...request.expected, resource: `project:${boundaryIds.other}` } })).toThrow("does not identify");
    expect(() => parseSceneBoundaryContextRequest({ ...request, expected: { ...request.expected, grant: "forged" } })).toThrow("unknown field");
    expect(() => parseSceneBoundaryContextRequest(Object.values(request))).toThrow("expected an object");
  });

  it("retains the service's exact opaque digests in a detached response", () => {
    const response = boundaryContext({ scene_id: boundaryIds.priorScene, boundary_id: boundaryIds.predecessor, exit_sha256: "b".repeat(64) });
    const parsed = parseSceneBoundaryAuthoringContext(response, boundaryRequest(boundaryIds.predecessor));
    expect(parsed).toEqual(response);
    expect(parsed.source).not.toBe(response.source);
    expect(parsed.source.film_options).not.toBe(response.source.film_options);
    expect(parsed.predecessor).not.toBe(response.predecessor);
    expect(parsed.source.dependencies_sha256).toBe("a".repeat(64));
    expect(parsed.predecessor!.exit_sha256).toBe("b".repeat(64));
  });

  it.each(["project_id", "generation", "branch_id", "deliverable_profile_id"] as const)(
    "rejects a response for another %s", (field) => {
      const response = boundaryContext();
      response.source[field] = boundaryIds.other;
      expect(() => parseSceneBoundaryAuthoringContext(response, boundaryRequest())).toThrow("differs from the requested snapshot");
    });

  it("rejects another scene, revision and any changed authored Film option", () => {
    const request = boundaryRequest();
    expect(() => parseSceneBoundaryAuthoringContext({ ...boundaryContext(), scene_id: boundaryIds.other }, request)).toThrow("requested snapshot");
    expect(() => parseSceneBoundaryAuthoringContext({ ...boundaryContext(), source: { ...boundarySource(), authored_revision: 8 } }, request)).toThrow("requested snapshot");
    for (const override of [{ frame_rate: { num: 24, den: 1 } }, { font_family: "Other family" },
      { mono_font_family: "Other mono" }, { role: "reveal" }, { archetype: "statement" }]) {
      expect(() => parseSceneBoundaryAuthoringContext({ ...boundaryContext(), source: { ...boundarySource(),
        film_options: { ...boundaryOptions(), ...override } } }, request)).toThrow("differs from the authored options");
    }
  });

  it("never substitutes a predecessor or accepts an omitted, wrong-schema or authority-bearing response", () => {
    const predecessor = { scene_id: boundaryIds.priorScene, boundary_id: boundaryIds.predecessor, exit_sha256: "b".repeat(64) };
    expect(() => parseSceneBoundaryAuthoringContext(boundaryContext(predecessor), boundaryRequest())).toThrow("explicitly selected");
    expect(() => parseSceneBoundaryAuthoringContext(boundaryContext(), boundaryRequest(boundaryIds.predecessor))).toThrow("explicitly selected");
    expect(() => parseSceneBoundaryAuthoringContext(boundaryContext({ ...predecessor, boundary_id: boundaryIds.other }), boundaryRequest(boundaryIds.predecessor))).toThrow("explicitly selected");
    const { predecessor: omitted, ...missing } = boundaryContext();
    expect(omitted).toBeNull();
    expect(() => parseSceneBoundaryAuthoringContext(missing, boundaryRequest())).toThrow("missing predecessor");
    expect(() => parseSceneBoundaryAuthoringContext({ ...boundaryContext(), schema: "motionwright.scene-boundary-authoring-context/2" }, boundaryRequest())).toThrow("unsupported authoring context");
    expect(() => parseSceneBoundaryAuthoringContext({ ...boundaryContext(), grant: "forged" }, boundaryRequest())).toThrow("unknown field");
    expect(() => parseSceneBoundaryAuthoringContext({ ...boundaryContext(predecessor),
      predecessor: { ...predecessor, native_ready: true } }, boundaryRequest(boundaryIds.predecessor))).toThrow("unknown field");
  });

  it("requires the real desktop service for context capture and mutation while preserving legacy projects", async () => {
    const project = source(), before = structuredClone(project), storageBefore = await bootstrap();
    expect(project.production_design?.scene_boundaries).toBeUndefined();
    await expect(sceneBoundaryAuthoringContext(boundaryRequest())).rejects.toThrow("actual StudioService");
    await expect(applyChange(project, { type: "upsert_scene_boundary", boundary: explicitBoundary() })).rejects.toThrow("actual StudioService");
    await expect(applyChange(project, { type: "remove_scene_boundary", boundary_id: boundaryIds.boundary })).rejects.toThrow("actual StudioService");
    expect(project).toEqual(before);
    expect(await bootstrap()).toEqual(storageBefore);
  });
});
