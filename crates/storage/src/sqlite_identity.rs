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

#[cfg(not(windows))]
pub(super) fn connection_identity(_connection: &Connection) -> Result<String> {
    // No cast to SQLite's private unixFile layout and no pathname reopen.
    Err(unavailable())
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use rusqlite::Connection;

    #[test]
    fn independent_connections_to_the_same_file_have_the_same_identity() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("shared.sqlite3");
        let first = Connection::open(&path).unwrap();
        first
            .execute_batch("CREATE TABLE witness(value INTEGER); INSERT INTO witness VALUES(7);")
            .unwrap();
        let second = Connection::open(&path).unwrap();
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
        let first = Connection::open(&path).unwrap();
        first
            .execute_batch("CREATE TABLE witness(value INTEGER);")
            .unwrap();
        let second = Connection::open(&path).unwrap();
        second.busy_timeout(std::time::Duration::ZERO).unwrap();
        first
            .execute_batch("BEGIN IMMEDIATE; INSERT INTO witness VALUES(1);")
            .unwrap();
        let original = connection_identity(&first).unwrap();
        assert_eq!(original, connection_identity(&first).unwrap());
        assert!(matches!(
            second.execute("INSERT INTO witness VALUES(2)", []),
            Err(rusqlite::Error::SqliteFailure(error, _))
                if error.code == rusqlite::ErrorCode::DatabaseBusy
        ));
        first
            .execute_batch("INSERT INTO witness VALUES(3); COMMIT;")
            .unwrap();
        assert_eq!(
            second
                .query_row("SELECT COUNT(*) FROM witness", [], |row| row
                    .get::<_, u64>(0))
                .unwrap(),
            2
        );
    }

    #[test]
    fn copied_logical_database_has_a_different_physical_identity() {
        let directory = tempfile::tempdir().unwrap();
        let original = directory.path().join("original.sqlite3");
        let copied = directory.path().join("copied.sqlite3");
        {
            let database = Connection::open(&original).unwrap();
            database.execute_batch("CREATE TABLE witness(value TEXT); INSERT INTO witness VALUES('same logical payload');").unwrap();
        }
        std::fs::copy(&original, &copied).unwrap();
        let first = Connection::open(&original).unwrap();
        let second = Connection::open(&copied).unwrap();
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
        assert!(connection_identity(&Connection::open_in_memory().unwrap()).is_err());
    }
}
