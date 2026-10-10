import {expect,it} from 'vitest';
import {nativeOriginalMixAudition} from './api';
import {fixtureProject} from './fixture';
it('does not fabricate a local voice/music/SFX mix in a browser-only prototype',async()=>{
  const project=structuredClone(fixtureProject);
  await expect(nativeOriginalMixAudition(project,{
    deliverable_profile_id:'sample-profile',
    music_asset_id:'music',
    sfx_asset_id:'sfx',
    sfx_gain_db:-8,duck_attenuation_db:14,
    duck_attack_samples:2400,duck_release_samples:4800,
    owner_attests_preview_rights:true
  })).rejects.toThrow('verified desktop domain service');
});
