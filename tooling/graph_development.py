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
import re
import sys
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


def checked_independent_reader(reader, writer_oracle, initializer, source, database_path):
    """Join two actual original payloads; cannot mint a Native/Store receipt."""
    require(type(reader) is dict and type(writer_oracle) is dict
            and type(initializer) is dict and type(source) is dict,
            "DEV_READER_ORIGINALS_REQUIRED")
    expected_fields = {
        "schema", "source", "process_id", "process", "opened_before",
        "opened_after", "physical_store", "held_sqlite_pager_witness",
        "writer_oracle", "full_project", "journal", "asset_integrity_page",
        "journal_after_revision_exclusive", "journal_complete",
        "native_callbacks_allowed", "automatic_retry", "returned_live",
        "formal_release_acceptance",
    }
    require(set(reader) == expected_fields
            and reader["schema"] == "motionwright.graph-dev-live-reader-open/1"
            and reader["source"] == source
            and reader["returned_live"] is True
            and reader["native_callbacks_allowed"] is False
            and reader["automatic_retry"] is False
            and reader["formal_release_acceptance"] is False
            and type(reader["process_id"]) is int and reader["process_id"] > 1,
            "DEV_READER_ORIGINAL_FIELDS_OR_SCOPE")
    require(reader["journal_after_revision_exclusive"] == 0
            and reader["journal_complete"] is True
            and writer_oracle.get("schemaVersion") == 1
            and writer_oracle.get("journalAfterRevisionExclusive") == 0
            and writer_oracle.get("journalComplete") is True
            and reader["full_project"] == writer_oracle.get("project")
            and reader["journal"] == writer_oracle.get("journal"),
            "DEV_READER_PROJECT_OR_COMPLETE_JOURNAL_MISMATCH")
    project, history = reader["full_project"], reader["journal"]
    require(type(project) is dict and type(history) is list
            and type(project.get("revision")) is int
            and project["revision"] == len(history)
            and all(type(row) is dict and row.get("revision") == index + 1
                    for index, row in enumerate(history)),
            "DEV_READER_HISTORY_REVISIONS_NOT_CONTIGUOUS")
    # Physical CAS bytes are independently SHA-256 verified by the retained
    # read-only Store and bounded to its original project's one imported SVG.
    assets, page = project.get("assets"), reader["asset_integrity_page"]
    require(type(assets) is list and len(assets) == 1 and type(assets[0]) is dict
            and assets[0].get("media_type") == "image/svg+xml"
            and type(assets[0].get("content_sha256")) is str
            and re.fullmatch(r"[0-9a-f]{64}", assets[0]["content_sha256"])
            and type(page) is dict and set(page) == {
                "project_id", "generation", "revision", "total_assets",
                "items", "next", "complete", "checked_bytes"}
            and page["project_id"] == project.get("id")
            and page["generation"] == project.get("generation")
            and page["revision"] == project["revision"]
            and type(page["total_assets"]) is int and page["total_assets"] == 1
            and type(page["items"]) is list and len(page["items"]) == 1
            and page["next"] is None and page["complete"] is True
            and type(page["checked_bytes"]) is int,
            "DEV_READER_ORIGINAL_CAS_INTEGRITY_PAGE_INVALID")
    asset_result = page["items"][0]
    require(type(asset_result) is dict
            and set(asset_result) == {"asset_id", "size_bytes", "status"}
            and asset_result["asset_id"] == assets[0].get("id")
            and asset_result["status"] == "verified"
            and type(asset_result["size_bytes"]) is int
            and 0 < asset_result["size_bytes"] <= 4 * 1024 * 1024
            and page["checked_bytes"] == asset_result["size_bytes"],
            "DEV_READER_CAS_BLOB_BYTES_NOT_HASH_VERIFIED")
    require(reader["physical_store"] == initializer
            and initializer.get("path") == database_path
            and type(initializer.get("device")) is str
            and type(initializer.get("inode")) is str
            and initializer["device"].isdigit() and initializer["inode"].isdigit(),
            "DEV_READER_ORIGINAL_SQLITE_INODE_MISMATCH")
    return {"project_id": project.get("id"), "revision": project["revision"],
            "full_journal_events": len(history), "independent_read_only": True,
            "independent_cas_asset_bytes_hashed": True,
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
            command = ci.execute(argv, repo / "apps/studio", case_env, output, "graph-cases", 450,
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
            readback_value = ci.graph_json(ci.graph_read(report["reader_original"], 4 * ci.GRAPH_BYTES))
            # Verify the read-only process against the WRITER'S original,
            # retained under the canonical case root, rather than accepting a
            # boolean emitted by the Studio browser as Store proof.
            oracle_path = case_root / "graph-dev-closed-store/oracle.json"
            writer_ref = readback_value.get("writer_oracle")
            require(type(writer_ref) is dict and writer_ref.get("path") == str(oracle_path)
                    and writer_ref == ci.graph_ref(oracle_path, 4 * ci.GRAPH_BYTES),
                    "DEV_WRITER_ORIGINAL_ORACLE_REF_MISMATCH")
            writer_oracle = ci.graph_json(ci.graph_read(writer_ref, 4 * ci.GRAPH_BYTES))
            physical_path = case_root / "initializer-store-physical.original.json"
            initializer_ref = ci.graph_ref(physical_path, ci.GRAPH_BYTES)
            initializer = ci.graph_json(ci.graph_read(initializer_ref, ci.GRAPH_BYTES))
            report["reader_reconciliation"] = checked_independent_reader(
                readback_value, writer_oracle, initializer, index["source"],
                str(case_root / "store/motionwright.sqlite3"))
            report["writer_oracle"] = writer_ref
            report["initializer_physical_original"] = initializer_ref
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
                    and observation.get("read_only_bootstrap_verified") is True
                    and observation.get("independent_cas_asset_hash_verified") is True,
                    "DEV_ORIGINAL_SAVE_AND_REOPEN_NOT_PROVEN")
            saved = readback_value["full_project"]
            graphs = saved.get("production_design", {}).get("semantic_graphs", [])
            admitted_graphs = [graph for graph in graphs if type(graph) is dict
                               and graph.get("id") == observation.get("saved_graph_id")]
            require(type(graphs) is list and len(admitted_graphs) == 1
                    and saved["revision"] == observation.get("saved_revision")
                    and observation.get("journal") == ["upsert_semantic_graph"]
                    and type(admitted_graphs[0].get("content")) is dict
                    and [row.get("id") for row in admitted_graphs[0]["content"].get("events", [])]
                        == observation.get("event_ids")
                    and type(readback_value["journal"][-1].get("change")) is dict
                    and readback_value["journal"][-1]["change"].get("type") == "upsert_semantic_graph",
                    "DEV_SAVED_STUDIO_GRAPH_NOT_PRESENT_IN_INDEPENDENT_ORIGINAL")
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


def sealing_context(event, original_run, binding, recipient, sdk_sha):
    """Bind encryption to the original request; this grants no Native authority."""
    require(type(event) is dict and type(original_run) is dict
            and type(recipient) is dict and set(recipient) == {"spki_der_base64", "sha256"},
            "DEV_SEAL_ORIGINAL_INPUTS")
    actor = original_run.get("actor", {})
    inputs = event.get("inputs", {})
    require(original_run.get("repository", {}).get("full_name") == "seradotcom/motionwright"
            and original_run.get("head_repository", {}).get("full_name") == "seradotcom/motionwright"
            and original_run.get("event") == "workflow_dispatch"
            and original_run.get("path", "").split("@", 1)[0] == ".github/workflows/studio-service-verification.yml"
            and original_run.get("run_attempt") == 1
            and str(original_run.get("id")) == binding.get("GITHUB_RUN_ID")
            and original_run.get("head_sha") == binding.get("GITHUB_SHA")
            and binding.get("GITHUB_RUN_ATTEMPT") == "1"
            and actor.get("type") == "User" and type(actor.get("id")) is int and actor["id"] > 0
            and event.get("repository", {}).get("full_name") == "seradotcom/motionwright"
            and event.get("sender", {}).get("type") == "User"
            and event["sender"].get("id") == actor["id"]
            and type(original_run.get("head_branch")) is str and bool(original_run["head_branch"])
            and event.get("ref") in (original_run["head_branch"], "refs/heads/" + original_run["head_branch"]),
            "DEV_SEAL_ORIGINAL_RUN_MISMATCH")
    require(inputs.get("selection") == "phase1-native-graph"
            and inputs.get("graph_case_mode") == "development-first-save"
            and type(recipient["spki_der_base64"]) is str and bool(recipient["spki_der_base64"])
            and type(recipient["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", recipient["sha256"])
            and inputs.get("graph_review_spki_der_base64") == recipient["spki_der_base64"]
            and inputs.get("graph_review_spki_sha256") == recipient["sha256"],
            "DEV_SEAL_ORIGINAL_RECIPIENT_OR_SELECTION_CHANGED")
    require(type(sdk_sha) is str and re.fullmatch(r"[0-9a-f]{40}", sdk_sha)
            and type(binding.get("GITHUB_SHA")) is str and type(binding.get("GITHUB_RUN_ID")) is str
            and re.fullmatch(r"[0-9a-f]{40}", binding["GITHUB_SHA"])
            and re.fullmatch(r"[1-9][0-9]*", binding["GITHUB_RUN_ID"]),
            "DEV_SEAL_SOURCE_CONTEXT")
    return {"source_sha": binding["GITHUB_SHA"], "semwright_sha": sdk_sha,
            "run_id": binding["GITHUB_RUN_ID"], "run_attempt": "1"}


def sealing_roots(root, *, producer_complete, carrier_retained):
    """Select actual originals, including a failed producer with no index."""
    require(type(producer_complete) is bool and type(carrier_retained) is bool,
            "DEV_SEAL_CLOSED_CAPTURE_MODE")
    root = Path(root)
    if producer_complete and carrier_retained:
        return {"owner": root / "e/graph-cases",
                "controller": root / "e/graph-development-controller", "clock": root / "tools"}
    # No full producer index is required to preserve a failed original. The
    # entire bounded e tree includes its partial commands, receipt and sources.
    return {"owner": root / "e", "clock": root / "tools"}


def sealing_status(*, producer_complete, capture_complete, receipt, expected_source):
    """A retained partial/failure can never become a successful Save."""
    if not producer_complete or not capture_complete or type(receipt) is not dict:
        return "UNKNOWN"
    if (receipt.get("source") != expected_source or receipt.get("formal_acceptance") is not False
            or receipt.get("R74_reviewed") is not False or receipt.get("automatic_retry") is not False
            or receipt.get("Graph6_cases_accepted") != 0):
        return "UNKNOWN"
    status = receipt.get("status")
    return status if status in ("CLOSED_GRAPH_DEVELOPMENT_OBSERVATIONS", "FAIL", "UNKNOWN") else "UNKNOWN"


def seal(repo, ci):
    """Encrypt originals after success or producer failure, without replay."""
    repo, root = ci.graph_layout(repo)
    clock = ci.GraphOriginalClock(root)
    clock.admit("development-private-closure", 600, reserve=300)
    outcome = os.environ.get("MOTIONWRIGHT_GRAPH_PRODUCER_OUTCOME")
    carrier_outcome = os.environ.get("MOTIONWRIGHT_GRAPH_CARRIER_UPLOAD_OUTCOME")
    require(outcome in ("success", "failure", "cancelled")
            and carrier_outcome in ("success", "failure", "cancelled", "skipped"),
            "DEV_SEAL_ORIGINAL_STEP_OUTCOME_REQUIRED")
    recipient = {"spki_der_base64": os.environ.get("MOTIONWRIGHT_GRAPH_REVIEW_SPKI_DER_BASE64", ""),
                 "sha256": os.environ.get("MOTIONWRIGHT_GRAPH_REVIEW_SPKI_SHA256", "")}
    event_ref = ci.graph_ref(Path(os.environ["GITHUB_EVENT_PATH"]), ci.GRAPH_BYTES)
    event_raw = ci.graph_read(event_ref, ci.GRAPH_BYTES)
    event = ci.graph_json(event_raw)
    # These envelope labels identify the requested source/run, never claim a
    # completed build or an admitted producer when the original failed.
    lock_ref = ci.graph_ref(repo / "SOURCE_LOCK.json", ci.GRAPH_BYTES)
    lock_raw = ci.graph_read(lock_ref, ci.GRAPH_BYTES)
    lock = ci.graph_json(lock_raw)
    context = sealing_context(event, clock.original["run"], os.environ, recipient,
                              lock["dependencies"]["semwright"]["revision"])
    output = root / "e/graph-development-controller"
    destination = root / "graph-development"
    require(not destination.exists(), "DEV_PRIVATE_SEAL_ALREADY_ATTEMPTED")
    destination.mkdir(mode=0o700)
    # The same source-owned sealer and dependencies were installed in the
    # producer's first bounded preparation. No installation or key fallback.
    sys.path.insert(0, str(repo / "tooling"))
    spec = importlib.util.spec_from_file_location("motionwright_development_sealer",
                                                  repo / "tooling/ci_private_evidence.py")
    custody = importlib.util.module_from_spec(spec); spec.loader.exec_module(custody)
    custody.public_key(recipient)
    # Retain the exact dispatch and source lock even if the producer failed
    # before its own original copies were published. The clock root is sealed.
    retained_event = ci.graph_put(root / "tools/graph-development-seal-dispatch.original.json", event_raw)
    retained_lock = ci.graph_put(root / "tools/graph-development-seal-source-lock.original.json", lock_raw)

    producer_complete, index_ref, index, carrier_ref = False, None, None, None
    provenance_error = None
    if outcome == "success":
        try:
            _, _, index_ref, index = original_index(repo, ci)
            source_carrier = ci.graph_json(ci.graph_read(ci.graph_ref(
                root / "graph-review/carrier.original.json", ci.GRAPH_BYTES), ci.GRAPH_BYTES))
            require(index["source"]["sha"] == context["source_sha"]
                    and index["source"]["sdk_sha"] == context["semwright_sha"]
                    and index["source"]["run_id"] == context["run_id"]
                    and index["source"]["run_attempt"] == 1
                    and source_carrier.get("index_sha256") == index_ref["sha256"]
                    and source_carrier.get("source_sha") == context["source_sha"]
                    and source_carrier.get("recipient_spki_sha256") == recipient["sha256"],
                    "DEV_ORIGINAL_BUILD_CARRIER_MISMATCH")
            carrier_ref = ci.graph_ref(root / "graph-review/producer-originals.mwseal", custody.MAX_FILE)
            require(source_carrier.get("envelope") == {
                "filename": "producer-originals.mwseal", "sha256": carrier_ref["sha256"],
                "size_bytes": carrier_ref["size_bytes"]}, "DEV_SOURCE_CARRIER_CHANGED")
            producer_complete = True
        except Exception as error:
            # Preserve the failed original/index as bytes in the encrypted
            # partial capture, never invent the absent producer contract.
            provenance_error = {"type": type(error).__name__, "message": str(error)}
    receipt = {}
    receipt_path = output / "receipt.original.json"
    if receipt_path.is_file():
        try:
            receipt = ci.graph_json(ci.graph_read(ci.graph_ref(receipt_path, ci.GRAPH_BYTES), ci.GRAPH_BYTES))
        except Exception as error:
            provenance_error = {"type": type(error).__name__, "message": str(error)}
    carrier_retained = carrier_outcome == "success"
    roots = sealing_roots(root, producer_complete=producer_complete, carrier_retained=carrier_retained)
    ci.graph_put_json(root / "tools/graph-development-seal.intent.original.json", {
        "scope": SCOPE, "context": context, "dispatch_original": event_ref, "locked_source_original": lock_ref,
        "retained_dispatch_original": retained_event, "retained_locked_source_original": retained_lock,
        "producer_step_outcome": outcome, "producer_complete": producer_complete,
        "carrier_upload_outcome": carrier_outcome, "producer_index": index_ref,
        "source_carrier": carrier_ref, "provenance_error": provenance_error,
        "selected_roots": {key: str(value) for key, value in roots.items()},
        "formal_acceptance": False, "automatic_retry": False})
    # Existing 512 MiB / 4096 member / 256 MiB file limits remain unchanged.
    # Missing/changed/oversize originals make capture partial, never successful.
    plaintext, complete = custody.gather_snapshot(roots, preserve_partial=True)
    encrypted = custody.encrypt_snapshot(plaintext, recipient, context)
    require(len(encrypted) <= custody.MAX_FILE, "DEV_EXISTING_ENCRYPTED_FILE_BOUND")
    custody.new_bytes(destination / "development-originals.mwseal", encrypted)
    status = sealing_status(producer_complete=producer_complete, capture_complete=complete,
                            receipt=receipt, expected_source=index["source"] if producer_complete else None)
    admission = clock.admit("development-encrypted-upload", 300, reserve=0)
    public = {"schema": "motionwright.graph-development-public/1", "scope": SCOPE,
              "source_sha": context["source_sha"], "run_id": context["run_id"],
              "status": status, "private_capture_complete": complete,
              "producer_step_outcome": outcome, "producer_complete": producer_complete,
              "producer_index_sha256": index_ref["sha256"] if index_ref else None,
              "source_carrier_sha256": carrier_ref["sha256"] if carrier_ref else None,
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
