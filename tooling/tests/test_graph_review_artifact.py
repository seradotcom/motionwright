"""Raw Graph artifact handoff tests; fixtures never contain private keys."""
import copy
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class GraphReviewArtifactTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("graph_review_artifact"),
                             "raw Graph artifact transport is missing")
        self.transport = importlib.import_module("graph_review_artifact")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Windows hosted runners may expose TEMP through a DOS 8.3 alias.
        # Canonicalize this inert fixture, not the production custody guard.
        self.output = Path(self.temp.name).resolve(strict=True) / "private-transport"
        self.assertEqual(self.output.parent, self.output.parent.resolve(strict=True))
        self.source = "a" * 40
        self.run_id = "42400011223"
        self.name = "graph-originals-review-" + self.source + "-" + self.run_id + "-1"
        repo = {"id": 1234, "full_name": "seradotcom/motionwright"}
        self.run = {"id": int(self.run_id), "run_attempt": 1,
                    "head_sha": self.source, "event": "workflow_dispatch",
                    "path": ".github/workflows/studio-service-verification.yml",
                    "workflow_id": 321, "status": "in_progress",
                    "repository": repo, "head_repository": copy.deepcopy(repo)}
        self.archive = self.zip_bytes(["producer-originals.mwseal", "carrier.original.json"])
        self.item = {"id": 876, "name": self.name, "expired": False,
                     "size_in_bytes": len(self.archive),
                     "digest": "sha256:" + hashlib.sha256(self.archive).hexdigest(),
                     "workflow_run": {"id": int(self.run_id), "head_sha": self.source,
                                      "repository_id": 1234, "head_repository_id": 1234}}
        self.rows = [self.item]
        self.calls = []
        self.changed_run = None
        self.changed_item = None

    def zip_bytes(self, names, symlink=False):
        out = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(out, "w") as archive:
                for name in names:
                    row = zipfile.ZipInfo(name)
                    row.create_system = 3
                    row.external_attr = ((stat.S_IFLNK if symlink else stat.S_IFREG) | 0o600) << 16
                    archive.writestr(row, b"opaque retained bytes")
        return out.getvalue()

    def api(self, route, label, directory):
        self.calls.append(route)
        prefix = "repos/seradotcom/motionwright/actions/"
        if route == prefix + "runs/" + self.run_id:
            value = self.changed_run if self.changed_run is not None and self.calls.count(route) > 1 else self.run
        elif route == prefix + "runs/" + self.run_id + "/artifacts?per_page=100&page=1":
            value = {"total_count": len(self.rows), "artifacts": self.rows}
        elif route == prefix + "artifacts/876":
            value = self.changed_item if self.changed_item is not None and self.calls.count(route) > 1 else self.item
        elif route == prefix + "artifacts/876/zip":
            (directory / (label + ".stdout")).write_bytes(self.archive)
            return self.archive
        else:
            raise AssertionError("unexpected API route " + route)
        raw = json.dumps(value).encode()
        (directory / (label + ".stdout")).write_bytes(raw)
        return raw

    def download(self):
        with mock.patch.object(self.transport, "gh_json_or_bytes", side_effect=self.api):
            return self.transport.download(self.source, self.run_id, self.output)

    def assert_rejected(self, before_zip=False):
        with self.assertRaises(ValueError):
            self.download()
        self.assertFalse((self.output / "transport-result.original.json").exists())
        if before_zip:
            self.assertFalse(any(route.endswith("/zip") for route in self.calls))

    def test_api_uses_installed_gh_and_retains_exact_response(self):
        import subprocess
        executable = str(Path(self.temp.name) / "installed gh.exe")
        response = b"PK\x03\x04raw original response"
        directory = self.output.parent / "api"
        directory.mkdir()
        route = "repos/seradotcom/motionwright/actions/artifacts/876/zip"

        expected_timeout = 300
        def execute(argv, **kwargs):
            self.assertEqual(argv, [executable, "api", route])
            self.assertEqual(kwargs["timeout"], expected_timeout)
            self.assertFalse(kwargs.get("shell", False))
            kwargs["stdout"].write(response)
            kwargs["stderr"].write(b"retained diagnostic")
            return subprocess.CompletedProcess(argv, 0)

        with mock.patch("shutil.which", return_value=executable) as which, \
                mock.patch("subprocess.run", side_effect=execute):
            raw = self.transport.gh_json_or_bytes(route, "api-control", directory)
        which.assert_called_once_with("gh")
        self.assertEqual(raw, response)
        self.assertEqual((directory / "api-control.stdout").read_bytes(), response)
        self.assertEqual((directory / "api-control.stderr").read_bytes(), b"retained diagnostic")
        intent = json.loads((directory / "api-control.intent.json").read_bytes())
        self.assertEqual(intent["argv"], [executable, "api", route])
        self.assertEqual(json.loads((directory / "api-control.exit.json").read_bytes())["exit_code"], 0)
        with mock.patch("shutil.which", return_value=executable), \
                mock.patch("subprocess.run") as run:
            with self.assertRaises((ValueError, FileExistsError)):
                self.transport.gh_json_or_bytes(route, "api-control", directory)
            run.assert_not_called()
        # Only the immutable binary archive gets a larger transfer budget.
        route = "repos/seradotcom/motionwright/actions/runs/42400011223"
        expected_timeout = 120
        with mock.patch("shutil.which", return_value=executable), \
                mock.patch("subprocess.run", side_effect=execute):
            self.assertEqual(self.transport.gh_json_or_bytes(route, "metadata-control", directory), response)
        self.assertEqual(json.loads((directory / "metadata-control.intent.json").read_bytes())["timeout_seconds"], 120)
    def test_in_progress_retains_exact_raw_zip_and_api_originals(self):
        result = self.download()
        self.assertEqual((self.output / "artifact.original.zip").read_bytes(), self.archive)
        self.assertEqual(result["artifact_id"], 876)
        self.assertEqual(result["zip_sha256"], hashlib.sha256(self.archive).hexdigest())
        self.assertEqual(result["decision"], "TRANSPORT_VERIFIED_REQUIRES_INDEPENDENT_REVIEW")
        self.assertTrue((self.output / "run-before.stdout").is_file())
        self.assertTrue((self.output / "run-after.stdout").is_file())
        self.assertTrue((self.output / "transport-result.original.json").is_file())
        with self.assertRaises(ValueError):
            self.download()
        self.assertEqual((self.output / "artifact.original.zip").read_bytes(), self.archive)

    def test_foreign_retried_wrong_workflow_or_nonlive_run_rejected_before_zip(self):
        mutations = [("run_attempt", 2), ("head_sha", "b" * 40),
                     ("event", "push"), ("path", ".github/workflows/foreign.yml"),
                     ("status", "completed"), ("repository", {"id": 1234, "full_name": "foreign/repo"}),
                     ("head_repository", {"id": 987, "full_name": "foreign/repo"})]
        for field, value in mutations:
            with self.subTest(field=field):
                original = self.run[field]
                self.run[field] = value
                self.output = Path(self.temp.name) / field
                self.assert_rejected(before_zip=True)
                self.run[field] = original

    def test_bad_digest_rejected_after_raw_bytes_are_retained(self):
        self.item["digest"] = "sha256:" + "0" * 64
        self.assert_rejected()
        self.assertEqual((self.output / "artifact.original.zip").read_bytes(), self.archive)

    def test_duplicate_or_foreign_artifact_rejected_before_zip(self):
        for scenario in ("duplicate", "name", "run", "source", "repository", "expired", "size"):
            with self.subTest(scenario=scenario):
                item = copy.deepcopy(self.item)
                self.rows = [item]
                if scenario == "duplicate": self.rows.append(copy.deepcopy(item))
                if scenario == "name": item["name"] = "foreign"
                if scenario == "run": item["workflow_run"]["id"] += 1
                if scenario == "source": item["workflow_run"]["head_sha"] = "c" * 40
                if scenario == "repository": item["workflow_run"]["head_repository_id"] += 1
                if scenario == "expired": item["expired"] = True
                if scenario == "size": item["size_in_bytes"] = self.transport.MAX_ZIP + 1
                self.output = Path(self.temp.name) / scenario
                self.assert_rejected(before_zip=True)

    def test_duplicate_foreign_and_symlink_members_rejected(self):
        good = ["producer-originals.mwseal", "carrier.original.json"]
        for index, (names, link) in enumerate(((good + [good[0]], False),
                                             (good + ["foreign"], False), (good, True))):
            with self.subTest(index=index):
                self.archive = self.zip_bytes(names, symlink=link)
                self.item["size_in_bytes"] = len(self.archive)
                self.item["digest"] = "sha256:" + hashlib.sha256(self.archive).hexdigest()
                self.output = Path(self.temp.name) / str(index)
                self.assert_rejected()

    def test_changed_attempt_or_artifact_identity_rejected_after_download(self):
        self.changed_run = {**self.run, "run_attempt": 2}
        self.assert_rejected()
        self.changed_run = None
        self.calls = []
        self.output = Path(self.temp.name) / "changed-artifact"
        self.changed_item = {**self.item, "digest": "sha256:" + "f" * 64}
        self.assert_rejected()


if __name__ == "__main__":
    unittest.main()
