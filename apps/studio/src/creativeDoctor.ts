/** Browser-demo projection of source metadata, never a native probe.
 * The real Tauri report is produced by the Rust Native SDK backed doctor.
 */
import type { CreativeRuntimeDoctorReport, CreativeDoctorRenderer, Project } from "./types";

const ROUTES: Array<{
  kind: Project["scenes"][number]["renderer"];
  driver: string | null;
  optional: "remotion-renderer" | "manim-gl-renderer" | null;
}> = [
  {kind:"motion-canvas",driver:"driver:motion-canvas",optional:null},
  {kind:"mlt",driver:"driver:mlt-video",optional:null},
  {kind:"blender",driver:"driver:blender",optional:null},
  {kind:"manim-community",driver:"driver:manim-community",optional:null},
  {kind:"remotion",driver:null,optional:"remotion-renderer"},
  {kind:"manim-gl",driver:null,optional:"manim-gl-renderer"},
];

export function browserCreativeDoctor(
  project: Project, sourcePin: string,
): CreativeRuntimeDoctorReport {
  const renderers:CreativeDoctorRenderer[]=ROUTES.map(({kind,driver,optional})=>{
    const scene_count=project.scenes.filter(scene=>scene.renderer===kind).length;
    const admitted=!!optional && project.extensions.some(extension=>
      extension.kind===optional && extension.enabled && extension.rights_status==="cleared");
    return {
      renderer:kind,
      required_semwright_driver:driver,
      scene_count,
      declared_extension_admitted:admitted,
      provider_verified:false,
      state: scene_count===0 ? "not_used" :
        driver ? "native_provider_not_tested" :
        admitted ? "optional_descriptor_only" : "optional_extension_not_admitted",
    };
  });
  return {
    schema:"motionwright.creative-runtime-doctor/1",
    project_id:project.id,generation:project.generation,revision:project.revision,
    semwright_native_sdk_source_pin:sourcePin,
    runtime_version_evidence:"browser_demo",
    runtime_version_detail:"A browser demo cannot inspect an owner CLI, Broker, Driver Host or local files.",
    verdict:"browser_only",
    renderers,
    outputs:project.deliverables.map(profile=>({
      profile_id:profile.id,width:profile.width,height:profile.height,
      estimated_uncompressed_rgba_frame_bytes:profile.width*profile.height*4,
      source_profile_validated:false,render_and_delivery_verified:false,
    })),
    checks:[
      {id:"project_graph",status:"not_tested",
        detail:"This demo shows its authored project metadata but cannot run the Rust project validator."},
      {id:"native_sdk_contracts",status:"not_tested",
        detail:"The Semwright Native SDK is not connected in browser demo mode."},
      {id:"semwright_cli_version",status:"not_tested",
        detail:"No owner CLI is available for a local read-only version probe."},
      {id:"broker_session_and_effect_grants",status:"not_tested",
        detail:"A browser cannot perform a native Broker handshake or mint a policy grant."},
      {id:"driver_host_renderer_discovery",status:"not_tested",
        detail:"Source renderer names are not installed Driver Host evidence."},
      {id:"asset_blob_integrity",status:"not_tested",
        detail:"Asset metadata hashes do not prove the existence of local CAS bytes."},
      {id:"installation_footprint_and_rollback",status:"not_tested",
        detail:"No package size, download destination, installation or rollback was examined."},
      {id:"rendered_media_acceptance",status:"not_tested",
        detail:"Native source readback, export media and human review were not performed."},
    ],
    ready_for_native_production:false,
    install_performed:false,
    broker_dispatch_performed:false,
    authorizing_grant_created:false,
  };
}
