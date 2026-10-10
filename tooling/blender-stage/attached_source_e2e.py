#!/usr/bin/env python3
"""Owner-free, disposable real Blender GLB attachment observation acceptance."""
from __future__ import annotations
import hashlib,json,os,subprocess,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
FIXTURE=ROOT/'target/debug/examples/native_catalog_fixture'
INSPECTOR=ROOT/'target/debug/examples/attached_source_probe'
BLENDER=ROOT/'runtime/blender-stage/fixed_render.py'

def digest(path:Path)->str:
    with path.open('rb')as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()
def invoke(args:list[str],limit:int=240)->str:
    result=subprocess.run(args,capture_output=True,text=True,timeout=limit,check=False)
    if result.returncode:
        raise AssertionError('Native source observation process failed: '+
            result.stdout[-1400:]+result.stderr[-2200:])
    if 'Traceback (most recent call last)' in result.stdout or 'Traceback (most recent call last)' in result.stderr:
        raise AssertionError('Blender failure was hidden by a misleading status code')
    return result.stdout
def main()->None:
    if os.environ.get('GITHUB_ACTIONS')!='true':
        raise SystemExit('Native Blender source verification is CI-only')
    destination=ROOT/'verification/attached-native-glb'
    destination.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='motionwright-original-attach-')as temp:
        assets=Path(temp)/'assets';assets.mkdir()
        stage=json.loads(invoke([str(FIXTURE),'device-stage','landscape','en','0'*64,'none','none']))
        if stage['output']['backend']!='blender_stage':
            raise AssertionError('Fixture was not produced by the actual typed Blender stage')
        original=Path(temp)/'source.json'
        original.write_text(json.dumps(stage['output']['source'],sort_keys=True,separators=(',',':')))
        output=Path(temp)/'native';output.mkdir()
        invoke(['blender','-b','--factory-startup','-noaudio','--python',str(BLENDER),
                '--',str(original),str(output),str(assets),'0,30,89'],360)
        glb=output/'stage.glb'
        blender=output/'stage.blend'
        receipt=json.loads((output/'stage.json').read_text())
        if digest(glb)!=receipt['interchange']['sha256'] or digest(blender)!=receipt['original_source']['sha256']:
            raise AssertionError('Original stage asset digest changed before attached inspection')
        readback=destination/'source-attach-observation.json'
        invoke([str(INSPECTOR),str(glb),str(readback)],45)
        observation=json.loads(readback.read_text())
        if (observation['source_sha256']!=digest(glb)
            or observation['schema']!='motionwright.attached-native-source-observation/1'
            or observation['source_format']!='glb_2_0_structured_attach'
            or observation['semantically_editable_by_motionwright']
            or observation['runtime_or_imported_code_executed']
            or not observation['original_binary_preserved']
            or not observation['glb_chunks']
            or not any(p['path']=='nodes' for p in observation['properties'])):
            raise AssertionError('Real Blender GLB structure did not preserve opaque source and observable identity')
        result={
            'schema':'motionwright.real-blender-source-observation-e2e/1',
            'motionwright_sha':invoke(['git','rev-parse','HEAD'],10).strip(),
            'actual_glb_sha256':digest(glb),
            'original_editable_blend_sha256':digest(blender),
            'typed_observed_properties':len(observation['properties']),
            'exact_glb_chunks':len(observation['glb_chunks']),
            'preserved_used_extensions':observation['used_extensions'],
            'semantic_editability':'NOT_ADMITTED',
            'renderer_pixels':'SEPARATE_NATIVE_STAGE_GATE',
            'owner_project_import':'NOT_PERFORMED',
            'creative_approval':'NOT_REVIEWED',
            'result':'PASS'
        }
        (destination/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
if __name__=='__main__':main()
