import {expect,it} from 'vitest';
import {creativeDistillationExperiment} from '../api';
import {fixtureProject} from '../fixture';
it('refuses forged source distillation or plugin-install authority in browser demo',async()=>{
 const project=structuredClone(fixtureProject);
 await expect(creativeDistillationExperiment(project,'Investigate a source',
  {} as never,{} as never,{} as never,[
   {original_source_sha256:'ab'.repeat(32),polarity:'positive',observed_strength_or_failure:'Good',source_rights_note:'Owned source',source_owner_attested_rights:true},
   {original_source_sha256:'cd'.repeat(32),polarity:'positive',observed_strength_or_failure:'Good',source_rights_note:'Owned source',source_owner_attested_rights:true},
   {original_source_sha256:'de'.repeat(32),polarity:'negative',observed_strength_or_failure:'Bad',source_rights_note:'Owned source',source_owner_attested_rights:true},
   {original_source_sha256:'ef'.repeat(32),polarity:'negative',observed_strength_or_failure:'Bad',source_rights_note:'Owned source',source_owner_attested_rights:true},
  ],Array(9).fill({} as never))).rejects.toThrow('local desktop domain');
});
