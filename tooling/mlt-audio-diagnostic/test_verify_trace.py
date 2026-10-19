"""Contract tests with constructed in-memory samples; no native media claim."""
import copy,io,struct,unittest,wave
import verify_trace as v
def wav_bytes(count,ending_zeros=0):
    pcm=b"".join(struct.pack("<hh",0 if i>=count-ending_zeros else (i*17+31)%20000-10000,
                          0 if i>=count-ending_zeros else (i*41+7001)%24000-12000) for i in range(count))
    out=io.BytesIO()
    with wave.open(out,"wb") as f:
        f.setnchannels(2);f.setsampwidth(2);f.setframerate(48000);f.writeframes(pcm)
    return out.getvalue()
def record(kind,prepared,consumed,pts=0,position=-1,p0=b"",p1=b"",layout=0,offset1=0,before=v.UNKNOWN,after=v.UNKNOWN):
    w=[0]*v.WORDS
    w[:10]=[ord(kind),3 if kind in "AF" else 8,5 if kind in "AF" else 8,2,48000,prepared,consumed,pts,position,layout]
    if kind=="A":w[6]=v.UNKNOWN
    w[10:18]=[len(p0),len(p1),0,offset1,v.NA if kind in "AF" else prepared*8,
                v.NA if kind in "AF" else 0,before,after]
    if kind in "BD":w[18:22]=[1,48000,64,1024]
    return [w,p0,p1]
def encode(records,errors=0):
    body=b""
    for w,p0,p1 in records:
        body+=struct.pack("<"+str(v.WORDS)+"Q",*(x&v.UNKNOWN for x in w))+p0+p1
    return struct.pack("<8s7Q",b"MWATAP01",1,v.WORDS,len(records),v.FILE_HEADER+len(body),errors,1,4)+body
def fixture(chunks=(1024,576),prepared=1024,bug=False,stride=None,ending_zeros=0):
    wav=wav_bytes(sum(chunks),ending_zeros)
    packed,_=v.wav_float(wav)
    records=[record("A",sum(chunks),sum(chunks),pts=v.NA,position=0,p0=packed)]
    fifo=len(packed);cursor=0;pts=0
    for n in chunks:
        block=packed[cursor*8:(cursor+n)*8]
        records.append(record("F",n,n,pts=pts,p0=block,before=fifo,after=fifo-len(block)))
        fifo-=len(block);cursor+=n
        fl,fr=v.channels(block)
        size=max(n,prepared);plane_stride=max(size,stride or 0)
        if bug and n<size:
            backing=fl+fr+b"\0"*((plane_stride+size)*4-len(fl)-len(fr))
            bl=backing[:size*4];br=backing[plane_stride*4:(plane_stride+size)*4]
        else:bl=fl+b"\0"*((size-n)*4);br=fr+b"\0"*((size-n)*4)
        records.append(record("B",size,n,pts=pts,p0=bl,p1=br,layout=1,offset1=plane_stride*4))
        pts+=size
    records.append(record("D",prepared,0,pts=pts,layout=2))
    return wav,records
class TraceContractTests(unittest.TestCase):
    def test_correct_content_and_padding_remain_separate_from_master_acceptance(self):
        wav,r=fixture();d=v.analyze_trace(wav,encode(r))
        self.assertEqual(d["encoder_input_content"],"PASS")
        self.assertEqual(d["WAV_samples_per_channel"],1600)
        self.assertEqual(d["accepted_B_prepared_samples_per_channel"],2048)
        self.assertEqual(d["extra_prepared_samples_over_WAV"],448)
        self.assertEqual(d["drain"]["submitted_PCM_samples"],0)
        self.assertEqual(d["actual_final_media_readback"],"NOT_PERFORMED_BY_THIS_TOOL")
        self.assertEqual(d["duration_and_encoder_padding_acceptance"],"NOT_GRANTED")
    def test_short_final_frame_without_extra_prepared_samples(self):
        wav,r=fixture(chunks=(576,),prepared=576)
        d=v.analyze_trace(wav,encode(r))
        self.assertEqual(d["encoder_input_content"],"PASS")
        self.assertEqual(d["extra_prepared_samples_over_WAV"],0)
    def test_real_source_silence_is_counted_as_source(self):
        wav,r=fixture(ending_zeros=256)
        d=v.analyze_trace(wav,encode(r))
        self.assertTrue(d["A_matches_entire_WAV_float_conversion"])
        self.assertEqual(d["WAV_samples_per_channel"],1600)
    def test_576_1024_compact_planes_fail_right_content_and_left_padding(self):
        wav,r=fixture(bug=True);d=v.analyze_trace(wav,encode(r))
        self.assertEqual(d["encoder_input_content"],"FAIL")
        self.assertTrue(d["A_matches_entire_WAV_float_conversion"])
        self.assertTrue(d["F_matches_entire_A_bytes"])
        self.assertFalse(d["B_source_prefixes_match_matching_F_per_plane"])
        self.assertFalse(d["B_declared_padding_is_zero_per_plane"])
        self.assertEqual(d["compact_planar_partial_mechanism_frames"],[1])
        self.assertGreater(d["frames"][-1]["right_valid_mismatches"],0)
        self.assertGreater(d["frames"][-1]["left_padding_nonzero_samples"],0)
    def test_crop_then_repack_trap_is_rejected_for_both_plane_strides(self):
        for stride in [5,32]:
            with self.subTest(stride=stride):
                wav,r=fixture(chunks=(3,),prepared=5,bug=True,stride=stride)
                f=r[1][1];bl,br=r[2][1:]
                # A wrong decoder can reconstruct all useful original bytes from the compact prefix.
                if stride==5:self.assertEqual((bl+br)[:24],b"".join(v.channels(f)))
                d=v.analyze_trace(wav,encode(r))
                self.assertEqual(d["encoder_input_content"],"FAIL")
                self.assertGreater(d["frames"][0]["right_valid_mismatches"],0)
                self.assertGreater(d["frames"][0]["left_padding_nonzero_samples"],0)
    def test_swapped_channels_fail_without_alignment_or_normalization(self):
        wav,r=fixture();r[2][1],r[2][2]=r[2][2],r[2][1]
        d=v.analyze_trace(wav,encode(r))
        self.assertEqual(d["encoder_input_content"],"FAIL")
        self.assertGreater(d["frames"][0]["left_valid_mismatches"],0)
    def test_wrong_same_length_WAV_is_not_admitted_by_trace(self):
        wav,r=fixture();other=bytearray(wav);other[-4]^=1
        d=v.analyze_trace(bytes(other),encode(r))
        self.assertFalse(d["A_matches_entire_WAV_float_conversion"])
        self.assertEqual(d["encoder_input_content"],"FAIL")
    def test_A_padding_is_not_trimmed_to_source_length(self):
        wav,r=fixture()
        shorter=wav_bytes(1599)
        d=v.analyze_trace(shorter,encode(r))
        self.assertFalse(d["A_matches_entire_WAV_float_conversion"])
        self.assertEqual(d["observed_A_samples_per_channel"],1600)
    def test_F_mutation_is_not_hidden_by_correct_A_and_B(self):
        wav,r=fixture();b=bytearray(r[1][1]);b[4]^=1;r[1][1]=bytes(b)
        d=v.analyze_trace(wav,encode(r))
        self.assertFalse(d["F_matches_entire_A_bytes"])
        self.assertEqual(d["encoder_input_content"],"FAIL")
    def test_nonzero_padding_in_single_channel_fails(self):
        wav,r=fixture();p=bytearray(r[4][2]);struct.pack_into("<f",p,576*4,.5);r[4][2]=bytes(p)
        d=v.analyze_trace(wav,encode(r))
        self.assertTrue(d["B_source_prefixes_match_matching_F_per_plane"])
        self.assertFalse(d["B_declared_padding_is_zero_per_plane"])
        self.assertEqual(d["frames"][-1]["right_padding_nonzero_samples"],1)
    def test_padding_relabeled_as_FIFO_consumption_is_rejected(self):
        wav,r=fixture();r[4][0][6]=1024
        with self.assertRaisesRegex(v.TraceError,"matching_FIFO"):v.analyze_trace(wav,encode(r))
    def test_PTS_jump_and_duplicate_fail_even_with_correct_PCM(self):
        for pts in [0,1025]:
            with self.subTest(pts=pts):
                wav,r=fixture();r[3][0][7]=pts;r[4][0][7]=pts
                with self.assertRaisesRegex(v.TraceError,"PTS_discontinuity"):v.analyze_trace(wav,encode(r))
    def test_FIFO_delta_cannot_be_self_consistent_but_omit_A(self):
        wav,r=fixture();r[1][0][16]+=8;r[1][0][17]+=8
        with self.assertRaisesRegex(v.TraceError,"FIFO_before"):v.analyze_trace(wav,encode(r))
    def test_FIFO_delta_units_mismatch_rejected(self):
        wav,r=fixture();r[1][0][17]+=4
        with self.assertRaisesRegex(v.TraceError,"FIFO_delta"):v.analyze_trace(wav,encode(r))
    def test_send_rejection_missing_result_and_fill_failure_are_not_passes(self):
        for field,value,reason in [(15,-11,"send_not_accepted"),(15,v.NA,"send_not_accepted"),(14,-22,"fill_failed")]:
            with self.subTest(field=field,value=value):
                wav,r=fixture();r[2][0][field]=value
                with self.assertRaisesRegex(v.TraceError,reason):v.analyze_trace(wav,encode(r))
    def test_duplicate_accepted_B_is_rejected(self):
        wav,r=fixture();r.insert(3,copy.deepcopy(r[2]))
        with self.assertRaisesRegex(v.TraceError,"matching_FIFO"):v.analyze_trace(wav,encode(r))
    def test_missing_or_early_drain_is_rejected(self):
        wav,r=fixture()
        with self.assertRaisesRegex(v.TraceError,"final_drain"):v.analyze_trace(wav,encode(r[:-1]))
        r=[r[0],r[-1]]
        with self.assertRaisesRegex(v.TraceError,"pending_FIFO"):v.analyze_trace(wav,encode(r))
    def test_drain_cannot_carry_PCM_or_consumed_source(self):
        for field,value in [(6,1),(9,1),(10,4)]:
            wav,r=fixture();r[-1][0][field]=value
            with self.assertRaises(v.TraceError):v.analyze_trace(wav,encode(r))
    def test_instrument_overflow_and_reported_errors_invalidate_trace(self):
        wav,r=fixture()
        with self.assertRaisesRegex(v.TraceError,"instrument_reported_errors"):v.analyze_trace(wav,encode(r,errors=1))
        data=bytearray(encode(r));struct.pack_into("<Q",data,24,257)
        with self.assertRaisesRegex(v.TraceError,"count_or_length"):v.analyze_trace(wav,bytes(data))
    def test_truncation_trailing_data_and_wrong_record_word_count_rejected(self):
        wav,r=fixture();data=encode(r)
        for mutated in [data[:-1],data+b"x",data[:16]+struct.pack("<Q",18)+data[24:]]:
            with self.assertRaises(v.TraceError):v.analyze_trace(wav,mutated)
    def test_nan_or_inf_in_content_or_padding_rejected(self):
        for n in [float("nan"),float("inf")]:
            wav,r=fixture();p=bytearray(r[4][2]);struct.pack_into("<f",p,600*4,n);r[4][2]=bytes(p)
            with self.assertRaisesRegex(v.TraceError,"nonfinite"):v.analyze_trace(wav,encode(r))
    def test_planar_lengths_and_offsets_cannot_alias_or_escape(self):
        for field,value in [(10,4092),(11,0),(13,0),(13,v.BACKING_CAPACITY)]:
            wav,r=fixture();r[2][0][field]=value
            with self.assertRaises(v.TraceError):v.analyze_trace(wav,encode(r))
    def test_duplicate_or_omitted_sample_fails_with_unchanged_duration(self):
        wav,r=fixture();p=bytearray(r[2][1]);p[4:8]=p[0:4];r[2][1]=bytes(p)
        d=v.analyze_trace(wav,encode(r))
        self.assertEqual(d["encoder_input_content"],"FAIL")
        self.assertEqual(d["frames"][0]["left_valid_mismatches"],1)
    def test_source_WAV_wrong_profile_rejected(self):
        wav,r=fixture();data=bytearray(wav);struct.pack_into("<I",data,24,44100)
        with self.assertRaisesRegex(v.TraceError,"WAV_profile"):v.analyze_trace(bytes(data),encode(r))

    def test_native_30fps_profile_adds_temporal_assertions_to_content(self):
        wav,r=fixture()
        d=v.analyze_trace(wav,encode(r),expected_native_video_frames=1)
        self.assertEqual(d["native_frame_contract"]["A_position_sequence"],"VERIFIED")
    def test_native_A_duplicate_regressive_and_skipped_positions_fail(self):
        wav,r=fixture(chunks=(1024,1024,1024,128))
        pcm=r[0][1]
        a0=record("A",1600,v.UNKNOWN,pts=v.NA,position=0,p0=pcm[:12800])
        a1=record("A",1600,v.UNKNOWN,pts=v.NA,position=1,p0=pcm[12800:])
        base=[a0,a1]+r[1:]
        self.assertEqual(v.analyze_trace(wav,encode(base),expected_native_video_frames=2)["encoder_input_content"],"PASS")
        for positions in [(0,0),(1,0),(0,2)]:
            changed=copy.deepcopy(base)
            changed[0][0][8],changed[1][0][8]=positions
            with self.assertRaisesRegex(v.TraceError,"native_A_position_sequence"):
                v.analyze_trace(wav,encode(changed),expected_native_video_frames=2)
    def test_native_A_wrong_sample_boundaries_fail_with_identical_total_PCM(self):
        wav,r=fixture(chunks=(1024,1024,1024,128));pcm=r[0][1]
        altered=[record("A",1599,v.UNKNOWN,pts=v.NA,position=0,p0=pcm[:1599*8]),
                 record("A",1601,v.UNKNOWN,pts=v.NA,position=1,p0=pcm[1599*8:])]+r[1:]
        self.assertEqual(v.analyze_trace(wav,encode(altered))["encoder_input_content"],"PASS")
        with self.assertRaisesRegex(v.TraceError,"native_A_samples"):
            v.analyze_trace(wav,encode(altered),expected_native_video_frames=2)
    def test_native_codec_shape_and_source_duration_are_bound(self):
        wav,r=fixture();r[2][0][21]=2048;r[4][0][21]=2048;r[-1][0][21]=2048
        with self.assertRaisesRegex(v.TraceError,"native_AAC_frame_size"):
            v.analyze_trace(wav,encode(r),expected_native_video_frames=1)
        wav,r=fixture()
        with self.assertRaisesRegex(v.TraceError,"native_A_frame_count"):
            v.analyze_trace(wav,encode(r),expected_native_video_frames=2)
    def test_codec_metadata_cannot_change_mid_trace(self):
        for field,value in [(18,2),(19,96000),(20,0),(21,512)]:
            wav,r=fixture()
            # For a time-base change keep the rational PTS equivalent.
            if field in (18,19):
                r[4][0][18:20]=[2,96000]
            else:r[4][0][field]=value
            with self.assertRaisesRegex(v.TraceError,"codec_metadata_changed"):
                v.analyze_trace(wav,encode(r))
    def test_zero_FIFO_fetch_and_zero_PCM_B_are_rejected(self):
        wav,r=fixture()
        r[3][0][5]=r[3][0][6]=0;r[3][0][10]=0;r[3][1]=b""
        with self.assertRaises(v.TraceError):v.analyze_trace(wav,encode(r))
        wav,r=fixture();r[4][0][6]=0
        with self.assertRaises(v.TraceError):v.analyze_trace(wav,encode(r))
    def test_C_constructor_traces_when_explicitly_supplied(self):
        import os,pathlib
        folder=os.environ.get("MOTIONWRIGHT_TAP_CONSTRUCTOR_DIR")
        if not folder:self.skipTest("C constructors require explicit private directory")
        out=io.BytesIO()
        with wave.open(out,"wb") as f:
            f.setnchannels(2);f.setsampwidth(2);f.setframerate(48000)
            f.writeframes(b"".join(struct.pack("<hh",(i+1)*8,-(i+2049)*8) for i in range(576)))
        for name,result in [("padded","PASS"),("compact","FAIL")]:
            report=v.analyze_trace(out.getvalue(),(pathlib.Path(folder)/(name+".trace")).read_bytes())
            self.assertEqual(report["encoder_input_content"],result)
            self.assertFalse(report["release_authorized"])
    def test_CLI_rejects_wrong_input_hash_and_bounds_reads(self):
        import contextlib,hashlib,pathlib,tempfile
        wav,r=fixture();trace=encode(r)
        with tempfile.TemporaryDirectory(prefix="mw-tap-reader-") as temp:
            folder=pathlib.Path(temp);wp=folder/"owned.wav";tp=folder/"input.trace"
            wp.write_bytes(wav);tp.write_bytes(trace)
            args=["--wav",str(wp),"--wav-sha256",hashlib.sha256(wav).hexdigest(),
                  "--trace",str(tp),"--trace-sha256",hashlib.sha256(trace).hexdigest(),
                  "--native-video-frames","1"]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(v.main(args),0)
                changed=list(args);changed[3]="0"*64
                self.assertEqual(v.main(changed),2)
                tp.write_bytes(b"x"*(v.CAPACITY+1))
                self.assertEqual(v.main(args),2)

if __name__=="__main__":unittest.main()
