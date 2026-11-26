//! Local observation of an already configured hybrid owner.
//!
//! No configuration, session, receipt or filesystem permission is created.
//! The caller still needs its normal one-time RenderLocal authorization, and
//! every actual operation retains the existing SDK permission/budget fences.
#[cfg(target_os = "linux")]
use super::hybrid_budget::{HYBRID_GRANT_MAX_BYTES, HybridProjectGrant};
use super::hybrid_budget::{
    HybridGrantDaemon, grant_path_identity, project_digest, require, verify_grant_daemon,
};
use super::*;
use std::sync::Arc;

#[derive(Debug, Clone)]
struct LocalFile {
    path: PathBuf,
    sha256: String,
    bytes: u64,
    maximum: u64,
    snapshot: fs::Metadata,
    file: Arc<fs::File>,
}

impl LocalFile {
    #[cfg(target_os = "linux")]
    fn capture(path: &Path, maximum: u64) -> NativeResult<Self> {
        use std::os::unix::fs::MetadataExt;
        let before = safe_metadata(path)?;
        require(
            before.is_file() && before.nlink() == 1 && before.uid() == unsafe { libc::geteuid() },
            "Local hybrid configuration must be an original owner file",
        )?;
        let parent = path
            .parent()
            .ok_or_else(|| invalid("Local hybrid configuration parent is absent"))?;
        let name = path
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| invalid("Local hybrid configuration member is invalid"))?;
        let captured = capture_production_file_with_bounds(parent, name, 1, None, maximum)?;
        let (file, opened) = open_verified_artifact_input(path, &before)?;
        require(
            same_artifact_snapshot(&before, &opened),
            "Local hybrid configuration changed while retaining its identity",
        )?;
        let selected = Self {
            path: path.to_path_buf(),
            sha256: captured.descriptor.sha256,
            bytes: captured.descriptor.size_bytes,
            maximum,
            snapshot: before,
            file: Arc::new(file),
        };
        selected.recheck()?;
        Ok(selected)
    }

    fn recheck(&self) -> NativeResult<Vec<u8>> {
        let current = safe_metadata(&self.path)?;
        let retained = self
            .file
            .metadata()
            .map_err(|_| invalid("Local hybrid retained configuration is unavailable"))?;
        require(
            same_artifact_snapshot(&self.snapshot, &current)
                && same_artifact_snapshot(&self.snapshot, &retained),
            "Local hybrid configuration identity changed",
        )?;
        let bytes = read_verified_production_artifact(
            self.path
                .parent()
                .ok_or_else(|| invalid("Local hybrid configuration parent is absent"))?,
            self.path
                .file_name()
                .and_then(|name| name.to_str())
                .ok_or_else(|| invalid("Local hybrid configuration member is invalid"))?,
            &self.sha256,
            self.maximum,
        )?;
        require(
            bytes.len() as u64 == self.bytes
                && same_artifact_snapshot(&self.snapshot, &safe_metadata(&self.path)?)
                && same_artifact_snapshot(
                    &self.snapshot,
                    &self.file.metadata().map_err(|_| {
                        invalid("Local hybrid retained configuration is unavailable")
                    })?,
                ),
            "Local hybrid configuration changed during its bounded read",
        )?;
        Ok(bytes)
    }
}

#[derive(Debug, Clone)]
struct LocalPath {
    path: PathBuf,
    directory: bool,
    identity: [u64; 5],
    file: Arc<fs::File>,
}

impl LocalPath {
    #[cfg(target_os = "linux")]
    fn capture(path: &Path, directory: bool) -> NativeResult<Self> {
        use std::os::unix::fs::OpenOptionsExt;
        let identity = grant_path_identity(path, directory)?;
        let flags = libc::O_NOFOLLOW
            | libc::O_CLOEXEC
            | if directory {
                libc::O_DIRECTORY
            } else {
                libc::O_PATH
            };
        let file = fs::OpenOptions::new()
            .read(true)
            .custom_flags(flags)
            .open(path)
            .map_err(|_| invalid("Local hybrid owner path cannot be retained"))?;
        let selected = Self {
            path: path.to_path_buf(),
            directory,
            identity,
            file: Arc::new(file),
        };
        selected.recheck()?;
        Ok(selected)
    }

    fn recheck(&self) -> NativeResult<()> {
        require(
            grant_path_identity(&self.path, self.directory)? == self.identity
                && retained_path_identity(&self.file)? == self.identity,
            "Local hybrid owner path identity changed",
        )
    }
}

#[cfg(target_os = "linux")]
fn retained_path_identity(file: &fs::File) -> NativeResult<[u64; 5]> {
    use std::os::unix::fs::MetadataExt;
    let metadata = file
        .metadata()
        .map_err(|_| invalid("Local hybrid retained owner path is unavailable"))?;
    Ok([
        metadata.dev(),
        metadata.ino(),
        u64::from(metadata.mode()),
        u64::from(metadata.uid()),
        u64::from(metadata.gid()),
    ])
}

#[cfg(not(target_os = "linux"))]
fn retained_path_identity(_file: &fs::File) -> NativeResult<[u64; 5]> {
    Err(Error::new(
        ErrorCode::Unsupported,
        "Local hybrid owner observation requires the Linux provider host",
    ))
}

/// Private, in-memory custody. The descriptors keep the original nodes alive;
/// neither a saved JSON document nor an IPC payload can reconstruct this type.
#[derive(Debug, Clone)]
pub(super) struct LocalHybridOwner {
    project_id: Uuid,
    expected: RevisionStamp,
    project_sha256: String,
    connection_identity: String,
    connection: LocalFile,
    owner_config: LocalFile,
    project: LocalPath,
    output: LocalPath,
    socket: LocalPath,
    daemon: HybridGrantDaemon,
}

impl LocalHybridOwner {
    pub(super) fn project_root(&self) -> &Path {
        &self.project.path
    }
    pub(super) fn output_root(&self) -> &Path {
        &self.output.path
    }
    pub(super) fn output_identity(&self) -> &[u64; 5] {
        &self.output.identity
    }
    pub(super) fn project_sha256(&self) -> &str {
        &self.project_sha256
    }

    pub(super) fn public_observation(&self) -> Value {
        json!({
            "schema":"motionwright.native.local-hybrid-owner-observation/1",
            "source_project_sha256":self.project_sha256,
            "connection_identity":self.connection_identity,
            "connection_sha256":self.connection.sha256,
            "owner_config_sha256":self.owner_config.sha256,
            "daemon_pid":self.daemon.pid,
            "daemon_start_ticks":self.daemon.start_ticks,
            "authority_origin":"existing_local_owner_configuration",
            "original_ci_receipt":false,
            "effect_grant_issued":false,
            "native_effect_started_by_observation":false,
            "automatic_retry":false
        })
    }

    pub(super) fn recheck(&self, coordinator: &ProductionCoordinator) -> NativeResult<()> {
        let current = coordinator
            .service
            .project(self.project_id)
            .map_err(storage_error)?;
        require(
            RevisionStamp::from(&current) == self.expected
                && project_digest(&current)? == self.project_sha256
                && coordinator.client.connection().resource == current.resource_key()
                && coordinator.client.connection().identity()? == self.connection_identity
                && coordinator.client.connection().output_root == self.output.path
                && coordinator.client.connection().socket == self.socket.path,
            "Local hybrid owner belongs to another saved source or production connection",
        )?;
        self.project.recheck()?;
        self.output.recheck()?;
        self.socket.recheck()?;
        let config = self.owner_config.recheck()?;
        super::hybrid_budget::verify_owner_grant_profile(
            &config,
            &self.project.path,
            &self.output.path,
        )?;
        let connection = self.connection.recheck()?;
        require(
            serde_json::from_slice::<Value>(&connection)
                .map_err(|_| invalid("Local hybrid connection original is malformed"))?
                == serde_json::to_value(coordinator.client.connection())
                    .map_err(|_| invalid("Local hybrid connection encoding failed"))?,
            "Local hybrid connection original names another owner",
        )?;
        verify_grant_daemon(&self.daemon)?;
        self.project.recheck()?;
        self.output.recheck()?;
        self.socket.recheck()
    }

    pub(super) fn require_unused_snapshots(&self, namespace: &str) -> NativeResult<()> {
        self.output.recheck()?;
        for ordinal in [0, 2] {
            for suffix in [
                "semwright-motion.original.json",
                "semwright-motion-custody.original.json",
            ] {
                require(
                    matches!(fs::symlink_metadata(self.output.path.join(
                        format!("{namespace}-mc-{ordinal}.{suffix}"))),
                        Err(error) if error.kind() == std::io::ErrorKind::NotFound),
                    "Local hybrid source original already exists or is unavailable; preserve it without replay",
                )?;
            }
        }
        self.output.recheck()
    }
}

#[cfg(target_os = "linux")]
fn read_proc_member(pid: u32, name: &str) -> NativeResult<Vec<u8>> {
    use std::os::unix::fs::OpenOptionsExt;
    require(
        pid > 0 && matches!(name, "stat" | "cmdline"),
        "Local hybrid daemon observation has an invalid selector",
    )?;
    let file = fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(PathBuf::from(format!("/proc/{pid}")).join(name))
        .map_err(|_| invalid("Local hybrid daemon observation is unavailable"))?;
    let mut bytes = Vec::new();
    file.take(HYBRID_GRANT_MAX_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| invalid("Local hybrid daemon observation failed"))?;
    require(
        !bytes.is_empty() && bytes.len() as u64 <= HYBRID_GRANT_MAX_BYTES,
        "Local hybrid daemon observation exceeds its bound",
    )?;
    Ok(bytes)
}

/// No protocol request is sent. A nonblocking connect has one bounded wait;
/// a full queue, interruption or any ambiguous result refuses admission.
#[cfg(target_os = "linux")]
fn socket_peer_pid(path: &Path) -> NativeResult<u32> {
    use std::os::{
        fd::{AsRawFd, FromRawFd, OwnedFd},
        unix::ffi::OsStrExt,
    };
    let raw_path = path.as_os_str().as_bytes();
    let mut address: libc::sockaddr_un = unsafe { std::mem::zeroed() };
    require(
        path.is_absolute()
            && !raw_path.is_empty()
            && raw_path.len() < address.sun_path.len()
            && !raw_path.contains(&0),
        "Local hybrid socket name exceeds its closed bound",
    )?;
    address.sun_family = libc::AF_UNIX as libc::sa_family_t;
    for (target, source) in address.sun_path.iter_mut().zip(raw_path) {
        *target = *source as libc::c_char;
    }
    let raw = unsafe {
        libc::socket(
            libc::AF_UNIX,
            libc::SOCK_STREAM | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
            0,
        )
    };
    require(
        raw >= 0,
        "Local hybrid peer observation cannot open a socket",
    )?;
    // SAFETY: successful socket returns one owned descriptor; every return closes it.
    let fd = unsafe { OwnedFd::from_raw_fd(raw) };
    let connected = unsafe {
        libc::connect(
            fd.as_raw_fd(),
            (&address as *const libc::sockaddr_un).cast(),
            std::mem::size_of::<libc::sockaddr_un>() as libc::socklen_t,
        )
    };
    if connected != 0 {
        require(
            std::io::Error::last_os_error().raw_os_error() == Some(libc::EINPROGRESS),
            "Local hybrid owner socket cannot be observed without retry",
        )?;
        let mut poll = libc::pollfd {
            fd: fd.as_raw_fd(),
            events: libc::POLLOUT,
            revents: 0,
        };
        require(
            unsafe { libc::poll(&mut poll, 1, 1_500) } == 1
                && poll.revents & libc::POLLOUT != 0
                && poll.revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL) == 0,
            "Local hybrid owner socket observation timed out or was interrupted",
        )?;
        let mut error: libc::c_int = 0;
        let mut size = std::mem::size_of::<libc::c_int>() as libc::socklen_t;
        require(
            unsafe {
                libc::getsockopt(
                    fd.as_raw_fd(),
                    libc::SOL_SOCKET,
                    libc::SO_ERROR,
                    (&mut error as *mut libc::c_int).cast(),
                    &mut size,
                )
            } == 0
                && size as usize == std::mem::size_of::<libc::c_int>()
                && error == 0,
            "Local hybrid owner socket connection was refused",
        )?;
    }
    let mut peer: libc::ucred = unsafe { std::mem::zeroed() };
    let mut size = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
    require(
        unsafe {
            libc::getsockopt(
                fd.as_raw_fd(),
                libc::SOL_SOCKET,
                libc::SO_PEERCRED,
                (&mut peer as *mut libc::ucred).cast(),
                &mut size,
            )
        } == 0
            && size as usize == std::mem::size_of::<libc::ucred>()
            && peer.pid > 0
            && peer.uid == unsafe { libc::geteuid() },
        "Local hybrid socket peer is not the current owner",
    )?;
    Ok(peer.pid as u32)
}

#[cfg(target_os = "linux")]
fn observe_daemon(socket: &LocalPath, config: &Path) -> NativeResult<HybridGrantDaemon> {
    use super::hybrid_budget::daemon_start_from_stat;
    socket.recheck()?;
    let pid = socket_peer_pid(&socket.path)?;
    let start_ticks = daemon_start_from_stat(&read_proc_member(pid, "stat")?, pid)?;
    let raw = read_proc_member(pid, "cmdline")?;
    require(
        raw.ends_with(&[0]),
        "Local hybrid daemon argv is incomplete",
    )?;
    let argv = raw[..raw.len() - 1]
        .split(|byte| *byte == 0)
        .map(|part| {
            String::from_utf8(part.to_vec())
                .map_err(|_| invalid("Local hybrid daemon argv is malformed"))
        })
        .collect::<NativeResult<Vec<_>>>()?;
    require(
        argv.len() == 5
            && Path::new(&argv[0]).is_absolute()
            && Path::new(&argv[0])
                .file_name()
                .and_then(|name| name.to_str())
                == Some("semwrightd")
            && argv[1] == "--config"
            && Path::new(&argv[2]) == config
            && argv[3] == "--socket"
            && Path::new(&argv[4]) == socket.path,
        "Local hybrid socket is not served by the selected owner configuration",
    )?;
    let daemon = HybridGrantDaemon {
        argv,
        pid,
        start_ticks,
    };
    verify_grant_daemon(&daemon)?;
    socket.recheck()?;
    Ok(daemon)
}

impl ProductionCoordinator {
    /// Observe the Linux owner already selected by the trusted application.
    /// This only reads and retains existing authority; it creates no grant,
    /// output, receipt, budget, renderer work or replacement Broker process.
    /// The normal application RenderLocal grant remains mandatory before use.
    pub fn with_local_hybrid_project_owner(
        self,
        project_id: Uuid,
        expected: &RevisionStamp,
        connection_path: &Path,
    ) -> NativeResult<Self> {
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (project_id, expected, connection_path);
            Err(Error::new(
                ErrorCode::Unsupported,
                "Local hybrid owner observation requires the Linux provider host",
            ))
        }
        #[cfg(target_os = "linux")]
        {
            require(
                self.active_motion_budget.is_none()
                    && self.active_hybrid_budget.is_none()
                    && self.hybrid_project_grant.is_none(),
                "Local hybrid owner custody must precede its original native root",
            )?;
            let current = self.service.project(project_id).map_err(storage_error)?;
            require(
                RevisionStamp::from(&current) == *expected
                    && current.id == project_id
                    && self.client.connection().resource == current.resource_key(),
                "Local hybrid owner selection requires the current saved Project",
            )?;
            current
                .validate()
                .map_err(|_| invalid("Local hybrid source Project is invalid"))?;
            let source_sha256 = project_digest(&current)?;
            let owner = self
                .client
                .connection()
                .output_root
                .parent()
                .ok_or_else(|| invalid("Local hybrid output owner is absent"))?;
            let config_path = owner.join("config/owner.toml");
            require(
                self.client.connection().output_root == owner.join("output")
                    && self.client.connection().socket == owner.join("runtime/broker.sock")
                    && connection_path == owner.join("config/motionwright-connection.json"),
                "Local hybrid connection does not select the existing closed owner layout",
            )?;
            let project = LocalPath::capture(&owner.join("project"), true)?;
            let output = LocalPath::capture(&owner.join("output"), true)?;
            let socket = LocalPath::capture(&owner.join("runtime/broker.sock"), false)?;
            let connection = LocalFile::capture(connection_path, MAX_CONFIG_BYTES)?;
            let owner_config = LocalFile::capture(&config_path, HYBRID_GRANT_MAX_BYTES)?;
            super::hybrid_budget::verify_owner_grant_profile(
                &owner_config.recheck()?,
                &project.path,
                &output.path,
            )?;
            let daemon = observe_daemon(&socket, &config_path)?;
            let selected = LocalHybridOwner {
                project_id,
                expected: expected.clone(),
                project_sha256: source_sha256,
                connection_identity: self.client.connection().identity()?,
                connection,
                owner_config,
                project,
                output,
                socket,
                daemon,
            };
            selected.recheck(&self)?;
            selected.require_unused_snapshots(&format!(
                "mw-hybrid-{}",
                &selected.project_sha256[..20]
            ))?;
            let mut retained = self;
            retained.hybrid_project_grant =
                Some(Arc::new(HybridProjectGrant::Local(Box::new(selected))));
            Ok(retained)
        }
    }
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;
    use std::os::unix::{
        fs::{PermissionsExt, symlink},
        net::UnixListener,
    };

    fn private(path: &Path, bytes: &[u8]) {
        fs::write(path, bytes).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o600)).unwrap();
    }

    #[test]
    fn local_hybrid_owner_files_preserve_bytes_and_reject_same_bytes_replacement() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("owner.toml");
        let raw = b"original bytes, including whitespace\n";
        private(&path, raw);
        let retained = LocalFile::capture(&path, 256).unwrap();
        assert_eq!(retained.recheck().unwrap(), raw);
        fs::rename(&path, root.path().join("original-preserved.toml")).unwrap();
        private(&path, raw);
        assert!(retained.recheck().is_err());
        assert_eq!(
            fs::read(root.path().join("original-preserved.toml")).unwrap(),
            raw
        );
    }

    #[test]
    fn local_hybrid_owner_files_reject_links_oversize_and_changed_permissions() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("owner.toml");
        private(&path, b"original");
        assert!(LocalFile::capture(&path, 7).is_err());
        let linked = root.path().join("linked.toml");
        symlink(&path, &linked).unwrap();
        assert!(LocalFile::capture(&linked, 8).is_err());
        fs::remove_file(&linked).unwrap();
        fs::hard_link(&path, &linked).unwrap();
        assert!(LocalFile::capture(&path, 8).is_err());
        fs::remove_file(&linked).unwrap();
        let retained = LocalFile::capture(&path, 8).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o666)).unwrap();
        assert!(retained.recheck().is_err());
        assert!(LocalFile::capture(&path, 8).is_err());
    }

    #[test]
    fn local_hybrid_owner_root_accepts_new_contents_but_rejects_replacement() {
        let root = tempfile::tempdir().unwrap();
        let output = root.path().join("output");
        fs::create_dir(&output).unwrap();
        let retained = LocalPath::capture(&output, true).unwrap();
        private(&output.join("original.json"), b"original");
        retained.recheck().unwrap();
        fs::rename(&output, root.path().join("original-output")).unwrap();
        fs::create_dir(&output).unwrap();
        assert!(retained.recheck().is_err());
        assert!(!output.join("original.json").exists());
    }

    #[test]
    fn local_hybrid_owner_socket_observes_real_peer_and_refuses_non_broker_process() {
        let root = tempfile::tempdir().unwrap();
        let socket = root.path().join("broker.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        assert_eq!(socket_peer_pid(&socket).unwrap(), std::process::id());
        let (stream, _) = listener.accept().unwrap();
        drop(stream);
        let retained = LocalPath::capture(&socket, false).unwrap();
        assert!(observe_daemon(&retained, &root.path().join("owner.toml")).is_err());
        let (stream, _) = listener.accept().unwrap();
        drop(stream);
        fs::rename(&socket, root.path().join("original.sock")).unwrap();
        let _replacement = UnixListener::bind(&socket).unwrap();
        assert!(retained.recheck().is_err());
    }

    #[test]
    fn local_hybrid_owner_proc_selector_and_socket_names_are_bounded() {
        assert!(read_proc_member(0, "stat").is_err());
        assert!(read_proc_member(std::process::id(), "../environ").is_err());
        assert!(socket_peer_pid(Path::new("relative.sock")).is_err());
        assert!(socket_peer_pid(&PathBuf::from(format!("/{}", "x".repeat(200)))).is_err());
        let stat = read_proc_member(std::process::id(), "stat").unwrap();
        assert!(
            super::super::hybrid_budget::daemon_start_from_stat(&stat, std::process::id()).unwrap()
                > 0
        );
    }

    fn coordinator(
        root: &Path,
    ) -> (
        StudioService,
        motionwright_domain::Project,
        ProductionCoordinator,
    ) {
        let service = StudioService::open(root.join("project.sqlite3")).unwrap();
        let project = service
            .create_project("Local owner refusal fixture")
            .unwrap();
        let executable = root.join("transport-witness");
        let session = root.join("session.json");
        let socket = root.join("broker.sock");
        let output = root.join("output");
        private(&executable, b"This file is never executed.\n");
        private(&session, b"{}");
        private(&socket, b"");
        fs::create_dir(&output).unwrap();
        let connection = ProductionConnection {
            schema: CONNECTION_SCHEMA.into(),
            executable_sha256: sha256_file(&executable).unwrap(),
            executable,
            socket,
            session_file: session,
            output_root: output,
            resource: project.resource_key(),
        };
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        (service, project, coordinator)
    }

    fn assert_no_effects(
        root: &Path,
        service: &StudioService,
        project: &motionwright_domain::Project,
    ) {
        assert_eq!(service.project(project.id).unwrap(), *project);
        assert!(
            service
                .production_receipts(project.id, 10)
                .unwrap()
                .is_empty()
        );
        let database = rusqlite::Connection::open_with_flags(
            root.join("project.sqlite3"),
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        )
        .unwrap();
        let roots: i64 = database
            .query_row(
                "SELECT COUNT(*) FROM production_operation_roots",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(roots, 0);
        assert_eq!(fs::read_dir(root.join("output")).unwrap().count(), 0);
    }

    #[test]
    fn local_hybrid_owner_constructor_refuses_stale_source_before_owner_io() {
        let root = tempfile::tempdir().unwrap();
        let (service, project, coordinator) = coordinator(root.path());
        let mut stale = RevisionStamp::from(&project);
        stale.revision += 1;
        let result = coordinator.with_local_hybrid_project_owner(
            project.id,
            &stale,
            &root.path().join("absent-connection.json"),
        );
        assert!(matches!(result, Err(error) if error.message
            == "Local hybrid owner selection requires the current saved Project"));
        assert_no_effects(root.path(), &service, &project);
    }

    #[test]
    fn local_hybrid_owner_constructor_refuses_unselected_layout_without_any_effect() {
        let root = tempfile::tempdir().unwrap();
        let (service, project, coordinator) = coordinator(root.path());
        let result = coordinator.with_local_hybrid_project_owner(
            project.id,
            &RevisionStamp::from(&project),
            &root.path().join("foreign.json"),
        );
        assert!(matches!(result, Err(error) if error.message
            == "Local hybrid connection does not select the existing closed owner layout"));
        assert_no_effects(root.path(), &service, &project);
    }
}
