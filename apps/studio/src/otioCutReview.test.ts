import {describe,expect,it} from "vitest";
import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import OtioCutReviewWorkbench from "./OtioCutReviewWorkbench";
import {inspectOtioCutPreview,otioHasAbsoluteAnchors,readOtioCutProposalFile} from "./otioCutReview";
import {reviewableOtioProject,sampleOtioPreview} from "./otioCutReviewTestFixtures";

describe("source-bound OTIO cut review is a non-executable proposal",()=>{
  it("admits only an exact reviewed reordering and end trim, never marks imported",()=>{
    const project=reviewableOtioProject();
    const original=structuredClone(project);
    const data=sampleOtioPreview(project);
    const parsed=inspectOtioCutPreview(project,JSON.stringify(data));
    expect(parsed).toEqual(data);
    expect(parsed.edit_imported).toBe(false);
    expect(parsed.rendering_performed).toBe(false);
    expect(otioHasAbsoluteAnchors(project)).toBe(true);
    expect(project).toEqual(original);
  });
  it("accepts the original bridge JSON member order while preserving the exact edit sequence",()=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    const bridgeJson=(value:unknown)=>JSON.stringify(value,(_key,entry:unknown)=>
      entry && typeof entry==="object" && !Array.isArray(entry)
        ? Object.fromEntries(Object.keys(entry).sort().map(key=>[
          key,(entry as Record<string,unknown>)[key],
        ]))
        : entry);
    expect(bridgeJson(data)).not.toBe(JSON.stringify(data));
    const parsed=inspectOtioCutPreview(project,bridgeJson(data));
    expect(parsed.native_change_proposals).toEqual(data.native_change_proposals);
    expect(parsed).toEqual(data);
    expect(()=>inspectOtioCutPreview(project,bridgeJson({
      ...data,native_change_proposals:[...data.native_change_proposals].reverse(),
    }))).toThrow(/reordered, nonminimal, duplicated or changed/i);
  });
  it.each(["project_id","generation","expected_project_revision","source_project_sha256"])(
    "rejects a stale, unrelated or malformed %s",key=>{
      const project=reviewableOtioProject(),data=sampleOtioPreview(project);
      const forged={...data,[key]:key==="expected_project_revision"?project.revision+1:"different"};
      expect(()=>inspectOtioCutPreview(project,JSON.stringify(forged))).toThrow();
    }
  );
  it.each([
    "edit_imported","source_media_relinked","rendering_performed","publication_approved",
  ])("never trusts a claim that %s is completed",key=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({...data,[key]:true}))).toThrow();
  });
  it("rejects media cardinality mismatch and forged unknown fields",()=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({...data,media_refs_verified:1}))).toThrow();
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({...data,launch_new_provider:true}))).toThrow();
    const stripped={...data};delete (stripped as Partial<typeof data>).property_fidelity;
    expect(()=>inspectOtioCutPreview(project,JSON.stringify(stripped))).toThrow();
  });
  it("rejects a changed fidelity status or missing acknowledged property",()=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    data.property_fidelity[0].status="authentic_media";
    expect(()=>inspectOtioCutPreview(project,JSON.stringify(data))).toThrow();
    const noLoss=sampleOtioPreview(project);noLoss.loss_report=[];
    expect(()=>inspectOtioCutPreview(project,JSON.stringify(noLoss))).toThrow();
  });
  it("cannot add, delete, duplicate or invent original scenes",()=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    const forged=[...data.proposed_scene_order];forged[2]=forged[0];
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({...data,proposed_scene_order:forged}))).toThrow();
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({...data,proposed_scene_order:[...data.proposed_scene_order,"new"]}))).toThrow();
  });
  it("blocks nonminimal, duplicated, reordered or unsafe native edit claims",()=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({
      ...data,native_change_proposals:[...data.native_change_proposals].reverse(),
    }))).toThrow();
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({
      ...data,native_change_proposals:[...data.native_change_proposals,...data.native_change_proposals],
    }))).toThrow();
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({
      ...data,native_change_proposals:[],
    }))).toThrow();
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({
      ...data,native_change_proposals:[{type:"download_source",uri:"http://invalid.example"}],
    }))).toThrow();
  });
  it("cannot convert external NLE clip extension into a Motionwright end trim",()=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    const duration={num:"13",den:"1"};
    data.native_change_proposals[1]={type:"set_scene_duration",scene_id:project.scenes[2].id,duration};
    expect(()=>inspectOtioCutPreview(project,JSON.stringify(data))).toThrow(/cannot extend/i);
  });
  it("honors existing project and scene content locks plus human approval",()=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    project.scenes[0].status="approved";
    expect(()=>inspectOtioCutPreview(project,JSON.stringify(data))).toThrow(/review/i);
    project.scenes[0].status="draft";
    project.locks.push({id:"test-lock",resource:"scene:"+project.scenes[2].id,kind:"timing",note:"source lock"});
    expect(()=>inspectOtioCutPreview(project,JSON.stringify(data))).toThrow(/locked/i);
  });
  it("refuses invalid pasted JSON and over-budget input",()=>{
    const project=reviewableOtioProject();
    expect(()=>inspectOtioCutPreview(project,"not json")).toThrow();
    expect(()=>inspectOtioCutPreview(project," ".repeat(130000))).toThrow();
  });
  it("exposes the source-bound file chooser in the actual owner review screen",()=>{
    const project=reviewableOtioProject();
    const markup=renderToStaticMarkup(createElement(OtioCutReviewWorkbench,{
      project,commit:async()=>{throw new Error("no owner action during display");},
      busy:false,desktopMode:true,
    }));
    expect(markup).toContain('aria-label="OTIO bridge proposal JSON file"');
    expect(markup).toContain('type="file"');
    expect(markup).toContain('accept=".json,application/json"');
    expect(markup).toContain('aria-label="OTIO imported cut preview JSON"');
    expect(markup).toContain('Create the original media-bound OTIO bundle');
    expect(markup).toContain('aria-label="New OTIO media bundle destination"');
    expect(markup).toContain('aria-label="Original OTIO video asset 1"');
    expect(markup).toContain('aria-label="Original OTIO MP4 file path 1"');
    expect(markup).toContain('Export original media-bound OTIO bundle');
    expect(markup).toContain('Preview edited OTIO with verified media');
    expect(markup).not.toContain("Commit reviewed OTIO cut");
  });
  it("reads a real bridge proposal file into the existing review without mutating the project",async()=>{
    const project=reviewableOtioProject();
    const original=structuredClone(project);
    const preview=sampleOtioPreview(project);
    const content=JSON.stringify(preview);
    const file=new Blob([content],{type:"application/json"});
    const selected=await readOtioCutProposalFile(project,{
      name:"original-preview.json",size:file.size,arrayBuffer:()=>file.arrayBuffer(),
    });
    expect(selected.text).toBe(content);
    expect(selected.preview).toEqual(preview);
    expect(selected.preview.edit_imported).toBe(false);
    expect(selected.preview.publication_approved).toBe(false);
    expect(project).toEqual(original);
  });
  it("refuses oversized, wrong-extension or changed proposal files before any review",async()=>{
    const project=reviewableOtioProject();
    const bytes=new Blob([JSON.stringify(sampleOtioPreview(project))]);
    const read=()=>bytes.arrayBuffer();
    await expect(readOtioCutProposalFile(project,{
      name:"edited.otio",size:bytes.size,arrayBuffer:read,
    })).rejects.toThrow(/JSON proposal/);
    await expect(readOtioCutProposalFile(project,{
      name:"preview.json",size:128001,arrayBuffer:read,
    })).rejects.toThrow(/128,000/);
    await expect(readOtioCutProposalFile(project,{
      name:"preview.json",size:bytes.size+1,arrayBuffer:read,
    })).rejects.toThrow(/changed while reading/);
  });
  it("refuses invalid UTF-8 and proposals for an earlier saved revision",async()=>{
    const project=reviewableOtioProject();
    const bad=new Blob([new Uint8Array([0xff,0xfe])]);
    await expect(readOtioCutProposalFile(project,{
      name:"preview.json",size:bad.size,arrayBuffer:()=>bad.arrayBuffer(),
    })).rejects.toThrow(/UTF-8/);
    const stale={...sampleOtioPreview(project),
      expected_project_revision:project.revision+1};
    const staleBlob=new Blob([JSON.stringify(stale)]);
    await expect(readOtioCutProposalFile(project,{
      name:"preview.json",size:staleBlob.size,
      arrayBuffer:()=>staleBlob.arrayBuffer(),
    })).rejects.toThrow(/stale, foreign/);
  });
});
