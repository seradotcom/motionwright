use super::*;
use crate::av_delivery::DeliveryHandle;
use std::io::Write;

// Explicit synthetic MP4-shaped bytes test the owner read boundary, not a
// renderer, decoder, caption attestation, human approval or physical outcome.
struct Fixture {
    root: tempfile::TempDir,
    project: Project,
    source: DeliverySource,
    deliveries: NativeMasterDeliveryRegistry,
    token: Uuid,
    bytes: Vec<u8>,
}

impl Fixture {
    fn new(size: usize) -> Self {
        let root = tempfile::tempdir().unwrap();
        let project = Project::new("Original bounded review source").unwrap();
        let mut bytes = (0..size)
            .map(|index| (index % 251) as u8)
            .collect::<Vec<_>>();
        bytes[4..8].copy_from_slice(b"ftyp");
        std::fs::write(root.path().join("master.mp4"), &bytes).unwrap();
        let token = Uuid::new_v4();
        let source = DeliverySource {
            owner_root: root.path().to_path_buf(),
            relative_path: "master.mp4".into(),
            sha256: hex::encode(Sha256::digest(&bytes)),
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            deliverable_id: project.deliverables[0].id,
            frame_rate_num: 30,
            frame_rate_den: 1,
            frame_count: 120,
            review_only: true,
            audio_analysis: None,
        };
        let deliveries = NativeMasterDeliveryRegistry::default();
        deliveries
            .entries
            .lock()
            .unwrap()
            .push_back(DeliveryHandle {
                token,
                project_id: source.project_id,
                generation: source.generation,
                revision: source.revision,
                deliverable_id: source.deliverable_id,
                frame_rate_num: 30,
                frame_rate_den: 1,
                frame_count: 120,
                owner_root: source.owner_root.clone(),
                relative_path: source.relative_path.clone(),
                sha256: source.sha256.clone(),
                review_only: true,
                audio_analysis: None,
            });
        Self {
            root,
            project,
            source,
            deliveries,
            token,
            bytes,
        }
    }

    fn request(&self, streams: &ReviewStreams, stream: Uuid, range: &str) -> Response<Vec<u8>> {
        let request = Request::builder()
            .uri(media_url(stream))
            .header(header::RANGE, range)
            .body(Vec::new())
            .unwrap();
        streams
            .response(stream, &self.project, &self.deliveries, &request)
            .unwrap()
    }
}

#[test]
fn master_above_ipc_limit_plays_exact_bounded_ranges_and_seeks() {
    let fixture = Fixture::new(16 * 1024 * 1024 + 257);
    assert!(fixture.source.review_bytes().is_err());
    let streams = ReviewStreams::default();
    let descriptor = streams.prepare(&fixture.source, fixture.token).unwrap();
    assert_eq!(descriptor.size_bytes, fixture.bytes.len() as u64);
    assert_ne!(descriptor.stream_token, fixture.token);
    let first = fixture.request(&streams, descriptor.stream_token, "bytes=0-");
    assert_eq!(first.status(), StatusCode::PARTIAL_CONTENT);
    assert_eq!(first.body(), &fixture.bytes[..MAX_RANGE_BYTES as usize]);
    assert_eq!(
        first.headers()[header::CONTENT_LENGTH],
        MAX_RANGE_BYTES.to_string()
    );
    let cross = fixture.request(&streams, descriptor.stream_token, "bytes=1048568-1048590");
    assert_eq!(cross.body(), &fixture.bytes[1048568..=1048590]);
    let tail = fixture.request(&streams, descriptor.stream_token, "bytes=-73");
    assert_eq!(tail.body(), &fixture.bytes[fixture.bytes.len() - 73..]);
    assert!(
        !fixture.source.export_allowed(),
        "read-only playback cannot promote caption export"
    );
    assert_eq!(fixture.deliveries.entries.lock().unwrap().len(), 1);
    let public = serde_json::to_value(&descriptor).unwrap();
    assert!(public.get("path").is_none() && public.get("owner_root").is_none());
    assert!(
        !serde_json::to_string(&descriptor)
            .unwrap()
            .contains("master.mp4")
    );
}

#[test]
fn every_returned_block_rejects_same_length_in_place_byte_changes() {
    let fixture = Fixture::new(2 * BLOCK_BYTES + 19);
    let streams = ReviewStreams::default();
    let descriptor = streams.prepare(&fixture.source, fixture.token).unwrap();
    let mut writer = File::options()
        .write(true)
        .open(fixture.root.path().join("master.mp4"))
        .unwrap();
    writer
        .seek(SeekFrom::Start(BLOCK_BYTES as u64 + 25))
        .unwrap();
    writer.write_all(b"changed").unwrap();
    writer.sync_all().unwrap();
    let request = Request::builder()
        .header(header::RANGE, "bytes=1048576-1048608")
        .body(Vec::new())
        .unwrap();
    assert_eq!(
        streams
            .response(
                descriptor.stream_token,
                &fixture.project,
                &fixture.deliveries,
                &request
            )
            .unwrap_err(),
        StatusCode::GONE
    );
}

#[test]
fn changed_length_or_incomplete_original_is_never_released() {
    let fixture = Fixture::new(BLOCK_BYTES + 17);
    let streams = ReviewStreams::default();
    let descriptor = streams.prepare(&fixture.source, fixture.token).unwrap();
    File::options()
        .write(true)
        .open(fixture.root.path().join("master.mp4"))
        .unwrap()
        .set_len(200)
        .unwrap();
    let request = Request::builder()
        .header(header::RANGE, "bytes=0-99")
        .body(Vec::new())
        .unwrap();
    assert_eq!(
        streams
            .response(
                descriptor.stream_token,
                &fixture.project,
                &fixture.deliveries,
                &request
            )
            .unwrap_err(),
        StatusCode::GONE
    );
    assert!(
        ReviewStreams::default()
            .prepare(&fixture.source, fixture.token)
            .is_err()
    );
}

#[test]
fn a_replaced_descriptor_cannot_pass_the_original_complete_digest() {
    let fixture = Fixture::new(80);
    let other = fixture.root.path().join("substituted.mp4");
    let mut bytes = fixture.bytes.clone();
    bytes[25] ^= 1;
    std::fs::write(&other, bytes).unwrap();
    assert!(
        ReviewStream::capture_file(File::open(other).unwrap(), &fixture.source, fixture.token)
            .is_err()
    );
    let mut invalid = fixture.bytes.clone();
    invalid[4..8].copy_from_slice(b"html");
    std::fs::write(fixture.root.path().join("master.mp4"), &invalid).unwrap();
    let mut source = fixture.source;
    source.sha256 = hex::encode(Sha256::digest(invalid));
    assert!(
        ReviewStreams::default()
            .prepare(&source, fixture.token)
            .is_err()
    );
}

#[test]
fn stale_foreign_removed_profile_and_evicted_delivery_have_no_read_authority() {
    let fixture = Fixture::new(80);
    let streams = ReviewStreams::default();
    let descriptor = streams.prepare(&fixture.source, fixture.token).unwrap();
    let request = Request::builder()
        .header(header::RANGE, "bytes=0-20")
        .body(Vec::new())
        .unwrap();
    for kind in 0..4 {
        let mut project = fixture.project.clone();
        match kind {
            0 => project.id = Uuid::new_v4(),
            1 => project.generation = Uuid::new_v4(),
            2 => project.revision += 1,
            _ => project.deliverables.clear(),
        }
        assert_eq!(
            streams
                .response(
                    descriptor.stream_token,
                    &project,
                    &fixture.deliveries,
                    &request
                )
                .unwrap_err(),
            StatusCode::GONE
        );
    }
    fixture.deliveries.entries.lock().unwrap().clear();
    assert_eq!(
        streams
            .response(
                descriptor.stream_token,
                &fixture.project,
                &fixture.deliveries,
                &request
            )
            .unwrap_err(),
        StatusCode::GONE
    );
    assert!(
        ReviewStreams::default()
            .find(descriptor.stream_token)
            .is_err()
    );
}

#[test]
fn stream_cache_is_bounded_and_duplicate_prepare_retains_the_original_reader() {
    let fixture = Fixture::new(80);
    let streams = ReviewStreams::default();
    let first = streams.prepare(&fixture.source, fixture.token).unwrap();
    assert_eq!(
        streams
            .prepare(&fixture.source, fixture.token)
            .unwrap()
            .stream_token,
        first.stream_token
    );
    assert_eq!(streams.streams.lock().unwrap().len(), 1);
    for _ in 0..MAX_STREAMS {
        streams.prepare(&fixture.source, Uuid::new_v4()).unwrap();
    }
    assert_eq!(streams.streams.lock().unwrap().len(), MAX_STREAMS);
    assert!(streams.find(first.stream_token).is_err());
}

#[test]
fn http_range_parser_cannot_expand_the_read_budget_or_select_multiple_sources() {
    assert_eq!(byte_range(Some("bytes=3-5"), 10, false), Ok((3, 5, true)));
    assert_eq!(byte_range(Some("bytes=7-"), 10, false), Ok((7, 9, true)));
    assert_eq!(byte_range(Some("bytes=-3"), 10, false), Ok((7, 9, true)));
    assert_eq!(byte_range(Some("bytes=2-900"), 10, false), Ok((2, 9, true)));
    assert_eq!(byte_range(None, 10, false), Ok((0, 9, false)));
    assert_eq!(
        byte_range(None, MAX_MASTER_BYTES, true),
        Ok((0, MAX_MASTER_BYTES - 1, false))
    );
    assert!(byte_range(None, MAX_RANGE_BYTES + 1, false).is_err());
    assert_eq!(
        byte_range(Some("bytes=0-"), MAX_MASTER_BYTES, false),
        Ok((0, MAX_RANGE_BYTES - 1, true))
    );
    for range in [
        "bytes=-0",
        "bytes=10-",
        "bytes=5-2",
        "bytes=0-1,3-4",
        "bytes= 1-2",
        "bytes=18446744073709551616-",
        "bytes=0-18446744073709551616",
        "items=0-1",
        "bytes=--1",
    ] {
        assert!(byte_range(Some(range), 10, false).is_err(), "{range}");
    }
}

#[test]
fn media_protocol_accepts_only_opaque_canonical_local_tokens() {
    let token = Uuid::new_v4();
    for uri in [
        format!("http://motionwright-media.localhost/{token}"),
        format!("motionwright-media://localhost/{token}"),
    ] {
        assert_eq!(request_token(&uri.parse().unwrap()).unwrap(), token);
    }
    for uri in [
        format!("https://motionwright-media.localhost/{token}"),
        format!("http://motionwright-media.localhost:80/{token}"),
        format!("http://motionwright-media.localhost.evil/{token}"),
        format!("motionwright-media://foreign/{token}"),
        format!("motionwright-media://localhost/{token}?path=private"),
        format!("motionwright-media://localhost/{token}/private"),
        format!(
            "motionwright-media://localhost/{}",
            token.to_string().to_uppercase()
        ),
        "motionwright-media://localhost/%2e%2e/private".into(),
        "file:///private/master.mp4".into(),
    ] {
        // Some non-HTTP values (notably file:///...) are rejected by http
        // before a protocol request can exist. Either boundary must reject.
        if let Ok(parsed) = uri.parse::<Uri>() {
            assert!(request_token(&parsed).is_err(), "{uri}");
        }
    }
}

#[test]
fn head_methods_request_bodies_and_duplicate_headers_preserve_the_read_boundary() {
    let fixture = Fixture::new(80);
    let streams = ReviewStreams::default();
    let descriptor = streams.prepare(&fixture.source, fixture.token).unwrap();
    let request = Request::builder()
        .method(Method::HEAD)
        .body(Vec::new())
        .unwrap();
    let response = streams
        .response(
            descriptor.stream_token,
            &fixture.project,
            &fixture.deliveries,
            &request,
        )
        .unwrap();
    assert!(response.body().is_empty());
    assert_eq!(response.headers()[header::CONTENT_LENGTH], "80");
    for request in [
        Request::builder()
            .method(Method::POST)
            .body(Vec::new())
            .unwrap(),
        Request::builder().body(vec![1]).unwrap(),
        Request::builder()
            .header(header::RANGE, "bytes=0-2")
            .header(header::RANGE, "bytes=3-4")
            .body(Vec::new())
            .unwrap(),
    ] {
        assert!(
            streams
                .response(
                    descriptor.stream_token,
                    &fixture.project,
                    &fixture.deliveries,
                    &request
                )
                .is_err()
        );
    }
    let invalid = Request::builder()
        .header(header::RANGE, "bytes=80-")
        .body(Vec::new())
        .unwrap();
    let response = streams
        .response(
            descriptor.stream_token,
            &fixture.project,
            &fixture.deliveries,
            &invalid,
        )
        .unwrap();
    assert_eq!(response.status(), StatusCode::RANGE_NOT_SATISFIABLE);
    assert_eq!(response.headers()[header::CONTENT_RANGE], "bytes */80");
    assert!(response.body().is_empty());
}

#[test]
fn queued_media_readers_are_bounded_before_workers_and_released_after_response() {
    let streams = ReviewStreams::default();
    let clone = streams.clone();
    let mut permits = Vec::new();
    for _ in 0..MAX_ACTIVE_READS {
        permits.push(streams.admit_read().expect("bounded available slot"));
    }
    assert!(streams.admit_read().is_none());
    assert!(clone.admit_read().is_none());
    drop(permits.pop());
    let replacement = clone.admit_read().unwrap();
    assert!(streams.admit_read().is_none());
    drop(replacement);
    drop(permits);
    assert_eq!(streams.active_reads.load(Ordering::Acquire), 0);
}

#[test]
fn playback_selectors_cannot_mix_receipt_hints_with_session_or_private_file_authority() {
    let session = serde_json::json!({
        "project_id":Uuid::new_v4(),"generation":Uuid::new_v4(),"revision":7,
        "export_token":Uuid::new_v4(),"stream":true,
    });
    assert!(matches!(
        serde_json::from_value::<PlaybackSelection>(session.clone()).unwrap(),
        PlaybackSelection::Session(_)
    ));
    let receipt = serde_json::json!({
        "project_id":Uuid::new_v4(),"generation":Uuid::new_v4(),"revision":7,
        "deliverable_id":Uuid::new_v4(),"decoded_measurement_receipt_id":Uuid::new_v4(),
        "expected_master_sha256":"1".repeat(64),
    });
    assert!(matches!(
        serde_json::from_value::<PlaybackSelection>(receipt.clone()).unwrap(),
        PlaybackSelection::Receipt(_)
    ));
    for key in [
        "export_token",
        "stream",
        "path",
        "owner_root",
        "review_only",
        "permission",
        "native_receipt",
    ] {
        let mut forged = receipt.clone();
        forged[key] = serde_json::json!("untrusted");
        assert!(
            serde_json::from_value::<PlaybackSelection>(forged).is_err(),
            "{key}"
        );
    }
    let mut mixed = session;
    mixed["decoded_measurement_receipt_id"] = serde_json::json!(Uuid::new_v4());
    assert!(serde_json::from_value::<PlaybackSelection>(mixed).is_err());
}

#[test]
fn overlapping_decoder_ranges_wait_within_admission_and_keep_exact_original_bytes() {
    let fixture = Fixture::new(2 * BLOCK_BYTES + 257);
    let streams = ReviewStreams::default();
    let descriptor = streams.prepare(&fixture.source, fixture.token).unwrap();
    let stream = streams.find(descriptor.stream_token).unwrap();
    let barrier = std::sync::Barrier::new(MAX_ACTIVE_READS);
    std::thread::scope(|scope| {
        let mut workers = Vec::new();
        for worker in 0..MAX_ACTIVE_READS {
            let stream = stream.clone();
            let permit = streams.admit_read().unwrap();
            let original = &fixture.bytes;
            let barrier = &barrier;
            workers.push(scope.spawn(move || {
                let _permit = permit;
                barrier.wait();
                for offset in 0..8 {
                    let start =
                        ((worker * 29137 + offset * 137389) % (original.len() - 1024)) as u64;
                    let bytes = stream.read_range(start, start + 1023).unwrap();
                    assert_eq!(bytes, original[start as usize..start as usize + 1024]);
                }
            }));
        }
        for worker in workers {
            worker.join().unwrap();
        }
    });
    assert_eq!(streams.active_reads.load(Ordering::Acquire), 0);
}

#[test]
fn abandoned_preparation_waiter_does_not_release_a_running_original_reader() {
    use std::{sync::mpsc, time::Duration};
    let streams = ReviewStreams::default();
    let clone = streams.clone();
    let preparing = streams.admit_preparation().unwrap();
    let other = clone.admit_preparation().unwrap();
    assert!(streams.admit_preparation().is_none());
    // Existing decoder ranges have an independent, bounded capacity.
    let ranges = (0..MAX_ACTIVE_READS)
        .map(|_| streams.admit_read().unwrap())
        .collect::<Vec<_>>();
    let (entered_tx, entered_rx) = mpsc::channel();
    let (release_tx, release_rx) = mpsc::channel();
    let (done_tx, done_rx) = mpsc::channel();
    let worker = tauri::async_runtime::spawn_blocking(move || {
        let permit = preparing;
        entered_tx.send(()).unwrap();
        let _ = release_rx.recv();
        drop(permit);
        done_tx.send(()).unwrap();
    });
    entered_rx.recv_timeout(Duration::from_secs(5)).unwrap();
    // Dropping an IPC wait does not stop already-running blocking IO.
    drop(worker);
    assert!(clone.admit_preparation().is_none());
    drop(other);
    let replacement = streams.admit_preparation().unwrap();
    assert!(clone.admit_preparation().is_none());
    release_tx.send(()).unwrap();
    done_rx.recv_timeout(Duration::from_secs(5)).unwrap();
    assert_eq!(clone.active_preparations.load(Ordering::Acquire), 1);
    drop(replacement);
    drop(ranges);
    assert_eq!(clone.active_preparations.load(Ordering::Acquire), 0);
    assert_eq!(clone.active_reads.load(Ordering::Acquire), 0);
    assert!(clone.admit_preparation().is_some());
}

#[test]
fn saved_mux_selector_is_closed_and_cannot_mix_authority_sources() {
    let selected = serde_json::json!({
        "project_id":Uuid::new_v4(),"generation":Uuid::new_v4(),"revision":7,
        "deliverable_id":Uuid::new_v4(),"mux_receipt_id":Uuid::new_v4(),
        "expected_master_sha256":"1".repeat(64),
    });
    assert!(matches!(
        serde_json::from_value::<PlaybackSelection>(selected.clone()).unwrap(),
        PlaybackSelection::SavedMux(_)
    ));
    for key in [
        "export_token",
        "decoded_measurement_receipt_id",
        "path",
        "owner_root",
        "stream",
        "permission",
        "review_only",
        "native_receipt",
    ] {
        let mut mixed = selected.clone();
        mixed[key] = serde_json::json!("untrusted");
        assert!(
            serde_json::from_value::<PlaybackSelection>(mixed).is_err(),
            "{key}"
        );
    }
}
