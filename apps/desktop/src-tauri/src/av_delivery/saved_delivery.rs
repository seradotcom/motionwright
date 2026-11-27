//! Saved original export is a new owner-authorized copy, not token recovery.
use super::*;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SavedMasterExportRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub mux_receipt_id: Uuid,
    pub expected_master_sha256: String,
    pub effect_grant: Uuid,
    pub destination: String,
    #[serde(default)]
    pub include_integrity_manifest: bool,
}

impl SavedMasterExportRequest {
    pub(crate) fn selection(&self) -> SavedMasterRecoveryRequest {
        SavedMasterRecoveryRequest {
            project_id: self.project_id,
            generation: self.generation,
            revision: self.revision,
            deliverable_id: self.deliverable_id,
            mux_receipt_id: self.mux_receipt_id,
            expected_master_sha256: self.expected_master_sha256.clone(),
        }
    }
}

pub(crate) fn prepare_saved_delivery(
    project: &Project,
    request: &SavedMasterRecoveryRequest,
    service: StudioService,
    connection_path: &Path,
    custody: &ProductionReceiptCustody,
) -> Result<(DeliverySource, String), String> {
    if project.id != request.project_id
        || project.generation != request.generation
        || project.revision != request.revision
        || !valid_sha256(&request.expected_master_sha256)
    {
        return Err("Saved MP4 delivery source is no longer current.".into());
    }
    let connection = ProductionConnection::load(connection_path)
        .map_err(|_| "The original owner connection is unavailable.")?;
    let identity = connection
        .identity()
        .map_err(|_| "Original connection identity is unavailable.")?;
    let coordinator = ProductionCoordinator::with_receipt_custody(service, connection, custody)
        .map_err(|_| "The original owner connection cannot read this saved master.")?;
    let qualified = coordinator.qualify_saved_master_delivery(
        project.id, &project.stamp(), request.mux_receipt_id, request.deliverable_id,
        &request.expected_master_sha256,
    ).map_err(|_| "This saved MP4 cannot be delivered. Check that it matches the authored timing and that the original source and decoded audio are available. Captions and saved audio targets require their original verification.")?;
    let source = source_from_qualified(&qualified);
    let current = ProductionConnection::load(connection_path).map_err(
        |_| "The original owner connection changed during saved delivery qualification.",
    )?;
    if current.identity().ok().as_deref() != Some(identity.as_str()) {
        return Err(
            "The original owner connection changed during saved delivery qualification.".into(),
        );
    }
    Ok((source, identity))
}

fn source_from_qualified(
    proof: &motionwright_native::production::VerifiedSavedMasterDelivery,
) -> DeliverySource {
    let original = proof.original();
    let (root, path, sha, _) = original.artifact();
    let (num, den, frames) = original.clock();
    DeliverySource {
        project_id: original.project_id(),
        generation: original.stamp().generation,
        revision: original.stamp().revision,
        deliverable_id: original.deliverable_id(),
        owner_root: root.to_path_buf(),
        relative_path: path.into(),
        sha256: sha.into(),
        frame_rate_num: num,
        frame_rate_den: den,
        frame_count: frames,
        review_only: false,
        audio_analysis: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn saved_delivery_selector_is_closed_and_requires_a_new_grant() {
        let original = serde_json::json!({
            "project_id":Uuid::new_v4(), "generation":Uuid::new_v4(), "revision":4,
            "deliverable_id":Uuid::new_v4(), "mux_receipt_id":Uuid::new_v4(),
            "expected_master_sha256":"a".repeat(64), "effect_grant":Uuid::new_v4(),
            "destination":"/explicit/new.mp4", "include_integrity_manifest":true,
        });
        let request: SavedMasterExportRequest = serde_json::from_value(original.clone()).unwrap();
        assert_eq!(request.selection().mux_receipt_id, request.mux_receipt_id);
        for field in [
            "source_path",
            "owner_root",
            "export_token",
            "review_only",
            "audio_qc",
        ] {
            let mut injected = original.clone();
            injected[field] = serde_json::json!("client-authored");
            assert!(serde_json::from_value::<SavedMasterExportRequest>(injected).is_err());
        }
        let mut no_grant = original;
        no_grant.as_object_mut().unwrap().remove("effect_grant");
        assert!(serde_json::from_value::<SavedMasterExportRequest>(no_grant).is_err());
    }
}
