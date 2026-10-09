import {expect,it} from 'vitest';
import {fixtureProject} from '../fixture';
import {nativeHtmlFrameSelection} from './NativeCreativeEditor';
import {nativeCreativeAvailable,nativeDocumentState,previewNativeEdit,proposeNativeCanvas,renderNativeHtml,recoverNativeHtmlPreview} from '../api';
import type {HyperframesRenderEvidence} from './nativeTypes';
function fixture(){
  const project=structuredClone(fixtureProject),scene=project.scenes[0],profile=project.deliverables[0];
  const artifact={relative_path:'hf-0123456789abcdef0123456789abcdef/result.json',sha256:'ab'.repeat(32),bytes:100,media_type:'application/json'};
  const evidence:HyperframesRenderEvidence={project_id:project.id,generation:project.generation,revision:project.revision,scene_id:scene.id,document_id:'native-source',profile_id:profile.id,job_ref:'hf-0123456789abcdef0123456789abcdef',source_sha256:'ab'.repeat(32),plan_sha256:'cd'.repeat(32),rate:{num:30000,den:1001},width:1920,height:1080,frame_count:210,alpha:true,color:'srgb',frames:artifact,mezzanine:artifact,source:artifact,document:artifact,observations:artifact,runtime_receipt_sha256:'ef'.repeat(32),creative_approval:'required',preview:[{token:'server-owned-test-grant',segment_id:'native-source',scene_ids:[scene.id],frame_count:210}]};
  return {project,scene,profile,evidence};
}
it('selects a rational native sample only from the current generation, revision and output',()=>{
  const {project,scene,profile,evidence}=fixture();
  expect(nativeHtmlFrameSelection(project,scene,profile,evidence,1.001)?.frameIndex).toBe(30);
  expect(nativeHtmlFrameSelection(project,scene,profile,evidence,999)?.frameIndex).toBe(209);
  expect(nativeHtmlFrameSelection({...project,revision:project.revision+1},scene,profile,evidence,0)).toBeNull();
  expect(nativeHtmlFrameSelection({...project,generation:'new-generation'},scene,profile,evidence,0)).toBeNull();
  expect(nativeHtmlFrameSelection(project,scene,project.deliverables[1],evidence,0)).toBeNull();
  expect(nativeHtmlFrameSelection(project,scene,profile,{...evidence,preview:[]},0)).toBeNull();
  expect(nativeHtmlFrameSelection(project,scene,profile,{...evidence,preview:[{...evidence.preview[0],scene_ids:['another-scene']}]},0)).toBeNull();
});
it('does not simulate native validation, frames or recovery in browser mode',async()=>{
  const {project,scene,profile}=fixture();expect(nativeCreativeAvailable()).toBe(false);
  await expect(nativeDocumentState(project,'source')).rejects.toThrow('desktop');
  await expect(proposeNativeCanvas(project,scene.id,profile.id)).rejects.toThrow('not simulated');
  await expect(previewNativeEdit(project,{kind:'remove_native_scene',id:'source',expected_source_sha256:'ab'.repeat(32)})).rejects.toThrow('desktop');
  await expect(renderNativeHtml(project,'source','attempt')).rejects.toThrow('installed');
  await expect(recoverNativeHtmlPreview(project,'source','attempt')).rejects.toThrow('desktop');
});
