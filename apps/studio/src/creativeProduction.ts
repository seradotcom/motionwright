import type { CanvasKeyframe, CanvasNode, CanvasTransform, Change, NodeProperty, NodeStyle, Project, RationalTime } from "./types";
import { rationalSeconds, seconds } from "./types";

export interface HeroConfig {
  eyebrow: string; headline: string; body: string; wordmark: string;
  foreground: string; accent: string; motion: boolean;
}
export interface ProductHeroInstance {
  id: string; scene_id: string; component_version: number;
  config: HeroConfig; baseline: CanvasNode[];
}
export type NarrativeEvidenceKind = "graphic_study" | "real_capture" | "licensed_footage" | "synthetic_labeled";
export type ProductionClock = { kind: "timeline" } | { kind: "voice"; voice_track_id: string } | { kind: "music"; asset_id: string; beats: RationalTime[] };
export interface PlannedShot { scene_id: string; purpose: string; claim_ids: string[]; asset_ids: string[]; evidence_kind: NarrativeEvidenceKind; }
export interface ProductionPlan {
  objective: string; audience: string; concept: string; reference_constraints: string[]; exclusions: string[];
  shots: PlannedShot[]; clock: ProductionClock;
  approval: { reviewer: string; note: string; content_sha256: string } | null;
}
export type Fidelity = "native" | "translated" | "baked" | "approximate" | "unavailable";
export interface FidelityReport {
  renderer: string; renderer_version: string; visual: Fidelity; temporal: Fidelity;
  structural: Fidelity; editable: Fidelity; losses: string[]; evidence_sha256: string | null;
}
export interface NativeCapsule {
  id: string; scene_id: string; source_asset_id: string; source_sha256: string; label: string;
  fidelity: FidelityReport; editable_parameters: string[]; native_editor_hint: string;
}
export interface ProductionDesign { plan: ProductionPlan | null; heroes: ProductHeroInstance[]; capsules: NativeCapsule[]; }
export type ScopedCanvasEdit =
  | { kind: "text"; node_id: string; text: string }
  | { kind: "style"; node_id: string; style: NodeStyle }
  | { kind: "transform"; node_id: string; transform: CanvasTransform }
  | { kind: "keyframe"; node_id: string; keyframe: CanvasKeyframe };
export interface CreativePatch { scene_id: string; base_revision: number; rationale: string; edits: ScopedCanvasEdit[]; }
export type ProductionDesignChange = Extract<Change, { type: "upsert_product_hero" | "detach_product_hero" | "set_production_plan" | "upsert_native_capsule" | "apply_creative_patch" }>;

export const emptyProductionDesign = (): ProductionDesign => ({ plan: null, heroes: [], capsules: [] });
export const defaultHeroConfig = (): HeroConfig => ({
  eyebrow: "MOTIONWRIGHT / CREATIVE PRODUCTION", headline: "Make the work.\nKeep the craft.",
  body: "A composition you can direct, revise and keep editing.", wordmark: "Mw",
  foreground: "#F2F4F3", accent: "#A5C8DF", motion: true,
});
const uuidPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const digestPattern = /^[a-f0-9]{64}$/;
function textBudget(value: string, max: number, label: string) {
  if (!value.trim() || new TextEncoder().encode(value).length > max || /[\u0000-\u0009\u000b-\u001f\u007f]/u.test(value)) throw new Error("Invalid " + label);
}
export function validateHero(config: HeroConfig) {
  for (const [key, max] of [["eyebrow", 48], ["headline", 64], ["body", 150], ["wordmark", 8]] as const) {
    if (!config[key].trim() || Array.from(config[key]).length > max || /[\u0000-\u0009\u000b-\u001f\u007f]/u.test(config[key])) throw new Error("Hero " + key + " exceeds the layout budget or contains control characters.");
  }
  for (const color of [config.foreground, config.accent]) if (!/^#[a-f0-9]{6}$/i.test(color)) throw new Error("Hero colors must use #RRGGBB.");
}
export async function heroNodeId(instance: string, role: string): Promise<string> {
  if (!uuidPattern.test(instance)) throw new Error("Invalid component identity.");
  const namespace = new TextEncoder().encode("motionwright.product-hero.v1\0");
  const bytes = new Uint8Array(instance.replaceAll("-", "").match(/../g)!.map((value) => parseInt(value, 16)));
  const label = new TextEncoder().encode(role);
  const input = new Uint8Array(namespace.length + bytes.length + label.length);
  input.set(namespace); input.set(bytes, namespace.length); input.set(label, namespace.length + bytes.length);
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", input));
  hash[6] = (hash[6] & 15) | 128; hash[8] = (hash[8] & 63) | 128;
  const hex = Array.from(hash.slice(0, 16), (value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
function wrapCopy(text: string, columns: number, maxLines: number): string {
  const lines: string[] = [];
  for (const paragraph of text.split("\n")) {
    let line = "";
    for (const word of paragraph.trim().split(/\s+/u).filter(Boolean)) {
      if (Array.from(word).length > columns) throw new Error("Hero copy contains a word wider than this layout. Insert an intentional break.");
      if (line && Array.from(line).length + 1 + Array.from(word).length > columns) { lines.push(line); line = ""; }
      line += (line ? " " : "") + word;
    }
    lines.push(line);
  }
  if (lines.length > maxLines) throw new Error("Hero copy exceeds this aspect's line budget. Shorten it or author another variant.");
  return lines.join("\n");
}
function entryKeys(node: CanvasNode, start: number, end: number, offset: number, rotation: number): CanvasKeyframe[] {
  const keys: CanvasKeyframe[] = [];
  for (const [property, initial, terminal] of [["y", node.y + offset, node.y], ["opacity", 0, 1], ["rotation_deg", rotation, 0]] as const) {
    if (property === "rotation_deg" && rotation === 0) continue;
    keys.push({ at: rationalSeconds(0), property, value: initial, interpolation: "hold" });
    if (start > 0) keys.push({ at: rationalSeconds(start / 1000), property, value: initial, interpolation: "hold" });
    keys.push({ at: rationalSeconds(end / 1000), property, value: terminal, interpolation: "ease_out_cubic" });
  }
  return keys;
}
/** Editorial realization mirrored against Rust fixtures in CI; not renderer evidence. */
export async function realizeProductHero(id: string, config: HeroConfig, w = 1920, h = 1080): Promise<CanvasNode[]> {
  validateHero(config);
  if (!Number.isInteger(w) || !Number.isInteger(h) || w < 320 || h < 320 || w > 7680 || h > 7680) throw new Error("Hero output dimensions are out of bounds.");
  const portrait = h > w, square = h === w, scale = Math.min(w,h) / 1080, left = w * .09;
  const [headlineY, bodyY, markX, markY, markW, markH] = portrait ? [h*.15,h*.39,w*.16,h*.56,w*.68,h*.26]
    : square ? [h*.16,h*.43,w*.51,h*.60,w*.38,h*.25] : [h*.28,h*.62,w*.64,h*.26,w*.27,h*.40];
  const headline = wrapCopy(config.headline, portrait ? 19 : square ? 22 : 20, 3);
  const body = wrapCopy(config.body, portrait ? 33 : square ? 38 : 40, 4);
  const textWidth = portrait || square ? w*.82 : w*.49;
  const entries: Array<[string, string, number, number, number, number, number, number, number, number, number, number]> = [
    ["eyebrow",config.eyebrow,left,h*.085,w*.80,40*scale,23*scale,500,0,500,14*scale,0],
    ["headline",headline,left,headlineY,textWidth,portrait?h*.22:h*.30,(square?68:82)*scale,650,120,1060,54*scale,0],
    ["body",body,left,bodyY,textWidth,portrait?h*.13:h*.17,30*scale,400,460,1260,24*scale,0],
    ["wordmark",config.wordmark,markX,markY,markW,markH,(portrait?246:square?186:276)*scale,600,160,1460,86*scale,-5],
    ["rule","",left,h*.87,portrait?w*.18:w*.09,3*scale,0,400,600,1360,10*scale,0],
    ["disclosure","GRAPHIC STUDY / NOT A PRODUCT CAPTURE",left,h*.905,w*.82,28*scale,17*scale,400,740,1500,10*scale,0],
  ];
  return Promise.all(entries.map(async ([role,text,x,y,width,height,fontSize,weight,start,end,offset,rotation], index) => {
    const isText = role !== "rule";
    const node: CanvasNode = {
      id: await heroNodeId(id,role), name: "ProductHeroReveal / " + role,
      kind: isText ? "text" : "rectangle", parent_id: null, x,y,width,height,rotation_deg:0,opacity:1,
      text: isText ? text : null, coordinate_space:"project_pixels",z_index:10+index,
      style: { fill: role === "wordmark" || role === "rule" ? config.accent : config.foreground,
        stroke:null,stroke_width:0,font_family:isText?"Instrument Sans Variable":null,
        font_size:isText?fontSize:null,font_weight:isText?weight:null,line_height:isText?1.12:null,blend_mode:"normal" },
      relations:[],property_locks:[],keyframes:[],
    };
    if (config.motion) node.keyframes = entryKeys(node,start,end,offset,rotation);
    return node;
  }));
}
export function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object" || Array.isArray(a) !== Array.isArray(b)) return false;
  const ak = Object.keys(a), bk = Object.keys(b);
  return ak.length === bk.length && ak.every((key) => Object.hasOwn(b,key) && sameValue((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key]));
}
function mergeValue(base: unknown, current: unknown, incoming: unknown, path: string): unknown {
  if (sameValue(incoming,base) || sameValue(incoming,current)) return structuredClone(current);
  if (sameValue(current,base)) return structuredClone(incoming);
  if (base && current && incoming && typeof base === "object" && typeof current === "object" && typeof incoming === "object" && !Array.isArray(base) && !Array.isArray(current) && !Array.isArray(incoming)) {
    const result = { ...current } as Record<string, unknown>;
    for (const [key, value] of Object.entries(incoming)) {
      if (["__proto__","prototype","constructor"].includes(key)) throw new Error("Invalid component property.");
      result[key] = mergeValue((base as Record<string,unknown>)[key], (current as Record<string,unknown>)[key], value, path + "." + key);
    }
    return result;
  }
  throw new Error("Component override conflict at " + path + ". Human values retained; no changes committed.");
}
const fieldsForLock: Record<NodeProperty, Array<keyof CanvasNode>> = {
  position:["x","y"],size:["width","height"],rotation:["rotation_deg"],opacity:["opacity"],text:["text"],style:["style"],parent:["parent_id"],order:["z_index"],
};
function motionLock(property: CanvasKeyframe["property"]): NodeProperty {
  return property === "x" || property === "y" ? "position" : property === "width" || property === "height" ? "size" : property === "rotation_deg" ? "rotation" : "opacity";
}
export function mergeComponentNodes(base: CanvasNode[], current: CanvasNode[], incoming: CanvasNode[]): CanvasNode[] {
  if (base.length !== incoming.length) throw new Error("Component topology needs an explicit migration.");
  const result = structuredClone(current);
  base.forEach((old,index) => {
    const next = incoming[index], i = result.findIndex((node) => node.id === old.id);
    if (old.id !== next.id || i < 0) throw new Error("Component identity changed or a human deleted this node. Detach or restore before updating.");
    const live = result[i]; const candidate = mergeValue(old,live,next,old.name) as CanvasNode;
    for (const property of live.property_locks) {
      if (fieldsForLock[property].some((field) => !sameValue(live[field],candidate[field])) || !sameValue(live.keyframes.filter((k) => motionLock(k.property) === property), candidate.keyframes.filter((k) => motionLock(k.property) === property))) throw new Error("Component property is locked: " + property);
    }
    result[i] = candidate;
  });
  return result;
}
function unlocked(project: Project, scene: string | null, kinds: string[]) {
  const targets = ["project:" + project.id, ...(scene ? ["scene:" + scene] : [])];
  if (project.locks.some((lock) => targets.includes(lock.resource) && kinds.includes(lock.kind))) throw new Error("Creative scope is locked.");
}
export async function planContentDigest(plan: ProductionPlan): Promise<string> {
  // Keep the Rust struct order explicit; arbitrary object insertion order is not a wire contract.
  const value = { objective:plan.objective,audience:plan.audience,concept:plan.concept,reference_constraints:plan.reference_constraints,exclusions:plan.exclusions,
    shots:plan.shots.map((s) => ({scene_id:s.scene_id,purpose:s.purpose,claim_ids:s.claim_ids,asset_ids:s.asset_ids,evidence_kind:s.evidence_kind})),
    clock: plan.clock.kind === "timeline" ? { kind:"timeline" } : plan.clock.kind === "voice" ? { kind:"voice",voice_track_id:plan.clock.voice_track_id } : { kind:"music",asset_id:plan.clock.asset_id,beats:plan.clock.beats }, approval:null };
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify(value)))), b => b.toString(16).padStart(2,"0")).join("");
}
export async function validateProductionPlan(project: Project, plan: ProductionPlan) {
  textBudget(plan.objective,8000,"objective"); textBudget(plan.audience,2000,"audience"); textBudget(plan.concept,12000,"concept");
  for (const list of [plan.reference_constraints,plan.exclusions]) { if (list.length > 64) throw new Error("Too many plan decisions."); list.forEach((value) => textBudget(value,2000,"plan decision")); }
  if (!plan.shots.length || plan.shots.length > 256 || new Set(plan.shots.map(s => s.scene_id)).size !== plan.shots.length) throw new Error("Plan requires unique existing scenes.");
  for (const shot of plan.shots) {
    textBudget(shot.purpose,4000,"shot purpose");
    if (!project.scenes.some(s => s.id === shot.scene_id) || shot.claim_ids.length > 128 || shot.asset_ids.length > 128
      || new Set(shot.claim_ids).size !== shot.claim_ids.length || new Set(shot.asset_ids).size !== shot.asset_ids.length
      || shot.claim_ids.some(id => !project.brief.claims.some(c => c.id === id)) || shot.asset_ids.some(id => !project.assets.some(a => a.id === id))) throw new Error("Shot has duplicate or unknown evidence references.");
    if (["real_capture","licensed_footage"].includes(shot.evidence_kind) && (!shot.asset_ids.length || shot.asset_ids.some(id => !digestPattern.test(project.assets.find(a => a.id === id)?.content_sha256 ?? "")))) throw new Error("Real capture or footage requires digest-bound source assets, not invented UI.");
  }
  if (plan.clock.kind === "voice" && !project.audio.voice_tracks.some(t => t.id === (plan.clock as Extract<ProductionClock,{kind:"voice"}>).voice_track_id)) throw new Error("Voice clock unavailable.");
  if (plan.clock.kind === "music") {
    const clock = plan.clock;
    if (!project.assets.some(a => a.id === clock.asset_id && a.media_type.startsWith("audio/") && a.content_sha256)
      || !clock.beats.length || clock.beats.length > 20000 || clock.beats.some((at,i) => !Number.isFinite(seconds(at)) || seconds(at) < 0 || (i > 0 && seconds(at) <= seconds(clock.beats[i-1])))) throw new Error("Music clock needs a measured asset and ordered beats.");
  }
  if (plan.approval) {
    textBudget(plan.approval.reviewer,200,"reviewer"); textBudget(plan.approval.note,2000,"approval note");
    if (plan.approval.content_sha256 !== await planContentDigest(plan)) throw new Error("Plan approval is stale.");
  }
}
/** Browser editorial mode uses the same Change contract; native mode calls Rust through IPC. */
export async function applyProductionDesignChange(project: Project, change: ProductionDesignChange): Promise<void> {
  const design = structuredClone(project.production_design ?? emptyProductionDesign());
  unlocked(project,null,["content"]);
  switch (change.type) {
    case "upsert_product_hero": {
      unlocked(project,change.scene_id,["content","position","style","timing","renderer"]);
      const scene = project.scenes.find(s => s.id === change.scene_id);
      if (!scene || scene.renderer !== "motion-canvas" || seconds(scene.duration) < 2) throw new Error("ProductHeroReveal needs a Motion Canvas scene of at least two seconds.");
      const baseline = await realizeProductHero(change.instance_id,change.config);
      const existing = design.heroes.find(h => h.id === change.instance_id);
      let nodes: CanvasNode[];
      if (existing) {
        if (existing.scene_id !== scene.id) throw new Error("Component cannot move scenes through an update.");
        nodes = mergeComponentNodes(existing.baseline,scene.nodes,baseline);
      } else {
        if (design.heroes.length >= 64 || design.heroes.some(h => h.scene_id === scene.id) || scene.nodes.some(n => baseline.some(b => n.id === b.id))) throw new Error("Component limit or identity collision.");
        nodes = [...scene.nodes,...baseline];
      }
      const hero: ProductHeroInstance = {id:change.instance_id,scene_id:scene.id,component_version:1,config:structuredClone(change.config),baseline};
      const index = design.heroes.findIndex(h => h.id === hero.id);
      if (index < 0) design.heroes.push(hero); else design.heroes[index] = hero;
      scene.nodes = nodes; scene.status = "draft"; break;
    }
    case "detach_product_hero": {
      const existing = design.heroes.find(h => h.id === change.instance_id);
      if (!existing) throw new Error("Component not found.");
      unlocked(project,existing.scene_id,["content"]);
      design.heroes = design.heroes.filter(h => h.id !== change.instance_id); break;
    }
    case "set_production_plan":
      if (change.plan) await validateProductionPlan(project,change.plan);
      design.plan = structuredClone(change.plan); break;
    case "upsert_native_capsule": {
      const capsule = change.capsule; unlocked(project,capsule.scene_id,["content"]);
      textBudget(capsule.label,256,"capsule label"); textBudget(capsule.native_editor_hint,256,"native editor label");
      const f = capsule.fidelity; textBudget(f.renderer,128,"renderer"); textBudget(f.renderer_version,128,"renderer version");
      if (!uuidPattern.test(capsule.id) || !project.scenes.some(s => s.id === capsule.scene_id) || !digestPattern.test(capsule.source_sha256)
        || !project.assets.some(a => a.id === capsule.source_asset_id && a.content_sha256 === capsule.source_sha256)
        || capsule.editable_parameters.length > 64 || f.losses.length > 128 || (f.evidence_sha256 !== null && !digestPattern.test(f.evidence_sha256))) throw new Error("Native capsule needs an existing digest-bound source asset.");
      capsule.editable_parameters.forEach(v => textBudget(v,2000,"parameter")); f.losses.forEach(v => textBudget(v,2000,"loss"));
      if ([f.visual,f.temporal,f.structural,f.editable].some(v => !["native","translated"].includes(v)) && !f.losses.length) throw new Error("Losses must be explicit.");
      if (!design.capsules.some(c => c.id === capsule.id) && design.capsules.length >= 256) throw new Error("Capsule budget exceeded.");
      const index = design.capsules.findIndex(c => c.id === capsule.id);
      if (index < 0) design.capsules.push(structuredClone(capsule)); else design.capsules[index] = structuredClone(capsule); break;
    }
    case "apply_creative_patch": throw new Error("Use previewCreativePatch and the shared atomic patch handler.");
  }
  project.production_design = design;
}
