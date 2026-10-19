"""Bounded diagnostic reader. A trace never authorizes or accepts a production master."""
from __future__ import annotations
import argparse,hashlib,io,json,math,pathlib,struct,sys,wave
from fractions import Fraction
CAPACITY=2*1024*1024
MAX_RECORDS=256
WORDS=22
FILE_HEADER=64
RECORD_HEADER=WORDS*8
UNKNOWN=(1<<64)-1
NA=-(1<<63)
BACKING_CAPACITY=48000*2*8
class TraceError(ValueError):pass
def require(value,reason):
    if not value:raise TraceError(reason)
def signed(value):return value-(1<<64) if value&(1<<63) else value
def parse_trace(data:bytes):
    require(type(data)is bytes and FILE_HEADER<=len(data)<=CAPACITY,"trace_size")
    magic,version,words,count,total,errors,endian,width=struct.unpack_from("<8s7Q",data)
    require(magic==b"MWATAP01" and version==1 and words==WORDS,"trace_header_version")
    require(1<=count<=MAX_RECORDS and total==len(data),"trace_count_or_length")
    require(errors==0,"instrument_reported_errors")
    require(endian==1 and width==4,"unsupported_float_representation")
    records=[];cursor=FILE_HEADER
    for index in range(count):
        require(cursor+RECORD_HEADER<=len(data),"truncated_record")
        w=struct.unpack_from("<"+str(WORDS)+"Q",data,cursor)
        cursor+=RECORD_HEADER
        n0,n1=w[10:12]
        require(n0<=CAPACITY and n1<=CAPACITY-n0 and cursor+n0+n1<=len(data),"truncated_payload")
        require(w[0] in [ord(x) for x in "AFBD"],"unknown_record_kind")
        kind=chr(w[0]);prepared,consumed=w[5:7];layout=w[9]
        require(w[3]==2 and w[4]==48000,"unsupported_audio_profile")
        require(0<=prepared<=8192 and (consumed==UNKNOWN if kind=="A" else 0<=consumed<=prepared),"sample_count_bounds")
        p0=data[cursor:cursor+n0];p1=data[cursor+n0:cursor+n0+n1];cursor+=n0+n1
        require((n0+n1)%4==0 and all(math.isfinite(v[0]) for v in struct.iter_unpack("<f",p0+p1)),"nonfinite_or_unaligned_pcm")
        fill,send=signed(w[14]),signed(w[15])
        if kind in "AF":
            require(prepared>0 and (consumed==UNKNOWN if kind=="A" else prepared==consumed),"packed_consumption_count")
            require(w[1]==3 and w[2]==5 and layout==0 and n0==prepared*8 and n1==0,"packed_format_or_length")
            require(fill==NA and send==NA and w[18:22]==(0,0,0,0),"packed_control_metadata")
            require(w[12]==0 and w[13]==0,"packed_plane_offsets")
            if kind=="A":
                require(signed(w[7])==NA and signed(w[8])>=0 and w[16]==UNKNOWN and w[17]==UNKNOWN,"A_metadata")
            else:
                require(signed(w[8])==-1 and w[16]>=w[17] and w[16]-w[17]==n0,"FIFO_delta")
        else:
            require(w[1]==w[2] and w[1] in (3,8),"frame_format")
            require(signed(w[8])==-1 and w[16]==UNKNOWN and w[17]==UNKNOWN,"frame_metadata")
            require(0<w[18]<=1000000000 and 0<w[19]<=1000000000,"codec_time_base")
            require(w[21]<=8192,"codec_frame_size")
            require(fill>=0,"avcodec_fill_failed")
            require(send==0,"avcodec_send_not_accepted")
            if kind=="D":
                require(consumed==0 and layout==2 and n0==n1==0,"drain_has_no_submitted_PCM")
            elif w[1]==8:
                require(consumed>0 and layout==1 and n0==n1==prepared*4,"planar_frame_length")
                require(w[12]==0 and w[13]>=n0 and w[13]+n1<=BACKING_CAPACITY,"planar_backing_offsets")
            else:
                require(consumed>0 and layout==0 and n0==prepared*8 and n1==0 and w[12]==w[13]==0,"packed_frame_length")
        records.append({"kind":kind,"words":w,"prepared":prepared,"fifo_consumed":consumed,
                        "pts":signed(w[7]),"position":signed(w[8]),"layout":layout,"p0":p0,"p1":p1})
    require(cursor==len(data),"trailing_trace_bytes")
    require(records[-1]["kind"]=="D" and sum(r["kind"]=="D" for r in records)==1,"missing_or_multiple_final_drain")
    return records
def wav_float(data:bytes):
    require(len(data)<=CAPACITY,"source_WAV_budget")
    try:
        with wave.open(io.BytesIO(data),"rb") as f:
            require((f.getnchannels(),f.getsampwidth(),f.getframerate(),f.getcomptype())==(2,2,48000,"NONE"),"source_WAV_profile")
            count=f.getnframes();pcm=f.readframes(count+1)
            require(count>0 and len(pcm)==count*4,"source_WAV_truncated")
    except (wave.Error,EOFError) as e:raise TraceError("source_WAV_structure") from e
    return b"".join(struct.pack("<f",v[0]/32768.0) for v in struct.iter_unpack("<h",pcm)),count
def channels(packed):
    require(len(packed)%8==0,"packed_stereo_alignment")
    return (b"".join(packed[i:i+4] for i in range(0,len(packed),8)),
            b"".join(packed[i+4:i+8] for i in range(0,len(packed),8)))
def mismatch(a,b):
    require(len(a)%4==len(b)%4==0,"sample_alignment")
    return abs(len(a)-len(b))//4+sum(a[i:i+4]!=b[i:i+4] for i in range(0,min(len(a),len(b)),4))
def nonzero(data):
    return sum(v[0]!=0.0 for v in struct.iter_unpack("<f",data))

def verify_native_frame_contract(records, video_frames):
    """Verify the fixed 30 fps AAC consumer profile, separately from PCM bytes."""
    require(type(video_frames) is int and 1 <= video_frames <= MAX_RECORDS, "native_video_frame_count")
    observed = [r for r in records if r["kind"] == "A"]
    require(len(observed) == video_frames, "native_A_frame_count")
    require([r["position"] for r in observed] == list(range(video_frames)), "native_A_position_sequence")
    require(all(r["prepared"] == 1600 for r in observed), "native_A_samples_per_30fps_frame")
    frames = [r for r in records if r["kind"] in "BD"]
    require(all(r["words"][21] == 1024 and r["prepared"] == 1024 for r in frames), "native_AAC_frame_size")
    require(all(0 < r["fifo_consumed"] <= 1024 for r in frames if r["kind"] == "B"), "native_AAC_consumption")
    return {"frame_rate": [30, 1], "video_frames": video_frames,
            "audio_samples_per_video_frame": 1600, "codec_frame_size": 1024,
            "A_position_sequence": "VERIFIED", "scope": "fixed original consumer diagnostic"}

def analyze_trace(source_wav:bytes,trace:bytes,expected_native_video_frames=None):
    source,source_samples=wav_float(source_wav);records=parse_trace(trace)
    native_contract=(verify_native_frame_contract(records,expected_native_video_frames)
                     if expected_native_video_frames is not None else "NOT_CHECKED")
    codec_metadata=[tuple(r["words"][i] for i in (1,2,3,4,18,19,20,21)) for r in records if r["kind"] in "BD"]
    require(len(set(codec_metadata))==1,"codec_metadata_changed_during_trace")
    if expected_native_video_frames is not None:
        require(source_samples==expected_native_video_frames*1600,"native_source_sample_count")
    observed=[];fetched=[];submitted=[];pending=None;fifo_bytes=0;prepared_cursor=0;drain=None
    for r in records:
        w=r["words"]
        if r["kind"]=="A":
            require(pending is None,"A_inside_pending_encoder_attempt")
            observed.append(r);fifo_bytes+=len(r["p0"])
        elif r["kind"]=="F":
            require(pending is None and w[16]==fifo_bytes,"FIFO_before_or_unmatched_fetch")
            fifo_bytes-=len(r["p0"])
            require(fifo_bytes>=0 and w[17]==fifo_bytes,"FIFO_after")
            fetched.append(r);pending=r
        elif r["kind"]=="B":
            require(pending is not None and r["fifo_consumed"]==pending["prepared"],"B_without_matching_FIFO_consumption")
            require(r["pts"]==pending["pts"],"FIFO_encoder_PTS_join")
            require(Fraction(r["pts"]*w[18],w[19])==Fraction(prepared_cursor,48000),"encoder_PTS_discontinuity")
            require(not submitted or submitted[-1]["fifo_consumed"]==submitted[-1]["prepared"],"partial_before_final_frame")
            submitted.append(r);prepared_cursor+=r["prepared"];pending=None
        else:
            require(pending is None and fifo_bytes==0,"drain_with_pending_FIFO")
            drain=r
    require(observed and fetched and len(fetched)==len(submitted),"incomplete_audio_stages")
    A=b"".join(r["p0"] for r in observed);F=b"".join(r["p0"] for r in fetched)
    frames=[];all_content=True;all_padding=True;mechanisms=[]
    for number,(f,b) in enumerate(zip(fetched,submitted,strict=True)):
        fl,fr=channels(f["p0"])
        bl,br=(b["p0"],b["p1"]) if b["layout"]==1 else channels(b["p0"])
        consumed=b["fifo_consumed"];prepared=b["prepared"];end=consumed*4
        left_error=mismatch(fl,bl[:end]);right_error=mismatch(fr,br[:end])
        left_padding=nonzero(bl[end:]);right_padding=nonzero(br[end:])
        all_content &= left_error==right_error==0
        all_padding &= left_padding==right_padding==0
        mechanism=False
        if b["layout"]==1 and prepared>consumed:
            packed_planes=fl+fr
            backing_size=max(b["words"][12]+len(bl),b["words"][13]+len(br))
            predicted=packed_planes+b"\0"*(backing_size-len(packed_planes))
            mechanism=(bl==predicted[b["words"][12]:b["words"][12]+len(bl)]
                       and br==predicted[b["words"][13]:b["words"][13]+len(br)]
                       and (left_error>0 or right_error>0 or left_padding>0 or right_padding>0))
            if mechanism:mechanisms.append(number)
        frames.append({"frame_index":number,"encoder_pts":b["pts"],"time_base":[b["words"][18],b["words"][19]],
                       "fifo_consumed_samples_per_channel":consumed,"prepared_nb_samples":prepared,
                       "plane_offsets_bytes":[b["words"][12],b["words"][13]],"left_valid_mismatches":left_error,
                       "right_valid_mismatches":right_error,"left_padding_nonzero_samples":left_padding,
                       "right_padding_nonzero_samples":right_padding,"compact_planes_with_larger_declared_stride_match":mechanism})
    A_matches=A==source;F_matches=F==A
    encoder_content=A_matches and F_matches and all_content and all_padding
    return {"schema":"motionwright.mlt-audio-input-diagnostic/1","scope":"private source/tap readback, not production-master acceptance",
            "source_WAV_sha256":hashlib.sha256(source_wav).hexdigest(),"trace_sha256":hashlib.sha256(trace).hexdigest(),
            "WAV_samples_per_channel":source_samples,"observed_A_samples_per_channel":len(A)//8,
            "fetched_F_samples_per_channel":len(F)//8,"accepted_B_fifo_consumed_samples_per_channel":sum(r["fifo_consumed"] for r in submitted),
            "accepted_B_prepared_samples_per_channel":prepared_cursor,"extra_prepared_samples_over_WAV":prepared_cursor-source_samples,
            "A_matches_entire_WAV_float_conversion":A_matches,"F_matches_entire_A_bytes":F_matches,
            "B_source_prefixes_match_matching_F_per_plane":all_content,"B_declared_padding_is_zero_per_plane":all_padding,
            "encoder_input_content":"PASS" if encoder_content else "FAIL",
            "compact_planar_partial_mechanism_frames":mechanisms,"frames":frames,
            "A_frame_positions":[r["position"] for r in observed],
            "native_frame_contract":native_contract,
            "speaker_layout":"channel indices compared; speaker mapping requires source-bound instrument and final-media verification",
            "drain":{"submitted_PCM_samples":0,"auxiliary_prepared_nb_samples":drain["prepared"],"auxiliary_PTS_not_submitted":drain["pts"]},
            "accepted_codec_capabilities":sorted(set(r["words"][20] for r in submitted)),
            "codec_frame_sizes":sorted(set(r["words"][21] for r in submitted)),
            "actual_final_media_readback":"NOT_PERFORMED_BY_THIS_TOOL","duration_and_encoder_padding_acceptance":"NOT_GRANTED",
            "native_operation_authority":"NOT_ESTABLISHED_BY_TRACE_CONTENT","release_authorized":False}
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--wav",type=pathlib.Path,required=True);p.add_argument("--wav-sha256",required=True)
    p.add_argument("--trace",type=pathlib.Path,required=True);p.add_argument("--trace-sha256",required=True)
    p.add_argument("--native-video-frames",type=int,required=True)
    args=p.parse_args(argv)
    try:
        values=[]
        for path,digest in [(args.wav,args.wav_sha256),(args.trace,args.trace_sha256)]:
            require(len(digest)==64 and all(c in "0123456789abcdef" for c in digest),"expected_SHA256_shape")
            require(path.is_file() and not path.is_symlink() and path.stat().st_size<=CAPACITY,"input_file_budget")
            with path.open("rb") as handle:data=handle.read(CAPACITY+1)
            require(len(data)<=CAPACITY,"input_file_budget")
            require(hashlib.sha256(data).hexdigest()==digest,"input_SHA256_mismatch");values.append(data)
        report=analyze_trace(*values,expected_native_video_frames=args.native_video_frames)
        print(json.dumps(report,indent=2,allow_nan=False))
        return 0 if report["encoder_input_content"]=="PASS" else 1
    except (TraceError,OSError) as e:
        print(json.dumps({"diagnostic_complete":False,"error":str(e),"production_master_accepted":False}))
        return 2
if __name__=="__main__":raise SystemExit(main())
