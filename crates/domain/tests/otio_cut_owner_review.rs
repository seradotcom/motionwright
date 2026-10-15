use motionwright_domain::{
    Change, LockKind, OTIO_PREVIEW_SCHEMA, OtioEditPreview, OtioNativeCut, OtioOwnerCutReview,
    OtioPropertyFidelity, Project, RationalTime, SceneStatus,
};
use uuid::Uuid;

fn source() -> Project {
    let mut project = Project::new("Original film, unchanged native semantics").unwrap();
    for name in ["Hook", "Evidence", "Close"] {
        project
            .apply_change(&Change::AddScene {
                name: name.into(),
                objective: "Preserve first-party creative work".into(),
                duration_seconds: 4,
            })
            .unwrap();
    }
    project
}
fn fidelity(missing: usize) -> Vec<OtioPropertyFidelity> {
    const ITEMS: [(&str, &str, &str); 12] = [
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
    ITEMS
        .iter()
        .map(|(property, status, import_state)| OtioPropertyFidelity {
            property: (*property).into(),
            status: (*status).into(),
            import_state: (*import_state).into(),
            missing_count: (*property == "unbound_scene_media").then_some(missing),
        })
        .collect()
}
fn review(project: &Project) -> OtioOwnerCutReview {
    let ids = project.scenes.iter().map(|s| s.id).collect::<Vec<_>>();
    OtioOwnerCutReview {
        preview: Box::new(OtioEditPreview {
            schema: OTIO_PREVIEW_SCHEMA.into(),
            source_project_sha256: "a".repeat(64),
            project_id: project.id,
            generation: project.generation,
            expected_project_revision: project.revision,
            original_scene_order: ids.clone(),
            proposed_scene_order: vec![ids[1], ids[0], ids[2]],
            native_change_proposals: vec![
                OtioNativeCut::MoveScene {
                    scene_id: ids[1],
                    to_index: 0,
                },
                OtioNativeCut::SetSceneDuration {
                    scene_id: ids[0],
                    duration: RationalTime::new(2, 1).unwrap(),
                },
            ],
            media_refs_verified: 2,
            missing_references_retained: 1,
            loss_report: vec![
                "Motionwright semantic geometry cannot roundtrip as OTIO".into(),
                "Imported media rights remain separately unverified".into(),
            ],
            property_fidelity: fidelity(1),
            edit_imported: false,
            source_media_relinked: false,
            owner_revision_commit_required: true,
            human_review_required: true,
            rendering_performed: false,
            semantic_native_roundtrip: "NOT_SUPPORTED".into(),
            nle_receiver_acceptance: "NOT_VERIFIED".into(),
            publication_approved: false,
        }),
        reviewer_declaration: "Original project owner, not independently authenticated".into(),
        rationale: "Approve bounded rearrangement and tail trim only; keep all editable nodes"
            .into(),
        acknowledges_property_losses: true,
        acknowledges_absolute_audio_marker_positions: false,
        confirms_single_revision_change: true,
    }
}

#[test]
fn one_atomic_otio_review_preserves_native_scenes_and_reflows_only_approved_cuts() {
    let mut p = source();
    let original = p.clone();
    let cut = review(&p);
    let encoded = serde_json::to_value(&cut.preview).unwrap();
    assert_eq!(encoded["schema"], OTIO_PREVIEW_SCHEMA);
    p.apply_change(&Change::ApplyOtioOwnerCutReview {
        review: Box::new(cut),
    })
    .unwrap();
    assert_eq!(p.scenes.len(), 3);
    assert_eq!(p.scenes[0].id, original.scenes[1].id);
    assert_eq!(p.scenes[1].id, original.scenes[0].id);
    assert_eq!(p.scenes[2].id, original.scenes[2].id);
    assert_eq!(p.scenes[0].start, RationalTime::ZERO);
    assert_eq!(p.scenes[1].start, original.scenes[0].duration);
    assert_eq!(p.scenes[1].duration, RationalTime::new(2, 1).unwrap());
    assert_eq!(p.scenes[2].start, RationalTime::new(6, 1).unwrap());
    assert_eq!(p.scenes[1].nodes, original.scenes[0].nodes);
    assert_eq!(p.assets, original.assets);
    assert_eq!(p.paid_generation, original.paid_generation);
}

#[test]
fn stale_foreign_forged_or_structurally_ambiguous_otio_review_fails_closed() {
    let p = source();
    let good = review(&p);
    let mut attempts = Vec::new();
    let mut item = good.clone();
    item.preview.project_id = Uuid::new_v4();
    attempts.push(item);
    let mut item = good.clone();
    item.preview.generation = Uuid::new_v4();
    attempts.push(item);
    let mut item = good.clone();
    item.preview.expected_project_revision += 1;
    attempts.push(item);
    let mut item = good.clone();
    item.preview.source_project_sha256 = "INVALID".into();
    attempts.push(item);
    let mut item = good.clone();
    item.preview.proposed_scene_order[2] = item.preview.proposed_scene_order[0];
    attempts.push(item);
    let mut item = good.clone();
    item.preview.original_scene_order.swap(0, 1);
    attempts.push(item);
    let mut item = good.clone();
    item.preview.native_change_proposals.swap(0, 1);
    attempts.push(item);
    let mut item = good.clone();
    item.preview
        .native_change_proposals
        .push(good.preview.native_change_proposals[0].clone());
    attempts.push(item);
    let mut item = good.clone();
    item.preview.property_fidelity[0].status = "verified_code_execution".into();
    attempts.push(item);
    let mut item = good.clone();
    item.preview.property_fidelity.remove(0);
    attempts.push(item);
    let mut item = good.clone();
    item.preview.media_refs_verified = 3;
    attempts.push(item);
    let mut item = good.clone();
    item.preview.edit_imported = true;
    attempts.push(item);
    let mut item = good.clone();
    item.preview.publication_approved = true;
    attempts.push(item);
    let mut item = good.clone();
    item.preview.semantic_native_roundtrip = "PASS".into();
    attempts.push(item);
    let mut item = good.clone();
    item.acknowledges_property_losses = false;
    attempts.push(item);
    let mut item = good.clone();
    item.confirms_single_revision_change = false;
    attempts.push(item);
    let mut item = good.clone();
    item.preview.native_change_proposals.clear();
    attempts.push(item);
    let mut item = good.clone();
    item.preview.native_change_proposals[1] = OtioNativeCut::SetSceneDuration {
        scene_id: p.scenes[0].id,
        duration: RationalTime::new(6, 1).unwrap(),
    };
    attempts.push(item);
    for (index, cut) in attempts.into_iter().enumerate() {
        let mut document = p.clone();
        assert!(
            document
                .apply_change(&Change::ApplyOtioOwnerCutReview {
                    review: Box::new(cut)
                })
                .is_err(),
            "forged or unauthorized OTIO case {index} was admitted"
        );
        assert_eq!(document, p);
    }
}

#[test]
fn a_late_locked_scene_failure_cannot_commit_an_earlier_valid_reorder() {
    let mut p = source();
    let first = p.scenes[0].id;
    p.apply_change(&Change::SetLock {
        resource: format!("scene:{first}"),
        kind: LockKind::Timing,
        note: "Do not trim approved timing".into(),
    })
    .unwrap();
    let saved = p.clone();
    assert!(
        p.apply_change(&Change::ApplyOtioOwnerCutReview {
            review: Box::new(review(&p))
        })
        .is_err()
    );
    assert_eq!(p, saved, "the entire compound cut must be atomic");
}

#[test]
fn protected_reviews_and_nonportable_absolute_markers_require_explicit_handling() {
    let mut p = source();
    p.scenes[0].status = SceneStatus::Approved;
    assert!(
        p.apply_change(&Change::ApplyOtioOwnerCutReview {
            review: Box::new(review(&p))
        })
        .is_err()
    );
    p.scenes[0].status = SceneStatus::Draft;
    p.markers.push(motionwright_domain::Marker {
        id: Uuid::new_v4(),
        at: RationalTime::ZERO,
        label: "Original absolute cue".into(),
    });
    let mut cut = review(&p);
    let before = p.clone();
    assert!(
        p.apply_change(&Change::ApplyOtioOwnerCutReview {
            review: Box::new(cut.clone())
        })
        .is_err()
    );
    assert_eq!(p, before);
    cut.acknowledges_absolute_audio_marker_positions = true;
    p.apply_change(&Change::ApplyOtioOwnerCutReview {
        review: Box::new(cut),
    })
    .unwrap();
    assert_eq!(
        p.markers, before.markers,
        "absolute markers are never silently retimed"
    );
}

#[test]
fn preview_json_is_strict_and_never_installs_executable_fields() {
    let p = source();
    let cut = review(&p);
    let mut json = serde_json::to_value(cut).unwrap();
    json["preview"]["native_change_proposals"][0]["effect"] = "arbitrary-code".into();
    assert!(serde_json::from_value::<OtioOwnerCutReview>(json).is_err());
    let mut json = serde_json::to_value(review(&p)).unwrap();
    json["preview"]["new_media_file"] = "https://unverified.example/file.mp4".into();
    assert!(serde_json::from_value::<OtioOwnerCutReview>(json).is_err());
}
