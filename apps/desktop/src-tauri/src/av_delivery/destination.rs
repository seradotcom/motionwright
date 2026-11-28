use super::{File, Path, PathBuf, Uuid, validated_destination};
use std::{ffi::OsStr, fs::OpenOptions, io};

const CHANGED: &str =
    "The MP4 export destination directory changed. Select it again before exporting.";

pub(crate) struct PreparedMasterDestination {
    path: PathBuf,
    directory: same_file::Handle,
    #[cfg(windows)]
    _ancestors: Vec<File>,
}

impl PreparedMasterDestination {
    pub(crate) fn open(value: &str) -> Result<Self, String> {
        let path = validated_destination(value)?;
        let parent = path.parent().ok_or(CHANGED)?;
        #[cfg(unix)]
        let directory = unix::open_parent(parent).map_err(|_| CHANGED)?;
        #[cfg(windows)]
        let (directory, ancestors) = windows::hold_parent(parent).map_err(|_| CHANGED)?;
        let directory = same_file::Handle::from_file(directory).map_err(|_| CHANGED)?;
        let target = Self {
            path,
            directory,
            #[cfg(windows)]
            _ancestors: ancestors,
        };
        target.check_current()?;
        Ok(target)
    }

    pub(super) fn path(&self) -> &Path {
        &self.path
    }

    pub(crate) fn check_current(&self) -> Result<(), String> {
        let parent = self.path.parent().ok_or(CHANGED)?;
        #[cfg(unix)]
        let current = unix::open_parent(parent).map_err(|_| CHANGED)?;
        #[cfg(windows)]
        let current = windows::open_directory(parent).map_err(|_| CHANGED)?;
        let current = same_file::Handle::from_file(current).map_err(|_| CHANGED)?;
        if self.directory != current {
            return Err(CHANGED.into());
        }
        Ok(())
    }

    pub(super) fn stage(&self) -> Result<ExportStage<'_>, String> {
        self.check_current()?;
        #[cfg(unix)]
        let workspace = unix::Workspace::new(self.directory.as_file());
        #[cfg(windows)]
        let workspace = windows::Workspace::new(self.path.parent().ok_or(CHANGED)?);
        Ok(ExportStage {
            target: self,
            workspace: workspace.map_err(|_| "Unable to create the MP4 export staging area.")?,
        })
    }
}

pub(super) const STAGED_MEDIA: &str = "master";
pub(super) const STAGED_PROOF: &str = "integrity";

pub(super) struct ExportStage<'a> {
    target: &'a PreparedMasterDestination,
    #[cfg(unix)]
    workspace: unix::Workspace<'a>,
    #[cfg(windows)]
    workspace: windows::Workspace,
}

impl ExportStage<'_> {
    pub(super) fn create(&self, name: &'static str) -> io::Result<File> {
        #[cfg(unix)]
        let result = self.workspace.create(name);
        #[cfg(windows)]
        let result = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(self.workspace.path.join(name));
        result
    }

    pub(super) fn publish(&self, name: &'static str, destination: &Path) -> io::Result<()> {
        self.target.check_current().map_err(io::Error::other)?;
        #[cfg(unix)]
        let leaf = destination
            .file_name()
            .ok_or_else(|| io::Error::other(CHANGED))?;
        #[cfg(unix)]
        let result = self.workspace.publish(name, leaf);
        #[cfg(windows)]
        let result = windows::publish(&self.workspace.path.join(name), destination);
        result
    }
}

#[cfg(unix)]
mod unix {
    use super::*;
    use std::{
        ffi::CString,
        os::{
            fd::{AsRawFd, FromRawFd},
            unix::{
                ffi::OsStrExt,
                fs::{MetadataExt, OpenOptionsExt},
            },
        },
        path::Component,
    };

    fn c_name(name: &OsStr) -> io::Result<CString> {
        CString::new(name.as_bytes()).map_err(io::Error::other)
    }

    fn owned_fd(fd: libc::c_int) -> io::Result<File> {
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: opening syscalls return a new descriptor on success.
        Ok(unsafe { File::from_raw_fd(fd) })
    }

    fn open_directory(parent: &File, name: &std::ffi::CStr) -> io::Result<File> {
        // SAFETY: parent is live and name is a terminated single path component.
        owned_fd(unsafe {
            libc::openat(
                parent.as_raw_fd(),
                name.as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        })
    }

    pub(super) fn open_parent(parent: &Path) -> io::Result<File> {
        let mut directory = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_DIRECTORY | libc::O_CLOEXEC)
            .open("/")?;
        for entry in parent.components() {
            match entry {
                Component::RootDir => (),
                Component::Normal(name) => directory = open_directory(&directory, &c_name(name)?)?,
                _ => return Err(io::Error::other(CHANGED)),
            }
        }
        Ok(directory)
    }

    pub(super) struct Workspace<'a> {
        parent: &'a File,
        directory: File,
        name: CString,
    }

    impl<'a> Workspace<'a> {
        pub(super) fn new(parent: &'a File) -> io::Result<Self> {
            let name = CString::new(format!(
                ".motionwright-delivery-{}",
                Uuid::new_v4().simple()
            ))
            .map_err(io::Error::other)?;
            // SAFETY: parent and name are live. Exclusive creation makes a private stage.
            if unsafe { libc::mkdirat(parent.as_raw_fd(), name.as_ptr(), 0o700) } != 0 {
                return Err(io::Error::last_os_error());
            }
            let directory = open_directory(parent, &name)?;
            let workspace = Self {
                parent,
                directory,
                name,
            };
            let meta = workspace.directory.metadata()?;
            // SAFETY: geteuid reads process identity without retaining a pointer.
            if meta.uid() != unsafe { libc::geteuid() } || meta.mode() & 0o777 != 0o700 {
                return Err(io::Error::other(
                    "MP4 export staging directory is not private.",
                ));
            }
            Ok(workspace)
        }

        pub(super) fn create(&self, name: &str) -> io::Result<File> {
            let name = c_name(OsStr::new(name))?;
            // SAFETY: directory and name are live. Exclusive creation refuses existing entries.
            owned_fd(unsafe {
                libc::openat(
                    self.directory.as_raw_fd(),
                    name.as_ptr(),
                    libc::O_WRONLY
                        | libc::O_CREAT
                        | libc::O_EXCL
                        | libc::O_NOFOLLOW
                        | libc::O_CLOEXEC,
                    0o600 as libc::mode_t,
                )
            })
        }

        pub(super) fn publish(&self, source: &str, target: &OsStr) -> io::Result<()> {
            let source = c_name(OsStr::new(source))?;
            let target = c_name(target)?;
            let from = self.directory.as_raw_fd();
            let to = self.parent.as_raw_fd();
            // SAFETY: both descriptors and terminated components live through the call.
            #[cfg(target_os = "linux")]
            let result = unsafe {
                libc::renameat2(
                    from,
                    source.as_ptr(),
                    to,
                    target.as_ptr(),
                    libc::RENAME_NOREPLACE,
                )
            };
            // SAFETY: both descriptors and terminated components live through the call.
            #[cfg(target_os = "macos")]
            let result = unsafe {
                libc::renameatx_np(
                    from,
                    source.as_ptr(),
                    to,
                    target.as_ptr(),
                    libc::RENAME_EXCL,
                )
            };
            #[cfg(not(any(target_os = "linux", target_os = "macos")))]
            return Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "Exclusive MP4 publication is unavailable.",
            ));
            #[cfg(any(target_os = "linux", target_os = "macos"))]
            if result == 0 {
                Ok(())
            } else {
                Err(io::Error::last_os_error())
            }
        }
    }

    impl Drop for Workspace<'_> {
        fn drop(&mut self) {
            // SAFETY: these are constant private staging entries on the held directory.
            unsafe {
                libc::unlinkat(self.directory.as_raw_fd(), c"master".as_ptr(), 0);
                libc::unlinkat(self.directory.as_raw_fd(), c"integrity".as_ptr(), 0);
            }
            let same_directory = open_directory(self.parent, &self.name)
                .and_then(|candidate| candidate.metadata())
                .ok()
                .zip(self.directory.metadata().ok())
                .is_some_and(|(entry, held)| {
                    entry.dev() == held.dev() && entry.ino() == held.ino()
                });
            if same_directory {
                // SAFETY: the parent descriptor and generated stage name are live.
                // Only an empty directory can be removed here.
                unsafe {
                    libc::unlinkat(
                        self.parent.as_raw_fd(),
                        self.name.as_ptr(),
                        libc::AT_REMOVEDIR,
                    )
                };
            }
        }
    }
}

#[cfg(windows)]
pub(super) mod windows {
    use super::*;
    use std::os::windows::{
        ffi::OsStrExt,
        fs::{MetadataExt, OpenOptionsExt},
    };
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
        FILE_SHARE_READ, FILE_SHARE_WRITE, MOVEFILE_WRITE_THROUGH, MoveFileExW,
    };

    pub(in crate::av_delivery) fn local_drive(path: &Path) -> bool {
        use std::path::{Component, Prefix};
        use windows_sys::Win32::Storage::FileSystem::GetDriveTypeW;
        use windows_sys::Win32::System::WindowsProgramming::{
            DRIVE_FIXED, DRIVE_RAMDISK, DRIVE_REMOVABLE,
        };

        let Some(Component::Prefix(prefix)) = path.components().next() else {
            return false;
        };
        let (Prefix::Disk(letter) | Prefix::VerbatimDisk(letter)) = prefix.kind() else {
            return false;
        };
        let root = [u16::from(letter), b':' as u16, b'\\' as u16, 0];
        // SAFETY: this is a NUL-terminated drive root with no user path operands.
        matches!(
            unsafe { GetDriveTypeW(root.as_ptr()) },
            DRIVE_FIXED | DRIVE_RAMDISK | DRIVE_REMOVABLE
        )
    }

    pub(super) fn open_directory(path: &Path) -> io::Result<File> {
        let file = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
            .open(path)?;
        let meta = file.metadata()?;
        if !meta.is_dir() || meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(io::Error::other(CHANGED));
        }
        Ok(file)
    }

    pub(super) fn hold_parent(parent: &Path) -> io::Result<(File, Vec<File>)> {
        let paths = parent
            .ancestors()
            .filter(|path| path.has_root())
            .take(65)
            .collect::<Vec<_>>();
        if paths.len() > 64 {
            return Err(io::Error::other(CHANGED));
        }
        let mut dirs = paths
            .into_iter()
            .rev()
            .map(open_directory)
            .collect::<io::Result<Vec<_>>>()?;
        let directory = dirs.pop().ok_or_else(|| io::Error::other(CHANGED))?;
        Ok((directory, dirs))
    }

    pub(super) struct Workspace {
        pub(super) path: PathBuf,
        directory: Option<File>,
    }

    impl Workspace {
        pub(super) fn new(parent: &Path) -> io::Result<Self> {
            let path = parent.join(format!(
                ".motionwright-delivery-{}",
                Uuid::new_v4().simple()
            ));
            std::fs::create_dir(&path)?;
            let directory = open_directory(&path)?;
            Ok(Self {
                path,
                directory: Some(directory),
            })
        }
    }

    impl Drop for Workspace {
        fn drop(&mut self) {
            for name in [STAGED_MEDIA, STAGED_PROOF] {
                let _ = std::fs::remove_file(self.path.join(name));
            }
            drop(self.directory.take());
            // A sibling target or a foreign subtree is never recursively removed.
            let _ = std::fs::remove_dir(&self.path);
        }
    }

    pub(super) fn publish(source: &Path, destination: &Path) -> io::Result<()> {
        fn wide(path: &Path) -> Vec<u16> {
            path.as_os_str().encode_wide().chain([0]).collect()
        }
        let source = wide(source);
        let destination = wide(destination);
        // SAFETY: both private paths are NUL-terminated, validated, and live.
        // No replacement or cross-volume copy flags are set.
        if unsafe {
            MoveFileExW(
                source.as_ptr(),
                destination.as_ptr(),
                MOVEFILE_WRITE_THROUGH,
            )
        } == 0
        {
            Err(io::Error::last_os_error())
        } else {
            Ok(())
        }
    }
}
