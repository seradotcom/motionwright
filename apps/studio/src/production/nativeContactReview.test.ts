import {expect,it} from 'vitest';
import {exactGrant,samplesFor} from './NativeHtmlContactReview';
import type {HyperframesRenderEvidence} from './nativeTypes';
it('contact coverage is capped to five distinct frame-aligned samples',()=>{
    expect(samplesFor(1)).toEqual([0]);
    expect(samplesFor(2)).toEqual([0,1]);
    expect(samplesFor(3)).toEqual([0,1,2]);
    expect(samplesFor(90)).toEqual([0,22,44,66,89]);
    expect(samplesFor(3600)).toHaveLength(5);
});
it('readback requires one matching source-owned exact scene/interval grant',()=>{
    const evidence={
      document_id:'native-source',scene_id:'source-scene',frame_count:90,
      preview:[{token:'source-only-test-token',segment_id:'native-source',scene_ids:['source-scene'],frame_count:90}]
    }as HyperframesRenderEvidence;
    expect(exactGrant(evidence)).toBe('source-only-test-token');
    expect(exactGrant({...evidence,preview:[]})).toBeNull();
    expect(exactGrant({...evidence,preview:[{...evidence.preview[0],scene_ids:['other']}]})).toBeNull();
    expect(exactGrant({...evidence,preview:[{...evidence.preview[0],frame_count:89}]})).toBeNull();
    expect(exactGrant({...evidence,preview:[...evidence.preview,...evidence.preview]})).toBeNull();
});
