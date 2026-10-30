/** Linux-only identity join for the existing private R44 reopener.
 * A3's unchanged FD probe corroborates the process; A2's read-only witness
 * reports the SQLite pager. Neither is promoted to product admission here.
 */
import fs from 'node:fs';
import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { need } from './custody.mjs';

const DECIMAL = /^(0|[1-9][0-9]*)$/;
export function requireCoherentProcNamespace() {
  need(process.platform === 'linux', 'REOPEN_IDENTITY_LINUX_ONLY');
  const self = fs.readFileSync('/proc/self/stat', 'utf8');
  need(self.startsWith(`${process.pid} (`)
    && fs.readlinkSync('/proc/self') === String(process.pid),
    'REOPEN_PROC_PID_NAMESPACE_MISMATCH');
  return true;
}
export function captureLinuxProcessIdentity(pid) {
  requireCoherentProcNamespace();
  need(Number.isSafeInteger(pid) && pid > 1 && pid !== process.pid,
    'REOPEN_IDENTITY_CHILD_PID');
  const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
  const at = stat.lastIndexOf(') ');
  need(at > 0 && stat.startsWith(`${pid} (`), 'REOPEN_PROCESS_STAT_SHAPE');
  const fields = stat.slice(at + 2).trim().split(/\s+/);
  need(fields.length >= 20 && DECIMAL.test(fields[19])
    && !['Z','X','x'].includes(fields[0]), 'REOPEN_PROCESS_NOT_LIVE');
  return Object.freeze({pid, start_ticks: fields[19],
    uid: String(fs.statSync(`/proc/${pid}`, {bigint: true}).uid)});
}

export function expectedLinuxHeldStoreDigest(physical) {
  need(physical && DECIMAL.test(physical.device) && DECIMAL.test(physical.inode),
    'REOPEN_PHYSICAL_IDENTITY_SHAPE');
  const dev = BigInt(physical.device), ino = BigInt(physical.inode);
  need(dev <= 0xffffffffffffffffn && ino > 0n, 'REOPEN_PHYSICAL_IDENTITY_BOUND');
  // Linux gnu_dev_major/minor public encoding, kept distinct from raw st_dev.
  const major = ((dev >> 8n) & 0xfffn) | ((dev >> 32n) & 0xfffff000n);
  const minor = (dev & 0xffn) | ((dev >> 12n) & 0xffffff00n);
  const identity = `linux:${major}:${minor}:${ino}`;
  return createHash('sha256').update(identity, 'utf8').digest('hex');
}

export function requireHeldStoreWitness(value, expected) {
  need(typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
    && value === expected, 'REOPEN_HELD_STORE_WITNESS_MISMATCH');
  return value;
}

function processKey(value) {
  return {pid:value.pid,start_ticks:value.start_ticks,uid:value.uid};
}
function descriptors(value) {
  need(Array.isArray(value) && value.length > 0 && value.length <= 1024,
    'REOPEN_OPENED_DESCRIPTOR_SET');
  return value.map(r=>({fd:r.fd,target:r.target,device:r.device,inode:r.inode,flags:r.flags}))
    .sort((a,b)=>a.fd-b.fd);
}
export function requireSameOpenedProcessAndStore(first, second, atSpawn) {
  need(first?.descriptor_observed_live === true && second?.descriptor_observed_live === true,
    'REOPEN_OPENED_DESCRIPTOR_OBSERVATION_REQUIRED');
  need(isDeepStrictEqual(processKey(first.process), atSpawn)
    && isDeepStrictEqual(processKey(second.process), atSpawn),
    'REOPEN_OPENED_PROCESS_INCARNATION_CHANGED');
  need(first.source_path === second.source_path
    && isDeepStrictEqual(first.source_physical, second.source_physical)
    && isDeepStrictEqual(descriptors(first.opened_descriptors), descriptors(second.opened_descriptors)),
    'REOPEN_OPENED_DESCRIPTOR_SET_CHANGED');
  return true;
}
