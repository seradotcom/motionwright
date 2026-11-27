//! Admission of the protected Windows OTIO runtime and its private workspace.
//! windows-sys 0.61.2 features: Win32_Foundation, Win32_Security_Authorization,
//! Win32_Storage_FileSystem, Win32_System_Com, Win32_System_SystemInformation,
//! Win32_System_SystemServices, Win32_System_WindowsProgramming, Win32_UI_Shell.

use std::{
    ffi::{OsString, c_void},
    fs::{self, File, OpenOptions},
    io::Read,
    mem::size_of,
    os::windows::{
        ffi::{OsStrExt, OsStringExt},
        fs::OpenOptionsExt,
        io::{AsRawHandle, FromRawHandle, OwnedHandle},
    },
    path::{Component, Path, PathBuf, Prefix},
    ptr::null_mut,
};
use windows_sys::Win32::{
    Foundation::{GENERIC_ALL, GENERIC_WRITE, LocalFree},
    Security::{
        ACE_HEADER, ACL, ACL_REVISION, ACL_REVISION_DS,
        Authorization::{
            ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
            ConvertStringSidToSidW, GetSecurityInfo, SE_FILE_OBJECT,
        },
        DACL_SECURITY_INFORMATION, EqualSid, GetAce, GetTokenInformation, INHERIT_ONLY_ACE,
        IsValidAcl, IsValidSid, IsWellKnownSid, OWNER_SECURITY_INFORMATION, PSID,
        SECURITY_ATTRIBUTES, TOKEN_QUERY, TOKEN_USER, TokenUser, WinBuiltinAdministratorsSid,
        WinLocalSystemSid,
    },
    Storage::FileSystem::{
        BY_HANDLE_FILE_INFORMATION, CreateDirectoryW, DELETE, FILE_APPEND_DATA,
        FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT, FILE_DELETE_CHILD,
        FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_READ_ATTRIBUTES,
        FILE_READ_DATA, FILE_SHARE_READ, FILE_SHARE_WRITE, FILE_WRITE_ATTRIBUTES, FILE_WRITE_DATA,
        FILE_WRITE_EA, GetDriveTypeW, GetFileInformationByHandle, GetVolumeInformationByHandleW,
        READ_CONTROL, WRITE_DAC, WRITE_OWNER,
    },
    System::{
        Com::CoTaskMemFree,
        SystemInformation::GetSystemWindowsDirectoryW,
        SystemServices::{ACCESS_ALLOWED_ACE_TYPE, ACCESS_DENIED_ACE_TYPE, FILE_PERSISTENT_ACLS},
        Threading::{GetCurrentProcess, OpenProcessToken},
        WindowsProgramming::DRIVE_FIXED,
    },
    UI::Shell::{FOLDERID_ProgramFiles, KF_FLAG_DEFAULT_PATH, SHGetKnownFolderPath},
};

const REFUSED: &str = "OTIO needs a protected Python 3 and ffprobe installation under Motionwright\\OTIO Runtime in Program Files.";
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

unsafe fn trusted_sid(sid: PSID, installer: &LocalAllocation, owner_sid: Option<PSID>) -> bool {
    // SAFETY: the caller checked the live SID, and installer owns a valid SID.
    unsafe {
        IsWellKnownSid(sid, WinLocalSystemSid) != 0
            || IsWellKnownSid(sid, WinBuiltinAdministratorsSid) != 0
            || EqualSid(sid, installer.0) != 0
            || owner_sid.is_some_and(|owner| EqualSid(sid, owner) != 0)
    }
}

#[derive(Clone, Copy)]
enum Scope {
    Ancestor,
    Runtime,
}

fn verify_acl(
    file: &File,
    installer: &LocalAllocation,
    scope: Scope,
    owner_sid: Option<PSID>,
) -> Admission<()> {
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
            IsValidSid(owner) == 0
                || !trusted_sid(owner, installer, owner_sid)
                || IsValidAcl(acl) == 0
        }
    {
        return Err(REFUSED);
    }
    unsafe { verify_aces(acl, installer, scope, owner_sid) }
}

// SAFETY: acl is validated and remains live with its containing descriptor.
unsafe fn verify_aces(
    acl: *const ACL,
    installer: &LocalAllocation,
    scope: Scope,
    owner_sid: Option<PSID>,
) -> Admission<()> {
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
            && !unsafe { trusted_sid(sid, installer, owner_sid) }
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
    held_entry_for(path, directory, installer, scope, None)
}

fn held_entry_for(
    path: &Path,
    directory: bool,
    installer: &LocalAllocation,
    scope: Scope,
    owner_sid: Option<PSID>,
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
    verify_acl(&file, installer, scope, owner_sid)?;
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
    require_fixed_volume(root)?;
    let volume = held_entry(root, true, installer, Scope::Ancestor)?;
    require_acl_volume(&volume)?;
    entries.push(volume);
    for parent in parents.into_iter().rev().skip(1) {
        entries.push(held_entry(parent, true, installer, Scope::Runtime)?);
    }
    Ok(())
}

fn require_fixed_volume(root: &Path) -> Admission<()> {
    let wide: Vec<_> = root.as_os_str().encode_wide().chain([0]).collect();
    // SAFETY: the volume root is a NUL-terminated local drive path.
    if unsafe { GetDriveTypeW(wide.as_ptr()) } != DRIVE_FIXED {
        return Err(REFUSED);
    }
    Ok(())
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
            && versioned_dll.replace(stem.to_owned()).is_some()
        {
            return Err(REFUSED);
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

const WORKSPACE_REFUSED: &str =
    "OTIO needs a private workspace on a local Windows volume with protected ancestor paths.";

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
            return Err(WORKSPACE_REFUSED);
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
            return Err(WORKSPACE_REFUSED);
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
            return Err(WORKSPACE_REFUSED);
        }
        // SAFETY: TOKEN_USER was written at the aligned base by GetTokenInformation.
        let sid = unsafe { (*buffer.as_ptr().cast::<TOKEN_USER>()).User.Sid };
        let offset = (sid as usize)
            .checked_sub(buffer.as_ptr() as usize)
            .ok_or(WORKSPACE_REFUSED)?;
        if offset
            .checked_add(8)
            .is_none_or(|end| end > written as usize)
        {
            return Err(WORKSPACE_REFUSED);
        }
        // SAFETY: the SID header is within the returned buffer. Check the
        // complete sub-authority array before passing the pointer to another API.
        let count = unsafe { *sid.cast::<u8>().add(1) } as usize;
        if offset + 8 + count * 4 > written as usize || unsafe { IsValidSid(sid) } == 0 {
            return Err(WORKSPACE_REFUSED);
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
            return Err(WORKSPACE_REFUSED);
        }
        // SAFETY: successful conversion returns a NUL-terminated string.
        let len = (0..192)
            .find(|index| unsafe { *text.add(*index) } == 0)
            .ok_or(WORKSPACE_REFUSED)?;
        let sid = String::from_utf16(unsafe { std::slice::from_raw_parts(text, len) })
            .map_err(|_| WORKSPACE_REFUSED)?;
        if !sid.starts_with("S-") || !sid[2..].bytes().all(|b| b.is_ascii_digit() || b == b'-') {
            return Err(WORKSPACE_REFUSED);
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
            return Err(WORKSPACE_REFUSED);
        }
        Ok(descriptor)
    }
}

pub(super) struct WindowsOtioWorkspaceParent {
    path: PathBuf,
    _ancestors: Vec<File>,
    installer: LocalAllocation,
    user: CurrentUserSid,
}

impl WindowsOtioWorkspaceParent {
    pub(super) fn admit(parent: &Path) -> Admission<Self> {
        let mut components = parent.components();
        let Some(Component::Prefix(prefix)) = components.next() else {
            return Err(WORKSPACE_REFUSED);
        };
        if !matches!(prefix.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_))
            || !matches!(components.next(), Some(Component::RootDir))
            || !components.all(|part| matches!(part, Component::Normal(_)))
            || parent.as_os_str().encode_wide().any(|unit| unit == 0)
        {
            return Err(WORKSPACE_REFUSED);
        }
        let parents: Vec<_> = parent.ancestors().take(65).collect();
        if parents.len() > 64 {
            return Err(WORKSPACE_REFUSED);
        }
        let installer = trusted_installer_sid()?;
        let user = CurrentUserSid::read()?;
        let mut ancestors = Vec::with_capacity(parents.len());
        // The prefix by itself (C:) is not a directory below the drive root.
        for path in parents.into_iter().rev().filter(|path| path.has_root()) {
            let volume_root = ancestors.is_empty();
            if volume_root {
                require_fixed_volume(path).map_err(|_| WORKSPACE_REFUSED)?;
            }
            let held = held_entry_for(path, true, &installer, Scope::Ancestor, Some(user.sid()))
                .map_err(|_| WORKSPACE_REFUSED)?;
            if volume_root {
                require_acl_volume(&held).map_err(|_| WORKSPACE_REFUSED)?;
            }
            ancestors.push(held);
        }
        if ancestors.is_empty() {
            return Err(WORKSPACE_REFUSED);
        }
        Ok(Self {
            path: parent.to_path_buf(),
            _ancestors: ancestors,
            installer,
            user,
        })
    }

    pub(super) fn create(self, name: &str) -> Admission<WindowsOtioWorkspace> {
        if name.is_empty()
            || !name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-')
            || matches!(name, "." | "..")
        {
            return Err(WORKSPACE_REFUSED);
        }
        let root = self.path.join(name);
        let descriptor = self.user.private_descriptor()?;
        let attributes = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor.0,
            bInheritHandle: 0,
        };
        let path: Vec<_> = root.as_os_str().encode_wide().chain([0]).collect();
        // SAFETY: the absolute local path is terminated and the security
        // descriptor lives through exclusive directory creation.
        if unsafe { CreateDirectoryW(path.as_ptr(), &attributes) } == 0 {
            return Err(WORKSPACE_REFUSED);
        }
        let mut workspace = WindowsOtioWorkspace {
            root,
            root_handle: None,
            parent: self,
        };
        workspace.root_handle = Some(workspace.hold(&workspace.root, true)?);
        Ok(workspace)
    }
}

pub(super) struct WindowsOtioWorkspace {
    root: PathBuf,
    root_handle: Option<File>,
    parent: WindowsOtioWorkspaceParent,
}

impl WindowsOtioWorkspace {
    pub(super) fn path(&self) -> &Path {
        &self.root
    }

    pub(super) fn hold(&self, path: &Path, directory: bool) -> Admission<File> {
        held_entry_for(
            path,
            directory,
            &self.parent.installer,
            Scope::Runtime,
            Some(self.parent.user.sid()),
        )
        .map_err(|_| WORKSPACE_REFUSED)
    }

    pub(super) fn hold_original(&self, path: &Path, expected: &[u8]) -> Admission<File> {
        let mut file = self.hold(path, false)?;
        let mut bytes = Vec::new();
        (&mut file)
            .take(expected.len() as u64 + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| WORKSPACE_REFUSED)?;
        if bytes != expected {
            return Err(WORKSPACE_REFUSED);
        }
        Ok(file)
    }
}

impl Drop for WindowsOtioWorkspace {
    fn drop(&mut self) {
        drop(self.root_handle.take());
        // An unresolved inner workspace is nonempty and keeps the private root.
        // Ancestor handles remain retained during this single-directory removal.
        let _ = fs::remove_dir(&self.root);
    }
}
