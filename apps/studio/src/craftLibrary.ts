import type { CanvasNode } from "./types";
import { defaultHeroConfig, realizeProductHero, type HeroConfig } from "./creativeProduction";
import { defaultProductStageConfig, realizeProductStage, type ProductStageConfig } from "./productStage";
import { defaultProceduralConfig, defaultProceduralInfluence, defaultProceduralPath, realizeProceduralField, type ProceduralConfig } from "./proceduralField";

export const CRAFT_FAMILIES=["typography","product","layout","transition","camera","data","procedural","sound"] as const;
export type CraftFamily=typeof CRAFT_FAMILIES[number];
export type CraftRoute="component"|"stage"|"procedural"|"patch"|"boundary"|"camera"|"data"|"canvas";
export type CraftExample=
  | {kind:"hero";config:HeroConfig}
  | {kind:"stage";config:ProductStageConfig}
  | {kind:"procedural";config:ProceduralConfig};
export type CraftCoverage="editable_candidate"|"bounded_partial"|"spec_only";
export interface CraftDefinition {
  id:string;family:CraftFamily;title:string;version:1;status:"DRAFT";
  coverage:CraftCoverage;detail:string;limits:string;route?:CraftRoute;example?:CraftExample;
}
const hero=(layout:NonNullable<HeroConfig["layout"]>,headline:string,body:string,wordmark="MW"):CraftExample=>({
  kind:"hero",config:{...defaultHeroConfig(),layout,eyebrow:"ORIGINAL STUDY",headline,body,wordmark,motion:true}
});
const stage=(frame:ProductStageConfig["frame"],animate_focus=false):CraftExample=>({
  kind:"stage",config:{...defaultProductStageConfig(),frame,animate_focus,
    author_attestation:"Synthetic first-party catalogue preview only. No external assets."}
});
const field=(config:Partial<ProceduralConfig>):CraftExample=>({
  kind:"procedural",config:{...defaultProceduralConfig(),...config}
});
function entry(family:CraftFamily,slug:string,title:string,coverage:CraftCoverage,detail:string,limits:string,
  route?:CraftRoute,example?:CraftExample):CraftDefinition{
  const prefix=family==="typography"?"type":family;
  return {id:"motionwright."+prefix+"."+slug,family,title,version:1,status:"DRAFT",coverage,detail,limits,route,example};
}
const heroLimits="Six original objects, three aspect studies, explicit copy budgets and compatible overrides. Native glyph fit and human craft review remain separate.";
const stageLimits="Generic editable graphics only. Source rights and capture identity must be reviewed in the authoring editor. No external UI is fabricated.";
const fieldLimits="1-64 stable editable nodes, bounded geometry and deterministic seed. Native admission checks the selected motion and style profile.";
const none="No executable definition or representative output has been admitted for this candidate.";
/** Closed, first-party index. A spec row is never a shipped component; a
 * generated example is not a creative or native acceptance decision. */
export const CRAFT_LIBRARY:readonly CraftDefinition[]=[
  entry("typography","hero-focus","Headline focus","editable_candidate","Grouped entrance, one dominant typographic idea.",heroLimits,"component",hero("typography_focus","A clear idea","Keep the source visible.")),
  entry("typography","contrast-pair","Typographic contrast","editable_candidate","Primary and supporting ideas enter with different hierarchy.",heroLimits,"component",hero("typography_contrast","One clear idea","Context makes the difference.")),
  entry("typography","measured-number","Measured number","bounded_partial","Bind exact fixed-point counters and labels to a saved measured table.","Requires an imported source, units, precision and original node. No synthetic measurement preview.","data"),
  entry("typography","caption-emphasis","Caption emphasis","bounded_partial","Saved word emphasis already uses transcript and native caption cues.","Requires measured voice, aligned transcript and supported caption output. Not a standalone generator."),
  entry("typography","context-label","Context label","editable_candidate","Create an editable label tied to an original translating object.","Canvas Follow supports bounded exact translation, not arbitrary parent transforms.","canvas"),
  entry("product","hero-reveal","Product hero reveal","editable_candidate","Editable opening composition for authored product benefits.",heroLimits,"component",hero("product_hero_reveal","Make the work.\nKeep the craft.","A composition you can direct and revise.")),
  entry("product","screen-focus","Screen focus","bounded_partial","An original semantic screen and animated focus marker.",stageLimits,"stage",stage("browser",true)),
  entry("product","flow-bridge","Product flow bridge","spec_only","Connect two genuine product states.",none),
  entry("product","device-stage","Device stage","editable_candidate","Generic handset presentation from original semantic objects.",stageLimits,"stage",stage("generic_handset")),
  entry("product","browser-stage","Browser stage","editable_candidate","Original application-window graphic without third-party chrome.",stageLimits,"stage",stage("browser")),
  entry("layout","responsive-story","Responsive story","editable_candidate","Split narrative composition replans across aspect ratios.",heroLimits,"component",hero("split_explanation","One clear idea","Keep the context.","CONTEXT")),
  entry("layout","evidence-pair","Evidence pair","bounded_partial","MetricEvidence lays out authored claim and supporting context.","Unverified editorial study; does not establish measured evidence or authenticated claims.","component",hero("metric_evidence","42% growth","Illustrative value, source not verified.","DATA")),
  entry("layout","comparison","Conceptual comparison","bounded_partial","Equal-authority authored concepts with explicit evidence disclosure.","Not a measured before/after or proof of improvement.","component",hero("contrast_comparison","Original idea","Alternative idea")),
  entry("layout","reference-board","Reference board","spec_only","Arrange permission-bound references by narrative role.",none),
  entry("transition","shared-object","Shared object","bounded_partial","Review matched original objects at a source-bound scene cut.","Only the admitted boundary appearance/clock profile; not general cross-shot morphing.","boundary"),
  entry("transition","focus-transfer","Focus transfer","editable_candidate","Complementary exact opacity fades on two independent saved objects.","No overwrite of existing motion, locks or managed components; exact shared frame interval.","patch"),
  entry("transition","mask-window","Mask window","bounded_partial","Original rectangular Clip group and editable child layout.","Static rectangular clipping only; animated mask-window transition remains open.","canvas"),
  entry("transition","camera-match","Camera match","bounded_partial","Compare explicit source camera and boundary geometry.","Exact supported cut appearance only; arbitrary camera matching remains open.","boundary"),
  entry("transition","hard-cut-hold","Hard cut and hold","bounded_partial","Authored timing/hold and explicit scene-boundary review.","No automatic orchestration of a held narrative transition.","boundary"),
  entry("camera","arc-reveal","Arc reveal","spec_only","Bounded camera arc toward an informative face.",none),
  entry("camera","dolly-focus","Dolly focus","spec_only","Native camera approach to a meaningful target.",none),
  entry("camera","detail-return","Detail and return","bounded_partial","Approach an original planar detail with a shared position/scale track, hold, then return to the same context.","Motion Canvas v3, static independent original shapes and text, 1.05-1.25x zoom, at most 80 source pixels per axis, exact 30fps Replan safe-area endpoints. No optical dolly, 3D rig or physical acceptance.","camera"),
  entry("camera","group-reframe","Group reframe","bounded_partial","Original planar camera reframe with exact scene clock.","Only supported planar pan; no invented perspective or zoom projection.","camera"),
  entry("data","causal-diagram","Causal diagram","spec_only","Explicit nodes and causal relations with units.",none),
  entry("data","series-reveal","Series reveal","bounded_partial","Reveal every original dated measurement in calendar order with exact visibility tracks and a shared zero-based bar axis.","Two to four rows in one saved table with distinct ISO dates, matching units and precision, common axis, exact frame clocks and independent label/bar pairs. Reveal timing is editorial, not elapsed time. Re-review after source changes; no managed chart instance or physical acceptance.","patch"),
  entry("data","state-comparison","Measured state comparison","bounded_partial","Lay out two original source-bound label/bar pairs on the same zero-based axis.","Requires one saved table, distinct labels, identical units and precision, common axis and supported replan profiles. Re-review after source or layout changes; not a managed chart instance.","patch"),
  entry("data","system-flow","System flow","spec_only","Events tied to an execution or labeled synthetic source.",none),
  entry("procedural","repeater","Repeater","editable_candidate","Stable square/circle identities on a controlled grid.",fieldLimits,"procedural",field({distribution:"grid",count:20,columns:5,shape:"circle",size:42})),
  entry("procedural","influence-field","Influence field","editable_candidate","Bounded static influence modifies original objects.",fieldLimits,"procedural",field({distribution:"grid",count:20,columns:5,influence:{...defaultProceduralInfluence(),scale_percent:170,fill:"#D9A46E"}})),
  entry("procedural","path-distribution","Path distribution","editable_candidate","Equal-distance placement on an authored polyline.",fieldLimits,"procedural",field({distribution:"path",count:17,shape:"circle",path:defaultProceduralPath()})),
  entry("procedural","grid-response","Responsive grid","editable_candidate","Replan columns from count and physical output without shrinking source items.",fieldLimits,"procedural",field({distribution:"adaptive_grid",count:24,shape:"circle",size:38})),
  entry("procedural","seeded-texture","Seeded texture","bounded_partial","Original seeded scatter of individual editable shapes.","No raster texture/shader synthesis or unbounded density.","procedural",field({distribution:"scatter",seed:127,count:48,size:18,shape:"circle"})),
  entry("sound","focus-hit","Focus hit","bounded_partial","Existing semantic sound cue authoring and source-bound PCM mixing.","Needs approved cue, selected voice/output clock and real mix; no sample audio invented."),
  entry("sound","transition-tail","Transition tail","bounded_partial","Original cue and envelope timing against the saved output clock.","No arbitrary transition-linked audio generator or invented native playback."),
  entry("sound","energy-bed","Energy bed","bounded_partial","Original music source, reviewed cut and voice-aware mix.","Requires authorized music bytes and a valid saved mix recipe."),
  entry("sound","silence-release","Silence release","bounded_partial","Fade the three verified original buses into an exact silent tail without extending the cut.","Open Audio, then Original mix. Requires an exact 48-kHz saved cut, original sources and reviewed narration before the fade. PCM silence is not encoded-output or listening acceptance."),
];
const previewId="00000000-0000-4000-8000-000000000031";
export const craftDefinition=(id:string)=>CRAFT_LIBRARY.find(item=>item.id===id);
/** Use the actual first-party typed generators, never thumbnails from another
 * component. Procedural example is intentionally the authored stage study. */
export async function realizeCraftExample(definition:CraftDefinition,width:number,height:number):Promise<{nodes:CanvasNode[];width:number;height:number}>{
  if(!definition.example)throw new Error("This definition requires real source objects or remains a specification.");
  const example=structuredClone(definition.example);
  if(example.kind==="hero")return {nodes:await realizeProductHero(previewId,example.config,width,height),width,height};
  if(example.kind==="stage")return {nodes:await realizeProductStage(previewId,example.config,width,height),width,height};
  return {nodes:await realizeProceduralField(previewId,example.config),width:1920,height:1080};
}
/** Example loading is explicit and never writes a project. A synthetic rights
 * declaration used by the preview must not enter an owner's authoring draft. */
export function craftAuthoringExample(definition:CraftDefinition):CraftExample|null{
  if(!definition.example)return null;
  const example=structuredClone(definition.example);
  if(example.kind==="stage")example.config.author_attestation="";
  return example;
}
