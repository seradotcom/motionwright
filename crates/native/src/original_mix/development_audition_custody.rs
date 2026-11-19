//! Optional custody for one genuine Tauri development audition.
//!
//! This creates no owner, permit, effect grant, R74 decision or synthetic IPC.
//! The ordinary build has no compile-time source marker and cannot enable it.
//! Startup alone admits paths; the WebView cannot select files or a namespace.
use super::PreparedOriginalMix;
use motionwright_domain::Project;
use motionwright_service::StudioService;
use std::path::Path;
use uuid::Uuid;

type Result<T> = std::result::Result<T, String>;
const ENV_MANIFEST: &str = "MOTIONWRIGHT_DEVELOPMENT_AUDITION_MANIFEST";

#[derive(Clone, Default)]
pub struct DevelopmentAuditionCustody {
    #[cfg(target_os = "linux")]
    inner: Option<std::sync::Arc<std::sync::Mutex<linux::State>>>,
}

pub struct DevelopmentAuditionTicket {
    #[cfg(target_os = "linux")]
    inner: std::sync::Arc<std::sync::Mutex<linux::State>>,
}

impl DevelopmentAuditionCustody {
    /// An absent runtime manifest is inert, even in a diagnostic build.
    pub fn from_startup(
        compiled_source: Option<&str>,
        sdk_sha: &str,
        data_dir: &Path,
        workspace_session: Uuid,
        service: &StudioService,
    ) -> Result<Self> {
        let Some(manifest) = std::env::var_os(ENV_MANIFEST) else {
            return Ok(Self::default());
        };
        let source = compiled_source
            .ok_or("This desktop build does not admit development audition custody.")?;
        #[cfg(target_os = "linux")]
        {
            linux::admit(
                source,
                sdk_sha,
                data_dir,
                workspace_session,
                service,
                Path::new(&manifest),
            )
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (
                source,
                sdk_sha,
                data_dir,
                workspace_session,
                service,
                manifest,
            );
            Err("Development audition custody requires its admitted Linux CI namespace.".into())
        }
    }

    /// Called only inside the existing OwnerInvocation, after saved-request validation.
    /// The durable intent consumes the one slot before any PCM preparation.
    pub fn reserve(
        &self,
        service: &StudioService,
        project: &Project,
        profile_id: Uuid,
        workspace_session: Uuid,
    ) -> Result<Option<DevelopmentAuditionTicket>> {
        #[cfg(target_os = "linux")]
        if let Some(inner) = &self.inner {
            let mut state = inner.lock().map_err(|_| linux::fault())?;
            state.check(service, project, profile_id, workspace_session)?;
            linux::need(
                state.stage == linux::Stage::Armed,
                "Development audition slot was already consumed.",
            )?;
            state.stage = linux::Stage::Attempted;
            let receipt = state.identity("motionwright.development-audition-intent/1");
            state.output.write_json("intent.original.json", &receipt)?;
            return Ok(Some(DevelopmentAuditionTicket {
                inner: inner.clone(),
            }));
        }
        let _ = (service, project, profile_id, workspace_session);
        Ok(None)
    }

    /// Called on actual application Exit, after DesktopOwner has retired.
    /// A failed/abandoned audition cannot acquire a successful writer handoff.
    pub fn prepare_exit_handoff(&self, service: &StudioService) -> Result<()> {
        #[cfg(target_os = "linux")]
        if let Some(inner) = &self.inner {
            let mut state = inner.lock().map_err(|_| linux::fault())?;
            linux::need(
                state.stage == linux::Stage::Prepared,
                "No original audition response was prepared before exit.",
            )?;
            let project = service
                .project(state.admission.project_id)
                .map_err(|_| linux::fault())?;
            state.check(
                service,
                &project,
                state.admission.deliverable_profile_id,
                state.workspace_session,
            )?;
            state.stage = linux::Stage::HandoffAttempted;
            state.output.write_json(
                "writer-handoff-intent.original.json",
                &state.identity("motionwright.development-audition-writer-handoff-intent/1"),
            )?;
            service
                .prepare_read_only_handoff()
                .map_err(|_| linux::fault())?;
            state.check(
                service,
                &project,
                state.admission.deliverable_profile_id,
                state.workspace_session,
            )?;
            let mut receipt = state.identity("motionwright.development-audition-writer-handoff/1");
            receipt["writer_handoff_prepared"] = true.into();
            receipt["writer_process_closed"] = false.into();
            receipt["closure_requires_independent_process_observation"] = true.into();
            state
                .output
                .write_json("writer-handoff.original.json", &receipt)?;
            state.stage = linux::Stage::HandedOff;
        }
        let _ = service;
        Ok(())
    }
}

impl DevelopmentAuditionTicket {
    /// Retain these exact native result bytes after the command's fresh owner
    /// phase and current-revision check. This is not an IPC delivery receipt.
    pub fn publish(
        self,
        service: &StudioService,
        project: &Project,
        mix: &PreparedOriginalMix,
        workspace_session: Uuid,
    ) -> Result<()> {
        #[cfg(target_os = "linux")]
        {
            let mut state = self.inner.lock().map_err(|_| linux::fault())?;
            linux::need(
                state.stage == linux::Stage::Attempted,
                "Development audition was already published or abandoned.",
            )?;
            state.check(
                service,
                project,
                mix.plan().deliverable_profile_id,
                workspace_session,
            )?;
            mix.verify_project(project, state.admission.deliverable_profile_id)
                .map_err(|_| linux::fault())?;
            linux::need(
                mix.wav_bytes().len() <= 3 * 1024 * 1024
                    && mix.plan().sample_frames == 96_000
                    && mix.plan().sample_rate == 48_000
                    && mix.plan().channels == 2
                    && !mix.plan().owner_mixing_approval_authenticated
                    && !mix.plan().is_mastered
                    && !mix.evidence().owner_release_approved
                    && !mix.evidence().listening_intelligibility_approved
                    && !mix.evidence().independently_verified_lufs
                    && !mix.evidence().independently_verified_true_peak,
                "Development audition exceeded its exact fixture or authority bounds.",
            )?;
            let wav =
                state
                    .output
                    .write("audition.original.wav", mix.wav_bytes(), 3 * 1024 * 1024)?;
            linux::need(
                wav["sha256"].as_str() == Some(mix.wav_sha256()),
                "Original audition WAV digest differs.",
            )?;
            let mut receipt =
                state.identity("motionwright.development-audition-prepared-response/1");
            receipt["wav"] = wav;
            receipt["plan"] = serde_json::to_value(mix.plan()).map_err(|_| linux::fault())?;
            receipt["evidence"] =
                serde_json::to_value(mix.evidence()).map_err(|_| linux::fault())?;
            receipt["audio_inputs_sha256"] = mix.audio_inputs_sha256().into();
            receipt["saved_recipe"] =
                serde_json::to_value(&state.recipe).map_err(|_| linux::fault())?;
            receipt["prepared_for_original_tauri_response"] = true.into();
            receipt["ipc_delivery_observed"] = false.into();
            receipt["ui_observation_required"] = true.into();
            state
                .output
                .write_json("prepared-response.original.json", &receipt)?;
            state.check(
                service,
                project,
                state.admission.deliverable_profile_id,
                workspace_session,
            )?;
            state.stage = linux::Stage::Prepared;
        }
        #[cfg(not(target_os = "linux"))]
        let _ = (self, service, project, mix, workspace_session);
        Ok(())
    }
}

#[cfg(target_os = "linux")]
mod linux {
    use super::*;
    use motionwright_domain::{Change, DeliveryRecipe};
    use serde::{Deserialize, Serialize};
    use serde_json::{Value, json};
    use sha2::{Digest, Sha256};
    use std::{
        ffi::CString,
        fs::{self, File, Metadata, OpenOptions},
        io::{Read, Write},
        os::{
            fd::{AsRawFd, FromRawFd},
            unix::{
                ffi::OsStrExt,
                fs::{MetadataExt, OpenOptionsExt},
            },
        },
        path::{Component, PathBuf},
        sync::{Arc, Mutex},
    };

    const MAX_JSON: usize = 512 * 1024;

    pub(super) fn fault() -> String {
        "Development audition custody failed; preserve this attempt without retry.".into()
    }

    pub(super) fn need(ok: bool, message: &str) -> Result<()> {
        if ok { Ok(()) } else { Err(message.into()) }
    }

    fn io<T>(value: std::io::Result<T>) -> Result<T> {
        value.map_err(|_| fault())
    }

    fn sha(bytes: &[u8]) -> String {
        hex::encode(Sha256::digest(bytes))
    }
    fn hex(value: &str, count: usize) -> bool {
        value.len() == count
            && value
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    }

    fn env(name: &str) -> Result<String> {
        std::env::var(name).map_err(|_| fault())
    }

    #[derive(Debug, Deserialize)]
    #[serde(deny_unknown_fields)]
    pub(super) struct Admission {
        schema: String,
        source_sha: String,
        sdk_sha: String,
        run_id: String,
        run_attempt: String,
        private_root: PathBuf,
        before_receipt_sha256: String,
        pub(super) project_id: Uuid,
        generation: Uuid,
        revision_before: u64,
        pub(super) deliverable_profile_id: Uuid,
    }

    #[derive(Clone, Debug, PartialEq, Eq, Serialize)]
    struct Node {
        device: u64,
        inode: u64,
        mode: u32,
        uid: u32,
        gid: u32,
    }
    fn node(metadata: &Metadata) -> Node {
        Node {
            device: metadata.dev(),
            inode: metadata.ino(),
            mode: metadata.mode(),
            uid: metadata.uid(),
            gid: metadata.gid(),
        }
    }

    #[derive(Clone, Debug, PartialEq, Eq)]
    struct Leaf {
        node: Node,
        links: u64,
        bytes: u64,
        mtime: i64,
        mtime_ns: i64,
        ctime: i64,
        ctime_ns: i64,
    }
    fn leaf(metadata: &Metadata) -> Result<Leaf> {
        // SAFETY: geteuid is a read-only, infallible process identity query.
        let uid = unsafe { libc::geteuid() };
        need(
            metadata.is_file()
                && metadata.nlink() == 1
                && metadata.uid() == uid
                && metadata.mode() & 0o077 == 0,
            "Custody input must be a private single-link regular file.",
        )?;
        Ok(Leaf {
            node: node(metadata),
            links: metadata.nlink(),
            bytes: metadata.len(),
            mtime: metadata.mtime(),
            mtime_ns: metadata.mtime_nsec(),
            ctime: metadata.ctime(),
            ctime_ns: metadata.ctime_nsec(),
        })
    }

    /// Retain every traversed directory; no symlink component is followed.
    /// Directory content timestamps are not identity; legitimate child writes
    /// must not masquerade as a namespace replacement.
    pub(super) struct Directory {
        chain: Vec<(PathBuf, File, Node)>,
    }
    impl Directory {
        fn open(path: &Path, private_root: &Path) -> Result<Self> {
            need(
                path.is_absolute()
                    && path.starts_with(private_root)
                    && path.components().count() <= 32
                    && path
                        .components()
                        .all(|c| matches!(c, Component::RootDir | Component::Normal(_))),
                "Development custody path is not a bounded absolute descendant.",
            )?;
            let mut current = PathBuf::from("/");
            let root = io(OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
                .open(&current))?;
            let root_node = node(&io(root.metadata())?);
            let mut chain = vec![(current.clone(), root, root_node)];
            for component in path.components().skip(1) {
                current.push(component.as_os_str());
                let name = CString::new(component.as_os_str().as_bytes()).map_err(|_| fault())?;
                let parent = &chain.last().ok_or_else(fault)?.1;
                // SAFETY: the live retained parent FD and NUL-free name are valid.
                let fd = unsafe {
                    libc::openat(
                        parent.as_raw_fd(),
                        name.as_ptr(),
                        libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                    )
                };
                need(fd >= 0, "Cannot retain development custody namespace.")?;
                // SAFETY: successful openat returns a newly owned descriptor.
                let file = unsafe { File::from_raw_fd(fd) };
                let metadata = io(file.metadata())?;
                if current.starts_with(private_root) {
                    // SAFETY: read-only process identity query.
                    let uid = unsafe { libc::geteuid() };
                    need(
                        metadata.uid() == uid && metadata.mode() & 0o022 == 0,
                        "Private custody namespace permits another writer.",
                    )?;
                    if current == private_root {
                        need(
                            metadata.mode() & 0o077 == 0,
                            "Development custody root is not private.",
                        )?;
                    }
                }
                chain.push((current.clone(), file, node(&metadata)));
            }
            let value = Self { chain };
            value.verify()?;
            Ok(value)
        }

        fn path(&self) -> &Path {
            &self.chain.last().expect("retained root").0
        }
        fn file(&self) -> &File {
            &self.chain.last().expect("retained root").1
        }
        fn identity(&self) -> &Node {
            &self.chain.last().expect("retained root").2
        }

        fn verify(&self) -> Result<()> {
            for (path, file, expected) in &self.chain {
                let named = io(fs::symlink_metadata(path))?;
                need(
                    named.is_dir()
                        && !named.file_type().is_symlink()
                        && node(&named) == *expected
                        && node(&io(file.metadata())?) == *expected,
                    "Development custody directory identity changed.",
                )?;
            }
            Ok(())
        }

        fn child_new(&self, name: &str, private_root: &Path) -> Result<Self> {
            self.verify()?;
            let member = CString::new(name).map_err(|_| fault())?;
            // SAFETY: fixed literal child name and retained parent descriptor.
            let result = unsafe { libc::mkdirat(self.file().as_raw_fd(), member.as_ptr(), 0o700) };
            need(
                result == 0,
                "Development custody output already exists or cannot be created.",
            )?;
            io(self.file().sync_all())?;
            let child = Self::open(&self.path().join(name), private_root)?;
            self.verify()?;
            Ok(child)
        }

        pub(super) fn write_json(&self, name: &str, value: &Value) -> Result<()> {
            let raw = serde_json::to_vec(value).map_err(|_| fault())?;
            self.write(name, &raw, MAX_JSON).map(|_| ())
        }

        pub(super) fn write(&self, name: &str, bytes: &[u8], maximum: usize) -> Result<Value> {
            const MEMBERS: &[&str] = &[
                "startup.original.json",
                "intent.original.json",
                "audition.original.wav",
                "prepared-response.original.json",
                "writer-handoff-intent.original.json",
                "writer-handoff.original.json",
            ];
            need(
                MEMBERS.contains(&name)
                    && !bytes.is_empty()
                    && bytes.len() <= maximum
                    && maximum <= 3 * 1024 * 1024,
                "Development custody output exceeded its closed bounds.",
            )?;
            self.verify()?;
            let member = CString::new(name).map_err(|_| fault())?;
            // SAFETY: NUL-free fixed member and live parent FD. O_EXCL retains
            // the first attempt; NOFOLLOW and NONBLOCK reject hostile leaves.
            let fd = unsafe {
                libc::openat(
                    self.file().as_raw_fd(),
                    member.as_ptr(),
                    libc::O_RDWR
                        | libc::O_CREAT
                        | libc::O_EXCL
                        | libc::O_NOFOLLOW
                        | libc::O_CLOEXEC
                        | libc::O_NONBLOCK,
                    0o600,
                )
            };
            need(
                fd >= 0,
                "Development original already exists or cannot be created; do not retry.",
            )?;
            // SAFETY: successful openat returns a newly owned descriptor.
            let mut file = unsafe { File::from_raw_fd(fd) };
            io(file.write_all(bytes))?;
            io(file.sync_all())?;
            io(self.file().sync_all())?;
            let original = leaf(&io(file.metadata())?)?;
            need(
                original.bytes == bytes.len() as u64
                    && leaf(&io(fs::symlink_metadata(self.path().join(name)))?)? == original,
                "Development output identity changed while publishing.",
            )?;
            use std::io::{Seek, SeekFrom};
            io(file.seek(SeekFrom::Start(0)))?;
            let mut readback = Vec::with_capacity(bytes.len());
            io(Read::by_ref(&mut file)
                .take(maximum as u64 + 1)
                .read_to_end(&mut readback))?;
            need(
                readback == bytes
                    && leaf(&io(file.metadata())?)? == original
                    && leaf(&io(fs::symlink_metadata(self.path().join(name)))?)? == original,
                "Development output bytes changed during readback.",
            )?;
            self.verify()?;
            Ok(
                json!({"name": name, "bytes": bytes.len(), "sha256": sha(bytes),
                "identity": original.node, "links": original.links}),
            )
        }

        fn read(&self, name: &str, maximum: usize) -> Result<Input> {
            need(
                matches!(
                    name,
                    "development-audition-admission.json" | "tauri-audio-before.private.json"
                ) && maximum <= MAX_JSON,
                "Development input is outside the closed manifest set.",
            )?;
            self.verify()?;
            let member = CString::new(name).map_err(|_| fault())?;
            // SAFETY: fixed member, retained parent, nonfollowing regular-file open.
            let fd = unsafe {
                libc::openat(
                    self.file().as_raw_fd(),
                    member.as_ptr(),
                    libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK,
                )
            };
            need(fd >= 0, "Development custody input cannot be retained.")?;
            // SAFETY: successful openat returns a newly owned descriptor.
            let mut file = unsafe { File::from_raw_fd(fd) };
            let expected = leaf(&io(file.metadata())?)?;
            need(
                expected.bytes > 0 && expected.bytes <= maximum as u64,
                "Development input exceeded its byte bound.",
            )?;
            let path = self.path().join(name);
            need(
                leaf(&io(fs::symlink_metadata(&path))?)? == expected,
                "Development input changed at open.",
            )?;
            let mut raw = Vec::with_capacity(expected.bytes as usize);
            io(Read::by_ref(&mut file)
                .take(maximum as u64 + 1)
                .read_to_end(&mut raw))?;
            let result = Input {
                path,
                file,
                expected,
                raw,
            };
            result.verify()?;
            self.verify()?;
            Ok(result)
        }
    }

    struct Input {
        path: PathBuf,
        file: File,
        expected: Leaf,
        raw: Vec<u8>,
    }
    impl Input {
        fn verify(&self) -> Result<()> {
            need(
                leaf(&io(self.file.metadata())?)? == self.expected
                    && leaf(&io(fs::symlink_metadata(&self.path))?)? == self.expected
                    && self.raw.len() as u64 == self.expected.bytes,
                "Development custody original input changed.",
            )
        }
    }

    #[derive(Clone, Copy, PartialEq, Eq)]
    pub(super) enum Stage {
        Armed,
        Attempted,
        Prepared,
        HandoffAttempted,
        HandedOff,
    }

    pub(super) struct State {
        pub(super) admission: Admission,
        manifest: Input,
        before: Input,
        evidence: Directory,
        data: Directory,
        cas: Directory,
        pub(super) output: Directory,
        database_identity: String,
        pub(super) workspace_session: Uuid,
        pub(super) recipe: DeliveryRecipe,
        pub(super) stage: Stage,
    }
    impl State {
        pub(super) fn identity(&self, schema: &str) -> Value {
            json!({"schema": schema, "source_sha": self.admission.source_sha,
                "sdk_sha": self.admission.sdk_sha, "run_id": self.admission.run_id,
                "run_attempt": self.admission.run_attempt, "project_id": self.admission.project_id,
                "generation": self.admission.generation, "revision": self.admission.revision_before + 1,
                "deliverable_profile_id": self.admission.deliverable_profile_id,
                "workspace_session": self.workspace_session, "desktop_pid": std::process::id(),
                "database_path": self.data.path().join("motionwright.sqlite3"),
                "database_identity": self.database_identity,
                "cas_path": self.cas.path(), "cas_identity": self.cas.identity(),
                "manifest_sha256": sha(&self.manifest.raw),
                "before_receipt_sha256": self.admission.before_receipt_sha256,
                "original_owner": "existing Tauri OwnerInvocation; no new authority",
                "source_classification": "SYNTHETIC_TONES_NOT_SPEECH",
                "automatic_retry": false, "human_approval": false, "release_authorized": false,
                "formal_G2": false, "formal_G3": false})
        }

        pub(super) fn check(
            &self,
            service: &StudioService,
            project: &Project,
            profile_id: Uuid,
            session: Uuid,
        ) -> Result<()> {
            self.manifest.verify()?;
            self.before.verify()?;
            self.evidence.verify()?;
            self.data.verify()?;
            self.cas.verify()?;
            self.output.verify()?;
            let current_identity = service
                .store()
                .lock()
                .database_file_identity()
                .map_err(|_| fault())?;
            need(
                current_identity == self.database_identity
                    && session == self.workspace_session
                    && !session.is_nil()
                    && project.id == self.admission.project_id
                    && project.generation == self.admission.generation
                    && project.revision == self.admission.revision_before + 1
                    && profile_id == self.admission.deliverable_profile_id
                    && project.production_design.delivery_recipes.get(&profile_id)
                        == Some(&self.recipe),
                "Development audition source, Store, session or saved recipe changed.",
            )
        }
    }

    pub(super) fn admit(
        source: &str,
        sdk: &str,
        data_dir: &Path,
        session: Uuid,
        service: &StudioService,
        manifest_path: &Path,
    ) -> Result<DevelopmentAuditionCustody> {
        need(hex(source, 40) && hex(sdk, 40) && !session.is_nil()
            && env("CI")? == "true" && env("GITHUB_ACTIONS")? == "true"
            && env("GITHUB_EVENT_NAME")? == "workflow_dispatch"
            && env("GITHUB_REPOSITORY")? == "seradotcom/motionwright"
            && env("MW_AUDIO_DIAGNOSTIC_SELECTION")? == "tauri-audition"
            && env("GITHUB_SHA")? == source && env("GITHUB_RUN_ATTEMPT")? == "1"
            && env("GITHUB_WORKFLOW_REF")?.starts_with("seradotcom/motionwright/.github/workflows/v05-original-native-mix.yml@refs/heads/"),
            "Development audition startup is outside its compiled manual CI case.")?;
        let run_id = env("GITHUB_RUN_ID")?;
        need(
            !run_id.is_empty() && run_id.len() <= 20 && run_id.bytes().all(|b| b.is_ascii_digit()),
            "Development audition run identity is invalid.",
        )?;
        let runner = PathBuf::from(env("RUNNER_TEMP")?);
        need(
            runner.is_absolute() && io(fs::canonicalize(&runner))? == runner,
            "Development runner root is not canonical.",
        )?;
        let root = runner.join(format!("motionwright-tauri-audition-{run_id}-1"));
        need(
            manifest_path == root.join("evidence/development-audition-admission.json")
                && data_dir == root.join("profile/data/com.seradotcom.motionwright"),
            "Development audition manifest or actual Tauri Store path differs.",
        )?;
        let evidence = Directory::open(&root.join("evidence"), &root)?;
        let manifest = evidence.read("development-audition-admission.json", 16 * 1024)?;
        let admission: Admission = serde_json::from_slice(&manifest.raw).map_err(|_| fault())?;
        need(
            admission.schema == "motionwright.development-tauri-audition-admission/1"
                && admission.source_sha == source
                && admission.sdk_sha == sdk
                && admission.run_id == run_id
                && admission.run_attempt == "1"
                && admission.private_root == root
                && hex(&admission.before_receipt_sha256, 64)
                && !admission.project_id.is_nil()
                && !admission.generation.is_nil()
                && !admission.deliverable_profile_id.is_nil()
                && admission.revision_before < 64,
            "Development audition admission differs from its closed case.",
        )?;
        let before = evidence.read("tauri-audio-before.private.json", MAX_JSON)?;
        need(
            sha(&before.raw) == admission.before_receipt_sha256,
            "Development audition fixture witness changed.",
        )?;
        let witness: Value = serde_json::from_slice(&before.raw).map_err(|_| fault())?;
        let project: Project =
            serde_json::from_value(witness["project"].clone()).map_err(|_| fault())?;
        let expected_change: Change =
            serde_json::from_value(witness["expected_change"].clone()).map_err(|_| fault())?;
        let recipe = match expected_change {
            Change::SetDeliveryRecipe {
                deliverable_profile_id,
                recipe: Some(recipe),
            } if deliverable_profile_id == admission.deliverable_profile_id
                && recipe.original_mix.is_some() =>
            {
                recipe
            }
            _ => return Err(fault()),
        };
        let actual = service.project(admission.project_id).map_err(|_| fault())?;
        let database_identity = service
            .store()
            .lock()
            .database_file_identity()
            .map_err(|_| fault())?;
        let data = Directory::open(data_dir, &root)?;
        let cas = Directory::open(&data_dir.join("blobs"), &root)?;
        need(
            witness["schema"] == "motionwright.tauri-audio-fixture-before/1"
                && witness["source_classification"] == "SYNTHETIC_TONES_NOT_SPEECH"
                && witness["writer_handoff_verified"] == true
                && witness["database_path"].as_str()
                    == data_dir.join("motionwright.sqlite3").to_str()
                && witness["database_identity"] == database_identity
                && witness["cas_root"]["identity"]
                    == serde_json::to_value(cas.identity()).map_err(|_| fault())?
                && project == actual
                && project.id == admission.project_id
                && project.generation == admission.generation
                && project.revision == admission.revision_before
                && project.production_design.delivery_recipes.is_empty(),
            "Development audition fixture, actual Store or original CAS identity differs.",
        )?;
        let output = evidence.child_new("native-audition", &root)?;
        let state = State {
            admission,
            manifest,
            before,
            evidence,
            data,
            cas,
            output,
            database_identity,
            workspace_session: session,
            recipe,
            stage: Stage::Armed,
        };
        let mut receipt = state.identity("motionwright.development-audition-startup/1");
        receipt["revision"] = project.revision.into();
        receipt["native_effect_or_owner_granted"] = false.into();
        state.output.write_json("startup.original.json", &receipt)?;
        Ok(DevelopmentAuditionCustody {
            inner: Some(Arc::new(Mutex::new(state))),
        })
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::os::unix::fs::{DirBuilderExt, PermissionsExt};

        fn root() -> tempfile::TempDir {
            let root = tempfile::tempdir().unwrap();
            fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
            root
        }

        #[test]
        fn output_is_exclusive_exact_and_bounded() {
            let root = root();
            let directory = Directory::open(root.path(), root.path()).unwrap();
            let result = directory
                .write("audition.original.wav", b"original", 64)
                .unwrap();
            assert_eq!(result["sha256"], sha(b"original"));
            assert!(
                directory
                    .write("audition.original.wav", b"second", 64)
                    .is_err()
            );
            assert!(directory.write("../outside", b"x", 64).is_err());
            assert!(
                directory
                    .write("intent.original.json", b"oversized", 2)
                    .is_err()
            );
            assert_eq!(
                fs::read(root.path().join("audition.original.wav")).unwrap(),
                b"original"
            );
        }

        #[test]
        fn replaced_directory_or_symlink_cannot_publish() {
            let root = root();
            let child = root.path().join("evidence");
            fs::DirBuilder::new().mode(0o700).create(&child).unwrap();
            let retained = Directory::open(&child, root.path()).unwrap();
            fs::rename(&child, root.path().join("original")).unwrap();
            fs::DirBuilder::new().mode(0o700).create(&child).unwrap();
            assert!(retained.write("intent.original.json", b"{}", 64).is_err());
            let link = root.path().join("linked");
            std::os::unix::fs::symlink(&child, &link).unwrap();
            assert!(Directory::open(&link, root.path()).is_err());
        }

        #[test]
        fn directory_child_writes_do_not_change_identity() {
            let root = root();
            let directory = Directory::open(root.path(), root.path()).unwrap();
            directory.write("intent.original.json", b"{}", 64).unwrap();
            directory.verify().unwrap();
            directory.child_new("native-audition", root.path()).unwrap();
            directory.verify().unwrap();
            assert!(directory.child_new("native-audition", root.path()).is_err());
        }

        #[test]
        fn input_rejects_links_and_retains_leaf_identity() {
            let root = root();
            let path = root.path().join("tauri-audio-before.private.json");
            fs::write(&path, b"{}").unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
            let directory = Directory::open(root.path(), root.path()).unwrap();
            let retained = directory
                .read("tauri-audio-before.private.json", 64)
                .unwrap();
            fs::write(&path, b"changed").unwrap();
            assert!(retained.verify().is_err());
            fs::hard_link(&path, root.path().join("alias")).unwrap();
            assert!(
                directory
                    .read("tauri-audio-before.private.json", 64)
                    .is_err()
            );
        }

        #[test]
        fn private_root_and_unknown_manifest_fields_fail_closed() {
            let root = root();
            fs::set_permissions(root.path(), fs::Permissions::from_mode(0o755)).unwrap();
            assert!(Directory::open(root.path(), root.path()).is_err());
            assert!(
                serde_json::from_str::<Admission>(r#"{"schema":"x","fake_owner":true}"#).is_err()
            );
            assert!(!hex("A".repeat(40).as_str(), 40));
            assert!(!hex("a".repeat(41).as_str(), 40));
        }
    }
}
