//! Owned SQLite regressions for durable known-request result integrity.
//! These cases never retry an UNKNOWN operation or invoke a provider.

use motionwright_domain::{Change, Project};
use motionwright_storage::{ApplyOutcome, Store};
use rusqlite::{Connection, params, types::Value};
use uuid::Uuid;

type Snapshot = Vec<Vec<Vec<Value>>>;

fn snapshot(connection: &Connection) -> Snapshot {
    [
        "SELECT * FROM projects ORDER BY id",
        "SELECT * FROM events ORDER BY project_id,revision",
        "SELECT * FROM receipts ORDER BY project_id,request_id",
    ]
    .into_iter()
    .map(|sql| {
        let mut statement = connection.prepare(sql).unwrap();
        let columns = statement.column_count();
        let rows = statement
            .query_map([], |row| {
                (0..columns)
                    .map(|column| row.get::<_, Value>(column))
                    .collect::<rusqlite::Result<Vec<_>>>()
            })
            .unwrap();
        rows.map(|row| row.unwrap()).collect()
    })
    .collect()
}

struct KnownRequest {
    // Drop the store before the owned temporary directory.
    store: Store,
    base: Project,
    change: Change,
    original: ApplyOutcome,
    _directory: tempfile::TempDir,
}

fn known_request() -> KnownRequest {
    let directory = tempfile::tempdir().unwrap();
    let mut store = Store::open(directory.path().join("state.sqlite3")).unwrap();
    let base = store.create_named_project("Original project").unwrap();
    let change = Change::RenameProject {
        title: "First committed title".into(),
    };
    let original = store
        .apply(base.id, &base.stamp(), "known-first", &change)
        .unwrap();
    assert!(!original.replayed);
    KnownRequest {
        store,
        base,
        change,
        original,
        _directory: directory,
    }
}

fn reject_replaced_result(edit: impl FnOnce(&mut ApplyOutcome, &mut Store)) {
    let mut fixture = known_request();
    let mut replaced = fixture.original.clone();
    edit(&mut replaced, &mut fixture.store);
    let oracle = Connection::open(fixture.store.path()).unwrap();
    assert_eq!(
        oracle
            .execute(
                "UPDATE receipts SET result_json=?1 WHERE project_id=?2 AND request_id=?3",
                params![
                    serde_json::to_string(&replaced).unwrap(),
                    fixture.base.id.to_string(),
                    &fixture.original.request_id,
                ],
            )
            .unwrap(),
        1
    );
    // The request key and digest stay original. Compare all logical SQL rows
    // after deliberate fixture corruption, including the corrupt stored JSON.
    oracle.execute_batch("PRAGMA query_only=ON").unwrap();
    let before = snapshot(&oracle);
    let result = fixture.store.apply(
        fixture.base.id,
        &fixture.base.stamp(),
        &fixture.original.request_id,
        &fixture.change,
    );
    assert_eq!(snapshot(&oracle), before);
    assert!(result.is_err(), "accepted corrupt known result: {result:?}");
}

#[test]
fn known_request_rejects_a_valid_result_from_another_project() {
    reject_replaced_result(|outcome, store| {
        let donor = store.create_named_project("Unrelated project").unwrap();
        let foreign = store
            .apply(
                donor.id,
                &donor.stamp(),
                "foreign-known",
                &Change::RenameProject {
                    title: "Unrelated committed title".into(),
                },
            )
            .unwrap();
        assert_eq!(foreign.previous_revision, outcome.previous_revision);
        assert_eq!(foreign.project.revision, outcome.project.revision);
        assert_ne!(foreign.project.id, outcome.project.id);
        foreign.project.validate().unwrap();
        outcome.project = foreign.project;
    });
}

#[test]
fn known_request_rejects_a_different_embedded_request_id() {
    reject_replaced_result(|outcome, _| {
        outcome.request_id = "another-request".into();
    });
}

#[test]
fn known_request_rejects_a_different_result_generation() {
    reject_replaced_result(|outcome, _| {
        let original = outcome.project.generation;
        outcome.project.generation = Uuid::now_v7();
        assert_ne!(outcome.project.generation, original);
        outcome.project.validate().unwrap();
    });
}

#[test]
fn known_request_rejects_a_persisted_replay_flag() {
    reject_replaced_result(|outcome, _| {
        outcome.replayed = true;
    });
}

#[test]
fn known_request_rejects_an_inconsistent_revision_transition() {
    reject_replaced_result(|outcome, _| {
        outcome.previous_revision = outcome.project.revision;
        outcome.project.validate().unwrap();
    });
}

#[test]
fn known_request_rejects_a_valid_result_ahead_of_current_authority() {
    reject_replaced_result(|outcome, _| {
        let change = Change::RenameProject {
            title: "Never committed to SQLite".into(),
        };
        let previous = outcome.project.revision;
        outcome.project.apply_change(&change).unwrap();
        outcome
            .project
            .commit_revision(previous.checked_add(1).unwrap(), &change)
            .unwrap();
        outcome.previous_revision = previous;
        outcome.project.validate().unwrap();
    });
}

#[test]
fn known_request_rejects_a_result_that_breaks_domain_invariants() {
    reject_replaced_result(|outcome, _| {
        outcome.project.title = " ".into();
        assert!(outcome.project.validate().is_err());
    });
}

#[test]
fn known_request_rejects_a_result_bound_to_another_valid_event() {
    reject_replaced_result(|outcome, store| {
        let later = store
            .apply(
                outcome.project.id,
                &outcome.project.stamp(),
                "known-second",
                &Change::RenameProject {
                    title: "Second committed title".into(),
                },
            )
            .unwrap();
        outcome.project = later.project;
        outcome.previous_revision = later.previous_revision;
        // Identity, key, generation, replay state and revision delta remain
        // plausible. Only the original intent/event binding distinguishes it.
        outcome.project.validate().unwrap();
        assert_eq!(
            outcome.previous_revision.checked_add(1),
            Some(outcome.project.revision)
        );
    });
}

enum EventDamage {
    Missing,
    DifferentPayload,
    DifferentKind,
}

fn reject_corrupt_event(damage: EventDamage) {
    let mut fixture = known_request();
    let oracle = Connection::open(fixture.store.path()).unwrap();
    let revision = i64::try_from(fixture.original.project.revision).unwrap();
    let project_id = fixture.base.id.to_string();
    let changed = match damage {
        EventDamage::Missing => oracle
            .execute(
                "DELETE FROM events WHERE project_id=?1 AND revision=?2",
                params![project_id, revision],
            )
            .unwrap(),
        EventDamage::DifferentPayload => oracle
            .execute(
                "UPDATE events SET payload_json=?3 WHERE project_id=?1 AND revision=?2",
                params![
                    project_id,
                    revision,
                    serde_json::to_string(&Change::RenameProject {
                        title: "Different recorded intent".into()
                    })
                    .unwrap(),
                ],
            )
            .unwrap(),
        EventDamage::DifferentKind => oracle
            .execute(
                "UPDATE events SET kind='foreign-kind' WHERE project_id=?1 AND revision=?2",
                params![project_id, revision],
            )
            .unwrap(),
    };
    assert_eq!(changed, 1);
    oracle.execute_batch("PRAGMA query_only=ON").unwrap();
    let before = snapshot(&oracle);
    let result = fixture.store.apply(
        fixture.base.id,
        &fixture.base.stamp(),
        &fixture.original.request_id,
        &fixture.change,
    );
    assert_eq!(snapshot(&oracle), before);
    assert!(
        result.is_err(),
        "accepted corrupt event binding: {result:?}"
    );
}

#[test]
fn known_request_rejects_a_missing_commit_event() {
    reject_corrupt_event(EventDamage::Missing);
}

#[test]
fn known_request_rejects_a_changed_commit_event_payload() {
    reject_corrupt_event(EventDamage::DifferentPayload);
}

#[test]
fn known_request_rejects_a_changed_commit_event_kind() {
    reject_corrupt_event(EventDamage::DifferentKind);
}

#[test]
fn known_request_replays_its_original_snapshot_after_later_edit_and_reopen() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("state.sqlite3");
    let mut store = Store::open(&path).unwrap();
    let base = store.create_named_project("Before edits").unwrap();
    let first_change = Change::RenameProject {
        title: "First committed snapshot".into(),
    };
    let first = store
        .apply(base.id, &base.stamp(), "first-known", &first_change)
        .unwrap();
    let later = store
        .apply(
            base.id,
            &first.project.stamp(),
            "later-known",
            &Change::RenameProject {
                title: "Later current snapshot".into(),
            },
        )
        .unwrap();
    assert!(later.project.revision > first.project.revision);
    drop(store);

    let oracle = Connection::open(&path).unwrap();
    oracle.execute_batch("PRAGMA query_only=ON").unwrap();
    let before = snapshot(&oracle);
    let mut reopened = Store::open(&path).unwrap();
    let replay = reopened
        .apply(base.id, &base.stamp(), "first-known", &first_change)
        .unwrap();
    let mut expected = first;
    expected.replayed = true;
    assert_eq!(replay, expected);
    assert_eq!(reopened.load_project(base.id).unwrap(), later.project);
    assert_eq!(snapshot(&oracle), before);
}
