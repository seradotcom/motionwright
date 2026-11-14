#!/usr/bin/env python3
"""Authenticate the installed Ubuntu AT-SPI GI binding before building a candidate.

This is read-only and *headless*: it constructs genuine GI MatchRule
objects without starting DBus, Xvfb, Motionwright, Native or product storage.
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
            and not os.environ.get("DISPLAY") and not os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
            "ATSPI_ABI_HEADLESS_GITHUB_LINUX_REQUIRED")
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
        from linux_first_use_accessible import AtspiBridge
        result["stage"] = "GI_IMPORT"
        bridge = object.__new__(AtspiBridge)
        bridge.api = Atspi
        constructed = []
        for roles in (("dialog",), ("entry", "text", "button")):
            result["stage"] = "MATCH_RULE_ROLES_" + str(len(roles))
            # Verify the exact constructor used by the installed worker.
            # No registry, Collection query, editor or product can run here.
            rule = bridge.collection_rule(roles)
            require(rule is not None, "ATSPI_ABI_NULL_MATCH_RULE")
            constructed.append({"roles": list(roles), "constructor": "GI_ORIGINAL"})
        result["stage"] = "COLLECTION_METHOD_AVAILABLE"
        require(callable(Atspi.Collection.get_matches)
                and Atspi.CollectionSortOrder.CANONICAL is not None,
                "ATSPI_ABI_COLLECTION_METHOD_UNAVAILABLE")
        result.update(status="PASS", stage="COMPLETED", checks=constructed)
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
