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
export function classifyHostSandbox({uidMap,status,apparmor,restrict}) {
  if (restrict.trim() !== '1') return 'chromium-userns';
  const mapLines=uidMap.trim().split(/\n/);
  // A rootless user namespace maps namespace uid 0 to exactly one non-root
  // host uid. Normal/full host uid mappings never satisfy this condition.
  const rootless=mapLines.length===1 &&
    /^0\s+[1-9][0-9]*\s+1$/.test(mapLines[0].trim().replace(/\s+/g,' '));
  const nnp=/^NoNewPrivs:\s+1$/m.test(status);
  const caps=/^CapEff:\s+0+$/m.test(status);
  const confined=/\bbwrap\b/i.test(apparmor)&&/\(enforce\)\s*$/.test(apparmor.trim());
  return rootless&&nnp&&caps&&confined?'semwright-bwrap-outer':'chromium-userns';
}
export function detectNativeSandboxMode() {
  return classifyHostSandbox({
    uidMap:readBoundedKernelStatus('/proc/self/uid_map'),
    status:readBoundedKernelStatus('/proc/self/status'),
    apparmor:readBoundedKernelStatus('/proc/self/attr/current'),
    restrict:readBoundedKernelStatus('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')
  });
}
