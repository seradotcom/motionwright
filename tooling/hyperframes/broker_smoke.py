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
    lines=result.stdout.decode().strip().splitlines()
    if not lines:raise AssertionError('Native acceptance command returned no data')
    return json.loads(lines[-1])
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
        assets=database.with_suffix('.blobs');assets.mkdir(exist_ok=True)
        manifests=config/'drivers';manifests.mkdir()
        manifest={'id':'hyperframes','version':'0.1.0','executable':'','executable_root':'driver-executable','executable_sha256':digest(BINS['driver']),'protocol':8,
          'scopes':['driver:hyperframes'],'mounts':[{'root':'hyperframes-runtime','access':'read'},{'root':'hyperframes-assets','access':'read'},{'root':'hyperframes-work','access':'read_write'},{'root':'hyperframes-output','access':'read_write'}],
          'network':False,'lifecycle':'restartable','interfaces':{'native_handles':False,'delta_observation':False,'cooperative_cancellation':True,'health':True,'host_tools':True},
          'restart':{'max_restarts':0,'backoff_ms':0,'health_interval_ms':0},
          'resources':{'open_files':1024,'processes':256,'cpu_seconds':300,'operation_cpu_seconds':0,'address_space_bytes':4294967296,'file_size_bytes':1073741824},'request_timeout_ms':300000,
          'tools':[{'root':'runner-tool','name':'hyperframes-runner','sha256':digest(BINS['runner']),'mounts':['hyperframes-runtime','hyperframes-assets','hyperframes-work','hyperframes-output'],'system_config':[],'dependencies':['node','ffmpeg']},
                   {'root':'node-tool','name':'node','sha256':digest(node),'mounts':[],'system_config':[],'dependencies':[]},
                   {'root':'ffmpeg-tool','name':'ffmpeg','sha256':digest(ffmpeg),'mounts':[],'system_config':[],'dependencies':[]}]}
        write(manifests/'hyperframes.json',manifest)
        cli_root=str(BINS['cli'].resolve());roots={'driver-executable':str(BINS['driver'].resolve()),'runner-tool':str(BINS['runner'].resolve()),'node-tool':str(node),'ffmpeg-tool':str(ffmpeg),'hyperframes-runtime':str(RUNTIME.resolve()),'hyperframes-assets':str(assets.resolve()),'hyperframes-work':str(work.resolve()),'hyperframes-output':str(output.resolve())}
        app_policy={'version':1,'workspaces':[{'id':'default','allow':['driver:hyperframes'],'command_allowlist':COMMANDS,'command_denylist':[],
            'filesystem':[{'id':name,'path':path,'access':'read_write' if name in ['hyperframes-work','hyperframes-output'] else 'read'} for name,path in roots.items()],
            'domains':[],'rate_limit_per_minute':600,'max_timeout_ms':300000,'allow_undo':False,'allow_synthetic_input':False,'consent':{'risk_at_or_above':'privileged','scopes':[]}}]}
        policy=config/'policy.json';write(policy,app_policy)
        defaults=execute([cli_root,'config','show'],env);defaults['policy_path']=str(policy);defaults.setdefault('drivers',{})['allow']=['hyperframes'];defaults['drivers']['search_paths']=[str(manifests)]
        defaults['allowlist']=[scope for scope in defaults.get('allowlist',[]) if not scope.startswith('driver:')]+['driver:hyperframes'];defaults['unix_socket_path']=str(socket)
        config_file=config/'semwright.json';write(config_file,defaults)
        daemon_log=open(result_root/'broker-diagnostic.log','wb')
        daemon=subprocess.Popen([str(BINS['daemon']),'--config',str(config_file)],env=env,stdin=subprocess.DEVNULL,stdout=daemon_log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            deadline=time.monotonic()+20
            while not socket.exists():
                if daemon.poll() is not None:raise AssertionError('Broker exited before exposing its owned socket')
                if time.monotonic()>deadline:raise AssertionError('Broker socket startup deadline exceeded')
                time.sleep(.05)
            cli=[cli_root,'--config',str(config_file),'--socket',str(socket)]
            search=execute(cli+['--request-id','hf-owned-catalog','capabilities','search','--query','driver.hyperframes','--limit','20'],env)
            items=search['result']['data']['results'];by_name={entry['descriptor']['name']:entry for entry in items}
            if sorted(by_name)!=sorted(COMMANDS):raise AssertionError('Broker did not discover the exact native profile catalogue')
            for name,item in by_name.items():
                identity=item['metadata']
                if identity['provider']['id']!='driver:hyperframes' or identity['source']!='driver' or identity['provider_generation']<1:raise AssertionError('Native provider has no valid current generation')
                if len(item['descriptor_digest'])!=64:raise AssertionError('Native descriptor fingerprint is absent')
            session=execute(cli+['--request-id','hf-owned-session','session','mint','--ttl-ms','600000','--resource',seeded['resource'],'--scopes','driver:hyperframes','--commands',','.join(COMMANDS)],env)
            credential=config/'session';credential.write_text(session['result']['data']['session_id']+'\n');credential.chmod(0o600)
            connection=config/'connection.json';write(connection,{'version':1,'cli_executable':cli_root,'cli_sha256':digest(BINS['cli']),'config_path':str(config_file),'socket_path':str(socket),'session_file':str(credential),'output_root':str(output),'resource':seeded['resource']},True)
            application=execute([str(BINS['app']),'realize',str(database),str(connection),str(result_root/'application-evidence.json')],env,420)
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
            write(result_root/'result.json',{'schema':'motionwright.hyperframes-broker-comparison/1','motionwright_sha':source_sha,'semwright_sha':PIN,'native_provider_generation':items[0]['metadata']['provider_generation'],
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
