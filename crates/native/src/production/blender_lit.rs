//! Source-bound native Blender still: real Cycles raster from an
//! owner-provisioned CAMERA+LIGHT in the pinned Semwright Blender host.
//! No generated light/camera, external Python, 3D rig editing, or approval.
use super::*;
use serde::{Deserialize, Serialize};

pub const BLENDER_LIT_STILL_SCHEMA: &str = "motionwright.blender-lit-still/1";
const MAX_LIT_PNG_BYTES: u64 = 16 * 1024 * 1024;
const PNG_SIGNATURE: [u8; 8] = [137, 80, 78, 71, 13, 10, 26, 10];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlenderLitStillProfile {
    pub width: u32,
    pub height: u32,
    pub samples: u32,
    /// A declaration only: cameras and lights must still be observed in the live host.
    pub owner_provisioned_camera_and_light: bool,
}
impl BlenderLitStillProfile {
    pub fn validate(&self) -> NativeResult<()> {
        if !(320..=1920).contains(&self.width)
            || !(180..=1080).contains(&self.height)
            || u64::from(self.width) * u64::from(self.height) > 2_073_600
            || !(4..=32).contains(&self.samples)
            || !self.owner_provisioned_camera_and_light
        {
            return Err(invalid(
                "Blender still requires owner-declared existing camera/light and a bounded 320–1920 by 180–1080 CPU Cycles profile",
            ));
        }
        Ok(())
    }
}

fn active_camera_and_lights(
    scene: &Value,
    object_list: &Value,
) -> NativeResult<(String, Vec<String>)> {
    let camera = scene
        .get("camera")
        .and_then(Value::as_str)
        .filter(|text| !text.trim().is_empty() && text.len() <= 128)
        .ok_or_else(|| invalid("Source Blender scene has no active owner-provisioned camera"))?;
    if object_list.get("truncated").and_then(Value::as_bool) != Some(false) {
        return Err(invalid(
            "Source Blender object discovery is missing, truncated or untrusted",
        ));
    }
    let objects = object_list
        .get("objects")
        .and_then(Value::as_array)
        .filter(|values| values.len() <= 2_000)
        .ok_or_else(|| invalid("Source Blender object inspection is incomplete"))?;
    let camera_visible = objects.iter().any(|row| {
        row.get("name").and_then(Value::as_str) == Some(camera)
            && row.get("type").and_then(Value::as_str) == Some("CAMERA")
    });
    let lights: Vec<_> = objects
        .iter()
        .filter(|row| row.get("type").and_then(Value::as_str) == Some("LIGHT"))
        .filter_map(|row| row.get("name").and_then(Value::as_str))
        .filter(|name| !name.trim().is_empty() && name.len() <= 128)
        .map(str::to_owned)
        .collect();
    if !camera_visible || lights.is_empty() || lights.len() > 64 {
        return Err(invalid(
            "Source Blender still needs an observed active CAMERA and at least one provisioned LIGHT",
        ));
    }
    Ok((camera.to_owned(), lights))
}
fn verified_lit_png(
    root: &Path,
    relative: &str,
    width: u32,
    height: u32,
) -> NativeResult<(String, PathBuf, u64)> {
    let path = output_artifact_path(root, relative, MAX_LIT_PNG_BYTES)?;
    let bytes = fs::read(&path).map_err(|_| backend("Blender render output is unreadable"))?;
    if bytes.len() < 33
        || bytes[..8] != PNG_SIGNATURE
        || &bytes[12..16] != b"IHDR"
        || u32::from_be_bytes(bytes[16..20].try_into().unwrap()) != width
        || u32::from_be_bytes(bytes[20..24].try_into().unwrap()) != height
        || bytes[24] != 8
        || ![2, 6].contains(&bytes[25])
    {
        return Err(backend(
            "Blender output is not a source-resolution RGB/RGBA 8-bit PNG",
        ));
    }
    let digest = hex::encode(Sha256::digest(&bytes));
    let verified = verify_output_artifact(root, relative, &digest, MAX_LIT_PNG_BYTES)?;
    Ok((digest, verified, bytes.len() as u64))
}
impl ProductionCoordinator {
    /// Inspect the original shared Blender session and render an actual lit
    /// PNG *after* a verified native Blender GLB contribution. No implicit
    /// geometry creation or camera/light edits are permitted here.
    pub async fn render_blender_lit_still(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        geometry_request_id: &str,
        scene_id: Uuid,
        profile: BlenderLitStillProfile,
    ) -> NativeResult<Value> {
        profile.validate()?;
        if request_id.len() < 8
            || request_id.len() > 60
            || !request_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
            || geometry_request_id.len() < 8
            || geometry_request_id.len() > 60
            || !geometry_request_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
        {
            return Err(invalid(
                "Lit still and original Blender geometry request IDs must be bounded alphanumeric tokens",
            ));
        }
        let project = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
            || self.client.connection().resource != project.resource_key()
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Owner-provisioned Blender render source or connection changed",
            ));
        }
        if !project.scenes.iter().any(|scene| {
            scene.id == scene_id && scene.renderer == motionwright_domain::RendererKind::Blender
        }) {
            return Err(invalid("Lit still requires a Blender-owned source scene"));
        }
        let receipt_key = format!("{geometry_request_id}:export");
        let prior = self
            .service
            .latest_production_receipt(project_id, &receipt_key)
            .map_err(storage_error)?
            .ok_or_else(|| {
                invalid("Render a verified Blender GLB contribution before requesting a lit still")
            })?;
        if prior.stage != "completed"
            || prior.command != "driver.blender.export.glb"
            || prior.generation != project.generation
            || prior.revision != project.revision
        {
            return Err(invalid(
                "Original source GLB receipt is stale or not completed",
            ));
        }
        let source = prior
            .payload
            .pointer("/result/data")
            .ok_or_else(|| backend("Original Blender geometry receipt has no driver result"))?;
        let relative = required_string(source, "/path", "Original Blender GLB path is absent")?;
        let sha = required_string(source, "/sha256", "Original Blender GLB SHA-256 is absent")?;
        let source_path = verify_output_artifact(
            &self.client.connection().output_root,
            &relative,
            &sha,
            BLENDER_GLTF_MAX_BYTES,
        )?;
        if source.get("format").and_then(Value::as_str) != Some("glb")
            || source.get("objects").and_then(Value::as_u64).unwrap_or(0) == 0
        {
            return Err(backend(
                "Original Blender geometry is not a verified export",
            ));
        }
        // Execute live reads through the SAME observed-resource Semwright
        // session; a prior GLB can never prove lighting by itself.
        let inspect = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:inspect"),
                "driver.blender.scene.inspect",
                json!({}),
                false,
            )
            .await?;
        let inventory = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:objects"),
                "driver.blender.object.list",
                json!({}),
                false,
            )
            .await?;
        let (camera, lights) =
            active_camera_and_lights(response_data(&inspect)?, response_data(&inventory)?)?;
        let render_settings = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:settings"),
                "driver.blender.render.settings",
                json!({"width":profile.width,"height":profile.height,
                   "samples":profile.samples,"engine":"CYCLES","device":"CPU"}),
                true,
            )
            .await?;
        let settings = response_data(&render_settings)?;
        if settings.get("changed").and_then(Value::as_bool) != Some(true)
            || settings.get("engine").and_then(Value::as_str) != Some("CYCLES")
        {
            return Err(backend(
                "Pinned Blender host did not establish real CPU Cycles settings",
            ));
        }
        let identity = serde_json::to_vec(&(
            project.id,
            project.generation,
            project.revision,
            scene_id,
            request_id,
            geometry_request_id,
            profile,
        ))
        .map_err(|_| invalid("Lit source request cannot be serialized"))?;
        let name = format!(
            "motionwright-lit-{}.png",
            &hex::encode(Sha256::digest(&identity))[..40]
        );
        if self.client.connection().output_root.join(&name).exists() {
            return Err(Error::new(
                ErrorCode::Conflict,
                "This exact lit PNG already exists; inspect the old receipt, never overwrite it",
            ));
        }
        let rendered = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:render"),
                "driver.blender.render",
                json!({"path":name}),
                true,
            )
            .await?;
        let result = response_data(&rendered)?;
        if result.get("changed").and_then(Value::as_bool) != Some(true)
            || result.get("format").and_then(Value::as_str) != Some("png")
            || result.get("path").and_then(Value::as_str) != Some(name.as_str())
        {
            return Err(backend(
                "Pinned Blender host did not confirm this exact PNG render",
            ));
        }
        let (png_sha256, verified_path, bytes) = verified_lit_png(
            &self.client.connection().output_root,
            &name,
            profile.width,
            profile.height,
        )?;
        if self
            .service
            .project(project_id)
            .map_err(storage_error)?
            .revision
            != project.revision
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Blender project changed while the lit native shot was rendered",
            ));
        }
        Ok(json!({
          "schema":BLENDER_LIT_STILL_SCHEMA,
          "renderer":"blender","native_driver":"driver:blender",
          "project_id":project.id,"generation":project.generation,
          "project_revision":project.revision,"scene_id":scene_id,
          "original_glb_sha256":sha,"original_glb_path":source_path,
          "owner_camera":camera,"owner_lights":lights,
          "owner_camera_light_declaration_unverified":true,
          "engine":"CYCLES","device":"CPU","width":profile.width,
          "height":profile.height,"samples":profile.samples,
          "artifact":{"path":name,"sha256":png_sha256,"bytes":bytes,
             "verified_path":verified_path},
          "native_png_generated":true,"camera_or_lights_modified":false,
          "source_rights_authenticated":false,"creative_quality_approved":false,
          "physical_video_rendered":false,"publication_approved":false
        }))
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn profile_and_owner_ambient_source_require_real_bounded_camera_and_light() {
        let ok = BlenderLitStillProfile {
            width: 640,
            height: 360,
            samples: 12,
            owner_provisioned_camera_and_light: true,
        };
        ok.validate().unwrap();
        for invalid_profile in [
            BlenderLitStillProfile { width: 16, ..ok },
            BlenderLitStillProfile { height: 160, ..ok },
            BlenderLitStillProfile { samples: 400, ..ok },
            BlenderLitStillProfile {
                owner_provisioned_camera_and_light: false,
                ..ok
            },
        ] {
            assert!(invalid_profile.validate().is_err());
        }
        let scene = json!({"camera":"Camera"});
        let inventory = json!({"truncated":false,"objects":[
            {"name":"Camera","type":"CAMERA"},
            {"name":"Owner Key Light","type":"LIGHT"},
        ]});
        let (cam, lights) = active_camera_and_lights(&scene, &inventory).unwrap();
        assert_eq!(cam, "Camera");
        assert_eq!(lights, vec!["Owner Key Light"]);
        assert!(active_camera_and_lights(&json!({"camera":null}), &inventory).is_err());
        assert!(active_camera_and_lights(&scene, &json!({"truncated":true,"objects":[]})).is_err());
        assert!(
            active_camera_and_lights(
                &scene,
                &json!({"truncated":false,
            "objects":[{"name":"Camera","type":"CAMERA"}]})
            )
            .is_err()
        );
        assert!(
            active_camera_and_lights(
                &scene,
                &json!({"truncated":false,
            "objects":[{"name":"Camera","type":"EMPTY"},{"name":"Light","type":"LIGHT"}]})
            )
            .is_err()
        );
    }
}
