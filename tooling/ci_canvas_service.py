#!/usr/bin/env python3
"""Run selected Studio contracts against the freshly built SQLite service."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import stat
import tarfile

CANVAS_TITLES = (
    "structural removal denial is accessible and preserves authored hierarchy through repeated revisions and reopen",
    "an empty library reaches thirteen authored revisions while object, scene and workspace drafts survive a process restart",
    "an independent peer wins overlapping copy while disjoint geometry and locked drafts are preserved",
    "a late real transform acknowledgement preserves newer invalid raw input without replay after reload",
    "another tab restores foreign input explicitly and changed recovery bytes cannot be overwritten or discarded from an older view",
    "lost reply after real Canvas commit cannot replay the already saved operation",
    "Canvas pointer readback is required before real-service commit and explicit reload restores draft",
    "authored Canvas style persists across two real revisions and reopen without losing uncommitted typography",
    "Canvas hierarchy retains parent and raw Z through two real revisions and a service restart",
    "Canvas relation add and remove preserve stable IDs and source intent through reopen",
    "Canvas removal isolates the real children denial and permits explicit repair after reopen",
    "Canvas removal isolates the real incoming relations denial and permits explicit repair after reopen",
    "Canvas removal stays pending through selection and remount before the real commit response",
    "Canvas removal stays pending through selection and remount after the real commit response",
    "Canvas removal committed with a lost reply stays unknown until real SQLite reopen and never replays",
    "Canvas removal sends nothing when its record cannot be retained and verified",
    "Canvas removal sends nothing when its pointer cannot be retained and verified",
    "saved parented Canvas text stays visible in real editorial study after service reopen",
    "saved localized profile text overrides the editorial SVG across two revisions and reopening",
)
INK_TITLE = "a saved ink token changes the actual SVG text paint across revisions and reopen"
PROGRAM_TITLES = (
    "Timeline Program renders actual saved Canvas text, style and selected locale through revisions and restart",
    "a real saved partial cut excludes an editable Program scene until an explicit full-sequence revision",
    "real SQLite profile cut with stale localized text shows only the excluded original source after reopen",
)
BRIEF_TITLES = (
    "saving the title or brief commits only that group and conserves the other raw inputs",
    "a late real reply preserves newer typing and reload never replays an already committed brief",
    "returning an in-flight field to its original value survives navigation and reload until explicitly saved",
    "a real brief validation failure preserves every input through reload and correction",
    "two real clients resolve only the overlapping brief field by keeping the saved value",
    "two real clients resolve only the overlapping brief field by keeping the draft value",
    "reopening revokes a real grant without changing the revision or clearing the refused brief",
)
BRIEF_RECOVERY_TITLES = (
    "lost brief session writer pointer stops service grant; explicit Restore saves once and survives reopen",
)
BRIEF_UNCERTAINTY_TITLES = (
    "an unrelated r+1 is reviewed without acknowledging or replaying an uncertain Brief",
    "a committed Brief followed by r+2 preserves later input until separate review",
    "Brief record retention failure is NOT_SENT and retry backup permits one explicit save",
    "Brief pointer retention failure is NOT_SENT and retry backup permits one explicit save",
    "a late Brief reply cannot consume the pending state of a new recovery writer",
    "a failed recovery fork pointer keeps the earlier Brief UNKNOWN and sends nothing",
)
ACTIVE_VOICE_TITLES = (
    "active voice selection preserves both measured sources and canonical state through keyboard save and reopen",
    "a held original selection reply blocks duplicate sends across workspace remount",
    "an unrelated r+1 leaves active voice UNKNOWN until separate saved-state review",
    "a committed active voice followed by r+2 remains UNKNOWN after lost reply and reload",
    "active voice record retention failure is NOT_SENT and reload never submits it",
    "active voice pointer retention failure is NOT_SENT and reload never submits it",
)
CAMERA_TITLES = (
    "Camera reframe keeps raw input, commits twice and reopens exact SQLite Project",
    "Camera unconfirmed response cannot replay a real SQLite mutation",
    "a peer upsert with identical saved Camera values cannot acknowledge the held original",
    "a peer detach with identical saved Camera values cannot acknowledge the held original",
    "a same-revision Camera inspection cannot release an original that is still held",
    "a late saved-Camera read for scene A preserves the exact visible author copy of scene B",
    "a successful original Camera reply closes only scene A while scene B keeps its exact author bytes",
)
GESTURE_TITLES = (
    "rapid pointer move and release commits one exact Canvas transform without losing the final delta",
    "pointercancel discards the transient delta and later pointerup cannot apply it",
    "quick release with Auto-key writes a single X/Y motion pair, not a base transform",
    "a pose gesture cannot overwrite a peer after same-view reopen and release",
    "a pose gesture cannot overwrite a peer after same-view reopen and cancel",
    "a keyed gesture cannot overwrite a peer after same-view reopen and release",
    "a keyed gesture cannot overwrite a peer after same-view reopen and cancel",
)
NARRATIVE_TITLES = (
    'Narrative drafts survive actual workspace navigation/reload; separate premise and objective commits survive SQLite restart',
    'a held original premise receipt preserves newer typing across actual workspace remount',
    'a held original objective receipt preserves newer typing across actual workspace remount',
    'an uncertain Narrative unrelated outcome survives reload and is reviewed before a separate intent',
    'an uncertain Narrative committed outcome survives reload and is reviewed before a separate intent',
    'a lost Narrative writer pointer blocks real grants and writes; explicit Restore commits exactly once',
    'a real objective validation refusal preserves both fields until fresh review and correction',
    "an independent service author's overlapping premise is reviewed without consuming a disjoint objective draft",
)
BEAT_DRAFT_TITLES = (
    'beat raw drafts survive actual workspace navigation and reload before one exact SQLite commit and restart',
    'a held original beat receipt preserves later raw edits through actual workspace remount and a separate save',
    'an uncertain beat committed outcome requires real project/history review before any separate intent',
    'an uncertain beat preempted outcome requires real project/history review before any separate intent',
    'a peer retime preserves the local beat draft until explicit text with saved timing is committed',
    'a peer removal retains beat text across reload and only an explicit recreation creates a new event',
    'a shorter saved scene retains invalid beat times until the author explicitly compares and repairs them',
    'a silent beat writer pointer failure blocks real grants and saves until an explicit copy restore',
    'a failed beat ACK checkpoint preserves original UNKNOWN bytes and later text until real fresh review',
    'a real original beat removal keeps later typing through restart without an automatic recreation event',
    'a beat exported during local recovery failure imports without a write and saves exactly once to SQLite after repair',
)

BEAT_TIME_TITLES = (
    'copy-only scene beat edits retain exact fractional frame times in SQLite and after process restart',
    'authored decimal and sample times save exactly while incomplete and out-of-scene inputs cannot commit',
    'Fill timing gap commits the exact shared frame boundary and preserves both beats after restart',
    'a saved sub-microsecond gap remains editable and cannot display exact coverage until filled',
)

DELIVERY_RECIPE_TITLES = (
    'two profiles retain both canonical groups through explicit saves, clear and fresh-process reopen',
    'a committed reply held across reload preserves Sent then Old until fresh history review and explicit conflict resolution',
    'typing across Film profile and mix scopes during a real save retains every unsent input',
    'two real clients reject a stale save, review current history and resolve only the overlapping field with keep saved',
    'two real clients reject a stale save, review current history and resolve only the overlapping field with keep draft',
    'invalid numeric raw survives a real reload and only corrected values reach the service',
    'a full recovery budget preserves memory and blocks service writes until a verified copy fits',
    'an unreadable own copy is retained until explicit discard without losing newer typed input',
    "a new window with copied session storage offers another window's draft only by explicit recovery",
    'project content locks and a real revoked grant retain inputs for a fresh reviewed intent without replay',
    'Film and PCM guards use saved options and expose unsupported combinations without pretending a native result',
    "portable export and verified import preserve both recipes and CAS while isolating the new generation's drafts",
)

RECIPE_AUTHORITY_TITLES = (
    'real film peer values never acknowledge an uncertain recipe request; fresh history review precedes a separate save',
    'real mix peer values never acknowledge an uncertain recipe request; fresh history review precedes a separate save',
    'a real original recipe ACK preserves later raw numeric typing even when it returns to the submitted value',
    'silent recipe data recovery failure prevents a real grant or SQLite write; verified retry saves once',
    'silent recipe pointer recovery failure prevents a real grant or SQLite write; verified retry saves once',
)

REVIEW_TITLES = (
    'review raw range survives actual App navigation and reload before one exact SQLite commit and restart',
    'a held original review reply preserves later input before a distinct granted save',
    'a committed but lost review reply remains UNKNOWN through explicit saved-history inspection and a separate intent',
    'denied review custody prevents any SQLite mutation until a confirmed copy and explicit save',
    'an original resolution keeps a later raw reason through actual reopen and new SQLite acknowledgement',
    'an actual saved alternative retains raw rationale and records only an open manual review',
)

INSPECTOR_TITLES = (
    'Inspector raw objective and exact fraction survive actual App navigation before SQLite commits and restart',
    'held original Inspector reply retains later input before a separately granted save',
    'committed lost Inspector reply stays UNKNOWN through inspection and a distinct new intent',
    'denied Inspector custody prevents SQLite mutation until verified recovery and explicit save',
    'incomplete Inspector duration survives actual service restart without mutation',
    'Inspector stale input requires explicit retention against actual saved source',
)

PROFILE_METADATA_TITLES = (
    'raw fields survive A-B-A, an unrelated real revision, in-app reopen and browser reload without a profile write',
    'a delayed real save preserves later A input and selected B, then only an explicit valid save reaches SQLite',
    'an actual stale CAS refusal preserves sent and later raw input without retry before explicit conflict review',
    'an unreadable local recovery copy stays intact while later input is kept and only explicit discard permits saving',
    'a newly created output profile does not replace the active draft when its real response arrives late',
    'a real profile Save stops before its grant when recovery becomes blocked: changed-before-save',
    'a real profile Save stops before its grant when recovery becomes blocked: readback-differs',
    'an unknown own copy blocks actual Save until explicit reading proves absence and preserves the later raw',
    'a known quota failure blocks real Save until verified recovery without changing the authored raw',
    'custom cut stays empty until an explicit full-sequence choice across reload and real revisions',
    'a custom cut typed empty after a real save stays empty through ACK, reload and a peer full-sequence conflict',
    'a locally empty cut does not adopt a peer custom list and reviews repeated saved cuts without losing other raw input',
    'a draft whose profile was removed remains readable and downloadable, then editable after repeated revisions restore that exact profile',
    'a removed profile keeps corrupt recovery bytes and later window input as separate downloadable snapshots without a profile write',
)

PROFILE_AUTHORITY_TITLES = (
    'original profile reply confirms exact raw-derived fields in SQLite and after process restart',
    'held original profile reply retains later identical spelling and incomplete input before a separately granted save',
    'lost committed profile reply retains UNKNOWN through restart and explicit later intent',
    'lost committed profile reply retains UNKNOWN through restart and explicit clean inspection',
    'silent profile writer-pointer failure prevents grants and SQLite changes until verified recovery',
    'profile recovery budget blocks actual transport without evicting any retained original',
)

PROJECTION_NAVIGATION_TITLES = (
    'remembered 65-scene App opening and process restart use bounded native pages without complete reads',
    'explicit small-project browsing pages 129 native nodes and preserves off-page references',
    'a real peer revision returns typed stale-page failure and preserves the view until explicit reload',
    'a lost genuine node-page delivery is reported safely without fallback, replay or lost prior page',
    'bounded observations retain an original committed Review UNKNOWN and later authored text without replay',
    'a genuinely missing selected head preserves preference and allows an explicit healthy page choice',
)

SELECTIONS = (
    ("canvas", "canvas-draft-service.spec.ts", CANVAS_TITLES),
    ("ink", "visual-language-draft-service.spec.ts", (INK_TITLE,)),
    ("program", "program-design-service.spec.ts", PROGRAM_TITLES),
    ("brief", "brief-service.spec.ts", BRIEF_TITLES),
    ("brief-recovery", "brief-recovery-admission-service.spec.ts", BRIEF_RECOVERY_TITLES),
    ("brief-uncertain", "brief-uncertain-outcome-service.spec.ts", BRIEF_UNCERTAINTY_TITLES),
    ("active-voice", "audio-active-voice-service.spec.ts", ACTIVE_VOICE_TITLES),
    ("camera", "camera-reframe-service.spec.ts", CAMERA_TITLES),
    ("gesture", "canvas-gesture-service.spec.ts", GESTURE_TITLES),
    ("narrative", "narrative-service.spec.ts", NARRATIVE_TITLES),
    ('beat-draft', 'scene-beat-draft-service.spec.ts', BEAT_DRAFT_TITLES),
    ('beat-time', 'scene-beat-time-service.spec.ts', BEAT_TIME_TITLES),
    ('delivery-recipe', 'delivery-recipe.spec.ts', DELIVERY_RECIPE_TITLES),
    ('recipe-authority', 'delivery-recipe-authority-service.spec.ts', RECIPE_AUTHORITY_TITLES),
    ('review', 'review-service.spec.ts', REVIEW_TITLES),
    ('inspector', 'scene-inspector-service.spec.ts', INSPECTOR_TITLES),
    ('profile-metadata', 'delivery-profile-preservation.spec.ts', PROFILE_METADATA_TITLES),
    ('profile-authority', 'delivery-profile-authority-service.spec.ts', PROFILE_AUTHORITY_TITLES),
    ('projection-navigation', 'project-projection-service.spec.ts', PROJECTION_NAVIGATION_TITLES),
)
# Preserve the original per-file process limits. Each authoring bound includes
# every declared test timeout and the configured 120s server startup. These are
# execution bounds; only an original run establishes the actual timing.
SELECTION_GROUPS = {
    "complete-authoring": {
        "labels": ("beat-draft", "beat-time", "delivery-recipe", "recipe-authority", "review",
                   "inspector", "profile-metadata", "profile-authority"),
        "required_tests": 64,
    },
    "authoring-drafts": {
        "labels": ("beat-draft", "beat-time", "delivery-recipe", "recipe-authority", "review"),
        "required_tests": 38,
    },
}
SELECTION_TIMEOUTS = {
    "canvas": 1800,
    "ink": 180,
    "program": 600,
    "brief": 900,
    "brief-recovery": 180,
    "brief-uncertain": 900,
    "active-voice": 900,
    "camera": 1200,
    "gesture": 900,
    "narrative": 1200,
    'beat-draft': 1500,
    'beat-time': 660,
    'delivery-recipe': 1260,
    'recipe-authority': 780,
    'review': 900,
    'inspector': 900,
    'profile-metadata': 1500,
    'profile-authority': 900,
    'projection-navigation': 1260,
}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def selected_selections(selection):
    """Return a closed scope; an explicit subset never claims the full roster."""
    require(isinstance(selection, str), "selection must be a single named scope")
    if selection == "all":
        return SELECTIONS
    if selection in SELECTION_GROUPS:
        group = SELECTION_GROUPS[selection]
        labels, expected = group.get("labels"), group.get("required_tests")
        require(isinstance(labels, tuple) and labels and all(isinstance(x, str) for x in labels),
                "group must declare a nonempty tuple of file selections")
        require(len(set(labels)) == len(labels), "group repeats a file selection")
        require(type(expected) is int and expected > 0, "group must declare its exact case count")
        chosen = tuple(row for row in SELECTIONS if row[0] in labels)
        require(len(chosen) == len(labels), "group contains an unknown or duplicate file selection")
        require(sum(len(titles) for _, _, titles in chosen) == expected,
                "group case count differs from its declared closed scope")
        return chosen
    chosen = tuple(row for row in SELECTIONS if row[0] == selection)
    require(len(chosen) == 1, "unknown or empty service selection")
    return chosen


def selection_plan(selection):
    chosen = selected_selections(selection)
    labels = {row[0] for row in chosen}
    return {
        "requested": selection,
        "scope": "ALL_DEFINED_SERVICE_CASES" if selection == "all" else "EXPLICIT_SELECTED_SERVICE_CASES",
        "required": [{"label": label, "file": filename, "titles": list(titles)}
                     for label, filename, titles in chosen],
        "not_selected": [{"label": label, "file": filename, "titles": list(titles),
                          "status": "NOT_SELECTED", "technical_credit": "NONE"}
                         for label, filename, titles in SELECTIONS if label not in labels],
        "required_tests": sum(len(titles) for _, _, titles in chosen),
        "full_default_tests": sum(len(titles) for _, _, titles in SELECTIONS),
        "formal_acceptance": False,
    }


def validate_report(document, filename, titles):
    """Reject empty, omitted, repeated, retried or expected-failure selections."""
    require(isinstance(document, dict), "report must be an object")
    require(document.get("errors") == [], "report has missing or nonempty global errors")
    stats = document.get("stats", {})
    require(stats.get("expected") == len(titles), "selected success count differs")
    for name in ("skipped", "unexpected", "flaky"):
        require(stats.get(name) == 0, "selected " + name + " count is not zero")
    found = []

    def visit(suites):
        require(isinstance(suites, list), "suite list is missing")
        for suite in suites:
            require(isinstance(suite, dict), "suite must be an object")
            for spec in suite.get("specs", []):
                require(spec.get("file") == filename, "unexpected test file")
                require(spec.get("ok") is True, "test specification did not pass")
                title = spec.get("title")
                require(title in titles and title not in found, "unknown or duplicate test title")
                found.append(title)
                tests = spec.get("tests", [])
                require(len(tests) == 1, "each selected case must execute once")
                test = tests[0]
                require(test.get("projectId") == "chromium", "unexpected browser project")
                require(test.get("expectedStatus") == "passed", "expected failure or skip is forbidden")
                require(test.get("status") == "expected", "test is not an ordinary pass")
                results = test.get("results", [])
                require(len(results) == 1, "retry or missing original result")
                result = results[0]
                require(result.get("status") == "passed" and result.get("retry") == 0,
                        "original result must pass without retry")
                require(not result.get("error") and not result.get("errors"),
                        "original result contains an error")
            visit(suite.get("suites", []))

    visit(document.get("suites"))
    require(set(found) == set(titles) and len(found) == len(titles), "selected inventory differs")
    return {"passed": len(found), "skipped": 0, "retries": 0, "titles": found}


def digest(path):
    require(path.is_file() and not path.is_symlink(), "regular file required: " + str(path))
    with path.open("rb") as stream:
        value = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"bytes": path.stat().st_size, "sha256": value}


def write_new(path, value):
    body = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        require(stream.write(body) == len(body), "short evidence write")
        stream.flush()
        os.fsync(stream.fileno())


def source_observation(repo):
    def git(*arguments):
        return subprocess.check_output(["git", *arguments], cwd=repo, timeout=30).decode().strip()

    head = git("rev-parse", "HEAD")
    require(re.fullmatch("[0-9a-f]{40}", head) is not None, "invalid checkout SHA")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        require(head == os.environ.get("GITHUB_SHA"), "checkout differs from the selected CI source")
    require(git("diff", "--name-only", "HEAD", "--") == "", "tracked source has changed")
    paths = (
        ".github/workflows/ci.yml", ".github/workflows/studio-service-verification.yml",
        "Cargo.lock", "apps/studio/package-lock.json",
        "crates/service/examples/studio_review_session.rs",
        "apps/studio/playwright.config.ts",
        "apps/studio/tests/support/scopedChangeService.ts",
        "apps/studio/tests/canvas-draft-service.spec.ts",
        "apps/studio/tests/visual-language-draft-service.spec.ts",
        "apps/studio/tests/program-design-service.spec.ts",
        "apps/studio/tests/brief-service.spec.ts",
        "apps/studio/tests/brief-recovery-admission-service.spec.ts",
        "apps/studio/tests/brief-uncertain-outcome-service.spec.ts",
        "apps/studio/src/App.tsx",
        "apps/studio/src/CreativeWorkspaces.tsx",
        "apps/studio/src/NarrativeWorkspace.tsx",
        "apps/studio/src/narrativeDraft.ts",
        "apps/studio/tests/narrative-service.spec.ts",
        "apps/studio/src/BriefWorkspace.tsx",
        "apps/studio/src/briefDraft.ts",
        "apps/studio/src/AudioWorkspace.tsx",
        "apps/studio/src/audioDraft.ts",
        "apps/studio/tests/audio-active-voice-service.spec.ts",
        "apps/studio/src/ProductionDesignWorkspace.tsx",
        "apps/studio/src/CameraReframeWorkbench.tsx",
        "apps/studio/src/cameraReframeDraft.ts",
        "apps/studio/src/cameraReframe.ts",
        "apps/studio/src/projectMutation.ts",
        "apps/studio/src/CanvasWorkspace.tsx",
        "apps/studio/src/canvasCamera.ts",
        "apps/studio/tests/camera-reframe-service.spec.ts",
        "apps/studio/tests/canvas-gesture-service.spec.ts",
        "apps/studio/src/CreativeAlternativesPanel.tsx",
        "apps/studio/src/DeliveryProfiles.tsx",
        "apps/studio/src/HistoryWorkspaces.tsx",
        "apps/studio/src/NativeOriginalMixReview.tsx",
        "apps/studio/src/ReviewWorkspace.tsx",
        "apps/studio/src/SceneBeatEditor.tsx",
        "apps/studio/src/audioNarrationProtectedMarkup.test.ts",
        "apps/studio/src/deliveryRecipe.test.ts",
        "apps/studio/src/deliveryRecipe.ts",
        "apps/studio/src/deliveryRecipeDraft.test.ts",
        "apps/studio/src/deliveryRecipeDraft.ts",
        "apps/studio/src/deliveryRecipeRecoveryReview.test.ts",
        "apps/studio/src/reviewDraft.test.ts",
        "apps/studio/src/reviewDraft.ts",
        "apps/studio/src/sceneBeatDraft.test.ts",
        "apps/studio/src/sceneBeatDraft.ts",
        "apps/studio/src/sceneBeatTiming.test.ts",
        "apps/studio/src/sceneBeatTiming.ts",
        "apps/studio/tests/delivery-recipe-authority-service.spec.ts",
        "apps/studio/tests/delivery-recipe-authority.spec.ts",
        "apps/studio/tests/delivery-recipe-recovery-review.spec.ts",
        "apps/studio/tests/delivery-recipe.spec.ts",
        "apps/studio/tests/fixtures/delivery-recipe-authority.html",
        "apps/studio/tests/fixtures/delivery-recipe-authority.tsx",
        "apps/studio/tests/fixtures/review-authority.html",
        "apps/studio/tests/fixtures/review-authority.tsx",
        "apps/studio/tests/fixtures/scene-beat-authority.html",
        "apps/studio/tests/fixtures/scene-beat-authority.tsx",
        "apps/studio/tests/native-original-mix-voice-binding.spec.ts",
        "apps/studio/tests/native-original-mix.spec.ts",
        "apps/studio/tests/review-preservation.spec.ts",
        "apps/studio/tests/review-service.spec.ts",
        "apps/studio/tests/scene-beat-draft-service.spec.ts",
        "apps/studio/tests/scene-beat-preservation.spec.ts",
        "apps/studio/tests/scene-beat-time-service.spec.ts",
        "apps/studio/src/SceneInspector.tsx",
        "apps/studio/src/deliveryProfileDraft.test.ts",
        "apps/studio/src/deliveryProfileDraft.ts",
        "apps/studio/src/sceneInspectorDraft.test.ts",
        "apps/studio/src/sceneInspectorDraft.ts",
        "apps/studio/tests/delivery-profile-authority-service.spec.ts",
        "apps/studio/tests/delivery-profile-authority.spec.ts",
        "apps/studio/tests/delivery-profile-preservation.spec.ts",
        "apps/studio/tests/fixtures/delivery-profile-authority.html",
        "apps/studio/tests/fixtures/delivery-profile-authority.tsx",
        "apps/studio/tests/fixtures/scene-inspector-authority.html",
        "apps/studio/tests/fixtures/scene-inspector-authority.tsx",
        "apps/studio/tests/scene-inspector-preservation.spec.ts",
        "apps/studio/tests/scene-inspector-service.spec.ts",
        "Cargo.toml",
        "SOURCE_LOCK.json",
        "rust-toolchain.toml",
        "crates/domain/Cargo.toml",
        "crates/storage/Cargo.toml",
        "crates/service/Cargo.toml",
        "apps/desktop/src-tauri/Cargo.toml",
        "apps/studio/package.json",
        "apps/studio/src/api.ts",
        "apps/studio/src/types.ts",
        "apps/studio/index.html",
        "apps/studio/vite.config.ts",
        "apps/studio/tsconfig.json",
        "apps/studio/tsconfig.app.json",
        "apps/studio/src/main.tsx",
        "apps/studio/src/styles.css",
        "apps/studio/src/fixture.ts",
        "apps/studio/src/ProjectLibraryDialog.tsx",
        "apps/studio/src/projectLibrary.ts",
        "apps/studio/src/project-library.css",
        "apps/studio/src/ProjectProjectionWorkspace.tsx",
        "apps/studio/src/projectProjection.ts",
        "apps/studio/src/projectProjection.test.ts",
        "apps/studio/src/projectMutation.test.ts",
        "apps/studio/tests/project-projection-service.spec.ts",
        "apps/studio/tests/project-projection.spec.ts",
        "apps/studio/tests/fixtures/project-projection.html",
        "apps/studio/tests/fixtures/project-projection.tsx",
        "apps/desktop/src-tauri/src/effect_grants.rs",
        "apps/desktop/src-tauri/src/project_library.rs",
        "apps/desktop/src-tauri/src/project_projection.rs",
        "apps/desktop/src-tauri/src/caption_export.rs",
        "apps/desktop/src-tauri/src/file_import.rs",
        "crates/domain/src/lib.rs",
        "crates/domain/src/canvas.rs",
        "crates/domain/src/history.rs",
        "crates/domain/src/creative.rs",
        "crates/service/src/lib.rs",
        "crates/service/src/project_creation.rs",
        "crates/service/src/project_projection.rs",
        "crates/storage/src/lib.rs",
        "crates/storage/src/project_creation.rs",
        "crates/storage/src/project_projection.rs",
        "tooling/ci_canvas_service.py",
        "tooling/tests/test_ci_canvas_service.py",
    )
    files = {}
    for name in paths:
        original = subprocess.check_output(["git", "show", head + ":" + name], cwd=repo, timeout=30)
        observed = digest(repo / name)
        require(observed == {"bytes": len(original), "sha256": hashlib.sha256(original).hexdigest()},
                "source differs from Git: " + name)
        files[name] = observed
    return {"checkout": head, "tree": git("rev-parse", "HEAD^{tree}"), "files": files}


def execute(argv, cwd, env, output, label, timeout):
    stdout = output / (label + ".stdout.log")
    stderr = output / (label + ".stderr.log")
    command = {"argv": argv, "cwd": str(cwd), "started_utc": datetime.now(timezone.utc).isoformat()}
    with stdout.open("xb") as out, stderr.open("xb") as err:
        try:
            result = subprocess.run(argv, cwd=cwd, env=env, stdout=out, stderr=err,
                                    timeout=timeout, check=False)
            command["exit_code"] = result.returncode
            command["diagnostic_custody"] = "DIRECT_PROCESS_EXIT_OBSERVED"
        except subprocess.TimeoutExpired:
            command["exit_code"] = None
            command["observation"] = "TIMED_OUT_NO_RETRY"
            # subprocess.run observes and kills its direct process only. The
            # original descendants may still hold these streams. Retain this
            # bounded snapshot without claiming sealed diagnostic custody.
            command["diagnostic_custody"] = "UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED"
            command["timeout_seconds"] = timeout
    command["finished_utc"] = datetime.now(timezone.utc).isoformat()
    command["stdout"] = digest(stdout)
    command["stderr"] = digest(stderr)
    write_new(output / (label + ".command.json"), command)
    return command



ARCHIVE_ROOTS = ("e", "tools")
ARCHIVE_LIMITS = {"members": 20000, "file_bytes": 512 * 1024 * 1024,
                  "total_bytes": 2 * 1024 * 1024 * 1024, "path_bytes": 1024}
ARCHIVE_CHUNK = 1024 * 1024


def archive_member_name(name):
    require(isinstance(name, str) and name and len(name.encode("utf-8")) <= ARCHIVE_LIMITS["path_bytes"],
            "archive member name exceeds bound")
    parts = name.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    require(len(parts) <= 64 and parts[0] in ARCHIVE_ROOTS
            and all(part not in ("", ".", "..") and not part.endswith((" ", "."))
                    and part.split(".")[0].upper() not in reserved for part in parts)
            and "\\" not in name and ":" not in name
            and all(ord(c) >= 32 and ord(c) != 127 for c in name),
            "unsafe archive member name")
    return name


def archive_stat(path):
    value = path.lstat()
    require(not stat.S_ISLNK(value.st_mode)
            and not getattr(value, "st_file_attributes", 0) & 0x400,
            "archive symlink or reparse point forbidden")
    require(stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode),
            "archive nonregular input forbidden")
    if stat.S_ISREG(value.st_mode):
        require(value.st_nlink == 1, "archive hard-linked input forbidden")
    return value


def archive_identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)


def archive_ancestors(path):
    for parent in (path, *path.parents):
        if parent.exists() or parent.is_symlink():
            archive_stat(parent)


def archive_file_digest(path, limit):
    archive_ancestors(path)
    before = archive_stat(path)
    require(stat.S_ISREG(before.st_mode) and before.st_size <= limit,
            "archive file exceeds bound")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        require(archive_identity(os.fstat(stream.fileno())) == archive_identity(before),
                "archive file changed before read")
        count, value = 0, hashlib.sha256()
        while block := stream.read(min(ARCHIVE_CHUNK, limit - count + 1)):
            count += len(block)
            require(count <= limit, "archive file grew beyond bound")
            value.update(block)
        require(archive_identity(os.fstat(stream.fileno())) == archive_identity(before),
                "archive open file changed during read")
    require(count == before.st_size and archive_identity(archive_stat(path)) == archive_identity(before),
            "archive file changed during read")
    archive_ancestors(path)
    return {"bytes": count, "sha256": value.hexdigest()}


def archive_inventory(root):
    archive_ancestors(root)
    require(root.is_dir(), "archive evidence directory missing")
    entries, identities, folded, total = {}, {}, set(), 0
    pending = [root / name for name in reversed(ARCHIVE_ROOTS)]
    while pending:
        path = pending.pop()
        name = archive_member_name(path.relative_to(root).as_posix())
        require(len(entries) < ARCHIVE_LIMITS["members"] and name.casefold() not in folded,
                "archive member bound or alias collision")
        folded.add(name.casefold())
        value = archive_stat(path)
        identity = archive_identity(value)
        item = {"name": name, "kind": "directory" if stat.S_ISDIR(value.st_mode) else "file",
                "mode": stat.S_IMODE(value.st_mode)}
        if item["kind"] == "directory":
            children = []
            with os.scandir(path) as stream:
                for child in stream:
                    children.append(Path(child.path))
                    require(len(entries) + len(pending) + len(children) <= ARCHIVE_LIMITS["members"],
                            "archive member bound")
            require(archive_identity(archive_stat(path)) == identity, "archive directory changed")
            pending.extend(sorted(children, key=lambda p: p.name, reverse=True))
        else:
            item.update(archive_file_digest(path, ARCHIVE_LIMITS["file_bytes"]))
            total += item["bytes"]
            require(total <= ARCHIVE_LIMITS["total_bytes"], "archive total byte bound")
        entries[name], identities[name] = item, identity
    require(all(entries.get(name, {}).get("kind") == "directory" for name in ARCHIVE_ROOTS),
            "archive e/tools directories required")
    return {"entries": dict(sorted(entries.items())), "identities": identities, "file_bytes": total}


def archive_json(root, inventory, name):
    item = inventory["entries"].get(name)
    require(item is not None and item["kind"] == "file" and item["bytes"] <= 64 * 1024 * 1024,
            "bounded original JSON required: " + name)
    path = root / name
    require(archive_file_digest(path, 64 * 1024 * 1024) == {k: item[k] for k in ("bytes", "sha256")},
            "original JSON changed")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        require(archive_identity(os.fstat(stream.fileno())) == inventory["identities"][name],
                "original JSON replaced before parse")
        raw = stream.read(item["bytes"] + 1)
        require(archive_identity(os.fstat(stream.fileno())) == inventory["identities"][name],
                "original JSON changed during parse")
    require(len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"],
            "original JSON changed during parse")
    return json.loads(raw)


def archive_closed_bindings(root, inventory, source):
    for tool in ("rustc", "cargo", "node", "npm", "python"):
        identity = inventory["entries"].get("tools/" + tool + ".txt", {})
        require(identity.get("kind") == "file" and identity.get("bytes", 0) > 0,
                "archive tool identity missing")
    receipt = archive_json(root, inventory, "e/receipt.json")
    require(receipt.get("status") == "PASS", "unsealed failed or incomplete service execution")
    require(receipt.get("source_before") == receipt.get("source_after") == source
            and archive_json(root, inventory, "e/source-before.json") == source,
            "archive source binding differs")
    plan = receipt.get("selection_plan", {})
    require(plan == selection_plan(plan.get("requested")), "archive selection binding differs")
    required = selected_selections(plan["requested"])
    selections = receipt.get("selections", [])
    require([x.get("label") for x in selections] == [x[0] for x in required],
            "archive selected roster differs")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for field, variable in (("run_id", "GITHUB_RUN_ID"), ("run_attempt", "GITHUB_RUN_ATTEMPT"),
                                ("job", "GITHUB_JOB")):
            require(receipt.get(field) == os.environ.get(variable) and receipt.get(field),
                    "archive run identity differs")

    def command_closed(command, label):
        require(type(command.get("exit_code")) is int and command["exit_code"] == 0
                and command.get("diagnostic_custody") == "DIRECT_PROCESS_EXIT_OBSERVED"
                and not command.get("observation"),
                "unsealed command or unobserved descendants")
        require(archive_json(root, inventory, "e/" + label + ".command.json") == command,
                "archive original command differs")
        for stream in ("stdout", "stderr"):
            item = inventory["entries"].get("e/" + label + "." + stream + ".log", {})
            require(item.get("kind") == "file"
                    and command.get(stream) == {k: item.get(k) for k in ("bytes", "sha256")},
                    "archive original stream differs")

    command_closed(receipt.get("build", {}), "build")
    for selection, (_, filename, titles) in zip(selections, required):
        label = selection["label"]
        command_closed(selection.get("command", {}), label)
        original = inventory["entries"].get("e/" + label + ".json", {})
        require(selection.get("status") == "PASS" and selection.get("expected_tests") == len(titles)
                and selection.get("original_report") == {k: original.get(k) for k in ("bytes", "sha256")},
                "archive report binding differs")
        require(selection.get("verified") == validate_report(
            archive_json(root, inventory, "e/" + label + ".json"), filename, titles),
            "archive original selected results differ")
    binary = receipt.get("retained_service_binary", {})
    require(binary.get("path") in ("executed-studio-service", "executed-studio-service.exe"),
            "archive retained executable name differs")
    retained = inventory["entries"].get("e/" + binary["path"], {})
    require(retained.get("kind") == "file"
            and {k: retained.get(k) for k in ("bytes", "sha256")} == {k: binary.get(k) for k in ("bytes", "sha256")}
            == {k: receipt.get("service_binary", {}).get(k) for k in ("bytes", "sha256")},
            "archive retained executable differs")
    captures = []
    for name in inventory["entries"]:
        if not name.endswith("/manifest.json"):
            continue
        manifest = archive_json(root, inventory, name)
        if not any(key in manifest for key in ("entries", "snapshotDirectory", "snapshotSha256")):
            continue
        entries = manifest.get("entries")
        require(manifest.get("schemaVersion") == 1 and isinstance(entries, list)
                and 0 < len(entries) <= ARCHIVE_LIMITS["members"], "invalid closed-store manifest")
        aggregate = hashlib.sha256(json.dumps(entries, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        require(manifest.get("sourceBeforeSha256") == manifest.get("sourceAfterSha256")
                == manifest.get("snapshotSha256") == aggregate, "closed-store aggregate differs")
        prefix = name.rsplit("/", 1)[0] + "/store"
        require(Path(manifest.get("snapshotDirectory", "")) == root / prefix,
                "closed-store snapshot path differs")
        actual = {}
        for member_name, item in inventory["entries"].items():
            if member_name != prefix and not member_name.startswith(prefix + "/"):
                continue
            relative = member_name[len(prefix):].lstrip("/")
            entry = {"path": relative, "kind": item["kind"]}
            if item["kind"] == "file":
                entry.update(sizeBytes=item["bytes"], sha256=item["sha256"])
            actual[relative] = entry
        require(all(isinstance(e, dict) and isinstance(e.get("path"), str) for e in entries),
                "closed-store entry malformed")
        expected = {e["path"]: e for e in entries}
        require(len(expected) == len(entries) and actual == expected,
                "complete closed-store files/directories differ")
        database = archive_member_name(prefix + "/" + manifest.get("databaseRelativePath", ""))
        require(inventory["entries"].get(database, {}).get("kind") == "file",
                "closed-store database missing")
        lifecycle = manifest.get("lifecycle")
        require(isinstance(lifecycle, list) and lifecycle, "closed-store lifecycle missing")
        for process in lifecycle:
            exits = process.get("exits")
            require(process.get("taints") == [] and isinstance(exits, list) and exits
                    and all(type(x.get("processId")) is int and x["processId"] > 0
                            and type(x.get("code")) is int and x["code"] == 0
                            and x.get("signal") is None for x in exits),
                    "unsealed closed-store process lifecycle")
        captures.append({"manifest": name, "sha256": inventory["entries"][name]["sha256"],
                         "snapshot_sha256": aggregate, "entries": len(entries),
                         "evidence_scope": manifest.get("evidenceScope")})
    return {"receipt": {k: inventory["entries"]["e/receipt.json"][k] for k in ("bytes", "sha256")},
            "selection_plan": plan, "captures": captures}


def write_closed_tar(root, target, inventory):
    with target.open("xb") as stream:
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, item in inventory["entries"].items():
                path = root / name
                require(archive_identity(archive_stat(path)) == inventory["identities"][name],
                        "archive source changed before copy")
                info = tarfile.TarInfo(name)
                info.mode, info.mtime = item["mode"], 0
                info.type = tarfile.DIRTYPE if item["kind"] == "directory" else tarfile.REGTYPE
                if item["kind"] == "directory":
                    archive.addfile(info)
                else:
                    archive_ancestors(path)
                    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                    with os.fdopen(os.open(path, flags), "rb") as original:
                        require(archive_identity(os.fstat(original.fileno())) == inventory["identities"][name],
                                "archive source replaced before copy")
                        info.size = item["bytes"]
                        archive.addfile(info, original)
                        require(archive_identity(os.fstat(original.fileno())) == inventory["identities"][name],
                                "archive source changed during copy")
        stream.flush()
        os.fsync(stream.fileno())



def archive_header_bounds(path):
    """Check physical TAR headers before tarfile can allocate extended metadata."""
    last_end, count, pending_pax = 0, 0, False
    with path.open("rb") as stream:
        while True:
            header = stream.read(512)
            require(len(header) == 512, "truncated archive header")
            if header == bytes(512):
                require(not pending_pax and stream.read(512) == bytes(512), "invalid archive end marker")
                tail = stream.read(11265)
                require(len(tail) <= 10240 and not any(tail) and not stream.read(1),
                        "archive trailing data exceeds bound")
                return last_end
            member = tarfile.TarInfo.frombuf(header, encoding="utf-8", errors="strict")
            count += 1
            require(count <= ARCHIVE_LIMITS["members"] * 2 and member.size >= 0,
                    "archive physical header bound")
            if member.type == tarfile.XHDTYPE:
                require(not pending_pax and member.size <= 4096, "archive extended metadata exceeds bound")
                pending_pax = True
            else:
                require(member.type in (tarfile.DIRTYPE, tarfile.REGTYPE)
                        and member.size <= ARCHIVE_LIMITS["file_bytes"]
                        and (member.type != tarfile.DIRTYPE or member.size == 0),
                        "archive physical member type or size")
                pending_pax = False
            stream.seek(((member.size + 511) // 512) * 512, 1)
            require(stream.tell() <= path.stat().st_size, "truncated archive payload")
            last_end = stream.tell()


def verify_closed_tar(path, inventory):
    archive_ancestors(path)
    before = archive_identity(archive_stat(path))
    size = archive_stat(path).st_size
    require(size <= ARCHIVE_LIMITS["total_bytes"] + ARCHIVE_LIMITS["members"] * 4096 + 11264,
            "archive envelope exceeds bound")
    physical_end = archive_header_bounds(path)
    expected, seen, folded, total, last_end = inventory["entries"], set(), set(), 0, 0
    with tarfile.open(path, mode="r|") as archive:
        for member in archive:
            name = archive_member_name(member.name)
            require(len(seen) < ARCHIVE_LIMITS["members"] and name not in seen
                    and name.casefold() not in folded and name in expected,
                    "duplicate, unexpected or excessive archive member")
            seen.add(name)
            folded.add(name.casefold())
            item = expected[name]
            require(member.type == (tarfile.DIRTYPE if item["kind"] == "directory" else tarfile.REGTYPE)
                    and not member.linkname and member.sparse is None
                    and set(member.pax_headers) <= {"path"},
                    "archive member type or extension differs")
            require(member.mode == item["mode"] and member.uid == member.gid == 0
                    and member.uname == member.gname == "" and member.mtime == 0,
                    "archive member metadata differs")
            require(member.size == item.get("bytes", 0) and member.size <= ARCHIVE_LIMITS["file_bytes"],
                    "archive member size differs")
            if item["kind"] == "file":
                value, count = hashlib.sha256(), 0
                with archive.extractfile(member) as original:
                    while block := original.read(min(ARCHIVE_CHUNK, member.size - count + 1)):
                        count += len(block)
                        require(count <= member.size, "archive member grew beyond declared size")
                        value.update(block)
                require(count == member.size and value.hexdigest() == item["sha256"],
                        "archive member digest differs")
                total += count
                require(total <= ARCHIVE_LIMITS["total_bytes"], "archive total byte bound")
            last_end = member.offset_data + ((member.size + 511) // 512) * 512
    require(seen == set(expected) and total == inventory["file_bytes"],
            "archive complete member structure differs")
    require(physical_end == last_end and 1024 <= size - last_end <= 11264 and (size - last_end) % 512 == 0,
            "archive end marker or padding differs")
    with path.open("rb") as stream:
        stream.seek(last_end)
        require(not any(stream.read()), "archive trailing nonzero data")
    observed = archive_file_digest(path, ARCHIVE_LIMITS["total_bytes"] + ARCHIVE_LIMITS["members"] * 4096 + 11264)
    require(archive_identity(archive_stat(path)) == before, "archive changed during verification")
    return {"members": len(seen), "file_bytes": total, "complete_structure": True, "archive_digest": observed}


def retain_closed_archive(repo, root):
    output = root / "retained"
    archive_ancestors(output)
    require(not output.exists(), "fresh archive output directory required")
    output.mkdir()
    report = {"schema": "motionwright.studio-service-archive/1", "status": "FAIL",
              "custody": "UNSEALED_DIAGNOSTICS", "source": None,
              "human_acceptance": False, "formal_acceptance": False,
              "scope": "Original e/tools tree, direct command exits and declared helper closures; no additional app reopen"}
    try:
        source = source_observation(repo)
        report["source"] = source
        before = archive_inventory(root)
        report["bindings"] = archive_closed_bindings(root, before, source)
        write_new(output / "input-inventory.json", before)
        archive = output / "closed-evidence.tar"
        write_closed_tar(root, archive, before)
        report["verified"] = verify_closed_tar(archive, before)
        require(archive_inventory(root) == before, "original evidence changed during retention")
        require(source_observation(repo) == source, "source changed during retention")
        require(archive_file_digest(archive, ARCHIVE_LIMITS["total_bytes"] + ARCHIVE_LIMITS["members"] * 4096 + 11264)
                == report["verified"]["archive_digest"], "retained archive changed")
        report["archive"] = {"path": archive.name, **report["verified"]["archive_digest"]}
        report["input_inventory"] = digest(output / "input-inventory.json")
        report["originals_equal_before_after"] = True
        report["status"], report["custody"] = "PASS", "VERIFIED_ORIGINAL_TREE_COPY"
    except Exception as error:
        report["error_type"], report["error"] = type(error).__name__, str(error)
    write_new(output / "archive-receipt.json", report)
    return report


def archive_main(arguments):
    parser = argparse.ArgumentParser(description="Retain the complete synthetic CI evidence tree in a verified TAR.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(arguments)
    repo, root = (Path(os.path.abspath(p)) for p in (args.repo, args.evidence))
    require(os.environ.get("GITHUB_ACTIONS") == "true", "archive command is restricted to its synthetic CI job")
    require(repo.name == "g" and root == repo.parent / "v"
            and Path(os.environ.get("GITHUB_WORKSPACE", "")) == repo.parent,
            "archive command requires the isolated CI g/v layout")
    report = retain_closed_archive(repo, root)
    print(json.dumps({"status": report["status"], "custody": report["custody"],
                      "receipt": str(root / "retained/archive-receipt.json")}))
    return 0 if report["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--selection", default="all", choices=["all", *SELECTION_GROUPS, *(row[0] for row in SELECTIONS)])
    args = parser.parse_args()
    selections = selected_selections(args.selection)
    repo, output, target = (p.resolve() for p in (args.repo, args.output, args.target))
    require(repo.is_dir(), "repository missing")
    require(not output.exists() and not target.exists(), "fresh evidence and build directories required")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "motionwright.canvas-service-ci/1", "status": "FAIL",
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "job": os.environ.get("GITHUB_JOB"), "selections": [],
              "selection_plan": selection_plan(args.selection),
              "installed_tauri": "NOT_RUN", "native_render": "NOT_RUN", "human_acceptance": "NOT_RUN"}
    try:
        before = source_observation(repo)
        report["source_before"] = before
        write_new(output / "source-before.json", before)
        cargo, node = shutil.which("cargo"), shutil.which("node")
        require(cargo is not None and node is not None, "cargo and node are required")
        env = dict(os.environ)
        env["CARGO_TARGET_DIR"] = str(target)
        build = execute([cargo, "build", "--locked", "-p", "motionwright-service",
                         "--example", "studio_review_session"], repo, env, output, "build", 1200)
        report["build"] = build
        require(build["exit_code"] == 0, "real service build failed")
        binary = target / "debug/examples/studio_review_session"
        if os.name == "nt":
            binary = binary.with_suffix(".exe")
        report["service_binary"] = {"path": str(binary), **digest(binary)}
        retained_binary = output / ("executed-studio-service.exe" if os.name == "nt" else "executed-studio-service")
        shutil.copyfile(binary, retained_binary)
        report["retained_service_binary"] = {"path": retained_binary.name, **digest(retained_binary)}
        require(digest(retained_binary) == digest(binary), "retained service binary differs")
        require(source_observation(repo) == before, "source changed during service compilation")
        cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
        report["playwright_cli"] = digest(cli)
        report["node"] = {"path": node, **digest(Path(node))}
        for label, filename, titles in selections:
            case_env = dict(env)
            stores = output / (label + "-stores")
            stores.mkdir()
            case_env["MOTIONWRIGHT_SERVICE_TEST_BIN"] = str(binary)
            case_env["MOTIONWRIGHT_SERVICE_TEST_TMPDIR"] = str(stores)
            case_env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(output / (label + ".json"))
            argv = [node, str(cli), "test", "tests/" + filename, "--project=chromium",
                    "--workers=1", "--retries=0", "--reporter=json",
                    "--output=" + str(output / (label + "-test-results"))]
            if label == "ink":
                argv += ["--grep", re.escape(INK_TITLE) + "$"]
            command = execute(argv, repo / "apps/studio", case_env, output, label,
                              SELECTION_TIMEOUTS[label])
            selection = {"label": label, "expected_tests": len(titles), "command": command, "status": "FAIL"}
            report["selections"].append(selection)
            try:
                result_path = output / (label + ".json")
                selection["original_report"] = digest(result_path)
                require(result_path.stat().st_size <= 64 * 1024 * 1024, "report exceeds bound")
                document = json.loads(result_path.read_bytes())
                selection["verified"] = validate_report(document, filename, titles)
                require(command["exit_code"] == 0, "selected process did not exit zero")
                selection["status"] = "PASS"
            except Exception as error:
                selection["error"] = str(error)
            if command["exit_code"] is None:
                break
        report["source_after"] = source_observation(repo)
        require(report["source_after"] == before, "source changed during browser execution")
        require(digest(binary) == {k: report["service_binary"][k] for k in ("bytes", "sha256")},
                "service executable changed")
        require(digest(cli) == report["playwright_cli"], "Playwright CLI changed")
        require(digest(retained_binary) == digest(binary), "retained service binary changed")
        require(len(report["selections"]) == len(selections)
                and all(x["status"] == "PASS" for x in report["selections"]), "required selection failed")
        report["status"] = "PASS"
    except Exception as error:
        report["error"] = str(error)
    write_new(output / "receipt.json", report)
    print(json.dumps({"status": report["status"],
                      "selected_tests": sum(x["expected_tests"] for x in report["selections"]),
                      "receipt": str(output / "receipt.json")}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(archive_main(sys.argv[2:]) if sys.argv[1:2] == ["archive"] else main())
