"""Finite private Film180 process originals; no dispatch, retry or acceptance.

The roster is source owned and includes only names actually emitted by the
legacy FilmAttempt. A missing file is an observation, never an invented empty
stream, terminal or proof that no effects occurred. FixedTxt is a separate scope.
"""
from __future__ import annotations

# Tuple order is the actual legacy producer's causal sequence. Packet/PCM and
# failed-subprocess captures have no separate intent file in that producer.
COMMANDS = (
    ("source.seed", "original", True),
    ("original.source-reopen", "original", True),
    ("broker.motion-canvas-discovery", "single", True),
    ("broker.frames-encode", "single", True),
    ("broker.av-mux", "single", True),
    ("broker.sync-probe", "single", True),
    ("original.render", "original", True),
    ("original.stage-audio", "original", True),
    ("original.master", "original", True),
    ("original.original-packet-probe", "original", False),
    ("original.original-pcm-decode", "original", False),
    ("source.edit", "edited", True),
    ("edited.source-reopen", "edited", True),
    ("edited.render", "edited", True),
    ("edited.master", "edited", True),
    ("edited.original-packet-probe", "edited", False),
    ("edited.original-pcm-decode", "edited", False),
    ("original.failed-subprocess", "single", False),
)
TYPE = "film-command-original"
SUMMARY_SCHEMA = "motionwright.native.film180-command-originals/1"
MAX_STDOUT = 4 * 1024 * 1024
MAX_STDERR = 1024 * 1024
MAX_JSON = 1024 * 1024
MAX_INTENT = 64 * 1024
ERROR_PATH = "original-attempt-error.json"


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
    rows.append(dict(root="observation", relative_path=ERROR_PATH, phase="single",
        layer="attempt-custody", role="film-attempt-error", type=TYPE, maximum=MAX_JSON, policy=None))
    return rows


MAX_MEMBERS = 68
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


def summarize(rows, guards, payloads, selected_contract):
    """Derive only custody facts from the SAME retained buffers, never reopen.

    Payloads are exact TAR member buffers. Exit codes are reported separately
    from capture completeness; neither an exit zero nor absence grants success.
    """
    import native_evidence_projection as core
    core.require(is_contract(selected_contract), "film-command-contract")
    by_path = {(r["root"], r["relative_path"]): r for r in rows}
    absent = {(g["root"], g["relative_path"]): g for g in guards}
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
    return dict(schema=SUMMARY_SCHEMA, context=selected_contract["context"],
        fixed_members=MAX_MEMBERS, maximum_total_bytes=MAX_TOTAL, retained_original_bytes=total,
        commands=commands, attempt_error=members[ERROR_PATH],
        absent_means_no_effects=False, stream_eof_claimed=False,
        automatic_retry=False, physical_acceptance=False, human_acceptance=False,
        release_authorized=False)


def require_successful_capture(summary):
    import native_evidence_projection as core
    core.require(type(summary) is dict and summary.get("schema") == SUMMARY_SCHEMA,
                 "film-command-summary")
    required = [row for row in summary["commands"] if row["label"] != "original.failed-subprocess"]
    core.require(len(required) == 17 and all(row["capture_status"] == "ORIGINALS_RETAINED"
        and row["reported_result"] == "EXIT_ZERO_REPORTED" for row in required)
        and summary["attempt_error"]["state"] == "ABSENT_OBSERVED"
        and next(row for row in summary["commands"] if row["label"] == "original.failed-subprocess")
            ["capture_status"] == "ABSENT_OBSERVED", "film-command-custody-not-complete")
