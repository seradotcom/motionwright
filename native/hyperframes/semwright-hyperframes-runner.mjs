/** Native CLI adapter only. Driver Host owns supervision, isolation and jobs.
 * No downloader, Broker, capability registry, retries or arbitrary-code surface.
 */
import * as fs from 'node:fs/promises';
import {constants} from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {spawn} from 'node:child_process';

export const VERSION='0.8.181';
export const COMMIT='ae02e5aceff4742e5d72c20f0859eb3e6b449035';
export const NPM_METADATA_SHA256='d3f1dd614e50191bd08391108fb17e521aded5c9a9aa0f30947672f8667ea673';
export const NPM_INTEGRITY='sha512-rBShZnCVW5xLe+6VViIv+SEAjYQ1Z9z8mt5M+XTCHoRj/eGRg5Z/yyvVNsWZZeuipoba5yhNfodOnwn94n4PPw==';
const SOURCE_LIMIT=262144,MAX_LOG=4*1024*1024;
export const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
export function requireValue(value,code){if(!value)throw new Error(code);}
const digest=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value);
const slug=value=>typeof value==='string'&&/^[a-z0-9-]{1,96}$/.test(value);
export function exactKeys(value,keys,code){requireValue(value!==null&&typeof value==='object'&&!Array.isArray(value)
  &&Object.keys(value).sort().join('\n')===[...keys].sort().join('\n'),code);}
export function relative(value){return typeof value==='string'&&value.length>0&&value.length<=1024&&!value.startsWith('/')
  &&!value.includes('\\')&&value.split('/').every(p=>/^[A-Za-z0-9._@+-]+$/.test(p)&&!['.','..','.env','.npmrc','.netrc','.git','config','credentials','secrets'].includes(p)
    &&!['.session','.pem','.key'].some(s=>p.endsWith(s)));}
export function strict(bytes){requireValue(Buffer.isBuffer(bytes)&&bytes.length>0&&bytes.length<=8*1024*1024,'json_budget');
  const value=JSON.parse(bytes.toString('utf8'));
  // Canonical private intake JSON also rejects duplicate keys and nonfinite or
  // precision-losing numbers, rather than trusting JSON.parse last-key wins.
  requireValue(Buffer.from(JSON.stringify(value,null,2)+'\n').equals(bytes),'noncanonical_json');return value;}
export async function regular(file,limit){
  requireValue(path.isAbsolute(file)&&await fs.realpath(file)===file,'noncanonical_file');
  const before=await fs.lstat(file);requireValue(before.isFile()&&!before.isSymbolicLink()&&before.nlink===1&&before.size<=limit,'file_kind_or_budget');
  const handle=await fs.open(file,constants.O_RDONLY|(constants.O_NOFOLLOW??0));
  try {const bytes=await handle.readFile();const after=await handle.stat(),named=await fs.lstat(file);
    const key=s=>[s.dev,s.ino,s.size,s.mtimeMs,s.ctimeMs,s.nlink].join(':');
    requireValue(bytes.length===before.size&&bytes.length<=limit&&key(before)===key(after)&&key(before)===key(named),'file_changed');return bytes;
  } finally {await handle.close();}
}
export async function actualNodeIdentity(){
  // This fixed kernel link always names this process's executable, including a
  // Host-sealed memfd. No caller path or other process may select the identity.
  requireValue(process.platform==='linux','native_profile_linux_required');
  const handle=await fs.open('/proc/self/exe',constants.O_RDONLY);
  try{
    const before=await handle.stat();requireValue(before.isFile()&&before.size>0&&before.size<=256*1024*1024,'node_fd_kind_or_budget');
    const hash=createHash('sha256');let offset=0;const buffer=Buffer.alloc(65536);
    while(offset<=before.size){const {bytesRead}=await handle.read(buffer,0,Math.min(buffer.length,before.size+1-offset),offset);
      if(bytesRead===0)break;offset+=bytesRead;requireValue(offset<=before.size,'node_fd_growth');hash.update(buffer.subarray(0,bytesRead));}
    const after=await handle.stat();const key=x=>[x.dev,x.ino,x.size,x.mtimeMs,x.ctimeMs,x.nlink].join(':');
    requireValue(offset===before.size&&key(before)===key(after),'node_fd_changed');
    return {sha256:hash.digest('hex'),bytes:offset,version:process.version,identity_source:'linux-self-executable-fd'};
  }finally{await handle.close();}
}
async function fresh(file,bytes){const handle=await fs.open(file,'wx',0o600);try{await handle.writeFile(bytes);await handle.sync();}finally{await handle.close();}}
const jsonBytes=value=>Buffer.from(JSON.stringify(value,null,2)+'\n');
export function validateLock(lock){
  exactKeys(lock,['schema','hyperframes_version','hyperframes_commit','node_version','node_sha256','browser_version','ffmpeg_version','ffprobe_version','files'],'runtime_lock_shape');
  requireValue(lock.schema==='semwright.hyperframes.runtime-lock/1'&&lock.hyperframes_version===VERSION&&lock.hyperframes_commit===COMMIT
    &&/^v(?:2[2-9]|[3-9][0-9])\.\d+\.\d+$/.test(lock.node_version)&&digest(lock.node_sha256),'runtime_identity');
  for(const key of ['browser_version','ffmpeg_version','ffprobe_version'])requireValue(typeof lock[key]==='string'&&lock[key].length>0&&lock[key].length<=512&&!/[\x00-\x1f\x7f]/.test(lock[key]),'runtime_version');
  requireValue(Array.isArray(lock.files)&&lock.files.length>0&&lock.files.length<=30000,'runtime_roster');
  const names=new Set(),roles=new Map();let total=0;
  for(const file of lock.files){exactKeys(file,['path','sha256','bytes','role'],'runtime_file_shape');
    requireValue(relative(file.path)&&file.path!=='runtime-lock.json'&&!names.has(file.path)&&digest(file.sha256)
      &&Number.isSafeInteger(file.bytes)&&file.bytes>0&&file.bytes<=512*1024*1024
      &&['runner','hyperframes-cli','package-lock','browser','ffmpeg','ffprobe','dependency'].includes(file.role),'runtime_file_identity');
    names.add(file.path);total+=file.bytes;if(file.role!=='dependency'){requireValue(!roles.has(file.role),'runtime_role_duplicate');roles.set(file.role,file);}
  }
  requireValue(total<=2*1024*1024*1024&&roles.size===6,'runtime_closure_budget');
  requireValue(roles.get('runner').path==='semwright-hyperframes-runner.mjs'
    &&roles.get('hyperframes-cli').path==='node_modules/hyperframes/bin/hyperframes.mjs'
    &&roles.get('package-lock').path==='package-lock.json'
    &&lock.files.some(f=>f.path==='origin/hyperframes-0.8.181.json'&&f.sha256===NPM_METADATA_SHA256&&f.bytes===3553&&f.role==='dependency')
    &&lock.files.some(f=>f.path==='node_modules/hyperframes/package.json'&&f.bytes<=1024*1024&&f.role==='dependency'),'runtime_entrypoint');return roles;
}
export function deepEqual(a,b){
  if(a===null||b===null||typeof a!=='object'||typeof b!=='object')return Object.is(a,b);
  if(Array.isArray(a)!==Array.isArray(b))return false;
  const keys=Object.keys(a).sort(),other=Object.keys(b).sort();
  return keys.length===other.length&&keys.every((k,i)=>k===other[i]&&deepEqual(a[k],b[k]));
}
export function validateRequest(request){
  exactKeys(request,['schema','fingerprint','runtime_lock_sha256','attempt_id','source'],'render_request_shape');
  requireValue(request.schema==='semwright.hyperframes.render-request/1'&&digest(request.fingerprint)&&digest(request.runtime_lock_sha256)
    &&slug(request.attempt_id),'render_request_identity');
  const source=request.source;
  exactKeys(source,['fingerprint','source_sha256','source_bytes','html_sha256','html_bytes','compiler','hyperframes_version','hyperframes_commit','composition'],'render_source_shape');
  requireValue(source.fingerprint===request.fingerprint&&digest(source.source_sha256)&&digest(source.html_sha256)
    &&Number.isSafeInteger(source.source_bytes)&&source.source_bytes>0&&source.source_bytes<=SOURCE_LIMIT
    &&Number.isSafeInteger(source.html_bytes)&&source.html_bytes>0&&source.html_bytes<=SOURCE_LIMIT
    &&source.compiler==='semwright-hyperframes-rect-clips-v3'&&source.hyperframes_version===VERSION&&source.hyperframes_commit===COMMIT,'render_source_identity');
  const c=source.composition;
  exactKeys(c,['schema','binding','width','height','fps','frame_count','background','clips'],'composition_shape');
  requireValue(c&&c.schema==='semwright.hyperframes.composition/1'&&c.fps===30&&Number.isSafeInteger(c.frame_count)&&c.frame_count>0&&c.frame_count<=300
    &&c.frame_count%30===0&&Number.isSafeInteger(c.width)&&c.width>=16&&c.width<=1920&&c.width%2===0
    &&Number.isSafeInteger(c.height)&&c.height>=16&&c.height<=1080&&c.height%2===0,'render_clock');return source;
}
export function nativeArguments(cli,bundle,output){return [cli,'render',bundle,'--composition','.',
  '--format','png-sequence','--fps','30','--workers','1','--no-browser-gpu','--no-best-effort','--sdr','--strict','--quiet','--output',output];}
async function verifyNamespace(root,files){
  const names=new Set(files.map(f=>f.path)),dirs=new Set(['']);
  for(const name of names){let parent=path.posix.dirname(name);while(parent!=='.'){dirs.add(parent);parent=path.posix.dirname(parent);}}
  for(const dir of dirs){const full=path.join(root,dir);requireValue(await fs.realpath(full)===full,'runtime_directory_redirect');
    const entries=await fs.readdir(full);requireValue(entries.length<=30001,'runtime_namespace_budget');
    for(const name of entries){const rel=dir?dir+'/'+name:name;
      requireValue(rel==='runtime-lock.json'||names.has(rel)||dirs.has(rel),'unlisted_runtime_entry');
    }
  }
}
async function runProcess(executable,argv,env,cwd,prefix){
  // Exactly one child per call. No polling by relaunch and no command fallback.
  let out=[],err=[],outSize=0,errSize=0,over=false;
  const result=await new Promise((resolve,reject)=>{
    const child=spawn(executable,argv,{env,cwd,stdio:['ignore','pipe','pipe']});
    const capture=(target,chunk,isOut)=>{if(isOut)outSize+=chunk.length;else errSize+=chunk.length;
      if(outSize>MAX_LOG||errSize>MAX_LOG){over=true;child.kill('SIGTERM');return;}target.push(chunk);};
    child.stdout.on('data',b=>capture(out,b,true));child.stderr.on('data',b=>capture(err,b,false));
    child.once('error',reject);child.once('close',(code,signal)=>resolve({code,signal}));
  });
  const stdout=Buffer.concat(out),stderr=Buffer.concat(err);
  await fresh(prefix+'.stdout',stdout);await fresh(prefix+'.stderr',stderr);
  const record={argv_sha256:sha(jsonBytes([executable,...argv])),exit_code:result.code,signal:result.signal,
    stdout_sha256:sha(stdout),stdout_bytes:stdout.length,stderr_sha256:sha(stderr),stderr_bytes:stderr.length,
    capture_complete:!over,automatic_retry:false};
  await fresh(prefix+'.json',jsonBytes(record));requireValue(!over,'native_log_budget');return {record,stdout,stderr};
}
export function pngDimensions(bytes){requireValue(bytes.length>=33&&bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))
  &&bytes.readUInt32BE(8)===13&&bytes.subarray(12,16).toString('ascii')==='IHDR','native_png_header');
  return {width:bytes.readUInt32BE(16),height:bytes.readUInt32BE(20)};}
export function frameNames(names,count){
  requireValue(Array.isArray(names)&&names.length===count,'native_frame_count');
  // hyperframes ae02e5 encodeStage + formatExportFrameName: export starts at frame_000001.png.
  // The original CLI namespace is retained without renaming or fallback.
  const sorted=[...names].sort();
  for(let i=0;i<sorted.length;i++){
    requireValue(sorted[i]==='frame_'+String(i+1).padStart(6,'0')+'.png','native_frame_sequence');
  }return sorted;
}
export async function main(){
  let output=null;
  try{
    requireValue(process.argv.length===4,'closed_adapter_arguments');
    const [runtimeRoot,projectRoot,outputRoot]=process.argv.slice(1);
    for(const root of [runtimeRoot,projectRoot,outputRoot])requireValue(path.isAbsolute(root)&&await fs.realpath(root)===root,'canonical_mount');
    let chunks=[],length=0;for await(const chunk of process.stdin){length+=chunk.length;requireValue(length<=1024*1024,'stdin_budget');chunks.push(chunk);}
    const request=JSON.parse(Buffer.concat(chunks).toString('utf8'));const source=validateRequest(request);
    const lockBytes=await regular(path.join(runtimeRoot,'runtime-lock.json'),8*1024*1024);
    requireValue(sha(lockBytes)===request.runtime_lock_sha256,'runtime_lock_pin');const lock=strict(lockBytes);const roles=validateLock(lock);
    await verifyNamespace(runtimeRoot,lock.files);
    for(const file of lock.files){const bytes=await regular(path.join(runtimeRoot,file.path),file.bytes);
      requireValue(bytes.length===file.bytes&&sha(bytes)===file.sha256,'runtime_file_pin');}
    const packageLock=JSON.parse((await regular(path.join(runtimeRoot,'package-lock.json'),8*1024*1024)).toString('utf8'));
    const published=packageLock.packages?.['node_modules/hyperframes'];
    requireValue(published?.version===VERSION&&published?.resolved==='https://registry.npmjs.org/hyperframes/-/hyperframes-0.8.181.tgz'
      &&published?.integrity===NPM_INTEGRITY,'public_distribution_lock');
    const node=await actualNodeIdentity();
    requireValue(node.sha256===lock.node_sha256&&node.version===lock.node_version,'actual_node_identity');
    const executable='/proc/self/exe';
    const packageJson=JSON.parse((await regular(path.join(runtimeRoot,'node_modules/hyperframes/package.json'),SOURCE_LIMIT)).toString('utf8'));
    requireValue(packageJson.name==='hyperframes'&&packageJson.version===VERSION,'actual_cli_package_version');
    const bundle=path.join(projectRoot,'source-'+request.fingerprint);
    const savedSource=await regular(path.join(bundle,'semwright-hyperframes.json'),SOURCE_LIMIT),html=await regular(path.join(bundle,'index.html'),SOURCE_LIMIT);
    requireValue(savedSource.length===source.source_bytes&&sha(savedSource)===source.source_sha256
      &&html.length===source.html_bytes&&sha(html)===source.html_sha256,'original_native_source_binding');
    // JSON property order is not a source identity: exact bytes were pinned above.
    const sourceValue=JSON.parse(savedSource.toString('utf8'));
    requireValue(deepEqual(sourceValue,source.composition),'source_composition_differs');
    output=path.join(outputRoot,'render-'+request.attempt_id);await fs.mkdir(output,{mode:0o700});
    const cache=path.join(output,'private-cache'),temp=path.join(output,'private-temp');await fs.mkdir(cache,{mode:0o700});await fs.mkdir(temp,{mode:0o700});
    const absolute=role=>path.join(runtimeRoot,roles.get(role).path);
    const env={PATH:path.dirname(absolute('ffmpeg')),LANG:'C.UTF-8',LC_ALL:'C.UTF-8',
      CI:'true',NO_COLOR:'1',HYPERFRAMES_NO_TELEMETRY:'1',DO_NOT_TRACK:'1',
      HYPERFRAMES_BROWSER_PATH:absolute('browser'),HYPERFRAMES_FFMPEG_PATH:absolute('ffmpeg'),HYPERFRAMES_FFPROBE_PATH:absolute('ffprobe'),
      XDG_CACHE_HOME:cache,TMPDIR:temp,HYPERFRAMES_EXTRACT_CACHE_DIR:'disabled'};
    // Retain Host's existing owner home. Never repurpose HOME or forward the
    // owner environment, credentials, NODE_OPTIONS or unpinned browser paths.
    if(process.env.HOME)env.HOME=process.env.HOME;
    const versions={node:process.version,hyperframes:packageJson.version};
    for(const role of ['browser','ffmpeg','ffprobe']){
      const flag=role==='browser'?'--version':'-version';
      const measured=await runProcess(absolute(role),[flag],env,output,path.join(output,'preflight-'+role));
      const first=measured.stdout.toString('utf8').trim().split(/\r?\n/)[0];
      requireValue(measured.record.exit_code===0&&measured.record.signal===null&&first===lock[role+'_version'],'actual_dependency_version');versions[role]=first;
    }
    const argv=nativeArguments(absolute('hyperframes-cli'),bundle,path.join(output,'frames'));
    await fresh(path.join(output,'native-command.intent.json'),jsonBytes({schema:'semwright.hyperframes.native-command/1',
      argv:[executable,...argv],source_fingerprint:source.fingerprint,runtime_lock_sha256:request.runtime_lock_sha256,automatic_retry:false}));
    const native=await runProcess(executable,argv,env,bundle,path.join(output,'native-command'));
    requireValue(native.record.exit_code===0&&native.record.signal===null,'native_cli_not_completed');
    requireValue((await regular(path.join(bundle,'semwright-hyperframes.json'),SOURCE_LIMIT)).equals(savedSource)
      &&(await regular(path.join(bundle,'index.html'),SOURCE_LIMIT)).equals(html),'native_source_changed_during_render');
    const names=frameNames(await fs.readdir(path.join(output,'frames')),source.composition.frame_count),frames=[];
    for(let i=0;i<names.length;i++){
      const bytes=await regular(path.join(output,'frames',names[i]),16*1024*1024);const dimensions=pngDimensions(bytes);
      requireValue(dimensions.width===source.composition.width&&dimensions.height===source.composition.height,'native_png_dimensions');
      frames.push({index:i,file:'frames/'+names[i],bytes:bytes.length,sha256:sha(bytes),...dimensions});
    }
    const receipt={schema:'semwright.hyperframes.native-render/1',status:'completed',attempt_id:request.attempt_id,
      fingerprint:source.fingerprint,source_sha256:source.source_sha256,html_sha256:source.html_sha256,
      runtime_lock_sha256:request.runtime_lock_sha256,hyperframes_version:VERSION,hyperframes_commit:COMMIT,
      frame_count:frames.length,fps:30,frames,native_command:native.record,versions,source_unchanged:true,
      pixel_decoding_performed:false,automatic_retry:false};
    await fresh(path.join(output,'native-render-receipt.json'),jsonBytes(receipt));
    process.stdout.write(JSON.stringify({schema:'semwright.hyperframes.adapter-result/1',status:'completed',
      attempt_id:request.attempt_id,fingerprint:source.fingerprint,frames:frames.length,automatic_retry:false})+'\n');
  }catch(error){
    if(output){try{await fresh(path.join(output,'native-render.failure.json'),jsonBytes({schema:'semwright.hyperframes.native-failure/1',
      status:'NOT_VERIFIED',error_type:error?.constructor?.name??'Error',automatic_retry:false,originals_retained:true}));}catch{}}
    process.stderr.write('hyperframes-adapter=NOT_VERIFIED; automatic_retry=false\n');process.exitCode=1;
  }
}
