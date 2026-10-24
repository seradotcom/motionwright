"""Portable frontend adapter regressions: real private Git, local TAR/SRI only."""
from __future__ import annotations
import base64, copy, gzip, hashlib, io, json, os, shutil, subprocess, tarfile, tempfile, unittest
from pathlib import Path
from unittest import mock
import candidate_frontend_authentication as auth
import candidate_dependency_capture as capture

INDEX_NAME = "assets/licenses/frontend/index.json"
NOTICE = b"FIXTURE AUTHENTICITY TEXT ONLY; NOT A DISTRIBUTION LICENSE\n"
LOCAL = b"export const local = 1;\n"
NPM = b"export const fixture = 1;\n"

def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()

def descriptor(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

def fixture_archive():
    package = encoded({"name": "fixture", "version": "1.0.0", "license": "MIT"})
    files = {"package.json": package, "index.js": NPM, "LICENSE": NOTICE}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, raw in sorted(files.items()):
            member = tarfile.TarInfo("package/" + name)
            member.size, member.mtime = len(raw), 0
            archive.addfile(member, io.BytesIO(raw))
    archive = gzip.compress(stream.getvalue(), mtime=0)
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive).digest()).decode()
    entry = {"version": "1.0.0", "resolved": "https://registry.npmjs.org/fixture/-/fixture-1.0.0.tgz",
             "integrity": integrity}
    lock = encoded({"lockfileVersion": 3, "packages": {"node_modules/fixture": entry}})
    return lock, archive, package, entry

class Fixture:
    def __init__(self, root):
        self.root, self.repo = root, root / "repo"
        self.repo.mkdir()
        self.out, self.archive_root = self.repo / "dist", root / "archives"
        self.archive_root.mkdir()
        self.out.mkdir()
        (self.repo / "receipts").mkdir()
        self.lock, archive, package, entry = fixture_archive()
        (self.archive_root / "fixture.tgz").write_bytes(archive)
        inputs = {"main.js": LOCAL, "package-lock.json": self.lock,
                  "package.json": encoded({"name": "fixture-app", "private": True}),
                  ".gitignore": b"dist/\nreceipts/\n"}
        for name, raw in inputs.items():
            (self.repo / name).write_bytes(raw)
        self.git("init", "-q")
        for key, value in (("user.name", "Fixture"), ("user.email", "fixture@example.invalid"),
                           ("core.autocrlf", "false"), ("commit.gpgsign", "false"),
                           ("core.hooksPath", str(root / "absent-hooks")),
                           ("core.attributesFile", os.devnull)):
            self.git("config", key, value)
        self.git("add", "--", *sorted(inputs))
        self.git("commit", "-qm", "private fixture")
        self.head = self.git("rev-parse", "HEAD").decode().strip()
        self.before = capture.source_snapshot(self.repo, self.head)
        records = {r["path"]: r for r in self.before["files"]}
        local_id, npm_id, virtual_id = [hashlib.sha256(v).hexdigest()
                                         for v in (b"local", b"npm", b"virtual")]
        self.local_id, self.virtual_id = local_id, virtual_id
        component_id = "npm:fixture@1.0.0#fixture"
        modules = [
            {"module_id_sha256": local_id, "kind": "local", "path": "main.js",
             "component_id": None, "source_path_in_package": None,
             "raw_source": {k: records["main.js"][k] for k in ("bytes", "sha256", "git_blob")},
             "rollup_transformed_code": descriptor(LOCAL), "npm_license_evidence": False,
             "virtual_origin": None},
            {"module_id_sha256": npm_id, "kind": "npm", "path": "node_modules/fixture/index.js",
             "component_id": component_id, "source_path_in_package": "index.js",
             "raw_source": descriptor(NPM), "rollup_transformed_code": descriptor(NPM),
             "npm_license_evidence": True, "virtual_origin": None},
            {"module_id_sha256": virtual_id, "kind": "virtual", "path": None,
             "component_id": None, "source_path_in_package": None, "raw_source": None,
             "rollup_transformed_code": descriptor(b"generated"),
             "npm_license_evidence": False, "virtual_origin": "NOT_VERIFIED"},
        ]
        notice_path = "assets/licenses/frontend/texts/" + descriptor(NOTICE)["sha256"] + ".txt"
        self.assets = {"assets/app.js": b"/* fixture output, never executed */\n",
                       "index.html": b"<!doctype html>\n", notice_path: NOTICE}
        self.index = {
            "schema": "motionwright.frontend-notices/1",
            "completion": "OBSERVED_RENDERED_JS_MODULE_CLOSURE_ONLY",
            "full_package_inventory": "NOT_VERIFIED",
            "non_chunk_asset_dependency_closure": "NOT_VERIFIED",
            "redistribution_permission": "NOT_VERIFIED",
            "source_binding": {"mode": "EXACT_HEAD_AND_TRACKED_BLOBS", "source_sha": self.head,
                "tracked_inputs": [{k: row[k] for k in ("path", "bytes", "sha256", "git_blob")}
                                   for row in self.before["files"]]},
            "package_lock": {"path": "package-lock.json", **descriptor(self.lock)},
            "modules": modules,
            "original_outputs": [
                {"path": "assets/app.js", "kind": "chunk", **descriptor(self.assets["assets/app.js"]),
                 "modules": [{"module_id_sha256": row["module_id_sha256"], "rollup_rendered_length": 1}
                             for row in modules]},
                {"path": "index.html", "kind": "asset", **descriptor(self.assets["index.html"])}],
            "components": [{"id": component_id, "name": "fixture", "version": "1.0.0",
                "role": "frontend-emitted", "lock_key": "node_modules/fixture",
                "source": {"resolved": entry["resolved"], "integrity": entry["integrity"]},
                "package_metadata": {"path": "node_modules/fixture/package.json",
                                     **descriptor(package), "license_declaration": "MIT"},
                "notices": [{"source_path_in_package": "LICENSE", "asset": notice_path,
                             **descriptor(NOTICE), "evidence_kind": "license-text"}]}],
            "generated_notice_assets": [{"path": notice_path, **descriptor(NOTICE)}],
        }
        for name, raw in self.assets.items():
            target = self.out / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        self.write_index()

    def git(self, *args):
        env = dict(os.environ)
        for key in list(env):
            if key.startswith("GIT_"):
                env.pop(key)
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
        result = subprocess.run(["git", "-C", str(self.repo), *args],
                                capture_output=True, env=env, timeout=15)
        if result.returncode:
            raise RuntimeError("Git fixture setup failed: " + result.stderr.decode(errors="replace"))
        return result.stdout

    def write_index(self):
        (self.out / INDEX_NAME).write_bytes(encoded(self.index))

    def local(self):
        return next(row for row in self.index["modules"] if row["kind"] == "local")

    def run(self, receipt="receipt.json"):
        return auth.authenticate_build(repo=self.repo, expected_source=self.head,
                   output_directory=self.out, archive_root=self.archive_root,
                   archive_map={"node_modules/fixture": "fixture.tgz"},
                   receipt_path=self.repo / "receipts" / receipt)

    def pure(self):
        return auth.authenticate_frontend(encoded(self.index), self.lock,
                   {"node_modules/fixture": (self.archive_root / "fixture.tgz").read_bytes()}, self.assets)

@unittest.skipUnless(shutil.which("git"), "real private Git fixture is required")
class FrontendAdapterTests(unittest.TestCase):
    def setUp(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
        isolated = mock.patch.dict(os.environ, env, clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="mw-front-auth-")
        self.addCleanup(self.temp.cleanup)
        self.f = Fixture(Path(self.temp.name).resolve())

    def rejected(self, code, action, receipt="receipt.json"):
        with self.assertRaises(auth.FrontendEvidenceError) as caught:
            action()
        self.assertEqual(str(caught.exception), code)
        self.assertFalse((self.f.repo / "receipts" / receipt).exists())

    def source_unchanged(self):
        self.assertEqual(capture.source_snapshot(self.f.repo, self.f.head), self.f.before)

    def test_local_module_omitted_from_tracked_inputs_is_rejected(self):
        self.f.index["source_binding"]["tracked_inputs"] = [
            row for row in self.f.index["source_binding"]["tracked_inputs"] if row["path"] != "main.js"]
        self.f.write_index()
        self.assertEqual((self.f.repo / "main.js").read_bytes(), LOCAL)
        caught = None
        try:
            self.f.run()
        except auth.FrontendEvidenceError as error:
            caught = error
        self.source_unchanged()
        self.assertIsInstance(caught, auth.FrontendEvidenceError)
        self.assertEqual(str(caught), "local_module_tracked_input_required")
        self.assertFalse((self.f.repo / "receipts" / "receipt.json").exists())

    def test_asset_added_after_initial_enumeration_is_rejected(self):
        original = auth.authenticate_frontend
        late = self.f.out / "late-added.bin"
        calls = []
        def authenticate_then_add(*args, **kwargs):
            result = original(*args, **kwargs)
            late.write_bytes(b"late real bytes")
            calls.append(True)
            return result
        caught = None
        with mock.patch.object(auth, "authenticate_frontend", side_effect=authenticate_then_add):
            try:
                self.f.run()
            except auth.FrontendEvidenceError as error:
                caught = error
        self.assertEqual(calls, [True])
        self.assertEqual(late.read_bytes(), b"late real bytes")
        self.source_unchanged()
        self.assertIsInstance(caught, auth.FrontendEvidenceError)
        self.assertEqual(str(caught), "emitted_asset_set_changed")
        self.assertFalse((self.f.repo / "receipts" / "receipt.json").exists())

    def test_local_raw_bytes_digest_and_blob_must_match_the_same_tracked_input(self):
        original = copy.deepcopy(self.f.local())
        for key, value in (("bytes", len(LOCAL) + 1), ("sha256", "0"*64), ("git_blob", "0"*40)):
            with self.subTest(field=key):
                self.f.local().update(copy.deepcopy(original))
                self.f.local()["raw_source"][key] = value
                self.f.write_index()
                self.rejected("local_module_source_mismatch", self.f.run)
        self.source_unchanged()

    def test_npm_local_and_virtual_authentication_scopes_are_separate(self):
        pure = self.f.pure()
        self.assertNotIn("observed_js_source_and_notice_authentication", pure)
        self.assertEqual(pure["observed_npm_source_and_notice_authentication"], "VERIFIED")
        self.assertEqual(pure["local_module_source_authentication"], "WRAPPER_VALIDATION_REQUIRED")
        self.assertEqual(pure["virtual_module_origin_authentication"], "NOT_VERIFIED")
        self.assertEqual(pure["virtual_module_ids"], [self.f.virtual_id])
        report = self.f.run()
        self.assertEqual(report["local_module_source_authentication"],
                         "EXACT_TRACKED_GIT_RAW_SOURCE_BYTES_VERIFIED")
        self.assertEqual(report["local_modules"], [{"module_id_sha256": self.f.local_id,
            "path": "main.js", **self.f.local()["raw_source"]}])
        self.assertEqual(report["virtual_module_origin_authentication"], "NOT_VERIFIED")
        self.assertEqual(report["components"][0]["source_authentication"]["status"],
                         "LOCKED_ARCHIVE_AND_OBSERVED_SOURCE_BYTES_VERIFIED")
        self.assertEqual(report["full_package_inventory"], "NOT_VERIFIED")
        self.assertEqual(report["redistribution_permission"], "NOT_INFERRED")
        self.source_unchanged()

    def test_existing_asset_changed_after_authentication_is_rejected(self):
        original = auth.authenticate_frontend
        target = self.f.out / "index.html"
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            raw = target.read_bytes()
            target.write_bytes(raw[:-1] + b"X")
            return result
        with mock.patch.object(auth, "authenticate_frontend", side_effect=mutate):
            self.rejected("emitted_asset_changed", self.f.run)
        self.source_unchanged()

    def test_empty_directories_and_index_consume_the_entry_budget(self):
        self.assertEqual(auth.MAX_EMITTED_ENTRIES, 100000)
        count = sum(1 for _ in self.f.out.rglob("*"))
        for name in ("empty-a", "empty-b"):
            (self.f.out / name).mkdir()
        with mock.patch.object(auth, "MAX_EMITTED_ENTRIES", count + 2):
            self.f.run("at-limit.json")
            (self.f.out / "empty-c").mkdir()
            self.rejected("emitted_entry_count_budget",
                          lambda: self.f.run("over-limit.json"), "over-limit.json")

    def test_directory_depth_32_is_admitted_and_33_rejected(self):
        self.assertEqual(auth.MAX_EMITTED_DEPTH, 32)
        target = self.f.out
        for _ in range(32):
            target /= "d"
            target.mkdir()
        self.f.run("depth32.json")
        (target / "d").mkdir()
        self.rejected("emitted_path_depth_budget", lambda: self.f.run("depth33.json"), "depth33.json")

    def test_total_byte_budget_includes_the_separate_index(self):
        self.assertEqual(auth.MAX_EMITTED_BYTES, 128 * 1024 * 1024)
        count = sum(path.stat().st_size for path in self.f.out.rglob("*") if path.is_file())
        with mock.patch.object(auth, "MAX_EMITTED_BYTES", count):
            self.f.run("exact-bytes.json")
        with mock.patch.object(auth, "MAX_EMITTED_BYTES", count - 1):
            self.rejected("emitted_asset_budget", lambda: self.f.run("over-bytes.json"), "over-bytes.json")

    def test_index_changed_after_authentication_is_rejected(self):
        original = auth.authenticate_frontend
        target = self.f.out / INDEX_NAME
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            target.write_bytes(target.read_bytes() + b" ")
            return result
        with mock.patch.object(auth, "authenticate_frontend", side_effect=mutate):
            self.rejected("index_changed_during_authentication", self.f.run)

    def test_post_authentication_source_change_is_detected_by_real_git_snapshot(self):
        original = auth.authenticate_frontend
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.f.repo / "main.js").write_bytes(LOCAL.replace(b"1", b"2"))
            return result
        with mock.patch.object(auth, "authenticate_frontend", side_effect=mutate):
            with self.assertRaises(capture.CaptureError) as caught:
                self.f.run()
        self.assertEqual(str(caught.exception), "tracked_bytes_differ:main.js")
        self.assertFalse((self.f.repo / "receipts" / "receipt.json").exists())

    def test_output_symlink_is_rejected_without_reading_its_target(self):
        outside = self.f.root / "outside.bin"
        outside.write_bytes(b"outside")
        link = self.f.out / "link.bin"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest("Physical symlink capability unavailable: " + type(error).__name__)
        with self.assertRaises(capture.CaptureError) as caught:
            self.f.run()
        self.assertEqual(str(caught.exception), "redirected_path")
        self.assertEqual(outside.read_bytes(), b"outside")
        self.assertFalse((self.f.repo / "receipts" / "receipt.json").exists())

    def test_receipt_stays_detached_and_existing_receipt_is_never_overwritten(self):
        self.rejected("detached_receipt_required", lambda: self.f.run(self.f.out / "receipt.json"))
        self.assertFalse((self.f.out / "receipt.json").exists())
        target = self.f.repo / "receipts" / "existing.json"
        target.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            self.f.run("existing.json")
        self.assertEqual(target.read_bytes(), b"original")

if __name__ == "__main__":
    unittest.main(verbosity=2)
