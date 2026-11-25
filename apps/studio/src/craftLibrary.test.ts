import {describe,it,expect} from "vitest";
import {CRAFT_FAMILIES,CRAFT_LIBRARY,craftAuthoringExample,craftDefinition,realizeCraftExample} from "./craftLibrary";
import {rawStage,rawProcedural,proceduralConfig} from "./creativeWorkbenchDraft";

describe("first-party craft definitions",()=>{
  it("retains the exact bounded 36-candidate roster and never invents admission",()=>{
    expect(CRAFT_LIBRARY).toHaveLength(36);
    expect(new Set(CRAFT_LIBRARY.map(item=>item.id)).size).toBe(36);
    expect(new Set(CRAFT_LIBRARY.map(item=>item.family))).toEqual(new Set(CRAFT_FAMILIES));
    expect(CRAFT_LIBRARY.every(item=>item.status==="DRAFT"&&item.version===1&&item.limits.length>0)).toBe(true);
    expect(CRAFT_LIBRARY.filter(item=>item.coverage==="spec_only").every(item=>!item.route&&!item.example)).toBe(true);
  });
  it("realizes each available preview with the actual bounded generator and stable editable IDs",async()=>{
    for(const definition of CRAFT_LIBRARY.filter(item=>item.example)){
      const original=structuredClone(definition);
      const base=await realizeCraftExample(definition,1920,1080);
      expect(base.nodes.length,definition.id).toBeGreaterThan(0);
      expect(new Set(base.nodes.map(node=>node.id)).size).toBe(base.nodes.length);
      expect(base.nodes.every(node=>["text","rectangle","shape","circle"].includes(node.kind))).toBe(true);
      for(const [width,height] of [[1080,1080],[1080,1920]]){
        const sample=await realizeCraftExample(definition,width,height);
        expect(sample.nodes.map(node=>node.id),definition.id).toEqual(base.nodes.map(node=>node.id));
        if(definition.example?.kind==="procedural")expect(sample).toEqual(base);
        else expect([sample.width,sample.height]).toEqual([width,height]);
      }
      expect(definition).toEqual(original);
    }
  });
  it("does not transfer preview source rights and preserves typed procedural inputs",()=>{
    const stage=craftAuthoringExample(craftDefinition("motionwright.product.device-stage")!);
    expect(stage?.kind).toBe("stage");
    if(stage?.kind==="stage")expect(rawStage(stage.config).author_attestation).toBe("");
    for(const definition of CRAFT_LIBRARY.filter(item=>item.example?.kind==="procedural")){
      const example=craftAuthoringExample(definition);
      if(example?.kind==="procedural")expect(proceduralConfig(rawProcedural(example.config))).toEqual(example.config);
    }
  });
  it("refuses a spec-only preview rather than using another component",async()=>{
    await expect(realizeCraftExample(craftDefinition("motionwright.data.series-reveal")!,1920,1080)).rejects.toThrow(/real source/);
  });
});
