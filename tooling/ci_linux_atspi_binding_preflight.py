#!/usr/bin/env python3
"""Authenticate the installed Ubuntu AT-SPI GI binding before building a candidate.

This is read-only: it starts an isolated accessibility registry but never opens
Motionwright, runs Native, issues a UI action, or touches product storage.
The result is a separate original, outside the closed Linux source-gate roster.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def require(ok, code):
    if not ok:
        raise ValueError(code)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source
    root = Path(__file__).resolve().parents[1]
    require(re.fullmatch(r"[0-9a-f]{40}", source) is not None,
            "ATSPI_ABI_EXACT_SOURCE_REQUIRED")
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("DISPLAY") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
            "ATSPI_ABI_GITHUB_LINUX_SESSION_REQUIRED")
    require(os.environ.get("GITHUB_SHA") == source
            and subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                text=True, timeout=8).strip() == source,
            "ATSPI_ABI_CHECKOUT_SOURCE_MISMATCH")
    result = {"schema": "motionwright.linux-atspi-system-binding/1",
              "source_sha": source,
              "status": "FAILED", "stage": "PRE_IMPORT",
              "read_only": True, "native_effects": 0, "editor_effects": 0,
              "installed_application": "NOT_LAUNCHED",
              "formal_G2": "NOT_GRANTED", "formal_G3": "STOP",
              "observed_utc": datetime.now(timezone.utc).isoformat()}
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        from linux_first_use_accessible import AtspiBridge, MAX_NODES
        result["stage"] = "GI_IMPORT"
        require(Atspi.init() in (0, 1), "ATSPI_ABI_INIT_FAILED")
        result["stage"] = "BRIDGE_INIT"
        bridge = AtspiBridge(Atspi, deadline=time.monotonic() + 15)
        matches = []
        for roles in (("dialog",), ("entry", "text", "button")):
            result["stage"] = "COLLECTION_ROLES_" + str(len(roles))
            # Call the *real* libatspi GObject method, not a mock. The empty
            # desktop is enough to detect MatchRule marshaling/signature faults.
            found = bridge.matching_roles(bridge.desktop, roles)
            require(type(found) is list and len(found) <= MAX_NODES,
                    "ATSPI_ABI_RESULT_INVALID")
            matches.append({"roles": list(roles), "bounded_results": len(found)})
        result.update(status="PASS", stage="COMPLETED", checks=matches)
    except Exception as error:
        # This source-only diagnostic has no product data or private inputs.
        # Retain the actual GI type error so we can fix ABI before a build.
        detail = str(error)
        detail = detail.replace(str(root), "[checkout]")
        result.update(failure_type=type(error).__name__,
                      failure_detail=detail[:500])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, sort_keys=True, indent=2)
        output.write("\n")
    print("linux-atspi-system-binding=" + result["status"]
          + " stage=" + result["stage"]
          + " failure_type=" + result.get("failure_type", "NONE"), flush=True)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
