import importlib.util, io, json, os, pathlib, subprocess, sys, tempfile, threading, time, unittest
from unittest import mock
from types import SimpleNamespace
MODULE = pathlib.Path(__file__).with_name("candidate_dependency_capture.py")
spec = importlib.util.spec_from_file_location("candidate_dependency_capture", MODULE)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)

class CaptureContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="a2-notice-capture-", dir=os.environ.get("TEMP"))
        self.root = pathlib.Path(self.temp.name).resolve()
    def tearDown(self):
        self.temp.cleanup()
    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout
    def repo(self):
        self.git("init", "-q")
        (self.root/"tracked.txt").write_bytes(b"first\nsecond\n")
        self.git("-c", "core.autocrlf=false", "add", "tracked.txt")
        self.git("-c", "user.name=Inventory fixture", "-c", "user.email=fixture@invalid.local",
                 "commit", "-qm", "Exact source fixture")
        return self.git("rev-parse", "HEAD").decode().strip()
    def redirect(self, link, target):
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("directory symlink unavailable")
            result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode:
                self.skipTest("directory junction unavailable")
    def test_current_git_source_and_exact_crlf_are_explicitly_distinguished(self):
        head = self.repo()
        exact = capture.source_snapshot(self.root, head)
        self.assertEqual(exact["files"][0]["checkout_transform"], "EXACT")
        self.git("config", "core.autocrlf", "true")
        (self.root/"tracked.txt").write_bytes(b"first\r\nsecond\r\n")
        crlf = capture.source_snapshot(self.root, head)
        self.assertEqual(crlf["files"][0]["git_blob"], exact["files"][0]["git_blob"])
        self.assertNotEqual(crlf["files"][0]["sha256"], exact["files"][0]["sha256"])
        self.assertEqual(crlf["files"][0]["checkout_transform"], "EXACT_LF_TO_CRLF")
    def test_mixed_newlines_are_not_silently_normalized(self):
        head = self.repo()
        (self.root/"tracked.txt").write_bytes(b"first\r\nsecond\n")
        with self.assertRaisesRegex(capture.CaptureError, "tracked_bytes_differ"):
            capture.source_snapshot(self.root, head)
    def test_same_size_tracked_change_cannot_keep_source_binding(self):
        head = self.repo()
        (self.root/"tracked.txt").write_bytes(b"other\nsecond\n")
        with self.assertRaisesRegex(capture.CaptureError, "tracked_bytes_differ"):
            capture.source_snapshot(self.root, head)
    def test_staged_source_change_and_wrong_head_are_rejected(self):
        head = self.repo()
        with self.assertRaisesRegex(capture.CaptureError, "source_sha_or_root"):
            capture.source_snapshot(self.root, "0"*40)
        (self.root/"tracked.txt").write_bytes(b"other\nsecond\n")
        self.git("-c", "core.autocrlf=false", "add", "tracked.txt")
        with self.assertRaisesRegex(capture.CaptureError, "index_differs"):
            capture.source_snapshot(self.root, head)
    def test_admitted_anchor_alias_works_and_internal_redirect_is_rejected(self):
        physical = self.root/"physical"; physical.mkdir()
        (physical/"notice.txt").write_bytes(b"actual original notice\n")
        alias = self.root/"alias"; self.redirect(alias, physical)
        self.assertEqual(capture.stable_read(alias, "notice.txt"), b"actual original notice\n")
        with self.assertRaisesRegex(capture.CaptureError, "redirected_path"):
            capture.stable_read(self.root, "alias/notice.txt")
    def test_outside_and_parent_escape_reject_before_creating_directories(self):
        outside = self.root.parent/(self.root.name+"-outside")
        with self.assertRaisesRegex(capture.CaptureError, "path_outside_anchor"):
            capture.new_directory(self.root, outside)
        self.assertFalse(outside.exists())
        with self.assertRaisesRegex(capture.CaptureError, "invalid_relative_path"):
            capture.new_directory(self.root, pathlib.Path("inside/../escaped"))
        self.assertFalse((self.root/"inside").exists())
    def test_existing_state_is_not_reused(self):
        state = capture.new_directory(self.root, "state")
        (state/"foreign").write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):
            capture.new_directory(self.root, "state")
        self.assertEqual((state/"foreign").read_bytes(), b"preserve")
    def test_original_stdout_and_stderr_are_separate_and_command_runs_once(self):
        state = self.root/"state"; state.mkdir()
        command=[sys.executable,"-B","-c",
                 "from pathlib import Path; import sys; Path('witness').open('ab').write(b'1'); sys.stdout.buffer.write(b'{\"reason\":\"build-finished\",\"success\":true}\\n'); sys.stderr.buffer.write(b'diagnostic\\n')"]
        result = capture.run_captured(command, self.root, state, "build", timeout=10)
        self.assertEqual(result["returncode"],0)
        self.assertEqual((state/"build.stderr").read_bytes(),b"diagnostic\n")
        self.assertEqual(capture.cargo_messages((state/"build.stdout").read_bytes()),
                         [{"reason":"build-finished","success":True}])
        with self.assertRaises(FileExistsError):
            capture.run_captured(command, self.root, state, "build", timeout=10)
        self.assertEqual((self.root/"witness").read_bytes(),b"1")
    def test_stream_budget_failure_preserves_bounded_original_and_no_success(self):
        state = self.root/"state"; state.mkdir()
        with self.assertRaisesRegex(capture.CaptureError,"process_failed"):
            capture.run_captured([sys.executable,"-B","-c","import sys;sys.stdout.write('x'*100000)"],
                                 self.root,state,"too-large",stream_limit=31,timeout=10)
        self.assertEqual((state/"too-large.stdout").stat().st_size,31)
        result=json.loads((state/"too-large.completion.json").read_bytes())
        self.assertIn("stream_budget_exceeded",result["errors"])
    def test_failed_process_cannot_be_promoted_by_success_shaped_stdout(self):
        state=self.root/"state";state.mkdir()
        script='print(\'{"reason":"build-finished","success":true}\');raise SystemExit(7)'
        with self.assertRaisesRegex(capture.CaptureError,"process_failed"):
            capture.run_captured([sys.executable,"-B","-c",script],self.root,state,"bad",timeout=10)
        self.assertEqual(json.loads((state/"bad.completion.json").read_bytes())["returncode"],7)
    def test_non_cargo_build_logs_are_retained_but_not_units(self):
        raw=b'vite building...\n{"chunk":"a.js"}\n{"reason":"build-finished","success":true}\n'
        self.assertEqual(capture.cargo_messages(raw),[{"reason":"build-finished","success":True}])
    def test_duplicate_truncated_unknown_and_missing_cargo_records_reject(self):
        for raw in (b'{"reason":"build-finished","reason":"compiler-artifact"}\n',
                    b'{"reason":"build-finished"\n',
                    b'{"reason":"invented","success":true}\n', b"ordinary log only\n"):
            with self.subTest(raw=raw),self.assertRaises(capture.CaptureError):
                capture.cargo_messages(raw)
    def test_artifact_bytes_are_verified_and_redirect_cannot_substitute(self):
        target=self.root/"target";target.mkdir()
        (target/"unit.rlib").write_bytes(b"compiler artifact fixture")
        messages=[{"reason":"compiler-artifact","filenames":[str(target/"unit.rlib")],"executable":None}]
        observation=capture.artifact_identities({"target_directory":str(target)},messages)
        self.assertEqual(observation,[{"path":"unit.rlib","bytes":25,"sha256":capture.sha(b"compiler artifact fixture")}])
        outside=self.root/"outside";outside.mkdir();(outside/"unit.rlib").write_bytes(b"foreign")
        self.redirect(target/"redirect",outside)
        messages[0]["filenames"]=[str(target/"redirect/unit.rlib")]
        with self.assertRaisesRegex(capture.CaptureError,"redirected_path"):
            capture.artifact_identities({"target_directory":str(target)},messages)


    def test_sibling_loader_reuses_only_the_physical_canonical_modules(self):
        for name in ("cargo_license_sources", "cargo_build_inventory"):
            with self.subTest(name=name):
                module = capture._load_sibling(name)
                self.assertEqual(pathlib.Path(module.__file__).resolve(strict=True),
                                 MODULE.resolve(strict=True).with_name(name + ".py"))
                self.assertIs(capture._load_sibling(name), module)

    def test_foreign_cached_sibling_cannot_supply_the_verifier(self):
        foreign = self.root / "foreign.py"
        foreign.write_text("raise RuntimeError('foreign verifier must never execute')\n")
        for name in ("cargo_license_sources", "cargo_build_inventory"):
            with self.subTest(name=name), mock.patch.dict(
                    sys.modules, {name: SimpleNamespace(__file__=str(foreign))}):
                with self.assertRaisesRegex(capture.CaptureError, "sibling_module_identity_mismatch"):
                    capture._load_sibling(name)

    def test_cached_sibling_without_a_physical_file_is_rejected(self):
        for value in (None, str(self.root / "missing.py")):
            with self.subTest(file=value), mock.patch.dict(
                    sys.modules, {"cargo_license_sources": SimpleNamespace(__file__=value)}):
                with self.assertRaisesRegex(capture.CaptureError, "sibling_module_identity_mismatch"):
                    capture._load_sibling("cargo_license_sources")

    def native_review_fixture(self):
        manifest = self.root / "vendor/glib-0.18.5/Cargo.toml"
        identity = "path+" + manifest.parent.as_uri() + "#glib@0.18.5"
        component = {"id": identity, "name": "glib", "version": "0.18.5",
                     "source": None, "manifest_path": str(manifest)}
        review = {"component_id": identity, "code": "native_content_review_required",
                  "detail": "Rust wrapper material does not establish native C content."}
        return component, review

    def test_native_glib_review_uses_public_component_identity(self):
        component, review = self.native_review_fixture()
        original = dict(review)
        result = capture.public_native_reviews([review], {component["id"]: component}, self.root)
        self.assertEqual(result, [{**review, "component_id":
            "cargo:path:vendor/glib-0.18.5/Cargo.toml#glib@0.18.5"}])
        serialized = capture.encoded({"native_content_reviews": result}).decode()
        self.assertNotIn(component["id"], serialized)
        self.assertNotIn(self.root.as_uri(), serialized)
        self.assertEqual(review, original)

    def test_native_review_cannot_publish_an_unresolved_component_id(self):
        component, review = self.native_review_fixture()
        review["component_id"] = "path+file:///unresolved/private/glib#glib@0.18.5"
        with self.assertRaisesRegex(capture.CaptureError, "native_review_component_unresolved"):
            capture.public_native_reviews([review], {component["id"]: component}, self.root)

    def test_native_registry_review_preserves_existing_public_identity(self):
        identity = "registry+https://github.com/rust-lang/crates.io-index#libsqlite3-sys@0.30.1"
        component = {"id": identity, "name": "libsqlite3-sys", "version": "0.30.1",
                     "source": "registry+https://github.com/rust-lang/crates.io-index"}
        review = {"component_id": identity, "code": "native_content_review_required",
                  "detail": "Native content remains a separate review."}
        result = capture.public_native_reviews([review], {identity: component}, self.root)
        self.assertEqual(result, [review])

    def command_fixture(self):
        cargo = self.root / ("cargo.exe" if os.name == "nt" else "cargo")
        cargo.write_bytes(b"INERT TOOL IDENTITY FIXTURE; NEVER EXECUTED")
        manifest = self.root / "Cargo.toml"
        manifest.write_text('[package]\nname="fixture"\nversion="1.0.0"\n')
        command = [str(cargo), "build", "--locked", "--offline", "-p", "fixture",
                   "--target", "x86_64-pc-windows-gnu", "--message-format=json-render-diagnostics"]
        contract = dict(cargo=str(cargo), cwd=self.root, manifest=manifest,
                        target="x86_64-pc-windows-gnu", package="fixture")
        return command, contract

    def test_explicit_build_contract_binds_driver_target_package_features_and_profile(self):
        command, contract = self.command_fixture()
        command += ["--features", "run,shared/buildside", "--no-default-features", "--release", "-j", "1"]
        result = capture.validate_cargo_build(command, **contract,
                    metadata_features=("run", "shared/buildside"), no_default_features=True)
        self.assertEqual(result["features"], ["run", "shared/buildside"])
        self.assertEqual(result["profile"], "release")
        self.assertFalse(result["default_features"])
        self.assertEqual(result["cargo_executable"]["sha256"],
                         capture.sha(b"INERT TOOL IDENTITY FIXTURE; NEVER EXECUTED"))

    def test_target_and_package_declarations_cannot_label_a_different_build(self):
        command, contract = self.command_fixture()
        for position, value in ((5, "foreign"), (7, "aarch64-apple-darwin")):
            changed = command.copy(); changed[position] = value
            with self.subTest(position=position), self.assertRaisesRegex(capture.CaptureError, "scope_mismatch"):
                capture.validate_cargo_build(changed, **contract)
        no_target = command[:6] + command[8:]
        with self.assertRaisesRegex(capture.CaptureError, "scope_mismatch"):
            capture.validate_cargo_build(no_target, **contract)

    def test_feature_defaults_and_manifest_cannot_diverge_from_metadata(self):
        command, contract = self.command_fixture()
        for extra, metadata, defaults in ((["--features","different"],(),False),
                                         ([],("run",),False),
                                         (["--no-default-features"],(),False),
                                         ([],(),True)):
            with self.subTest(extra=extra,metadata=metadata,defaults=defaults), self.assertRaises(capture.CaptureError):
                capture.validate_cargo_build(command+extra, **contract,
                            metadata_features=metadata, no_default_features=defaults)
        foreign = self.root / "foreign.toml"; foreign.write_text("foreign")
        with self.assertRaisesRegex(capture.CaptureError, "manifest_scope_mismatch"):
            capture.validate_cargo_build(command+["--manifest-path",str(foreign)], **contract)

    def test_arbitrary_command_containing_cargo_words_is_not_a_build_driver(self):
        command, contract = self.command_fixture()
        with self.assertRaisesRegex(capture.CaptureError, "actual_cargo_executable_required"):
            capture.validate_cargo_build([sys.executable, *command[1:]], **contract)
        changed = command.copy(); changed[1] = "test"
        with self.assertRaisesRegex(capture.CaptureError, "single_cargo_build_required"):
            capture.validate_cargo_build(changed, **contract)

    def test_ambiguous_dev_workspace_and_config_build_arguments_are_rejected(self):
        command, contract = self.command_fixture()
        for extra in (["--workspace"],["--tests"],["--all-targets"],["--config","external.toml"],
                      ["--target-dir","foreign"],["--release","--profile","custom"],
                      ["--target","x86_64-pc-windows-gnu"],["-j","0"]):
            with self.subTest(extra=extra), self.assertRaises(capture.CaptureError):
                capture.validate_cargo_build(command+extra, **contract)

    def test_missing_lock_or_non_json_output_cannot_produce_a_capture_contract(self):
        command, contract = self.command_fixture()
        for changed in ([arg for arg in command if arg!="--locked"],
                        [arg.replace("json-render-diagnostics","human") for arg in command]):
            with self.subTest(changed=changed), self.assertRaisesRegex(capture.CaptureError, "locked_cargo_json"):
                capture.validate_cargo_build(changed, **contract)


    def test_tauri_all_bins_selection_is_part_of_the_build_contract(self):
        command, contract = self.command_fixture()
        result = capture.validate_cargo_build(command+["--bins"], **contract)
        self.assertEqual(result["binary_target_selection"], "all-bins")

    def test_crlf_without_actual_checkout_policy_is_not_authenticated(self):
        head = self.repo()
        self.git("config", "core.autocrlf", "false")
        (self.root/"tracked.txt").write_bytes(b"first\r\nsecond\r\n")
        with self.assertRaisesRegex(capture.CaptureError, "tracked_bytes_differ"):
            capture.source_snapshot(self.root, head)

    def test_retained_process_pipe_is_unconfirmed_at_the_shared_deadline(self):
        state=self.root/"pipe-state";state.mkdir()
        read_fd,write_fd=os.pipe()
        drained=threading.Event()
        class Pipe:
            def __init__(self): self.inner=os.fdopen(read_fd,"rb")
            def read(self,n): return self.inner.read(n)
            def close(self):
                self.inner.close();drained.set()
        class Process:
            stdout=Pipe();stderr=io.BytesIO();returncode=0
            def wait(self,timeout=None): return 0
            def kill(self): pass
        timer=threading.Timer(0.75,lambda:os.close(write_fd));timer.daemon=True
        caught=None;started=time.monotonic()
        with mock.patch.object(capture.subprocess,"Popen",return_value=Process()):
            timer.start()
            try:
                capture.run_captured(["inert-capture-fixture"],self.root,state,"pipe",timeout=0.1)
            except capture.CaptureError as error:
                caught=error
            finally:
                elapsed=time.monotonic()-started
                self.assertTrue(drained.wait(1.2),"bounded pipe fixture did not close")
                timer.join(timeout=0.1)
        print("MW_CAPTURE_PIPE_ORACLE "+json.dumps({"configured_seconds":0.1,"writer_release_seconds":0.75,
                 "returned_seconds":elapsed,"error":str(caught) if caught else None}),flush=True)
        self.assertIsInstance(caught,capture.CaptureError)
        self.assertIn("capture_stream_custody_unclosed",str(caught))
        self.assertLess(elapsed,0.65)
        completion=json.loads((state/"pipe.completion.json").read_bytes())
        self.assertIs(completion["stdout_final"],False)
        self.assertNotIn("stdout_sha256",completion)

    def test_timeout_reaping_is_bounded_and_preserves_failed_completion(self):
        state=self.root/"wait-state";state.mkdir();timeouts=[]
        class Process:
            stdout=io.BytesIO();stderr=io.BytesIO();returncode=None
            def wait(self,timeout=None):
                timeouts.append(timeout)
                if len(timeouts)==1:raise subprocess.TimeoutExpired("inert-capture-fixture",timeout)
                return -9
            def kill(self): pass
        with mock.patch.object(capture.subprocess,"Popen",return_value=Process()):
            with self.assertRaisesRegex(capture.CaptureError,"process_failed"):
                capture.run_captured(["inert-capture-fixture"],self.root,state,"timeout",timeout=0.1)
        self.assertEqual(len(timeouts),2)
        self.assertTrue(all(value is not None and 0<=value<=1 for value in timeouts))
        completion=json.loads((state/"timeout.completion.json").read_bytes())
        self.assertIn("process_timeout",completion["errors"])
        self.assertEqual(completion["returncode"],-9)

    def test_build_driver_readback_rejects_same_size_tool_replacement(self):
        command,contract=self.command_fixture()
        driver=capture.validate_cargo_build(command,**contract)
        capture.revalidate_build_driver(driver,contract["cargo"])
        path=pathlib.Path(contract["cargo"]);raw=path.read_bytes()
        path.write_bytes(b"x"+raw[1:])
        with self.assertRaisesRegex(capture.CaptureError,"cargo_executable_changed"):
            capture.revalidate_build_driver(driver,contract["cargo"])


    def binary_binding_fixture(self):
        cargo_build_inventory = capture._load_sibling("cargo_build_inventory")
        fixtures = capture._load_sibling("test_cargo_build_inventory")
        fixture, ROOT, TRIPLE = fixtures.fixture, fixtures.ROOT, fixtures.TRIPLE
        metadata, messages = fixture()
        target = self.root / "target"
        target.mkdir()
        metadata["target_directory"] = str(target)
        for position, message in enumerate(messages):
            if message["reason"] != "compiler-artifact":
                continue
            replacements = {}
            for number, original in enumerate(message["filenames"]):
                path = target / f"unit-{position}-{number}.artifact"
                path.write_bytes(f"observed artifact {position} {number}\n".encode())
                replacements[original] = str(path)
            message["filenames"] = [replacements[p] for p in message["filenames"]]
            if message["executable"] is not None:
                message["executable"] = replacements[message["executable"]]
        inventory = cargo_build_inventory.derive_components(
            metadata, messages, root_package_name=ROOT, target_triple=TRIPLE)
        artifacts = capture.artifact_identities(metadata, messages)
        return metadata, inventory, artifacts

    def bound_index(self):
        metadata, inventory, artifacts = self.binary_binding_fixture()
        public_id = "cargo:path:app/Cargo.toml#app@0.1.0"
        binding = capture.bind_artifacts(metadata, inventory, artifacts, public_root_id=public_id)
        index = {"schema": "motionwright.cargo-build-notices/1", "source": "1"*40,
                 "source_tree": "2"*40, "target": inventory["target_triple"],
                 "root_package": inventory["root_package_name"],
                 "components": [{"component_id": public_id, "name": inventory["root_package_name"]}],
                 **binding}
        executable = pathlib.Path(inventory["root_binary_observations"][0]["executable"])
        return metadata, inventory, artifacts, index, executable

    def binding_arguments(self, index, binary):
        raw = capture.encoded(index)
        return raw, dict(expected_index_sha256=capture.sha(raw),
                         expected_source="1"*40, expected_source_tree="2"*40,
                         expected_target=index["target"], expected_package=index["root_package"],
                         expected_binary=index["root_binary_observations"][0]["name"],
                         binary_root=binary.parent, binary_path=binary)

    def test_root_executable_is_joined_to_exact_captured_artifact(self):
        metadata, inventory, artifacts, index, executable = self.bound_index()
        root = index["root_binary_observations"][0]
        relative = executable.relative_to(pathlib.Path(metadata["target_directory"])).as_posix()
        artifact = next(row for row in artifacts if row["path"] == relative)
        self.assertEqual(root["path"], relative)
        self.assertEqual(root["bytes"], artifact["bytes"])
        self.assertEqual(root["sha256"], artifact["sha256"])
        self.assertEqual(root["name"], inventory["root_binary_observations"][0]["name"])
        self.assertEqual(root["unit_id"], inventory["root_binary_observations"][0]["unit_id"])
        self.assertEqual(index["artifact_identities_sha256"], capture.sha(capture.encoded(artifacts)))
        self.assertNotIn(str(self.root), capture.encoded(index).decode())

    def test_root_must_be_observed_unique_and_present_in_artifact_roster(self):
        from copy import deepcopy
        metadata, inventory, artifacts = self.binary_binding_fixture()
        root = inventory["root_binary_observations"][0]
        relative = pathlib.Path(root["executable"]).relative_to(
            pathlib.Path(metadata["target_directory"])).as_posix()
        missing = [row for row in artifacts if row["path"] != relative]
        cases = [
            (inventory, missing, "root_binary_not_in_artifact_roster"),
            ({**inventory, "root_binary_observations": []}, artifacts, "root_binary_not_observed"),
            ({**inventory, "root_binary_observations": [root, deepcopy(root)]},
             artifacts, "duplicate_root_binary_observation"),
        ]
        for observed, roster, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(capture.CaptureError, reason):
                capture.bind_artifacts(metadata, observed, roster, public_root_id="cargo:path:app#app@0.1.0")

    def test_same_size_root_replacement_after_observation_is_rejected(self):
        metadata, inventory, artifacts = self.binary_binding_fixture()
        executable = pathlib.Path(inventory["root_binary_observations"][0]["executable"])
        original = executable.read_bytes()
        executable.write_bytes(b"X" + original[1:])
        with self.assertRaisesRegex(capture.CaptureError, "root_binary_changed_after_capture"):
            capture.bind_artifacts(metadata, inventory, artifacts, public_root_id="cargo:path:app#app@0.1.0")

    def test_installed_binary_must_equal_captured_root_bytes(self):
        _, _, _, index, executable = self.bound_index()
        installed = self.root / "installed"; installed.mkdir()
        binary = installed / executable.name
        original = executable.read_bytes(); binary.write_bytes(original)
        raw, arguments = self.binding_arguments(index, binary)
        result = capture.verify_binary_binding(raw, **arguments)
        self.assertEqual(result["status"], "CAPTURED_ROOT_BINARY_BYTE_EXACT")
        binary.write_bytes(b"X" + original[1:])
        with self.assertRaisesRegex(capture.CaptureError, "installed_binary_differs_from_cargo_capture"):
            capture.verify_binary_binding(raw, **arguments)

    def test_same_binary_cannot_transfer_between_source_tree_target_or_package(self):
        _, _, _, index, executable = self.bound_index()
        raw, arguments = self.binding_arguments(index, executable)
        changes = {"expected_source": "3"*40, "expected_source_tree": "4"*40,
                   "expected_target": "aarch64-apple-darwin", "expected_package": "other"}
        for key, value in changes.items():
            with self.subTest(key=key), self.assertRaisesRegex(capture.CaptureError, "candidate_scope_mismatch"):
                capture.verify_binary_binding(raw, **{**arguments, key: value})

    def test_artifact_roster_digest_and_unique_paths_are_required(self):
        from copy import deepcopy
        _, _, _, index, executable = self.bound_index()
        changed = deepcopy(index)
        changed["artifact_identities"][0]["sha256"] = "0"*64
        raw, arguments = self.binding_arguments(changed, executable)
        with self.assertRaisesRegex(capture.CaptureError, "artifact_roster_digest_mismatch"):
            capture.verify_binary_binding(raw, **arguments)
        changed = deepcopy(index)
        changed["artifact_identities"].append(deepcopy(changed["artifact_identities"][0]))
        changed["artifact_identities_sha256"] = capture.sha(capture.encoded(changed["artifact_identities"]))
        raw, arguments = self.binding_arguments(changed, executable)
        with self.assertRaisesRegex(capture.CaptureError, "duplicate_artifact_identity"):
            capture.verify_binary_binding(raw, **arguments)

    def test_root_target_selection_and_component_identity_must_be_unique(self):
        from copy import deepcopy
        _, _, _, index, executable = self.bound_index()
        changed = deepcopy(index)
        duplicate = deepcopy(changed["root_binary_observations"][0]); duplicate["unit_id"] = "f"*64
        changed["root_binary_observations"].append(duplicate)
        raw, arguments = self.binding_arguments(changed, executable)
        with self.assertRaisesRegex(capture.CaptureError, "root_binary_selection_not_unique"):
            capture.verify_binary_binding(raw, **arguments)
        changed = deepcopy(index); changed["components"][0]["component_id"] = "cargo:path:foreign#app@0.1.0"
        raw, arguments = self.binding_arguments(changed, executable)
        with self.assertRaisesRegex(capture.CaptureError, "root_binary_component_identity_mismatch"):
            capture.verify_binary_binding(raw, **arguments)

    def test_artifact_identity_rejects_escape_malformed_digest_and_bool_size(self):
        from copy import deepcopy
        _, _, _, index, executable = self.bound_index()
        cases = [("path", "../foreign"), ("path", "/absolute"), ("path", "C:/foreign"),
                 ("path", "folder\\foreign"), ("sha256", "not-a-digest"), ("bytes", True)]
        for key, value in cases:
            changed = deepcopy(index)
            changed["artifact_identities"][0][key] = value
            changed["artifact_identities_sha256"] = capture.sha(capture.encoded(changed["artifact_identities"]))
            raw, arguments = self.binding_arguments(changed, executable)
            with self.subTest(key=key, value=value), self.assertRaises(capture.CaptureError):
                capture.verify_binary_binding(raw, **arguments)

    def test_index_bytes_must_match_the_candidate_receipt_digest(self):
        _, _, _, index, executable = self.bound_index()
        raw, arguments = self.binding_arguments(index, executable)
        with self.assertRaisesRegex(capture.CaptureError, "cargo_index_digest_mismatch"):
            capture.verify_binary_binding(raw+b"\n", **arguments)
        with self.assertRaisesRegex(capture.CaptureError, "cargo_index_digest_mismatch"):
            capture.verify_binary_binding(raw, **{**arguments, "expected_index_sha256": "0"*64})

if __name__=="__main__":
    unittest.main(verbosity=2)
