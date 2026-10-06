use motionwright_domain::{DomainError, RationalTime};
use motionwright_storage::{Result, StorageError};
use std::{fs::File, path::Path};
use symphonia::{
    core::{
        codecs::audio::AudioDecoderOptions,
        errors::Error as SymphoniaError,
        formats::{FormatOptions, TrackType, probe::Hint},
        io::MediaSourceStream,
        meta::MetadataOptions,
    },
    default::{get_codecs, get_probe},
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AudioMeasurement {
    pub sample_rate_hz: u32,
    pub channels: u16,
    pub decoded_frames: u64,
    pub duration: RationalTime,
}

fn invalid(message: impl Into<String>) -> StorageError {
    DomainError::Invalid(message.into()).into()
}

pub fn measure_audio_file(path: impl AsRef<Path>) -> Result<AudioMeasurement> {
    let path = path.as_ref();
    let source = File::open(path)?;
    let stream = MediaSourceStream::new(Box::new(source), Default::default());
    let mut hint = Hint::new();
    if let Some(extension) = path.extension().and_then(|value| value.to_str()) {
        hint.with_extension(extension);
    }
    let mut format = get_probe()
        .probe(
            &hint,
            stream,
            FormatOptions::default(),
            MetadataOptions::default(),
        )
        .map_err(|error| invalid(format!("audio probe failed: {error}")))?;
    let track = format
        .default_track(TrackType::Audio)
        .ok_or_else(|| invalid("audio source contains no decodable audio track"))?;
    let track_id = track.id;
    let params = track
        .codec_params
        .as_ref()
        .and_then(|params| params.audio())
        .ok_or_else(|| invalid("audio codec parameters are unavailable"))?;
    let mut decoder = get_codecs()
        .make_audio_decoder(params, &AudioDecoderOptions::default())
        .map_err(|error| invalid(format!("audio decoder unavailable: {error}")))?;

    let mut decoded_frames = 0_u64;
    let mut observed_rate = None;
    let mut observed_channels = None;
    loop {
        let packet = match format.next_packet() {
            Ok(Some(packet)) => packet,
            Ok(None) => break,
            Err(SymphoniaError::ResetRequired) => {
                return Err(invalid(
                    "audio stream changed track layout during measurement",
                ));
            }
            Err(error) => return Err(invalid(format!("audio demux failed: {error}"))),
        };
        if packet.track_id != track_id {
            continue;
        }
        let decoded = decoder
            .decode(&packet)
            .map_err(|error| invalid(format!("audio decode failed: {error}")))?;
        let spec = decoded.spec();
        let rate = spec.rate();
        let channels = u16::try_from(spec.channels().count())
            .map_err(|_| invalid("audio channel count exceeds supported bounds"))?;
        if !(8_000..=384_000).contains(&rate) || channels == 0 || channels > 32 {
            return Err(invalid("decoded audio format is outside supported bounds"));
        }
        if observed_rate.is_some_and(|value| value != rate)
            || observed_channels.is_some_and(|value| value != channels)
        {
            return Err(invalid(
                "audio format changes sample rate or channels mid-stream",
            ));
        }
        observed_rate = Some(rate);
        observed_channels = Some(channels);
        decoded_frames = decoded_frames
            .checked_add(
                u64::try_from(decoded.frames())
                    .map_err(|_| invalid("decoded frame count overflow"))?,
            )
            .ok_or_else(|| invalid("decoded frame count overflow"))?;
    }

    let sample_rate_hz = observed_rate.ok_or_else(|| invalid("audio decoded no frames"))?;
    let channels = observed_channels.ok_or_else(|| invalid("audio decoded no frames"))?;
    if decoded_frames == 0 {
        return Err(invalid("audio decoded no frames"));
    }
    let frames = i64::try_from(decoded_frames)
        .map_err(|_| invalid("audio duration exceeds rational-time bounds"))?;
    let duration = RationalTime::new(frames, i64::from(sample_rate_hz))
        .map_err(|error| invalid(format!("audio duration is invalid: {error}")))?;
    Ok(AudioMeasurement {
        sample_rate_hz,
        channels,
        decoded_frames,
        duration,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn write_pcm16_wav(path: &Path, sample_rate: u32, channels: u16, frames: u32) {
        let data_bytes = frames * u32::from(channels) * 2;
        let mut file = File::create(path).unwrap();
        file.write_all(b"RIFF").unwrap();
        file.write_all(&(36 + data_bytes).to_le_bytes()).unwrap();
        file.write_all(b"WAVEfmt ").unwrap();
        file.write_all(&16_u32.to_le_bytes()).unwrap();
        file.write_all(&1_u16.to_le_bytes()).unwrap();
        file.write_all(&channels.to_le_bytes()).unwrap();
        file.write_all(&sample_rate.to_le_bytes()).unwrap();
        file.write_all(&(sample_rate * u32::from(channels) * 2).to_le_bytes())
            .unwrap();
        file.write_all(&(channels * 2).to_le_bytes()).unwrap();
        file.write_all(&16_u16.to_le_bytes()).unwrap();
        file.write_all(b"data").unwrap();
        file.write_all(&data_bytes.to_le_bytes()).unwrap();
        file.write_all(&vec![0_u8; data_bytes as usize]).unwrap();
        file.sync_all().unwrap();
    }

    #[test]
    fn measures_decoded_frames_instead_of_guessing_from_file_size() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("voice.wav");
        write_pcm16_wav(&path, 48_000, 1, 72_000);
        let measured = measure_audio_file(&path).unwrap();
        assert_eq!(measured.sample_rate_hz, 48_000);
        assert_eq!(measured.channels, 1);
        assert_eq!(measured.decoded_frames, 72_000);
        assert_eq!(measured.duration, RationalTime::new(3, 2).unwrap());
    }
}
