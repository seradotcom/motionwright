//! Custody of an unpublished portable bundle on Linux and macOS.

use std::{
    ffi::OsStr,
    fs::{self, File},
    io,
    os::unix::{ffi::OsStrExt, fs::MetadataExt},
    path::{Component, Path, PathBuf},
};

use rustix::fs::{
    AtFlags, Dir, Mode, OFlags, RenameFlags, mkdirat, openat, renameat_with, unlinkat,
};
use uuid::Uuid;

const CHANGED: &str = "portable bundle destination or staging directory changed";

pub(super) struct BundleExportStage {
    requested_parent: PathBuf,
    parent: File,
    directory: File,
    name: String,
    path: PathBuf,
    published: bool,
}

impl BundleExportStage {
    pub(super) fn new(parent: &Path) -> io::Result<Self> {
        let requested_parent = std::path::absolute(parent)?;
        let path = fs::canonicalize(&requested_parent)?;
        let parent = open_absolute_directory(&path)?;
        // A directory rename is name-based on Unix. The source entry must be
        // protected against removal or replacement by other OS principals.
        if !protects_stage_entry(&parent)? {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "portable bundle export needs a destination directory with protected entries",
            ));
        }

        let name = format!(".motionwright-export-{}", Uuid::new_v4().simple());
        let path = path.join(&name);
        mkdirat(&parent, name.as_str(), Mode::from_raw_mode(0o700))?;
        let directory = open_directory(&parent, OsStr::new(&name))?;
        let meta = directory.metadata()?;
        if meta.uid() != rustix::process::geteuid().as_raw()
            || meta.mode() & 0o777 != 0o700
            || !acl_cannot_grant_access(&directory)
        {
            // No content has been written into this directory.
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "portable bundle staging directory is not private",
            ));
        }
        Ok(Self {
            requested_parent,
            parent,
            directory,
            name,
            path,
            published: false,
        })
    }

    pub(super) fn path(&self) -> &Path {
        &self.path
    }

    pub(super) fn create(&self, relative: &Path) -> io::Result<File> {
        let file_name = relative
            .file_name()
            .ok_or_else(|| io::Error::other(CHANGED))?;
        let mut directory = self.directory.try_clone()?;
        for component in relative.parent().into_iter().flat_map(Path::components) {
            let Component::Normal(name) = component else {
                return Err(io::Error::other(CHANGED));
            };
            match mkdirat(&directory, name, Mode::from_raw_mode(0o700)) {
                Ok(()) | Err(rustix::io::Errno::EXIST) => {}
                Err(error) => return Err(error.into()),
            }
            directory = open_directory(&directory, name)?;
        }
        let flags =
            OFlags::RDWR | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC;
        Ok(openat(&directory, file_name, flags, Mode::from_raw_mode(0o600))?.into())
    }

    pub(super) fn publish(&mut self, destination: &OsStr) -> io::Result<()> {
        let visible_parent = open_absolute_directory(&fs::canonicalize(&self.requested_parent)?)?;
        if !same_directory(&visible_parent, &self.parent)?
            || !self.source_is_current()
            || !protects_stage_entry(&self.parent)?
        {
            return Err(io::Error::other(CHANGED));
        }
        renameat_with(
            &self.parent,
            self.name.as_str(),
            &self.parent,
            destination,
            RenameFlags::NOREPLACE,
        )?;
        self.published = true;
        Ok(())
    }

    fn source_is_current(&self) -> bool {
        open_directory(&self.parent, OsStr::new(&self.name))
            .and_then(|visible| same_directory(&visible, &self.directory))
            .unwrap_or(false)
    }
}

impl Drop for BundleExportStage {
    fn drop(&mut self) {
        if !self.published {
            let _ = clear_directory(&self.directory, 3);
            if protects_stage_entry(&self.parent).unwrap_or(false) && self.source_is_current() {
                // Removing an empty directory here never follows a name into its contents.
                let _ = unlinkat(&self.parent, self.name.as_str(), AtFlags::REMOVEDIR);
            }
        }
    }
}

fn open_absolute_directory(path: &Path) -> io::Result<File> {
    let mut directory = File::open("/")?;
    for component in path.components() {
        match component {
            Component::RootDir => (),
            Component::Normal(name) => directory = open_directory(&directory, name)?,
            _ => return Err(io::Error::other(CHANGED)),
        }
    }
    Ok(directory)
}

fn open_directory(parent: &File, name: &OsStr) -> io::Result<File> {
    let flags = OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC;
    Ok(openat(parent, name, flags, Mode::empty())?.into())
}

fn same_directory(left: &File, right: &File) -> io::Result<bool> {
    let left = left.metadata()?;
    let right = right.metadata()?;
    Ok(left.dev() == right.dev() && left.ino() == right.ino())
}

fn protects_stage_entry(parent: &File) -> io::Result<bool> {
    let meta = parent.metadata()?;
    let uid = rustix::process::geteuid().as_raw();
    Ok((meta.uid() == uid || meta.uid() == 0)
        && (meta.mode() & 0o022 == 0 || meta.mode() & 0o1000 != 0)
        && acl_cannot_grant_access(parent))
}

// Clear the private tree via retained directories. The bundle has three
// subdirectory levels. Opening directories with NOFOLLOW keeps links out of
// traversal; unlinkat can remove the link itself without touching its target.
fn clear_directory(directory: &File, depth: usize) -> io::Result<()> {
    for entry in Dir::read_from(directory)? {
        let entry = entry?;
        let name = entry.file_name();
        if matches!(name.to_bytes(), b"." | b"..") {
            continue;
        }
        match open_directory(directory, OsStr::from_bytes(name.to_bytes())) {
            Ok(child) if depth > 0 => {
                clear_directory(&child, depth - 1)?;
                unlinkat(directory, name, AtFlags::REMOVEDIR)?;
            }
            Ok(_) => return Err(io::Error::other(CHANGED)),
            Err(_) => unlinkat(directory, name, AtFlags::empty())?,
        }
    }
    Ok(())
}

#[cfg(target_os = "linux")]
fn acl_cannot_grant_access(_: &File) -> bool {
    // Linux access ACLs are constrained by the group bits of st_mode.
    true
}

#[cfg(target_os = "macos")]
fn acl_cannot_grant_access(handle: &File) -> bool {
    use std::os::fd::AsRawFd;

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
