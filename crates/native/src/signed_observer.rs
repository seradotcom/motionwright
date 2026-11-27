//! Native scope for a signed observation of a saved, verified PNG.
use super::*;

pub(super) const SCOPE: &str = "signed-frame-inspection:";

pub(super) fn selection(query: &Query) -> NativeResult<Option<Uuid>> {
    let Some(value) = query.scope.strip_prefix(SCOPE) else {
        return Ok(None);
    };
    if query.limit != 1 || query.cursor.is_some() {
        return Err(Error::invalid(
            "Signed frame inspection requires one item and no cursor",
        ));
    }
    Uuid::parse_str(value)
        .ok()
        .filter(|id| !id.is_nil() && id.to_string() == value)
        .map(Some)
        .ok_or_else(|| Error::invalid("Signed frame inspection requires a canonical asset UUID"))
}

fn require_attached_frame(
    asset_sha256: &str,
    review: &motionwright_service::ObserverReview,
) -> NativeResult<()> {
    if review.source_frame_sha256 != asset_sha256 {
        return Err(Error::invalid(
            "The signed observation does not measure the selected saved PNG",
        ));
    }
    Ok(())
}

pub(super) async fn observe(
    service: &StudioService,
    identity: Option<&graph_store_provider::MountedStoreIdentity>,
    query: &Query,
    context: &CallContext,
    asset_id: Uuid,
) -> NativeResult<ObservationPage> {
    // The application's existing resource-read scope requires genuine Host admission.
    context.driver_context()?;
    let identity = identity.ok_or_else(|| {
        Error::new(
            ErrorCode::PermissionDenied,
            "Signed frame inspection requires the owner-mounted Store",
        )
    })?;
    let project_id = project_id_from_resource(&query.resource)?;
    let project = identity.current_project(service, project_id)?;
    let version = resource_version(&project)?;
    if let Some(expected) = context.expected() {
        expected.validate()?;
        if expected != &version {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "The signed frame reference is stale",
            ));
        }
    }
    let digest = project
        .assets
        .iter()
        .find(|asset| asset.id == asset_id && asset.media_type == "image/png")
        .and_then(|asset| asset.content_sha256.clone())
        .ok_or_else(|| {
            Error::new(
                ErrorCode::NotFound,
                "No saved PNG matches the selected asset",
            )
        })?;
    let (config, evidence) = graph_store_provider::signed_observer_directories(asset_id)?;
    context.check_cancelled()?;
    let reader = service.clone();
    let stamp = project.stamp();
    let review = tokio::task::spawn_blocking(move || {
        // Verify the original attached CAS content before reading independent evidence.
        reader
            .verified_asset_media(project_id, &stamp, asset_id, &digest)
            .map_err(storage_error)?;
        let review = reader
            .review_observer_evidence(project_id, &stamp, &config, &evidence)
            .map_err(storage_error)?;
        require_attached_frame(&digest, &review)?;
        Ok::<_, Error>(review)
    })
    .await
    .map_err(|_| {
        Error::new(
            ErrorCode::BackendFailed,
            "Signed frame inspection worker failed",
        )
    })??;
    context.check_cancelled()?;
    if identity.current_project(service, project_id)? != project {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Project changed during signed frame inspection",
        ));
    }
    let page = ObservationPage {
        version,
        scope: query.scope.clone(),
        items: vec![json!({
            "schema": "motionwright.native.signed-frame-review/1",
            "asset_id": asset_id,
            "review": review
        })],
        complete: true,
        next: None,
    };
    page.validate_for(query)?;
    context.check_cancelled()?;
    Ok(page)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn signed_frame_scope_refuses_local_context_and_malformed_selection() {
        let root = tempfile::tempdir().unwrap();
        let service = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        let project = service.create_project("Signed source").unwrap();
        let asset_id = Uuid::new_v4();
        let mut query = Query {
            resource: project.resource_key(),
            scope: format!("{SCOPE}{asset_id}"),
            limit: 1,
            cursor: None,
        };
        assert_eq!(selection(&query).unwrap(), Some(asset_id));
        let context = CallContext::application_local("signed-source-negative").unwrap();
        let observer = MotionwrightObserver::new(service.clone());
        let error = observer.observe(&query, &context).await.unwrap_err();
        let denied = context
            .driver_context()
            .err()
            .expect("a local context has no Host admission");
        assert_eq!(error.code, denied.code);
        assert_eq!(service.project(project.id).unwrap(), project);
        query.limit = 2;
        assert!(selection(&query).is_err());
        query.limit = 1;
        query.scope = format!("{SCOPE}{}", Uuid::nil());
        assert!(selection(&query).is_err());
    }

    #[test]
    fn signed_frame_review_cannot_substitute_another_asset() {
        let expected = "a".repeat(64);
        let review = motionwright_service::ObserverReview {
            owner_generation: 7,
            verifier_id: "independent_png".into(),
            verifier_version: "1.0.0".into(),
            method: "png_dimensions_v1".into(),
            units: "pixels".into(),
            coverage: "single_full_frame".into(),
            producer_sha256: "b".repeat(64),
            source_frame_sha256: expected.clone(),
            frame_index: 0,
            width: 320,
            height: 180,
            limitations: vec!["Dimensions only.".into()],
            gate_eligible: false,
        };
        assert!(require_attached_frame(&expected, &review).is_ok());
        assert!(require_attached_frame(&"c".repeat(64), &review).is_err());
        assert!(!review.gate_eligible);
    }
}
