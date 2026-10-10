//! Real-source fixture linking the existing Rust OTIO interchange contract
//! to externally verified owner media bytes. No renderer or execution grant.
use motionwright_domain::{Asset, Change, Project, otio_interchange};
use serde_json::{Value, json};
use uuid::Uuid;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let sha = std::env::args()
        .nth(1)
        .ok_or("Expected SHA-256 of original external MP4")?;
    if sha.len() != 64
        || !sha
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err("A canonical lower-hex SHA-256 is required".into());
    }
    let mut project = Project::new("Original source media editorial exchange")?;
    for (name, objective) in [
        (
            "Opening source",
            "Editorial entrance from authorized original video",
        ),
        (
            "Detail source",
            "Second source interval from the same video",
        ),
        (
            "Abstract branded CTA",
            "Native graphics with no inferred clip media",
        ),
    ] {
        project.apply_change(&Change::AddScene {
            name: name.into(),
            objective: objective.into(),
            duration_seconds: 1,
        })?;
    }
    let asset_id = Uuid::from_u128(0x00fe_ed51);
    project.assets.push(Asset {
        id: asset_id,
        name: "First-party original H.264 test video".into(),
        media_type: "video/mp4".into(),
        content_sha256: Some(sha.clone()),
        source_revision: Some("disposable-ci-original-asset-1".into()),
    });
    project.validate()?;
    let original = otio_interchange(&project)?;
    let parsed: Value = serde_json::from_str(&original.body)?;
    println!(
        "{}",
        json!({
            "schema":"motionwright.real-otio-fixture/1",
            "project":project,
            "original_otio":parsed,
            "owner_media_asset_id":asset_id,
            "expected_media_sha256":sha,
            "editorial_source_provenance":"REAL_FFMPEG_GENERATED_ORIGINAL",
            "creative_approval":"NOT_PERFORMED"
        })
    );
    Ok(())
}
