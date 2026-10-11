// Synthetic Broker envelopes exercise Motionwright's exact source admission.
// They are NOT live Semwright media, operator recovery, or a provider fix.

fn integrity_options(project: &motionwright_domain::Project) -> FilmBuildOptions {
    FilmBuildOptions {
        frame_rate: Rate::new(30, 1).unwrap(),
        font_family: "Instrument Sans Variable".into(),
        mono_font_family: "IBM Plex Mono".into(),
        scene_intents: vec![crate::film::SceneFilmIntent {
            scene_id: project.scenes[0].id,
            role: NarrativeRole::Mechanism,
            archetype: Archetype::Statement,
        }],
    }
}

#[test]
fn status_and_result_views_require_the_acknowledged_native_job_reference() {
    let expected = "private-acknowledged-job";
    let correct = json!({
        "job_ref": expected, "state": "succeeded",
        "artifact": { "frame_count": 60 }
    });
    assert!(require_acknowledged_motion_job(&correct, expected, "status").is_ok());
    assert!(require_acknowledged_motion_job(&correct, expected, "result").is_ok());
    for wrong in [
        json!({"job_ref": "private-foreign-job", "state": "succeeded", "artifact": {"frame_count":60}}),
        json!({"state": "succeeded", "artifact": {"frame_count":60}}),
        json!({"job_ref": null, "state": "succeeded", "artifact": {"frame_count":60}}),
        json!({"job_ref": expected, "state": "succeeded", "artifact": {"frame_count":60, "sha256":"padded"}}),
    ] {
        for observation in ["status", "result"] {
            // A matching job reference can carry unrelated validation failures,
            // but only mismatch/missing identity is judged by this function.
            let mismatch = wrong.get("job_ref").and_then(Value::as_str) != Some(expected);
            let verdict = require_acknowledged_motion_job(&wrong, expected, observation);
            assert_eq!(verdict.is_err(), mismatch);
            if let Err(error) = verdict {
                assert!(!error.outcome_known);
                assert!(error.message.contains("unconfirmed"));
                assert!(error.message.contains("do not redispatch"));
                assert!(!error.message.contains("private-"));
                assert!(!error.message.contains("padded"));
            }
        }
    }
}

#[cfg(unix)]
#[tokio::test]
async fn forged_status_or_result_must_never_become_native_render_evidence() {
    // The artificial fake CLI returns *valid-looking* statuses for the wrong
    // job, incomplete statuses and an unobservable result. A genuine
    // native/provenance-positive test is exercised elsewhere in this module.
    let cases = [
        "foreign_status",
        "missing_status",
        "foreign_result",
        "missing_result",
        "result_unavailable",
    ];
    for case in cases {
        let (service, initial, temp) = fixture_service();
        let project = motion_canvas_fixture(&service, initial);
        let mut connection = fake_render_connection(&temp, project.resource_key());
        let script = fs::read_to_string(&connection.executable).unwrap();
        let status_line = r#"data='{"job_ref":"job-native-1","state":"succeeded","artifact":{"directory":"render-native-1","frame_count":60}}'"#;
        let result_line = r#"data='{"job_ref":"job-native-1","state":"succeeded","artifact":{"directory":"render-native-1","frame_count":60,"manifest_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}'"#;

        let altered = match case {
            "foreign_status" => script.replace(
                status_line,
                &status_line.replacen("job-native-1", "private-foreign-job", 1),
            ),
            "missing_status" => script.replace(
                status_line,
                &status_line.replacen(r#""job_ref":"job-native-1","#, "", 1),
            ),
            "foreign_result" => script.replace(
                result_line,
                &result_line.replacen("job-native-1", "private-foreign-job", 1),
            ),
            "missing_result" => script.replace(
                result_line,
                &result_line.replacen(r#""job_ref":"job-native-1","#, "", 1),
            ),
            "result_unavailable" => script.replace(result_line, "exit 43"),
            _ => unreachable!(),
        };
        assert_ne!(
            script, altered,
            "fixture mutation must actually exercise {case}"
        );
        fs::write(&connection.executable, altered).unwrap();
        connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
        let coordinator = ProductionCoordinator::new(service, connection).unwrap();
        let stamp = RevisionStamp::from(&project);
        let options = integrity_options(&project);
        let run = || {
            coordinator.render_motion_canvas_segments(
                project.id,
                &stamp,
                "render-integrity-case",
                project.deliverables[0].id,
                &options,
            )
        };
        let failure = run().await.expect_err(case);
        assert!(
            !failure.outcome_known,
            "{case} must not be promoted to a definite successful/failed render"
        );
        assert!(
            failure.message.contains("do not redispatch"),
            "{case} must direct operator reconciliation, not new execution"
        );
        assert!(!failure.message.contains("private-foreign-job"));
        assert!(!failure.message.contains("job-native-1"));

        let receipts = coordinator.receipts(project.id, 100).unwrap();
        assert_eq!(
            receipts
                .iter()
                .filter(|r| r.command == "driver.motion-canvas.render.start"
                    && r.stage == "dispatching")
                .count(),
            1,
            "{case} may not start another render"
        );
        assert_eq!(
            receipts
                .iter()
                .filter(|r| r.command == "driver.motion-canvas.composition.verify")
                .count(),
            0,
            "{case} may not claim native verification"
        );
        // A second request with the exact same owner/request identity cannot
        // turn the earlier unknown status into a new renderer mutation.
        let second = run().await.expect_err("failed source must not become PASS");
        assert!(!second.outcome_known);
        let replay_receipts = coordinator.receipts(project.id, 100).unwrap();
        assert_eq!(
            replay_receipts
                .iter()
                .filter(|r| r.command == "driver.motion-canvas.render.start"
                    && r.stage == "dispatching")
                .count(),
            1,
            "{case} cannot redispatch a started render"
        );
    }
}
