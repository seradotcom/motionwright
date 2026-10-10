#!/usr/bin/env python3
"""Source-proposed dirty ranges verified against 180 *actual* native frames.

This proves a lossless PNG reassembly and real transfer-byte difference,
NOT rendering/compilation/encoding speedups: both source variants are fully
rendered by the first-party direct native runner for an independent oracle.
"""
from __future__ import annotations
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from time import perf_counter_ns
from uuid import UUID
import zipfile

from PIL import Image,ImageDraw
from verify_dirty_transfer import rebuild

ROOT=Path(__file__).resolve().parents[2]
RUNTIME=ROOT/'runtime/hyperframes'
RUNNER=ROOT/'target/debug/motionwright-hyperframes-runner'
FIXTURE=ROOT/'target/debug/examples/native_frame_delta_fixture'
FRAME_COUNT=90

def require(yes:bool,why:str)->None:
    if not yes:raise AssertionError(why)
def digest(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def sha_file(file:Path)->str:
    h=hashlib.sha256()
    with file.open('rb')as inp:
        while part:=inp.read(1024*1024):h.update(part)
    return h.hexdigest()
def run(args:list[str],seconds:int)->bytes:
    observed=subprocess.run(args,capture_output=True,timeout=seconds,check=False)
    if observed.returncode:
        raise AssertionError('Direct native renderer returned error: '+observed.stderr.decode('utf8','replace')[-1500:])
    return observed.stdout
def make_frame_zip(target:Path,frame_dir:Path,indexes:list[int],receipt:dict)->int:
    with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_STORED)as archive:
        for index in indexes:
            frame=frame_dir/f'frame-{index:06}.png'
            data=frame.read_bytes()
            require(digest(data)==receipt['frames'][index]['sha256'],'Native source frame drift')
            name=f'frames/frame-{index:06}.png'
            descriptor=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0))
            descriptor.compress_type=zipfile.ZIP_STORED
            descriptor.external_attr=0o644<<16
            archive.writestr(descriptor,data)
    return target.stat().st_size
def main()->None:
    if os.getenv('GITHUB_ACTIONS')!='true':
        raise SystemExit('Real browser comparisons are only permitted on disposable CI')
    runtime_doc=json.loads((RUNTIME/'runtime.json').read_text())
    runtime_sha=sha_file(RUNTIME/'runtime.json')
    binary=RUNTIME/runtime_doc['files']['browser']['path']
    require(binary.is_file() and sha_file(binary)==runtime_doc['files']['browser']['sha256'],
            'Owner-selected browser fingerprint mismatch')
    fixture=json.loads(run([str(FIXTURE),runtime_sha],30))
    require(fixture['schema']=='motionwright.original-native-delta-e2e/1',
            'Unknown native source-delta schema')
    draft=fixture['source_invalidation']
    require(draft['schema']=='motionwright.native-source-invalidation/1'
            and draft['reusable_intervals']==[{'start':0,'end_exclusive':30}]
            and draft['dirty_intervals']==[{'start':30,'end_exclusive':90}]
            and not draft['rendered_pixel_equivalence_verified']
            and draft['actual_native_frames_avoided']==0,
            'Native source-level invalidation overstated real execution/overlap')
    output=ROOT/'verification/native-dirty-e2e'
    output.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='motionwright-native-dirty-oracle-')as temp:
        root=Path(temp)
        work=root/'work';results=root/'output';assets=root/'assets'
        for p in (work,results,assets):p.mkdir()
        stages={}
        for label,offset in (('before',1),('after',2)):
            row=fixture[label]
            job='hf-'+UUID(int=offset).hex
            jobdir=work/job;dest=results/job;jobdir.mkdir();dest.mkdir()
            plan=row['plan_json'].encode()
            source=row['source_html'].encode()
            require(digest(source)==row['source_sha256'],'Original HTML was not source-bound')
            (jobdir/'plan.json').write_bytes(plan)
            (jobdir/'index.html').write_bytes(source)
            command=[
                str(RUNNER),'render','--runtime-root',str(RUNTIME),
                '--node-sealed',str(Path(shutil.which('node')).resolve()),
                '--ffmpeg-sealed',str(Path(shutil.which('ffmpeg')).resolve()),
                '--chromium-sealed',str(binary),
                '--work-root',str(work),'--output-root',str(results),
                '--assets-root',str(assets),
                '--job',job,'--plan-sha256',digest(plan),
                '--source-sha256',row['source_sha256']
            ]
            start=perf_counter_ns()
            run(command,300)
            rendering_ns=perf_counter_ns()-start
            manifest=json.loads((dest/'frames.json').read_text())
            require(manifest['source_sha256']==row['source_sha256']
                    and manifest['frame_count']==FRAME_COUNT
                    and manifest['observation']['coverage']=='all_frames',
                    'Full native oracle failed to render every source frame')
            require(manifest['sandbox_mode']=='chromium-userns',
                    'Direct acceptance cannot relax Chromium native user namespace')
            for i,receipt in enumerate(manifest['frames']):
                require(i==receipt['frame'],'Native capture index drift')
                path=dest/receipt['relative_path']
                require(path.is_file() and sha_file(path)==receipt['sha256'],
                        'PNG source hash drift during source-bound native acceptance')
            stages[label]=(dest,manifest,rendering_ns)
        before_dir,before,render_before_ns=stages['before']
        after_dir,after,render_after_ns=stages['after']
        # Execute the same *original* admitted HyperFrames capture engine, not
        # a Python pillow/FFmpeg facsimile. This bounded command is a separate
        # direct-renderer window job; it intentionally cannot produce a master.
        after_source=fixture['after'];window_job='hf-'+UUID(int=3).hex
        winwork=work/window_job;winoutput=results/window_job
        winwork.mkdir();winoutput.mkdir()
        plan=after_source['plan_json'].encode()
        html=after_source['source_html'].encode()
        (winwork/'plan.json').write_bytes(plan)
        (winwork/'index.html').write_bytes(html)
        window_args=[
            str(RUNNER),'render-window','--runtime-root',str(RUNTIME),
            '--node-sealed',str(Path(shutil.which('node')).resolve()),
            '--ffmpeg-sealed',str(Path(shutil.which('ffmpeg')).resolve()),
            '--chromium-sealed',str(binary),
            '--work-root',str(work),'--output-root',str(results),
            '--assets-root',str(assets),
            '--job',window_job,'--plan-sha256',digest(plan),
            '--source-sha256',after_source['source_sha256'],
            '--first-frame','30','--end-frame-exclusive','90'
        ]
        window_start=perf_counter_ns()
        run(window_args,300)
        actual_window_render_ns=perf_counter_ns()-window_start
        window=json.loads((winoutput/'frames.json').read_text())
        window_result=json.loads((winoutput/'result.json').read_text())
        window_rows=(winoutput/'observations.ndjson').read_text().splitlines()
        require(window['schema']=='motionwright.hyperframes-native-frame-window/1'
            and window_result['schema']=='motionwright.hyperframes-native-frame-window-result/1',
            'Partial rendering falsely described itself as a mastered video')
        require(window['timeline_total_frames']==FRAME_COUNT
            and window['frame_count']==60
            and window['selected_window']=={'start':30,'end_exclusive':90}
            and window['observation']['coverage']=='selected_window'
            and window_result['complete_master'] is False
            and window_result['mezzanine'] is None
            and not (winoutput/'mezzanine.mkv').exists(),
            'Window render tried to claim a complete timeline/encoded master')
        require(len(window['frames'])==len(window_rows)==60
            and [entry['frame'] for entry in window['frames']]==list(range(30,90))
            and all(json.loads(row)['frame'] in range(30,90) for row in window_rows),
            'Partial renderer did not actually skip the first thirty native frame observations')
        require(not any((winoutput/'frames'/f'frame-{i:06}.png').exists()for i in range(30)),
            'Skipped native PNG frames were unexpectedly rendered')
        require(all(sha_file(winoutput/'frames'/f'frame-{frame:06}.png')==
              after['frames'][frame]['sha256']for frame in range(30,90)),
              'Partial native capture diverges from the identical full-after source')
        # No owner cache permission/claim may emerge from a bounded frame
        # render request. Cache custody is a separately validated receiver.
        require(window_result['native_frames_rendered']==60
                and window_result['native_frames_reused']==0
                and window_result['owner_cache_authority']=='NOT_GRANTED',
                'Partial renderer attempted to claim unauthorized cache reuse')
        # Negative interval controls must fail before executing a renderer.
        for first,last in [('90','90'),('91','92'),('0','3601')]:
            forbidden=list(window_args)
            forbidden[-3]=first;forbidden[-1]=last
            completed=subprocess.run(forbidden,capture_output=True,timeout=45,check=False)
            require(completed.returncode!=0 and b'Frame window' in completed.stderr,
                'Invalid/noncanonical source frame interval was not rejected at the contract gate')
        reusable=set(range(0,30));dirty=set(range(30,90))
        require(len(reusable|dirty)==FRAME_COUNT and not reusable&dirty,
                'Source frame partition was not exhaustive/disjoint')
        verify_start=perf_counter_ns()
        for frame in reusable:
            require(before['frames'][frame]['sha256']==after['frames'][frame]['sha256'],
                    f'Native full-render oracle disproves source reuse for frame {frame}')
        source_validation_ns=perf_counter_ns()-verify_start
        zip_manifest={
            'schema':'motionwright.native-dirty-png-transfer/1',
            'before_source_sha256':before['source_sha256'],
            'after_source_sha256':after['source_sha256'],
            'total_frames':FRAME_COUNT,
            'reusable_intervals':draft['reusable_intervals'],
            'reusable_frame_receipts':[before['frames'][i]['sha256'] for i in range(30)],
            'dirty_frame_receipts':[after['frames'][i]['sha256'] for i in range(30,90)],
            'dirty_intervals':draft['dirty_intervals'],
            'metadata_reused':False,
            'native_frames_not_rendered':0,
            'encoder_stage_avoided':False,
            'source_only_visual_not_audio':True
        }
        full_zip=root/'full-after.zip';patch_zip=output/'dirty-60frames.zip'
        full_bytes=make_frame_zip(full_zip,after_dir/'frames',list(range(FRAME_COUNT)),after)
        patch_bytes=make_frame_zip(patch_zip,winoutput/'frames',sorted(dirty),after)
        # Actual receiver path, independently verifying EVERY original cache
        # receipt and every transmitted new PNG without reading the after
        # full-render source directory. A forged/stale cache fails closed.
        receipt_file=output/'transfer-source.json'
        receipt_file.write_text(json.dumps(zip_manifest,indent=2)+'\n')
        reconstructed=root/'rebuilt'
        receiver=rebuild(patch_zip,receipt_file,before_dir/'frames',reconstructed)
        require(receiver['receipts_verified']=='ALL_FRAME_SHA256'
                and receiver['frames']==FRAME_COUNT
                and receiver['reused']==30 and receiver['transferred']==60,
                'Independent receiver did not reconstruct precisely the intended timeline')
        for index in range(FRAME_COUNT):
            name=f'frame-{index:06}.png'
            require(sha_file(reconstructed/name)==after['frames'][index]['sha256'],
                    f'Independent receiver frame {index} differs from full-after native screenshot')
        actual_bundle_bytes=patch_bytes+receipt_file.stat().st_size
        require(actual_bundle_bytes<full_bytes,
                'Dirty transfer plus its source receipt did not measurably reduce bytes')
        input_mkv=after_dir/'mezzanine.mkv'
        encoded=root/'rebuilt.mkv'
        encode_start=perf_counter_ns()
        run([shutil.which('ffmpeg'),'-nostdin','-hide_banner','-v','error',
             '-framerate','30','-i',str(reconstructed/'frame-%06d.png'),
             '-c:v','ffv1','-pix_fmt','bgra','-level','3',str(encoded)],90)
        encode_ns=perf_counter_ns()-encode_start
        def ffv1_md5(file:Path)->bytes:
            return run([shutil.which('ffmpeg'),'-nostdin','-hide_banner','-v','error',
                        '-i',str(file),'-pix_fmt','bgra','-f','framemd5','-'],90)
        decoded_oracle=ffv1_md5(input_mkv)
        decoded_rebuilt=ffv1_md5(encoded)
        require(decoded_oracle==decoded_rebuilt,
                'Full native FFV1 decode differs from patch-reassembled FFV1 frames')
        # Keep modest samples and a contact sheet, not whole temporary renders.
        selected=(0,29,30,45,89)
        sheet=Image.new('RGB',(320*len(selected),390),(20,24,30))
        painter=ImageDraw.Draw(sheet)
        for index,col in enumerate(selected):
            for label,offset in (('before',0),('after',195)):
                with Image.open(stages[label][0]/'frames'/f'frame-{col:06}.png')as img:
                    preview=img.convert('RGB').resize((320,180))
                    sheet.paste(preview,(320*index,offset+15))
                painter.text((320*index+8,offset+2),f'{label} frame {col}',fill=(232,237,240))
        sheet.save(output/'native-dirty-review.png')
        measurements={
            'schema':'motionwright.native-dirty-direct-oracle/1',
            'motionwright_sha':run(['git','rev-parse','HEAD'],10).decode().strip(),
            'before_source_sha256':before['source_sha256'],
            'after_source_sha256':after['source_sha256'],
            'native_renderer_pinned_runtime_sha256':runtime_sha,
            'frame_count':FRAME_COUNT,
            'same_source_full_vs_patch_exact_png':'PASS',
            'decoded_FFV1_full_vs_patch':'PASS',
            'dirty_intervals':draft['dirty_intervals'],
            'reused_intervals':draft['reusable_intervals'],
            'rendered_native_frames_in_actual_test':240,
            'native_rendered_after_window_only':60,
            'native_rendered_full_after_oracle':90,
            'actually_skipped_native_frames_in_partial_after_run':30,
            'actually_avoided_native_render_frames':0,
            'actually_avoided_codec_frames':0,
            'frame_receipts_equivalent':True,
            'source_readback_reused':False,
            'actual_transfer_full_zip_bytes':full_bytes,
            'actual_transfer_dirty_zip_bytes':patch_bytes,
            'actual_transfer_dirty_bundle_bytes':actual_bundle_bytes,
            'actual_transfer_reduction_bytes':full_bytes-actual_bundle_bytes,
            'independent_patch_receiver':'PASS_SHA256_FOR_ALL_90_FRAMES',
            'validation_measured_ns':source_validation_ns,
            'native_before_render_measured_ns':render_before_ns,
            'native_after_full_render_measured_ns':render_after_ns,
            'reconstructed_ffv1_encoding_measured_ns':encode_ns,
            'native_after_window_render_measured_ns':actual_window_render_ns,
            'inferred_render_speedup':'NOT_MEASURED_OR_CLAIMED',
            'render_window_source_digest':window['source_sha256'],
            'render_window_master_status':'NOT_PRODUCED',
            'window_observation_readback_frames':60,
            'audio_spring_shutter_and_transition_independence':'NOT_VERIFIED',
            'creative_approval':'NOT_REVIEWED'
        }
        (output/'result.json').write_text(json.dumps(measurements,indent=2)+'\n')

        print(json.dumps({
            'source_bound_native_delta':'PASS',
            'frames_oracle_verified':FRAME_COUNT,
            'reuse_png_candidates':30,
            'after_frames_actually_skipped_in_partial_render':30,
            'actual_native_render_frames_avoided_over_three_acceptance_runs':0,
            'transfer_bytes_saved':full_bytes-actual_bundle_bytes
        }))
if __name__=='__main__':main()
