#!/usr/bin/env python3
"""Negative and successful owner-provision contract tests using only disposable synthetic files."""
from __future__ import annotations
import argparse,hashlib,json,os,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import owner_provision as provision
def sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
class OwnerProvisionTests(unittest.TestCase):
 def setUp(self)->None:
  self.temp=tempfile.TemporaryDirectory(prefix='mw-owner-test-')
  self.addCleanup(self.temp.cleanup)
  root=Path(self.temp.name)
  runtime=root/'runtime';runtime.mkdir()
  self.executable={}
  for name in ('driver','runner','node','ffmpeg'):
   file=root/name;file.write_text(f'{name} synthetic executable');file.chmod(0o700);self.executable[name]=file
  for name in ('work','output','assets'):(root/name).mkdir(mode=0o700)
  files={}
  for name in ('hyperframes_script','gsap_script','sans_font','mono_font','browser'):
   file=runtime/name;file.write_text(f'{name} synthetic digest material');
   if name=='browser':file.chmod(0o700)
   files[name]={'path':name,'sha256':sha(file),'bytes':file.stat().st_size}
  inventory=runtime/'runtime-files.json';inventory.write_text('{"schema":1,"files":[]}')
  lock=runtime/'package-lock.json';lock.write_text('{"name":"synthetic"}')
  receipt={'schema':1,'hyperframes':'0.8.143','capture_profile':'hyperframes-core-chromium-png-v2',
     'gsap':'3.15.0','playwright':'1.55.1','fontkit':'2.0.4','platform':'linux','architecture':'x64',
     'npm_lock_sha256':sha(lock),'inventory':{'sha256':sha(inventory)},'files':files}
  (runtime/'runtime.json').write_text(json.dumps(receipt))
  self.runtime=runtime
  self.args=argparse.Namespace(runtime=str(runtime),work=str(root/'work'),output=str(root/'output'),assets=str(root/'assets'),
      approved_license_terms=True,approved_sandbox_controls=True,approved_large_browser_tool=True,**{k:str(v)for k,v in self.executable.items()})
 def test_success_does_not_modify_runtime_or_install_dependencies(self):
  before={p.name:sha(p)for p in self.runtime.iterdir()if p.is_file()}
  result=provision.make(self.args)
  self.assertEqual(result['manifest']['id'],'hyperframes')
  self.assertFalse(result['manifest']['network'])
  self.assertEqual(result['manifest']['sha256'],sha(self.executable['driver']))
  self.assertEqual(len(result['manifest']['tools']),4)
  self.assertEqual(result['manifest']['tools'][-1]['name'],'chromium')
  self.assertEqual(result['manifest']['tools'][-1]['sealed_executable_profile'],'linux_browser320_mib')
  self.assertEqual(result['manifest']['tools'][0]['dependencies'],['node','ffmpeg','chromium'])
  self.assertIn('network = false',result['owner'].replace('driver_network = false','network = false'))
  self.assertEqual(before,{p.name:sha(p)for p in self.runtime.iterdir()if p.is_file()})
 def test_owner_must_approve_both_rights_and_sandbox(self):
  for field in ('approved_license_terms','approved_sandbox_controls','approved_large_browser_tool'):
   setattr(self.args,field,False)
   with self.assertRaisesRegex(ValueError,'explicitly confirm'):provision.make(self.args)
   setattr(self.args,field,True)
 def test_reject_modified_runtime_binary(self):
  file=self.runtime/'hyperframes_script';file.write_text('changed')
  with self.assertRaisesRegex(ValueError,'changed'):provision.make(self.args)
 def test_reject_runtime_and_dependency_symlinks(self):
  root=Path(self.temp.name);link=root/'runtime-link';link.symlink_to(self.runtime)
  self.args.runtime=str(link)
  with self.assertRaisesRegex(ValueError,'symbolic'):provision.make(self.args)
  self.args.runtime=str(self.runtime)
  asset=self.runtime/'sans_font';asset.unlink();asset.symlink_to(self.runtime/'gsap_script')
  with self.assertRaisesRegex(ValueError,'symbolic'):provision.make(self.args)
 def test_reject_relative_paths_and_unapproved_executables(self):
  self.args.node='node'
  with self.assertRaisesRegex(ValueError,'absolute'):provision.make(self.args)
  self.args.node=str(self.executable['node'])
  self.executable['node'].chmod(0o600)
  with self.assertRaisesRegex(ValueError,'executable'):provision.make(self.args)
 def test_reject_package_lock_drift(self):
  (self.runtime/'package-lock.json').write_text('{"changed":true}')
  with self.assertRaisesRegex(ValueError,'lock'):provision.make(self.args)
if __name__=='__main__':unittest.main()
