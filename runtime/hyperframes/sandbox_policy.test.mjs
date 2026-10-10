import test from 'node:test';
import assert from 'node:assert/strict';
import {classifyHostSandbox, inspectHostSandboxProof, inspectUidProofParts, mapShape, classifyPinnedBrowserProbe, detectNativeSandboxMode} from './sandbox_policy.mjs';
const baseline={
  uidMap:'         0       1001          1\n',
  status:'Name:\tnode\nUid:\t0\t0\t0\t0\nCapEff:\t0000000000000000\nNoNewPrivs:\t1\n',
  apparmor:'bwrap//&unpriv_bwrap (enforce)\n',
  restrict:'1\n'
};
test('only a verified rootless, enforced no-new-privileges Host grants the outer bwrap launch profile',()=>{
  assert.equal(classifyHostSandbox(baseline),'semwright-bwrap-outer');
  const nonzeroInside={...baseline,uidMap:'1001 1001 1\n',
    status:baseline.status.replace('Uid:\t0\t0\t0\t0','Uid:\t1001\t1001\t1001\t1001')};
  assert.equal(classifyHostSandbox(nonzeroInside),'semwright-bwrap-outer',
    'One mapped unprivileged UID remains valid even if namespace uid is nonzero');
  assert.equal(classifyHostSandbox({...nonzeroInside,uidMap:'1001 0 1\n'}),'chromium-userns');
  assert.equal(classifyHostSandbox({...nonzeroInside,status:baseline.status}),'chromium-userns');
  assert.equal(classifyHostSandbox({...nonzeroInside,uidMap:'1001 1001 65536\n'}),'chromium-userns');
  for(const change of [
    {restrict:'0'}, {uidMap:'         0          0 4294967295\n'},
    {uidMap:'         0       1001 4294967295\n'},
    {uidMap:'         0       1001          1\n         1       1002          1\n'},
    {status:'Name:\tnode\nCapEff:\t0000000000000000\nNoNewPrivs:\t0\n'},
    {status:'Name:\tnode\nCapEff:\t0000000000000001\nNoNewPrivs:\t1\n'},
    {apparmor:'unconfined\n'}, {apparmor:'bwrap (complain)'},
    {apparmor:''}, {uidMap:''}
  ]){
    assert.equal(classifyHostSandbox({...baseline,...change}),'chromium-userns',JSON.stringify(change));
  }
});
test('UID map classifier reports shape, never numeric host IDs',()=>{
 const cases=[
  ['         0       1001          1\n','single_uid_host_nonzero'],
  ['         0          0          1\n','single_uid_host_zero'],
  ['         0          0 4294967295\n','host_zero_large_range'],
  ['         0       1001      65536\n','host_nonzero_large_range'],
  ['', 'missing'],
  ['not valid','malformed'],
  ['0 1001 1\n1 1002 1\n','multiple_ranges'],
  ['1 1001 1','namespace_root_not_zero']
 ];
 for(const [raw,expected]of cases){
  assert.equal(mapShape(raw),expected);
  assert.ok(!mapShape(raw).includes('1001'));
 }
});
test('Host proof retains exact five independent true/false safety inputs',()=>{
 const confirmed=inspectHostSandboxProof(baseline);
 assert.deepEqual(confirmed,{rootless:true,nnp:true,caps:true,confined:true,restrictEnabled:true});
 for(const [name,changed] of [
   ['rootless',{uidMap:'         0          0 4294967295\n'}],
   ['nnp',{status:'NoNewPrivs:\t0\nCapEff:\t0000000000000000\n'}],
   ['caps',{status:'NoNewPrivs:\t1\nCapEff:\t0000000000000001\n'}],
   ['confined',{apparmor:'unconfined\n'}],
   ['restrictEnabled',{restrict:'0\n'}]
 ]){
   const proof=inspectHostSandboxProof({...baseline,...changed});
   assert.equal(proof[name],false);
   assert.ok(Object.values(proof).some(value=>value===false));
 }
});
test('the executing process cannot acquire bwrap authority merely by setting an environment variable',()=>{
  const before=detectNativeSandboxMode();
  process.env.MOTIONWRIGHT_ASSUME_HOST_SANDBOX='1';
  assert.equal(detectNativeSandboxMode(),before);
  delete process.env.MOTIONWRIGHT_ASSUME_HOST_SANDBOX;
});


test('pinned browser preflight classifies causes without persisting arbitrary diagnostic text',()=>{
  const cases=[
    ['ETIMEDOUT','SIGTERM','private home path /home/alice/secret','browser_binary_probe_timeout'],
    ['', 'SIGSEGV','private file name','browser_binary_probe_sigsegv'],
    ['', 'SIGABRT','private command-line value','browser_binary_probe_sigabrt'],
    ['', 'SIGSYS','seccomp syscall','browser_binary_probe_sigsys'],
    ['', 'SIGTRAP','chromium trap','browser_binary_probe_sigtrap'],
    ['', 'SIGKILL','process killed','browser_binary_probe_sigkill'],
    ['', 'SIGBUS','memory mapped file','browser_binary_probe_sigbus'],
    ['', 'SIGTERM','unknown termination','browser_binary_probe_sigother'],
    ['', '', 'ICU initialization failed to load icudtl.dat','browser_binary_probe_missing_icu'],
    ['', '', 'Missing headless_lib.pak resource','browser_binary_probe_missing_sidecar'],
    ['', '', 'error while loading shared libraries libnss3.so','browser_binary_probe_missing_shared_library'],
    ['', '', 'permission denied executing bin','browser_binary_probe_permission_denied'],
    ['', '', 'failed to move to new namespace','browser_binary_probe_namespace_denied'],
    ['', '', 'pthread_create: Resource temporarily unavailable','browser_binary_probe_process_limit'],
    ['', '', 'unrecognized raw stderr /home/alice/private-app','browser_binary_probe_failed']
  ];
  for(const [code,signal,diagnostic,expected] of cases){
    assert.equal(classifyPinnedBrowserProbe(code,signal,diagnostic),expected);
    const category=classifyPinnedBrowserProbe(code,signal,diagnostic);
    assert.ok(/^browser_binary_probe_[a-z_]+$/.test(category));
    assert.ok(!category.includes('private')&&!category.includes('/home/'));
  }
});

test('outer UID proof diagnostics never depend on or disclose host UID numbers',()=>{
 const original=inspectUidProofParts(baseline);
 assert.deepEqual(original,{oneRow:true,canonicalShape:true,nonrootHost:true,
   oneUidOnly:true,processUidPresent:true,processUidMatches:true});
 const modifications=[
  [{uidMap:'0 0 1\n'},'nonrootHost'],
  [{uidMap:'0 1001 65536\n'},'oneUidOnly'],
  [{uidMap:'0 1001 1\n1 1002 1\n'},'oneRow'],
  [{uidMap:'not an integer range'},'canonicalShape'],
  [{status:baseline.status.replace(/^Uid:.*$/m,'')},'processUidPresent'],
  [{status:baseline.status.replace('Uid:\t0\t0\t0\t0','Uid:\t1\t1\t1\t1')},'processUidMatches']
 ];
 for(const [updated,flag]of modifications){
  const facts=inspectUidProofParts({...baseline,...updated});
  assert.equal(facts[flag],false);
  assert.ok(Object.values(facts).some(v=>v===false));
  assert.ok(Object.keys(facts).every(name=>!name.includes('1001')));
 }
});
