//! Physical evidence from SQLite's retained main-database handle.
//! This value is not an authorization grant and must remain bound to the Store borrow.
use crate::{Result, StorageError};
use rusqlite::Connection;

fn unavailable() -> StorageError {
    std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "retained SQLite file identity is unavailable",
    )
    .into()
}

#[cfg(windows)]
pub(super) fn connection_identity(connection: &Connection) -> Result<String> {
    use rusqlite::ffi;
    use windows_sys::Win32::Foundation::{HANDLE, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_ID_INFO, FileIdInfo, GetFileInformationByHandleEx,
    };

    let mut handle: HANDLE = std::ptr::null_mut();
    // SAFETY: the Connection remains borrowed and live throughout both calls.
    // SQLite's public WIN32_GET_HANDLE control writes one borrowed HANDLE into
    // this initialized, correctly aligned output. We never close, duplicate,
    // transfer, or reopen that handle.
    let result = unsafe {
        ffi::sqlite3_file_control(
            connection.handle(),
            c"main".as_ptr(),
            ffi::SQLITE_FCNTL_WIN32_GET_HANDLE,
            std::ptr::from_mut(&mut handle).cast(),
        )
    };
    if result != ffi::SQLITE_OK || handle.is_null() || handle == INVALID_HANDLE_VALUE {
        return Err(unavailable());
    }
    let mut information = std::mem::MaybeUninit::<FILE_ID_INFO>::uninit();
    // SAFETY: successful WIN32_GET_HANDLE returned SQLite's still-live main
    // database handle. FileIdInfo writes the size/alignment supplied here;
    // the buffer is read only after the OS reports success.
    let status = unsafe {
        GetFileInformationByHandleEx(
            handle,
            FileIdInfo,
            information.as_mut_ptr().cast(),
            std::mem::size_of::<FILE_ID_INFO>() as u32,
        )
    };
    if status == 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    // SAFETY: successful FileIdInfo initialized the entire FILE_ID_INFO.
    let information = unsafe { information.assume_init() };
    Ok(format!(
        "windows:{}:{}",
        information.VolumeSerialNumber,
        hex::encode(information.FileId.Identifier)
    ))
}

#[cfg(target_os = "linux")]
pub(super) fn connection_identity(connection: &Connection) -> Result<String> {
    // The sole Store constructor explicitly uses PRIVATE_CACHE and rejects
    // URI filenames, so another connection cannot share this file's pager.
    // Unit-test connections below use the same explicit cache policy.
    linux::connection_identity(connection)
}

#[cfg(target_os = "linux")]
mod linux {
    use super::{Connection, Result, unavailable};
    use rusqlite::ffi;
    use std::ffi::{c_int, c_void};
    use std::io::Read;

    const MAX_MAPS_BYTES: usize = 4 * 1024 * 1024;

    struct ConnectionLock<'connection> {
        connection: &'connection Connection,
        mutex: *mut ffi::sqlite3_mutex,
    }

    impl<'connection> ConnectionLock<'connection> {
        fn enter(connection: &'connection Connection) -> Self {
            // SAFETY: the borrowed connection remains live. SQLite's database
            // mutex is recursive; a NULL mutex in NOMUTEX mode is a documented
            // no-op. The Store borrow also excludes concurrent Rust use.
            let mutex = unsafe {
                let mutex = ffi::sqlite3_db_mutex(connection.handle());
                ffi::sqlite3_mutex_enter(mutex);
                mutex
            };
            Self { connection, mutex }
        }

        fn file(&self) -> Result<*mut ffi::sqlite3_file> {
            let mut file = std::ptr::null_mut::<ffi::sqlite3_file>();
            // SAFETY: FILE_POINTER writes one correctly aligned public
            // sqlite3_file pointer; the connection and its mutex remain held.
            let status = unsafe {
                ffi::sqlite3_file_control(
                    self.connection.handle(),
                    c"main".as_ptr(),
                    ffi::SQLITE_FCNTL_FILE_POINTER,
                    std::ptr::from_mut(&mut file).cast(),
                )
            };
            if status != ffi::SQLITE_OK || file.is_null() {
                return Err(unavailable());
            }
            Ok(file)
        }

        fn mmap_limit(&self, value: &mut ffi::sqlite3_int64) -> Result<()> {
            // SAFETY: MMAP_SIZE consumes/returns exactly one sqlite3_int64.
            // The borrowed connection and database mutex remain held.
            let status = unsafe {
                ffi::sqlite3_file_control(
                    self.connection.handle(),
                    c"main".as_ptr(),
                    ffi::SQLITE_FCNTL_MMAP_SIZE,
                    std::ptr::from_mut(value).cast(),
                )
            };
            if status == ffi::SQLITE_OK {
                Ok(())
            } else {
                Err(unavailable())
            }
        }
    }

    impl Drop for ConnectionLock<'_> {
        fn drop(&mut self) {
            // SAFETY: this releases exactly the acquisition in enter; SQLite
            // also accepts NULL here for a connection in NOMUTEX mode.
            unsafe { ffi::sqlite3_mutex_leave(self.mutex) };
        }
    }

    type Unfetch =
        unsafe extern "C" fn(*mut ffi::sqlite3_file, ffi::sqlite3_int64, *mut c_void) -> c_int;

    struct Mapping<'lock, 'connection> {
        lock: &'lock ConnectionLock<'connection>,
        file: *mut ffi::sqlite3_file,
        pointer: *mut c_void,
        unfetch: Unfetch,
        restore_limit: Option<ffi::sqlite3_int64>,
    }

    impl<'lock, 'connection> Mapping<'lock, 'connection> {
        fn acquire(lock: &'lock ConnectionLock<'connection>) -> Result<Self> {
            let file = lock.file()?;
            // SAFETY: sqlite3_file and its pMethods member are public VFS ABI.
            // No private unixFile layout or OS descriptor cast is involved.
            let methods = unsafe { std::ptr::addr_of!((*file).pMethods).read() };
            if methods.is_null() {
                return Err(unavailable());
            }
            // SAFETY: iVersion is the first public member. Do not form a
            // reference to the full methods table before checking its version:
            // a legitimate older VFS may allocate only the earlier prefix.
            let version = unsafe { std::ptr::addr_of!((*methods).iVersion).read() };
            if version < 3 {
                return Err(unavailable());
            }
            // SAFETY: version >= 3 guarantees both public method slots exist.
            let (fetch, unfetch) = unsafe {
                (
                    std::ptr::addr_of!((*methods).xFetch).read(),
                    std::ptr::addr_of!((*methods).xUnfetch).read(),
                )
            };
            let (Some(fetch), Some(unfetch)) = (fetch, unfetch) else {
                return Err(unavailable());
            };
            let mut original_limit = -1;
            lock.mmap_limit(&mut original_limit)?;
            if original_limit < 0 {
                return Err(unavailable());
            }
            let mut mapping = Self {
                lock,
                file,
                pointer: std::ptr::null_mut(),
                unfetch,
                restore_limit: None,
            };
            if original_limit < 4096 {
                // Record restoration before changing anything: the VFS can
                // change its limit and then report a mapping error.
                mapping.restore_limit = Some(original_limit);
                let mut requested_limit = 4096;
                lock.mmap_limit(&mut requested_limit)?;
                if requested_limit != original_limit {
                    return Err(unavailable());
                }
            }
            let mut pointer = std::ptr::null_mut();
            // SAFETY: both public VFS methods exist, the file is still held by
            // this connection, and this initialized output receives the fetch.
            // xFetch may decline by returning NULL; that is never an identity.
            let status = unsafe { fetch(file, 0, 1, &mut pointer) };
            if status != ffi::SQLITE_OK || pointer.is_null() {
                return Err(unavailable());
            }
            mapping.pointer = pointer;
            Ok(mapping)
        }

        fn release(&mut self) -> Result<()> {
            let mut failed = false;
            if !self.pointer.is_null() {
                let pointer = std::mem::replace(&mut self.pointer, std::ptr::null_mut());
                // SAFETY: pair the one successful xFetch with its original
                // non-NULL pointer and offset. NULL means "unmap" to SQLite
                // and must never be used to release a declined fetch.
                if unsafe { (self.unfetch)(self.file, 0, pointer) } != ffi::SQLITE_OK {
                    failed = true;
                }
            }
            if let Some(original_limit) = self.restore_limit.take() {
                let mut requested_limit = original_limit;
                if self.lock.mmap_limit(&mut requested_limit).is_err() {
                    failed = true;
                }
                let mut observed_limit = -1;
                if self.lock.mmap_limit(&mut observed_limit).is_err()
                    || observed_limit != original_limit
                {
                    failed = true;
                }
            }
            if failed { Err(unavailable()) } else { Ok(()) }
        }

        fn finish(mut self) -> Result<()> {
            self.release()
        }
    }

    impl Drop for Mapping<'_, '_> {
        fn drop(&mut self) {
            // An early error still releases/restores. The success path calls
            // finish and checks its result before returning any identity.
            let _ = self.release();
        }
    }

    fn pair(value: &[u8], separator: u8) -> Result<(&[u8], &[u8])> {
        let mut parts = value.split(|byte| *byte == separator);
        let first = parts.next().ok_or_else(unavailable)?;
        let second = parts.next().ok_or_else(unavailable)?;
        if first.is_empty() || second.is_empty() || parts.next().is_some() {
            return Err(unavailable());
        }
        Ok((first, second))
    }

    fn number(value: &[u8], radix: u32) -> Result<u64> {
        if value.is_empty() || value.first() == Some(&b'+') {
            return Err(unavailable());
        }
        let text = std::str::from_utf8(value).map_err(|_| unavailable())?;
        u64::from_str_radix(text, radix).map_err(|_| unavailable())
    }

    fn mapping_identity(pointer: *mut c_void) -> Result<String> {
        let address = u64::try_from(pointer.addr()).map_err(|_| unavailable())?;
        let end_address = address.checked_add(1).ok_or_else(unavailable)?;
        let mut maps = Vec::new();
        let limit = u64::try_from(MAX_MAPS_BYTES + 1).map_err(|_| unavailable())?;
        // Only procfs is opened. Opening or closing another descriptor for
        // the database itself could release this process's POSIX SQL locks.
        std::fs::File::open("/proc/self/maps")?
            .take(limit)
            .read_to_end(&mut maps)?;
        if maps.len() > MAX_MAPS_BYTES || maps.last() != Some(&b'\n') {
            return Err(unavailable());
        }
        let mut identity = None;
        for line in maps
            .split(|byte| *byte == b'\n')
            .filter(|line| !line.is_empty())
        {
            let mut fields = line
                .split(|byte| byte.is_ascii_whitespace())
                .filter(|field| !field.is_empty());
            let (start, end) = pair(fields.next().ok_or_else(unavailable)?, b'-')?;
            let start = number(start, 16)?;
            let end = number(end, 16)?;
            if start >= end {
                return Err(unavailable());
            }
            if address < start || end_address > end {
                continue;
            }
            let permissions = fields.next().ok_or_else(unavailable)?;
            if permissions.len() != 4
                || permissions[0] != b'r'
                || !matches!(permissions[1], b'w' | b'-')
                || !matches!(permissions[2], b'x' | b'-')
                || !matches!(permissions[3], b'p' | b's')
            {
                return Err(unavailable());
            }
            let offset = number(fields.next().ok_or_else(unavailable)?, 16)?;
            let relative_offset = address.checked_sub(start).ok_or_else(unavailable)?;
            if offset.checked_add(relative_offset) != Some(0) {
                return Err(unavailable());
            }
            let (major, minor) = pair(fields.next().ok_or_else(unavailable)?, b':')?;
            let major = u32::try_from(number(major, 16)?).map_err(|_| unavailable())?;
            let minor = u32::try_from(number(minor, 16)?).map_err(|_| unavailable())?;
            let inode = number(fields.next().ok_or_else(unavailable)?, 10)?;
            if inode == 0 || identity.is_some() {
                return Err(unavailable());
            }
            // The pathname is intentionally unused, including "(deleted)".
            // This is a kernel witness for the retained mapping, not a path
            // lookup. The tag distinguishes major/minor from CAS's raw st_dev.
            identity = Some(format!("linux:{major}:{minor}:{inode}"));
        }
        identity.ok_or_else(unavailable)
    }

    pub(super) fn connection_identity(connection: &Connection) -> Result<String> {
        let lock = ConnectionLock::enter(connection);
        let mapping = Mapping::acquire(&lock)?;
        // Do not read the mapped page. Keep its VFS lease until procfs has
        // identified the exact address, then check all cleanup before success.
        let identity = mapping_identity(mapping.pointer);
        mapping.finish()?;
        identity
    }
}

#[cfg(not(any(windows, target_os = "linux")))]
pub(super) fn connection_identity(_connection: &Connection) -> Result<String> {
    // Other platforms require their own verified retained-handle witness.
    // There is no pathname fallback and no cast to SQLite's private layout.
    Err(unavailable())
}

#[cfg(all(test, any(windows, target_os = "linux")))]
mod tests {
    use super::*;
    use rusqlite::{Connection, OpenFlags};

    const LOCK_PROBE_DATABASE: &str = "MOTIONWRIGHT_SQLITE_IDENTITY_LOCK_PROBE_DATABASE";
    const LOCK_PROBE_MARKER: &str = "MOTIONWRIGHT_SQLITE_IDENTITY_EXTERNAL_WRITER_BUSY";

    fn private_flags() -> OpenFlags {
        OpenFlags::default()
            | OpenFlags::SQLITE_OPEN_NOFOLLOW
            | OpenFlags::SQLITE_OPEN_PRIVATE_CACHE
    }

    fn private_connection(path: &std::path::Path) -> Connection {
        Connection::open_with_flags(path, private_flags()).unwrap()
    }

    fn test_binary_command(test: &str) -> std::process::Command {
        let mut command = std::process::Command::new(std::env::current_exe().unwrap());
        command
            .args(["--exact", test, "--nocapture", "--test-threads=1"])
            .stdin(std::process::Stdio::null());
        command
    }

    struct ExitChildGuard(Option<std::process::Child>);

    impl Drop for ExitChildGuard {
        fn drop(&mut self) {
            if let Some(child) = self.0.as_mut() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }

    fn wait_for_child(guard: &mut ExitChildGuard) -> std::process::ExitStatus {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
        loop {
            let child = guard.0.as_mut().expect("probe child is owned until exit");
            if let Some(status) = child.try_wait().unwrap() {
                // Disarm only after observing and reaping terminal status.
                let _ = guard.0.take();
                return status;
            }
            assert!(
                std::time::Instant::now() < deadline,
                "external writer lock probe timed out"
            );
            std::thread::sleep(std::time::Duration::from_millis(25));
        }
    }

    #[test]
    fn independent_connections_to_the_same_file_have_the_same_identity() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("shared.sqlite3");
        let first = private_connection(&path);
        first
            .execute_batch("CREATE TABLE witness(value INTEGER); INSERT INTO witness VALUES(7);")
            .unwrap();
        let second = private_connection(&path);
        assert_eq!(
            connection_identity(&first).unwrap(),
            connection_identity(&second).unwrap()
        );
        first.execute("INSERT INTO witness VALUES(8)", []).unwrap();
        assert_eq!(
            second
                .query_row("SELECT COUNT(*) FROM witness", [], |row| row
                    .get::<_, u64>(0))
                .unwrap(),
            2
        );
    }

    #[test]
    fn identity_query_preserves_an_active_write_transaction() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("transaction.sqlite3");
        let first = private_connection(&path);
        // DELETE mode places the writer lock on the main file; a WAL writer
        // lock on -shm would not detect an accidental close of the main fd.
        first
            .execute_batch("PRAGMA journal_mode=DELETE; CREATE TABLE witness(value INTEGER);")
            .unwrap();
        first
            .execute_batch("BEGIN IMMEDIATE; INSERT INTO witness VALUES(1);")
            .unwrap();
        let original = connection_identity(&first).unwrap();
        assert_eq!(original, connection_identity(&first).unwrap());

        // A second connection in this process could report BUSY using
        // SQLite's in-process lock bookkeeping after the OS lock was lost.
        // Require an actual second process to observe the retained lock.
        let stdout_path = directory.path().join("writer-probe.stdout");
        let stderr_path = directory.path().join("writer-probe.stderr");
        let child = test_binary_command("sqlite_identity::tests::external_writer_lock_probe")
            .arg("--ignored")
            .env(LOCK_PROBE_DATABASE, &path)
            // Files avoid a full pipe blocking the child before its deadline.
            .stdout(std::fs::File::create(&stdout_path).unwrap())
            .stderr(std::fs::File::create(&stderr_path).unwrap())
            .spawn()
            .unwrap();
        let mut child = ExitChildGuard(Some(child));
        let status = wait_for_child(&mut child);
        let stdout_bytes = std::fs::read(&stdout_path).unwrap();
        let stderr_bytes = std::fs::read(&stderr_path).unwrap();
        let stdout = String::from_utf8_lossy(&stdout_bytes);
        let stderr = String::from_utf8_lossy(&stderr_bytes);
        assert!(
            status.success(),
            "external writer probe failed\nstdout:\n{stdout}\nstderr:\n{stderr}"
        );
        assert!(
            stdout.contains(LOCK_PROBE_MARKER),
            "external writer probe did not execute its BUSY oracle:\n{stdout}"
        );
        first
            .execute_batch("INSERT INTO witness VALUES(3); COMMIT;")
            .unwrap();
        let second = private_connection(&path);
        assert_eq!(
            second
                .query_row("SELECT COUNT(*) FROM witness", [], |row| row
                    .get::<_, u64>(0))
                .unwrap(),
            2
        );
    }

    #[test]
    #[ignore = "Requires the explicit database supplied by the parent lock-preservation test"]
    fn external_writer_lock_probe() {
        let path = std::env::var_os(LOCK_PROBE_DATABASE)
            .expect("the parent test must supply its retained database");
        let connection = private_connection(std::path::Path::new(&path));
        connection.busy_timeout(std::time::Duration::ZERO).unwrap();
        assert!(matches!(
            connection.execute("INSERT INTO witness VALUES(2)", []),
            Err(rusqlite::Error::SqliteFailure(error, _))
                if error.code == rusqlite::ErrorCode::DatabaseBusy
        ));
        println!("\n{LOCK_PROBE_MARKER}");
    }

    #[test]
    fn copied_logical_database_has_a_different_physical_identity() {
        let directory = tempfile::tempdir().unwrap();
        let original = directory.path().join("original.sqlite3");
        let copied = directory.path().join("copied.sqlite3");
        {
            let database = private_connection(&original);
            database.execute_batch("CREATE TABLE witness(value TEXT); INSERT INTO witness VALUES('same logical payload');").unwrap();
        }
        std::fs::copy(&original, &copied).unwrap();
        let first = private_connection(&original);
        let second = private_connection(&copied);
        let payload = |connection: &Connection| {
            connection
                .query_row("SELECT value FROM witness", [], |row| {
                    row.get::<_, String>(0)
                })
                .unwrap()
        };
        assert_eq!(payload(&first), payload(&second));
        assert_ne!(
            connection_identity(&first).unwrap(),
            connection_identity(&second).unwrap()
        );
    }

    #[test]
    fn memory_database_cannot_supply_a_physical_file_identity() {
        let connection = Connection::open_in_memory_with_flags(private_flags()).unwrap();
        assert!(connection_identity(&connection).is_err());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn retained_identity_survives_path_replacement_and_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("selected.sqlite3");
        let moved = directory.path().join("retained.sqlite3");
        let replacement = directory.path().join("replacement.sqlite3");
        {
            let database = private_connection(&path);
            database
                .execute_batch(
                    "PRAGMA journal_mode=DELETE;
                     CREATE TABLE witness(value TEXT);
                     INSERT INTO witness VALUES('same logical payload');",
                )
                .unwrap();
        }
        // Copy only after the producing connection and journal are closed.
        std::fs::copy(&path, &replacement).unwrap();
        let retained = private_connection(&path);
        let retained_identity = connection_identity(&retained).unwrap();
        let logical_payload: String = retained
            .query_row("SELECT value FROM witness", [], |row| row.get(0))
            .unwrap();
        std::fs::rename(&path, &moved).unwrap();
        std::fs::rename(&replacement, &path).unwrap();
        let selected = private_connection(&path);
        let replacement_payload: String = selected
            .query_row("SELECT value FROM witness", [], |row| row.get(0))
            .unwrap();
        assert_eq!(logical_payload, replacement_payload);
        assert_eq!(retained_identity, connection_identity(&retained).unwrap());
        assert_ne!(retained_identity, connection_identity(&selected).unwrap());
        // No writes use the renamed connections or their original pathname.
        drop(selected);
        drop(retained);
        let reopened = private_connection(&moved);
        assert_eq!(retained_identity, connection_identity(&reopened).unwrap());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn repeated_identity_queries_restore_the_actual_vfs_mmap_limit() {
        use rusqlite::ffi;

        fn actual_limit(connection: &Connection) -> ffi::sqlite3_int64 {
            let mut value: ffi::sqlite3_int64 = -1;
            // SAFETY: this public control reads the limit into one aligned
            // sqlite3_int64. The negative input requests no mutation.
            let status = unsafe {
                ffi::sqlite3_file_control(
                    connection.handle(),
                    c"main".as_ptr(),
                    ffi::SQLITE_FCNTL_MMAP_SIZE,
                    std::ptr::from_mut(&mut value).cast(),
                )
            };
            assert_eq!(status, ffi::SQLITE_OK);
            value
        }

        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("limits.sqlite3");
        let connection = private_connection(&path);
        connection
            .execute_batch(
                "PRAGMA journal_mode=DELETE;
                 CREATE TABLE witness(value INTEGER);
                 INSERT INTO witness VALUES(1);",
            )
            .unwrap();
        let mut original_identity = None;
        for requested in [0_i64, 4096_i64, 0_i64] {
            connection
                .pragma_update(None, "mmap_size", requested)
                .unwrap();
            assert_eq!(actual_limit(&connection), requested);
            for _ in 0..4 {
                let identity = connection_identity(&connection).unwrap();
                if let Some(original) = original_identity.as_ref() {
                    assert_eq!(original, &identity);
                } else {
                    original_identity = Some(identity);
                }
                // Read the VFS limit directly: a PRAGMA query could itself
                // change pager configuration and hide a failed restoration.
                assert_eq!(actual_limit(&connection), requested);
            }
        }
        connection
            .execute("INSERT INTO witness VALUES(2)", [])
            .unwrap();
    }
}
