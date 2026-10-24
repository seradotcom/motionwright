#!/usr/bin/env python3
"""Offline adaptation of the existing Owner provisioner to the reviewed .181 CLI.

Reads an explicit installed closure; never downloads, executes it, modifies an
existing policy, opens a session, starts a renderer, or retries a native effect.
Outputs review material for the existing Semwright54d Host, not another Host.
"""
from __future__ import annotations
import argparse,hashlib,json,os,re,stat,sys
from pathlib import Path
VERSION='0.8.181'
COMMIT='ae02e5aceff4742e5d72c20f0859eb3e6b449035'
SDK_PIN='54d3459a051adbce4cae9f60c0dea8f60c824be6'
METADATA_SHA='d3f1dd614e50191bd08391108fb17e521aded5c9a9aa0f30947672f8667ea673'
RUNNER_SHA='e2352fe31555f06f8a423e1cad4d84e8ff196f9707d04961ab476855d080b7b0'
INTEGRITY='sha512-rBShZnCVW5xLe+6VViIv+SEAjYQ1Z9z8mt5M+XTCHoRj/eGRg5Z/yyvVNsWZZeuipoba5yhNfodOnwn94n4PPw=='
RESOLVED='https://registry.npmjs.org/hyperframes/-/hyperframes-0.8.181.tgz'
ROLES={'runner','hyperframes-cli','package-lock','browser','ffmpeg','ffprobe'}
LOCK_KEYS={'schema','hyperframes_version','hyperframes_commit','node_version','node_sha256','browser_version','ffmpeg_version','ffprobe_version','files'}
def require(value:bool,message:str)->None:
 if not value:raise ValueError(message)
def sha(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def is_digest(value)->bool:return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None
def relative(value)->bool:
 return isinstance(value,str) and 0<len(value)<=1024 and '\\' not in value and all(re.fullmatch('[A-Za-z0-9._@+-]+',p) and p not in ('.','..','.env','.npmrc','.netrc','.git','config','credentials','secrets') and not p.endswith(('.session','.pem','.key')) for p in value.split('/'))
def no_duplicates(pairs):
 result={}
 for key,val in pairs:
  require(key not in result,'duplicate JSON key');result[key]=val
 return result
def parse_json(raw:bytes,canonical:bool=False):
 require(0<len(raw)<=8*1024*1024,'JSON budget')
 value=json.loads(raw.decode('utf-8'),object_pairs_hook=no_duplicates,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite JSON')))
 if canonical:require((json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()==raw,'noncanonical runtime lock')
 return value
def absolute(raw:str,directory:bool)->Path:
 require(isinstance(raw,str) and not any(ord(c)<32 or ord(c)==127 for c in raw),'invalid path')
 p=Path(raw);require(p.is_absolute(),'absolute owner-selected path required')
 walk=Path(p.parts[0])
 for part in p.parts[1:]:
  require(part not in ('.','..'),'unnormalized path');walk/=part;require(not stat.S_ISLNK(walk.lstat().st_mode),'symbolic link is not permitted')
 require(p.resolve(strict=True)==p,'canonical path required')
 require(p.is_dir() if directory else p.is_file(),'required directory or regular file absent');return p
def checked_bytes(p:Path,limit:int)->bytes:
 p=absolute(str(p),False);before=p.stat()
 require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<before.st_size<=limit,'file kind or budget')
 with p.open('rb') as stream:
  observed=os.fstat(stream.fileno());raw=stream.read(limit+1);after=os.fstat(stream.fileno())
 key=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_nlink)
 require(key(before)==key(observed)==key(after)==key(p.stat()) and len(raw)==before.st_size,'file changed during observation');return raw
def validate_lock(lock:dict)->dict:
 require(isinstance(lock,dict) and set(lock)==LOCK_KEYS,'runtime lock shape')
 require(lock['schema']=='semwright.hyperframes.runtime-lock/1' and lock['hyperframes_version']==VERSION and lock['hyperframes_commit']==COMMIT,'runtime identity')
 require(isinstance(lock['node_version'],str) and re.fullmatch(r'v(?:2[2-9]|[3-9][0-9])\.\d+\.\d+',lock['node_version']) and is_digest(lock['node_sha256']),'Node identity')
 for role in ('browser','ffmpeg','ffprobe'):
  text=lock[role+'_version'];require(isinstance(text,str) and 0<len(text)<=512 and not any(ord(c)<32 or ord(c)==127 for c in text),'tool version')
 require(isinstance(lock['files'],list) and 0<len(lock['files'])<=30000,'runtime roster')
 names=set();roles={};total=0
 for row in lock['files']:
  require(isinstance(row,dict) and set(row)=={'path','sha256','bytes','role'},'runtime file shape')
  require(relative(row['path']) and row['path']!='runtime-lock.json' and row['path'] not in names and is_digest(row['sha256']) and type(row['bytes']) is int and 0<row['bytes']<=512*1024*1024 and row['role'] in ROLES|{'dependency'},'runtime file identity')
  names.add(row['path']);total+=row['bytes']
  if row['role']!='dependency':
   require(row['role'] not in roles,'duplicate runtime role');roles[row['role']]=row
 require(set(roles)==ROLES and total<=2*1024*1024*1024,'runtime closure budget or roles')
 require(roles['runner']['path']=='semwright-hyperframes-runner.mjs' and roles['runner']['sha256']==RUNNER_SHA and roles['hyperframes-cli']['path']=='node_modules/hyperframes/bin/hyperframes.mjs' and roles['package-lock']['path']=='package-lock.json','runtime entrypoint')
 require(any(x['path']=='origin/hyperframes-0.8.181.json' and x['sha256']==METADATA_SHA and x['bytes']==3553 and x['role']=='dependency' for x in lock['files']),'original registry metadata')
 require(any(x['path']=='node_modules/hyperframes/package.json' and x['bytes']<=262144 and x['role']=='dependency' for x in lock['files']),'actual package entry')
 return roles
def validate_runtime(runtime:Path)->tuple:
 raw=checked_bytes(runtime/'runtime-lock.json',8*1024*1024);lock=parse_json(raw,True);roles=validate_lock(lock)
 expected={x['path']:x for x in lock['files']};directories={''}
 for name in expected:
  parent=Path(name).parent
  while str(parent)!='.':directories.add(parent.as_posix());parent=parent.parent
 for rel in directories:
  folder=absolute(str(runtime/rel),True);entries=list(folder.iterdir());require(len(entries)<=30001,'namespace budget')
  for p in entries:
   local=p.relative_to(runtime).as_posix();require(local=='runtime-lock.json' or local in expected or local in directories,'unlisted runtime entry')
 for name,row in expected.items():
  data=checked_bytes(runtime/name,row['bytes']);require(len(data)==row['bytes'] and sha(data)==row['sha256'],'runtime dependency changed')
 pl=parse_json(checked_bytes(runtime/'package-lock.json',8*1024*1024));pkg=parse_json(checked_bytes(runtime/'node_modules/hyperframes/package.json',262144))
 item=pl.get('packages',{}).get('node_modules/hyperframes',{})
 require(item.get('version')==VERSION and item.get('resolved')==RESOLVED and item.get('integrity')==INTEGRITY,'public distribution lock')
 require(pkg.get('name')=='hyperframes' and pkg.get('version')==VERSION,'actual CLI package version')
 return lock,roles,sha(raw)
def check_host_profile(manifest:dict)->None:
 # Specific .181 compatibility guard; SDK deserialization/Host validation remain mandatory.
 require(manifest['protocol']==7 and manifest['resources']['operation_cpu_seconds']==0,'SDK54 Linux Host tools require operation_cpu_seconds=0')
 require(manifest['network'] is False and manifest['loopback_port'] is None,'external network and proxy are not authorized')
 require(len(manifest['tools'])==1 and manifest['tools'][0]['name']=='hyperframes-node','fixed Node Host tool required')
 require(set(manifest['tools'][0])=={'root','name','sha256','mounts','system_config','dependencies'},'unsupported ToolMount fields')
 require(manifest['tools'][0]['mounts']==['runtime','project','output'] and manifest['tools'][0]['dependencies']==[] and manifest['tools'][0]['system_config']==[],'unexpected tool authority')
def build_manifest(paths:dict,driver_sha:str,node_sha:str)->dict:
 manifest={'manifest_version':1,'protocol':7,'id':'hyperframes','version':'0.1.0','publisher':'motionwright',
  'executable':str(paths['driver']),'sha256':driver_sha,'application':{'desktop_id':None,'process_names':['node'],'supported_versions':['HyperFrames 0.8.181 (owner installed)']},'transport':'stdio_v1',
  'mounts':[{'root':name,'read_only':name=='runtime','execute':name=='runtime'} for name in ('runtime','project','output')],
  'system_config':[],'secrets':[],'tools':[{'root':'hyperframes-node-tool','name':'hyperframes-node','sha256':node_sha,'mounts':['runtime','project','output'],'system_config':[],'dependencies':[]}],
  'network':False,'nvidia_gpu':False,'loopback_port':None,
  'resources':{'open_files':512,'processes':256,'cpu_seconds':300,'operation_cpu_seconds':0,'address_space_bytes':4294967296,'file_size_bytes':1073741824},'request_timeout_ms':300000,
  'interfaces':{'dynamic_capabilities':False,'cooperative_cancellation':False,'events':False,'health':True,'progress':False,'artifacts':False,'native_refs':False,'host_tools':True}}
 check_host_profile(manifest);return manifest
def make(args:argparse.Namespace)->dict:
 require(sys.platform=='linux','Only the reviewed Linux native profile can be provisioned')
 require(args.approved_license_terms and args.approved_sandbox_controls and args.approved_runtime_executables,'Owner must explicitly confirm rights and runtime execution authority')
 paths={name:absolute(getattr(args,name),name in ('runtime','project','output')) for name in ('runtime','driver','node','project','output')}
 roots=[paths[x] for x in ('runtime','project','output')]
 for i,left in enumerate(roots):
  for right in roots[i+1:]:require(not left.is_relative_to(right) and not right.is_relative_to(left),'mounts must be distinct and nonoverlapping')
 for name in ('driver','node'):
  require(paths[name].stat().st_mode&0o111,'regular executable required')
  require(not any(paths[name].is_relative_to(paths[x]) for x in ('project','output')),'executable overlaps writable mount')
 lock,roles,lock_sha=validate_runtime(paths['runtime'])
 for role in ('browser','ffmpeg','ffprobe'):require((paths['runtime']/roles[role]['path']).stat().st_mode&0o111,'runtime tool must be executable')
 node_sha=sha(checked_bytes(paths['node'],256*1024*1024));require(node_sha==lock['node_sha256'],'Node differs from the finalized identity')
 driver_sha=sha(checked_bytes(paths['driver'],512*1024*1024));manifest=build_manifest(paths,driver_sha,node_sha)
 owner=['# REVIEW ONLY: owner must admit this configuration through the existing Semwright Host.',
  'drivers = ["__OWNER_SET_MANIFEST_PATH__"]','driver_network = false','','[policy]','profile = "workspace"','allow = ["driver:hyperframes"]']
 for name,p,writable in [('runtime',paths['runtime'],False),('project',paths['project'],True),('output',paths['output'],True),('hyperframes-node-tool',paths['node'],False)]:
  owner+=['','[[policy.filesystem]]','name = '+json.dumps(name),'path = '+json.dumps(str(p)),'read = true','write = '+str(writable).lower()]
 return {'manifest':manifest,'owner':'\n'.join(owner)+'\n','runtime_lock_sha256':lock_sha,'native_driver_sha256':driver_sha,'node_sha256':node_sha,'semwright_source_pin':SDK_PIN,'tool_versions_observed':False,'native_executed':False,'automatic_retry':False}
def main()->None:
 p=argparse.ArgumentParser(description=__doc__)
 for name in ('runtime','driver','node','project','output'):p.add_argument('--'+name,required=True)
 p.add_argument('--out-dir',required=True)
 for flag in ('approved-license-terms','approved-sandbox-controls','approved-runtime-executables'):p.add_argument('--'+flag,action='store_true')
 args=p.parse_args()
 try:
  result=make(args);dest=Path(args.out_dir);require(dest.is_absolute() and not dest.exists(),'fresh absolute output directory required')
  parent=absolute(str(dest.parent),True);require(parent/dest.name==dest,'canonical output directory required')
  for name in ('runtime','project','output'):require(not dest.is_relative_to(Path(getattr(args,name))),'review output overlaps native mount')
  dest.mkdir(mode=0o700);manifest=dest/'hyperframes-driver.json'
  values={'hyperframes-driver.json':json.dumps(result['manifest'],indent=2)+'\n','semwright-owner-REVIEW.toml':result['owner'].replace('__OWNER_SET_MANIFEST_PATH__',json.dumps(str(manifest))[1:-1]),'provision-receipt.json':json.dumps({k:v for k,v in result.items() if k not in ('manifest','owner')},indent=2)+'\n'}
  for name,text in values.items():
   with (dest/name).open('x',encoding='utf-8',newline='\n') as output:output.write(text);output.flush();os.fsync(output.fileno())
   (dest/name).chmod(0o600)
  print(json.dumps({'status':'owner_review_required','manifest':str(manifest),'runtime_lock_sha256':result['runtime_lock_sha256'],'host_sandbox':'required','network':False,'native_executed':False,'automatic_retry':False}))
 except (OSError,ValueError,KeyError,TypeError) as error:p.exit(2,f'Owner provision refused: {error}\n')
if __name__=='__main__':main()
