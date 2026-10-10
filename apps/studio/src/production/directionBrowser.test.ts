import {expect,it} from 'vitest';
import {creativeDirectionSourceState,creativeDirectionStudy} from '../api';
import {fixtureProject} from '../fixture';
import type {CreativeDirectionStudyRequest} from './directionTypes';

it('browser preview never invents a canonical creative source fingerprint',async()=>{
 const project=structuredClone(fixtureProject);
 await expect(creativeDirectionSourceState(project)).rejects.toThrow('canonical desktop service');
});
it('browser preview never chooses a user concept or verifies media rights',async()=>{
 const project=structuredClone(fixtureProject);
 const request={
  schema:'motionwright.creative-direction-study/1',
  project_id:project.id,generation:project.generation,revision:project.revision,
  project_sha256:'ab'.repeat(32),product_version:'owner-build',
  references:[{}],alternatives:[{},{}]
 }as CreativeDirectionStudyRequest;
 await expect(creativeDirectionStudy(project,request)).rejects.toThrow('browser demo');
});
it('a modified project scope cannot reuse a creative source study',async()=>{
 const project=structuredClone(fixtureProject);
 const request={
  schema:'motionwright.creative-direction-study/1',
  project_id:project.id,generation:project.generation,revision:project.revision+1,
  project_sha256:'ab'.repeat(32),product_version:'owner-build',
  references:[{}],alternatives:[{},{}]
 }as CreativeDirectionStudyRequest;
 await expect(creativeDirectionStudy(project,request)).rejects.toThrow();
});
