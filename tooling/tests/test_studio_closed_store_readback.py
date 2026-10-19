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
