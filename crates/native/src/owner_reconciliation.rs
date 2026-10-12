//! v0.1 owner-initiated, read-only reconciliation of an *acknowledged* job.
//! Never dispatch render.start, retry a native job or treat status as media proof.
use motionwright_domain::{Project, RevisionStamp};
use motionwright_service::{
    ProductionJobApplicability, ProductionJobProjection, ProductionJobState,
};
use motionwright_storage::ProductionReceipt;
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult, Value};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorizedMotionStatusOrigin {
    pub job_ref: String,
    pub start_provider_generation: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct OwnerMotionStatusObservation {
    pub schema: String,
    pub job_ref: String,
    /// UNKNOWN never upgrades a local receipt to SUCCESS or FAILED.
    pub provider_state: String,
    pub diagnostic: String,
    pub provider_generation_matches_original_start: bool,
    pub source_authority_checked: bool,
    pub broker_observed: bool,
    pub read_only: bool,
    pub native_result_verified: bool,
    pub output_bytes_verified: bool,
    pub automatic_retry_permitted: bool,
}

fn deny(message: &'static str) -> Error {
    Error::new(ErrorCode::StaleReference, message)
}

/// Authorize this *read* from existing application-owned source receipts,
/// not from a user-supplied job name, screenshot or arbitrary driver file.
/// Full jobs enumeration must be against the same receipt watermark.
pub fn authorize_original_motion_status(
    project: &Project,
    expected: &RevisionStamp,
    requested_ref: &str,
    job: &ProductionJobProjection,
    acknowledged_start: &ProductionReceipt,
) -> NativeResult<AuthorizedMotionStatusOrigin> {
    if expected.resource != project.resource_key()
        || expected.generation != project.generation
        || expected.revision != project.revision
        || requested_ref.is_empty()
        || requested_ref.len() > 512
        || !requested_ref.bytes().all(|b| b.is_ascii_graphic())
    {
        return Err(deny("Owner native job observation is not source-bound"));
    }
    if job.job_ref != requested_ref
        || job.provider != "driver:motion-canvas"
        || job.generation != project.generation
        || job.revision != project.revision
        || job.applicability != ProductionJobApplicability::Current
        || !matches!(
            job.state,
            ProductionJobState::OutcomeUnknown | ProductionJobState::CancelRequested
        )
        || job.root_request_id != acknowledged_start.request_id
        || acknowledged_start.project_id != project.id
        || acknowledged_start.generation != project.generation
        || acknowledged_start.revision != project.revision
        || acknowledged_start.command != "driver.motion-canvas.render.start"
        || acknowledged_start.stage != "completed"
        || acknowledged_start
            .payload
            .pointer("/result/data/job_ref")
            .and_then(Value::as_str)
            != Some(requested_ref)
        || acknowledged_start
            .payload
            .pointer("/result/execution/provenance/provider")
            .and_then(Value::as_str)
            != Some("driver:motion-canvas")
    {
        return Err(deny(
            "The requested native job has no matching acknowledged original start",
        ));
    }
    let original_generation = acknowledged_start
        .payload
        .pointer("/result/execution/provenance/provider_generation")
        .and_then(Value::as_u64)
        .ok_or_else(|| deny("Original native render provider generation is not proven"))?;
    Ok(AuthorizedMotionStatusOrigin {
        job_ref: requested_ref.to_owned(),
        start_provider_generation: original_generation,
    })
}

fn response(
    origin: &AuthorizedMotionStatusOrigin,
    state: &'static str,
    diagnostic: &'static str,
    generation_matches: bool,
    broker_observed: bool,
) -> OwnerMotionStatusObservation {
    OwnerMotionStatusObservation {
        schema: "motionwright.v01-owner-native-status/1".into(),
        job_ref: origin.job_ref.clone(),
        provider_state: state.into(),
        diagnostic: diagnostic.into(),
        provider_generation_matches_original_start: generation_matches,
        source_authority_checked: true,
        broker_observed,
        read_only: true,
        native_result_verified: false,
        output_bytes_verified: false,
        automatic_retry_permitted: false,
    }
}

/// Classify a single read-only canonical Broker status response. No data
/// from an unknown generation or job_ref can promote an original job.
/// Messages are closed, constant labels; private provider text never enters UI.
pub fn classify_original_motion_status(
    origin: &AuthorizedMotionStatusOrigin,
    broker_response: Option<&Value>,
) -> OwnerMotionStatusObservation {
    let Some(reply) = broker_response else {
        return response(origin, "unknown", "broker_status_unavailable", false, false);
    };
    let observed_ref = reply.pointer("/data/job_ref").and_then(Value::as_str);
    let observed_provider = reply
        .pointer("/execution/provenance/provider")
        .and_then(Value::as_str);
    let observed_generation = reply
        .pointer("/execution/provenance/provider_generation")
        .and_then(Value::as_u64);
    if observed_ref != Some(origin.job_ref.as_str())
        || observed_provider != Some("driver:motion-canvas")
    {
        return response(
            origin,
            "unknown",
            "native_job_identity_unconfirmed",
            false,
            false,
        );
    }
    if observed_generation != Some(origin.start_provider_generation) {
        return response(
            origin,
            "unknown",
            "provider_generation_changed",
            false,
            false,
        );
    }
    let Some(state) = reply.pointer("/data/state").and_then(Value::as_str) else {
        return response(origin, "unknown", "native_state_missing", true, false);
    };
    if !matches!(
        state,
        "queued" | "starting" | "rendering" | "succeeded" | "failed" | "cancelled"
    ) {
        return response(origin, "unknown", "native_state_unrecognized", true, false);
    }
    // Even a native SUCCEEDED status is only a status observation, not
    // render.result + composition.verify + decoded-media proof.
    response(
        origin,
        match state {
            "queued" => "queued",
            "starting" => "starting",
            "rendering" => "rendering",
            "succeeded" => "succeeded_unverified",
            "failed" => "failed_unverified",
            "cancelled" => "cancelled_unverified",
            _ => unreachable!(),
        },
        "original_native_job_status_read_only",
        true,
        true,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_service::ProductionJobProgress;
    use motionwright_storage::ProductionReceipt;
    use serde_json::json;
    use uuid::Uuid;

    fn scenario() -> (Project, ProductionJobProjection, ProductionReceipt) {
        let project = Project::new("Original owner native render").unwrap();
        let job_ref = "original-job:one".to_string();
        let start = ProductionReceipt {
            id: Uuid::now_v7(),
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: "owner-first-original-start".into(),
            request_sha256: "a".repeat(64),
            command: "driver.motion-canvas.render.start".into(),
            stage: "completed".into(),
            payload: json!({"result": {
                "data":{"job_ref": job_ref},
                "execution":{"provenance":{
                    "provider":"driver:motion-canvas","provider_generation":7
                }}
            }}),
            created_at: "2026-10-12T07:00:00Z".into(),
        };
        let job = ProductionJobProjection {
            job_ref,
            provider: "driver:motion-canvas".into(),
            root_request_id: start.request_id.clone(),
            generation: project.generation,
            revision: project.revision,
            state: ProductionJobState::OutcomeUnknown,
            cancellation_requested: false,
            applicability: ProductionJobApplicability::Current,
            last_command: "driver.motion-canvas.render.status".into(),
            created_at: start.created_at.clone(),
            last_observed_at: start.created_at.clone(),
            local_observations: 2,
            provider_generation: Some(7),
            progress: None::<ProductionJobProgress>,
            artifact_available: false,
            result_available: false,
            last_observation: motionwright_service::ProductionObservationState::OutcomeUnknown,
        };
        (project, job, start)
    }

    #[test]
    fn exact_acknowledged_start_authorizes_only_one_read() {
        let (project, job, start) = scenario();
        let origin = authorize_original_motion_status(
            &project,
            &RevisionStamp::from(&project),
            &job.job_ref,
            &job,
            &start,
        )
        .unwrap();
        assert_eq!(origin.start_provider_generation, 7);
    }

    #[test]
    fn foreign_job_generation_and_unacknowledged_or_unscoped_start_rejected() {
        let (project, job, start) = scenario();
        let mut bad = job.clone();
        bad.job_ref = "foreign-job".into();
        assert!(
            authorize_original_motion_status(
                &project,
                &RevisionStamp::from(&project),
                &bad.job_ref,
                &bad,
                &start
            )
            .is_err()
        );
        bad = job.clone();
        bad.generation = Uuid::new_v4();
        assert!(
            authorize_original_motion_status(
                &project,
                &RevisionStamp::from(&project),
                &bad.job_ref,
                &bad,
                &start
            )
            .is_err()
        );
        let mut bad_start = start.clone();
        bad_start.stage = "dispatching".into();
        assert!(
            authorize_original_motion_status(
                &project,
                &RevisionStamp::from(&project),
                &job.job_ref,
                &job,
                &bad_start
            )
            .is_err()
        );
        bad_start = start.clone();
        bad_start.payload = json!({"result":{"data":{"job_ref":"foreign"}}});
        assert!(
            authorize_original_motion_status(
                &project,
                &RevisionStamp::from(&project),
                &job.job_ref,
                &job,
                &bad_start
            )
            .is_err()
        );
    }

    #[test]
    fn current_successful_or_stale_jobs_cannot_be_used_as_unknown_recovery() {
        let (project, mut job, start) = scenario();
        job.state = ProductionJobState::Succeeded;
        assert!(
            authorize_original_motion_status(
                &project,
                &RevisionStamp::from(&project),
                &job.job_ref,
                &job,
                &start
            )
            .is_err()
        );
        job.state = ProductionJobState::CancelRequested;
        job.applicability = ProductionJobApplicability::Stale;
        assert!(
            authorize_original_motion_status(
                &project,
                &RevisionStamp::from(&project),
                &job.job_ref,
                &job,
                &start
            )
            .is_err()
        );
    }

    #[test]
    fn successful_status_is_never_media_result_verification() {
        let (project, job, start) = scenario();
        let origin = authorize_original_motion_status(
            &project,
            &RevisionStamp::from(&project),
            &job.job_ref,
            &job,
            &start,
        )
        .unwrap();
        let reply = json!({"data":{"job_ref":origin.job_ref,"state":"succeeded","artifact":{"frame_count":60}},
            "execution":{"provenance":{"provider":"driver:motion-canvas","provider_generation":7}}});
        let observed = classify_original_motion_status(&origin, Some(&reply));
        assert_eq!(observed.provider_state, "succeeded_unverified");
        assert!(observed.broker_observed);
        assert!(!observed.native_result_verified);
        assert!(!observed.output_bytes_verified);
        assert!(!observed.automatic_retry_permitted);
    }

    #[test]
    fn provider_generation_change_foreign_result_and_timeout_fail_closed() {
        let (project, job, start) = scenario();
        let origin = authorize_original_motion_status(
            &project,
            &RevisionStamp::from(&project),
            &job.job_ref,
            &job,
            &start,
        )
        .unwrap();
        for bad in [
            json!({"data":{"job_ref":job.job_ref,"state":"succeeded"},
                "execution":{"provenance":{"provider":"driver:motion-canvas","provider_generation":8}}}),
            json!({"data":{"job_ref":"another-job","state":"succeeded"},
                "execution":{"provenance":{"provider":"driver:motion-canvas","provider_generation":7}}}),
            json!({"data":{"job_ref":job.job_ref,"state":"arbitrary_terminal"},
                "execution":{"provenance":{"provider":"driver:motion-canvas","provider_generation":7}}}),
        ] {
            let observed = classify_original_motion_status(&origin, Some(&bad));
            assert_eq!(observed.provider_state, "unknown");
            assert!(!observed.native_result_verified);
            assert!(!observed.broker_observed);
        }
        let unavailable = classify_original_motion_status(&origin, None);
        assert_eq!(unavailable.provider_state, "unknown");
        assert!(!unavailable.broker_observed);
    }
}
