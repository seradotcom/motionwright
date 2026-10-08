use motionwright_domain::RevisionStamp;
use motionwright_service::{StudioService, VoiceImportMetadata};
use serde::Serialize;
use std::{
    fs::File,
    io::{Seek, SeekFrom, Write},
    time::Instant,
};

const SAMPLE_RATE: u32 = 8_000;
const CHANNELS: u16 = 1;
const DURATION_SECONDS: u32 = 60 * 60;
const PAGE_SIZE: usize = 256;

#[derive(Debug, Serialize)]
struct WaveformBenchReport {
    schema: &'static str,
    duration_seconds: u32,
    sample_rate_hz: u32,
    channels: u16,
    source_bytes: u64,
    frames: u64,
    peak_count: u64,
    proxy_bytes: u64,
    page_size: usize,
    first_page_peaks: usize,
    distant_page_peaks: usize,
    first_page_millis: u128,
    cached_distant_page_micros: u128,
    algorithm: String,
    source_sha256: String,
}

fn write_sparse_pcm16_wav(path: &std::path::Path, sample_rate: u32, frames: u32) {
    let data_bytes = frames
        .checked_mul(u32::from(CHANNELS))
        .and_then(|value| value.checked_mul(2))
        .expect("benchmark WAV byte count");
    let mut file = File::create(path).expect("create benchmark WAV");
    file.write_all(b"RIFF").unwrap();
    file.write_all(&(36 + data_bytes).to_le_bytes()).unwrap();
    file.write_all(b"WAVEfmt ").unwrap();
    file.write_all(&16_u32.to_le_bytes()).unwrap();
    file.write_all(&1_u16.to_le_bytes()).unwrap();
    file.write_all(&CHANNELS.to_le_bytes()).unwrap();
    file.write_all(&sample_rate.to_le_bytes()).unwrap();
    file.write_all(&(sample_rate * u32::from(CHANNELS) * 2).to_le_bytes())
        .unwrap();
    file.write_all(&(CHANNELS * 2).to_le_bytes()).unwrap();
    file.write_all(&16_u16.to_le_bytes()).unwrap();
    file.write_all(b"data").unwrap();
    file.write_all(&data_bytes.to_le_bytes()).unwrap();

    let final_len = 44_u64 + u64::from(data_bytes);
    file.seek(SeekFrom::Start(final_len - 1)).unwrap();
    file.write_all(&[0]).unwrap();
    file.sync_all().unwrap();
}

fn main() {
    let temp = tempfile::tempdir().expect("benchmark tempdir");
    let db = temp.path().join("studio.sqlite3");
    let source = temp.path().join("one-hour-voice.wav");
    let frames = SAMPLE_RATE * DURATION_SECONDS;
    write_sparse_pcm16_wav(&source, SAMPLE_RATE, frames);

    let service = StudioService::open(&db).expect("open service");
    let initial = service
        .create_project("Long audio paging")
        .expect("create project");
    let imported = service
        .import_voice_file(
            initial.id,
            &RevisionStamp::from(&initial),
            "waveform-long-audio",
            &source,
            VoiceImportMetadata {
                name: "one-hour-voice.wav".into(),
                media_type: "audio/wav".into(),
                label: "One hour measured take".into(),
            },
        )
        .expect("import measured voice")
        .project;
    let track = imported.audio.voice_tracks.first().expect("imported track");

    let first_started = Instant::now();
    let first = service
        .waveform_page(imported.id, track.id, 0, PAGE_SIZE)
        .expect("first waveform page");
    let first_page_millis = first_started.elapsed().as_millis();

    assert_eq!(first.total_frames, u64::from(frames));
    assert_eq!(first.sample_rate_hz, SAMPLE_RATE);
    assert_eq!(first.channels, CHANNELS);
    assert_eq!(first.page_size, PAGE_SIZE);
    assert_eq!(first.peaks.len(), PAGE_SIZE);
    assert!(first.peaks.iter().all(|peak| *peak == 0.0));
    assert!(first.has_next);

    let page_count = first.peak_count.div_ceil(PAGE_SIZE as u64);
    let distant_page_index = page_count.saturating_sub(1);
    let cached_started = Instant::now();
    let distant = service
        .waveform_page(imported.id, track.id, distant_page_index, PAGE_SIZE)
        .expect("cached distant waveform page");
    let cached_distant_page_micros = cached_started.elapsed().as_micros();

    assert_eq!(distant.page_index, distant_page_index);
    assert!(distant.peaks.len() <= PAGE_SIZE);
    assert!(!distant.peaks.is_empty());
    assert!(distant.has_previous);
    assert!(!distant.has_next);
    assert!(distant.peaks.iter().all(|peak| *peak == 0.0));

    let proxy_bytes = 72_u64 + first.peak_count * 2;
    let report = WaveformBenchReport {
        schema: "motionwright.waveform-performance.v1",
        duration_seconds: DURATION_SECONDS,
        sample_rate_hz: SAMPLE_RATE,
        channels: CHANNELS,
        source_bytes: std::fs::metadata(&source).unwrap().len(),
        frames: u64::from(frames),
        peak_count: first.peak_count,
        proxy_bytes,
        page_size: PAGE_SIZE,
        first_page_peaks: first.peaks.len(),
        distant_page_peaks: distant.peaks.len(),
        first_page_millis,
        cached_distant_page_micros,
        algorithm: first.algorithm,
        source_sha256: first.source_sha256,
    };
    println!("{}", serde_json::to_string_pretty(&report).unwrap());
}
