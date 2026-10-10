import {expect,it} from 'vitest';
import {nativeNarrationSourceSnapshot,nativeNarrationReplacementImpact} from './api';
import {fixtureProject} from './fixture';
import type {NarrationSourceSnapshot} from './narrationReviewTypes';

it('cannot invent a measured approved narration from an unconnected browser preview',async()=>{
  const project=structuredClone(fixtureProject);
  await expect(nativeNarrationSourceSnapshot(project))
    .rejects.toThrow('canonical local desktop service');
});
it('cannot claim a revision comparison without a real original source and desktop authority',async()=>{
  const project=structuredClone(fixtureProject);
  project.revision=17;
  const snapshot={
    project_id:project.id,generation:project.generation,
    revision:16,exact_content_sha256:'ab'.repeat(32)
  } as NarrationSourceSnapshot;
  await expect(nativeNarrationReplacementImpact(project,snapshot))
    .rejects.toThrow('canonical local desktop service');
});
