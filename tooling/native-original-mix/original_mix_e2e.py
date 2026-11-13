#!/usr/bin/env python3
"""First-party *synthetic proxy*, independently metered actual voice/music/SFX mix.

No generated speech or licensed music, no LUFS mastering and no auto PASS for
intelligibility. Runs ONLY on disposable CI and preserves all original stems.
"""
from __future__ import annotations
import array, hashlib, json, math, os, re, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tooling'))
from native_original_mix_custody import OriginalPcmCustody
FIXTURE=ROOT/'target/debug/examples/native_mix_fixture'
FRAMES=115200
RATE=48000
WINDOWS=((28800,52800),(72000,91200))
LANGUAGES=('en','es','de')

def hash_bytes(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def sha(path:Path)->str:return hashlib.file_digest(path.open('rb'),'sha256').hexdigest()
def require(yes:bool,why:str)->None:
    if not yes:raise AssertionError(why)
def make_synthetic_proxy(locale:str)->dict[str,bytes]:
    require(locale in LANGUAGES,'Unknown synthetic localization')
    center={'en':190.0,'es':205.0,'de':220.0}[locale]
    output={name:array.array('h') for name in ('voice','music','sfx')}
    for frame in range(FRAMES):
        t=frame/RATE
        # Proxy deliberately consists of tones and MUST NOT be treated as speech.
        active=any(start<=frame<end for start,end in WINDOWS)
        if active:
            begin=next(start for start,end in WINDOWS if start<=frame<end)
            end=next(end for start,end in WINDOWS if start<=frame<end)
            ramp=min(1.0,(frame-begin)/400,(end-1-frame)/400)
            vocal=0.14*max(0.0,ramp)*math.sin(math.tau*center*t)
        else:vocal=0.0
        fade=min(1.0,frame/600)
        music=fade*(0.12*math.sin(math.tau*100.0*t)
                    +0.045*math.sin(math.tau*155.0*t))
        cue=0.0
        if 57000<=frame<67500:
            local=frame-57000
            envelope=min(1.0,local/200,(67500-1-frame)/800)
            cue=0.065*max(0.0,envelope)*math.sin(math.tau*(260.0+70.0*local/10500.0)*t)
        for name,sample in (('voice',vocal),('music',music),('sfx',cue)):
            i16=int(round(sample*32767))
            require(-32768<=i16<=32767,'Original fixture generator would clip')
            output[name].extend([i16,i16])
    if sys.byteorder!='little':
        for samples in output.values():samples.byteswap()
    return {name:samples.tobytes() for name,samples in output.items()}
def decode_pcm(raw:bytes,index:int)->float:
    offset=index*4
    return int.from_bytes(raw[offset:offset+2],'little',signed=True)/32768.0

def independent_measure(wav:Path,captures:OriginalPcmCustody,locale:str)->dict:
    original=captures.observe(wav)
    probe=captures.run(['ffprobe','-v','error','-show_streams','-show_format',
                        '-of','json',str(wav)],locale+'-ffprobe',inputs=[original])
    info=json.loads(probe['stdout'])
    audio=[s for s in info['streams'] if s.get('codec_type')=='audio']
    require(len(audio)==1 and audio[0]['codec_name']=='pcm_s16le'
            and audio[0]['sample_rate']=='48000' and audio[0]['channels']==2,
            'Actual mix WAV stream is not canonical 48 kHz stereo PCM')
    decode=captures.run(['ffmpeg','-nostdin','-v','error','-i',str(wav),
                        '-c:a','pcm_s16le','-f','s16le','-ac','2','-ar','48000','-'],
                        locale+'-decode',30,inputs=[original])
    decoded=decode['stdout']
    require(decoded==wav.read_bytes()[44:] and len(decoded)==FRAMES*4,
            'Independent PCM decode differs from native mixed source')
    meter=captures.run(['ffmpeg','-nostdin','-hide_banner','-v','info','-i',str(wav),
        '-filter_complex','ebur128=peak=true','-f','null','-'],locale+'-meter',40,
        inputs=[original])
    stderr=meter['stderr'].decode('utf8','replace')
    summary=stderr.rsplit('Summary:',1)[-1]
    loud=re.search(r'Integrated loudness:\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS',summary)
    peak=re.search(r'True peak:\s*Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS',summary)
    require(loud is not None and peak is not None,
            'Independent meter omitted integrated loudness or intersample true peak')
    value=float(loud.group(1))
    true_peak=float(peak.group(1))
    require(math.isfinite(value) and math.isfinite(true_peak),
            'Original media has non-finite loudness or true-peak results')
    require(-65<value<0 and true_peak<-1.0,
            'Mix fails independent true peak/headroom safety; no silent limiter allowed')
    return {'integrated_lufs':value,'true_peak_dbfs':true_peak,
            'format':'pcm_s16le','clock_hz':48000,'channels':2,'frames':FRAMES,
            'decoded_pcm_sha256':hash_bytes(decoded),
            'analyzer':'FFmpeg EBU R128 peak=true independently decoded',
            'original_commands':{'ffprobe':probe['original'],'decode':decode['original'],
                                 'meter':meter['original']},
            'mastered':False,'intelligibility_approved':False}

def envelope_visual(wav:Path,destination:Path)->str:
    from PIL import Image,ImageDraw
    data=wav.read_bytes()[44:]
    samples=array.array('h');samples.frombytes(data)
    if sys.byteorder!='little':samples.byteswap()
    panel=Image.new('RGB',(980,360),(20,26,34))
    draw=ImageDraw.Draw(panel)
    stride=math.ceil(FRAMES/920)
    for ch,y in ((0,115),(1,270)):
        draw.line((30,y,950,y),fill=(80,95,111),width=1)
        for x in range(920):
            low=x*stride
            high=min(FRAMES,low+stride)
            if low>=FRAMES:break
            vals=[samples[2*i+ch]for i in range(low,high)]
            minimum=min(vals)/32768;maximum=max(vals)/32768
            draw.line((x+30,y-int(maximum*125),
                       x+30,y-int(minimum*125)),fill=(131,205,227)if ch==0 else(230,193,129))
    for start,end in WINDOWS:
        x1=30+round(start/FRAMES*920);x2=30+round(end/FRAMES*920)
        draw.rectangle((x1,7,x2,21),outline=(241,180,99))
    draw.text((30,29),'SYNTHETIC VOICE PROXY + ORIGINAL MUSIC/SFX - NOT SPEECH',fill=(238,237,235))
    draw.text((30,184),'48k / stereo / R128 technical check only - LISTENING REVIEW REQUIRED',
              fill=(164,176,182))
    panel.save(destination)
    return sha(destination)

def main()->None:
    require(os.environ.get('GITHUB_ACTIONS')=='true',
            'Mix acceptance and external decode are restricted to disposable CI')
    require(FIXTURE.is_file(),'Native Rust original audio mixer binary is absent')
    directory=ROOT/'verification/original-voice-music-sfx'
    directory.mkdir(parents=True,exist_ok=False)
    captures=OriginalPcmCustody(directory,ROOT,{
        'scope':'ACTUAL_SYNTHETIC_PCM_NOT_HUMAN_OR_RELEASE_ACCEPTANCE',
        'workflow_source':os.environ.get('GITHUB_SHA'),
        'run_id':os.environ.get('GITHUB_RUN_ID'),
        'run_attempt':os.environ.get('GITHUB_RUN_ATTEMPT'),
        'job':os.environ.get('GITHUB_JOB')})
    source=captures.run(['git','rev-parse','HEAD'],'source-identity',10)
    source_sha=source['stdout'].decode().strip()
    require(re.fullmatch(r'[0-9a-f]{40}',source_sha) is not None
            and source_sha==os.environ.get('GITHUB_SHA'),
            'Actual fixture source differs from dispatched source')
    results=[]
    for locale in LANGUAGES:
        inputs=make_synthetic_proxy(locale)
        source_paths=[]
        originals={}
        for name,data in inputs.items():
            original=captures.retain(f'{locale}-{name}.s16le',data)
            originals[name]=original
            source_paths.append(str(directory/original['path']))
        wav=directory/f'{locale}-synthetic-audio-preview.wav'
        receipt=directory/f'{locale}-source-receipt.json'
        native=captures.run([str(FIXTURE),*source_paths,locale,str(wav),str(receipt)],
                            locale+'-native-mix',inputs=originals.values())
        captures.observe(receipt)
        saved=json.loads(receipt.read_text())
        captures.observe(receipt)
        require(saved['source_pcm_sha256']=={name:item['sha256'] for name,item in originals.items()},
                'Native source receipt differs from retained original PCM inputs')
        require(saved['schema']=='motionwright.original-voice-music-sfx-e2e/1'
                and saved['source_plan']['language']==locale
                and saved['original_source_classification']=='SYNTHETIC_TONE_VOICE_PROXY_NOT_SPEECH_NOT_APPROVED'
                and saved['release_approved']is False
                and saved['human_listening_approval']is False,
                'Native mixer source receipt claimed ungranted speech/release authority')
        require(sha(wav)==saved['output_wav_sha256'],
                'Original generated media has unexpected final digest')
        evidence=saved['mix_evidence']
        require(evidence['independently_verified_lufs']is False
                and evidence['independently_verified_true_peak']is False
                and evidence['measured_music_duck_windows']==2,
                'Native bus claims unmeasured R128 or lost exact voice windows')
        levels=independent_measure(wav,captures,locale)
        require(levels['decoded_pcm_sha256']==evidence['rendered_pcm_sha256'],
                'Native PCM receipt disagrees with independent decoded WAV')
        mixed=wav.read_bytes()[44:]
        def ratio(frame:int)->float:
            # Subtract independently verified synthetic proxy and cue from
            # actual mixed channel, then normalize to equal original music.
            mv=decode_pcm(inputs['music'],frame)
            vv=decode_pcm(inputs['voice'],frame)
            sv=decode_pcm(inputs['sfx'],frame)
            out=decode_pcm(mixed,frame)
            expect=mv*10**(-12/20)
            require(abs(expect)>0.005,'Measurement point has near-zero music source')
            return (out-vv-sv*10**(-8/20))/expect
        candidate=[i for i in range(35000,50000) if abs(decode_pcm(inputs['music'],i))>0.06]
        require(len(candidate)>100,'Synthetic music fixture cannot independently test ducking')
        observed=[ratio(i) for i in candidate[::max(1,len(candidate)//100)]]
        require(all(0.15<item<0.24 for item in observed),
                'Music was not accurately ducked during manually approved voice intervals')
        before=[i for i in range(10000,15000) if abs(decode_pcm(inputs['music'],i))>0.06]
        require(all(0.98<ratio(i)<1.02 for i in before[::max(1,len(before)//40)]),
                'Music level changed outside explicit voice windows')
        pic=directory/f'{locale}-waveform.png'
        img_sha=envelope_visual(wav,pic)
        require(captures.observe(pic)['sha256']==img_sha,'Waveform original changed')
        results.append({
            'locale':locale,'input_sha256':saved['source_pcm_sha256'],
            'original_inputs':originals,'native_command':native['original'],
            'native_pcm_sha256':evidence['rendered_pcm_sha256'],
            'original_wav_sha256':sha(wav),
            'waveform_png_sha256':img_sha,
            'source_windows':saved['source_plan']['voice_windows'],
            'ducker_technically_measured':'PASS',
            'independent_meter':levels,
            'voice_content':'SYNTHETIC_TONE_NOT_SPEECH',
            'human_listening_review':'NOT_PERFORMED',
            'commercial_source_rights':'NOT_VERIFIED'
        })
    result={'schema':'motionwright.first-party-mix-localization-e2e/1',
        'motionwright_sha':source_sha,'source_command':source['original'],
        'sample_rate':48000,'channels':2,'source_classification':'SYNTHETIC_TONES_ONLY_NOT_VOICE',
        'independent_decode':'PASS','actual_music_ducking':'PASS',
        'locales':list(LANGUAGES),'profiles':results,
        'target_lufs_autonormalized':False,'true_peak_limiter_applied':False,
        'creative_approval':'NOT_REVIEWED','actual_speech_intelligibility':'NOT_MEASURED',
        'mastering':'NOT_PERFORMED','release_approval':'NOT_GRANTED'}
    inventory=captures.inventory()
    expected={f'{locale}-{name}.s16le' for locale in LANGUAGES for name in ('voice','music','sfx')}
    expected.update(f'{locale}-{name}' for locale in LANGUAGES
                    for name in ('synthetic-audio-preview.wav','source-receipt.json','waveform.png'))
    labels={'source-identity'}|{locale+'-'+name for locale in LANGUAGES
                              for name in ('native-mix','ffprobe','decode','meter')}
    require(set(captures.commands)==labels,'Original command roster is incomplete')
    expected.update(f'commands/{label}/{label}.{suffix}' for label in labels
                    for suffix in ('stdout.log','stderr.log','command.json'))
    require({item['path'] for item in inventory}==expected,
            'Retained PCM and command original inventory is incomplete or unexpected')
    result['original_inventory']=inventory
    result['command_originals']=captures.commands
    result_raw=(json.dumps(result,indent=2)+'\n').encode()
    from native_original_mix_custody import MAX_EVIDENCE
    require(sum(item['bytes'] for item in inventory)+len(result_raw)<=MAX_EVIDENCE,
            'PCM originals and final result exceed retained evidence budget')
    captures.retain('result.json',result_raw)
    print(json.dumps({'original_audio_bus_mixing':'PASS','locales':LANGUAGES,
                      'independent_pcm_decode':'PASS','human_approval':'NOT_REVIEWED'}))
if __name__=='__main__':main()
