import {expect,it} from 'vitest';
import {applyChange} from './api';
import {fixtureProject} from './fixture';

it('browser-only creative demos cannot record forged source approval',async()=>{
  const project=structuredClone(fixtureProject);
  const old=structuredClone(project);
  await expect(applyChange(project,{
    type:'record_narration_take',
    expected_source_sha256:'ab'.repeat(32),
    expected_project_revision:project.revision,
    reviewer:'Not authenticated user',
    reason:'I falsely approved this voice'
  })).rejects.toThrow('canonical desktop StudioService revision');
  expect(project).toEqual(old);
});
it('browser-only creative demos cannot silently release a recorded source',async()=>{
  const project=structuredClone(fixtureProject);
  await expect(applyChange(project,{
    type:'release_narration_take',
    expected_source_sha256:'cd'.repeat(32),
    reviewer:'Not authenticated',
    reason:'Forged source unlock'
  })).rejects.toThrow('canonical desktop StudioService revision');
});
