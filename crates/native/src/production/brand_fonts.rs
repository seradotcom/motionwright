//! CAS-to-managed-font handoff through the existing authorized public driver.
//! The owner staging directory is retained; mount observation is not a write grant.
use super::*;
use motionwright_service::VerifiedBrandFont;
use semwright_motion_authoring::Film;
use std::collections::BTreeSet;
#[cfg(all(test, unix))]
use std::fs::OpenOptions;
#[cfg(unix)]
use std::{
    ffi::CString,
    io::{Seek, SeekFrom, Write},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::{ffi::OsStrExt, fs::MetadataExt},
    },
};

pub(super) fn font_relative_name(font: &VerifiedBrandFont) -> NativeResult<String> {
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
    Ok(format!("mw-font-{}.{}", font.source.sha256, extension))
}

#[cfg(unix)]
#[derive(Debug)]
struct FontStagingRoot {
    directories: Vec<fs::File>,
    components: Vec<CString>,
}

#[cfg(unix)]
impl FontStagingRoot {
    fn open(root: &Path) -> NativeResult<Self> {
        Self::open_with_hook(root, |_| {})
    }

    fn open_with_hook(root: &Path, mut after_component: impl FnMut(&Path)) -> NativeResult<Self> {
        let selected = safe_metadata(root)?;
        if !selected.is_dir() {
            return Err(invalid(
                "Brand font staging root must be an owner directory",
            ));
        }
        let selected_identity = (selected.dev(), selected.ino());
        let retained_path = retained_font_root_path(root)?;
        let mut names = Vec::new();
        for part in retained_path.components() {
            match part {
                Component::RootDir => {}
                Component::Normal(name) => {
                    names.push(
                        CString::new(name.as_bytes()).map_err(|_| {
                            invalid("Brand font root contains an invalid component")
                        })?,
                    );
                }
                _ => return Err(invalid("Brand font root must be absolute and normalized")),
            }
        }
        if names.is_empty() || names.len() > 64 {
            return Err(invalid(
                "Brand font root must be below the volume root within its component budget",
            ));
        }
        let slash = CString::new("/").unwrap();
        let flags = libc::O_RDONLY
            | libc::O_DIRECTORY
            | libc::O_NOFOLLOW
            | libc::O_CLOEXEC
            | libc::O_NONBLOCK;
        // SAFETY: slash is a live terminated absolute root; successful fd has one owner.
        let fd = unsafe { libc::open(slash.as_ptr(), flags) };
        if fd < 0 {
            return Err(backend("Brand font volume root could not be retained"));
        }
        // SAFETY: a successful open returned this fresh descriptor.
        let root_file = unsafe { fs::File::from_raw_fd(fd) };
        let mut directories = vec![root_file];
        let mut observed_path = PathBuf::from("/");
        for name in &names {
            // A single component is resolved only under the retained parent.
            // SAFETY: parent fd and terminated name remain live; flags create no file.
            let fd = unsafe {
                libc::openat(
                    directories.last().unwrap().as_raw_fd(),
                    name.as_ptr(),
                    flags,
                )
            };
            if fd < 0 {
                return Err(backend(
                    "Brand font ancestor could not be retained without links",
                ));
            }
            // SAFETY: successful openat returned this fresh descriptor.
            let next = unsafe { fs::File::from_raw_fd(fd) };
            if !next
                .metadata()
                .map_err(|_| backend("Brand font ancestor metadata unavailable"))?
                .is_dir()
            {
                return Err(invalid("Brand font ancestor is not a directory"));
            }
            directories.push(next);
            observed_path.push(std::ffi::OsStr::from_bytes(name.as_bytes()));
            after_component(&observed_path);
        }
        let retained = Self {
            directories,
            components: names,
        };
        let metadata = retained
            .directory()
            .metadata()
            .map_err(|_| backend("Retained brand font root metadata unavailable"))?;
        if (metadata.dev(), metadata.ino()) != selected_identity {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Owner brand font root changed during acquisition",
            ));
        }
        retained.revalidate()?;
        Ok(retained)
    }

    fn directory(&self) -> &fs::File {
        self.directories
            .last()
            .expect("retained root always has a directory")
    }

    fn entry(parent: &fs::File, name: &CString) -> NativeResult<libc::stat> {
        let mut value = std::mem::MaybeUninit::<libc::stat>::uninit();
        // SAFETY: fd/name are live; fstatat initializes value only on zero return.
        let code = unsafe {
            libc::fstatat(
                parent.as_raw_fd(),
                name.as_ptr(),
                value.as_mut_ptr(),
                libc::AT_SYMLINK_NOFOLLOW,
            )
        };
        if code != 0 {
            return Err(backend("Retained brand font entry cannot be observed"));
        }
        // SAFETY: successful fstatat initialized the complete stat structure.
        Ok(unsafe { value.assume_init() })
    }

    #[allow(clippy::unnecessary_cast)]
    fn entry_identity(entry: &libc::stat) -> (u64, u64) {
        // libc aliases differ across Unix; std MetadataExt exposes both as u64.
        (entry.st_dev as u64, entry.st_ino as u64)
    }

    fn revalidate(&self) -> NativeResult<()> {
        for (index, name) in self.components.iter().enumerate() {
            let entry = Self::entry(&self.directories[index], name)?;
            let retained = self.directories[index + 1]
                .metadata()
                .map_err(|_| backend("Retained brand font ancestor metadata unavailable"))?;
            if entry.st_mode & libc::S_IFMT != libc::S_IFDIR
                || Self::entry_identity(&entry) != (retained.dev(), retained.ino())
            {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Brand font ancestor entry changed",
                ));
            }
        }
        let root = self
            .directory()
            .metadata()
            .map_err(|_| backend("Retained brand font root metadata unavailable"))?;
        if !root.is_dir() || root.mode() & 0o022 != 0 {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Retained brand font root is not an owner directory",
            ));
        }
        Ok(())
    }

    fn child_name(relative: &str) -> NativeResult<CString> {
        if relative.is_empty()
            || relative.len() > 255
            || relative.contains('/')
            || relative.contains('\\')
            || Path::new(relative).components().count() != 1
            || !matches!(
                Path::new(relative).components().next(),
                Some(Component::Normal(_))
            )
        {
            return Err(invalid(
                "Brand font staging requires one bounded child name",
            ));
        }
        CString::new(relative).map_err(|_| invalid("Invalid brand font child name"))
    }

    fn open_child(&self, relative: &str, create: bool) -> std::io::Result<fs::File> {
        let name = Self::child_name(relative)
            .map_err(|_| std::io::Error::from(std::io::ErrorKind::InvalidInput))?;
        let flags = (if create {
            libc::O_RDWR | libc::O_CREAT | libc::O_EXCL
        } else {
            libc::O_RDONLY
        }) | libc::O_NOFOLLOW
            | libc::O_NONBLOCK
            | libc::O_CLOEXEC;
        // SAFETY: root fd and one terminated component are live. Creation is exclusive;
        // mode applies only to a new child. No absolute pathname can escape the fd.
        let fd = unsafe {
            libc::openat(
                self.directory().as_raw_fd(),
                name.as_ptr(),
                flags,
                0o600 as libc::c_uint,
            )
        };
        if fd < 0 {
            return Err(std::io::Error::last_os_error());
        }
        // SAFETY: successful openat returned one fresh owned descriptor.
        Ok(unsafe { fs::File::from_raw_fd(fd) })
    }

    fn open_existing(&self, relative: &str, expected_len: u64) -> NativeResult<fs::File> {
        let file = self
            .open_child(relative, false)
            .map_err(|_| backend("Staged brand font could not be opened"))?;
        let opened = file
            .metadata()
            .map_err(|_| backend("Staged brand font metadata could not be read"))?;
        if !opened.is_file()
            || opened.len() != expected_len
            || opened.nlink() != 1
            || opened.mode() & 0o022 != 0
        {
            return Err(backend("Staged brand font changed before its bounded read"));
        }
        Ok(file)
    }

    fn verify(
        &self,
        relative: &str,
        file: &mut fs::File,
        font: &VerifiedBrandFont,
    ) -> NativeResult<()> {
        let expected_len = font.bytes.len() as u64;
        let before = file
            .metadata()
            .map_err(|_| backend("Staged font metadata unavailable"))?;
        if !before.is_file()
            || before.len() != expected_len
            || before.nlink() != 1
            || before.mode() & 0o022 != 0
        {
            return Err(backend("Staged brand font changed before its bounded read"));
        }
        file.seek(SeekFrom::Start(0))
            .map_err(|_| backend("Staged font seek failed"))?;
        let mut bytes = Vec::with_capacity(font.bytes.len());
        Read::by_ref(file)
            .take(expected_len + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| backend("Staged brand font could not be read"))?;
        let after = file
            .metadata()
            .map_err(|_| backend("Staged font metadata unavailable"))?;
        let entry = Self::entry(self.directory(), &Self::child_name(relative)?)?;
        if entry.st_mode & libc::S_IFMT != libc::S_IFREG
            || Self::entry_identity(&entry) != (after.dev(), after.ino())
            || !after.is_file()
            || after.nlink() != 1
            || after.len() != expected_len
            || (
                before.dev(),
                before.ino(),
                before.mtime(),
                before.mtime_nsec(),
                before.ctime(),
                before.ctime_nsec(),
            ) != (
                after.dev(),
                after.ino(),
                after.mtime(),
                after.mtime_nsec(),
                after.ctime(),
                after.ctime_nsec(),
            )
            || bytes != font.bytes
            || hex::encode(Sha256::digest(&bytes)) != font.source.sha256
        {
            return Err(backend(
                "Staged brand font differs; no overwrite is allowed",
            ));
        }
        self.revalidate()
    }

    fn stage(&self, font: &VerifiedBrandFont) -> NativeResult<String> {
        self.stage_with_hook(font, || {})
    }

    fn stage_with_hook(
        &self,
        font: &VerifiedBrandFont,
        before_open: impl FnOnce(),
    ) -> NativeResult<String> {
        let relative = font_relative_name(font)?;
        self.revalidate()?;
        before_open();
        let mut file = match self.open_child(&relative, true) {
            Ok(mut file) => {
                // Retain any partial child on failure. Never delete/recreate by pathname.
                file.write_all(&font.bytes)
                    .map_err(|_| backend("Brand font handoff write failed"))?;
                file.sync_all()
                    .map_err(|_| backend("Brand font handoff sync failed"))?;
                self.directory()
                    .sync_all()
                    .map_err(|_| backend("Brand font handoff directory sync failed"))?;
                file
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                self.open_existing(&relative, font.bytes.len() as u64)?
            }
            Err(_) => {
                return Err(backend(
                    "Brand font handoff could not be created exclusively",
                ));
            }
        };
        self.verify(&relative, &mut file, font)?;
        Ok(relative)
    }
}

#[cfg(unix)]
fn retained_font_root_path(root: &Path) -> NativeResult<PathBuf> {
    #[cfg(target_os = "macos")]
    for alias in ["/tmp", "/var", "/etc"] {
        let alias = Path::new(alias);
        if root.starts_with(alias)
            && fs::symlink_metadata(alias).is_ok_and(|metadata| metadata.file_type().is_symlink())
        {
            let target = trusted_macos_system_alias(alias)
                .ok_or_else(|| invalid("Untrusted macOS staging root alias"))?;
            return Ok(target.join(
                root.strip_prefix(alias)
                    .map_err(|_| invalid("Invalid staging alias"))?,
            ));
        }
    }
    Ok(root.to_path_buf())
}

#[cfg(not(unix))]
struct FontStagingRoot;
#[cfg(not(unix))]
impl FontStagingRoot {
    fn open(_root: &Path) -> NativeResult<Self> {
        Err(Self::unsupported())
    }
    fn revalidate(&self) -> NativeResult<()> {
        Err(Self::unsupported())
    }
    fn stage(&self, font: &VerifiedBrandFont) -> NativeResult<String> {
        let _ = font_relative_name(font)?;
        Err(Self::unsupported())
    }
    fn unsupported() -> Error {
        Error::new(
            ErrorCode::Unsupported,
            "Selected brand fonts require retained directory-relative no-replace staging on this platform",
        )
    }
}

#[cfg(test)]
fn stage_font(root: &Path, font: &VerifiedBrandFont) -> NativeResult<String> {
    let _ = font_relative_name(font)?;
    FontStagingRoot::open(root)?.stage(font)
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

enum ActiveFontStaging<'a> {
    Legacy(FontStagingRoot),
    Retained(&'a brand_font_staging::RetainedFontRoot),
}

impl ActiveFontStaging<'_> {
    fn revalidate(&self) -> NativeResult<()> {
        match self {
            Self::Legacy(root) => root.revalidate(),
            Self::Retained(root) => root.revalidate(),
        }
    }

    fn stage(&self, font: &VerifiedBrandFont) -> NativeResult<String> {
        match self {
            Self::Legacy(root) => root.stage(font),
            Self::Retained(root) => root.stage(font),
        }
    }
}

impl ProductionCoordinator {
    /// Backend-only owner configuration. The same directory must already be the
    /// Broker/provider's read-only media grant. This setter grants no authority,
    /// adds no WebView field and does not write files or invoke the provider.
    pub fn with_brand_font_media_root(mut self, root: PathBuf) -> NativeResult<Self> {
        if self.retained_brand_font_root.is_some() {
            return Err(Error::new(
                ErrorCode::Conflict,
                "A retained font owner cannot be replaced by a pathname",
            ));
        }
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
        // Observe the same canonical provider instance before staging. The
        // separately configured root remains the local owner write authority.
        let staging = match self.retained_brand_font_root.as_deref() {
            Some(retained) => ActiveFontStaging::Retained(retained),
            None => ActiveFontStaging::Legacy(FontStagingRoot::open(root)?),
        };
        let bound = self.bind_font_media(root).await?;
        staging.revalidate()?;
        let staged = resolved
            .into_iter()
            .map(|font| {
                let relative = staging.stage(&font)?;
                Ok((font, relative))
            })
            .collect::<NativeResult<Vec<_>>>()?;
        staging.revalidate()?;
        let inspected = bound
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
                staging.revalidate()?;
                let created = bound.execute(
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
            staging.revalidate()?;
            let listed = bound
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
            staging.revalidate()?;
            let imported = bound
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
            staging.revalidate()?;
            let observed = bound
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
        staging.revalidate()?;
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

    #[cfg(unix)]
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
    #[cfg(unix)]
    #[test]
    fn brand_font_handoff_replaced_by_fifo_is_rejected_without_a_writer_or_blocking_open() {
        use std::ffi::CString;
        use std::os::unix::ffi::OsStrExt;
        use std::process::{Child, ExitStatus, Stdio};
        use std::time::Instant;

        const CHILD_MARKER: &str = "MOTIONWRIGHT_TEST_BRAND_FONT_FIFO_CHILD_MARKER";
        const TEST_NAME: &str = "production::brand_fonts::tests::brand_font_handoff_replaced_by_fifo_is_rejected_without_a_writer_or_blocking_open";

        // The same test binary isolates a potentially blocking regression. No
        // detached thread or product executor is created. A marker proves that
        // the exact child case ran, rather than an empty successful filter.
        if let Some(marker) = std::env::var_os(CHILD_MARKER) {
            let marker = PathBuf::from(marker);
            assert!(marker.is_absolute());
            let path = marker.parent().unwrap().join("font.ttf");
            let font = font();
            fs::write(&path, &font.bytes).unwrap();
            let before = safe_metadata(&path).unwrap();
            assert!(before.is_file());
            assert_eq!(before.len(), font.bytes.len() as u64);

            // This is the actual pre-open race: the regular file was already
            // observed. Do not call the initial metadata guard again afterward.
            fs::remove_file(&path).unwrap();
            let fifo = CString::new(path.as_os_str().as_bytes()).unwrap();
            // SAFETY: the NUL-terminated path is live for this call; this test
            // owns the parent and the removed leaf. No writer is ever opened.
            assert_eq!(unsafe { libc::mkfifo(fifo.as_ptr(), 0o600) }, 0);
            let retained = FontStagingRoot::open(marker.parent().unwrap()).unwrap();
            let error = retained
                .open_existing("font.ttf", before.len())
                .unwrap_err();
            assert_eq!(
                error.message,
                "Staged brand font changed before its bounded read"
            );
            let mut observed = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(marker)
                .unwrap();
            writeln!(observed, "{}:rejected-before-read", std::process::id()).unwrap();
            return;
        }

        fn observe_until(
            child: &mut Child,
            deadline: Instant,
        ) -> std::io::Result<Option<ExitStatus>> {
            loop {
                if let Some(status) = child.try_wait()? {
                    return Ok(Some(status));
                }
                if Instant::now() >= deadline {
                    return Ok(None);
                }
                std::thread::sleep(
                    deadline
                        .saturating_duration_since(Instant::now())
                        .min(Duration::from_millis(10)),
                );
            }
        }

        let directory = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(directory.path()).unwrap();
        let marker = root.join("fifo-rejection.observed");
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", TEST_NAME, "--nocapture"])
            .env(CHILD_MARKER, &marker)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::inherit())
            .spawn()
            .unwrap();
        let original_pid = child.id();
        let first = observe_until(&mut child, Instant::now() + Duration::from_secs(10));
        let status = match first {
            Ok(Some(status)) => status,
            original => {
                // Kill this same child once and observe its terminal with one
                // bounded cleanup allowance. No second test dispatch or join.
                let kill_result = child.kill();
                let terminal = observe_until(&mut child, Instant::now() + Duration::from_secs(5));
                panic!(
                    "font FIFO control did not finish: pid={original_pid}, original={original:?}, kill={kill_result:?}, cleanup_terminal={terminal:?}"
                );
            }
        };
        assert!(
            status.success(),
            "font FIFO child failed: pid={original_pid}, terminal={status:?}"
        );
        assert_eq!(
            fs::read(&marker).unwrap(),
            format!("{original_pid}:rejected-before-read\n").as_bytes()
        );
        println!(
            "font FIFO control: pid={original_pid}, terminal={status:?}, rejected_before_read=true"
        );
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
                "version": 1, "duration": {"num": "1", "den": "1"},
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

    #[cfg(unix)]
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

    #[cfg(unix)]
    #[test]
    fn retained_font_root_swap_after_validation_never_writes_the_replacement() {
        use std::os::unix::fs::symlink;
        for linked in [false, true] {
            for existing in [false, true] {
                let temp = tempfile::tempdir().unwrap();
                let base = fs::canonicalize(temp.path()).unwrap();
                let root = base.join("media");
                let original = base.join("retained-original");
                let decoy = base.join("decoy");
                fs::create_dir(&root).unwrap();
                fs::create_dir(&decoy).unwrap();
                let font = font();
                let relative = font_relative_name(&font).unwrap();
                if existing {
                    fs::write(root.join(&relative), &font.bytes).unwrap();
                    fs::write(decoy.join(&relative), &font.bytes).unwrap();
                }
                let retained = FontStagingRoot::open(&root).unwrap();
                let result = retained.stage_with_hook(&font, || {
                    fs::rename(&root, &original).unwrap();
                    if linked {
                        symlink(&decoy, &root).unwrap();
                    } else {
                        fs::rename(&decoy, &root).unwrap();
                    }
                });
                assert!(
                    result.is_err(),
                    "replacement must not produce a valid handoff"
                );
                assert_eq!(fs::read(original.join(&relative)).unwrap(), font.bytes);
                let replacement = if linked { &decoy } else { &root };
                if existing {
                    assert_eq!(fs::read(replacement.join(&relative)).unwrap(), font.bytes);
                    assert_eq!(fs::read_dir(replacement).unwrap().count(), 1);
                } else {
                    assert_eq!(fs::read_dir(replacement).unwrap().count(), 0);
                }
                assert!(retained.revalidate().is_err());
            }
        }
    }

    #[cfg(unix)]
    #[test]
    fn retained_font_ancestor_swap_during_acquisition_cannot_retarget_staging() {
        use std::os::unix::fs::symlink;
        for linked in [false, true] {
            let temp = tempfile::tempdir().unwrap();
            let base = fs::canonicalize(temp.path()).unwrap();
            let ancestor = base.join("ancestor");
            let root = ancestor.join("nested/media");
            let original = base.join("retained-ancestor");
            let decoy = base.join("decoy");
            fs::create_dir_all(&root).unwrap();
            fs::create_dir_all(decoy.join("nested/media")).unwrap();
            let mut reached = false;
            let result = FontStagingRoot::open_with_hook(&root, |opened| {
                if opened == ancestor && !reached {
                    reached = true;
                    fs::rename(&ancestor, &original).unwrap();
                    if linked {
                        symlink(&decoy, &ancestor).unwrap();
                    } else {
                        fs::rename(&decoy, &ancestor).unwrap();
                    }
                }
            });
            assert!(reached, "the actual component acquisition barrier must run");
            assert!(result.is_err());
            assert_eq!(
                fs::read_dir(original.join("nested/media")).unwrap().count(),
                0
            );
            let replacement = if linked { decoy } else { ancestor };
            assert_eq!(
                fs::read_dir(replacement.join("nested/media"))
                    .unwrap()
                    .count(),
                0
            );
        }
    }

    #[cfg(unix)]
    #[test]
    fn retained_font_leaf_replacement_with_identical_bytes_is_not_the_same_entry() {
        let temp = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(temp.path()).unwrap();
        let retained = FontStagingRoot::open(&root).unwrap();
        let font = font();
        let relative = retained.stage(&font).unwrap();
        let mut opened = retained
            .open_existing(&relative, font.bytes.len() as u64)
            .unwrap();
        let kept = root.join("original-kept.ttf");
        fs::rename(root.join(&relative), &kept).unwrap();
        fs::write(root.join(&relative), &font.bytes).unwrap();
        assert!(retained.verify(&relative, &mut opened, &font).is_err());
        assert_eq!(fs::read(&kept).unwrap(), font.bytes);
        assert_eq!(fs::read(root.join(&relative)).unwrap(), font.bytes);
        assert_ne!(
            fs::metadata(&kept).unwrap().ino(),
            fs::metadata(root.join(&relative)).unwrap().ino()
        );
    }

    #[cfg(not(unix))]
    #[test]
    fn brand_font_staging_without_retained_directory_support_refuses_before_creation() {
        let temp = tempfile::tempdir().unwrap();
        let error = stage_font(temp.path(), &font()).unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(error.outcome_known);
        assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 0);
    }
}
