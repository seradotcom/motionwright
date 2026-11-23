/* Compile with the actual macOS SDK before admitting the Rust libproc binding.
 * This checks the public ABI only; no process or database is opened.
 */
#include <stddef.h>
#include <libproc.h>
#include <sys/proc_info.h>

_Static_assert(PROC_PIDREGIONPATHINFO == 8, "libproc flavor changed");
_Static_assert(sizeof(struct proc_regioninfo) == 96, "region size changed");
_Static_assert(offsetof(struct proc_regioninfo, pri_offset) == 16, "offset changed");
_Static_assert(offsetof(struct proc_regioninfo, pri_address) == 80, "address changed");
_Static_assert(offsetof(struct proc_regioninfo, pri_size) == 88, "length changed");
_Static_assert(sizeof(struct vinfo_stat) == 136, "vnode stat size changed");
_Static_assert(offsetof(struct vinfo_stat, vst_ino) == 8, "inode changed");
_Static_assert(sizeof(struct vnode_info) == 152, "vnode size changed");
_Static_assert(offsetof(struct proc_regionwithpathinfo, prp_vip) == 96, "vnode changed");
_Static_assert(offsetof(struct proc_regionwithpathinfo, prp_vip.vip_path) == 248, "path changed");
_Static_assert(sizeof(struct proc_regionwithpathinfo) == 1272, "response size changed");
_Static_assert(_Alignof(struct proc_regionwithpathinfo) == 8, "response alignment changed");
