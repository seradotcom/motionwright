/** Actual Linux child SQLite FD observation, never a parent path-only inference.
 * This is a source-only proposal for the existing A6 same-store reopener.
 * It must be invoked while that child remains alive and after a genuine read.
 * An UNKNOWN observation is never retried or accepted as G2 evidence.
 */
import fs from 'node:fs';
import path from 'node:path';

const DECIMAL=/^(0|[1-9][0-9]*)$/;
function need(condition, code) { if (!condition) throw new Error(code); }
function processStamp(pid) {
  const record=fs.readFileSync('/proc/'+pid+'/stat','utf8');
  const end=record.lastIndexOf(') ');
  need(end>0 && record.startsWith(String(pid)+' ('),'OPENED_STORE_PROC_SHAPE');
  const fields=record.slice(end+2).trim().split(/\s+/);
  need(fields.length>=20 && DECIMAL.test(fields[19])
    && !['Z','X','x'].includes(fields[0]),'OPENED_STORE_PROCESS_DEAD');
  const uid=fs.statSync('/proc/'+pid,{bigint:true}).uid;
  return {pid,start_ticks:fields[19],state:fields[0],uid:String(uid)};
}
function sameProcess(a,b) {
  need(a.pid===b.pid && a.start_ticks===b.start_ticks && a.uid===b.uid,
    'OPENED_STORE_PID_REUSED_OR_DIED');
}
function fileStamp(file) {
  const s=fs.statSync(file,{bigint:true});
  need(s.isFile() && s.nlink===1n,'OPENED_STORE_FILE_TYPE');
  return {device:String(s.dev),inode:String(s.ino)};
}
function sameFile(a,b) {
  need(a.device===b.device && a.inode===b.inode,'OPENED_STORE_FILE_CHANGED');
}
/** expected is an exact {path,device,inode} from A6's stableRead().physical.
 * No injectable fake FS and no caller-provided success boolean.
 */
export function observeOpenedSQLiteDescriptor({pid,expected}) {
  need(process.platform==='linux','OPENED_STORE_LINUX_ONLY');
  need(Number.isSafeInteger(pid) && pid>1 && pid!==process.pid,'OPENED_STORE_CHILD_PID');
  need(expected && typeof expected==='object' && typeof expected.path==='string'
    && path.isAbsolute(expected.path) && DECIMAL.test(expected.device)
    && DECIMAL.test(expected.inode),'OPENED_STORE_EXPECTED_SHAPE');
  need(fs.realpathSync.native(expected.path)===expected.path,'OPENED_STORE_NONCANONICAL_PATH');
  const pathname=expected.path;
  const before=fileStamp(pathname);
  sameFile(before,expected);
  const processBefore=processStamp(pid);
  const fdRoot='/proc/'+pid+'/fd';
  const names=fs.readdirSync(fdRoot);
  need(names.length>0 && names.length<=1024,'OPENED_STORE_FD_ROSTER_BOUND');
  const observed=[];
  for (const name of names) {
    if (!DECIMAL.test(name)) continue;
    const filename=path.join(fdRoot,name);
    // A vanished or unreadable descriptor taints the one attempt. Never skip it.
    const target=fs.readlinkSync(filename);
    const stat=fs.statSync(filename,{bigint:true});
    const basename=path.basename(pathname);
    if (target===pathname || target===pathname+' (deleted)' ||
        path.basename(target).startsWith(basename+' (deleted)')) {
      need(target===pathname,'OPENED_STORE_DESCRIPTOR_DELETED');
      need(stat.isFile(),'OPENED_STORE_DESCRIPTOR_NOT_FILE');
      const opened={device:String(stat.dev),inode:String(stat.ino)};
      sameFile(opened,before);
      const info=fs.readFileSync('/proc/'+pid+'/fdinfo/'+name,'utf8');
      const match=info.match(/^flags:\s+([0-7]+)$/m);
      need(!!match,'OPENED_STORE_DESCRIPTOR_FLAGS_UNKNOWN');
      observed.push({fd:Number(name),target,device:opened.device,inode:opened.inode,flags:match[1]});
    }
  }
  need(observed.length>0,'OPENED_STORE_SQLITE_FD_NOT_OBSERVED');
  const processAfter=processStamp(pid);
  sameProcess(processBefore,processAfter);
  sameFile(fileStamp(pathname),before);
  for (const row of observed) {
    const filename=path.join(fdRoot,String(row.fd));
    const target=fs.readlinkSync(filename);
    const stat=fs.statSync(filename,{bigint:true});
    need(target===row.target && String(stat.dev)===row.device &&
      String(stat.ino)===row.inode,'OPENED_STORE_DESCRIPTOR_CHANGED');
  }
  return Object.freeze({
    schema:'motionwright.graph.opened-service-sqlite-fd/1',
    process:processAfter,source_path:pathname,source_physical:before,
    opened_descriptors:observed,descriptor_observed_live:true,
    scope:'actual Linux child opened SQLite main-file descriptor only',
    native_recovery_verified:false,host_closed:false,gate_credit:false,
  });
}
