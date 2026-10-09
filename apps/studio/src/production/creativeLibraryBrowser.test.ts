import {expect,it} from 'vitest';
import {creativeComponentCatalog,creativeComponentProposal,creativeDataNormalize,creativeSoundAudition} from '../api';
import {fixtureProject} from '../fixture';

it('requires real desktop source-admission for catalog, component generation and data provenance',async()=>{
    const project=structuredClone(fixtureProject);
    await expect(creativeComponentCatalog()).rejects.toThrow('native service');
    await expect(creativeComponentProposal(project,project.scenes[0].id,project.deliverables[0].id,
      {} as never,{} as never,{} as never)).rejects.toThrow('not simulated');
    await expect(creativeDataNormalize({} as never)).rejects.toThrow('native domain service');
    await expect(creativeSoundAudition(project,{} as never,'ab'.repeat(32))).rejects.toThrow('local native domain service');
});
