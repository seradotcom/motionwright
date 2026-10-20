#!/usr/bin/env python3
"""Prepare a pinned MLT planar repair with the independently pinned observer.

This is a source verification tool. It does not compile, install, admit, or
dispatch a provider. The existing source generators each receive only the
unmodified consumer that their own source contracts admit.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys

ORIGINAL_SHA256 = "c1c8850ff049a8775bb3d24c64be9b9365659773ce019902585c056f5be2f910"
REPAIRED_SHA256 = "246e84b02d25d0fe5ea08d59d5abb03c6dfd42d5d15dabeb464c8051f8ed8de0"
INSTRUMENTED_SHA256 = "9190366bf2cddfd8214497fa9ec42fa2ef55dfbed5f8248c69b7aaae7854cabb"
COMBINED_SHA256 = "579eba9bb5a128e78cf078e0ae47f72a6ddbeb9147cf4451a4900541ed9eb56c"
HELPER_SHA256 = "d90db74a261da8053a7f8258f1b9975e692ab54888408cd0a2818ee26e720bf9"
LEGACY_HELPER_SHA256 = "fa56e6635f8731f62c9fca1305ee1b916e752526bbeeaaa4404addb24e327013"
LEGACY_HELPER_REFERENCE = {
    "source_sha": "3fce3e7688273548af79fd5d39f5106355798a4c",
    "generator_path": "tooling/mlt-audio-diagnostic/repair_planar_source.py",
    "generator_sha256": "709cff5e40e306762d203c61135769926e49d5d6dbbd2e2c4b9e0e06597c2c29",
    "helper_sha256": LEGACY_HELPER_SHA256,
    "source_reference_only": True,
}
# Preserve the byte-exact pre-U8P helper as a historical negative control.
# Its source reference is not an execution of that prior commit in this run.
LEGACY_HELPER = b"""static uint8_t *interleaved_to_planar(int samples,
                                      int prepared_samples,
                                      int channels,
                                      uint8_t *audio,
                                      int bytes_per_sample,
                                      enum AVSampleFormat sample_fmt)
{
    int linesize = 0;
    int allocated_bytes = av_samples_get_buffer_size(&linesize,
                                                     channels,
                                                     prepared_samples,
                                                     sample_fmt,
                                                     0);
    if (samples < 0 || samples > prepared_samples || channels < 1
        || channels > MAX_AUDIO_STREAMS * 2 || bytes_per_sample < 1
        || bytes_per_sample != av_get_bytes_per_sample(sample_fmt)
        || !av_sample_fmt_is_planar(sample_fmt)
        || allocated_bytes <= 0 || allocated_bytes > AUDIO_ENCODE_BUFFER_SIZE
        || linesize <= 0
        || (int64_t) linesize * channels > AUDIO_ENCODE_BUFFER_SIZE)
        return NULL;

    uint8_t *buffer = mlt_pool_alloc(AUDIO_ENCODE_BUFFER_SIZE);
    if (!buffer)
        return NULL;
    memset(buffer, 0, AUDIO_ENCODE_BUFFER_SIZE);
    for (int c = 0; c < channels; c++) {
        uint8_t *p = buffer + (size_t) c * linesize;
        uint8_t *q = audio + (size_t) c * bytes_per_sample;
        for (int i = 0; i < samples; i++) {
            memcpy(p, q, bytes_per_sample);
            p += bytes_per_sample;
            q += channels * bytes_per_sample;
        }
    }
    return buffer;
}

"""
MAX_SOURCE_BYTES = 256 * 1024
DIAGNOSTIC_FOLDER = "tooling/mlt-audio-diagnostic"
VERIFIER_PATH = "tooling/mlt_repair_verification.py"
VERIFIER_TEST_PATH = "tooling/tests/test_mlt_repair_verification.py"
DIAGNOSTIC_DIGESTS = {
    "README.md": "ce82e3ce647603385ca3233b1bdebc1c25cf732a6ad9758a7d0a8fad7ec51e1d",
    "audio_tap.h": "06bd150f81728aa0339ac70f9f4d19f29100f4a7902144cd092643f48b7e9d2c",
    "instrument_consumer.py": "b211aedb430412bb36f7e5e86d708d9dd4b11c0287a31ba85f183380223f2576",
    "repair_planar_source.py": "028d1f6e7e2a034aeb60b0e40dfc5154af401691f960483c3d0b77fe09bde87d",
    "test_audio_tap.c": "4d19fdfdf37d8380d318b731269fe08a835db89de8b671dbb60fe9e61b1f3c1b",
    "test_planar_padding.c": "ab44f0a12a583d878cfbbb0272a209c8526446128cef660b505053d9463e5b9b",
    "test_repair_planar_source.py": "33753a745e38f77b11ca29d875e47d39a291badc57dc10cb90fb58a1fc149ae4",
    "test_verify_trace.py": "ffd144a932604e2beb9681a9608dc449b360623717be4d90c74cdc7475bbc261",
    "verify_trace.py": "7d2c809c0beea42ebf2e1183fc13bb16871032892a8b6f12fa6ff926ee854ed6",
}
OUTPUT_DIGESTS = {
    "original-consumer.c": ORIGINAL_SHA256,
    "repaired-consumer.c": REPAIRED_SHA256,
    "instrumented-original-consumer.c": INSTRUMENTED_SHA256,
    "consumer_avformat.c": COMBINED_SHA256,
    "planar-helper-under-test.h": HELPER_SHA256,
    "legacy-planar-helper-under-test.h": LEGACY_HELPER_SHA256,
    "audio_tap.h": DIAGNOSTIC_DIGESTS["audio_tap.h"],
}
FALSE_CLAIMS = (
    "provider_compiled",
    "provider_admitted",
    "native_dispatched",
    "master_audio_tail_corrected",
    "production_master_accepted",
    "release_authorized",
)


def require(ok: object, message: str) -> None:
    if not ok:
        raise ValueError(message)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def legacy_planar_helper() -> bytes:
    require(sha(LEGACY_HELPER) == LEGACY_HELPER_SHA256,
            "historical planar helper digest differs")
    return LEGACY_HELPER


def validate_legacy_rejection(returncode: int, stdout: bytes, stderr: bytes) -> dict:
    """Admit only the original zero-padding defect, never a generic C failure."""
    require(type(returncode) is int and returncode == 1,
            "legacy helper must return exactly the expected rejection exit 1")
    require(type(stdout) is bytes and stdout == b"",
            "legacy helper emitted unexpected stdout")
    observations = [(576, 0), (576, 1), (1, 0), (1, 1)]
    expected = b"".join(
        ("MLT_PLANAR_RED repaired plane diverged from source or FFmpeg silence "
         f"format=5 consumed={consumed} prepared=1024 channel={channel} "
         f"frame={consumed}\n").encode("ascii")
        for consumed, channel in observations
    ) + b"MLT_PLANAR_FAIL checks=12 errors=4\n"
    require(type(stderr) is bytes and stderr == expected,
            "legacy helper did not produce exactly four original U8P tail divergences")
    return {
        "expected_rejection_observed": True,
        "exit_code": returncode,
        "case_count_reported": 12,
        "u8p_tail_mismatches": [
            {"format": 5, "consumed": consumed, "prepared": 1024,
             "channel": channel, "frame": consumed}
            for consumed, channel in observations
        ],
        "stdout_sha256": sha(stdout),
        "stderr_sha256": sha(stderr),
    }


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def regular_path(path: Path, directory: bool = False) -> None:
    require(path.is_absolute(), "path must be absolute")
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode), "symlink source or ancestor")
        require(not getattr(info, "st_file_attributes", 0) & 0x400, "reparse source or ancestor")
        if item != path or directory:
            require(stat.S_ISDIR(info.st_mode), "directory must be regular")
        else:
            require(stat.S_ISREG(info.st_mode), "source must be a regular file")


def read_regular(path: Path, limit: int = MAX_SOURCE_BYTES) -> bytes:
    regular_path(path)
    before = path.stat()
    require(before.st_size <= limit, "source exceeds bounded size")
    raw = path.read_bytes()
    after = path.stat()
    identity = lambda value: (
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns
    )
    require(identity(before) == identity(after) and len(raw) == before.st_size,
            "source changed during read")
    return raw


def read_json(path: Path) -> dict:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON field")
            result[key] = value
        return result
    value = json.loads(read_regular(path, 64 * 1024), object_pairs_hook=no_duplicates)
    require(isinstance(value, dict), "receipt must be an object")
    return value


def source_snapshot(source_dir: Path, verification_dir: Path) -> dict[str, bytes]:
    regular_path(source_dir, directory=True)
    regular_path(verification_dir, directory=True)
    require({p.name for p in source_dir.iterdir()} == set(DIAGNOSTIC_DIGESTS),
            "diagnostic source set is not closed")
    require({p.name for p in verification_dir.iterdir()}
            == {"mlt_repair_verification.py", "test_mlt_repair_verification.py"},
            "verification source set is not closed")
    result = {}
    for name, expected in DIAGNOSTIC_DIGESTS.items():
        data = read_regular(source_dir / name)
        require(sha(data) == expected, "diagnostic source digest differs: " + name)
        result[DIAGNOSTIC_FOLDER + "/" + name] = data
    result[VERIFIER_PATH] = read_regular(verification_dir / "mlt_repair_verification.py")
    result[VERIFIER_TEST_PATH] = read_regular(verification_dir / "test_mlt_repair_verification.py")
    require(read_regular(Path(__file__).absolute()) == result[VERIFIER_PATH],
            "running verifier differs from exported source")
    return result


def validate_binding(receipt: dict, sources: dict[str, bytes], expected_source: str) -> None:
    require(re.fullmatch(r"[0-9a-f]{40}", expected_source), "invalid expected source SHA")
    require(receipt.get("schema") == "motionwright.mlt-observer-source-inputs/1",
            "unsupported source receipt")
    require(receipt.get("source_sha") == expected_source, "source receipt SHA differs")
    require(isinstance(receipt.get("tree"), str)
            and re.fullmatch(r"[0-9a-f]{40}", receipt["tree"]), "invalid source tree")
    digests = receipt.get("sources")
    blobs = receipt.get("source_blob_oids")
    require(isinstance(digests, dict) and set(digests) == set(sources),
            "source digest inventory is not closed")
    require(isinstance(blobs, dict) and set(blobs) == set(sources),
            "source blob inventory is not closed")
    for name, data in sources.items():
        require(digests[name] == sha(data), "source receipt digest differs: " + name)
        require(blobs[name] == git_blob(data), "source receipt blob differs: " + name)


def changes(base: list[bytes], target: list[bytes]) -> list[tuple[int, int, list[bytes]]]:
    result = []
    matcher = difflib.SequenceMatcher(None, base, target, autojunk=False)
    for kind, start, stop, left, right in matcher.get_opcodes():
        if kind != "equal":
            result.append((start, stop, target[left:right]))
    return result


def conflicts(left: tuple, right: tuple) -> bool:
    start, stop, _ = left
    other_start, other_stop, _ = right
    if start == stop:
        return other_start <= start <= other_stop
    if other_start == other_stop:
        return start <= other_start <= stop
    return max(start, other_start) < min(stop, other_stop)


def apply_changes(base: list[bytes], edits: list[tuple]) -> bytes:
    result = base[:]
    for start, stop, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        result[start:stop] = replacement
    return b"".join(result)


def merge_disjoint(original: bytes, repaired: bytes, instrumented: bytes) -> tuple[bytes, dict]:
    """Compose changes in original line coordinates, rejecting ambiguous overlap."""
    base = original.splitlines(keepends=True)
    repair_edits = changes(base, repaired.splitlines(keepends=True))
    observer_edits = changes(base, instrumented.splitlines(keepends=True))
    require(apply_changes(base, repair_edits) == repaired, "repair reconstruction differs")
    require(apply_changes(base, observer_edits) == instrumented, "observer reconstruction differs")
    require(not any(conflicts(a, b) for a in repair_edits for b in observer_edits),
            "repair and observer edits overlap")
    combined = apply_changes(base, repair_edits + observer_edits)

    def describe(edits: list[tuple]) -> list[dict]:
        return [{"start_line_zero_based": start, "stop_line_exclusive": stop,
                 "replacement_lines": len(lines), "replacement_sha256": sha(b"".join(lines))}
                for start, stop, lines in edits]
    return combined, {"repair_edits": describe(repair_edits),
                      "observer_edits": describe(observer_edits), "overlapping_edits": 0}


def compose_pinned(original: bytes, repaired: bytes, instrumented: bytes) -> tuple[bytes, dict]:
    for data, expected, label in (
        (original, ORIGINAL_SHA256, "original"),
        (repaired, REPAIRED_SHA256, "repaired"),
        (instrumented, INSTRUMENTED_SHA256, "instrumented original"),
    ):
        require(sha(data) == expected, label + " consumer digest differs")
    combined, proof = merge_disjoint(original, repaired, instrumented)
    require(sha(combined) == COMBINED_SHA256, "combined consumer digest differs")
    return combined, proof


def load_exact(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, "source module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    require(read_regular(path) == data, "output readback differs")


def receipt_for_outputs(source_receipt: dict, source_receipt_bytes: bytes,
                        sources: dict[str, bytes], proof: dict) -> dict:
    result = {
        "schema": "motionwright.mlt-planar-repair-preparation/2",
        "source_sha": source_receipt["source_sha"],
        "tree": source_receipt["tree"],
        "source_inputs_sha256": sha(source_receipt_bytes),
        "sources": {name: sha(data) for name, data in sorted(sources.items())},
        "source_blob_oids": {name: git_blob(data) for name, data in sorted(sources.items())},
        "outputs": OUTPUT_DIGESTS,
        "composition": proof,
        "source_generators_received_only_original_consumer": True,
        "original_source_unchanged": True,
        "C_layout_checks": "NOT_RUN",
        "legacy_C_negative_checks": "NOT_RUN",
        "legacy_helper_reference": LEGACY_HELPER_REFERENCE,
        "full_consumer_compile": "NOT_RUN",
        "physical_runtime": "NOT_RUN",
    }
    result.update({name: False for name in FALSE_CLAIMS})
    return result


def verify_prepared(output_dir: Path, expected_receipt: dict) -> dict:
    regular_path(output_dir, directory=True)
    require({p.name for p in output_dir.iterdir()}
            == set(OUTPUT_DIGESTS) | {"prepare-receipt.json"}, "prepared output set is not closed")
    receipt = read_json(output_dir / "prepare-receipt.json")
    require(receipt == expected_receipt, "preparation receipt changed")
    require(receipt.get("outputs") == OUTPUT_DIGESTS, "prepared output manifest differs")
    require(all(receipt.get(name) is False for name in FALSE_CLAIMS),
            "source preparation cannot promote native or release claims")
    for name, expected in OUTPUT_DIGESTS.items():
        require(sha(read_regular(output_dir / name)) == expected,
                "prepared source digest differs: " + name)
    return receipt


def prepare(source_dir: Path, verification_dir: Path, source_receipt_path: Path,
            original_path: Path, output_dir: Path, expected_source: str) -> dict:
    require(not output_dir.exists() and not output_dir.is_symlink(),
            "output directory already exists")
    regular_path(output_dir.parent, directory=True)
    require(output_dir.is_absolute(), "output directory must be absolute")
    sources = source_snapshot(source_dir, verification_dir)
    receipt_bytes = read_regular(source_receipt_path, 64 * 1024)
    inputs = read_json(source_receipt_path)
    validate_binding(inputs, sources, expected_source)
    original = read_regular(original_path)
    require(len(original) == 96690 and sha(original) == ORIGINAL_SHA256,
            "original consumer digest or size differs")
    repair = load_exact("mw_pinned_planar_repair", source_dir / "repair_planar_source.py")
    observer = load_exact("mw_pinned_audio_observer", source_dir / "instrument_consumer.py")
    repaired, helper = repair.repair_exact_original(original)
    instrumented = observer.instrument(original)
    combined, proof = compose_pinned(original, repaired, instrumented)
    require(sha(helper) == HELPER_SHA256 and combined.count(helper) == 1,
            "exact tested helper is not present once in combined consumer")
    require(source_snapshot(source_dir, verification_dir) == sources,
            "source files changed while preparing")
    require(read_regular(source_receipt_path, 64 * 1024) == receipt_bytes,
            "source receipt changed while preparing")
    require(read_regular(original_path) == original, "original consumer changed while preparing")
    outputs = {
        "original-consumer.c": original, "repaired-consumer.c": repaired,
        "instrumented-original-consumer.c": instrumented, "consumer_avformat.c": combined,
        "planar-helper-under-test.h": helper,
        "legacy-planar-helper-under-test.h": legacy_planar_helper(),
        "audio_tap.h": sources[DIAGNOSTIC_FOLDER + "/audio_tap.h"],
    }
    prepared_receipt = receipt_for_outputs(inputs, receipt_bytes, sources, proof)
    output_dir.mkdir(exist_ok=False)
    for name, data in outputs.items():
        write_new(output_dir / name, data)
    write_new(output_dir / "prepare-receipt.json",
              (json.dumps(prepared_receipt, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return verify_prepared(output_dir, prepared_receipt)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "verify-prepared"))
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--verification-source-dir", type=Path, required=True)
    parser.add_argument("--source-receipt", type=Path, required=True)
    parser.add_argument("--original-consumer", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    args = parser.parse_args(argv)
    try:
        require(sys.dont_write_bytecode, "run this source verifier with Python -B")
        if args.command == "prepare":
            result = prepare(args.source_dir, args.verification_source_dir, args.source_receipt,
                             args.original_consumer, args.output_dir, args.expected_source)
        else:
            sources = source_snapshot(args.source_dir, args.verification_source_dir)
            raw = read_regular(args.source_receipt, 64 * 1024)
            inputs = read_json(args.source_receipt)
            validate_binding(inputs, sources, args.expected_source)
            original = read_regular(args.original_consumer)
            require(sha(original) == ORIGINAL_SHA256, "original consumer digest differs")
            repaired = read_regular(args.output_dir / "repaired-consumer.c")
            instrumented = read_regular(args.output_dir / "instrumented-original-consumer.c")
            _, proof = compose_pinned(original, repaired, instrumented)
            expected = receipt_for_outputs(inputs, raw, sources, proof)
            result = verify_prepared(args.output_dir, expected)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"source_preparation_complete": False, "error": str(error)}),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
