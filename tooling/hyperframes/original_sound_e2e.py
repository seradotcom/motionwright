#!/usr/bin/env python3
"""Compare original generated PCM WAV with independent FFmpeg/ffprobe decoded audio."""
from __future__ import annotations
import array,hashlib,json,math,os,re,shutil,subprocess,sys,tempfile
from pathlib import Path
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[2]
EXAMPLE=ROOT/'target/debug/examples/original_sound_fixture'
RECIPES=('focus-hit','transition-tail','energy-bed')
def digest(path:Path)->str:
 with path.open('rb')as file:return hashlib.file_digest(file,'sha256').hexdigest()
def command(argv:list[str],timeout:int=45)->bytes:
 process=subprocess.run(argv,capture_output=True,timeout=timeout)
 if process.returncode:raise AssertionError('Executable returned failure: '+str(argv[:2])+' '+process.stderr.decode(errors='replace')[-1800:])
 return process.stdout
def waveform(pcm:bytes,destination:Path)->dict:
 samples=array.array('h');samples.frombytes(pcm)
 if sys.byteorder!='little':samples.byteswap()
 if len(samples)%2:raise AssertionError('Stereo PCM payload is not sample aligned')
 frames=len(samples)//2
 columns=900;stride=max(1,(frames+columns-1)//columns)
 image=Image.new('RGB',(940,360),(17,24,31))
 draw=ImageDraw.Draw(image)
 for ch,baseline in [(0,102),(1,259)]:
  draw.line([(20,baseline),(920,baseline)],fill=(77,98,115),width=1)
  for x in range(columns):
   first=x*stride
   if first>=frames:break
   count=min(stride,frames-first)
   segment=[int(samples[2*(first+i)+ch])for i in range(count)]
   lower=min(segment)/32768.0;upper=max(segment)/32768.0
   draw.line([(x+20,baseline-round(upper*68)),(x+20,baseline-round(lower*68))],
             fill=(150,206,230)if ch==0 else(217,186,126),width=1)
 draw.text((20,16),'ORIGINAL SOURCE AUDIO - NO MASTERING OR HUMAN APPROVAL',fill=(222,230,236))
 draw.text((20,183),'48 kHz / stereo / independent PCM decode / synthetic original only',fill=(166,177,187))
 image.save(destination)
 peak=max(abs(int(value))for value in samples)/32768
 rms=math.sqrt(sum((int(sample)/32768)**2 for sample in samples)/len(samples))
 if not (0<peak<1 and 0<rms<1):raise AssertionError('Original PCM contains clipping or silence')
 return {'sample_peak_dbfs':round(20*math.log10(peak),3),'sample_rms_dbfs':round(20*math.log10(rms),3),
         'waveform_png_sha256':digest(destination),'observed_frames':frames}

def measure_r128(wav:Path)->dict:
 argv=['ffmpeg','-nostdin','-hide_banner','-v','info','-i',str(wav),'-filter_complex','ebur128=peak=true','-f','null','-']
 process=subprocess.run(argv,capture_output=True,timeout=40,check=False)
 if process.returncode:raise AssertionError('Independent EBU-R128 analyzer failed to decode native original WAV')
 output=process.stderr.decode('utf8','replace')
 summary=output.rsplit('Summary:',1)[-1]
 match=re.search(r'Integrated loudness:\s*I:\s*(-?\d+(?:\.\d+)?)\s+LUFS',summary)
 if not match:raise AssertionError('Independent EBU-R128 output did not contain integrated loudness')
 value=float(match.group(1))
 if not math.isfinite(value):raise AssertionError('Native audio R128 loudness cannot be measured')
 return {'integrated_lufs':value,'meter':'ffmpeg_ebur128_true_peak_independent',
         'status':'measured_only_not_mastered'}

def main()->None:
 if os.getenv('GITHUB_ACTIONS')!='true':raise SystemExit('Original WAV audio acceptance runs only in disposable CI')
 output=ROOT/'verification/creative-original-audio';output.mkdir(parents=True,exist_ok=True)
 results=[]
 with tempfile.TemporaryDirectory(prefix='motionwright-audio-')as scratch:
  root=Path(scratch)
  for recipe in RECIPES:
   hashes=[]
   for repeat in range(2):
    target=root/f'{recipe}-{repeat}.wav'
    receipt=root/f'{recipe}-{repeat}.json'
    command([str(EXAMPLE),recipe,str(target),str(receipt)])
    original=json.loads(receipt.read_text())
    check=original['receipt']
    if digest(target)!=check['wav_sha256'] or check['schema']!='motionwright.original-sound-pcm/1':
     raise AssertionError('Source-bound original audio digest mismatch')
    media=json.loads(command(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(target)]))
    streams=[stream for stream in media['streams']if stream.get('codec_type')=='audio']
    if len(streams)!=1:raise AssertionError('Original WAV must contain precisely one audio stream')
    stream=streams[0]
    if stream['codec_name']!='pcm_s16le' or stream['sample_rate']!='48000' or stream['channels']!=2:
     raise AssertionError('Original WAV PCM codec, clock or channel count differs')
    pcm=command(['ffmpeg','-nostdin','-v','error','-i',str(target),'-f','s16le','-acodec','pcm_s16le','-ac','2','-ar','48000','-'],15)
    if len(pcm)!=check['data_bytes'] or pcm!=target.read_bytes()[44:]:
     raise AssertionError('Native original PCM does not decode to independently verified samples')
    if not any(b for b in pcm):raise AssertionError('Original generated sound is entirely silent')
    hashes.append(digest(target))
    if repeat==0:
     shutil.copyfile(target,output/f'{recipe}.wav')
     shutil.copyfile(receipt,output/f'{recipe}-source.json')
     measured=waveform(pcm,output/f'{recipe}-waveform.png')
     measured.update(measure_r128(target))
   if hashes[0]!=hashes[1]:raise AssertionError('Original generated WAV is not deterministic for exact typed inputs')
   results.append({'recipe':recipe,'native_wav_sha256':hashes[0],'decoded_full_pcm_match':True,
                   'two_source_runs_match':True,'independent_audio_measurement':measured})
  denied=subprocess.run([str(EXAMPLE),'silence-release',str(root/'silence.wav'),str(root/'silence.json')],capture_output=True,timeout=15)
  if denied.returncode==0 or (root/'silence.wav').exists():raise AssertionError('Bus gain automation was silently treated as a generated audio master')
 document={'schema':'motionwright.original-audio-decode-acceptance/1','source_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
           'codec':'pcm_s16le','sample_rate':48000,'channels':2,'synthetic_original_only':True,'voice_mix':'not_verified',
           'mastering':'not_performed','creative_approval':'required','decoded_pcm':'PASS','rows':results}
 (output/'result.json').write_text(json.dumps(document,indent=2)+'\n')
 print(json.dumps({'creative_original_audio':'PASS','cases':len(results),'evidence':str(output)}))
if __name__=='__main__':main()
