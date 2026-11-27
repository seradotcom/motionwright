use base64::{Engine, engine::general_purpose::STANDARD};
use ed25519_dalek::{Signer, SigningKey};
use motionwright_service::StudioService;
use rusqlite::{Connection, params};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::fs;

fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn signed(key: &SigningKey, payload: Value) -> Vec<u8> {
    let signature = key.sign(&serde_json::to_vec(&payload).unwrap());
    serde_json::to_vec(&json!({
        "payload":payload,
        "signature_b64":STANDARD.encode(signature.to_bytes()),
    }))
    .unwrap()
}

#[test]
fn saved_project_accepts_only_current_independently_signed_observation() {
    let base = tempfile::tempdir().unwrap();
    let db = base.path().join("project.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let project = service.create_project("Source observation").unwrap();
    let trust = base.path().join("observer-policy");
    let evidence = base.path().join("evidence");
    fs::create_dir(&trust).unwrap();
    fs::create_dir(&evidence).unwrap();
    let owner = SigningKey::from_bytes(&[1; 32]);
    let verifier = SigningKey::from_bytes(&[2; 32]);
    let source = b"# The reviewed source is data, and never executed.\n";
    let producer_sha = "ab".repeat(32);
    fs::write(trust.join("owner.pub"), owner.verifying_key().to_bytes()).unwrap();
    fs::write(trust.join("verifier.py"), source).unwrap();
    fs::write(trust.join("producer.sha256"), &producer_sha).unwrap();
    let registry = Connection::open(trust.join("policies.sqlite3")).unwrap();
    registry
        .execute_batch(
            "PRAGMA application_id=1297567568; PRAGMA user_version=1;
         CREATE TABLE policy_owner (singleton INTEGER PRIMARY KEY, key_sha256 TEXT NOT NULL);
         CREATE TABLE policies (generation INTEGER PRIMARY KEY, envelope BLOB NOT NULL);",
        )
        .unwrap();
    registry
        .execute(
            "INSERT INTO policy_owner VALUES (1, ?1)",
            [digest(&owner.verifying_key().to_bytes())],
        )
        .unwrap();
    let policy = |generation, revoked: Vec<&str>| {
        signed(
            &owner,
            json!({
                "admitted_ids":["owner_png_dimension_v1"],
                "owner_generation":generation,
                "revoked_ids":revoked,
                "schema":"motionwright.observation-owner-policy/1",
            }),
        )
    };
    let original = policy(7, vec![]);
    registry
        .execute("INSERT INTO policies VALUES (?1, ?2)", params![7, original])
        .unwrap();
    fs::write(evidence.join("owner-policy.json"), &original).unwrap();
    let mut png_bytes = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut png_bytes, 3, 2);
        encoder.set_color(png::ColorType::Rgb);
        let mut writer = encoder.write_header().unwrap();
        writer.write_image_data(&[42; 18]).unwrap();
    }
    fs::write(evidence.join("frame.png"), &png_bytes).unwrap();
    let limits = json!(["Dimensions only. No creative approval."]);
    fs::write(evidence.join("owner-admission.json"), signed(&owner, json!({
        "admission_id":"owner_png_dimension_v1", "coverage":"single_full_frame",
        "limitations":limits, "method":"png_dimensions_v1", "owner_generation":7,
        "permission":"observe_png_dimensions_only", "schema":"motionwright.observation-verifier-admission/1",
        "units":"pixels", "verifier_id":"independent_png",
        "verifier_public_key_b64":STANDARD.encode(verifier.verifying_key().to_bytes()),
        "verifier_source_sha256":digest(source), "verifier_version":"1.0.0",
    }))).unwrap();
    fs::write(evidence.join("observed-frame.json"), signed(&verifier, json!({
        "admission_id":"owner_png_dimension_v1", "coverage":"single_full_frame", "frame_index":0,
        "height":2, "limitations":limits, "method":"png_dimensions_v1", "producer_id":"native_example",
        "producer_sha256":producer_sha, "schema":"motionwright.observation-evidence/1",
        "source_frame_path":"frame.png", "source_frame_sha256":digest(&png_bytes), "units":"pixels",
        "verifier_id":"independent_png", "verifier_version":"1.0.0", "width":3,
    }))).unwrap();

    let scope = project.stamp();
    let inspect = || service.review_observer_evidence(project.id, &scope, &trust, &evidence);
    let valid = inspect().unwrap();
    assert!(valid.gate_eligible);
    assert_eq!(
        (valid.width, valid.height, valid.owner_generation),
        (3, 2, 7)
    );
    let revoked = policy(8, vec!["owner_png_dimension_v1"]);
    registry
        .execute("INSERT INTO policies VALUES (?1, ?2)", params![8, revoked])
        .unwrap();
    assert!(inspect().is_err());
    fs::write(evidence.join("owner-policy.json"), &revoked).unwrap();
    assert!(!inspect().unwrap().gate_eligible);
    assert_eq!(service.project(project.id).unwrap(), project);
    fs::write(evidence.join("frame.png"), [1, 2]).unwrap();
    assert!(inspect().is_err());
}
