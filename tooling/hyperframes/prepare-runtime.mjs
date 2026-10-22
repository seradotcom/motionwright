// Explicit offline finalization of an owner-installed closure; never installs or renders.
import * as fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {VERSION,COMMIT,NPM_INTEGRITY,sha,requireValue,relative,regular,validateLock,actualNodeIdentity}
  from '../../native/hyperframes/semwright-hyperframes-runner.mjs';
export const REVIEWED_RUNNER_SHA256='e2352fe31555f06f8a423e1cad4d84e8ff196f9707d04961ab476855d080b7b0';
export const RESOLVED='https://registry.npmjs.org/hyperframes/-/hyperframes-0.8.181.tgz';
const KEYS=['runtime','browser','ffmpeg','ffprobe','browser_version','ffmpeg_version','ffprobe_version'];
export function argumentsFrom(argv){
  requireValue(argv.length===KEYS.length*2,'closed_finalizer_arguments');const result={};
  for(let i=0;i<argv.length;i+=2){const key=argv[i].replace(/^--/,'').replaceAll('-','_');
    requireValue(argv[i].startsWith('--')&&KEYS.includes(key)&&!(key in result)&&typeof argv[i+1]==='string'&&argv[i+1].length>0,'closed_finalizer_arguments');result[key]=argv[i+1];}
  requireValue(KEYS.every(key=>key in result),'closed_finalizer_arguments');return result;
}
export function validateDistribution(packageLock,packageJson){
  const item=packageLock.packages?.['node_modules/hyperframes'];
  requireValue(item?.version===VERSION&&item?.resolved===RESOLVED&&item?.integrity===NPM_INTEGRITY,'public_distribution_lock');
  requireValue(packageJson.name==='hyperframes'&&packageJson.version===VERSION,'actual_cli_package_version');
}
export function makeLock(files,options,node){
  const lock={schema:'semwright.hyperframes.runtime-lock/1',hyperframes_version:VERSION,hyperframes_commit:COMMIT,
    node_version:node.version,node_sha256:node.sha256,browser_version:options.browser_version,
    ffmpeg_version:options.ffmpeg_version,ffprobe_version:options.ffprobe_version,files};
  validateLock(lock);return lock;
}
export async function collectRuntime(options,node){
  const root=options.runtime;requireValue(path.isAbsolute(root)&&await fs.realpath(root)===root,'canonical_runtime');
  const selected={runner:'semwright-hyperframes-runner.mjs','hyperframes-cli':'node_modules/hyperframes/bin/hyperframes.mjs',
    'package-lock':'package-lock.json',browser:options.browser,ffmpeg:options.ffmpeg,ffprobe:options.ffprobe};
  const roleByPath=new Map();
  for(const [role,file] of Object.entries(selected)){requireValue(relative(file)&&!roleByPath.has(file),'runtime_selected_path');roleByPath.set(file,role);}
  const rows=[];let total=0;
  async function visit(dir){
    requireValue(await fs.realpath(dir)===dir,'runtime_directory_redirect');const entries=await fs.readdir(dir,{withFileTypes:true});
    requireValue(entries.length>0&&entries.length<=30000,'runtime_directory_budget');
    for(const entry of entries.sort((a,b)=>a.name<b.name?-1:a.name>b.name?1:0)){
      const file=path.join(dir,entry.name),rel=path.relative(root,file).split(path.sep).join('/');
      requireValue(relative(rel)&&rel!=='runtime-lock.json'&&!entry.isSymbolicLink(),'runtime_namespace');
      if(entry.isDirectory()){await visit(file);continue;}
      requireValue(entry.isFile(),'runtime_regular_file');const stat=await fs.lstat(file);
      requireValue(rows.length<30000&&stat.size>0&&stat.size<=512*1024*1024&&total+stat.size<=2*1024*1024*1024,'runtime_closure_budget');
      const bytes=await regular(file,stat.size);total+=bytes.length;
      const role=roleByPath.get(rel)??'dependency';
      if(role==='runner')requireValue(sha(bytes)===REVIEWED_RUNNER_SHA256,'reviewed_runner_pin');
      if(['browser','ffmpeg','ffprobe'].includes(role)&&process.platform==='linux')requireValue((stat.mode&0o111)!==0,'runtime_tool_not_executable');
      rows.push({path:rel,sha256:sha(bytes),bytes:bytes.length,role});
    }
  }
  await visit(root);rows.sort((a,b)=>a.path<b.path?-1:a.path>b.path?1:0);
  validateDistribution(JSON.parse((await regular(path.join(root,'package-lock.json'),8*1024*1024)).toString('utf8')),
    JSON.parse((await regular(path.join(root,'node_modules/hyperframes/package.json'),262144)).toString('utf8')));
  return makeLock(rows,options,node);
}
export async function finalize(options){
  // These expected tool version strings are re-measured by the admitted adapter
  // inside Host before native rendering. This finalizer does not execute them.
  requireValue(process.platform==='linux','native_profile_linux_required');
  const lock=await collectRuntime(options,await actualNodeIdentity());
  const bytes=Buffer.from(JSON.stringify(lock,null,2)+'\n');requireValue(bytes.length<=8*1024*1024,'runtime_lock_budget');
  const destination=path.join(options.runtime,'runtime-lock.json');const handle=await fs.open(destination,'wx',0o600);
  try{await handle.writeFile(bytes);await handle.sync();}finally{await handle.close();}
  return {schema:lock.schema,hyperframes_version:VERSION,runtime_lock_sha256:sha(bytes),files:lock.files.length,
    node_identity_observed:true,tool_versions_observed:false,native_executed:false,automatic_retry:false};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  try{console.log(JSON.stringify(await finalize(argumentsFrom(process.argv.slice(2)))));}
  catch(error){console.error('HyperFrames finalization refused: '+error.message);process.exitCode=2;}
}
