//! Real SQLite reopen with explicitly synthetic custody bytes. No decoder,
//! original Native execution, publication or human acceptance is asserted.
use super::*;

fn original_selection(
    service: &StudioService,
    project: &Project,
    coordinator: &ProductionCoordinator,
) -> (SourceBinding, Uuid, String) {
    let binding = seed_recoverable_chain(service, project, coordinator);
    let proof = coordinator
        .reopen_original_mix_audio_measurements(
            project.id,
            &project.stamp(),
            &binding.caller_request_id,
            binding.deliverable_id,
            binding.lane,
        )
        .unwrap();
    (
        binding,
        proof.decoded.receipt.local_id,
        proof
            .decoded
            .context
            .native_parent
            .as_ref()
            .unwrap()
            .master
            .sha256
            .clone(),
    )
}

#[test]
fn original_master_review_reopens_sqlite_and_same_source_without_dispatch_or_receipt_changes() {
    let (root, service, project, coordinator) = fixture();
    let (binding, receipt, digest) = original_selection(&service, &project, &coordinator);
    let receipts = service.production_receipts(project.id, 256).unwrap();
    let connection = coordinator.client.connection().clone();
    drop(coordinator);
    drop(service);
    let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
    let current = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
    let source = current
        .recover_original_master_review_from_receipt(
            project.id,
            &project.stamp(),
            receipt,
            binding.deliverable_id,
            &digest,
        )
        .unwrap();
    assert_eq!(source.project_id(), project.id);
    assert_eq!(source.stamp(), &project.stamp());
    assert_eq!(source.deliverable_id(), binding.deliverable_id);
    assert_eq!(source.clock(), (30, 1, 60));
    let (owner_root, relative, sha256, bytes) = source.artifact();
    assert_eq!(relative, expected_master_path(&binding).unwrap());
    assert_eq!(sha256, digest);
    let original = fs::read(owner_root.join(relative)).unwrap();
    assert_eq!(bytes, original.len() as u64);
    assert_eq!(hex::encode(Sha256::digest(&original)), digest);
    assert_eq!(
        reopened.production_receipts(project.id, 256).unwrap(),
        receipts
    );
    assert_eq!(reopened.project(project.id).unwrap(), project);
    assert_eq!(current.client.execute_calls.load(Ordering::SeqCst), 0);
}

#[test]
fn original_master_review_refuses_foreign_hints_changed_source_and_changed_media() {
    let (_root, service, project, coordinator) = fixture();
    let (binding, receipt, digest) = original_selection(&service, &project, &coordinator);
    let before = service.production_receipts(project.id, 256).unwrap();
    for kind in 0..5 {
        let mut stamp = project.stamp();
        let mut selected = receipt;
        let mut profile = binding.deliverable_id;
        let mut sha = digest.clone();
        match kind {
            0 => stamp.revision += 1,
            1 => stamp.generation = Uuid::new_v4(),
            2 => selected = Uuid::new_v4(),
            3 => profile = Uuid::new_v4(),
            _ => sha = "0".repeat(64),
        }
        assert!(
            coordinator
                .recover_original_master_review_from_receipt(
                    project.id, &stamp, selected, profile, &sha,
                )
                .is_err()
        );
    }
    let path = coordinator
        .client
        .connection()
        .output_root
        .join(expected_master_path(&binding).unwrap());
    let mut bytes = fs::read(&path).unwrap();
    bytes[0] ^= 1;
    fs::write(&path, bytes).unwrap();
    assert!(
        coordinator
            .recover_original_master_review_from_receipt(
                project.id,
                &project.stamp(),
                receipt,
                binding.deliverable_id,
                &digest,
            )
            .is_err()
    );
    assert_eq!(
        service.production_receipts(project.id, 256).unwrap(),
        before
    );
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
}

#[test]
fn original_master_review_keeps_unknown_parent_unresolved_without_replaying_it() {
    let (_root, service, project, coordinator) = fixture();
    let (binding, receipt, digest) = original_selection(&service, &project, &coordinator);
    let parent_id = format!(
        "{}{}",
        binding.namespace().unwrap(),
        binding.lane.mux_suffix()
    );
    let parent = service
        .latest_production_receipt(project.id, &parent_id)
        .unwrap()
        .unwrap();
    service
        .append_production_receipt(ProductionReceiptInput {
            project_id: parent.project_id,
            generation: parent.generation,
            revision: parent.revision,
            request_id: parent.request_id,
            request_sha256: parent.request_sha256,
            command: parent.command,
            stage: "outcome_unknown".into(),
            payload: json!({"error":"synthetic unresolved original"}),
        })
        .unwrap();
    let before = service.production_receipts(project.id, 256).unwrap();
    assert!(
        coordinator
            .recover_original_master_review_from_receipt(
                project.id,
                &project.stamp(),
                receipt,
                binding.deliverable_id,
                &digest,
            )
            .is_err()
    );
    assert_eq!(
        service.production_receipts(project.id, 256).unwrap(),
        before
    );
    assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
}

#[test]
fn saved_delivery_audio_requires_the_original_passing_final_measurement_and_exact_mux() {
    for target in [-23.0, -14.0] {
        let (_root, service, mut project, coordinator) = fixture();
        let profile = project.deliverables[0].id;
        let mut recipe = project.production_design.delivery_recipes[&profile].clone();
        recipe.audio_targets = Some(DeliveryAudioTargets {
            target_lufs: Some(target),
            target_true_peak_dbtp: Some(-1.0),
        });
        apply(
            &service,
            &mut project,
            "saved-delivery-target",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: profile,
                recipe: Some(recipe),
            },
        );
        let (binding, _, sha) = original_selection(&service, &project, &coordinator);
        let key = format!(
            "{}{}",
            binding.namespace().unwrap(),
            binding.lane.mux_suffix()
        );
        let parent = service
            .latest_production_receipt(project.id, &key)
            .unwrap()
            .unwrap();
        let before = service.production_receipts(project.id, 256).unwrap();
        let result =
            coordinator.verify_saved_master_delivery_audio(&project, &parent, profile, &sha);
        assert_eq!(result.is_ok(), target == -23.0);
        let mut foreign = parent.clone();
        foreign.id = Uuid::new_v4();
        assert!(
            coordinator
                .verify_saved_master_delivery_audio(&project, &foreign, profile, &sha)
                .is_err()
        );
        assert!(
            coordinator
                .verify_saved_master_delivery_audio(&project, &parent, profile, &"0".repeat(64))
                .is_err()
        );
        assert_eq!(
            service.production_receipts(project.id, 256).unwrap(),
            before
        );
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
    }
}
