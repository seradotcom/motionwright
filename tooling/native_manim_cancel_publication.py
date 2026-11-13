"""Read-only publication of one actual cancellation and its encrypted originals."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re

from ci_private_evidence import (
    HEX, MAGIC, MAX_FILES, MAX_TOTAL, SEAL_SCHEMA, json_bytes, regular, strict,
)
from native_manim_cancel import EVIDENCE_NAME, STREAM_LIMIT, need, new_json, sha


def original(root, name):
    raw, _ = regular(root / name, STREAM_LIMIT)
    value = strict(raw)
    need(type(value) is dict, "cancel_public_original_object")
    return raw, value


def verify(root, ciphertext, context, recipient_sha):
    raw_result, result = original(root, "result.json")
    _, preseal = original(root, "cancel-verification.original.json")
    _, terminal = original(root, "original-terminal.json")
    _, custody = original(root, "cancellation-custody.json")
    protocol_raw, protocol = original(root, EVIDENCE_NAME)
    start_raw, start = original(root, "cancel-start.original.json")
    witness_raw, witness = original(root, "cancel-process-witness.original.json")
    closure_raw, closure = original(root, "cancel-process-closure.original.json")
    control_raw, control = original(root, "cancel-control.original.json")
    _, launch = original(root, "original-host-launch.json")
    _, inventory = original(root, "runtime-inventory.json")

    need(context["run_attempt"] == "1"
         and result.get("motionwright_sha") == context["source_sha"]
         and result.get("semwright_sha") == context["semwright_sha"]
         and result.get("native_manim_cancel") == "CLOSED_WITH_UNPUBLISHABLE_RESULT",
         "cancel_public_source_scope")
    expected = dict(result)
    for field in ("motionwright_sha", "semwright_sha", "sealed_originals_sha256", "sealed_originals_bytes"):
        del expected[field]
    need(json_bytes(expected) == json_bytes(preseal)
         and json_bytes(terminal.get("native_result")) == json_bytes(preseal),
         "cancel_public_original_terminal_binding")
    need(terminal.get("schema") == "motionwright.manim.cancellation.original-terminal/1"
         and terminal.get("native_example_invoked") is True
         and terminal.get("native_error") is None and terminal.get("later_error") is None
         and terminal.get("secondary_errors") == [] and terminal.get("automatic_retry") is False,
         "cancel_public_terminal_complete")
    observation = terminal.get("original_process_observation", {})
    need(type(observation.get("process_returncode")) is int
         and observation["process_returncode"] == 0
         and observation.get("automatic_retry") is False, "cancel_public_process_terminal")
    need(type(result.get("native_render_starts")) is int and result["native_render_starts"] == 1
         and type(result.get("cancel_requests")) is int and result["cancel_requests"] == 1
         and all(result.get(field) is False for field in (
             "automatic_retry", "broker_cleanup_used_as_job_closure", "human_acceptance", "formal_acceptance")),
         "cancel_public_effect_scope")
    need(result["protocol_original_sha256"] == sha(protocol_raw)
         and result["process_witness_sha256"] == sha(witness_raw)
         and result["process_closure_sha256"] == sha(closure_raw)
         and protocol.get("cancellation_exercised") is True
         and protocol.get("project_unchanged") is True
         and protocol.get("physical_process_closure_claimed") is False
         and protocol.get("protocol_error") is None
         and protocol.get("receipt_read_error") is None and protocol.get("project_read_error") is None
         and json_bytes(protocol.get("project_before")) == json_bytes(protocol.get("project_after"))
         and protocol.get("control_original_sha256") == sha(control_raw)
         and protocol.get("physical_witness_sha256") == sha(witness_raw),
         "cancel_public_protocol_originals")
    need(start.get("schema") == "motionwright.manim.active-cancel-start/1"
         and control.get("schema") == "motionwright.manim.active-cancel-control/1"
         and control.get("action") == "cancel"
         and witness.get("schema") == "motionwright.manim.active-process-witness/1"
         and control.get("witness_sha256") == sha(witness_raw)
         and witness.get("start_original_sha256") == sha(start_raw),
         "cancel_public_witness_control")
    for field in ("job_ref", "source_file", "plan_sha256"):
        need(start.get(field) == control.get(field) == witness.get(field),
             "cancel_public_single_original_job")
    need(type(start.get("job_ref")) is str and re.fullmatch(r"manim-[0-9a-f]{32}", start["job_ref"])
         and start["source_file"] == "mw_" + start["job_ref"][6:] + ".py"
         and result["job_ref"] == start["job_ref"]
         and protocol["request_id"] == start["request_id"] == result["request_id"],
         "cancel_public_original_job_identity")
    for name in ("render_terminal", "post_cancel_result"):
        response = protocol[name]
        error = response.get("error", {})
        need(response.get("result") is None and error.get("code") == "Cancelled"
             and error.get("outcome_known") is True, "cancel_public_known_cancelled_terminal")
    for name, states in (("cancel_response", ("cancelled", "cancelling")),
                         ("post_cancel_status", ("cancelled",))):
        response = protocol[name]
        need(response.get("error") is None, "cancel_public_observation_failed")
        envelope = response["result"]["result"]
        data = envelope["data"]
        need(data["state"] in states and data.get("artifact") is None
             and all(data.get(field) == start[field] for field in ("job_ref", "source_file", "plan_sha256", "profile"))
             and envelope["execution"]["provenance"]["provider_generation"] == start["provider_generation"],
             "cancel_public_observation_identity")
    need(closure.get("original_pidfds_exited") is True
         and closure.get("remaining_owned_namespace_processes") == []
         and closure.get("broker_alive_before_cleanup") is True
         and type(closure.get("empty_namespace_observations")) is int
         and closure["empty_namespace_observations"] >= 2
         and closure.get("signals_sent_by_observer") == 0
         and witness.get("signals_sent") == 0
         and closure["broker_pid"] == witness["broker_pid"] == launch["broker_pid"]
         and closure["broker_start_ticks"] == witness["broker_start_ticks"]
         and witness["manifest_sha256"] == launch["manifest_sha256"]
         and witness["session_sha256"] == launch["session_sha256"],
         "cancel_public_closure_before_broker_cleanup")
    processes = witness["originals"]
    need(type(processes) is list and len(processes) == 2
         and processes[0]["pid"] != processes[1]["pid"], "cancel_public_two_physical_originals")
    for label, process, expected_hash in zip(("runner", "python"), processes,
                                            (launch["runner_sha256"], launch["python_sha256"])):
        need(type(process["pid"]) is int and process["pid"] > 0
             and type(process["start_ticks"]) is int and process["start_ticks"] > 0
             and process["executable_sha256"] == expected_hash
             and process["namespace"] == [closure["namespace_device"], closure["namespace_inode"]],
             "cancel_public_pinned_process_identity")
        stat_raw, _ = regular(root / ("cancel-" + label + ".stat.original"), 8192)
        close = stat_raw.rfind(b")")
        fields = stat_raw[close + 2:].split()
        need(int(stat_raw[:stat_raw.index(b" ")]) == process["pid"]
             and int(fields[19]) == process["start_ticks"] and fields[0] != b"Z",
             "cancel_public_retained_kernel_stat")
        argv_raw, _ = regular(root / ("cancel-" + label + ".cmdline.original"), 256 * 1024)
        need(argv_raw.endswith(b"\0"), "cancel_public_original_argv")
        argv = argv_raw[:-1].split(b"\0")
        if label == "runner":
            need(b"--source" in argv and argv.index(b"--source") + 1 < len(argv)
                 and argv[argv.index(b"--source") + 1].decode() == start["source_file"],
                 "cancel_public_runner_source")
        else:
            need(len(argv) == 10 and argv[1] == b"-c"
                 and sha(argv[2]) == witness["fixed_writer_sha256"]
                 and Path(os.fsdecode(argv[3])).name == start["source_file"],
                 "cancel_public_python_source")
    need(inventory.get("schema") == "motionwright.manim.runtime-inventory/2"
         and inventory.get("context") == context
         and sha(regular(root / "runtime-inventory.json", STREAM_LIMIT)[0])
             == launch["runtime_inventory_sha256"], "cancel_public_runtime_context")
    need(custody.get("schema") == "motionwright.manim.cancellation.sealed-custody/1"
         and custody.get("status") == "PASS" and custody.get("context") == context
         and custody.get("selected_originals_complete") is True
         and custody.get("native_result_retained") is True
         and type(custody.get("secondary_error_count")) is int and custody["secondary_error_count"] == 0
         and custody.get("original_owner_tree_removed") is False
         and custody.get("producer_authenticated_by_encryption") is False
         and custody.get("recipient_sha256") == recipient_sha, "cancel_public_complete_custody")
    envelope, _ = regular(ciphertext, MAX_TOTAL + MAX_FILES * 4096 + 8192 + 64)
    need(sha(envelope) == custody["ciphertext_sha256"] == result["sealed_originals_sha256"]
         and len(envelope) == custody["ciphertext_bytes"] == result["sealed_originals_bytes"]
         and envelope.startswith(MAGIC), "cancel_public_ciphertext_binding")
    offset = len(MAGIC)
    length = int.from_bytes(envelope[offset:offset + 4], "big")
    need(0 < length <= 8192, "cancel_public_seal_header_bound")
    aad = envelope[offset + 4:offset + 4 + length]
    header = strict(aad)
    need(set(header) == {"schema", "algorithm", "recipient_spki_sha256", "context",
                        "nonce", "wrapped_key", "plaintext_sha256", "plaintext_bytes"}
         and json_bytes(header) == aad and header["schema"] == SEAL_SCHEMA
         and header["algorithm"] == "RSA-OAEP-SHA256+AES-256-GCM"
         and header["context"] == context and header["recipient_spki_sha256"] == recipient_sha
         and type(header["plaintext_bytes"]) is int
         and 0 < header["plaintext_bytes"] <= MAX_TOTAL + MAX_FILES * 4096
         and type(header["plaintext_sha256"]) is str and HEX.fullmatch(header["plaintext_sha256"])
         and len(envelope) == offset + 4 + length + header["plaintext_bytes"] + 16,
         "cancel_public_seal_header_binding")
    for key, size in (("nonce", 12), ("wrapped_key", 384)):
        raw = base64.b64decode(header[key], validate=True)
        need(len(raw) == size and base64.b64encode(raw).decode() == header[key],
             "cancel_public_seal_header_encoding")
    return {"schema": "motionwright.manim.active-cancel-public/1",
            "status": "VERIFIED_SCOPED_ACTIVE_CANCELLATION", "context": context,
            "native_render_starts": 1, "cancel_requests": 1,
            "original_processes_observed": 2, "original_namespace_closed_before_broker_cleanup": True,
            "result_unpublishable": True, "saved_project_unchanged": True,
            "sealed_originals_sha256": custody["ciphertext_sha256"],
            "sealed_originals_bytes": custody["ciphertext_bytes"],
            "recipient_sha256": recipient_sha, "publisher_native_effects": 0,
            "automatic_retry": False, "human_acceptance": False,
            "formal_acceptance": False, "release_approved": False}


def main():
    temporary = Path(os.environ["RUNNER_TEMP"])
    context = {"source_sha": os.environ["GITHUB_SHA"],
               "semwright_sha": json.loads(Path("SOURCE_LOCK.json").read_bytes())["dependencies"]["semwright"]["revision"],
               "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"]}
    summary = {"schema": "motionwright.manim.active-cancel-public/1", "status": "NOT_PUBLISHED",
               "context": context, "publisher_native_effects": 0, "automatic_retry": False,
               "human_acceptance": False, "formal_acceptance": False, "release_approved": False}
    if os.environ.get("NATIVE_CI_JOB_STATUS") == "success":
        try:
            summary = verify(temporary / "manim-cancel-private" / context["source_sha"],
                             temporary / "manim-cancel-sealed" / "originals.mwci", context,
                             os.environ["PRIVATE_EVIDENCE_RECIPIENT_SHA256"])
        except BaseException:
            # Originals remain private; publication neither signals nor retries.
            pass
    output = Path("verification/native-manim-public")
    output.mkdir(parents=True, exist_ok=False)
    new_json(output / "public-evidence.json", summary)
    if summary["status"] != "VERIFIED_SCOPED_ACTIVE_CANCELLATION":
        raise SystemExit("Manim cancellation publication withheld; inspect encrypted originals. No replay.")


if __name__ == "__main__":
    main()
