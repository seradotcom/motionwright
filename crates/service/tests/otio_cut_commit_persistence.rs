//! One canonical revision for a multi-operation reviewed OTIO edit.
//! No NLE executable, relinker, media file or paid provider is invoked.
use motionwright_domain::{
    Change, OTIO_PREVIEW_SCHEMA, OtioEditPreview, OtioNativeCut, OtioOwnerCutReview,
    OtioPropertyFidelity, Project, RationalTime,
};
use motionwright_service::StudioService;

fn source(service: &StudioService) -> Project {
    let mut project = service
        .create_project("Owner source edited in an external NLE")
        .unwrap();
    for (index, name) in ["Hook", "Proof", "Close"].iter().enumerate() {
        project = service
            .apply(
                project.id,
                &project.stamp(),
                &format!("otio:scene:{index}"),
                &Change::AddScene {
                    name: (*name).into(),
                    objective: "Preserve editable semantics".into(),
                    duration_seconds: 4,
                },
            )
            .unwrap()
            .project;
    }
    project
}
fn review(project: &Project) -> OtioOwnerCutReview {
    let ids = project.scenes.iter().map(|s| s.id).collect::<Vec<_>>();
    let properties = [
        ("scene_order", "editable_otio", "bounded_proposal_only"),
        (
            "scene_duration",
            "editable_otio",
            "bounded_frame_exact_end_trim_only",
        ),
        (
            "source_video_media",
            "verified_external_reference",
            "source_sha_revalidated_no_new_media",
        ),
        (
            "media_source_in_point",
            "represented_in_otio",
            "not_supported_native_scene_offset",
        ),
        (
            "unbound_scene_media",
            "missing_reference",
            "cannot_invent_media",
        ),
        (
            "native_object_geometry_hierarchy_text_masks",
            "source_project_only",
            "not_supported",
        ),
        (
            "camera_keyframes_effects_and_procedural_controls",
            "source_project_only",
            "not_supported",
        ),
        (
            "source_audio_and_voice_cues",
            "source_project_only",
            "not_roundtripped_as_nle_audio",
        ),
        (
            "captions_transcripts_alignment",
            "source_project_only",
            "not_roundtripped",
        ),
        (
            "human_locks_creative_reviews_branches",
            "source_project_only",
            "not_roundtripped",
        ),
        (
            "runtime_render_authority",
            "not_exported",
            "requires_separate_owner_grant",
        ),
        (
            "publication_rights",
            "not_granted",
            "requires_separate_owner_approval",
        ),
    ];
    OtioOwnerCutReview {
        preview: Box::new(OtioEditPreview {
            schema: OTIO_PREVIEW_SCHEMA.into(),
            source_project_sha256: "a".repeat(64),
            project_id: project.id,
            generation: project.generation,
            expected_project_revision: project.revision,
            original_scene_order: ids.clone(),
            proposed_scene_order: vec![ids[2], ids[0], ids[1]],
            native_change_proposals: vec![
                OtioNativeCut::MoveScene {
                    scene_id: ids[2],
                    to_index: 0,
                },
                OtioNativeCut::SetSceneDuration {
                    scene_id: ids[0],
                    duration: RationalTime::new(3, 1).unwrap(),
                },
            ],
            media_refs_verified: 0,
            missing_references_retained: 3,
            loss_report: vec!["Other NLE audio/effects remain untransferred".into()],
            property_fidelity: properties
                .into_iter()
                .map(|(a, b, c)| OtioPropertyFidelity {
                    property: a.into(),
                    status: b.into(),
                    import_state: c.into(),
                    missing_count: (a == "unbound_scene_media").then_some(3),
                })
                .collect(),
            edit_imported: false,
            source_media_relinked: false,
            owner_revision_commit_required: true,
            human_review_required: true,
            rendering_performed: false,
            semantic_native_roundtrip: "NOT_SUPPORTED".into(),
            nle_receiver_acceptance: "NOT_VERIFIED".into(),
            publication_approved: false,
        }),
        reviewer_declaration: "Owner (self-declared)".into(),
        rationale: "I reviewed the cut order and tail trim; no external media or effects imported"
            .into(),
        acknowledges_property_losses: true,
        acknowledges_absolute_audio_marker_positions: false,
        confirms_single_revision_change: true,
    }
}

#[test]
fn one_owner_authorized_review_commits_both_cut_operations_with_one_sqlite_cas() {
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("owner-reviewed-otio.sqlite3");
    let store = StudioService::open(&db).unwrap();
    let original = source(&store);
    let change = Change::ApplyOtioOwnerCutReview {
        review: Box::new(review(&original)),
    };
    let result = store
        .apply(
            original.id,
            &original.stamp(),
            "review:original-nle-cut",
            &change,
        )
        .unwrap();
    let final_project = result.project;
    assert_eq!(final_project.revision, original.revision + 1);
    assert_eq!(
        final_project
            .scenes
            .iter()
            .map(|s| s.id)
            .collect::<Vec<_>>(),
        vec![
            original.scenes[2].id,
            original.scenes[0].id,
            original.scenes[1].id
        ]
    );
    assert_eq!(
        final_project.scenes[1].duration,
        RationalTime::new(3, 1).unwrap()
    );
    assert_eq!(
        final_project.scenes[2].start,
        RationalTime::new(7, 1).unwrap()
    );
    assert_eq!(final_project.assets, original.assets);
    let replay = store
        .apply(
            original.id,
            &original.stamp(),
            "review:original-nle-cut",
            &change,
        )
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, final_project);
    assert!(
        store
            .apply(
                original.id,
                &original.stamp(),
                "review:stale-other",
                &change
            )
            .is_err()
    );
    assert_eq!(store.project(original.id).unwrap(), final_project);
    drop(store);
    assert_eq!(
        StudioService::open(&db)
            .unwrap()
            .project(original.id)
            .unwrap(),
        final_project
    );
}
#[test]
fn failed_later_trim_must_not_commit_earlier_reorder_or_create_a_history_event() {
    let temp = tempfile::tempdir().unwrap();
    let store = StudioService::open(temp.path().join("failed-otio.sqlite3")).unwrap();
    let mut original = source(&store);
    original = store
        .apply(
            original.id,
            &original.stamp(),
            "lock:tail",
            &Change::SetLock {
                resource: format!("scene:{}", original.scenes[0].id),
                kind: motionwright_domain::LockKind::Timing,
                note: "Owner-approved source timing".into(),
            },
        )
        .unwrap()
        .project;
    let change = Change::ApplyOtioOwnerCutReview {
        review: Box::new(review(&original)),
    };
    assert!(
        store
            .apply(original.id, &original.stamp(), "review:locked-cut", &change)
            .is_err()
    );
    assert_eq!(store.project(original.id).unwrap(), original);
}
#[test]
fn imported_original_project_has_a_fresh_generation_and_cannot_accept_stale_preview() {
    let temp = tempfile::tempdir().unwrap();
    let sender = StudioService::open(temp.path().join("otio-send.sqlite3")).unwrap();
    let original = source(&sender);
    let previous = review(&original);
    let backup = sender.export_project(original.id).unwrap();
    let receiver = StudioService::open(temp.path().join("otio-receiver.sqlite3")).unwrap();
    let imported = receiver.import_project(&backup).unwrap();
    assert_ne!(imported.generation, original.generation);
    let mut attempted = imported.clone();
    assert!(
        attempted
            .apply_change(&Change::ApplyOtioOwnerCutReview {
                review: Box::new(previous)
            })
            .is_err()
    );
    assert_eq!(attempted, imported);
    assert_eq!(receiver.project(imported.id).unwrap(), imported);
}
