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
  plan_digest_cases:Array<{plan:ProductionPlan;content_sha256:string}>;
  brand_profile_cases:Array<{profile:BrandProfile;content_sha256:string}>;
};
if(fixture.schema!=="motionwright.rust-studio-creative-parity/1" || fixture.hero_cases.length!==15 || fixture.plan_digest_cases.length!==3 || fixture.brand_profile_cases.length!==2) throw new Error("Unexpected Rust fixture schema or coverage.");

describe("current Rust implementation and Studio are the same editorial contract",()=>{
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
