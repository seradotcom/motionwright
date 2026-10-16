"""Adversarial 3-scene original PCM / decoded AAC acceptance (no native job)."""
from __future__ import annotations
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from semantic_sfx_composition_av_evidence import (
    inspect_original_composition, inspect_decoded_multiscene_audio,
    original_multiscene_pcm, PCM_SAMPLES, SOURCE_FRAMES, MediaProbeError,
)
from semantic_sfx_av_evidence import sha
from semantic_sfx_av_public_artifact import curate


def tone(left_hz:int,right_hz:int)->list[tuple[int,int]]:
    return [
        (
            round(12000*math.sin(2*math.pi*left_hz*i/48000)),
            round(12000*math.sin(2*math.pi*right_hz*i/48000)),
        )
        for i in range(SOURCE_FRAMES)
    ]


def wav(path:Path,values:list[tuple[int,int]])->None:
    with wave.open(str(path),"wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(b"".join(struct.pack("<hh",l,r) for l,r in values))


class OriginalThreeSceneSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls)->None:
        cls.folder=tempfile.TemporaryDirectory()
        cls.root=Path(cls.folder.name)
        cls.first=tone(440,660)
        cls.last=tone(740,980)
        cls.mixed=original_multiscene_pcm((cls.first,cls.last))
        cls.first_path=cls.root/"original-focus-sfx.wav"
        cls.second_path=cls.root/"original-confirm-sfx.wav"
        cls.review_path=cls.root/"semantic-sfx-master.wav"
        cls.filename="mw-sfx-source-bound-r9-f540-0123456789abcdef.wav"
        wav(cls.first_path,cls.first)
        wav(cls.second_path,cls.last)
        wav(cls.review_path,cls.mixed)
        (cls.root/cls.filename).write_bytes(cls.review_path.read_bytes())
        cls.receipt={
            "schema":"motionwright.semantic-sfx-av-prepared/1",
            "frames":540,"fps":"30/1","sample_rate":48000,"channels":2,
            "sample_count":864000,
            "revision":9,"native_preparation_verified":True,
            "native_preparation_revision":9,"original_source_count":2,
            "scene_ids":[
                "00000000-0000-4000-8000-000000000001",
                "00000000-0000-4000-8000-000000000002",
                "00000000-0000-4000-8000-000000000003",
            ],
            "source_cue_ids":[
                "10000000-0000-4000-8000-000000000001",
                "10000000-0000-4000-8000-000000000002",
            ],
            "audio_relative":cls.filename,
            "first_source_sha256":sha(cls.first_path),
            "second_source_sha256":sha(cls.second_path),
            "audio_sha256":sha(cls.review_path),
            "scheduled_sfx":[
                {"absolute_start_frame":6,"absolute_end_frame":30,
                 "first_sample":9600,"sample_count":38400},
                {"absolute_start_frame":380,"absolute_end_frame":404,
                 "first_sample":608000,"sample_count":38400},
            ],
            "sample_peak_pcm16":max(max(abs(l),abs(r)) for l,r in cls.mixed),
        }
        cls.pcm=b"".join(struct.pack("<hh",l,r) for l,r in cls.mixed)

    @classmethod
    def tearDownClass(cls)->None:
        cls.folder.cleanup()

    def test_immutable_cas_sources_rebuild_exact_third_scene_pcm(self):
        report=inspect_original_composition(self.root,self.receipt)
        self.assertEqual(report["max_sample_difference_pcm16"],0)
        self.assertEqual(report["exact_sfx_sample_count"],PCM_SAMPLES)
        self.assertEqual(report["original_scene_count"],3)
        self.assertEqual(report["middle_scene_silence_sample_count"],288000)
        self.assertEqual(report["real_imported_original_stems"],2)

    def test_independent_decoded_windows_identify_two_distant_scene_sources(self):
        result=inspect_decoded_multiscene_audio(self.pcm)
        self.assertEqual(result["stereo_channel_identity"],
                         "confirmed_scene0_440_660_scene2_740_980_hz")
        self.assertEqual(result["expected_onset_samples"],[9600,608000])
        self.assertLessEqual(result["max_absolute_onset_error_samples"],1024)
        self.assertLess(result["semantic_sfx_windows_pcm16"]["silence_pcm16_rms"]["middle_scene"],1)

    def test_wrong_scene_clock_rights_or_source_digest_fail_closed(self):
        for key,value in [
            ("frames",60),("sample_count",96000),
            ("native_preparation_verified",False),
            ("native_preparation_revision",7),
            ("original_source_count",1),
            ("audio_relative","../private.wav"),
            ("first_source_sha256","f"*64),
            ("second_source_sha256","f"*64),
            ("audio_sha256","f"*64),
            ("scene_ids",["only-one"]),
            ("source_cue_ids",[]),
        ]:
            with self.subTest(key=key):
                broken={**self.receipt,key:value}
                with self.assertRaises(MediaProbeError):
                    inspect_original_composition(self.root,broken)

    def test_shifted_last_scene_sfx_sample_is_rejected(self):
        candidate=json.loads(json.dumps(self.receipt))
        candidate["scheduled_sfx"][1]["absolute_start_frame"]=379
        with self.assertRaises(MediaProbeError):
            inspect_original_composition(self.root,candidate)
        candidate=json.loads(json.dumps(self.receipt))
        candidate["scheduled_sfx"][1]["first_sample"]=609600
        with self.assertRaises(MediaProbeError):
            inspect_original_composition(self.root,candidate)

    def test_silent_swapped_or_late_native_aac_cannot_pass(self):
        swapped=b"".join(struct.pack("<hh",r,l) for l,r in self.mixed)
        quiet=bytes(len(self.pcm))
        shifted=bytes(9600*4)+self.pcm[:-9600*4]
        for name,data in (("swapped",swapped),("silent",quiet),("late",shifted)):
            with self.subTest(name=name),self.assertRaises(MediaProbeError):
                inspect_decoded_multiscene_audio(data)

    def test_missing_real_h264_cannot_publish_verified_multiscene(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/"artifact"
            result=curate(self.root,target,"a"*40,True)
            self.assertEqual(result["technical_result"],"NOT_VERIFIED")
            self.assertEqual(result["files"],{})
            self.assertFalse((target/"motionwright-master.mp4").exists())


if __name__=="__main__":
    unittest.main()
