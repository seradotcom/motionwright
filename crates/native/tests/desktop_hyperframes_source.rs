//! Real Desktop module, SQLite, grants and no-clobber filesystem tests.
//! This does not launch Tauri, a provider, a renderer or an editor.
#[allow(dead_code)]
#[path = "../../../apps/desktop/src-tauri/src/effect_grants.rs"]
mod effect_grants;
#[path = "../../../apps/desktop/src-tauri/src/hyperframes_source.rs"]
mod hyperframes_source;

#[cfg(test)]
mod desktop_source_tests {
    use super::{effect_grants::*, hyperframes_source::*};
    use motionwright_domain::{
        CanvasNode, Change, CoordinateSpace, NodeStyle, Project, RationalTime, VisualToken,
    };
    use motionwright_native::hyperframes::compile_hyperframes_source;
    use motionwright_service::StudioService;
    use std::{collections::BTreeSet, fs, path::Path};
    use uuid::Uuid;

    fn fixture() -> (tempfile::TempDir, StudioService, Project) {
        let dir = tempfile::tempdir().unwrap();
        let service = StudioService::open(dir.path().join("original.sqlite3")).unwrap();
        let mut p = service.create_project("Saved HyperFrames source").unwrap();
        let mut language = p.visual_language.clone();
        language.palette.retain(|t| t.name != "background");
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
                    name: "Saved card".into(),
                    objective: "Preserved editable source".into(),
                    duration_seconds: 1,
                },
            )
            .unwrap()
            .project;
        let node = CanvasNode {
            id: Uuid::now_v7(),
            name: "Original rectangle".into(),
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
        profile.width = 640;
        profile.height = 360;
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
    fn binding(p: &Project) -> SourceRequest {
        SourceRequest {
            project_id: p.id,
            generation: p.generation,
            revision: p.revision,
            active_branch: p.active_branch,
            deliverable_id: p.deliverables[0].id,
        }
    }
    fn request(p: &Project, path: &Path, format: SourceFormat) -> ExportRequest {
        ExportRequest {
            source: binding(p),
            expected_fingerprint: compile_hyperframes_source(p, p.deliverables[0].id)
                .unwrap()
                .fingerprint,
            format,
            path: path.to_str().unwrap().into(),
            effect_grant: Uuid::nil(),
        }
    }
    fn authorize(grants: &EffectGrantRegistry, request: &mut ExportRequest) {
        let format = match request.format {
            SourceFormat::Html => "html",
            SourceFormat::Json => "json",
        };
        let subject = format!(
            "hyperframes-source|{}|{}|{}|{}|{}",
            request.source.active_branch,
            request.source.deliverable_id,
            request.expected_fingerprint,
            format,
            request.path.trim()
        );
        request.effect_grant = grants
            .issue(
                EffectKind::DeliverLocal,
                EffectScope::project(
                    request.source.project_id,
                    request.source.generation,
                    request.source.revision,
                ),
                &subject,
            )
            .unwrap()
            .token;
    }

    #[test]
    fn saved_source_exports_exact_native_files_and_reopens_original_sqlite() {
        let (dir, service, p) = fixture();
        let snapshot = inspect_source(&service, binding(&p)).unwrap();
        assert_eq!(snapshot.project, p);
        let compiled = compile_hyperframes_source(&p, p.deliverables[0].id).unwrap();
        assert_eq!(snapshot.fingerprint, compiled.fingerprint);
        let grants = EffectGrantRegistry::default();
        for (format, name, bytes) in [
            (SourceFormat::Html, "index.html", &compiled.html),
            (
                SourceFormat::Json,
                "semwright-hyperframes.json",
                &compiled.source,
            ),
        ] {
            let output = dir.path().join(name);
            let mut req = request(&p, &output, format);
            authorize(&grants, &mut req);
            let receipt = export_source(&service, &grants, req).unwrap();
            assert_eq!(fs::read(output).unwrap(), *bytes);
            assert_eq!(receipt.size_bytes, bytes.len().to_string());
            assert!(
                !receipt.project_modified
                    && !receipt.editor_launched
                    && !receipt.native_execution_started
            );
        }
        assert_eq!(service.project(p.id).unwrap(), p);
        assert!(service.production_receipts(p.id, 32).unwrap().is_empty());
        drop(service);
        let reopened = StudioService::open(dir.path().join("original.sqlite3")).unwrap();
        assert_eq!(reopened.project(p.id).unwrap(), p);
        assert_eq!(
            inspect_source(&reopened, binding(&p)).unwrap().fingerprint,
            snapshot.fingerprint
        );
        assert!(reopened.production_receipts(p.id, 32).unwrap().is_empty());
    }

    #[test]
    fn stale_revision_branch_generation_and_profile_never_write() {
        let (dir, service, p) = fixture();
        let grants = EffectGrantRegistry::default();
        let output = dir.path().join("index.html");
        for case in 0..5 {
            let mut req = request(&p, &output, SourceFormat::Html);
            authorize(&grants, &mut req);
            match case {
                0 => req.source.revision += 1,
                1 => req.source.generation = Uuid::now_v7(),
                2 => req.source.active_branch = Uuid::now_v7(),
                3 => req.source.deliverable_id = Uuid::now_v7(),
                _ => req.expected_fingerprint = "0".repeat(64),
            }
            assert!(export_source(&service, &grants, req).is_err());
            assert!(!output.exists());
        }
        assert_eq!(service.project(p.id).unwrap(), p);
    }

    #[test]
    fn missing_foreign_and_replayed_grants_cannot_publish_source() {
        let (dir, service, p) = fixture();
        let grants = EffectGrantRegistry::default();
        let output = dir.path().join("index.html");
        assert!(
            export_source(&service, &grants, request(&p, &output, SourceFormat::Html)).is_err()
        );
        let mut req = request(&p, &output, SourceFormat::Html);
        authorize(&grants, &mut req);
        let token = req.effect_grant;
        req.format = SourceFormat::Json;
        req.path = dir
            .path()
            .join("semwright-hyperframes.json")
            .to_str()
            .unwrap()
            .into();
        assert!(export_source(&service, &grants, req).is_err());
        let mut valid = request(&p, &output, SourceFormat::Html);
        authorize(&grants, &mut valid);
        let consumed = valid.effect_grant;
        export_source(&service, &grants, valid).unwrap();
        let mut replay = request(&p, &output, SourceFormat::Html);
        replay.effect_grant = consumed;
        assert!(export_source(&service, &grants, replay).is_err());
        assert_ne!(token, consumed);
        assert!(!dir.path().join("semwright-hyperframes.json").exists());
    }

    #[test]
    fn existing_file_is_preserved_and_failed_stage_is_removed() {
        let (dir, service, p) = fixture();
        let grants = EffectGrantRegistry::default();
        let output = dir.path().join("index.html");
        fs::write(&output, b"original foreign bytes").unwrap();
        let mut req = request(&p, &output, SourceFormat::Html);
        authorize(&grants, &mut req);
        assert!(
            export_source(&service, &grants, req)
                .unwrap_err()
                .contains("not overwritten")
        );
        assert_eq!(fs::read(output).unwrap(), b"original foreign bytes");
        assert!(fs::read_dir(dir.path()).unwrap().all(|entry| {
            !entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".motionwright-hyperframes-")
        }));
    }

    #[test]
    fn unsupported_saved_content_is_rejected_before_export() {
        let (dir, service, p) = fixture();
        let mut style = p.scenes[0].nodes[0].style.clone();
        style.blend_mode = motionwright_domain::BlendMode::Multiply;
        let changed = service
            .apply(
                p.id,
                &p.stamp(),
                "unrepresentable-style",
                &Change::UpdateCanvasStyle {
                    scene_id: p.scenes[0].id,
                    node_id: p.scenes[0].nodes[0].id,
                    style,
                },
            )
            .unwrap()
            .project;
        assert!(inspect_source(&service, binding(&changed)).is_err());
        let mut req = request(&p, &dir.path().join("index.html"), SourceFormat::Html);
        req.source = binding(&changed);
        let grants = EffectGrantRegistry::default();
        authorize(&grants, &mut req);
        assert!(export_source(&service, &grants, req).is_err());
        assert!(!dir.path().join("index.html").exists());
        assert_eq!(service.project(p.id).unwrap(), changed);
    }

    #[test]
    fn foreign_fields_and_unknown_formats_are_not_an_export_request() {
        let (_dir, _service, p) = fixture();
        let mut value = serde_json::to_value(binding(&p)).unwrap();
        value["launch_editor"] = serde_json::json!(true);
        assert!(serde_json::from_value::<SourceRequest>(value).is_err());
        assert!(serde_json::from_value::<SourceFormat>(serde_json::json!("script")).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn dangling_destination_symlink_is_never_followed() {
        let (dir, service, p) = fixture();
        let output = dir.path().join("index.html");
        let foreign = dir.path().join("outside.html");
        std::os::unix::fs::symlink(&foreign, &output).unwrap();
        let grants = EffectGrantRegistry::default();
        let mut req = request(&p, &output, SourceFormat::Html);
        authorize(&grants, &mut req);
        assert!(export_source(&service, &grants, req).is_err());
        assert!(!foreign.exists());
        assert!(
            fs::symlink_metadata(output)
                .unwrap()
                .file_type()
                .is_symlink()
        );
    }
}
