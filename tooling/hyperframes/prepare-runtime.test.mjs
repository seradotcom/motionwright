// G0 contract tests only. Synthetic tools are never executed.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {argumentsFrom,collectRuntime,makeLock,validateDistribution,RESOLVED} from './prepare-runtime.mjs';
import {VERSION,COMMIT,NPM_INTEGRITY,validateLock} from '../../native/hyperframes/semwright-hyperframes-runner.mjs';
const here=path.dirname(fileURLToPath(import.meta.url)),source=path.resolve(here,'../..');
const tempRoot=path.join(source,'.tmp','hyperframes-contract');
const fakeNode={version:'v22.22.0',sha256:'1'.repeat(64)};
const options={browser:'tools/browser',ffmpeg:'tools/ffmpeg',ffprobe:'tools/ffprobe',browser_version:'Chromium synthetic G0',ffmpeg_version:'ffmpeg synthetic G0',ffprobe_version:'ffprobe synthetic G0'};
async function fixture(t){
  await fs.mkdir(tempRoot,{recursive:true});
  const root=await fs.mkdtemp(path.join(tempRoot,'hf-g0-'));t.after(()=>fs.rm(root,{recursive:true,force:true}));
  async function write(name,bytes){const file=path.join(root,name);await fs.mkdir(path.dirname(file),{recursive:true});await fs.writeFile(file,bytes,{mode:0o700});}
  const packageLock={packages:{'node_modules/hyperframes':{version:VERSION,resolved:RESOLVED,integrity:NPM_INTEGRITY}}};
  await write('package-lock.json',JSON.stringify(packageLock,null,2)+'\n');
  await write('node_modules/hyperframes/package.json',JSON.stringify({name:'hyperframes',version:VERSION}));
  await write('node_modules/hyperframes/bin/hyperframes.mjs','/* synthetic CLI: never executed */\n');
  await write('semwright-hyperframes-runner.mjs',await fs.readFile(path.join(here,'../../native/hyperframes/semwright-hyperframes-runner.mjs')));
  await write('origin/hyperframes-0.8.181.json',await fs.readFile(path.join(here,'../../tooling/hyperframes/fixtures/hyperframes-0.8.181-original.json')));
  for(const role of ['browser','ffmpeg','ffprobe'])await write(options[role],'synthetic non-executable-content '+role+'\n');
  return {root,write,packageLock,options:{...options,runtime:await fs.realpath(root)}};
}
test('closed explicit finalizer arguments',()=>{const args=['--runtime','/owner/runtime',...Object.entries(options).flatMap(([k,v])=>['--'+k.replaceAll('_','-'),v])];assert.equal(argumentsFrom(args).ffprobe,'tools/ffprobe');});
test('unknown and duplicate arguments refuse',()=>{const args=['--runtime','/owner/runtime',...Object.entries(options).flatMap(([k,v])=>['--'+k.replaceAll('_','-'),v])];assert.throws(()=>argumentsFrom([...args,'--install','true']));args[2]='--runtime';assert.throws(()=>argumentsFrom(args));});
test('original closure produces exactly the admitted lock and preserves files',async t=>{const f=await fixture(t);const lock=await collectRuntime(f.options,fakeNode);assert.equal(lock.hyperframes_version,VERSION);assert.equal(lock.hyperframes_commit,COMMIT);assert.equal(lock.files.length,8);assert.equal(validateLock(lock).size,6);assert.equal((await fs.readdir(f.root)).includes('runtime-lock.json'),false);});
test('finalizer reuses exact adapter validator',async t=>{const f=await fixture(t);const lock=await collectRuntime(f.options,fakeNode);assert.deepEqual(makeLock(lock.files,f.options,fakeNode),lock);});
for(const field of ['version','resolved','integrity'])test('reject distribution '+field+' drift',()=>{const item={version:VERSION,resolved:RESOLVED,integrity:NPM_INTEGRITY};item[field]='different';assert.throws(()=>validateDistribution({packages:{'node_modules/hyperframes':item}},{name:'hyperframes',version:VERSION}),/public_distribution_lock/);});
test('reject renamed package',()=>{assert.throws(()=>validateDistribution({packages:{'node_modules/hyperframes':{version:VERSION,resolved:RESOLVED,integrity:NPM_INTEGRITY}}},{name:'other',version:VERSION}),/actual_cli_package_version/);});
test('reject changed reviewed adapter before finalization',async t=>{const f=await fixture(t);await f.write('semwright-hyperframes-runner.mjs','changed');await assert.rejects(collectRuntime(f.options,fakeNode),/reviewed_runner_pin/);});
test('reject changed original metadata',async t=>{const f=await fixture(t);await f.write('origin/hyperframes-0.8.181.json','different metadata');await assert.rejects(collectRuntime(f.options,fakeNode),/runtime_entrypoint/);});
test('reject stale preexisting runtime lock without overwrite',async t=>{const f=await fixture(t);await f.write('runtime-lock.json','old original');await assert.rejects(collectRuntime(f.options,fakeNode),/runtime_namespace/);assert.equal(await fs.readFile(path.join(f.root,'runtime-lock.json'),'utf8'),'old original');});
test('reject two selected roles at same path',async t=>{const f=await fixture(t);await assert.rejects(collectRuntime({...f.options,ffprobe:f.options.ffmpeg},fakeNode),/runtime_selected_path/);});
test('reject an ambient traversal path',async t=>{const f=await fixture(t);await assert.rejects(collectRuntime({...f.options,browser:'../outside'},fakeNode),/runtime_selected_path/);});
test('reject missing selected tool',async t=>{const f=await fixture(t);await fs.unlink(path.join(f.root,options.ffprobe));await assert.rejects(collectRuntime(f.options,fakeNode),/runtime_closure_budget/);});
test('reject unpinned empty file instead of silently skipping it',async t=>{const f=await fixture(t);await f.write('empty.txt','');await assert.rejects(collectRuntime(f.options,fakeNode),/runtime_closure_budget/);});
test('reject unrepresented empty directory',async t=>{const f=await fixture(t);await fs.mkdir(path.join(f.root,'empty-dir'));await assert.rejects(collectRuntime(f.options,fakeNode),/runtime_directory_budget/);});
test('reject a forbidden credential filename',async t=>{const f=await fixture(t);await f.write('.npmrc','synthetic');await assert.rejects(collectRuntime(f.options,fakeNode),/runtime_namespace/);});
test('reject control-bearing expected version',async t=>{const f=await fixture(t);await assert.rejects(collectRuntime({...f.options,browser_version:'Chromium\nforged'},fakeNode),/runtime_version/);});
test('reject unsupported Node major metadata',async t=>{const f=await fixture(t);await assert.rejects(collectRuntime(f.options,{...fakeNode,version:'v20.0.0'}),/runtime_identity/);});
test('reject hard-linked closure file',async t=>{const f=await fixture(t);await fs.link(path.join(f.root,options.browser),path.join(f.root,'alias-browser'));await assert.rejects(collectRuntime(f.options,fakeNode),/file_kind_or_budget/);});
