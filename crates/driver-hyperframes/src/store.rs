//! Exact source/runtime files under existing Host-granted mounts.
use crate::{
    Error, ErrorCode, Result,
    model::{self, CompiledSource, SourceInventory},
};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeSet,
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};

fn failed(message: &str) -> Error {
    Error::new(ErrorCode::Unavailable, message)
}
pub fn regular(path: &Path, max: u64) -> Result<Vec<u8>> {
    let before =
        fs::symlink_metadata(path).map_err(|_| failed("Required native original is absent"))?;
    if !before.is_file()
        || before.file_type().is_symlink()
        || before.len() > max
        || path
            .canonicalize()
            .map_err(|_| failed("Native original is not canonical"))?
            != path
    {
        return Err(Error::invalid(
            "Native original is not a bounded canonical regular file",
        ));
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let mut file = options
        .open(path)
        .map_err(|_| failed("Native original could not be opened"))?;
    let opened = file
        .metadata()
        .map_err(|_| failed("Native original descriptor unavailable"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if before.nlink() != 1 || before.dev() != opened.dev() || before.ino() != opened.ino() {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Native original descriptor differs from its name",
            ));
        }
    }
    let mut bytes = Vec::new();
    Read::by_ref(&mut file)
        .take(max + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| failed("Native original read failed"))?;
    let after = fs::symlink_metadata(path).map_err(|_| failed("Native original disappeared"))?;
    let finished = file
        .metadata()
        .map_err(|_| failed("Native original descriptor changed"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let identity = |m: &fs::Metadata| {
            (
                m.dev(),
                m.ino(),
                m.nlink(),
                m.len(),
                m.mtime(),
                m.mtime_nsec(),
                m.ctime(),
                m.ctime_nsec(),
            )
        };
        if identity(&before) != identity(&opened)
            || identity(&before) != identity(&after)
            || identity(&before) != identity(&finished)
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Native original changed during descriptor capture",
            ));
        }
    }
    if bytes.len() as u64 != before.len()
        || bytes.len() as u64 > max
        || before.len() != after.len()
        || before.modified().ok() != after.modified().ok()
        || after.file_type().is_symlink()
        || path
            .canonicalize()
            .map_err(|_| failed("Native original namespace changed"))?
            != path
    {
        return Err(Error::new(
            ErrorCode::Conflict,
            "Native original changed during capture",
        ));
    }
    Ok(bytes)
}
pub fn fresh_bytes(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| Error::invalid("Native output parent missing"))?;
    if parent
        .canonicalize()
        .map_err(|_| failed("Native output parent absent"))?
        != parent
    {
        return Err(Error::invalid("Native output parent is not canonical"));
    }
    let mut opts = OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let mut out = opts.open(path).map_err(|_| {
        Error::new(
            ErrorCode::Conflict,
            "Native output already exists or cannot be reserved",
        )
    })?;
    out.write_all(bytes)
        .and_then(|_| out.sync_all())
        .map_err(|_| {
            failed("Native output write failed; original reservation retained").uncertain()
        })?;
    if regular(path, bytes.len() as u64).map_err(|error| error.uncertain())? != bytes {
        return Err(failed("Native original write readback differs").uncertain());
    }
    Ok(())
}
pub fn fresh_json(path: &Path, value: &impl Serialize) -> Result<()> {
    let mut bytes = serde_json::to_vec_pretty(value)?;
    bytes.push(b'\n');
    fresh_bytes(path, &bytes)
}
pub fn canonical_root(root: PathBuf) -> Result<PathBuf> {
    if !root.is_absolute()
        || !root.is_dir()
        || root
            .canonicalize()
            .map_err(|_| failed("Native mount unavailable"))?
            != root
    {
        return Err(failed(
            "Native grant must be a canonical existing directory",
        ));
    }
    Ok(root)
}
pub fn relative(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 1024
        && !value.starts_with('/')
        && !value.contains('\\')
        && value.split('/').all(|part| {
            !part.is_empty()
                && part != "."
                && part != ".."
                && part
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"._@+-".contains(&b))
                && !matches!(
                    part,
                    ".env" | ".npmrc" | ".netrc" | ".git" | "config" | "credentials" | "secrets"
                )
                && !part.ends_with(".session")
                && !part.ends_with(".pem")
                && !part.ends_with(".key")
        })
}
#[derive(Clone)]
pub struct Store {
    pub root: PathBuf,
}
impl Store {
    pub fn new(root: PathBuf) -> Result<Self> {
        Ok(Self {
            root: canonical_root(root)?,
        })
    }
    pub fn bundle(&self, fingerprint: &str) -> Result<PathBuf> {
        if !model::sha_valid(fingerprint) {
            return Err(Error::invalid("HyperFrames fingerprint invalid"));
        }
        Ok(self.root.join(format!("source-{fingerprint}")))
    }
    pub fn inspect(&self) -> Result<SourceInventory> {
        let value: SourceInventory = serde_json::from_slice(&regular(
            &self.root.join("semwright-hyperframes-head.json"),
            512 * 1024,
        )?)?;
        let compiled = model::compile(&value.composition)?;
        if value != compiled.inventory() {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Native source head is inconsistent",
            ));
        }
        let dir = self.bundle(&value.fingerprint)?;
        if regular(
            &dir.join("semwright-hyperframes.json"),
            model::MAX_SOURCE_BYTES as u64,
        )? != compiled.source
            || regular(&dir.join("index.html"), model::MAX_SOURCE_BYTES as u64)? != compiled.html
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Native HTML or source was edited outside its managed profile; original is retained",
            ));
        }
        Ok(value)
    }
    pub fn current(&self) -> Result<Option<SourceInventory>> {
        if !self
            .root
            .join("semwright-hyperframes-head.json")
            .try_exists()
            .map_err(|_| failed("Native head existence unknown"))?
        {
            return Ok(None);
        }
        self.inspect().map(Some)
    }
    pub fn apply(
        &self,
        source: &CompiledSource,
        expected: Option<&str>,
    ) -> Result<SourceInventory> {
        if !cfg!(target_os = "linux") {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Initial HyperFrames provider profile requires Linux Host",
            ));
        }
        // A crashed writer leaves this reservation for original-owner reconciliation.
        // It is never removed by a later attempted mutation.
        let lock = self.root.join("semwright-hyperframes-write-reservation");
        fresh_bytes(&lock, source.fingerprint.as_bytes())?;
        let mut effect_started = false;
        let result = (|| {
            let current = self.current()?;
            if current.as_ref().map(|s| s.fingerprint.as_str()) != expected {
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "HyperFrames expected source changed",
                ));
            }
            if current
                .as_ref()
                .is_some_and(|s| s.fingerprint == source.fingerprint)
            {
                return Ok(source.inventory());
            }
            let bundle = self.bundle(&source.fingerprint)?;
            fs::create_dir(&bundle).map_err(|_| {
                Error::new(
                    ErrorCode::Conflict,
                    "Native source bundle already exists; reconcile the original apply",
                )
            })?;
            effect_started = true;
            fresh_bytes(&bundle.join("semwright-hyperframes.json"), &source.source)?;
            fresh_bytes(&bundle.join("index.html"), &source.html)?;
            let inventory = source.inventory();
            fresh_json(&bundle.join("source-inventory.json"), &inventory)?;
            let pending = self
                .root
                .join(format!("head-{}.pending", source.fingerprint));
            fresh_json(&pending, &inventory)?;
            if self.current()?.as_ref().map(|s| s.fingerprint.as_str()) != expected {
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "Native source head changed during apply",
                ));
            }
            fs::rename(&pending, self.root.join("semwright-hyperframes-head.json"))
                .map_err(|_| failed("Native source head publication failed"))?;
            fs::File::open(&self.root)
                .and_then(|f| f.sync_all())
                .map_err(|_| failed("Native source directory sync failed"))?;
            self.inspect()
        })();
        // Only this synchronous, observed writer releases its own reservation.
        fs::remove_file(&lock)
            .map_err(|_| failed("Native source writer reservation cleanup failed").uncertain())?;
        if effect_started {
            result.map_err(Error::uncertain)
        } else {
            result
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "kebab-case")]
pub enum RuntimeRole {
    Runner,
    HyperframesCli,
    PackageLock,
    Browser,
    Ffmpeg,
    Ffprobe,
    Dependency,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RuntimeFile {
    pub path: String,
    pub sha256: String,
    pub bytes: u64,
    pub role: RuntimeRole,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RuntimeLock {
    pub schema: String,
    pub hyperframes_version: String,
    pub hyperframes_commit: String,
    pub node_version: String,
    pub node_sha256: String,
    pub browser_version: String,
    pub ffmpeg_version: String,
    pub ffprobe_version: String,
    pub files: Vec<RuntimeFile>,
}
impl RuntimeLock {
    pub fn validate(&self) -> Result<()> {
        if self.schema != "semwright.hyperframes.runtime-lock/1"
            || self.hyperframes_version != model::HYPERFRAMES_VERSION
            || self.hyperframes_commit != model::HYPERFRAMES_COMMIT
            || !model::sha_valid(&self.node_sha256)
            || !self.node_version.starts_with('v')
            || self.node_version.len() > 32
            || self.node_version[1..]
                .split('.')
                .next()
                .and_then(|s| s.parse::<u32>().ok())
                .is_none_or(|v| v < 22)
            || self.files.is_empty()
            || self.files.len() > 30000
        {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "HyperFrames runtime lock is not the admitted exact dependency profile",
            ));
        }
        for value in [
            &self.browser_version,
            &self.ffmpeg_version,
            &self.ffprobe_version,
        ] {
            if value.is_empty() || value.len() > 512 || value.chars().any(char::is_control) {
                return Err(Error::invalid("Runtime version identity exceeds bounds"));
            }
        }
        let mut paths = BTreeSet::new();
        let mut total = 0u64;
        for file in &self.files {
            if !relative(&file.path)
                || file.path == "runtime-lock.json"
                || !paths.insert(&file.path)
                || !model::sha_valid(&file.sha256)
                || file.bytes == 0
                || file.bytes > 512 * 1024 * 1024
            {
                return Err(Error::invalid(
                    "Runtime dependency inventory is not a closed regular-file list",
                ));
            }
            total = total
                .checked_add(file.bytes)
                .ok_or_else(|| Error::invalid("Runtime byte count overflow"))?;
        }
        if total > 2 * 1024 * 1024 * 1024 {
            return Err(Error::invalid("Runtime closure exceeds byte budget"));
        }
        for role in [
            RuntimeRole::Runner,
            RuntimeRole::HyperframesCli,
            RuntimeRole::PackageLock,
            RuntimeRole::Browser,
            RuntimeRole::Ffmpeg,
            RuntimeRole::Ffprobe,
        ] {
            if self.files.iter().filter(|f| f.role == role).count() != 1 {
                return Err(Error::invalid(
                    "Required runtime role is missing or ambiguous",
                ));
            }
        }
        if self.role(RuntimeRole::Runner)?.path != "semwright-hyperframes-runner.mjs"
            || self.role(RuntimeRole::HyperframesCli)?.path
                != "node_modules/hyperframes/bin/hyperframes.mjs"
            || self.role(RuntimeRole::PackageLock)?.path != "package-lock.json"
            || !self.files.iter().any(|f| {
                f.path == "origin/hyperframes-0.8.181.json"
                    && f.bytes == 3553
                    && f.sha256 == model::NPM_METADATA_SHA256
                    && f.role == RuntimeRole::Dependency
            })
            || !self.files.iter().any(|f| {
                f.path == "node_modules/hyperframes/package.json"
                    && f.bytes <= 1024 * 1024
                    && f.role == RuntimeRole::Dependency
            })
        {
            return Err(Error::invalid(
                "Runtime entrypoint differs from its pinned closed contract",
            ));
        }
        Ok(())
    }
    pub fn role(&self, role: RuntimeRole) -> Result<&RuntimeFile> {
        self.files
            .iter()
            .find(|f| f.role == role)
            .ok_or_else(|| Error::invalid("Runtime role absent"))
    }
}
pub fn runtime(root: &Path, expected: &str) -> Result<RuntimeLock> {
    if !model::sha_valid(expected) {
        return Err(Error::invalid("Runtime lock digest is not an admitted pin"));
    }
    let bytes = regular(&root.join("runtime-lock.json"), 8 * 1024 * 1024)?;
    if model::sha(&bytes) != expected {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Exact admitted HyperFrames runtime lock is absent",
        ));
    }
    let lock: RuntimeLock = serde_json::from_slice(&bytes)?;
    lock.validate()?;
    // Enumerate only the dedicated, predeclared runtime closure. Unexpected
    // names are rejected before stat/open/hash; no owner tree is traversed.
    let files = lock
        .files
        .iter()
        .map(|f| f.path.as_str())
        .collect::<BTreeSet<_>>();
    let mut directories = BTreeSet::new();
    directories.insert(String::new());
    for file in &lock.files {
        let mut parent = Path::new(&file.path).parent();
        while let Some(path) = parent {
            let relative = path
                .to_str()
                .ok_or_else(|| Error::invalid("Runtime path encoding invalid"))?;
            directories.insert(relative.to_owned());
            parent = path.parent();
        }
    }
    for relative in &directories {
        let directory = root.join(relative);
        if directory
            .canonicalize()
            .map_err(|_| failed("Runtime directory is absent"))?
            != directory
        {
            return Err(Error::invalid(
                "Runtime directory escaped its admitted closure",
            ));
        }
        let entries =
            fs::read_dir(&directory).map_err(|_| failed("Runtime namespace cannot be observed"))?;
        let mut count = 0;
        for entry in entries {
            count += 1;
            if count > 30001 {
                return Err(Error::invalid("Runtime namespace budget exceeded"));
            }
            let entry = entry.map_err(|_| failed("Runtime namespace entry unreadable"))?;
            let name = entry
                .file_name()
                .into_string()
                .map_err(|_| Error::invalid("Runtime namespace encoding invalid"))?;
            let path = if relative.is_empty() {
                name
            } else {
                format!("{relative}/{name}")
            };
            if path != "runtime-lock.json"
                && !files.contains(path.as_str())
                && !directories.contains(&path)
            {
                return Err(Error::new(
                    ErrorCode::Unsupported,
                    "Unlisted runtime content exists; no native execution allowed",
                ));
            }
        }
    }
    for file in &lock.files {
        let bytes = regular(&root.join(&file.path), file.bytes)?;
        if bytes.len() as u64 != file.bytes || model::sha(&bytes) != file.sha256 {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Pinned HyperFrames dependency changed; no native job was started",
            ));
        }
    }
    let packages: serde_json::Value =
        serde_json::from_slice(&regular(&root.join("package-lock.json"), 8 * 1024 * 1024)?)?;
    let cli = packages
        .pointer("/packages/node_modules~1hyperframes")
        .ok_or_else(|| Error::invalid("Exact published CLI is absent from package lock"))?;
    if cli.get("version").and_then(serde_json::Value::as_str) != Some(model::HYPERFRAMES_VERSION)
        || cli.get("resolved").and_then(serde_json::Value::as_str)
            != Some("https://registry.npmjs.org/hyperframes/-/hyperframes-0.8.181.tgz")
        || cli.get("integrity").and_then(serde_json::Value::as_str) != Some(model::NPM_INTEGRITY)
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Package lock is not the exact public HyperFrames distribution",
        ));
    }
    Ok(lock)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_source_roundtrip_preserves_original_and_refuses_unmanaged_html_changes() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::new(dir.path().to_path_buf()).unwrap();
        let original = model::compile(&crate::fixture()).unwrap();
        let first = store.apply(&original, None).unwrap();
        assert_eq!(store.inspect().unwrap(), first);
        let mut changed = original.composition.clone();
        changed.binding.revision += 1;
        changed.clips[0].fill = "#00AA66".into();
        let compiled = model::compile(&changed).unwrap();
        store.apply(&compiled, Some(&first.fingerprint)).unwrap();
        assert_eq!(
            regular(
                &store.bundle(&first.fingerprint).unwrap().join("index.html"),
                256 * 1024
            )
            .unwrap(),
            original.html
        );
        fs::write(
            store
                .bundle(&compiled.fingerprint)
                .unwrap()
                .join("index.html"),
            b"<p>external edit retained</p>",
        )
        .unwrap();
        assert!(store.inspect().is_err());
        assert!(store.apply(&original, Some(&compiled.fingerprint)).is_err());
    }
    #[test]
    fn missing_or_unpinned_dependency_is_read_only_rejection() {
        let dir = tempfile::tempdir().unwrap();
        assert!(runtime(dir.path(), &"0".repeat(64)).is_err());
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 0);
    }
    #[test]
    fn secret_and_traversal_paths_are_rejected_before_dependency_reads() {
        for path in [
            "../secret",
            "/etc/passwd",
            "a/../../b",
            "runtime/motionwright.session",
            ".npmrc",
            "a/.env",
            "secret.pem",
            "x\\y",
        ] {
            assert!(!relative(path), "{path}");
        }
    }
}
