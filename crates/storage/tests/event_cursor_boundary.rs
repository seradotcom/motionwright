//! Event cursors represent unsigned revision sequence numbers. A cursor beyond
//! SQLite's signed integer range must exhaust the journal, not wrap negative.
use motionwright_domain::Change;
use motionwright_storage::Store;

#[test]
fn unsigned_event_cursor_overflow_never_replays_consumed_history() {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("events.sqlite3");
    let mut store = Store::open(&database).unwrap();
    let mut project = store
        .create_named_project("Events from durable project")
        .unwrap();
    let unrelated = store.create_named_project("Unrelated project").unwrap();

    for revision in 1..=3 {
        project = store
            .apply(
                project.id,
                &project.stamp(),
                &format!("rename-event-{revision}"),
                &Change::RenameProject {
                    title: format!("Revision {revision}"),
                },
            )
            .unwrap()
            .project;
    }
    assert_eq!(project.revision, 3);
    assert!(
        store
            .event_records_since(unrelated.id, 0, 256)
            .unwrap()
            .is_empty()
    );

    let events = store.event_records_since(project.id, 0, 256).unwrap();
    assert_eq!(
        events
            .iter()
            .map(|event| event.revision)
            .collect::<Vec<_>>(),
        vec![1, 2, 3]
    );
    assert_eq!(
        store.event_records_since(project.id, 1, 1).unwrap()[0].revision,
        2
    );
    assert_eq!(
        store.event_records_through(project.id, 3, 1).unwrap()[0].revision,
        3
    );
    assert_eq!(
        store
            .event_records_through(project.id, u64::MAX, 1)
            .unwrap()[0]
            .revision,
        3
    );

    // SQL revisions are nonnegative signed i64 values. Any cursor >= i64::MAX
    // must return zero newer events; unchecked casts wrap beyond that bound.
    for cursor in [i64::MAX as u64, i64::MAX as u64 + 1, u64::MAX] {
        assert!(
            store
                .event_records_since(project.id, cursor, 256)
                .unwrap()
                .is_empty(),
            "out-of-range cursor returned old events: {cursor}"
        );
        assert!(
            store
                .events_since(project.id, cursor, 256)
                .unwrap()
                .is_empty(),
            "compatibility API returned replayed history: {cursor}"
        );
    }

    drop(store);
    let reopened = Store::open(&database).unwrap();
    assert_eq!(reopened.load_project(project.id).unwrap(), project);
    assert_eq!(
        reopened
            .event_records_since(project.id, 0, 256)
            .unwrap()
            .len(),
        3
    );
    for cursor in [i64::MAX as u64 + 1, u64::MAX] {
        assert!(
            reopened
                .event_records_since(project.id, cursor, 1)
                .unwrap()
                .is_empty(),
            "reopen repeated already-consumed events: {cursor}"
        );
        assert!(
            reopened
                .events_since(project.id, cursor, 1)
                .unwrap()
                .is_empty()
        );
    }
}
