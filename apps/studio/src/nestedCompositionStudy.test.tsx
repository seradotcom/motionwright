import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import CompositionStudy from "./CompositionStudy";
import { fixtureProject } from "./fixture";

describe("nested composition study", () => {
  it("retains every clipping plane through an ordinary Group and a rotated leaf", () => {
    const base=structuredClone(fixtureProject.scenes[0].nodes[0]);
    Object.assign(base,{kind:"group",parent_id:null,keyframes:[],relations:[],rotation_deg:0,opacity:1,
      coordinate_space:"project_pixels",x:200,y:100,width:600,height:500,z_index:1});
    base.style.blend_mode="normal";
    const outer={...structuredClone(base),id:crypto.randomUUID(),kind:"clip_group"};
    const middle={...structuredClone(base),id:crypto.randomUUID(),parent_id:outer.id,z_index:2};
    const inner={...structuredClone(base),id:crypto.randomUUID(),parent_id:middle.id,kind:"clip_group",
      x:400,y:50,width:600,height:400,z_index:3};
    const leaf={...structuredClone(base),id:crypto.randomUUID(),kind:"rectangle",parent_id:inner.id,
      x:300,y:50,width:600,height:500,rotation_deg:30,z_index:4};
    const nodes=[outer,middle,inner,leaf], original=structuredClone(nodes);
    const markup=renderToStaticMarkup(<CompositionStudy nodes={nodes} time={0} background="#000000" label="Nested composition"/>);
    expect(markup).toContain('<rect x="400" y="100" width="400" height="350"');
    expect(markup).toContain('clip-path="url(#');
    expect(markup).toContain('data-parent-id="'+inner.id+'"');
    expect(markup).toContain('transform="rotate(30 600 300)"');
    expect(nodes).toEqual(original);
    inner.x=900; inner.y=800;
    const hidden=renderToStaticMarkup(<CompositionStudy nodes={nodes} time={0} background="#000000" label="Disjoint masks"/>);
    expect(hidden).toContain('<rect x="900" y="800" width="0" height="0"');
  });
});

describe("unsupported retained hierarchy stays visible as an error", () => {
  it("never drops group motion, blend or opacity to draw flattened descendants", () => {
    const base=structuredClone(fixtureProject.scenes[0].nodes[0]);
    Object.assign(base,{id:"parent",kind:"group",parent_id:null,keyframes:[],relations:[],rotation_deg:0,opacity:1,z_index:1});
    base.style.blend_mode="normal";
    const leaf={...structuredClone(base),id:"leaf",kind:"rectangle",parent_id:base.id,z_index:2};
    for(const changed of [{...base,rotation_deg:10},{...base,opacity:.5},
      {...base,style:{...base.style,blend_mode:"screen" as const}},
      {...base,keyframes:[{at:{num:"1",den:"1"},property:"x" as const,value:25,interpolation:"linear" as const}]}]) {
      const source=structuredClone([changed,leaf]);
      const html=renderToStaticMarkup(<CompositionStudy nodes={source} time={0} background="#000000" label="Unsupported group"/>);
      expect(html).toContain('role="alert"');expect(html).toContain("No flattened fallback");
      expect(html).not.toContain("<svg");expect(source).toEqual([changed,leaf]);
    }
  });
  it("rejects an unrecognized saved blend without injecting arbitrary CSS", () => {
    const node=structuredClone(fixtureProject.scenes[0].nodes[0]);
    node.style.blend_mode="url(untrusted)" as never;
    const html=renderToStaticMarkup(<CompositionStudy nodes={[node]} time={0} background="#000000" label="Unknown blend"/>);
    expect(html).toContain('role="alert"');expect(html).not.toContain("<svg");expect(html).not.toContain("url(untrusted)");
  });
});
