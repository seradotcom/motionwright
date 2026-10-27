import { readFileSync, statSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { heroNodeId, mergeComponentNodes, planContentDigest, realizeProductHero } from "../src/creativeProduction";
import { brandDigest } from "../src/brandGovernance";
import type { BrandProfile } from "../src/brandGovernance";
import type { HeroConfig, ProductionPlan } from "../src/creativeProduction";
import type { CanvasNode } from "../src/types";

const path=process.env.MOTIONWRIGHT_RUST_CREATIVE_FIXTURE;
if(!path) throw new Error("This conformance suite requires the current Rust example output, not a checked-in or fabricated oracle.");
if(statSync(path).size>1_048_576) throw new Error("Rust creative fixture exceeds its fixed budget.");
const fixture=JSON.parse(readFileSync(path,"utf8")) as {
  schema:string;
  hero_cases:Array<{id:string;config:HeroConfig;width:number;height:number;nodes:CanvasNode[]}>;
  three_way_merge:{base:CanvasNode[];current:CanvasNode[];incoming:CanvasNode[];expected:CanvasNode[]};
  craft_three_way_merge_cases:Array<{
    id:string;width:number;height:number;base_config:HeroConfig;incoming_config:HeroConfig;
    base:CanvasNode[];current:CanvasNode[];incoming:CanvasNode[];expected:CanvasNode[];
    serialized_roundtrip:CanvasNode[];conflict:CanvasNode[];conflict_error:string;
  }>;
  plan_digest_cases:Array<{plan:ProductionPlan;content_sha256:string}>;
  brand_profile_cases:Array<{profile:BrandProfile;content_sha256:string}>;
};
if(fixture.schema!=="motionwright.rust-studio-creative-parity/1" || fixture.hero_cases.length!==24 || !Array.isArray(fixture.craft_three_way_merge_cases) || fixture.craft_three_way_merge_cases.length!==2 || fixture.plan_digest_cases.length!==3 || fixture.brand_profile_cases.length!==2) throw new Error("Unexpected Rust fixture schema or coverage.");

describe("current Rust implementation and Studio are the same editorial contract",()=>{
  it("keeps all original configs and covers both craft layouts in every aspect",()=>{
    const layouts=["product_hero_reveal","split_explanation","metric_evidence","step_sequence","contrast_comparison","editorial_quote"] as const;
    for(const layout of layouts) for(const [width,height] of [[1920,1080],[1080,1920],[1080,1080]]) {
      const cases=fixture.hero_cases.filter(row=>(row.config.layout??"product_hero_reveal")===layout && row.width===width && row.height===height);
      const expected=layout==="product_hero_reveal"?3:1;
      expect(cases).toHaveLength(expected);
      expect(new Set(cases.map(row=>JSON.stringify(row.config))).size).toBe(expected);
      expect(cases.map(row=>row.config.motion).sort()).toEqual(layout==="product_hero_reveal"?[false,true,true]:[true]);
    }
    expect(fixture.craft_three_way_merge_cases.map(row=>[row.base_config.layout,row.incoming_config.layout])).toEqual([
      ["contrast_comparison","editorial_quote"],["editorial_quote","contrast_comparison"],
    ]);
  });
  it.each(fixture.hero_cases)("matches every generated property at $width x $height",async(row)=>{
    const nodes=await realizeProductHero(row.id,row.config,row.width,row.height);
    expect(nodes).toEqual(row.nodes);
    for(const node of row.nodes) expect(await heroNodeId(row.id,node.name.split(" / ")[1])).toBe(node.id);
  });
  it("matches the backend three-way result while preserving human overrides",()=>{
    const row=fixture.three_way_merge;
    expect(mergeComponentNodes(row.base,row.current,row.incoming)).toEqual(row.expected);
    const conflict=structuredClone(row.incoming),body=conflict.find(n=>n.name.endsWith("body"))!;
    body.text="A conflicting generated replacement.";
    expect(()=>mergeComponentNodes(row.base,row.current,conflict)).toThrow("override conflict");
  });
  it.each(fixture.craft_three_way_merge_cases)("preserves human craft edits through $base_config.layout to $incoming_config.layout",async(row)=>{
    const before=structuredClone(row);
    expect(await realizeProductHero(row.id,row.base_config,row.width,row.height)).toEqual(row.base);
    expect(await realizeProductHero(row.id,row.incoming_config,row.width,row.height)).toEqual(row.incoming);
    const result=mergeComponentNodes(row.base,row.current,row.incoming);
    expect(result).toEqual(row.expected);
    const eyebrow=await heroNodeId(row.id,"eyebrow"),body=await heroNodeId(row.id,"body"),headline=await heroNodeId(row.id,"headline");
    const humanText=row.current.find(node=>node.id===eyebrow)!.text;
    const humanFill=row.current.find(node=>node.id===body)!.style.fill;
    expect(humanText).not.toEqual(row.base.find(node=>node.id===eyebrow)!.text);
    expect(humanFill).not.toEqual(row.base.find(node=>node.id===body)!.style.fill);
    expect(result.find(node=>node.id===eyebrow)!.text).toEqual(humanText);
    expect(result.find(node=>node.id===body)!.style.fill).toEqual(humanFill);
    expect(row.incoming.find(node=>node.id===headline)!.text).not.toEqual(row.current.find(node=>node.id===headline)!.text);
    expect(result.find(node=>node.id===headline)!.text).toEqual(row.incoming.find(node=>node.id===headline)!.text);
    expect(result.map(node=>node.id)).toEqual(row.base.map(node=>node.id));
    expect(row.serialized_roundtrip).toEqual(row.expected);
    expect(JSON.parse(JSON.stringify(result))).toEqual(row.serialized_roundtrip);
    expect(row.conflict_error).toContain("override conflict");
    expect(()=>mergeComponentNodes(row.base,row.current,row.conflict)).toThrow("override conflict");
    expect(row).toEqual(before);
  });
  it.each(fixture.brand_profile_cases)("matches Rust mandatory BrandProfile digest at version $profile.version",async(row)=>{
    expect(await brandDigest(row.profile)).toBe(row.content_sha256);
    // Reordered transport objects must not change the approval authority.
    const reordered=JSON.parse(JSON.stringify(row.profile,(_key,value)=>value && typeof value==="object" && !Array.isArray(value)?Object.fromEntries(Object.entries(value).reverse()):value)) as BrandProfile;
    expect(await brandDigest(reordered)).toBe(row.content_sha256);
    const reorderedRules={...row.profile,rules:[...row.profile.rules].reverse()};
    expect(await brandDigest(reorderedRules)).not.toBe(row.content_sha256);
  });
  it.each(fixture.plan_digest_cases)("matches the backend content digest for $plan.clock.kind",async(row)=>{
    expect(await planContentDigest(row.plan)).toBe(row.content_sha256);
    // JSON object insertion order is not an authority or hashing contract.
    const reordered=JSON.parse(JSON.stringify(row.plan,(_key,value)=>value && !Array.isArray(value) && typeof value==="object"?Object.fromEntries(Object.entries(value).reverse()):value)) as ProductionPlan;
    expect(await planContentDigest(reordered)).toBe(row.content_sha256);
  });
});
