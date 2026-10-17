// Native source adapter for the pinned public @hyperframes/producer API.
// The Rust driver supplies this exact module through the existing Host's stdin.
// This is not an alternate browser, encoder, scheduler or HTML compiler.
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';

const REVISION = 'fa9366915620ea9baa1d03b0eb44caaca013dc21';
const VERSION = '0.8.167';
const PROFILE = 'motionwright.hyperframes.finite-source/1';
const MAX_SOURCE = 32 * 1024 * 1024;
const MAX_RUNTIME = 1024 * 1024 * 1024;
const MAX_OUTPUT = 512 * 1024 * 1024;
const MAX_DIAGNOSTIC = 4 * 1024 * 1024;
const originalStdoutWrite = process.stdout.write.bind(process.stdout);

function reject(code) { const error = new Error(code); error.code = code; throw error; }
function ensure(value, code) { if (!value) reject(code); }
function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
function digest(value) { ensure(typeof value === 'string' && /^[0-9a-f]{64}$/.test(value), 'INVALID_DIGEST'); }
function exactKeys(value, keys) {
  ensure(value !== null && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key)), 'CLOSED_OBJECT_REQUIRED');
}
function integer(value, min, max) { ensure(Number.isSafeInteger(value) && value >= min && value <= max, 'INTEGER_BOUND'); }
function equal(a, b) {
  if (a === b) return true;
  if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false;
  const left = Object.keys(a).sort(), right = Object.keys(b).sort();
  return left.length === right.length && left.every((key,index) => key === right[index] && equal(a[key],b[key]));
}

export function relativeName(value) {
  ensure(typeof value === 'string' && value.length > 0 && value.length <= 512
    && /^[A-Za-z0-9/._@+-]+$/.test(value), 'RELATIVE_PATH_REQUIRED');
  const parts = value.split('/');
  ensure(parts.length <= 24 && parts.every(part => part && part !== '.' && part !== '..'
    && !part.endsWith('.') && !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(part)), 'PORTABLE_PATH_REQUIRED');
  return value;
}

function noLinks(target, isDirectory = false) {
  ensure(path.isAbsolute(target), 'ABSOLUTE_HOST_ROOT_REQUIRED');
  let current = target;
  for (let i = 0; i < 128; i++) {
    const stat = fs.lstatSync(current, {bigint: true});
    ensure(!stat.isSymbolicLink(), 'LINKED_PATH');
    if (current === target) ensure(isDirectory ? stat.isDirectory() : stat.isFile(), 'WRONG_FILE_KIND');
    const parent = path.dirname(current);
    if (parent === current) return;
    current = parent;
  }
  reject('ANCESTOR_LIMIT');
}

function sameStat(a, b) {
  return a.isFile() && b.isFile() && a.dev === b.dev && a.ino === b.ino && a.size === b.size
    && a.mtimeNs === b.mtimeNs && a.ctimeNs === b.ctimeNs && a.nlink === 1n && b.nlink === 1n;
}

export function readBounded(target, cap, expected) {
  noLinks(target);
  const descriptor = fs.openSync(target, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0));
  try {
    const before = fs.fstatSync(descriptor, {bigint: true});
    ensure(before.isFile() && before.nlink === 1n && before.size > 0n && before.size <= BigInt(cap), 'FILE_BYTE_BOUND');
    const initial = Number(before.size);
    const data = Buffer.alloc(initial);
    let consumed = 0;
    while (consumed < initial) {
      const count = fs.readSync(descriptor, data, consumed, Math.min(64 * 1024, initial - consumed), null);
      ensure(count > 0, 'SOURCE_TRUNCATED');
      consumed += count;
    }
    const extra = Buffer.alloc(1);
    ensure(fs.readSync(descriptor, extra, 0, 1, null) === 0, 'SOURCE_GREW');
    const after = fs.fstatSync(descriptor, {bigint: true});
    const named = fs.lstatSync(target, {bigint: true});
    ensure(sameStat(before, after) && sameStat(after, named) && !named.isSymbolicLink(), 'SOURCE_IDENTITY_CHANGED');
    if (expected !== undefined) { digest(expected); ensure(sha256(data) === expected, 'SOURCE_HASH_CHANGED'); }
    return data;
  } finally { fs.closeSync(descriptor); }
}

function hashBounded(target, cap, expected) {
  noLinks(target);
  const descriptor = fs.openSync(target, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0));
  try {
    const before = fs.fstatSync(descriptor, {bigint: true});
    ensure(before.isFile() && before.nlink === 1n && before.size > 0n && before.size <= BigInt(cap), 'FILE_BYTE_BOUND');
    const initial = Number(before.size), hash = createHash('sha256'), buffer = Buffer.alloc(1024 * 1024);
    let consumed = 0;
    for (;;) {
      const allowance = Math.min(buffer.length, initial - consumed + 1);
      const count = fs.readSync(descriptor, buffer, 0, allowance, null);
      if (count === 0) break;
      consumed += count;
      ensure(consumed <= initial, 'SOURCE_GREW');
      hash.update(buffer.subarray(0, count));
    }
    const after = fs.fstatSync(descriptor, {bigint: true}), named = fs.lstatSync(target, {bigint: true});
    ensure(consumed === initial && sameStat(before, after) && sameStat(after, named) && !named.isSymbolicLink(), 'SOURCE_IDENTITY_CHANGED');
    const actual = hash.digest('hex');
    if (expected !== undefined) { digest(expected); ensure(actual === expected, 'SOURCE_HASH_CHANGED'); }
    return {bytes: consumed, sha256: actual};
  } finally { fs.closeSync(descriptor); }
}

function atomicNew(target, bytes, cap) {
  ensure(bytes.length > 0 && bytes.length <= cap, 'ARTIFACT_BYTE_BOUND');
  const descriptor = fs.openSync(target, 'wx', 0o600);
  try {
    let written = 0;
    while (written < bytes.length) {
      const count = fs.writeSync(descriptor, bytes, written, bytes.length - written);
      ensure(count > 0, 'ARTIFACT_WRITE_INCOMPLETE'); written += count;
    }
    fs.fsyncSync(descriptor);
  } finally { fs.closeSync(descriptor); }
}

export function validatePacket(packet) {
  exactKeys(packet, ['request', 'request_sha256', 'source_name', 'operation_name', 'html_sha256', 'source_profile', 'clock', 'width', 'height', 'resource_files', 'runner_sha256', 'tool_pins']);
  exactKeys(packet.request, ['schema', 'project_id', 'generation', 'revision', 'scene_id', 'capsule_id', 'operation_id', 'source_sha256', 'canonical_dependency_sha256', 'owner_profile_sha256']);
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  for (const key of ['project_id', 'generation', 'scene_id', 'capsule_id', 'operation_id']) {
    ensure(uuid.test(packet.request[key]) && packet.request[key] !== '00000000-0000-0000-0000-000000000000', 'REQUEST_IDENTITY');
  }
  integer(packet.request.revision, 0, Number.MAX_SAFE_INTEGER);
  ensure(packet.request.schema === 'motionwright.hyperframes.source-render/1' && packet.source_profile === PROFILE, 'SOURCE_PROFILE');
  for (const value of [packet.request_sha256, packet.html_sha256, packet.runner_sha256, packet.request.source_sha256,
    packet.request.canonical_dependency_sha256, packet.request.owner_profile_sha256]) digest(value);
  ensure(packet.source_name === `mw-hf-${packet.request.source_sha256}.json`
    && packet.operation_name === `hf-${packet.request.operation_id.replaceAll('-', '')}`, 'SOURCE_NAMES');
  exactKeys(packet.clock, ['fps', 'frame_count']); exactKeys(packet.clock.fps, ['num', 'den']);
  const num = packet.clock.fps.num, den = packet.clock.fps.den;
  ensure(typeof num === 'string' && typeof den === 'string' && /^(24|25|30|50|60|24000|30000|60000)$/.test(num)
    && /^(1|1001)$/.test(den), 'EXACT_FPS');
  ensure((den === '1' && ['24','25','30','50','60'].includes(num)) || (den === '1001' && ['24000','30000','60000'].includes(num)), 'EXACT_FPS');
  integer(packet.clock.frame_count, 1, 180); integer(packet.width, 64, 1920); integer(packet.height, 64, 1080);
  ensure(packet.width % 2 === 0 && packet.height % 2 === 0
    && packet.clock.frame_count * Number(den) <= 6 * Number(num)
    && packet.width * packet.height * packet.clock.frame_count <= 64 * 1024 * 1024, 'PRE_CAPTURE_BUDGET');
  exactKeys(packet.tool_pins, ['node_sha256','chrome_sha256','ffmpeg_sha256','ffprobe_sha256']);
  for (const value of Object.values(packet.tool_pins)) digest(value);
  ensure(Array.isArray(packet.resource_files) && packet.resource_files.length <= 32, 'RESOURCE_COUNT');
  const names = new Set();
  let total = 0;
  for (const file of packet.resource_files) {
    exactKeys(file, ['name', 'bytes', 'sha256']); digest(file.sha256); integer(file.bytes, 1, 4 * 1024 * 1024);
    ensure(new RegExp(`^assets/${file.sha256}\\.(png|woff2)$`).test(file.name) && !names.has(file.name), 'RESOURCE_NAME');
    names.add(file.name); total += file.bytes;
  }
  ensure(total <= 12 * 1024 * 1024, 'RESOURCE_TOTAL');
}

export function preflightSource(packet, sourceRoot) {
  validatePacket(packet); noLinks(sourceRoot, true);
  const bytes = readBounded(path.join(sourceRoot, packet.source_name), MAX_SOURCE, packet.request.source_sha256);
  const carrier = JSON.parse(bytes.toString('utf8'));
  exactKeys(carrier, ['schema','producer_revision','document','html','resources']);
  ensure(carrier.schema === PROFILE && carrier.producer_revision === REVISION && typeof carrier.html === 'string'
    && Buffer.byteLength(carrier.html) <= 256 * 1024 && sha256(Buffer.from(carrier.html)) === packet.html_sha256, 'ADMITTED_HTML_CHANGED');
  ensure(equal(carrier.document.clock, packet.clock) && carrier.document.width === packet.width && carrier.document.height === packet.height, 'ADMITTED_CLOCK_CHANGED');
  ensure(Array.isArray(carrier.resources) && carrier.resources.length === packet.resource_files.length, 'ADMITTED_RESOURCES_CHANGED');
  const files = new Map(), byName = new Map(packet.resource_files.map(file => [file.name, file]));
  for (const resource of carrier.resources) {
    exactKeys(resource, ['kind','sha256','bytes_hex']); digest(resource.sha256);
    ensure(['png','woff2'].includes(resource.kind) && typeof resource.bytes_hex === 'string'
      && resource.bytes_hex.length <= 8 * 1024 * 1024 && resource.bytes_hex.length % 2 === 0 && /^[0-9a-f]+$/.test(resource.bytes_hex), 'RESOURCE_ENCODING');
    const name = `assets/${resource.sha256}.${resource.kind}`;
    ensure(byName.has(name) && !files.has(name), 'RESOURCE_CLOSURE');
    const data = Buffer.from(resource.bytes_hex, 'hex'), expected = byName.get(name);
    ensure(data.length === expected.bytes && sha256(data) === expected.sha256, 'RESOURCE_BYTES_CHANGED');
    files.set(name, data);
  }
  // The HTML grammar is checked by the Rust admission function. This second
  // read checks the exact admitted byte hash and resource closure; it does not
  // duplicate the authoring grammar or accept a new document from the caller.
  return {bytes, carrier, files};
}

export function verifyRuntimeClosure(runtimeRoot, expectedSha, expectedRunner) {
  noLinks(runtimeRoot, true);
  const ownerBytes = readBounded(path.join(runtimeRoot, 'owner-profile.json'), 4 * 1024 * 1024, expectedSha);
  const owner = JSON.parse(ownerBytes.toString('utf8'));
  exactKeys(owner, ['schema','producer_revision','producer_version','runner_sha256','tool_pins','chromium_major','files']);
  ensure(owner.schema === 'motionwright.hyperframes.owner-runtime/1' && owner.producer_revision === REVISION && owner.producer_version === VERSION
    && owner.runner_sha256 === expectedRunner && Array.isArray(owner.files) && owner.files.length > 0 && owner.files.length <= 20_000, 'OWNER_RUNTIME_PROFILE');
  integer(owner.chromium_major, 100, 999);
  const expected = new Map(), casefold = new Set(), expectedDirectories = new Set();
  let total = 0;
  for (const file of owner.files) {
    exactKeys(file, ['path','bytes','sha256']); relativeName(file.path); digest(file.sha256); integer(file.bytes, 1, 128 * 1024 * 1024);
    ensure(file.path !== 'owner-profile.json' && !casefold.has(file.path.toLowerCase()), 'RUNTIME_DUPLICATE_PATH');
    expected.set(file.path, file); casefold.add(file.path.toLowerCase()); total += file.bytes;
    const parts = file.path.split('/');
    for (let end = 1; end < parts.length; end++) expectedDirectories.add(parts.slice(0,end).join('/'));
    ensure(total <= MAX_RUNTIME, 'RUNTIME_BYTE_BUDGET');
  }
  const actual = new Set();
  let visitedEntries = 0;
  const walk = (directory, prefix = '', depth = 0) => {
    ensure(depth <= 24, 'RUNTIME_DEPTH');
    // Incremental enumeration bounds the traversal before collecting an
    // arbitrary directory listing. Directory entries count as work too.
    const stream = fs.opendirSync(directory, {bufferSize:32});
    try {
      for (let entry; (entry = stream.readSync()) !== null;) {
        ensure(++visitedEntries <= 40_000, 'RUNTIME_ENTRY_BUDGET');
        const name = prefix ? `${prefix}/${entry.name}` : entry.name;
        relativeName(name);
        const full = path.join(directory, entry.name);
        ensure(!entry.isSymbolicLink(), 'LINKED_RUNTIME');
        if (entry.isDirectory()) {
          ensure(expectedDirectories.has(name), 'UNDECLARED_RUNTIME_DIRECTORY');
          walk(full, name, depth + 1); continue;
        }
        ensure(entry.isFile(), 'RUNTIME_NONREGULAR');
        if (name === 'owner-profile.json') continue;
        ensure(expected.has(name) && !actual.has(name) && actual.size < 20_000, 'UNDECLARED_RUNTIME_FILE');
        const file = expected.get(name), proof = hashBounded(full, 128 * 1024 * 1024, file.sha256);
        ensure(proof.bytes === file.bytes, 'RUNTIME_SIZE_CHANGED'); actual.add(name);
      }
    } finally { stream.closeSync(); }
  };
  walk(runtimeRoot);
  ensure(actual.size === expected.size, 'RUNTIME_CLOSURE_MISSING');
  for (const name of ['producer','engine','core']) {
    const packageName = `node_modules/@hyperframes/${name}/package.json`;
    ensure(expected.has(packageName), 'RUNTIME_PACKAGE_MISSING');
    const pkg = JSON.parse(readBounded(path.join(runtimeRoot, packageName), 64 * 1024, expected.get(packageName).sha256).toString('utf8'));
    ensure(pkg.name === `@hyperframes/${name}` && pkg.version === VERSION, 'RUNTIME_PACKAGE_PIN');
  }
  ensure(expected.has('node_modules/@hyperframes/producer/dist/index.js') && expected.has('node_modules/@hyperframes/engine/dist/index.js') && expected.has('bun.lock'), 'PUBLIC_ENTRY_MISSING');
  return {owner, ownerBytes};
}

function fixedEnvironment(operation, tools) {
  for (const key of Object.keys(process.env)) {
    if (/^(NODE_OPTIONS|NODE_PATH|HYPERFRAMES_|PRODUCER_|HF_|PUPPETEER_|PLAYWRIGHT_|FFMPEG_|FFPROBE_)/.test(key)) delete process.env[key];
  }
  const temporary = path.join(operation, 'temporary'), cache = path.join(operation, 'cache');
  fs.mkdirSync(temporary); fs.mkdirSync(cache);
  for (const key of ['TEMP','TMP','TMPDIR']) process.env[key] = temporary;
  for (const key of ['XDG_CACHE_HOME','NPM_CONFIG_CACHE','PLAYWRIGHT_BROWSERS_PATH','PUPPETEER_CACHE_DIR']) process.env[key] = cache;
  process.env.HYPERFRAMES_FFMPEG_PATH = tools.ffmpeg;
  process.env.HYPERFRAMES_FFPROBE_PATH = tools.ffprobe;
  process.env.HYPERFRAMES_BROWSER_PATH = tools.chrome;
  process.env.HYPERFRAMES_EXTRACT_CACHE_DIR = 'off';
  process.env.PRODUCER_RENDERS_DIR = path.join(operation, 'producer-work');
  process.env.PRODUCER_LOW_MEMORY_MODE = 'true';
  process.env.PRODUCER_FORCE_SCREENSHOT = 'true';
  process.env.HF_STATIC_DEDUP = 'false';
  process.env.PRODUCER_EXPERIMENTAL_FAST_CAPTURE = 'false';
  process.env.PRODUCER_ENABLE_STREAMING_ENCODE = 'false';
  // No HOME/USERPROFILE rewriting, package manager invocation or ambient tool
  // search is used. The read-only runtime closure and Host remain mandatory.
}

function fixedEngineConfig(engine, chrome, chromiumMajor, operation) {
  const cfg = engine.resolveConfig({
    fps:30,quality:'high',format:'png',jpegQuality:100,concurrency:1,coresPerWorker:1,minParallelFrames:1_000_000,largeRenderThreshold:1_000_000,
    chromePath:chrome,disableGpu:true,browserGpuMode:'software',allowSoftwareWebGpu:false,enableBrowserPool:false,
    browserTimeout:30_000,protocolTimeout:30_000,expectedChromiumMajor:chromiumMajor,forceScreenshot:true,staticFrameDedup:false,
    useDrawElement:false,enableDrawElementWorkerEncode:false,lowMemoryMode:true,enablePageSideCompositing:false,
    vp9CpuUsed:1,enableChunkedEncode:false,chunkSizeFrames:180,enableStreamingEncode:false,
    streamingEncodeMaxDurationSeconds:6,streamingEncodeDurationCapEnabled:true,ffmpegEncodeTimeout:120_000,
    ffmpegProcessTimeout:30_000,ffmpegStreamingTimeout:30_000,hdr:false,hdrAutoDetect:false,audioGain:1,
    frameDataUriCacheLimit:1,frameDataUriCacheBytesLimitMb:16,playerReadyTimeout:30_000,renderReadyTimeout:15_000,pageNavigationTimeout:30_000,
    verifyRuntime:true,extractCacheDir:'off',extractCacheMaxBytes:16*1024*1024,debug:true,
  });
  engine.validateEngineConfigSnapshot(cfg);
  for (const [key, value] of Object.entries({concurrency:1,chromePath:chrome,disableGpu:true,browserGpuMode:'software',allowSoftwareWebGpu:false,
    enableBrowserPool:false,forceScreenshot:true,staticFrameDedup:false,useDrawElement:false,enableDrawElementWorkerEncode:false,
    enablePageSideCompositing:false,enableChunkedEncode:false,enableStreamingEncode:false,hdr:false,hdrAutoDetect:false,verifyRuntime:true,format:'png'})) {
    ensure(equal(cfg[key], value), 'EFFECTIVE_CONFIG_CHANGED');
  }
  return cfg;
}

export async function main(packet) {
  let operation, diagnosticFd, diagnosticBytes = 0, diagnosticOverflow = false, controller;
  let originalJob;
  const log = (kind, value) => {
    if (diagnosticOverflow || diagnosticFd === undefined) return;
    let line;
    try { line = Buffer.from(`${JSON.stringify({kind,value})}\n`); } catch { diagnosticOverflow = true; controller?.abort(); return; }
    if (line.length > 64*1024 || diagnosticBytes + line.length > MAX_DIAGNOSTIC) { diagnosticOverflow = true; controller?.abort(); return; }
    let written = 0;
    while (written < line.length) { const count=fs.writeSync(diagnosticFd,line,written,line.length-written); ensure(count>0,'DIAGNOSTIC_WRITE'); written+=count; }
    diagnosticBytes += line.length;
  };
  const originalConsole = {...console};
  try {
    validatePacket(packet);
    const args = process.argv.slice(2);
    ensure(args.length === 6, 'HOST_ARGUMENT_SHAPE');
    const [sourceRoot,runtimeRoot,operationRoot,chrome,ffmpeg,ffprobe] = args;
    for (const root of [sourceRoot,runtimeRoot,operationRoot]) noLinks(root, true);
    const roots = [sourceRoot,runtimeRoot,operationRoot].map(root=>fs.realpathSync(root));
    ensure(new Set(roots).size === 3 && roots.every((root,i)=>roots.every((other,j)=>i===j || (!root.startsWith(`${other}${path.sep}`) && !other.startsWith(`${root}${path.sep}`)))), 'OVERLAPPING_HOST_ROOTS');
    const source = preflightSource(packet, sourceRoot);
    const runtime = verifyRuntimeClosure(runtimeRoot, packet.request.owner_profile_sha256, packet.runner_sha256);
    ensure(equal(runtime.owner.tool_pins,packet.tool_pins), 'OWNER_TOOL_BINDING');
    for (const [tool,pin] of [[process.execPath,'node_sha256'],[chrome,'chrome_sha256'],[ffmpeg,'ffmpeg_sha256'],[ffprobe,'ffprobe_sha256']]) hashBounded(tool,MAX_OUTPUT,packet.tool_pins[pin]);
    integer(Number(process.versions.node.split('.')[0]),22,22);
    operation = path.join(operationRoot, packet.operation_name);
    // mkdir without recursive is the exclusive original-attempt claim. A
    // preserved directory is never removed or reused, even after a dead PID.
    fs.mkdirSync(operation, {mode:0o700});
    diagnosticFd = fs.openSync(path.join(operation,'diagnostic.jsonl'),'wx',0o600);
    controller = new AbortController();
    for (const signal of ['SIGINT','SIGTERM']) process.once(signal,()=>controller.abort());
    fixedEnvironment(operation,{chrome,ffmpeg,ffprobe});
    for (const level of ['log','info','warn','error','debug']) console[level]=(...values)=>log(`upstream.${level}`,values);
    const project = path.join(operation,'project'); fs.mkdirSync(project); fs.mkdirSync(path.join(project,'assets'));
    atomicNew(path.join(operation,'source.json'),source.bytes,MAX_SOURCE);
    atomicNew(path.join(project,'index.html'),Buffer.from(source.carrier.html),256*1024);
    for (const [name,bytes] of source.files) atomicNew(path.join(project,name),bytes,4*1024*1024);
    log('source.admitted',{source_sha256:packet.request.source_sha256,clock:packet.clock,width:packet.width,height:packet.height,resources:packet.resource_files});
    ensure(!controller.signal.aborted && !diagnosticOverflow,'CANCELLED_BEFORE_PRODUCER');
    // Only public package entry points. No deep import of compile/probe/capture
    // stages; their pipeline and cancellation behavior remain upstream-owned.
    const producer = await import(pathToFileURL(path.join(runtimeRoot,'node_modules/@hyperframes/producer/dist/index.js')).href);
    const engine = await import(pathToFileURL(path.join(runtimeRoot,'node_modules/@hyperframes/engine/dist/index.js')).href);
    ensure(typeof producer.createRenderJob==='function' && typeof producer.executeRenderJob==='function'
      && typeof engine.resolveConfig==='function' && typeof engine.validateEngineConfigSnapshot==='function','PUBLIC_API_MISSING');
    const cfg = fixedEngineConfig(engine,chrome,runtime.owner.chromium_major,operation);
    const options = {fps:{num:Number(packet.clock.fps.num),den:Number(packet.clock.fps.den)},quality:'high',format:'mp4',
      workers:1,useGpu:false,debug:true,strictness:'strict',entryFile:'index.html',producerConfig:cfg,
      crf:18,videoFrameFormat:'png',hdrMode:'force-sdr',resumeSegments:false,keepSegments:false};
    const configBytes = Buffer.from(`${JSON.stringify({schema:'motionwright.hyperframes.effective-config/1',options,node_version:process.versions.node})}\n`);
    atomicNew(path.join(operation,'effective-config.json'),configBytes,64*1024);
    const output = path.join(operation,'output.mp4'); ensure(!fs.existsSync(output),'OUTPUT_EXISTS');
    ensure(!controller.signal.aborted && !diagnosticOverflow,'CANCELLED_BEFORE_PRODUCER');
    originalJob = producer.createRenderJob(options);
    log('producer.original',{id:originalJob.id,config_sha256:sha256(configBytes),request_sha256:packet.request_sha256});
    const observeOnly = (job) => log('producer.progress',{id:job.id,status:job.status,progress:job.progress,stage:job.currentStage,
      totalFrames:job.totalFrames,framesRendered:job.framesRendered});
    const beforeCommit = () => {
      ensure(!controller.signal.aborted && !diagnosticOverflow,'CANCELLED_OR_DIAGNOSTIC_LIMIT');
      ensure(preflightSource(packet,sourceRoot).bytes.equals(source.bytes),'SOURCE_CHANGED_BEFORE_COMMIT');
      ensure(originalJob.totalFrames === packet.clock.frame_count,'RESOLVED_FRAME_COUNT_CHANGED');
    };
    await producer.executeRenderJob(originalJob,project,output,observeOnly,controller.signal,beforeCommit);
    ensure(!controller.signal.aborted && !diagnosticOverflow,'CANCELLED_OR_DIAGNOSTIC_LIMIT');
    ensure(originalJob.status==='complete' && originalJob.outcome==='completed' && originalJob.outputPath===output
      && Array.isArray(originalJob.warnings) && originalJob.warnings.length===0 && !originalJob.error
      && originalJob.totalFrames===packet.clock.frame_count && originalJob.framesRendered===packet.clock.frame_count,'PRODUCER_RESULT_NOT_CLOSED');
    // This recheck is custody only. The independent decoder must still check
    // every final frame, its clock, visual intent and an edited source reopen.
    preflightSource(packet,sourceRoot);
    verifyRuntimeClosure(runtimeRoot,packet.request.owner_profile_sha256,packet.runner_sha256);
    const proof=(name,cap)=>({name,...hashBounded(path.join(operation,name),cap)});
    const video=proof('output.mp4',MAX_OUTPUT),retained=proof('source.json',MAX_SOURCE),effective=proof('effective-config.json',64*1024);
    log('producer.closed',{id:originalJob.id,status:originalJob.status,outcome:originalJob.outcome,totalFrames:originalJob.totalFrames,framesRendered:originalJob.framesRendered,video});
    ensure(!diagnosticOverflow,'DIAGNOSTIC_LIMIT'); fs.fsyncSync(diagnosticFd); fs.closeSync(diagnosticFd); diagnosticFd=undefined;
    const result={schema:'motionwright.hyperframes.source-render-result/1',request_sha256:packet.request_sha256,
      carrier_sha256:packet.request.source_sha256,html_sha256:packet.html_sha256,runtime_profile_sha256:packet.request.owner_profile_sha256,
      runtime_closure_verified:true,runner_sha256:packet.runner_sha256,producer_revision:REVISION,producer_version:VERSION,
      producer_job_id:originalJob.id,producer_status:'complete',operation_id:packet.request.operation_id,clock:packet.clock,width:packet.width,height:packet.height,
      video,retained_source:retained,diagnostic:proof('diagnostic.jsonl',MAX_DIAGNOSTIC),effective_config:effective,
      complete_report_retained:true,pixel_fidelity_verified:false,editable_roundtrip_verified:false,export_authorized:false};
    const resultBytes=Buffer.from(`${JSON.stringify(result)}\n`); ensure(resultBytes.length<=32*1024,'RESULT_LIMIT');
    atomicNew(path.join(operation,'result.json'),resultBytes,32*1024);
    originalStdoutWrite(resultBytes);
  } catch (error) {
    // Keep the original operation and all materialized outputs. A diagnostic
    // failure never triggers Producer again or substitutes a successful report.
    try { log('runner.rejected',{code:typeof error?.code==='string'?error.code:'PRODUCER_FAILED',producer_job_id:originalJob?.id ?? null}); } catch {}
    originalStdoutWrite(`${JSON.stringify({schema:'motionwright.hyperframes.runner-failure/1',status:'rejected',operation_id:packet?.request?.operation_id ?? null,producer_job_id:originalJob?.id ?? null})}\n`);
    process.exitCode=1;
  } finally {
    for (const level of ['log','info','warn','error','debug']) console[level]=originalConsole[level];
    if (diagnosticFd!==undefined) { try { fs.fsyncSync(diagnosticFd);fs.closeSync(diagnosticFd); } catch {} }
  }
}
