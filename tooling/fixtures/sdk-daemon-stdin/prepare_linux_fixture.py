from __future__ import annotations
import argparse,hashlib,json,os,sys,shutil,stat,tomllib
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument("--driver",type=Path,required=True)
p.add_argument("--tool",type=Path,required=True)
p.add_argument("--sandbox-helper",type=Path,required=True)
p.add_argument("--sdk-source",type=Path,required=True)
p.add_argument("--output",type=Path,required=True)
a=p.parse_args()
assert sys.platform=="linux","Linux fixture preparer required"
parent=a.output.parent.resolve(strict=True);info=parent.stat()
assert info.st_uid==os.getuid()and stat.S_IMODE(info.st_mode)==0o700
sdk=a.sdk_source.resolve(strict=True)
with(sdk/"Cargo.toml").open("rb")as stream:version=tomllib.load(stream)["workspace"]["package"]["version"]
assert isinstance(version,str)and version
out=parent/a.output.name;out.mkdir(mode=0o700)
source_driver,source_tool,helper=(x.resolve(strict=True)for x in(a.driver,a.tool,a.sandbox_helper))
assert all(x.is_file()for x in(source_driver,source_tool,helper))
driver,tool=out/"driver",out/"tool"
for source,destination in((source_driver,driver),(source_tool,tool)):
    with source.open("rb")as src,destination.open("xb")as dst:shutil.copyfileobj(src,dst,length=1048576)
    destination.chmod(0o500)
def sha(path):
    with path.open("rb")as stream:return hashlib.file_digest(stream,"sha256").hexdigest()
manifest={"manifest_version":1,"protocol":7,"id":"fixture","version":version,
    "publisher":"semwright-tests","executable":str(driver),"sha256":sha(driver),
    "application":{"desktop_id":"org.semwright.DriverFixture","process_names":[],"supported_versions":[]},
    "transport":"stdio_v1","mounts":[],"system_config":[],"secrets":[],
    "tools":[{"root":"fixture-tool-root","name":"probe","sha256":sha(tool),"mounts":[],
        "system_config":[],"dependencies":[],"nvidia_gpu":False,"resources":None}],
    "network":False,"nvidia_gpu":False,"loopback_port":None,
    "resources":{"open_files":128,"processes":16,"cpu_seconds":20,"operation_cpu_seconds":0,
        "address_space_bytes":536870912,"file_size_bytes":16777216},
    "request_timeout_ms":5000,"interfaces":{"dynamic_capabilities":True,
        "cooperative_cancellation":True,"events":True,"progress":True,"artifacts":True,
        "health":True,"native_refs":False,"host_tools":True}}
manifest_path=out/"manifest.json"
with manifest_path.open("x",encoding="utf-8")as f:json.dump(manifest,f,sort_keys=True);f.write("\n")
manifest_path.chmod(0o600)
config=out/"config.toml"
text=("drivers = ["+json.dumps(str(manifest_path))+"]\n"
    "driver_network = false\ndriver_nvidia_gpu = false\n"
    "[policy]\nprofile = \"observe\"\n[[policy.filesystem]]\n"
    "name = \"fixture-tool-root\"\npath = "+json.dumps(str(tool))+"\nread = true\nwrite = false\n")
with config.open("x",encoding="utf-8")as f:f.write(text)
config.chmod(0o600)
print(json.dumps({"SEMWRIGHT_TEST_STDIN_CUSTODY_CONFIG":str(config),
    "SEMWRIGHT_TEST_SANDBOX_HELPER":str(helper),"driver_sha256":sha(driver),
    "tool_sha256":sha(tool),"manifest_sha256":sha(manifest_path),"config_sha256":sha(config)}))
