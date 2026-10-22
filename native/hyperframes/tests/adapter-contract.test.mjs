import test from 'node:test';
import assert from 'node:assert/strict';
import {VERSION,COMMIT,NPM_METADATA_SHA256,sha,relative,strict,validateLock,validateRequest,nativeArguments,pngDimensions,frameNames,deepEqual,actualNodeIdentity} from '../semwright-hyperframes-runner.mjs';
const clone=value=>structuredClone(value),pin='0'.repeat(64);
function runtime(){return {schema:'semwright.hyperframes.runtime-lock/1',hyperframes_version:VERSION,hyperframes_commit:COMMIT,
 node_version:'v24.19.0',node_sha256:pin,browser_version:'Chrome 131.0.0.0',ffmpeg_version:'ffmpeg test',ffprobe_version:'ffprobe test',
 files:[['semwright-hyperframes-runner.mjs','runner'],['node_modules/hyperframes/bin/hyperframes.mjs','hyperframes-cli'],
 ['package-lock.json','package-lock'],['chrome/chrome','browser'],['ffmpeg/ffmpeg','ffmpeg'],['ffmpeg/ffprobe','ffprobe'],['node_modules/hyperframes/package.json','dependency']].map(([path,role])=>({path,role,sha256:pin,bytes:1})).concat([{path:'origin/hyperframes-0.8.181.json',role:'dependency',sha256:NPM_METADATA_SHA256,bytes:3553}])};}
function request(){return {schema:'semwright.hyperframes.render-request/1',fingerprint:pin,runtime_lock_sha256:pin,attempt_id:'hf-original',
 source:{fingerprint:pin,source_sha256:pin,source_bytes:100,html_sha256:pin,html_bytes:100,compiler:'semwright-hyperframes-rect-clips-v3',
 hyperframes_version:VERSION,hyperframes_commit:COMMIT,composition:{schema:'semwright.hyperframes.composition/1',binding:{},width:640,height:360,fps:30,frame_count:60,background:'#101820',clips:[]}}};}
test('closed admitted runtime shape is accepted',()=>assert.equal(validateLock(runtime()).size,6));
for(const [label,edit] of [
 ['missing npm origin',r=>r.files=r.files.filter(f=>f.path!=='origin/hyperframes-0.8.181.json')],
 ['wrong published metadata',r=>r.files.find(f=>f.path==='origin/hyperframes-0.8.181.json').sha256=pin],
 ['missing CLI package identity',r=>r.files=r.files.filter(f=>f.path!=='node_modules/hyperframes/package.json')],
 ['wrong version',r=>r.hyperframes_version='latest'],['wrong source',r=>r.hyperframes_commit='1'.repeat(40)],
 ['old Node',r=>r.node_version='v20.19.0'],['invalid node pin',r=>r.node_sha256='unknown'],
 ['extra input',r=>r.command='npm install'],['missing browser',r=>r.files=r.files.filter(f=>f.role!=='browser')],
 ['duplicate role',r=>r.files.push({path:'other/chrome',role:'browser',sha256:pin,bytes:1})],
 ['duplicate path',r=>r.files.push({...r.files[0],role:'dependency'})],['zero file',r=>r.files[0].bytes=0],
 ['traversal',r=>r.files[0].path='../run.mjs'],['unreviewed entrypoint',r=>r.files[0].path='arbitrary.mjs'],
 ['extra file field',r=>r.files[0].execute='sh'],['credential member',r=>r.files.push({path:'private/motionwright.session',role:'dependency',sha256:pin,bytes:1})]
])test('runtime rejects '+label,()=>{const r=runtime();edit(r);assert.throws(()=>validateLock(r));});
test('request source exact supported clock accepted',()=>assert.equal(validateRequest(request()).composition.frame_count,60));
for(const [label,edit] of [['extra command',r=>r.argv=['npm','install']],['missing pin',r=>r.runtime_lock_sha256=''],
 ['unknown source field',r=>r.source.path='/etc/passwd'],['unbounded attempt',r=>r.attempt_id='../escape'],
 ['fractional clock',r=>r.source.composition.frame_count=31],['odd dimensions',r=>r.source.composition.width=641],
 ['unsupported fps',r=>r.source.composition.fps=60],['unknown composition field',r=>r.source.composition.script='fetch("https://example.com")']])
 test('render refuses '+label,()=>{const r=request();edit(r);assert.throws(()=>validateRequest(r));});
test('canonical intake rejects duplicate keys and unsafe numeric spelling',()=>{
 for(const raw of ['{"x":1,"x":2}','{"x":9007199254740993}','{"x":1e999}','{"x":NaN}'])assert.throws(()=>strict(Buffer.from(raw)));
 assert.deepEqual(strict(Buffer.from('{\n  "x": 1\n}\n')),{x:1});
});
test('runtime paths reject secret, redirect and traversal before read',()=>{
 for(const p of ['/etc/passwd','../x','a/../../x','a\\b','.env','a/.npmrc','x.key','x.pem','runtime/motionwright.session'])assert.equal(relative(p),false,p);
 assert.equal(relative('node_modules/hyperframes/dist/index.js'),true);
});
test('native arguments preserve original input and output with no source-provided flags',()=>{
 assert.deepEqual(nativeArguments('/pinned/cli.mjs','/original/source','/private/frames'),['/pinned/cli.mjs','render','/original/source','--composition','.',
 '--format','png-sequence','--fps','30','--workers','1','--no-browser-gpu','--no-best-effort','--sdr','--strict','--quiet','--output','/private/frames']);
});
test('PNG dimensions are inspected without claiming a decode',()=>{
 const b=Buffer.alloc(33);Buffer.from([137,80,78,71,13,10,26,10]).copy(b);b.writeUInt32BE(13,8);b.write('IHDR',12);b.writeUInt32BE(640,16);b.writeUInt32BE(360,20);
 assert.deepEqual(pngDimensions(b),{width:640,height:360});b[1]=0;assert.throws(()=>pngDimensions(b));
});
test('frame sequence refuses gaps, duplicate and non-PNG output',()=>{
 for(const n of [['frame_000001.png','frame_000003.png'],['frame_000001.png','frame_000001.png'],['frame_000001.png','secret.json']])assert.throws(()=>frameNames(n,2));
 assert.throws(()=>frameNames(['frame_000001.png'],2));
});
test('source semantic comparison retains values and array order across JSON property order',()=>{
 assert.equal(deepEqual({a:1,b:[{x:'same',y:2}]},{b:[{y:2,x:'same'}],a:1}),true);
 assert.equal(deepEqual({a:[1,2]},{a:[2,1]}),false);assert.equal(deepEqual({a:1},{a:'1'}),false);
});
test('fixed executable FD supplies an actual digest, not a trusted request claim',async()=>{
 const id=await actualNodeIdentity();assert.match(id.sha256,/^[0-9a-f]{64}$/);assert.equal(id.version,process.version);assert.ok(id.bytes>0);
 assert.equal(id.identity_source,'linux-self-executable-fd');assert.equal(id.sha256===sha(Buffer.from('not executable')),false);
});

test('exact upstream one-based exported frame namespace is required',()=>{
 assert.deepEqual(frameNames(['frame_000002.png','frame_000001.png'],2),['frame_000001.png','frame_000002.png']);
 for(const names of [
  ['frame_000000.png','frame_000001.png'],
  ['frame-000000.png','frame-000001.png'],
  ['000000.png','000001.png'],
  ['frame_00000.png','frame_00001.png'],
 ])assert.throws(()=>frameNames(names,2));
});
