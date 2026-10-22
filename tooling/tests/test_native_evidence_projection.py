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

def rewrite_tar(raw, mutate_manifest=None, extra=None, mutate_payload=None):
    out = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as source, \
         tarfile.open(fileobj=out, mode="w", format=tarfile.USTAR_FORMAT) as target:
        for info in source.getmembers():
            data = source.extractfile(info).read()
            if info.name == "selected-evidence-manifest.json" and mutate_manifest:
                d = n.strict(data); mutate_manifest(d); data = n.canonical(d)
            if mutate_payload: data = mutate_payload(info.name, data)
            info.size = len(data); target.addfile(info, io.BytesIO(data))
        if extra:
            info, data = extra
            target.addfile(info, io.BytesIO(data) if data is not None else None)
    return out.getvalue()

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

if __name__ == "__main__":
    unittest.main(verbosity=2)
