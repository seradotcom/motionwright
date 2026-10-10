#!/usr/bin/env node
// Read-only CI admission probe for pinned Playwright executables.
// Never executes, changes permissions, strips or installs a binary.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

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
if(rows[1].sha256!==receipt.files.browser.sha256 || rows[1].bytes!==receipt.files.browser.bytes)
  throw new Error('Pinned headless shell changed after installation');
const report={
  schema:'motionwright.exact-browser-tool-size-admission/1',
  profile:'hyperframes-core-chromium-png-v2',
  sealed_host_executable_limit_bytes:limit,
  candidates:rows,
  authority:'read_only_verification_not_permission_to_execute',
  core_mutation:'none',
  policy_change:'none'
};
const destination=path.resolve('verification/browser-tool-profile');
fs.mkdirSync(destination,{recursive:true});
fs.writeFileSync(path.join(destination,'result.json'),JSON.stringify(report,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({browser_size_admission:'MEASURED_NOT_EXECUTED',
  rows:rows.map(({kind,bytes,seal_with_existing_host_limit})=>({kind,bytes,seal_with_existing_host_limit}))}));
