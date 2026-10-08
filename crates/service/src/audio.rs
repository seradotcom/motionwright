use motionwright_domain::{DomainError, RationalTime};
use motionwright_storage::{Result, StorageError};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::Path,
};
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

pub const WAVEFORM_PROXY_VERSION: u32 = 1;
pub const WAVEFORM_FRAMES_PER_PEAK: u32 = 2_048;
pub const MAX_WAVEFORM_PAGE_SIZE: usize = 2_048;
const WAVEFORM_MAGIC: &[u8; 8] = b"MWPEAK01";
const WAVEFORM_HEADER_BYTES: u64 = 72;

type AudioReader = Box<dyn symphonia::core::formats::FormatReader>;
type AudioDecoder = Box<dyn symphonia::core::codecs::audio::AudioDecoder>;
type OpenAudio = (AudioReader, AudioDecoder, u32);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AudioMeasurement {
    pub sample_rate_hz: u32,
    pub channels: u16,
    pub decoded_frames: u64,
    pub duration: RationalTime,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WaveformProxySummary {
    pub source_sha256: String,
    pub sample_rate_hz: u32,
    pub channels: u16,
    pub frames_per_peak: u32,
    pub total_frames: u64,
    pub peak_count: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WaveformPage {
    pub schema: String,
    pub algorithm: String,
    pub source_sha256: String,
    pub sample_rate_hz: u32,
    pub channels: u16,
    pub frames_per_peak: u32,
    pub total_frames: u64,
    pub peak_count: u64,
    pub page_index: u64,
    pub page_size: usize,
    pub start_peak: u64,
    pub peaks: Vec<f32>,
    pub has_previous: bool,
    pub has_next: bool,
}

fn invalid(message: impl Into<String>) -> StorageError {
    DomainError::Invalid(message.into()).into()
}

fn invalid_proxy(message: impl Into<String>) -> StorageError {
    StorageError::InvalidDerivedCache(message.into())
}

fn open_audio(path: &Path) -> Result<OpenAudio> {
    let source = File::open(path)?;
    let stream = MediaSourceStream::new(Box::new(source), Default::default());
    let mut hint = Hint::new();
    if let Some(extension) = path.extension().and_then(|value| value.to_str()) {
        hint.with_extension(extension);
    }
    let format = get_probe()
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
    let decoder = get_codecs()
        .make_audio_decoder(params, &AudioDecoderOptions::default())
        .map_err(|error| invalid(format!("audio decoder unavailable: {error}")))?;
    Ok((format, decoder, track_id))
}

fn validate_decoded_format(
    observed_rate: &mut Option<u32>,
    observed_channels: &mut Option<u16>,
    rate: u32,
    channels: u16,
) -> Result<()> {
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
    *observed_rate = Some(rate);
    *observed_channels = Some(channels);
    Ok(())
}

pub fn measure_audio_file(path: impl AsRef<Path>) -> Result<AudioMeasurement> {
    let path = path.as_ref();
    let (mut format, mut decoder, track_id) = open_audio(path)?;

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
        validate_decoded_format(&mut observed_rate, &mut observed_channels, rate, channels)?;
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

pub fn waveform_fingerprint(source_sha256: &str, frames_per_peak: u32) -> Result<String> {
    let source = hex::decode(source_sha256)
        .map_err(|_| invalid("waveform source digest is not hexadecimal"))?;
    if source.len() != 32 || frames_per_peak == 0 {
        return Err(invalid("waveform fingerprint inputs are invalid"));
    }
    let mut hasher = Sha256::new();
    hasher.update(b"motionwright.waveform.sample-peak-max-abs");
    hasher.update(WAVEFORM_PROXY_VERSION.to_le_bytes());
    hasher.update(frames_per_peak.to_le_bytes());
    hasher.update(source);
    Ok(hex::encode(hasher.finalize()))
}

fn quantize_peak(value: f32) -> u16 {
    (value.clamp(0.0, 1.0) * f32::from(u16::MAX)).round() as u16
}

fn write_waveform_header(file: &mut File, summary: &WaveformProxySummary) -> Result<()> {
    let source = hex::decode(&summary.source_sha256)
        .map_err(|_| invalid_proxy("waveform proxy source digest is malformed"))?;
    if source.len() != 32 {
        return Err(invalid_proxy(
            "waveform proxy source digest has invalid length",
        ));
    }
    file.seek(SeekFrom::Start(0))?;
    file.write_all(WAVEFORM_MAGIC)?;
    file.write_all(&WAVEFORM_PROXY_VERSION.to_le_bytes())?;
    file.write_all(&summary.sample_rate_hz.to_le_bytes())?;
    file.write_all(&summary.channels.to_le_bytes())?;
    file.write_all(&0_u16.to_le_bytes())?;
    file.write_all(&summary.frames_per_peak.to_le_bytes())?;
    file.write_all(&summary.total_frames.to_le_bytes())?;
    file.write_all(&summary.peak_count.to_le_bytes())?;
    file.write_all(&source)?;
    Ok(())
}

fn read_u16(input: &mut File) -> Result<u16> {
    let mut bytes = [0_u8; 2];
    input.read_exact(&mut bytes)?;
    Ok(u16::from_le_bytes(bytes))
}

fn read_u32(input: &mut File) -> Result<u32> {
    let mut bytes = [0_u8; 4];
    input.read_exact(&mut bytes)?;
    Ok(u32::from_le_bytes(bytes))
}

fn read_u64(input: &mut File) -> Result<u64> {
    let mut bytes = [0_u8; 8];
    input.read_exact(&mut bytes)?;
    Ok(u64::from_le_bytes(bytes))
}

fn read_waveform_header(input: &mut File) -> Result<WaveformProxySummary> {
    input.seek(SeekFrom::Start(0))?;
    let mut magic = [0_u8; 8];
    input.read_exact(&mut magic)?;
    if &magic != WAVEFORM_MAGIC {
        return Err(invalid_proxy("waveform proxy magic is invalid"));
    }
    let version = read_u32(input)?;
    if version != WAVEFORM_PROXY_VERSION {
        return Err(invalid_proxy("waveform proxy version is unsupported"));
    }
    let sample_rate_hz = read_u32(input)?;
    let channels = read_u16(input)?;
    let reserved = read_u16(input)?;
    let frames_per_peak = read_u32(input)?;
    let total_frames = read_u64(input)?;
    let peak_count = read_u64(input)?;
    let mut source = [0_u8; 32];
    input.read_exact(&mut source)?;

    if reserved != 0
        || !(8_000..=384_000).contains(&sample_rate_hz)
        || channels == 0
        || channels > 32
        || frames_per_peak == 0
        || total_frames == 0
        || peak_count == 0
    {
        return Err(invalid_proxy("waveform proxy header is invalid"));
    }
    let expected_peaks = total_frames.div_ceil(u64::from(frames_per_peak));
    if peak_count != expected_peaks {
        return Err(invalid_proxy(
            "waveform proxy peak count does not match decoded frame count",
        ));
    }

    Ok(WaveformProxySummary {
        source_sha256: hex::encode(source),
        sample_rate_hz,
        channels,
        frames_per_peak,
        total_frames,
        peak_count,
    })
}

pub fn generate_waveform_proxy(
    source_path: impl AsRef<Path>,
    source_sha256: &str,
    destination: impl AsRef<Path>,
    frames_per_peak: u32,
) -> Result<WaveformProxySummary> {
    if frames_per_peak == 0 {
        return Err(invalid("waveform frames-per-peak must be positive"));
    }
    let source_digest = hex::decode(source_sha256)
        .map_err(|_| invalid("waveform source digest is not hexadecimal"))?;
    if source_digest.len() != 32 {
        return Err(invalid("waveform source digest length is invalid"));
    }

    let source_path = source_path.as_ref();
    let (mut format, mut decoder, track_id) = open_audio(source_path)?;
    let mut output = OpenOptions::new()
        .write(true)
        .read(true)
        .create_new(true)
        .open(destination)?;
    output.write_all(&[0_u8; WAVEFORM_HEADER_BYTES as usize])?;

    let mut total_frames = 0_u64;
    let mut peak_count = 0_u64;
    let mut window_frames = 0_u32;
    let mut window_peak = 0.0_f32;
    let mut observed_rate = None;
    let mut observed_channels = None;
    let mut samples = Vec::<f32>::new();

    loop {
        let packet = match format.next_packet() {
            Ok(Some(packet)) => packet,
            Ok(None) => break,
            Err(SymphoniaError::ResetRequired) => {
                return Err(invalid(
                    "audio stream changed track layout during waveform analysis",
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
        validate_decoded_format(&mut observed_rate, &mut observed_channels, rate, channels)?;

        let channel_count = usize::from(channels);
        let sample_count = decoded.samples_interleaved();
        samples.resize(sample_count, 0.0);
        decoded.copy_to_slice_interleaved(&mut samples);
        if sample_count % channel_count != 0 {
            return Err(invalid("decoded interleaved audio has incomplete frames"));
        }

        for frame in samples[..sample_count].chunks_exact(channel_count) {
            let mut frame_peak = 0.0_f32;
            for sample in frame {
                if !sample.is_finite() {
                    return Err(invalid("decoded audio contains a non-finite sample"));
                }
                frame_peak = frame_peak.max(sample.abs());
            }
            window_peak = window_peak.max(frame_peak);
            window_frames += 1;
            total_frames = total_frames
                .checked_add(1)
                .ok_or_else(|| invalid("decoded frame count overflow"))?;
            if window_frames == frames_per_peak {
                output.write_all(&quantize_peak(window_peak).to_le_bytes())?;
                peak_count = peak_count
                    .checked_add(1)
                    .ok_or_else(|| invalid("waveform peak count overflow"))?;
                window_frames = 0;
                window_peak = 0.0;
            }
        }
    }

    if window_frames > 0 {
        output.write_all(&quantize_peak(window_peak).to_le_bytes())?;
        peak_count = peak_count
            .checked_add(1)
            .ok_or_else(|| invalid("waveform peak count overflow"))?;
    }

    let sample_rate_hz = observed_rate.ok_or_else(|| invalid("audio decoded no frames"))?;
    let channels = observed_channels.ok_or_else(|| invalid("audio decoded no frames"))?;
    if total_frames == 0 || peak_count == 0 {
        return Err(invalid("audio decoded no frames"));
    }
    let summary = WaveformProxySummary {
        source_sha256: source_sha256.to_ascii_lowercase(),
        sample_rate_hz,
        channels,
        frames_per_peak,
        total_frames,
        peak_count,
    };
    write_waveform_header(&mut output, &summary)?;
    output.flush()?;
    output.sync_all()?;
    Ok(summary)
}

pub fn read_waveform_page(
    proxy_path: impl AsRef<Path>,
    expected_source_sha256: &str,
    expected_sample_rate_hz: u32,
    expected_channels: u16,
    page_index: u64,
    page_size: usize,
) -> Result<WaveformPage> {
    if page_size == 0 || page_size > MAX_WAVEFORM_PAGE_SIZE {
        return Err(invalid("waveform page size is out of bounds"));
    }
    let mut input = File::open(proxy_path)?;
    let summary = read_waveform_header(&mut input)?;
    if !summary
        .source_sha256
        .eq_ignore_ascii_case(expected_source_sha256)
        || summary.sample_rate_hz != expected_sample_rate_hz
        || summary.channels != expected_channels
        || summary.frames_per_peak != WAVEFORM_FRAMES_PER_PEAK
    {
        return Err(invalid_proxy(
            "waveform proxy identity does not match the selected voice track",
        ));
    }

    let expected_len = WAVEFORM_HEADER_BYTES
        .checked_add(
            summary
                .peak_count
                .checked_mul(2)
                .ok_or_else(|| invalid_proxy("waveform proxy byte length overflow"))?,
        )
        .ok_or_else(|| invalid_proxy("waveform proxy byte length overflow"))?;
    if input.metadata()?.len() != expected_len {
        return Err(invalid_proxy(
            "waveform proxy byte length does not match its header",
        ));
    }

    let page_size_u64 =
        u64::try_from(page_size).map_err(|_| invalid("waveform page size is invalid"))?;
    let start_peak = page_index
        .checked_mul(page_size_u64)
        .ok_or_else(|| invalid("waveform page offset overflow"))?;
    if start_peak >= summary.peak_count && page_index != 0 {
        return Err(invalid("waveform page is outside the available proxy"));
    }
    let remaining = summary.peak_count.saturating_sub(start_peak);
    let count = remaining.min(page_size_u64);
    let offset = WAVEFORM_HEADER_BYTES
        .checked_add(
            start_peak
                .checked_mul(2)
                .ok_or_else(|| invalid("waveform page offset overflow"))?,
        )
        .ok_or_else(|| invalid("waveform page offset overflow"))?;
    input.seek(SeekFrom::Start(offset))?;

    let mut peaks = Vec::with_capacity(usize::try_from(count).unwrap_or(page_size));
    for _ in 0..count {
        peaks.push(f32::from(read_u16(&mut input)?) / f32::from(u16::MAX));
    }

    Ok(WaveformPage {
        schema: "motionwright.waveform-page.v1".into(),
        algorithm: "sample-peak-max-abs-v1".into(),
        source_sha256: summary.source_sha256,
        sample_rate_hz: summary.sample_rate_hz,
        channels: summary.channels,
        frames_per_peak: summary.frames_per_peak,
        total_frames: summary.total_frames,
        peak_count: summary.peak_count,
        page_index,
        page_size,
        start_peak,
        peaks,
        has_previous: page_index > 0,
        has_next: start_peak.saturating_add(count) < summary.peak_count,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn write_pcm16_wav(path: &Path, sample_rate: u32, channels: u16, samples: &[i16]) {
        assert_eq!(samples.len() % usize::from(channels), 0);
        let data_bytes = u32::try_from(samples.len() * 2).unwrap();
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
        for sample in samples {
            file.write_all(&sample.to_le_bytes()).unwrap();
        }
        file.sync_all().unwrap();
    }

    #[test]
    fn measures_decoded_frames_instead_of_guessing_from_file_size() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("voice.wav");
        let samples = vec![0_i16; 72_000];
        write_pcm16_wav(&path, 48_000, 1, &samples);
        let measured = measure_audio_file(&path).unwrap();
        assert_eq!(measured.sample_rate_hz, 48_000);
        assert_eq!(measured.channels, 1);
        assert_eq!(measured.decoded_frames, 72_000);
        assert_eq!(measured.duration, RationalTime::new(3, 2).unwrap());
    }

    #[test]
    fn waveform_proxy_is_paged_and_bound_to_source_measurements() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("voice.wav");
        let mut samples = vec![0_i16; 5_000];
        samples[100] = i16::MAX;
        samples[2_100] = i16::MAX / 2;
        samples[4_500] = i16::MAX / 4;
        write_pcm16_wav(&source, 8_000, 1, &samples);

        let source_sha256 = {
            let bytes = std::fs::read(&source).unwrap();
            hex::encode(Sha256::digest(bytes))
        };
        let proxy = temp.path().join("voice.mwpeak");
        let summary =
            generate_waveform_proxy(&source, &source_sha256, &proxy, WAVEFORM_FRAMES_PER_PEAK)
                .unwrap();
        assert_eq!(summary.total_frames, 5_000);
        assert_eq!(summary.peak_count, 3);

        let first = read_waveform_page(&proxy, &source_sha256, 8_000, 1, 0, 2).unwrap();
        assert_eq!(first.peaks.len(), 2);
        assert!(first.peaks[0] > 0.99);
        assert!((0.49..0.51).contains(&first.peaks[1]));
        assert!(first.has_next);
        assert!(!first.has_previous);

        let second = read_waveform_page(&proxy, &source_sha256, 8_000, 1, 1, 2).unwrap();
        assert_eq!(second.peaks.len(), 1);
        assert!((0.24..0.26).contains(&second.peaks[0]));
        assert!(second.has_previous);
        assert!(!second.has_next);
    }

    #[test]
    fn waveform_proxy_rejects_track_identity_mismatch() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("voice.wav");
        write_pcm16_wav(&source, 8_000, 1, &vec![0_i16; 4_096]);
        let source_sha256 = {
            let bytes = std::fs::read(&source).unwrap();
            hex::encode(Sha256::digest(bytes))
        };
        let proxy = temp.path().join("voice.mwpeak");
        generate_waveform_proxy(&source, &source_sha256, &proxy, WAVEFORM_FRAMES_PER_PEAK).unwrap();

        let error = read_waveform_page(&proxy, &source_sha256, 48_000, 1, 0, 32).unwrap_err();
        assert!(matches!(error, StorageError::InvalidDerivedCache(_)));
    }
}
