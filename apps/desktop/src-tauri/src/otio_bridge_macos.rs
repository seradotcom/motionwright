//! Read-only admission of an administrator-provisioned OTIO runtime on macOS.
use std::{
    fs::{self, File, OpenOptions},
    io,
    os::{
        fd::AsRawFd,
        unix::fs::{MetadataExt, OpenOptionsExt},
    },
    path::{Path, PathBuf},
};

const ROOT: &str = "/Library/Application Support/Motionwright/OTIO Runtime";
const MAX_ENTRIES: usize = 24_000;
const MAX_DEPTH: usize = 24;
const MAX_BYTES: u64 = 1024 * 1024 * 1024;

pub(super) fn admit() -> Result<(PathBuf, PathBuf, PathBuf), String> {
    let root = Path::new(ROOT);
    inspect_runtime(root).map_err(|_| format!(
        "OTIO needs a protected Python 3 and ffprobe runtime installed by an administrator at {ROOT}."
    ))?;
    Ok((
        root.join("bin/python3"),
        root.join("bin/ffprobe"),
        root.to_path_buf(),
    ))
}

fn unsafe_entry() -> io::Error {
    io::Error::new(
        io::ErrorKind::PermissionDenied,
        "OTIO runtime is not a plain root-owned installation",
    )
}

fn inspect_runtime(root: &Path) -> io::Result<()> {
    let parents: Vec<_> = root.ancestors().collect();
    for directory in parents.into_iter().rev() {
        if !protected_metadata(directory)?.is_dir() {
            return Err(unsafe_entry());
        }
    }
    let mut pending = vec![(root.to_path_buf(), 0)];
    let mut entries = 0;
    let mut bytes = 0u64;
    while let Some((folder, depth)) = pending.pop() {
        for entry in fs::read_dir(folder)? {
            entries += 1;
            if entries > MAX_ENTRIES || depth >= MAX_DEPTH {
                return Err(unsafe_entry());
            }
            let path = entry?.path();
            let metadata = protected_metadata(&path)?;
            if metadata.is_dir() {
                pending.push((path, depth + 1));
            } else {
                bytes = bytes
                    .checked_add(metadata.len())
                    .filter(|&total| total <= MAX_BYTES)
                    .ok_or_else(unsafe_entry)?;
            }
        }
    }
    for program in ["bin/python3", "bin/ffprobe"] {
        let metadata = protected_metadata(&root.join(program))?;
        if !metadata.is_file() || metadata.mode() & 0o111 == 0 || metadata.mode() & 0o6000 != 0 {
            return Err(unsafe_entry());
        }
    }
    Ok(())
}

fn protected_metadata(path: &Path) -> io::Result<fs::Metadata> {
    let initial = path.symlink_metadata()?;
    if !initial.is_file() && !initial.is_dir() {
        return Err(unsafe_entry());
    }
    let kind = if initial.is_dir() {
        libc::O_DIRECTORY
    } else {
        0
    };
    let handle = OpenOptions::new()
        .read(true)
        .custom_flags(kind | libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC)
        .open(path)?;
    let metadata = handle.metadata()?;
    if metadata.uid() != 0
        || metadata.mode() & 0o022 != 0
        || metadata.dev() != initial.dev()
        || metadata.ino() != initial.ino()
        || !acl_cannot_grant_access(&handle)
    {
        return Err(unsafe_entry());
    }
    Ok(metadata)
}

pub(super) fn acl_cannot_grant_access(handle: &File) -> bool {
    // Darwin's descriptor ACL interface in libSystem (sys/acl.h).
    const ACL_TYPE_EXTENDED: libc::c_int = 0x100;
    const ACL_FIRST_ENTRY: libc::c_int = 0;
    const ACL_NEXT_ENTRY: libc::c_int = -1;
    const ACL_EXTENDED_DENY: libc::c_int = 2;

    unsafe extern "C" {
        fn acl_get_fd_np(fd: libc::c_int, kind: libc::c_int) -> *mut libc::c_void;
        fn acl_valid(acl: *mut libc::c_void) -> libc::c_int;
        fn acl_get_entry(
            acl: *mut libc::c_void,
            id: libc::c_int,
            entry: *mut *mut libc::c_void,
        ) -> libc::c_int;
        fn acl_get_tag_type(entry: *mut libc::c_void, tag: *mut libc::c_int) -> libc::c_int;
        fn acl_free(acl: *mut libc::c_void) -> libc::c_int;
    }
    // SAFETY: the descriptor is live and the Darwin functions operate on a
    // single owned ACL value without mutating the filesystem.
    unsafe {
        *libc::__error() = 0;
        let acl = acl_get_fd_np(handle.as_raw_fd(), ACL_TYPE_EXTENDED);
        if acl.is_null() {
            return io::Error::last_os_error().raw_os_error() == Some(libc::ENOENT);
        }
        let safe = acl_valid(acl) == 0 && {
            let mut index = ACL_FIRST_ENTRY;
            loop {
                let mut entry = std::ptr::null_mut();
                if acl_get_entry(acl, index, &mut entry) == -1 {
                    break io::Error::last_os_error().raw_os_error() == Some(libc::EINVAL);
                }
                // Deny entries can only restrict the already checked POSIX mode.
                let mut tag = 0;
                if entry.is_null()
                    || acl_get_tag_type(entry, &mut tag) != 0
                    || tag != ACL_EXTENDED_DENY
                {
                    break false;
                }
                index = ACL_NEXT_ENTRY;
            }
        };
        acl_free(acl) == 0 && safe
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn macos_runtime_accepts_deny_only_acl_and_refuses_mixed_acl() {
        let fixture = tempfile::tempdir().unwrap();
        let file = fixture.path().join("python3");
        fs::write(&file, b"temporary ACL fixture").unwrap();
        let handle = File::open(&file).unwrap();
        assert!(acl_cannot_grant_access(&handle));
        unsafe extern "C" {
            fn acl_from_text(text: *const libc::c_char) -> *mut libc::c_void;
            fn acl_set_fd(fd: libc::c_int, acl: *mut libc::c_void) -> libc::c_int;
            fn acl_free(acl: *mut libc::c_void) -> libc::c_int;
        }
        let deny = c"!#acl 1\ngroup::everyone::deny:delete\n";
        let mixed = c"!#acl 1\ngroup::everyone::deny:delete\ngroup::everyone::allow:write\n";
        for (text, safe) in [(deny, true), (mixed, false)] {
            // SAFETY: attach only this fixed ACL to the temporary test file.
            let acl = unsafe { acl_from_text(text.as_ptr()) };
            assert!(!acl.is_null(), "{}", io::Error::last_os_error());
            let status = unsafe { acl_set_fd(handle.as_raw_fd(), acl) };
            let error = io::Error::last_os_error();
            assert_eq!(unsafe { acl_free(acl) }, 0);
            assert_eq!(status, 0, "{error}");
            assert_eq!(acl_cannot_grant_access(&handle), safe);
        }
    }

    #[test]
    fn macos_runtime_refuses_non_plain_executables_and_writable_closure() {
        let fixture = tempfile::tempdir().unwrap();
        let directory = fixture.path().canonicalize().unwrap();
        let binary = directory.join("python3");
        fs::write(&binary, b"not an installed interpreter").unwrap();
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&binary, fs::Permissions::from_mode(0o777)).unwrap();
        assert!(protected_metadata(&binary).is_err());
        let link = directory.join("linked");
        std::os::unix::fs::symlink(&binary, &link).unwrap();
        assert!(protected_metadata(&link).is_err());
    }
}
