//! Admission for an administrator-provisioned Windows OTIO runtime.
//! windows-sys 0.61.2 features: Win32_Foundation, Win32_Security_Authorization,
//! Win32_Storage_FileSystem, Win32_System_Com, Win32_System_SystemInformation,
//! Win32_System_SystemServices, Win32_System_WindowsProgramming, Win32_UI_Shell.

use std::{
    ffi::{OsString, c_void},
    fs::{self, File, OpenOptions},
    io::Read,
    os::windows::{
        ffi::{OsStrExt, OsStringExt},
        fs::OpenOptionsExt,
        io::AsRawHandle,
    },
    path::{Component, Path, PathBuf, Prefix},
    ptr::null_mut,
};
use windows_sys::Win32::{
    Foundation::{GENERIC_ALL, GENERIC_WRITE, LocalFree},
    Security::{
        ACE_HEADER, ACL, ACL_REVISION, ACL_REVISION_DS,
        Authorization::{ConvertStringSidToSidW, GetSecurityInfo, SE_FILE_OBJECT},
        DACL_SECURITY_INFORMATION, EqualSid, GetAce, INHERIT_ONLY_ACE, IsValidAcl, IsValidSid,
        IsWellKnownSid, OWNER_SECURITY_INFORMATION, PSID, WinBuiltinAdministratorsSid,
        WinLocalSystemSid,
    },
    Storage::FileSystem::{
        BY_HANDLE_FILE_INFORMATION, DELETE, FILE_APPEND_DATA, FILE_ATTRIBUTE_DIRECTORY,
        FILE_ATTRIBUTE_REPARSE_POINT, FILE_DELETE_CHILD, FILE_FLAG_BACKUP_SEMANTICS,
        FILE_FLAG_OPEN_REPARSE_POINT, FILE_READ_ATTRIBUTES, FILE_READ_DATA, FILE_SHARE_READ,
        FILE_SHARE_WRITE, FILE_WRITE_ATTRIBUTES, FILE_WRITE_DATA, FILE_WRITE_EA, GetDriveTypeW,
        GetFileInformationByHandle, GetVolumeInformationByHandleW, READ_CONTROL, WRITE_DAC,
        WRITE_OWNER,
    },
    System::{
        Com::CoTaskMemFree,
        SystemInformation::GetSystemWindowsDirectoryW,
        SystemServices::{ACCESS_ALLOWED_ACE_TYPE, ACCESS_DENIED_ACE_TYPE, FILE_PERSISTENT_ACLS},
        WindowsProgramming::DRIVE_FIXED,
    },
    UI::Shell::{FOLDERID_ProgramFiles, KF_FLAG_DEFAULT_PATH, SHGetKnownFolderPath},
};

const REFUSED: &str =
    "The machine-managed Windows OTIO runtime is missing or could not be verified.";
type Admission<T> = Result<T, &'static str>;
const MAX_PATH: usize = 1024;
const MAX_DEPTH: usize = 16;
const MAX_ENTRIES: usize = 2048;
const MAX_RUNTIME_BYTES: u64 = 1024 * 1024 * 1024;

#[derive(Debug)]
pub(super) struct WindowsOtioRuntime {
    pub(super) cwd: PathBuf,
    pub(super) python: PathBuf,
    pub(super) ffprobe: PathBuf,
    pub(super) system_root: PathBuf,
    // Hold the namespace against replacement throughout the supervised launch.
    _entries: Vec<File>,
}

struct LocalAllocation(*mut c_void);
impl Drop for LocalAllocation {
    fn drop(&mut self) {
        // SAFETY: only LocalFree results are stored here, and null is accepted.
        unsafe { LocalFree(self.0) };
    }
}

struct ShellAllocation(*mut u16);
impl Drop for ShellAllocation {
    fn drop(&mut self) {
        // SAFETY: releases the Shell API's allocation, including null.
        unsafe { CoTaskMemFree(self.0.cast()) };
    }
}

fn program_files() -> Admission<PathBuf> {
    let mut path = ShellAllocation(null_mut());
    // SAFETY: the GUID and out pointer live through the call. The known folder
    // selects Program Files for the calling process architecture.
    let result = unsafe {
        SHGetKnownFolderPath(
            &FOLDERID_ProgramFiles,
            KF_FLAG_DEFAULT_PATH as u32,
            null_mut(),
            &mut path.0,
        )
    };
    if result < 0 || path.0.is_null() {
        return Err(REFUSED);
    }
    for length in 0..MAX_PATH {
        // SAFETY: the API returns a NUL-terminated UTF-16 allocation.
        if unsafe { *path.0.add(length) } == 0 {
            let units = unsafe { std::slice::from_raw_parts(path.0, length) };
            return Ok(OsString::from_wide(units).into());
        }
    }
    Err(REFUSED)
}

fn system_windows_directory() -> Admission<PathBuf> {
    let mut units = [0; MAX_PATH];
    // SAFETY: buffer capacity is the provided number of UTF-16 units.
    let length =
        unsafe { GetSystemWindowsDirectoryW(units.as_mut_ptr(), MAX_PATH as u32) } as usize;
    if length == 0 || length >= MAX_PATH {
        return Err(REFUSED);
    }
    Ok(OsString::from_wide(&units[..length]).into())
}

fn trusted_installer_sid() -> Admission<LocalAllocation> {
    let input: Vec<_> = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
        .encode_utf16()
        .chain([0])
        .collect();
    let mut sid = LocalAllocation(null_mut());
    // SAFETY: legal NUL-terminated SID string and writable out pointer.
    if unsafe { ConvertStringSidToSidW(input.as_ptr(), &mut sid.0) } == 0 || sid.0.is_null() {
        return Err(REFUSED);
    }
    Ok(sid)
}

unsafe fn trusted_sid(sid: PSID, installer: &LocalAllocation) -> bool {
    // SAFETY: the caller checked the live SID, and installer owns a valid SID.
    unsafe {
        IsWellKnownSid(sid, WinLocalSystemSid) != 0
            || IsWellKnownSid(sid, WinBuiltinAdministratorsSid) != 0
            || EqualSid(sid, installer.0) != 0
    }
}

#[derive(Clone, Copy)]
enum Scope {
    Ancestor,
    Runtime,
}

fn verify_acl(file: &File, installer: &LocalAllocation, scope: Scope) -> Admission<()> {
    let mut owner = null_mut();
    let mut acl: *mut ACL = null_mut();
    let mut descriptor = LocalAllocation(null_mut());
    // SAFETY: the live handle and output pointers remain valid; the allocated
    // descriptor owns both the returned owner and ACL for these checks.
    let result = unsafe {
        GetSecurityInfo(
            file.as_raw_handle(),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &mut owner,
            null_mut(),
            &mut acl,
            null_mut(),
            &mut descriptor.0,
        )
    };
    if result != 0
        || owner.is_null()
        || acl.is_null()
        || unsafe {
            IsValidSid(owner) == 0 || !trusted_sid(owner, installer) || IsValidAcl(acl) == 0
        }
    {
        return Err(REFUSED);
    }
    unsafe { verify_aces(acl, installer, scope) }
}

// SAFETY: acl is validated and remains live with its containing descriptor.
unsafe fn verify_aces(acl: *const ACL, installer: &LocalAllocation, scope: Scope) -> Admission<()> {
    let acl_header = unsafe { &*acl };
    if !matches!(
        u32::from(acl_header.AclRevision),
        ACL_REVISION | ACL_REVISION_DS
    ) || acl_header.AceCount > 256
    {
        return Err(REFUSED);
    }

    // Add-file and add-subdirectory rights above the managed tree permit
    // unrelated siblings. Replacement, security and reparse-affecting rights still fail.
    let mut mutation = DELETE
        | FILE_DELETE_CHILD
        | WRITE_DAC
        | WRITE_OWNER
        | FILE_WRITE_ATTRIBUTES
        | FILE_WRITE_EA
        | GENERIC_ALL
        | GENERIC_WRITE;
    if matches!(scope, Scope::Runtime) {
        mutation |= FILE_WRITE_DATA | FILE_APPEND_DATA;
    }
    for index in 0..u32::from(acl_header.AceCount) {
        let mut ace: *mut c_void = null_mut();
        // SAFETY: this index is in the validated, live ACL.
        if unsafe { GetAce(acl, index, &mut ace) } == 0 || ace.is_null() {
            return Err(REFUSED);
        }
        let header = unsafe { &*ace.cast::<ACE_HEADER>() };
        let ace_type = u32::from(header.AceType);
        let length = usize::from(header.AceSize);
        if !matches!(ace_type, ACCESS_ALLOWED_ACE_TYPE | ACCESS_DENIED_ACE_TYPE)
            || header.AceFlags & !0x1f != 0
            || length < 16
            || length % 4 != 0
        {
            return Err(REFUSED);
        }
        // Simple allow/deny ACEs contain a header, 32-bit mask and inline SID.
        let bytes = unsafe { std::slice::from_raw_parts(ace.cast::<u8>(), length) };
        if bytes[8] != 1 || length != 16 + usize::from(bytes[9]) * 4 {
            return Err(REFUSED);
        }
        let sid = unsafe { ace.cast::<u8>().add(8).cast() };
        if unsafe { IsValidSid(sid) == 0 } {
            return Err(REFUSED);
        }
        let mask = u32::from_le_bytes([bytes[4], bytes[5], bytes[6], bytes[7]]);
        // Ignore deny ACEs conservatively. Descendants are checked on their
        // own handles, so inherit-only grants are irrelevant to this entry.
        if ace_type == ACCESS_ALLOWED_ACE_TYPE
            && u32::from(header.AceFlags) & INHERIT_ONLY_ACE == 0
            && mask & mutation != 0
            && !unsafe { trusted_sid(sid, installer) }
        {
            return Err(REFUSED);
        }
    }
    Ok(())
}

fn held_entry(
    path: &Path,
    directory: bool,
    installer: &LocalAllocation,
    scope: Scope,
) -> Admission<File> {
    let file = OpenOptions::new()
        .access_mode(READ_CONTROL | FILE_READ_ATTRIBUTES | FILE_READ_DATA)
        .share_mode(FILE_SHARE_READ | if directory { FILE_SHARE_WRITE } else { 0 })
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(|_| REFUSED)?;
    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    // SAFETY: the handle is live and info is a writable native structure.
    if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
        || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || (info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY != 0) != directory
        || (!directory && info.nNumberOfLinks != 1)
    {
        return Err(REFUSED);
    }
    verify_acl(&file, installer, scope)?;
    Ok(file)
}

fn held_ancestors(
    path: &Path,
    installer: &LocalAllocation,
    entries: &mut Vec<File>,
) -> Admission<()> {
    let mut components = path.components();
    let Some(Component::Prefix(prefix)) = components.next() else {
        return Err(REFUSED);
    };
    if !matches!(prefix.kind(), Prefix::Disk(_))
        || !matches!(components.next(), Some(Component::RootDir))
        || !components.all(|part| matches!(part, Component::Normal(_)))
        || path.as_os_str().encode_wide().count() >= MAX_PATH
    {
        return Err(REFUSED);
    }
    let parents: Vec<_> = path.ancestors().take(MAX_DEPTH + 1).collect();
    if parents.len() > MAX_DEPTH {
        return Err(REFUSED);
    }
    let root = parents.last().ok_or(REFUSED)?;
    let wide: Vec<_> = root.as_os_str().encode_wide().chain([0]).collect();
    // SAFETY: the volume root is a NUL-terminated local drive path.
    if unsafe { GetDriveTypeW(wide.as_ptr()) } != DRIVE_FIXED {
        return Err(REFUSED);
    }
    let volume = held_entry(root, true, installer, Scope::Ancestor)?;
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
    entries.push(volume);
    for parent in parents.into_iter().rev().skip(1) {
        entries.push(held_entry(parent, true, installer, Scope::Runtime)?);
    }
    Ok(())
}

fn held_tree(
    path: &Path,
    installer: &LocalAllocation,
    entries: &mut Vec<File>,
    depth: usize,
    bytes: &mut u64,
) -> Admission<()> {
    if depth >= MAX_DEPTH {
        return Err(REFUSED);
    }
    for entry in fs::read_dir(path).map_err(|_| REFUSED)? {
        let entry = entry.map_err(|_| REFUSED)?;
        if entries.len() >= MAX_ENTRIES {
            return Err(REFUSED);
        }
        let path = entry.path();
        if path.as_os_str().encode_wide().count() >= MAX_PATH {
            return Err(REFUSED);
        }
        let directory = entry.file_type().map_err(|_| REFUSED)?.is_dir();
        let file = held_entry(&path, directory, installer, Scope::Runtime)?;
        if !directory {
            let size = file.metadata().map_err(|_| REFUSED)?.len();
            *bytes = bytes
                .checked_add(size)
                .filter(|&total| total <= MAX_RUNTIME_BYTES)
                .ok_or(REFUSED)?;
        }
        entries.push(file);
        if directory {
            held_tree(&path, installer, entries, depth + 1, bytes)?;
        }
    }
    Ok(())
}

fn python_version(stem: &str) -> bool {
    stem.strip_prefix("python3").is_some_and(|minor| {
        (1..=2).contains(&minor.len()) && minor.bytes().all(|byte| byte.is_ascii_digit())
    })
}

fn verify_python_embed(
    root: &Path,
    installer: &LocalAllocation,
    entries: &mut Vec<File>,
) -> Admission<()> {
    // Require the official embeddable CPython search-path layout. The protected
    // provisioning authority also selects a standalone FFprobe distribution.
    let mut version = None;
    let mut versioned_dll = None;
    for item in fs::read_dir(root).map_err(|_| REFUSED)? {
        let name = item.map_err(|_| REFUSED)?.file_name();
        let name = name.to_str().ok_or(REFUSED)?.to_ascii_lowercase();
        if name == "pyvenv.cfg" {
            return Err(REFUSED);
        }
        if let Some(stem) = name
            .strip_suffix(".dll")
            .filter(|stem| python_version(stem))
        {
            if versioned_dll.replace(stem.to_owned()).is_some() {
                return Err(REFUSED);
            }
        }
        let Some(stem) = name.strip_suffix("._pth") else {
            continue;
        };
        if !python_version(stem) || version.replace(stem.to_owned()).is_some() {
            return Err(REFUSED);
        }
    }
    let version = version.ok_or(REFUSED)?;
    if versioned_dll.as_deref() != Some(version.as_str()) {
        return Err(REFUSED);
    }
    let zip = format!("{version}.zip");
    let pth = root.join(format!("{version}._pth"));
    for path in [
        root.join(format!("{version}.dll")),
        root.join(&zip),
        pth.clone(),
    ] {
        entries.push(held_entry(&path, false, installer, Scope::Runtime)?);
    }
    let mut contents = String::new();
    File::open(pth)
        .map_err(|_| REFUSED)?
        .take(4097)
        .read_to_string(&mut contents)
        .map_err(|_| REFUSED)?;
    let active: Vec<_> = contents
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .collect();
    if contents.len() > 4096 || active.as_slice() != [zip.as_str(), "."] {
        return Err(REFUSED);
    }
    Ok(())
}

pub(super) fn trusted_windows_otio_runtime() -> Admission<WindowsOtioRuntime> {
    let program_files = program_files()?;
    let system_root = system_windows_directory()?;
    let installer = trusted_installer_sid()?;
    let mut entries = Vec::new();
    held_ancestors(&program_files, &installer, &mut entries)?;
    held_ancestors(&system_root, &installer, &mut entries)?;
    entries.push(held_entry(
        &system_root.join("System32"),
        true,
        &installer,
        Scope::Runtime,
    )?);
    let vendor = program_files.join("Motionwright");
    entries.push(held_entry(&vendor, true, &installer, Scope::Runtime)?);
    let cwd = vendor.join("OTIO Runtime");
    entries.push(held_entry(&cwd, true, &installer, Scope::Runtime)?);
    let mut runtime_bytes = 0;
    held_tree(&cwd, &installer, &mut entries, 0, &mut runtime_bytes)?;
    let python = cwd.join("python.exe");
    let ffprobe = cwd.join("ffprobe.exe");
    for executable in [&python, &ffprobe] {
        entries.push(held_entry(executable, false, &installer, Scope::Runtime)?);
    }
    verify_python_embed(&cwd, &installer, &mut entries)?;
    Ok(WindowsOtioRuntime {
        cwd,
        python,
        ffprobe,
        system_root,
        _entries: entries,
    })
}
