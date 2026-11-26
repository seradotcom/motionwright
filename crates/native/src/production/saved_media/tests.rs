//! Synthetic receipt custody in real SQLite; no Native execution or codec proof.
use super::*;
use semwright_native_sdk::types::InvocationProvenance;
use std::sync::atomic::Ordering;

struct Fixture {
    root: tempfile::TempDir,
    service: StudioService,
    project: Project,
    coordinator: ProductionCoordinator,
    context: SavedMasterReceiptContext,
    data: Value,
    bytes: Vec<u8>,
}

impl Fixture {
    fn new() -> Self {
        let root = tempfile::tempdir().unwrap();
        let service = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        let project = service
            .create_project("Synthetic original media; no Native effect")
            .unwrap();
        let executable = root.path().join("never-executed-client");
        let session_file = root.path().join("synthetic-session");
        let socket = root.path().join("synthetic-socket");
        let output_root = root.path().join("owner-output");
        fs::write(&executable, b"Not executable; never dispatch").unwrap();
        fs::write(&session_file, b"Synthetic; no credentials").unwrap();
        fs::write(&socket, b"No Broker").unwrap();
        fs::create_dir(&output_root).unwrap();
        let connection = ProductionConnection {
            schema: CONNECTION_SCHEMA.into(),
            executable_sha256: hex::encode(Sha256::digest(fs::read(&executable).unwrap())),
            executable,
            session_file,
            socket,
            output_root,
            resource: project.resource_key(),
        };
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let profile = &project.deliverables[0];
        assert_eq!((profile.frame_rate.num, profile.frame_rate.den), (30, 1));
        let bytes = b"\0\0\0\x18ftypisomSYNTHETIC-CUSTODY-NOT-DECODABLE".to_vec();
        fs::write(
            coordinator
                .client
                .connection()
                .output_root
                .join("original.mp4"),
            &bytes,
        )
        .unwrap();
        let args = json!({
            "video_root":"output","video_path":"original.mkv","video_sha256":"9".repeat(64),
            "audio_root":"output","audio_path":"original.wav","audio_sha256":"8".repeat(64),
            "width":profile.width,"height":profile.height,"fps_num":30,"fps_den":1,"frame_count":60,
            "sample_rate":48000,"channels":2,"profile":"h264-aac-mp4","output_path":"original.mp4",
            "max_bytes":MLT_MASTER_MAX_BYTES,
        });
        let context =
            SavedMasterReceiptContext::new(&coordinator, &project, profile.id, &args).unwrap();
        let data = json!({
            "profile":"h264-aac-mp4","frame_count":60,"fps_num":30,"fps_den":1,"sample_rate":48000,"channels":2,
            "audio_sample_frames":96128,"decoded_audio_sample_frames":96128,
            "media":{"video":true,"audio":true,"width":profile.width,"height":profile.height,
                "frames":60,"duration_num":2000,"duration_den":1000,"codecs":["h264","aac"]},
            "decoded_audio_media":{"video":false,"audio":true,"codecs":["pcm_s16le"]},
            "artifact":{"root":"output","path":"original.mp4","sha256":hex::encode(Sha256::digest(&bytes)),"bytes":bytes.len()},
            "decoded_audio":{"root":"output","path":"decoded.wav","sha256":"7".repeat(64),"bytes":384556},
            "video_sha256":"9".repeat(64),"audio_sha256":"8".repeat(64),
        });
        Self {
            root,
            service,
            project,
            coordinator,
            context,
            data,
            bytes,
        }
    }

    fn seed(&self, alter: impl FnOnce(&mut Value, &mut Value)) -> ProductionReceipt {
        let execution = Execution {
            backend: "driver:mlt-video".into(),
            duration_ms: 1,
            policy_decision: "allow".into(),
            fallbacks_attempted: vec![],
            dry_run: false,
            provenance: Some(InvocationProvenance {
                provider: "driver:mlt-video".into(),
                source: SourceKind::Driver,
                provider_version: "synthetic-no-native-execution".into(),
                capability_version: "1".into(),
                descriptor_sha256: "a1".repeat(32),
                untrusted_metadata: false,
                catalog_revision: 1,
                execution_provider: Some("driver:mlt-video".into()),
                provider_generation: Some(2),
            }),
        };
        let mut intent = json!({
            "mutation":true,"connection":self.coordinator.client.connection().identity().unwrap(),
            "job_ref":null, "original_media_reopen":self.context.payload(),
        });
        let mut result = json!({
            "result":{"data":self.data,"execution":execution,"warnings":[],"provenance_checked":true},
            "broker_request_id":"synthetic-mux","job_ref":null,
            "original_media_reopen":self.context.payload(),
        });
        alter(&mut intent, &mut result);
        self.append("dispatching", intent);
        self.append("completed", result)
    }

    fn append(&self, stage: &str, payload: Value) -> ProductionReceipt {
        self.service
            .append_production_receipt(ProductionReceiptInput {
                project_id: self.project.id,
                generation: self.project.generation,
                revision: self.project.revision,
                request_id: "synthetic-original-mux".into(),
                request_sha256: request_digest(COMMAND, &self.context.args).unwrap(),
                command: COMMAND.into(),
                stage: stage.into(),
                payload,
            })
            .unwrap()
    }

    fn recover(&self, receipt: Uuid) -> NativeResult<VerifiedOriginalMasterReview> {
        self.coordinator.recover_saved_master_review(
            self.project.id,
            &self.project.stamp(),
            receipt,
            self.project.deliverables[0].id,
            self.data["artifact"]["sha256"].as_str().unwrap(),
        )
    }
}

#[test]
fn saved_master_reopens_real_sqlite_without_audio_measurements_or_new_effects() {
    let fixture = Fixture::new();
    let selected = fixture.seed(|_, _| {});
    let prior = fixture
        .service
        .production_receipts(fixture.project.id, 256)
        .unwrap();
    assert_eq!(prior.len(), 2);
    assert!(prior.iter().all(|r| r.command == COMMAND));
    let inventory = fixture
        .coordinator
        .saved_master_receipt_inventory(
            fixture.project.id,
            &fixture.project.stamp(),
            fixture.project.deliverables[0].id,
        )
        .unwrap();
    assert_eq!(inventory.items.len(), 1);
    assert_eq!(inventory.items[0].mux_receipt_id, selected.id);
    let public = serde_json::to_string(&inventory).unwrap();
    assert!(
        !public.contains("original.mp4")
            && !public.contains("owner-output")
            && !public.contains("connection")
    );
    let connection = fixture.coordinator.client.connection().clone();
    let Fixture {
        root,
        service,
        project,
        coordinator,
        bytes,
        ..
    } = fixture;
    drop(coordinator);
    drop(service);
    let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
    let current = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
    let proof = current
        .recover_saved_master_review(
            project.id,
            &project.stamp(),
            selected.id,
            project.deliverables[0].id,
            &hex::encode(Sha256::digest(&bytes)),
        )
        .unwrap();
    assert_eq!(proof.project_id(), project.id);
    assert_eq!(proof.stamp(), &project.stamp());
    assert_eq!(proof.clock(), (30, 1, 60));
    let (root, path, sha, len) = proof.artifact();
    assert_eq!(fs::read(root.join(path)).unwrap(), bytes);
    assert_eq!(sha, hex::encode(Sha256::digest(&bytes)));
    assert_eq!(len, bytes.len() as u64);
    assert_eq!(
        reopened.production_receipts(project.id, 256).unwrap(),
        prior
    );
    assert_eq!(reopened.project(project.id).unwrap(), project);
    assert_eq!(current.client.execute_calls.load(Ordering::SeqCst), 0);
}

#[test]
fn saved_master_refuses_foreign_identity_source_owner_and_changed_original_bytes() {
    let f = Fixture::new();
    let selected = f.seed(|_, _| {});
    let prior = f.service.production_receipts(f.project.id, 256).unwrap();
    for variant in 0..5 {
        let mut stamp = f.project.stamp();
        let mut receipt = selected.id;
        let mut profile = f.project.deliverables[0].id;
        let mut hash = f.data["artifact"]["sha256"].as_str().unwrap().to_owned();
        match variant {
            0 => stamp.revision += 1,
            1 => stamp.generation = Uuid::new_v4(),
            2 => receipt = Uuid::new_v4(),
            3 => profile = Uuid::new_v4(),
            _ => hash = "0".repeat(64),
        }
        assert!(
            f.coordinator
                .recover_saved_master_review(f.project.id, &stamp, receipt, profile, &hash)
                .is_err()
        );
    }
    let mut changed = f.project.clone();
    changed.title.push_str(" changed without source authority");
    assert!(f.context.check_current(&f.coordinator, &changed).is_err());
    let mut connection = f.coordinator.client.connection().clone();
    connection.output_root = f.root.path().join("different-owner");
    fs::create_dir(&connection.output_root).unwrap();
    let foreign = ProductionCoordinator::new(f.service.clone(), connection).unwrap();
    assert!(f.context.check_current(&foreign, &f.project).is_err());
    let path = f
        .coordinator
        .client
        .connection()
        .output_root
        .join("original.mp4");
    let mut tampered = f.bytes.clone();
    tampered[16] ^= 1;
    fs::write(path, tampered).unwrap();
    assert!(f.recover(selected.id).is_err());
    assert_eq!(
        f.service.production_receipts(f.project.id, 256).unwrap(),
        prior
    );
    assert_eq!(f.coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
}

#[test]
fn saved_master_unknown_is_not_settled_by_a_later_claimed_completion() {
    let f = Fixture::new();
    let selected = f.seed(|_, _| {});
    f.append(
        "outcome_unknown",
        json!({"error":"synthetic unresolved original"}),
    );
    let later = f.append("completed", selected.payload);
    let prior = f.service.production_receipts(f.project.id, 256).unwrap();
    let error = f.recover(later.id).err().unwrap();
    assert!(!error.outcome_known);
    assert_eq!(
        f.service.production_receipts(f.project.id, 256).unwrap(),
        prior
    );
    assert_eq!(f.coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
}

#[test]
fn saved_master_refuses_legacy_missing_contradictory_and_unverified_context() {
    for variant in 0..7 {
        let f = Fixture::new();
        let selected = f.seed(|intent, result| match variant {
            0 => {
                intent.as_object_mut().unwrap().remove(CONTEXT_KEY);
                result.as_object_mut().unwrap().remove(CONTEXT_KEY);
            }
            1 => {
                result[CONTEXT_KEY]["connection_identity"] = json!("0".repeat(64));
            }
            2 => {
                result[CONTEXT_KEY]["args"]["video_sha256"] = json!("0".repeat(64));
            }
            3 => {
                result["result"]["provenance_checked"] = json!(false);
            }
            4 => {
                result["result"]["execution"]["dry_run"] = json!(true);
            }
            5 => {
                result["result"]["data"]["artifact"]["path"] = json!("../outside.mp4");
            }
            _ => {
                intent["connection"] = json!("0".repeat(64));
            }
        });
        let prior = f.service.production_receipts(f.project.id, 256).unwrap();
        assert!(f.recover(selected.id).is_err(), "variant {variant}");
        assert_eq!(
            f.service.production_receipts(f.project.id, 256).unwrap(),
            prior
        );
        assert_eq!(f.coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
    }
}

#[test]
fn saved_master_discovery_is_empty_before_production_and_stops_at_latest_unknown() {
    let f = Fixture::new();
    let discover = || {
        f.coordinator
            .saved_master_receipt_inventory(
                f.project.id,
                &f.project.stamp(),
                f.project.deliverables[0].id,
            )
            .unwrap()
    };
    assert!(discover().items.is_empty());
    let selected = f.seed(|_, _| {});
    assert_eq!(discover().items[0].mux_receipt_id, selected.id);
    f.append(
        "outcome_unknown",
        json!({"error":"synthetic original unknown"}),
    );
    assert!(discover().items.is_empty());
}
