import { describe, expect, it } from "vitest";
import sourceLock from "../../../SOURCE_LOCK.json";
import { fixtureBootstrap, fixtureProject } from "./fixture";
import { browserCreativeDoctor } from "./creativeDoctor";
import type { ExtensionProfile, Project } from "./types";

const PIN=sourceLock.dependencies.semwright.revision;
function project():Project {return structuredClone(fixtureProject);}

describe("MW05-E15-02 creative runtime doctor: browser never invents native readiness",()=>{
  it("uses the immutable source pin present in the actual checked-in Native SDK",()=>{
    expect(PIN).toMatch(/^[0-9a-f]{40}$/);
    expect(fixtureBootstrap.native_sdk.pinned_revision).toBe(PIN);
    const report=browserCreativeDoctor(project(),fixtureBootstrap.native_sdk.pinned_revision);
    expect(report.schema).toBe("motionwright.creative-runtime-doctor/1");
    expect(report.semwright_native_sdk_source_pin).toBe(PIN);
    expect(report.runtime_version_evidence).toBe("browser_demo");
  });
  it("reports all native effects and installation actions as not executed",()=>{
    const report=browserCreativeDoctor(project(),PIN);
    expect(report.verdict).toBe("browser_only");
    expect(report.ready_for_native_production).toBe(false);
    expect(report.install_performed).toBe(false);
    expect(report.broker_dispatch_performed).toBe(false);
    expect(report.authorizing_grant_created).toBe(false);
    expect(report.checks).toHaveLength(8);
    expect(report.checks.every(check=>check.status==="not_tested")).toBe(true);
    expect(report.outputs.every(profile=>!profile.render_and_delivery_verified)).toBe(true);
    expect(report.renderers.every(renderer=>!renderer.provider_verified)).toBe(true);
  });
  it("uses actual source project IDs and revision, never a synthetic ready flag",()=>{
    const p=project();
    const r=browserCreativeDoctor(p,PIN);
    expect(r.project_id).toBe(p.id);
    expect(r.generation).toBe(p.generation);
    expect(r.revision).toBe(p.revision);
    expect(r.outputs.map(x=>x.profile_id)).toEqual(p.deliverables.map(x=>x.id));
  });
  it("projects all six supported renderer categories with bounded scene counts",()=>{
    const p=project();
    const r=browserCreativeDoctor(p,PIN);
    expect(r.renderers).toHaveLength(6);
    expect(r.renderers.reduce((count,item)=>count+item.scene_count,0)).toBe(p.scenes.length);
    const blender=r.renderers.find(x=>x.renderer==="blender")!;
    expect(blender.state).toBe("native_provider_not_tested");
    expect(blender.required_semwright_driver).toBe("driver:blender");
    expect(blender.provider_verified).toBe(false);
  });
  it("distinguishes an explicitly admitted extension from an installed provider",()=>{
    const p=project();
    p.scenes[0].renderer="remotion";
    p.extensions.push({
      id:crypto.randomUUID(),kind:"remotion-renderer",
      name:"Owner optional",package_version:"1.0.0",digest_sha256:"a".repeat(64),
      license:"MIT",source:"https://example.invalid/owner",rights_status:"cleared",
      enabled:true,permissions:["read_project"],
    });
    const r=browserCreativeDoctor(p,PIN);
    const optional=r.renderers.find(x=>x.renderer==="remotion")!;
    expect(optional.declared_extension_admitted).toBe(true);
    expect(optional.state).toBe("optional_descriptor_only");
    expect(optional.provider_verified).toBe(false);
  });
  it("keeps a disabled or unreviewed renderer unadmitted",()=>{
    const p=project();
    p.scenes[0].renderer="manim-gl";
    const profile:ExtensionProfile={
      id:crypto.randomUUID(),kind:"manim-gl-renderer",name:"Unknown package",
      package_version:"0.1",digest_sha256:"a".repeat(64),license:"UNKNOWN",
      source:"https://example.invalid",rights_status:"unknown",enabled:false,
      permissions:[],
    };
    p.extensions.push(profile);
    const r=browserCreativeDoctor(p,PIN);
    expect(r.renderers.find(x=>x.renderer==="manim-gl")?.state)
      .toBe("optional_extension_not_admitted");
  });
  it("raw RGBA frame estimates are never presented as installer or cache sizes",()=>{
    const p=project();
    const r=browserCreativeDoctor(p,PIN);
    expect(r.outputs[0].estimated_uncompressed_rgba_frame_bytes)
      .toBe(p.deliverables[0].width*p.deliverables[0].height*4);
    expect(r.checks.find(x=>x.id==="installation_footprint_and_rollback")?.status)
      .toBe("not_tested");
  });
  it("is deterministic and cannot mutate renderer, source or permissions",()=>{
    const p=project();
    const before=structuredClone(p);
    const first=browserCreativeDoctor(p,PIN);
    const second=browserCreativeDoctor(p,PIN);
    expect(first).toEqual(second);
    expect(p).toEqual(before);
  });
  it("serializes a sanitized report without project-private connection files or tickets",()=>{
    const encoded=JSON.stringify(browserCreativeDoctor(project(),PIN));
    for(const forbidden of ["session_file","connection_file","socket","password","access_token","grant_token"]){
      expect(encoded).not.toContain(forbidden);
    }
    expect(encoded).not.toContain("ready_for_native_production\":true");
  });
  it("does not claim real provider readiness for an empty project",()=>{
    const p=project();p.scenes=[];
    const report=browserCreativeDoctor(p,PIN);
    expect(report.renderers.every(x=>x.state==="not_used")).toBe(true);
    expect(report.verdict).toBe("browser_only");
  });
});
