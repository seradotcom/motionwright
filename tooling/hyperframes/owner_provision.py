#!/usr/bin/env python3
"""Offline, explicit HyperFrames Owner provisioner for the existing Semwright Broker.

Never installs a package, executes source, edits an existing Broker policy,
starts a renderer or accepts a network URL. Outputs a manifest and a reviewable
owner.toml template, bound to the exact installed runtime and binary digests.
"""
from __future__ import annotations
import argparse,hashlib,json,os,stat
from pathlib import Path
VERSION="0.8.143"
def digest(path:Path)->str:
 with path.open('rb')as input:return hashlib.file_digest(input,'sha256').hexdigest()
def absolute(path:str,kind:str)->Path:
 if not path or not Path(path).is_absolute():raise ValueError(f"{kind}: absolute owner-selected path required")
 original=Path(path);parts=original.parts;walk=Path(parts[0])
 for segment in parts[1:]:
  walk=walk/segment
  mode=walk.lstat().st_mode
  if stat.S_ISLNK(mode):raise ValueError(f"{kind}: symbolic links are not permitted")
 candidate=original.resolve(strict=True)
 mode=candidate.stat().st_mode
 if kind in ("runtime","work","output","assets") and not stat.S_ISDIR(mode):raise ValueError(f"{kind}: a real directory is required")
 if kind in ("driver","runner","node","ffmpeg") and (not stat.S_ISREG(mode) or not bool(mode&0o111)):raise ValueError(f"{kind}: a regular executable is required")
 if kind=="runtime-file" and not stat.S_ISREG(mode):raise ValueError("Runtime dependency must be a regular file")
 return candidate
def value(path:Path)->dict:return json.loads(path.read_text('utf-8'))
def file_descriptor(path:Path)->dict:return {"path":str(path),"sha256":digest(path),"bytes":path.stat().st_size}
def make(args:argparse.Namespace)->dict:
 if not args.approved_license_terms or not args.approved_sandbox_controls or not args.approved_large_browser_tool:
  raise ValueError("Owner must explicitly confirm dependency license review and the restricted sandbox policy")
 paths={name:absolute(getattr(args,name),name)for name in ["runtime","driver","runner","node","ffmpeg","work","output","assets"]}
 runtime=paths["runtime"];rfile=absolute(str(runtime/"runtime.json"),"runtime-file");receipt=value(rfile)
 if receipt.get("schema")!=1 or receipt.get("hyperframes")!=VERSION or receipt.get("capture_profile")!="hyperframes-core-chromium-png-v2":
  raise ValueError("Installed runtime receipt does not match the supported native profile")
 if receipt.get("gsap")!="3.15.0" or receipt.get("playwright")!="1.55.1" or receipt.get("fontkit")!="2.0.4":
  raise ValueError("Native dependency version differs from the pinned supported profile")
 if receipt.get("platform")!="linux" or receipt.get("architecture")!="x64":
  raise ValueError("Only the verified Linux x64 native profile can be provisioned")
 lock=absolute(str(runtime/"package-lock.json"),"runtime-file");declared=receipt.get("npm_lock_sha256")
 if not isinstance(declared,str) or len(declared)!=64 or digest(lock)!=declared:
  raise ValueError("Installed npm lock differs from its owner-reviewed fingerprint")
 inventory_path=absolute(str(runtime/"runtime-files.json"),"runtime-file")
 inventory=receipt.get("inventory")
 if not isinstance(inventory,dict) or digest(inventory_path)!=inventory.get("sha256"):
  raise ValueError("Native runtime source inventory changed")
 files=receipt.get("files",{})
 if set(files)!={"browser","hyperframes_script","gsap_script","sans_font","mono_font"}:
  raise ValueError("Runtime core module and font inventory is not complete")
 for name,item in files.items():
  relative=item.get("path")
  if not isinstance(relative,str) or ".." in Path(relative).parts or Path(relative).is_absolute():raise ValueError(f"{name}: unnormalized runtime path")
  dependency=absolute(str(runtime/relative),"runtime-file")
  if name=="browser" and (dependency.stat().st_size>320*1024*1024 or dependency.stat().st_size<=0):
   raise ValueError("Owner-selected browser exceeds the 320 MiB Linux Host authority")
  if digest(dependency)!=item.get("sha256") or dependency.stat().st_size!=item.get("bytes"):
   raise ValueError(f"{name}: installed runtime dependency changed")
 browser=absolute(str(runtime/files["browser"]["path"]),"runtime-file")
 if browser.stat().st_size>320*1024*1024 or browser.stat().st_size<=0 or not browser.stat().st_mode&0o111:
  raise ValueError("Owner-reviewed Linux Chromium requires a regular executable no larger than 320 MiB")
 tools=[
  {"root":"hyperframes-runner-root","name":"hyperframes-runner","sha256":digest(paths["runner"]),
   "mounts":["hyperframes-runtime","hyperframes-assets","hyperframes-work","hyperframes-output"],
   "dependencies":["node","ffmpeg","chromium"]},
  {"root":"node-root","name":"node","sha256":digest(paths["node"]),"mounts":[],"dependencies":[]},
  {"root":"ffmpeg-root","name":"ffmpeg","sha256":digest(paths["ffmpeg"]),"mounts":[],"dependencies":[]},
  {"root":"chromium-root","name":"chromium","sha256":digest(browser),
   "sealed_executable_profile":"linux_browser320_mib","mounts":[],"dependencies":[]},
 ]
 manifest={
  "manifest_version":1,"protocol":8,"id":"hyperframes","version":"0.1.0","publisher":"motionwright",
  "executable":str(paths["driver"]),"sha256":digest(paths["driver"]),
  "application":{"desktop_id":None,"process_names":["motionwright-hyperframes-runner","node","ffmpeg"],
                 "supported_versions":["HyperFrames Core 0.8.143 (owner installed)"]},
  "transport":"stdio_v1",
  "mounts":[{"root":root,"read_only":read_only}for root,read_only in [
   ("hyperframes-runtime",True),("hyperframes-assets",True),("hyperframes-work",False),("hyperframes-output",False)]],
  "system_config":[],"secrets":[],"tools":tools,"network":False,"loopback_port":None,
  "resources":{"open_files":512,"processes":256,"cpu_seconds":300,"operation_cpu_seconds":0,
               "address_space_bytes":4294967296,"file_size_bytes":1073741824},
  "request_timeout_ms":300000,
  "interfaces":{"dynamic_capabilities":False,"cooperative_cancellation":True,"events":False,
                "health":True,"progress":False,"artifacts":False,"native_refs":False,"host_tools":True},
 }
 grants=[("hyperframes-runtime",paths["runtime"],False),("hyperframes-assets",paths["assets"],False),
         ("hyperframes-work",paths["work"],True),("hyperframes-output",paths["output"],True),
         ("hyperframes-runner-root",paths["runner"],False),("node-root",paths["node"],False),
         ("ffmpeg-root",paths["ffmpeg"],False),("chromium-root",browser,False)]
 owner=["# REVIEW: merge these entries into the real owner-owned Semwright config; not applied automatically.",
        'drivers = ["__OWNER_SET_MANIFEST_PATH__"]','driver_network = false','','[policy]',
        'profile = "workspace"','allow = ["driver:hyperframes"]']
 for name,path,writable in grants:
  owner+=['','[[policy.filesystem]]',f'name = {json.dumps(name)}',f'path = {json.dumps(str(path))}',
          'read = true',f'write = {"true" if writable else "false"}']
 return {"manifest":manifest,"owner":"\n".join(owner)+"\n","runtime_sha256":digest(rfile),
         "native_driver_sha256":digest(paths["driver"]),"native_runner_sha256":digest(paths["runner"])}
def main()->None:
 p=argparse.ArgumentParser(description=__doc__)
 for name in ("runtime","driver","runner","node","ffmpeg","work","output","assets"):
  p.add_argument("--"+name,required=True,help="Absolute verified owner-owned path")
 p.add_argument("--out-dir",required=True,help="Fresh directory for review-only manifest and owner-policy template")
 p.add_argument("--approved-license-terms",action="store_true",help="Owner has independently reviewed all dependency and source rights")
 p.add_argument("--approved-sandbox-controls",action="store_true",help="Owner accepts the bounded Semwright Host policy; no network or arbitrary source")
 p.add_argument("--approved-large-browser-tool",action="store_true",help="Owner explicitly reviewed SHA-256 and bounded 320 MiB Linux-only Chromium tool authority")
 args=p.parse_args()
 try:
  result=make(args)
  dest=Path(args.out_dir)
  if not dest.is_absolute() or dest.exists():raise ValueError("Output must be a fresh absolute directory, never an overwrite")
  dest.mkdir(mode=0o700)
  manifest=dest/"hyperframes-driver.json"
  manifest.write_text(json.dumps(result["manifest"],indent=2)+"\n",encoding="utf-8");manifest.chmod(0o600)
  policy=dest/"semwright-owner-REVIEW.toml"
  policy.write_text(result["owner"].replace("__OWNER_SET_MANIFEST_PATH__",str(manifest)),encoding="utf-8");policy.chmod(0o600)
  receipt=dest/"provision-receipt.json"
  receipt.write_text(json.dumps({k:v for k,v in result.items() if k not in("manifest","owner")},indent=2)+"\n");receipt.chmod(0o600)
  print(json.dumps({"status":"owner_review_required","manifest":str(manifest),"policy_template":str(policy),
                    "runtime_sha256":result["runtime_sha256"],"host_sandbox":"required","network":False}))
 except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError)as error:
  p.exit(2,f"Owner provision refused: {error}\n")
if __name__=="__main__":main()
