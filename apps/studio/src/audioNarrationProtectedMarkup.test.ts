import {expect,it} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import AudioWorkspace from './AudioWorkspace';
import {fixtureProject} from './fixture';
import {emptyProductionDesign} from './creativeProduction';

function render(protectedSource:boolean):string{
  const project=structuredClone(fixtureProject);
  const track='018f0000-0000-7000-8000-000000000020';
  const transcript='018f0000-0000-7000-8000-000000000030';
  project.audio.active_voice_track_id=track;
  project.audio.voice_tracks=[{
    id:track,asset_id:'018f0000-0000-7000-8000-000000000010',
    label:'Source original',sample_rate_hz:48000,channels:1,
    measured_duration:{num:'3',den:'1'},
    source_sha256:'ab'.repeat(32),loudness_lufs:null,true_peak_dbfs:null
  }];
  project.audio.transcript=[{
    id:transcript,voice_track_id:track,
    start:{num:'0',den:'1'},end:{num:'2',den:'1'},
    text:'Keep original source',
    speaker:null,alignment:{kind:'manual'}
  }];
  project.production_design={
    ...emptyProductionDesign(),
    narration_take_lock:protectedSource?{
      voice_track_id:track,voice_asset_id:'018f0000-0000-7000-8000-000000000010',
      voice_asset_sha256:'ab'.repeat(32),source_content_sha256:'cd'.repeat(32),
      source_project_revision:11,recorded_reviewer:'Original owner',
      recorded_reason:'Preserve original voice and captions',
      reviewer_identity_authenticated:false,media_bytes_independently_decoded:false,
      artistic_quality_approved:false,publication_approved:false
    }:null
  };
  return renderToStaticMarkup(createElement(AudioWorkspace,{
    project,commit:async()=>{},desktopMode:true,
    importVoice:async()=>{},playhead:0,onSeek:()=>{}
  }));
}
function input(markup:string,control:string):string{
  const tag=markup.match(new RegExp('<(?:input|textarea|button)\\b[^>]*aria-label="'+control+'"[^>]*>'));
  if(!tag)throw new Error('Source control missing: '+control);
  return tag[0];
}
it('protected original narration disables all source edits while preserving seeking and mix',()=>{
  const unlocked=render(false), locked=render(true);
  const fields=[
    'Voice file absolute path',
    'Start 018f0000-0000-7000-8000-000000000030',
    'End 018f0000-0000-7000-8000-000000000030',
    'Speaker 018f0000-0000-7000-8000-000000000030',
    'Transcript 018f0000-0000-7000-8000-000000000030',
    'New transcript start','New transcript end','New transcript speaker','New transcript text',
    'Cue time 018f0000-0000-7000-8000-000000000221',
    'Cue label 018f0000-0000-7000-8000-000000000221',
    'New cue time','New cue label'
  ];
  for(const field of fields){
    expect(input(locked,field),field).toContain('disabled');
    expect(input(unlocked,field),field).not.toContain('disabled');
  }
  expect(input(locked,'Music gain dB')).not.toContain('disabled');
  expect(input(locked,'Voice gain dB')).not.toContain('disabled');
  expect(locked).toContain('Use the explicit recorded-source release');
  expect(locked).toContain('Release exact recorded source');
  expect(unlocked).not.toContain('Use the explicit recorded-source release');
  expect(locked).toContain('Seek to transcript at');
});
