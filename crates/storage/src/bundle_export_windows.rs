//! Custody of an unpublished portable bundle on a local Windows ACL volume.

use std::{
    ffi::{OsStr, c_void},
    fs::{self, File, OpenOptions},
    io,
    mem::{offset_of, size_of},
    os::windows::{
        ffi::OsStrExt,
        fs::{MetadataExt, OpenOptionsExt},
        io::{AsRawHandle, FromRawHandle, OwnedHandle},
    },
    path::{Component, Path, PathBuf, Prefix},
    ptr::null_mut,
};
use uuid::Uuid;
use windows_sys::Win32::{
    Foundation::LocalFree,
    Security::{
        ACE_HEADER, ACL,
        Authorization::{
            ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
            GetSecurityInfo, SE_FILE_OBJECT,
        },
        DACL_SECURITY_INFORMATION, EqualSid, GetAce, GetSecurityDescriptorControl,
        GetSecurityDescriptorDacl, GetTokenInformation, IsValidAcl, IsValidSid, IsWellKnownSid,
        OWNER_SECURITY_INFORMATION, PSID, SE_DACL_PROTECTED, SECURITY_ATTRIBUTES, TOKEN_QUERY,
        TOKEN_USER, TokenUser, WinBuiltinAdministratorsSid, WinLocalSystemSid,
    },
    Storage::FileSystem::{
        CreateDirectoryW, DELETE, FILE_ATTRIBUTE_REPARSE_POINT, FILE_DISPOSITION_INFO,
        FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_GENERIC_READ,
        FILE_RENAME_INFO, FILE_SHARE_READ, FILE_SHARE_WRITE, FileDispositionInfo, FileRenameInfo,
        GetDriveTypeW, GetVolumeInformationByHandleW, SetFileInformationByHandle,
    },
    System::{
        SystemServices::FILE_PERSISTENT_ACLS,
        Threading::{GetCurrentProcess, OpenProcessToken},
        WindowsProgramming::{DRIVE_FIXED, DRIVE_RAMDISK, DRIVE_REMOVABLE},
    },
};

const REFUSED: &str =
    "portable bundle export needs a protected directory on a local Windows ACL volume";
type Admission<T> = Result<T, &'static str>;

pub(super) struct BundleExportStage {
    requested_parent: PathBuf,
    parent: PathBuf,
    _ancestors: Vec<File>,
    directory: File,
    path: PathBuf,
    published: bool,
}

impl BundleExportStage {
    pub(super) fn new(parent: &Path) -> io::Result<Self> {
        let requested_parent = std::path::absolute(parent)?;
        let parent = fs::canonicalize(&requested_parent)?;
        if !local_drive(&parent) {
            return Err(io::Error::other(REFUSED));
        }
        let mut ancestors = Vec::new();
        for path in parent.ancestors().filter(|path| path.has_root()) {
            if ancestors.len() == 64 {
                return Err(io::Error::other(REFUSED));
            }
            ancestors.push(path);
        }
        let ancestors = ancestors
            .into_iter()
            .rev()
            .map(|path| open_directory(path, 0))
            .collect::<io::Result<Vec<_>>>()?;
        let held_parent = ancestors.last().ok_or_else(|| io::Error::other(REFUSED))?;

        let path = parent.join(format!(".motionwright-export-{}", Uuid::new_v4().simple()));
        create_private_directory(&path, held_parent).map_err(io::Error::other)?;
        let directory = open_directory(&path, DELETE)?;
        verify_private_directory(&directory).map_err(io::Error::other)?;
        Ok(Self {
            requested_parent,
            parent,
            _ancestors: ancestors,
            directory,
            path,
            published: false,
        })
    }

    pub(super) fn path(&self) -> &Path {
        &self.path
    }

    pub(super) fn create(&self, relative: &Path) -> io::Result<File> {
        let mut directory = self.path.clone();
        for component in relative.parent().into_iter().flat_map(Path::components) {
            let Component::Normal(name) = component else {
                return Err(io::Error::other(REFUSED));
            };
            directory.push(name);
            match fs::create_dir(&directory) {
                Ok(()) => (),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => (),
                Err(error) => return Err(error),
            }
            open_directory(&directory, 0)?;
        }
        let name = relative
            .file_name()
            .ok_or_else(|| io::Error::other(REFUSED))?;
        OpenOptions::new()
            .read(true)
            .write(true)
            .share_mode(0)
            .create_new(true)
            .open(directory.join(name))
    }

    pub(super) fn publish(&mut self, destination: &OsStr) -> io::Result<()> {
        if fs::canonicalize(&self.requested_parent)? != self.parent {
            return Err(io::Error::other(REFUSED));
        }
        let target = self.parent.join(destination);
        if fs::symlink_metadata(&target).is_ok() {
            return Err(io::ErrorKind::AlreadyExists.into());
        }
        verify_private_directory(&self.directory).map_err(io::Error::other)?;
        rename_directory(&self.directory, &target).map_err(|error| {
            if fs::symlink_metadata(&target).is_ok() {
                io::ErrorKind::AlreadyExists.into()
            } else {
                error
            }
        })?;
        self.published = true;
        Ok(())
    }
}

impl Drop for BundleExportStage {
    fn drop(&mut self) {
        if self.published {
            return;
        }
        // The private directory and every ancestor are still held without
        // FILE_SHARE_DELETE. Remove the children, then retire the held object.
        if let Ok(children) = fs::read_dir(&self.path) {
            for child in children.flatten() {
                let _ = match child.file_type() {
                    Ok(kind) if kind.is_dir() && !kind.is_symlink() => {
                        fs::remove_dir_all(child.path())
                    }
                    _ => fs::remove_file(child.path()),
                };
            }
        }
        let disposition = FILE_DISPOSITION_INFO { DeleteFile: true };
        // SAFETY: the original directory handle has DELETE permission and the
        // disposition stays alive through the call.
        unsafe {
            SetFileInformationByHandle(
                self.directory.as_raw_handle(),
                FileDispositionInfo,
                (&disposition as *const FILE_DISPOSITION_INFO).cast(),
                size_of::<FILE_DISPOSITION_INFO>() as u32,
            );
        }
    }
}

pub(super) fn local_drive(path: &Path) -> bool {
    let Some(Component::Prefix(prefix)) = path.components().next() else {
        return false;
    };
    let (Prefix::Disk(letter) | Prefix::VerbatimDisk(letter)) = prefix.kind() else {
        return false;
    };
    let root = [u16::from(letter), b':' as u16, b'\\' as u16, 0];
    // SAFETY: this is a NUL-terminated local drive root.
    matches!(
        unsafe { GetDriveTypeW(root.as_ptr()) },
        DRIVE_FIXED | DRIVE_RAMDISK | DRIVE_REMOVABLE
    )
}

fn open_directory(path: &Path, additional_access: u32) -> io::Result<File> {
    let file = OpenOptions::new()
        .access_mode(FILE_GENERIC_READ | additional_access)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)?;
    let meta = file.metadata()?;
    if !meta.is_dir() || meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(io::Error::other(REFUSED));
    }
    Ok(file)
}

fn rename_directory(directory: &File, destination: &Path) -> io::Result<()> {
    let name = destination.as_os_str().encode_wide().collect::<Vec<_>>();
    if name.is_empty() || name.len() >= 32767 || name.contains(&0) {
        return Err(io::ErrorKind::InvalidInput.into());
    }
    let name_bytes = name.len() * size_of::<u16>();
    let size = (offset_of!(FILE_RENAME_INFO, FileName) + name_bytes + size_of::<u16>())
        .max(size_of::<FILE_RENAME_INFO>());
    let mut buffer = vec![0usize; size.div_ceil(size_of::<usize>())];
    let info = buffer.as_mut_ptr().cast::<FILE_RENAME_INFO>();
    // SAFETY: the aligned, zeroed buffer has space for the fixed fields, full
    // UTF-16 name and terminator. Zero flags refuse replacement. A null root
    // delegates absolute path resolution through the retained ancestor chain.
    let result = unsafe {
        (*info).FileNameLength = name_bytes as u32;
        std::ptr::copy_nonoverlapping(
            name.as_ptr(),
            std::ptr::addr_of_mut!((*info).FileName).cast(),
            name.len(),
        );
        SetFileInformationByHandle(
            directory.as_raw_handle(),
            FileRenameInfo,
            info.cast(),
            size as u32,
        )
    };
    if result == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

struct LocalAllocation(*mut c_void);
impl Drop for LocalAllocation {
    fn drop(&mut self) {
        // SAFETY: only LocalFree results are stored here, and null is accepted.
        unsafe { LocalFree(self.0) };
    }
}

fn require_acl_volume(volume: &File) -> Admission<()> {
    let mut flags = 0;
    // SAFETY: only the volume flags output was requested and is writable.
    if unsafe {
        GetVolumeInformationByHandleW(
            volume.as_raw_handle(),
            null_mut(),
            0,
            null_mut(),
            null_mut(),
            &mut flags,
            null_mut(),
            0,
        )
    } == 0
        || flags & FILE_PERSISTENT_ACLS == 0
    {
        return Err(REFUSED);
    }
    Ok(())
}

struct CurrentUserSid {
    buffer: Vec<usize>,
    offset: usize,
}

impl CurrentUserSid {
    fn read() -> Admission<Self> {
        let mut token = null_mut();
        // SAFETY: the current-process handle is borrowed and the token output is writable.
        if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0
            || token.is_null()
        {
            return Err(REFUSED);
        }
        // SAFETY: a successful OpenProcessToken returns one new owned handle.
        let token = unsafe { OwnedHandle::from_raw_handle(token) };
        let mut needed = 0u32;
        // SAFETY: a zero-size probe asks for the required TOKEN_USER buffer length.
        unsafe {
            GetTokenInformation(token.as_raw_handle(), TokenUser, null_mut(), 0, &mut needed)
        };
        let size = needed as usize;
        if !(size_of::<TOKEN_USER>()..=65536).contains(&size) {
            return Err(REFUSED);
        }
        let mut buffer = vec![0usize; size.div_ceil(size_of::<usize>())];
        let mut written = 0u32;
        // SAFETY: the aligned buffer is writable and at least needed bytes long.
        if unsafe {
            GetTokenInformation(
                token.as_raw_handle(),
                TokenUser,
                buffer.as_mut_ptr().cast(),
                needed,
                &mut written,
            )
        } == 0
            || written > needed
            || (written as usize) < size_of::<TOKEN_USER>()
        {
            return Err(REFUSED);
        }
        // SAFETY: TOKEN_USER was written at the aligned base by GetTokenInformation.
        let sid = unsafe { (*buffer.as_ptr().cast::<TOKEN_USER>()).User.Sid };
        let offset = (sid as usize)
            .checked_sub(buffer.as_ptr() as usize)
            .ok_or(REFUSED)?;
        if offset
            .checked_add(8)
            .is_none_or(|end| end > written as usize)
        {
            return Err(REFUSED);
        }
        // SAFETY: the SID header is within the returned buffer. Check the
        // complete sub-authority array before passing the pointer to another API.
        let count = unsafe { *sid.cast::<u8>().add(1) } as usize;
        if offset + 8 + count * 4 > written as usize || unsafe { IsValidSid(sid) } == 0 {
            return Err(REFUSED);
        }
        Ok(Self { buffer, offset })
    }

    fn sid(&self) -> PSID {
        // The allocation does not move and offset was checked in read.
        self.buffer
            .as_ptr()
            .cast::<u8>()
            .wrapping_add(self.offset)
            .cast_mut()
            .cast()
    }

    fn private_descriptor(&self) -> Admission<LocalAllocation> {
        let mut text = null_mut();
        // SAFETY: the SID buffer is retained and the output pointer is writable.
        let result = unsafe { ConvertSidToStringSidW(self.sid(), &mut text) };
        let _text_allocation = LocalAllocation(text.cast());
        if result == 0 || text.is_null() {
            return Err(REFUSED);
        }
        // SAFETY: successful conversion returns a NUL-terminated string.
        let len = (0..192)
            .find(|index| unsafe { *text.add(*index) } == 0)
            .ok_or(REFUSED)?;
        let sid = String::from_utf16(unsafe { std::slice::from_raw_parts(text, len) })
            .map_err(|_| REFUSED)?;
        if !sid.starts_with("S-") || !sid[2..].bytes().all(|b| b.is_ascii_digit() || b == b'-') {
            return Err(REFUSED);
        }
        // The protected DACL is applied at creation and inherited by files and directories.
        let sddl: Vec<_> = format!("D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{sid})")
            .encode_utf16()
            .chain([0])
            .collect();
        let mut descriptor = LocalAllocation(null_mut());
        // SAFETY: SDDL is terminated and descriptor receives a LocalFree allocation.
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl.as_ptr(),
                1,
                &mut descriptor.0,
                null_mut(),
            )
        } == 0
            || descriptor.0.is_null()
        {
            return Err(REFUSED);
        }
        Ok(descriptor)
    }
}

fn create_private_directory(root: &Path, parent: &File) -> Admission<()> {
    require_acl_volume(parent).map_err(|_| REFUSED)?;
    let descriptor = CurrentUserSid::read()?.private_descriptor()?;
    let attributes = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor.0,
        bInheritHandle: 0,
    };
    let path: Vec<_> = root.as_os_str().encode_wide().chain([0]).collect();
    // SAFETY: the absolute local path is terminated and the security
    // descriptor lives through exclusive directory creation.
    if unsafe { CreateDirectoryW(path.as_ptr(), &attributes) } == 0 {
        return Err(REFUSED);
    }
    Ok(())
}

/// Inspect the actual held directory before storing any bytes in it.
fn verify_private_directory(directory: &File) -> Admission<()> {
    let user = CurrentUserSid::read()?;
    let expected = user.private_descriptor()?;
    let mut owner = null_mut();
    let mut actual_acl: *mut ACL = null_mut();
    let mut actual = LocalAllocation(null_mut());
    let mut control = 0;
    let mut revision = 0;
    let mut present = 0;
    let mut defaulted = 0;
    let mut expected_acl: *mut ACL = null_mut();
    // SAFETY: both descriptors own their ACLs for the entire comparison. All
    // output pointers are initialized, live, and sized to the Win32 API.
    unsafe {
        if GetSecurityInfo(
            directory.as_raw_handle(),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &mut owner,
            null_mut(),
            &mut actual_acl,
            null_mut(),
            &mut actual.0,
        ) != 0
            || owner.is_null()
            || IsValidSid(owner) == 0
            || (EqualSid(owner, user.sid()) == 0
                && IsWellKnownSid(owner, WinLocalSystemSid) == 0
                && IsWellKnownSid(owner, WinBuiltinAdministratorsSid) == 0)
            || actual.0.is_null()
            || GetSecurityDescriptorControl(actual.0, &mut control, &mut revision) == 0
            || control & SE_DACL_PROTECTED == 0
            || GetSecurityDescriptorDacl(
                expected.0,
                &mut present,
                &mut expected_acl,
                &mut defaulted,
            ) == 0
            || present == 0
            || actual_acl.is_null()
            || expected_acl.is_null()
            || IsValidAcl(actual_acl) == 0
            || IsValidAcl(expected_acl) == 0
            || (*actual_acl).AceCount != 2
            || (*expected_acl).AceCount != 2
        {
            return Err(REFUSED);
        }
        // Compare each ACE rather than allocated ACL capacity. Rights,
        // inheritance and identities must all match the protected descriptor.
        for index in 0..2 {
            let mut actual_ace: *mut c_void = null_mut();
            let mut expected_ace: *mut c_void = null_mut();
            if GetAce(actual_acl, index, &mut actual_ace) == 0
                || GetAce(expected_acl, index, &mut expected_ace) == 0
                || actual_ace.is_null()
                || expected_ace.is_null()
            {
                return Err(REFUSED);
            }
            let size = (*actual_ace.cast::<ACE_HEADER>()).AceSize;
            if size != (*expected_ace.cast::<ACE_HEADER>()).AceSize
                || std::slice::from_raw_parts(actual_ace.cast::<u8>(), usize::from(size))
                    != std::slice::from_raw_parts(expected_ace.cast::<u8>(), usize::from(size))
            {
                return Err(REFUSED);
            }
        }
    }
    Ok(())
}
