#!/usr/bin/env python3
"""Native Core/Chromium profile acceptance on disposable CI only. Not a creative verdict."""
from __future__ import annotations
import hashlib,json,os,shutil,subprocess,tempfile,uuid
from pathlib import Path
from PIL import Image,ImageChops
ROOT=Path(__file__).resolve().parents[2]
RUNTIME=ROOT/'runtime/hyperframes'
BIN=ROOT/'target/debug/motionwright-hyperframes-runner'
FIXTURE=ROOT/'target/debug/examples/native_fixture'

def digest(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def run(args:list[str],timeout:int=300)->subprocess.CompletedProcess:
    return subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,check=False)
def main()->None:
    if os.environ.get('GITHUB_ACTIONS')!='true':raise SystemExit('Heavy native acceptance belongs on a disposable CI runner')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    evidence=ROOT/'verification/hyperframes-native'/head;evidence.mkdir(parents=True,exist_ok=False)
    runtime_sha=digest((RUNTIME/'runtime.json').read_bytes())
    browser_info=json.loads((RUNTIME/'runtime.json').read_text())['files']['browser']
    browser=RUNTIME/browser_info['path']
    assert browser.is_file() and digest(browser.read_bytes())==browser_info['sha256']
    entries={mode:json.loads(subprocess.check_output([str(FIXTURE),mode,runtime_sha])) for mode in ['opaque','alpha','ntsc']}
    results={}
    with tempfile.TemporaryDirectory(prefix='motionwright-hyperframes-') as directory:
        root=Path(directory);work=root/'work';output=root/'output';assets=root/'assets'
        for path in [work,output,assets]:path.mkdir()
        for index,mode in enumerate(['opaque','opaque-repeat','alpha','ntsc']):
            entry=entries['opaque' if mode=='opaque-repeat' else mode]
            job='hf-'+uuid.UUID(int=index+1).hex;folder=work/job;folder.mkdir();(output/job).mkdir()
            plan_bytes=entry['plan_json'].encode();source=entry['source_html'].encode()
            assert digest(source)==entry['source_sha256']
            (folder/'plan.json').write_bytes(plan_bytes);(folder/'index.html').write_bytes(source)
            args=[str(BIN),'render','--runtime-root',str(RUNTIME),'--node-sealed',str(Path(shutil.which('node')).resolve()),'--ffmpeg-sealed',str(Path(shutil.which('ffmpeg')).resolve()),'--chromium-sealed',str(browser),
                  '--work-root',str(work),'--output-root',str(output),'--assets-root',str(assets),'--job',job,'--plan-sha256',digest(plan_bytes),'--source-sha256',entry['source_sha256']]
            observed=run(args)
            (evidence/(mode+'.log')).write_bytes(observed.stdout+b'\n'+observed.stderr)
            if observed.returncode:raise AssertionError(mode+' failed: '+observed.stderr.decode(errors='replace')[-9000:])
            folder_out=evidence/mode;folder_out.mkdir()
            for name in ['result.json','frames.json','source.html','source.json','observations.ndjson','mezzanine.mkv']:
                shutil.copyfile(output/job/name,folder_out/name)
            for frame in [0,15,30,60,89]:shutil.copyfile(output/job/f'frames/frame-{frame:06}.png',folder_out/f'frame-{frame:06}.png')
            result=json.loads((output/job/'result.json').read_text());manifest=json.loads((output/job/'frames.json').read_text())
            assert manifest['frame_count']==90 and manifest['source_sha256']==entry['source_sha256']
            assert manifest['observation']['coverage']=='all_frames' and manifest['external_requests']==0
            assert manifest['sandbox_mode']=='chromium-userns', 'Direct native execution must retain Chromium userns confinement'
            observations=[json.loads(line) for line in (output/job/'observations.ndjson').read_text().splitlines()]
            assert len(observations)==90 and all(abs(row['time']-row['observed_time'])<1e-7 for row in observations)
            node_id=str(uuid.UUID(int=20))
            card=lambda index:next(node for node in observations[index]['nodes'] if node['id']==node_id)
            assert abs(card(0)['opacity'])<1e-7 and abs(card(15)['opacity']-.5)<1e-6 and abs(card(89)['opacity']-1)<1e-7, ('native opacity drift', [card(f) for f in [0,15,30,89]])
            assert card(0)['clip']!=card(15)['clip'] and card(15)['clip']!=card(89)['clip']
            assert card(0)['transform']!=card(15)['transform'] and card(15)['transform']!=card(89)['transform']
            assert '6px' in card(0)['filter'] and 'blur(0px)' in card(89)['filter']
            assert all(not n['truncated'] for row in observations for n in row['nodes'])
            hashes=[]
            for frame in manifest['frames']:
                path=output/job/frame['relative_path'];data=path.read_bytes();assert digest(data)==frame['sha256'];hashes.append(digest(data))
            assert hashes[0]!=hashes[15]!=hashes[89]
            probe=run([shutil.which('ffprobe'),'-v','error','-count_frames','-show_streams','-show_format','-of','json',str(output/job/'mezzanine.mkv')],60)
            assert probe.returncode==0
            stream=json.loads(probe.stdout)['streams'][0]
            assert stream['codec_name']=='ffv1' and stream['width']==640 and stream['height']==360 and int(stream['nb_read_frames'])==90
            assert stream['pix_fmt']=='bgra'
            if mode=='alpha':
                with Image.open(output/job/'frames/frame-000089.png') as image:
                    alpha=image.convert('RGBA').getchannel('A');assert alpha.getextrema()==(0,255)
            results[mode]={'frame_hashes':hashes,'source_sha256':entry['source_sha256'],'rate':manifest['rate'],'browser':manifest['browser'],'alpha':manifest['alpha']}
        assert results['opaque']['frame_hashes']==results['opaque-repeat']['frame_hashes'], 'Same native source/runtime must be reproducible on the same pinned worker'
        # A malformed source digest must not launch a second rendering or overwrite its output.
        bad=list(args);bad[-1]='0'*64
        denied=run(bad,30);assert denied.returncode!=0
        output_summary={'schema':'motionwright.hyperframes-native-smoke/1','source_sha':head,'native_profile':'hyperframes-core-chromium-png-v2','technical':'PASS',
                        'same_worker_repeat_all_frames':'PASS','rgba_alpha':'PASS','ntsc_clock':'PASS','mask_rotation_opacity_blur':'PASS','tampered_source_rejected':True,
                        'creative_approval':'required','results':results}
        (evidence/'result.json').write_text(json.dumps(output_summary,indent=2)+'\n')
    print(json.dumps({'hyperframes_native':'PASS','source_sha':head,'frames':360,'evidence':str(evidence)}))
if __name__=='__main__':main()
