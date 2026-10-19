//! Real service + SQLite + 128 original PCM WAV assets and >150 projects.
//! Bounded keyset summaries must not eagerly deserialize stored documents,
//! read immutable asset bodies, or misreport a truncated page as complete.
use motionwright_service::{ProjectCursor, StudioService};
use rusqlite::Connection;
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, fs, path::Path};

const OTHER_PROJECTS: usize = 154;
const ORIGINAL_ASSETS: usize = 128;
const PAGE_LIMIT: usize = 23;

fn pcm_wav(sample: i16) -> Vec<u8> {
    let sample_rate = 8_000_u32;
    let frames = 256_u32;
    let bytes_per_frame = 2_u32;
    let mut bytes = Vec::with_capacity(44 + (frames * bytes_per_frame) as usize);
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + frames * bytes_per_frame).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&sample_rate.to_le_bytes());
    bytes.extend_from_slice(&(sample_rate * bytes_per_frame).to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&(frames * bytes_per_frame).to_le_bytes());
    for _ in 0..frames {
        bytes.extend_from_slice(&sample.to_le_bytes());
    }
    bytes
}

fn stats(database: &Path) -> (i64, i64, i64) {
    let conn = Connection::open(database).unwrap();
    let project_count = conn
        .query_row("SELECT COUNT(*) FROM projects", [], |row| row.get(0))
        .unwrap();
    let event_count = conn
        .query_row("SELECT COUNT(*) FROM events", [], |row| row.get(0))
        .unwrap();
    let page_count = conn
        .query_row("PRAGMA page_count", [], |row| row.get(0))
        .unwrap();
    (project_count, event_count, page_count)
}

#[test]
fn large_library_service_cursor_is_bounded_exhaustive_and_document_independent() {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("many-projects.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let mut big = service.create_project("Original media library").unwrap();
    let mut asset_bytes = Vec::new();

    for index in 0..ORIGINAL_ASSETS {
        let source = directory.path().join(format!("original-{index:03}.wav"));
        let bytes = pcm_wav(index as i16 + 1);
        fs::write(&source, &bytes).unwrap();
        let digest = hex::encode(Sha256::digest(&bytes));
        let outcome = service
            .import_asset_file(
                big.id,
                &big.stamp(),
                &format!("asset-import-{index:03}"),
                &source,
                format!("Original audio {index:03}.wav"),
                "audio/wav".into(),
            )
            .unwrap();
        big = outcome.project;
        asset_bytes.push((digest, bytes));
    }
    assert_eq!(big.assets.len(), ORIGINAL_ASSETS);
    assert_eq!(big.revision, ORIGINAL_ASSETS as u64);

    for index in 0..OTHER_PROJECTS {
        service
            .create_project(&format!("Independent project {index:03}"))
            .unwrap();
    }
    let (count, events, _) = stats(&database);
    assert_eq!(count as usize, OTHER_PROJECTS + 1);
    assert_eq!(events as usize, ORIGINAL_ASSETS);

    // Synthetic corruption makes eager Project JSON deserialization fail.
    // The summary API intentionally reads only indexed SQL authority columns.
    let conn = Connection::open(&database).unwrap();
    conn.execute(
        "UPDATE projects SET document_json='not-an-actual-project-document'
         WHERE id=?1",
        [big.id.to_string()],
    )
    .unwrap();
    drop(conn);
    assert!(service.project(big.id).is_err());
    let before_page = stats(&database);

    let mut cursor: Option<ProjectCursor> = None;
    let mut seen = BTreeSet::new();
    let mut page_count = 0_usize;
    let mut first_truncated = false;
    loop {
        let page = service
            .project_summaries(cursor.as_ref(), PAGE_LIMIT)
            .unwrap();
        page_count += 1;
        assert!(page_count <= 10, "unbounded cursor did not terminate");
        assert!(!page.items.is_empty());
        assert!(page.items.len() <= PAGE_LIMIT);
        for item in &page.items {
            assert!(
                seen.insert(item.id),
                "duplicate ID after paging: {}",
                item.id
            );
            assert!(!item.title.is_empty());
        }
        assert_eq!(page.truncated, page.next_cursor.is_some());
        if page_count == 1 {
            first_truncated = page.truncated;
        }
        if !page.truncated {
            assert!(page.next_cursor.is_none());
            break;
        }
        cursor = page.next_cursor;
    }
    assert!(first_truncated);
    assert_eq!(seen.len(), OTHER_PROJECTS + 1);
    assert!(seen.contains(&big.id));
    assert_eq!(page_count, 7);
    assert_eq!(
        stats(&database),
        before_page,
        "listing changed SQLite rows or pages"
    );
    for (digest, bytes) in &asset_bytes {
        assert_eq!(
            service.store().lock().read_blob(digest, 2048).unwrap(),
            *bytes
        );
    }

    drop(service);
    let reopened = StudioService::open(&database).unwrap();
    assert!(reopened.project(big.id).is_err());
    let first = reopened.project_summaries(None, PAGE_LIMIT).unwrap();
    assert_eq!(first.items.len(), PAGE_LIMIT);
    assert!(first.truncated);
    assert!(first.next_cursor.is_some());
    assert_eq!(stats(&database), before_page);
    for (digest, bytes) in &asset_bytes {
        assert_eq!(
            reopened.store().lock().read_blob(digest, 2048).unwrap(),
            *bytes
        );
    }
}
