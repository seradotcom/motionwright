//! CAS-to-managed-font handoff through the existing authorized public driver.
//! No renderer, browser loader, install command or second font registry lives here.
use super::*;
use motionwright_service::VerifiedBrandFont;
use semwright_motion_authoring::Film;
use std::{collections::BTreeSet, fs::OpenOptions, io::Write};

fn verify_staged_font(path: &Path, font: &VerifiedBrandFont) -> NativeResult<()> {
    let expected_len = font.bytes.len() as u64;
    let metadata = safe_metadata(path)?;
    if !metadata.is_file() || metadata.len() != expected_len {
        return Err(backend(
            "Staged brand font is not the expected bounded regular file",
        ));
    }
    let mut file =
        fs::File::open(path).map_err(|_| backend("Staged brand font could not be opened"))?;
    let opened = file
        .metadata()
        .map_err(|_| backend("Staged brand font metadata could not be read"))?;
    if !opened.is_file() || opened.len() != expected_len {
        return Err(backend("Staged brand font changed before its bounded read"));
    }
    let mut bytes = Vec::with_capacity(font.bytes.len());
    Read::by_ref(&mut file)
        .take(expected_len + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| backend("Staged brand font could not be read"))?;
    let after = file
        .metadata()
        .map_err(|_| backend("Staged brand font metadata could not be rechecked"))?;
    let current = safe_metadata(path)?;
    if !after.is_file()
        || after.len() != expected_len
        || !current.is_file()
        || current.len() != expected_len
        || bytes != font.bytes
        || hex::encode(Sha256::digest(&bytes)) != font.source.sha256
    {
        return Err(backend(
            "Staged brand font differs; no overwrite is allowed",
        ));
    }
    Ok(())
}

fn stage_font(root: &Path, font: &VerifiedBrandFont) -> NativeResult<String> {
    if !safe_metadata(root)?.is_dir() {
        return Err(invalid(
            "Brand font media grant must identify a plain owner directory",
        ));
    }
    let extension = match font.source.media_type.as_str() {
        "font/ttf" => "ttf",
        "font/otf" => "otf",
        _ => {
            return Err(invalid(
                "Brand font media type is outside the admitted profile",
            ));
        }
    };
    if font.bytes.is_empty()
        || font.bytes.len() as u64 > motionwright_service::MAX_BRAND_FONT_BYTES
        || hex::encode(Sha256::digest(&font.bytes)) != font.source.sha256
    {
        return Err(backend(
            "Brand font CAS bytes do not match the selected source",
        ));
    }
    let relative = format!("mw-font-{}.{}", font.source.sha256, extension);
    let path = root.join(&relative);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    match options.open(&path) {
        Ok(mut file) => {
            // A failed write keeps its original partial file for reconciliation.
            file.write_all(&font.bytes)
                .map_err(|_| backend("Brand font handoff write failed"))?;
            file.sync_all()
                .map_err(|_| backend("Brand font handoff sync failed"))?;
            #[cfg(unix)]
            fs::File::open(root)
                .and_then(|directory| directory.sync_all())
                .map_err(|_| backend("Brand font handoff directory sync failed"))?;
        }
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            // Read-only content comparison, never another native import.
        }
        Err(_) => {
            return Err(backend(
                "Brand font handoff could not be created exclusively",
            ));
        }
    }
    // This is bounded content verification. The provider must still resolve the
    // owner media grant and verify expected_sha256 from its own actual snapshot.
    verify_staged_font(&path, font)?;
    Ok(relative)
}

fn matching_import(list: &Value, id: &str, digest: &str, bytes: u64) -> NativeResult<bool> {
    let items = list
        .get("items")
        .and_then(Value::as_array)
        .filter(|items| items.len() <= 128)
        .ok_or_else(|| backend("Managed font asset list is malformed"))?;
    let found = items
        .iter()
        .filter(|item| item.get("id").and_then(Value::as_str) == Some(id))
        .collect::<Vec<_>>();
    if found.is_empty() {
        return Ok(false);
    }
    if found.len() != 1
        || found[0].get("kind").and_then(Value::as_str) != Some("font")
        || found[0].get("sha256").and_then(Value::as_str) != Some(digest)
        || found[0].get("bytes").and_then(Value::as_u64) != Some(bytes)
    {
        return Err(backend(
            "Managed font identity has different content; explicit reconciliation is required",
        ));
    }
    Ok(true)
}

fn empty_managed_project(film: &Film, generation: Uuid) -> Value {
    // Exact existing Project v1 create schema; an empty owner project admits
    // assets before the server plans the first Film. No provisional text render.
    json!({
        "schema_version": 1, "component_version": 1,
        "id": film.id, "generation": generation.simple().to_string(), "revision": 1,
        "settings": {
            "width": film.output.width, "height": film.output.height,
            "fps": film.output.frame_rate.num, "fps_denominator": film.output.frame_rate.den,
            "background": film.editorial.colors.get("background"), "color_space": "srgb"
        },
        "theme": {
            "font_family": crate::film::MOTION_CANVAS_FONT_FAMILY,
            "mono_family": crate::film::MOTION_CANVAS_MONO_FONT_FAMILY,
            "font_size": 36.0, "font_weight": 400, "spacing": 16.0,
            "line_width": 2.0, "radius": 0.0,
            "colors": {"ink":"#202020","surface":"#FFFFFF"}
        },
        "variables": {}, "scenes": [], "assets": [], "audio": []
    })
}

impl ProductionCoordinator {
    /// Backend-only owner configuration. The same directory must already be the
    /// Broker/provider's read-only media grant. This setter grants no authority,
    /// adds no WebView field and does not write files or invoke the provider.
    pub fn with_brand_font_media_root(mut self, root: PathBuf) -> NativeResult<Self> {
        if !safe_metadata(&root)?.is_dir() {
            return Err(invalid(
                "Brand font handoff requires an existing owner media grant",
            ));
        }
        self.brand_font_media_root = Some(root);
        Ok(self)
    }

    pub(super) async fn prepare_motion_fonts(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        prefix: &str,
        film: &Film,
    ) -> NativeResult<()> {
        let selected = [&film.editorial.font, &film.editorial.mono_font]
            .into_iter()
            .filter(|spec| spec.asset_digest.is_some())
            .collect::<Vec<_>>();
        if selected.is_empty() {
            return Ok(());
        }
        let root = self.brand_font_media_root.as_ref().ok_or_else(|| {
            Error::new(
                ErrorCode::Unsupported,
                "Selected brand fonts require the owner-granted media handoff root",
            )
        })?;
        let mut resolved = Vec::new();
        let mut seen = BTreeSet::new();
        // Resolve and compare every selection before staging or native mutation.
        for spec in selected {
            let font = self
                .service
                .verified_brand_font(project_id, expected, &spec.family)
                .map_err(storage_error)?;
            if spec.asset_digest.as_ref().map(|digest| digest.as_str())
                != Some(font.source.sha256.as_str())
            {
                return Err(backend("Brand font source changed since Film projection"));
            }
            if seen.insert(spec.family.clone()) {
                resolved.push(font);
            }
        }
        let staged = resolved
            .into_iter()
            .map(|font| {
                let relative = stage_font(root, &font)?;
                Ok((font, relative))
            })
            .collect::<NativeResult<Vec<_>>>()?;
        let inspected = self
            .execute(
                project_id,
                expected,
                &format!("{prefix}:font-project-inspect"),
                "driver.motion-canvas.composition.inspect",
                json!({}),
                false,
            )
            .await?;
        let inspected = response_data(&inspected)?;
        match inspected.get("fingerprint") {
            Some(Value::Null) => {
                let created = self.execute(
                    project_id, expected, &format!("{prefix}:font-project-create"),
                    "driver.motion-canvas.project.create",
                    json!({"project":empty_managed_project(film, expected.generation), "dry_run":false}), true,
                ).await?;
                if response_data(&created)?
                    .get("applied")
                    .and_then(Value::as_bool)
                    != Some(true)
                {
                    return Err(backend(
                        "Managed font project creation was not acknowledged",
                    ));
                }
            }
            Some(Value::String(value)) if is_sha256(value) => {}
            _ => {
                return Err(backend(
                    "Managed font project inspection has no source identity",
                ));
            }
        }
        for (index, (font, relative)) in staged.into_iter().enumerate() {
            let id = format!("asset-{}", font.source.asset_id.simple());
            let listed = self
                .execute(
                    project_id,
                    expected,
                    &format!("{prefix}:font-list:{index}"),
                    "driver.motion-canvas.asset.list",
                    json!({}),
                    false,
                )
                .await?;
            let list = response_data(&listed)?;
            if matching_import(list, &id, &font.source.sha256, font.bytes.len() as u64)? {
                continue;
            }
            let fingerprint = required_string(
                list,
                "/fingerprint",
                "Managed font asset list has no source fingerprint",
            )?;
            if !is_sha256(&fingerprint) {
                return Err(backend("Managed font source fingerprint is malformed"));
            }
            // The provider checks expected_sha256 before install/commit. A
            // different mount or changed file cannot be silently admitted.
            let imported = self
                .execute(
                    project_id,
                    expected,
                    &format!("{prefix}:font-import:{index}"),
                    "driver.motion-canvas.asset.import",
                    json!({
                        "expected_fingerprint": fingerprint, "id": id, "kind":"font",
                        "source": relative, "expected_sha256":font.source.sha256,
                        "provenance":font.source.source_revision, "license":null, "dry_run":false,
                    }),
                    true,
                )
                .await?;
            if response_data(&imported)?
                .get("applied")
                .and_then(Value::as_bool)
                != Some(true)
            {
                return Err(backend("Managed font import was not acknowledged"));
            }
            let observed = self
                .execute(
                    project_id,
                    expected,
                    &format!("{prefix}:font-readback:{index}"),
                    "driver.motion-canvas.asset.list",
                    json!({}),
                    false,
                )
                .await?;
            if !matching_import(
                response_data(&observed)?,
                &id,
                &font.source.sha256,
                font.bytes.len() as u64,
            )? {
                return Err(backend(
                    "Managed font import is absent from independent source readback",
                ));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_service::BrandFontSource;

    fn font() -> VerifiedBrandFont {
        let bytes = b"CAS-only test; not actual font evidence".to_vec();
        VerifiedBrandFont {
            source: BrandFontSource {
                family: "Brand Family".into(),
                asset_id: Uuid::new_v4(),
                sha256: hex::encode(Sha256::digest(&bytes)),
                media_type: "font/ttf".into(),
                source_revision: None,
            },
            bytes,
        }
    }

    #[test]
    fn brand_font_handoff_is_exclusive_and_never_overwrites_different_bytes() {
        let temp = tempfile::tempdir().unwrap();
        let font = font();
        let relative = stage_font(temp.path(), &font).unwrap();
        assert_eq!(fs::read(temp.path().join(&relative)).unwrap(), font.bytes);
        assert_eq!(stage_font(temp.path(), &font).unwrap(), relative);
        fs::write(temp.path().join(&relative), b"different").unwrap();
        assert!(stage_font(temp.path(), &font).is_err());
        assert_eq!(fs::read(temp.path().join(relative)).unwrap(), b"different");
    }

    #[test]
    fn brand_font_handoff_rejects_wrong_cas_digest() {
        let temp = tempfile::tempdir().unwrap();
        let mut font = font();
        font.source.sha256 = "b".repeat(64);
        assert!(stage_font(temp.path(), &font).is_err());
        assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 0);
    }

    #[test]
    fn brand_font_import_readback_requires_exact_kind_hash_length_and_identity() {
        let id = "asset-test";
        let digest = "a".repeat(64);
        let good = json!({"items":[{"id":id,"kind":"font","sha256":digest,"bytes":42}]});
        assert!(matching_import(&good, id, &digest, 42).unwrap());
        assert!(!matching_import(&json!({"items":[]}), id, &digest, 42).unwrap());
        for field in ["kind", "sha256", "bytes"] {
            let mut wrong = good.clone();
            wrong["items"][0][field] = Value::Null;
            assert!(matching_import(&wrong, id, &digest, 42).is_err());
        }
        assert!(
            matching_import(
                &json!({"items":[good["items"][0],good["items"][0]]}),
                id,
                &digest,
                42
            )
            .is_err()
        );
    }

    #[cfg(unix)]
    #[test]
    fn brand_font_handoff_rejects_symlink_substitution() {
        use std::os::unix::fs::symlink;
        let temp = tempfile::tempdir().unwrap();
        let font = font();
        let relative = format!("mw-font-{}.ttf", font.source.sha256);
        let other = tempfile::tempdir().unwrap();
        let target = other.path().join("font.ttf");
        fs::write(&target, &font.bytes).unwrap();
        symlink(target, temp.path().join(relative)).unwrap();
        assert!(stage_font(temp.path(), &font).is_err());
    }
    #[test]
    fn brand_font_seed_request_digest_survives_reconstruction_without_new_identity() {
        // Source-only seed serialization: no renderer or physical font evidence.
        let film: Film = serde_json::from_value(json!({
            "version": semwright_motion_authoring::AUTHORING_VERSION,
            "id": "font-seed-fixture",
            "output": {
                "width": 1920, "height": 1080,
                "frame_rate": {"num": 30, "den": 1}, "aspect": "landscape",
                "safe_area": {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}
            },
            "editorial": {
                "version": 1,
                "font": {
                    "family": "Instrument Sans Variable", "asset_digest": null,
                    "fallback": "allow_and_report", "permitted_fallbacks": []
                },
                "mono_font": {
                    "family": "IBM Plex Mono", "asset_digest": null,
                    "fallback": "allow_and_report", "permitted_fallbacks": []
                },
                "type_scale": {"body": 32.0},
                "colors": {"background": "#FFFFFF", "text": "#111111"},
                "spacing": {}, "stroke": 0.0, "corner_radius": 0.0
            },
            "timing": {
                "version": 1, "duration": {"num": 1, "den": 1},
                "spans": [], "constraints": []
            },
            "sequences": [], "cues": {"version": 1, "cues": []}, "assets": []
        }))
        .unwrap();
        let generation = Uuid::parse_str("11111111-2222-4333-8444-555555555555").unwrap();
        let first = json!({
            "project": empty_managed_project(&film, generation), "dry_run": false
        });
        let reconstructed = json!({
            "project": empty_managed_project(&film, generation), "dry_run": false
        });
        assert_eq!(
            first["project"]["generation"],
            "11111111222243338444555555555555"
        );
        assert_eq!(
            request_digest("driver.motion-canvas.project.create", &first).unwrap(),
            request_digest("driver.motion-canvas.project.create", &reconstructed).unwrap()
        );
        let replacement = json!({
            "project": empty_managed_project(
                &film, Uuid::parse_str("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee").unwrap()
            ),
            "dry_run": false
        });
        assert_ne!(
            request_digest("driver.motion-canvas.project.create", &first).unwrap(),
            request_digest("driver.motion-canvas.project.create", &replacement).unwrap()
        );
    }

    #[test]
    fn brand_font_handoff_rejects_a_longer_existing_file_and_preserves_it() {
        let temp = tempfile::tempdir().unwrap();
        let font = font();
        let relative = stage_font(temp.path(), &font).unwrap();
        let mut changed = font.bytes.clone();
        changed.push(0);
        fs::write(temp.path().join(&relative), &changed).unwrap();
        assert!(stage_font(temp.path(), &font).is_err());
        assert_eq!(fs::read(temp.path().join(relative)).unwrap(), changed);
    }
}
