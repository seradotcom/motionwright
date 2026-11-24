//! Original Desktop code with real SQLite and original fixture PNG bytes.
//! Provider envelopes below are explicit synthetic transport fixtures;
//! no Tauri host, Broker, renderer, human approval or physical acceptance runs.
#[allow(dead_code)]
#[path = "../../../apps/desktop/src-tauri/src/effect_grants.rs"]
mod effect_grants;
#[path = "../../../apps/desktop/src-tauri/src/hyperframes_render.rs"]
mod hyperframes_render;
#[allow(dead_code)]
#[path = "../../../apps/desktop/src-tauri/src/hyperframes_source.rs"]
mod hyperframes_source;
#[allow(dead_code)]
#[path = "../../../apps/desktop/src-tauri/src/native_preview.rs"]
mod native_preview;

#[cfg(test)]
mod desktop_render_tests {
    use super::{
        effect_grants::*, hyperframes_render::*, hyperframes_source::*, native_preview::*,
    };
    use motionwright_domain::{
        CanvasNode, Change, CoordinateSpace, NodeStyle, Project, RationalTime, VisualToken,
    };
    use motionwright_native::{
        hyperframes::compile_hyperframes_source, production::HyperframesSourceEvidence,
    };
    use motionwright_service::{ProductionReceiptInput, StudioService};
    use serde_json::{Value, json};
    use sha2::{Digest, Sha256};
    use std::{collections::BTreeSet, fs, path::Path};
    use uuid::Uuid;
    fn sha(bytes: &[u8]) -> String {
        format!("{:x}", Sha256::digest(bytes))
    }
    fn fixture() -> (tempfile::TempDir, StudioService, Project) {
        let dir = tempfile::tempdir().unwrap();
        let service = StudioService::open(dir.path().join("original.sqlite3")).unwrap();
        let mut p = service
            .create_project("HyperFrames Desktop original receipt fixture")
            .unwrap();
        let mut language = p.visual_language.clone();
        language.palette.retain(|token| token.name != "background");
        language.palette.push(VisualToken {
            name: "background".into(),
            value: "#101820".into(),
        });
        p = service
            .apply(
                p.id,
                &p.stamp(),
                "language",
                &Change::SetVisualLanguage {
                    visual_language: language,
                },
            )
            .unwrap()
            .project;
        p = service
            .apply(
                p.id,
                &p.stamp(),
                "scene",
                &Change::AddScene {
                    name: "Saved rectangle".into(),
                    objective: "Original source".into(),
                    duration_seconds: 1,
                },
            )
            .unwrap()
            .project;
        let node = CanvasNode {
            id: Uuid::now_v7(),
            name: "Original".into(),
            kind: "rect".into(),
            parent_id: None,
            x: 120.0,
            y: 120.0,
            width: 720.0,
            height: 360.0,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: None,
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: 0,
            style: NodeStyle {
                fill: Some("#E53935".into()),
                ..Default::default()
            },
            relations: vec![],
            property_locks: BTreeSet::new(),
            keyframes: vec![],
        };
        p = service
            .apply(
                p.id,
                &p.stamp(),
                "node",
                &Change::AddCanvasNode {
                    scene_id: p.scenes[0].id,
                    node,
                },
            )
            .unwrap()
            .project;
        let mut profile = p.deliverables[0].clone();
        profile.width = 64;
        profile.height = 36;
        profile.captions = false;
        profile.burn_in_captions = false;
        profile.frame_rate = RationalTime::new(30, 1).unwrap();
        p = service
            .apply(
                p.id,
                &p.stamp(),
                "profile",
                &Change::UpsertDeliverable { profile },
            )
            .unwrap()
            .project;
        (dir, service, p)
    }
    fn request(p: &Project, grants: &EffectGrantRegistry) -> RenderRequest {
        let mut request = RenderRequest {
            source: SourceRequest {
                project_id: p.id,
                generation: p.generation,
                revision: p.revision,
                active_branch: p.active_branch,
                deliverable_id: p.deliverables[0].id,
            },
            expected_fingerprint: compile_hyperframes_source(p, p.deliverables[0].id)
                .unwrap()
                .inventory()
                .fingerprint,
            runtime_lock_sha256: "1".repeat(64),
            request_id: format!("hf-ui-{}", Uuid::new_v4()),
            effect_grant: Uuid::nil(),
        };
        request.effect_grant = grants
            .issue(
                EffectKind::RenderLocal,
                EffectScope::project(p.id, p.generation, p.revision),
                &subject(&request),
            )
            .unwrap()
            .token;
        request
    }
    fn read(p: &Project, request: &RenderRequest) -> ReadRequest {
        ReadRequest {
            project_id: p.id,
            generation: p.generation,
            request_id: request.request_id.clone(),
        }
    }
    fn evidence(intent: &OriginalIntent, output: &Path) -> HyperframesSourceEvidence {
        let source = compile_hyperframes_source(&intent.project, intent.source.deliverable_id)
            .unwrap()
            .inventory();
        let job = format!(
            "hf-{}",
            sha(format!(
                "{}\n{}\n{}",
                intent.request_id, intent.fingerprint, intent.runtime_lock_sha256
            )
            .as_bytes())
        );
        let directory = format!("render-{job}");
        let folder = output.join(&directory).join("frames");
        fs::create_dir_all(&folder).unwrap();
        let png = ORIGINAL_PNG;
        let frames = (0..source.composition.frame_count).map(|index| {
            let file = format!("frames/frame_{:06}.png", index + 1);
            fs::write(output.join(&directory).join(&file), png).unwrap();
            json!({"index":index,"file":file,"bytes":png.len(),"sha256":sha(png),"width":64,"height":36})
        }).collect::<Vec<_>>();
        let result = json!({
            "state":"succeeded","job_ref":job,"directory":directory,"runtime_lock_sha256":intent.runtime_lock_sha256,
            "receipt":{"schema":"semwright.hyperframes.native-render/1","status":"completed","attempt_id":job,
                "fingerprint":source.fingerprint,"source_sha256":source.source_sha256,"html_sha256":source.html_sha256,
                "runtime_lock_sha256":intent.runtime_lock_sha256,"frame_count":30,"fps":30,"frames":frames,
                "source_unchanged":true,"automatic_retry":false,"pixel_decoding_performed":false,
                "native_command":{"exit_code":0,"signal":null,"capture_complete":true,"automatic_retry":false}}
        });
        HyperframesSourceEvidence {
            project_resource: intent.project.resource_key(),
            generation: intent.source.generation,
            revision: intent.source.revision,
            deliverable_id: intent.source.deliverable_id,
            source,
            runtime_lock_sha256: intent.runtime_lock_sha256.clone(),
            job_ref: job,
            result,
            output_kind: "native_png_sequence_intermediate".into(),
            human_acceptance: false,
        }
    }
    fn original_receipts(
        service: &StudioService,
        intent: &OriginalIntent,
        evidence: &HyperframesSourceEvidence,
        tamper: Option<&str>,
    ) {
        // Match ProductionCoordinator::execute exactly: connection/mutation
        // live in the dispatch intent, provenance in the completed SDK result.
        for (suffix, command, data) in [
            (
                "render-start",
                "driver.hyperframes.render.start",
                json!({"job_ref":evidence.job_ref,"state":"running"}),
            ),
            (
                "render-result",
                "driver.hyperframes.render.result",
                evidence.result.clone(),
            ),
        ] {
            let job_ref = if suffix == "render-start" {
                Value::Null
            } else {
                json!(evidence.job_ref)
            };
            let mut original = json!({"connection":intent.connection_identity,
                "mutation":suffix=="render-start","job_ref":job_ref});
            let mut payload = json!({"job_ref":job_ref, "broker_request_id":Uuid::new_v4(),
                "result":{"data":data,"execution":{"provenance":{"provider":"driver:hyperframes","provider_generation":3}}}});
            if suffix == "render-result" {
                match tamper {
                    Some("provider") => {
                        payload["result"]["execution"]["provenance"]["provider"] =
                            json!("driver:motion-canvas")
                    }
                    Some("generation") => {
                        payload["result"]["execution"]["provenance"]["provider_generation"] =
                            json!(4)
                    }
                    Some("connection") => original["connection"] = json!("3".repeat(64)),
                    Some("job") => {
                        payload["result"]["data"]["job_ref"] =
                            json!("hf-".to_string() + &"4".repeat(64))
                    }
                    Some("source") => {
                        payload["result"]["data"]["receipt"]["source_sha256"] =
                            json!("5".repeat(64))
                    }
                    _ => {}
                }
            }
            let input = ProductionReceiptInput {
                project_id: intent.project.id,
                generation: intent.source.generation,
                revision: intent.source.revision,
                request_id: format!("{}:{suffix}", intent.request_id),
                request_sha256: sha(suffix.as_bytes()),
                command: command.into(),
                stage: "dispatching".into(),
                payload: original,
            };
            service.append_production_receipt(input.clone()).unwrap();
            service
                .append_production_receipt(ProductionReceiptInput {
                    stage: "completed".into(),
                    payload,
                    ..input
                })
                .unwrap();
        }
    }
    fn frame_request(p: &Project, token: Uuid, index: u64) -> NativeFrameRequest {
        NativeFrameRequest {
            project_id: p.id,
            generation: p.generation,
            revision: p.revision,
            token,
            frame_index: index,
        }
    }

    #[test]
    fn durable_intent_and_live_lease_prevent_duplicate_dispatch() {
        let (_dir, service, p) = fixture();
        let grants = EffectGrantRegistry::default();
        let r = request(&p, &grants);
        let registry = RenderRegistry::default();
        let lease = registry.acquire(p.id).unwrap();
        assert!(registry.clone().acquire(p.id).is_err());
        let intent = begin(&service, &grants, &r, &"2".repeat(64)).unwrap();
        assert_eq!(service.production_receipts(p.id, 256).unwrap().len(), 1);
        let receipt = service
            .latest_production_receipt(p.id, &r.request_id)
            .unwrap()
            .unwrap();
        assert_eq!(receipt.stage, "dispatching");
        assert_eq!(receipt.payload["intent"]["fingerprint"], intent.fingerprint);
        assert!(begin(&service, &grants, &r, &"2".repeat(64)).is_err());
        assert_eq!(service.project(p.id).unwrap(), p);
        drop(lease);
        assert!(registry.acquire(p.id).is_ok());
    }

    #[test]
    fn altered_source_runtime_and_grant_reject_before_native_intent() {
        for changed in [
            "runtime",
            "fingerprint",
            "branch",
            "revision",
            "grant",
            "request_id",
        ] {
            let (_dir, service, p) = fixture();
            let grants = EffectGrantRegistry::default();
            let mut r = request(&p, &grants);
            match changed {
                "runtime" => r.runtime_lock_sha256 = "3".repeat(64),
                "fingerprint" => r.expected_fingerprint = "3".repeat(64),
                "branch" => r.source.active_branch = Uuid::new_v4(),
                "revision" => r.source.revision += 1,
                "grant" => r.effect_grant = Uuid::new_v4(),
                "request_id" => r.request_id = "hf-ui-not-an-original".into(),
                _ => unreachable!(),
            }
            assert!(
                begin(&service, &grants, &r, &"2".repeat(64)).is_err(),
                "{changed}"
            );
            assert!(service.production_receipts(p.id, 256).unwrap().is_empty());
            assert_eq!(service.project(p.id).unwrap(), p);
        }
    }

    #[test]
    fn completed_original_reopens_sqlite_and_verifies_each_png_without_native_execution() {
        let (dir, service, p) = fixture();
        let grants = EffectGrantRegistry::default();
        let r = request(&p, &grants);
        let intent = begin(&service, &grants, &r, &"2".repeat(64)).unwrap();
        let original = evidence(&intent, dir.path());
        original_receipts(&service, &intent, &original, None);
        finish(&service, &intent, &Ok(original)).unwrap();
        drop(service);
        let reopened = StudioService::open(dir.path().join("original.sqlite3")).unwrap();
        let recovery = read_attempt(&reopened, &read(&p, &r)).unwrap();
        assert_eq!(recovery.view.state, "completed");
        assert!(recovery.view.source_current);
        assert_eq!(recovery.original_project.as_ref(), Some(&p));
        assert_eq!(
            recovery.connection_identity.as_deref(),
            Some(intent.connection_identity.as_str())
        );
        let registry = NativePreviewRegistry::default();
        let grant = registry
            .register_hyperframes(
                dir.path(),
                &p.resource_key(),
                recovery.original_project.as_ref().unwrap(),
                &recovery.evidence.unwrap(),
            )
            .unwrap();
        for index in [0, 29] {
            assert_eq!(
                registry
                    .read_frame(&p, &frame_request(&p, grant.token, index))
                    .unwrap(),
                ORIGINAL_PNG
            );
        }
        let watermark = reopened.production_receipt_watermark(p.id).unwrap();
        let jobs = reopened.production_jobs_snapshot(p.id, &watermark).unwrap();
        assert_eq!(jobs.len(), 1);
        assert_eq!(jobs[0].provider, "driver:hyperframes");
        assert_eq!(
            serde_json::to_value(jobs[0].state).unwrap(),
            json!("succeeded")
        );
        assert!(jobs[0].result_available);
        assert_eq!(reopened.project(p.id).unwrap(), p);
    }

    #[test]
    fn lost_desktop_ack_recovers_original_result_without_appending_or_dispatching() {
        let (dir, service, p) = fixture();
        let grants = EffectGrantRegistry::default();
        let r = request(&p, &grants);
        let intent = begin(&service, &grants, &r, &"2".repeat(64)).unwrap();
        let original = evidence(&intent, dir.path());
        original_receipts(&service, &intent, &original, None);
        let before = service.production_receipt_watermark(p.id).unwrap();
        let recovery = read_attempt(&service, &read(&p, &r)).unwrap();
        assert_eq!(recovery.view.state, "completed");
        assert_eq!(service.production_receipt_watermark(p.id).unwrap(), before);
        assert_eq!(
            service
                .latest_production_receipt(p.id, &r.request_id)
                .unwrap()
                .unwrap()
                .stage,
            "dispatching"
        );
        assert!(!recovery.view.automatic_retry);
    }

    #[test]
    fn foreign_provider_connection_job_or_source_never_recovers_a_success() {
        for changed in ["provider", "generation", "connection", "job", "source"] {
            let (dir, service, p) = fixture();
            let grants = EffectGrantRegistry::default();
            let r = request(&p, &grants);
            let intent = begin(&service, &grants, &r, &"2".repeat(64)).unwrap();
            let original = evidence(&intent, dir.path());
            original_receipts(&service, &intent, &original, Some(changed));
            assert!(read_attempt(&service, &read(&p, &r)).is_err(), "{changed}");
        }
    }

    #[test]
    fn unknown_original_blocks_new_request_ids_after_reopen_and_keeps_project_editable() {
        let (dir, service, p) = fixture();
        let grants = EffectGrantRegistry::default();
        let r = request(&p, &grants);
        begin(&service, &grants, &r, &"2".repeat(64)).unwrap();
        drop(service);
        let service = StudioService::open(dir.path().join("original.sqlite3")).unwrap();
        let recovery = read_attempt(&service, &read(&p, &r)).unwrap();
        assert_eq!(recovery.view.state, "outcome_unknown");
        assert!(recovery.evidence.is_none());
        let mut profile = p.deliverables[0].clone();
        profile.name = "Edited while native outcome remains unknown".into();
        let next = service
            .apply(
                p.id,
                &p.stamp(),
                "independent-edit",
                &Change::UpsertDeliverable { profile },
            )
            .unwrap()
            .project;
        let fresh = request(&next, &grants);
        assert!(begin(&service, &grants, &fresh, &"2".repeat(64)).is_err());
        assert_eq!(service.project(p.id).unwrap(), next);
    }

    #[test]
    fn png_reads_reject_drift_and_cannot_supply_motion_canvas_master_authority() {
        let (dir, service, p) = fixture();
        let grants = EffectGrantRegistry::default();
        let r = request(&p, &grants);
        let intent = begin(&service, &grants, &r, &"2".repeat(64)).unwrap();
        let original = evidence(&intent, dir.path());
        let registry = NativePreviewRegistry::default();
        let grant = registry
            .register_hyperframes(dir.path(), &p.resource_key(), &p, &original)
            .unwrap();
        assert!(
            registry
                .master_source(&p, grant.token, p.deliverables[0].id)
                .is_err()
        );
        assert!(
            registry
                .read_frame(&p, &frame_request(&p, grant.token, 30))
                .is_err()
        );
        let path = dir.path().join(format!(
            "render-{}/frames/frame_000001.png",
            original.job_ref
        ));
        fs::write(&path, b"replaced original").unwrap();
        assert!(
            registry
                .read_frame(&p, &frame_request(&p, grant.token, 0))
                .is_err()
        );
        assert!(
            registry
                .register_hyperframes(dir.path(), &p.resource_key(), &p, &original)
                .is_err()
        );
        fs::write(path, ORIGINAL_PNG).unwrap();
        let mut wrong = original.clone();
        wrong.result["receipt"]["frames"][0]["file"] = json!("../outside.png");
        assert!(
            registry
                .register_hyperframes(dir.path(), &p.resource_key(), &p, &wrong)
                .is_err()
        );
        wrong = original.clone();
        wrong.result["receipt"]["frames"][0]["width"] = json!(1920);
        assert!(
            registry
                .register_hyperframes(dir.path(), &p.resource_key(), &p, &wrong)
                .is_err()
        );
        assert!(
            registry
                .register_hyperframes(dir.path(), "project:foreign", &p, &original)
                .is_err()
        );
    }

    #[test]
    fn historical_results_remain_original_and_never_become_current_preview() {
        let (dir, service, p) = fixture();
        let grants = EffectGrantRegistry::default();
        let r = request(&p, &grants);
        let intent = begin(&service, &grants, &r, &"2".repeat(64)).unwrap();
        let original = evidence(&intent, dir.path());
        original_receipts(&service, &intent, &original, None);
        finish(&service, &intent, &Ok(original.clone())).unwrap();
        let registry = NativePreviewRegistry::default();
        let grant = registry
            .register_hyperframes(dir.path(), &p.resource_key(), &p, &original)
            .unwrap();
        let mut profile = p.deliverables[0].clone();
        profile.name = "Another revision".into();
        let next = service
            .apply(
                p.id,
                &p.stamp(),
                "next-revision",
                &Change::UpsertDeliverable { profile },
            )
            .unwrap()
            .project;
        let recovery = read_attempt(&service, &read(&next, &r)).unwrap();
        assert_eq!(recovery.view.state, "completed");
        assert!(!recovery.view.source_current);
        assert_eq!(recovery.original_project.as_ref(), Some(&p));
        assert_eq!(
            recovery.connection_identity.as_deref(),
            Some(intent.connection_identity.as_str())
        );
        assert!(
            registry
                .register_hyperframes(dir.path(), &p.resource_key(), &next, &original)
                .is_err()
        );
        assert!(
            registry
                .read_frame(&next, &frame_request(&p, grant.token, 0))
                .is_err()
        );
    }
    // Actual first-party PNG bytes; this is an image transport fixture, not a rendered fidelity claim.
    const ORIGINAL_PNG: &[u8] = &[
        137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82, 0, 0, 0, 64, 0, 0, 0, 36, 8,
        6, 0, 0, 0, 57, 12, 60, 146, 0, 0, 0, 100, 73, 68, 65, 84, 120, 156, 237, 208, 49, 17, 0,
        32, 16, 192, 176, 215, 128, 22, 180, 224, 223, 9, 200, 200, 64, 135, 236, 189, 206, 218,
        231, 254, 108, 116, 128, 214, 0, 29, 160, 53, 64, 7, 104, 13, 208, 1, 90, 3, 116, 128, 214,
        0, 29, 160, 53, 64, 7, 104, 13, 208, 1, 90, 3, 116, 128, 214, 0, 29, 160, 53, 64, 7, 104,
        13, 208, 1, 90, 3, 116, 128, 214, 0, 29, 160, 53, 64, 7, 104, 13, 208, 1, 90, 3, 116, 128,
        214, 0, 29, 160, 61, 110, 72, 115, 211, 239, 116, 204, 125, 0, 0, 0, 0, 73, 69, 78, 68,
        174, 66, 96, 130,
    ];
}
