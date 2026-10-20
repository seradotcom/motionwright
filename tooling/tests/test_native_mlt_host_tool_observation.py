"""Offline namespace and descriptor regressions for the original MLT witness."""
from __future__ import annotations
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_g2_observation as g2
import native_mlt_loaded_module as loaded

CWD = "/tmp/semwright-video-" + "a" * 32
OUTPUT = CWD + "/partial.mp4"

def command(output=OUTPUT, executable="/plugin/tools/melt"):
    return (executable + "\0project.mlt\0-silent\0-consumer\0avformat:" + output + "\0").encode()

def metadata(**changes):
    fields = dict(st_dev=8, st_ino=19, st_mode=stat.S_IFREG | 0o500, st_uid=0, st_gid=0,
                  st_size=13, st_mtime_ns=5, st_ctime_ns=7, st_nlink=1)
    fields.update(changes)
    return SimpleNamespace(**fields)

def proc_stat(pid, parent, start):
    return (str(pid) + " (melt) " + " ".join(["S",str(parent)]+["0"]*17+[str(start)])).encode()


class HostConsumerSelectionTests(unittest.TestCase):
    def test_absolute_namespace_output_does_not_use_the_host_display_path_as_authority(self):
        self.assertEqual(g2.host_tool_consumer(command(), "/another-proc-link-display"), OUTPUT)
    def test_relative_output_is_resolved_only_from_the_exact_original_private_cwd(self):
        self.assertEqual(g2.host_tool_consumer(command("partial.mp4"), CWD), OUTPUT)
        with self.assertRaises(g2.G2Unverified):
            g2.host_tool_consumer(command("partial.mp4"), "/tmp")
    def test_unrelated_mp4_in_tmp_or_another_name_is_refused(self):
        for value in ("/tmp/partial.mp4", CWD+"/other.mp4", CWD+"/../partial.mp4",
                      "/home/owner/output/partial.mp4"):
            with self.subTest(value=value), self.assertRaises(g2.G2Unverified):
                g2.host_tool_consumer(command(value), CWD)
    def test_foreign_executable_argument_is_not_accepted_by_basename(self):
        with self.assertRaisesRegex(g2.G2Unverified,"executable_argument"):
            g2.host_tool_consumer(command(executable="/untrusted/melt"), CWD)
    def test_duplicate_consumers_are_not_collapsed(self):
        raw = command() + b"-consumer\0avformat:partial.mp4\0"
        with self.assertRaises(g2.G2Unverified):
            g2.host_tool_consumer(raw, CWD)
    def test_discovery_and_lossless_intermediaries_do_not_select_an_mp4_master(self):
        self.assertIsNone(g2.host_tool_consumer(b"/plugin/tools/melt\0-version\0", CWD))
        self.assertIsNone(g2.host_tool_consumer(command("partial.mkv"), CWD))
    def test_loaded_module_default_does_not_accept_relative_output_without_cwd_binding(self):
        with self.assertRaises(loaded.LoadedModuleUnverified):
            loaded.verified_melt_cmdline(command("partial.mp4"), OUTPUT)
        value = loaded.verified_melt_cmdline(command("partial.mp4"), OUTPUT, relative_cwd=CWD)
        self.assertTrue(value["relative_cwd_binding_required"])
        self.assertEqual(value["actual_consumer_argument"], "partial.mp4")
    def test_relative_context_cannot_rebind_another_directory_or_basename(self):
        for expected,cwd in [(OUTPUT,"/tmp"),(OUTPUT,CWD+"0"),(CWD+"/other.mp4",CWD)]:
            with self.subTest(expected=expected,cwd=cwd), self.assertRaises(loaded.LoadedModuleUnverified):
                loaded.verified_melt_cmdline(command("partial.mp4"), expected, relative_cwd=cwd)


class NamespaceOpenTests(unittest.TestCase):
    def setUp(self):
        # The fixture models Linux fdinfo while contract tests also run on Windows.
        patch = mock.patch.object(g2.os,"O_ACCMODE",3,create=True)
        patch.start()
        self.addCleanup(patch.stop)
    def test_fixed_namespace_walk_uses_no_follow_and_closes_all_intermediate_fds(self):
        calls = []
        def opened(path,flags,**kwargs):
            calls.append((str(path),flags,kwargs))
            return 10+len(calls)
        with mock.patch.object(g2.os,"open",side_effect=opened), \
             mock.patch.object(g2.os,"close") as close, \
             mock.patch.object(g2.os,"O_DIRECTORY",0x10000,create=True), \
             mock.patch.object(g2.os,"O_NOFOLLOW",0x20000,create=True):
            fd = g2.namespace_path(Path("/proc/41"),"/tmp/semwright-video-abc/partial.mp4")
        self.assertEqual(fd,14)
        self.assertEqual([row[0] for row in calls],
                         [str(Path("/proc/41/root")),"tmp","semwright-video-abc","partial.mp4"])
        self.assertNotIn("dir_fd",calls[0][2])
        for _,flags,options in calls[1:]:
            self.assertTrue(flags & 0x20000)
            self.assertIn("dir_fd",options)
        self.assertEqual(close.call_args_list,[mock.call(11),mock.call(12),mock.call(13)])
    def test_symlink_component_failure_does_not_fall_back_or_leak_parent_fd(self):
        with mock.patch.object(g2.os,"open",side_effect=[10,OSError(40,"symlink")]) as opened, \
             mock.patch.object(g2.os,"close") as close, \
             mock.patch.object(g2.os,"O_DIRECTORY",0x10000,create=True), \
             mock.patch.object(g2.os,"O_NOFOLLOW",0x20000,create=True):
            with self.assertRaises(OSError):
                g2.namespace_path(Path("/proc/41"),"/tmp/job/partial.mp4")
        self.assertEqual(opened.call_count,2)
        close.assert_called_once_with(10)
    def test_namespace_walk_rejects_traversal_and_foreign_relative_paths(self):
        for value in ("../partial.mp4","//tmp/partial.mp4","/tmp/../partial.mp4",
                      "/tmp//partial.mp4","/tmp\\partial.mp4"):
            with self.subTest(value=value), mock.patch.object(g2.os,"open") as opened:
                with self.assertRaises(g2.G2Unverified):
                    g2.namespace_path(Path("/proc/41"),value)
                opened.assert_not_called()
    def test_native_descriptor_must_be_writable_and_the_same_inode(self):
        expected = metadata()
        fd = mock.MagicMock()
        fd.name = "9"
        fd.stat.return_value = expected
        proc = mock.MagicMock()
        proc.__truediv__.return_value.iterdir.return_value = [fd]
        with mock.patch.object(g2,"bounded_proc_bytes",return_value=b"flags:\t01\n"):
            self.assertEqual(g2.matching_write_descriptors(proc,expected),[9])
        with mock.patch.object(g2,"bounded_proc_bytes",return_value=b"flags:\t00\n"):
            with self.assertRaisesRegex(g2.G2Unverified,"open_output_not_observed"):
                g2.matching_write_descriptors(proc,expected)
        fd.stat.return_value = metadata(st_ino=20)
        with mock.patch.object(g2,"bounded_proc_bytes",return_value=b"flags:\t01\n"):
            with self.assertRaises(g2.G2Unverified):
                g2.matching_write_descriptors(proc,expected)
    def test_ambiguous_fdinfo_flags_cannot_claim_a_write_descriptor(self):
        fd = mock.MagicMock()
        fd.name = "9"
        fd.stat.return_value = metadata()
        proc = mock.MagicMock()
        proc.__truediv__.return_value.iterdir.return_value = [fd]
        with mock.patch.object(g2,"bounded_proc_bytes",return_value=b"flags:\t01\nflags:\t02\n"):
            with self.assertRaisesRegex(g2.G2Unverified,"fd_flags"):
                g2.matching_write_descriptors(proc,metadata())


class HostOwnerContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.owner = Path(self.tmp.name).resolve()
        self.output = self.owner/"output"
        for name in ("output","bin","config"):
            (self.owner/name).mkdir()
        self.runner = self.owner/"bin/semwright-mlt-runtime-runner"
        self.runner.write_bytes(b"test-double-runner-not-an-executable")
        self.manifest = {"id":"mlt-video","tools":[{"name":"mlt-runner","root":"mlt-runner-root",
            "dependencies":["melt","ffprobe","ffmpeg"],"sha256":g2.sha(self.runner.read_bytes())}]}
        self.path = self.owner/"config/mlt-video-driver.json"
        self.path.write_text(json.dumps(self.manifest),encoding="utf-8")
        self.actual_lstat = Path.lstat
        def private_mode(path,*args,**kwargs):
            result = self.actual_lstat(path,*args,**kwargs)
            if path in (self.owner,self.output):
                return metadata(st_dev=result.st_dev,st_ino=result.st_ino,st_mode=stat.S_IFDIR|0o700,
                                st_uid=result.st_uid,st_gid=result.st_gid)
            return result
        self.patches = [mock.patch.object(Path,"lstat",private_mode),
                        mock.patch.object(g2.os,"getuid",return_value=self.owner.stat().st_uid,create=True)]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
    def test_owner_manifest_and_binary_bytes_form_the_preflight_contract(self):
        result = g2.host_tool_contract(self.output)
        self.assertEqual(result["runner_sha256"],g2.sha(self.runner.read_bytes()))
        self.assertEqual(result["manifest_sha256"],g2.sha(self.path.read_bytes()))
        self.assertEqual(result["output_path"],str(self.output))
    def test_changed_runner_bytes_cannot_keep_a_stale_manifest_pin(self):
        self.runner.write_bytes(b"changed")
        with self.assertRaisesRegex(g2.G2Unverified,"source_pin"):
            g2.host_tool_contract(self.output)
    def test_duplicate_runner_tool_and_changed_dependencies_are_rejected(self):
        self.manifest["tools"].append(dict(self.manifest["tools"][0]))
        self.path.write_text(json.dumps(self.manifest),encoding="utf-8")
        with self.assertRaises(g2.G2Unverified):
            g2.host_tool_contract(self.output)
        self.manifest["tools"] = [self.manifest["tools"][0]]
        self.manifest["tools"][0]["dependencies"] = ["melt"]
        self.path.write_text(json.dumps(self.manifest),encoding="utf-8")
        with self.assertRaises(g2.G2Unverified):
            g2.host_tool_contract(self.output)


class ProcNode:
    def __init__(self,model,key=""):
        self.model,self.key = model,key
    def __truediv__(self,name):
        return ProcNode(self.model,(self.key+"/"+str(name)).strip("/"))
    def __str__(self):
        return "/proc/"+self.key
    def stat(self):
        value = self.model.stats.get(self.key,self.model.directory)
        return value() if callable(value) else value
    def is_dir(self):
        return stat.S_ISDIR(self.stat().st_mode)
    def is_symlink(self):
        return self.key in self.model.symlinks
    def read_bytes(self):
        if self.key not in self.model.raw:
            raise FileNotFoundError(2,self.key)
        return self.model.raw[self.key]
    def open(self,_mode):
        if self.key not in self.model.raw:
            raise FileNotFoundError(2,self.key)
        return io.BytesIO(self.model.raw[self.key])


class NamespaceObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.output = self.root/"output"
        self.output.mkdir()
        self.partial = self.root/"partial"
        self.partial.write_bytes(b"not-a-real-native-video")
        self.raw,self.stats,self.links,self.symlinks = {},{},{},set()
        self.directory = metadata(st_mode=stat.S_IFDIR|0o700)
        self.work = metadata(st_ino=70,st_mode=stat.S_IFDIR|0o700)
        self.root_metadata = metadata(st_ino=80,st_mode=stat.S_IFDIR|0o755)
        self.runner_metadata = metadata(st_ino=90)
        for pid,parent,start in [(10,1,100),(20,10,200),(41,20,300)]:
            self.raw[str(pid)+"/stat"] = proc_stat(pid,parent,start)
            self.stats[str(pid)] = self.directory
            self.stats[str(pid)+"/root"] = self.root_metadata
            self.links[str(pid)+"/ns/mnt"] = "mnt:[native]"
            self.links[str(pid)+"/ns/user"] = "user:[native]"
            self.links[str(pid)+"/ns/pid"] = "pid:[native]"
        self.raw["20/cmdline"] = ("\0".join(g2.HOST_RUNNER_ARGUMENTS)+"\0").encode()
        self.raw["41/cmdline"] = command()
        self.links["41/cwd"] = CWD
        self.links["self/ns/mnt"] = "mnt:[observer]"
        self.stats["41/cwd"] = self.work
        self.stats["20/exe"] = self.runner_metadata
        self.contract = {"runner_sha256":"b"*64,"output_path":str(self.output),
                         "output_identity":list(g2.node_identity(self.output.lstat()))}
        self.opened_fds = []
        self.original_fstat = os.fstat
        def open_namespace(proc,name,**kwargs):
            self.assertEqual(str(proc),"/proc/41")
            self.assertEqual(name,OUTPUT)
            fd = os.open(self.partial,os.O_RDONLY|getattr(os,"O_BINARY",0))
            self.opened_fds.append(fd)
            return fd
        def observed_fstat(fd):
            value = self.original_fstat(fd)
            fields = {name:getattr(value,name) for name in
                      ("st_dev","st_ino","st_mode","st_uid","st_gid","st_size",
                       "st_mtime_ns","st_ctime_ns","st_nlink")}
            fields["st_mode"] &= ~0o022
            fields["st_uid"] = 0
            return SimpleNamespace(**fields)
        @contextmanager
        def binary(path,digest,budget):
            self.assertEqual(str(path),"/proc/20/root/plugin/bin")
            self.assertEqual(digest,"b"*64)
            self.assertEqual(budget,loaded.MAX_MELT)
            yield {"sha256":digest,"stat":self.runner_metadata}
        self.patches = [
            mock.patch.object(g2,"PROC_ROOT",ProcNode(self)),
            mock.patch.object(g2.os,"getuid",return_value=0,create=True),
            mock.patch.object(g2.os,"readlink",side_effect=lambda node:self.links[node.key]),
            mock.patch.object(g2,"host_tool_contract",return_value=self.contract),
            mock.patch.object(g2,"namespace_directory",
                              side_effect=lambda _proc,name:self.output.lstat() if name=="/workspace/output" else self.work),
            mock.patch.object(g2,"namespace_path",side_effect=open_namespace),
            mock.patch.object(g2,"matching_write_descriptors",return_value=[9]),
            mock.patch.object(g2.os,"fstat",side_effect=observed_fstat),
            mock.patch.object(loaded,"_verified_binary",side_effect=binary),
            mock.patch.object(loaded,"inspect_live_original",return_value={"test_double":True}),
        ]
        self.active = [patch.start() for patch in self.patches]
        for patch in self.patches:
            self.addCleanup(patch.stop)
        self.attest = self.active[-1]
        self.addCleanup(self.cleanup)
    def cleanup(self):
        for fd in self.opened_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self.tmp.cleanup()
    def observe(self):
        return g2.NamespaceMaster.observe(41,(10,100),self.output,self.contract,"a"*64)
    def test_exact_parent_namespace_output_fd_and_single_shared_module_inspector(self):
        result = self.observe()
        self.assertEqual(result.identity,(41,300))
        self.assertEqual(result.receipt["runtime_parent"]["pid"],20)
        self.assertEqual(result.receipt["original_melt_write_descriptors"],[9])
        self.assertEqual(result.receipt["publication_binding"],"PENDING_ORIGINAL_SOURCE_BYTES")
        self.attest.assert_called_once_with(41,g2.MELT,"a"*64,g2.admission.INSTALLED,
                                           g2.REPAIRED_MODULE_SHA256,OUTPUT,host_sealed_melt=True)
        result.close()
    def test_selected_pid_generation_must_match_the_captured_descriptor_identity(self):
        observer = object.__new__(g2.LiveObserver)
        observer.ancestor = (10,100)
        observer.output_root = self.output
        observer.host_contract = self.contract
        observer.melt_sha = "a"*64
        observer.namespace_masters = {}
        captured = mock.Mock(identity=(41,301))
        with mock.patch.object(g2.NamespaceMaster,"observe",return_value=captured):
            with self.assertRaisesRegex(g2.G2Unverified,"identity_changed_before_capture"):
                observer.observe_namespace(41)
        captured.close.assert_called_once_with()
        self.assertEqual(observer.namespace_masters,{})
    def test_parent_with_foreign_arguments_cannot_supply_authority(self):
        self.raw["20/cmdline"] = b"/plugin/bin\0another-operation\0"
        with self.assertRaisesRegex(g2.G2Unverified,"parent_not_original"):
            self.observe()
        self.attest.assert_not_called()
        self.assertEqual(self.opened_fds,[])
    def test_foreign_namespace_or_observer_namespace_is_rejected(self):
        self.links["20/ns/mnt"] = "mnt:[foreign]"
        with self.assertRaisesRegex(g2.G2Unverified,"namespace_mismatch"):
            self.observe()
        self.links["20/ns/mnt"] = self.links["41/ns/mnt"] = "mnt:[observer]"
        with self.assertRaisesRegex(g2.G2Unverified,"namespace_mismatch"):
            self.observe()
        self.attest.assert_not_called()
    def test_foreign_output_mount_inode_is_not_the_owner_job(self):
        with mock.patch.object(g2,"namespace_directory",return_value=metadata(st_mode=stat.S_IFDIR|0o700)):
            with self.assertRaisesRegex(g2.G2Unverified,"output_mount"):
                self.observe()
        self.attest.assert_not_called()
    def test_nonprivate_or_foreign_owner_scratch_cannot_bind(self):
        self.work = metadata(st_ino=70,st_mode=stat.S_IFDIR|0o755)
        with self.assertRaisesRegex(g2.G2Unverified,"private_work_identity"):
            self.observe()
        self.work = metadata(st_ino=70,st_mode=stat.S_IFDIR|0o700,st_uid=999)
        with self.assertRaisesRegex(g2.G2Unverified,"private_work_identity"):
            self.observe()
        self.attest.assert_not_called()
    def test_changed_preflight_owner_contract_is_refused(self):
        with mock.patch.object(g2,"host_tool_contract",return_value={**self.contract,"runner_sha256":"f"*64}):
            with self.assertRaisesRegex(g2.G2Unverified,"source_contract_changed"):
                self.observe()
        self.attest.assert_not_called()
    def test_unobserved_write_fd_cannot_be_replaced_by_a_named_partial_file(self):
        with mock.patch.object(g2,"matching_write_descriptors",
                               side_effect=g2.G2Unverified("g2_original_melt_open_output_not_observed")):
            with self.assertRaisesRegex(g2.G2Unverified,"open_output_not_observed"):
                self.observe()
        self.attest.assert_not_called()
        with self.assertRaises(OSError):
            self.original_fstat(self.opened_fds[0])
    def test_shared_module_rejection_remains_a_failure_and_closes_the_held_fd(self):
        self.attest.side_effect = loaded.LoadedModuleUnverified("module inode changed")
        with self.assertRaisesRegex(loaded.LoadedModuleUnverified,"inode changed"):
            self.observe()
        with self.assertRaises(OSError):
            self.original_fstat(self.opened_fds[0])
    def test_pid_change_after_module_read_cannot_keep_a_receipt(self):
        def change(*_args,**_kwargs):
            self.raw["41/stat"] = proc_stat(41,20,301)
            return {"test_double":True}
        self.attest.side_effect = change
        with self.assertRaisesRegex(g2.G2Unverified,"process_context_changed"):
            self.observe()
        with self.assertRaises(OSError):
            self.original_fstat(self.opened_fds[0])
    def test_parent_executable_inode_mismatch_is_rejected_before_module_acceptance(self):
        self.stats["20/exe"] = metadata(st_ino=999)
        with self.assertRaisesRegex(g2.G2Unverified,"parent_executable"):
            self.observe()
        self.attest.assert_not_called()
    def test_same_pid_is_not_accepted_with_changed_namespace_root(self):
        def change(*_args,**_kwargs):
            self.stats["41/root"] = metadata(st_ino=999,st_mode=stat.S_IFDIR|0o755)
            return {"test_double":True}
        self.attest.side_effect = change
        with self.assertRaisesRegex(g2.G2Unverified,"process_context_changed"):
            self.observe()
    def test_no_mp4_consumer_does_not_open_an_output_or_call_the_inspector(self):
        self.raw["41/cmdline"] = b"/plugin/tools/melt\0-version\0"
        self.assertIsNone(self.observe())
        self.assertEqual(self.opened_fds,[])
        self.attest.assert_not_called()


class HeldOutputCustodyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.output = self.root/"output"
        self.output.mkdir()
        self.evidence = self.root/"private"
        self.evidence.mkdir()
        self.original = self.root/"partial.mp4"
        self.raw = bytes(range(256))+b"\r\n\x1a"
        self.original.write_bytes(self.raw)
        self.fd = os.open(self.original,os.O_RDONLY|getattr(os,"O_BINARY",0))
        self.opened = os.fstat(self.fd)
        self.contract = {"output_path":str(self.output)}
        self.captured = g2.NamespaceMaster((41,300),self.fd,self.opened,
            {"output_path":OUTPUT,"owner_contract":self.contract})
        self.master = self.output/"master.mp4"
        self.master.write_bytes(self.raw)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.captured.close)
    def test_retained_raw_bytes_keep_binary_semantics_and_are_written_once(self):
        self.captured.retain(self.evidence)
        path = self.evidence/"original-melt-consumer-41-300.mp4"
        self.assertEqual(path.read_bytes(),self.raw)
        with mock.patch.object(g2,"new_bytes") as write:
            self.captured.retain(self.evidence)
        write.assert_not_called()
    def test_same_source_descriptor_bytes_bind_the_published_master_without_same_inode_claim(self):
        self.captured.retain(self.evidence)
        with mock.patch.object(g2,"process_identity",side_effect=FileNotFoundError()), \
             mock.patch.object(g2,"host_tool_contract",return_value=self.contract):
            result = self.captured.bind_master(self.master)
        self.assertEqual(result["publication_binding"],"SAME_ORIGINAL_OPEN_DESCRIPTOR_BYTES")
        self.assertEqual(result["published_output_sha256"],g2.sha(self.raw))
        self.assertFalse(result["human_acceptance"])
        self.assertNotEqual(self.original.stat().st_ino,self.master.stat().st_ino)
    def test_different_published_bytes_are_rejected_even_if_name_and_size_match(self):
        self.captured.retain(self.evidence)
        self.master.write_bytes(bytes([self.raw[0]^1])+self.raw[1:])
        with mock.patch.object(g2,"process_identity",side_effect=FileNotFoundError()), \
             self.assertRaisesRegex(g2.G2Unverified,"bytes_differ"):
            self.captured.bind_master(self.master)
    def test_a_still_live_original_pid_cannot_supply_completed_source_bytes(self):
        self.captured.retain(self.evidence)
        with mock.patch.object(g2,"process_identity",return_value=(20,300)), \
             self.assertRaisesRegex(g2.G2Unverified,"still_live"):
            self.captured.bind_master(self.master)
    def test_missing_private_source_capture_is_not_silently_reconstructed_from_master(self):
        with self.assertRaisesRegex(g2.G2Unverified,"not_retained"):
            self.captured.bind_master(self.master)
    def test_existing_snapshot_survives_failed_exclusive_write_and_no_automatic_replay(self):
        path = self.evidence/"original-melt-consumer-41-300.mp4"
        path.write_bytes(b"original preexisting evidence")
        with self.assertRaises(FileExistsError):
            self.captured.retain(self.evidence)
        self.captured.retain(self.evidence)
        self.assertEqual(path.read_bytes(),b"original preexisting evidence")
        self.assertIsNone(self.captured.private_snapshot)
    def test_same_inode_mutation_during_read_is_rejected(self):
        actual = os.fstat(self.fd)
        fields = {name:getattr(actual,name) for name in
                  ("st_dev","st_ino","st_mode","st_uid","st_gid","st_size",
                   "st_mtime_ns","st_ctime_ns","st_nlink")}
        changed = SimpleNamespace(**{**fields,"st_mtime_ns":fields["st_mtime_ns"]+1})
        with mock.patch.object(g2.os,"fstat",side_effect=[actual,changed]), \
             self.assertRaisesRegex(g2.G2Unverified,"changed_during_read"):
            g2.read_held_output(self.fd,self.opened)
    def test_foreign_inode_on_the_observer_descriptor_is_rejected(self):
        altered = metadata(st_dev=self.opened.st_dev,st_ino=self.opened.st_ino+1,
                           st_mode=self.opened.st_mode,st_uid=self.opened.st_uid,st_gid=self.opened.st_gid,
                           st_size=len(self.raw))
        with mock.patch.object(g2.os,"fstat",return_value=altered), \
             self.assertRaisesRegex(g2.G2Unverified,"identity_or_budget"):
            g2.read_held_output(self.fd,self.opened)
    def test_changed_owner_contract_cannot_authorize_the_published_result(self):
        self.captured.retain(self.evidence)
        with mock.patch.object(g2,"process_identity",side_effect=FileNotFoundError()), \
             mock.patch.object(g2,"host_tool_contract",return_value={"foreign":True}), \
             self.assertRaisesRegex(g2.G2Unverified,"source_contract_changed"):
            self.captured.bind_master(self.master)



class LoadedHostSealedExecutableTests(unittest.TestCase):
    def model(self):
        model = SimpleNamespace(stats={}, symlinks=set(),
                                directory=metadata(st_mode=stat.S_IFDIR|0o700))
        model.stats["41"] = metadata(st_mode=stat.S_IFDIR|0o700)
        model.stats["41/exe"] = metadata()
        return model, ProcNode(model,"41")

    def context(self, source=None):
        @contextmanager
        def binary(path,digest,budget):
            self.assertEqual(str(path),"/proc/41/root/plugin/tools/melt")
            self.assertEqual(digest,"e"*64)
            self.assertEqual(budget,loaded.MAX_MELT)
            yield {"stat":metadata() if source is None else source,"sha256":digest}
        return binary

    def test_sealed_executable_uses_existing_same_descriptor_hash_verifier(self):
        model,proc = self.model()
        with mock.patch.object(loaded,"_verified_binary",side_effect=self.context()) as binary:
            with loaded._verified_host_sealed_melt(proc,"e"*64) as value:
                self.assertEqual(value["sha256"],"e"*64)
        binary.assert_called_once()

    def test_wrong_namespace_copy_mode_owner_or_actual_exe_inode_is_rejected(self):
        for source in (metadata(st_mode=stat.S_IFREG|0o700),metadata(st_uid=1),metadata(st_ino=99)):
            model,proc = self.model()
            with self.subTest(source=source), \
                 mock.patch.object(loaded,"_verified_binary",side_effect=self.context(source)):
                with self.assertRaises(loaded.LoadedModuleUnverified):
                    with loaded._verified_host_sealed_melt(proc,"e"*64):
                        self.fail("foreign executable was admitted")

    def test_symlink_namespace_component_is_refused_before_hashing(self):
        model,proc = self.model()
        model.symlinks.add("41/root/plugin")
        with mock.patch.object(loaded,"_verified_binary") as binary:
            with self.assertRaisesRegex(loaded.LoadedModuleUnverified,"namespace_directory"):
                with loaded._verified_host_sealed_melt(proc,"e"*64):
                    pass
            binary.assert_not_called()

    def test_changed_actual_executable_after_the_read_cannot_keep_the_receipt(self):
        model,proc = self.model()
        with mock.patch.object(loaded,"_verified_binary",side_effect=self.context()):
            with self.assertRaisesRegex(loaded.LoadedModuleUnverified,"live_executable_changed"):
                with loaded._verified_host_sealed_melt(proc,"e"*64):
                    model.stats["41/exe"] = metadata(st_ino=99)

    def inspect(self, *, foreign_mapping=False):
        from tooling.tests.test_native_mlt_loaded_module import _ObservedBinaryPath
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            local = root/"libmltavformat.so"
            raw = b"test-double-module-not-an-ELF"
            local.write_bytes(raw)
            expected = "/usr/lib/x86_64-linux-gnu/mlt-7/libmltavformat.so"
            module = _ObservedBinaryPath(local,expected)
            model,proc = self.model()
            model.raw = {
                "41/stat":proc_stat(41,20,300),
                "41/cmdline":command("partial.mp4"),
                "41/environ":b"LC_ALL=C\0",
                "41/maps":("1000-2000 r-xp 00000000 08:01 "+str(local.stat().st_ino)+" "
                           + ("/tmp/foreign/libmltavformat.so" if foreign_mapping else expected)+"\n").encode(),
            }
            cwd_meta = metadata(st_mode=stat.S_IFDIR|0o700,st_ino=71)
            model.stats["41/cwd"] = cwd_meta
            model.stats["41/root"+CWD] = cwd_meta
            proc_root = ProcNode(model)
            real_path = loaded.Path
            original_binary = loaded._verified_binary
            @contextmanager
            def binary(path,digest,budget):
                if isinstance(path,ProcNode):
                    with self.context()(path,digest,budget) as value:
                        yield value
                else:
                    with original_binary(path,digest,budget) as value:
                        yield value
            def path(value):
                return proc_root if value=="/proc" else real_path(value)
            links = {"41/exe":"/plugin/tools/melt","41/cwd":"/proc-display"+CWD}
            with mock.patch.object(loaded,"Path",side_effect=path), \
                 mock.patch.object(loaded.sys,"platform","linux"), \
                 mock.patch.object(loaded.os,"readlink",side_effect=lambda node:links[node.key]), \
                 mock.patch.object(loaded.os,"major",return_value=8,create=True), \
                 mock.patch.object(loaded.os,"minor",return_value=1,create=True), \
                 mock.patch.object(loaded,"_verified_binary",side_effect=binary):
                return loaded.inspect_live_original(
                    41,real_path("/usr/bin/melt-7"),"e"*64,module,g2.sha(raw),OUTPUT,
                    host_sealed_melt=True,
                )

    def test_host_mode_preserves_the_original_module_mapping_and_relative_cwd_gates(self):
        result = self.inspect()
        self.assertEqual(result["actual_process_executable_scope"],"host-sealed-melt")
        self.assertEqual(result["actual_process_cwd"],CWD)
        self.assertTrue(result["source_bound_native_consumer_argv"]["relative_cwd_binding_required"])
        self.assertEqual(result["mapped_native_module"]["mapped_segments"],1)
        self.assertFalse(result["human_audio_acceptance"])

    def test_host_mode_does_not_accept_an_alternate_mapped_module(self):
        with self.assertRaisesRegex(loaded.LoadedModuleUnverified,"alternate_or_deleted"):
            self.inspect(foreign_mapping=True)


if __name__ == "__main__":
    unittest.main()
