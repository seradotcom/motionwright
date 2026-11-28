//! Project-owned delivery slots, ordered by the existing production receipt ledger.
use super::production::{append_record, query_receipts, request_history};
use super::{
    BlobDescriptor, ProductionReceipt, ProductionReceiptInput, Result, StagedFileImport,
    StorageError, Store, file_import, load_project_from_row, validate_digest,
    validate_production_receipt_order_integrity,
};
use motionwright_domain::{DomainError, Project, RevisionStamp};
use rusqlite::{Connection, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::Path;
use uuid::Uuid;

pub(super) const COMMAND_PREFIX: &str = "motionwright.private-delivery:";
const MUX_COMMAND: &str = "driver.mlt-video.av.mux";
const MAX_MP4_BYTES: u64 = 1024 * 1024 * 1024;

/// A read-only selection of one original and one exact private slot head.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateDeliveryPlan {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub mux_receipt_id: Uuid,
    pub previous_commit_id: Option<Uuid>,
    pub artifact: BlobDescriptor,
}

impl PrivateDeliveryPlan {
    /// The same immutable identity binds the owner grant and durable request.
    pub fn identity(&self) -> Result<String> {
        let mut digest = Sha256::new();
        digest.update(b"motionwright.private-delivery-plan/1");
        digest.update([0]);
        digest.update(serde_json::to_vec(self)?);
        Ok(hex::encode(digest.finalize()))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PrivateDeliveryManifest {
    pub commit_id: Uuid,
    pub plan: PrivateDeliveryPlan,
}

fn invalid_original() -> StorageError {
    DomainError::Invalid("private delivery requires the exact completed original master".into())
        .into()
}

fn check_project(project: &Project, generation: Uuid, deliverable: Uuid) -> Result<()> {
    if project.generation != generation {
        return Err(StorageError::GenerationConflict);
    }
    if !project
        .deliverables
        .iter()
        .any(|profile| profile.id == deliverable)
    {
        return Err(StorageError::NotFound);
    }
    Ok(())
}

fn check_revision(project: &Project, revision: u64) -> Result<()> {
    if project.revision != revision {
        return Err(StorageError::Conflict {
            expected: revision,
            actual: project.revision,
        });
    }
    Ok(())
}

fn original(
    conn: &Connection,
    project: &Project,
    deliverable_id: Uuid,
    mux_receipt_id: Uuid,
    sha256: &str,
) -> Result<BlobDescriptor> {
    validate_digest(sha256)?;
    let receipts = query_receipts(
        conn,
        "SELECT r.receipt_id,r.project_id,r.generation,r.revision,r.request_id,
                r.request_sha256,r.command,r.stage,r.payload_json,r.created_at
         FROM production_receipts AS r
         JOIN production_receipt_order AS o ON o.receipt_id=r.receipt_id
         WHERE r.project_id=?1 AND r.receipt_id=?2",
        params![project.id.to_string(), mux_receipt_id.to_string()],
    )?;
    let receipt = receipts.first().ok_or_else(invalid_original)?;
    let original_profile = receipt
        .payload
        .pointer("/original_media_reopen/deliverable_id")
        .and_then(serde_json::Value::as_str)
        .and_then(|value| Uuid::parse_str(value).ok());
    if original_profile != Some(deliverable_id)
        || receipt.command != MUX_COMMAND
        || receipt.stage != "completed"
        || receipt.generation != project.generation
        || receipt.revision != project.revision
        || receipt
            .payload
            .pointer("/result/provenance_checked")
            .and_then(serde_json::Value::as_bool)
            != Some(true)
        || receipt
            .payload
            .pointer("/result/data/artifact/sha256")
            .and_then(serde_json::Value::as_str)
            != Some(sha256)
    {
        return Err(invalid_original());
    }
    let size_bytes = receipt
        .payload
        .pointer("/result/data/artifact/bytes")
        .and_then(serde_json::Value::as_u64)
        .filter(|size| (1..=MAX_MP4_BYTES).contains(size))
        .ok_or_else(invalid_original)?;
    Ok(BlobDescriptor {
        sha256: sha256.into(),
        size_bytes,
    })
}

fn slot_command(deliverable: Uuid) -> String {
    format!("{COMMAND_PREFIX}{deliverable}")
}

fn manifest(receipt: ProductionReceipt) -> Result<PrivateDeliveryManifest> {
    let plan: PrivateDeliveryPlan = serde_json::from_value(receipt.payload)?;
    let identity = plan.identity()?;
    if receipt.stage != "completed"
        || receipt.command != slot_command(plan.deliverable_id)
        || receipt.project_id != plan.project_id
        || receipt.generation != plan.generation
        || receipt.revision != plan.revision
        || receipt.request_sha256 != identity
        || receipt.request_id != format!("private-delivery:{identity}")
        || receipt.id.is_nil()
        || plan.previous_commit_id == Some(receipt.id)
        || plan.mux_receipt_id.is_nil()
        || !(1..=MAX_MP4_BYTES).contains(&plan.artifact.size_bytes)
    {
        return Err(StorageError::InvalidBackup(
            "private delivery receipt binding is invalid".into(),
        ));
    }
    validate_digest(&plan.artifact.sha256)?;
    Ok(PrivateDeliveryManifest {
        commit_id: receipt.id,
        plan,
    })
}

fn current_slot(
    conn: &Connection,
    project: &Project,
    deliverable: Uuid,
) -> Result<Option<PrivateDeliveryManifest>> {
    query_receipts(
        conn,
        "SELECT r.receipt_id,r.project_id,r.generation,r.revision,r.request_id,
                r.request_sha256,r.command,r.stage,r.payload_json,r.created_at
         FROM production_receipts AS r
         JOIN production_receipt_order AS o ON o.receipt_id=r.receipt_id
         WHERE r.project_id=?1 AND r.generation=?2 AND r.command=?3
         ORDER BY o.sequence DESC LIMIT 1",
        params![
            project.id.to_string(),
            project.generation.to_string(),
            slot_command(deliverable)
        ],
    )?
    .pop()
    .map(manifest)
    .transpose()
}

fn verify_artifact(root: &Path, artifact: &BlobDescriptor) -> Result<()> {
    let path = super::checked_existing_blob_path_from_root(root, &artifact.sha256)?;
    if super::portability::hash_portable_file(&path, artifact.size_bytes)? != *artifact {
        return Err(StorageError::InvalidBlobDigest);
    }
    Ok(())
}

/// Resolve retention from the same SQLite receipt that makes a private slot visible.
pub(super) fn committed_media(
    conn: &Connection,
    project_id: Uuid,
    generation: Uuid,
    receipt_id: Uuid,
    request_sha256: &str,
    digest: &str,
) -> Result<bool> {
    validate_production_receipt_order_integrity(conn, Some(project_id))?;
    let history = request_history(
        conn,
        project_id,
        &format!("private-delivery:{request_sha256}"),
    )?;
    if history.is_empty() {
        return Ok(false);
    }
    let [receipt]: [ProductionReceipt; 1] =
        history.try_into().map_err(|_| StorageError::RequestReuse)?;
    let delivery = manifest(receipt)?;
    if delivery.commit_id != receipt_id
        || delivery.plan.project_id != project_id
        || delivery.plan.generation != generation
        || delivery.plan.artifact.sha256 != digest
    {
        return Err(StorageError::RequestReuse);
    }
    Ok(true)
}

impl Store {
    /// Resolve one durable slot and verify its exact retained artifact without
    /// keeping a SQLite transaction or borrowing the Store during media IO.
    pub fn prepare_private_delivery_inspection(
        &self,
        project_id: Uuid,
        generation: Uuid,
        deliverable_id: Uuid,
    ) -> Result<impl FnOnce() -> Result<Option<PrivateDeliveryManifest>> + use<>> {
        let tx = self.conn.unchecked_transaction()?;
        let project = load_project_from_row(&tx, project_id)?;
        check_project(&project, generation, deliverable_id)?;
        validate_production_receipt_order_integrity(&tx, Some(project_id))?;
        let result = current_slot(&tx, &project, deliverable_id)?;
        tx.commit()?;
        let root = self.blob_root.clone();
        Ok(move || {
            if let Some(manifest) = &result {
                verify_artifact(&root, &manifest.plan.artifact)?;
            }
            Ok(result)
        })
    }

    pub fn prepare_private_delivery(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        deliverable_id: Uuid,
        mux_receipt_id: Uuid,
        sha256: &str,
    ) -> Result<PrivateDeliveryPlan> {
        let tx = self.conn.unchecked_transaction()?;
        let project = load_project_from_row(&tx, project_id)?;
        check_project(&project, expected.generation, deliverable_id)?;
        if expected.resource != project.resource_key() {
            return Err(StorageError::GenerationConflict);
        }
        check_revision(&project, expected.revision)?;
        validate_production_receipt_order_integrity(&tx, Some(project_id))?;
        let artifact = original(&tx, &project, deliverable_id, mux_receipt_id, sha256)?;
        let current = current_slot(&tx, &project, deliverable_id)?;
        tx.commit()?;
        if let Some(current) = current.as_ref()
            && current.plan.mux_receipt_id == mux_receipt_id
            && current.plan.revision == expected.revision
            && current.plan.artifact == artifact
        {
            return Ok(current.plan.clone());
        }
        let previous_commit_id = current.map(|item| item.commit_id);
        Ok(PrivateDeliveryPlan {
            project_id,
            generation: expected.generation,
            revision: expected.revision,
            deliverable_id,
            mux_receipt_id,
            previous_commit_id,
            artifact,
        })
    }

    /// The caller qualifies the original through the Native reader and admits
    /// this exact plan as a separate effect before entering publication.
    pub fn commit_private_delivery<P>(
        &mut self,
        plan: &PrivateDeliveryPlan,
        captured: &StagedFileImport,
        admit_publication: impl FnOnce() -> std::io::Result<P>,
    ) -> Result<PrivateDeliveryManifest> {
        let identity = plan.identity()?;
        let request_id = format!("private-delivery:{identity}");
        let tx = rusqlite::Transaction::new_unchecked(&self.conn, TransactionBehavior::Immediate)?;
        let project = load_project_from_row(&tx, plan.project_id)?;
        check_project(&project, plan.generation, plan.deliverable_id)?;
        validate_production_receipt_order_integrity(&tx, Some(plan.project_id))?;
        let history = request_history(&tx, plan.project_id, &request_id)?;
        match history.len() {
            0 => (),
            1 => {
                let prior = manifest(
                    history
                        .into_iter()
                        .next()
                        .ok_or(StorageError::RequestReuse)?,
                )?;
                if prior.plan != *plan {
                    return Err(StorageError::RequestReuse);
                }
                tx.commit()?;
                verify_artifact(&self.blob_root, &prior.plan.artifact)?;
                return Ok(prior);
            }
            _ => return Err(StorageError::RequestReuse),
        }
        check_revision(&project, plan.revision)?;
        let head = current_slot(&tx, &project, plan.deliverable_id)?.map(|item| item.commit_id);
        if head != plan.previous_commit_id {
            return Err(StorageError::PrivateDeliveryConflict);
        }
        if original(
            &tx,
            &project,
            plan.deliverable_id,
            plan.mux_receipt_id,
            &plan.artifact.sha256,
        )? != plan.artifact
            || captured.descriptor() != &plan.artifact
        {
            return Err(StorageError::InvalidBlobDigest);
        }
        if captured.captured_root() != self.blob_root.as_path() {
            return Err(StorageError::UnsafeSourcePath(
                "private delivery capture belongs to another blob root".into(),
            ));
        }
        let _permit = admit_publication()?;
        let receipt = append_record(
            &tx,
            ProductionReceiptInput {
                project_id: plan.project_id,
                generation: plan.generation,
                revision: plan.revision,
                request_id,
                request_sha256: identity.clone(),
                command: slot_command(plan.deliverable_id),
                stage: "completed".into(),
                payload: serde_json::to_value(plan)?,
            },
            &project,
            None,
        )?;
        let result = manifest(receipt)?;
        let intent = self.begin_private_delivery_publication(&result, captured, &identity)?;
        if let Some(intent) = &intent {
            intent.publish(captured)?;
        } else {
            file_import::publish_file_import_under_root(&self.blob_root, captured)?;
        }
        tx.commit()?;
        if let Some(intent) = intent {
            // The durable receipt remains authoritative if sidecar finalization is interrupted.
            let _ = intent.finish_committed();
        }
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn inspect(
        store: &Store,
        project: Uuid,
        generation: Uuid,
        deliverable: Uuid,
    ) -> Result<Option<PrivateDeliveryManifest>> {
        store.prepare_private_delivery_inspection(project, generation, deliverable)?()
    }

    fn capture_fixture(
        store: &Store,
        folder: &std::path::Path,
        name: &str,
        bytes: &[u8],
    ) -> StagedFileImport {
        let original = folder.join(name);
        std::fs::write(&original, bytes).unwrap();
        store
            .file_import_capture_target()
            .capture_private_delivery_file(&original, 128)
            .unwrap()
    }

    fn completed_fixture(
        store: &mut Store,
        project: &Project,
        deliverable_id: Uuid,
        artifact: &BlobDescriptor,
    ) -> ProductionReceipt {
        store
            .append_production_receipt(ProductionReceiptInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id: Uuid::now_v7().to_string(),
                request_sha256: "a".repeat(64),
                command: MUX_COMMAND.into(),
                stage: "completed".into(),
                payload: json!({
                    "original_media_reopen": {"deliverable_id": deliverable_id},
                    "result": {"provenance_checked": true, "data": {
                        "artifact": {"sha256": artifact.sha256, "bytes": artifact.size_bytes}
                    }}
                }),
            })
            .unwrap()
    }

    #[test]
    fn private_delivery_reclaims_its_owned_blob_when_sqlite_commit_rolls_back() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("studio.sqlite3");
        let media = directory.path().join("media");
        let open = || Store::open_with_managed_blob_root(&database, &media).unwrap();
        let mut store = open();
        let project = store
            .create_named_project("Private delivery rollback fixture")
            .unwrap();
        let deliverable = project.deliverables[0].id;
        let captured = capture_fixture(
            &store,
            directory.path(),
            "uncommitted.mp4",
            b"uncommitted master",
        );
        let digest = captured.descriptor().sha256.clone();
        let original = completed_fixture(&mut store, &project, deliverable, captured.descriptor());
        let plan = store
            .prepare_private_delivery(
                project.id,
                &project.stamp(),
                deliverable,
                original.id,
                &digest,
            )
            .unwrap();

        // This constraint fails COMMIT after the receipt and file publication.
        store
            .conn
            .execute_batch(
                "CREATE TEMP TABLE delivery_parent (id INTEGER PRIMARY KEY);
             CREATE TEMP TABLE delivery_child (
                 id INTEGER REFERENCES delivery_parent(id) DEFERRABLE INITIALLY DEFERRED
             );
             CREATE TEMP TRIGGER reject_delivery_commit AFTER INSERT ON main.production_receipts
             WHEN new.command LIKE 'motionwright.private-delivery:%'
             BEGIN INSERT INTO delivery_child VALUES (1); END;",
            )
            .unwrap();
        assert!(matches!(
            store.commit_private_delivery(&plan, &captured, || Ok(())),
            Err(StorageError::Sql(error)) if error.sqlite_error_code() == Some(rusqlite::ErrorCode::ConstraintViolation)
        ));
        assert_eq!(store.verify_blob(&digest).unwrap(), plan.artifact);
        assert_eq!(
            inspect(&store, project.id, project.generation, deliverable).unwrap(),
            None
        );
        drop(captured);
        drop(store);

        let mut reopened = open();
        let report = reopened
            .recover_cas(crate::CasRetentionPolicy {
                retention: std::time::Duration::ZERO,
                max_objects: 32,
            })
            .unwrap();
        assert!(
            matches!(
                reopened.verify_blob(&digest),
                Err(StorageError::BlobMissing { .. })
            ),
            "{report:?}"
        );
        assert_eq!(
            inspect(&reopened, project.id, project.generation, deliverable).unwrap(),
            None
        );
    }

    #[test]
    fn private_delivery_compares_slot_head_and_preserves_exact_receipts_after_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("studio.sqlite3");
        let media = directory.path().join("media");
        let open = || Store::open_with_managed_blob_root(&database, &media).unwrap();
        let mut store = open();
        let project = store
            .create_named_project("Private delivery fixture")
            .unwrap();
        let deliverable = project.deliverables[0].id;
        let first_bytes = b"synthetic original A";
        let second_bytes = b"synthetic original B";
        let a = capture_fixture(&store, directory.path(), "first.mp4", first_bytes);
        let b = capture_fixture(&store, directory.path(), "second.mp4", second_bytes);
        let first = completed_fixture(&mut store, &project, deliverable, a.descriptor());
        let second = completed_fixture(&mut store, &project, deliverable, b.descriptor());
        let mut peer = open();
        let prepare = |store: &Store, receipt: &ProductionReceipt, artifact: &BlobDescriptor| {
            store
                .prepare_private_delivery(
                    project.id,
                    &project.stamp(),
                    deliverable,
                    receipt.id,
                    &artifact.sha256,
                )
                .unwrap()
        };
        let same_slot_a = prepare(&store, &first, a.descriptor());
        let stale_b = prepare(&peer, &second, b.descriptor());
        assert_eq!(same_slot_a.previous_commit_id, None);
        assert_eq!(stale_b.previous_commit_id, None);
        assert!(matches!(
            store.commit_private_delivery(&same_slot_a, &a, || {
                Err::<(), _>(std::io::ErrorKind::PermissionDenied.into())
            }),
            Err(StorageError::Io(_))
        ));
        assert_eq!(
            inspect(&store, project.id, project.generation, deliverable).unwrap(),
            None
        );
        assert!(matches!(
            store.verify_blob(&a.descriptor().sha256),
            Err(StorageError::BlobMissing { .. })
        ));

        let committed = store
            .commit_private_delivery(&same_slot_a, &a, || Ok(()))
            .unwrap();
        assert!(matches!(
            peer.commit_private_delivery(&stale_b, &b, || -> std::io::Result<()> {
                panic!("stale slot may not start publication")
            }),
            Err(StorageError::PrivateDeliveryConflict)
        ));
        assert!(matches!(
            store.verify_blob(&b.descriptor().sha256),
            Err(StorageError::BlobMissing { .. })
        ));

        let current_a = prepare(&peer, &first, a.descriptor());
        assert_eq!(current_a, same_slot_a);
        assert_eq!(
            peer.commit_private_delivery(&current_a, &a, || -> std::io::Result<()> {
                panic!("known delivery may not start publication again")
            })
            .unwrap(),
            committed
        );

        let replaced = prepare(&peer, &second, b.descriptor());
        assert_eq!(replaced.previous_commit_id, Some(committed.commit_id));
        let committed_b = peer
            .commit_private_delivery(&replaced, &b, || Ok(()))
            .unwrap();
        drop(a);
        drop(b);
        drop(peer);
        drop(store);

        let mut reopened = open();
        let unrelated_bundle = crate::BundleImportRecoveryRequest {
            project_id: project.id,
            fingerprint: crate::BundleFingerprint::from_sha256(replaced.identity().unwrap())
                .unwrap(),
        };
        assert_eq!(
            reopened
                .recover_project_bundle_import(&unrelated_bundle)
                .unwrap(),
            None
        );
        assert_eq!(
            inspect(&reopened, project.id, project.generation, deliverable).unwrap(),
            Some(committed_b.clone())
        );
        assert_eq!(
            reopened
                .read_blob(&committed.plan.artifact.sha256, 128)
                .unwrap(),
            first_bytes
        );
        assert_eq!(
            reopened
                .read_blob(&committed_b.plan.artifact.sha256, 128)
                .unwrap(),
            second_bytes
        );
        assert!(matches!(
            inspect(&reopened, project.id, Uuid::now_v7(), deliverable),
            Err(StorageError::GenerationConflict)
        ));
        let path = super::super::checked_existing_blob_path_from_root(
            &media,
            &committed_b.plan.artifact.sha256,
        )
        .unwrap();
        std::fs::write(path, b"synthetic original X").unwrap();
        assert!(matches!(
            inspect(&reopened, project.id, project.generation, deliverable),
            Err(StorageError::InvalidBlobDigest)
        ));
    }
}
