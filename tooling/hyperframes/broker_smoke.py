#!/usr/bin/env python3
"""Real Semwright Broker/Driver Host acceptance; compare against the same competent direct renderer.

All roots, test files, grants and processes belong to this disposable CI fixture.
No project-supplied executable paths, source programs, credential values or URLs.
"""
from __future__ import annotations
import hashlib,json,os,shutil,signal,subprocess,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
PIN=json.loads((ROOT/'SOURCE_LOCK.json').read_text())['dependencies']['semwright']['revision']
SW=Path(os.environ.get('SEMWRIGHT_CHECKOUT',str(ROOT/'_semwright'))).resolve()
RUNTIME=ROOT/'runtime/hyperframes'
BINS={'cli':SW/'target/debug/semwright','daemon':SW/'target/debug/semwrightd','sandbox':SW/'target/debug/semwright-sandbox',
      'driver':ROOT/'target/debug/motionwright-hyperframes-driver','runner':ROOT/'target/debug/motionwright-hyperframes-runner',
      'app':ROOT/'target/debug/examples/native-hyperframes-e2e','fixture':ROOT/'target/debug/examples/native_fixture'}
COMMANDS=['driver.hyperframes.doctor','driver.hyperframes.render.start','driver.hyperframes.render.status','driver.hyperframes.render.cancel','driver.hyperframes.render.result']
def digest(path:Path)->str:
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def write(path:Path,value:dict,private:bool=False)->None:
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2)+'\n');
    if private:path.chmod(0o600)
def execute(command:list[str],env:dict[str,str],timeout:int=360)->dict:
    result=subprocess.run(command,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    if result.returncode:raise AssertionError('Native acceptance command failed: '+str(command[:2])+'\n'+result.stderr.decode(errors='replace')[-15000:])
    output=result.stdout.decode().strip()
    if not output:raise AssertionError('Native acceptance command returned no data')
    try:return json.loads(output)
    except json.JSONDecodeError:
        for line in reversed(output.splitlines()):
            try:return json.loads(line)
            except json.JSONDecodeError:pass
        raise AssertionError('Native command output was neither one JSON document nor newline-delimited JSON')
def schema(body:dict)->dict:
    return body.get('result',{}).get('data',body)
def main()->None:
    if os.environ.get('GITHUB_ACTIONS')!='true':raise SystemExit('Native integration acceptance requires a disposable CI runner')
    source_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=SW,text=True).strip()!=PIN:raise AssertionError('Semwright checkout differs from the immutable product dependency')
    for key,path in BINS.items():
        if not path.is_file() or path.is_symlink():raise AssertionError('Missing regular compiled tool: '+key)
    node=Path(shutil.which('node')).resolve();ffmpeg=Path(shutil.which('ffmpeg')).resolve()
    result_root=ROOT/'verification/hyperframes-broker'/source_sha;result_root.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='motionwright-hf-broker-') as directory:
        temp=Path(directory);home=temp/'home';runtime_state=temp/'runtime-state';state=temp/'state';config=temp/'config';work=temp/'work';output=temp/'output';data=temp/'data'
        for p in [home,runtime_state,state,config,work,output,data]:p.mkdir();p.chmod(0o700)
        env=os.environ.copy();env.update({'HOME':str(home),'XDG_RUNTIME_DIR':str(runtime_state),'XDG_STATE_HOME':str(state),'SEMWRIGHT_WORKSPACE':'default','SEMWRIGHT_TEST_DRIVER_SANDBOX':'1','SEMWRIGHT_TEST_SANDBOX_HELPER':str(BINS['sandbox'])})
        for name in ['DISPLAY','WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS']:env.pop(name,None)
        socket=runtime_state/'semwright.sock';database=data/'motionwright.sqlite3';fixture_file=temp/'fixture.json';source_file=temp/'source.json'
        fixture=execute([str(BINS['fixture']),'opaque',digest(RUNTIME/'runtime.json')],env)
        write(fixture_file,fixture);seeded=execute([str(BINS['app']),'seed',str(database),str(fixture_file),str(source_file)],env)
        assets=data/'blobs'
        if not assets.is_dir(): raise AssertionError('The application-owned asset root is unavailable')
        bin_dir=config/'bin';bin_dir.mkdir()
        driver=bin_dir/'motionwright-hyperframes-driver'
        runner=bin_dir/'motionwright-hyperframes-runner'
        shutil.copyfile(BINS['driver'],driver);shutil.copyfile(BINS['runner'],runner)
        driver.chmod(0o700);runner.chmod(0o700)
        manifest={
            'manifest_version':1,'protocol':8,'id':'hyperframes','version':'0.1.0','publisher':'motionwright',
            'executable':str(driver),'sha256':digest(driver),
            'application':{'desktop_id':None,'process_names':['motionwright-hyperframes-runner','node','ffmpeg'],
                           'supported_versions':['HyperFrames Core 0.8.143 / Chromium native profile']},
            'transport':'stdio_v1',
            'mounts':[{'root':'hyperframes-runtime','read_only':True},
                      {'root':'hyperframes-assets','read_only':True},
                      {'root':'hyperframes-work','read_only':False},
                      {'root':'hyperframes-output','read_only':False}],
            'system_config':[],'secrets':[],
            'tools':[
                {'root':'hyperframes-runner-root','name':'hyperframes-runner','sha256':digest(runner),
                 'mounts':['hyperframes-runtime','hyperframes-assets','hyperframes-work','hyperframes-output'],
                 'dependencies':['node','ffmpeg']},
                {'root':'node-root','name':'node','sha256':digest(node),'mounts':[],'dependencies':[]},
                {'root':'ffmpeg-root','name':'ffmpeg','sha256':digest(ffmpeg),'mounts':[],'dependencies':[]},
            ],
            'network':False,'loopback_port':None,
            'resources':{'open_files':512,'processes':256,'cpu_seconds':300,'operation_cpu_seconds':0,
                         'address_space_bytes':4294967296,'file_size_bytes':1073741824},
            'request_timeout_ms':300000,
            'interfaces':{'dynamic_capabilities':False,'cooperative_cancellation':True,'events':False,
                          'health':True,'progress':False,'artifacts':False,'native_refs':False,'host_tools':True}
        }
        manifest_path=config/'hyperframes-driver.json'
        write(manifest_path,manifest,True)
        grants=[('hyperframes-runtime',RUNTIME,False),('hyperframes-assets',assets,False),
                ('hyperframes-work',work,True),('hyperframes-output',output,True),
                ('hyperframes-runner-root',runner,False),('node-root',node,False),('ffmpeg-root',ffmpeg,False)]
        lines=[f'drivers = [{json.dumps(str(manifest_path))}]','driver_network = false','','[policy]',
               'profile = "workspace"','allow = ["driver:hyperframes"]']
        for name,path,writable in grants:
            lines+=['','[[policy.filesystem]]',f'name = {json.dumps(name)}',
                    f'path = {json.dumps(str(path.resolve()))}','read = true',
                    f'write = {"true" if writable else "false"}']
        config_file=config/'owner.toml';config_file.write_text('\n'.join(lines)+'\n');config_file.chmod(0o600)
        socket=runtime_state/'broker.sock'
        session=runtime_state/'motionwright.session'
        daemon_log=open(result_root/'broker-diagnostic.log','wb')
        daemon=subprocess.Popen([str(BINS['daemon']),'--config',str(config_file),'--socket',str(socket)],env=env,stdin=subprocess.DEVNULL,stdout=daemon_log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            deadline=time.monotonic()+20
            while not socket.exists():
                if daemon.poll() is not None:raise AssertionError('Broker exited before exposing its owned socket')
                if time.monotonic()>deadline:raise AssertionError('Broker socket startup deadline exceeded')
                time.sleep(.05)
            cli=[str(BINS['cli']),'--socket',str(socket),'--session-file',str(session),'--json']
            offset=0;revisions=set();capabilities=[]
            while True:
                page=execute(cli+['capabilities','search','','--provider','driver:hyperframes',
                                  '--limit','100','--offset',str(offset)],env)
                data=page.get('data',{})
                revisions.add(data.get('revision'))
                capabilities+=data.get('capabilities',[])
                cursor=data.get('next_offset')
                if cursor is None:break
                if not isinstance(cursor,int) or cursor<=offset:raise AssertionError('Malformed canonical capability cursor')
                offset=cursor
            if len(revisions)!=1 or len(capabilities)<5:
                raise AssertionError('HyperFrames Driver Host did not expose a consistent native catalog')
            names={item.get('id') for item in capabilities}
            if set(COMMANDS)-names:raise AssertionError('Native capabilities missing: '+str(set(COMMANDS)-names))
            if not session.is_file() or (session.stat().st_mode&0o777)!=0o600:
                raise AssertionError('Canonical Broker did not provision a private application session')
            connection=config/'connection.json'
            write(connection,{
                'schema':'motionwright-semwright-connection/1','executable':str(BINS['cli'].resolve()),
                'executable_sha256':digest(BINS['cli']),'socket':str(socket),
                'session_file':str(session),'output_root':str(output),'resource':seeded['resource']
            },True)
            try:
                application=execute([str(BINS['app']),'realize',str(database),str(connection),str(result_root/'application-evidence.json')],env,420)
            except Exception:
                # Disposable synthetic fixture only. Do not log session credentials
                # or user filesystem content when preserving failure evidence.
                attempts=[]
                for folder in sorted(work.glob('hf-*')):
                    journal=folder/'job.json'
                    attempts.append({'attempt_folder':folder.name,
                                     'journal':json.loads(journal.read_text()) if journal.is_file() else None,
                                     'has_native_plan':(folder/'plan.json').is_file(),
                                     'has_native_source':(folder/'index.html').is_file(),
                                     'native_result_present':(output/folder.name/'result.json').is_file()})
                write(result_root/'attempt-diagnostics.json',{'schema':'motionwright.synthetic-attempt-diagnostics/1','attempts':attempts,
                     'source_sha256':json.loads(source_file.read_text())['source_sha256']})
                raise
            proof=json.loads((result_root/'application-evidence.json').read_text());native=proof['native_result'];job=native['job_ref']
            if application['hyperframes_canonical']!='PASS' or not proof['same_attempt_replayed']:raise AssertionError('Application did not prove logical-attempt reuse')
            if len(list(work.glob('hf-*')))!=1:raise AssertionError('Reconciliation submitted a duplicate native capture')
            canonical=json.loads((output/native['frames']['relative_path']).read_text())
            # Run the same exact source directly, without Motionwright's orchestration layer.
            direct_work=temp/'direct-work';direct_output=temp/'direct-output';direct_work.mkdir();direct_output.mkdir();direct_id='hf-00000000000000000000000000000064';(direct_work/direct_id).mkdir();(direct_output/direct_id).mkdir()
            source=json.loads(source_file.read_text());plan_bytes=source['plan_json'].encode();html=source['source_html'].encode()
            (direct_work/direct_id/'plan.json').write_bytes(plan_bytes);(direct_work/direct_id/'index.html').write_bytes(html)
            execute([str(BINS['runner']),'render','--runtime-root',str(RUNTIME),'--node-sealed',str(node),'--ffmpeg-sealed',str(ffmpeg),'--work-root',str(direct_work),'--output-root',str(direct_output),'--assets-root',str(assets),'--job',direct_id,'--plan-sha256',hashlib.sha256(plan_bytes).hexdigest(),'--source-sha256',source['source_sha256']],env,360)
            direct=json.loads((direct_output/direct_id/'frames.json').read_text())
            canonical_hashes=[frame['sha256'] for frame in canonical['frames']];direct_hashes=[frame['sha256'] for frame in direct['frames']]
            if canonical_hashes!=direct_hashes:raise AssertionError('Broker-mediated and direct native pixels differ on the exact same source/runtime')
            for key in ['frames','source','document','observations','mezzanine']:
                artifact=native[key];path=output/artifact['relative_path'];assert digest(path)==artifact['sha256'];shutil.copyfile(path,result_root/Path(artifact['relative_path']).name)
            for frame in [0,15,30,60,89]:shutil.copyfile(output/job/f'frames/frame-{frame:06}.png',result_root/f'native-frame-{frame:06}.png')
            write(result_root/'result.json',{'schema':'motionwright.hyperframes-broker-comparison/1','motionwright_sha':source_sha,'semwright_sha':PIN,'native_provider_generation':len(revisions),
                'broker_host_execution':'PASS','exact_direct_engine_pixel_comparison':'PASS','compared_frames':len(canonical_hashes),'canonical_hashes':canonical_hashes,'same_attempt_reuse':'PASS','source_revision_staleness':'PASS',
                'project_id':seeded['project_id'],'generation':seeded['generation'],'source_revision':seeded['revision'],'native_source_sha256':source['source_sha256'],'runtime_sha256':digest(RUNTIME/'runtime.json'),
                'creative_advantage_over_competent_agent':'not_measured','human_creative_approval':'required'})
        finally:
            if daemon.poll() is None:
                os.killpg(daemon.pid,signal.SIGTERM)
                try:daemon.wait(timeout=8)
                except subprocess.TimeoutExpired:os.killpg(daemon.pid,signal.SIGKILL);daemon.wait(timeout=8)
            daemon_log.close()
    print(json.dumps({'hyperframes_broker':'PASS','source_sha':source_sha,'evidence':str(result_root)}))
if __name__=='__main__':main()
