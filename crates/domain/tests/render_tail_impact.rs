//! Source-timeline dirty windows only; never promotes estimated affected
//! frames into actual native cache savings or renderer wallclock claims.
use motionwright_domain::*;
use uuid::Uuid;

fn source() -> Project {
    let mut p = Project::new("Three original editable scenes").unwrap();
    for label in ["Open", "Explain", "Close"] {
        p.apply_change(&Change::AddScene {
            name: label.into(),
            objective: format!("An original {label} source scene"),
            duration_seconds: 2,
        })
        .unwrap();
    }
    p
}
fn request(p: &Project, cause: RenderImpactCause, scenes: Vec<Uuid>) -> RenderTailInspectRequest {
    RenderTailInspectRequest {
        expected_generation: p.generation,
        expected_revision: p.revision,
        changed_scene_ids: scenes,
        cause,
        tail_frames: 0,
        tail_source_asset_id: None,
    }
}
#[test]
fn visual_edit_affects_only_owned_source_interval() {
    let p = source();
    let changed = p.scenes[1].id;
    let result = p
        .inspect_render_tail_impact(&request(&p, RenderImpactCause::SceneVisual, vec![changed]))
        .unwrap();
    assert_eq!(
        result.profiles[0].source_timeline_windows,
        vec![SourceFrameWindow {
            start: 60,
            end: 120
        }]
    );
    assert_eq!(result.profiles[0].affected_scene_ids, vec![changed]);
    assert_eq!(result.project_revision, p.revision);
    assert_eq!(result.schema, RENDER_TAIL_IMPACT_SCHEMA);
    assert!(!result.actually_invalidated_caches);
    assert_eq!(result.native_frames_regenerated_or_avoided, None);
    assert_eq!(result.native_renderer_saved_wallclock_micros, None);
    assert!(!result.profiles[0].output_frame_projection_verified);
    assert!(!result.profiles[0].exceeds_source_duration);
}
#[test]
fn authenticated_source_audio_tail_spills_into_adjacent_scene_without_fabricating_cache_work() {
    let mut p = source();
    let source = Asset {
        id: Uuid::new_v4(),
        name: "Original measured sound".into(),
        media_type: "audio/wav".into(),
        content_sha256: Some("a".repeat(64)),
        source_revision: Some("test-measured-voice".into()),
    };
    p.apply_change(&Change::AddAsset {
        asset: source.clone(),
    })
    .unwrap();
    let mut query = request(&p, RenderImpactCause::SceneAudioTail, vec![p.scenes[0].id]);
    query.tail_frames = 15;
    query.tail_source_asset_id = Some(source.id);
    let report = p.inspect_render_tail_impact(&query).unwrap();
    assert_eq!(report.declared_audio_tail_frames, 15);
    assert_eq!(
        report.profiles[0].source_timeline_windows,
        vec![SourceFrameWindow { start: 0, end: 75 }]
    );
    assert_eq!(
        report.profiles[0].affected_scene_ids,
        vec![p.scenes[0].id, p.scenes[1].id]
    );
    let mut scoped = p.clone();
    let target = scoped.scenes[1].id;
    scoped.deliverables[0].included_scene_ids = vec![target];
    let subset = scoped.inspect_render_tail_impact(&query).unwrap();
    assert_eq!(
        subset.profiles[0].source_timeline_windows,
        vec![SourceFrameWindow { start: 60, end: 75 }]
    );
    assert_eq!(subset.profiles[0].affected_scene_ids, vec![target]);
    assert!(subset.profiles[0].is_scoped_selection);
    assert!(!subset.profiles[0].output_frame_projection_verified);
}
#[test]
fn scene_retime_conservatively_marks_all_downstream_even_with_cuts_and_profiles() {
    let mut p = source();
    let selected = p.scenes[2].id;
    p.deliverables[0].included_scene_ids = vec![selected];
    let query = request(&p, RenderImpactCause::TimeRipple, vec![p.scenes[1].id]);
    let result = p.inspect_render_tail_impact(&query).unwrap();
    assert_eq!(
        result.profiles[0].source_timeline_windows,
        vec![SourceFrameWindow {
            start: 120,
            end: 180
        }]
    );
    assert_eq!(result.profiles[0].affected_scene_ids, vec![selected]);
    assert!(result.profiles[0].is_scoped_selection);
}
#[test]
fn global_mix_change_never_claims_only_one_scene_dirty() {
    let p = source();
    let query = request(&p, RenderImpactCause::GlobalMix, vec![]);
    let result = p.inspect_render_tail_impact(&query).unwrap();
    assert_eq!(
        result.profiles[0].source_timeline_windows,
        vec![SourceFrameWindow { start: 0, end: 180 }]
    );
    assert_eq!(
        result.profiles[0].affected_scene_ids,
        p.scenes.iter().map(|s| s.id).collect::<Vec<_>>()
    );
    assert_eq!(result.native_renderer_saved_wallclock_micros, None);
}
#[test]
fn different_exact_rational_frame_rates_do_not_round_or_invent_output_cut_times() {
    let mut p = source();
    p.deliverables[0].frame_rate = RationalTime::new(24, 1).unwrap();
    p.deliverables[1].frame_rate = RationalTime::new(30000, 1001).unwrap();
    let query = request(&p, RenderImpactCause::SceneVisual, vec![p.scenes[0].id]);
    let result = p.inspect_render_tail_impact(&query).unwrap();
    assert_eq!(
        result.profiles[0].source_timeline_windows,
        vec![SourceFrameWindow { start: 0, end: 48 }]
    );
    assert_eq!(
        result.profiles[1].source_timeline_windows,
        vec![SourceFrameWindow { start: 0, end: 60 }]
    );
}
#[test]
fn audit_scope_rejects_stale_generation_revision_duplicate_scene_and_missing_tail_source() {
    let p = source();
    let mut query = request(&p, RenderImpactCause::SceneVisual, vec![p.scenes[0].id]);
    query.expected_revision = p.revision + 1;
    assert!(
        p.inspect_render_tail_impact(&query)
            .unwrap_err()
            .to_string()
            .contains("stale")
    );
    query.expected_revision = p.revision;
    query.expected_generation = Uuid::new_v4();
    assert!(p.inspect_render_tail_impact(&query).is_err());
    query.expected_generation = p.generation;
    query.changed_scene_ids.push(query.changed_scene_ids[0]);
    assert!(p.inspect_render_tail_impact(&query).is_err());
    query.changed_scene_ids = vec![Uuid::new_v4()];
    assert!(p.inspect_render_tail_impact(&query).is_err());
    query.changed_scene_ids = vec![p.scenes[0].id];
    query.cause = RenderImpactCause::SceneAudioTail;
    query.tail_frames = 30;
    assert!(p.inspect_render_tail_impact(&query).is_err());
    query.tail_source_asset_id = Some(Uuid::new_v4());
    assert!(p.inspect_render_tail_impact(&query).is_err());
    query.tail_source_asset_id = None;
    query.cause = RenderImpactCause::SceneVisual;
    assert!(p.inspect_render_tail_impact(&query).is_err());
    query.tail_frames = 0;
    query.cause = RenderImpactCause::GlobalMix;
    assert!(p.inspect_render_tail_impact(&query).is_err());
    query.changed_scene_ids.clear();
    assert!(p.inspect_render_tail_impact(&query).is_ok());
}
#[test]
fn audio_tail_exceeding_output_duration_is_flagged_not_silently_dropped() {
    let mut p = source();
    let sound = Asset {
        id: Uuid::new_v4(),
        name: "Source sound bytes".into(),
        media_type: "audio/wav".into(),
        content_sha256: Some("a".repeat(64)),
        source_revision: None,
    };
    p.apply_change(&Change::AddAsset {
        asset: sound.clone(),
    })
    .unwrap();
    let mut query = request(&p, RenderImpactCause::SceneAudioTail, vec![p.scenes[2].id]);
    query.tail_frames = 30;
    query.tail_source_asset_id = Some(sound.id);
    let report = p.inspect_render_tail_impact(&query).unwrap();
    assert!(report.profiles[0].exceeds_source_duration);
    assert_eq!(
        report.profiles[0].source_timeline_windows,
        vec![SourceFrameWindow {
            start: 120,
            end: 180
        }]
    );
    assert!(!report.actually_invalidated_caches);
}
#[test]
fn unknown_fields_and_unverifiable_audio_fps_fail_closed_without_modifying_project() {
    let mut p = source();
    let untouched = p.clone();
    p.deliverables[0].frame_rate = RationalTime::new(241, 1).unwrap();
    assert!(
        p.inspect_render_tail_impact(&request(&p, RenderImpactCause::GlobalMix, vec![]))
            .is_err()
    );
    assert_eq!(untouched.scenes, p.scenes);
    let mut payload = serde_json::to_value(request(
        &untouched,
        RenderImpactCause::SceneVisual,
        vec![untouched.scenes[0].id],
    ))
    .unwrap();
    payload["true_cache_savings"] = serde_json::json!(99);
    assert!(serde_json::from_value::<RenderTailInspectRequest>(payload).is_err());
}
