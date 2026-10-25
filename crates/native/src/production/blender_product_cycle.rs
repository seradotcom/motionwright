//! Fixed product source decoder and caller for the existing native source phases.
//! Execution authority remains in ProductionCoordinator and the owner connection.

use super::*;

pub const BLENDER_PRODUCT_RECIPE_MEDIA_TYPE: &str =
    "application/vnd.motionwright.blender-product+json";
pub(super) const BLENDER_PRODUCT_NATIVE_MEDIA_TYPE: &str = "application/x-blender";
pub(super) const PRODUCT_CALLER_SCHEMA: &str = "motionwright.blender.product-cycle-caller/2";

/// Decode a source document, never an execution grant or a provider command.
pub fn decode_blender_product_recipe(bytes: &[u8]) -> NativeResult<ProductScene> {
    if bytes.is_empty() || bytes.len() as u64 > PRODUCT_RECIPE_MAX_BYTES {
        return Err(invalid(
            "Product recipe exceeds its fixed source byte bound",
        ));
    }
    let source: ProductScene = serde_json::from_slice(bytes)
        .map_err(|_| invalid("Product recipe is not a closed ProductScene v1 document"))?;
    source.validate().map_err(|_| {
        Error::new(
            ErrorCode::Unsupported,
            "Product recipe is outside the fixed native source profile",
        )
    })?;
    Ok(source)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlenderProductCycleRequest {
    pub project_id: Uuid,
    pub expected: RevisionStamp,
    pub scene_id: Uuid,
    pub capsule_id: Uuid,
    pub request_id: String,
    pub annotation_body: String,
}

/// Retain this journal on every terminal, including a local custody failure.
#[derive(Debug, Default, Serialize)]
pub struct BlenderProductCycleJournal {
    schema: String,
    source_actions: Vec<Value>,
    root_intent: Option<Value>,
    root_terminal: Option<Value>,
    create: BlenderProductJournal,
    edit: BlenderProductJournal,
    verify: BlenderProductJournal,
}

fn cycle_create_request(request: &BlenderProductCycleRequest) -> BlenderProductCreateRequest {
    BlenderProductCreateRequest {
        project_id: request.project_id,
        expected: request.expected.clone(),
        scene_id: request.scene_id,
        capsule_id: request.capsule_id,
        request_id: request.request_id.clone(),
    }
}

fn cycle_reopen_request(
    request: &BlenderProductCycleRequest,
    project: &motionwright_domain::Project,
    phase: BlenderProductReopenPhase,
) -> BlenderProductReopenRequest {
    BlenderProductReopenRequest {
        project_id: request.project_id,
        expected: project.stamp(),
        scene_id: request.scene_id,
        capsule_id: request.capsule_id,
        request_id: request.request_id.clone(),
        phase,
    }
}

fn cycle_journal_is_empty(journal: &BlenderProductCycleJournal) -> bool {
    journal.schema.is_empty()
        && journal.source_actions.is_empty()
        && journal.root_intent.is_none()
        && journal.root_terminal.is_none()
        && [&journal.create, &journal.edit, &journal.verify]
            .iter()
            .all(|phase| {
                phase.schema.is_empty()
                    && phase.source_binding.is_none()
                    && phase.calls.is_empty()
                    && phase.evidence.is_empty()
            })
}

fn cycle_phase_name(phase: &BlenderProductReopenPhase) -> &'static str {
    match phase {
        BlenderProductReopenPhase::EditAnnotation { .. } => "original",
        BlenderProductReopenPhase::VerifyEdited => "edited",
    }
}

fn cycle_record_terminal<T>(
    actions: &mut [Value],
    index: usize,
    result: &NativeResult<T>,
    success: impl FnOnce(&T) -> Value,
) {
    actions[index]["terminal"] = match result {
        Ok(value) => json!({"status": "ok", "value": success(value)}),
        Err(error) => json!({"status": "error", "error": error}),
    };
}

fn cycle_connection_roots(
    coordinators: [&ProductionCoordinator; 3],
    project: &motionwright_domain::Project,
) -> NativeResult<[PathBuf; 3]> {
    let first_store = coordinators[0].service.store();
    let mut roots = Vec::with_capacity(3);
    for coordinator in coordinators {
        if !std::sync::Arc::ptr_eq(&first_store, &coordinator.service.store()) {
            return Err(invalid(
                "Product cycle coordinators must share one canonical StudioService",
            ));
        }
        let connection = coordinator.client.connection();
        if connection.resource != project.resource_key()
            || !safe_metadata(&connection.output_root)?.is_dir()
        {
            return Err(invalid(
                "Product cycle connection is not bound to its canonical resource",
            ));
        }
        let root = fs::canonicalize(&connection.output_root)
            .map_err(|_| backend("Product cycle output root could not be canonicalized"))?;
        if roots
            .iter()
            .any(|other: &PathBuf| root.starts_with(other) || other.starts_with(&root))
        {
            return Err(invalid(
                "Product cycle requires three separate, non-nested owner output roots",
            ));
        }
        roots.push(root);
    }
    let roots: [PathBuf; 3] = roots
        .try_into()
        .map_err(|_| invalid("Product cycle requires exactly three output roots"))?;
    source_output_must_be_new(&roots[0], ORIGINAL_FILE)?;
    product_png_must_be_new(&roots[0], PRODUCT_FRAME_600)?;
    product_png_must_be_new(&roots[0], PRODUCT_FRAME_601)?;
    source_output_must_be_new(&roots[1], ORIGINAL_FILE)?;
    source_output_must_be_new(&roots[1], EDITED_FILE)?;
    source_output_must_be_new(&roots[2], EDITED_FILE)?;
    product_png_must_be_new(&roots[2], PRODUCT_FRAME_601)?;
    Ok(roots)
}

fn cycle_imported_asset(
    before: &motionwright_domain::Project,
    after: &motionwright_domain::Project,
    witness: &ProductNativeSourceWitness,
) -> NativeResult<Uuid> {
    if after.id != before.id
        || after.generation != before.generation
        || before.revision.checked_add(1) != Some(after.revision)
        || before
            .assets
            .iter()
            .any(|old| after.assets.iter().find(|asset| asset.id == old.id) != Some(old))
    {
        return Err(
            backend("Product file import did not retain its exact prior asset identities")
                .uncertain(),
        );
    }
    let ids = before
        .assets
        .iter()
        .map(|asset| asset.id)
        .collect::<BTreeSet<_>>();
    let added = after
        .assets
        .iter()
        .filter(|asset| !ids.contains(&asset.id))
        .collect::<Vec<_>>();
    if added.len() != 1
        || added[0].name != witness.file.relative_path
        || added[0].media_type != BLENDER_PRODUCT_NATIVE_MEDIA_TYPE
        || added[0].content_sha256.as_deref() != Some(witness.file.sha256.as_str())
    {
        return Err(backend(
            "Product file import did not return one exact saved native source asset",
        )
        .uncertain());
    }
    Ok(added[0].id)
}

fn cycle_import_and_attach(
    service: &motionwright_service::StudioService,
    request: &BlenderProductReopenRequest,
    witness: &ProductNativeSourceWitness,
    source_root: &Path,
    actions: &mut Vec<Value>,
) -> NativeResult<(motionwright_domain::Project, Uuid)> {
    let before = product_source_scope_project(service, request, witness)?;
    check_product_staged_source(source_root, witness)?;
    let prior_capsules = before
        .production_design
        .capsules
        .iter()
        .filter(|capsule| capsule.scene_id == request.scene_id)
        .collect::<Vec<_>>();
    if prior_capsules.len() != 1 || prior_capsules[0].id != request.capsule_id {
        return Err(invalid(
            "Product atomic source save requires its existing unique capsule",
        ));
    }
    let spec = motionwright_domain::NativeCapsuleSourceSpec {
        name: witness.file.relative_path.clone(),
        media_type: BLENDER_PRODUCT_NATIVE_MEDIA_TYPE.into(),
        capsule_id: request.capsule_id,
        scene_id: request.scene_id,
        label: prior_capsules[0].label.clone(),
        editable_parameters: vec!["product.annotation.body".into()],
        native_editor_hint: "Blender".into(),
    };
    let phase = cycle_phase_name(&request.phase);
    // This operation cannot reuse a historical import-only/attach request.
    // The unchanged root source-attempt barrier still rejects old UNKNOWN work.
    let import_id = format!("{}:product-source-v2-{phase}", request.request_id);
    let import_index = actions.len();
    actions.push(json!({
        "stage": "source_import_and_capsule_bind",
        "request_id": import_id,
        "expected": request.expected,
        "source": witness.file,
        "intent": motionwright_service::FileImportIntent::NativeCapsuleSource {
            spec: spec.clone(),
        },
        "atomic_revision": true,
        "source_acceptance_inherited": false,
        "execution_grant_inherited": false,
        "terminal": {"status": "reserved"},
    }));
    let imported = service
        .import_native_capsule_source_file(
            request.project_id,
            &request.expected,
            &import_id,
            source_root.join(&witness.file.relative_path),
            spec.clone(),
        )
        .map_err(|error| storage_error(error).uncertain());
    // Preserve the mutation terminal before validating its returned source.
    cycle_record_terminal(
        actions,
        import_index,
        &imported,
        |outcome| json!({"project": outcome.project}),
    );
    let imported = imported?;
    let asset_id = cycle_imported_asset(&before, &imported.project, witness)?;
    let asset = imported
        .project
        .assets
        .iter()
        .find(|asset| asset.id == asset_id)
        .ok_or_else(|| backend("Atomic product source asset is absent").uncertain())?;
    let expected_capsule = spec.bind_asset(asset).map_err(|_| {
        backend("Atomic product source asset violates its source-save intent").uncertain()
    })?;
    let mut expected_capsules = before.production_design.capsules.clone();
    let prior_index = expected_capsules
        .iter()
        .position(|capsule| capsule.id == request.capsule_id)
        .ok_or_else(|| backend("Atomic product source lost its prior capsule").uncertain())?;
    expected_capsules[prior_index] = expected_capsule;
    if imported.project.production_design.capsules != expected_capsules {
        return Err(backend(
            "Atomic product source save did not bind exactly its closed source capsule",
        )
        .uncertain());
    }
    if service
        .project(request.project_id)
        .map_err(|error| storage_error(error).uncertain())?
        != imported.project
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Project changed after atomic native product source save",
        )
        .uncertain());
    }
    let attached_request = BlenderProductReopenRequest {
        expected: imported.project.stamp(),
        ..request.clone()
    };
    let (saved, saved_asset_id) = read_saved_product_native_source(
        service,
        &attached_request,
        witness,
        BLENDER_PRODUCT_NATIVE_MEDIA_TYPE,
    )
    .map_err(Error::uncertain)?;
    if saved != imported.project || saved_asset_id != asset_id {
        return Err(
            backend("Saved product capsule changed during atomic source verification").uncertain(),
        );
    }
    Ok((saved, asset_id))
}

fn cycle_stage_cas_source(
    service: &motionwright_service::StudioService,
    request: &BlenderProductReopenRequest,
    witness: &ProductNativeSourceWitness,
    root: &Path,
    actions: &mut Vec<Value>,
) -> NativeResult<()> {
    use std::io::Write;
    let (project, asset_id) = read_saved_product_native_source(
        service,
        request,
        witness,
        BLENDER_PRODUCT_NATIVE_MEDIA_TYPE,
    )?;
    let destination = source_output_must_be_new(root, &witness.file.relative_path)?;
    // The existing store validates immutable bytes. The destination is a fixed name
    // in an owner-provisioned output root; request data cannot supply a path.
    let bytes = {
        let handle = service.store();
        let store = handle.lock();
        if store.load_project(project.id).map_err(storage_error)? != project {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Project changed before native product CAS staging",
            ));
        }
        store
            .read_blob(&witness.file.sha256, ORIGINAL_MAX_BYTES)
            .map_err(storage_error)?
    };
    if bytes.len() as u64 != witness.file.bytes
        || hex::encode(sha2::Sha256::digest(&bytes)) != witness.file.sha256
    {
        return Err(invalid(
            "Product staging bytes do not match the attached immutable source",
        ));
    }
    let index = actions.len();
    actions.push(json!({
        "stage": "stage_attached_cas", "expected": request.expected,
        "source_asset_id": asset_id, "source": witness.file,
        "owner_output_root": root, "exclusive_create": true,
        "owner_staging_exclusion_required": true,
        "terminal": {"status": "reserved"},
    }));
    let staged = (|| -> NativeResult<()> {
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options
            .open(&destination)
            .map_err(|_| backend("Product CAS destination could not be created exclusively"))?;
        file.write_all(&bytes)
            .map_err(|_| backend("Product CAS staging write did not complete").uncertain())?;
        file.sync_all()
            .map_err(|_| backend("Product CAS staging sync did not complete").uncertain())?;
        drop(file);
        check_product_staged_source(root, witness).map_err(Error::uncertain)?;
        if service.project(project.id).map_err(storage_error)? != project {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Project changed during native product CAS staging",
            )
            .uncertain());
        }
        Ok(())
    })();
    cycle_record_terminal(
        actions,
        index,
        &staged,
        |()| json!({"cas_bytes_verified": true, "file": witness.file}),
    );
    staged
}

impl ProductionCoordinator {
    /// Run one explicit source attempt through three existing owner connections.
    /// The owner must retain exclusive control of their staging roots for this
    /// call. A failure never causes native mutation replay or a new runtime.
    pub async fn realize_blender_product_cycle(
        &self,
        editor: &ProductionCoordinator,
        verifier: &ProductionCoordinator,
        request: &BlenderProductCycleRequest,
        journal: &mut BlenderProductCycleJournal,
    ) -> NativeResult<Value> {
        let create_request = cycle_create_request(request);
        product_request_valid(&create_request)?;
        if !cycle_journal_is_empty(journal) {
            return Err(invalid("Product cycle requires one new caller journal"));
        }
        let source = read_saved_product_source(
            &self.service,
            &create_request,
            BLENDER_PRODUCT_RECIPE_MEDIA_TYPE,
            decode_blender_product_recipe,
        )?;
        let (edited_source, edit_steps) = crate::blender_product_scene::project_product_edit(
            &source.source,
            &crate::blender_product_scene::ProductEdit::Annotation(request.annotation_body.clone()),
        )
        .map_err(|_| {
            invalid("Product cycle annotation edit is outside the closed source profile")
        })?;
        if edited_source.annotation == source.source.annotation || edit_steps.len() != 1 {
            return Err(invalid(
                "Product cycle requires exactly one nontrivial annotation edit",
            ));
        }
        let roots = cycle_connection_roots([self, editor, verifier], &source.project)?;
        let claim = lineage::reserve_product_cycle(self, request, &source)?;
        journal.schema = PRODUCT_CALLER_SCHEMA.into();
        journal.root_intent = Some(claim.journal_intent());
        let result = async {
            let mut original = None;
            let created = self
                .realize_saved_blender_product_source(
                    &create_request,
                    &source,
                    &mut journal.create,
                    &mut original,
                )
                .await?;
            let original = original.ok_or_else(|| {
                backend("Completed product creation did not retain its native source witness")
                    .uncertain()
            })?;
            let original_request = cycle_reopen_request(
                request,
                &source.project,
                BlenderProductReopenPhase::EditAnnotation {
                    body: request.annotation_body.clone(),
                },
            );
            let (with_original, original_asset_id) = cycle_import_and_attach(
                &self.service,
                &original_request,
                &original,
                &roots[0],
                &mut journal.source_actions,
            )?;
            let edit_request = cycle_reopen_request(
                request,
                &with_original,
                BlenderProductReopenPhase::EditAnnotation {
                    body: request.annotation_body.clone(),
                },
            );
            cycle_stage_cas_source(
                &self.service,
                &edit_request,
                &original,
                &roots[1],
                &mut journal.source_actions,
            )?;
            let mut edited = None;
            let edit_result = editor
                .reopen_saved_blender_product_source(
                    &edit_request,
                    &original,
                    BLENDER_PRODUCT_NATIVE_MEDIA_TYPE,
                    &mut journal.edit,
                    &mut edited,
                )
                .await?;
            let edited = edited.ok_or_else(|| {
                backend("Completed product edit did not retain its native source witness")
                    .uncertain()
            })?;
            let edited_request = cycle_reopen_request(
                request,
                &with_original,
                BlenderProductReopenPhase::VerifyEdited,
            );
            let (with_edited, edited_asset_id) = cycle_import_and_attach(
                &self.service,
                &edited_request,
                &edited,
                &roots[1],
                &mut journal.source_actions,
            )?;
            let verify_request = cycle_reopen_request(
                request,
                &with_edited,
                BlenderProductReopenPhase::VerifyEdited,
            );
            cycle_stage_cas_source(
                &self.service,
                &verify_request,
                &edited,
                &roots[2],
                &mut journal.source_actions,
            )?;
            let mut unused_continuation = None;
            let verified = verifier
                .reopen_saved_blender_product_source(
                    &verify_request,
                    &edited,
                    BLENDER_PRODUCT_NATIVE_MEDIA_TYPE,
                    &mut journal.verify,
                    &mut unused_continuation,
                )
                .await?;
            if unused_continuation.is_some()
                || self
                    .service
                    .project(request.project_id)
                    .map_err(storage_error)?
                    != with_edited
            {
                return Err(
                    backend("Product verification changed its final source binding").uncertain(),
                );
            }
            let value = json!({
                "schema": PRODUCT_CALLER_SCHEMA,
                "project_id": request.project_id, "scene_id": request.scene_id,
                "capsule_id": request.capsule_id, "expected_before": request.expected,
                "expected_after": with_edited.stamp(),
                "recipe_asset_id": original.recipe_asset_id,
                "recipe_sha256": original.recipe_sha256,
                "original_source": original.file, "edited_source": edited.file,
                "create": created, "edit": edit_result, "verify": verified,
                "same_capsule_repointed": true, "native_sources_persisted_in_cas": true,
                "source_acceptance_inherited": false, "execution_grant_inherited": false,
                "delivery_profile_selected": false, "full_1200_frame_render": false,
                "decoded_pixel_difference_verified": false,
                "visual_acceptance": false, "human_acceptance": false,
            });
            lineage::complete_product_cycle(
                &claim,
                &source,
                (&original, &with_original, original_asset_id),
                (&edited, &with_edited, edited_asset_id),
                &verified,
                value,
            )
            .map_err(Error::uncertain)
        }
        .await;
        lineage::finish_product_cycle(&self.service, claim, result, &mut journal.root_terminal)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::blender_product_scene::{CameraCurve, Coating};
    use motionwright_domain::{Fidelity, FidelityReport, NativeCapsule, NativePropertyFidelity};

    fn recipe() -> ProductScene {
        ProductScene::vessel("CallerFixture", CameraCurve::BezierAutoClamped)
    }

    #[test]
    fn product_recipe_decoder_retains_the_explicit_curve_and_source_fields() {
        let mut source = recipe();
        source.coating = Coating::Graphite;
        source.roughness = 0.0;
        source.metallic = 1.0;
        source.samples = 256;
        source.annotation = "SOURCE\nPRESERVED".into();
        let bytes = serde_json::to_vec(&source).unwrap();
        assert_eq!(decode_blender_product_recipe(&bytes).unwrap(), source);
        assert_eq!(
            decode_blender_product_recipe(&bytes).unwrap().camera_curve,
            CameraCurve::BezierAutoClamped
        );
    }

    #[test]
    fn product_recipe_decoder_rejects_unprojected_fields_and_out_of_profile_sources() {
        let base = serde_json::to_value(recipe()).unwrap();
        let mut variants = Vec::new();
        for (key, value) in [
            ("version", json!(2)),
            ("namespace", json!("../source")),
            ("roughness", json!(-0.01)),
            ("metallic", json!(1.01)),
            ("samples", json!(15)),
            ("camera_curve", json!("native_default")),
            ("annotation", json!("A\nB\nC")),
            ("annotation", json!("")),
            ("annotation", json!("\u{00e1}")),
            ("execution_grant", json!({"allow": true})),
            ("path", json!("/tmp/untrusted.blend")),
        ] {
            let mut candidate = base.clone();
            candidate[key] = value;
            variants.push(serde_json::to_vec(&candidate).unwrap());
        }
        variants.push(vec![]);
        variants.push(vec![b' '; PRODUCT_RECIPE_MAX_BYTES as usize + 1]);
        variants.push(vec![0xff]);
        variants.push(b"null".to_vec());
        for bytes in variants {
            assert!(decode_blender_product_recipe(&bytes).is_err());
        }
        let mut duplicate = serde_json::to_string(&base).unwrap();
        duplicate.insert_str(1, "\"version\":1,");
        assert!(decode_blender_product_recipe(duplicate.as_bytes()).is_err());
    }

    #[test]
    fn product_cycle_request_cannot_import_a_source_path_codec_or_execution_grant() {
        let project = motionwright_domain::Project::new("Request fixture").unwrap();
        let request = BlenderProductCycleRequest {
            project_id: project.id,
            expected: project.stamp(),
            scene_id: Uuid::from_u128(2),
            capsule_id: Uuid::from_u128(3),
            request_id: "product-fixture".into(),
            annotation_body: "EDITED\nSURFACE".into(),
        };
        let value = serde_json::to_value(&request).unwrap();
        serde_json::from_value::<BlenderProductCycleRequest>(value.clone()).unwrap();
        for key in [
            "source",
            "path",
            "mime",
            "decoder",
            "grant",
            "request_stamp",
        ] {
            let mut malicious = value.clone();
            malicious[key] = json!("untrusted");
            assert!(serde_json::from_value::<BlenderProductCycleRequest>(malicious).is_err());
        }
    }

    #[test]
    fn product_capsule_repoint_discards_every_prior_source_fidelity_claim() {
        let prior = NativeCapsule {
            id: Uuid::from_u128(1),
            scene_id: Uuid::from_u128(2),
            source_asset_id: Uuid::from_u128(3),
            source_sha256: "a".repeat(64),
            label: "Existing capsule identity".into(),
            fidelity: FidelityReport {
                renderer: "Prior renderer".into(),
                renderer_version: "prior".into(),
                visual: Fidelity::Native,
                temporal: Fidelity::Native,
                structural: Fidelity::Native,
                editable: Fidelity::Native,
                losses: vec![],
                evidence_sha256: Some("b".repeat(64)),
            },
            editable_parameters: vec!["unrelated.previous.property".into()],
            native_editor_hint: "Prior editor".into(),
            property_fidelity: vec![NativePropertyFidelity {
                path: "prior.property".into(),
                fidelity: Fidelity::Native,
                loss_note: None,
            }],
        };
        let immutable_prior = prior.clone();
        let next = product_repoint_capsule(&prior, Uuid::from_u128(4), &"c".repeat(64));
        assert_eq!(prior, immutable_prior);
        assert_eq!(
            (next.id, next.scene_id, &next.label),
            (prior.id, prior.scene_id, &prior.label)
        );
        assert_ne!(next.source_asset_id, prior.source_asset_id);
        assert_eq!(next.source_sha256, "c".repeat(64));
        assert_eq!(
            [
                next.fidelity.visual,
                next.fidelity.temporal,
                next.fidelity.structural,
                next.fidelity.editable
            ],
            [Fidelity::Unavailable; 4]
        );
        assert_eq!(next.fidelity.evidence_sha256, None);
        assert_eq!(next.fidelity.losses.len(), 1);
        assert!(!next.fidelity.losses[0].is_empty());
        assert!(!next.fidelity.usable_without_loss_consent());
        assert!(next.property_fidelity.is_empty());
        assert_eq!(next.editable_parameters, vec!["product.annotation.body"]);
        assert_eq!(next.native_editor_hint, "Blender");
        next.fidelity.validate().unwrap();
    }
}
