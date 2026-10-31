"""Finite private Film180 process originals; no dispatch, retry or acceptance.

The roster is source owned and includes only names actually emitted by the
FilmAttempt. A missing file is an observation, never an invented empty
stream, terminal or proof that no effects occurred. FixedTxt is a separate scope.
"""
from __future__ import annotations

# Tuple order is the actual producer's causal sequence. Packet/PCM and
# failed-subprocess captures have no separate intent file in that producer.
COMMANDS = (
    ("source.seed", "original", True),
    ("original.source-reopen", "original", True),
    ("broker.motion-canvas-discovery", "single", True),
    ("broker.frames-encode", "single", True),
    ("broker.av-mux", "single", True),
    ("broker.sync-probe", "single", True),
    ("original.stage-audio", "original", True),
    ("original.session", "original", True),
    ("original.original-packet-probe", "original", False),
    ("original.original-pcm-decode", "original", False),
    ("source.edit", "edited", True),
    ("edited.source-reopen", "edited", True),
    ("edited.session", "edited", True),
    ("edited.original-packet-probe", "edited", False),
    ("edited.original-pcm-decode", "edited", False),
    ("original.failed-subprocess", "single", False),
)
TYPE = "film-command-original"
SUMMARY_SCHEMA = "motionwright.native.film180-command-originals/2"
SHARED_STAGE_SCHEMA = "motionwright.native.film180.shared-stage/1"
SHARED_SESSION_SCHEMA = "motionwright.native.film180.shared-session/1"
MAX_STAGE = 64 * 1024
MAX_STDOUT = 4 * 1024 * 1024
MAX_STDERR = 1024 * 1024
MAX_JSON = 1024 * 1024
MAX_INTENT = 64 * 1024
ERROR_PATH = "original-attempt-error.json"
OBSERVER_ORIGINALS = (
    ("observer-diagnostic.json", "film-observer-diagnostic", 3 * 1024 * 1024),
    ("observer-stop-error.json", "film-observer-error", 64 * 1024),
    ("observer-intermediate-error.json", "film-observer-error", 64 * 1024),
    ("observer-diagnostic-error.json", "film-observer-error", 64 * 1024),
    ("observer-close-error.json", "film-observer-error", 64 * 1024),
    ("original-observation-error.json", "film-observer-error", 64 * 1024),
)


def entries():
    rows = []
    for label, phase, has_intent in COMMANDS:
        stdout_max = (2 * 1024 * 1024 if label.endswith(".original-pcm-decode")
                      else 1024 * 1024 if label.endswith(".original-packet-probe")
                      else MAX_STDOUT)
        parts = (("intent.json", MAX_INTENT),) if has_intent else ()
        parts += (("stdout", stdout_max), ("stderr", MAX_STDERR), ("terminal.json", MAX_JSON))
        for suffix, maximum in parts:
            rows.append(dict(root="observation", relative_path=label + "." + suffix,
                phase=phase, layer="attempt-custody", role="film-command-" + suffix.split(".")[0],
                type=TYPE, maximum=maximum, policy=None))
    for phase in ("original", "edited"):
        for stage in ("render", "master"):
            for suffix in ("started", "terminal"):
                rows.append(dict(root="observation",
                    relative_path=f"{phase}-shared-session/{stage}.{suffix}.json",
                    phase=phase, layer="attempt-custody", role="film-native-stage-" + suffix,
                    type=TYPE, maximum=MAX_STAGE, policy=None))
    rows.append(dict(root="observation", relative_path=ERROR_PATH, phase="single",
        layer="attempt-custody", role="film-attempt-error", type=TYPE, maximum=MAX_JSON, policy=None))
    for phase in ("original", "edited"):
        for name, role, maximum in OBSERVER_ORIGINALS:
            rows.append(dict(root="observation", relative_path=f"{phase}-live-observer/{name}",
                phase=phase, layer="attempt-custody", role=role,
                type=TYPE, maximum=maximum, policy=None))
    return rows


MAX_MEMBERS = 80
MAX_TOTAL = sum(row["maximum"] for row in entries())


def contract(context):
    import native_evidence_projection as core
    core.context(context)
    core.require(context["scenario"] == "film180-g2", "film-command-scenario")
    roster = entries()
    core.require(len(roster) == MAX_MEMBERS
                 and len({r["relative_path"] for r in roster}) == MAX_MEMBERS,
                 "film-command-fixed-roster")
    return dict(schema=core.DIAGNOSTIC_CONTRACT_SCHEMA, context=dict(context), entries=roster)


def is_contract(value):
    import native_evidence_projection as core
    if type(value) is not dict or type(value.get("context")) is not dict:
        return False
    try:
        return value == contract(value["context"])
    except (ValueError, TypeError, KeyError, core.SelectionError):
        return False


def entry_for(root, path, selected_contract):
    if not is_contract(selected_contract):
        return None
    return next((row for row in selected_contract["entries"]
                 if row["root"] == root and row["relative_path"] == path), None)


def role_contract():
    result = {}
    for row in entries():
        key = (row["phase"], row["layer"], row["role"])
        result[key] = (0, result.get(key, (0, 0))[1] + 1)
    return result


def _json(raw):
    import native_evidence_projection as core
    try:
        value = core.strict(raw)
        return value if type(value) is dict else None
    except (ValueError, TypeError, core.SelectionError):
        return None


def checked_stage(value, phase, stage):
    """Validate a native stage receipt; it is not a subprocess terminal."""
    import re
    import native_evidence_projection as core
    keys = {"schema", "phase", "stage", "producer_pid", "project_id", "generation",
            "revision", "canonical_Film_sha256", "request_id", "state", "result",
            "evidence_sha256", "automatic_retry"}
    core.require(phase in ("original", "edited") and stage in ("render", "master")
        and type(value) is dict and set(value) == keys
        and value["schema"] == SHARED_STAGE_SCHEMA and value["phase"] == phase
        and value["stage"] == stage and value["automatic_retry"] is False,
        "film-shared-stage-contract")
    uuid = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
    core.require(type(value["producer_pid"]) is int and value["producer_pid"] > 0
        and type(value["revision"]) is int and value["revision"] >= 1
        and all(type(value[k]) is str and re.fullmatch(uuid, value[k])
                for k in ("project_id", "generation"))
        and type(value["canonical_Film_sha256"]) is str
        and re.fullmatch(r"[0-9a-f]{64}", value["canonical_Film_sha256"]),
        "film-shared-stage-source")
    expected_request = (f"film180-{phase}-native-single-run-v1" if stage == "render"
                        else f"film180-{phase}-av-master-v1")
    core.require(value["request_id"] == expected_request
        and value["state"] in ("STARTED", "SUCCESS_REPORTED", "ERROR_OR_UNKNOWN"),
        "film-shared-stage-request")
    result = value["result"]
    if value["state"] != "SUCCESS_REPORTED":
        core.require(result is None and value["evidence_sha256"] is None,
                     "film-shared-stage-nonterminal-success")
        return value
    core.require(type(result) is dict and type(value["evidence_sha256"]) is str
        and re.fullmatch(r"[0-9a-f]{64}", value["evidence_sha256"]),
        "film-shared-stage-result")
    render_keys = {"schema", "native_film180_host", "canonical_Film_sha256", "project_id",
        "generation", "revision", "frame_count", "physical_pixels_independently_decoded",
        "original_audio_master_produced", "human_approval"}
    master_keys = {"schema", "native_result_reported", "source_project_id", "generation",
        "revision", "canonical_Film_sha256", "frame_count", "original_source_audio_sha256",
        "master_audio_tail_sample_fidelity_verified", "all_180_decoded_frame_pixels_verified",
        "human_listening_or_visual_approval"}
    flags = (render_keys - {"schema", "native_film180_host", "canonical_Film_sha256",
             "project_id", "generation", "revision", "frame_count"} if stage == "render"
             else {"master_audio_tail_sample_fidelity_verified", "all_180_decoded_frame_pixels_verified",
                   "human_listening_or_visual_approval"})
    core.require(set(result) == (render_keys if stage == "render" else master_keys)
        and all(result[k] is False for k in flags), "film-shared-stage-no-acceptance")
    if stage == "master":
        core.require(type(result["original_source_audio_sha256"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", result["original_source_audio_sha256"]),
            "film-shared-stage-audio-source")
    project_key = "project_id" if stage == "render" else "source_project_id"
    core.require(result.get(project_key) == value["project_id"]
        and result.get("generation") == value["generation"]
        and type(result.get("revision")) is int and result["revision"] == value["revision"]
        and result.get("canonical_Film_sha256") == value["canonical_Film_sha256"]
        and type(result.get("frame_count")) is int and result["frame_count"] == 180,
        "film-shared-stage-result-source")
    core.require((stage == "render" and result.get("schema") == "motionwright.native.film180.original-result/1"
                  and result.get("native_film180_host") == "SUCCEEDED_REPORTED")
        or (stage == "master" and result.get("schema") == "motionwright.native.film180.av-master/1"
                  and result.get("native_result_reported") == "SUCCEEDED"),
        "film-shared-stage-result-kind")
    return value


def checked_stage_pair(started, terminal, phase, stage):
    import native_evidence_projection as core
    checked_stage(started, phase, stage)
    checked_stage(terminal, phase, stage)
    fixed = set(started) - {"state", "result", "evidence_sha256"}
    core.require(started["state"] == "STARTED"
        and terminal["state"] in ("SUCCESS_REPORTED", "ERROR_OR_UNKNOWN")
        and all(started[k] == terminal[k] for k in fixed), "film-shared-stage-continuity")
    return terminal


def checked_session(value, phase, stages):
    import native_evidence_projection as core
    core.require(type(value) is dict and set(value) == {
        "schema", "phase", "producer_pid", "project_id", "generation", "revision",
        "canonical_Film_sha256", "render", "master", "coordinator_recreated_between_stages",
        "automatic_retry"} and value["schema"] == SHARED_SESSION_SCHEMA
        and value["phase"] == phase and value["automatic_retry"] is False
        and value["coordinator_recreated_between_stages"] is False
        and type(value["producer_pid"]) is int and type(value["revision"]) is int
        and set(stages) == {"render", "master"}, "film-shared-session-contract")
    for stage, terminal in stages.items():
        checked_stage(terminal, phase, stage)
        core.require(terminal["state"] == "SUCCESS_REPORTED"
            and all(value[k] == terminal[k] for k in (
                "phase", "producer_pid", "project_id", "generation", "revision", "canonical_Film_sha256"))
            and value[stage] == terminal["result"], "film-shared-session-stage-link")
    return value


def summarize(rows, guards, payloads, selected_contract):
    """Derive only custody facts from the SAME retained buffers, never reopen.

    Payloads are exact TAR member buffers. Exit codes are reported separately
    from capture completeness; neither an exit zero nor absence grants success.
    """
    import native_evidence_projection as core
    core.require(is_contract(selected_contract), "film-command-contract")
    by_path = {(r["root"], r["relative_path"]): r for r in rows}
    absent = {(g["root"], g["relative_path"]): g for g in guards}
    def retained_value(row):
        # Existing source receipts are selected as bounded policy projections.
        # Bind the original hash carried by that envelope; never claim that the
        # full original JSON bytes were substituted into the selected archive.
        raw = payloads[row["member"]]
        value = core.strict(raw)
        core.require(type(value) is dict, "film-shared-stage-retained-object")
        if row["kind"] == "json-projection":
            core.require(value.get("schema") == core.PROJECTION_SCHEMA
                and value.get("context") == selected_contract["context"]
                and value.get("policy") == row["policy"]
                and value.get("source_sha256") == row["expected_sha256"]
                and value.get("full_original_json_claimed") is False,
                "film-shared-stage-projection-source")
            return value["value"]
        core.require(row["kind"] == "json-original"
            and core.sha(raw) == row["expected_sha256"], "film-shared-stage-original-source")
        return value
    members = {}
    total = 0
    for entry in selected_contract["entries"]:
        key = (entry["root"], entry["relative_path"])
        row, guard = by_path.get(key), absent.get(key)
        if row is not None:
            raw = payloads.get(row.get("member"))
            included = (row["state"] == "INCLUDED" and type(raw) is bytes)
            if included:
                core.require(len(raw) <= entry["maximum"] and core.sha(raw) == row["retained_sha256"],
                             "film-command-original-buffer")
                total += len(raw)
            members[entry["relative_path"]] = dict(
                state=row["state"], bytes=len(raw) if included else None,
                sha256=core.sha(raw) if included else None)
        else:
            core.require(guard is not None, "film-command-uncovered-path")
            members[entry["relative_path"]] = dict(
                state="ABSENT_OBSERVED" if guard["absent_before"] and guard["absent_after"] else "ABSENCE_UNCERTAIN",
                bytes=None, sha256=None)
    core.require(total <= MAX_TOTAL, "film-command-total-budget")
    commands = []
    for label, phase, has_intent in COMMANDS:
        suffixes = (("intent.json",) if has_intent else ()) + ("stdout", "stderr", "terminal.json")
        selected = {suffix: members[label + "." + suffix] for suffix in suffixes}
        all_present = all(value["state"] == "INCLUDED" for value in selected.values())
        all_absent = all(value["state"] == "ABSENT_OBSERVED" for value in selected.values())
        terminal_row = by_path.get(("observation", label + ".terminal.json"))
        terminal_raw = payloads.get(terminal_row.get("member")) if terminal_row else None
        terminal = _json(terminal_raw) if type(terminal_raw) is bytes else None
        linked = terminal is not None and terminal.get("automatic_retry") is False
        if linked:
            for stream in ("stdout", "stderr"):
                size_key = stream + ("_observed_bytes" if label.endswith(".original-packet-probe") else "_bytes")
                member = selected[stream]
                linked = linked and member["state"] == "INCLUDED" and type(terminal.get(size_key)) is int                     and terminal[size_key] == member["bytes"] and terminal.get(stream + "_sha256") == member["sha256"]
        code = terminal.get("exit_code") if terminal else None
        deadline = terminal.get("deadline_exceeded") if terminal else None
        result = ("UNKNOWN" if terminal is None or type(code) is not int or deadline is not False
                  else "EXIT_ZERO_REPORTED" if code == 0 else "EXIT_NONZERO_REPORTED")
        commands.append(dict(label=label, phase=phase, parts=selected,
            capture_status="ORIGINALS_RETAINED" if all_present and linked else
                "ABSENT_OBSERVED" if all_absent else "INCOMPLETE_OR_UNKNOWN",
            terminal_links_original_streams=bool(linked), reported_result=result))
    native_stages = []
    for phase in ("original", "edited"):
        linked_stages = {}
        phase_rows = []
        for stage in ("render", "master"):
            base = f"{phase}-shared-session/{stage}"
            parts = {suffix: members[base + "." + suffix + ".json"]
                     for suffix in ("started", "terminal")}
            valid = False
            state = "UNKNOWN"
            try:
                originals = []
                for suffix in ("started", "terminal"):
                    row = by_path[("observation", base + "." + suffix + ".json")]
                    core.require(row["state"] == "INCLUDED", "film-shared-stage-not-retained")
                    originals.append(core.strict(payloads[row["member"]]))
                terminal = checked_stage_pair(*originals, phase, stage)
                state = terminal["state"]
                if state == "SUCCESS_REPORTED":
                    source_name = (f"{phase}-native-Film180-evidence.json" if stage == "render"
                                   else f"{phase}-H264-AAC-native-master.json")
                    source = by_path[("observation", source_name)]
                    core.require(source["state"] == "INCLUDED"
                        and source["expected_sha256"] == terminal["evidence_sha256"],
                        "film-shared-stage-evidence-hash")
                    source_value = retained_value(source)
                    core.require(source_value.get("project_resource") == "project:" + terminal["project_id"]
                        and source_value.get("generation") == terminal["generation"]
                        and source_value.get("revision") == terminal["revision"],
                        "film-shared-stage-native-source")
                    reopen_row = by_path[("observation", f"{phase}-reopened-before-render.json")]
                    core.require(reopen_row["state"] == "INCLUDED", "film-shared-stage-reopen-absent")
                    reopened = retained_value(reopen_row)
                    core.require(reopened.get("saved_film_sha256") == terminal["canonical_Film_sha256"]
                        and all(reopened.get(k) == terminal[k]
                                for k in ("project_id", "generation", "revision")),
                        "film-shared-stage-reopened-source")
                linked_stages[stage] = terminal
                valid = True
            except (ValueError, TypeError, KeyError, core.SelectionError):
                pass
            phase_rows.append(dict(label=phase + "." + stage, phase=phase, parts=parts,
                capture_status="ORIGINAL_STAGE_RETAINED" if valid else "INCOMPLETE_OR_UNKNOWN",
                reported_result=state, session_link_verified=False))
        try:
            session_row = by_path[("observation", phase + ".session.stdout")]
            core.require(session_row["state"] == "INCLUDED", "film-shared-session-original-absent")
            checked_session(core.strict(payloads[session_row["member"]]), phase, linked_stages)
            for row in phase_rows:
                row["session_link_verified"] = True
        except (ValueError, TypeError, KeyError, core.SelectionError):
            pass
        native_stages.extend(phase_rows)
    return dict(schema=SUMMARY_SCHEMA, context=selected_contract["context"],
        fixed_members=MAX_MEMBERS, maximum_total_bytes=MAX_TOTAL, retained_original_bytes=total,
        commands=commands, native_stages=native_stages, attempt_error=members[ERROR_PATH],
        absent_means_no_effects=False, stream_eof_claimed=False,
        automatic_retry=False, physical_acceptance=False, human_acceptance=False,
        release_authorized=False)


def require_successful_capture(summary):
    import native_evidence_projection as core
    core.require(type(summary) is dict and summary.get("schema") == SUMMARY_SCHEMA,
                 "film-command-summary")
    required = [row for row in summary["commands"] if row["label"] != "original.failed-subprocess"]
    stages = summary.get("native_stages")
    core.require(type(stages) is list and len(stages) == 4
        and {row["label"] for row in stages} == {
            "original.render", "original.master", "edited.render", "edited.master"}
        and all(row["capture_status"] == "ORIGINAL_STAGE_RETAINED"
            and row["reported_result"] == "SUCCESS_REPORTED"
            and row["session_link_verified"] is True for row in stages),
        "film-shared-stages-not-complete")
    core.require(len(required) == 15 and all(row["capture_status"] == "ORIGINALS_RETAINED"
        and row["reported_result"] == "EXIT_ZERO_REPORTED" for row in required)
        and summary["attempt_error"]["state"] == "ABSENT_OBSERVED"
        and next(row for row in summary["commands"] if row["label"] == "original.failed-subprocess")
            ["capture_status"] == "ABSENT_OBSERVED", "film-command-custody-not-complete")
