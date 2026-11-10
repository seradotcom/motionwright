import test from 'node:test';
import assert from 'node:assert/strict';
import { inspectDevelopmentRunCore, inspectR74StatusCore,
  assertOriginalProducerAdmission, assertGraphProducerAdmission,
} from '../../tests/support/graph-owner/producer_guard.mjs';

const fixture=()=>({
  binding:{source_sha:'a'.repeat(40),run_id:'12345',case_id:'graph-flow',
    index_sha256:'b'.repeat(64),request_sha256:'c'.repeat(64)},
  run:{id:12345,run_attempt:1,head_sha:'a'.repeat(40),head_branch:'development-source',
    repository:{full_name:'seradotcom/motionwright'},
    head_repository:{full_name:'seradotcom/motionwright'},
    event:'workflow_dispatch',status:'in_progress',path:'.github/workflows/studio-service-verification.yml',
    workflow_id:44,actor:{type:'User',id:99}},
  event:{ref:'refs/heads/development-source',repository:{full_name:'seradotcom/motionwright'},
    sender:{type:'User',id:99},inputs:{selection:'phase1-native-graph',graph_case_mode:'development-first-save'}},
});

test('development decoder carries explicit scope and grants no formal admission brand',()=>{
  const {run,event,binding}=fixture();
  const value=inspectDevelopmentRunCore(run,event,binding);
  assert.equal(value.kind,'EXPLICIT_DEVELOPMENT_ORIGINAL');
  assert.equal(value.formal_acceptance,false);
  assert.equal(value.automatic_retry,false);
  assert.equal(Object.hasOwn(value,'status_id'),false);
  assert.throws(()=>assertOriginalProducerAdmission(value),/UNBRANDED/);
  assert.throws(()=>assertGraphProducerAdmission(value),/UNBRANDED/);
  assert.throws(()=>assertGraphProducerAdmission(Object.freeze({development:value})),/UNBRANDED/);
});

for(const [label,change] of [
  ['ordinary graph-flow dispatch',x=>{x.event.inputs.graph_case_mode='graph-flow'}],
  ['unrelated selector',x=>{x.event.inputs.selection='phase1-native-gn1'}],
  ['different actor',x=>{x.event.sender.id++}],
  ['different branch',x=>{x.event.ref='refs/heads/main'}],
  ['reused attempt',x=>{x.run.run_attempt=2}],
  ['completed run',x=>{x.run.status='completed'}],
  ['different source',x=>{x.run.head_sha='d'.repeat(40)}],
  ['foreign workflow',x=>{x.run.path='.github/workflows/other.yml'}],
  ['another case',x=>{x.binding.case_id='graph-removal'}],
  ['plain environment claim',x=>{x.run={};x.event={}}],
]) test('development denies '+label,()=>{
  const value=fixture();change(value);
  assert.throws(()=>inspectDevelopmentRunCore(value.run,value.event,value.binding),/DEV_/);
});

test('formal R74 still rejects an authentic-looking development run without independent status',()=>{
  const {run,binding}=fixture();
  const {request_sha256,...formal}=binding;
  assert.throws(()=>inspectR74StatusCore(run,[],formal),/R74_STATUS_NOT_PRESENT/);
});

test('malformed original hash cannot be used as development authority',()=>{
  const {run,event,binding}=fixture();
  binding.request_sha256='caller-ready';
  assert.throws(()=>inspectDevelopmentRunCore(run,event,binding),/DEV_ORIGINAL_BINDING/);
});
