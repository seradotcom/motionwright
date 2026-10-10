// Executed only by the owner-pinned Host runner after Rust validates the document.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {detectNativeSandboxMode} from './sandbox_policy.mjs';
import {spawnSync} from 'node:child_process';

const [runtimeRoot,workRoot,outputRoot,assetRoot,expectedPlanSha,sealedBrowser] = process.argv.slice(2);
if(process.argv.length!==8 || ![runtimeRoot,workRoot,outputRoot,assetRoot,sealedBrowser].every(value=>path.isAbsolute(value)) || !/^[a-f0-9]{64}$/.test(expectedPlanSha)) throw new Error('Capture requires exact Host-bound arguments');
// Static diagnostic milestones in an already owner-writable job root. No
// user text, secret values, browser stderr, URLs or system paths are logged.
const allowedPhases=new Set(['entry','plan','runtime','modules','assets','fonts','browser','page','fonts_loaded','frames','browser_namespace_denied','browser_process_limit','browser_dependency_missing','browser_executable_denied','browser_browser_crash','browser_launch_unknown','browser_binary_exec_denied','browser_binary_probe_failed','browser_binary_probe_missing_icu','browser_binary_probe_missing_sidecar','browser_binary_probe_missing_shared_library','browser_binary_probe_namespace_denied','browser_binary_probe_permission_denied','browser_binary_probe_process_limit','browser_binary_probe_signal','browser_binary_probe_ok','outer_host_bwrap_verified','chromium_userns_selected']);
function phase(name){
  if(!allowedPhases.has(name))throw new Error('Unsupported native capture phase marker');
  fs.appendFileSync(path.join(workRoot,'native-capture-phases.txt'),name+'\n',{encoding:'utf8',flag:'a'});
}
phase('entry');
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
function readBounded(root,relative,max) {
  if(!relative || path.isAbsolute(relative) || relative.split(/[\\/]/).some(p=>!p || p==='.' || p==='..')) throw new Error('Unnormalized native asset path');
  let cursor=root;
  for(const part of relative.split('/')) {cursor=path.join(cursor,part);if(fs.lstatSync(cursor).isSymbolicLink())throw new Error('Native asset path traverses a symlink');}
  const stat=fs.statSync(cursor);
  if(!stat.isFile() || stat.size<=0 || stat.size>max)throw new Error('Native asset is not a bounded regular file');
  return fs.readFileSync(cursor);
}
const planBytes=readBounded(workRoot,'plan.json',2*1024*1024);
if(hash(planBytes)!==expectedPlanSha)throw new Error('Native plan changed after driver admission');
const plan=JSON.parse(planBytes),doc=plan.document,c=doc.canvas;
phase('plan');
if(doc.version!==1 || doc.nodes.length>256 || c.frames<1 || c.frames>3600 || c.width*c.height>8294400)throw new Error('Unadmitted capture dimensions or document version');
const receiptBytes=readBounded(runtimeRoot,'runtime.json',1024*1024),receipt=JSON.parse(receiptBytes);
if(receipt.schema!==1 || receipt.hyperframes!=='0.8.143' || receipt.gsap!=='3.15.0' || receipt.playwright!=='1.55.1')throw new Error('Native runtime receipt is incompatible');
if(hash(receiptBytes)!==plan.runtime_receipt_sha256)throw new Error('Native runtime identity changed after source admission');
phase('runtime');
const require=createRequire(path.join(runtimeRoot,'package.json'));
const {chromium}=require('playwright');
const fontkit=require('fontkit');
phase('modules');
const files=new Map();
const source=readBounded(workRoot,'index.html',3*1024*1024);
files.set('/index.html',{bytes:source,type:'text/html; charset=utf-8'});
for(const [url,key,type] of [['/runtime/hyperframes.js','hyperframes_script','text/javascript'],['/runtime/gsap.js','gsap_script','text/javascript'],['/runtime/sans.woff2','sans_font','font/woff2'],['/runtime/mono.woff2','mono_font','font/woff2']]) {
  const entry=receipt.files[key];if(!entry)throw new Error('Missing runtime file receipt');
  const bytes=readBounded(runtimeRoot,entry.path,16*1024*1024);if(hash(bytes)!==entry.sha256)throw new Error('Native runtime dependency changed');
  files.set(url,{bytes,type});
}
const author=readBounded(workRoot,'authoring.js',256*1024);
files.set('/runtime/authoring.js',{bytes:author,type:'text/javascript'});
const assetById=new Map(),assetMimes={png:'image/png',jpeg:'image/jpeg',mp4:'video/mp4',woff2:'font/woff2'},extensions={png:'png',jpeg:'jpg',mp4:'mp4',woff2:'woff2'};
let assetBytes=0;
for(const asset of doc.assets) {
  if(!asset.rights.use_authorized || !/^[a-f0-9]{64}$/.test(asset.sha256))throw new Error('Asset has no explicit rights or digest');
  const bytes=readBounded(assetRoot,'sha256/'+asset.sha256.slice(0,2)+'/'+asset.sha256,64*1024*1024);assetBytes+=bytes.length;
  if(assetBytes>256*1024*1024 || hash(bytes)!==asset.sha256)throw new Error('Native asset changed or total resource budget exceeded');
  const item={bytes,type:assetMimes[asset.kind]};files.set('/assets/'+asset.sha256+'.'+extensions[asset.kind],item);assetById.set(asset.id,{...asset,bytes});
}
phase('assets');
const fonts=new Map();
const fontFaces=[];
for(const node of doc.nodes)if(node.content.kind==='text') {
  const content=node.content,key=content.font.kind==='asset'?content.font.asset_id:content.font.kind;
  if(!fonts.has(key)) {
    const fontBytes=content.font.kind==='asset'?assetById.get(key)?.bytes:files.get('/runtime/'+(key==='mono'?'mono':'sans')+'.woff2').bytes;
    if(!fontBytes)throw new Error('Missing source font bytes');
    fonts.set(key,fontkit.create(fontBytes));
    fontFaces.push({id:key,sha256:hash(fontBytes),family:fonts.get(key).familyName});
  }
  const face=fonts.get(key),cps=new Set(face.characterSet),wght=face.variationAxes?.wght;
  for(const run of content.runs) {
    for(const char of run.text)if(char.codePointAt(0)>32 && !cps.has(char.codePointAt(0)))throw new Error('Font coverage missing for node '+node.id+' at U+'+char.codePointAt(0).toString(16));
    const weight=wght?run.weight>=wght.min && run.weight<=wght.max:run.weight===(face['OS/2']?.usWeightClass ?? 400);
    if(!weight || (run.italic && !face.italicAngle))throw new Error('Requested exact font face is unavailable; no synthetic weight or italic substitution');
  }
}
phase('fonts');
const framesDir=path.join(outputRoot,'frames');fs.mkdirSync(framesDir,{recursive:false});
const observationsPath=path.join(outputRoot,'observations.ndjson');
const observationFd=fs.openSync(observationsPath,'wx',0o600);let observationBytes=0;
const diagnostics=[];const refused=[];const deadline=Date.now()+240000;
// The exact same owner-pinned browser binary is preflighted with --version
// inside the current Semwright Host confinement. This is not an installation
// or a relaxation of browser sandboxing. It distinguishes OS exec denial from
// a later Chromium sandbox/namespace startup failure, without copying stderr.
// The fixed Rust launcher matched the owner-pinned sha256 against the
// exact binary exposed by the Host's sealed ToolPath. Do not execute from
// the data-only runtime mount (noexec) even when those bytes match.
const browserBinary=sealedBrowser;
const executable=fs.statSync(browserBinary);
if(!executable.isFile() || (executable.mode&0o111)===0){
  phase('browser_binary_exec_denied');
  throw new Error('Owner-installed browser binary lacks executable permissions');
}
// Probe stdout/stderr is ephemeral inside this exact synthetic CI invocation.
// Only one fixed error CLASS crosses into journal/phase evidence; never persist
// browser stderr (it may include absolute machine paths or user content).
const probe=spawnSync(browserBinary,['--version'],{
  timeout:8000,encoding:'utf8',maxBuffer:16384,
  stdio:['ignore','pipe','pipe']
});
if(probe.error?.code==='EACCES'||probe.error?.code==='EPERM'){
  phase('browser_binary_exec_denied');
  throw new Error('Host denied execution of the exact owner-pinned browser binary');
}
if(probe.error || probe.status!==0){
  const message=String(probe.stderr??'').toLowerCase().slice(0,12000);
  const failure=probe.signal!==null?'browser_binary_probe_signal'
    :/icudtl|icu data|icu_util|invalid file descriptor to icu/.test(message)?'browser_binary_probe_missing_icu'
    :/\.pak|resource bundle|failed to load resource|v8_context_snapshot|snapshot_blob/.test(message)?'browser_binary_probe_missing_sidecar'
    :/error while loading shared libraries|cannot open shared object|libnss3|libatk|libasound|libx11|libglib/.test(message)?'browser_binary_probe_missing_shared_library'
    :/permission denied|operation not permitted|eacces|eperm/.test(message)?'browser_binary_probe_permission_denied'
    :/namespace|sandbox|userns/.test(message)?'browser_binary_probe_namespace_denied'
    :/pthread_create|resource temporarily unavailable|eagain|fork/.test(message)?'browser_binary_probe_process_limit'
    :'browser_binary_probe_failed';
  phase(failure);
  throw new Error('Owner-pinned browser executable could not complete bounded version probe: '+failure);
}
phase('browser_binary_probe_ok');
// This is a security boundary, not a performance fallback: the strictly
// confined Semwright Host already owns the namespace and denies nested
// user namespaces. Only a verified rootless AppArmor-enforced Host may
// select the outer sandbox; all other execution retains Chromium's userns.
const sandboxMode=detectNativeSandboxMode();
phase(sandboxMode==='semwright-bwrap-outer'?'outer_host_bwrap_verified':'chromium_userns_selected');
let browser;
try{
  browser=await chromium.launch({executablePath:sealedBrowser,
    headless:true,chromiumSandbox:sandboxMode==='chromium-userns',args:['--enable-logging=stderr'],timeout:30000});
}catch(error){
  // Record only one of five fixed classes. Browser launch diagnostics could
  // include machine paths, source snippets or private argv and must not be
  // copied into a persistent readback/CI artifact.
  const message=String(error?.message??'').toLowerCase().slice(0,6000);
  const category=/resource temporarily unavailable|eagain|pthread_create|fork: retry|unable to create thread/.test(message)
    ?'browser_process_limit'
    :/failed to move to new namespace|no usable sandbox|unprivileged userns|unprivileged_userns|user namespace|zygote.*sandbox|failed to set permissions/.test(message)
    ?'browser_namespace_denied'
    :/spawn.*eacces|spawn.*eperm|permission denied.*chrom|permission denied.*headless_shell/.test(message)
    ?'browser_executable_denied'
    :/error while loading shared libraries|shared object file|cannot open shared object file|executable doesn't exist|no such file or directory/.test(message)
    ?'browser_dependency_missing'
    :/target page, context or browser has been closed|browser closed|failed to launch|exited with code/.test(message)
    ?'browser_browser_crash'
    :'browser_launch_unknown';
  phase(category);
  throw new Error('Native browser launch failed inside the owner-controlled profile: '+category);
}
phase('browser');
try {
  const context=await browser.newContext({viewport:{width:c.width,height:c.height},deviceScaleFactor:1,locale:'en-US',timezoneId:'UTC',colorScheme:'light',reducedMotion:'no-preference',serviceWorkers:'block',acceptDownloads:false});
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.origin!=='http://motionwright.invalid' || request.method()!=='GET' || url.search || url.hash) {refused.push('origin-or-method');return route.abort();}
    if(url.pathname==='/favicon.ico')return route.fulfill({status:204,body:''});
    const item=files.get(url.pathname);if(!item){refused.push(url.pathname.slice(0,120));return route.abort();}
    const range=request.headers().range;
    if(range) {
      const match=/^bytes=(\d+)-(\d*)$/.exec(range);if(!match)return route.fulfill({status:416,body:''});
      const start=Number(match[1]),end=match[2]?Math.min(Number(match[2]),item.bytes.length-1):item.bytes.length-1;
      if(start>end || start>=item.bytes.length)return route.fulfill({status:416,body:''});
      return route.fulfill({status:206,contentType:item.type,headers:{'Content-Range':`bytes ${start}-${end}/${item.bytes.length}`,'Accept-Ranges':'bytes'},body:item.bytes.subarray(start,end+1)});
    }
    return route.fulfill({status:200,contentType:item.type,headers:{'Cache-Control':'no-store'},body:item.bytes});
  });
  const page=await context.newPage();page.setDefaultTimeout(15000);
  phase('page');
  page.on('popup',popup=>popup.close());page.on('pageerror',e=>{if(diagnostics.length<8)diagnostics.push(e.message.slice(0,1024));});
  await page.goto('http://motionwright.invalid/index.html',{waitUntil:'load'});
  await page.waitForFunction(()=>!!window.__playerReady && !!window.__renderReady && typeof window.__player?.renderSeek==='function' && typeof window.__mwInspect==='function');
  await page.evaluate(async()=>{
    await document.fonts.ready;
    for(const el of document.querySelectorAll('.mw-text span')){
      const style=getComputedStyle(el);const result=await document.fonts.load(`${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`,el.textContent);
      if(!result.length || !document.fonts.check(`${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`,el.textContent))throw new Error('Native font did not load');
    }
    await Promise.all([...document.images].map(img=>img.decode()));
  });
  phase('fonts_loaded');
  const duration=await page.evaluate(()=>window.__player.getDuration());
  if(Math.abs(duration-c.frames*c.rate.den/c.rate.num)>1e-7)throw new Error('HyperFrames timeline duration differs from the exact output contract');
  const captured=[];let bytesWritten=0;
  for(let frame=0;frame<c.frames;frame++) {
    if(Date.now()>deadline)throw new Error('Native capture deadline exceeded');
    const time=frame*c.rate.den/c.rate.num;
    const state=await page.evaluate(async({time,frame,videoSources})=>{
      window.__player.renderSeek(time,{suppressEvents:true,exact:true});
      await window.__hfWaitForSeekCompletion?.();
      for(const source of videoSources){
        const video=document.querySelector(`[data-mw-video="${source.id}"]`);
        if(!video)throw new Error('Native video is absent');
        if(video.readyState<1)await new Promise((resolve,reject)=>{video.addEventListener('loadedmetadata',resolve,{once:true});video.addEventListener('error',()=>reject(new Error('Native video decode failed')),{once:true});});
        const target=time+source.start;
        if(!Number.isFinite(video.duration) || target>=video.duration)throw new Error('Video source does not cover the requested exact output range');
        video.pause();
        if(Math.abs(video.currentTime-target)>1e-7)await new Promise((resolve,reject)=>{video.addEventListener('seeked',resolve,{once:true});video.addEventListener('error',()=>reject(new Error('Native video seek failed')),{once:true});video.currentTime=target;});
      }
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      return {frame,time,observed_time:window.__player.getTime(),nodes:window.__mwInspect()};
    },{time,frame,videoSources:doc.nodes.filter(n=>n.content.kind==='video').map(n=>({id:n.id,start:n.content.source_start_frame*n.content.source_rate.den/n.content.source_rate.num}))});
    if(Math.abs(state.observed_time-time)>1e-7)throw new Error('HyperFrames quantized to a different frame time');
    if(state.nodes.length!==doc.nodes.length || state.nodes.some(n=>n.truncated)){ const clipped=state.nodes.filter(n=>n.truncated).map(n=>({id:n.id,text:n.text,box:n.box,font:n.computed_font})); throw new Error('Native text clipping or source identity drift '+JSON.stringify({frame,node_count:state.nodes.length,expected:doc.nodes.length,clipped:clipped.slice(0,8)}).slice(0,2000)); }
    if(diagnostics.length || refused.length)throw new Error('Native composition requested an unadmitted operation: '+[...diagnostics,...refused].join(';').slice(0,1500));
    const bytes=await page.screenshot({type:'png',omitBackground:c.background===null,animations:'allow',caret:'hide',timeout:15000});
    bytesWritten+=bytes.length;if(bytes.length>32*1024*1024 || bytesWritten>512*1024*1024)throw new Error('Native frame artifact budget exceeded');
    const name=`frame-${String(frame).padStart(6,'0')}.png`;fs.writeFileSync(path.join(framesDir,name),bytes,{flag:'wx'});
    captured.push({frame,time:{num:String(frame*c.rate.den),den:String(c.rate.num)},relative_path:'frames/'+name,sha256:hash(bytes),bytes:bytes.length});
    const line=JSON.stringify(state)+'\n';observationBytes+=Buffer.byteLength(line);if(observationBytes>64*1024*1024)throw new Error('Readback observation budget exceeded');fs.writeSync(observationFd,line);
  }
  phase('frames');
  fs.fsyncSync(observationFd);
  const result={schema:'motionwright.hyperframes-native-frames/1',project_id:plan.project_id,generation:plan.generation,revision:plan.revision,scene_id:plan.scene_id,
    width:c.width,height:c.height,rate:c.rate,frame_count:c.frames,alpha:c.background===null,color:'srgb',source_sha256:hash(source),plan_sha256:expectedPlanSha,
    hyperframes:'0.8.143',gsap:'3.15.0',playwright:'1.55.1',browser:browser.version(),runtime_receipt_sha256:hash(receiptBytes),authoring_sha256:hash(author),font_faces:fontFaces,
    observation:{method:'native-dom-css-font-face-and-frame-time-readback/1',units:'CSS pixels/degrees/opacity/rational seconds',coverage:'all_frames',frames:c.frames,
      relative_path:'observations.ndjson',sha256:hash(fs.readFileSync(observationsPath)),limitations:['DOM boxes do not prove pixel visibility after overlap.','Technical readback is not independent aesthetic approval.','Video sampling uses native decoded media time, not a codec-independent bit-identical source frame guarantee.']},
    frames:captured,external_requests:0,sandbox_mode:sandboxMode,creative_approval:'required'};
  fs.writeFileSync(path.join(outputRoot,'frames.json'),JSON.stringify(result,null,2)+'\n',{flag:'wx'});
  console.log(JSON.stringify({frames:captured.length,source_sha256:result.source_sha256,alpha:result.alpha}));
} finally {fs.closeSync(observationFd);await browser.close();}
