#!/usr/bin/env python3
"""Create a NOT_RUN, source-hashed experimental protocol; no fake competitor runs."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

from benchmark_compare import SCHEMA, check_study

EDIT_INTENTS = (
    "Edit the original primary statement without flattening typography.",
    "Increase secondary-text legibility while retaining the selected hierarchy.",
    "Change the brand accent and preserve authored paint overrides.",
    "Adjust 2D camera motion while keeping the same focal object identity.",
    "Protect a human-edited copy field against later automated revisions.",
    "Change a native layer effect without flattening mask/clip semantics.",
    "Revise the entrance timing with exact frame and curve control.",
    "Make a scoped repair and show precise before/after native differences.",
    "Add a contrast comparison using only owner-authorized source assets.",
    "Revisit the first design choice and prove all intervening edits survive.",
)

BRIEF = """# Paired creative capability study — SYNTHETIC PROTOCOL ONLY

Create a 90-frame 640×360 product hero that preserves native editable text,
original image/source identity, deliberate camera motion, and intentional
human edits. The same owner-authored brief and ten changes must be used in
Motionwright canonical Host and by a competent direct HyperFrames operator.

This generated brief is *not* an actual brand approval or a real product
demonstration. No commercial media or claims are provided. Replace this
source with a licensed, real creative brief and attached media before running
a fair comparison. Preserve unfavorable outcomes and all operator limitations.
"""

def create(destination: Path) -> dict:
    if destination.exists():
        raise ValueError("Refusing to overwrite benchmark study or operator evidence")
    destination.mkdir(parents=True, exist_ok=False)
    def write(relative: str, body: str) -> dict:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf8")
        return {"path":relative,"sha256":hashlib.sha256(target.read_bytes()).hexdigest()}
    brief = write("inputs/brief.md", BRIEF)
    requests = [{"index":i, "edit_request":write(f"inputs/edits/edit-{i:02d}.md",
                   f"# Shared owner revision {i}\n\n{intent}\n\n"
                   "Both operators must receive exactly this request and retain original source.\n")}
                for i,intent in enumerate(EDIT_INTENTS,1)]
    study = {
        "schema":SCHEMA,"study_id":"motionwright-v05-original-hero-protocol",
        "mode":"synthetic_protocol_fixture",
        "task":{"brief":brief,"assets":[],
                "audience":"Creative production reviewer (not a real customer)",
                "locale":"en","canvas":{"width":640,"height":360,
                       "rate_num":30,"rate_den":1,"frames":90},
                "required_capabilities":["editable_type","animated_camera"],
                "budget_minutes":120,"revision_requests":requests},
        "arms":[],"reviews":[],
        "owner_decision":{"approved":False,"reason":None,"reviewer":None}
    }
    assert check_study(study,destination)["state"]=="NOT_RUN"
    write("study.json",json.dumps(study,sort_keys=True,indent=2)+"\n")
    report = check_study(study,destination)
    write("status.json",json.dumps(report,sort_keys=True,indent=2)+"\n")
    write("README.md", """# Human-operated benchmark — NOT RUN

This package is a reproducible **protocol**, not a competitive victory.

- Use the same real original brief, assets, frames, locale and 120-minute
  budget for both capable systems.
- Record one baseline native source/render plus ten changed editable sources
  and rendered outputs for each arm.
- One arm must use Motionwright via canonical Semwright Host; the other must
  use competent direct HyperFrames or Blender. They must both have the
  required feature coverage, with operator-proficiency evidence.
- Supply exact SHA-256 paths for all project sources, renders and revised
  input requests. Reports preserve overrun, costs and human-lock violations.
- Have at least two independent blinded reviewers score the eight categories.
  Record *unfavorable* results and limitations; never auto-declare a winner.
- Owner approval, release/publish rights and human creative approval are
  separate and remain false in this generated document.

Command after replacing synthetic inputs and collecting real evidence:

    python3 tooling/creative-benchmark/benchmark_compare.py \
      <study.json> --root <authorized-artifacts-root> --output <new-report.json>

The runner inspects files without executing project source, network access,
provider installation or changing Semwright's execution grants.
""")
    return {"schema":"motionwright.benchmark-protocol-created/1",
            "state":"NOT_RUN","study_id":study["study_id"],
            "brief_sha256":brief["sha256"],"revision_count":10}

def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(create(args.output_dir),sort_keys=True))

if __name__=="__main__":
    main()
