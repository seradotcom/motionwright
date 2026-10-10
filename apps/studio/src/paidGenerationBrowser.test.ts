import {expect,it} from 'vitest';
import {fixtureProject} from './fixture';
import {
 nativePaidGenerationPreflight,nativePaidGenerationReservePreflight,
 nativePaidGenerationJournal
} from './api';
import type {PaidGenerationSpec} from './paidGenerationTypes';

it('the browser demo never creates an imaginary paid provider task or reserves money',async()=>{
 const project=structuredClone(fixtureProject);
 await expect(nativePaidGenerationPreflight(project,{} as PaidGenerationSpec))
   .rejects.toThrow('browser demos cannot reserve or charge');
 await expect(nativePaidGenerationReservePreflight(project,'018f0000-0000-7000-8000-000000000001'))
   .rejects.toThrow('canonical desktop project revision');
 await expect(nativePaidGenerationJournal(project))
   .rejects.toThrow('not simulated in a browser demo');
});
