"""Sanitize exact-source native render failure receipts for CI-only evidence.

Never upload stderr, provider JSON, private file paths, local stack frames,
source-media names or arbitrary renderer text. The only public information is
a small, explicitly allowlisted observation reason and bounded counters.
This module does not change render outcomes, approvals, grants, or retry jobs.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

OUTPUT_DIR = re.compile(r"^render-[a-f0-9]{32}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
MAX_RECEIPT_BYTES = 64 * 1024
MAX_RENDER_DIRECTORIES = 64
MAX_RECEIPTS = 4
REASON_MARKERS = {
    "native observation count incomplete": "observation_count_incomplete",
    "native observation/frame mismatch": "observation_frame_mismatch",
    "native observation byte budget exceeded": "observation_byte_budget",
    "native font readiness observation incomplete": "font_readiness_incomplete",
    "native font readiness receipt exceeds bounds": "font_receipt_budget",
    "native observation receipt mismatch": "observation_receipt_mismatch",
    "duplicate frame payload": "duplicate_frame",
    "invalid frame payload": "invalid_frame_payload",
    "invalid png payload": "invalid_png_payload",
    "native failure output alias": "failure_output_alias",
}

def safe_observation_diagnostic(owner_output_root: Path) -> dict:
    """Return a tiny fixed-vocabulary report, never any untrusted input text."""
    report = {
        "schema": "motionwright-native-render-observation/1",
        "status": "unclassified",
        "reason": "unknown",
        "receipts_examined": 0,
        "raw_driver_text_exposed": False,
        "source_mutated": False,
    }
    try:
        if owner_output_root.is_symlink() or not owner_output_root.is_dir():
            return report
        for offset, entry in enumerate(owner_output_root.iterdir()):
            if offset >= MAX_RENDER_DIRECTORIES:
                return report
            if not OUTPUT_DIR.fullmatch(entry.name) or entry.is_symlink() or not entry.is_dir():
                continue
            receipt = entry / "native-failure-receipt.json"
            if receipt.is_symlink() or not receipt.is_file():
                continue
            if not (1 <= receipt.stat().st_size <= MAX_RECEIPT_BYTES):
                continue
            if report["receipts_examined"] >= MAX_RECEIPTS:
                return report
            report["receipts_examined"] += 1
            data = json.loads(receipt.read_bytes())
            if (not isinstance(data, dict)
                or data.get("version") != 2
                or data.get("ok") is not False
                or data.get("error_class") != "observation"
                or not isinstance(data.get("render_input_digest"), str)
                or not DIGEST.fullmatch(data["render_input_digest"])
                or not isinstance(data.get("local_stack_only"), str)
                or len(data["local_stack_only"]) > 16_384):
                continue
            # The first line can contain private paths, names, JS stack details.
            # Compare it in memory against only allowlisted static substrings.
            # NEVER add the original first line or any arbitrary substring to
            # the returned structure.
            first_line = data["local_stack_only"].split("\n", 1)[0].lower()
            for substring, safe_reason in REASON_MARKERS.items():
                if substring in first_line:
                    report["status"] = "identified"
                    report["reason"] = safe_reason
                    return report
            report["status"] = "observation_receipt_present"
    except (OSError, ValueError, TypeError, UnicodeError):
        report["status"] = "unclassified"
        report["reason"] = "unknown"
    return report
