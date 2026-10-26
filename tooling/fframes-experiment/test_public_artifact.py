"""Synthetic protocol fixtures exercise custody gates, never native acceptance."""
from __future__ import annotations

import base64
import copy
import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest import mock
import wave

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import measure as m
import ci_private_evidence as custody
import public_artifact as pub
from test_measure import capture_completed, framehash_bytes, png_bytes

MW = "a" * 40
SDK = "b" * 40


def _pcm_from_input(raw):
    with wave.open(io.BytesIO(raw), "rb") as stream:
        original = stream.readframes(stream.getnframes())
    return b"".join(struct.pack("<f", value[0] / 32768) for value in struct.iter_unpack("<h", original))


def _float_wav(pcm):
    fmt = struct.pack("<HHIIHH", 3, 2, 48000, 48000 * 8, 8, 32)
    body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def protocol_fixture(private):
    """Files are intentionally synthetic. This helper performs no subprocesses."""
    metrics, captures = {}, {}
    checkout = private.parent / "upstream"
    source_root = private.parent / "motionwright"
    binary = str(checkout / "target/release" / m.APP)
    ffmpeg, ffprobe = str(private.parent / "ffmpeg"), str(private.parent / "ffprobe")
    manifest = (f'[workspace.dependencies]\nsemwright-types = {{git="https://github.com/seradotcom/semwright", rev="{SDK}"}}\n').encode()
    (private / "motionwright-Cargo.toml").write_bytes(manifest)
    for folder in ("frame-preview", "frame-preview-repeat", "audio"):
        (private / folder).mkdir()
    first_png, rgba = png_bytes(m.WIDTH, m.HEIGHT, level=0)
    repeat_png, _ = png_bytes(m.WIDTH, m.HEIGHT, level=9)
    (private / "frame-preview/30.png").write_bytes(first_png)
    (private / "frame-preview-repeat/30.png").write_bytes(repeat_png)
    movie = b"synthetic unit-test carrier; not a playable native movie\0" + b"x" * 2048
    (private / "upstream-source-preview.mp4").write_bytes(movie)
    spec = m.create_audio_fixture(private / "audio/inputs")
    bed_wav = (private / "audio/inputs/bed.wav").read_bytes()
    cue_wav = (private / "audio/inputs/cue.wav").read_bytes()
    bed, cue = _pcm_from_input(bed_wav), _pcm_from_input(cue_wav)
    values = [item[0] * 0.5 for item in struct.iter_unpack("<f", bed)]
    for i, item in enumerate(struct.iter_unpack("<f", cue)):
        values[48000 + i] += item[0] * 0.25
    mixed = struct.pack("<" + str(len(values)) + "f", *values)
    mixed_wav = _float_wav(mixed)
    (private / "audio/native-mix.wav").write_bytes(mixed_wav)
    (private / "audio/probe.rs").write_bytes(m.AUDIO_PROBE_RS.encode())
    video_meta = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264",
                                "width": m.WIDTH, "height": m.HEIGHT, "r_frame_rate": "30/1", "nb_frames": "30"}],
                  "format": {"duration": "1.000000"}}
    audio_meta = {"streams": [{"codec_type": "audio", "codec_name": "pcm_f32le", "sample_rate": "48000",
                                "channels": 2, "duration": "2.000000"}]}
    audio_cli = {"output": str(private / "audio/native-mix.wav"), "start_seconds": 0.0,
                 "end_seconds": 2.0, "missing_files": []}
    fd_path = "/proc/12345/fd/42"
    file_input = {"kind": "retained_regular_descriptor", "descriptor_path": fd_path,
                  "relative_path": "upstream-source-preview.mp4", "bytes": len(movie), "sha256": m.sha(movie)}
    build = ["cargo", "build", "--locked", "--quiet", "--release", "--manifest-path", str(checkout / "Cargo.toml"),
             "--target-dir", str(checkout / "target"), "-p", m.PKG]
    commands = {
        "motionwright_source_head": ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        "motionwright_source_manifest": ["git", "-C", str(source_root), "show", "HEAD:Cargo.toml"],
        "source_checkout": ["git", "clone", "--quiet", "--filter=blob:none", m.REPO, str(checkout)],
        "exact_source_checkout": ["git", "-C", str(checkout), "checkout", "--detach", "--quiet", m.PIN],
        "upstream_source_head": ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        "upstream_tracked_before": ["git", "-C", str(checkout), "diff", "--exit-code", "HEAD", "--"],
        "upstream_tracked_after": ["git", "-C", str(checkout), "diff", "--exit-code", "HEAD", "--"],
        "cold_release_build": build + ["--bin", m.APP], "warm_release_build": build + ["--bin", m.APP],
        "audio_probe_build": build + ["--example", m.AUDIO_APP],
        "cli_startup_0": [binary, "--help"], "cli_startup_1": [binary, "--help"],
        "cold_headless_native_preview": [binary, "frame", "1s", "-o", str(private / "frame-preview"), "--scale", "0.25"],
        "warm_headless_native_preview": [binary, "frame", "1s", "-o", str(private / "frame-preview-repeat"), "--scale", "0.25"],
        "cold_bounded_video_render": [binary, "render", "0s..1s", "--draft", "--scale", "0.25", "-o",
                                        str(private / "upstream-source-preview.mp4")],
        "native_audio_mix_render": [str(checkout / "target/release/examples" / m.AUDIO_APP), "--json", "--assets",
                                     str(private / "audio/inputs"), "audio", "render", "0s..2s", "--float", "-o",
                                     str(private / "audio/native-mix.wav")],
        "ffmpeg_version": [ffmpeg, "-version"], "ffprobe_version": [ffprobe, "-version"],
        "rustc_version": ["rustc", "--version"], "architecture": ["uname", "-m"],
        "video_metadata": [ffprobe, "-v", "error", "-show_entries",
                           "stream=index,codec_type,codec_name,width,height,r_frame_rate,nb_frames", "-show_entries",
                           "format=duration", "-of", "json", fd_path],
        "audio_mix_metadata": [ffprobe, "-v", "error", "-show_entries",
                               "stream=codec_name,codec_type,sample_rate,channels,duration", "-of", "json",
                               str(private / "audio/native-mix.wav")],
        "video_full_framehash": [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-noautorotate",
                                 "-i", fd_path, "-map", "0:v:0", "-an", "-sn", "-dn", "-vsync", "0", "-pix_fmt", "rgba",
                                 "-c:v", "rawvideo", "-f", "framehash", "-hash", "sha256", "pipe:1"],
    }
    for label in ("cold_png_rgba_decode", "warm_png_rgba_decode"):
        commands[label] = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-noautorotate", "-f",
                           "image2pipe", "-c:v", "png", "-i", "pipe:0", "-map", "0:v:0", "-an", "-sn", "-dn",
                           "-pix_fmt", "rgba", "-c:v", "rawvideo", "-f", "rawvideo", "pipe:1"]
    for label in ("audio_mix_pcm_decode", "audio_bed_pcm_decode", "audio_cue_pcm_decode"):
        commands[label] = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-f", "wav", "-i",
                           "pipe:0", "-map", "0:a:0", "-vn", "-sn", "-dn", "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"]
    streams = {
        "motionwright_source_head": (MW + "\n").encode(), "upstream_source_head": (m.PIN + "\n").encode(),
        "motionwright_source_manifest": manifest, "upstream_tracked_before": b"", "upstream_tracked_after": b"",
        "cold_png_rgba_decode": rgba, "warm_png_rgba_decode": rgba,
        "video_metadata": m.json_bytes(video_meta), "video_full_framehash": framehash_bytes(),
        "audio_mix_metadata": m.json_bytes(audio_meta), "native_audio_mix_render": m.json_bytes(audio_cli),
        "audio_mix_pcm_decode": mixed, "audio_bed_pcm_decode": bed, "audio_cue_pcm_decode": cue,
    }
    inputs = {"cold_png_rgba_decode": first_png, "warm_png_rgba_decode": repeat_png,
              "audio_mix_pcm_decode": mixed_wav, "audio_bed_pcm_decode": bed_wav, "audio_cue_pcm_decode": cue_wav}
    for label in pub.ALL_PHASES:
        folder = private / "processes" / label
        folder.mkdir(parents=True)
        stdout, stderr, stdin = streams.get(label, b"synthetic original\n"), b"", inputs.get(label)
        request = {"argv": commands[label], "cwd": str(private), "timeout_seconds": 15,
                   "stdout_limit_bytes": max(m.MAX_STDOUT, len(stdout) + 1), "stderr_limit_bytes": m.MAX_STDERR,
                   "cleanup_allowance_seconds": 10, "automatic_retry": False,
                   "stdin_bytes": None if stdin is None else len(stdin), "stdin_sha256": None if stdin is None else m.sha(stdin),
                   "file_input": file_input if label in {"video_metadata", "video_full_framehash"} else None}
        cap = {}
        capture_completed(cap, stdout, stderr, input_bytes=stdin)
        cap.pop("stdout")
        cap.pop("stderr")
        cap.update({"stdout_sha256": m.sha(stdout), "stdout_retained_bytes": len(stdout),
                    "stderr_sha256": m.sha(stderr), "stderr_retained_bytes": len(stderr)})
        argv_raw, cap_raw = m.json_bytes(request), m.json_bytes(cap)
        for name, raw in (("argv.json", argv_raw), ("capture.json", cap_raw),
                          ("stdout.bin", stdout), ("stderr.bin", stderr)):
            (folder / name).write_bytes(raw)
        if stdin is not None:
            (folder / "stdin.bin").write_bytes(stdin)
        metrics[label] = {"capture": "processes/" + label, "argv_sha256": m.sha(argv_raw), "originals_retained": True,
                          "capture_sha256": m.sha(cap_raw), "elapsed_seconds": 0.01, "exit_code": 0}
        captures[label] = cap
    def preview(raw, relative, label):
        return {"width": m.WIDTH, "height": m.HEIGHT, "bytes": len(raw), "sha256": m.sha(raw),
                "transport_crc_checked": True, "rgba_sha256": m.sha(rgba), "rgba_bytes": len(rgba),
                "pixel_format": "rgba", "decode_capture": label, "encoded_path": relative}
    comparison = m.compare_audio_pcm(bed, cue, mixed)
    report = {
        "schema": m.EXPERIMENT_SCHEMA, "status": "EXPERIMENTAL_UPSTREAM_SOURCE_RENDER_PASS", "motionwright_sha": MW,
        "fframes_exact_sha": m.PIN, "fframes_license": m.LICENSE, "fframes_repository": m.REPO,
        "runtime": "disposable_ubuntu_24_04", "source_code_admitted_to_motionwright": False,
        "semwright_driver_host_executed": False, "owner_install_permission": False, "backend_release_admitted": False,
        "artistic_quality_reviewed": False, "private_originals_upload_confirmed": False,
        "hyperframes_same_fixture_comparison": "NOT_RUN", "interactive_gpu_preview": "NOT_RUN_HEADLESS_CI",
        "audio_score_composition": "OBSERVED_SYNTHETIC_STEREO_MIX", "cold_build_measured": True,
        "warm_build_measured": True, "frame_preview_measured": True, "full_video_render_measured": True,
        "declared_semwright_sha": SDK, "source_manifest_sha256": m.sha(manifest),
        "independent_tools": {"ffmpeg_sha256": "c" * 64, "ffprobe_sha256": "d" * 64}, "binary_sha256": "e" * 64,
        "measurements": metrics, "preview_png": preview(first_png, "frame-preview/30.png", "cold_png_rgba_decode"),
        "repeated_preview_png": preview(repeat_png, "frame-preview-repeat/30.png", "warm_png_rgba_decode"),
        "repeated_encoded_png_identical": first_png == repeat_png, "repeated_decoded_rgba_identical": True,
        "video": {"encoded_path": "upstream-source-preview.mp4", "bytes": len(movie), "sha256": m.sha(movie),
                  "codec": "h264", "fps": "30/1", "width": m.WIDTH, "height": m.HEIGHT, "duration_seconds": 1.0,
                  "metadata_capture": "video_metadata", "decode_capture": "video_full_framehash",
                  "decode": m.parse_framehash(framehash_bytes(), m.WIDTH, m.HEIGHT, 30),
                  "retained_descriptor_checked_before_after": True},
        "audio": {**comparison, "status": "OBSERVED_SYNTHETIC_STEREO_MIX", "original_wav_sha256": m.sha(mixed_wav),
                  "original_wav_bytes": len(mixed_wav), "encoded_path": "audio/native-mix.wav",
                  "fixture_path": "audio/fixture.json", "fixture_sha256": m.sha(m.json_bytes(spec)),
                  "probe_source_path": "audio/probe.rs", "probe_source_sha256": m.sha(m.AUDIO_PROBE_RS.encode()),
                  "binary_sha256": "f" * 64, "render_capture": "native_audio_mix_render", "decode_capture": "audio_mix_pcm_decode",
                  "baseline_hello_world_modified": False, "baseline_build_timings_reused": False},
        "failure_summary": "private path / secret stderr must never be public",
    }
    (private / "result.json").write_bytes(m.json_bytes(report))
    return report


class PublicFframesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = cls.key.public_key().public_bytes(serialization.Encoding.DER,
                                                 serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient = {"spki_der_base64": base64.b64encode(der).decode(), "sha256": m.sha(der)}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mw-fframes-public-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.private, self.public = self.root / "private", self.root / "public"
        self.private.mkdir()
        self.report = protocol_fixture(self.private)
        self.context = {"source_sha": MW, "semwright_sha": SDK, "run_id": "123", "run_attempt": "1"}

    def save(self):
        (self.private / "result.json").write_bytes(m.json_bytes(self.report))

    def publish(self, status="success", **overrides):
        args = {"recipient": self.recipient, "context": self.context}
        args.update(overrides)
        return pub.publish(self.private, self.public, MW, status, **args)

    def recorded(self):
        return json.loads((self.public / "public-evidence.json").read_bytes())

    def reject(self, reason):
        with self.assertRaises((AssertionError, ValueError, OSError)) as caught:
            self.publish()
        self.assertIn(reason, str(caught.exception))
        self.assertEqual(self.recorded()["ci_evidence"], "NOT_VERIFIED")

    def rewrite_capture(self, label, update):
        path = self.private / "processes" / label / "capture.json"
        obj = json.loads(path.read_bytes())
        update(obj)
        raw = m.json_bytes(obj)
        path.write_bytes(raw)
        self.report["measurements"][label]["capture_sha256"] = m.sha(raw)
        self.save()

    def test_synthetic_upstream_metrics_are_public_as_facts_not_adoption(self):
        report = self.publish()
        self.assertEqual(report["ci_evidence"], "PASS")
        self.assertFalse(report["renderer_adoption_authorized"])
        self.assertFalse(report["v05_release_ready"])
        self.assertEqual(report["audio_fidelity_assessed"], "SYNTHETIC_STEREO_MIX_ONLY")
        self.assertEqual(report["human_creative_acceptance"], "NOT_RUN")
        self.assertFalse(report["private_original_custody"]["upload_confirmed"])
        self.assertEqual(set(report["actual_measured_seconds"]), set(pub.PHASES))
        self.assertEqual(sorted(p.name for p in self.public.iterdir()),
                         [pub.SEAL_FILE, "public-evidence.json"])
        public_raw = (self.public / "public-evidence.json").read_bytes()
        self.assertNotIn(b"private path", public_raw)
        self.assertNotIn(b"failure_summary", public_raw)
        self.assertNotIn(str(self.private).encode(), public_raw)

    def test_sealed_capsule_recovers_exact_original_streams_and_context(self):
        report = self.publish()
        encrypted = (self.public / pub.SEAL_FILE).read_bytes()
        raw, context = custody.decrypt_snapshot(encrypted, self.key)
        self.assertEqual(context, self.context)
        self.assertEqual(report["private_original_custody"]["capsule_sha256"], m.sha(encrypted))
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            name = "fframes/processes/cold_png_rgba_decode/stdout.bin"
            actual = archive.extractfile(name).read()
            self.assertEqual(actual, (self.private / name.removeprefix("fframes/")).read_bytes())
            self.assertEqual(archive.extractfile("fframes/result.json").read(),
                             (self.private / "result.json").read_bytes())

    def test_failure_status_does_not_publish_stale_pass(self):
        report = self.publish("failure")
        self.assertEqual(report["ci_evidence"], "NOT_VERIFIED")
        self.assertEqual(report["outcome_category"], "UPSTREAM_JOB_FAILED")
        self.assertEqual(report["actual_measured_seconds"], {})
        self.assertEqual(report["source_preview"], {})
        self.assertEqual(report["source_one_second_video"], {})
        self.assertEqual(report["private_original_custody"]["status"], "SEALED")

    def test_early_failure_without_source_receipt_still_seals_without_source_credit(self):
        (self.private / "result.json").unlink()
        report = self.publish("failure")
        self.assertEqual(report["ci_evidence"], "NOT_VERIFIED")
        self.assertEqual(report["private_original_custody"]["status"], "SEALED_UNVERIFIED_SOURCE")
        self.assertFalse(report["private_original_custody"]["source_binding_verified"])
        self.assertTrue((self.public / pub.SEAL_FILE).is_file())

    def test_foreign_commit_is_not_allowed_to_claim_pass(self):
        self.report["motionwright_sha"] = "c" * 40
        self.save()
        self.reject("unverified_or_admitted")
        self.assertEqual(self.recorded()["private_original_custody"]["status"], "SEALED_UNVERIFIED_SOURCE")

    def test_renderer_adoption_cannot_be_spoofed(self):
        self.report["backend_release_admitted"] = True
        self.save()
        self.reject("unverified_or_admitted")

    def test_missing_timing_is_not_equivalent_to_source_measurement(self):
        del self.report["measurements"]["warm_release_build"]
        self.save()
        self.reject("source_process_roster")

    def test_invalid_native_preview_and_video_hash_refused(self):
        self.report["preview_png"]["rgba_sha256"] = self.report["preview_png"]["sha256"]
        self.save()
        self.reject("preview_decoded_original_binding")

    def test_duplicate_or_ambiguous_evidence_fields_rejected(self):
        (self.private / "result.json").write_bytes(b'{"status":"PASS","status":"different"}')
        self.reject("invalid_or_duplicate")

    def test_existing_target_and_private_overlap_denied(self):
        self.public.mkdir()
        with self.assertRaisesRegex(pub.Refused, "existing_public_destination"):
            self.publish("failure")
        with self.assertRaisesRegex(pub.Refused, "overlapping_private_public_roots"):
            pub.publish(self.private, self.private / "out", MW, "failure")

    def test_actual_rgba_bytes_are_required_even_with_a_success_result(self):
        (self.private / "processes/cold_png_rgba_decode/stdout.bin").unlink()
        self.reject("stdout.bin")

    def test_incomplete_terminal_and_boolean_exit_are_rejected(self):
        self.rewrite_capture("cold_png_rgba_decode", lambda obj: obj["terminal"].update({"pipes_closed": False}))
        self.reject("source_capture_incomplete")

    def test_boolean_exit_is_not_an_integer_success(self):
        self.rewrite_capture("cold_png_rgba_decode", lambda obj: obj["terminal"].update({"exit_code": False}))
        self.reject("source_capture_incomplete")

    def test_edited_argv_cannot_relabel_a_process_as_native_render(self):
        path = self.private / "processes/cold_bounded_video_render/argv.json"
        request = json.loads(path.read_bytes())
        request["argv"] = ["python", "synthetic-video.py"]
        raw = m.json_bytes(request)
        path.write_bytes(raw)
        self.report["measurements"]["cold_bounded_video_render"]["argv_sha256"] = m.sha(raw)
        self.save()
        self.reject("baseline_video_render_argv")

    def test_ffprobe_metadata_cannot_replace_full_decode_original(self):
        path = self.private / "processes/video_full_framehash/stdout.bin"
        raw = (self.private / "processes/video_metadata/stdout.bin").read_bytes()
        path.write_bytes(raw)
        self.rewrite_capture("video_full_framehash", lambda obj: (
            obj.update({"stdout_sha256": m.sha(raw), "stdout_retained_bytes": len(raw)}),
            obj["terminal"].update({"stdout_bytes": len(raw)})))
        self.reject("framehash")

    def test_audio_not_run_and_fabricated_human_acceptance_are_rejected(self):
        self.report["audio_score_composition"] = "NOT_RUN_NO_AUDIO_IN_HELLO_FIXTURE"
        self.save()
        self.reject("unverified_or_admitted")

    def test_modified_audio_original_cannot_inherit_its_digest(self):
        path = self.private / "audio/native-mix.wav"
        raw = path.read_bytes()
        path.write_bytes(raw[:-4] + struct.pack("<f", 0.5))
        self.reject("audio_decode_binding")

    def test_missing_key_or_context_cannot_claim_pass(self):
        with self.assertRaisesRegex(pub.Refused, "private_custody_inputs_required"):
            self.publish(recipient=None, context=None)
        report = self.recorded()
        self.assertEqual(report["ci_evidence"], "NOT_VERIFIED")
        self.assertEqual(report["private_original_custody"]["status"], "NOT_SEALED")
        self.assertFalse((self.public / pub.SEAL_FILE).exists())

    def test_upstream_pin_cannot_substitute_for_the_real_declared_sdk_pin(self):
        wrong = {**self.context, "semwright_sha": m.PIN}
        with self.assertRaisesRegex(pub.Refused, "declared_sdk_context"):
            self.publish(context=wrong)
        self.assertEqual(self.recorded()["ci_evidence"], "NOT_VERIFIED")

    def test_primary_validation_error_survives_failed_single_custody_attempt(self):
        primary = ValueError("original evidence rejection")
        with mock.patch.object(pub, "validate_originals", side_effect=primary):
            with mock.patch.object(custody, "gather_snapshot", side_effect=OSError("secondary custody")) as gather:
                with self.assertRaises(ValueError) as caught:
                    self.publish()
        self.assertIs(caught.exception, primary)
        gather.assert_called_once()
        self.assertEqual(self.recorded()["outcome_category"], "ORIGINAL_EVIDENCE_REJECTED")

    def test_failed_experiment_keeps_primary_category_when_sealing_fails(self):
        with mock.patch.object(custody, "encrypt_snapshot", side_effect=OSError("private secret failure")) as seal:
            report = self.publish("failure")
        seal.assert_called_once()
        self.assertEqual(report["outcome_category"], "UPSTREAM_JOB_FAILED")
        self.assertEqual(report["private_original_custody"]["status"], "NOT_SEALED")
        self.assertNotIn("private secret failure", json.dumps(report))

    def test_changed_original_between_validation_and_snapshot_is_sealed_without_pass(self):
        original_gather = custody.gather_snapshot
        def change_then_capture(*args, **kwargs):
            path = self.private / "processes/cold_png_rgba_decode/stdout.bin"
            raw = path.read_bytes()
            path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
            return original_gather(*args, **kwargs)
        with mock.patch.object(custody, "gather_snapshot", side_effect=change_then_capture) as gather:
            with mock.patch.object(custody, "encrypt_snapshot", wraps=custody.encrypt_snapshot) as seal:
                with self.assertRaisesRegex(pub.Refused, "snapshot_incomplete_or_changed"):
                    self.publish()
        gather.assert_called_once()
        seal.assert_called_once()
        report = self.recorded()
        self.assertEqual(report["private_original_custody"]["status"], "SEALED")
        self.assertFalse(report["private_original_custody"]["validated_originals_match_snapshot"])
        self.assertEqual(report["ci_evidence"], "NOT_VERIFIED")

    def test_partial_snapshot_is_sealed_but_never_accepted(self):
        gather_real = custody.gather_snapshot
        def partial(*args, **kwargs):
            raw, _complete = gather_real(*args, **kwargs)
            return raw, False
        with mock.patch.object(custody, "gather_snapshot", side_effect=partial):
            with self.assertRaisesRegex(pub.Refused, "snapshot_incomplete_or_changed"):
                self.publish()
        self.assertEqual(self.recorded()["private_original_custody"]["status"], "SEALED")
        self.assertFalse(self.recorded()["private_original_custody"]["selected_originals_complete"])

    def test_public_destination_failure_preserves_original_and_stops_all_custody(self):
        cases = (("validation", ValueError("original evidence rejection")),
                 ("interruption", KeyboardInterrupt("original supervisor interruption")),
                 ("no_previous_error", None))
        for category, primary in cases:
            with self.subTest(category=category):
                destination_error = OSError("public destination cannot be created")
                expected = destination_error if primary is None else primary
                with mock.patch.object(pub, "validate_originals", return_value=({}, {}),
                                       side_effect=primary):
                    with mock.patch.object(pub.Path, "mkdir", side_effect=destination_error) as mkdir:
                        with mock.patch.object(custody, "gather_snapshot") as gather:
                            with mock.patch.object(custody, "encrypt_snapshot") as seal:
                                with mock.patch.object(custody, "new_bytes") as sealed_writer:
                                    with mock.patch.object(m, "new_bytes") as receipt_writer:
                                        with self.assertRaises(type(expected)) as caught:
                                            self.publish()
                self.assertIs(caught.exception, expected)
                mkdir.assert_called_once()
                gather.assert_not_called()
                seal.assert_not_called()
                sealed_writer.assert_not_called()
                receipt_writer.assert_not_called()
                self.assertFalse(self.public.exists())
                self.assertFalse((self.public / pub.SEAL_FILE).exists())
                self.assertFalse((self.public / "public-evidence.json").exists())

    def test_supervisory_interrupt_is_not_replaced_by_custody_failure(self):
        primary = KeyboardInterrupt("original supervisor interruption")
        with mock.patch.object(pub, "validate_originals", side_effect=primary):
            with mock.patch.object(custody, "gather_snapshot", side_effect=OSError("secondary")):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    self.publish()
        self.assertIs(caught.exception, primary)


if __name__ == "__main__":
    unittest.main()
