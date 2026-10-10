import test from 'node:test';
import assert from 'node:assert/strict';
import {classifyHostSandbox,detectNativeSandboxMode} from './sandbox_policy.mjs';
const baseline={
  uidMap:'         0       1001          1\n',
  status:'Name:\tnode\nCapEff:\t0000000000000000\nNoNewPrivs:\t1\n',
  apparmor:'bwrap//&unpriv_bwrap (enforce)\n',
  restrict:'1\n'
};
test('only a verified rootless, enforced no-new-privileges Host grants the outer bwrap launch profile',()=>{
  assert.equal(classifyHostSandbox(baseline),'semwright-bwrap-outer');
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
test('the executing process cannot acquire bwrap authority merely by setting an environment variable',()=>{
  const before=detectNativeSandboxMode();
  process.env.MOTIONWRIGHT_ASSUME_HOST_SANDBOX='1';
  assert.equal(detectNativeSandboxMode(),before);
  delete process.env.MOTIONWRIGHT_ASSUME_HOST_SANDBOX;
});
