// The Semwright Driver Host already places its tools inside an enforced,
// rootless, no-network Bubblewrap/AppArmor sandbox. Ubuntu's restrictive
// bwrap-userns-restrict profile explicitly prevents its children from
// creating another user namespace. Outside that exact Host boundary, retain
// Chromium's own native user namespace sandbox. Never trust an env flag.
import fs from 'node:fs';

function readBoundedKernelStatus(path) {
  try {
    const stat = fs.lstatSync(path);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 32768) return '';
    return fs.readFileSync(path, 'utf8').slice(0,32768);
  } catch {
    return '';
  }
}
export function inspectHostSandboxProof({uidMap,status,apparmor,restrict}) {
  const mapLines=uidMap.trim().split(/\n/);
  // Require a strict one-UID map to a nonzero host UID, not a host-wide map.
  const rootless=mapLines.length===1 &&
    /^0\s+[1-9][0-9]*\s+1$/.test(mapLines[0].trim().replace(/\s+/g,' '));
  const nnp=/^NoNewPrivs:\s+1$/m.test(status);
  const caps=/^CapEff:\s+0+$/m.test(status);
  const confined=/\bbwrap\b/i.test(apparmor)&&/\(enforce\)\s*$/.test(apparmor.trim());
  const restrictEnabled=restrict.trim()==='1';
  return {rootless,nnp,caps,confined,restrictEnabled};
}
export function classifyHostSandbox(observations) {
  const proof=inspectHostSandboxProof(observations);
  return Object.values(proof).every(Boolean)?'semwright-bwrap-outer':'chromium-userns';
}
function readHostSandboxInput() {
  return {
    uidMap:readBoundedKernelStatus('/proc/self/uid_map'),
    status:readBoundedKernelStatus('/proc/self/status'),
    apparmor:readBoundedKernelStatus('/proc/self/attr/current'),
    restrict:readBoundedKernelStatus('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')
  };
}
export function mapShape(uidMap){
  const rows=uidMap.trim().split(/\n/).filter(Boolean);
  if(!rows.length)return 'missing';
  if(rows.length!==1)return 'multiple_ranges';
  const match=/^\s*([0-9]+)\s+([0-9]+)\s+([0-9]+)\s*$/.exec(rows[0]);
  if(!match)return 'malformed';
  const [,inside,outside,count]=match.map(String);
  if(inside!=='0')return 'namespace_root_not_zero';
  if(outside==='0' && count==='1')return 'single_uid_host_zero';
  if(outside!=='0' && count==='1')return 'single_uid_host_nonzero';
  if(outside==='0')return 'host_zero_large_range';
  return 'host_nonzero_large_range';
}
export function detectHostUidMapShape(){
  return mapShape(readBoundedKernelStatus('/proc/self/uid_map'));
}
export function detectHostSandboxProof() {
  return inspectHostSandboxProof(readHostSandboxInput());
}
export function detectNativeSandboxMode() {
  return classifyHostSandbox(readHostSandboxInput());
}


// This classifier is pure and returns one fixed observation symbol. Chromium
// stderr is never included in the returned value, persisted artifacts or
// application diagnostics. Its data is untrusted even when the binary is pinned.
export function classifyPinnedBrowserProbe(errorCode, signal, stderr) {
  const error=String(errorCode??'');
  const failureSignal=String(signal??'');
  const message=String(stderr??'').toLowerCase().slice(0,12000);
  if(error==='ETIMEDOUT')return 'browser_binary_probe_timeout';
  const signals={
    SIGSEGV:'browser_binary_probe_sigsegv',
    SIGABRT:'browser_binary_probe_sigabrt',
    SIGTRAP:'browser_binary_probe_sigtrap',
    SIGSYS:'browser_binary_probe_sigsys',
    SIGKILL:'browser_binary_probe_sigkill',
    SIGBUS:'browser_binary_probe_sigbus'
  };
  if(failureSignal)return signals[failureSignal]??'browser_binary_probe_sigother';
  if(/icudtl|icu data|icu_util|invalid file descriptor to icu/.test(message))
    return 'browser_binary_probe_missing_icu';
  if(/\.pak|resource bundle|failed to load resource|v8_context_snapshot|snapshot_blob/.test(message))
    return 'browser_binary_probe_missing_sidecar';
  if(/error while loading shared libraries|cannot open shared object|libnss3|libatk|libasound|libx11|libglib/.test(message))
    return 'browser_binary_probe_missing_shared_library';
  if(/permission denied|operation not permitted|eacces|eperm/.test(message))
    return 'browser_binary_probe_permission_denied';
  if(/namespace|sandbox|userns/.test(message))
    return 'browser_binary_probe_namespace_denied';
  if(/pthread_create|resource temporarily unavailable|eagain|fork/.test(message))
    return 'browser_binary_probe_process_limit';
  return 'browser_binary_probe_failed';
}
