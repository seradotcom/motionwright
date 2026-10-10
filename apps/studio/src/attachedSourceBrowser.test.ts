import {expect,it} from 'vitest';
import {nativeAttachedSourceInspection} from './api';
import {fixtureProject} from './fixture';
import {emptyProductionDesign} from './creativeProduction';

it('browser preview never fabricates GLB inspection or semantic editability',async()=>{
 const project=structuredClone(fixtureProject);
 await expect(nativeAttachedSourceInspection(project,'00000000-0000-4000-8000-000000000001'))
   .rejects.toThrow('connected desktop service');
});
it('a source with no owner project capsule is not a trusted native input',async()=>{
 const project=structuredClone(fixtureProject);
 // The browser demo must fail even for a plausible source/capsule ID.
 project.production_design=emptyProductionDesign();
 await expect(nativeAttachedSourceInspection(project,'00000000-0000-4000-8000-000000000002'))
   .rejects.toThrow('connected desktop service');
});
