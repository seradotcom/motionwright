//! Host-specific primitives for the bounded, non-executing offline copier.
//! macOS metadata is refused, never stripped: this copier cannot preserve an
//! extended ACL, quarantine, resource fork or other extended attribute.
use std::{ffi::CStr, fs::File, io, os::fd::AsRawFd};

#[cfg(target_os = "linux")]
pub const PLATFORM: &str = "linux-x86_64";
#[cfg(target_os = "macos")]
pub const PLATFORM: &str = "macos-aarch64";
pub const SUPPORTS_HYPERFRAMES: bool = cfg!(target_os = "linux");
#[cfg(target_os = "linux")]
pub const FIREFOX_SUFFIX: &str = "/firefox/firefox";
#[cfg(target_os = "macos")]
pub const FIREFOX_SUFFIX: &str = "/firefox/Nightly.app/Contents/MacOS/firefox";
pub const HEADER_BYTES: usize = 32;

pub fn native_executable(header: &[u8]) -> bool {
    #[cfg(target_os = "linux")]
    {
        header.len() >= 20 && &header[..6] == b"\x7fELF\x02\x01" && header[18..20] == [0x3e, 0]
    }
    #[cfg(target_os = "macos")]
    {
        // Thin, little-endian Mach-O 64, ARM64_ALL, MH_EXECUTE. No Intel,
        // ARM64e or universal-container inference; inspection never runs it.
        header.len() >= HEADER_BYTES
            && header[..4] == [0xcf, 0xfa, 0xed, 0xfe]
            && header[4..8] == [0x0c, 0, 0, 1]
            && header[8..12] == [0; 4]
            && header[12..16] == [2, 0, 0, 0]
    }
}

pub fn errno() -> *mut libc::c_int {
    // SAFETY: both functions return the current thread's errno storage.
    #[cfg(target_os = "linux")]
    unsafe {
        libc::__errno_location()
    }
    #[cfg(target_os = "macos")]
    unsafe {
        libc::__error()
    }
}

pub fn available_bytes(value: &libc::statvfs) -> Option<u64> {
    #[cfg(target_os = "linux")]
    let blocks = value.f_bavail;
    #[cfg(target_os = "macos")]
    let blocks = u64::from(value.f_bavail);
    blocks.checked_mul(value.f_frsize)
}

pub fn rename_exclusive(parent: &File, from: &CStr, to: &CStr) -> io::Result<()> {
    // SAFETY: the caller retains the directory and both NUL-terminated single
    // components. Both syscalls refuse an existing destination; no fallback
    // replaces data when a filesystem does not support exclusive rename.
    #[cfg(target_os = "linux")]
    let code = unsafe {
        libc::renameat2(
            parent.as_raw_fd(),
            from.as_ptr(),
            parent.as_raw_fd(),
            to.as_ptr(),
            libc::RENAME_NOREPLACE,
        )
    };
    #[cfg(target_os = "macos")]
    let code = unsafe {
        libc::renameatx_np(
            parent.as_raw_fd(),
            from.as_ptr(),
            parent.as_raw_fd(),
            to.as_ptr(),
            libc::RENAME_EXCL,
        )
    };
    if code == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

#[cfg(target_os = "linux")]
pub fn verify_security(_file: &File) -> Result<(), String> {
    // Linux owner/mode/single-link checks remain in the retained file reader.
    Ok(())
}

#[cfg(target_os = "macos")]
pub fn verify_security(file: &File) -> Result<(), String> {
    const REFUSED: &str = "This macOS runtime folder has extended permissions or attributes that the offline copier cannot preserve, or they could not be inspected. Its original security metadata was not removed.";
    // Darwin's descriptor-based ACL API is in libSystem but not exposed by
    // libc 0.2.190. ABI: Apple Libc include/sys/acl.h, posix1e/acl_file.c,
    // posix1e/acl_entry.c and gen/filesec.c. ACL_TYPE_EXTENDED=0x100.
    unsafe extern "C" {
        fn acl_get_fd_np(fd: libc::c_int, kind: libc::c_int) -> *mut libc::c_void;
        fn acl_valid(acl: *mut libc::c_void) -> libc::c_int;
        fn acl_get_entry(
            acl: *mut libc::c_void,
            entry_id: libc::c_int,
            entry: *mut *mut libc::c_void,
        ) -> libc::c_int;
        fn acl_free(acl: *mut libc::c_void) -> libc::c_int;
    }
    // SAFETY: a zero-size query uses no output buffer and reads only metadata
    // on the retained descriptor. Any attribute, including quarantine, refuses.
    if unsafe { libc::flistxattr(file.as_raw_fd(), std::ptr::null_mut(), 0, 0) } != 0 {
        return Err(REFUSED.into());
    }
    // SAFETY: errno is thread-local; the live descriptor is read, never changed.
    unsafe { *errno() = 0 };
    let acl = unsafe { acl_get_fd_np(file.as_raw_fd(), 0x100) };
    if acl.is_null() {
        // Darwin filesec_get_property reports ENOENT for an absent ACL, even
        // though fstatx_np read the existing retained descriptor successfully.
        // Other failures, including unsupported ACL retrieval, stay closed.
        return if io::Error::last_os_error().raw_os_error() == Some(libc::ENOENT) {
            Ok(())
        } else {
            Err(REFUSED.into())
        };
    }
    let mut entry = std::ptr::null_mut();
    // SAFETY: acl_get_fd_np returned an owned ACL. Validate before inspecting;
    // the entry pointer is never dereferenced and acl_free releases exactly once.
    let valid = unsafe { acl_valid(acl) };
    let found = if valid == 0 {
        unsafe { acl_get_entry(acl, 0, &mut entry) }
    } else {
        0
    };
    let error = io::Error::last_os_error().raw_os_error();
    let freed = unsafe { acl_free(acl) };
    // A valid empty Darwin ACL returns -1/EINVAL for ACL_FIRST_ENTRY. Every
    // nonempty ACL is refused, even if POSIX mode bits still look private.
    if valid == 0 && found == -1 && error == Some(libc::EINVAL) && freed == 0 {
        Ok(())
    } else {
        Err(REFUSED.into())
    }
}
