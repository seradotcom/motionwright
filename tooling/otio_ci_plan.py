#!/usr/bin/env python3
"""Select OTIO verification jobs; this plan never admits test or media results."""
import sys
if __name__ == "__main__" and (not sys.flags.isolated or not sys.dont_write_bytecode):
    sys.stderr.write("Use python -I -B tooling/otio_ci_plan.py\n")
    raise SystemExit(2)

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

WORKFLOW = ".github/workflows/v05-otio-real-media.yml"
PLANNER = "tooling/otio_ci_plan.py"
TESTS = "tooling/tests/test_otio_ci_plan.py"
REPOSITORY = "seradotcom/motionwright"
SCOPES = ("all", "rust_media", "windows_paths")
SHA = re.compile(r"[0-9a-f]{40}")
ZERO = "0" * 40
LIMIT = 2 * 1024 * 1024
MAX_DIFF = 8 * 1024 * 1024
BOTH = {
    WORKFLOW, PLANNER, "tooling/ci_test_runner.py",
    "tooling/source_policy.py", "tooling/creative_status_policy.py",
    "tooling/acceptance_policy.py", ".gitattributes",
    "tooling/otio-media/bridge.py",
    "tooling/otio-media/test_bridge.py",
    "tooling/otio-media/test_native_media.py",
}
LINUX = {
    "tooling/otio-media/native_e2e.py",
    "tooling/otio-media/public_artifact.py",
    "tooling/otio-media/test_public_artifact.py",
}
RUST_ROOT = {"Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json"}
DOCUMENT = "docs/creative-production/V05_OTIO_MEDIA_INTERCHANGE.md"


class PlanError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PlanError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def exact_sha(value, label, zero=False):
    require(isinstance(value, str) and SHA.fullmatch(value), label + " must be an exact lowercase SHA")
    require(zero or value != ZERO, label + " must not be the zero SHA")
    return value


def safe_path(value):
    require(isinstance(value, str) and 0 < len(value) <= 1024, "Invalid Git path")
    require(not value.startswith("/") and not any(c in value for c in "\\:"),
            "Absolute or platform-ambiguous Git path")
    require(not any(ord(c) < 32 or ord(c) == 127 for c in value), "Control byte in Git path")
    require(all(p not in ("", ".", "..") for p in value.split("/")), "Non-canonical Git path")
    require(value != ".git" and not value.startswith(".git/"), "Git administration path is not an input")
    return value


def regular_path(path):
    path = Path(os.path.abspath(path))
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                "Linked or reparse path")
    return path


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_file(path, limit=LIMIT):
    path = regular_path(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, "Non-regular or oversized file")
    with path.open("rb") as stream:
        data = stream.read(min(limit + 1, before.st_size + 1))
    after = path.lstat()
    require(len(data) == before.st_size and identity(after) == identity(before),
            "Input changed while reading")
    return data


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON field")
            result[key] = value
        return result
    def constant(value):
        raise PlanError("Non-finite JSON number: " + value)
    result = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    require(isinstance(result, dict), "Expected JSON object")
    return result


def git_env():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_GRAFT_FILE=os.devnull, GIT_NO_LAZY_FETCH="1")
    return env


class Git:
    def __init__(self, repo):
        self.repo = regular_path(repo)
        require(self.repo.is_dir(), "Repository directory is missing")

    def run(self, *args, optional=False, limit=MAX_DIFF):
        argv = ["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
                "-C", str(self.repo), *args]
        self.last_returncode = None
        try:
            p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=git_env(), timeout=20, check=False)
        except subprocess.TimeoutExpired as error:
            if optional:
                return None
            raise PlanError("Git source observation timed out") from error
        self.last_returncode = p.returncode
        if p.returncode:
            if optional:
                return None
            raise PlanError("Git source observation failed: " + args[0])
        if len(p.stdout) > limit:
            if optional:
                return None
            raise PlanError("Git source observation exceeded its byte budget")
        return p.stdout

    def commit(self, value, optional=False):
        exact_sha(value, "Commit")
        raw = self.run("cat-file", "commit", value, optional=optional, limit=LIMIT)
        if raw is None:
            return None
        require(hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == value,
                "Commit object identity differs")
        headers = raw.split(b"\n\n", 1)[0].splitlines()
        trees = [line[5:].decode("ascii") for line in headers if line.startswith(b"tree ")]
        parents = [line[7:].decode("ascii") for line in headers if line.startswith(b"parent ")]
        require(len(trees) == 1, "Commit tree is ambiguous")
        exact_sha(trees[0], "Tree")
        for parent in parents:
            exact_sha(parent, "Parent")
        return {"sha": value, "tree": trees[0], "parents": parents, "commit_sha256": sha256(raw)}

    def blob(self, commit, path):
        safe_path(path)
        rows = self.run("ls-tree", "-z", commit, "--", path, limit=LIMIT).split(b"\0")
        require(len(rows) == 2 and not rows[-1], "Source path is absent or ambiguous")
        meta, raw_name = rows[0].split(b"\t", 1)
        mode, kind, oid = meta.decode("ascii").split()
        require(mode in ("100644", "100755") and kind == "blob"
                and raw_name.decode("utf-8") == path, "Source path is not a regular Git blob")
        oid = exact_sha(oid, "Blob")
        size = int(self.run("cat-file", "-s", oid, limit=80).strip())
        require(0 <= size <= LIMIT, "Source blob exceeds budget")
        raw = self.run("cat-file", "blob", oid, limit=LIMIT)
        require(len(raw) == size and hashlib.sha1(b"blob " + str(size).encode() + b"\0" + raw).hexdigest() == oid,
                "Source blob identity differs")
        return raw, oid

    def ancestor(self, base, head):
        result = self.run("merge-base", "--is-ancestor", base, head, optional=True, limit=0)
        if result is not None:
            return True
        status = self.last_returncode
        if status != 1 or self.run("rev-parse", "--is-shallow-repository", limit=16).strip() != b"false":
            return None
        return False


def context(args):
    keys = ("GITHUB_REPOSITORY", "GITHUB_EVENT_NAME", "GITHUB_SHA", "GITHUB_REF",
            "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB",
            "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF")
    ctx = {key: os.environ.get(key, "") for key in keys}
    require(ctx["GITHUB_REPOSITORY"] == REPOSITORY, "Unexpected repository")
    require(ctx["GITHUB_EVENT_NAME"] == args.event_name and args.event_name in
            ("workflow_dispatch", "pull_request", "push"), "Unexpected event identity")
    exact_sha(ctx["GITHUB_SHA"], "GITHUB_SHA")
    exact_sha(ctx["GITHUB_WORKFLOW_SHA"], "GITHUB_WORKFLOW_SHA")
    require(args.source_sha == ctx["GITHUB_SHA"], "Requested source is not the event source")
    require(ctx["GITHUB_REF"].startswith("refs/") and not any(ord(c) < 32 for c in ctx["GITHUB_REF"]),
            "Invalid event ref")
    require(ctx["GITHUB_WORKFLOW_REF"] == REPOSITORY + "/" + WORKFLOW + "@" + ctx["GITHUB_REF"],
            "Unexpected workflow ref")
    require(ctx["GITHUB_JOB"] == "otio-impact", "Unexpected planning job")
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(re.fullmatch(r"[1-9][0-9]{0,19}", ctx[key]), "Invalid " + key)
    for argument, variable in ((args.event_file, "GITHUB_EVENT_PATH"),
                               (args.github_output, "GITHUB_OUTPUT")):
        expected = os.environ.get(variable, "")
        require(expected and os.path.normcase(os.path.abspath(argument)) ==
                os.path.normcase(os.path.abspath(expected)), "Unexpected " + variable + " path")
    return ctx


def source_snapshot(git, ctx):
    head = git.run("rev-parse", "--verify", "HEAD", limit=80).strip().decode("ascii")
    require(head == ctx["GITHUB_SHA"], "Checkout is not the exact event source")
    commit = git.commit(head)
    shown_root = git.run("rev-parse", "--show-toplevel", limit=8192).strip().decode("utf-8")
    require(Path(os.path.abspath(shown_root)) == git.repo, "Repository argument is not the checkout root")
    require(not git.run("diff", "--name-only", "--no-ext-diff", "--no-textconv", "HEAD", "--").strip(),
            "Tracked source is modified")
    files, blobs = {}, {}
    for path in (PLANNER, WORKFLOW):
        raw, oid = git.blob(head, path)
        physical = read_file(git.repo / path)
        require(physical == raw, "Executable/workflow bytes differ from the original Git blob")
        files[path] = {"git_blob": oid, "bytes": len(raw), "sha256": sha256(raw)}
        blobs[path] = raw
    require(regular_path(__file__) == regular_path(git.repo / PLANNER),
            "The executing planner is outside the source checkout")
    if ctx["GITHUB_WORKFLOW_SHA"] == head:
        workflow_raw, workflow_oid = blobs[WORKFLOW], files[WORKFLOW]["git_blob"]
    else:
        workflow_raw, workflow_oid = git.blob(ctx["GITHUB_WORKFLOW_SHA"], WORKFLOW)
    require(workflow_oid == files[WORKFLOW]["git_blob"], "Executed workflow definition differs")
    require(read_file(__file__) == blobs[PLANNER], "Planner changed during source observation")
    return {**commit, "files": files, "workflow_commit": ctx["GITHUB_WORKFLOW_SHA"],
            "workflow_blob": workflow_oid, "workflow_sha256": sha256(workflow_raw)}


def parse_diff(raw):
    parts = raw.split(b"\0")
    require(parts and parts[-1] == b"", "Incomplete NUL-delimited Git diff")
    parts.pop()
    paths, changes = set(), []
    while parts:
        status = parts.pop(0).decode("ascii")
        require(re.fullmatch(r"(?:[AMDTUXB]|[RC][0-9]{1,3})", status),
                "Unsupported Git diff status")
        if status[0] in "RC":
            require(int(status[1:]) <= 100, "Invalid similarity score")
        width = 2 if status[0] in "RC" else 1
        require(len(parts) >= width, "Truncated rename/copy paths")
        names = [safe_path(parts.pop(0).decode("utf-8")) for _ in range(width)]
        paths.update(names)
        changes.append({"status": status, "paths": names})
    require(len(paths) <= 10000, "Changed path inventory exceeds budget")
    return sorted(paths), changes


def change_inventory(git, event, ctx, source):
    kind = ctx["GITHUB_EVENT_NAME"]
    common = {"checkout_sha": source["sha"], "checkout_tree": source["tree"],
              "event_base_sha": None, "pr_head_sha": None, "actual_merge_base_parent": None,
              "diff_base_sha": None, "merge_base_sha": None, "paths": [], "changes": []}
    if kind == "workflow_dispatch":
        return {**common, "state": "MANUAL_SCOPE", "reason": "explicit-original-event-scope"}
    if kind == "push":
        require(event.get("after") == source["sha"] and event.get("ref") == ctx["GITHUB_REF"],
                "Push source/ref does not match the checkout event")
        base = event.get("before")
        if base is not None:
            exact_sha(base, "Push before", zero=True)
    else:
        pr = event.get("pull_request")
        require(isinstance(pr, dict), "Missing original pull request")
        require(isinstance(pr.get("head"), dict) and isinstance(pr.get("base", {}), dict),
                "Invalid pull request source descriptors")
        head = exact_sha(pr["head"].get("sha"), "PR head")
        base = pr.get("base", {}).get("sha")
        if base is not None:
            exact_sha(base, "PR base")
        common["pr_head_sha"] = head
        parents = source["parents"]
        if source["sha"] != head:
            require(len(parents) == 2 and parents[1] == head,
                    "PR checkout is neither exact head nor ordered two-parent merge")
            common["actual_merge_base_parent"] = parents[0]
    common["event_base_sha"] = base
    if not base or base == ZERO or git.commit(base, optional=True) is None:
        return {**common, "state": "CONSERVATIVE_FULL", "reason": "event-base-unavailable"}
    if git.run("rev-parse", "--is-shallow-repository", limit=16).strip() != b"false":
        return {**common, "state": "CONSERVATIVE_FULL", "reason": "incomplete-git-history"}
    if kind == "pull_request":
        actual_base = common["actual_merge_base_parent"]
        if actual_base and actual_base != base:
            related = git.ancestor(base, actual_base)
            require(related is not False, "PR actual base is unrelated to the event base")
            if related is None:
                return {**common, "state": "CONSERVATIVE_FULL", "reason": "base-ancestry-unresolved"}
        raw_bases = git.run("merge-base", "--all", base, common["pr_head_sha"], optional=True, limit=256)
        if raw_bases is None or len(raw_bases.splitlines()) != 1:
            return {**common, "state": "CONSERVATIVE_FULL", "reason": "merge-base-unresolved"}
        base = exact_sha(raw_bases.strip().decode("ascii"), "Merge base")
        common["merge_base_sha"] = base
    common["diff_base_sha"] = base
    raw = git.run("diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z",
                  "--find-renames", "--find-copies", base, source["sha"], "--", optional=True)
    if raw is None:
        return {**common, "state": "CONSERVATIVE_FULL", "reason": "changed-path-observation-unavailable"}
    paths, changes = parse_diff(raw)
    if any(c["status"][0] in "UXB" for c in changes):
        return {**common, "state": "CONSERVATIVE_FULL", "reason": "unresolved-git-change-kind",
                "paths": paths, "changes": changes, "diff_sha256": sha256(raw)}
    return {**common, "state": "EXACT_CHANGED_PATHS", "reason": "original-commit-diff",
            "paths": paths, "changes": changes, "diff_sha256": sha256(raw)}


def select_jobs(event_name, scope, inventory):
    require(scope in SCOPES, "Unknown manual scope")
    result = {"rust_media": False, "windows_paths": False, "reasons": []}
    def select(rust, windows, reason, path=None):
        result["rust_media"] |= rust
        result["windows_paths"] |= windows
        result["reasons"].append({"reason": reason, "path": path,
                                  "rust_media": rust, "windows_paths": windows})
    if event_name == "workflow_dispatch":
        select(scope in ("all", "rust_media"), scope in ("all", "windows_paths"), "explicit-manual-" + scope)
    else:
        require(scope == "all", "Automatic events cannot narrow the manual scope")
        if inventory["state"] != "EXACT_CHANGED_PATHS":
            select(True, True, inventory["reason"])
        else:
            for path in inventory["paths"]:
                safe_path(path)
                if path in BOTH:
                    select(True, True, "shared-bridge-or-execution-control", path)
                elif path in RUST_ROOT or path.startswith(("crates/domain/", ".cargo/", "rust-toolchain")):
                    select(True, False, "canonical-rust-exporter-dependency", path)
                elif path in LINUX:
                    select(True, False, "linux-media-or-publication-input", path)
                elif path == TESTS or path == DOCUMENT or (path.startswith("docs/") and path.endswith(".md")):
                    select(False, False, "g0-only-test-or-documentation", path)
                else:
                    select(True, True, "unreviewed-dependency-conservative-full", path)
            if not inventory["paths"]:
                select(False, False, "exact-empty-diff-g0-only")
    return result


def make_plan(args, git, ctx, raw_event):
    event = strict_json(raw_event)
    require(isinstance(event.get("repository"), dict)
            and event["repository"].get("full_name") == REPOSITORY, "Original event repository differs")
    if ctx["GITHUB_EVENT_NAME"] == "workflow_dispatch":
        inputs = event.get("inputs", {})
        require(isinstance(inputs, dict), "Invalid manual inputs")
        require(inputs.get("scope", "all") == args.scope, "Requested scope differs from original event")
    before = source_snapshot(git, ctx)
    inventory = change_inventory(git, event, ctx, before)
    selected = select_jobs(ctx["GITHUB_EVENT_NAME"], args.scope, inventory)
    require(source_snapshot(git, ctx) == before, "Source changed while planning")
    require(read_file(args.event_file) == raw_event, "Original event changed while planning")
    result = {
        "schema": "motionwright.otio-ci-job-plan/1", "status": "JOB_REQUESTS_PLANNED",
        "execution_context": ctx, "source": before, "event_sha256": sha256(raw_event),
        "requested_scope": args.scope, "change_inventory": inventory, "g0_required": True,
        "jobs": {name: {"selected": selected[name], "result": "NOT_EXECUTED_BY_PLANNER"}
                 for name in ("rust_media", "windows_paths")},
        "reasons": selected["reasons"],
        "test_results_admitted": False, "native_media_verified": False,
        "formal_acceptance": False, "release_candidate_verified": False,
        "reuse_authorized": False, "minutes_saved": None,
    }
    result["plan_id"] = sha256(canonical(result))
    return result


def write_plan(path, value, repo):
    path = regular_path(path)
    require(path != repo and path != repo / PLANNER and path != repo / WORKFLOW,
            "Plan output cannot replace a source")
    if path.is_relative_to(repo):
        relative = path.relative_to(repo)
        require(relative.parts[0] == "verification", "In-checkout plan output must be under verification")
    require(not path.exists(), "Plan output already exists; retain the original")
    path.parent.mkdir(parents=True, exist_ok=True)
    regular_path(path.parent)
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--event-file", type=Path, required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args(argv)
    ctx = context(args)
    git = Git(args.repo)
    raw_event = read_file(args.event_file)
    plan = make_plan(args, git, ctx, raw_event)
    regular_path(args.github_output)
    output_before = read_file(args.github_output)
    require(not output_before, "Runner output is not fresh; retain the original")
    write_plan(args.output, plan, git.repo)
    with args.github_output.open("ab") as stream:
        stream.write(("plan_id=" + plan["plan_id"] + "\n" +
                      "rust_media=" + str(plan["jobs"]["rust_media"]["selected"]).lower() + "\n" +
                      "windows_paths=" + str(plan["jobs"]["windows_paths"]["selected"]).lower() + "\n").encode("ascii"))
    require(source_snapshot(git, ctx) == plan["source"], "Source changed before planner completion")
    require(read_file(args.event_file) == raw_event, "Original event changed before planner completion")
    print(json.dumps({"status": plan["status"], "plan_id": plan["plan_id"],
                      "rust_media": plan["jobs"]["rust_media"]["selected"],
                      "windows_paths": plan["jobs"]["windows_paths"]["selected"],
                      "test_results_admitted": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PlanError, OSError, ValueError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "NOT_ADMITTED", "error": str(error),
                          "test_results_admitted": False}), file=sys.stderr)
        raise SystemExit(2)
