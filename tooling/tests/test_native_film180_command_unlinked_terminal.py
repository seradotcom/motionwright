"""Pure Film180 custody result projection; no Host, files, crypto or subprocess."""
from __future__ import annotations
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_film180_command_custody as custody
import native_mlt_evidence_selection as mlt


def fixture(*, missing_stderr: bool):
    context = dict(source_sha="a" * 40, semwright_sha=mlt.SEM,
                   run_id="34567", run_attempt="1", scenario="film180-g2")
    contract = custody.contract(context)
    streams = {"stdout": b"ONE ORIGINAL\n", "stderr": b"ORIGINAL ERROR BYTES"}
    terminal = {
        "exit_code": 0, "deadline_exceeded": False,
        "automatic_retry": False,
        "stdout_sha256": hashlib.sha256(streams["stdout"]).hexdigest(),
        "stdout_bytes": len(streams["stdout"]),
        "stderr_sha256": hashlib.sha256(streams["stderr"]).hexdigest(),
        "stderr_bytes": len(streams["stderr"]),
    }
    data = {"original.session.stdout": streams["stdout"],
            "original.session.terminal.json": json.dumps(terminal).encode(),
            "original.session.intent.json": b'{"automatic_retry":false}'}
    if not missing_stderr:
        data["original.session.stderr"] = streams["stderr"]
    rows, guards, payloads = [], [], {}
    for entry in contract["entries"]:
        path = entry["relative_path"]
        if path in data:
            member = "selected-" + str(len(rows))
            raw = data[path]
            rows.append({"root": entry["root"], "relative_path": path,
                         "state": "INCLUDED", "member": member,
                         "retained_sha256": hashlib.sha256(raw).hexdigest(),
                         "expected_sha256": hashlib.sha256(raw).hexdigest(),
                         "kind": "json-original"})
            payloads[member] = raw
        else:
            guards.append({"root": entry["root"], "relative_path": path,
                           "absent_before": True, "absent_after": True})
    return custody.summarize(rows, guards, payloads, contract)


class FilmOriginalUnlinkedTerminalTests(unittest.TestCase):
    def test_clean_retained_terminal_can_report_original_exit_zero(self):
        value = fixture(missing_stderr=False)
        row = next(r for r in value["commands"] if r["label"] == "original.session")
        self.assertEqual(row["capture_status"], "ORIGINALS_RETAINED")
        self.assertTrue(row["terminal_links_original_streams"])
        self.assertEqual(row["reported_result"], "EXIT_ZERO_REPORTED")
        self.assertFalse(value["physical_acceptance"])

    def test_terminal_cannot_report_exit_zero_without_original_stderr(self):
        value = fixture(missing_stderr=True)
        row = next(r for r in value["commands"] if r["label"] == "original.session")
        self.assertEqual(row["capture_status"], "INCOMPLETE_OR_UNKNOWN")
        self.assertFalse(row["terminal_links_original_streams"])
        self.assertEqual(row["parts"]["stderr"]["state"], "ABSENT_OBSERVED")
        self.assertEqual(row["reported_result"], "UNKNOWN")
        self.assertFalse(value["physical_acceptance"])


if __name__ == "__main__":
    unittest.main()
