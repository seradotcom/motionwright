//! Actual local copier, SQLite and filesystem tests. The ELF/Mach-O headers below are
//! inert first-party byte fixtures; no runtime, shell, provider or renderer runs.
use super::*;
use std::{
    ffi::CString,
    fs,
    os::unix::{ffi::OsStrExt, fs::symlink},
    path::PathBuf,
};
const PIN: &str = "c2c40c74e5714a3e680415d0dddcf27743f0f0fa";
fn executable_header() -> Vec<u8> {
    let mut bytes = vec![0_u8; 128];
    #[cfg(target_os = "linux")]
    {
        bytes[..6].copy_from_slice(b"\x7fELF\x02\x01");
        bytes[18] = 0x3e;
    }
    #[cfg(target_os = "macos")]
    {
        bytes[..4].copy_from_slice(&[0xcf, 0xfa, 0xed, 0xfe]);
        bytes[4..8].copy_from_slice(&[0x0c, 0, 0, 1]);
        bytes[12..16].copy_from_slice(&[2, 0, 0, 0]);
    }
    bytes
}
struct Fixture {
    dir: tempfile::TempDir,
    source: PathBuf,
    app: PathBuf,
    service: StudioService,
    profiles: RuntimeProfiles,
    grants: EffectGrantRegistry,
    scope: Scope,
}
impl Fixture {
    fn new() -> Self {
        // Canonicalize only the test-owned temp root (Darwin /var is a symlink).
        // Product paths still traverse retained descriptors without following links.
        let base = std::env::temp_dir().canonicalize().unwrap();
        let dir = tempfile::tempdir_in(base).unwrap();
        let source = dir.path().join("original-package");
        let app = dir.path().join("app");
        fs::create_dir_all(&source).unwrap();
        fs::create_dir_all(&app).unwrap();
        let package = PathBuf::from(
            std::env::var("MOTIONWRIGHT_RUNTIME_TEST_PACKAGE_MC")
                .expect("the original offline package producer fixture is required"),
        );
        copy_fixture(&package, &source);
        for (name, expected) in [
            ("render.mjs", bundle::MC_ENTRY_SHA),
            ("package-lock.json", bundle::MC_LOCK_SHA),
            ("package.json", bundle::MC_PACKAGE_SHA),
        ] {
            assert_eq!(
                files::sha(&fs::read(source.join("runtime").join(name)).unwrap()),
                expected
            );
        }
        let service = StudioService::open(app.join("projects.sqlite3")).unwrap();
        let project = service
            .create_project("Original runtime setup fixture")
            .unwrap();
        let scope = Scope {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
        };
        let profiles = RuntimeProfiles::new(&app);
        Self {
            dir,
            source,
            app,
            service,
            profiles,
            grants: EffectGrantRegistry::default(),
            scope,
        }
    }
    fn plan(&self) -> PlanView {
        self.profiles
            .inspect(&self.service, self.scope, &self.source, PIN)
            .unwrap()
    }
    fn grant(&self, subject: &str) -> Uuid {
        self.grants
            .issue(
                EffectKind::InstallRuntime,
                EffectScope::project(
                    self.scope.project_id,
                    self.scope.generation,
                    self.scope.revision,
                ),
                subject,
            )
            .unwrap()
            .token
    }
    fn request(&self, plan: &PlanView) -> InstallRequest {
        InstallRequest {
            scope: self.scope,
            plan_id: plan.plan_id,
            manifest_sha256: plan.manifest_sha256.clone(),
            effect_grant: self.grant(&plan.effect_subject),
        }
    }
    fn install(&self, plan: &PlanView) -> InstallView {
        self.profiles
            .install(&self.service, &self.grants, self.request(plan), PIN)
            .unwrap()
    }
    fn read(&self, id: Uuid) -> InstallView {
        self.profiles
            .read(
                &self.service,
                ReadRequest {
                    scope: self.scope,
                    install_id: id,
                },
                PIN,
            )
            .unwrap()
    }
    fn rollback_request(&self, view: &InstallView) -> RollbackRequest {
        RollbackRequest {
            scope: self.scope,
            install_id: view.install_id,
            manifest_sha256: view.manifest_sha256.clone(),
            effect_grant: self.grant(&view.rollback_subject),
        }
    }
}
fn copy_fixture(source: &Path, destination: &Path) {
    // Test-only setup copies the already generated inert fixture. The application
    // and its tests never launch a subprocess or execute a packaged file.
    for entry in fs::read_dir(source).unwrap() {
        let entry = entry.unwrap();
        let kind = entry.file_type().unwrap();
        assert!(!kind.is_symlink());
        let target = destination.join(entry.file_name());
        if kind.is_dir() {
            fs::create_dir(&target).unwrap();
            copy_fixture(&entry.path(), &target);
        } else {
            assert!(kind.is_file());
            fs::copy(entry.path(), target).unwrap();
        }
    }
}
#[test]
fn actual_copy_reopens_sqlite_and_rollback_preserves_original_files() {
    let f = Fixture::new();
    assert!(f.profiles.supported());
    let original = f.service.project(f.scope.project_id).unwrap();
    let plan = f.plan();
    assert_eq!(plan.platform, crate::runtime_profile_platform::PLATFORM);
    assert!(plan.required_free_bytes > plan.total_bytes);
    assert!(plan.available_bytes >= plan.required_free_bytes);
    let view = f.install(&plan);
    assert_eq!(view.state, "installed");
    assert!(view.content_verified);
    assert!(!view.execution_performed);
    assert_eq!(fs::read(&view.node_path).unwrap(), executable_header());
    let source_bytes = fs::read(f.source.join(bundle::MANIFEST)).unwrap();
    let service = StudioService::open(f.app.join("projects.sqlite3")).unwrap();
    let reopened = RuntimeProfiles::new(&f.app);
    assert_eq!(
        reopened
            .read(
                &service,
                ReadRequest {
                    scope: f.scope,
                    install_id: view.install_id
                },
                PIN
            )
            .unwrap()
            .state,
        "installed"
    );
    let result = reopened
        .rollback(&service, &f.grants, f.rollback_request(&view), PIN)
        .unwrap();
    assert_eq!(result.state, "rolled_back");
    assert!(!result.rollback_available);
    assert!(!Path::new(&view.destination).exists());
    assert_eq!(
        fs::read(Path::new(&result.disabled_destination).join("tools/node")).unwrap(),
        executable_header()
    );
    assert_eq!(
        fs::read(f.source.join(bundle::MANIFEST)).unwrap(),
        source_bytes
    );
    assert_eq!(service.project(f.scope.project_id).unwrap(), original);
    assert!(
        reopened
            .rollback(&service, &f.grants, f.rollback_request(&view), PIN)
            .is_err()
    );
}
#[test]
fn exact_grant_scope_manifest_and_expiry_reject_before_package_writes() {
    for change in [
        "grant",
        "subject",
        "generation",
        "revision",
        "manifest",
        "expiry",
    ] {
        let f = Fixture::new();
        let plan = f.plan();
        let mut request = f.request(&plan);
        match change {
            "grant" => request.effect_grant = Uuid::new_v4(),
            "subject" => request.effect_grant = f.grant("runtime-profile-install|other"),
            "generation" => request.scope.generation = Uuid::new_v4(),
            "revision" => request.scope.revision += 1,
            "manifest" => request.manifest_sha256 = "a".repeat(64),
            "expiry" => {
                f.profiles
                    .state
                    .lock()
                    .unwrap()
                    .plans
                    .get_mut(&plan.plan_id)
                    .unwrap()
                    .expires = Instant::now()
            }
            _ => unreachable!(),
        }
        assert!(
            f.profiles
                .install(&f.service, &f.grants, request, PIN)
                .is_err(),
            "{change}"
        );
        assert!(!Path::new(&plan.destination).exists(), "{change}");
        assert!(
            f.service
                .production_receipts(f.scope.project_id, 256)
                .unwrap()
                .is_empty()
        );
    }
}
#[test]
fn changed_selected_bytes_leave_a_partial_original_that_can_be_rolled_back() {
    let f = Fixture::new();
    let plan = f.plan();
    fs::write(
        f.source.join("tools/node"),
        b"changed after explicit inspection",
    )
    .unwrap();
    assert!(
        f.profiles
            .install(&f.service, &f.grants, f.request(&plan), PIN)
            .is_err()
    );
    let result = f.read(plan.plan_id);
    assert_eq!(result.state, "partial_copy");
    assert!(!result.content_verified);
    assert!(result.rollback_available);
    let before = f
        .service
        .production_receipt_watermark(f.scope.project_id)
        .unwrap();
    let read = f.read(plan.plan_id);
    assert_eq!(read.state, "partial_copy");
    assert_eq!(
        before,
        f.service
            .production_receipt_watermark(f.scope.project_id)
            .unwrap()
    );
    let rollback = f
        .profiles
        .rollback(&f.service, &f.grants, f.rollback_request(&result), PIN)
        .unwrap();
    assert!(
        Path::new(&rollback.disabled_destination)
            .join(bundle::MANIFEST)
            .is_file()
    );
    assert_eq!(
        fs::read(f.source.join("tools/node")).unwrap(),
        b"changed after explicit inspection"
    );
}
#[test]
fn link_hardlink_fifo_and_source_root_replacement_are_rejected() {
    for kind in ["symlink", "hardlink", "fifo", "root"] {
        let f = Fixture::new();
        let node = f.source.join("tools/node");
        if kind == "root" {
            let plan = f.plan();
            fs::rename(&f.source, f.dir.path().join("retained-source")).unwrap();
            fs::create_dir(&f.source).unwrap();
            assert!(
                f.profiles
                    .install(&f.service, &f.grants, f.request(&plan), PIN)
                    .is_err()
            );
            assert!(!Path::new(&plan.destination).exists());
        } else {
            fs::remove_file(&node).unwrap();
            let outside = f.dir.path().join("owner-original-node");
            fs::write(&outside, executable_header()).unwrap();
            match kind {
                "symlink" => symlink(&outside, &node).unwrap(),
                "hardlink" => fs::hard_link(&outside, &node).unwrap(),
                "fifo" => {
                    let name = CString::new(node.as_os_str().as_bytes()).unwrap();
                    // SAFETY: fixture pathname is live; this creates one inert named pipe.
                    assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
                }
                _ => unreachable!(),
            }
            assert!(
                f.profiles
                    .inspect(&f.service, f.scope, &f.source, PIN)
                    .is_err(),
                "{kind}"
            );
            assert_eq!(fs::read(outside).unwrap(), executable_header());
        }
        assert!(
            f.service
                .production_receipts(f.scope.project_id, 256)
                .unwrap()
                .is_empty()
        );
    }
}
#[test]
fn destination_identity_and_project_scope_cannot_be_substituted() {
    let f = Fixture::new();
    let plan = f.plan();
    let root = f.app.join("runtime-profiles-v1");
    fs::rename(&root, f.app.join("original-profile-root")).unwrap();
    fs::create_dir(&root).unwrap();
    fs::write(
        root.join("foreign-marker"),
        b"leave this foreign directory untouched",
    )
    .unwrap();
    assert!(
        f.profiles
            .install(&f.service, &f.grants, f.request(&plan), PIN)
            .is_err()
    );
    assert_eq!(
        fs::read(root.join("foreign-marker")).unwrap(),
        b"leave this foreign directory untouched"
    );
    assert!(!Path::new(&plan.destination).exists());
    let f = Fixture::new();
    let plan = f.plan();
    let installed = f.install(&plan);
    let held = f.dir.path().join("original-installation");
    fs::rename(&installed.destination, &held).unwrap();
    fs::create_dir(&installed.destination).unwrap();
    assert!(
        f.profiles
            .read(
                &f.service,
                ReadRequest {
                    scope: f.scope,
                    install_id: installed.install_id
                },
                PIN
            )
            .is_err()
    );
    assert!(
        f.profiles
            .rollback(&f.service, &f.grants, f.rollback_request(&installed), PIN)
            .is_err()
    );
    assert_eq!(
        fs::read(held.join("tools/node")).unwrap(),
        executable_header()
    );
    let foreign = Scope {
        generation: Uuid::new_v4(),
        ..f.scope
    };
    assert!(f.profiles.inventory(&f.service, foreign, PIN).is_err());
}
#[test]
fn unknown_original_survives_restart_and_blocks_a_new_profile_identity() {
    let f = Fixture::new();
    let plan = f.plan();
    fs::write(f.source.join("tools/node"), b"interrupted source fixture").unwrap();
    assert!(
        f.profiles
            .install(&f.service, &f.grants, f.request(&plan), PIN)
            .is_err()
    );
    let service = StudioService::open(f.app.join("projects.sqlite3")).unwrap();
    let profiles = RuntimeProfiles::new(&f.app);
    assert_eq!(
        profiles
            .read(
                &service,
                ReadRequest {
                    scope: f.scope,
                    install_id: plan.plan_id
                },
                PIN
            )
            .unwrap()
            .state,
        "partial_copy"
    );
    assert!(profiles.inspect(&service, f.scope, &f.source, PIN).is_err());
    assert_eq!(
        profiles
            .inventory(&service, f.scope, PIN)
            .unwrap()
            .profiles
            .len(),
        1
    );
    assert_eq!(
        service.project(f.scope.project_id).unwrap().revision,
        f.scope.revision
    );
}
#[test]
fn lost_reply_recovers_original_bytes_without_reinstall_or_new_receipt() {
    let f = Fixture::new();
    let plan = f.plan();
    let _lost_reply = f.install(&plan);
    let before = f
        .service
        .production_receipt_watermark(f.scope.project_id)
        .unwrap();
    let service = StudioService::open(f.app.join("projects.sqlite3")).unwrap();
    let profiles = RuntimeProfiles::new(&f.app);
    let inventory = profiles.inventory(&service, f.scope, PIN).unwrap();
    assert_eq!(inventory.profiles[0].state, "copy_completed");
    assert!(!inventory.profiles[0].content_verified);
    let recovered = profiles
        .read(
            &service,
            ReadRequest {
                scope: f.scope,
                install_id: plan.plan_id,
            },
            PIN,
        )
        .unwrap();
    assert!(recovered.content_verified);
    assert_eq!(
        service
            .production_receipt_watermark(f.scope.project_id)
            .unwrap(),
        before
    );
    assert!(
        f.profiles
            .install(&f.service, &f.grants, f.request(&plan), PIN)
            .is_err()
    );
    assert_eq!(
        fs::read_dir(f.app.join("runtime-profiles-v1"))
            .unwrap()
            .count(),
        1
    );
}
#[test]
fn original_rollback_without_ack_is_recovered_without_another_move() {
    let f = Fixture::new();
    let plan = f.plan();
    let installed = f.install(&plan);
    let history = f.profiles.history(&f.service, f.scope, PIN).unwrap();
    let (record, _) = history.get(&installed.install_id).unwrap();
    let request = f.rollback_request(&installed);
    // Reproduce the actual admitted rollback cut point: original intent and
    // no-replace rename committed; the process dies before the final receipt.
    f.grants
        .consume(
            request.effect_grant,
            EffectKind::InstallRuntime,
            EffectScope::project(f.scope.project_id, f.scope.generation, f.scope.revision),
            &installed.rollback_subject,
        )
        .unwrap();
    append(&f.service, record, "rollback_dispatching").unwrap();
    f.profiles
        .root()
        .unwrap()
        .disable(&installed.install_id.to_string(), record.directory.unwrap())
        .unwrap();
    let before = f
        .service
        .production_receipt_watermark(f.scope.project_id)
        .unwrap();
    let reopened = RuntimeProfiles::new(&f.app);
    let result = reopened
        .read(
            &f.service,
            ReadRequest {
                scope: f.scope,
                install_id: installed.install_id,
            },
            PIN,
        )
        .unwrap();
    assert_eq!(result.state, "rolled_back");
    assert!(!result.rollback_available);
    assert!(
        reopened
            .rollback(&f.service, &f.grants, f.rollback_request(&installed), PIN)
            .is_err()
    );
    assert_eq!(
        f.service
            .production_receipt_watermark(f.scope.project_id)
            .unwrap(),
        before
    );
    assert_eq!(
        fs::read(Path::new(&result.disabled_destination).join("tools/node")).unwrap(),
        executable_header()
    );
}
#[test]
fn closed_manifest_rejects_authority_fields_paths_collisions_and_budgets() {
    let f = Fixture::new();
    let bytes = fs::read(f.source.join(bundle::MANIFEST)).unwrap();
    let original: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert!(bundle::parse(&bytes, PIN).is_ok());
    assert!(bundle::parse(&bytes, &"f".repeat(40)).is_err());
    for change in [
        "authority",
        "platform",
        "version",
        "node_version",
        "pin",
        "traversal",
        "absolute",
        "ads",
        "secret",
        "file_budget",
        "total_budget",
        "case_directory",
        "ancestor",
    ] {
        let mut value = original.clone();
        match change {
            "authority" => value["execute_on_install"] = serde_json::json!(true),
            "platform" => value["platform"] = serde_json::json!("unsupported-platform"),
            "version" => value["version"] = serde_json::json!("latest"),
            "node_version" => value["node_version"] = serde_json::json!("24.0.0"),
            "pin" => value["semwright_source_pin"] = serde_json::json!("f".repeat(40)),
            "traversal" => value["files"][0]["path"] = serde_json::json!("runtime/../outside"),
            "absolute" => value["files"][0]["path"] = serde_json::json!("/outside"),
            "ads" => value["files"][0]["path"] = serde_json::json!("runtime/entry:stream"),
            "secret" => value["files"][0]["path"] = serde_json::json!("runtime/.env"),
            "file_budget" => {
                value["files"][0]["bytes"] = serde_json::json!(bundle::MAX_FILE_BYTES + 1)
            }
            "total_budget" => {
                for entry in value["files"].as_array_mut().unwrap() {
                    entry["bytes"] = serde_json::json!(bundle::MAX_FILE_BYTES);
                }
            }
            "case_directory" | "ancestor" => {
                let paths = if change == "case_directory" {
                    ["runtime/Folder/a", "runtime/folder/b"]
                } else {
                    ["runtime/parent", "runtime/parent/child"]
                };
                for path in paths {
                    value["files"].as_array_mut().unwrap().push(serde_json::json!({"path":path,"sha256":"f".repeat(64),"bytes":1,"role":"dependency","executable":false}));
                }
            }
            _ => unreachable!(),
        }
        assert!(
            bundle::parse(&serde_json::to_vec(&value).unwrap(), PIN).is_err(),
            "{change}"
        );
    }
}

#[test]
#[cfg(target_os = "linux")]
fn hyperframes_original_runner_and_lock_install_without_executing_the_fixture_tools() {
    let f = Fixture::new();
    fs::rename(&f.source, f.dir.path().join("unused-motion-canvas-fixture")).unwrap();
    fs::create_dir(&f.source).unwrap();
    let package = PathBuf::from(
        std::env::var("MOTIONWRIGHT_RUNTIME_TEST_PACKAGE_HF")
            .expect("the original HyperFrames offline package producer fixture is required"),
    );
    copy_fixture(&package, &f.source);
    let plan = f.plan();
    assert_eq!(plan.profile, "hyperframes");
    let installed = f.install(&plan);
    assert_eq!(f.read(installed.install_id).state, "installed");
    assert!(!installed.execution_performed);
    assert_eq!(fs::read(installed.node_path).unwrap(), executable_header());
    assert_eq!(
        files::sha(&fs::read(f.source.join("runtime/semwright-hyperframes-runner.mjs")).unwrap()),
        bundle::HF_ENTRY_SHA
    );
    // Even a freshly rehashed outer description cannot legitimize an invented
    // field or a different Node identity in the actual closed runtime lock.
    let original = fs::read(f.source.join("runtime/runtime-lock.json")).unwrap();
    for field in ["execute_on_install", "node_sha256", "node_version"] {
        let mut lock: serde_json::Value = serde_json::from_slice(&original).unwrap();
        lock[field] = serde_json::json!("unsupported");
        let bytes = serde_json::to_vec(&lock).unwrap();
        fs::write(f.source.join("runtime/runtime-lock.json"), &bytes).unwrap();
        let mut manifest: bundle::BundleManifest =
            serde_json::from_slice(&fs::read(f.source.join(bundle::MANIFEST)).unwrap()).unwrap();
        let row = manifest
            .files
            .iter_mut()
            .find(|row| row.role == FileRole::RuntimeLock)
            .unwrap();
        row.bytes = bytes.len() as u64;
        row.sha256 = files::sha(&bytes);
        let raw = serde_json::to_vec(&manifest).unwrap();
        fs::write(f.source.join(bundle::MANIFEST), &raw).unwrap();
        let parsed = bundle::parse(&raw, PIN).unwrap();
        assert!(
            files::inspect(
                &Directory::open(&f.source).unwrap(),
                &parsed,
                &files::sha(&raw)
            )
            .is_err(),
            "{field}"
        );
    }
}

#[test]
fn unavailable_runtime_storage_keeps_original_projects_open_and_unchanged() {
    let f = Fixture::new();
    let original = f.service.project(f.scope.project_id).unwrap();
    let path = f.app.join("runtime-profiles-v1");
    fs::rename(&path, f.app.join("retained-runtime-directory")).unwrap();
    fs::write(&path, b"preserve this original foreign file").unwrap();
    let profiles = RuntimeProfiles::new(&f.app);
    assert!(profiles.inventory(&f.service, f.scope, PIN).is_err());
    assert!(
        profiles
            .inspect(&f.service, f.scope, &f.source, PIN)
            .is_err()
    );
    let reopened = StudioService::open(f.app.join("projects.sqlite3")).unwrap();
    assert_eq!(reopened.project(f.scope.project_id).unwrap(), original);
    assert_eq!(
        fs::read(path).unwrap(),
        b"preserve this original foreign file"
    );
    assert!(
        reopened
            .production_receipts(f.scope.project_id, 256)
            .unwrap()
            .is_empty()
    );
}

#[test]
fn foreign_architecture_or_truncated_executable_is_refused_before_effects() {
    for kind in ["foreign", "truncated"] {
        let f = Fixture::new();
        let mut bytes = executable_header();
        if kind == "truncated" {
            bytes.truncate(16);
        } else {
            #[cfg(target_os = "linux")]
            {
                bytes[18] = 0xb7;
            } // ELF ARM64 does not become x86_64.
            #[cfg(target_os = "macos")]
            {
                bytes[4..8].copy_from_slice(&[7, 0, 0, 1]);
            } // Mach-O Intel.
        }
        fs::write(f.source.join("tools/node"), &bytes).unwrap();
        let path = f.source.join(bundle::MANIFEST);
        let mut manifest: bundle::BundleManifest =
            serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        let node = manifest
            .files
            .iter_mut()
            .find(|row| row.role == FileRole::Node)
            .unwrap();
        node.bytes = bytes.len() as u64;
        node.sha256 = files::sha(&bytes);
        fs::write(path, serde_json::to_vec(&manifest).unwrap()).unwrap();
        assert!(
            f.profiles
                .inspect(&f.service, f.scope, &f.source, PIN)
                .is_err(),
            "{kind}"
        );
        assert!(
            f.service
                .production_receipts(f.scope.project_id, 256)
                .unwrap()
                .is_empty()
        );
        assert_eq!(fs::read(f.source.join("tools/node")).unwrap(), bytes);
    }
}

#[test]
fn rollback_never_replaces_a_foreign_disabled_destination() {
    let f = Fixture::new();
    let plan = f.plan();
    let view = f.install(&plan);
    let foreign = Path::new(&view.disabled_destination);
    fs::create_dir(foreign).unwrap();
    fs::write(
        foreign.join("owner-original"),
        b"preserve foreign rollback target",
    )
    .unwrap();
    let original = fs::read(&view.node_path).unwrap();
    let parent = Directory::open(&f.app.join("runtime-profiles-v1")).unwrap();
    let child = view.install_id.to_string();
    let identity = parent.child(&child).unwrap().identity();
    // Exercise the actual host no-replace syscall, even before the service's
    // original-directory identity check independently refuses this collision.
    assert!(parent.disable(&child, identity).is_err());
    assert!(
        f.profiles
            .rollback(&f.service, &f.grants, f.rollback_request(&view), PIN)
            .is_err()
    );
    assert_eq!(
        fs::read(foreign.join("owner-original")).unwrap(),
        b"preserve foreign rollback target"
    );
    assert_eq!(fs::read(&view.node_path).unwrap(), original);
    assert!(Path::new(&view.destination).is_dir());
}

#[cfg(target_os = "macos")]
fn set_mac_attribute(path: &Path, name: &str) {
    use std::os::fd::AsRawFd;
    let file = fs::File::open(path).unwrap();
    let name = CString::new(name).unwrap();
    let bytes = b"0081;00000000;MotionwrightInertFixture;";
    // SAFETY: only a test-owned retained file is given this inert attribute.
    assert_eq!(
        unsafe {
            libc::fsetxattr(
                file.as_raw_fd(),
                name.as_ptr(),
                bytes.as_ptr().cast(),
                bytes.len(),
                0,
                0,
            )
        },
        0
    );
}
#[cfg(target_os = "macos")]
fn assert_mac_attribute_preserved(path: &Path, name: &str) {
    use std::os::fd::AsRawFd;
    let file = fs::File::open(path).unwrap();
    let name = CString::new(name).unwrap();
    let mut bytes = [0_u8; 128];
    // SAFETY: the destination buffer is live and bounded; only metadata is read.
    let count = unsafe {
        libc::fgetxattr(
            file.as_raw_fd(),
            name.as_ptr(),
            bytes.as_mut_ptr().cast(),
            bytes.len(),
            0,
            0,
        )
    };
    assert!(count > 0);
    assert_eq!(
        &bytes[..count as usize],
        b"0081;00000000;MotionwrightInertFixture;"
    );
}

#[cfg(target_os = "macos")]
#[test]
fn macos_extended_attributes_refuse_inspection_and_survive_unchanged() {
    for name in ["com.apple.quarantine", "org.motionwright.inert-fixture"] {
        for relative in ["", "tools/node", "runtime"] {
            let f = Fixture::new();
            let path = f.source.join(relative);
            let original = fs::read(f.source.join("tools/node")).unwrap();
            set_mac_attribute(&path, name);
            assert!(
                f.profiles
                    .inspect(&f.service, f.scope, &f.source, PIN)
                    .is_err()
            );
            assert_mac_attribute_preserved(&path, name);
            assert_eq!(fs::read(f.source.join("tools/node")).unwrap(), original);
            assert!(
                f.service
                    .production_receipts(f.scope.project_id, 256)
                    .unwrap()
                    .is_empty()
            );
        }
    }
}

#[cfg(target_os = "macos")]
#[test]
fn macos_extended_acl_refuses_source_and_destination_before_effects() {
    use std::os::{fd::AsRawFd, unix::fs::MetadataExt};
    unsafe extern "C" {
        fn acl_from_text(text: *const libc::c_char) -> *mut libc::c_void;
        fn acl_set_fd(fd: libc::c_int, acl: *mut libc::c_void) -> libc::c_int;
        fn acl_free(acl: *mut libc::c_void) -> libc::c_int;
    }
    for relative in ["source-root", "source-node", "destination"] {
        let f = Fixture::new();
        let plan = f.plan();
        let path = match relative {
            "source-root" => f.source.clone(),
            "source-node" => f.source.join("tools/node"),
            _ => f.app.join("runtime-profiles-v1"),
        };
        let file = fs::File::open(&path).unwrap();
        // SAFETY: only this temporary fixture gets an extended write ACE. No
        // subprocess, runtime tool or authority provider executes in this test.
        let acl = unsafe { acl_from_text(c"!#acl 1\ngroup::everyone::allow:write\n".as_ptr()) };
        assert!(!acl.is_null(), "{}", std::io::Error::last_os_error());
        assert_eq!(unsafe { acl_set_fd(file.as_raw_fd(), acl) }, 0);
        assert_eq!(unsafe { acl_free(acl) }, 0);
        // Darwin's POSIX bits alone do not disclose this additional permission.
        assert_eq!(file.metadata().unwrap().mode() & 0o022, 0);
        if relative == "destination" {
            let failure = f
                .profiles
                .install(&f.service, &f.grants, f.request(&plan), PIN)
                .unwrap_err();
            assert_eq!(failure.certainty, "not_started");
            assert!(!Path::new(&plan.destination).exists());
        } else {
            assert!(
                f.profiles
                    .inspect(&f.service, f.scope, &f.source, PIN)
                    .is_err()
            );
        }
        assert!(crate::runtime_profile_platform::verify_security(&file).is_err());
        assert!(
            f.service
                .production_receipts(f.scope.project_id, 256)
                .unwrap()
                .is_empty()
        );
        assert_eq!(
            fs::read(f.source.join("tools/node")).unwrap(),
            executable_header()
        );
    }
}

#[cfg(target_os = "macos")]
#[test]
fn macos_attribute_change_after_plan_retains_one_partial_original_without_replay() {
    let f = Fixture::new();
    let plan = f.plan();
    let node = f.source.join("tools/node");
    set_mac_attribute(&node, "com.apple.quarantine");
    let failure = f
        .profiles
        .install(&f.service, &f.grants, f.request(&plan), PIN)
        .unwrap_err();
    assert_eq!(failure.certainty, "outcome_unknown");
    let original = f.read(plan.plan_id);
    assert_eq!(original.state, "partial_copy");
    let before = f
        .service
        .production_receipts(f.scope.project_id, 256)
        .unwrap();
    assert!(
        f.profiles
            .install(&f.service, &f.grants, f.request(&plan), PIN)
            .is_err()
    );
    assert_eq!(
        f.service
            .production_receipts(f.scope.project_id, 256)
            .unwrap(),
        before
    );
    let rolled = f
        .profiles
        .rollback(&f.service, &f.grants, f.rollback_request(&original), PIN)
        .unwrap();
    assert_eq!(rolled.state, "rolled_back");
    assert!(Path::new(&rolled.disabled_destination).is_dir());
    assert_mac_attribute_preserved(&node, "com.apple.quarantine");
    assert_eq!(fs::read(node).unwrap(), executable_header());
}

#[cfg(target_os = "macos")]
#[test]
fn macos_hyperframes_manifest_is_refused_without_mutating_the_original() {
    let f = Fixture::new();
    let path = f.source.join(bundle::MANIFEST);
    let original = fs::read(&path).unwrap();
    let mut manifest: bundle::BundleManifest = serde_json::from_slice(&original).unwrap();
    manifest.profile = Profile::Hyperframes;
    manifest.version = Profile::Hyperframes.version().into();
    assert!(bundle::parse(&serde_json::to_vec(&manifest).unwrap(), PIN).is_err());
    assert_eq!(fs::read(path).unwrap(), original);
    assert!(
        f.service
            .production_receipts(f.scope.project_id, 256)
            .unwrap()
            .is_empty()
    );
}
