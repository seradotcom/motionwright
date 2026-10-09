//! File-boundary helpers shared by the driver and its fixed native runner.
//! A digest does not grant file access: all callers first receive Host-owned roots.
use semwright_types::{Error, ErrorCode, Result};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Component, Path, PathBuf},
};

pub fn invalid(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::InvalidArgument, message)
}
pub fn hex_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub fn token(value: &str) -> Result<()> {
    if value.len() != 35
        || !value.starts_with("hf-")
        || !value.as_bytes()[3..]
            .iter()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(b))
    {
        return Err(invalid("Invalid HyperFrames logical job reference"));
    }
    Ok(())
}
pub fn root(path: &Path) -> Result<PathBuf> {
    if !path.is_absolute() {
        return Err(invalid("Owner-granted roots must be absolute"));
    }
    let m = fs::symlink_metadata(path)?;
    if m.file_type().is_symlink() || !m.is_dir() {
        return Err(invalid("Owner-granted root must be a real directory"));
    }
    fs::canonicalize(path).map_err(Into::into)
}
pub fn regular(root: &Path, relative: &str, max: u64) -> Result<(PathBuf, u64)> {
    if relative.is_empty()
        || relative.len() > 512
        || relative.contains('\\')
        || relative.chars().any(char::is_control)
    {
        return Err(invalid("Unnormalized native artifact path"));
    }
    let p = Path::new(relative);
    if p.is_absolute() {
        return Err(invalid("Artifact locator must be relative"));
    }
    let mut cursor = root.to_path_buf();
    for component in p.components() {
        let Component::Normal(component) = component else {
            return Err(invalid("Artifact locator contains traversal"));
        };
        cursor.push(component);
        if fs::symlink_metadata(&cursor)?.file_type().is_symlink() {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Native artifact traverses a symlink",
            ));
        }
    }
    let canonical = fs::canonicalize(&cursor)?;
    let m = fs::metadata(&canonical)?;
    if !canonical.starts_with(root) || !m.is_file() || m.len() == 0 || m.len() > max {
        return Err(invalid(
            "Artifact is outside its owner-granted root or byte budget",
        ));
    }
    Ok((canonical, m.len()))
}
pub fn read(root: &Path, relative: &str, max: u64) -> Result<Vec<u8>> {
    let (path, len) = regular(root, relative, max)?;
    let file = fs::File::open(&path)?;
    let mut bytes = Vec::with_capacity(len as usize);
    file.take(max + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 != len {
        return Err(invalid("Native input changed while being read"));
    }
    Ok(bytes)
}
pub fn sha(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
pub fn file_sha(path: &Path, max: u64) -> Result<String> {
    let mut file = fs::File::open(path)?;
    let mut hash = Sha256::new();
    let mut count = 0u64;
    let mut buffer = [0u8; 131072];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        count = count
            .checked_add(read as u64)
            .ok_or_else(|| invalid("File size overflow"))?;
        if count > max {
            return Err(invalid("File exceeded read budget"));
        }
        hash.update(&buffer[..read]);
    }
    Ok(hex::encode(hash.finalize()))
}
pub fn write_new(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}
pub fn atomic_json(path: &Path, value: &impl serde::Serialize) -> Result<()> {
    let temporary = path.with_extension(format!("{}.tmp", uuid::Uuid::new_v4().simple()));
    write_new(&temporary, &serde_json::to_vec(value)?)?;
    if let Ok(metadata) = fs::symlink_metadata(path) {
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            let _ = fs::remove_file(temporary);
            return Err(invalid("Refusing to replace a non-regular driver journal"));
        }
    }
    fs::rename(&temporary, path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn paths_and_digests_are_not_commands_or_grants() {
        assert!(token("hf-0123456789abcdef0123456789abcdef").is_ok());
        for v in [
            "hf-../../elsewhere",
            "hf-0123456789abcdef0123456789abcdeF",
            "",
        ] {
            assert!(token(v).is_err());
        }
        let dir = tempfile::tempdir().unwrap();
        for v in ["../outside", "/outside", "a\\b", ""] {
            assert!(regular(dir.path(), v, 100).is_err());
        }
        assert!(!hex_digest(&"Z".repeat(64)));
        assert!(hex_digest(&"ab".repeat(32)));
    }
    #[cfg(unix)]
    #[test]
    fn symlink_artifacts_are_never_opened() {
        let dir = tempfile::tempdir().unwrap();
        let outside = tempfile::NamedTempFile::new().unwrap();
        std::os::unix::fs::symlink(outside.path(), dir.path().join("artifact")).unwrap();
        assert!(regular(dir.path(), "artifact", 100).is_err());
    }
}
