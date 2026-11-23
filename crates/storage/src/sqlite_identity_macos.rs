//! Public Darwin libproc witness for an existing SQLite VFS mapping.
//!
//! ABI: Apple's <sys/proc_info.h> proc_regionwithpathinfo and <libproc.h>.
//! https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc_info.h
//! These fixed-width public structures are checked against the native SDK by
//! tests/macos_region_abi.c. No SQLite private layout or database pathname is read.
use super::{Result, unavailable};
use std::ffi::{c_int, c_void};

const PROC_PIDREGIONPATHINFO: c_int = 8;
const VM_PROT_READ: u32 = 1;

#[repr(C)]
struct RegionInfo {
    protection: u32,
    _maximum_protection: u32,
    _inheritance: u32,
    _flags: u32,
    offset: u64,
    _vm_counters: [u32; 14],
    address: u64,
    size: u64,
}

#[repr(C)]
struct VnodeStat {
    device: u32,
    mode: u16,
    _links: u16,
    inode: u64,
    _owner_ids: [u32; 2],
    _timestamps: [i64; 8],
    _size: i64,
    _blocks: i64,
    _block_size: i32,
    _flags: u32,
    _generation: u32,
    _special_device: u32,
    _reserved: [i64; 2],
}

#[repr(C)]
struct VnodeInfo {
    stat: VnodeStat,
    _kind: i32,
    _padding: i32,
    _filesystem: [i32; 2],
}

#[repr(C)]
struct RegionWithPath {
    region: RegionInfo,
    vnode: VnodeInfo,
    // Required ABI storage only. Identity never depends on these path bytes.
    _path: [i8; 1024],
}

const _: () = {
    assert!(std::mem::size_of::<RegionInfo>() == 96);
    assert!(std::mem::offset_of!(RegionInfo, offset) == 16);
    assert!(std::mem::offset_of!(RegionInfo, address) == 80);
    assert!(std::mem::offset_of!(RegionInfo, size) == 88);
    assert!(std::mem::size_of::<VnodeStat>() == 136);
    assert!(std::mem::offset_of!(VnodeStat, inode) == 8);
    assert!(std::mem::size_of::<VnodeInfo>() == 152);
    assert!(std::mem::offset_of!(RegionWithPath, vnode) == 96);
    assert!(std::mem::offset_of!(RegionWithPath, _path) == 248);
    assert!(std::mem::size_of::<RegionWithPath>() == 1272);
    assert!(std::mem::align_of::<RegionWithPath>() == 8);
};

#[link(name = "proc")]
unsafe extern "C" {
    fn proc_pidinfo(
        pid: c_int,
        flavor: c_int,
        argument: u64,
        buffer: *mut c_void,
        buffer_size: c_int,
    ) -> c_int;
}

pub(super) fn mapping_identity(pointer: *mut c_void) -> Result<String> {
    if pointer.is_null() {
        return Err(unavailable());
    }
    let address = u64::try_from(pointer.addr()).map_err(|_| unavailable())?;
    let pid = c_int::try_from(std::process::id()).map_err(|_| unavailable())?;
    let size = c_int::try_from(std::mem::size_of::<RegionWithPath>()).map_err(|_| unavailable())?;
    let mut information = std::mem::MaybeUninit::<RegionWithPath>::zeroed();
    // SAFETY: libproc writes into one aligned, fully sized public ABI buffer.
    // The queried address is only a numeric kernel lookup; this function never
    // dereferences it. The caller retains SQLite's xFetch lease and connection
    // mutex until this observation and the checked xUnfetch have completed.
    let read = unsafe {
        proc_pidinfo(
            pid,
            PROC_PIDREGIONPATHINFO,
            address,
            information.as_mut_ptr().cast(),
            size,
        )
    };
    if read != size {
        return Err(unavailable());
    }
    // SAFETY: exact successful byte count initializes the public structure.
    // All fields are integers or integer arrays; no invalid Rust discriminants.
    let information = unsafe { information.assume_init() };
    let region = &information.region;
    let vnode = &information.vnode.stat;
    let end = region
        .address
        .checked_add(region.size)
        .ok_or_else(unavailable)?;
    let relative = address
        .checked_sub(region.address)
        .ok_or_else(unavailable)?;
    if region.protection & VM_PROT_READ == 0
        || address >= end
        || region.offset.checked_add(relative) != Some(0)
        || vnode.mode & 0o170000 != 0o100000
        || vnode.inode == 0
    {
        return Err(unavailable());
    }
    // Darwin dev_t is signed 32-bit; MetadataExt::dev exposes its cast to u64.
    // Match the existing CAS pathname observation without opening another fd.
    let device = (vnode.device as i32) as u64;
    Ok(format!("unix:{device}:{}", vnode.inode))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn anonymous_or_null_memory_cannot_supply_a_database_vnode() {
        assert!(mapping_identity(std::ptr::null_mut()).is_err());
        let stack_value = 17_u64;
        assert!(mapping_identity(std::ptr::from_ref(&stack_value).cast_mut().cast()).is_err());
    }
}
