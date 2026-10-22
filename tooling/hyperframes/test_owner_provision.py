#!/usr/bin/env python3
"""G0 profile and custody tests; synthetic executables are never invoked."""
from __future__ import annotations
import argparse,copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import owner_provision as p
SOURCE=Path(__file__).resolve().parents[2]
TEMP_ROOT=SOURCE/'.tmp/hyperframes-contract'
class OwnerProvisionTests(unittest.TestCase):
 def setUp(self):
  TEMP_ROOT.mkdir(parents=True,exist_ok=True)
  self.temp=tempfile.TemporaryDirectory(prefix='hf-owner-g0-',dir=TEMP_ROOT);self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name).resolve();self.runtime=self.root/'runtime';self.runtime.mkdir()
  self.paths={name:self.root/name for name in ('driver','node','project','output')};self.paths['runtime']=self.runtime
  self.files={
   'semwright-hyperframes-runner.mjs':('runner',(SOURCE/'native/hyperframes/semwright-hyperframes-runner.mjs').read_bytes()),
   'node_modules/hyperframes/bin/hyperframes.mjs':('hyperframes-cli',b'synthetic CLI never executed'),
   'node_modules/hyperframes/package.json':('dependency',json.dumps({'name':'hyperframes','version':p.VERSION}).encode()),
   'origin/hyperframes-0.8.181.json':('dependency',(Path(__file__).parent/'fixtures/hyperframes-0.8.181-original.json').read_bytes()),
   'package-lock.json':('package-lock',json.dumps({'packages':{'node_modules/hyperframes':{'version':p.VERSION,'resolved':p.RESOLVED,'integrity':p.INTEGRITY}}}).encode()),
   'tools/browser':('browser',b'synthetic browser never executed'),
   'tools/ffmpeg':('ffmpeg',b'synthetic ffmpeg never executed'),
   'tools/ffprobe':('ffprobe',b'synthetic ffprobe never executed')}
  for name,(_,raw) in self.files.items():self.put(name,raw)
  self.lock={'schema':'semwright.hyperframes.runtime-lock/1','hyperframes_version':p.VERSION,'hyperframes_commit':p.COMMIT,
   'node_version':'v22.22.0','node_sha256':'1'*64,'browser_version':'Chromium synthetic G0','ffmpeg_version':'ffmpeg synthetic G0','ffprobe_version':'ffprobe synthetic G0',
   'files':[{'path':name,'sha256':p.sha(raw),'bytes':len(raw),'role':role} for name,(role,raw) in sorted(self.files.items())]}
  self.write_lock()
 def put(self,name,raw):
  dest=self.runtime/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
 def write_lock(self):self.put('runtime-lock.json',(json.dumps(self.lock,indent=2)+'\n').encode())
 def manifest(self):return p.build_manifest(self.paths,'2'*64,'1'*64)
 def test_manifest_matches_existing_node_host_and_mount_contract(self):
  m=self.manifest();self.assertEqual(m['protocol'],7);self.assertEqual(m['tools'][0]['name'],'hyperframes-node');self.assertEqual(m['tools'][0]['mounts'],['runtime','project','output'])
  self.assertEqual(m['mounts'],[{'root':'runtime','read_only':True,'execute':True},{'root':'project','read_only':False,'execute':False},{'root':'output','read_only':False,'execute':False}])
  self.assertEqual([x for x,v in m['interfaces'].items() if v],['health','host_tools']);self.assertEqual(m['request_timeout_ms'],300000)
 def test_historical_nonzero_operation_cpu_is_rejected(self):
  m=self.manifest();self.assertEqual(m['resources']['cpu_seconds'],300);self.assertEqual(m['resources']['address_space_bytes'],4294967296)
  m['resources']['operation_cpu_seconds']=240
  with self.assertRaisesRegex(ValueError,'operation_cpu_seconds=0'):p.check_host_profile(m)
 def test_historical_unknown_browser_sealed_field_is_rejected(self):
  m=self.manifest();m['tools'][0]['sealed_executable_profile']='linux_browser320_mib'
  with self.assertRaisesRegex(ValueError,'unsupported ToolMount'):p.check_host_profile(m)
 def test_external_network_and_proxy_not_enabled_as_loopback_fix(self):
  for key,value in [('network',True),('loopback_port',54321)]:
   with self.subTest(key=key):
    m=self.manifest();m[key]=value
    with self.assertRaisesRegex(ValueError,'not authorized'):p.check_host_profile(m)
 def test_no_extra_tools_or_mount_authority(self):
  for field,value in [('mounts',['runtime','project','output','ambient']),('dependencies',['curl']),('system_config',['secrets'])]:
   with self.subTest(field=field):
    m=self.manifest();m['tools'][0][field]=value
    with self.assertRaisesRegex(ValueError,'unexpected tool authority'):p.check_host_profile(m)
 def test_offline_validation_preserves_all_original_files(self):
  before={x.relative_to(self.runtime).as_posix():p.sha(x.read_bytes()) for x in self.runtime.rglob('*') if x.is_file()}
  lock,roles,digest=p.validate_runtime(self.runtime);self.assertEqual(lock,self.lock);self.assertEqual(set(roles),p.ROLES);self.assertEqual(digest,before['runtime-lock.json'])
  self.assertEqual(before,{x.relative_to(self.runtime).as_posix():p.sha(x.read_bytes()) for x in self.runtime.rglob('*') if x.is_file()})
 def test_changed_dependency_refuses_before_output(self):
  self.put('tools/browser',b'changed')
  with self.assertRaisesRegex(ValueError,'changed|budget'):p.validate_runtime(self.runtime)
 def test_unlisted_file_is_not_ignored(self):
  self.put('ambient.txt',b'not admitted')
  with self.assertRaisesRegex(ValueError,'unlisted'):p.validate_runtime(self.runtime)
 def test_unlisted_empty_directory_is_not_ignored(self):
  (self.runtime/'empty-dir').mkdir()
  with self.assertRaisesRegex(ValueError,'unlisted'):p.validate_runtime(self.runtime)
 def test_changed_runner_pin_is_rejected_even_with_rewritten_inventory(self):
  row=next(x for x in self.lock['files'] if x['role']=='runner');row['sha256']='3'*64
  with self.assertRaisesRegex(ValueError,'entrypoint'):p.validate_lock(self.lock)
 def test_missing_original_metadata_is_rejected(self):
  self.lock['files']=[x for x in self.lock['files'] if not x['path'].startswith('origin/')]
  with self.assertRaisesRegex(ValueError,'original registry metadata'):p.validate_lock(self.lock)
 def test_duplicate_and_unknown_lock_fields_are_rejected(self):
  self.lock['ambient_path']='/outside'
  with self.assertRaisesRegex(ValueError,'shape'):p.validate_lock(self.lock)
  with self.assertRaisesRegex(ValueError,'duplicate'):p.parse_json(b'{"schema":1,"schema":2}')
 def test_noncanonical_json_is_rejected(self):
  self.put('runtime-lock.json',json.dumps(self.lock).encode())
  with self.assertRaisesRegex(ValueError,'noncanonical'):p.validate_runtime(self.runtime)
 def test_no_boolean_or_zero_size_is_accepted_as_bytes(self):
  for value in (True,False,0):
   with self.subTest(value=value):
    other=copy.deepcopy(self.lock);other['files'][0]['bytes']=value
    with self.assertRaisesRegex(ValueError,'file identity'):p.validate_lock(other)
 def test_duplicate_role_and_traversal_paths_rejected(self):
  other=copy.deepcopy(self.lock);other['files'][-1]['path']='../outside'
  with self.assertRaisesRegex(ValueError,'file identity'):p.validate_lock(other)
  other=copy.deepcopy(self.lock);other['files'].append({**other['files'][0],'path':'second-runner.mjs'})
  with self.assertRaisesRegex(ValueError,'duplicate runtime role'):p.validate_lock(other)
 def test_wrong_distribution_does_not_become_valid_by_hashing_it(self):
  name='package-lock.json';value={'packages':{'node_modules/hyperframes':{'version':'0.8.167','resolved':p.RESOLVED,'integrity':p.INTEGRITY}}};raw=json.dumps(value).encode();self.put(name,raw)
  row=next(x for x in self.lock['files'] if x['path']==name);row.update(bytes=len(raw),sha256=p.sha(raw));self.write_lock()
  with self.assertRaisesRegex(ValueError,'distribution'):p.validate_runtime(self.runtime)
 def test_nonlinux_provisioning_refuses_before_any_policy_or_tool_call(self):
  with patch.object(p.sys,'platform','win32'):
   with self.assertRaisesRegex(ValueError,'Linux'):p.make(argparse.Namespace())
  self.assertFalse((self.root/'hyperframes-driver.json').exists())
if __name__=='__main__':unittest.main()
