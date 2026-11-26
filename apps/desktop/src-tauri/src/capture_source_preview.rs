//! Owner-only original source image read, shared with the real-Service fixture.
use motionwright_domain::RevisionStamp;
use motionwright_service::StudioService;
use serde::Deserialize;
use uuid::Uuid;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CapturePngRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub capture_id: Uuid,
    pub screenshot_sha256: String,
}
pub fn read(service: &StudioService, request: &CapturePngRequest) -> Result<Vec<u8>, String> {
    let expected = RevisionStamp {
        resource: format!("project:{}", request.project_id),
        generation: request.generation,
        revision: request.revision,
    };
    service
        .registered_capture_png(
            request.project_id,
            &expected,
            request.capture_id,
            &request.screenshot_sha256,
        )
        .map_err(|_| {
            "The original capture or its source binding is unavailable; reselect the saved source."
                .to_owned()
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn capture_read_selector_rejects_caller_paths_bytes_and_authority() {
        let value = serde_json::json!({"project_id": Uuid::now_v7(), "generation": Uuid::now_v7(),
            "revision": 0, "capture_id": Uuid::now_v7(), "screenshot_sha256": "ab".repeat(32)});
        assert!(serde_json::from_value::<CapturePngRequest>(value.clone()).is_ok());
        for field in ["path", "bytes", "authority", "effect_grant", "manifest"] {
            let mut wrong = value.clone();
            wrong[field] = serde_json::json!("caller-controlled");
            assert!(serde_json::from_value::<CapturePngRequest>(wrong).is_err());
        }
    }
}
