//! Test-only SQLite v6 writer marker for controlled corruption fixtures.
//! This version function is not native authorization or a Broker grant.
use rusqlite::Connection;
use std::path::Path;

pub(super) fn registered_v6_writer(db: &Connection) {
    use rusqlite::functions::FunctionFlags;
    let version: u32 = db
        .pragma_query_value(None, "user_version", |row| row.get(0))
        .unwrap();
    assert_eq!(version, 6, "only a genuine current v6 fixture");
    db.create_scalar_function(
        "motionwright_writer_epoch_guard_v6",
        0,
        FunctionFlags::SQLITE_UTF8
            | FunctionFlags::SQLITE_DETERMINISTIC
            | FunctionFlags::SQLITE_INNOCUOUS,
        |_| Ok(6_i64),
    )
    .unwrap();
}

pub(super) fn open_v6_writer(path: impl AsRef<Path>) -> Connection {
    let db = Connection::open(path).unwrap();
    registered_v6_writer(&db);
    db
}
