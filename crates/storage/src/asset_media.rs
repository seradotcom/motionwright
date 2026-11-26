//! Bounded, retained reads of imported media owned by one exact Project.
//! Preparing the handle is short; its full hash runs after releasing Store's lock.
use super::{
    Result, StorageError, Store, VerifiedBlobMetadata, checked_existing_blob_path_from_root,
    ensure_regular_file, file_import, verified_blob_changed, verified_blob_metadata,
};
use motionwright_domain::{DomainError, RevisionStamp};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{Read, Seek, SeekFrom},
};
use uuid::Uuid;

pub const ASSET_MEDIA_MAX_BYTES: u64 = 1024 * 1024 * 1024;
pub const ASSET_MEDIA_RANGE_BYTES: u64 = 4 * 1024 * 1024;
const BLOCK_BYTES: usize = 1024 * 1024;
const IMAGE_MAX_BYTES: u64 = 16 * 1024 * 1024;
#[path = "asset_image_bounds.rs"]
mod image_bounds;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AssetMediaKind {
    Mp4,
    Wav,
    Png,
    Jpeg,
}
impl AssetMediaKind {
    pub fn media_type(self) -> &'static str {
        match self {
            Self::Mp4 => "video/mp4",
            Self::Wav => "audio/wav",
            Self::Png => "image/png",
            Self::Jpeg => "image/jpeg",
        }
    }
    pub fn is_image(self) -> bool {
        matches!(self, Self::Png | Self::Jpeg)
    }
    pub fn maximum_bytes(self) -> u64 {
        if self.is_image() {
            IMAGE_MAX_BYTES
        } else {
            ASSET_MEDIA_MAX_BYTES
        }
    }
    fn from_media_type(value: &str) -> Result<Self> {
        match value {
            "video/mp4" => Ok(Self::Mp4),
            "audio/wav" | "audio/x-wav" | "audio/wave" => Ok(Self::Wav),
            "image/png" => Ok(Self::Png),
            "image/jpeg" => Ok(Self::Jpeg),
            _ => Err(DomainError::Invalid(
                "Original media preview supports MP4, WAV, static PNG and JPEG assets".into(),
            )
            .into()),
        }
    }
}

/// The file and its owner binding cannot be reconstructed from caller JSON.
pub struct AssetMediaSource {
    file: File,
    metadata: VerifiedBlobMetadata,
    stamp: RevisionStamp,
    asset_id: Uuid,
    sha256: String,
    kind: AssetMediaKind,
}

pub struct VerifiedAssetMedia {
    source: AssetMediaSource,
    blocks: Vec<[u8; 32]>,
}
impl AssetMediaSource {
    pub fn verify(mut self) -> Result<VerifiedAssetMedia> {
        if verified_blob_metadata(&self.file.metadata()?)? != self.metadata {
            return Err(verified_blob_changed());
        }
        let size = self.metadata.size;
        if self.kind.is_image() {
            image_bounds::check(&mut self.file, size, self.kind)?;
            self.file.seek(SeekFrom::Start(0))?;
        }
        let mut digest = Sha256::new();
        let mut blocks = Vec::with_capacity(size.div_ceil(BLOCK_BYTES as u64) as usize);
        let mut buffer = vec![0; BLOCK_BYTES];
        let mut offset = 0;
        while offset < size {
            let count = (size - offset).min(BLOCK_BYTES as u64) as usize;
            self.file.read_exact(&mut buffer[..count])?;
            if offset == 0 {
                let supported = match self.kind {
                    AssetMediaKind::Png | AssetMediaKind::Jpeg => true,
                    AssetMediaKind::Mp4 => buffer.get(4..8) == Some(&b"ftyp"[..]),
                    AssetMediaKind::Wav => {
                        buffer.get(..4) == Some(&b"RIFF"[..])
                            && buffer.get(8..12) == Some(&b"WAVE"[..])
                            && u64::from(u32::from_le_bytes(
                                buffer[4..8].try_into().expect("bounded WAV header"),
                            )) + 8
                                == size
                    }
                };
                if !supported {
                    return Err(DomainError::Invalid(
                        "Original media header does not match its saved type".into(),
                    )
                    .into());
                }
            }
            digest.update(&buffer[..count]);
            blocks.push(Sha256::digest(&buffer[..count]).into());
            offset += count as u64;
        }
        let mut extra = [0; 1];
        if self.file.read(&mut extra)? != 0
            || verified_blob_metadata(&self.file.metadata()?)? != self.metadata
            || hex::encode(digest.finalize()) != self.sha256
        {
            return Err(verified_blob_changed());
        }
        Ok(VerifiedAssetMedia {
            source: self,
            blocks,
        })
    }
}
impl VerifiedAssetMedia {
    pub fn stamp(&self) -> &RevisionStamp {
        &self.source.stamp
    }
    pub fn asset_id(&self) -> Uuid {
        self.source.asset_id
    }
    pub fn sha256(&self) -> &str {
        &self.source.sha256
    }
    pub fn media_kind(&self) -> AssetMediaKind {
        self.source.kind
    }
    pub fn size_bytes(&self) -> u64 {
        self.source.metadata.size
    }

    pub fn read_range(&mut self, start: u64, end: u64) -> Result<Vec<u8>> {
        let size = self.size_bytes();
        if start > end || end >= size || end - start >= ASSET_MEDIA_RANGE_BYTES {
            return Err(
                DomainError::Invalid("Original media range exceeds its bound".into()).into(),
            );
        }
        if verified_blob_metadata(&self.source.file.metadata()?)? != self.source.metadata {
            return Err(verified_blob_changed());
        }
        let mut bytes = Vec::with_capacity((end - start + 1) as usize);
        let mut buffer = vec![0; BLOCK_BYTES];
        for index in start / BLOCK_BYTES as u64..=end / BLOCK_BYTES as u64 {
            let offset = index * BLOCK_BYTES as u64;
            let count = (size - offset).min(BLOCK_BYTES as u64) as usize;
            self.source.file.seek(SeekFrom::Start(offset))?;
            self.source.file.read_exact(&mut buffer[..count])?;
            let actual: [u8; 32] = Sha256::digest(&buffer[..count]).into();
            if self.blocks.get(index as usize) != Some(&actual) {
                return Err(verified_blob_changed());
            }
            let lo = start.saturating_sub(offset) as usize;
            let hi = (end - offset + 1).min(count as u64) as usize;
            bytes.extend_from_slice(&buffer[lo..hi]);
        }
        if verified_blob_metadata(&self.source.file.metadata()?)? != self.source.metadata {
            return Err(verified_blob_changed());
        }
        Ok(bytes)
    }
}
impl Store {
    pub fn open_asset_media_source(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        asset_id: Uuid,
        sha256: &str,
    ) -> Result<AssetMediaSource> {
        let project = self.load_project(project_id)?;
        if expected.resource != project.resource_key() || expected.generation != project.generation
        {
            return Err(StorageError::GenerationConflict);
        }
        if expected.revision != project.revision {
            return Err(StorageError::Conflict {
                expected: expected.revision,
                actual: project.revision,
            });
        }
        let asset = project
            .assets
            .iter()
            .find(|asset| asset.id == asset_id && asset.content_sha256.as_deref() == Some(sha256))
            .ok_or_else(|| {
                DomainError::Invalid(
                    "Original media asset is not owned by this Project revision".into(),
                )
            })?;
        let kind = AssetMediaKind::from_media_type(&asset.media_type)?;
        let path = checked_existing_blob_path_from_root(self.blob_root(), sha256)?;
        let metadata =
            verified_blob_metadata(&ensure_regular_file(&path, "original media asset")?)?;
        let maximum = kind.maximum_bytes();
        if !(12..=maximum).contains(&metadata.size) {
            return Err(StorageError::BlobTooLarge {
                size: metadata.size,
                limit: maximum,
            });
        }
        let file = file_import::open_regular_import_source(&path, maximum)?;
        if verified_blob_metadata(&file.metadata()?)? != metadata
            || verified_blob_metadata(&ensure_regular_file(&path, "original media asset")?)?
                != metadata
        {
            return Err(verified_blob_changed());
        }
        Ok(AssetMediaSource {
            file,
            metadata,
            stamp: expected.clone(),
            asset_id,
            sha256: sha256.to_owned(),
            kind,
        })
    }
}
