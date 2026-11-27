"""Admit KDE's pinned native installer for a disposable Actions editor run."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
from source_checkout_observation import git, regular_file, require

REPOSITORY = "seradotcom/motionwright"
WORKFLOW = ".github/workflows/v05-otio-real-media.yml"
JOB = "kdenlive-native-editor"
KDE_RELEASE = "https://download.kde.org/stable/kdenlive/26.08"
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class Distribution:
    folder: str
    filename: str
    sha256: str

    @property
    def url(self) -> str:
        return f"{KDE_RELEASE}/{self.folder}/{self.filename}"


DISTRIBUTIONS = {
    ("win32", "amd64", "Windows", "X64"): Distribution(
        "windows", "kdenlive-26.08.3.exe",
        "1f4027f287c3066887b1c112664b10c4e71a6772914fd5ea41074df429652186",
    ),
    ("darwin", "arm64", "macOS", "ARM64"): Distribution(
        "macOS", "kdenlive-26.08.3-arm64.dmg",
        "22b385f1e2e5bf45c5bb6954bd58a97db34cead219a9a9ae6faa499f0096d71e",
    ),
}


def selection(env, operating_system, machine):
    expected = {
        "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": REPOSITORY, "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": JOB,
    }
    require(all(env.get(name) == value for name, value in expected.items()),
            "only the original hosted Kdenlive editor job is admitted")
    run_id = env.get("GITHUB_RUN_ID", "")
    source = env.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[1-9][0-9]{0,19}", run_id)
            and re.fullmatch(r"[0-9a-f]{40}", source)
            and env.get("GITHUB_WORKFLOW_SHA") == source
            and env.get("GITHUB_WORKFLOW_REF", "").startswith(f"{REPOSITORY}/{WORKFLOW}@refs/heads/"),
            "native editor run/workflow identity differs")
    host = (operating_system, machine.lower(), env.get("RUNNER_OS"), env.get("RUNNER_ARCH"))
    distribution = DISTRIBUTIONS.get(host)
    require(distribution is not None, "unsupported native Kdenlive runner or architecture")
    return {"source_sha": source, "run_id": run_id, "run_attempt": "1",
            "runner_os": host[2], "runner_arch": host[3]}, distribution


def publish(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def acquire(runner, curl, context, distribution):
    require(runner.is_absolute() and runner.resolve(strict=True) == runner,
            "native editor runner root is not canonical")
    for ancestor in (runner, *runner.parents):
        observed = ancestor.lstat()
        require(stat.S_ISDIR(observed.st_mode)
                and not getattr(observed, "st_file_attributes", 0) & 0x400,
                "native editor runner root crosses a link or reparse point")
    owner = runner / f"{JOB}-{context['run_id']}-1"
    owner.mkdir(mode=0o700)
    archive = owner / distribution.filename
    record = {"context": context, "url": distribution.url, "archive": distribution.filename,
              "expected_sha256": distribution.sha256, "automatic_retry": False,
              "editor_launches": 0, "native_render_calls": 0}
    publish(owner / "download-intent.json", record)
    subprocess.run(
        [str(curl), "--disable", "--fail", "--silent", "--show-error", "--location",
         "--max-redirs", "4", "--proto", "=https", "--proto-redir", "=https",
         "--connect-timeout", "30", "--max-time", "240", "--max-filesize",
         str(MAX_ARCHIVE_BYTES), "--output", str(archive), distribution.url],
        cwd=owner, stdin=subprocess.DEVNULL, check=True, timeout=250,
    )
    original = regular_file(archive, MAX_ARCHIVE_BYTES)
    require(original["bytes"] > 0 and original["sha256"] == distribution.sha256,
            "native Kdenlive distribution digest differs")
    receipt = {**record, "schema": "motionwright.kdenlive-native-distribution/1",
               "status": "VERIFIED_OFFICIAL_ARCHIVE", "original": original}
    publish(owner / "distribution.json", receipt)
    return receipt


def main():
    context, distribution = selection(os.environ, sys.platform, platform.machine())
    require(git(ROOT, "rev-parse", "HEAD").decode("ascii").strip() == context["source_sha"],
            "native Kdenlive checkout differs from the workflow")
    runner = Path(os.environ["RUNNER_TEMP"])
    curl = (Path(os.environ["SystemRoot"]) / "System32/curl.exe"
            if sys.platform == "win32" else Path("/usr/bin/curl"))
    require(curl.is_absolute() and curl.resolve(strict=True) == curl,
            "native platform curl is not canonical")
    receipt = acquire(runner, curl, context, distribution)
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
