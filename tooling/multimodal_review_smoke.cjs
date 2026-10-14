#!/usr/bin/env node
/** Lightweight fallback using an explicitly selected cached TypeScript compiler.
 * No installation, emit files, renderer, browser or native authority. Full tests remain in CI.
 * Usage: node tooling/multimodal_review_smoke.cjs <path-to-typescript-library.js>
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const cp = require('node:child_process');
const root = path.resolve(__dirname, '..');
const compiler = process.argv[2];
if (!compiler || !path.isAbsolute(compiler) || !fs.statSync(compiler).isFile()) {
  throw new Error('An explicit existing cached TypeScript library path is required; nothing is installed.');
}
const ts = require(compiler);
const sourceHashes = new Map();
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const relative = file => path.relative(root, file).split(path.sep).join('/');
function transpile(file) {
  const source = fs.readFileSync(file, 'utf8');
  sourceHashes.set(relative(file), hash(Buffer.from(source)));
  const result = ts.transpileModule(source, { fileName: file, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } });
  const errors = (result.diagnostics || []).filter(d => d.category === ts.DiagnosticCategory.Error);
  if (errors.length) throw new Error(relative(file) + ': ' + errors.map(d => ts.flattenDiagnosticMessageText(d.messageText, ' ')).join('; '));
  return result.outputText;
}
const syntaxFiles = [
  'nativeInspectionAnalysis.ts', 'waveformReview.ts', 'NativeInspectionAnalysisPanel.tsx',
  'WaveformReviewPanel.tsx', 'NativeInspectionWorkbench.tsx', 'AudioWorkspace.tsx',
  'nativeInspectionAnalysis.test.ts', 'waveformReview.test.ts',
  '../tests/multimodal-review.spec.ts',
];
for (const file of syntaxFiles) transpile(path.join(root, 'apps/studio/src', file));
// Execute only the pure modules and their ordinary relative TS dependencies in this process.
require.extensions['.ts'] = (module, filename) => {
  const allowed = path.join(root, 'apps/studio/src') + path.sep;
  if (!path.resolve(filename).startsWith(allowed)) throw new Error('TypeScript dependency outside the selected source tree.');
  module._compile(transpile(filename), filename);
};
const { fixtureProject } = require('../apps/studio/src/fixture.ts');
const inspection = require('../apps/studio/src/nativeInspection.ts');
const visual = require('../apps/studio/src/nativeInspectionAnalysis.ts');
const audio = require('../apps/studio/src/waveformReview.ts');
const checks = [];
function check(name, action) {
  try { action(); checks.push({ name, outcome: 'passed' }); }
  catch (error) { checks.push({ name, outcome: 'failed', reason: error.message }); }
}
function nativeFixture() {
  const project = structuredClone(fixtureProject), scene = project.scenes[0], profile = project.deliverables[0];
  const evidence = { project_resource: 'project:' + project.id, generation: project.generation,
    revision: project.revision, deliverable_id: profile.id, frame_rate: { num: 30, den: 1 },
    segments: [{ segment_id: 'synthetic-smoke', scene_ids: project.scenes.slice(0, 2).map(s => s.id), frame_count: 450,
      plan_ref: 'synthetic-only', fingerprint: 'synthetic-only', job_ref: 'synthetic-only', artifact: {}, verification: {} }],
    preview: [{ segment_id: 'synthetic-smoke', scene_ids: project.scenes.slice(0, 2).map(s => s.id), frame_count: 450, token: 'PRIVATE_TEST_GRANT' }] };
  const overview = inspection.planNativeInspection(project, scene, profile, evidence);
  assert.ok(overview);
  const plan = visual.nativeInspectionWindow(overview, overview.samples[0].frameIndex, 3);
  const samples = plan.samples.map(s => ({ frameIndex: s.frameIndex, timelineTime: s.timelineTime,
    pngSha256: 'ab'.repeat(32), width: plan.width, height: plan.height,
    thumbnail: { width: 2, height: 2, pixels: new Uint8ClampedArray([20,30,40,255,20,30,40,255,20,30,40,255,20,30,40,255]) } }));
  return { project, overview, plan, samples };
}
function audioFixture() {
  const project = structuredClone(fixtureProject);
  const track = { id: 'synthetic-track', asset_id: 'synthetic-asset', label: 'Synthetic take', sample_rate_hz: 8000,
    channels: 2, source_sha256: 'ab'.repeat(32), measured_duration: { num: '3', den: '10' }, loudness_lufs: null, true_peak_dbfs: null };
  project.audio.voice_tracks = [track]; project.audio.active_voice_track_id = track.id;
  const page = { schema: 'motionwright.waveform-page.v1', algorithm: 'sample-peak-max-abs-v1',
    source_sha256: track.source_sha256, sample_rate_hz: 8000, channels: 2, frames_per_peak: 400,
    total_frames: 2400, peak_count: 6, page_index: 0, page_size: 6, start_peak: 0,
    peaks: [.1, 1, 1, 0, 0, 0], has_previous: false, has_next: false };
  return { project, track, page, key: audio.waveformReviewKey(project, track, 0, 6) };
}
check('current existing native plan is reusable without a new grant', () => {
  const { overview, plan } = nativeFixture();
  assert.equal(overview.samples.at(-1).frameIndex, 209);
  assert.deepEqual(plan.samples.map(s => s.frameIndex), [0,1,2]);
  assert.ok(plan.samples.every(s => s.token === 'PRIVATE_TEST_GRANT'));
});
check('native expectation is opt-in and never produces an approval', () => {
  const { plan, samples } = nativeFixture(), r = visual.analyzeNativeInspection(plan, samples);
  assert.equal(r.candidates.length, 0); assert.equal(r.creativeApproval, 'not_assessed'); assert.equal(r.effectsVerdict, 'not_issued');
  assert.equal(visual.analyzeNativeInspection(plan, samples, true).candidates[0].code, 'expected_motion_unchanged');
});
check('adjacent A-B-A localizes the center only', () => {
  const { plan, samples } = nativeFixture(); samples[1].thumbnail.pixels.fill(255);
  const r = visual.analyzeNativeInspection(plan, samples); assert.equal(r.candidates[0].anchorFrame, 1);
  assert.equal(r.candidates[0].code, 'isolated_thumbnail_change');
});
check('gaps cannot be diagnosed as adjacent temporal failures', () => {
  const { plan, samples } = nativeFixture();
  for (let i=0;i<3;i++) { plan.samples[i].frameIndex=i*2; samples[i].frameIndex=i*2; }
  samples[1].thumbnail.pixels.fill(255);
  const r=visual.analyzeNativeInspection(plan,samples,true); assert.equal(r.candidates.length,0); assert.equal(r.coverage.temporalGaps,2);
});
check('changed alpha is localized in thumbnail coordinates', () => {
  const { samples }=nativeFixture();samples[1].thumbnail.pixels[15]=0;
  const r=visual.thumbnailDifference(samples[0],samples[1]); assert.equal(r.changedPixels,1); assert.deepEqual(r.changedBox,{x:1,y:1,width:1,height:1});
});
for (const first of [-1,210,1.5,NaN,Infinity]) check('out-of-scene window rejected: '+String(first),()=>{
  const {overview}=nativeFixture();assert.throws(()=>visual.nativeInspectionWindow(overview,first));
});
check('partial native read batch rejected',()=>{const {plan,samples}=nativeFixture();assert.throws(()=>visual.analyzeNativeInspection(plan,samples.slice(1)));});
check('native image source mismatches rejected',()=>{const {plan,samples}=nativeFixture();samples[0].pngSha256='bad';assert.throws(()=>visual.analyzeNativeInspection(plan,samples));});
check('recorded candidate reuses AddReview and never leaks the runtime token',()=>{
  const {project,plan,samples}=nativeFixture(), before=structuredClone(project), c=visual.analyzeNativeInspection(plan,samples,true).candidates[0];
  const change=visual.nativeAnalysisReviewChange(project,plan,samples,c.key,true);
  assert.equal(change.type,'add_review');assert.ok(!change.body.includes('PRIVATE_TEST_GRANT'));assert.equal(JSON.parse(change.body).repair_applied,false);
  assert.deepEqual(project,before);assert.throws(()=>visual.nativeAnalysisReviewChange({...project,revision:project.revision+1},plan,samples,c.key,true));
});
check('unknown candidate cannot be admitted',()=>{const {project,plan,samples}=nativeFixture();assert.throws(()=>visual.nativeAnalysisReviewChange(project,plan,samples,'forged',true));});
check('NTSC frame time remains a rational string',()=>{
  const {overview}=nativeFixture();overview.frameRate={num:30000,den:1001};overview.samples[0].timelineTime={num:'7',den:'1'};
  assert.deepEqual(visual.nativeInspectionWindow(overview,1,1).samples[0].timelineTime,{num:'211001',den:'30000'});
});
check('measured waveform proxy validates source and duration',()=>{const f=audioFixture();audio.validateWaveformReviewPage(f.project,f.track,f.page,0,6);});
for (const mismatch of [{source_sha256:'cd'.repeat(32)},{page_index:1},{start_peak:1},{has_next:true},{total_frames:2399},{peaks:[0,0]},{peaks:[NaN,1,1,0,0,0]}]) {
  check('waveform mismatch rejected: '+Object.keys(mismatch)[0],()=>{const f=audioFixture();assert.throws(()=>audio.validateWaveformReviewPage(f.project,f.track,{...f.page,...mismatch},0,6));});
}
check('peak proxy is not true peak, and silence requires expectation',()=>{
  const f=audioFixture(),plain=audio.analyzeWaveformPage(f.project,f.track,f.page,0,6),r=audio.analyzeWaveformPage(f.project,f.track,f.page,0,6,true);
  assert.equal(plain.candidates.length,1);assert.equal(r.candidates.length,2);assert.equal(r.truePeak,'not_measured');
  const quiet=r.candidates.find(c=>c.code==='expected_sound_quiet_proxy');assert.deepEqual(quiet.start,{num:'3',den:'20'});assert.deepEqual(quiet.end,{num:'3',den:'10'});
});
check('waveform review retains take-relative evidence without a fake timeline anchor',()=>{
  const f=audioFixture(),c=audio.analyzeWaveformPage(f.project,f.track,f.page,0,6).candidates[0];
  const change=audio.waveformReviewChange(f.project,f.track,f.page,0,6,false,c.key,f.key);
  assert.equal(change.start,null);assert.equal(change.end,null);assert.equal(JSON.parse(change.body).coverage.coordinate_space,'voice_take_relative');
  assert.throws(()=>audio.waveformReviewChange({...f.project,generation:'changed'},f.track,f.page,0,6,false,c.key,f.key));
});
check('bounded waveform detail reports omissions',()=>{
  const f=audioFixture();f.track.measured_duration={num:'2',den:'1'};
  Object.assign(f.page,{total_frames:16000,peak_count:40,page_size:40,peaks:Array.from({length:40},(_,i)=>i%2===0?1:.2)});
  const r=audio.analyzeWaveformPage(f.project,f.track,f.page,0,40);assert.equal(r.candidates.length,16);assert.equal(r.omittedCandidates,4);
});
let stable=true;
for(const [name,digest] of sourceHashes) if(hash(fs.readFileSync(path.join(root,name)))!==digest) stable=false;
const observedHead=cp.spawnSync('git',['-C',root,'rev-parse','HEAD'],{encoding:'utf8'});
const failed=checks.filter(c=>c.outcome==='failed');
console.log(JSON.stringify({schema:'motionwright.multimodal-review-smoke/1',scope:'Pure-module synthetic checks and cached-compiler syntax check only',
  compiler:ts.version,node:process.version,syntax_files:syntaxFiles,checks,passed:checks.length-failed.length,failed:failed.length,
  source_files:Object.fromEntries(sourceHashes),source_stable_during_run:stable,
  checkout_head:observedHead.status===0?observedHead.stdout.trim():null,
  native_renderer_executed:false,browser_executed:false,full_project_typecheck:false,creative_approval:'not_assessed'},null,2));
process.exitCode=failed.length||!stable?1:0;
