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
        reusable=set(range(0,30));dirty=set(range(30,90))
        require(len(reusable|dirty)==FRAME_COUNT and not reusable&dirty,
                'Source frame partition was not exhaustive/disjoint')
        verify_start=perf_counter_ns()
        for frame in reusable:
            require(before['frames'][frame]['sha256']==after['frames'][frame]['sha256'],
                    f'Native full-render oracle disproves source reuse for frame {frame}')
        source_validation_ns=perf_counter_ns()-verify_start
        reconstructed=root/'rebuilt';reconstructed.mkdir()
        for index in range(FRAME_COUNT):
            chosen=before_dir if index in reusable else after_dir
            name=f'frame-{index:06}.png'
            shutil.copyfile(chosen/'frames'/name,reconstructed/name)
            require(sha_file(reconstructed/name)==after['frames'][index]['sha256'],
                    f'Reconstructed frame {index} differs from full-after native screenshot')
        zip_manifest={
            'schema':'motionwright.native-dirty-png-transfer/1',
            'before_source_sha256':before['source_sha256'],
            'after_source_sha256':after['source_sha256'],
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
        patch_bytes=make_frame_zip(patch_zip,after_dir/'frames',sorted(dirty),after)
        # Actually reconstruct a consumer's source-bound frame output with only
        # the transmitted dirty PNGs and its intact previously validated cache.
        with zipfile.ZipFile(patch_zip) as received:
            require(len(received.namelist())==60,'Transfer unexpectedly carries more than changed PNGs')
            for frame in dirty:
                require(digest(received.read(f'frames/frame-{frame:06}.png'))==
                        after['frames'][frame]['sha256'],'Transferred patch changed source bytes')
        require(patch_bytes<full_bytes,'Dirty frame transfer did not measurably reduce PNG bytes')
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
            'rendered_native_frames_in_actual_test':180,
            'actually_avoided_native_render_frames':0,
            'actually_avoided_codec_frames':0,
            'frame_receipts_equivalent':True,
            'source_readback_reused':False,
            'actual_transfer_full_zip_bytes':full_bytes,
            'actual_transfer_dirty_zip_bytes':patch_bytes,
            'actual_transfer_reduction_bytes':full_bytes-patch_bytes,
            'validation_measured_ns':source_validation_ns,
            'native_before_render_measured_ns':render_before_ns,
            'native_after_full_render_measured_ns':render_after_ns,
            'reconstructed_ffv1_encoding_measured_ns':encode_ns,
            'inferred_render_speedup':'NOT_MEASURED_OR_CLAIMED',
            'audio_spring_shutter_and_transition_independence':'NOT_VERIFIED',
            'creative_approval':'NOT_REVIEWED'
        }
        (output/'result.json').write_text(json.dumps(measurements,indent=2)+'\n')
        (output/'transfer-source.json').write_text(json.dumps(zip_manifest,indent=2)+'\n')
        print(json.dumps({
            'source_bound_native_delta':'PASS',
            'frames_oracle_verified':FRAME_COUNT,
            'reuse_png_candidates':30,
            'actual_native_render_frames_avoided':0,
            'transfer_bytes_saved':full_bytes-patch_bytes
        }))
if __name__=='__main__':main()
