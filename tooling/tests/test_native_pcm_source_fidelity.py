"""Pure source-bound stereo full-content tests; no native media/Host effects."""
import hashlib
import io
import pathlib
import struct
import sys
import unittest
import wave

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from native_pcm_source_fidelity import (
    inspect_source_pcm16_stereo_content, PcmContentError,
)

COUNT=52_800

def source_wave(raw, rate=48_000, channels=2):
    out=io.BytesIO()
    with wave.open(out,"wb") as media:
        media.setnchannels(channels)
        media.setsampwidth(2)
        media.setframerate(rate)
        media.writeframes(raw)
    return out.getvalue()

def original_bytes():
    # Author-level exact 400Hz-left/600Hz-right source. Explicitly different
    # signals so audio identity and channel swaps are independently testable.
    return b"".join(struct.pack("<hh",((i%120)-60)*250,
                    ((i%80)-40)*260) for i in range(COUNT))

def silent_right(raw, first, stop):
    out=bytearray(raw)
    for i in range(first,stop):
        struct.pack_into("<h",out,4*i+2,0)
    return bytes(out)

def witness(wave_blob,decoded):
    return inspect_source_pcm16_stereo_content(
        wave_blob,decoded,
        original_wav_sha256=hashlib.sha256(wave_blob).hexdigest(),
        decoded_pcm_sha256=hashlib.sha256(decoded).hexdigest(),
    )

class OriginalPcmSourceFidelityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=original_bytes()
        cls.wave=source_wave(cls.source)

    def test_clean_full_52800_source_frames_are_supported_only_as_content_screen(self):
        got=witness(self.wave,self.source)
        self.assertEqual(got["source_stereo_frames"],COUNT)
        self.assertEqual(got["decoded_stereo_frames"],COUNT)
        self.assertEqual(got["raw_decoded_frames_after_source"],0)
        self.assertEqual(got["informative_source_windows_by_channel"],[207,207])
        self.assertEqual(got["minimum_source_window_energy_ratio"],[1,1])
        self.assertEqual(got["maximum_source_window_energy_ratio"],[1,1])
        self.assertEqual(got["source_window_temporal_identity_checked_by_channel"],[207,207])
        self.assertEqual(got["minimum_source_window_correlation"],[1,1])
        self.assertEqual(got["maximum_source_window_correlation"],[1,1])
        self.assertEqual(got["last_informative_window_by_channel"][1]["to"],COUNT)
        self.assertEqual(got["terminal_source_window_informative_by_channel"],[True,True])
        self.assertFalse(got["source_to_decoded_PTS_and_AAC_priming_verified"])
        self.assertFalse(got["native_Provider_effect_or_audio_job_run"])
        self.assertFalse(got["human_audition_approved"])

    def test_codec_tail_padding_is_not_mistaken_for_audible_source_fidelity(self):
        added=self.source+bytes(448*4)
        got=witness(self.wave,added)
        self.assertEqual(got["raw_decoded_frames_after_source"],448)
        self.assertFalse(got["source_to_decoded_PTS_and_AAC_priming_verified"])

    def test_mute_final_256_right_authoring_frames_fails(self):
        bad=silent_right(self.source,COUNT-256,COUNT)
        with self.assertRaisesRegex(PcmContentError,"channel=1:"):
            witness(self.wave,bad+bytes(448*4))

    def test_mute_only_final_64_right_frames_fails(self):
        bad=silent_right(self.source,COUNT-64,COUNT)
        with self.assertRaisesRegex(PcmContentError,"channel=1:"):
            witness(self.wave,bad)

    def test_mid_source_256frame_dropout_fails(self):
        bad=silent_right(self.source,30_000,30_256)
        with self.assertRaisesRegex(PcmContentError,"channel=1:"):
            witness(self.wave,bad)

    def test_complete_right_channel_loss_fails(self):
        with self.assertRaisesRegex(PcmContentError,"source_audio_content_missing_or_scaled"):
            witness(self.wave,silent_right(self.source,0,COUNT))

    def test_swapped_right_and_left_channel_is_not_a_matching_source(self):
        swapped=b"".join(struct.pack("<hh",r,l)
                         for l,r in struct.iter_unpack("<hh",self.source))
        with self.assertRaises(PcmContentError):
            witness(self.wave,swapped)

    def test_untrusted_source_and_decoder_digests_are_rejected(self):
        with self.assertRaisesRegex(PcmContentError,"source_digest_changed"):
            inspect_source_pcm16_stereo_content(self.wave,self.source,
                 original_wav_sha256="0"*64,
                 decoded_pcm_sha256=hashlib.sha256(self.source).hexdigest())
        with self.assertRaisesRegex(PcmContentError,"decoded_digest_changed"):
            inspect_source_pcm16_stereo_content(self.wave,self.source,
                 original_wav_sha256=hashlib.sha256(self.wave).hexdigest(),
                 decoded_pcm_sha256="0"*64)
        with self.assertRaisesRegex(PcmContentError,"source_sha256_missing"):
            inspect_source_pcm16_stereo_content(self.wave,self.source,
                 original_wav_sha256="bad",decoded_pcm_sha256="f"*64)

    def test_mono_and_wrong_samplerate_and_nonwave_are_rejected(self):
        with self.assertRaisesRegex(PcmContentError,"unsupported_source_audio_profile"):
            witness(source_wave(self.source,channels=1),self.source)
        with self.assertRaisesRegex(PcmContentError,"unsupported_source_audio_profile"):
            witness(source_wave(self.source,rate=44_100),self.source)
        with self.assertRaises(PcmContentError):
            witness(b"RIFF",self.source)

    def test_partial_stereo_samples_and_unbounded_padding_are_rejected(self):
        with self.assertRaisesRegex(PcmContentError,"decoded_not_whole_stereo_frames"):
            witness(self.wave,self.source[:-1])
        with self.assertRaisesRegex(PcmContentError,"decoded_pcm_duration_budget"):
            witness(self.wave,self.source+bytes(1100*4))
        with self.assertRaisesRegex(PcmContentError,"decoded_pcm_duration_budget"):
            witness(self.wave,self.source[:-4])

    def test_arbitrary_nonperiodic_original_source_passes_and_reordered_block_fails(self):
        # The product gate must not be limited to the historical 400/600Hz
        # periodic MLT CI waveform. This exercises a separate seeded, bounded
        # nonperiodic source with identical multichannel frame/sample budgets.
        from random import Random
        prng=Random(20261017)
        raw=b"".join(struct.pack("<hh",
                   prng.randint(-18000,18000),prng.randint(-15000,15000))
                   for _ in range(10_240))
        alternative=source_wave(raw)
        good=witness(alternative,raw)
        self.assertEqual(good["source_stereo_frames"],10_240)
        self.assertEqual(good["source_window_temporal_identity_checked_by_channel"],
                         [40,40])
        before=bytearray(raw)
        first=256*17
        segment=[bytes(before[(first+i)*4:(first+i+1)*4]) for i in range(256)]
        for i,frame in enumerate(reversed(segment)):
            before[(first+i)*4:(first+i+1)*4]=frame
        with self.assertRaisesRegex(PcmContentError,
                 "source_audio_content_temporally_changed"):
            witness(alternative,bytes(before))

    def test_injected_sfx_in_authored_silence_is_rejected_not_skipped(self):
        # Energy/correlation must also reject a loud unsourced effect inside
        # explicit source silence, where source correlation is undefined.
        original=bytearray(self.source)
        window=256*120
        for at in range(window,window+256):
            struct.pack_into("<hh",original,at*4,0,0)
        authoritative=bytes(original)
        authored_wave=source_wave(authoritative)
        self.assertFalse(witness(authored_wave,authoritative)[
            "terminal_source_window_informative_by_channel"]==[False,False])
        corrupted=bytearray(authoritative)
        for at in range(window,window+256):
            struct.pack_into("<h",corrupted,at*4+2,4000)
        with self.assertRaisesRegex(PcmContentError,
                 r"source_audio_unapproved_sound_in_quiet_window:channel=1:"):
            witness(authored_wave,bytes(corrupted))

    def test_256frame_reordered_midtrack_with_identical_window_energy_rejected(self):
        # Historical RED from frozen PR284: sparse identity sampling accepted
        # this permutation despite a real 400/600 Hz authored source.
        first=256*137
        raw=bytearray(self.source)
        blocks=[bytes(raw[(first+i)*4:(first+i+1)*4]) for i in range(256)]
        for i,pair in enumerate(reversed(blocks)):
            raw[(first+i)*4:(first+i+1)*4]=pair
        altered=bytes(raw)
        for channel in range(2):
            source_l1=sum(abs(struct.unpack_from("<h",self.source,(first+i)*4+2*channel)[0])
                          for i in range(256))
            altered_l1=sum(abs(struct.unpack_from("<h",altered,(first+i)*4+2*channel)[0])
                          for i in range(256))
            self.assertEqual(source_l1,altered_l1)
        with self.assertRaisesRegex(PcmContentError,
                 r"source_audio_content_temporally_changed:channel=0:start=35072:end=35328"):
            witness(self.wave,altered)

    def test_256frame_polarity_inversion_in_middle_rejected_despite_energy_match(self):
        first=256*130
        raw=bytearray(self.source)
        for i in range(first,first+256):
            left,right=struct.unpack_from("<hh",raw,4*i)
            struct.pack_into("<hh",raw,4*i,-left,-right)
        with self.assertRaisesRegex(PcmContentError,
                 r"source_audio_content_temporally_changed:channel=0:"):
            witness(self.wave,bytes(raw))

    def test_clipping_and_modified_audio_are_not_silently_approved(self):
        huge=b"".join(struct.pack("<hh",32767,-32768) for _ in range(COUNT))
        with self.assertRaises(PcmContentError):
            witness(self.wave,huge)

    def test_large_unapproved_source_offset_is_not_implicitly_realigned(self):
        # Caller must supply trusted packet priming/skip separately; do not
        # search for an alignment that makes a shifted waveform look correct.
        offset=40
        raw=self.source
        shifted=bytes(offset*4)+raw[:-offset*4]
        with self.assertRaises(PcmContentError):
            witness(self.wave,shifted)

    def test_end_is_legitimately_silent_but_source_identity_does_not_become_a_grant(self):
        # A tail fade/quiet source must not be classified as a truncated
        # channel simply because its last frames are authored silence.
        faded=bytearray(self.source)
        for i in range(COUNT-512,COUNT):
            struct.pack_into("<hh",faded,4*i,0,0)
        result=witness(source_wave(bytes(faded)),bytes(faded))
        self.assertEqual(result["terminal_source_window_informative_by_channel"],
                         [False,False])
        self.assertFalse(result["source_permissions_or_licensing_verified"])

    def test_authoring_source_different_but_hash_consistent_still_rejects_wrong_content(self):
        changed=bytearray(self.source)
        struct.pack_into("<h",changed,4*5000,12000)
        new_source=source_wave(bytes(changed))
        # Deliberately let strong primary source alterations propagate through
        # SHA without acquiring permission; waveform evidence must be evaluated
        # by its actual bytes, not a remembered project name.
        good=witness(new_source,bytes(changed))
        self.assertEqual(good["source_stereo_frames"],COUNT)
        self.assertFalse(good["source_permissions_or_licensing_verified"])

    def test_silent_source_cannot_be_promoted_as_informative_fidelity(self):
        zeros=bytes(COUNT*4)
        with self.assertRaisesRegex(PcmContentError,"source_audio_channel_not_informative"):
            witness(source_wave(zeros),zeros)

if __name__=="__main__":
    unittest.main()
