import {expect,it} from 'vitest';
import {nativeLocalizedRepairPreflight} from '../api';
import {fixtureProject} from '../fixture';

it('browser demo must not invent a native localized repair or a trusted source comparison',async()=>{
 const project=structuredClone(fixtureProject);
 await expect(nativeLocalizedRepairPreflight(project,'fixture-source','ab'.repeat(32),
   'Propose a single source-bound source correction',[{
     kind:'set_pose',node_id:'fixture-node',property:'y',expected:20,next:22
   }])).rejects.toThrow('desktop domain service');
});
