"""Adversarial /2 selection tests. Synthetic bytes are not native render proof.

Pure contract tests use explicit handles, never a renderer. LinuxFilesystemTests
exercise real no-follow handles and actual Git blob/physical-source divergence.
They are intentionally skipped on the Windows authoring host.
"""
from __future__ import annotations
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_evidence_projection as n

CTX = {"source_sha": "1"*40, "semwright_sha": "2"*40,
       "run_id": "123", "run_attempt": "1", "scenario": "unit-fixture"}
POLICIES = {"terminal-v2": n.Object({
    "native_outcome": n.Field(n.String(values=("SUCCESS_REPORTED", "UNKNOWN"))),
    "automatic_retry": n.Field(n.Literal(False)),
    "process": n.Field(n.Object({"pid": n.Field(n.Number(minimum=1, integer=True)),
                                 "start_ticks": n.Field(n.String(pattern=r"[0-9]+"))}), False),
})}
ROLES = {("original", "readback", "media"): (1, 1),
         ("original", "attempt-custody", "terminal"): (1, 1)}
RAW = b'\x00\x00\x00\x18ftypSYNTHETIC-NOT-A-VIDEO'
SECRET = "SENTINEL_RAW_TICKET_OR_ENVIRONMENT"
TERMINAL = n.canonical({"native_outcome": "SUCCESS_REPORTED", "automatic_retry": False,
                       "canonical_failure_diagnostic": SECRET,
                       "observer_error_details": {"environ": SECRET}})
def spec_for(files=None):
    files = files or {("owner", "media.mp4"): RAW, ("owner", "terminal.json"): TERMINAL}
    members = []
    for path, role, layer, kind, policy in (
        ("media.mp4", "media", "readback", "original", None),
        ("terminal.json", "terminal", "attempt-custody", "json-projection", "terminal-v2"),
    ):
        raw = files[("owner", path)]
        members.append({"root": "owner", "relative_path": path, "role": role,
          "layer": layer, "phase": "original", "kind": kind, "required": True,
          "expected_sha256": n.sha(raw), "bytes": len(raw), "policy": policy})
    return {"schema": n.SPEC_SCHEMA, "context": dict(CTX),
            "roots": {"owner": "/virtual/owner", "source": "/virtual/source"},
            "members": members,
            "exclusions": [{"root": "owner", "relative_path": "runtime/motionwright.session",
                            "reason": "outside-reviewed-scope"},
                           {"root": "owner", "relative_path": "observer-diagnostic.json",
                            "reason": "outside-reviewed-scope"}],
            "source_bindings": [{"path": "tooling/native_evidence_projection.py", "sha256": "a"*64},
                                {"path": "producer.py", "sha256": "b"*64}],
            "origin": {"kind": "generated-first-party-fixture", "fixture_id": "unit-fixture",
                       "producer_paths": ["producer.py"], "private_data": True,
                       "upload_authorization": "NOT_GRANTED_BY_COLLECTOR"}}

class MemoryHandles:
    def __init__(self, files):
        self.files = dict(files)
        self.marks = {key: (1, i+10, 33152, 1, 1, len(raw), 100, 100, 1)
                      for i, (key, raw) in enumerate(files.items())}
        self.reads, self.stats, self.counts = [], [], {}
        self.stable = True
        self.hook = None
        self.changed_after_read = None
    def stat_member(self, root, parts):
        key = root, "/".join(parts)
        self.stats.append(key); self.counts[key] = self.counts.get(key, 0)+1
        if key not in self.files: raise FileNotFoundError()
        mark = self.marks[key]
        if self.changed_after_read == key and self.counts[key] > 1:
            return (*mark[:6], 101, *mark[7:])
        return mark
    def read(self, root, parts, maximum):
        key = root, "/".join(parts); self.reads.append(key)
        if self.hook: self.hook()
        raw = self.files[key]
        if len(raw) > maximum: raise n.SelectionError("member-byte-budget")
        return raw, self.marks[key]
    def stable_roots(self): return self.stable
    def close(self): pass

def rewrite_tar(raw, mutate_manifest=None, extra=None, mutate_payload=None, mutate_header=None):
    out = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as source, \
         tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as target:
        for info in source.getmembers():
            data = source.extractfile(info).read()
            if info.name == "selected-evidence-manifest.json" and mutate_manifest:
                d = n.strict(data); mutate_manifest(d); data = n.canonical(d)
            if mutate_payload: data = mutate_payload(info.name, data)
            if mutate_header: mutate_header(info)
            info.size = len(data); target.addfile(info, io.BytesIO(data))
        if extra:
            info, data = extra
            target.addfile(info, io.BytesIO(data) if data is not None else None)
    return out.getvalue()


def rewrite_first_local_pax(raw, mutate):
    """Change only a physical extension payload, preserving selected members."""
    offset = 0
    while offset + 512 <= len(raw) and raw[offset:offset + 512] != b"\0" * 512:
        info = tarfile.TarInfo.frombuf(raw[offset:offset + 512], "utf-8", "strict")
        end = offset + 512 + ((info.size + 511) // 512) * 512
        if info.type == tarfile.XHDTYPE:
            payload = mutate(raw[offset + 512:offset + 512 + info.size])
            info.size = len(payload)
            return (raw[:offset] + info.tobuf(format=tarfile.USTAR_FORMAT)
                    + payload + b"\0" * ((-len(payload)) % 512) + raw[end:])
        offset = end
    raise AssertionError("Fixture has no local PAX path record")


def rewrite_first_record_padding(raw, kind, *, at_end):
    """Change one padding byte without touching headers or selected payloads."""
    offset = 0
    while offset + 512 <= len(raw) and raw[offset:offset + 512] != b"\0" * 512:
        info = tarfile.TarInfo.frombuf(raw[offset:offset + 512], "utf-8", "strict")
        start = offset + 512 + info.size
        end = offset + 512 + ((info.size + 511) // 512) * 512
        if info.type == kind and end > start:
            if any(raw[start:end]):
                raise AssertionError("Fixture padding must initially be zero")
            position = end - 1 if at_end else start
            return raw[:position] + b"X" + raw[position + 1:]
        offset = end
    raise AssertionError("Fixture has no eligible physical record padding")

class SelectionContractTests(unittest.TestCase):
    def setUp(self):
        self.files = {("owner","media.mp4"): RAW, ("owner","terminal.json"): TERMINAL}
        self.spec = spec_for(self.files)
        self.roles = dict(ROLES)
        self.handles = MemoryHandles(self.files)
    def capture(self):
        with mock.patch.object(n, "RootHandles", return_value=self.handles), \
             mock.patch.object(n, "_verify_sources") as sources:
            raw, manifest = n.capture_selected_originals(self.spec, policies=POLICIES,
                                                        role_contract=self.roles)
            self.assertEqual(sources.call_count, 2)
            return raw, manifest
    def inspect(self, raw, context=None):
        return n.inspect_selected_tar(raw, expected_context=context or CTX,
                                      role_contract=ROLES, policies=POLICIES)
    def test_projection_has_new_identity_and_no_embedded_diagnostic_bytes(self):
        raw, m = self.capture()
        self.assertNotIn(SECRET.encode(), raw)
        terminal = next(x for x in m["members"] if x["role"] == "terminal")
        self.assertNotEqual(terminal["retained_sha256"], terminal["expected_sha256"])
        self.assertFalse(terminal["original_bytes"])
        self.assertTrue(m["required_layers_complete"])
        self.assertFalse(m["physical_acceptance"])
        self.assertFalse(m["human_acceptance"])
        self.assertEqual(self.inspect(raw), m)
    def test_exclusions_are_never_statted_opened_or_hashed(self):
        self.capture()
        for root, path in self.handles.reads + self.handles.stats:
            self.assertFalse(n.excluded(n.member_path(path)))
        self.assertEqual(len(self.handles.reads), 2)
    def test_required_excluded_paths_fail_before_any_root_is_opened(self):
        for path in ["runtime/motionwright.session", "edited1040/runtime/a",
                     "config/connection.json", "home/profile", "state/grants",
                     "observer-diagnostic.json", "observer/environ", "native.stdout",
                     "logs/native.txt", "work/result.stderr"]:
            with self.subTest(path=path):
                spec = copy.deepcopy(self.spec); spec["members"][0]["relative_path"] = path
                with mock.patch.object(n, "RootHandles") as handles:
                    with self.assertRaises(n.SelectionError):
                        n.capture_selected_originals(spec, policies=POLICIES, role_contract=ROLES)
                    handles.assert_not_called()
    def test_traversal_and_alias_spellings_fail_before_read(self):
        for path in ["../a", "a/../b", "/abs", "a//b", "a\\b", "C:/a", "./a", "a/\x00"]:
            with self.subTest(path=path), self.assertRaises(n.SelectionError):
                n.member_path(path)
    def test_missing_original_blocks_readback_but_keeps_terminal_projection(self):
        self.handles.files.pop(("owner","media.mp4"))
        _, m = self.capture()
        self.assertEqual(m["members"][0]["state"], "REQUIRED_MISSING")
        self.assertEqual(m["members"][1]["state"], "INCLUDED")
        self.assertFalse(m["required_layers_complete"])
        self.assertNotIn(("owner","media.mp4"), self.handles.reads)
    def test_unobserved_identity_does_not_read_existing_payload(self):
        self.spec["members"][0].update(expected_sha256=None, bytes=None)
        _, m = self.capture()
        self.assertEqual(m["members"][0]["state"], "CAPTURE_FAILED")
        self.assertNotIn(("owner","media.mp4"), self.handles.reads)
        self.assertFalse(m["required_layers_complete"])
    def test_unobserved_missing_is_required_missing_not_intentional_exclusion(self):
        self.spec["members"][0].update(expected_sha256=None, bytes=None)
        self.handles.files.pop(("owner","media.mp4"))
        _, m = self.capture()
        self.assertEqual(m["members"][0]["state"], "REQUIRED_MISSING")
    def test_changed_expected_digest_is_not_included(self):
        self.spec["members"][0]["expected_sha256"] = "f"*64
        _, m = self.capture()
        self.assertEqual(m["members"][0]["state"], "CHANGED")
        self.assertFalse(m["required_layers_complete"])
    def test_changed_after_read_blocks_custody_without_second_payload_read(self):
        self.handles.changed_after_read = ("owner","media.mp4")
        _, m = self.capture()
        self.assertEqual(m["members"][0]["state"], "CHANGED")
        self.assertFalse(m["required_layers_complete"])
        self.assertEqual(self.handles.reads.count(("owner","media.mp4")), 1)
    def test_root_namespace_change_blocks_layers(self):
        self.handles.stable = False
        _, m = self.capture()
        self.assertFalse(m["required_layers_complete"])
    def test_role_cannot_be_downgraded_to_optional(self):
        self.spec["members"][0]["required"] = False
        with self.assertRaises(n.SelectionError):
            self.capture()
    def test_missing_required_role_cannot_pass_empty_all(self):
        self.spec["members"] = self.spec["members"][1:]
        with self.assertRaises(n.SelectionError):
            self.capture()
    def test_phase_swap_is_not_a_second_original(self):
        self.spec["members"][0]["phase"] = "edited"
        with self.assertRaises(n.SelectionError):
            self.capture()
    def test_duplicate_selected_inode_is_not_captured_twice(self):
        self.handles.marks[("owner","terminal.json")] = self.handles.marks[("owner","media.mp4")]
        _, m = self.capture()
        self.assertEqual(m["members"][1]["state"], "CAPTURE_FAILED")
        self.assertFalse(m["required_layers_complete"])
        self.assertNotIn(("owner","terminal.json"), self.handles.reads)
    def test_mutating_input_spec_during_capture_cannot_add_a_member(self):
        def mutate():
            self.spec["members"].append({**self.spec["members"][0], "relative_path": "secret.bin"})
        self.handles.hook = mutate
        raw, m = self.capture()
        self.assertEqual(len(m["members"]), 2)
        self.assertNotIn(b"secret.bin", raw)
    def test_file_added_after_missing_selection_is_never_read(self):
        self.handles.files.pop(("owner","media.mp4"))
        self.handles.hook = lambda: self.handles.files.update({("owner","media.mp4"): RAW})
        _, m = self.capture()
        self.assertEqual(m["members"][0]["state"], "CHANGED")
        self.assertFalse(m["required_layers_complete"])
        self.assertNotIn(("owner","media.mp4"), self.handles.reads)
    def test_json_original_requires_closed_schema_and_preserves_exact_bytes(self):
        clean = b'{ "native_outcome": "SUCCESS_REPORTED", "automatic_retry": false }\n'
        item = self.spec["members"][1]
        item.update(kind="json-original", expected_sha256=n.sha(clean), bytes=len(clean))
        self.handles.files[("owner","terminal.json")] = clean
        mark = self.handles.marks[("owner","terminal.json")]
        self.handles.marks[("owner","terminal.json")] = (*mark[:5],len(clean),*mark[6:])
        raw, m = self.capture()
        self.assertEqual(m["members"][1]["retained_sha256"], n.sha(clean))
        self.assertTrue(m["members"][1]["original_bytes"])
        self.inspect(raw)
    def test_json_original_rejects_embedded_unknown_field_instead_of_silent_loss(self):
        self.spec["members"][1]["kind"] = "json-original"
        _, m = self.capture()
        self.assertEqual(m["members"][1]["state"], "CAPTURE_FAILED")
        self.assertFalse(m["required_layers_complete"])
    def test_json_cannot_use_unchecked_binary_kind(self):
        self.spec["members"][1].update(kind="original", policy=None)
        with self.assertRaises(n.SelectionError): self.capture()
    def test_unknown_json_policy_fails_closed(self):
        self.spec["members"][1]["policy"] = "raw-json"
        with self.assertRaises(n.SelectionError): self.capture()
    def test_wrong_source_run_attempt_or_scenario_rejects_intake(self):
        raw, _ = self.capture()
        for key, value in [("source_sha","3"*40),("run_id","124"),
                           ("run_attempt","2"),("scenario","other-fixture")]:
            with self.subTest(key=key), self.assertRaises(n.SelectionError):
                self.inspect(raw, {**CTX,key:value})
    def test_full_native_job_projection_names_round_trip_without_truncation(self):
        for suffix in ("intent", "binding"):
            self.setUp()
            relative = "native-job-hf-" + "a" * 64 + "." + suffix + ".json"
            row = next(r for r in self.spec["members"] if r["role"] == "terminal")
            row["relative_path"] = relative
            self.files[("owner", relative)] = self.files.pop(("owner", "terminal.json"))
            self.handles = MemoryHandles(self.files)
            with self.subTest(suffix=suffix):
                try:
                    raw, manifest = self.capture()
                except ValueError as error:
                    self.fail("Full native job filename was not preserved: " + str(error))
                retained = next(r for r in manifest["members"] if r["role"] == "terminal")
                expected = "selected/original/attempt-custody/owner/" + relative + ".projection.json"
                self.assertEqual(retained["member"], expected)
                self.assertEqual(len(expected.rsplit("/", 1)[1]), 106 if suffix == "intent" else 107)
                self.assertEqual(self.inspect(raw), manifest)
                with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
                    info = archive.getmember(expected)
                    self.assertEqual(info.pax_headers, {"path": expected})
                again, _ = self.capture()
                self.assertEqual(raw, again)

    def test_long_original_name_keeps_255_byte_path_budget(self):
        prefix = "selected/original/readback/owner/"
        for size in (255, 256):
            self.setUp()
            relative = "a" * 100 + "/" + "b" * (size - len(prefix) - 101)
            row = self.spec["members"][0]
            row["relative_path"] = relative
            self.files[("owner", relative)] = self.files.pop(("owner", "media.mp4"))
            self.handles = MemoryHandles(self.files)
            with self.subTest(size=size):
                if size == 256:
                    with self.assertRaises(n.SelectionError):
                        self.capture()
                else:
                    raw, manifest = self.capture()
                    self.assertEqual(self.inspect(raw), manifest)
                    self.assertEqual(manifest["members"][0]["member"], prefix + relative)

    def test_pax_metadata_other_than_exact_local_path_is_rejected(self):
        raw, _ = self.capture()
        for metadata in ({"comment": "unselected metadata"}, {"size": "1"},
                         {"linkpath": "other"}, {"GNU.sparse.map": "0,1"},
                         {"path": "../outside"}, {"path": "/absolute"}):
            def header(info):
                if info.name != "selected-evidence-manifest.json":
                    info.pax_headers = metadata
            with self.subTest(metadata=metadata), \
                 self.assertRaises((n.SelectionError, tarfile.TarError)):
                self.inspect(rewrite_tar(raw, mutate_header=header))

    def test_physical_global_and_gnu_headers_cannot_hide_in_effective_members(self):
        raw, _ = self.capture()
        for kind, payload in ((tarfile.XGLTYPE, b""),
                              (tarfile.XGLTYPE, b"14 comment=x\n"),
                              (tarfile.GNUTYPE_LONGNAME, b"selected/original/readback/owner/media.mp4\0"),
                              (tarfile.GNUTYPE_LONGLINK, b"other\0")):
            info = tarfile.TarInfo("././@PaxHeader" if kind == tarfile.XGLTYPE else "././@LongLink")
            info.type = kind; info.size = len(payload)
            extra = info.tobuf(format=tarfile.USTAR_FORMAT) + payload
            extra += b"\0" * ((-len(payload)) % 512)
            with self.subTest(kind=kind, empty=not payload), \
                 self.assertRaises((n.SelectionError, tarfile.TarError)):
                self.inspect(extra + raw)


    def long_projection(self):
        relative = "native-job-hf-" + "a" * 64 + ".intent.json"
        row = next(r for r in self.spec["members"] if r["role"] == "terminal")
        row["relative_path"] = relative
        self.files[("owner", relative)] = self.files.pop(("owner", "terminal.json"))
        self.handles = MemoryHandles(self.files)
        raw, manifest = self.capture()
        self.assertEqual(self.inspect(raw), manifest)
        return raw

    def test_local_pax_duplicate_path_records_are_rejected(self):
        raw = self.long_projection()
        cases = (
            ("different-first-path", lambda p: b"46 path=G0_UNSELECTED_DUPLICATE_PATH_SENTINEL\n" + p),
            ("same-path-twice", lambda p: p + p),
            ("invalid-utf8-hidden-first-path", lambda p: b"10 path=\xff\n" + p),
        )
        for label, change in cases:
            with self.subTest(case=label), \
                 self.assertRaises((n.SelectionError, tarfile.TarError, UnicodeError)):
                self.inspect(rewrite_first_local_pax(raw, change))

    def test_local_pax_payload_ends_after_the_exact_path_record(self):
        raw = self.long_projection()
        for suffix in (b"G0_UNSELECTED_TRAILING_RECORD_SENTINEL", b"\0\0"):
            with self.subTest(suffix=suffix), \
                 self.assertRaises((n.SelectionError, tarfile.TarError, UnicodeError)):
                self.inspect(rewrite_first_local_pax(raw, lambda p: p + suffix))

    def test_local_pax_record_length_uses_canonical_ascii_digits(self):
        raw = self.long_projection()
        def leading_zero(payload):
            _, body = payload.split(b" ", 1)
            length = len(payload) + 1
            changed = b"0" + str(length).encode("ascii") + b" " + body
            self.assertEqual(len(changed), length)
            return changed
        with self.assertRaises((n.SelectionError, tarfile.TarError, UnicodeError)):
            self.inspect(rewrite_first_local_pax(raw, leading_zero))


    def test_physical_record_padding_is_zero_filled(self):
        raw = self.long_projection()
        for kind in (tarfile.XHDTYPE, tarfile.REGTYPE):
            for at_end in (False, True):
                with self.subTest(kind=kind, at_end=at_end), \
                     self.assertRaises(n.SelectionError):
                    self.inspect(rewrite_first_record_padding(raw, kind, at_end=at_end))

    def test_extra_tar_member_is_rejected(self):
        raw, _ = self.capture()
        info = tarfile.TarInfo("unselected.bin"); info.size = len(SECRET)
        with self.assertRaises(n.SelectionError):
            self.inspect(rewrite_tar(raw, extra=(info, SECRET.encode())))
    def test_tar_symlink_is_rejected(self):
        raw, _ = self.capture()
        info = tarfile.TarInfo("alias"); info.type = tarfile.SYMTYPE; info.linkname = "/etc/passwd"
        with self.assertRaises(n.SelectionError):
            self.inspect(rewrite_tar(raw, extra=(info,None)))
    def test_tar_duplicate_manifest_is_rejected(self):
        raw, _ = self.capture()
        info = tarfile.TarInfo("selected-evidence-manifest.json"); info.size = 3
        with self.assertRaises(n.SelectionError):
            self.inspect(rewrite_tar(raw, extra=(info,b"{}\n")))
    def test_original_and_retained_digest_cannot_diverge(self):
        raw, _ = self.capture()
        def change(m): m["members"][0]["expected_sha256"] = "f"*64
        with self.assertRaises(n.SelectionError):
            self.inspect(rewrite_tar(raw, mutate_manifest=change))
    def test_unknown_or_capture_failure_cannot_be_verified_by_completeness_flag(self):
        raw, _ = self.capture()
        def change(m): m["members"][0]["state"] = "CAPTURE_FAILED"
        with self.assertRaises(n.SelectionError):
            self.inspect(rewrite_tar(raw, mutate_manifest=change))
    def test_exclusion_cannot_be_used_to_relabel_required_missing(self):
        raw, _ = self.capture()
        def change(m): m["members"][0]["state"] = "EXCLUDED_BY_SCOPE"
        with self.assertRaises(n.SelectionError):
            self.inspect(rewrite_tar(raw, mutate_manifest=change))
    def test_scope_flags_do_not_promote_acceptance_cleanup_or_replay(self):
        raw, _ = self.capture()
        for flag in ["human_acceptance","physical_acceptance","release_authorized",
                     "complete_owner_state_claimed","atomic_sqlite_snapshot_claimed",
                     "automatic_retry","raw_environment_included"]:
            with self.subTest(flag=flag), self.assertRaises(n.SelectionError):
                self.inspect(rewrite_tar(raw, mutate_manifest=lambda m:m.update({flag:True})))
    def test_projection_optional_object_is_closed_at_all_depths(self):
        value = {"native_outcome":"UNKNOWN","automatic_retry":False,
                 "process":{"pid":9,"start_ticks":"123","environ":SECRET}}
        result = n.project(value, POLICIES["terminal-v2"])
        self.assertEqual(result["process"], {"pid":9,"start_ticks":"123"})
        self.assertNotIn(SECRET, json.dumps(result))
    def test_projection_rejects_nonfinite_boolean_numbers_and_unbounded_arrays(self):
        for value, rule in [(float("nan"),n.Number()),(True,n.Number()),
                            ([1,2],n.Array(n.Number(),1)),("a\nb",n.String())]:
            with self.subTest(value=value), self.assertRaises(n.SelectionError):
                n.project(value,rule)
    def test_duplicate_keys_and_nonfinite_json_fail_before_projection(self):
        for raw in [b'{"a":1,"a":2}', b'{"a":NaN}',b'{"a":Infinity}']:
            with self.assertRaises(n.SelectionError): n.strict(raw)
    def test_source_mutation_fails_binding_before_capture(self):
        with mock.patch.object(n,"RootHandles",return_value=self.handles), \
             mock.patch.object(n,"_verify_sources",side_effect=n.SelectionError("physical-source-differs-from-git")):
            with self.assertRaises(n.SelectionError):
                n.capture_selected_originals(self.spec,policies=POLICIES,role_contract=ROLES)
        self.assertEqual(self.handles.reads, [])

    def add_wal_guard(self):
        self.roles[("original","persistence","db-wal")] = (0,1)
        self.spec["absence_guards"] = [{"root":"owner", "relative_path":"db/project.sqlite3-wal",
            "phase":"original", "layer":"persistence", "role":"db-wal"}]
    def test_absent_wal_guard_is_metadata_only_and_stable(self):
        self.add_wal_guard()
        raw,m = self.capture()
        self.assertTrue(m["required_layers_complete"])
        self.assertTrue(m["absence_guards"][0]["absent_before"])
        self.assertTrue(m["absence_guards"][0]["absent_after"])
        self.assertNotIn(("owner","db/project.sqlite3-wal"),self.handles.reads)
        n.inspect_selected_tar(raw,expected_context=CTX,role_contract=self.roles,policies=POLICIES)
    def test_wal_created_in_subdirectory_after_absence_blocks_persistence(self):
        self.add_wal_guard()
        def create_wal():
            key=("owner","db/project.sqlite3-wal")
            self.handles.files[key]=b"WAL"
            self.handles.marks[key]=(1,100,33152,1,1,3,100,100,1)
        self.handles.hook=create_wal
        raw,m=self.capture()
        self.assertTrue(self.handles.stable)  # Parent root itself did not change.
        self.assertFalse(m["required_layers_complete"])
        self.assertTrue(m["absence_guards"][0]["absent_before"])
        self.assertFalse(m["absence_guards"][0]["absent_after"])
        self.assertNotIn(("owner","db/project.sqlite3-wal"),self.handles.reads)
        with self.assertRaises(n.SelectionError):
            n.inspect_selected_tar(raw,expected_context=CTX,role_contract=self.roles,policies=POLICIES)
    def test_existing_wal_cannot_be_described_as_absent(self):
        self.add_wal_guard()
        key=("owner","db/project.sqlite3-wal")
        self.handles.files[key]=b"WAL";self.handles.marks[key]=(1,100,33152,1,1,3,100,100,1)
        _,m=self.capture()
        self.assertFalse(m["required_layers_complete"])
        self.assertFalse(m["absence_guards"][0]["absent_before"])
        self.assertNotIn(key,self.handles.reads)
    def test_optional_selected_capture_failure_still_blocks_its_layer(self):
        self.roles[("original","persistence","db-wal")]=(0,1)
        self.spec["members"].append({"root":"owner","relative_path":"db/project.sqlite3-wal",
            "phase":"original","layer":"persistence","role":"db-wal","kind":"original",
            "required":False,"expected_sha256":None,"bytes":None,"policy":None})
        _,m=self.capture()
        self.assertFalse(m["required_layers_complete"])
        layer=next(x for x in m["layers"] if x["layer"]=="persistence")
        self.assertFalse(layer["complete"])
    def test_absence_guard_cannot_replace_a_required_role(self):
        self.spec["absence_guards"]=[{"root":"owner","relative_path":"missing.mp4",
            "phase":"original","layer":"readback","role":"media"}]
        with self.assertRaises(n.SelectionError):self.capture()
    def test_pre_effect_source_preflight_uses_same_actual_source_validator(self):
        with mock.patch.object(n,"RootHandles",return_value=self.handles), \
             mock.patch.object(n,"_verify_sources") as verifier:
            proof=n.verify_source_bindings("/virtual/source",CTX["source_sha"],
                                            self.spec["source_bindings"])
        verifier.assert_called_once()
        self.assertTrue(proof["source_bytes_bound"])
        self.assertEqual(proof["native_effects"],0)

class DiagnosticSelectionV3Tests(unittest.TestCase):
    """Opaque diagnostic custody is not a producer EOF or acceptance oracle."""
    def setUp(self):
        self.files = {("owner", "media.mp4"): RAW, ("owner", "terminal.json"): TERMINAL}
        self.spec = spec_for(self.files)
        self.spec["schema"] = n.SPEC_SCHEMA_V3
        self.roles = dict(ROLES)
        self.contract = {"schema": n.DIAGNOSTIC_CONTRACT_SCHEMA,
                         "context": dict(CTX), "entries": []}
        for subtype, data in (("stdout", b"stdout-prefix\0" + SECRET.encode()),
                ("stderr", b"stderr-prefix\xff"), ("stdin", b""),
                ("typed-terminal", n.canonical({"native_outcome": "UNKNOWN",
                    "automatic_retry": False, "process": {"pid": 123, "start_ticks": "9"}}))):
            terminal = subtype == "typed-terminal"
            path = "attempt00/" + subtype + (".original.json" if terminal else ".original.bin")
            role = "diagnostic-" + subtype
            policy = "terminal-v2" if terminal else None
            self.files[("owner", path)] = data
            self.roles[("original", "attempt-custody", role)] = (0, 1)
            self.contract["entries"].append({"root": "owner", "relative_path": path,
                "phase": "original", "layer": "attempt-custody", "role": role,
                "type": subtype, "maximum": n.DIAGNOSTIC_MAXIMUM[subtype], "policy": policy})
            self.spec["members"].append({"root": "owner", "relative_path": path,
                "phase": "original", "layer": "attempt-custody", "role": role,
                "kind": "diagnostic-json-original" if terminal else "diagnostic-original",
                "diagnostic_type": subtype, "required": True, "expected_sha256": n.sha(data),
                "bytes": len(data), "policy": policy})
        self.handles = MemoryHandles(self.files)

    def capture(self, *, spec=None, contract=None, roles=None):
        with mock.patch.object(n, "RootHandles", return_value=self.handles), \
             mock.patch.object(n, "_verify_sources") as sources:
            result = n.capture_selected_originals(spec or self.spec, policies=POLICIES,
                role_contract=roles or self.roles,
                diagnostic_contract=self.contract if contract is None else contract)
            self.assertEqual(sources.call_count, 2)
            return result

    def inspect(self, raw, *, contract=None):
        return n.inspect_selected_tar(raw, expected_context=CTX, role_contract=self.roles,
            policies=POLICIES, expected_diagnostic_contract=self.contract if contract is None else contract)

    def reject_before_open(self, *, spec=None, contract=None, roles=None):
        with mock.patch.object(n, "RootHandles") as opened:
            with self.assertRaises(n.SelectionError):
                n.capture_selected_originals(spec or self.spec, policies=POLICIES,
                    role_contract=roles or self.roles,
                    diagnostic_contract=self.contract if contract is None else contract)
            opened.assert_not_called()

    def guard(self, subtype):
        entry = next(e for e in self.contract["entries"] if e["type"] == subtype)
        path = (entry["root"], entry["relative_path"])
        self.spec["members"] = [r for r in self.spec["members"]
                                if (r["root"], r["relative_path"]) != path]
        self.spec.setdefault("absence_guards", []).append({k: entry[k]
            for k in ("root", "relative_path", "phase", "layer", "role")})
        self.handles.files.pop(path, None)
        return path

    def test_complete_four_subtypes_retain_exact_bytes_without_eof_or_acceptance(self):
        raw, manifest = self.capture()
        self.assertEqual(manifest["schema"], n.MANIFEST_SCHEMA_V3)
        self.assertEqual(self.inspect(raw), manifest)
        self.assertEqual(manifest["diagnostic_contract"], self.contract)
        self.assertEqual(manifest["diagnostic_contract_sha256"], n.sha(n.canonical(self.contract)))
        self.assertTrue(manifest["raw_diagnostics_included"])
        self.assertTrue(manifest["diagnostic_roster_complete"])
        self.assertIn("unselected-raw-diagnostics", manifest["lost_scope"])
        self.assertNotIn("raw-diagnostics", manifest["lost_scope"])
        for key in ("diagnostic_eof_claimed", "diagnostic_history_complete_claimed",
                    "diagnostic_payloads_redacted", "raw_environment_included",
                    "physical_acceptance", "human_acceptance", "automatic_retry"):
            self.assertIs(manifest[key], False)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for row in manifest["members"]:
                if row["kind"] in n.DIAGNOSTIC_KINDS:
                    self.assertEqual(archive.extractfile(row["member"]).read(),
                                     self.files[(row["root"], row["relative_path"])])
        self.assertIn(SECRET.encode(), raw)  # Original opaque bytes, never a redaction claim.

    def test_v3_requires_nonempty_external_contract_before_capture_and_at_intake(self):
        for contract in (None, {}, {**self.contract, "entries": []}):
            with self.subTest(contract=contract), mock.patch.object(n, "RootHandles") as opened:
                with self.assertRaises(n.SelectionError):
                    n.capture_selected_originals(self.spec, policies=POLICIES,
                        role_contract=self.roles, diagnostic_contract=contract)
                opened.assert_not_called()
        raw, _ = self.capture()
        with self.assertRaises(n.SelectionError):
            n.inspect_selected_tar(raw, expected_context=CTX, role_contract=self.roles, policies=POLICIES)

    def test_legacy_rejects_contract_extensions_and_diagnostic_kinds(self):
        old = spec_for()
        self.reject_before_open(spec=old, roles=ROLES)
        changed = copy.deepcopy(self.spec); changed["schema"] = n.SPEC_SCHEMA
        with mock.patch.object(n, "RootHandles") as opened, self.assertRaises(n.SelectionError):
            n.capture_selected_originals(changed, policies=POLICIES, role_contract=self.roles)
        opened.assert_not_called()
        with mock.patch.object(n, "RootHandles", return_value=self.handles), \
             mock.patch.object(n, "_verify_sources"):
            raw, manifest = n.capture_selected_originals(old, policies=POLICIES, role_contract=ROLES)
        self.assertFalse(set(manifest) & n.DIAGNOSTIC_MANIFEST_FIELDS)
        self.assertEqual(manifest["lost_scope"], list(n.LOST_SCOPE))
        with self.assertRaises(n.SelectionError):
            n.inspect_selected_tar(raw, expected_context=CTX, role_contract=ROLES,
                policies=POLICIES, expected_diagnostic_contract=self.contract)
        forged = rewrite_tar(raw, lambda m: m.update(diagnostic_contract=self.contract))
        with self.assertRaises(n.SelectionError):
            n.inspect_selected_tar(forged, expected_context=CTX, role_contract=ROLES, policies=POLICIES)

    def test_contract_cannot_transfer_between_sources_runs_attempts_or_scenarios(self):
        for key, value in (("source_sha", "3"*40), ("semwright_sha", "4"*40),
                ("run_id", "124"), ("run_attempt", "2"), ("scenario", "other-fixture")):
            contract = copy.deepcopy(self.contract); contract["context"][key] = value
            with self.subTest(key=key): self.reject_before_open(contract=contract)
        raw, _ = self.capture()
        contract = copy.deepcopy(self.contract); contract["context"]["run_id"] = "124"
        with self.assertRaises(n.SelectionError): self.inspect(raw, contract=contract)

    def test_contract_fields_types_policies_and_maxima_are_closed(self):
        for key, value in (("maximum", True), ("maximum", 262145), ("maximum", 0),
                ("type", "environment"), ("layer", "readback"), ("policy", "terminal-v2")):
            contract = copy.deepcopy(self.contract); contract["entries"][0][key] = value
            with self.subTest(key=key, value=value): self.reject_before_open(contract=contract)
        for policy in (None, "unregistered-policy"):
            contract = copy.deepcopy(self.contract); contract["entries"][-1]["policy"] = policy
            with self.subTest(policy=policy): self.reject_before_open(contract=contract)
        contract = copy.deepcopy(self.contract); contract["extra"] = True
        self.reject_before_open(contract=contract)
        contract = copy.deepcopy(self.contract); contract["entries"].append(contract["entries"][0])
        self.reject_before_open(contract=contract)

    def test_every_contract_path_must_be_selected_or_guarded_once(self):
        spec = copy.deepcopy(self.spec); spec["members"].pop()
        self.reject_before_open(spec=spec)
        path = self.guard("stdin")
        spec = copy.deepcopy(self.spec); spec["absence_guards"] *= 2
        self.reject_before_open(spec=spec)
        spec = copy.deepcopy(self.spec); spec["absence_guards"] = []
        self.reject_before_open(spec=spec)
        spec = copy.deepcopy(self.spec)
        row = copy.deepcopy(spec["members"][2]); row["relative_path"] = "attempt00/extra.bin"
        spec["members"].append(row)
        roles = dict(self.roles); roles[(row["phase"], row["layer"], row["role"])] = (0, 2)
        self.reject_before_open(spec=spec, roles=roles)
        self.assertNotIn(path, self.handles.reads)

    def test_reserved_member_cannot_downgrade_type_or_move_scope(self):
        for kind in ("original", "json-original"):
            spec = copy.deepcopy(self.spec); row = spec["members"][2]
            row["kind"] = kind; row.pop("diagnostic_type")
            if kind == "json-original": row["policy"] = "terminal-v2"
            with self.subTest(kind=kind): self.reject_before_open(spec=spec)
        spec = copy.deepcopy(self.spec); spec["members"][2]["diagnostic_type"] = "stderr"
        self.reject_before_open(spec=spec)
        spec = copy.deepcopy(self.spec); spec["members"][2]["phase"] = "edited"
        roles = dict(self.roles); roles[("edited", "attempt-custody", "diagnostic-stdout")] = (0, 1)
        self.reject_before_open(spec=spec, roles=roles)

    def test_present_diagnostic_is_required_even_when_its_role_allows_absence(self):
        spec = copy.deepcopy(self.spec); spec["members"][2]["required"] = False
        self.reject_before_open(spec=spec)

    def test_absence_is_observed_bilaterally_without_open_or_hash(self):
        path = self.guard("stdin")
        raw, manifest = self.capture()
        self.assertEqual(self.inspect(raw), manifest)
        self.assertTrue(manifest["diagnostic_roster_complete"])
        self.assertEqual(self.handles.stats.count(path), 2)
        self.assertNotIn(path, self.handles.reads)
        self.assertEqual({k: manifest["absence_guards"][0][k]
            for k in ("absent_before", "absent_after", "opened", "hashed")},
            {"absent_before": True, "absent_after": True, "opened": False, "hashed": False})

    def test_all_diagnostics_observed_absent_do_not_claim_raw_payloads(self):
        for subtype in n.DIAGNOSTIC_MAXIMUM: self.guard(subtype)
        raw, manifest = self.capture()
        self.assertEqual(self.inspect(raw), manifest)
        self.assertFalse(manifest["raw_diagnostics_included"])
        self.assertTrue(manifest["diagnostic_roster_complete"])
        self.assertFalse(manifest["diagnostic_history_complete_claimed"])

    def test_guarded_diagnostic_present_before_capture_blocks_completeness(self):
        path = self.guard("stdin"); self.handles.files[path] = self.files[path]
        raw, manifest = self.capture()
        self.assertFalse(manifest["diagnostic_roster_complete"])
        self.assertFalse(manifest["required_layers_complete"])
        self.assertNotIn(path, self.handles.reads)
        with self.assertRaises(n.SelectionError): self.inspect(raw)

    def test_guarded_diagnostic_appearing_late_is_not_captured_or_admitted(self):
        path = self.guard("stdin")
        self.handles.hook = lambda: self.handles.files.update({path: self.files[path]})
        raw, manifest = self.capture()
        self.assertTrue(manifest["absence_guards"][0]["absent_before"])
        self.assertFalse(manifest["absence_guards"][0]["absent_after"])
        self.assertFalse(manifest["diagnostic_roster_complete"])
        self.assertNotIn(path, self.handles.reads)
        with self.assertRaises(n.SelectionError): self.inspect(raw)

    def test_missing_selected_diagnostic_cannot_be_relabelled_as_absence(self):
        row = self.spec["members"][2]
        self.handles.files.pop((row["root"], row["relative_path"]))
        raw, manifest = self.capture()
        self.assertEqual(manifest["members"][2]["state"], "REQUIRED_MISSING")
        self.assertFalse(manifest["diagnostic_roster_complete"])
        with self.assertRaises(n.SelectionError): self.inspect(raw)

    def test_changed_stream_hash_is_rejected_with_original_selection_retained(self):
        row = self.spec["members"][2]; key = (row["root"], row["relative_path"])
        self.handles.files[key] = b"X" * row["bytes"]
        raw, manifest = self.capture()
        self.assertEqual(manifest["members"][2]["state"], "CHANGED")
        self.assertEqual(manifest["members"][2]["expected_sha256"], row["expected_sha256"])
        with self.assertRaises(n.SelectionError): self.inspect(raw)

    def test_diagnostic_byte_budget_is_checked_before_any_read(self):
        for index, limit in ((2, 262144), (4, 65536), (5, 65536)):
            spec = copy.deepcopy(self.spec); spec["members"][index]["bytes"] = limit + 1
            with self.subTest(index=index): self.reject_before_open(spec=spec)

    def test_postread_changed_diagnostic_remains_truthfully_present_but_incomplete(self):
        for subtype in ("stderr", "stdin", "typed-terminal"): self.guard(subtype)
        row = self.spec["members"][2]
        self.handles.changed_after_read = (row["root"], row["relative_path"])
        raw, manifest = self.capture()
        changed = manifest["members"][2]
        self.assertEqual(changed["state"], "CHANGED")
        self.assertTrue(manifest["raw_diagnostics_included"])
        self.assertFalse(manifest["diagnostic_roster_complete"])
        self.assertFalse(manifest["required_layers_complete"])
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            self.assertEqual(archive.extractfile(changed["member"]).read(),
                             self.files[(row["root"], row["relative_path"])])
        with self.assertRaises(n.SelectionError): self.inspect(raw)

    def test_contract_is_frozen_before_payloads_and_cannot_be_changed_by_caller(self):
        expected = copy.deepcopy(self.contract)
        def mutate(): self.contract["entries"][0]["type"] = "environment"
        self.handles.hook = mutate
        raw, manifest = self.capture()
        self.assertEqual(manifest["diagnostic_contract"], expected)
        self.assertEqual(self.inspect(raw, contract=expected), manifest)

    def test_typed_terminal_requires_strict_closed_json_without_success_promotion(self):
        invalid = [n.canonical({"native_outcome": "UNKNOWN", "automatic_retry": False, "extra": True}),
            n.canonical({"native_outcome": "UNKNOWN", "automatic_retry": 0}),
            n.canonical({"native_outcome": "UNKNOWN", "automatic_retry": False,
                         "process": {"pid": True, "start_ticks": "9"}}),
            b'{"native_outcome":"UNKNOWN","native_outcome":"UNKNOWN","automatic_retry":false}\n']
        for data in invalid:
            self.setUp()
            row = self.spec["members"][-1]; key = (row["root"], row["relative_path"])
            row.update(expected_sha256=n.sha(data), bytes=len(data))
            self.handles.files[key] = data
            mark = self.handles.marks[key]; self.handles.marks[key] = (*mark[:5], len(data), *mark[6:])
            with self.subTest(data=data):
                raw, manifest = self.capture()
                self.assertEqual(manifest["members"][-1]["state"], "CAPTURE_FAILED")
                self.assertFalse(manifest["diagnostic_roster_complete"])
                with self.assertRaises(n.SelectionError): self.inspect(raw)

    def test_self_consistent_manifest_contract_cannot_replace_external_authority(self):
        raw, _ = self.capture()
        def forge(manifest):
            manifest["diagnostic_contract"]["entries"][0]["type"] = "stderr"
            manifest["diagnostic_contract_sha256"] = n.sha(n.canonical(manifest["diagnostic_contract"]))
            manifest["members"][2]["diagnostic_type"] = "stderr"
        with self.assertRaisesRegex(n.SelectionError, "selected-diagnostic-contract-mismatch"):
            self.inspect(rewrite_tar(raw, forge))

    def test_manifest_presence_completeness_and_scope_flags_cannot_be_forged(self):
        raw, _ = self.capture()
        for key, value in (("raw_diagnostics_included", False), ("raw_diagnostics_included", 1),
                ("diagnostic_roster_complete", False), ("diagnostic_eof_claimed", True),
                ("diagnostic_payloads_redacted", True), ("diagnostic_history_complete_claimed", True),
                ("lost_scope", list(n.LOST_SCOPE)), ("physical_acceptance", True)):
            with self.subTest(key=key), self.assertRaises(n.SelectionError):
                self.inspect(rewrite_tar(raw, lambda manifest: manifest.update({key: value})))

    def test_diagnostic_original_hash_and_canonical_tar_path_remain_bound(self):
        raw, _ = self.capture()
        def rehash(manifest): manifest["members"][2]["expected_sha256"] = "0" * 64
        with self.assertRaisesRegex(n.SelectionError, "selected-original-identity"):
            self.inspect(rewrite_tar(raw, rehash))
        def rename(manifest): manifest["members"][2]["member"] = "selected/original/attempt-custody/owner/other.bin"
        with self.assertRaisesRegex(n.SelectionError, "diagnostic-tar-path"):
            self.inspect(rewrite_tar(raw, rename))

    def test_diagnostic_contract_never_permits_excluded_or_unsafe_paths(self):
        for path in ("../outside", "runtime/diag.bin", "logs/diag.bin", "environ", "diag.stderr"):
            spec, contract = copy.deepcopy(self.spec), copy.deepcopy(self.contract)
            spec["members"][2]["relative_path"] = path
            contract["entries"][0]["relative_path"] = path
            with self.subTest(path=path): self.reject_before_open(spec=spec, contract=contract)
        spec, contract = copy.deepcopy(self.spec), copy.deepcopy(self.contract)
        spec["members"][2]["root"] = "source"; contract["entries"][0]["root"] = "source"
        self.reject_before_open(spec=spec, contract=contract)

    def test_source_postcheck_failure_keeps_payload_presence_without_completeness(self):
        with mock.patch.object(n, "RootHandles", return_value=self.handles), \
             mock.patch.object(n, "_verify_sources", side_effect=[None, n.SelectionError("source-changed")]):
            raw, manifest = n.capture_selected_originals(self.spec, policies=POLICIES,
                role_contract=self.roles, diagnostic_contract=self.contract)
        self.assertTrue(manifest["raw_diagnostics_included"])
        self.assertFalse(manifest["source_bytes_bound"])
        self.assertFalse(manifest["diagnostic_roster_complete"])
        with self.assertRaises(n.SelectionError): self.inspect(raw)


class PartialCustodyV3Tests(unittest.TestCase):
    """Partial byte custody preserves failures; it is never a native PASS."""
    setUp = DiagnosticSelectionV3Tests.setUp
    capture = DiagnosticSelectionV3Tests.capture
    guard = DiagnosticSelectionV3Tests.guard

    def inspect_partial(self, raw, *, contract=None):
        return n.inspect_selected_tar(raw, expected_context=CTX, role_contract=self.roles,
            policies=POLICIES, expected_diagnostic_contract=self.contract if contract is None else contract,
            require_complete=False)

    def missing_media(self):
        self.handles.files.pop(("owner", "media.mp4"))
        self.spec["members"][0].update(expected_sha256=None, bytes=None)

    def test_partial_missing_required_outputs_keep_roles_rows_and_original_diagnostics(self):
        self.missing_media()
        self.roles[("original", "readback", "decoder")] = (1, 1)
        self.spec["members"].append({"root": "owner", "relative_path": "decoded.frames.bin",
            "phase": "original", "layer": "readback", "role": "decoder", "kind": "original",
            "required": True, "expected_sha256": None, "bytes": None, "policy": None})
        raw, manifest = self.capture()
        self.assertEqual(self.inspect_partial(raw), manifest)
        self.assertEqual(manifest["role_contract"], n._contract_rows(self.roles))
        missing = [r for r in manifest["members"] if r["role"] in ("media", "decoder")]
        self.assertEqual(len(missing), 2)
        self.assertTrue(all(r["required"] is True and r["state"] == "REQUIRED_MISSING"
                            and "member" not in r for r in missing))
        self.assertFalse(manifest["required_layers_complete"])
        self.assertTrue(manifest["diagnostic_roster_complete"])
        self.assertTrue(manifest["raw_diagnostics_included"])
        self.assertEqual(manifest["absence_guards"], [])
        for key in ("physical_acceptance", "human_acceptance", "release_authorized",
                    "automatic_retry", "diagnostic_eof_claimed"):
            self.assertIs(manifest[key], False)
        with self.assertRaises(n.SelectionError):
            n.inspect_selected_tar(raw, expected_context=CTX, role_contract=self.roles,
                policies=POLICIES, expected_diagnostic_contract=self.contract)

    def test_partial_missing_selected_diagnostic_stays_required_and_incomplete(self):
        row = self.spec["members"][2]
        self.handles.files.pop((row["root"], row["relative_path"]))
        row.update(expected_sha256=None, bytes=None)
        raw, manifest = self.capture()
        self.assertEqual(self.inspect_partial(raw), manifest)
        missing = manifest["members"][2]
        self.assertIs(missing["required"], True)
        self.assertEqual(missing["state"], "REQUIRED_MISSING")
        self.assertFalse(manifest["required_layers_complete"])
        self.assertFalse(manifest["diagnostic_roster_complete"])
        self.assertTrue(manifest["raw_diagnostics_included"])
        self.assertEqual(manifest["absence_guards"], [])

    def test_partial_mode_is_explicit_typed_and_unavailable_to_legacy(self):
        raw, manifest = self.capture()
        self.assertEqual(self.inspect_partial(raw), manifest)
        for mode in (None, 0, 1, "false", [], {}):
            with self.subTest(mode=mode), self.assertRaisesRegex(n.SelectionError,
                    "selected-completeness-mode"):
                n.inspect_selected_tar(raw, expected_context=CTX, role_contract=self.roles,
                    policies=POLICIES, expected_diagnostic_contract=self.contract,
                    require_complete=mode)
        files = {("owner", "media.mp4"): RAW, ("owner", "terminal.json"): TERMINAL}
        with mock.patch.object(n, "RootHandles", return_value=MemoryHandles(files)), \
             mock.patch.object(n, "_verify_sources"):
            legacy, _ = n.capture_selected_originals(spec_for(files), policies=POLICIES,
                                                     role_contract=ROLES)
        with self.assertRaisesRegex(n.SelectionError, "partial-custody-on-legacy"):
            n.inspect_selected_tar(legacy, expected_context=CTX, role_contract=ROLES,
                                    policies=POLICIES, require_complete=False)

    def test_partial_cannot_omit_downgrade_or_replace_required_role_with_guard(self):
        self.missing_media()
        raw, _ = self.capture()
        def omit(m): m["members"] = [r for r in m["members"] if r["role"] != "media"]
        def downgrade(m): m["members"][0]["required"] = False
        def replace(m):
            row = m["members"].pop(0)
            m["absence_guards"].append({**{k: row[k] for k in
                ("root", "relative_path", "phase", "layer", "role")},
                "absent_before": True, "absent_after": True, "opened": False, "hashed": False})
        for mutation in (omit, downgrade, replace):
            with self.subTest(mutation=mutation.__name__), self.assertRaises(n.SelectionError):
                self.inspect_partial(rewrite_tar(raw, mutation))

    def test_optional_capture_failure_cannot_forge_global_complete_on_either_schema(self):
        for version in (2, 3):
            self.setUp()
            spec = copy.deepcopy(self.spec)
            roles, contract = dict(self.roles), self.contract
            if version == 2:
                spec["schema"] = n.SPEC_SCHEMA
                spec["members"] = spec["members"][:2]
                roles, contract = dict(ROLES), None
            roles[("original", "persistence", "optional-output")] = (0, 1)
            spec["members"].append({"root": "owner", "relative_path": "optional.bin",
                "phase": "original", "layer": "persistence", "role": "optional-output",
                "kind": "original", "required": False, "expected_sha256": None,
                "bytes": None, "policy": None})
            with mock.patch.object(n, "RootHandles", return_value=self.handles), \
                 mock.patch.object(n, "_verify_sources"):
                _, manifest = n.capture_selected_originals(spec, policies=POLICIES,
                    role_contract=roles, diagnostic_contract=contract)
            self.assertEqual(manifest["members"][-1]["state"], "CAPTURE_FAILED")
            self.assertFalse(manifest["required_layers_complete"])
            self.assertFalse(next(r for r in manifest["layers"]
                                  if r["layer"] == "persistence")["complete"])
            forged = copy.deepcopy(manifest)
            forged["required_layers_complete"] = True  # The only changed field.
            self.assertEqual({k: v for k, v in forged.items() if k != "required_layers_complete"},
                             {k: v for k, v in manifest.items() if k != "required_layers_complete"})
            with self.subTest(version=version), self.assertRaisesRegex(n.SelectionError,
                    "selected-global-completeness-mismatch"):
                n.validate_selected_manifest(forged, expected_context=CTX, role_contract=roles,
                                             expected_diagnostic_contract=contract)
            with self.assertRaises(n.SelectionError):
                n.validate_selected_manifest(manifest, expected_context=CTX, role_contract=roles,
                                             expected_diagnostic_contract=contract)
            if version == 3:
                self.assertEqual(n.validate_selected_manifest(manifest, expected_context=CTX,
                    role_contract=roles, expected_diagnostic_contract=contract,
                    require_complete=False), manifest)
                with self.assertRaisesRegex(n.SelectionError, "selected-global-completeness-mismatch"):
                    n.validate_selected_manifest(forged, expected_context=CTX, role_contract=roles,
                        expected_diagnostic_contract=contract, require_complete=False)

    def test_global_complete_false_or_integer_is_not_equivalent_to_recomputed_true(self):
        _, manifest = self.capture()
        for value in (False, 1, 0, None):
            changed = copy.deepcopy(manifest); changed["required_layers_complete"] = value
            with self.subTest(value=value), self.assertRaisesRegex(n.SelectionError,
                    "selected-global-completeness-mismatch"):
                n.validate_selected_manifest(changed, expected_context=CTX, role_contract=self.roles,
                    expected_diagnostic_contract=self.contract, require_complete=False)

    def test_partial_keeps_external_roster_and_retained_payload_identity_checks(self):
        self.missing_media()
        raw, _ = self.capture()
        with self.assertRaisesRegex(n.SelectionError, "diagnostic-contract-required"):
            n.inspect_selected_tar(raw, expected_context=CTX, role_contract=self.roles,
                                    policies=POLICIES, require_complete=False)
        foreign = copy.deepcopy(self.contract)
        foreign["entries"][0]["type"] = "stderr"
        with self.assertRaisesRegex(n.SelectionError, "selected-diagnostic-contract-mismatch"):
            self.inspect_partial(raw, contract=foreign)
        def change(name, data):
            return data + b"X" if name.endswith("stdout.original.bin") else data
        with self.assertRaises(n.SelectionError):
            self.inspect_partial(rewrite_tar(raw, mutate_payload=change))
        def forged_payload(m):
            row = m["members"][2]
            row.update(state="CAPTURE_FAILED", code="selected-capture-failed")
            m["layers"] = n._layers(m["members"], self.roles, True)
            m["diagnostic_roster_complete"] = False
        with self.assertRaisesRegex(n.SelectionError, "selected-partial-unadmitted-payload"):
            self.inspect_partial(rewrite_tar(raw, forged_payload))

    def test_partial_does_not_admit_changed_namespace_source_or_absence_guards(self):
        for failure in ("postread", "source", "guard"):
            self.setUp()
            if failure == "postread":
                row = self.spec["members"][2]
                self.handles.changed_after_read = (row["root"], row["relative_path"])
                raw, manifest = self.capture()
                self.assertTrue(manifest["raw_diagnostics_included"])
                self.assertIn("member", manifest["members"][2])
            elif failure == "source":
                with mock.patch.object(n, "RootHandles", return_value=self.handles), \
                     mock.patch.object(n, "_verify_sources", side_effect=[None,
                         n.SelectionError("source-changed")]):
                    raw, manifest = n.capture_selected_originals(self.spec, policies=POLICIES,
                        role_contract=self.roles, diagnostic_contract=self.contract)
                self.assertFalse(manifest["source_bytes_bound"])
            else:
                path = self.guard("stdin")
                self.handles.hook = lambda: self.handles.files.update({path: self.files[path]})
                raw, manifest = self.capture()
                self.assertFalse(manifest["absence_guards"][0]["absent_after"])
            with self.subTest(failure=failure), self.assertRaises(n.SelectionError):
                self.inspect_partial(raw)

    def test_nonstring_phase_and_kind_reject_as_selection_error_before_capture_io(self):
        for field, value in (("phase", []), ("phase", {}), ("kind", [])):
            spec = copy.deepcopy(self.spec); spec["members"][0][field] = value
            with self.subTest(field=field, value=value), \
                 mock.patch.object(n, "RootHandles") as opened:
                with self.assertRaises(n.SelectionError):
                    n.capture_selected_originals(spec, policies=POLICIES, role_contract=self.roles,
                                                 diagnostic_contract=self.contract)
                opened.assert_not_called()
        for value in ([], {}):
            contract = copy.deepcopy(self.contract); contract["entries"][0]["phase"] = value
            with self.subTest(contract_phase=value), mock.patch.object(n, "RootHandles") as opened:
                with self.assertRaises(n.SelectionError):
                    n.capture_selected_originals(self.spec, policies=POLICIES,
                        role_contract=self.roles, diagnostic_contract=contract)
                opened.assert_not_called()

    def test_nonstring_manifest_phase_and_kind_reject_with_typed_errors(self):
        raw, _ = self.capture()
        for field, value in (("phase", []), ("phase", {}), ("kind", [])):
            def mutate(m): m["members"][0][field] = value
            for partial in (False, True):
                with self.subTest(field=field, value=value, partial=partial), \
                     self.assertRaises(n.SelectionError):
                    n.inspect_selected_tar(rewrite_tar(raw, mutate), expected_context=CTX,
                        role_contract=self.roles, policies=POLICIES,
                        expected_diagnostic_contract=self.contract, require_complete=not partial)


@unittest.skipUnless(os.name == "posix" and hasattr(os,"O_NOFOLLOW"),
                     "actual Linux openat/Git cases run in GH Actions, not Windows authoring host")
class LinuxFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-evidence-selection-v2-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/"source"; self.source.mkdir(mode=0o700)
        self.owner = self.root/"owner"; self.owner.mkdir(mode=0o700)
        (self.source/"tooling").mkdir()
        (self.source/"tooling/native_evidence_projection.py").write_bytes(Path(n.__file__).read_bytes())
        (self.source/"producer.py").write_bytes(b"# Synthetic fixed-fixture source.\n")
        self.git("init","--quiet")
        self.git("add","tooling/native_evidence_projection.py","producer.py")
        self.git("-c","user.name=Motionwright Test","-c","user.email=fixture@example.invalid",
                 "commit","--quiet","-m","Synthetic source fixture")
        self.current = self.git("rev-parse","HEAD").decode().strip()
        (self.owner/"media.mp4").write_bytes(RAW)
        (self.owner/"terminal.json").write_bytes(TERMINAL)
        (self.owner/"runtime").mkdir(mode=0o700)
        (self.owner/"runtime/motionwright.session").write_text(SECRET)
        self.spec = spec_for()
        self.spec["roots"] = {"source":str(self.source),"owner":str(self.owner)}
        self.spec["context"]["source_sha"] = self.current
        self.spec["source_bindings"] = [{"path":p,"sha256":n.sha((self.source/p).read_bytes())}
             for p in ("tooling/native_evidence_projection.py","producer.py")]
    def git(self,*args):
        return subprocess.check_output(["git",*args],cwd=self.source,stderr=subprocess.PIPE)
    def capture(self):
        return n.capture_selected_originals(self.spec,policies=POLICIES,role_contract=ROLES)
    def test_actual_handles_capture_selected_only_and_validate_tar(self):
        real_open = os.open
        def guarded(path,*args,**kwargs):
            self.assertNotIn("session",str(path))
            self.assertNotIn("observer-diagnostic",str(path))
            return real_open(path,*args,**kwargs)
        # RootHandles feature detection checks the original supports_dir_fd set.
        with mock.patch.object(os,"open",side_effect=guarded) as opened, \
             mock.patch.object(os,"supports_dir_fd",os.supports_dir_fd | {opened}):
            raw,m = self.capture()
        self.assertTrue(m["required_layers_complete"])
        self.assertNotIn(SECRET.encode(),raw)
        n.inspect_selected_tar(raw,expected_context=self.spec["context"],
                               role_contract=ROLES,policies=POLICIES)
    def test_symlinked_selected_media_never_reads_target(self):
        (self.owner/"media.mp4").unlink()
        (self.owner/"media.mp4").symlink_to(self.owner/"runtime/motionwright.session")
        raw,m = self.capture()
        self.assertFalse(m["required_layers_complete"])
        self.assertEqual(m["members"][0]["state"],"CAPTURE_FAILED")
        self.assertNotIn(SECRET.encode(),raw)
    def test_hardlink_alias_to_excluded_secret_never_reads_target(self):
        (self.owner/"media.mp4").unlink()
        os.link(self.owner/"runtime/motionwright.session",self.owner/"media.mp4")
        self.spec["members"][0].update(expected_sha256=n.sha(SECRET.encode()),bytes=len(SECRET))
        raw,m = self.capture()
        self.assertFalse(m["required_layers_complete"])
        self.assertEqual(m["members"][0]["state"],"CAPTURE_FAILED")
        self.assertNotIn(SECRET.encode(),raw)
    def test_skip_worktree_clean_status_cannot_mask_changed_physical_producer(self):
        self.git("update-index","--skip-worktree","producer.py")
        (self.source/"producer.py").write_bytes(b"# Changed physical source.\n")
        self.assertEqual(self.git("status","--porcelain").strip(),b"")
        with self.assertRaisesRegex(n.SelectionError,"physical-source-differs-from-git"):
            self.capture()
    def test_nonprivate_data_root_is_rejected(self):
        self.owner.chmod(0o755)
        with self.assertRaisesRegex(n.SelectionError,"root-private-mode"): self.capture()
    def test_source_and_data_root_overlap_is_rejected(self):
        nested = self.source/"owner"; nested.mkdir(mode=0o700)
        self.spec["roots"]["owner"] = str(nested)
        with self.assertRaisesRegex(n.SelectionError,"overlapping-roots"): self.capture()

    def diagnostic_fixture(self):
        data = b"explicit-stream\0" + SECRET.encode()
        path = "capture.stdout.bin"
        (self.owner/path).write_bytes(data)
        self.spec["schema"] = n.SPEC_SCHEMA_V3
        self.spec["members"].append({"root": "owner", "relative_path": path,
            "phase": "original", "layer": "attempt-custody", "role": "diagnostic-stdout",
            "kind": "diagnostic-original", "diagnostic_type": "stdout", "required": True,
            "expected_sha256": n.sha(data), "bytes": len(data), "policy": None})
        roles = {**ROLES, ("original", "attempt-custody", "diagnostic-stdout"): (0, 1)}
        contract = {"schema": n.DIAGNOSTIC_CONTRACT_SCHEMA, "context": dict(self.spec["context"]),
            "entries": [{"root": "owner", "relative_path": path, "phase": "original",
                "layer": "attempt-custody", "role": "diagnostic-stdout", "type": "stdout",
                "maximum": 262144, "policy": None}]}
        return data, path, roles, contract

    def test_v3_actual_handles_retain_only_registered_diagnostic_original(self):
        data, path, roles, contract = self.diagnostic_fixture()
        raw, manifest = n.capture_selected_originals(self.spec, policies=POLICIES,
            role_contract=roles, diagnostic_contract=contract)
        self.assertTrue(manifest["diagnostic_roster_complete"])
        self.assertTrue(manifest["raw_diagnostics_included"])
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            row = manifest["members"][-1]
            self.assertEqual(row["relative_path"], path)
            self.assertEqual(archive.extractfile(row["member"]).read(), data)
        self.assertEqual(n.inspect_selected_tar(raw, expected_context=self.spec["context"],
            role_contract=roles, policies=POLICIES, expected_diagnostic_contract=contract), manifest)

    def test_v3_diagnostic_kind_does_not_bypass_real_symlink_or_hardlink_guards(self):
        _, path, roles, contract = self.diagnostic_fixture()
        target = self.owner/path
        for link in ("symlink", "hardlink"):
            target.unlink()
            if link == "symlink": target.symlink_to(self.owner/"runtime/motionwright.session")
            else: os.link(self.owner/"runtime/motionwright.session", target)
            with self.subTest(link=link):
                raw, manifest = n.capture_selected_originals(self.spec, policies=POLICIES,
                    role_contract=roles, diagnostic_contract=contract)
                self.assertEqual(manifest["members"][-1]["state"], "CAPTURE_FAILED")
                self.assertFalse(manifest["diagnostic_roster_complete"])
                self.assertFalse(manifest["raw_diagnostics_included"])
                self.assertNotIn(SECRET.encode(), raw)
                with self.assertRaises(n.SelectionError):
                    n.inspect_selected_tar(raw, expected_context=self.spec["context"],
                        role_contract=roles, policies=POLICIES, expected_diagnostic_contract=contract)

    @unittest.skipUnless(sys.platform.startswith("linux") and hasattr(os, "pidfd_open"),
                         "real Linux FIFO replacement with original-child pidfd custody")
    def test_regular_to_fifo_race_is_nonblocking_with_closed_original_child(self):
        """Real FIFO plus a declared fault injection; no historical SHA credit."""
        import select
        import signal
        import time
        self.assertTrue(hasattr(signal, "pidfd_send_signal"))
        self.assertEqual(signal.getsignal(signal.SIGCHLD), signal.SIG_DFL)
        for omit_nonblock in (False, True):
            with self.subTest(omit_nonblock=omit_nonblock):
                started = time.monotonic()
                work_deadline, hard_deadline = started + 2.0, started + 5.0
                owned, close_errors, observation_errors = {}, [], []
                pid, waited, primary = None, None, None
                original, eof = bytearray(), False
                read_failed = wait_failed = False
                kill_attempts, release_attempts = 0, 0
                leaf = "fifo-race-" + ("negative" if omit_nonblock else "fixed") + ".bin"

                def close_once(name):
                    fd = owned.pop(name, None)  # Retire even if close is uncertain.
                    if fd is None:
                        return True
                    try:
                        os.close(fd)
                        return True
                    except BaseException as error:
                        close_errors.append({"name": name, "fd": fd,
                                             "error": type(error).__name__, "message": str(error)})
                        return False

                def drain_once():
                    nonlocal eof, read_failed
                    if eof or read_failed or "reader" not in owned:
                        return
                    try:
                        block = os.read(owned["reader"], 4097 - len(original))
                        original.extend(block)
                        eof = block == b""
                        if len(original) > 4096:
                            read_failed = True
                            observation_errors.append({"operation": "read", "error": "prefix-budget"})
                    except (BlockingIOError, InterruptedError):
                        pass
                    except BaseException as error:
                        read_failed = True
                        observation_errors.append({"operation": "read", "error": type(error).__name__,
                                                   "message": str(error)})

                def wait_once():
                    nonlocal waited, wait_failed
                    if pid is None or waited is not None or wait_failed:
                        return
                    try:
                        returned, status = os.waitpid(pid, os.WNOHANG)
                        if returned:
                            if returned != pid:
                                raise AssertionError("wait did not return the original child")
                            waited = status
                    except InterruptedError:
                        pass
                    except BaseException as error:
                        wait_failed = True
                        observation_errors.append({"operation": "wait", "error": type(error).__name__,
                                                   "message": str(error)})

                def kill_once():
                    nonlocal kill_attempts
                    if waited is not None or wait_failed or kill_attempts or "pidfd" not in owned:
                        return
                    kill_attempts = 1
                    try:
                        signal.pidfd_send_signal(owned["pidfd"], signal.SIGKILL)
                    except BaseException as error:
                        observation_errors.append({"operation": "pidfd-signal", "error": type(error).__name__,
                                                   "message": str(error)})

                try:
                    (self.owner / leaf).write_bytes(b"original-regular-bytes")
                    owned["reader"], owned["writer"] = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
                    owned["gate_reader"], owned["gate_writer"] = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
                    pid = os.fork()
                    if pid == 0:
                        handles, status, real_open = None, 2, os.open
                        try:
                            closed = [close_once("reader"), close_once("gate_writer")]
                            if not all(closed):
                                raise AssertionError("child preparation close was uncertain")
                            token, gate_eof = bytearray(), False
                            while time.monotonic() < work_deadline:
                                remaining = max(0, work_deadline - time.monotonic())
                                ready, _, _ = select.select([owned["gate_reader"]], [], [], min(0.02, remaining))
                                if not ready:
                                    continue
                                block = os.read(owned["gate_reader"], 2 - len(token))
                                token.extend(block)
                                if len(token) > 1:
                                    raise AssertionError("foreign fixture release token")
                                if not block:
                                    gate_eof = True
                                    break
                            if bytes(token) != b"G" or not gate_eof:
                                raise AssertionError("fixture was not released by a pidfd-owning parent")
                            if not close_once("gate_reader"):
                                raise AssertionError("child release descriptor close was uncertain")
                            handles = n.RootHandles({"owner": str(self.owner)})
                            value, _ = handles.read("owner", (leaf,), 64)
                            if value != b"original-regular-bytes":
                                raise AssertionError("regular control changed")
                            os.write(owned["writer"], b"REGULAR_OK\n")
                            swapped = False

                            def replace_leaf_then_open(path, flags, *args, **kwargs):
                                nonlocal swapped
                                if path == leaf and not swapped and "dir_fd" in kwargs:
                                    swapped = True
                                    parent = kwargs["dir_fd"]
                                    os.unlink(leaf, dir_fd=parent)
                                    os.mkfifo(leaf, mode=0o600, dir_fd=parent)
                                    os.write(owned["writer"], b"FIFO_CREATED\n")
                                    if omit_nonblock:
                                        flags &= ~os.O_NONBLOCK
                                return real_open(path, flags, *args, **kwargs)

                            os.open = replace_leaf_then_open
                            try:
                                handles.read("owner", (leaf,), 64)
                            except n.SelectionError as error:
                                if str(error) != "member-changed-before-open" or not swapped:
                                    raise
                                os.write(owned["writer"], b"REJECTED_CHANGED_BEFORE_OPEN\n")
                                status = 0
                            else:
                                os.write(owned["writer"], b"UNEXPECTED_READ_SUCCESS\n")
                        except BaseException:
                            try:
                                if "writer" in owned:
                                    os.write(owned["writer"], b"CHILD_SOURCE_CONTROL_ERROR\n")
                            except BaseException:
                                pass
                        finally:
                            os.open = real_open
                            try:
                                if handles is not None:
                                    handles.close()
                            except BaseException:
                                status = 2
                            for name in tuple(owned):
                                if not close_once(name):
                                    status = 2
                            # This covers every post-fork path, including reader
                            # close and gate setup: never return into unittest.
                            os._exit(status)

                    closed = [close_once("writer"), close_once("gate_reader")]
                    if not all(closed):
                        raise AssertionError("parent preparation close was uncertain")
                    # SIGCHLD is default and the original child is unreaped;
                    # it cannot have been replaced by a reused PID at this bind.
                    owned["pidfd"] = os.pidfd_open(pid, 0)
                    release_attempts = 1
                    if os.write(owned["gate_writer"], b"G") != 1:
                        raise AssertionError("incomplete fixture release")
                    if not close_once("gate_writer"):
                        raise AssertionError("release close was uncertain")
                    while time.monotonic() < hard_deadline:
                        drain_once()
                        wait_once()
                        if read_failed or wait_failed:
                            raise AssertionError("original FIFO observation became uncertain")
                        if waited is not None and eof:
                            break
                        if time.monotonic() >= work_deadline:
                            kill_once()
                        delay = min(0.02, max(0, hard_deadline - time.monotonic()))
                        select.select([owned["reader"]] if not eof else [], [], [], delay)
                    if waited is None or not eof:
                        raise AssertionError("original FIFO child terminal or EOF UNKNOWN; no replay")
                except BaseException as error:
                    primary = error
                finally:
                    # If pidfd acquisition failed, this EOF releases the gated
                    # child to exit without touching a regular/FIFO source. There
                    # is no fallback signal or handle reacquisition by PID.
                    close_once("gate_writer")
                    if pid is not None and pid > 0:
                        wait_once()
                        if waited is None and time.monotonic() < hard_deadline:
                            kill_once()
                        while time.monotonic() < hard_deadline and not wait_failed:
                            drain_once()
                            wait_once()
                            if waited is not None and (eof or read_failed):
                                break
                            try:
                                time.sleep(min(0.01, max(0, hard_deadline - time.monotonic())))
                            except BaseException as error:
                                observation_errors.append({"operation": "cleanup-delay",
                                    "error": type(error).__name__, "message": str(error)})
                                if primary is None:
                                    primary = error
                                break
                    bound = "pidfd" in owned
                    for name in tuple(owned):
                        close_once(name)
                    finished = time.monotonic()
                    closure_before_deadline = finished < hard_deadline
                    closed = (waited is not None and eof and not read_failed and not wait_failed
                              and not close_errors and not observation_errors and closure_before_deadline)
                    record = {"schema": "motionwright.core-fifo-source-control/1",
                        "fault_injection_remove_nonblock": omit_nonblock,
                        "original_child_pid": pid, "original_pidfd_bound": bound,
                        "release_attempts": release_attempts,
                        "original_pipe_prefix_hex": bytes(original).hex(),
                        "original_pipe_prefix_sha256": n.sha(bytes(original)),
                        "original_pipe_eof": eof, "prefix_limit_exceeded": len(original) > 4096,
                        "wait_status": waited,
                        "exit_code": os.waitstatus_to_exitcode(waited) if waited is not None else None,
                        "kill_attempts": kill_attempts, "close_errors": close_errors,
                        "observation_errors": observation_errors,
                        "primary_error": None if primary is None else {
                            "type": type(primary).__name__, "message": str(primary)},
                        "status": "OBSERVED_ORIGINAL_TERMINAL" if closed else "UNKNOWN",
                        "elapsed_seconds": finished - started,
                        "closure_before_deadline": closure_before_deadline,
                        "work_seconds": 2.0, "original_closure_deadline_seconds": 5.0,
                        "global_descendants_closed": False, "native_operation_executed": False}
                    print("CORE_FIFO_SOURCE_ORIGINAL=" + json.dumps(record, sort_keys=True), flush=True)
                if primary is not None:
                    raise primary
                self.assertTrue(closed, "original FIFO custody incomplete; no replay")
                self.assertEqual(release_attempts, 1)
                if omit_nonblock:
                    self.assertEqual(bytes(original), b"REGULAR_OK\nFIFO_CREATED\n")
                    self.assertEqual(kill_attempts, 1)
                    self.assertTrue(os.WIFSIGNALED(waited))
                    self.assertEqual(os.WTERMSIG(waited), signal.SIGKILL)
                else:
                    self.assertEqual(bytes(original), b"REGULAR_OK\nFIFO_CREATED\nREJECTED_CHANGED_BEFORE_OPEN\n")
                    self.assertEqual(kill_attempts, 0)
                    self.assertTrue(os.WIFEXITED(waited))
                    self.assertEqual(os.WEXITSTATUS(waited), 0)


class ScopedJsonSnapshotLeafTests(unittest.TestCase):
    """Synthetic envelopes exercise leaves; they are not an SDK schema claim."""
    def setUp(self):
        self.files = {("owner", "media.mp4"): RAW, ("owner", "terminal.json"): TERMINAL}
        self.spec = spec_for(self.files)
        self.spec["roots"]["records"] = "/virtual/records"
        self.roles = dict(ROLES)
        receipt = n.Object({"payload": n.Field(n.JsonValue()),
                            "state": n.Field(n.String(values=("UNKNOWN",)))})
        self.envelope = n.Object({
            "schema": n.Field(n.Literal("unit-scoped-json-leaf/1")),
            "error": n.Field(n.Object({
                "code": n.Field(n.String()), "message": n.Field(n.JsonText()),
                "candidates": n.Field(n.Array(n.JsonText(), 8)),
                "progress": n.Field(n.Object({"note": n.Field(n.JsonText())})),
                "native_diagnostic": n.Field(n.Object({
                    "name": n.Field(n.String()), "text": n.Field(n.JsonText())})),
            })),
            "receipts": n.Field(n.Array(receipt, 4)),
            "attempted_receipt": n.Field(n.Nullable(receipt)),
        })
        self.value = {"schema": "unit-scoped-json-leaf/1", "error": {
            "code": "native-unknown", "message": "first\nsecond\t\u0000",
            "candidates": ["candidate\rline", "\u0001escaped"],
            "progress": {"note": "progress\ntext"},
            "native_diagnostic": {"name": "fixture", "text": "native\ntext"}},
            "receipts": [{"state": "UNKNOWN", "payload": {
                "dynamic\nkey": [None, True, 17, 2.5, {"text": "\u001fdata"}]}}],
            "attempted_receipt": {"state": "UNKNOWN", "payload": ["opaque", False]},
        }
        self.policies = dict(POLICIES)
        self.policies.update({key: self.envelope for key in n.JSON_SNAPSHOT_POLICIES})
        self.policy = "hyperframes-original-render-failure"

    def add_snapshot(self, policy=None, raw=None):
        policy = policy or self.policy
        phase, path = n.JSON_SNAPSHOT_POLICIES[policy]
        raw = raw if raw is not None else (json.dumps(self.value, indent=1, ensure_ascii=True) + "\n").encode()
        self.files[("records", path)] = raw
        role = "render-failure-custody" if path.endswith(".failure-custody.json") else "render-failure"
        row = {"root": "records", "relative_path": path, "role": role,
               "layer": "attempt-custody", "phase": phase, "kind": "json-original",
               "required": False, "expected_sha256": n.sha(raw), "bytes": len(raw), "policy": policy}
        self.roles[(phase, "attempt-custody", role)] = (0, 1)
        self.spec["members"].append(row)
        return row

    def capture(self):
        self.handles = MemoryHandles(self.files)
        with mock.patch.object(n, "RootHandles", return_value=self.handles), \
             mock.patch.object(n, "_verify_sources") as sources:
            raw, manifest = n.capture_selected_originals(self.spec, policies=self.policies,
                                                        role_contract=self.roles)
            self.assertEqual(sources.call_count, 2)
        return raw, manifest

    def inspect(self, raw, **kwargs):
        return n.inspect_selected_tar(raw, expected_context=CTX, role_contract=self.roles,
                                      policies=self.policies, **kwargs)

    def test_default_string_and_projection_do_not_acquire_json_leaf_permissions(self):
        self.assertEqual(n.project("ordinary", n.String()), "ordinary")
        for value, rule in [("escaped\ntext", n.String()), ("escaped\ntext", n.JsonText()),
                            ({"dynamic": True}, n.JsonValue()),
                            ({"payload": {}}, n.Object({"payload": n.Field(n.JsonValue())}))]:
            with self.subTest(rule=type(rule).__name__), self.assertRaises(n.SelectionError):
                n.project(value, rule, exact=True)

    def test_four_opted_in_files_retain_escaped_text_and_dynamic_payload_bytes(self):
        self.assertEqual(len(n.JSON_SNAPSHOT_POLICIES), 4)
        self.assertEqual((n.MAX_JSON, n.MAX_TOTAL), (8 * 1024 * 1024, 512 * 1024 * 1024))
        for policy in n.JSON_SNAPSHOT_POLICIES:
            self.add_snapshot(policy)
        raw, manifest = self.capture()
        self.assertEqual(self.inspect(raw), manifest)
        self.assertTrue(manifest["required_layers_complete"])
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for row in manifest["members"][-4:]:
                original = self.files[(row["root"], row["relative_path"])]
                self.assertEqual(archive.extractfile(row["member"]).read(), original)
                self.assertNotEqual(original, n.canonical(self.value))
                self.assertEqual(row["retained_sha256"], n.sha(original))
                self.assertTrue(row["original_bytes"])
        self.assertEqual(manifest["schema"], n.MANIFEST_SCHEMA)
        for flag in ("raw_diagnostics_included", "raw_environment_included", "physical_acceptance",
                     "human_acceptance", "full_original_json_claimed", "release_authorized"):
            self.assertIs(manifest[flag], False)

    def test_opt_in_is_bound_to_exact_policy_file_root_phase_kind_and_schema(self):
        row = self.add_snapshot()
        changes = [{"root": "owner"}, {"relative_path": "records/original-render.failure.json"},
                   {"phase": "edited"}, {"layer": "persistence"}, {"kind": "json-projection"},
                   {"policy": "foreign-policy"}, {"policy": [self.policy]}]
        self.policies["foreign-policy"] = self.envelope
        for change in changes:
            spec = copy.deepcopy(self.spec); spec["members"][-1].update(change)
            with self.subTest(change=change), mock.patch.object(n, "RootHandles") as handles:
                with self.assertRaises(n.SelectionError):
                    n.capture_selected_originals(spec, policies=self.policies, role_contract=self.roles)
                handles.assert_not_called()
        spec = copy.deepcopy(self.spec); spec["schema"] = n.SPEC_SCHEMA_V3
        with mock.patch.object(n, "RootHandles") as handles, self.assertRaisesRegex(
                n.SelectionError, "json-snapshot-policy-scope"):
            n.capture_selected_originals(spec, policies=self.policies, role_contract=self.roles)
        handles.assert_not_called()
        self.assertEqual(row["relative_path"], "original-render.failure.json")

    def test_unregistered_policy_cannot_use_new_leaves_on_another_selected_file(self):
        row = self.add_snapshot()
        row.update(relative_path="unrelated.json", policy="foreign-policy")
        self.policies["foreign-policy"] = self.envelope
        with mock.patch.object(n, "RootHandles") as handles:
            with self.assertRaisesRegex(n.SelectionError, "json-snapshot-leaf-not-opted-in"):
                n.capture_selected_originals(self.spec, policies=self.policies, role_contract=self.roles)
            handles.assert_not_called()

    def test_payload_leaf_cannot_replace_progress_diagnostic_or_the_envelope(self):
        self.add_snapshot()
        for policy in [n.JsonValue(), n.Object({"progress": n.Field(n.JsonValue())}),
                       n.Object({"native_diagnostic": n.Field(n.JsonValue())}),
                       n.Object({"receipts": n.Field(n.Array(n.JsonValue(), 4))}),
                       n.Object({"message": n.Field(n.JsonText(n.MAX_JSON + 1))})]:
            self.policies[self.policy] = policy
            with self.subTest(policy=repr(policy)), mock.patch.object(n, "RootHandles") as handles:
                with self.assertRaises(n.SelectionError):
                    n.capture_selected_originals(self.spec, policies=self.policies, role_contract=self.roles)
                handles.assert_not_called()

    def test_whole_file_node_depth_and_string_budgets_are_shared_not_per_leaf(self):
        self.assertEqual((n.JSON_SNAPSHOT_DEPTH, n.JSON_SNAPSHOT_NODES,
                          n.JSON_SNAPSHOT_STRING_BYTES), (32, 65536, n.MAX_JSON))
        self.policies[self.policy] = n.Object({"payload": n.Field(n.JsonValue())})
        for count, expected in [(65533, "INCLUDED"), (65534, "CAPTURE_FAILED")]:
            self.spec["members"] = self.spec["members"][:2]
            original = n.canonical({"payload": [None] * count})
            self.add_snapshot(raw=original)
            decode, calls = n.json.loads, []
            def watched_decode(data, *args, **kwargs):
                if data == original:
                    calls.append("snapshot")
                    self.assertEqual(expected, "INCLUDED", "node overflow reached JSON decoder")
                return decode(data, *args, **kwargs)
            with mock.patch.object(n.json, "loads", side_effect=watched_decode):
                _, manifest = self.capture()
            self.assertEqual(calls, ["snapshot"] if expected == "INCLUDED" else [])
            self.assertEqual(manifest["members"][-1]["state"], expected)
        for depth, expected in [(31, "INCLUDED"), (32, "CAPTURE_FAILED")]:
            payload = 0
            for _ in range(depth): payload = [payload]
            self.spec["members"] = self.spec["members"][:2]
            original = n.canonical({"payload": payload})
            self.add_snapshot(raw=original)
            decode, calls = n.json.loads, []
            def watched_decode(data, *args, **kwargs):
                if data == original:
                    calls.append("snapshot")
                    self.assertEqual(expected, "INCLUDED", "depth overflow reached JSON decoder")
                return decode(data, *args, **kwargs)
            with mock.patch.object(n.json, "loads", side_effect=watched_decode):
                _, manifest = self.capture()
            self.assertEqual(calls, ["snapshot"] if expected == "INCLUDED" else [])
            self.assertEqual(manifest["members"][-1]["state"], expected)
        self.policies[self.policy] = n.Object({"a": n.Field(n.JsonText()), "b": n.Field(n.JsonText())})
        self.spec["members"] = self.spec["members"][:2]
        self.add_snapshot(raw=n.canonical({"a": "x" * 40, "b": "y" * 40}))
        for budget, expected in [(82, "INCLUDED"), (81, "CAPTURE_FAILED")]:
            with mock.patch.object(n, "JSON_SNAPSHOT_STRING_BYTES", budget):
                _, manifest = self.capture()
            self.assertEqual(manifest["members"][-1]["state"], expected)

    def test_invalid_nonfinite_duplicate_and_surrogate_data_remain_incomplete(self):
        self.policies[self.policy] = n.Object({"payload": n.Field(n.JsonValue())})
        variants = [b'{"payload":NaN}', b'{"payload":Infinity}', b'{"payload":1e999}',
                    b'{"payload":0,"payload":1}', b'{"payload":"\\ud800"}',
                    b'{"payload":"\xff"}', b'{"payload":', '{"payload":true}'.encode("utf-16-le")]
        for original in variants:
            self.spec["members"] = self.spec["members"][:2]
            row = self.add_snapshot(raw=original)
            with self.subTest(original=original):
                raw, manifest = self.capture()
                self.assertEqual(manifest["members"][-1]["state"], "CAPTURE_FAILED")
                self.assertFalse(manifest["required_layers_complete"])
                self.assertNotIn("member", manifest["members"][-1])
                self.assertEqual(self.files[("records", row["relative_path"])], original)
                with self.assertRaises(n.SelectionError): self.inspect(raw)

    def test_oversize_is_rejected_at_reader_bound_without_truncation_or_partial_v2(self):
        original = b" " * n.MAX_JSON + b"{}"
        row = self.add_snapshot(raw=original)
        handles = MemoryHandles(self.files)
        with mock.patch.object(handles, "read", wraps=handles.read) as read, \
             mock.patch.object(n, "RootHandles", return_value=handles), mock.patch.object(n, "_verify_sources"):
            raw, manifest = n.capture_selected_originals(self.spec, policies=self.policies,
                                                        role_contract=self.roles)
        self.assertIn(mock.call("records", (row["relative_path"],), n.MAX_JSON), read.call_args_list)
        failed = manifest["members"][-1]
        self.assertEqual((failed["state"], failed["code"]), ("CAPTURE_FAILED", "member-byte-budget"))
        self.assertNotIn("retained_bytes", failed)
        self.assertEqual(handles.files[("records", row["relative_path"])], original)
        self.assertFalse(manifest["required_layers_complete"])
        with self.assertRaises(n.SelectionError): self.inspect(raw)
        with self.assertRaisesRegex(n.SelectionError, "partial-custody-on-legacy"):
            self.inspect(raw, require_complete=False)

    def test_closed_envelopes_and_typed_structs_reject_unexpected_fields(self):
        variants = [dict(self.value, unexpected="outside payload")]
        for field in ("progress", "native_diagnostic"):
            value = copy.deepcopy(self.value)
            value["error"][field]["unexpected"] = "outside payload"
            variants.append(value)
        for value in variants:
            self.spec["members"] = self.spec["members"][:2]
            self.add_snapshot(raw=n.canonical(value))
            raw, manifest = self.capture()
            self.assertEqual(manifest["members"][-1]["code"], "original-json-extra-field")
            self.assertFalse(manifest["required_layers_complete"])
            with self.assertRaises(n.SelectionError): self.inspect(raw)

    def test_intake_rechecks_scope_and_closed_data_despite_consistent_self_hashes(self):
        self.add_snapshot()
        raw, manifest = self.capture()
        row = manifest["members"][-1]
        changed = n.canonical(dict(self.value, invented_envelope_field=True))
        def metadata(value):
            target = value["members"][-1]
            target.update(expected_sha256=n.sha(changed), retained_sha256=n.sha(changed),
                          bytes=len(changed), retained_bytes=len(changed))
        forged = rewrite_tar(raw, mutate_manifest=metadata,
                             mutate_payload=lambda name, data: changed if name == row["member"] else data)
        with self.assertRaisesRegex(n.SelectionError, "original-json-extra-field"):
            self.inspect(forged)
        forged = rewrite_tar(raw, mutate_manifest=lambda value: value["members"][-1].update(
            relative_path="another.failure.json"))
        with self.assertRaisesRegex(n.SelectionError, "json-snapshot-member-scope"):
            self.inspect(forged)
        duplicate = copy.deepcopy(manifest)
        duplicate["members"].append(copy.deepcopy(duplicate["members"][-1]))
        with self.assertRaisesRegex(n.SelectionError, "json-snapshot-duplicate-policy"):
            n.validate_selected_manifest(duplicate, expected_context=CTX, role_contract=self.roles)


    def test_four_leaf_roles_and_optional_flag_survive_capture_and_intake(self):
        expected = {
            "hyperframes-original-render-failure": ("original", "render-failure"),
            "hyperframes-original-render-failure-custody": ("original", "render-failure-custody"),
            "hyperframes-edited-render-failure": ("edited", "render-failure"),
            "hyperframes-edited-render-failure-custody": ("edited", "render-failure-custody"),
        }
        for policy in expected:
            self.add_snapshot(policy)
        raw, manifest = self.capture()
        self.assertEqual(self.inspect(raw), manifest)
        actual = {}
        for row in manifest["members"][-4:]:
            self.assertIs(row["required"], False)
            self.assertEqual(row["state"], "INCLUDED")
            actual[row["policy"]] = (row["phase"], row["role"])
        self.assertEqual(actual, expected)
        self.assertEqual(manifest["schema"], n.MANIFEST_SCHEMA)

    def test_wrong_leaf_roles_and_nonfalse_required_reject_before_root_handles(self):
        for policy in n.JSON_SNAPSHOT_POLICIES:
            self.spec["members"] = self.spec["members"][:2]
            self.roles = dict(ROLES)
            row = self.add_snapshot(policy)
            opposite = ("render-failure" if row["role"] == "render-failure-custody"
                        else "render-failure-custody")
            # Both role names are permitted by this external role contract;
            # the file-specific guard must still reject their substitution.
            for role in (opposite, policy):
                self.roles[(row["phase"], "attempt-custody", role)] = (0, 1)
            for change in ({"role": opposite}, {"role": policy}, {"required": True},
                           {"required": 0}, {"required": None}):
                spec = copy.deepcopy(self.spec)
                spec["members"][-1].update(change)
                with self.subTest(policy=policy, change=change), \
                     mock.patch.object(n, "RootHandles") as opened, \
                     mock.patch.object(n, "_verify_sources") as sources:
                    with self.assertRaises(n.SelectionError):
                        n.capture_selected_originals(spec, policies=self.policies,
                                                    role_contract=self.roles)
                    opened.assert_not_called()
                    sources.assert_not_called()
            spec = copy.deepcopy(self.spec)
            del spec["members"][-1]["required"]
            with self.subTest(policy=policy, missing_required=True), \
                 mock.patch.object(n, "RootHandles") as opened:
                with self.assertRaises(n.SelectionError):
                    n.capture_selected_originals(spec, policies=self.policies,
                                                role_contract=self.roles)
                opened.assert_not_called()

    def test_intake_rejects_leaf_role_and_required_forgery_with_consistent_contract(self):
        for policy in n.JSON_SNAPSHOT_POLICIES:
            self.spec["members"] = self.spec["members"][:2]
            self.roles = dict(ROLES)
            row = self.add_snapshot(policy)
            opposite = ("render-failure" if row["role"] == "render-failure-custody"
                        else "render-failure-custody")
            self.roles[(row["phase"], "attempt-custody", opposite)] = (0, 1)
            raw, manifest = self.capture()
            self.assertEqual(self.inspect(raw), manifest)
            for change in ({"role": opposite}, {"required": True}):
                altered = copy.deepcopy(manifest)
                altered["members"][-1].update(change)
                # Payload digests, role-contract rows, scopes and layers are
                # unchanged and self-consistent; they are not this guard.
                with self.subTest(policy=policy, change=change):
                    with self.assertRaisesRegex(n.SelectionError, "json-snapshot-member-scope"):
                        n.validate_selected_manifest(altered, expected_context=CTX,
                                                     role_contract=self.roles)
                    forged = rewrite_tar(raw, mutate_manifest=lambda value:
                        value["members"][-1].update(change))
                    with self.assertRaisesRegex(n.SelectionError, "json-snapshot-member-scope"):
                        self.inspect(forged)

        for phase in ("original", "edited"):
            self.spec["members"] = self.spec["members"][:2]
            self.roles = dict(ROLES)
            for suffix in ("failure", "failure-custody"):
                self.add_snapshot("hyperframes-" + phase + "-render-" + suffix)
            raw, manifest = self.capture()
            self.assertEqual(self.inspect(raw), manifest)
            def swap_roles(value):
                first, second = value["members"][-2:]
                first["role"], second["role"] = second["role"], first["role"]
            altered = copy.deepcopy(manifest)
            swap_roles(altered)
            # This is a real two-leaf swap, not an isolated substitution.
            # Counts per phase/layer/role, required flags, policies, paths,
            # original payload bytes/digests, layers and scopes stay exact.
            counts = lambda value: sorted((row["phase"], row["layer"], row["role"])
                                          for row in value["members"])
            self.assertEqual(counts(altered), counts(manifest))
            self.assertEqual({key: value for key, value in altered.items() if key != "members"},
                             {key: value for key, value in manifest.items() if key != "members"})
            for before, after in zip(manifest["members"], altered["members"]):
                self.assertEqual({key: value for key, value in before.items() if key != "role"},
                                 {key: value for key, value in after.items() if key != "role"})
            with self.subTest(phase=phase, two_present_leaf_roles_swapped=True):
                with self.assertRaisesRegex(n.SelectionError, "json-snapshot-member-scope"):
                    n.validate_selected_manifest(altered, expected_context=CTX,
                                                 role_contract=self.roles)
                forged = rewrite_tar(raw, mutate_manifest=swap_roles)
                with self.assertRaisesRegex(n.SelectionError, "json-snapshot-member-scope"):
                    self.inspect(forged)

if __name__ == "__main__":
    unittest.main(verbosity=2)
