#!/usr/bin/env node
// Disposable-CI diagnostic for pinned Playwright executables and resources.
// Does not install, grant execution, disable namespaces, alter host policies,
// or persist runtime stderr; the fixture may execute --version in isolated CI.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import os from 'node:os';
import {spawnSync} from 'node:child_process';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../runtime/hyperframes');
// Playwright's .executablePath() otherwise defaults to ambient ~/.cache even
// when npm installation used an explicitly owner-scoped browser root.
process.env.PLAYWRIGHT_BROWSERS_PATH=path.join(root,'.browsers');
const require=createRequire(path.join(root,'package.json'));
const {chromium}=require('playwright');
const limit=256*1024*1024;
const receipt=JSON.parse(fs.readFileSync(path.join(root,'runtime.json'),'utf8'));
if(receipt.schema!==1 || receipt.hyperframes!=='0.8.143' || receipt.playwright!=='1.55.1')
  throw new Error('Runtime receipt differs from the admitted exact profile');
const candidateFiles=[
  ['playwright_full_chromium',chromium.executablePath()],
  ['playwright_chromium_headless_shell',path.join(root,receipt.files.browser.path)]
];
function readCandidate(kind,p){
  const absolute=fs.realpathSync(p);
  if(!absolute.startsWith(root+path.sep))throw new Error('Browser executable escaped the exact installed runtime root');
  const stat=fs.lstatSync(absolute);
  if(!stat.isFile() || stat.isSymbolicLink())throw new Error('Candidate browser is not a regular immutable file');
  const sha=crypto.createHash('sha256');
  const file=fs.openSync(absolute,'r');
  try {
    const block=Buffer.alloc(1024*1024);
    let offset=0;
    while(offset<stat.size){
      const read=fs.readSync(file,block,0,Math.min(block.length,stat.size-offset),offset);
      if(read<=0)throw new Error('Could not read the exact browser executable');
      sha.update(block.subarray(0,read));offset+=read;
    }
  }finally{fs.closeSync(file);}
  return {kind,bytes:stat.size,sha256:sha.digest('hex'),seal_with_existing_host_limit:stat.size>0&&stat.size<=limit,
    inside_owner_selected_runtime:true};
}
const rows=candidateFiles.map(([kind,file])=>readCandidate(kind,file));
const headless=path.join(root,receipt.files.browser.path),directory=path.dirname(headless);
const neighbors=fs.readdirSync(directory,{withFileTypes:true}).filter(x=>x.isFile())
  .map(x=>{
    const original=path.join(directory,x.name),stat=fs.lstatSync(original);
    if(!/^[a-zA-Z0-9_.-]{1,90}$/.test(x.name)||stat.size>64*1024*1024||stat.isSymbolicLink())
      return {name:x.name,sidecar_admissible:false};
    return {name:x.name,bytes:stat.size,sha256:crypto.createHash('sha256').update(fs.readFileSync(original)).digest('hex'),
      executable:(stat.mode&0o111)!==0,sidecar_admissible:stat.size<=32*1024*1024};
  });
const isolated=fs.mkdtempSync(path.join(os.tmpdir(),'mw-browser-sidecar-comparison-'));
let versionChecks;
try {
  const copied=path.join(isolated,'headless_shell');
  fs.copyFileSync(headless,copied,fs.constants.COPYFILE_EXCL);
  fs.chmodSync(copied,0o500);
  const diagnostic=file=>{
    const res=spawnSync(file,['--version'],{timeout:18000,maxBuffer:8*1024,
      env:{HOME:isolated,LANG:'C.UTF-8',LC_ALL:'C.UTF-8',TZ:'UTC'},
      stdio:['ignore','pipe','pipe'],encoding:'utf8'});
    return {success:res.status===0,exit_code:res.status,signal:res.signal,
      timeout:res.error?.code==='ETIMEDOUT',stderr_class:
        /icu|icudtl/i.test(res.stderr??'')?'icu':
        /\.pak|resources/i.test(res.stderr??'')?'resource_bundle':'unclassified'};
  };
  versionChecks={pinned_directory:diagnostic(headless),executable_alone:diagnostic(copied)};
}finally{fs.rmSync(isolated,{recursive:true,force:true});}

if(rows[1].sha256!==receipt.files.browser.sha256 || rows[1].bytes!==receipt.files.browser.bytes)
  throw new Error('Pinned headless shell changed after installation');
const report={
  schema:'motionwright.exact-browser-tool-size-admission/1',
  profile:'hyperframes-core-chromium-png-v2',
  sealed_host_executable_limit_bytes:limit,
  candidates:rows,
  pinned_headless_directory_sidecars:neighbors,
  isolated_version_probe:versionChecks,
  authority:'read_only_verification_not_permission_to_execute',
  core_mutation:'none',
  policy_change:'none'
};
const destination=path.resolve('verification/browser-tool-profile');
fs.mkdirSync(destination,{recursive:true});
fs.writeFileSync(path.join(destination,'result.json'),JSON.stringify(report,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({browser_size_admission:'MEASURED_NOT_EXECUTED',
  rows:rows.map(({kind,bytes,seal_with_existing_host_limit})=>({kind,bytes,seal_with_existing_host_limit}))}));
