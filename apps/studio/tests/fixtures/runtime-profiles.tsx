/** Synthetic IPC fixture: no real picker, filesystem installation, grant or Native provider. */
import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import RuntimeProfilesPanel from "../../src/RuntimeProfilesPanel";
import { fixtureProject } from "../../src/fixture";
import "../../src/styles.css";
import "../../src/production-design.css";
let project=structuredClone(fixtureProject);project.revision=4;
const id="aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",grant={token:"bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"};
const root=createRoot(document.getElementById("runtime-root")!);
const render=()=>flushSync(()=>root.render(<StrictMode><main style={{maxWidth:1000,margin:"24px auto",padding:20}}><RuntimeProfilesPanel project={project} desktop={true}/></main></StrictMode>));
type Request=Record<string,unknown>;
const calls:{command:string;request:Request}[]=JSON.parse(sessionStorage.getItem("runtime-fixture-calls")??"[]");
let loseNext=false,hold=false,release:((value:unknown)=>void)|null=null;
const scope=()=>({project_id:project.id,generation:project.generation,revision:project.revision});
const destination="/fixture/runtime-profiles-v1/"+id;
const platform=new URLSearchParams(location.search).get("platform")==="macos-aarch64"?"macos-aarch64":"linux-x86_64";
const plan=()=>({schema:"motionwright.runtime-profile-plan/1",scope:scope(),plan_id:id,profile:"motion_canvas",label:"Motion Canvas",version:"3.17.2",platform,
  manifest_sha256:"a".repeat(64),total_bytes:2048,required_free_bytes:20*1024**2,available_bytes:1024**3,file_count:7,destination,
  node_path:destination+"/tools/node",entry_path:destination+"/runtime/render.mjs",lock_path:destination+"/runtime/package-lock.json",lock_sha256:"b".repeat(64),
  declared_node_version:"22.22.0",effect_subject:"runtime-profile-install|"+id+"|"+"c".repeat(64),expires_in_seconds:900,execution_performed:false});
function original(verify:boolean){
  const state=sessionStorage.getItem("runtime-fixture-original")==="rolled_back"?"rolled_back":verify?"installed":"copy_completed";
  const base=state==="rolled_back"?"/fixture/runtime-profiles-v1/disabled-"+id:destination;
  return {schema:"motionwright.runtime-profile-installation/1",scope:scope(),install_id:id,profile:"motion_canvas",label:"Motion Canvas",version:"3.17.2",platform,
    manifest_sha256:"a".repeat(64),total_bytes:2048,file_count:7,destination,disabled_destination:"/fixture/runtime-profiles-v1/disabled-"+id,
    node_path:base+"/tools/node",entry_path:base+"/runtime/render.mjs",lock_path:base+"/runtime/package-lock.json",lock_sha256:"b".repeat(64),state,content_verified:state==="installed",rollback_available:state!=="rolled_back",
    rollback_subject:"runtime-profile-rollback|"+id+"|"+"c".repeat(64),execution_performed:false,automatic_retry:false};
}
(window as unknown as Record<string,unknown>).__TAURI_INTERNALS__={async invoke(command:string,args:{request:Request}){
  calls.push({command,request:structuredClone(args.request)});sessionStorage.setItem("runtime-fixture-calls",JSON.stringify(calls));
  if(command==="runtime_profile_inventory")return {schema:"motionwright.runtime-profile-inventory/1",scope:scope(),supported:true,platform,profiles:sessionStorage.getItem("runtime-fixture-original")?[original(false)]:[],alternatives:"Local editing and existing separately authorized connections remain available."};
  if(command==="choose_runtime_profile_bundle")return plan();
  if(command==="issue_effect_grant"){if(hold)return new Promise(resolve=>{release=resolve;});return grant;}
  if(command==="install_runtime_profile"){
    sessionStorage.setItem("runtime-fixture-original","installed");
    if(loseNext){loseNext=false;throw new Error("Synthetic installation acknowledgement lost after completion.");}
    return original(true);
  }
  if(command==="read_original_runtime_profile"){
    if(!sessionStorage.getItem("runtime-fixture-original"))throw new Error("No original runtime fixture exists.");
    return original(true);
  }
  if(command==="rollback_runtime_profile"){sessionStorage.setItem("runtime-fixture-original","rolled_back");return original(false);}
  throw new Error("Unexpected synthetic runtime command: "+command);
}};
(window as unknown as Record<string,unknown>).__runtimeProfileFixture={
  calls:()=>structuredClone(calls),loseNext:()=>{loseNext=true;},holdGrant:()=>{hold=true;},
  releaseGrant:()=>{hold=false;const pending=release;release=null;if(!pending)throw new Error("No pending fixture grant.");pending(grant);},
  revision:(value:number)=>{project={...structuredClone(project),revision:value};render();},
};
render();
