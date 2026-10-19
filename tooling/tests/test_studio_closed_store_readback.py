"""Reader contract tests with synthetic metadata; no Motionwright runner is used."""

from contextlib import closing
import copy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile


MODULE_PATH = Path(__file__).resolve().parents[1] / "studio_closed_store_readback.py"
SPEC = importlib.util.spec_from_file_location("studio_closed_store_readback", MODULE_PATH)
reader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reader
SPEC.loader.exec_module(reader)


def project(name="project", revision=2):
    return {"id": name, "generation": "generation", "active_branch": "main", "revision": revision,
            "title": "Previously observed unit fixture", "assets": [], "decision": {"approved": False, "value": 1}}


def history(value):
    return [{"project_id": value["id"], "revision": index + 1, "change": {"title": f"decision {index}"}}
            for index in range(value["revision"])]


def oracle(value):
    return {"schemaVersion": 1, "project": value, "journal": history(value),
            "journalAfterRevisionExclusive": 0, "journalComplete": True}


def response_bytes(checks, expected, *, listing=None):
    replies = []
    if listing is None:
        listing = {"items": [{"id": key} for key in expected], "next_cursor": None, "truncated": False}
    for request in checks:
        command = request["command"]
        args = request["args"]["request"]
        if command == "list_projects":
            result = listing
        elif command == "get_project":
            result = expected[args["project_id"]]["project"]
        else:
            result = [event for event in expected[args["project_id"]]["history"]
                      if args["after_revision"] < event["revision"] <= args["after_revision"] + args["limit"]]
        replies.append({"id": request["id"], "ok": True, "result": result})
    return b"".join(json.dumps(item).encode() + b"\n" for item in replies)


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="studio-readback-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.browser_report = self.root / "browser.json"
        self.specs = []

    def json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(reader.encoded(value))

    def capture(self, slug="brief-case", *, filename="brief-service.spec.ts", title="case", standalone=True,
                empty=False, healthy=False, value=None):
        value = value or project(slug)
        case = self.artifacts / slug
        capture = case / ("original-closed-store" if healthy else "closed-store-1")
        store = capture / "store"
        store.mkdir(parents=True)
        database = store / "studio.sqlite3"
        if empty:
            with closing(sqlite3.connect(database)) as connection:
                with connection:
                    connection.execute("CREATE TABLE projects(id TEXT)")
                    connection.execute("CREATE TABLE events(revision INTEGER)")
        else:
            database.write_bytes(b"synthetic unopened metadata fixture; not a product database")
        (store / "empty-directory").mkdir()
        entries = [{"path": "", "kind": "directory"}]
        for path in sorted(store.rglob("*")):
            entry = {"path": path.relative_to(store).as_posix(), "kind": "directory" if path.is_dir() else "file"}
            if path.is_file():
                entry.update({"sizeBytes": path.stat().st_size, "sha256": reader.sha256_file(path)})
            entries.append(entry)
        tree_digest = reader.sha256_bytes(json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode())
        source = case / "original-service"
        manifest = {"schemaVersion": 1, "capturedAt": "2026-01-01T00:00:01Z", "sourceDirectory": str(source),
                    "snapshotDirectory": str(store), "databaseRelativePath": "studio.sqlite3", "entries": entries,
                    "sourceBeforeSha256": tree_digest, "sourceAfterSha256": tree_digest, "snapshotSha256": tree_digest,
                    "lifecycle": [{"databasePath": str(source / "studio.sqlite3"), "taints": [], "exits": [
                        {"processId": 123, "code": 0, "signal": None, "observedAt": "2026-01-01T00:00:00Z"}]}]}
        self.json(capture / "manifest.json", manifest)
        if standalone:
            self.json(capture / "oracle.json", oracle(value))
        attachment_name, evidence = "case-evidence.json", {"unit_fixture": True, "capture": manifest}
        if healthy:
            attachment_name, evidence = "capture-lifecycle-negative.json", {}
        elif filename == "project-lifecycle-service.spec.ts":
            attachment_name, evidence = "project-lifecycle-evidence.json", {}
        elif filename == "delivery-recipe.spec.ts":
            attachment_name, evidence = "delivery-recipe-real-service-evidence.json", {"captures": [manifest]}
        elif filename == "project-mutation.spec.ts":
            attachment_name, evidence = "canonical-mutation-real-service-evidence.json", {"observations": {"closedStoreCaptures": [manifest]}}
        attachment = case / attachment_name
        self.json(attachment, evidence)
        self.specs.append({"file": filename, "title": title, "id": slug, "line": 1,
            "tests": [{"status": "expected", "expectedStatus": "passed", "projectId": "chromium", "results": [
                {"status": "passed", "retry": 0, "attachments": [{"path": str(attachment)}]}]}]})
        return capture, manifest, value

    def report(self):
        value = {"suites": [{"specs": self.specs}], "errors": [],
                 "stats": {"expected": len(self.specs), "unexpected": 0, "flaky": 0, "skipped": 0, "duration": 1.25}}
        self.json(self.browser_report, value)
        return value

    def seal(self):
        self.report()
        return reader.seal(SimpleNamespace(source=self.artifacts, browser_report=self.browser_report,
            source_commit="1" * 40, output=self.root / "seal"))

    def healthy(self, slug="healthy", damage="all"):
        journal_loss = damage in {"middle", "tail", "all"}
        title = (f"a {damage} journal loss in an isolated SQLite copy cannot receive a complete oracle" if journal_loss
                 else f"a {damage} project row cannot redirect reads or mutations and healthy projects remain usable")
        fixture = ("deliberate_journal_loss_in_separate_sqlite_copy" if journal_loss
                   else "deliberately_damaged_project_row_in_separate_sqlite_copy")
        capture, manifest, value = self.capture(slug, filename="scoped-change-capture.spec.ts", title=title, standalone=False, healthy=True)
        self.json(capture.parent / "capture-lifecycle-negative.json", {
            "observed": {"fixture": fixture, "damage": damage},
            "services": [{"transcript": "DO-NOT-COPY-TRANSIENT-GRANT"}]})
        if damage in {"middle", "tail", "all"}:
            self.json(capture.parent / "rejected-damaged-finalization-outcomes.json", {"oracle": None, "capture": None})
        return capture, manifest, value

    def all_categories(self):
        self.capture("standalone")
        delivered, manifest, value = self.capture("delivery", filename="delivery-recipe.spec.ts", standalone=False)
        expected = {"databasePath": reader.source_database(manifest), "projects": [
            {"project": value, "history": history(value), "cas": []}]}
        with zipfile.ZipFile(delivered.with_suffix(".zip"), "x") as bundle:
            for path in sorted(delivered.rglob("*")):
                bundle.write(path, path.relative_to(delivered).as_posix())
            bundle.writestr("readback-expectations.json", reader.encoded(expected))
        empty, _, _ = self.capture("empty", filename="project-lifecycle-service.spec.ts", standalone=False, empty=True)
        self.json(empty.parent / "project-lifecycle-evidence.json", {"status": "passed", "projectIndex": {
            "items": [], "next_cursor": None, "truncated": False}, "completeProjectOracles": [],
            "services": [{"transcript": "DO-NOT-COPY-TRANSIENT-GRANT"}]})
        self.json(empty.parent / "empty-store-outcomes.json", [{"status": "fulfilled", "taints": []}])
        mutation, manifest, value = self.capture("mutation", filename="project-mutation.spec.ts", title="real mutation", standalone=False)
        self.json(mutation.parent / "canonical-mutation-real-service-evidence.json", {"observations": {
            "closedStoreCaptures": [manifest], "final": value, "reopen": {"verification":
                "new_process_reopen_of_unmodified_complete_store", "project": value, "history": history(value), "cas": []}},
            "transcript": "DO-NOT-COPY-TRANSIENT-GRANT"})
        self.healthy()


class SQLiteLifecycleTests(FixtureCase):
    def test_empty_capture_closes_connection_after_success_and_sql_error(self):
        real_connect = sqlite3.connect
        for outcome in ("success", "sql-error"):
            with self.subTest(outcome=outcome):
                connections = []

                def tracked_connect(*args, **kwargs):
                    connection = real_connect(*args, **kwargs)
                    connections.append(connection)
                    if outcome == "sql-error":
                        connection.execute("CREATE TABLE projects(id TEXT)")
                        connection.commit()
                    return connection

                try:
                    with mock.patch.object(sqlite3, "connect", side_effect=tracked_connect) as connect:
                        if outcome == "sql-error":
                            with self.assertRaises(sqlite3.OperationalError):
                                self.capture("empty-" + outcome, empty=True)
                        else:
                            self.capture("empty-" + outcome, empty=True)
                        self.assertEqual(connect.call_count, 1)
                    self.assertEqual(len(connections), 1)
                    # Strong references prevent garbage collection from satisfying this check.
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connections[0].execute("SELECT 1")
                finally:
                    # A failed assertion must remain visible while cleanup releases real handles.
                    for connection in connections:
                        connection.close()

    def test_empty_counts_closes_connection_after_success_and_sql_error(self):
        real_connect = sqlite3.connect
        for outcome in ("success", "sql-error"):
            with self.subTest(outcome=outcome):
                database = self.root / ("counts-" + outcome + ".sqlite3")
                with closing(real_connect(database)) as connection:
                    with connection:
                        connection.execute("CREATE TABLE projects(id TEXT)")
                        connection.executemany("INSERT INTO projects VALUES (?)", [("one",), ("two",)])
                        if outcome == "success":
                            connection.execute("CREATE TABLE events(revision INTEGER)")
                            connection.executemany("INSERT INTO events VALUES (?)", [(1,), (2,), (3,)])
                connections = []

                def tracked_connect(*args, **kwargs):
                    connection = real_connect(*args, **kwargs)
                    connections.append(connection)
                    return connection

                try:
                    with mock.patch.object(reader.sqlite3, "connect", side_effect=tracked_connect) as connect:
                        if outcome == "sql-error":
                            with self.assertRaises(sqlite3.OperationalError):
                                reader.empty_counts(database)
                        else:
                            self.assertEqual(reader.empty_counts(database), {"projects": 2, "events": 3})
                        self.assertEqual(connect.call_count, 1)
                    self.assertEqual(len(connections), 1)
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connections[0].execute("SELECT 1")
                finally:
                    for connection in connections:
                        connection.close()


class JsonAndIdentityTests(FixtureCase):
    def test_json_rejects_duplicates_nonfinite_and_boolean_numeric_alias(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises((reader.VerificationError, ValueError)):
                reader.parse_json(raw)
        self.assertNotEqual(reader.canonical(False), reader.canonical(0))
        self.assertNotEqual(reader.canonical("1"), reader.canonical(1))

    def test_exact_decimal_comparison_has_no_float_rounding(self):
        self.assertEqual(reader.canonical(Decimal("1.000")), reader.canonical(1))
        self.assertNotEqual(reader.canonical(Decimal("9007199254740993.1")),
                            reader.canonical(Decimal("9007199254740993.2")))

    def test_json_excerpt_preserves_original_utf8_bytes_and_offsets(self):
        raw = b'{"skip":"grant", "observations" : {"name":"\xc3\x81","n":1.000}, "omit":true}'
        selected, start, end = reader.exact_member(raw, "observations")
        self.assertEqual(selected, b'{"name":"\xc3\x81","n":1.000}')
        self.assertEqual(selected, raw[start:end])

    def test_nested_original_declaration_keeps_exact_unicode_and_numeric_bytes(self):
        raw = b'{"omit":"grant", "stores" : [0, {"finalization":{"capture": {"name":"\xc3\x81","n":1.000}}}]}'
        selected, start, end = reader.exact_pointer(raw, ("stores", 1, "finalization", "capture"))
        self.assertEqual(selected, b'{"name":"\xc3\x81","n":1.000}')
        self.assertEqual(selected, raw[start:end])

    def test_source_database_supports_original_windows_and_posix_paths(self):
        self.assertEqual(reader.source_database({"sourceDirectory": r"D:\work\service", "databaseRelativePath": "studio.sqlite3"}),
                         r"D:\work\service\studio.sqlite3")
        self.assertEqual(reader.source_database({"sourceDirectory": "/work/service", "databaseRelativePath": "studio.sqlite3"}),
                         "/work/service/studio.sqlite3")

    def test_paths_reject_traversal_devices_ads_and_case_collisions(self):
        for value in ("../a", "/a", "a//b", "a/./b", r"a\b", "a:stream", "NUL.txt", "a.", "a ", "a/COM1"):
            with self.subTest(value=value), self.assertRaises(reader.VerificationError):
                reader.safe_relative(value)
        self.assertEqual(reader.safe_relative("blobs/sha256/aa/abc").as_posix(), "blobs/sha256/aa/abc")

    def test_reparse_attribute_is_rejected_without_platform_symlink_privilege(self):
        real_lstat = Path.lstat
        def lstat(path, *args, **kwargs):
            if path == self.artifacts:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return real_lstat(path, *args, **kwargs)
        with mock.patch.object(Path, "lstat", lstat), self.assertRaises(reader.VerificationError):
            reader.no_links(self.artifacts)

    def test_tree_budget_is_checked_before_hashing_a_large_file(self):
        (self.artifacts / "large").write_bytes(b"12345")
        with mock.patch.object(reader, "MAX_INPUT_FILE_BYTES", 4), mock.patch.object(reader, "file_identity") as identity:
            with self.assertRaises(reader.VerificationError):
                reader.tree(self.artifacts)
            identity.assert_not_called()


class BrowserAndSealTests(FixtureCase):
    def test_clean_capture_archive_preserves_empty_directories_and_original_bytes(self):
        capture, _, _ = self.capture()
        before = reader.tree(self.artifacts)
        archive, seal, index = self.seal()
        self.assertEqual(reader.tree(self.artifacts), before)
        self.assertEqual(index["closed_stores"], 1)
        self.assertEqual(index["category_counts"], {"standalone_oracle": 1})
        self.assertTrue(seal["zip_crc_verified"])
        with zipfile.ZipFile(archive) as bundle:
            self.assertIn("closed-stores/000/store/empty-directory/", bundle.namelist())
            self.assertEqual(bundle.read("closed-stores/000/manifest.json"), (capture / "manifest.json").read_bytes())

    def test_retries_fail_even_when_last_attempt_passed(self):
        self.capture()
        self.specs[0]["tests"][0]["results"].insert(0, {"status": "failed", "retry": 0})
        self.report()
        with self.assertRaises(reader.VerificationError):
            reader.browser_inventory(self.browser_report, self.artifacts)

    def test_browser_fixture_skip_and_expected_failure_are_not_success(self):
        self.capture()
        pristine = copy.deepcopy(self.specs)
        for modification in ("fixture", "skip", "expected_failure"):
            self.specs = copy.deepcopy(pristine)
            if modification == "fixture":
                self.specs[0]["file"] = "project-mutation.spec.ts"
            elif modification == "skip":
                self.specs[0]["tests"][0]["results"][0]["status"] = "skipped"
            else:
                self.specs[0]["tests"][0]["expectedStatus"] = "failed"
            self.report()
            with self.subTest(modification=modification), self.assertRaises(reader.VerificationError):
                reader.browser_inventory(self.browser_report, self.artifacts)

    def test_ambiguous_attachment_directory_is_rejected(self):
        self.capture()
        duplicate = copy.deepcopy(self.specs[0])
        duplicate["id"] = "other-case"
        self.specs.append(duplicate)
        with self.assertRaises(reader.VerificationError):
            self.seal()

    def test_attachment_outside_artifacts_cannot_authorize_capture(self):
        self.capture()
        outside = self.root / "outside.json"
        self.json(outside, {})
        self.specs[0]["tests"][0]["results"][0]["attachments"][0]["path"] = str(outside)
        self.report()
        with self.assertRaises((reader.VerificationError, ValueError)):
            reader.browser_inventory(self.browser_report, self.artifacts)

    def test_unique_source_positive_without_attachment_has_narrow_mapping(self):
        self.capture("scoped-change-capture-comp-unit", filename="scoped-change-capture.spec.ts",
            title="complete capture includes every file and a paginated full oracle after all known peers close")
        self.specs[0]["tests"][0]["results"][0]["attachments"] = []
        _, _, index = self.seal()
        self.assertIn("unique_source_test_title", index["records"][0]["test_mapping_basis"])

    def test_attachment_free_fallback_cannot_reuse_a_case_with_an_attachment(self):
        title = "complete capture includes every file and a paginated full oracle after all known peers close"
        self.capture("attached-case", filename="scoped-change-capture.spec.ts", title=title)
        self.capture("scoped-change-capture-comp-orphan")
        self.specs.pop()
        with self.assertRaises(reader.VerificationError):
            self.seal()

    def test_attachment_free_fallback_rejects_inline_attachments_too(self):
        title = "complete capture includes every file and a paginated full oracle after all known peers close"
        self.capture("scoped-change-capture-comp-unit", filename="scoped-change-capture.spec.ts", title=title)
        self.specs[0]["tests"][0]["results"][0]["attachments"] = [{"name": "inline", "body": "eA==", "contentType": "text/plain"}]
        with self.assertRaises(reader.VerificationError):
            self.seal()

    def test_complete_deleted_capture_is_detected_from_original_attachment(self):
        self.capture("retained")
        deleted, _, _ = self.capture("deleted")
        shutil.rmtree(deleted)
        with self.assertRaises(reader.VerificationError):
            self.seal()

    def test_coherent_extra_capture_under_same_case_is_not_admitted(self):
        capture, manifest, _ = self.capture()
        additional = capture.parent / "closed-store-2"
        shutil.copytree(capture, additional)
        manifest["snapshotDirectory"] = str(additional / "store")
        self.json(additional / "manifest.json", manifest)
        with self.assertRaises(reader.VerificationError):
            self.seal()

    def test_wrong_database_taint_and_nonzero_exit_reject_capture(self):
        capture, manifest, _ = self.capture()
        pristine = copy.deepcopy(manifest)
        record = {"source_files": reader.files(capture)}
        for change in ("taint", "exit", "database"):
            value = copy.deepcopy(pristine)
            if change == "taint":
                value["lifecycle"][0]["taints"] = ["previous UNKNOWN"]
            elif change == "exit":
                value["lifecycle"][0]["exits"][0]["code"] = 7
            else:
                value["lifecycle"][0]["databasePath"] = str(self.root / "different.sqlite3")
            self.json(capture / "manifest.json", value)
            record["source_files"] = reader.files(capture)
            with self.subTest(change=change), self.assertRaises(reader.VerificationError):
                reader.inspect_manifest(capture, record)

    def test_partial_capture_manifest_cannot_be_omitted_as_unrelated(self):
        capture, manifest, _ = self.capture(standalone=False)
        del manifest["lifecycle"]
        self.json(capture / "manifest.json", manifest)
        with self.assertRaises(reader.VerificationError):
            self.seal()

    def test_missing_capture_manifest_is_not_silently_dropped(self):
        self.capture("good")
        capture, _, _ = self.healthy()
        (capture / "manifest.json").unlink()
        with self.assertRaises(reader.VerificationError):
            self.seal()

    def test_all_five_original_formats_are_preserved_without_transient_grants(self):
        self.all_categories()
        archive, _, index = self.seal()
        self.assertEqual(len(index["category_counts"]), 5)
        with zipfile.ZipFile(archive) as bundle:
            payloads = b"".join(bundle.read(name) for name in bundle.namelist() if not name.endswith("/"))
            self.assertNotIn(b"DO-NOT-COPY-TRANSIENT-GRANT", payloads)
        self.assertEqual(sum(row["oracle_present"] for row in index["records"]), 1)

    def test_healthy_control_rejects_an_added_project_oracle(self):
        capture, _, value = self.healthy()
        self.json(capture / "oracle.json", oracle(value))
        with self.assertRaises(reader.VerificationError):
            self.seal()

    def test_journal_loss_control_requires_original_rejection_outcome(self):
        capture, _, _ = self.healthy()
        (capture.parent / "rejected-damaged-finalization-outcomes.json").unlink()
        with self.assertRaises(reader.VerificationError):
            self.seal()

    def test_damaged_copy_cannot_receive_a_capture_in_rejection_evidence(self):
        capture, _, _ = self.healthy()
        self.json(capture.parent / "rejected-damaged-finalization-outcomes.json", {"oracle": {}, "capture": {}})
        with self.assertRaises(reader.VerificationError):
            self.seal()

    def test_archive_extraction_rejects_traversal_collisions_and_byte_overrun(self):
        for index, names in enumerate((("../outside",), ("A/file", "a/other"), ("CON",), ("file", "file/child"))):
            archive = self.root / f"bad-{index}.zip"
            with zipfile.ZipFile(archive, "x") as bundle:
                for name in names:
                    bundle.writestr(name, b"data")
            destination = self.root / f"extract-{index}"
            destination.mkdir()
            with self.subTest(names=names), self.assertRaises(reader.VerificationError):
                reader.safe_extract(archive, destination, 1024)
            self.assertEqual(list(destination.iterdir()), [])
        archive = self.root / "large.zip"
        with zipfile.ZipFile(archive, "x") as bundle:
            bundle.writestr("large", b"12345")
        destination = self.root / "limited"
        destination.mkdir()
        with self.assertRaises(reader.VerificationError):
            reader.safe_extract(archive, destination, 4)


class OracleReadbackTests(FixtureCase):
    def expected(self, revision=2):
        value = project(revision=revision)
        return {value["id"]: {"project": value, "history": history(value), "origin": "preexisting-unit-fixture", "cas": []}}

    def test_full_project_history_pagination_and_eof_are_compared(self):
        expected = self.expected(101)
        checks, layout = reader.readback_plan(expected, {})
        self.assertEqual([row["args"]["request"]["after_revision"] for row in checks if row["command"] == "project_history"], [0, 100, 101])
        rows = reader.compare_responses(response_bytes(checks, expected), checks, layout, expected)
        self.assertTrue(rows[0]["journal_contiguous_and_eof"])

    def test_project_journal_eof_and_boolean_mutations_are_rejected(self):
        expected = self.expected()
        checks, layout = reader.readback_plan(expected, {})
        original = [json.loads(line) for line in response_bytes(checks, expected).splitlines()]
        for change in ("project", "journal", "eof", "boolean"):
            rows = copy.deepcopy(original)
            if change == "project":
                rows[1]["result"]["title"] = "new unobserved title"
            elif change == "journal":
                rows[2]["result"].pop()
            elif change == "eof":
                rows[-1]["result"] = [{"revision": 3}]
            else:
                rows[1]["result"]["decision"]["approved"] = 0
            raw = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
            with self.subTest(change=change), self.assertRaises(reader.VerificationError):
                reader.compare_responses(raw, checks, layout, expected)

    def test_extra_missing_truncated_or_changed_library_is_rejected(self):
        expected = self.expected()
        checks, layout = reader.readback_plan(expected, {})
        for listing in ({"items": [], "next_cursor": None, "truncated": False},
                        {"items": [{"id": "project"}, {"id": "extra"}], "next_cursor": None, "truncated": False},
                        {"items": [{"id": "project"}], "next_cursor": "later", "truncated": True}):
            with self.subTest(listing=listing), self.assertRaises(reader.VerificationError):
                reader.compare_responses(response_bytes(checks, expected, listing=listing), checks, layout, expected)
        with self.assertRaises(reader.VerificationError):
            reader.compare_responses(response_bytes(checks, expected), checks, layout, expected,
                {"items": [{"id": "project", "title": "uncaptured"}], "next_cursor": None, "truncated": False})

    def test_wrong_reply_identity_count_or_error_cannot_pass(self):
        expected = self.expected()
        checks, layout = reader.readback_plan(expected, {})
        original = [json.loads(line) for line in response_bytes(checks, expected).splitlines()]
        for change in ("missing", "extra", "error", "identity", "boolean_identity"):
            rows = copy.deepcopy(original)
            if change == "missing":
                rows.pop()
            elif change == "extra":
                rows.append(rows[-1])
            elif change == "error":
                rows[0]["ok"] = False
            elif change == "identity":
                rows[0]["id"] = 99
            else:
                rows[0]["id"] = False
            raw = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
            with self.subTest(change=change), self.assertRaises(reader.VerificationError):
                reader.compare_responses(raw, checks, layout, expected)

    def test_original_journal_must_be_complete_before_any_process(self):
        value = project()
        for changed in (history(value)[:-1], list(reversed(history(value)))):
            with self.assertRaises(reader.VerificationError):
                reader.validate_expected(value, changed, full_oracle=oracle(value))

    def test_referenced_cas_missing_or_incompatible_is_rejected(self):
        value = project()
        digest = "a" * 64
        value["assets"] = [{"id": "asset", "content_sha256": digest}]
        expected = {"project": value, "history": history(value), "origin": "unit", "cas": []}
        with self.assertRaises(reader.VerificationError):
            reader.verify_expected_cas(expected, {})
        with self.assertRaises(reader.VerificationError):
            reader.verify_expected_cas(expected, {f"sha256/aa/{digest}": {"bytes": 3, "sha256": digest}})

    def test_reopen_uses_only_archive_extraction_and_skips_healthy_controls(self):
        self.all_categories()
        archive, seal, index = self.seal()
        runner = self.root / "unexecuted-unit-runner"
        runner.write_bytes(b"Never executed by this unit test")
        before = reader.tree(self.artifacts)
        output = self.root / "readback"
        calls = []
        def simulated_transport(binary, database, checks, logs, remaining):
            self.assertTrue(archive.is_file())
            self.assertEqual(reader.sha256_file(archive), seal["archive_identity"]["sha256"])
            self.assertIn(output / "extracted", database.parents)
            self.assertNotIn(self.artifacts, database.parents)
            record = next(row for row in index["records"] if f"{row['index']:03d}" == logs.name)
            self.assertNotEqual(record["category"], "healthy_control_for_deliberately_damaged_copy")
            extracted = output / "extracted"
            captured = extracted / record["archive_path"]
            manifest = reader.checked_json_file(captured / "manifest.json")
            expected = reader.expected_projects(extracted, captured, record, manifest)
            raw = response_bytes(checks, expected)
            calls.append(database)
            return raw, {"exit_code": 0, "captured_bytes": {"stdout": len(raw), "stderr": 0},
                "stdin_bytes": len(json.dumps(checks).encode()),
                "stdin_sha256": "0" * 64, "stdout": reader.identity_bytes(raw), "stderr": reader.identity_bytes(b"")}
        with mock.patch.object(reader, "run_jsonl", side_effect=simulated_transport):
            report = reader.readback(SimpleNamespace(archive=archive, runner=runner, output=output,
                expected_archive_sha256=seal["archive_identity"]["sha256"],
                expected_runner_sha256=reader.sha256_file(runner), source_commit="1" * 40))
        self.assertEqual(len(calls), 4)
        self.assertEqual(report["stores_reopened"], 4)
        self.assertEqual(report["projects_fully_compared"], 3)
        self.assertEqual(report["empty_libraries_verified"], 1)
        self.assertEqual(report["custody_only_controls"], 1)
        self.assertIsNone(report["custody_only"][0]["full_project_equal"])
        self.assertFalse(report["custody_only"][0]["process_started"])
        self.assertEqual(reader.tree(self.artifacts), before)

    def test_existing_output_is_never_modified_on_rejection(self):
        self.capture()
        self.report()
        runner = self.root / "unexecuted-unit-runner"
        runner.write_bytes(b"Not executed")
        output = self.root / "existing"
        output.mkdir()
        sentinel = output / "receipt.json"
        sentinel.write_bytes(b"immutable original")
        with mock.patch("builtins.print"), mock.patch.object(reader, "run_jsonl") as transport:
            code = reader.main(["--browser-artifacts", str(self.artifacts), "--browser-report", str(self.browser_report),
                "--runner", str(runner), "--expected-runner-sha256", reader.sha256_file(runner),
                "--source-commit", "1" * 40, "--output", str(output)])
        self.assertEqual(code, 1)
        transport.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b"immutable original")


class CorrelatedClosureTests(FixtureCase):
    """Synthetic closed files and original-shaped receipts; no product process."""

    def paired(self):
        first, first_manifest, first_project = self.capture("paired")
        second = first.parent / "closed-store-2"
        shutil.copytree(first, second)
        second_manifest = copy.deepcopy(first_manifest)
        second_manifest["sourceDirectory"] = str(first.parent / "target-service")
        second_manifest["snapshotDirectory"] = str(second / "store")
        first_witness = copy.deepcopy(first_manifest["lifecycle"][0])
        second_witness = copy.deepcopy(first_witness)
        second_witness["databasePath"] = reader.source_database(second_manifest)
        second_witness["exits"] = [
            {"processId": 456, "code": 0, "signal": None, "observedAt": "2026-01-01T00:00:00Z"},
            {"processId": 789, "code": 0, "signal": None, "observedAt": "2026-01-01T00:00:01Z"}]
        first_manifest["lifecycle"] = [first_witness, second_witness]
        second_manifest["lifecycle"] = [copy.deepcopy(second_witness), copy.deepcopy(first_witness)]
        second_project = project("paired-target")
        self.json(second / "oracle.json", oracle(second_project))
        captures = [(first, first_manifest), (second, second_manifest)]
        self.persist_pair(captures)
        return captures, [first_project, second_project]

    def persist_pair(self, captures):
        for capture, manifest in captures:
            self.json(capture / "manifest.json", manifest)
        self.json(captures[0][0].parent / "case-evidence.json",
                  {"unit_fixture": True, "captures": [manifest for _, manifest in captures]})

    def capture_records(self, captures):
        self.report()
        classification = reader.browser_inventory(self.browser_report, self.artifacts)
        case = {key: value for key, value in classification["cases"][0].items()
                if key not in {"artifact_directories", "source_attachment_paths"}}
        return [{"index": index, "source": capture.relative_to(self.artifacts).as_posix(),
                 "archive_path": f"closed-stores/{index:03d}", "test": copy.deepcopy(case),
                 "source_files": reader.files(capture)}
                for index, (capture, _) in enumerate(captures)]

    def correlations(self, captures, records=None):
        records = records or self.capture_records(captures)
        return reader.correlate_closures([(record, manifest)
                                         for record, (_, manifest) in zip(records, captures)])

    def changed_archive(self, archive, change):
        with zipfile.ZipFile(archive) as source:
            payloads = {name: source.read(name) for name in source.namelist()}
        index = reader.parse_json(payloads["custody-index.json"])
        change(payloads, index)
        payloads["custody-index.json"] = reader.encoded(index)
        payloads["SHA256-MANIFEST.json"] = reader.encoded({
            name: reader.identity_bytes(raw) for name, raw in payloads.items()
            if not name.endswith("/") and name != "SHA256-MANIFEST.json"})
        changed = self.root / "synthetic-altered-custody.zip"
        with zipfile.ZipFile(changed, "x") as destination:
            for name, raw in payloads.items():
                destination.writestr(name, raw)
        return changed

    def test_seal_preserves_reciprocal_database_captures_and_original_oracles(self):
        captures, _ = self.paired()
        original = [reader.files(capture) for capture, _ in captures]
        with mock.patch.object(reader, "run_jsonl") as process:
            archive, verification, index = self.seal()
        process.assert_not_called()
        self.assertEqual(index["closed_stores"], 2)
        self.assertTrue(verification["all_capture_manifests_verified"])
        with zipfile.ZipFile(archive) as bundle:
            for record, (capture, _) in zip(index["records"], captures):
                for name in ("manifest.json", "oracle.json"):
                    self.assertEqual(bundle.read(record["archive_path"] + "/" + name), (capture / name).read_bytes())
        self.assertEqual([reader.files(capture) for capture, _ in captures], original)

    def test_single_database_preserves_all_distinct_same_store_peer_exits(self):
        capture, manifest, _ = self.capture()
        peer = copy.deepcopy(manifest["lifecycle"][0])
        peer["exits"][0]["processId"] = 456
        manifest["lifecycle"].append(peer)
        self.json(capture / "manifest.json", manifest)
        self.json(capture.parent / "case-evidence.json", {"capture": manifest})
        archive, _, index = self.seal()
        with zipfile.ZipFile(archive) as bundle:
            stored = reader.parse_json(bundle.read(index["records"][0]["archive_path"] + "/manifest.json"))
        self.assertEqual(stored["lifecycle"], manifest["lifecycle"])
        self.assertEqual(len(stored["lifecycle"]), 2)

    def test_unscoped_inspection_does_not_allow_a_foreign_database(self):
        captures, _ = self.paired()
        records = self.capture_records(captures)
        with self.assertRaisesRegex(reader.VerificationError, "another database"):
            reader.inspect_manifest(captures[0][0], records[0])

    def test_missing_own_closure_is_rejected_even_with_a_peer_capture(self):
        captures, _ = self.paired()
        captures[0][1]["lifecycle"] = captures[0][1]["lifecycle"][1:]
        self.persist_pair(captures)
        with self.assertRaisesRegex(reader.VerificationError, "own database"):
            self.seal()
        self.assertFalse((self.root / "seal/closed-stores-custody.zip").exists())

    def test_foreign_participant_needs_a_declared_capture_in_the_same_case(self):
        captures, _ = self.paired()
        shutil.rmtree(captures[1][0])
        self.persist_pair(captures[:1])
        with self.assertRaisesRegex(reader.VerificationError, "no correlated capture"):
            self.seal()
        self.assertFalse((self.root / "seal/closed-stores-custody.zip").exists())

    def test_other_browser_case_or_project_cannot_supply_a_closure(self):
        captures, _ = self.paired()
        original = self.capture_records(captures)
        for key in ("test_id", "project_id"):
            records = copy.deepcopy(original)
            records[1]["test"][key] += "-other"
            with self.subTest(identity=key), self.assertRaisesRegex(reader.VerificationError, "no correlated capture"):
                self.correlations(captures, records)

    def test_peer_requires_reciprocal_complete_process_receipts(self):
        original, _ = self.paired()
        for change in ("missing_reverse", "process_id", "observed_at", "missing_exit", "reordered_exits"):
            captures = [(path, copy.deepcopy(manifest)) for path, manifest in original]
            peer = captures[1][1]
            if change == "missing_reverse":
                peer["lifecycle"].pop()
            elif change == "process_id":
                peer["lifecycle"][0]["exits"][0]["processId"] += 1
            elif change == "observed_at":
                peer["lifecycle"][0]["exits"][0]["observedAt"] = "2026-01-01T00:00:02Z"
            elif change == "missing_exit":
                peer["lifecycle"][0]["exits"].pop()
            else:
                peer["lifecycle"][0]["exits"].reverse()
            with self.subTest(change=change), self.assertRaises(reader.VerificationError):
                self.correlations(captures)

    def test_every_participant_remains_subject_to_clean_exit_and_taint_guards(self):
        original, _ = self.paired()
        for change in ("taint", "unknown", "nonzero", "signal", "missing", "boolean_zero", "string_zero"):
            captures = [(path, copy.deepcopy(manifest)) for path, manifest in original]
            participant = captures[1][1]["lifecycle"][0]
            if change == "taint":
                participant["taints"] = ["retained UNKNOWN fixture"]
            elif change == "missing":
                participant["exits"] = []
            elif change == "signal":
                participant["exits"][0]["signal"] = "SIGTERM"
            else:
                participant["exits"][0]["code"] = {"unknown": None, "nonzero": 7, "boolean_zero": False, "string_zero": "0"}[change]
            with self.subTest(change=change), self.assertRaises(reader.VerificationError):
                self.correlations(captures)

    def test_all_same_database_peer_candidates_must_agree(self):
        captures, _ = self.paired()
        records = self.capture_records(captures)
        repeated_record = {**copy.deepcopy(records[1]), "index": 2, "source": "paired/closed-store-3"}
        repeated_manifest = copy.deepcopy(captures[1][1])
        pairs = [(record, manifest) for record, (_, manifest) in zip(records, captures)]
        self.assertEqual(len(reader.correlate_closures([*pairs, (repeated_record, repeated_manifest)])), 3)
        repeated_manifest["lifecycle"][0]["exits"][0]["processId"] += 1
        with self.assertRaisesRegex(reader.VerificationError, "different process closure witness"):
            reader.correlate_closures([*pairs, (repeated_record, repeated_manifest)])

    def test_case_metadata_and_capture_indices_cannot_be_ambiguous(self):
        captures, _ = self.paired()
        original = self.capture_records(captures)
        for change in ("title", "file", "index"):
            records = copy.deepcopy(original)
            if change == "index":
                records[1]["index"] = records[0]["index"]
            else:
                records[1]["test"][change] += "-changed"
            with self.subTest(change=change), self.assertRaises(reader.VerificationError):
                self.correlations(captures, records)

    def test_correlation_budget_rejects_fanout_without_omitting_receipts(self):
        captures, _ = self.paired()
        records = self.capture_records(captures)
        with mock.patch.object(reader, "MAX_REQUESTS", 1), self.assertRaisesRegex(reader.VerificationError, "budget exceeded"):
            self.correlations(captures, records)

    def test_changed_manifest_cannot_reuse_an_earlier_closure_binding(self):
        captures, _ = self.paired()
        records = self.capture_records(captures)
        bindings = self.correlations(captures, records)
        captures[0][1]["lifecycle"][1]["exits"][0]["processId"] += 1
        self.persist_pair(captures)
        records[0]["source_files"] = reader.files(captures[0][0])
        with self.assertRaisesRegex(reader.VerificationError, "correlated closure evidence"):
            reader.inspect_manifest(captures[0][0], records[0], closure_bindings=bindings)

    def test_windows_database_identity_is_not_confused_with_posix_case(self):
        captures, _ = self.paired()
        records = self.capture_records(captures)
        windows = [(path, copy.deepcopy(manifest)) for path, manifest in captures]
        for index, (_, manifest) in enumerate(windows):
            manifest["sourceDirectory"] = rf"D:\Fixture\Store{index}"
            manifest["lifecycle"] = [
                {**copy.deepcopy(captures[0][1]["lifecycle"][peer]),
                 "databasePath": rf"d:\fixture\store{peer}\STUDIO.SQLITE3"} for peer in range(2)]
        self.assertEqual(len(self.correlations(windows, records)), 2)
        windows[0][1]["sourceDirectory"] = "/Fixture/Store0"
        windows[0][1]["lifecycle"][0]["databasePath"] = "/fixture/store0/studio.sqlite3"
        with self.assertRaisesRegex(reader.VerificationError, "own database"):
            self.correlations(windows, records)

    def test_pre_readback_checks_every_capture_before_the_first_process(self):
        self.paired()
        archive, _, _ = self.seal()
        archive_before = reader.sha256_file(archive)
        def taint_second(payloads, index):
            record = index["records"][1]
            name = record["archive_path"] + "/manifest.json"
            manifest = reader.parse_json(payloads[name])
            manifest["lifecycle"][0]["taints"] = ["retained UNKNOWN in a synthetic negative"]
            payloads[name] = reader.encoded(manifest)
            record["source_files"]["manifest.json"] = reader.identity_bytes(payloads[name])
        changed = self.changed_archive(archive, taint_second)
        runner = self.root / "never-executed-runner"
        runner.write_bytes(b"No product process is allowed in this negative test")
        output = self.root / "readback"
        with mock.patch.object(reader, "run_jsonl") as process, self.assertRaisesRegex(reader.VerificationError, "tainted"):
            reader.readback(SimpleNamespace(archive=changed, runner=runner, output=output,
                expected_archive_sha256=reader.sha256_file(changed), expected_runner_sha256=reader.sha256_file(runner),
                source_commit="1" * 40))
        process.assert_not_called()
        result = reader.checked_json_file(output / "verification.json")
        self.assertEqual(result["state"], "FAIL")
        self.assertEqual(result["stores"], [])
        self.assertEqual(reader.sha256_file(archive), archive_before)

    def test_reciprocal_readback_preserves_original_oracles_on_fresh_copies(self):
        _, values = self.paired()
        archive, verification, _ = self.seal()
        runner = self.root / "unexecuted-unit-runner"
        runner.write_bytes(b"Synthetic transport only")
        output = self.root / "readback"
        original = reader.tree(self.artifacts)
        calls = []
        def synthetic_transport(binary, database, checks, logs, remaining):
            self.assertIn(output / "extracted", database.parents)
            self.assertNotIn(self.artifacts, database.parents)
            value = values[int(logs.name)]
            raw = response_bytes(checks, {value["id"]: {"project": value, "history": history(value)}})
            calls.append(database)
            return raw, {"exit_code": 0, "captured_bytes": {"stdout": len(raw), "stderr": 0},
                "stdin_bytes": len(json.dumps(checks).encode()), "stdin_sha256": "0" * 64,
                "stdout": reader.identity_bytes(raw), "stderr": reader.identity_bytes(b"")}
        with mock.patch.object(reader, "run_jsonl", side_effect=synthetic_transport):
            result = reader.readback(SimpleNamespace(archive=archive, runner=runner, output=output,
                expected_archive_sha256=verification["archive_identity"]["sha256"],
                expected_runner_sha256=reader.sha256_file(runner), source_commit="1" * 40))
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["stores_reopened"], 2)
        self.assertEqual(result["projects_fully_compared"], 2)
        self.assertTrue(result["all_checks_passed"])
        self.assertEqual(reader.tree(self.artifacts), original)


class OriginalArtifactRootTests(FixtureCase):
    """Synthetic path relocation only; never a browser or product-runner rerun."""

    paired = CorrelatedClosureTests.paired
    persist_pair = CorrelatedClosureTests.persist_pair
    changed_archive = CorrelatedClosureTests.changed_archive

    def original_fixture(self, logical_root="D:/original/browser-artifacts"):
        # Construct only this test's synthetic originals, then freeze their bytes.
        # The verifier must not rewrite a report or manifest to achieve relocation.
        prefix = self.artifacts.as_posix()

        def rewrite(value, key=None):
            if type(value) is dict:
                return {name: rewrite(child, name) for name, child in value.items()}
            if type(value) is list:
                return [rewrite(child, key) for child in value]
            if type(value) is str:
                path = value.replace("\\", "/")
                if path == prefix or path.startswith(prefix + "/"):
                    relocated = logical_root + path[len(prefix):]
                    if logical_root.startswith("D:/") and key in {"snapshotDirectory", "databasePath"}:
                        return relocated.replace("/", "\\")
                    return relocated
            return value

        for path in sorted(self.artifacts.rglob("*.json")):
            self.json(path, rewrite(json.loads(path.read_bytes())))
        report = rewrite(self.report())
        report["config"] = {"projects": [{"id": "chromium", "name": "chromium", "outputDir": logical_root}]}
        self.json(self.browser_report, report)
        self.logical_root = logical_root
        return report

    def relocated_seal(self, output="seal", logical_root=None):
        return reader.seal(SimpleNamespace(source=self.artifacts, browser_report=self.browser_report,
            source_commit="1" * 40, output=self.root / output,
            original_browser_artifacts_root=logical_root or self.logical_root))

    def inventory(self, *, logical_root=None):
        mapping = reader.OriginalArtifactRoot(logical_root or self.logical_root, self.artifacts)
        classification = reader.browser_inventory(self.browser_report, self.artifacts, artifact_mapping=mapping)
        return mapping, classification

    def main_args(self, output="main", logical_root=None):
        runner = self.root / "unexecuted-synthetic-runner"
        runner.write_bytes(b"Synthetic bytes only; not a Motionwright executable")
        return ["--browser-artifacts", str(self.artifacts), "--browser-report", str(self.browser_report),
            "--runner", str(runner), "--expected-runner-sha256", reader.sha256_file(runner),
            "--source-commit", "1" * 40, "--output", str(self.root / output),
            "--original-browser-artifacts-root", logical_root or self.logical_root]

    def test_windows_originals_are_preserved_with_logical_and_physical_provenance(self):
        capture, _, _ = self.capture()
        self.original_fixture()
        before, report_bytes = reader.tree(self.artifacts), self.browser_report.read_bytes()
        manifest_bytes = (capture / "manifest.json").read_bytes()
        with mock.patch.object(reader, "run_jsonl") as transport:
            archive, verification, index = self.relocated_seal(logical_root=r"D:\original\browser-artifacts")
        transport.assert_not_called()
        self.assertEqual(reader.tree(self.artifacts), before)
        self.assertEqual(self.browser_report.read_bytes(), report_bytes)
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(bundle.read("closed-stores/000/manifest.json"), manifest_bytes)
            self.assertIn(b"D:", bundle.read("closed-stores/000/manifest.json"))
        provenance = verification["original_artifact_root_translation"]
        self.assertEqual(provenance["original_root_normalized"], self.logical_root)
        self.assertEqual(provenance["physical_browser_artifacts_root"], str(self.artifacts))
        self.assertEqual(provenance["original_browser_report_identity"], reader.identity_bytes(report_bytes))
        self.assertEqual(len(provenance["translated_paths"]), 2)
        for key in ("original_report_and_manifest_paths_rewritten", "original_drive_alias_created", "browser_rerun_performed",
                    "original_browser_execution_authenticated_here", "source_commit_authenticated_here",
                    "runner_build_source_authenticated_here", "formal_acceptance"):
            self.assertIs(provenance[key], False)
        self.assertTrue(provenance["physical_input_tree_unchanged_during_sealing"])
        self.assertEqual(index["closed_stores"], 1)

    def test_posix_relocation_requires_the_explicit_opt_in(self):
        self.capture()
        self.original_fixture("/synthetic-original/browser-artifacts")
        with self.assertRaises(reader.VerificationError):
            reader.seal(SimpleNamespace(source=self.artifacts, browser_report=self.browser_report,
                source_commit="1" * 40, output=self.root / "default-must-reject"))
        _, _, index = self.relocated_seal()
        self.assertEqual(index["original_artifact_root_translation"]["original_path_flavor"], "posix")

    def test_logical_parser_rejects_traversal_devices_ads_and_ambiguous_separators(self):
        bad = ["", "D:relative", "relative/root", "D:/", "/", "//server/share", r"\\server\share",
               r"\\?\D:\original", r"\original\artifacts", "D:/root/../artifacts", "D:/./artifacts",
               "D:/root//artifacts", "D:/root/artifacts/", "D:/root/con.json", "D:/root/file:stream",
               "D:/root/name.", "D:/root/name ", "D:/root/a\x00b", "D:/root/a\x7fb", "/root/../artifacts",
               "/root/./artifacts", "/root//artifacts", "/root/artifacts/", "/root\\artifacts"]
        for path in bad:
            with self.subTest(path=repr(path)), self.assertRaises(reader.VerificationError):
                reader.original_artifact_path(path)
        self.assertEqual(reader.original_artifact_path(r"d:\original\browser-artifacts"),
                         reader.original_artifact_path("D:/original/browser-artifacts"))

    def test_root_cannot_be_a_parent_prefix_other_drive_or_other_report_directory(self):
        self.capture()
        self.original_fixture()
        for root in ("D:/original", "D:/original/browser-artifacts-old", "E:/original/browser-artifacts",
                     "/original/browser-artifacts", "D:/Original/browser-artifacts"):
            with self.subTest(root=root), self.assertRaises(reader.VerificationError):
                self.inventory(logical_root=root)

    def test_selected_project_output_directory_is_required_and_unambiguous(self):
        self.capture()
        original = self.original_fixture()
        for variant in ("missing_config", "missing_project", "duplicate_project", "wrong_output", "missing_identity"):
            changed = copy.deepcopy(original)
            if variant == "missing_config":
                del changed["config"]
            elif variant == "missing_project":
                changed["config"]["projects"][0]["id"] = "firefox"
                changed["config"]["projects"][0]["name"] = "firefox"
            elif variant == "duplicate_project":
                changed["config"]["projects"].append(copy.deepcopy(changed["config"]["projects"][0]))
            elif variant == "wrong_output":
                changed["config"]["projects"][0]["outputDir"] += "-other"
            else:
                changed["suites"][0]["specs"][0]["tests"][0]["projectId"] = ""
            self.json(self.browser_report, changed)
            with self.subTest(variant=variant), self.assertRaises(reader.VerificationError):
                self.inventory()

    def test_attachment_translation_rejects_outside_aliases_missing_paths_and_wrong_kinds(self):
        self.capture()
        original = self.original_fixture()
        valid = original["suites"][0]["specs"][0]["tests"][0]["results"][0]["attachments"][0]["path"]
        for bad in (valid.replace("browser-artifacts/", "browser-artifacts-other/"),
                    valid.replace("D:/", "E:/"), valid.replace("/brief-case/", "/brief-case/../brief-case/"),
                    valid.replace("/brief-case/", "/BRIEF-CASE/"), valid.replace("case-evidence.json", "missing.json"),
                    self.logical_root + "/brief-case/closed-store-1/store", "case-evidence.json"):
            changed = copy.deepcopy(original)
            changed["suites"][0]["specs"][0]["tests"][0]["results"][0]["attachments"][0]["path"] = bad
            self.json(self.browser_report, changed)
            with self.subTest(path=bad), self.assertRaises(reader.VerificationError):
                self.inventory()

    def test_case_collisions_in_the_physical_inventory_are_rejected(self):
        capture, _, _ = self.capture()
        self.original_fixture()
        (capture.parent / "CASE-EVIDENCE.JSON").write_bytes((capture.parent / "case-evidence.json").read_bytes())
        listing = [(str(capture.parent), [], ["case-evidence.json", "CASE-EVIDENCE.JSON"])]
        with mock.patch.object(reader.os, "walk", return_value=listing), self.assertRaises(reader.VerificationError):
            reader.OriginalArtifactRoot(self.logical_root, self.artifacts)

    def test_physical_links_and_reparse_points_are_rejected_before_resolution(self):
        capture, _, _ = self.capture()
        self.original_fixture()
        original_lstat = Path.lstat
        for target, mode, attributes in ((self.artifacts, stat.S_IFLNK, 0),
                                         (capture.parent, stat.S_IFDIR, 0x400),
                                         (capture.parent / "case-evidence.json", stat.S_IFREG, 0x400)):
            def observed(path, *args, **kwargs):
                if path == target:
                    return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
                return original_lstat(path, *args, **kwargs)
            with self.subTest(target=str(target)), mock.patch.object(Path, "lstat", observed), self.assertRaises(reader.VerificationError):
                reader.OriginalArtifactRoot(self.logical_root, self.artifacts)

    def test_manifest_cannot_translate_to_another_existing_capture(self):
        first, _, _ = self.capture("first")
        second, _, _ = self.capture("second")
        self.original_fixture()
        changed = reader.checked_json_file(first / "manifest.json")
        changed["snapshotDirectory"] = reader.checked_json_file(second / "manifest.json")["snapshotDirectory"]
        self.json(first / "manifest.json", changed)
        with mock.patch.object(reader, "run_jsonl") as process, self.assertRaises(reader.VerificationError):
            self.relocated_seal()
        process.assert_not_called()

    def test_original_embedded_capture_paths_remain_subject_to_the_root(self):
        capture, _, _ = self.capture()
        self.original_fixture()
        attachment = capture.parent / "case-evidence.json"
        changed = reader.checked_json_file(attachment)
        changed["capture"]["snapshotDirectory"] = "E:/elsewhere/brief-case/closed-store-1/store"
        self.json(attachment, changed)
        with mock.patch.object(reader, "run_jsonl") as process, self.assertRaises(reader.VerificationError):
            self.relocated_seal()
        process.assert_not_called()

    def test_changed_attachment_or_whole_input_tree_cannot_reuse_an_inventory(self):
        capture, _, _ = self.capture()
        self.original_fixture()
        mapping = reader.OriginalArtifactRoot(self.logical_root, self.artifacts)
        attachment = capture.parent / "case-evidence.json"
        original_bytes = attachment.read_bytes()
        attachment.write_bytes(original_bytes + b" ")
        with self.assertRaises(reader.VerificationError):
            reader.browser_inventory(self.browser_report, self.artifacts, artifact_mapping=mapping)
        attachment.write_bytes(original_bytes)
        mapping = reader.OriginalArtifactRoot(self.logical_root, self.artifacts)
        (self.artifacts / "new-unindexed-input.txt").write_bytes(b"added after inventory")
        with self.assertRaises(reader.VerificationError):
            reader.seal(SimpleNamespace(source=self.artifacts, browser_report=self.browser_report,
                source_commit="1" * 40, output=self.root / "seal"), artifact_mapping=mapping)

    def test_root_mapping_cannot_rebind_a_report_or_exceed_its_path_budget(self):
        capture, _, _ = self.capture()
        original = self.original_fixture()
        mapping, _ = self.inventory()
        with self.assertRaises(reader.VerificationError):
            mapping.bind_report(original, self.browser_report.read_bytes())
        manifest = reader.checked_json_file(capture / "manifest.json")
        with mock.patch.object(reader, "MAX_REQUESTS", 1), self.assertRaises(reader.VerificationError):
            mapping.relative(manifest["snapshotDirectory"], kind="directory")

    def test_windows_logical_database_closures_remain_reciprocal_and_unmodified(self):
        captures, _ = self.paired()
        self.original_fixture()
        before = [(path / "manifest.json").read_bytes() for path, _ in captures]
        archive, _, index = self.relocated_seal()
        self.assertEqual(index["closed_stores"], 2)
        with zipfile.ZipFile(archive) as bundle:
            for record, raw in zip(index["records"], before):
                self.assertEqual(bundle.read(record["archive_path"] + "/manifest.json"), raw)
                self.assertTrue(all(participant["databasePath"].startswith("D:\\")
                                    for participant in reader.parse_json(raw)["lifecycle"]))

    def test_failed_browser_case_is_not_made_admissible_by_relocation(self):
        self.capture()
        report = self.original_fixture()
        report["suites"][0]["specs"][0]["tests"][0]["results"][0]["status"] = "failed"
        self.json(self.browser_report, report)
        with mock.patch.object(reader, "readback") as readback, mock.patch("builtins.print"):
            code = reader.main(self.main_args())
        self.assertEqual(code, 1)
        readback.assert_not_called()
        receipt = reader.checked_json_file(self.root / "main" / "receipt.json")
        self.assertEqual(receipt["state"], "FAIL")
        self.assertFalse(receipt["all_checks_passed"])
        self.assertFalse(receipt["retry_attempted"])

    def test_unknown_readback_retains_translation_and_never_retries(self):
        self.capture()
        self.original_fixture()
        with mock.patch.object(reader, "readback", side_effect=reader.ReadbackUnknown("synthetic unknown")) as readback, \
                mock.patch.object(reader, "run_jsonl") as process, mock.patch("builtins.print"):
            code = reader.main(self.main_args())
        self.assertEqual(code, 1)
        readback.assert_called_once()
        process.assert_not_called()
        receipt = reader.checked_json_file(self.root / "main" / "receipt.json")
        self.assertEqual(receipt["state"], "UNKNOWN")
        self.assertFalse(receipt["all_checks_passed"])
        self.assertFalse(receipt["retry_attempted"])
        self.assertFalse(receipt["browser_rerun_performed"])
        self.assertEqual(receipt["original_artifact_root_translation"]["original_root_normalized"], self.logical_root)

    def test_main_uses_fresh_extraction_and_keeps_all_authentication_limits(self):
        _, _, value = self.capture()
        self.original_fixture()
        before, report_bytes = reader.tree(self.artifacts), self.browser_report.read_bytes()
        calls = []

        def synthetic_transport(binary, database, checks, logs, remaining):
            self.assertIn(self.root / "main" / "readback" / "extracted", database.parents)
            self.assertNotIn(self.artifacts, database.parents)
            expected = {value["id"]: {"project": value, "history": history(value)}}
            raw = response_bytes(checks, expected)
            calls.append(database)
            return raw, {"exit_code": 0, "captured_bytes": {"stdout": len(raw), "stderr": 0},
                "stdin_bytes": len(json.dumps(checks).encode()), "stdin_sha256": "0" * 64,
                "stdout": reader.identity_bytes(raw), "stderr": reader.identity_bytes(b"")}

        with mock.patch.object(reader, "run_jsonl", side_effect=synthetic_transport), \
                mock.patch.object(reader.sqlite3, "connect") as sqlite, mock.patch("builtins.print"):
            code = reader.main(self.main_args())
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        sqlite.assert_not_called()
        receipt = reader.checked_json_file(self.root / "main" / "receipt.json")
        self.assertFalse(receipt["expected_browser_inventory_authenticated_here"])
        self.assertFalse(receipt["runner_build_source_authenticated_here"])
        self.assertFalse(receipt["formal_acceptance"])
        self.assertFalse(receipt["browser_rerun_performed"])
        self.assertEqual(reader.tree(self.artifacts), before)
        self.assertEqual(self.browser_report.read_bytes(), report_bytes)

    def test_relocation_metadata_cannot_add_authenticated_or_human_acceptance_claims(self):
        self.capture()
        self.original_fixture()
        archive, _, _ = self.relocated_seal()
        changed = self.changed_archive(archive, lambda payloads, index:
            index["original_artifact_root_translation"].update({"formal_acceptance": True}))
        runner = self.root / "unexecuted-unit-runner"
        runner.write_bytes(b"Not an executable")
        with mock.patch.object(reader, "run_jsonl") as transport, self.assertRaises(reader.VerificationError):
            reader.readback(SimpleNamespace(archive=changed, runner=runner, output=self.root / "readback",
                expected_archive_sha256=reader.sha256_file(changed),
                expected_runner_sha256=reader.sha256_file(runner), source_commit="1" * 40))
        transport.assert_not_called()


class BoundedProcessTests(FixtureCase):
    def test_request_bytes_are_included_in_aggregate_budget_before_start(self):
        checks = [{"id": 0, "command": "list_projects", "args": {"request": {"cursor": None, "limit": 100}}}]
        with mock.patch.object(reader.subprocess, "Popen") as start, self.assertRaises(reader.VerificationError):
            reader.run_jsonl(Path(sys.executable).resolve(), self.root / "not-opened.sqlite3", checks,
                self.root / "not-created", reader.MAX_STDERR_BYTES + 1)
        start.assert_not_called()
        self.assertFalse((self.root / "not-created").exists())

    def child(self, code, *, maximum=4096, timeout=2):
        original_popen = reader.subprocess.Popen
        commands = []
        def python_child(command, **kwargs):
            commands.append(command)
            return original_popen([sys.executable, "-B", "-I", "-S", "-c", code], **kwargs)
        with mock.patch.object(reader.subprocess, "Popen", side_effect=python_child), \
                mock.patch.object(reader, "MAX_STDOUT_BYTES", maximum), mock.patch.object(reader, "TIMEOUT_SECONDS", timeout):
            try:
                result = reader.run_jsonl(Path(sys.executable).resolve(), self.root / "unopened-unit.sqlite3",
                    [{"id": 0, "command": "list_projects", "args": {"request": {"cursor": None, "limit": 100}}}],
                    self.root / "logs", reader.MAX_TOTAL_LOG_BYTES)
            finally:
                self.assertEqual(len(commands), 1, "Unknown or failed processes must never be retried")
        return result

    def test_real_child_captures_complete_stdout_stderr_and_eof(self):
        raw, receipt = self.child("import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data); sys.stderr.write('diagnostic')")
        self.assertTrue(receipt["complete"])
        self.assertEqual(receipt["state"], "PASS")
        self.assertEqual(receipt["stdin_sha256"], reader.sha256_bytes(raw))
        self.assertEqual(receipt["stderr"]["sha256"], reader.sha256_bytes(b"diagnostic"))

    def test_real_child_stdout_limit_retains_prefix_but_never_passes(self):
        with self.assertRaises(reader.VerificationError):
            self.child("import sys; sys.stdout.buffer.write(b'x'*8192)", maximum=1024)
        report = reader.checked_json_file(self.root / "logs" / "process.json")
        self.assertFalse(report["complete"])
        self.assertTrue(report["output_limit_exceeded"])
        self.assertEqual((self.root / "logs" / "responses.jsonl").stat().st_size, 1024)

    def test_real_child_stderr_limit_retains_prefix_but_never_passes(self):
        with self.assertRaises(reader.VerificationError):
            self.child("import sys; sys.stderr.buffer.write(b'e'*131072)")
        report = reader.checked_json_file(self.root / "logs" / "process.json")
        self.assertFalse(report["complete"])
        self.assertTrue(report["output_limit_exceeded"])
        self.assertLessEqual((self.root / "logs" / "stderr.log").stat().st_size, reader.MAX_STDERR_BYTES)

    def test_real_child_nonzero_exit_keeps_original_log_and_fails(self):
        with self.assertRaises(reader.VerificationError):
            self.child("import sys; sys.stdout.write('original failure'); sys.exit(7)")
        report = reader.checked_json_file(self.root / "logs" / "process.json")
        self.assertEqual(report["exit_code"], 7)
        self.assertFalse(report["complete"])
        self.assertEqual((self.root / "logs" / "responses.jsonl").read_bytes(), b"original failure")

    def test_real_child_timeout_is_unknown_with_no_retry(self):
        with self.assertRaises(reader.ReadbackUnknown):
            self.child("import time; time.sleep(2)", timeout=0.05)
        report = reader.checked_json_file(self.root / "logs" / "process.json")
        self.assertEqual(report["state"], "UNKNOWN")
        self.assertFalse(report["complete"])
        self.assertTrue(report["timed_out"])
        self.assertFalse(report["retry_attempted"])


if __name__ == "__main__":
    unittest.main()
