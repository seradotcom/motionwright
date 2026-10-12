import { describe,expect,it } from "vitest";
import { fixtureProject } from "./fixture";
import { inspectVariantImpact } from "./deliveryVariantImpact";
import type { DeliverableProfile,Project } from "./types";

function sample() {
  const project:Project=structuredClone(fixtureProject);
  const source=project.deliverables[0];
  const draft:DeliverableProfile=structuredClone(source);
  return {project,source,draft};
}
function compare(update:(draft:DeliverableProfile,project:Project)=>void,
  kind:"saved"|"parent"="saved") {
  const {project,source,draft}=sample();
  update(draft,project);
  return inspectVariantImpact(project,draft,source,kind);
}
const fields=(change:{changes:Array<{field:string}>})=>
  change.changes.map(item=>item.field);

describe("source-bound v0.5 delivery variant impact accounting",()=>{
  it("has no authored field differences for an unchanged saved profile, never a media PASS",()=>{
    const {project,source,draft}=sample();
    const before=structuredClone(project);
    const report=inspectVariantImpact(project,draft,source,"saved");
    expect(report).toMatchObject({
      schema:"motionwright.delivery-variant-impact/1",
      projectId:project.id,generation:project.generation,revision:project.revision,
      profileId:draft.id,baselineId:source.id,baselineKind:"saved",
      state:"NO_FIELD_DIFFERENCES",count:0,changes:[],impactedStages:[],
    });
    expect(report.sourceNote).toContain("not a cache invalidation oracle");
    expect(project).toEqual(before);
  });
  it("records only master stage for video codec change",()=>{
    const result=compare(d=>{d.video_codec="hevc";});
    expect(fields(result)).toEqual(["video_codec"]);
    expect(result.impactedStages).toEqual(["master"]);
    expect(result.changes[0]).toMatchObject({before:"h264",after:"hevc"});
  });
  it("detects output size changes as potential native picture/master work",()=>{
    const result=compare(d=>{d.width=1080;d.height=1920;});
    expect(fields(result)).toEqual(["frame_size"]);
    expect(result.changes[0]).toMatchObject({before:"1920 × 1080",after:"1080 × 1920"});
    expect(result.impactedStages).toEqual(["picture","master"]);
  });
  it("normalizes mathematically identical rational frame rates",()=>{
    const result=compare(d=>{d.frame_rate={num:"60000",den:"2000"};});
    expect(fields(result)).not.toContain("frame_rate");
  });
  it("detects frame-rate changes across picture, sound, caption and master stages",()=>{
    const result=compare(d=>{d.frame_rate={num:"24000",den:"1001"};});
    expect(fields(result)).toEqual(["frame_rate"]);
    expect(result.impactedStages).toEqual(["picture","sound","captions","master"]);
  });
  it("rejects invalid frame rates rather than approximating them",()=>{
    const {project,source,draft}=sample();
    draft.frame_rate={num:"0",den:"1"};
    expect(()=>inspectVariantImpact(project,draft,source,"saved"))
      .toThrow("Invalid delivery frame rate");
    draft.frame_rate={num:"9".repeat(100),den:"1"};
    expect(()=>inspectVariantImpact(project,draft,source,"saved"))
      .toThrow("Invalid bounded delivery frame rate");
  });
  it("treats normalized implicit all-scene cut and explicit unchanged source order as equal",()=>{
    const {project}=sample();
    const result=compare(d=>{d.included_scene_ids=project.scenes.map(s=>s.id);});
    expect(fields(result)).not.toContain("included_scene_ids");
  });
  it("detects an abbreviated source cut even if the editor kept scene order",()=>{
    const {project}=sample();
    const result=compare(d=>{d.included_scene_ids=project.scenes.slice(0,2).map(s=>s.id);});
    expect(fields(result)).toContain("included_scene_ids");
    expect(result.impactedStages).toEqual(["picture","sound","captions","master"]);
  });
  it("detects replaced selected scenes with unchanged counts, preserving source identities",()=>{
    const {project,source,draft}=sample();
    source.included_scene_ids=[project.scenes[0].id,project.scenes[1].id];
    draft.included_scene_ids=[project.scenes[0].id,project.scenes[2].id];
    const result=inspectVariantImpact(project,draft,source,"saved");
    expect(fields(result)).toContain("included_scene_ids");
    expect(result.changes.find(c=>c.field==="included_scene_ids")?.note).toContain("scene timing");
    expect(result.changes.find(c=>c.field==="included_scene_ids")?.before).not.toContain("source clipboard");
  });
  it("distinguishes protection intent from media mutations",()=>{
    const result=compare((d,p)=>{d.protected_scene_ids=[p.scenes[0].id];});
    expect(fields(result)).toEqual(["protected_scene_ids"]);
    expect(result.impactedStages).toEqual(["editorial"]);
  });
  it("reports actual source text override object instead of leaking private copy",()=>{
    const {project,source,draft}=sample();
    const node=project.scenes[0].nodes.find(n=>n.kind==="text")!;
    const privateCopy="PRIVATE exact customer-specific phrase";
    draft.text_overrides[node.id]=privateCopy;
    const result=inspectVariantImpact(project,draft,source,"saved");
    expect(fields(result)).toContain("text_override:"+node.id);
    expect(result.impactedStages).toEqual(["picture","master"]);
    expect(result.changes.find(c=>c.field==="text_override:"+node.id)?.label)
      .toContain(node.name);
    expect(JSON.stringify(result)).not.toContain(privateCopy);
    expect(draft.text_overrides[node.id]).toBe(privateCopy);
  });
  it("preserves the fact of a copy change even when character lengths are equal",()=>{
    const {project,source,draft}=sample();
    const id=project.scenes[0].nodes.find(n=>n.kind==="text")!.id;
    source.text_overrides[id]="before";
    draft.text_overrides[id]="after!";
    const result=inspectVariantImpact(project,draft,source,"saved");
    expect(fields(result)).toContain("text_override:"+id);
    expect(result.count).toBe(1);
  });
  it("identifies orphan text overrides rather than pretending a known native node",()=>{
    const result=compare(d=>{d.text_overrides["missing-object"]="PRIVATE";});
    expect(result.state).toBe("UNRESOLVED");
    expect(result.issues).toContain(
      "A localized text override refers to an object absent from the current source project.");
    expect(result.changes.find(c=>c.field==="text_override:missing-object")?.label)
      .toBe("Unresolved text object");
  });
  it("notes changed narration source and sidecar consequences",()=>{
    const result=compare(d=>{d.voice_track_id="new-voice";});
    expect(fields(result)).toEqual(["voice_track_id"]);
    expect(result.impactedStages).toEqual(["sound","captions","master"]);
  });
  it("distinguishes a caption format edit from burn-in pixel changes",()=>{
    const format=compare(d=>{d.caption_format="srt";});
    expect(format.impactedStages).toEqual(["captions"]);
    const burn=compare(d=>{d.burn_in_captions=true;});
    expect(burn.impactedStages).toEqual(["picture","captions","master"]);
  });
  it("does not claim a brand/locale variant has already been visually adapted",()=>{
    const result=compare(d=>{d.language="es-MX";d.brand_profile="team";});
    expect(fields(result)).toEqual(["language","brand_profile"]);
    expect(result.impactedStages).toEqual(["picture","sound","captions","master"]);
    expect(result.sourceNote).toContain("not a cache invalidation oracle");
  });
  it("treats metadata-only naming and notes as editorial, not a cache-purge command",()=>{
    const result=compare(d=>{d.name="Local social";d.adaptation_notes=["Pending author review"];});
    expect(fields(result)).toEqual(["name","adaptation_notes"]);
    expect(result.impactedStages).toEqual(["editorial"]);
  });
  it("does not infer a missing parent was source-verified",()=>{
    const {project,draft}=sample();
    draft.id="child";
    draft.parent_profile_id="missing-parent";
    const report=inspectVariantImpact(project,draft,null,"parent");
    expect(report.state).toBe("UNRESOLVED");
    expect(report.baselineId).toBeNull();
    expect(report.issues).toEqual(expect.arrayContaining([
      expect.stringContaining("referenced parent"),
      expect.stringContaining("parent that is not present"),
    ]));
  });
  it("handles a valid derived profile against its recorded parent without mutating it",()=>{
    const {project,source,draft}=sample();
    draft.id="child";
    draft.parent_profile_id=source.id;
    draft.source_revision=project.revision;
    draft.width=1080;draft.height=1920;
    const old=structuredClone(project);
    const result=inspectVariantImpact(project,draft,source,"parent");
    expect(result.baselineKind).toBe("parent");
    expect(result.state).toBe("CHANGES_NEED_REVIEW");
    expect(fields(result)).toEqual(expect.arrayContaining([
      "frame_size","parent_profile_id","source_revision",
    ]));
    expect(project).toEqual(old);
  });
  it("flags cyclic parent lineage using only bounded source project metadata",()=>{
    const {project,source,draft}=sample();
    const next:DeliverableProfile=structuredClone(source);
    next.id="child";
    next.parent_profile_id=source.id;
    source.parent_profile_id=next.id;
    project.deliverables.push(next);
    draft.id=next.id;
    draft.parent_profile_id=source.id;
    const report=inspectVariantImpact(project,draft,source,"parent");
    expect(report.state).toBe("UNRESOLVED");
    expect(report.issues.join(" ")).toContain("cycle");
  });
  it("rejects accidentally comparing a draft to a different saved identity",()=>{
    const {project,source,draft}=sample();
    draft.id="new-draft";
    const result=inspectVariantImpact(project,draft,source,"saved");
    expect(result.state).toBe("UNRESOLVED");
    expect(result.issues.join(" ")).toContain("different profile identity");
  });
  it("marks a historical derivative source revision for rechecking",()=>{
    const {project,source,draft}=sample();
    draft.id="child";
    draft.parent_profile_id=source.id;
    draft.source_revision=0;
    const report=inspectVariantImpact(project,draft,source,"parent");
    expect(report.issues.join(" ")).toContain("older project revision");
    expect(report.state).toBe("UNRESOLVED");
  });
  it("refuses a future revision origin",()=>{
    const {project,source,draft}=sample();
    draft.source_revision=project.revision+1;
    const result=inspectVariantImpact(project,draft,source,"saved");
    expect(result.state).toBe("UNRESOLVED");
    expect(result.issues.join(" ")).toContain("newer than the inspected project");
  });
  it("has an explicit no-baseline report rather than a false empty success",()=>{
    const {project,draft}=sample();
    const report=inspectVariantImpact(project,draft,null,"saved");
    expect(report.state).toBe("NO_BASELINE");
    expect(report.count).toBe(0);
    expect(report.sourceNote).toContain("not a cache invalidation oracle");
  });
  it("refuses oversized profile input without scanning private copy",()=>{
    const {project,source,draft}=sample();
    draft.text_overrides=Object.fromEntries(
      Array.from({length:513},(_,i)=>["synthetic-"+i,"private"])
    );
    expect(()=>inspectVariantImpact(project,draft,source,"saved"))
      .toThrow("bounded comparison limits");
  });
  it("refuses unbounded source profiles or semantic geometry",()=>{
    const {project,source,draft}=sample();
    project.deliverables=Array.from({length:129},(_,i)=>({
      ...structuredClone(source),id:"profile-"+i,
    }));
    expect(()=>inspectVariantImpact(project,draft,source,"saved"))
      .toThrow("Source project exceeds bounded");
  });
});
