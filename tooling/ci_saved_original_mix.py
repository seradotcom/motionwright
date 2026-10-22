#!/usr/bin/env python3
"""Execute the selected SavedMix contracts on an explicitly dispatched Linux job.

The consumers use reviewed provider protocol doubles. This gate does not render
MLT, master audio, approve listening, or grant release acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

import ci_storage_apply_recovery as custody

WORKFLOW = ".github/workflows/v05-original-native-mix.yml"
GATE_FILES = (WORKFLOW, "tooling/ci_saved_original_mix.py",
              "tooling/tests/test_ci_saved_original_mix.py",
              "tooling/ci_test_runner.py", "tooling/ci_storage_apply_recovery.py")
TOOLCHAIN = "1.98.1"
MAX_LOG = 16 * 1024 * 1024
MAX_BINARY = 128 * 1024 * 1024
MAX_SOURCE_TOTAL = 256 * 1024 * 1024
MAX_EVIDENCE = 512 * 1024 * 1024
GENERATED_ROOT = "apps/desktop/src-tauri/gen/schemas/"
GENERATED_FILES = frozenset(GENERATED_ROOT + name for name in (
    "acl-manifests.json", "capabilities.json", "linux-schema.json", "desktop-schema.json"))
MAX_GENERATED = 4 * 1024 * 1024
CASE_NAMES = {
    "original_mix": (
        "true_native_music_ducking_is_sample_accurate_and_does_not_change_voice_source",
        "short_voice_releases_from_the_reached_gain_without_a_pcm_discontinuity",
        "short_voice_envelopes_do_not_attenuate_voice_or_sfx_buses",
        "rendered_wav_has_exact_pcm_bytes_and_cannot_claim_an_audio_master",
        "container_asset_sha_and_actual_decoded_pcm_sha_remain_separate_original_identities",
        "identical_inputs_repeat_without_randomness_or_unapproved_resampling",
        "source_digest_or_project_revision_changes_fail_closed",
        "locales_require_explicit_profile_and_correct_voice_take",
        "low_confidence_asr_never_drives_mixer_ducking_automation",
        "no_clip_guard_or_unbounded_normalization_automatically_hides_clipping",
        "overlapping_or_cross_timeline_duck_windows_are_rejected",
        "unknown_fields_cannot_forge_a_live_listening_or_publication_grant",
        "measured_ducking_intervals_reject_a_different_voice_source_sha256",
        "persisted_mix_recipe_rejects_an_unsaved_duck_or_source_override",
    ),
    "saved_original_mix": (
        "saved_recipe_and_real_cas_sources_produce_one_exact_mix_without_a_new_revision",
        "reopened_sqlite_recipe_reproduces_the_bytes_and_a_real_edit_invalidates_the_old_mix",
        "changed_imported_wav_is_rejected_instead_of_becoming_a_new_mix_source",
        "requested_loudness_target_remains_unsupported_without_a_verified_meter",
    ),
    "saved_mix_native_consumers": (
        "single_saved_mix_supplies_exact_cas_pcm_to_mux",
        "multisegment_saved_mix_supplies_exact_cas_pcm_to_mux",
        "single_voice_only_keeps_existing_master_handoff",
        "multisegment_voice_only_keeps_existing_master_handoff",
        "single_saved_mix_rejects_other_valid_wav_before_provider",
        "multisegment_saved_mix_rejects_voice_substitution_before_provider",
        "single_saved_mix_rejects_clock_mismatch_before_provider",
        "multisegment_saved_mix_rejects_clock_mismatch_before_provider",
    ),
    "av_master": (
        "canonical_master_request_identity_is_stable_and_content_bound",
        "strict_rational_voice_alignment_rejects_padding_and_drift",
        "prepared_pcm_reader_retains_exact_bytes_and_never_replaces_a_prior_stage",
        "stages_exact_verified_wav_once_and_rejects_wrong_sha",
        "refuses_corrupted_prior_stage_and_malformed_request_identity",
        "rejects_symlink_substituted_prior_master_stage",
        "rejects_unsupported_pcm_rates_and_ambiguous_wav_chunks",
        "refuses_symlinked_measured_voice_source",
        "refuses_invalid_header_or_unbounded_chunks",
        "real_saved_factory_stages_exact_mix_bytes_idempotently",
        "real_saved_factory_refuses_changed_prior_stage_without_overwrite",
    ),
    "original_mix_native": (
        "shared_master_preparation_returns_exact_saved_factory_mix_without_mutation",
        "no_saved_recipe_preserves_only_supported_voice_path",
        "shared_master_preparation_rejects_real_stale_revision",
        "audition_request_must_equal_current_saved_recipe_before_mixing",
    ),
}
BINARIES = {
    name: {"package": "motionwright-native", "kind": "test", "target": name,
           "manifest": "crates/native/Cargo.toml", "source": "crates/native/tests/" + name + ".rs"}
    for name in ("original_mix", "saved_original_mix", "saved_mix_native_consumers")
}
BINARIES["desktop"] = {
    "package": "motionwright-desktop", "kind": "bin", "target": "motionwright-desktop",
    "manifest": "apps/desktop/src-tauri/Cargo.toml", "source": "apps/desktop/src-tauri/src/main.rs",
}
GROUPS = {
    name: {"binary": name if name in BINARIES else "desktop",
           "prefix": "" if name in BINARIES else name + "::tests::",
           "names": tuple(("" if name in BINARIES else name + "::tests::") + n for n in names)}
    for name, names in CASE_NAMES.items()
}
SHA = re.compile(r"[0-9a-f]{40}\Z")
require = custody.require
VerificationError = custody.VerificationError


def context(environment, expected_source, selection, platform=None):
    platform = sys.platform if platform is None else platform
    require(selection == "saved-mix" and environment.get("SAVED_MIX_SELECTION") == selection,
            "requires explicit saved-mix selection")
    require(platform == "linux" and environment.get("RUNNER_OS") == "Linux",
            "the 41-case roster requires a real Linux runner")
    require(environment.get("GITHUB_ACTIONS") == "true"
            and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
            "selected execution requires explicit GitHub dispatch")
    require(SHA.fullmatch(expected_source) is not None
            and environment.get("GITHUB_SHA") == expected_source
            and environment.get("GITHUB_WORKFLOW_SHA") == expected_source,
            "source, workflow and requested commit differ")
    require(environment.get("GITHUB_JOB") == "saved-mix", "wrong logical job")
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(re.fullmatch(r"[1-9][0-9]*", environment.get(key, "")) is not None,
                "missing run identity")
    repository = environment.get("GITHUB_REPOSITORY", "")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is not None
            and environment.get("GITHUB_WORKFLOW_REF", "").startswith(
                repository + "/" + WORKFLOW + "@"), "wrong workflow identity")
    return {key: environment.get(key, "") for key in (
        "GITHUB_REPOSITORY", "RUNNER_ARCH", "GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "RUNNER_OS", "SAVED_MIX_SELECTION")}


def source_paths(repo, expected_source):
    raw = custody.git(repo, "ls-tree", "-r", "-z", "--full-tree", expected_source)
    names = []
    for row in raw.split(b"\0"):
        if not row:
            continue
        meta, separator, name = row.partition(b"\t")
        require(separator == b"\t", "malformed Git tree entry")
        mode, kind, oid = meta.split()
        require(mode in (b"100644", b"100755") and kind == b"blob"
                and SHA.fullmatch(oid.decode("ascii")) is not None,
                "source tree includes a nonregular or invalid input")
        path = name.decode("utf-8")
        require(path and not Path(path).is_absolute()
                and all(part not in ("", ".", "..", ".git") for part in path.split("/"))
                and "\\" not in path and ":" not in path,
                "unsafe tracked source path")
        names.append(path)
    require(names and len(names) == len(set(names)), "missing or duplicated tracked input")
    require(set(GATE_FILES) | {"Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json",
                              "vendor/glib-0.18.5/Cargo.toml"} <= set(names),
            "missing gate, lock or transitive source input")
    require(sum((repo / name).lstat().st_size for name in names) <= MAX_SOURCE_TOTAL,
            "source snapshot exceeds its byte budget")
    return tuple(sorted(names))


def snapshot(repo, expected_source):
    # Capture every tracked file: Rust modules, include inputs, vendor overrides,
    # build scripts, Tauri configuration, manifests and locks share one tree.
    require(not (repo / "apps/desktop/src-tauri/gen").exists()
            and not (repo / "apps/desktop/src-tauri/gen").is_symlink(),
            "generated Desktop state must be absent before compilation")
    return custody.source_snapshot(repo, expected_source, source_paths(repo, expected_source))


def generated_schemas(repo):
    # Tauri's locked build script writes these four JSON outputs. They are absent
    # at preflight; no other ignored or untracked input is admitted afterward.
    extra = []
    for options in (("--others", "--exclude-standard"), ("--others", "--ignored", "--exclude-standard")):
        extra.extend(part.decode("utf-8") for part in custody.git(repo, "ls-files", "-z", *options).split(b"\0")
                     if part)
    require(len(extra) == len(set(extra)) and set(extra) <= GENERATED_FILES,
            "unexpected untracked or ignored build input")
    files = {}
    for relative in sorted(extra):
        path = repo / relative
        observed = custody.fingerprint(path, repo, MAX_GENERATED)
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == observed["sha256"]
                and custody.fingerprint(path, repo, MAX_GENERATED) == observed,
                "generated schema changed during observation")
        require(isinstance(strict_json(raw), dict), "generated schema is not a JSON object")
        files[relative] = observed
    return files


def source_after(repo, expected_source, before):
    require(custody.git(repo, "rev-parse", "HEAD").decode().strip() == expected_source,
            "checkout SHA changed during execution")
    require(not custody.git(repo, "status", "--porcelain=v1", "--untracked-files=no"),
            "tracked source changed during execution")
    require(set(source_paths(repo, expected_source)) == set(before["files"]),
            "tracked source inventory changed")
    after = {key: value for key, value in before.items() if key != "files"}
    after["files"] = {
        name: {**custody.fingerprint(repo / name, repo, 16 * 1024 * 1024),
               "git_blob_oid": identity["git_blob_oid"]}
        for name, identity in before["files"].items()
    }
    require(after == before, "physical tracked inputs changed during execution")
    return after


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicated JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(
                          VerificationError("nonfinite JSON number")))


def select_binary(raw, exit_code, spec, repo, target):
    require(exit_code == 0, "compilation failed")
    messages = [strict_json(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    require(all(isinstance(item, dict) for item in messages), "invalid compiler message")
    finished = [item for item in messages if item.get("reason") == "build-finished"]
    require(len(finished) == 1 and finished[0].get("success") is True,
            "missing unique successful compiler completion")
    selected = [item for item in messages if item.get("reason") == "compiler-artifact"
                and item.get("executable") and item.get("target", {}).get("name") == spec["target"]
                and item["target"].get("kind") == [spec["kind"]]
                and item.get("profile", {}).get("test") is True]
    require(len(selected) == 1, "missing or ambiguous selected test executable")
    item = selected[0]
    require(item.get("fresh") is False, "selected executable was not compiled in the fresh target")
    require(Path(item["manifest_path"]).absolute() == repo / spec["manifest"]
            and Path(item["target"]["src_path"]).absolute() == repo / spec["source"],
            "compiled artifact belongs to a different source checkout")
    path = Path(item["executable"]).absolute()
    identity = custody.fingerprint(path, target, MAX_BINARY)
    require(identity["bytes"] > 0, "empty selected test executable")
    return {"absolute_path": str(path), **identity}


def listing(raw, code):
    require(code == 0, "test enumeration failed")
    text = raw.decode("utf-8").replace("\r\n", "\n")
    lines = [line for line in text.splitlines() if line]
    if lines and re.fullmatch(r"\d+ tests?, 0 benchmarks?", lines[-1]):
        footer = lines.pop()
        require(int(footer.split()[0]) == len(lines), "enumeration footer count differs")
    require(lines and all(re.fullmatch(r"[A-Za-z0-9_:]+: test", line) for line in lines),
            "enumeration contains unsupported output or benchmarks")
    names = tuple(line[:-6] for line in lines)
    require(len(names) == len(set(names)), "duplicated enumerated test")
    return names


def admit_group(group, enumerated):
    spec = GROUPS[group]
    selected = tuple(name for name in enumerated if name.startswith(spec["prefix"]))
    expected = spec["names"]
    require(len(selected) == len(expected) and set(selected) == set(expected),
            "selected nominal roster has missing or extra cases: " + group)
    return tuple(sorted(selected))


def case_argv(binary, name):
    return [binary["absolute_path"], "--exact", name,
            "--test-threads=1", "--color", "never", "--format", "pretty"]


def admit_case(name, raw, command, binary, after, enumerated, expected_context):
    require(command.get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED"
            and command.get("exit_code") == 0, "failed or UNKNOWN test process")
    require(command.get("log_custody") == "VERIFIED_SNAPSHOT", "missing original log custody")
    require(command.get("argv") == case_argv(binary, name)
            and command.get("context") == expected_context, "test invocation differs")
    require(binary == after and name in enumerated, "test binary changed or case was not enumerated")
    text = raw.decode("utf-8").replace("\r\n", "\n")
    harness_lines = [line for line in text.splitlines()
                     if line.startswith("test ") and not line.startswith("test result: ")]
    require(harness_lines == ["test " + name + " ... ok"], "unexpected case result line")
    results = re.findall(r"(?m)^test ([^\r\n]+) \.\.\. (ok|FAILED|ignored)$", text)
    summaries = re.findall(
        r"(?m)^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; "
        r"(\d+) ignored; (\d+) measured; (\d+) filtered out; finished in [^\r\n]+$", text)
    require(results == [(name, "ok")] and len(summaries) == 1
            and summaries[0][0] == "ok"
            and tuple(map(int, summaries[0][1:])) == (1, 0, 0, 0, len(enumerated) - 1),
            "missing, duplicated, omitted, extra or unsuccessful case result")
    return {"name": name, "status": "PASS", "binary_sha256": binary["sha256"],
            "command": command}


def original_stream(command, stream, output):
    require(command.get("log_custody") == "VERIFIED_SNAPSHOT", "log custody incomplete")
    reference = command.get(stream)
    require(isinstance(reference, dict) and isinstance(reference.get("path"), str),
            "missing original stream reference")
    path = output / reference["path"]
    observed = custody.fingerprint(path, output, MAX_LOG)
    require(observed == reference, "original stream changed")
    with path.open("rb") as stream_handle:
        raw = stream_handle.read(MAX_LOG + 1)
    require(len(raw) <= MAX_LOG and hashlib.sha256(raw).hexdigest() == observed["sha256"]
            and custody.fingerprint(path, output, MAX_LOG) == observed,
            "original stream changed during admission")
    return raw


def validated_environment(environment):
    blocked = {"RUSTC", "RUSTDOC", "RUSTDOCFLAGS", "CARGO_ENCODED_RUSTDOCFLAGS",
               "CC", "CXX", "AR", "CFLAGS", "CXXFLAGS", "LDFLAGS"}
    require(not (blocked & environment.keys()), "inherited tool override")
    require(not any(key.startswith("CARGO_BUILD_") or key.startswith("CARGO_ALIAS_")
                    or (key.startswith("CARGO_TARGET_") and key != "CARGO_TARGET_DIR")
                    or key.startswith("MOTIONWRIGHT_") for key in environment),
            "inherited build or product override")
    return custody.isolated_build_environment(environment)


def build_environment(environment, target, tools):
    env = validated_environment(environment)
    env.update(CARGO_TARGET_DIR=str(target), CARGO_TERM_COLOR="never",
               RUSTC=tools["rustc"]["absolute_path"], CARGO=tools["cargo"]["absolute_path"],
               RUSTUP_TOOLCHAIN=TOOLCHAIN)
    env["PATH"] = str(Path(tools["cargo"]["absolute_path"]).parent) + os.pathsep + env.get("PATH", "")
    return env


def new_directory(path, repo):
    require(path.is_absolute() and ".." not in path.parts and not path.exists() and not path.is_symlink()
            and not path.resolve().is_relative_to(repo), "output must be new outside source")
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400, "linked output parent")
    path.mkdir()
    return path


class Execution:
    def __init__(self, repo, output, environment, execution_context):
        self.repo, self.output, self.env, self.context = repo, output, environment, execution_context
        self.commands = []
        self.originals = []
        self.unknown = False
        self.deadline = time.monotonic() + 75 * 60

    def run(self, argv, label, timeout):
        require(not self.unknown, "prior UNKNOWN forbids further commands")
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "selected job execution budget exhausted")
        try:
            command = custody.execute(argv, self.repo, self.env, self.output, label,
                                      min(timeout, remaining), self.context)
        except custody.UnknownObservation as error:
            self.unknown = True
            self.commands.append(error.command)
            raise VerificationError("command observation was not retained; no retry") from error
        self.commands.append(command)
        if custody.execution_unknown(command):
            self.unknown = True
            raise VerificationError("uncertain command; no further commands: " + label)
        require(command.get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED"
                and command.get("exit_code") == 0, "command failed or became UNKNOWN: " + label)
        try:
            original = custody.fingerprint(self.output / (label + ".command.json"), self.output, MAX_LOG)
            require(strict_json(original_stream(
                {"log_custody": "VERIFIED_SNAPSHOT", "metadata": original}, "metadata", self.output)) == command,
                "command metadata differs from original process observation")
            self.originals.append(original)
            stdout = original_stream(command, "stdout", self.output)
            original_stream(command, "stderr", self.output)
        except BaseException as error:
            self.unknown = True
            self.commands[-1] = {**command, "original_command": command,
                "observation": "UNKNOWN_CUSTODY_NO_RETRY",
                "uncertainty_reason": "COMMAND_OR_STREAM_READBACK_FAILED",
                "diagnostic_custody": "UNSEALED_COMMAND_OR_STREAM_READBACK",
                "secondary_observation_errors": [type(error).__name__ + ": " + str(error)]}
            raise VerificationError("command custody readback failed; no further commands: " + label) from error
        return command, stdout


def capture_tools(execution):
    rustup = shutil.which("rustup", path=execution.env.get("PATH"))
    require(rustup is not None, "rustup is unavailable")
    rustup = Path(rustup).resolve(strict=True)
    tools = {"rustup": {"absolute_path": str(rustup),
                        **custody.fingerprint(rustup, rustup.parent, MAX_BINARY)}}
    for name in ("cargo", "rustc", "cargo-clippy", "clippy-driver"):
        _, raw = execution.run([str(rustup), "which", "--toolchain", TOOLCHAIN, name],
                               "tool-" + name, 30)
        lines = raw.decode("utf-8").splitlines()
        require(len(lines) == 1 and Path(lines[0]).is_absolute(), "ambiguous tool path")
        path = Path(lines[0])
        require(path.resolve(strict=True) == path, "compiler tool path is not canonical")
        identity = custody.fingerprint(path, path.parent, MAX_BINARY)
        require(identity["bytes"] > 0, "empty compiler tool")
        tools[name] = {"absolute_path": str(path), **identity}
    _, version = execution.run([tools["rustc"]["absolute_path"], "-vV"], "rustc-version", 30)
    require(re.search(r"(?m)^release: " + re.escape(TOOLCHAIN) + r"$", version.decode()) is not None,
            "compiler version differs from workflow")
    execution.run([tools["cargo"]["absolute_path"], "-Vv"], "cargo-version", 30)
    execution.run([tools["clippy-driver"]["absolute_path"], "--version"], "clippy-version", 30)
    return tools


def retain_binary(binary, output, label):
    source = Path(binary["absolute_path"])
    destination = output / (label + ".test-executable")
    with source.open("rb") as reader, destination.open("xb") as writer:
        size = 0
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            size += len(chunk)
            require(size <= MAX_BINARY, "compiled executable grew during retention")
            writer.write(chunk)
    retained = custody.fingerprint(destination, output, MAX_BINARY)
    require(retained["sha256"] == binary["sha256"] and retained["bytes"] == binary["bytes"],
            "retained executable differs from compiled source")
    return retained


def run(repo, expected_source, output, target, selection):
    repo = repo.resolve(strict=True)
    new_directory(output, repo)
    receipt = {
        "schema": "motionwright.saved-original-mix-selected-execution/1",
        "status": "FAIL", "started_utc": custody.now(), "expected_source": expected_source,
        "scope": "41 selected Rust/SQLite/CAS source contracts; provider consumers use protocol doubles",
        "not_evidence_of": ["physical MLT output", "audio mastering", "listening approval",
                            "installed Desktop UI", "release or contractual acceptance"],
        "expected_counts": {name: len(group["names"]) for name, group in GROUPS.items()},
        "cases": [{"group": group, "name": name, "status": "NOT_RUN"}
                  for group, spec in GROUPS.items() for name in sorted(spec["names"])],
        "commands": [], "binaries": {},
        "formal_acceptance": False, "human_acceptance": False, "release_authorized": False,
    }
    execution = None
    source_before = None
    tools = {}
    compiled = {}
    try:
        ctx = context(os.environ, expected_source, selection)
        receipt["context"] = ctx
        require(Path(os.environ.get("GITHUB_WORKSPACE", "")).resolve(strict=True) == repo,
                "checkout differs from the dispatched workspace")
        environment = validated_environment(dict(os.environ))
        source_before = snapshot(repo, expected_source)
        receipt["source_before"] = source_before
        require(not target.is_relative_to(output) and not output.is_relative_to(target),
                "build and evidence roots must be separate")
        new_directory(target, repo)
        execution = Execution(repo, output, environment, ctx)
        tools = capture_tools(execution)
        receipt["tools"] = tools
        execution.env = build_environment(execution.env, target, tools)
        cargo = tools["cargo"]["absolute_path"]
        receipt["build_environment"] = {key: execution.env.get(key, "") for key in (
            "CARGO_TARGET_DIR", "CARGO_INCREMENTAL", "CARGO_PROFILE_TEST_DEBUG",
            "CARGO_PROFILE_DEV_DEBUG", "RUSTC", "CARGO", "RUSTUP_TOOLCHAIN")}
        receipt["generated_schema_observations"] = []
        for label, spec in BINARIES.items():
            argv = [cargo, "test", "--locked", "-p", spec["package"], "--" + spec["kind"],
                    spec["target"], "--no-run", "--message-format=json"]
            command, raw = execution.run(argv, "compile-" + label, 1800)
            binary = select_binary(raw, command["exit_code"], spec, repo, target)
            retained = retain_binary(binary, output, label)
            compiled[label] = binary
            receipt["binaries"][label] = {"compiled": binary, "retained": retained}
            receipt["generated_schema_observations"].append(
                {"phase": "compile-" + label, "files": generated_schemas(repo)})
        execution.run([cargo, "check", "--locked", "-p", "motionwright-desktop"],
                      "desktop-check", 1200)
        receipt["generated_schema_observations"].append(
            {"phase": "desktop-check", "files": generated_schemas(repo)})
        execution.run([cargo, "clippy", "--locked", "-p", "motionwright-native",
                       "-p", "motionwright-service", "-p", "motionwright-desktop",
                       "--all-targets", "--", "-D", "warnings"], "strict-clippy", 1800)
        generated = generated_schemas(repo)
        require(set(generated) == GENERATED_FILES, "incomplete generated Desktop schema inventory")
        receipt["generated_schema_observations"].append({"phase": "strict-clippy", "files": generated})
        rosters = {}
        for label, binary in compiled.items():
            command, raw = execution.run([binary["absolute_path"], "--list", "--format", "terse"],
                                         "list-" + label, 60)
            rosters[label] = listing(raw, command["exit_code"])
        receipt["enumerated_binaries"] = rosters
        receipt["selected_rosters"] = {
            group: admit_group(group, rosters[spec["binary"]]) for group, spec in GROUPS.items()
        }
        require(sum(map(len, receipt["selected_rosters"].values())) == 41,
                "selected roster does not contain exactly 41 cases")
        for index, case in enumerate(receipt["cases"]):
            binary = compiled[GROUPS[case["group"]]["binary"]]
            before = {"absolute_path": binary["absolute_path"],
                      **custody.fingerprint(Path(binary["absolute_path"]), target, MAX_BINARY)}
            require(before == binary, "executable changed before invocation")
            case["status"] = "STARTED"
            previous_commands = len(execution.commands)
            try:
                command, raw = execution.run(case_argv(binary, case["name"]),
                                             "case-" + str(index).zfill(2), 120)
                after = {"absolute_path": binary["absolute_path"],
                         **custody.fingerprint(Path(binary["absolute_path"]), target, MAX_BINARY)}
                result = admit_case(case["name"], raw, command, binary, after,
                                    rosters[GROUPS[case["group"]]["binary"]], ctx)
                case.update(result)
            except (VerificationError, ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
                case["status"] = "UNKNOWN" if execution.unknown else "FAIL"
                if len(execution.commands) > previous_commands:
                    case["command"] = execution.commands[-1]
                    if case["command"].get("observation") == "NOT_STARTED_LAUNCH_ERROR":
                        case["status"] = "NOT_STARTED"
                raise
        receipt["status"] = "PASS_SELECTED_SAVED_MIX_41"
    except (VerificationError, ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        receipt["error"] = type(error).__name__ + ": " + str(error)
        receipt["status"] = "UNKNOWN" if execution is not None and execution.unknown else "FAIL"
    finally:
        if execution is not None:
            receipt["commands"] = execution.commands
            receipt["command_originals"] = execution.originals
        if execution is not None and execution.unknown:
            receipt["postcheck"] = "NOT_RUN_UNKNOWN_PROCESS_OR_CUSTODY"
        if source_before is not None and not (execution is not None and execution.unknown):
            try:
                receipt["source_after"] = source_after(repo, expected_source, source_before)
                generated = generated_schemas(repo)
                receipt["generated_schemas_after"] = generated
                if receipt["status"] == "PASS_SELECTED_SAVED_MIX_41":
                    require(generated == receipt["generated_schema_observations"][-1]["files"],
                            "generated Desktop schemas changed during tests")
                for index, (name, identity) in enumerate(generated.items()):
                    destination = output / ("generated-schema-" + str(index) + ".json")
                    with destination.open("xb") as stream:
                        stream.write((repo / name).read_bytes())
                    retained = custody.fingerprint(destination, output, MAX_GENERATED)
                    require(retained["sha256"] == identity["sha256"]
                            and retained["bytes"] == identity["bytes"],
                            "retained generated schema differs")
                    receipt.setdefault("retained_generated_schemas", {})[name] = retained
                for name, identity in tools.items():
                    path = Path(identity["absolute_path"])
                    current = {"absolute_path": str(path),
                               **custody.fingerprint(path, path.parent, MAX_BINARY)}
                    require(current == identity, "compiler tool changed: " + name)
                require(execution is None or len(execution.originals) == len(receipt["commands"]),
                        "command original inventory is incomplete")
                for index, command in enumerate(receipt["commands"]):
                    original = execution.originals[index]
                    require(strict_json(original_stream(
                        {"log_custody": "VERIFIED_SNAPSHOT", "metadata": original}, "metadata", output)) == command,
                        "command metadata original changed")
                    original_stream(command, "stdout", output)
                    original_stream(command, "stderr", output)
                for info in receipt["binaries"].values():
                    binary = info["compiled"]
                    require({"absolute_path": binary["absolute_path"],
                             **custody.fingerprint(Path(binary["absolute_path"]), target, MAX_BINARY)} == binary,
                            "compiled test executable changed")
                    retained = info["retained"]
                    require(custody.fingerprint(output / retained["path"], output, MAX_BINARY) == retained,
                            "retained test executable changed")
                inventory = {path.name: custody.fingerprint(path, output, MAX_BINARY)
                             for path in output.iterdir()}
                require(sum(item["bytes"] for item in inventory.values()) <= MAX_EVIDENCE,
                        "evidence exceeds retained byte budget")
                receipt["artifacts_before_receipt"] = inventory
            except (VerificationError, ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
                receipt["postcheck_error"] = type(error).__name__ + ": " + str(error)
                if receipt["status"] != "UNKNOWN":
                    receipt["status"] = "FAIL"
        receipt["finished_utc"] = custody.now()
        receipt["passed"] = sum(case["status"] == "PASS" for case in receipt["cases"])
        receipt["not_run"] = sum(case["status"] == "NOT_RUN" for case in receipt["cases"])
        receipt["failed"] = sum(case["status"] == "FAIL" for case in receipt["cases"])
        receipt["unknown"] = sum(case["status"] == "UNKNOWN" for case in receipt["cases"])
        custody.write_new(output / "receipt.json", receipt)
    return (0 if receipt["status"] == "PASS_SELECTED_SAVED_MIX_41" else 2), receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--selection", choices=("saved-mix",), required=True)
    args = parser.parse_args(argv)
    try:
        code, receipt = run(args.repo, args.expected_source, args.output, args.target, args.selection)
        print(json.dumps({"status": receipt["status"], "passed": receipt["passed"],
                          "not_run": receipt["not_run"]}, sort_keys=True))
        return code
    except (VerificationError, ValueError, OSError, subprocess.SubprocessError) as error:
        print("SavedMix gate refused: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
