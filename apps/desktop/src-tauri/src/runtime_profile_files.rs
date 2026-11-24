//! Linux directory-handle confinement for the explicit offline profile copier.
//! Never executes a selected file, traverses a link, overwrites or deletes data.
use super::runtime_profile_bundle::{
    self as bundle, BundleFile, FileRole, Profile, ValidatedBundle,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::{CStr, CString},
    fs::{File, Metadata},
    io::{Read, Write},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::{ffi::OsStrExt, fs::MetadataExt},
    },
    path::{Component, Path, PathBuf},
    sync::Arc,
};
const INVALID: &str = "Runtime package files changed, are unsafe, or exceed their declared limits.";
const FLAGS: i32 = libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK;
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Identity {
    pub device: u64,
    pub inode: u64,
}
impl Identity {
    fn of(m: &Metadata) -> Self {
        Self {
            device: m.dev(),
            inode: m.ino(),
        }
    }
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileIdentity {
    id: Identity,
    bytes: u64,
    modified: (i64, i64),
    changed: (i64, i64),
    mode: u32,
}
impl FileIdentity {
    fn of(m: &Metadata) -> Result<Self, String> {
        if !m.is_file() || m.nlink() != 1 || m.mode() & 0o022 != 0 || m.uid() != owner_uid() {
            return Err(INVALID.into());
        }
        Ok(Self {
            id: Identity::of(m),
            bytes: m.len(),
            modified: (m.mtime(), m.mtime_nsec()),
            changed: (m.ctime(), m.ctime_nsec()),
            mode: m.mode(),
        })
    }
}
fn owner_uid() -> u32 {
    // SAFETY: geteuid has no arguments or memory side effects.
    unsafe { libc::geteuid() }
}
fn name(value: &str) -> Result<CString, String> {
    if value.is_empty() || value.len() > 255 || value.contains('/') || value == "." || value == ".."
    {
        return Err(INVALID.into());
    }
    CString::new(value).map_err(|_| INVALID.into())
}
fn open_at(parent: &File, part: &CStr, flags: i32, mode: u32) -> std::io::Result<File> {
    // SAFETY: parent and terminated name remain alive; a positive fd has one owner.
    let fd = unsafe { libc::openat(parent.as_raw_fd(), part.as_ptr(), flags, mode) };
    if fd < 0 {
        return Err(std::io::Error::last_os_error());
    }
    // SAFETY: successful openat returned this new owned descriptor.
    Ok(unsafe { File::from_raw_fd(fd) })
}
#[derive(Debug, Clone)]
pub struct Directory {
    file: Arc<File>,
    path: PathBuf,
    id: Identity,
}
impl Directory {
    pub fn open(path: &Path) -> Result<Self, String> {
        if !path.is_absolute() || path.components().count() > 64 {
            return Err(INVALID.into());
        }
        let slash = CString::new("/").unwrap();
        // SAFETY: slash is live; flags never create or follow a link.
        let fd = unsafe { libc::open(slash.as_ptr(), FLAGS | libc::O_DIRECTORY) };
        if fd < 0 {
            return Err(INVALID.into());
        }
        // SAFETY: the descriptor was just created and is owned once.
        let mut file = unsafe { File::from_raw_fd(fd) };
        let mut count = 0;
        for part in path.components() {
            match part {
                Component::RootDir => {}
                Component::Normal(part) => {
                    let part = CString::new(part.as_bytes()).map_err(|_| INVALID)?;
                    file =
                        open_at(&file, &part, FLAGS | libc::O_DIRECTORY, 0).map_err(|_| INVALID)?;
                    count += 1;
                }
                _ => return Err(INVALID.into()),
            }
        }
        if count == 0 {
            return Err(INVALID.into());
        }
        let m = file.metadata().map_err(|_| INVALID)?;
        if !m.is_dir() || m.uid() != owner_uid() || m.mode() & 0o022 != 0 {
            return Err(INVALID.into());
        }
        Ok(Self {
            file: Arc::new(file),
            path: path.to_path_buf(),
            id: Identity::of(&m),
        })
    }
    pub fn path(&self) -> &Path {
        &self.path
    }
    pub fn identity(&self) -> Identity {
        self.id
    }
    pub fn validate(&self) -> Result<(), String> {
        let actual = Self::open(&self.path)?;
        if actual.id != self.id {
            return Err(INVALID.into());
        }
        Ok(())
    }
    pub fn child(&self, part: &str) -> Result<Self, String> {
        let part_c = name(part)?;
        let file =
            open_at(&self.file, &part_c, FLAGS | libc::O_DIRECTORY, 0).map_err(|_| INVALID)?;
        let m = file.metadata().map_err(|_| INVALID)?;
        if !m.is_dir() || m.uid() != owner_uid() || m.mode() & 0o022 != 0 {
            return Err(INVALID.into());
        }
        Ok(Self {
            file: Arc::new(file),
            path: self.path.join(part),
            id: Identity::of(&m),
        })
    }
    pub fn maybe_child(&self, part: &str) -> Result<Option<Self>, String> {
        let part_c = name(part)?;
        match open_at(&self.file, &part_c, FLAGS | libc::O_DIRECTORY, 0) {
            Ok(file) => {
                let m = file.metadata().map_err(|_| INVALID)?;
                if !m.is_dir() || m.uid() != owner_uid() || m.mode() & 0o077 != 0 {
                    return Err(INVALID.into());
                }
                Ok(Some(Self {
                    file: Arc::new(file),
                    path: self.path.join(part),
                    id: Identity::of(&m),
                }))
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(_) => Err(INVALID.into()),
        }
    }
    pub fn create_child(&self, part: &str, existing: bool) -> Result<Self, String> {
        self.validate()?;
        let part_c = name(part)?;
        // SAFETY: only one validated child component is created under the retained parent.
        let code = unsafe { libc::mkdirat(self.file.as_raw_fd(), part_c.as_ptr(), 0o700) };
        if code != 0
            && (!existing
                || std::io::Error::last_os_error().kind() != std::io::ErrorKind::AlreadyExists)
        {
            return Err(
                "Runtime destination already exists or cannot be created; nothing was replaced."
                    .into(),
            );
        }
        let result = self.child(part)?;
        if result.file.metadata().map_err(|_| INVALID)?.mode() & 0o077 != 0 {
            return Err(INVALID.into());
        }
        self.file.sync_all().map_err(|_| INVALID)?;
        result.validate()?;
        Ok(result)
    }
    fn at(
        &self,
        relative: &str,
        expected: Option<&BTreeMap<String, Identity>>,
    ) -> Result<Self, String> {
        let mut dir = self.clone();
        let mut current = String::new();
        if relative.is_empty() {
            return Ok(dir);
        }
        for part in relative.split('/') {
            if !current.is_empty() {
                current.push('/');
            }
            current.push_str(part);
            dir = dir.child(part)?;
            if expected.is_some_and(|map| map.get(&current) != Some(&dir.id)) {
                return Err(INVALID.into());
            }
        }
        Ok(dir)
    }
    fn open_file(
        &self,
        relative: &str,
        expected: Option<&BTreeMap<String, Identity>>,
        create: bool,
    ) -> Result<File, String> {
        if relative != bundle::MANIFEST && !bundle::relative_file(relative) {
            return Err(INVALID.into());
        }
        let (parent, leaf) = relative.rsplit_once('/').unwrap_or(("", relative));
        let directory = self.at(parent, expected)?;
        let flags = if create {
            libc::O_RDWR
                | libc::O_CREAT
                | libc::O_EXCL
                | libc::O_NOFOLLOW
                | libc::O_CLOEXEC
                | libc::O_NONBLOCK
        } else {
            FLAGS
        };
        let file = open_at(&directory.file, &name(leaf)?, flags, 0o600).map_err(|_| INVALID)?;
        FileIdentity::of(&file.metadata().map_err(|_| INVALID)?)?;
        Ok(file)
    }
    pub fn read_small(&self, relative: &str, max: u64) -> Result<Vec<u8>, String> {
        self.validate()?;
        let file = self.open_file(relative, None, false)?;
        let before = FileIdentity::of(&file.metadata().map_err(|_| INVALID)?)?;
        if before.bytes > max {
            return Err(INVALID.into());
        }
        let mut bytes = Vec::new();
        (&file)
            .take(max + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| INVALID)?;
        if bytes.len() as u64 != before.bytes
            || FileIdentity::of(&file.metadata().map_err(|_| INVALID)?)? != before
        {
            return Err(INVALID.into());
        }
        self.validate()?;
        Ok(bytes)
    }
    pub fn write_new(&self, relative: &str, bytes: &[u8]) -> Result<(), String> {
        self.validate()?;
        let mut file = self.open_file(relative, None, true)?;
        file.write_all(bytes)
            .and_then(|()| file.sync_all())
            .map_err(|_| INVALID)?;
        self.validate()
    }
    fn names(&self) -> Result<BTreeSet<String>, String> {
        let dot = CString::new(".").unwrap();
        let file = open_at(&self.file, &dot, FLAGS | libc::O_DIRECTORY, 0).map_err(|_| INVALID)?;
        use std::os::fd::IntoRawFd;
        let fd = file.into_raw_fd();
        // SAFETY: fdopendir takes ownership only on success.
        let raw = unsafe { libc::fdopendir(fd) };
        if raw.is_null() {
            // SAFETY: fdopendir failed; the descriptor remains ours.
            unsafe { libc::close(fd) };
            return Err(INVALID.into());
        }
        struct Stream(*mut libc::DIR);
        impl Drop for Stream {
            fn drop(&mut self) {
                // SAFETY: this guard owns the successful fdopendir result exactly once.
                unsafe { libc::closedir(self.0) };
            }
        }
        let stream = Stream(raw);
        let mut names = BTreeSet::new();
        loop {
            // SAFETY: errno is thread-local; stream is open and used synchronously.
            unsafe { *libc::__errno_location() = 0 };
            // SAFETY: readdir is valid until the next call on this retained stream.
            let entry = unsafe { libc::readdir(stream.0) };
            if entry.is_null() {
                // SAFETY: read the thread-local errno set by the preceding call.
                if unsafe { *libc::__errno_location() } != 0 {
                    return Err(INVALID.into());
                }
                break;
            }
            // SAFETY: readdir provides a NUL-terminated d_name within the entry.
            let value = unsafe { CStr::from_ptr((*entry).d_name.as_ptr()) }
                .to_str()
                .map_err(|_| INVALID)?;
            if value == "." || value == ".." {
                continue;
            }
            if names.len() > bundle::MAX_FILES + bundle::MAX_DIRECTORIES
                || !names.insert(value.to_string())
            {
                return Err(INVALID.into());
            }
        }
        Ok(names)
    }
    pub fn free_bytes(&self) -> Result<u64, String> {
        let mut value = std::mem::MaybeUninit::<libc::statvfs>::uninit();
        // SAFETY: fstatvfs initializes the structure only on success.
        if unsafe { libc::fstatvfs(self.file.as_raw_fd(), value.as_mut_ptr()) } != 0 {
            return Err(INVALID.into());
        }
        // SAFETY: zero return initialized the full structure.
        let value = unsafe { value.assume_init() };
        value
            .f_bavail
            .checked_mul(value.f_frsize)
            .ok_or_else(|| INVALID.into())
    }
    pub fn exclusive(&self) -> Result<Exclusive, String> {
        self.validate()?;
        // SAFETY: flock only locks the open directory; it never modifies its bytes.
        if unsafe { libc::flock(self.file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Err("Another runtime installation or rollback owns the destination.".into());
        }
        Ok(Exclusive(self.file.clone()))
    }
    pub fn disable(&self, child: &str, identity: Identity) -> Result<Directory, String> {
        self.validate()?;
        let source = self.child(child)?;
        if source.id != identity {
            return Err(INVALID.into());
        }
        let target = format!("disabled-{child}");
        let from = name(child)?;
        let to = name(&target)?;
        // SAFETY: both names are single components under this retained private parent.
        // RENAME_NOREPLACE cannot replace a foreign destination. No file is deleted.
        let code = unsafe {
            libc::renameat2(
                self.file.as_raw_fd(),
                from.as_ptr(),
                self.file.as_raw_fd(),
                to.as_ptr(),
                libc::RENAME_NOREPLACE,
            )
        };
        if code != 0 {
            return Err(
                "Runtime rollback did not replace or remove an existing destination.".into(),
            );
        }
        self.file.sync_all().map_err(|_| INVALID)?;
        let disabled = self.child(&target)?;
        if disabled.id != identity {
            return Err(INVALID.into());
        }
        self.validate()?;
        Ok(disabled)
    }
}
pub struct Exclusive(Arc<File>);
impl Drop for Exclusive {
    fn drop(&mut self) {
        // SAFETY: the guard retains its locked file for the entire operation.
        unsafe { libc::flock(self.0.as_raw_fd(), libc::LOCK_UN) };
    }
}
#[derive(Debug, Clone)]
pub struct Snapshot {
    pub directories: BTreeMap<String, Identity>,
    pub files: BTreeMap<String, FileIdentity>,
}
pub fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn roster(
    root: &Directory,
    bundle: &ValidatedBundle,
) -> Result<BTreeMap<String, Identity>, String> {
    root.validate()?;
    let mut expected: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    expected
        .entry(String::new())
        .or_default()
        .insert(bundle::MANIFEST.into());
    for path in bundle
        .directories
        .iter()
        .chain(bundle.manifest.files.iter().map(|file| &file.path))
    {
        let (parent, leaf) = path.rsplit_once('/').unwrap_or(("", path));
        expected
            .entry(parent.to_string())
            .or_default()
            .insert(leaf.to_string());
    }
    let mut observed = BTreeMap::new();
    for (path, names) in expected {
        let directory = root.at(&path, None)?;
        if directory.names()? != names {
            return Err(INVALID.into());
        }
        if !path.is_empty() {
            observed.insert(path, directory.id);
        }
    }
    Ok(observed)
}
fn transfer(
    root: &Directory,
    entry: &BundleFile,
    dirs: &BTreeMap<String, Identity>,
    prior: Option<&FileIdentity>,
    mut destination: Option<File>,
) -> Result<FileIdentity, String> {
    let mut source = root.open_file(&entry.path, Some(dirs), false)?;
    let before = FileIdentity::of(&source.metadata().map_err(|_| INVALID)?)?;
    if before.bytes != entry.bytes || prior.is_some_and(|value| *value != before) {
        return Err(INVALID.into());
    }
    let mut hasher = Sha256::new();
    let mut count = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    let mut header = Vec::with_capacity(20);
    loop {
        let n = source.read(&mut buffer).map_err(|_| INVALID)?;
        if n == 0 {
            break;
        }
        count = count.checked_add(n as u64).ok_or(INVALID)?;
        if count > entry.bytes {
            return Err(INVALID.into());
        }
        if header.len() < 20 {
            header.extend_from_slice(&buffer[..n.min(20 - header.len())]);
        }
        hasher.update(&buffer[..n]);
        if let Some(out) = destination.as_mut() {
            out.write_all(&buffer[..n]).map_err(|_| INVALID)?;
        }
    }
    if count != entry.bytes
        || format!("{:x}", hasher.finalize()) != entry.sha256
        || FileIdentity::of(&source.metadata().map_err(|_| INVALID)?)? != before
    {
        return Err(INVALID.into());
    }
    if matches!(entry.role, FileRole::Node | FileRole::Browser)
        && (header.len() < 20 || &header[..6] != b"\x7fELF\x02\x01" || header[18..20] != [0x3e, 0])
    {
        return Err(INVALID.into());
    }
    if let Some(out) = destination {
        if entry.executable {
            // SAFETY: fchmod affects only this newly created, retained output file.
            if unsafe { libc::fchmod(out.as_raw_fd(), 0o700) } != 0 {
                return Err(INVALID.into());
            }
        }
        out.sync_all().map_err(|_| INVALID)?;
    }
    Ok(before)
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HyperframesLock {
    schema: String,
    hyperframes_version: String,
    hyperframes_commit: String,
    node_version: String,
    node_sha256: String,
    browser_version: String,
    ffmpeg_version: String,
    ffprobe_version: String,
    files: Vec<HyperframesLockFile>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HyperframesLockFile {
    path: String,
    sha256: String,
    bytes: u64,
    role: String,
}
fn validate_lock(root: &Directory, bundle: &ValidatedBundle) -> Result<(), String> {
    if bundle.manifest.profile != Profile::Hyperframes {
        return Ok(());
    }
    let bytes = root.read_small(
        &bundle.role(FileRole::RuntimeLock).path,
        bundle::MAX_MANIFEST_BYTES,
    )?;
    // Typed root and file records reject duplicate keys and invented authority.
    let lock: HyperframesLock = serde_json::from_slice(&bytes).map_err(|_| INVALID)?;
    if lock.schema != "semwright.hyperframes.runtime-lock/1"
        || lock.hyperframes_version != "0.8.181"
        || lock.hyperframes_commit != "ae02e5aceff4742e5d72c20f0859eb3e6b449035"
        || lock.node_version != "v22.22.0"
        || lock.node_sha256 != bundle.role(FileRole::Node).sha256
        || [
            &lock.browser_version,
            &lock.ffmpeg_version,
            &lock.ffprobe_version,
        ]
        .iter()
        .any(|value| value.is_empty() || value.len() > 512 || value.chars().any(char::is_control))
    {
        return Err(INVALID.into());
    }
    let expected = bundle
        .manifest
        .files
        .iter()
        .filter(|file| file.path.starts_with("runtime/") && file.role != FileRole::RuntimeLock)
        .map(|file| (file.path.strip_prefix("runtime/").unwrap(), file))
        .collect::<BTreeMap<_, _>>();
    if lock.files.len() != expected.len() {
        return Err(INVALID.into());
    }
    let mut seen = BTreeSet::new();
    let mut roles = BTreeSet::new();
    for row in &lock.files {
        let file = expected.get(row.path.as_str()).ok_or(INVALID)?;
        if !seen.insert(&row.path)
            || row.sha256 != file.sha256
            || row.bytes != file.bytes
            || row.bytes == 0
        {
            return Err(INVALID.into());
        }
        let role = row.role.as_str();
        if !matches!(
            role,
            "runner"
                | "hyperframes-cli"
                | "package-lock"
                | "browser"
                | "ffmpeg"
                | "ffprobe"
                | "dependency"
        ) {
            return Err(INVALID.into());
        }
        if role != "dependency" && !roles.insert(role) {
            return Err(INVALID.into());
        }
        if (role == "runner") != (file.role == FileRole::RuntimeEntry)
            || (role == "browser") != (file.role == FileRole::Browser)
            || (matches!(role, "browser" | "ffmpeg" | "ffprobe") && !file.executable)
            || (role == "hyperframes-cli"
                && row.path != "node_modules/hyperframes/bin/hyperframes.mjs")
            || (role == "package-lock" && row.path != "package-lock.json")
        {
            return Err(INVALID.into());
        }
    }
    if roles
        != BTreeSet::from([
            "runner",
            "hyperframes-cli",
            "package-lock",
            "browser",
            "ffmpeg",
            "ffprobe",
        ])
    {
        return Err(INVALID.into());
    }
    let metadata = expected
        .get("origin/hyperframes-0.8.181.json")
        .ok_or(INVALID)?;
    if metadata.sha256 != "d3f1dd614e50191bd08391108fb17e521aded5c9a9aa0f30947672f8667ea673"
        || metadata.bytes != 3553
        || metadata.role != FileRole::Dependency
    {
        return Err(INVALID.into());
    }
    let package: serde_json::Value = serde_json::from_slice(
        &root.read_small("runtime/node_modules/hyperframes/package.json", 1024 * 1024)?,
    )
    .map_err(|_| INVALID)?;
    let npm: serde_json::Value = serde_json::from_slice(
        &root.read_small("runtime/package-lock.json", bundle::MAX_MANIFEST_BYTES)?,
    )
    .map_err(|_| INVALID)?;
    let entry = &npm["packages"]["node_modules/hyperframes"];
    if package["name"] != "hyperframes"
        || package["version"] != "0.8.181"
        || entry["version"] != "0.8.181"
        || entry["resolved"] != "https://registry.npmjs.org/hyperframes/-/hyperframes-0.8.181.tgz"
        || entry["integrity"]
            != "sha512-rBShZnCVW5xLe+6VViIv+SEAjYQ1Z9z8mt5M+XTCHoRj/eGRg5Z/yyvVNsWZZeuipoba5yhNfodOnwn94n4PPw=="
    {
        return Err(INVALID.into());
    }
    Ok(())
}
pub fn inspect(
    root: &Directory,
    bundle: &ValidatedBundle,
    manifest_sha: &str,
) -> Result<Snapshot, String> {
    let directories = roster(root, bundle)?;
    let mut files = BTreeMap::new();
    for entry in &bundle.manifest.files {
        files.insert(
            entry.path.clone(),
            transfer(root, entry, &directories, None, None)?,
        );
    }
    validate_lock(root, bundle)?;
    if sha(&root.read_small(bundle::MANIFEST, bundle::MAX_MANIFEST_BYTES)?) != manifest_sha
        || roster(root, bundle)? != directories
    {
        return Err(INVALID.into());
    }
    Ok(Snapshot { directories, files })
}
pub fn copy(
    source: &Directory,
    destination: &Directory,
    bundle: &ValidatedBundle,
    manifest: &[u8],
    snapshot: &Snapshot,
) -> Result<(), String> {
    source.validate()?;
    if roster(source, bundle)? != snapshot.directories
        || sha(&source.read_small(bundle::MANIFEST, bundle::MAX_MANIFEST_BYTES)?) != sha(manifest)
    {
        return Err(INVALID.into());
    }
    destination.write_new(bundle::MANIFEST, manifest)?;
    let mut output_directories = BTreeMap::new();
    for path in &bundle.directories {
        let (parent, leaf) = path.rsplit_once('/').unwrap_or(("", path));
        let created = destination
            .at(parent, Some(&output_directories))?
            .create_child(leaf, false)?;
        output_directories.insert(path.clone(), created.identity());
    }
    for entry in &bundle.manifest.files {
        let file = destination.open_file(&entry.path, Some(&output_directories), true)?;
        transfer(
            source,
            entry,
            &snapshot.directories,
            snapshot.files.get(&entry.path),
            Some(file),
        )?;
    }
    source.validate()?;
    if roster(source, bundle)? != snapshot.directories {
        return Err(INVALID.into());
    }
    if roster(destination, bundle)? != output_directories {
        return Err(INVALID.into());
    }
    inspect(destination, bundle, &sha(manifest))?;
    destination.file.sync_all().map_err(|_| INVALID)?;
    Ok(())
}
