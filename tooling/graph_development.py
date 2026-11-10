"""One authenticated development Studio Graph original, with private custody.

Formal Graph runners never call this module. The producer index has a distinct
schema and one source-bound context/Save callback budget; both original owner
processes independently authenticate the live dispatch before Native effects.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path


TITLE = "Graph6 dev: original Native Host flow Save and independent same-Store read-only verification"
SCOPE = "phase1-native-graph-development"
SCHEMA = "motionwright.private.graph-development-producer-reference-index/1"


def require(value, reason):
    if not value:
        raise ValueError(reason)


def selected_report(document, *, listing):
    """Validate the actual serial, no-retry report; this grants no authority."""
    require(type(document) is dict and document.get("errors") == [], "DEV_REPORT_ERRORS")
    config, stats = document.get("config", {}), document.get("stats", {})
    projects = config.get("projects", [])
    require(config.get("workers") == 1 and config.get("forbidOnly") is True
            and config.get("maxFailures") == 1 and len(projects) == 1
            and projects[0].get("name") == projects[0].get("id") == "chromium"
            and projects[0].get("repeatEach") == 1 and projects[0].get("retries") == 0,
            "DEV_REPORT_EXECUTION_POLICY")
    specs = []
    def visit(suites):
        require(type(suites) is list, "DEV_REPORT_SUITES")
        for suite in suites:
            require(type(suite) is dict, "DEV_REPORT_SUITE")
            specs.extend(suite.get("specs", []))
            visit(suite.get("suites", []))
    visit(document.get("suites"))
    require(len(specs) == 1 and specs[0].get("title") == TITLE
            and Path(specs[0].get("file", "")).name == "graph_dev_first_save.spec.ts",
            "DEV_REPORT_ONE_EXACT_SOURCE_CASE")
    tests = specs[0].get("tests", [])
    require(len(tests) == 1 and tests[0].get("projectId") == "chromium",
            "DEV_REPORT_ONE_ORIGINAL_TEST")
    results = tests[0].get("results", [])
    if listing:
        require(results == [], "DEV_DISCOVERY_HAS_EFFECTS")
    else:
        require(specs[0].get("ok") is True and tests[0].get("status") == "expected"
                and tests[0].get("expectedStatus") == "passed"
                and len(results) == 1 and results[0].get("status") == "passed"
                and results[0].get("retry") == 0 and not results[0].get("errors")
                and stats.get("expected") == 1 and stats.get("unexpected") == 0
                and stats.get("flaky") == 0 and stats.get("skipped") == 0,
                "DEV_REPORT_NOT_ONE_ACKNOWLEDGED_PASS")
    return {"tests": 1, "retries": 0, "listing_only": listing,
            "formal_acceptance": False}


def original_index(repo, ci):
    repo, root = ci.graph_layout(repo)
    ref = ci.graph_ref(root / "e/graph-producer/producer-index.original.json", ci.GRAPH_BYTES)
    index = ci.graph_json(ci.graph_read(ref, ci.GRAPH_BYTES))
    require(index.get("schema") == SCHEMA
            and index["contract"]["execution"].get("scope") == SCOPE
            and index["contract"]["execution"].get("selected_case") == "graph-flow",
            "DEV_REQUIRES_SEPARATE_ORIGINAL_PRODUCER")
    require(not (root / "e/r74-wait-complete.original.json").exists(),
            "DEV_CANNOT_CONSUME_FORMAL_R74_EXECUTION")
    return repo, root, ref, index


def run(repo, ci):
    repo, root, index_ref, index = original_index(repo, ci)
    clock = ci.GraphOriginalClock(root)
    producer = root / "e/graph-producer"
    output = root / "e/graph-development-controller"
    require(not output.exists(), "DEV_ORIGINAL_ALREADY_ATTEMPTED")
    output.mkdir(mode=0o700)  # Irrevocable before discovery, owner or mutation.
    report = {
        "schema": "motionwright.graph-development-execution/1", "scope": SCOPE,
        "status": "UNKNOWN", "source": index["source"], "producer_index": index_ref,
        "formal_acceptance": False, "Graph6_cases_accepted": 0,
        "R74_reviewed": False, "automatic_retry": False,
    }
    ci.graph_put_json(output / "intent.original.json", report)
    try:
        ci.graph_verify_producer_index(index_ref, "graph-flow", development=True)
        token = os.environ.get(ci.GRAPH_TOKEN_ENV)
        require(type(token) is str and bool(token), "DEV_ACTUAL_READ_ONLY_METADATA_REQUIRED")
        node = str(Path(ci.shutil.which("node")).resolve(strict=True))
        cli = str(repo / "apps/studio/node_modules/@playwright/test/cli.js")
        argv = [node, cli, "test", "--config=tooling/graph_dev_first_save.config.ts",
                "--workers=1", "--retries=0", "--repeat-each=1",
                "--max-failures=1", "--forbid-only", "--reporter=json"]
        env = dict(os.environ,
            MOTIONWRIGHT_GRAPH_DEV_FIRST_SAVE="1",
            MOTIONWRIGHT_GRAPH_PRODUCER_ORIGINAL=index_ref["path"],
            MOTIONWRIGHT_GRAPH_PRODUCER_RUN="1",
            MOTIONWRIGHT_GRAPH_DEV_RESULTS_DIR=str(output / "results"),
            SEMWRIGHT_CHECKOUT=index["source"]["sdk_root"],
            SEMWRIGHT_TEST_MANIM_RUNTIME=index["runtime"]["root"],
            SEMWRIGHT_TEST_PYTHON=index["binaries"]["python3"]["path"],
            SEMWRIGHT_TEST_FFMPEG=index["binaries"]["ffmpeg"]["path"],
            PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
        env.pop("MOTIONWRIGHT_GRAPH_NATIVE_CASE", None)
        with clock.phase("discovery"):
            discovery_env = dict(env, PLAYWRIGHT_JSON_OUTPUT_FILE=str(output / "discovery.original.json"))
            discovery_env.pop(ci.GRAPH_TOKEN_ENV, None)
            command = ci.execute([*argv, "--list"], repo / "apps/studio", discovery_env,
                                 output, "graph-discovery", 45,
                                 deadline=clock, maximum_stream_bytes=ci.GRAPH_BYTES)
            ci.require_observed_command(command)
            require(command["exit_code"] == 0, "DEV_DISCOVERY_FAILED")
            report["discovery"] = selected_report(
                ci.graph_json((output / "discovery.original.json").read_bytes()), listing=True)
        with clock.phase("cases"):
            # This config keeps Playwright results outside the physical g-slot.
            # Create its exact authenticated parent before the factory pins it;
            # Playwright must neither create nor clean the Native custody tree.
            cases_root = Path(index["contract"]["execution"]["cases_root"])
            require(cases_root == root / "e/graph-cases" and not cases_root.exists()
                    and not cases_root.is_symlink(), "DEV_FRESH_ORIGINAL_CASES_ROOT")
            cases_root.mkdir(mode=0o700)
            report["runtime_before"] = ci.graph_runtime_unchanged(index, producer)
            case_env = dict(env, PLAYWRIGHT_JSON_OUTPUT_FILE=str(output / "execution.original.json"))
            command = ci.execute(argv, repo / "apps/studio", case_env, output, "graph-cases", 300,
                deadline=clock, maximum_file_bytes=ci.GRAPH_BUDGET["store_file_bytes"],
                maximum_stream_bytes=ci.GRAPH_BYTES,
                staging_file_budget=ci.graph_case_staging_budget(index, index_ref))
            ci.require_observed_command(command)
            require(command["exit_code"] == 0, "DEV_STUDIO_NATIVE_EXECUTION_FAILED")
            report["execution"] = selected_report(
                ci.graph_json((output / "execution.original.json").read_bytes()), listing=False)
        with clock.phase("closure"):
            ci.graph_verify_producer_index(index_ref, "graph-flow", development=True)
            report["runtime_after"] = ci.graph_runtime_unchanged(index, producer)
            source_after = ci.graph_source_view(ci.graph_source_observe(repo,
                index["source"]["sha"], tuple(index["contract"]["source_files"])))
            require(source_after == ci.graph_source_view(ci.graph_json(
                ci.graph_read(index["source_original"], 8 * ci.GRAPH_BYTES))["source_before"]),
                "DEV_SOURCE_CHANGED_AFTER_EXECUTION")
            report["source_after"] = source_after
            case_root = Path(ci.graph_case_layout(
                Path(index["contract"]["execution"]["cases_root"]), "graph-flow")["private_root"])
            # Neither the safe browser observation nor process exit alone can
            # establish the terminal Native/Store original. Require both actual
            # source-owned records; independent acceptance still assesses bytes.
            terminal = case_root / "graph-dev-live-reader/reader-terminal.original.json"
            readback = case_root / "graph-dev-live-reader/readback.original.json"
            report["reader_terminal"] = ci.graph_ref(terminal, ci.GRAPH_BYTES)
            report["reader_original"] = ci.graph_ref(readback, 4 * ci.GRAPH_BYTES)
            terminal_value = ci.graph_json(ci.graph_read(report["reader_terminal"], ci.GRAPH_BYTES))
            require(terminal_value.get("closed_after_read_only_proof") is True
                    and terminal_value.get("source") == index["source"],
                    "DEV_INDEPENDENT_READER_TERMINAL_ABSENT")
            ci.graph_tree_allocation(case_root, ci.GRAPH_BUDGET["case_bytes"],
                                     ci.GRAPH_BUDGET["case_members"])
            observations = list((output / "results").rglob("native-development-first-save.json"))
            require(len(observations) == 1, "DEV_ORIGINAL_UI_OBSERVATION_ABSENT")
            observation_ref = ci.graph_ref(observations[0], ci.GRAPH_BYTES)
            observation = ci.graph_json(ci.graph_read(observation_ref, ci.GRAPH_BYTES))
            require(observation.get("schema") == "motionwright.graph-dev-first-save-observation/1"
                    and observation.get("release_acceptance") is False
                    and observation.get("R74_reviewed") is False
                    and observation.get("graph_mutations") == 1
                    and observation.get("studio_original_ack_observed") is True
                    and observation.get("studio_saved_graph_ui_reopen_verified") is True
                    and observation.get("independent_same_store_reopen") is True
                    and observation.get("opened_sqlite_identity_witness_match") is True
                    and observation.get("read_only_bootstrap_verified") is True,
                    "DEV_ORIGINAL_SAVE_AND_REOPEN_NOT_PROVEN")
            report["studio_observation"] = observation_ref
            report["status"] = "CLOSED_GRAPH_DEVELOPMENT_OBSERVATIONS"
    except BaseException as error:
        # Private original detail is encrypted by the dedicated closure step.
        report.update(status="UNKNOWN" if clock.unknown else "FAIL",
                      error={"type": type(error).__name__, "message": str(error)})
    ci.graph_put_json(output / "receipt.original.json", report)
    print(json.dumps({"scope": SCOPE, "status": report["status"],
                      "formal_acceptance": False, "Graph6_cases_accepted": 0,
                      "automatic_retry": False}, sort_keys=True))
    return 0 if report["status"] == "CLOSED_GRAPH_DEVELOPMENT_OBSERVATIONS" else 1


def seal(repo, ci):
    """Encrypt original failure/success bytes. This never upgrades an outcome."""
    repo, root, index_ref, index = original_index(repo, ci)
    clock = ci.GraphOriginalClock(root)
    # Existing aggregate deadline and unchanged sealer limits bound this
    # closure even after a failed child. It starts no Native or Store process.
    clock.admit("development-private-closure", 600, reserve=300)
    output = root / "e/graph-development-controller"
    destination = root / "graph-development"
    require(not destination.exists(), "DEV_PRIVATE_SEAL_ALREADY_ATTEMPTED")
    destination.mkdir(mode=0o700)
    source_carrier = ci.graph_json((root / "graph-review/carrier.original.json").read_bytes())
    require(source_carrier.get("index_sha256") == index_ref["sha256"]
            and source_carrier.get("source_sha") == index["source"]["sha"],
            "DEV_ORIGINAL_BUILD_CARRIER_MISMATCH")
    recipient = {"spki_der_base64": os.environ.get("MOTIONWRIGHT_GRAPH_REVIEW_SPKI_DER_BASE64", ""),
                 "sha256": os.environ.get("MOTIONWRIGHT_GRAPH_REVIEW_SPKI_SHA256", "")}
    require(recipient["sha256"] == source_carrier.get("recipient_spki_sha256"),
            "DEV_ORIGINAL_RECIPIENT_CHANGED")
    spec = importlib.util.spec_from_file_location("motionwright_development_sealer",
                                                  repo / "tooling/ci_private_evidence.py")
    custody = importlib.util.module_from_spec(spec); spec.loader.exec_module(custody)
    custody.public_key(recipient)
    # Owner files may include sockets; the existing private sealer records
    # their metadata without following them. Raw streams stay private.
    cases_root = Path(index["contract"]["execution"]["cases_root"])
    plaintext, complete = custody.gather_snapshot(
        {"owner": cases_root, "controller": output, "clock": root / "tools"},
        preserve_partial=True)
    context = {"source_sha": index["source"]["sha"], "semwright_sha": index["source"]["sdk_sha"],
               "run_id": index["source"]["run_id"], "run_attempt": "1"}
    encrypted = custody.encrypt_snapshot(plaintext, recipient, context)
    require(len(encrypted) <= custody.MAX_FILE, "DEV_EXISTING_ENCRYPTED_FILE_BOUND")
    custody.new_bytes(destination / "development-originals.mwseal", encrypted)
    receipt_path = output / "receipt.original.json"
    receipt = ci.graph_json(receipt_path.read_bytes()) if receipt_path.is_file() else {}
    status = receipt.get("status", "UNKNOWN") if complete else "UNKNOWN"
    admission = clock.admit("development-encrypted-upload", 300, reserve=0)
    public = {"schema": "motionwright.graph-development-public/1", "scope": SCOPE,
              "source_sha": index["source"]["sha"], "run_id": index["source"]["run_id"],
              "status": status, "private_capture_complete": complete,
              "producer_index_sha256": index_ref["sha256"],
              "source_carrier_sha256": source_carrier["envelope"]["sha256"],
              "encrypted_sha256": hashlib.sha256(encrypted).hexdigest(),
              "recipient_spki_sha256": recipient["sha256"],
              "formal_acceptance": False, "R74_reviewed": False,
              "Graph6_cases_accepted": 0, "G2": "NOT_GRANTED", "G3": "STOP",
              "automatic_retry": False,
              "upload_deadline_mono_ns": admission["deadline_mono_ns"]}
    custody.new_bytes(destination / "development-public.json",
                      (json.dumps(public, sort_keys=True) + "\n").encode())
    print(json.dumps(public, sort_keys=True))
    return 0
