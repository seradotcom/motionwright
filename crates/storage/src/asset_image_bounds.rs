//! Structural decode bounds for static original images, before any WebView read.
//! This is not a pixel decoder; the viewer still reports unsupported/corrupt media.
use super::{AssetMediaKind, Result, StorageError};
use motionwright_domain::DomainError;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};

const MAX_AXIS: u32 = 8192;
const MAX_PIXELS: u64 = 16 * 1024 * 1024;
const MAX_PNG_CHUNKS: usize = 8192;
const MAX_JPEG_MARKERS: usize = 4096;
const MAX_JPEG_HEADER: u64 = 1024 * 1024;

fn invalid() -> StorageError {
    DomainError::Invalid(
        "Original image requires a bounded static PNG or 8-bit JPEG with at most 16,777,216 pixels"
            .into(),
    )
    .into()
}

fn bytes<const N: usize>(file: &mut File, offset: u64, size: u64) -> Result<[u8; N]> {
    if offset.checked_add(N as u64).is_none_or(|end| end > size) {
        return Err(invalid());
    }
    file.seek(SeekFrom::Start(offset))?;
    let mut data = [0; N];
    file.read_exact(&mut data)?;
    Ok(data)
}

fn dimensions(width: u32, height: u32) -> Result<()> {
    if !(1..=MAX_AXIS).contains(&width)
        || !(1..=MAX_AXIS).contains(&height)
        || u64::from(width) * u64::from(height) > MAX_PIXELS
    {
        return Err(invalid());
    }
    Ok(())
}

fn png(file: &mut File, size: u64) -> Result<()> {
    if bytes::<8>(file, 0, size)? != *b"\x89PNG\r\n\x1a\n" {
        return Err(invalid());
    }
    let mut offset = 8_u64;
    let mut image_data = false;
    for index in 0..MAX_PNG_CHUNKS {
        let header = bytes::<8>(file, offset, size)?;
        let length = u32::from_be_bytes(header[..4].try_into().map_err(|_| invalid())?);
        let kind = &header[4..];
        let end = offset
            .checked_add(u64::from(length) + 12)
            .filter(|end| *end <= size)
            .ok_or_else(invalid)?;
        if !kind.iter().all(u8::is_ascii_alphabetic)
            || !kind[2].is_ascii_uppercase()
            || matches!(kind, b"acTL" | b"fcTL" | b"fdAT")
        {
            return Err(invalid());
        }
        if index == 0 {
            if kind != b"IHDR" || length != 13 {
                return Err(invalid());
            }
            let ihdr = bytes::<13>(file, offset + 8, size)?;
            dimensions(
                u32::from_be_bytes(ihdr[..4].try_into().map_err(|_| invalid())?),
                u32::from_be_bytes(ihdr[4..8].try_into().map_err(|_| invalid())?),
            )?;
            let depth = ihdr[8];
            let valid_depth = match ihdr[9] {
                0 => matches!(depth, 1 | 2 | 4 | 8 | 16),
                2 | 4 | 6 => matches!(depth, 8 | 16),
                3 => matches!(depth, 1 | 2 | 4 | 8),
                _ => false,
            };
            if !valid_depth || ihdr[10] != 0 || ihdr[11] != 0 || ihdr[12] > 1 {
                return Err(invalid());
            }
        } else if kind == b"IHDR" {
            return Err(invalid());
        } else if kind == b"IDAT" {
            image_data = true;
        } else if kind == b"IEND" {
            return if length == 0 && image_data && end == size {
                Ok(())
            } else {
                Err(invalid())
            };
        }
        offset = end;
    }
    Err(invalid())
}

fn jpeg(file: &mut File, size: u64) -> Result<()> {
    if bytes::<2>(file, 0, size)? != [0xff, 0xd8] {
        return Err(invalid());
    }
    let mut offset = 2_u64;
    for _ in 0..MAX_JPEG_MARKERS {
        if offset > MAX_JPEG_HEADER {
            return Err(invalid());
        }
        let marker = bytes::<2>(file, offset, size)?;
        if marker[0] != 0xff {
            return Err(invalid());
        }
        if marker[1] == 0xff {
            offset += 1;
            continue;
        }
        if matches!(marker[1], 0 | 1 | 0xd0..=0xda) {
            return Err(invalid());
        }
        let length = u16::from_be_bytes(bytes::<2>(file, offset + 2, size)?);
        let end = offset
            .checked_add(2 + u64::from(length))
            .filter(|end| *end <= size && *end <= MAX_JPEG_HEADER)
            .ok_or_else(invalid)?;
        if length < 2 {
            return Err(invalid());
        }
        // Only ordinary baseline, extended sequential and progressive 8-bit
        // frames enter the viewer. Dynamic DNL heights and other SOF types do not.
        if matches!(marker[1], 0xc0..=0xcf) && !matches!(marker[1], 0xc4 | 0xc8 | 0xcc) {
            if !matches!(marker[1], 0xc0..=0xc2) || length < 8 {
                return Err(invalid());
            }
            let frame = bytes::<6>(file, offset + 4, size)?;
            if frame[0] != 8
                || !matches!(frame[5], 1 | 3 | 4)
                || length != 8 + 3 * u16::from(frame[5])
            {
                return Err(invalid());
            }
            dimensions(
                u32::from(u16::from_be_bytes([frame[3], frame[4]])),
                u32::from(u16::from_be_bytes([frame[1], frame[2]])),
            )?;
            return Ok(());
        }
        offset = end;
    }
    Err(invalid())
}

pub(super) fn check(file: &mut File, size: u64, kind: AssetMediaKind) -> Result<()> {
    match kind {
        AssetMediaKind::Png => png(file, size),
        AssetMediaKind::Jpeg => jpeg(file, size),
        _ => Err(invalid()),
    }
}
