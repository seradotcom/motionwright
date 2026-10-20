"""Read-only witnesses around the canonical single MLT assembly.

The caller owns the sole native dispatch. This module never starts a renderer,
installs a module, retries an operation, or grants media or human acceptance.
Its private receipts may contain owner paths and must not be published wholesale.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import threading
import zipfile

from cross_app_public_artifact import strict_object
from native_av_failure_guard import NativeCommandFailure
import native_mlt_loaded_module as loaded
import native_mlt_runtime_admission as admission

SCHEMA = "motionwright.native.mlt-g2-inputs/1"
TERMINAL_SCOPE = "canonical_assemble_command"
OFFICIAL_SCHEMA = "motionwright.mlt.official-runtime-intake/1"
REFERENCE_ZIP_SHA256 = "35ed8f9d2c840dd0d8702eaaf23454da87e49bdee2dd12f190b7be634806b27a"
REFERENCE_WAV_SHA256 = "66aeaa3aa6fff53834ab4003671ce79068bcbd6f631609f0f988b3ac9744ebe9"
REFERENCE_MANIFEST_SHA256 = "ca02511c2e7028a666d8c54f1494af096e19a47640d3cffe40865bfa7049f4b8"
REFERENCE_CHECKOUT = "a31505e3c0884a419b0b3b1072af0ba4057f9442"
REFERENCE_PR_HEAD = "30f02fd0b744cda36035a4473c98e066f89d3d00"
REPAIRED_MODULE_SHA256 = "c97df9c4b1d33dd6ea0ad5745501a70734f883709480622b680b8fcd512f7404"
COMPILER_RECEIPT_SHA256 = "0ec0d1a8fd000c3abd32f3b50d94fb20708b6d61327b216727b7a4551b202f75"
SEMWRIGHT_PIN = "54d3459a051adbce4cae9f60c0dea8f60c824be6"
MELT = Path("/usr/bin/melt-7")
MELT_ALIAS = Path("/usr/bin/melt")
MELT_ARCHIVE_ALIAS = {"archive_path": "usr/bin/melt",
                      "linkname": "melt-7", "target_path": "usr/bin/melt-7"}
INPUT_KEYS = {
    "schema", "motionwright_sha", "semwright_sha", "run_id", "run_attempt",
    "original_artifact_zip", "private_evidence_dir",
    "known_original_module_sha256", "known_melt_sha256",
    "official_runtime_receipt_path", "official_runtime_receipt_sha256",
    "original_c_source", "repaired_c_source", "compiled_module",
    "compiled_module_sha256", "compiler_receipt_path", "compiler_receipt_sha256",
    "installation_receipt_path", "installation_receipt_sha256",
}


class G2Unverified(AssertionError):
    """The native result is retained separately from a failed evidence gate."""


def require(ok: object, code: str) -> None:
    if not ok:
        raise G2Unverified(code)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_bytes(path: Path, data: bytes) -> None:
    """Exclusive private custody; a partial write is never a successful receipt."""
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    require(path.read_bytes() == data, "g2_private_custody_readback_changed")


def new_json(path: Path, value: object) -> None:
    new_bytes(path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def warn_secondary() -> None:
    try:
        os.write(2, b"MLT G2 secondary evidence failure; native outcome retained.\n")
    except OSError:
        pass


def reference_source(raw: bytes) -> tuple[bytes, dict]:
    require(sha(raw) == REFERENCE_ZIP_SHA256, "g2_original_artifact_zip_changed")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        expected = {
            "native-master-source.wav", "native-multisegment-master.mp4",
            "native-two-segment-video-only.mkv", "public-evidence.json",
        }
        require(len(entries) == 4 and {item.filename for item in entries} == expected,
                "g2_original_artifact_members_changed")
        require(all(not item.is_dir() and not stat.S_ISLNK(item.external_attr >> 16)
                    and 0 < item.file_size <= 512_000 for item in entries)
                and sum(item.file_size for item in entries) <= 1_000_000,
                "g2_original_artifact_member_budget")
        require(archive.testzip() is None, "g2_original_artifact_crc")
        document = archive.read("public-evidence.json")
        require(sha(document) == REFERENCE_MANIFEST_SHA256,
                "g2_original_public_manifest_changed")
        proof = strict_object(document)
        require(proof.get("motionwright_source_sha") == REFERENCE_CHECKOUT
                and proof.get("semwright_source_pin") == SEMWRIGHT_PIN
                and proof.get("mode") == "final-mp4",
                "g2_original_public_manifest_identity")
        wav = archive.read("native-master-source.wav")
        require(len(wav) == 211_244 and sha(wav) == REFERENCE_WAV_SHA256,
                "g2_original_wav_changed")
    return wav, {
        "workflow_run_id": 39054798740, "run_attempt": 1,
        "workflow_event": "pull_request", "workflow_head_sha": REFERENCE_PR_HEAD,
        "producer_checkout_sha": REFERENCE_CHECKOUT, "artifact_id": 12109323512,
        "artifact_zip_sha256": REFERENCE_ZIP_SHA256,
        "public_manifest_sha256": REFERENCE_MANIFEST_SHA256,
        "original_wav_sha256": REFERENCE_WAV_SHA256,
        "source_is_synthetic_ci_fixture": True,
        "historical_right_channel_failure_superseded": False,
    }


def verify_official_runtime_bindings(official: dict, original_sha: str, melt_sha: str) -> None:
    """Match admitted receipt bytes; this does not independently authenticate them."""
    require(type(official) is dict and official.get("schema") == OFFICIAL_SCHEMA
            and official.get("runtime_bindings") == {
                "original_module": {"installed_path": admission.INSTALLED.as_posix(),
                                    "sha256": original_sha},
                "melt": {"installed_path": MELT.as_posix(), "sha256": melt_sha},
            }, "g2_official_runtime_binary_bindings")
    members = official.get("members")
    require(type(members) is dict and set(members) == {"libmlt7", "melt"},
            "g2_official_runtime_member_bindings")
    for name, path, digest in (("libmlt7", admission.INSTALLED, original_sha),
                               ("melt", MELT, melt_sha)):
        row = members[name]
        require(type(row) is dict and row.get("installed_path") == path.as_posix()
                and row.get("sha256") == digest and row.get("member_type") == "regular"
                and row.get("architecture_header") == "ELF64-LSB-x86_64",
                "g2_official_runtime_member_bindings")
    require(members["libmlt7"].get("archive_alias") is None
            and members["melt"].get("archive_alias") == MELT_ARCHIVE_ALIAS,
            "g2_official_runtime_alias_binding")


def _alias_identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def verified_installed_melt_alias(expected_sha: str) -> dict:
    """Observe the one fixed alias without hashing or executing through it."""
    try:
        before = MELT_ALIAS.lstat()
        require(stat.S_ISLNK(before.st_mode), "g2_installed_melt_alias_not_symlink")
        target = os.readlink(MELT_ALIAS)
    except OSError as error:
        raise G2Unverified("g2_installed_melt_alias_unreadable") from error
    require(target == MELT_ARCHIVE_ALIAS["linkname"], "g2_installed_melt_alias_target")
    # Keep the existing regular-file, same-descriptor binary verifier strict.
    digest = loaded.sha256(MELT, expected_sha, loaded.MAX_MELT)
    try:
        after = MELT_ALIAS.lstat()
        target_after = os.readlink(MELT_ALIAS)
    except OSError as error:
        raise G2Unverified("g2_installed_melt_alias_changed") from error
    require(_alias_identity(before) == _alias_identity(after) and target_after == target,
            "g2_installed_melt_alias_changed")
    return {"installed_alias_path": MELT_ALIAS.as_posix(), "linkname": target,
            "canonical_installed_path": MELT.as_posix(), "sha256": digest,
            "alias_identity": list(_alias_identity(after))}


def process_identity(pid: int) -> tuple[int, int]:
    with (Path("/proc") / str(pid) / "stat").open("rb") as stream:
        raw = stream.read(8193)
    start = loaded.parse_process_starttime(raw)
    tail = raw.decode("ascii").rsplit(")", 1)[1].split()
    return int(tail[1]), start


def descendant_of(pid: int, ancestor: tuple[int, int]) -> bool:
    visited = set()
    for _ in range(64):
        if pid in visited or pid <= 1:
            return False
        visited.add(pid)
        parent, start = process_identity(pid)
        if pid == ancestor[0]:
            return start == ancestor[1]
        pid = parent
    return False


def selected_consumer(payload: bytes, output_root: Path) -> Path | None:
    if not (0 < len(payload) <= loaded.MAX_CMDLINE and payload.endswith(b"\0")):
        return None
    argv = payload[:-1].decode("utf-8").split("\0")
    consumers = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "-consumer"]
    if len(consumers) != 1 or not consumers[0].startswith("avformat:"):
        return None
    output = Path(consumers[0][len("avformat:"):])
    if output.suffix != ".mp4" or not output.is_absolute():
        return None
    if output_root not in output.parents:
        return None
    require(output.resolve() == output and ".." not in output.parts
            and not any(parent.is_symlink() for parent in output.parents),
            "g2_consumer_output_not_canonical")
    loaded.verified_melt_cmdline(payload, str(output))
    return output


class LiveObserver:
    """Observe only; a missed or ambiguous original PID cannot cause a replay."""
    def __init__(self, daemon_pid: int, output_root: Path, melt_sha: str):
        self.ancestor = (daemon_pid, process_identity(daemon_pid)[1])
        self.output_root = output_root.resolve(strict=True)
        self.melt_sha = melt_sha
        self.candidates: set[tuple[int, int]] = set()
        self.receipts: dict[tuple[int, int], dict] = {}
        self.outputs: dict[tuple[int, int], str] = {}
        self.observation_failures = 0
        self.fatal = False
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def scan(self) -> None:
        pids = [int(path.name) for path in Path("/proc").iterdir() if path.name.isdecimal()]
        require(len(pids) <= 8192, "g2_process_inventory_budget")
        for pid in pids:
            proc = Path("/proc") / str(pid)
            try:
                if os.readlink(proc / "exe") != str(MELT.resolve(strict=True)):
                    continue
                if proc.stat().st_uid != os.getuid() or not descendant_of(pid, self.ancestor):
                    continue
                before = process_identity(pid)[1]
                with (proc / "cmdline").open("rb") as stream:
                    output = selected_consumer(stream.read(loaded.MAX_CMDLINE + 1), self.output_root)
                if output is None:
                    continue
                identity = (pid, before)
                self.candidates.add(identity)
                self.outputs[identity] = str(output)
                require(len(self.candidates) <= 1, "g2_multiple_original_av_consumers")
                if identity in self.receipts:
                    continue
                proof = loaded.inspect_live_original(
                    pid, MELT, self.melt_sha, admission.INSTALLED,
                    REPAIRED_MODULE_SHA256, str(output),
                )
                require(process_identity(pid)[1] == before
                        and descendant_of(pid, self.ancestor),
                        "g2_native_process_identity_changed")
                self.receipts[identity] = {
                    "pid": pid, "process_start_ticks": before,
                    "daemon_pid": self.ancestor[0],
                    "daemon_start_ticks": self.ancestor[1],
                    "output_path": str(output), "live_module": proof,
                }
            except G2Unverified:
                self.fatal = True
                self.observation_failures += 1
            except (OSError, ValueError, loaded.LoadedModuleUnverified):
                # Re-observing the same live process is read-only. A process
                # that ends before a valid witness remains unverified.
                self.observation_failures += 1

    def start(self) -> None:
        require(self.thread is None, "g2_observer_already_started")
        self.scan()
        require(not self.candidates and not self.fatal,
                "g2_consumer_preexists_original_assemble")

        def watch() -> None:
            try:
                while not self.stop_event.is_set():
                    self.scan()
                    self.stop_event.wait(0.02)
            except Exception:
                self.fatal = True
                self.observation_failures += 1

        self.thread = threading.Thread(target=watch, name="mlt-original-observer", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
            require(not self.thread.is_alive(), "g2_observer_did_not_finish")

    def require_one(self, master: Path) -> dict:
        require(not self.fatal and len(self.candidates) == 1 and len(self.receipts) == 1,
                "g2_original_live_module_not_uniquely_witnessed")
        proof = next(iter(self.receipts.values()))
        require(proof["output_path"] == str(master.resolve(strict=True)),
                "g2_observed_consumer_is_not_native_result_artifact")
        return proof


class G2Session:
    def __init__(self, inputs: dict, evidence_dir: Path, wav: bytes, provenance: dict):
        self.inputs = inputs
        self.evidence_dir = evidence_dir
        self.original_wav = wav
        self.provenance = provenance
        self.native_result: dict | None = None
        self.native_error: BaseException | None = None
        self.native_traceback = None
        self.secondary_failures: list[str] = []
        self.observer: LiveObserver | None = None
        self.live_proof: dict | None = None
        self.pcm_proof: dict | None = None
        self.master_sha: str | None = None
        self.packet_observation: dict | None = None
        self.completed = False
        self.assembled_once = False
        self.melt_alias_observation: dict | None = None

    @classmethod
    def prepare(cls, path: Path, *, source_sha: str, semwright_sha: str, mode: str):
        require(mode == "final-mp4", "g2_requires_single_final_mp4_mode")
        runner = admission.disposable_linux_runner()
        data = strict_object(admission.checked_bytes(path, 64 * 1024))
        require(set(data) == INPUT_KEYS and data["schema"] == SCHEMA, "g2_inputs_schema")
        require(re.fullmatch(r"[0-9a-f]{40}", source_sha) is not None
                and data["motionwright_sha"] == source_sha
                and data["semwright_sha"] == semwright_sha == SEMWRIGHT_PIN
                and data["run_id"] == os.environ.get("GITHUB_RUN_ID")
                and data["run_attempt"] == os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
                and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
                and type(data["run_id"]) is str and data["run_id"].isdecimal()
                and type(data["run_attempt"]) is str and data["run_attempt"].isdecimal(),
                "g2_current_execution_binding")
        for key in INPUT_KEYS:
            if key.endswith("_sha256"):
                require(type(data[key]) is str and re.fullmatch(r"[0-9a-f]{64}", data[key]),
                        "g2_required_digest_invalid")
        require(data["compiled_module_sha256"] == REPAIRED_MODULE_SHA256
                and data["compiler_receipt_sha256"] == COMPILER_RECEIPT_SHA256,
                "g2_repair_only_original_G1_binary_changed")
        official_bytes = admission.checked_bytes(Path(data["official_runtime_receipt_path"]), 512 * 1024)
        require(sha(official_bytes) == data["official_runtime_receipt_sha256"],
                "g2_official_runtime_receipt_changed")
        official = strict_object(official_bytes)
        verify_official_runtime_bindings(
            official, data["known_original_module_sha256"], data["known_melt_sha256"])
        closure = admission.source_compiler_closure(
            Path(data["original_c_source"]), Path(data["repaired_c_source"]),
            Path(data["compiled_module"]), data["compiled_module_sha256"],
            Path(data["compiler_receipt_path"]), data["compiler_receipt_sha256"],
        )
        installed_bytes = admission.checked_bytes(Path(data["installation_receipt_path"]), 64 * 1024)
        require(sha(installed_bytes) == data["installation_receipt_sha256"],
                "g2_installation_receipt_changed")
        installed = strict_object(installed_bytes)
        require(installed.get("schema") == "motionwright.native.mlt-module-installed/1"
                and installed.get("installed_path") == str(admission.INSTALLED)
                and installed.get("original_module_sha256") == data["known_original_module_sha256"]
                and installed.get("original_backup_sha256") == data["known_original_module_sha256"]
                and installed.get("actual_installed_module_sha256") == REPAIRED_MODULE_SHA256
                and installed.get("module_replaced") is True
                and installed.get("new_native_master_issued") is False
                and all(installed.get(key) == value for key, value in closure.items()),
                "g2_installation_source_closure")
        require(sha(admission.checked_bytes(admission.INSTALLED, admission.MAX_ELF_BYTES))
                == REPAIRED_MODULE_SHA256, "g2_installed_repaired_module_changed")
        alias_observation = verified_installed_melt_alias(data["known_melt_sha256"])
        require(not admission.live_melt_pids(), "g2_melt_exists_before_original_assembly")
        wav, provenance = reference_source(
            admission.checked_bytes(Path(data["original_artifact_zip"]), 32 * 1024 * 1024)
        )
        private = Path(data["private_evidence_dir"])
        require(private.is_absolute() and private.parent == runner
                and not private.exists() and not private.is_symlink(),
                "g2_private_directory_must_be_fresh_runner_child")
        private.mkdir(mode=0o700)
        session = cls(data, private, wav, provenance)
        session.melt_alias_observation = alias_observation
        new_bytes(private / "original-source.wav", wav)
        new_json(private / "input-custody.json", {
            "schema": "motionwright.native.mlt-g2-input-custody/1",
            "current_motionwright_sha": source_sha, "semwright_sha": semwright_sha,
            "run_id": data["run_id"], "run_attempt": data["run_attempt"],
            "original_source": provenance, "source_compiler": closure,
            "official_runtime_receipt_sha256": data["official_runtime_receipt_sha256"],
            "installation_receipt_sha256": data["installation_receipt_sha256"],
            "known_original_module_sha256": data["known_original_module_sha256"],
            "known_melt_sha256": data["known_melt_sha256"],
            "installed_melt_alias": alias_observation,
            "native_effect_started": False,
        })
        return session

    def seed_environment(self, env: dict) -> dict:
        return {
            **env,
            "MOTIONWRIGHT_MLT_ORIGINAL_WAV": str(self.evidence_dir / "original-source.wav"),
            "MOTIONWRIGHT_MLT_ORIGINAL_WAV_SHA256": REFERENCE_WAV_SHA256,
        }

    def secondary(self, stage: str) -> None:
        self.secondary_failures.append(stage)
        warn_secondary()

    def run_original(self, run, daemon_pid: int, output_root: Path) -> dict:
        require(not self.assembled_once, "g2_original_assemble_may_only_be_invoked_once")
        self.assembled_once = True
        self.observer = LiveObserver(daemon_pid, output_root, self.inputs["known_melt_sha256"])
        self.observer.start()
        try:
            result = run()
        except BaseException as error:
            self.native_error, self.native_traceback = error, error.__traceback__
            try:
                self.observer.stop()
            except Exception:
                self.secondary("observer_stop_after_native_error")
            try:
                new_json(self.evidence_dir / "native-terminal.json", {
                    "terminal_scope": TERMINAL_SCOPE,
                    "native_terminal": "ERROR_OR_UNKNOWN",
                    "exception_type": type(error).__name__,
                    "canonical_failure_diagnostic": (
                        error.diagnostic if isinstance(error, NativeCommandFailure) else None
                    ),
                    "native_operation_retried": False,
                })
            except Exception:
                self.secondary("native_error_custody")
            try:
                if len(self.observer.outputs) == 1:
                    output = Path(next(iter(self.observer.outputs.values())))
                    if output.is_file():
                        packed = admission.checked_bytes(output, 256_000_000)
                        new_bytes(self.evidence_dir / "unconfirmed-native-output.mp4", packed)
                        new_json(self.evidence_dir / "unconfirmed-output.json", {
                            "sha256": sha(packed), "bytes": len(packed),
                            "native_terminal": "ERROR_OR_UNKNOWN", "artifact_accepted": False,
                        })
            except Exception:
                self.secondary("unconfirmed_native_output_custody")
            raise
        # Keep the exact parsed assemble response before every later observation,
        # file read, decode, or receipt write. The command may have failed after
        # an internal RPC succeeded; an absent response never proves its terminal.
        self.native_result = result
        try:
            new_json(self.evidence_dir / "native-terminal.json", {
                "terminal_scope": TERMINAL_SCOPE,
                "native_terminal": ("SUCCESS_REPORTED" if result.get("native_mlt_multisegment_e2e") == "PASS"
                                    else "RESULT_REPORTED_UNVERIFIED"),
                "native_result": result, "native_operation_retried": False,
            })
        except Exception:
            self.secondary("native_success_custody")
        try:
            self.observer.stop()
        except Exception:
            self.secondary("observer_stop_after_native_success")
        try:
            relative = result.get("master_artifact_path")
            require(type(relative) is str and relative.endswith(".mp4")
                    and not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                    "g2_reported_master_path_invalid")
            candidate = output_root / relative
            require(output_root.resolve(strict=True) in candidate.resolve(strict=True).parents,
                    "g2_reported_master_outside_owner_root")
            packed = admission.checked_bytes(candidate, 256_000_000)
            require(sha(packed) == result.get("master_artifact_sha256")
                    and len(packed) == result.get("master_artifact_bytes"),
                    "g2_reported_master_bytes_changed")
            new_bytes(self.evidence_dir / "original-native-master.mp4", packed)
            self.master_sha = sha(packed)
        except Exception:
            self.secondary("reported_native_master_custody")
        return result

    def retain_master(self, master: Path, expected_sha: str, expected_size: int) -> None:
        require(self.native_result is not None, "g2_master_without_native_terminal")
        packed = admission.checked_bytes(master, 256_000_000)
        require(sha(packed) == expected_sha and len(packed) == expected_size,
                "g2_native_master_bytes_changed")
        require(self.master_sha == expected_sha
                and admission.checked_bytes(self.evidence_dir / "original-native-master.mp4", 256_000_000) == packed,
                "g2_retained_master_differs_from_native_result")
        try:
            require(self.observer is not None, "g2_original_observer_missing")
            self.live_proof = self.observer.require_one(master)
            new_json(self.evidence_dir / "original-live-module.json", self.live_proof)
        except Exception:
            # Keep decoding this one original artifact even if a live witness
            # was missed. Completion still refuses an incomplete evidence chain.
            self.secondary("original_live_module_witness")

    def retain_packet_document(self, raw: bytes) -> None:
        require(0 < len(raw) <= 1_048_576, "g2_packet_document_budget")
        new_bytes(self.evidence_dir / "original-master-packets.json", raw)
        observed = strict_object(raw)
        require(type(observed.get("packets")) is list
                and 0 < len(observed["packets"]) <= 128
                and type(observed.get("streams")) is list
                and len(observed["streams"]) == 1,
                "g2_packet_document_shape")
        self.packet_observation = {
            "ffprobe_document_sha256": sha(raw),
            "packet_count": len(observed["packets"]),
            "packet_PTS_and_AAC_priming_verified": False,
        }

    def inspect_pcm(self, pcm: bytes) -> dict:
        # Import after the canonical producer's functions exist: the historical
        # fixed-fixture envelope deliberately reuses its midstream oracle.
        from native_mlt_ci_pcm_envelope import inspect_fixed_ci_stereo_envelope
        from native_pcm_source_fidelity import inspect_source_pcm16_stereo_content
        require(self.native_result is not None and self.master_sha is not None,
                "g2_pcm_without_retained_native_master")
        new_bytes(self.evidence_dir / "original-master-decoded.s16le", pcm)
        results = {}
        failures = []
        for name, inspect in (
            ("full_source_content", lambda: inspect_source_pcm16_stereo_content(
                self.original_wav, pcm, original_wav_sha256=REFERENCE_WAV_SHA256,
                decoded_pcm_sha256=sha(pcm))),
            ("fixed_fixture_envelope", lambda: inspect_fixed_ci_stereo_envelope(self.original_wav, pcm)),
        ):
            try:
                results[name] = {"state": "PASS", "witness": inspect()}
            except (AssertionError, ValueError) as error:
                results[name] = {"state": "FAIL", "reason": str(error)}
                failures.append(name)
        self.pcm_proof = {
            "schema": "motionwright.native.mlt-g2-source-content/1",
            "master_sha256": self.master_sha, "decoded_pcm_sha256": sha(pcm),
            "original_wav_sha256": REFERENCE_WAV_SHA256,
            "oracles": results, "source_start_offset_frames_assumed": 0,
            "packet_PTS_and_AAC_priming_verified": False,
            "physical_AV_master_acceptance": False, "human_acceptance": False,
        }
        new_json(self.evidence_dir / "source-content.json", self.pcm_proof)
        require(not failures, "g2_full_source_content_failed")
        return self.pcm_proof

    def complete(self) -> dict:
        require(self.native_result is not None
                and self.native_result.get("native_mlt_multisegment_e2e") == "PASS"
                and self.live_proof is not None and self.packet_observation is not None
                and self.pcm_proof is not None and not self.secondary_failures,
                "g2_evidence_chain_incomplete")
        require(all(row["state"] == "PASS" for row in self.pcm_proof["oracles"].values()),
                "g2_source_content_not_verified")
        require(sha(admission.checked_bytes(admission.INSTALLED, admission.MAX_ELF_BYTES))
                == REPAIRED_MODULE_SHA256, "g2_module_changed_after_native_effect")
        require(self.melt_alias_observation is not None
                and verified_installed_melt_alias(self.inputs["known_melt_sha256"])
                == self.melt_alias_observation,
                "g2_installed_melt_alias_changed_after_native_effect")
        self.completed = True
        return {
            "g2_source_content_and_live_module": "VERIFIED",
            "g2_original_wav_sha256": REFERENCE_WAV_SHA256,
            "g2_repaired_module_sha256": REPAIRED_MODULE_SHA256,
            "g2_whole_decoded_pcm_sha256": self.pcm_proof["decoded_pcm_sha256"],
            "g2_packet_observation_sha256": self.packet_observation["ffprobe_document_sha256"],
            "g2_packet_PTS_and_AAC_priming_verified": False,
            "g2_physical_AV_master_acceptance": False,
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, error, traceback):
        try:
            new_json(self.evidence_dir / "final-custody.json", {
                "schema": "motionwright.native.mlt-g2-custody/1",
                "terminal_scope": TERMINAL_SCOPE,
                "native_terminal": (
                    ("SUCCESS_REPORTED" if self.native_result.get("native_mlt_multisegment_e2e") == "PASS"
                     else "RESULT_REPORTED_UNVERIFIED") if self.native_result is not None
                    else "ERROR_OR_UNKNOWN" if self.native_error is not None else "NOT_STARTED"
                ),
                "native_result": self.native_result,
                "canonical_failure_diagnostic": (
                    self.native_error.diagnostic
                    if isinstance(self.native_error, NativeCommandFailure) else None
                ),
                "secondary_failures": self.secondary_failures,
                "post_native_error_type": None if error is None else type(error).__name__,
                "source_content_and_live_module_verified": self.completed and error is None,
                "original_source": self.provenance,
                "native_operation_retried": False,
                "packet_PTS_and_AAC_priming_verified": False,
                "physical_AV_master_acceptance": False,
                "human_acceptance": False,
            })
        except Exception:
            self.secondary("final_custody")
            if error is None:
                failure = G2Unverified("g2_final_custody_failed")
                failure.native_result = self.native_result
                raise failure
        if self.native_error is not None and error is not self.native_error:
            raise self.native_error.with_traceback(self.native_traceback)
        if error is not None and self.native_result is not None:
            try:
                error.motionwright_native_result = self.native_result
            except Exception:
                pass
        return False
