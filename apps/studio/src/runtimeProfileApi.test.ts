/** Synthetic IPC only. No OS picker, package install, provider or physical acceptance runs here. */
import { afterEach,beforeEach,describe,expect,it,vi } from "vitest";
import { fixtureProject } from "./fixture";
import { chooseRuntimeProfile,discardUnsentRuntimePreparation,installRuntimeProfile,parseRuntimeInstallation,parseRuntimePlan,
  readOriginalRuntimeProfile,readRuntimeRetention,rollbackRuntimeProfile,runtimeProfileInventory,RUNTIME_RETENTION_PREFIX,
  type RuntimeInstallation,type RuntimePlan } from "./runtimeProfileApi";
const {invokeMock}=vi.hoisted(()=>({invokeMock:vi.fn()}));
vi.mock("@tauri-apps/api/core",()=>({invoke:invokeMock}));
const id="aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",token="bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb";
const project=()=>structuredClone(fixtureProject);
function plan(p=project()):RuntimePlan{return{
  schema:"motionwright.runtime-profile-plan/1",scope:{project_id:p.id,generation:p.generation,revision:p.revision},plan_id:id,profile:"motion_canvas",label:"Motion Canvas",version:"3.17.2",platform:"linux-x86_64",
  manifest_sha256:"a".repeat(64),total_bytes:2048,required_free_bytes:20*1024**2,available_bytes:1024**3,file_count:7,destination:"/fixture/runtime-profiles-v1/"+id,
  node_path:"/fixture/runtime-profiles-v1/"+id+"/tools/node",entry_path:"/fixture/runtime-profiles-v1/"+id+"/runtime/render.mjs",lock_path:"/fixture/runtime-profiles-v1/"+id+"/runtime/package-lock.json",lock_sha256:"b".repeat(64),
  declared_node_version:"22.22.0",effect_subject:"runtime-profile-install|"+id+"|"+"c".repeat(64),expires_in_seconds:900,execution_performed:false,
};}
function installed(p=project(),state:RuntimeInstallation["state"]="installed"):RuntimeInstallation{
  const original=plan(p);return{schema:"motionwright.runtime-profile-installation/1",scope:original.scope,install_id:id,profile:original.profile,label:original.label,version:original.version,platform:original.platform,
    manifest_sha256:original.manifest_sha256,total_bytes:original.total_bytes,file_count:original.file_count,destination:original.destination,disabled_destination:"/fixture/runtime-profiles-v1/disabled-"+id,
    node_path:original.node_path,entry_path:original.entry_path,lock_path:original.lock_path,lock_sha256:original.lock_sha256,state,content_verified:state==="installed",rollback_available:state!=="rolled_back",
    rollback_subject:"runtime-profile-rollback|"+id+"|"+"c".repeat(64),execution_performed:false,automatic_retry:false};
}
beforeEach(()=>{invokeMock.mockReset();vi.stubGlobal("window",{__TAURI_INTERNALS__:{}});const values=new Map<string,string>();
  vi.stubGlobal("localStorage",{getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>{values.set(key,value);}});
});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});
describe("original local runtime operations",()=>{
  it("retains before one exact installation grant and dispatch",async()=>{
    const p=project(),selected=plan(p),guard=vi.fn();
    invokeMock.mockImplementation(async(command:string)=>{
      expect(readRuntimeRetention(p)[0].install_id).toBe(id);
      if(command==="issue_effect_grant"){expect(readRuntimeRetention(p)[0].state).toBe("prepared");return{token};}
      expect(command).toBe("install_runtime_profile");expect(readRuntimeRetention(p)[0].state).toBe("dispatched");return installed(p);
    });
    await expect(installRuntimeProfile(p,selected,guard)).resolves.toEqual(installed(p));
    expect(guard).toHaveBeenCalledTimes(2);
    expect(invokeMock.mock.calls[0]).toEqual(["issue_effect_grant",{request:{effect:"install_runtime",...selected.scope,subject:selected.effect_subject}}]);
    expect(invokeMock.mock.calls[1]).toEqual(["install_runtime_profile",{request:{scope:selected.scope,plan_id:id,manifest_sha256:selected.manifest_sha256,effect_grant:token}}]);
    expect(readRuntimeRetention(p)[0].state).toBe("acknowledged");
    await expect(installRuntimeProfile(p,selected,()=>{})).rejects.toThrow("already sent");expect(invokeMock).toHaveBeenCalledTimes(2);
  });
  it("keeps a lost reply across reopen and only reads the retained original",async()=>{
    const p=project(),selected=plan(p);invokeMock.mockResolvedValueOnce({token}).mockRejectedValueOnce(new Error("original acknowledgement lost"));
    await expect(installRuntimeProfile(p,selected,()=>{})).rejects.toThrow("lost");
    const original=readRuntimeRetention(p)[0];expect(original.state).toBe("dispatched");
    await expect(installRuntimeProfile(p,{...selected,plan_id:token},()=>{})).rejects.toThrow();
    invokeMock.mockResolvedValueOnce(installed(p));
    await readOriginalRuntimeProfile(p,original);
    expect(invokeMock.mock.calls.map(call=>call[0])).toEqual(["issue_effect_grant","install_runtime_profile","read_original_runtime_profile"]);
    expect(readRuntimeRetention(p)[0].state).toBe("acknowledged");
  });
  it("rejects an old permission reply after project A-B-A without dispatch",async()=>{
    const p=project();let epoch=0;let release!:(value:{token:string})=>void;
    invokeMock.mockImplementation(()=>new Promise(resolve=>{release=resolve;}));
    const action=installRuntimeProfile(p,plan(p),()=>{if(epoch!==0)throw new Error("retired view");});
    epoch=2;release({token});await expect(action).rejects.toThrow("retired");
    expect(invokeMock.mock.calls.map(call=>call[0])).toEqual(["issue_effect_grant"]);expect(readRuntimeRetention(p)[0].state).toBe("not_sent");
  });
  it("a concurrent duplicate cannot alter the first retained operation",async()=>{
    const p=project();let release!:(value:{token:string})=>void;
    invokeMock.mockImplementation((command:string)=>command==="issue_effect_grant"?new Promise(resolve=>{release=resolve;}):Promise.resolve(installed(p)));
    const first=installRuntimeProfile(p,plan(p),()=>{});
    await expect(installRuntimeProfile(p,plan(p),()=>{})).rejects.toThrow("unconfirmed");
    expect(readRuntimeRetention(p)[0].state).toBe("prepared");release({token});await first;
    expect(invokeMock.mock.calls.map(call=>call[0])).toEqual(["issue_effect_grant","install_runtime_profile"]);
  });
  it("rolls back exactly once and never acknowledges a lost rollback as installation success",async()=>{
    const p=project(),original=installed(p);
    invokeMock.mockResolvedValueOnce({token}).mockRejectedValueOnce(new Error("rollback reply lost"));
    await expect(rollbackRuntimeProfile(p,original,()=>{})).rejects.toThrow("lost");
    invokeMock.mockResolvedValueOnce(original);await readOriginalRuntimeProfile(p,original);
    expect(readRuntimeRetention(p)[0].state).toBe("dispatched");
    invokeMock.mockResolvedValueOnce(installed(p,"rolled_back"));await readOriginalRuntimeProfile(p,original);
    expect(readRuntimeRetention(p)[0].state).toBe("acknowledged");
    await expect(rollbackRuntimeProfile(p,original,()=>{})).rejects.toThrow("already sent");
    expect(invokeMock.mock.calls.filter(call=>call[0]==="rollback_runtime_profile")).toHaveLength(1);
    expect(invokeMock.mock.calls[0][1].request.subject).toBe(original.rollback_subject);
  });
  it("corrupt retention and unavailable storage prevent even a permission request",async()=>{
    const p=project();localStorage.setItem(RUNTIME_RETENTION_PREFIX+p.id+":"+p.generation,"{}");
    await expect(installRuntimeProfile(p,plan(p),()=>{})).rejects.toThrow();expect(invokeMock).not.toHaveBeenCalled();
    vi.stubGlobal("localStorage",{getItem:()=>null,setItem:()=>{throw new Error("storage unavailable");}});
    await expect(installRuntimeProfile(p,plan(p),()=>{})).rejects.toThrow("storage unavailable");expect(invokeMock).not.toHaveBeenCalled();
  });
  it("accepts only an exact backend pre-effect refusal as permission for a fresh selection",async()=>{
    const p=project(),selected=plan(p);
    const failure={schema:"motionwright.runtime-profile-failure/1",operation:"install",scope:selected.scope,install_id:id,
      manifest_sha256:selected.manifest_sha256,certainty:"not_started",message:"The original grant expired before admission."};
    invokeMock.mockResolvedValueOnce({token}).mockRejectedValueOnce(failure);
    await expect(installRuntimeProfile(p,selected,()=>{})).rejects.toThrow("expired");
    expect(readRuntimeRetention(p)[0].state).toBe("not_sent");
    invokeMock.mockResolvedValueOnce({token}).mockRejectedValueOnce({...failure,install_id:token});
    await expect(installRuntimeProfile(p,selected,()=>{})).rejects.toEqual({...failure,install_id:token});
    expect(readRuntimeRetention(p)[0].state).toBe("dispatched");
  });
  it("a malformed post-dispatch reply remains unconfirmed",async()=>{
    const p=project();invokeMock.mockResolvedValueOnce({token}).mockResolvedValueOnce({...installed(p),execution_performed:true});
    await expect(installRuntimeProfile(p,plan(p),()=>{})).rejects.toThrow();expect(readRuntimeRetention(p)[0].state).toBe("dispatched");
  });
  it("discards only an unsent preparation, never a dispatched original",async()=>{
    const p=project();let release!:(value:{token:string})=>void;
    invokeMock.mockImplementation(()=>new Promise(resolve=>{release=resolve;}));
    const action=installRuntimeProfile(p,plan(p),()=>{});const prepared=readRuntimeRetention(p)[0];
    discardUnsentRuntimePreparation(p,prepared);release({token});await expect(action).rejects.toThrow("advanced");
    expect(invokeMock).toHaveBeenCalledTimes(1);expect(readRuntimeRetention(p)[0].state).toBe("not_sent");
    expect(()=>discardUnsentRuntimePreparation(p,{...prepared,state:"dispatched"})).toThrow("never dispatched");
  });
  it("cancelled picker and unsupported platform stay effect-free",async()=>{
    const p=project();invokeMock.mockResolvedValueOnce(null);expect(await chooseRuntimeProfile(p)).toBeNull();
    invokeMock.mockResolvedValueOnce({schema:"motionwright.runtime-profile-inventory/1",scope:plan(p).scope,supported:false,platform:"unsupported",profiles:[],alternatives:"Local editing remains available."});
    expect((await runtimeProfileInventory(p)).supported).toBe(false);
    expect(invokeMock.mock.calls.map(call=>call[0])).toEqual(["choose_runtime_profile_bundle","runtime_profile_inventory"]);
    vi.stubGlobal("window",{});await expect(chooseRuntimeProfile(p)).rejects.toThrow("Desktop");expect(invokeMock).toHaveBeenCalledTimes(2);
  });
  it.each(["scope","version","digest","budget","execution","unknown"])("rejects a changed %s before permission",async field=>{
    const p=project(),value:Record<string,unknown>={...plan(p)};
    if(field==="scope")value.scope={...plan(p).scope,generation:token};
    if(field==="version")value.version="latest";
    if(field==="digest")value.manifest_sha256="invalid";
    if(field==="budget")value.total_bytes=3*1024**3;
    if(field==="execution")value.execution_performed=true;
    if(field==="unknown")value.authorize=true;
    expect(()=>parseRuntimePlan(value,p)).toThrow();await expect(installRuntimeProfile(p,value as unknown as RuntimePlan,()=>{})).rejects.toThrow();expect(invokeMock).not.toHaveBeenCalled();
  });
  it("a foreign original or a false verified state supplies no recovery authority",()=>{
    const p=project();expect(()=>parseRuntimeInstallation(installed(p),p,{install_id:token,manifest_sha256:"a".repeat(64)})).toThrow();
    expect(()=>parseRuntimeInstallation({...installed(p,"partial_copy"),content_verified:true},p)).toThrow();
    expect(()=>parseRuntimeInstallation({...installed(p,"rolled_back"),rollback_available:true},p)).toThrow();
  });
});
