#!/usr/bin/env python3
"""Fail-closed, non-executing benchmark against a competent direct renderer.

A comparison must use one exact brief, assets, revision requests and budgets.
Never manufacture benchmark results, artistic ratings, operator competence or
an alleged winner from a generated preview or from source-code presence.
"""
from __future__ import annotations
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys

SCHEMA = "motionwright.competent-creative-benchmark/1"
RUBRIC = ("narrative", "composition", "typography", "motion",
          "editability", "truthfulness", "responsiveness", "audio")
MODES = ("motionwright_canonical_host", "hyperframes_direct", "blender_direct")
HEX = re.compile(r"^[a-f0-9]{64}$")
SHA1 = re.compile(r"^[a-f0-9]{40}$")
SLUG = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
EXT = {".md", ".txt", ".png", ".jpg", ".jpeg", ".webp", ".mp4",
       ".mov", ".webm", ".blend", ".glb", ".zip", ".json", ".otio",
       ".mlt", ".wav", ".pdf"}
FILE_BUDGET = 512 * 1024 * 1024

class StudyRejected(ValueError):
    pass

def admit(yes: bool, reason: str) -> None:
    if not yes:
        raise StudyRejected(reason)

def shape(value: object, fields: set[str], label: str) -> dict:
    admit(isinstance(value, dict) and set(value) == fields,
          f"{label} must contain exactly {sorted(fields)}")
    return value

def prose(value: object, label: str, budget: int = 1200) -> str:
    admit(isinstance(value, str) and 1 <= len(value.strip()) <= budget
          and not any(ord(ch) < 32 and ch != "\n" for ch in value),
          f"{label} must be bounded, nonempty prose")
    return value

def artifact(value: object, root: Path, label: str) -> dict:
    ref = shape(value, {"path", "sha256"}, label)
    relative = Path(prose(ref["path"], label+" path", 250))
    admit(not relative.is_absolute() and all(part not in ("..", ".")
          for part in relative.parts) and relative.suffix.lower() in EXT,
          f"{label} has unsafe/unrecognized artifact path")
    admit(isinstance(ref["sha256"], str) and HEX.fullmatch(ref["sha256"]) is not None,
          f"{label} requires exact lowercase SHA-256")
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        admit(not cursor.is_symlink(), f"{label} includes a symlink")
    admit(cursor.is_file(), f"{label} file is absent")
    size = cursor.stat().st_size
    admit(0 < size <= FILE_BUDGET, f"{label} byte budget exceeded")
    hash_ = hashlib.sha256()
    with cursor.open("rb") as stream:
        while data := stream.read(1024*1024):
            hash_.update(data)
    admit(hash_.hexdigest() == ref["sha256"], f"{label} bytes changed")
    return {"sha256":ref["sha256"], "bytes":size, "path":str(relative)}

def number(value: object, label: str, high: int) -> int:
    admit(type(value) is int and 0 <= value <= high, f"{label} outside integer bound")
    return value

def dollars(value: object) -> Decimal:
    admit(isinstance(value, str) and re.fullmatch(r"(0|[1-9][0-9]{0,6})(\.[0-9]{1,4})?", value),
          "Cost is required as a nonnegative exact USD decimal string")
    return Decimal(value)

def check_study(study: dict, root: Path) -> dict:
    admit(not root.is_symlink(), "Benchmark root must be an owner-controlled real directory")
    root = root.resolve(strict=True)
    admit(root.is_dir(), "Benchmark artifact root is not a directory")
    doc = shape(study, {"schema", "study_id", "mode", "task", "arms", "reviews", "owner_decision"}, "Study")
    admit(doc["schema"] == SCHEMA, "Unknown study schema")
    admit(isinstance(doc["study_id"], str) and SLUG.fullmatch(doc["study_id"]) is not None,
          "Invalid study identity")
    admit(doc["mode"] in ("real_work", "synthetic_protocol_fixture"), "Unknown study mode")
    task = shape(doc["task"], {"brief", "assets", "audience", "locale", "canvas",
                              "required_capabilities", "budget_minutes", "revision_requests"}, "Task")
    artifact(task["brief"], root, "Shared brief")
    prose(task["audience"], "Target audience")
    admit(task["locale"] in ("en", "es", "de"), "Unsupported source locale")
    canvas = shape(task["canvas"], {"width", "height", "rate_num", "rate_den", "frames"}, "Canvas")
    for field in canvas:
        number(canvas[field], field, 120000)
        admit(canvas[field] > 0, "Every canvas field must be positive")
    admit(canvas["width"] * canvas["height"] <= 8294400 and
          canvas["width"] <= 4096 and canvas["height"] <= 4096 and
          canvas["frames"] <= 3600, "Canvas outside source-bound profile")
    budget = number(task["budget_minutes"], "Equal operator budget", 1440)
    admit(budget >= 5, "Budget must permit meaningful competent editing")
    caps = task["required_capabilities"]
    admit(isinstance(caps, list) and 1 <= len(caps) <= 24 and all(isinstance(v, str) and SLUG.fullmatch(v) for v in caps)
          and len(caps) == len(set(caps)),
          "Required capabilities need unique canonical identifiers")
    assets = task["assets"]
    admit(isinstance(assets, list) and len(assets) <= 16, "Asset set over budget")
    if doc["mode"] == "real_work":
        admit(bool(assets), "A real product study must provide original authorized assets")
    for i, a in enumerate(assets):
        item = shape(a, {"file", "owner", "rights_attested"}, f"Asset {i}")
        artifact(item["file"], root, f"Asset {i}")
        prose(item["owner"], "Original source owner")
        admit(item["rights_attested"] is True, "Asset rights were not independently attested")
    revisions = task["revision_requests"]
    admit(isinstance(revisions, list) and len(revisions) == 10,
          "Comparison requires precisely ten identical revision requests")
    intents: dict[int, str] = {}
    for index, request in enumerate(revisions, 1):
        item = shape(request, {"index", "edit_request"}, f"Revision {index}")
        admit(item["index"] == index, "Revision request order changed")
        intents[index] = artifact(item["edit_request"], root, f"Revision {index}")["sha256"]

    arms = doc["arms"]
    admit(isinstance(arms, list) and len(arms) in (0,2), "Study must have zero or two paired arms")
    arm_ids: set[str] = set()
    modes: set[str] = set()
    metrics = []
    for a in arms:
        arm = shape(a, {"id", "renderer", "version_sha", "operator", "capabilities", "iterations"}, "Arm")
        admit(isinstance(arm["id"], str) and SLUG.fullmatch(arm["id"]) is not None
              and arm["id"] not in arm_ids, "Arm identity duplicated/invalid")
        arm_ids.add(arm["id"])
        admit(arm["renderer"] in MODES, "Arm engine not admitted")
        modes.add(arm["renderer"])
        admit(isinstance(arm["version_sha"], str) and SHA1.fullmatch(arm["version_sha"]),
              "Arm lacks exact 40-character version SHA")
        person = shape(arm["operator"], {"qualification", "preparation_artifact"}, "Operator")
        prose(person["qualification"], "Why this direct-renderer operator is competent", 2000)
        artifact(person["preparation_artifact"], root, "Operator preparation evidence")
        admissible = arm["capabilities"]
        admit(isinstance(admissible, list)
              and all(isinstance(v,str) and SLUG.fullmatch(v) for v in admissible)
              and len(admissible) == len(set(admissible))
              and set(caps).issubset(admissible),
              "Arm cannot perform required equivalent feature coverage")
        rows = arm["iterations"]
        admit(isinstance(rows, list) and len(rows) == 11, "Arm needs baseline plus ten revisions")
        prev_sha = None
        all_sources = set()
        duration = 0
        charges = Decimal("0")
        violations = 0
        for index, value in enumerate(rows):
            attempt = shape(value, {"index", "edit_request_sha256", "editable_source",
                            "render", "elapsed_seconds", "cost_usd", "human_lock_review",
                            "observation", "source_editable"}, "Iteration")
            admit(attempt["index"] == index, "Arms have nonmatching revision order")
            expected = None if index == 0 else intents[index]
            admit(attempt["edit_request_sha256"] == expected, "Compared revision requests differ")
            source = artifact(attempt["editable_source"], root, "Editable original source")
            artifact(attempt["render"], root, "Rendered revision")
            admit(attempt["source_editable"] is True, "Flattened media is not an editable project")
            admit(source["sha256"] != prev_sha and source["sha256"] not in all_sources,
                  "Revision source was unchanged or stale")
            all_sources.add(source["sha256"])
            prev_sha = source["sha256"]
            duration += number(attempt["elapsed_seconds"], "Recorded work seconds", budget * 120)
            charges += dollars(attempt["cost_usd"])
            prose(attempt["observation"], "Honest iteration observation", 2000)
            admit(attempt["human_lock_review"] in ("verified", "violation", "not_applicable"),
                  "Human edit-protection outcome must not be assumed")
            violations += attempt["human_lock_review"] == "violation"
        admit(duration > 0, "Zero-duration benchmark cannot represent competent work")
        metrics.append({"arm_id": arm["id"], "renderer":arm["renderer"],
                        "total_wall_seconds":duration, "total_cost_usd":str(charges),
                        "source_revisions":len(all_sources),
                        "human_lock_violations":violations,
                        "budget_overrun":duration > budget * 60})
    if arms:
        admit("motionwright_canonical_host" in modes and
              ("hyperframes_direct" in modes or "blender_direct" in modes) and len(modes) == 2,
              "An accepted comparison must use canonical Motionwright and a direct capable engine")
    reviews = doc["reviews"]
    admit(isinstance(reviews, list) and len(reviews) <= 12, "Reviewer count is invalid")
    reviewers = set()
    if reviews:
        admit(len(arms)==2 and len(reviews)>=2, "Blind assessment requires both arms and two raters")
        for review in reviews:
            item = shape(review, {"reviewer_id", "scores", "limitations", "blind_order_attested"}, "Review")
            admit(isinstance(item["reviewer_id"], str) and SLUG.fullmatch(item["reviewer_id"])
                  and item["reviewer_id"] not in reviewers, "Reviewers must be independent")
            reviewers.add(item["reviewer_id"])
            admit(item["blind_order_attested"] is True, "Blind scores cannot be unblinded estimates")
            prose(item["limitations"], "Reviewer limitations", 4000)
            scores = shape(item["scores"], arm_ids, "Arm ratings")
            for arm_id in arm_ids:
                marks = shape(scores[arm_id], set(RUBRIC), "Quality rubric")
                for label in RUBRIC:
                    admit(type(marks[label]) is int and 1<=marks[label]<=5,
                          "Quality scores are human observations 1..5, never inferred")
    decision = shape(doc["owner_decision"], {"approved", "reason", "reviewer"}, "Owner decision")
    admit(type(decision["approved"]) is bool, "Owner approval must be explicit")
    if decision["approved"]:
        admit(doc["mode"] == "real_work" and len(arms)==2 and len(reviews)>=2,
              "Synthetic or incomplete cases cannot be approved")
        prose(decision["reason"], "Approver decision", 3000)
        prose(decision["reviewer"], "Approving owner identity", 200)
    state = ("NOT_RUN" if not arms else
             "EVIDENCE_NEEDS_BLIND_REVIEW" if not reviews else
             "SYNTHETIC_PROTOCOL_ONLY" if doc["mode"] == "synthetic_protocol_fixture" else
             "OWNER_DECISION_RECORDED" if decision["approved"] else "AWAITING_OWNER_DECISION")
    # Report every unfavorable rating and operator-observed failure. Means
    # are descriptive, not a mechanical winner or a publication decision.
    scores_by_arm = []
    if reviews:
        for arm_id in sorted(arm_ids):
            categories = {}
            for category in RUBRIC:
                raw = [item["scores"][arm_id][category] for item in reviews]
                mean = Decimal(sum(raw)) / Decimal(len(raw))
                categories[category] = {"ratings":raw, "mean":str(mean.quantize(Decimal("0.01")))}
            scores_by_arm.append({"arm_id":arm_id,"reviewer_count":len(reviews),
                                  "category_scores":categories})
    return {"schema":"motionwright.competent-creative-benchmark-report/1",
            "study_id":doc["study_id"],"state":state,"arm_metrics":metrics,
            "blind_review_scores":scores_by_arm,
            "reviewer_limitations":[{"reviewer_id":item["reviewer_id"],
                                    "limitations":item["limitations"]} for item in reviews],
            "human_quality_evaluated":len(reviews)>=2, "publication_approved":False,
            "limitations":[
              "Artifact hashes do not prove design quality or comparable operator competence.",
              "Results can favor a direct renderer; never suppress negative outcomes.",
              "Equal brief/assets/edit requests and time budget are necessary, not sufficient for fairness.",
              "No automated winner, user permission, publication approval or source-execution grant."
            ]}

def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("study",type=Path)
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    a=parser.parse_args()
    try:
        admit(a.study.is_file() and a.study.stat().st_size<=1_000_000, "Study JSON missing/oversize")
        study=json.loads(a.study.read_text(encoding="utf8"))
        result=check_study(study,a.root)
        a.output.parent.mkdir(parents=True,exist_ok=True)
        with a.output.open("x",encoding="utf8") as stream:
            json.dump(result,stream,sort_keys=True,indent=2)
            stream.write("\n")
        print(json.dumps({"result":result["state"],"arms":len(study["arms"]),
                          "creative_approval":"separate"}))
    except (StudyRejected, OSError, json.JSONDecodeError) as error:
        print(f"benchmark-rejected: {error}",file=sys.stderr)
        raise SystemExit(2) from error

if __name__=="__main__":
    main()
