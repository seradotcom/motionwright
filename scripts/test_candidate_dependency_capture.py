import importlib.util, io, json, os, pathlib, subprocess, sys, tempfile, threading, time, unittest
from unittest import mock
from types import SimpleNamespace
MODULE = pathlib.Path(__file__).with_name("candidate_dependency_capture.py")
spec = importlib.util.spec_from_file_location("candidate_dependency_capture", MODULE)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)

class CaptureContracts(unittest.TestCase):
    def setUp(self):
        # A fixture observes its local Git policy, independent of CI's injected
        # checkout configuration. Tests can still inject a rejected policy below.
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                           GIT_TERMINAL_PROMPT="0")
        self.enterContext(mock.patch.dict(os.environ, environment, clear=True))
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
    def test_windows_generated_preflight_refuses_preexisting_or_overlapping_roots(self):
        self.repo()
        manifest = self.root / "apps/desktop/src-tauri/Cargo.toml"
        manifest.parent.mkdir(parents=True)
        manifest.write_bytes(b"[package]\n")
        home = self.root / "cargo"; home.mkdir()
        target = self.root / "target"; target.mkdir()
        arguments = (self.root, home, manifest, "x86_64-pc-windows-msvc",
                     "motionwright-desktop", target, "state", "resources")
        capture.prepare_windows_generated_metadata(*arguments)
        (home / "preexisting").write_bytes(b"preserve")
        with self.assertRaisesRegex(capture.CaptureError, "cargo_home_not_empty"):
            capture.prepare_windows_generated_metadata(*arguments)
        (home / "preexisting").unlink()
        (target / "preexisting").write_bytes(b"preserve")
        with self.assertRaisesRegex(capture.CaptureError, "target_not_empty"):
            capture.prepare_windows_generated_metadata(*arguments)
        (target / "preexisting").unlink()
        generated = manifest.parent / "gen"
        generated.mkdir()
        with self.assertRaisesRegex(capture.CaptureError, "tauri_generated_root_preexisting"):
            capture.prepare_windows_generated_metadata(*arguments)
        generated.rmdir()
        changed = list(arguments)
        changed[-1] = "apps/desktop/src-tauri/resources"
        with self.assertRaisesRegex(capture.CaptureError, "output_inside_source_package"):
            capture.prepare_windows_generated_metadata(*changed)
        self.assertFalse((self.root / "state").exists())

    def test_linux_generated_preflight_is_explicit_and_keeps_fresh_roots(self):
        self.repo()
        manifest = self.root / "apps/desktop/src-tauri/Cargo.toml"
        manifest.parent.mkdir(parents=True)
        manifest.write_bytes(b"[package]\n")
        home = self.root / "cargo"; home.mkdir()
        target = self.root / "target"; target.mkdir()
        arguments = (self.root, home, manifest, "x86_64-unknown-linux-gnu",
                     "motionwright-desktop", target, "state", "resources")
        capture.prepare_tauri_generated_metadata(*arguments)
        with self.assertRaisesRegex(capture.CaptureError, "windows_generated_metadata_scope_invalid"):
            capture.prepare_windows_generated_metadata(*arguments)
        unsupported = list(arguments); unsupported[3] = "aarch64-unknown-linux-gnu"
        with self.assertRaisesRegex(capture.CaptureError, "tauri_generated_metadata_scope_invalid"):
            capture.prepare_tauri_generated_metadata(*unsupported)
        (home / "preexisting").write_bytes(b"preserve")
        with self.assertRaisesRegex(capture.CaptureError, "linux_capture_cargo_home_not_empty"):
            capture.prepare_tauri_generated_metadata(*arguments)
        self.assertFalse((self.root / "state").exists())

    def test_linux_observer_requires_exact_copy_roster_and_postread_bytes(self):
        self.repo()
        sources = capture._load_sibling("cargo_license_sources")
        for name in sources.LINUX_TAURI_SCHEMAS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"{}")
        triple = "x86_64-unknown-linux-gnu"
        observed = capture.observe_tauri_schemas(self.root, triple)
        self.assertEqual(set(observed), set(sources.LINUX_TAURI_SCHEMAS))
        with self.assertRaisesRegex(capture.CaptureError, "tauri_generated_schema_roster_invalid"):
            capture.observe_windows_tauri_schemas(self.root)
        desktop = self.root / "apps/desktop/src-tauri/gen/schemas/desktop-schema.json"
        linux = self.root / "apps/desktop/src-tauri/gen/schemas/linux-schema.json"
        desktop.write_bytes(b'{"changed":true}')
        with self.assertRaisesRegex(capture.CaptureError, "generated_tauri_schema_copy_mismatch"):
            capture.observe_tauri_schemas(self.root, triple)
        linux.write_bytes(desktop.read_bytes())
        home = self.root / "cargo"; home.mkdir()
        with self.assertRaisesRegex(capture.CaptureError, "generated_metadata_changed_after_notices"):
            capture.revalidate_tauri_generated_metadata(
                self.root, {"packages": []}, home, {}, observed, target=triple)
        (desktop.parent / "unreviewed-directory").mkdir()
        with self.assertRaisesRegex(capture.CaptureError, "tauri_generated_schema_roster_invalid"):
            capture.observe_tauri_schemas(self.root, triple)

    def test_generated_revalidation_uses_real_checkout_and_preserves_original_bytes(self):
        import test_cargo_license_sources as sources
        base = self.root / "fixture"; base.mkdir()
        fixture = sources.Fixture(base)
        component = fixture.git_package()
        metadata = {"packages": [component]}
        marker = pathlib.Path(component["manifest_path"]).parent / ".cargo-ok"
        marker.write_bytes(b"inert marker\n")
        before = capture.observe_cargo_git_markers(metadata, fixture.cargo)
        self.assertEqual(len(before), 1)
        self.assertEqual(next(iter(before.values()))["git_head"], component["source"].rsplit("#", 1)[-1])
        for name in sources.collector.WINDOWS_TAURI_SCHEMAS:
            path = fixture.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"{}")
        schemas = capture.observe_windows_tauri_schemas(fixture.workspace)
        self.assertEqual(len(schemas), 4)
        capture.revalidate_windows_generated_metadata(fixture.workspace, metadata, fixture.cargo, before, schemas)
        state = self.root / "retained"; state.mkdir()
        profile = {"tauri_schemas": schemas, "cargo_git_markers": before}
        retained = capture.retain_generated_originals(fixture.workspace, fixture.cargo, state, profile)
        self.assertEqual(len(retained), 5)
        for row in retained.values():
            self.assertEqual(capture.stable_hash(state, row["path"]),
                             {"bytes": row["bytes"], "sha256": row["sha256"]})
        with self.assertRaises(FileExistsError):
            capture.retain_generated_originals(fixture.workspace, fixture.cargo, state, profile)
        marker.write_bytes(b"changed marker\n")
        with self.assertRaisesRegex(capture.CaptureError, "cargo_generated_marker_changed"):
            capture.revalidate_windows_generated_metadata(fixture.workspace, metadata, fixture.cargo, before, schemas)
        marker.write_bytes(b"inert marker\n")
        first = fixture.workspace / sources.collector.WINDOWS_TAURI_SCHEMAS[0]
        first.write_bytes(b'{"changed":true}')
        with self.assertRaisesRegex(capture.CaptureError, "generated_metadata_changed_after_notices"):
            capture.revalidate_windows_generated_metadata(fixture.workspace, metadata, fixture.cargo, before, schemas)
        first.write_bytes(b'{"duplicate":1,"duplicate":2}')
        with self.assertRaisesRegex(capture.CaptureError, "generated_metadata_json_invalid"):
            capture.observe_windows_tauri_schemas(fixture.workspace)
        first.write_bytes(b"{}")
        (first.parent / "extra-empty-directory").mkdir()
        with self.assertRaisesRegex(capture.CaptureError, "tauri_generated_schema_roster_invalid"):
            capture.observe_windows_tauri_schemas(fixture.workspace)
        foreign = {"packages": [{**component, "manifest_path": str(fixture.workspace / "Cargo.toml")}]}
        with self.assertRaisesRegex(capture.CaptureError, "path_outside_anchor"):
            capture.observe_cargo_git_markers(foreign, fixture.cargo)

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
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1",
                             "GIT_CONFIG_KEY_0": "core.autocrlf", "GIT_CONFIG_VALUE_0": "true"}):
            with self.assertRaisesRegex(capture.CaptureError, "tracked_bytes_differ") as refused:
                capture.source_snapshot(self.root, head)
        self.assertEqual(refused.exception.__cause__.code, "git_crlf_context_unverified")
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



class TauriPermissionCaptureContracts(unittest.TestCase):
    """Real filesystem/Git contracts only; no Cargo or build process is run."""
    setUp = CaptureContracts.setUp
    tearDown = CaptureContracts.tearDown
    git = CaptureContracts.git

    def commit(self, *extra):
        paths = ["Cargo.lock", capture.COMMAND_PERMISSION_SOURCE,
                 "apps/desktop/src-tauri/Cargo.toml", *extra]
        self.git("-c", "core.autocrlf=false", "add", "--", *paths)
        self.git("-c", "user.name=Permission fixture", "-c",
                 "user.email=fixture@invalid.local", "commit", "-qm", "Exact permission fixture")
        return self.git("rev-parse", "HEAD").decode().strip()

    def generator_row(self, name, version, checksum, source=None):
        registry = "registry+https://github.com/rust-lang/crates.io-index"
        return (f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
                f'source = "{registry if source is None else source}"\n'
                f'checksum = "{checksum}"\n\n').encode()

    def fixture(self):
        self.permissions = capture._load_sibling("tauri_generated_permissions")
        self.git("init", "-q")
        self.source_path = self.root / capture.COMMAND_PERMISSION_SOURCE
        self.source_path.parent.mkdir(parents=True)
        self.source_bytes = (
            b"fn main() {\n"
            b"let attributes = tauri_build::Attributes::new().app_manifest("
            b'tauri_build::AppManifest::new().commands(&["bootstrap", "read_mix",]));\n'
            b'tauri_build::try_build(attributes).expect("fixture command ACL");\n}\n'
        )
        self.source_path.write_bytes(self.source_bytes)
        (self.source_path.parent / "Cargo.toml").write_bytes(
            b'[package]\nname = "motionwright-desktop"\nversion = "0.1.0"\nlicense = "MIT"\n')
        self.lock_bytes = (
            b"version = 3\n\n"
            + b"".join(self.generator_row(*row) for row in self.permissions.GENERATOR_PACKAGES)
            + b'[[package]]\nname = "motionwright-desktop"\nversion = "0.1.0"\n'
        )
        (self.root / "Cargo.lock").write_bytes(self.lock_bytes)
        self.head = self.commit()
        return capture.prepare_windows_command_permissions(self.root, self.head)

    def emit(self, expectation):
        for name, raw in expectation["expected_files"].items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

    def observe(self, expectation):
        return capture.observe_windows_command_permissions(self.root, self.head, expectation)

    def test_opt_in_requires_schemas_before_any_source_access_or_effect(self):
        kwargs = dict(
            repo=self.root / "missing-repository", expected_source="0" * 40,
            cargo="not-a-cargo-command", cargo_home=self.root / "missing-home",
            target_triple="x86_64-pc-windows-msvc", root_package_name="motionwright-desktop",
            manifest_path="apps/desktop/src-tauri/Cargo.toml",
            state_directory=self.root / "state", resources_directory=self.root / "resources",
            build_command=["not-a-build"],
        )
        for permission_flag, schema_flag, code in (
                (True, False, "requires_generated_schemas"),
                (True, 1, "requires_generated_schemas"),
                (1, True, "flag_invalid"),
                ("yes", True, "flag_invalid")):
            with self.subTest(permission_flag=permission_flag, schema_flag=schema_flag):
                with self.assertRaisesRegex(capture.CaptureError, "tauri_command_permissions_" + code):
                    capture.capture_build(
                        **kwargs, tauri_generated_schemas=schema_flag,
                        tauri_generated_command_permissions=permission_flag)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_requires_explicit_schemas_without_running_cargo(self):
        import contextlib
        output = io.StringIO()
        args = [
            "--repo", str(self.root / "missing"), "--expected-source", "0" * 40,
            "--cargo-home", str(self.root / "home"), "--manifest", "missing/Cargo.toml",
            "--state-directory", str(self.root / "state"),
            "--resources-directory", str(self.root / "resources"),
            "--target", "x86_64-pc-windows-msvc", "--root-package", "motionwright-desktop",
            "--tauri-generated-command-permissions", "--", "not-a-build",
        ]
        with contextlib.redirect_stderr(output):
            code = capture.main(args)
        self.assertEqual(code, 2)
        self.assertIn("tauri_command_permissions_requires_generated_schemas", output.getvalue())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_real_preflight_binds_source_blob_lock_and_exact_expected_files(self):
        expectation = self.fixture()
        self.assertEqual(set(expectation), {"command_source", "expected_files", "lock_identity"})
        descriptor = expectation["command_source"]
        self.assertEqual(descriptor, {
            "path": capture.COMMAND_PERMISSION_SOURCE,
            "git_blob": self.git("rev-parse", "HEAD:" + capture.COMMAND_PERMISSION_SOURCE).decode().strip(),
            "bytes": len(self.source_bytes), "sha256": capture.sha(self.source_bytes),
        })
        self.assertEqual(expectation["lock_identity"], {
            "path": "Cargo.lock", "git_blob": self.git("rev-parse", "HEAD:Cargo.lock").decode().strip(),
            "bytes": len(self.lock_bytes), "sha256": capture.sha(self.lock_bytes),
        })
        self.assertEqual(expectation["expected_files"],
                         self.permissions.derive_command_permissions(self.source_bytes))
        self.assertEqual(capture.prepare_windows_command_permissions(self.root, self.head), expectation)
        self.assertFalse((self.root / self.permissions.GENERATED_ROOT).exists())

    def test_preflight_refuses_existing_namespace_or_non_directory_parent(self):
        self.fixture()
        namespace = self.root / self.permissions.GENERATED_ROOT
        namespace.mkdir(parents=True)
        with self.assertRaisesRegex(capture.CaptureError, "root_preexisting"):
            capture.prepare_windows_command_permissions(self.root, self.head)
        namespace.rmdir()
        namespace.parent.rmdir()
        namespace.parent.write_bytes(b"ordinary parent file; never followed")
        with self.assertRaisesRegex(capture.CaptureError, "unexpected_file_type"):
            capture.prepare_windows_command_permissions(self.root, self.head)
        self.assertEqual(namespace.parent.read_bytes(), b"ordinary parent file; never followed")

    def test_source_head_dirty_bytes_and_index_are_independently_rejected(self):
        self.fixture()
        with self.assertRaisesRegex(capture.CaptureError, "source_head_mismatch"):
            capture.prepare_windows_command_permissions(self.root, "0" * 40)
        changed = self.source_bytes.replace(b'"bootstrap"', b'"bootstraq"')
        self.source_path.write_bytes(changed)
        with self.assertRaisesRegex(capture.CaptureError, "git_worktree_bytes_mismatch"):
            capture.prepare_windows_command_permissions(self.root, self.head)
        self.git("-c", "core.autocrlf=false", "add", "--", capture.COMMAND_PERMISSION_SOURCE)
        with self.assertRaisesRegex(capture.CaptureError, "git_index_not_clean"):
            capture.prepare_windows_command_permissions(self.root, self.head)
        self.assertFalse((self.root / self.permissions.GENERATED_ROOT).exists())

    def test_generator_lock_rejects_missing_wrong_or_ambiguous_exact_packages(self):
        self.fixture()
        name, version, checksum = self.permissions.GENERATOR_PACKAGES[0]
        row = self.generator_row(name, version, checksum)
        variants = (
            self.lock_bytes.replace(row, b""),
            self.lock_bytes.replace(row, self.generator_row(name, "2.7.0", checksum)),
            self.lock_bytes.replace(row, self.generator_row(name, version, "0" * 64)),
            self.lock_bytes.replace(row, self.generator_row(name, version, checksum,
                                                            "registry+https://example.invalid/index")),
            self.lock_bytes + row,
            self.lock_bytes + self.generator_row(name, "2.6.0", "0" * 64),
        )
        for number, raw in enumerate(variants):
            with self.subTest(number=number):
                (self.root / "Cargo.lock").write_bytes(raw)
                head = self.commit()
                with self.assertRaisesRegex(capture.CaptureError, "generator_lock_mismatch:tauri-build"):
                    capture.prepare_windows_command_permissions(self.root, head)
                self.assertFalse((self.root / self.permissions.GENERATED_ROOT).exists())

    def test_legal_crlf_proof_does_not_transfer_a_previous_byte_identity(self):
        expectation = self.fixture()
        self.git("config", "core.autocrlf", "true")
        crlf = self.source_bytes.replace(b"\n", b"\r\n")
        self.source_path.write_bytes(crlf)
        changed = capture.prepare_windows_command_permissions(self.root, self.head)
        self.assertEqual(changed["command_source"]["git_blob"], expectation["command_source"]["git_blob"])
        self.assertEqual(changed["command_source"]["sha256"], capture.sha(crlf))
        self.assertNotEqual(changed["command_source"], expectation["command_source"])
        self.emit(changed)
        with self.assertRaisesRegex(capture.CaptureError, "source_identity_changed"):
            self.observe(expectation)
        self.assertEqual(self.observe(changed)["command_source"], changed["command_source"])

    def test_postbuild_observation_has_only_source_descriptor_and_actual_file_digests(self):
        expectation = self.fixture()
        self.emit(expectation)
        observed = self.observe(expectation)
        self.assertEqual(set(observed), {"command_source", "files"})
        self.assertEqual(observed["command_source"], expectation["command_source"])
        self.assertEqual(observed["files"], {
            name: {"bytes": len(raw), "sha256": capture.sha(raw)}
            for name, raw in expectation["expected_files"].items()
        })
        for descriptor in observed["files"].values():
            self.assertEqual(set(descriptor), {"bytes", "sha256"})

    def test_namespace_missing_extra_directory_and_case_alias_are_rejected(self):
        expectation = self.fixture()
        self.emit(expectation)
        self.observe(expectation)
        names = sorted(expectation["expected_files"])
        target = self.root / names[0]
        original = target.read_bytes()
        target.unlink()
        with self.assertRaisesRegex(capture.CaptureError, "roster_invalid"):
            self.observe(expectation)
        target.write_bytes(original)
        extra = target.parent / "unexpected.toml"
        extra.write_bytes(b"not admitted")
        with self.assertRaisesRegex(capture.CaptureError, "roster_invalid"):
            self.observe(expectation)
        extra.unlink()
        target.unlink(); target.mkdir()
        with self.assertRaisesRegex(capture.CaptureError, "nonregular"):
            self.observe(expectation)
        target.rmdir(); target.write_bytes(original)
        temporary = target.with_suffix(".rename")
        alias = target.with_name(target.name.upper())
        target.rename(temporary); temporary.rename(alias)
        with self.assertRaisesRegex(capture.CaptureError, "roster_invalid"):
            self.observe(expectation)

    def test_permission_bytes_and_bounded_reads_are_not_descriptor_only(self):
        expectation = self.fixture()
        self.emit(expectation)
        target = self.root / sorted(expectation["expected_files"])[0]
        original = target.read_bytes()
        target.write_bytes(b"!" + original[1:])
        with self.assertRaisesRegex(capture.CaptureError, "bytes_mismatch"):
            self.observe(expectation)
        target.write_bytes(b"x" * (self.permissions.MAX_PERMISSION_BYTES + 1))
        with self.assertRaisesRegex(capture.CaptureError, "file_budget_exceeded"):
            self.observe(expectation)

    def test_tracked_overlap_and_staged_overlap_are_rejected_before_capture(self):
        expectation = self.fixture()
        self.emit(expectation)
        name = sorted(expectation["expected_files"])[0]
        self.git("-c", "core.autocrlf=false", "add", "--", name)
        with self.assertRaisesRegex(capture.CaptureError, "git_index_not_clean"):
            capture.authenticate_windows_command_source(self.root, self.head)
        self.head = self.commit(name)
        for file in (self.root / self.permissions.GENERATED_ROOT).iterdir():
            file.unlink()
        (self.root / self.permissions.GENERATED_ROOT).rmdir()
        with self.assertRaisesRegex(capture.CaptureError, "permission_tracked"):
            capture.prepare_windows_command_permissions(self.root, self.head)

    def test_retention_preserves_actual_source_and_permissions_once(self):
        expectation = self.fixture()
        self.emit(expectation)
        observed = self.observe(expectation)
        state = self.root / "state"; state.mkdir()
        originals = capture.retain_command_permission_originals(
            self.root, state, expectation, observed, expected_source=self.head)
        self.assertEqual(len(originals), len(expectation["expected_files"]) + 1)
        source_record = originals["workspace:" + capture.COMMAND_PERMISSION_SOURCE]
        self.assertEqual(source_record["path"], "generated-metadata/tauri-command-source/build.rs")
        self.assertEqual((state / source_record["path"]).read_bytes(), self.source_bytes)
        for name, raw in expectation["expected_files"].items():
            row = originals["workspace:" + name]
            self.assertEqual(row["path"],
                             "generated-metadata/tauri-command-permissions/" + pathlib.Path(name).name)
            self.assertEqual((state / row["path"]).read_bytes(), raw)
            self.assertEqual(capture.stable_hash(state, row["path"]),
                             {"bytes": row["bytes"], "sha256": row["sha256"]})
        with self.assertRaises(FileExistsError):
            capture.retain_command_permission_originals(
                self.root, state, expectation, observed, expected_source=self.head)
        self.assertEqual((state / source_record["path"]).read_bytes(), self.source_bytes)
        self.assertFalse((state / "capture-complete.json").exists())

    def test_forged_observation_is_refused_before_originals_are_created(self):
        import copy
        expectation = self.fixture()
        self.emit(expectation)
        observed = self.observe(expectation)
        state = self.root / "state"; state.mkdir()
        changes = []
        altered = copy.deepcopy(observed)
        altered["command_source"]["sha256"] = "0" * 64
        changes.append(altered)
        altered = copy.deepcopy(observed)
        altered["files"][sorted(altered["files"])[0]]["bytes"] += 1
        changes.append(altered)
        for changed in changes:
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(capture.CaptureError, "observation_changed"):
                    capture.retain_command_permission_originals(
                        self.root, state, expectation, changed, expected_source=self.head)
                self.assertEqual(list(state.iterdir()), [])

    def test_revalidation_refusal_does_not_rewrite_retained_originals(self):
        expectation = self.fixture()
        self.emit(expectation)
        observed = self.observe(expectation)
        state = self.root / "state"; state.mkdir()
        originals = capture.retain_command_permission_originals(
            self.root, state, expectation, observed, expected_source=self.head)
        name = sorted(expectation["expected_files"])[0]
        raw = expectation["expected_files"][name]
        (self.root / name).write_bytes(b"!" + raw[1:])
        with self.assertRaisesRegex(capture.CaptureError, "bytes_mismatch"):
            self.observe(expectation)
        self.assertEqual((state / originals["workspace:" + name]["path"]).read_bytes(), raw)
        self.assertFalse((state / "capture-complete.json").exists())

    def test_retained_inputs_survive_real_notice_collection_refusal(self):
        self.fixture()
        refused = self.root / "crates/refused/Cargo.toml"
        refused.parent.mkdir(parents=True)
        refused.write_bytes(b'[package]\nname = "refused"\nversion = "0.1.0"\nlicense = "MIT"\n')
        raw_lock = self.lock_bytes + b'\n[[package]]\nname = "refused"\nversion = "0.1.0"\n'
        (self.root / "Cargo.lock").write_bytes(raw_lock)
        self.head = self.commit("crates/refused/Cargo.toml")
        expectation = capture.prepare_windows_command_permissions(self.root, self.head)
        self.emit(expectation)
        observed = self.observe(expectation)
        state = self.root / "state"; state.mkdir()
        originals = capture.retain_command_permission_originals(
            self.root, state, expectation, observed, expected_source=self.head)
        cargo_home = self.root / "cargo-home"; cargo_home.mkdir()
        sources = capture._load_sibling("cargo_license_sources")
        component = {
            "id": refused.parent.as_uri() + "#refused@0.1.0",
            "name": "refused", "version": "0.1.0", "source": None,
            "manifest_path": str(refused), "roles": ["runtime", "target"],
            "license": "MIT", "license_file": None,
        }
        notices = sources.collect_cargo_licenses(
            [component], workspace_root=self.root, cargo_home=cargo_home, lock_bytes=raw_lock)
        self.assertEqual([row["code"] for row in notices["components"][0]["issues"]],
                         ["license_text_missing"])
        self.assertTrue(notices["issues"])
        for row in originals.values():
            self.assertEqual(capture.stable_hash(state, row["path"]),
                             {"bytes": row["bytes"], "sha256": row["sha256"]})
        self.assertFalse((state / "capture-complete.json").exists())


if __name__=="__main__":
    unittest.main(verbosity=2)
