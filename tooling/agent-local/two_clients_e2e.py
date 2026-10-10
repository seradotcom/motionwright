#!/usr/bin/env python3
"""True subprocess clients, one canonical SQLite project, Native SDK read pages."""
from __future__ import annotations
import json,os,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
ACTOR=ROOT/'target/debug/examples/local_agent_actor'
def expect(x:bool,message:str)->None:
 if not x:raise AssertionError(message)
def main()->None:
 expect(os.environ.get('GITHUB_ACTIONS')=='true','Run on disposable CI only')
 expect(ACTOR.is_file(),'Native SDK actor must be compiled before exercising clients')
 output=ROOT/'verification/native-agent-two-clients';output.mkdir(parents=True,exist_ok=False)
 events=[]
 with tempfile.TemporaryDirectory(prefix='mw-v05-two-agents-')as tmp:
  root=Path(tmp);db=root/'owner-project.sqlite3'
  def action(client:str,req:dict,write:bool=False,success:bool=True)->dict:
   expect(client in ('owner','agent_a','agent_b'),'Unknown actor identity')
   path=root/f'{client}-{len(events):03}.json';path.write_text(json.dumps(req)+'\n')
   argv=[str(ACTOR),str(db),str(path)]
   if write:argv.append('--owner-edit')
   p=subprocess.run(argv,capture_output=True,timeout=25,check=False)
   expect(len(p.stdout)<2*1024*1024 and len(p.stderr)<2*1024*1024,'Output response exceeds budget')
   if success:
    if p.returncode:raise AssertionError(f'{client} failed: '+p.stderr.decode(errors='replace')[-850:])
    data=json.loads(p.stdout)
    expect(data.get('no_remote_host_authority') is True,'Remote Host permission was invented')
   else:
    expect(p.returncode!=0 and not p.stdout,'A forged/stale operation was applied')
    data={'status':'EXPECTED_REJECTION'}
   events.append({'actor':client,'action':req['action'],'status':data.get('status','PASS'),
    'revision':data.get('revision')})
   return data
  owner=action('owner',{'action':'init','title':'One source, two independent clients'},write=True)
  resource=owner['project_id'];generation=owner['generation']
  names=[];offset=0
  while True:
   result=action('agent_a',{'action':'discover','offset':offset,'limit':9})
   names.extend(row['name'] for row in result['items'])
   if result['complete']:break
   offset=result['next_offset']
   expect(offset==len(names) and offset<=128,'SDK capability pagination made no progress')
  expect(len(set(names))==len(names) and len(names)>=50,'Not the full Native SDK registered catalog')
  expect({'driver.motionwright.project.rename','driver.motionwright.scene.add',
          'driver.motionwright.observe'}.issubset(names),'Expected registered capabilities absent')
  def read(client:str,scope='summary',limit=4,cursor=None,success=True):
   result=action(client,{'action':'observe','project_id':resource,
     'scope':scope,'limit':limit,'cursor':cursor},success=success)
   return result.get('page') if success else result
  a0=read('agent_a');b0=read('agent_b')
  expect(a0['version']==b0['version'] and a0['items'][0]['revision']=='0',
    'Independent Native SDK clients started from different project revisions')
  first={'action':'preview','project_id':resource,
   'expected_generation':generation,'expected_revision':0,
   'edit':{'operation':'project.rename','title':'Source after owner A revision'}}
  candidate=action('agent_a',first)
  expect(candidate['status']=='READ_ONLY_DOMAIN_VALIDATION' and not candidate['committed'],
    'Dry run implied write authority')
  expect(read('agent_b')['items'][0]['revision']=='0','Preview unexpectedly altered database')
  first_commit={**first,'action':'commit','request_id':'owner-approved-agent-a-rev1'}
  action('agent_a',first_commit,success=False)
  applied=action('agent_a',first_commit,write=True)
  expect(applied['revision']==1 and applied['previous_revision']==0,'Owner-local CAS 1 failed')
  second={'action':'commit','project_id':resource,
   'expected_generation':generation,'expected_revision':0,
   'request_id':'owner-approved-agent-b-rev2',
   'edit':{'operation':'scene.add','name':'Native original scene',
    'objective':'Continue same source without any shared chat', 'duration_seconds':3}}
  action('agent_b',second,write=True,success=False)
  expect(read('agent_b')['items'][0]['revision']=='1','Agent B did not refresh its new revision')
  preview=action('agent_b',{**{k:v for k,v in second.items() if k!='request_id'},'action':'preview','expected_revision':1})
  expect(preview['candidate_scene_count']==1 and not preview['committed'],'Preview applied scene')
  committed=action('agent_b',{**second,'expected_revision':1},write=True)
  expect(committed['revision']==2 and committed['scene_count']==1,'Agent B failed its independent commit')
  timeline=read('agent_a','timeline')
  expect(len(timeline['items'])==1 and timeline['items'][0]['name']=='Native original scene',
    'Agent A could not observe new source from agent B')
  history=[];cursor=None
  while True:
   page=read('agent_a','history',1,cursor)
   history.extend(row['revision'] for row in page['items'])
   if page['complete']:break
   cursor=page['next'];expect(len(history)<=3,'History page cursor stalled')
  expect(history==[1,2],f'Unexpected same-source journal: {history}')
  stale_history=read('agent_b','history',1)['next']
  third={'action':'commit','project_id':resource,
   'expected_generation':generation,'expected_revision':2,
   'request_id':'owner-approved-agent-a-rev3',
   'edit':{'operation':'narrative.premise.set',
    'premise':'Exact original project can be continued across clients'}}
  expect(action('agent_a',third,write=True)['revision']==3,'Third agent commit lost')
  read('agent_b','history',1,stale_history,success=False)
  action('agent_b',{**third,'request_id':'other-generation-rejected',
   'expected_generation':'00000000-0000-4000-8000-000000000009'},
   write=True,success=False)
  action('agent_a',{**third,'action':'preview','expected_revision':3,
   'edit':{'operation':'plugin.execute','command':'echo unapproved'}},success=False)
  action('agent_b',{'action':'discover','offset':0,'limit':999},success=False)
  final=read('agent_a')
  expect(final['items'][0]['revision']=='3' and
   final['items'][0]['title']=='Source after owner A revision' and
   final['items'][0]['scene_count']==1,'Rejected input changed canonical owner project')
  report={
   'schema':'motionwright.v05-local-agent-two-process/1',
   'status':'PASS_OWNER_LOCAL_CAS_AND_NATIVE_SDK_READ',
   'same_sqlite_project':True,'independent_subprocess_per_request':True,
   'shared_chat':False,'native_sdk_catalog_operations':len(names),
   'sdk_observation_scopes':['summary','timeline','history'],
   'owner_local_cas_revisions':3,'stale_revision_rejected':True,
   'stale_page_cursor_rejected':True,'unknown_operation_rejected':True,
   'cross_generation_rejected':True,'read_only_preview':'PASS',
   'canonical_database_backend':'existing_studio_service',
   'host_authenticated_agent_write':'NOT_TESTED_OR_GRANTED',
   'remote_listener':'NONE','creative_approval':'NOT_RUN',
   'same_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
   'events':events
  }
  (output/'result.json').write_text(json.dumps(report,indent=2)+'\n')
  expect(len(events)>=18,'Required two-client sequence is incomplete')
  print(json.dumps({'same_source_two_clients':'PASS','revisions':3,
   'registered_sdk_capabilities':len(names),'events':len(events),
   'host_write_authority':'NOT_CLAIMED'}))
if __name__=='__main__':main()
