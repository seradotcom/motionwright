//! Closed Linux owner fixture for the real R5 MLT audio derivative route.
//!
//! The owner wrapper provisions the exact Store::blob_root() as MLT MediaRO and
//! a separate OutputRW, keeps the existing daemon/Host alive, and retains its
//! original command/process custody. Seed/readback never issue a native call.
//! This fixture does not grant Desktop authority or accept resampling quality.
use motionwright_domain::{Project, RevisionStamp};
use motionwright_native::production::{
    AudioDerivativeIntent, AudioDerivativeProfile, ProductionConnection, ProductionCoordinator,
    capture_production_file,
};
use motionwright_service::StudioService;
use motionwright_storage::{ApplyOutcome, BlobDescriptor, FileImportIntent};
use semwright_semantic_composition::strict_decode;
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    env,
    error::Error,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

type FixtureResult<T> = Result<T, Box<dyn Error>>;
const JSON_LIMIT: usize = 8 * 1024 * 1024;
const SEED_SCHEMA: &str = "motionwright.audio-derivative-owner-seed/1";
const RESULT_SCHEMA: &str = "motionwright.audio-derivative-owner-result/2";
const ADOPTION_SCHEMA: &str = "motionwright.audio-derivative-adoption/2";

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Seed {
    schema: String,
    profile: AudioDerivativeProfile,
    request_id: String,
    resource: String,
    media_root: PathBuf,
    project: Project,
    source_asset_id: Uuid,
    source_sha256: String,
    source_bytes: u64,
    history_sha256: String,
    native_dispatch_performed: bool,
    formal_acceptance: bool,
}

fn require(condition: bool, message: &'static str) -> FixtureResult<()> {
    if condition {
        Ok(())
    } else {
        Err(message.into())
    }
}

fn sha(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn decode<T: DeserializeOwned>(bytes: &[u8]) -> FixtureResult<T> {
    strict_decode(bytes).map_err(|error| format!("Original JSON is invalid: {error}").into())
}

fn profile_id(profile: AudioDerivativeProfile) -> &'static str {
    match profile {
        AudioDerivativeProfile::Stereo48ToMono16 => "pcm16-stereo48-to-mono16-v1",
        AudioDerivativeProfile::Mono16ToDualMono48 => "pcm16-mono16-to-dualmono48-v1",
    }
}

fn request_id(profile: AudioDerivativeProfile) -> &'static str {
    match profile {
        AudioDerivativeProfile::Stereo48ToMono16 => "audio-owner-stereo48-mono16-native-v1",
        AudioDerivativeProfile::Mono16ToDualMono48 => "audio-owner-mono16-dualmono48-native-v1",
    }
}

fn parse_profile(value: &str) -> FixtureResult<AudioDerivativeProfile> {
    match value {
        "pcm16-stereo48-to-mono16-v1" => Ok(AudioDerivativeProfile::Stereo48ToMono16),
        "pcm16-mono16-to-dualmono48-v1" => Ok(AudioDerivativeProfile::Mono16ToDualMono48),
        _ => Err("Audio fixture profile is not one of the two closed SDK profiles".into()),
    }
}

fn fresh_output(path: &Path) -> FixtureResult<()> {
    require(
        cfg!(unix),
        "Audio owner fixture source custody requires Unix",
    )?;
    let parent = path.parent().ok_or("Output has no owner parent")?;
    require(
        path.is_absolute()
            && !path.is_symlink()
            && !path.exists()
            && parent.is_dir()
            && fs::canonicalize(parent)?.as_path() == parent
            && path.components().all(|part| {
                !matches!(
                    part,
                    std::path::Component::ParentDir | std::path::Component::CurDir
                )
            }),
        "Audio fixture output requires a fresh normalized path in a canonical owner parent",
    )?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        require(
            fs::symlink_metadata(parent)?.permissions().mode() & 0o022 == 0,
            "Audio fixture parent must not be writable by another group or user",
        )?;
    }
    Ok(())
}

fn read_original(path: &Path, maximum: usize) -> FixtureResult<Vec<u8>> {
    let parent = path.parent().ok_or("Original has no owner parent")?;
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or("Original file name is not valid UTF-8")?;
    require(
        path.is_absolute() && fs::canonicalize(parent)?.as_path() == parent,
        "Original path must use a canonical owner parent",
    )?;
    Ok(capture_production_file(parent, name, maximum as u64)?
        .into_parts()
        .0)
}

fn new_bytes(path: &Path, bytes: &[u8]) -> FixtureResult<()> {
    fresh_output(path)?;
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let mut file = options.open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    drop(file);
    require(
        read_original(path, bytes.len())? == bytes,
        "New source custody readback changed",
    )
}

fn new_json(path: &Path, value: &impl Serialize) -> FixtureResult<()> {
    let bytes = serde_json::to_vec_pretty(value)?;
    require(
        bytes.len() <= JSON_LIMIT,
        "Audio fixture evidence exceeds its closed budget",
    )?;
    new_bytes(path, &bytes)
}

// First-party deterministic PCM fixture, not a resampler or decoder.
// Both profiles use one second and distinct channel content where applicable.
fn source_wave(profile: AudioDerivativeProfile) -> Vec<u8> {
    let (rate, channels) = match profile {
        AudioDerivativeProfile::Stereo48ToMono16 => (48_000_u32, 2_u16),
        AudioDerivativeProfile::Mono16ToDualMono48 => (16_000_u32, 1_u16),
    };
    let frames = rate;
    let data_bytes = frames * u32::from(channels) * 2;
    let mut bytes = Vec::with_capacity(data_bytes as usize + 44);
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + data_bytes).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&channels.to_le_bytes());
    bytes.extend_from_slice(&rate.to_le_bytes());
    bytes.extend_from_slice(&(rate * u32::from(channels) * 2).to_le_bytes());
    bytes.extend_from_slice(&(channels * 2).to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&data_bytes.to_le_bytes());
    for index in 0..frames {
        let left = ((index % 257) as i32 - 128) * 100;
        bytes.extend_from_slice(&(left as i16).to_le_bytes());
        if channels == 2 {
            let right = -left / 2 + (index % 113) as i32 * 20 - 1_120;
            bytes.extend_from_slice(&(right as i16).to_le_bytes());
        }
    }
    bytes
}

fn seed(
    profile: AudioDerivativeProfile,
    database: &Path,
    source: &Path,
    target: &Path,
) -> FixtureResult<Seed> {
    let mut paths = HashSet::new();
    for path in [database, source, target] {
        fresh_output(path)?;
        require(paths.insert(path), "Audio seed outputs must be distinct")?;
    }
    let parent = database.parent().ok_or("Database has no owner parent")?;
    require(
        fs::read_dir(parent)?.next().is_none(),
        "Audio seed requires a dedicated empty owner database directory",
    )?;
    let bytes = source_wave(profile);
    let source_sha256 = sha(&bytes);
    new_bytes(source, &bytes)?;
    let service = StudioService::open(database)?;
    let original = service.create_project("Original native audio derivative owner fixture")?;
    let outcome = service.import_file_verified(
        original.id,
        &RevisionStamp::from(&original),
        "audio-owner-source-import-v1",
        source,
        &BlobDescriptor {
            sha256: source_sha256.clone(),
            size_bytes: bytes.len() as u64,
        },
        FileImportIntent::Asset {
            name: format!("original-{}.wav", profile_id(profile)),
            media_type: "audio/wav".into(),
        },
    )?;
    let project = outcome.project;
    let [asset] = project.assets.as_slice() else {
        return Err("Audio seed must contain exactly the one imported source Asset".into());
    };
    require(
        asset.content_sha256.as_deref() == Some(source_sha256.as_str())
            && asset.media_type == "audio/wav"
            && project.audio == original.audio
            && !outcome.replayed,
        "Canonical source import changed the authored audio or source identity",
    )?;
    let media_root = service.store().lock().blob_root().to_path_buf();
    require(
        fs::canonicalize(&media_root)? == media_root,
        "Store CAS root is not canonical",
    )?;
    let history_sha256 = sha(&serde_json::to_vec(&service.history(project.id, 0, 256)?)?);
    let saved = Seed {
        schema: SEED_SCHEMA.into(),
        profile,
        request_id: request_id(profile).into(),
        resource: project.resource_key(),
        media_root,
        source_asset_id: asset.id,
        source_sha256,
        source_bytes: bytes.len() as u64,
        history_sha256,
        project,
        native_dispatch_performed: false,
        formal_acceptance: false,
    };
    admit_seed(&service, &saved, true)?;
    drop(service);
    let reopened = StudioService::open(database)?;
    admit_seed(&reopened, &saved, true)?;
    require(
        reopened
            .production_receipt_watermark(saved.project.id)?
            .count
            == 0,
        "Source seed unexpectedly created a native production receipt",
    )?;
    new_json(target, &saved)?;
    Ok(saved)
}

fn admit_seed(service: &StudioService, seed: &Seed, original_revision: bool) -> FixtureResult<()> {
    require(
        seed.schema == SEED_SCHEMA
            && seed.request_id == request_id(seed.profile)
            && seed.resource == seed.project.resource_key()
            && !seed.native_dispatch_performed
            && !seed.formal_acceptance,
        "Saved audio seed schema, request or source scope changed",
    )?;
    seed.project.validate()?;
    let bytes = source_wave(seed.profile);
    require(
        seed.source_sha256 == sha(&bytes)
            && seed.source_bytes == bytes.len() as u64
            && seed.media_root == service.store().lock().blob_root(),
        "Saved source does not select the deterministic fixture and exact Store CAS",
    )?;
    let [asset] = seed.project.assets.as_slice() else {
        return Err("Saved audio source roster changed".into());
    };
    require(
        asset.id == seed.source_asset_id
            && asset.media_type == "audio/wav"
            && asset.content_sha256.as_deref() == Some(seed.source_sha256.as_str()),
        "Saved source Asset differs from its CAS descriptor",
    )?;
    let store = service.store();
    {
        let locked = store.lock();
        let verified = locked.verified_blob_path(&seed.source_sha256)?;
        require(
            verified.strip_prefix(&seed.media_root).is_ok(),
            "Verified source is outside the exact Store CAS",
        )?;
        require(
            locked.read_blob(&seed.source_sha256, seed.source_bytes)? == bytes,
            "Original source CAS bytes changed",
        )?;
    }
    if original_revision {
        require(
            service.project(seed.project.id)? == seed.project
                && sha(&serde_json::to_vec(&service.history(
                    seed.project.id,
                    0,
                    256,
                )?)?)
                    == seed.history_sha256,
            "Saved original project or history changed before its one native use",
        )?;
    }
    Ok(())
}

fn observation<T: Serialize, E: std::fmt::Display>(result: Result<T, E>) -> Value {
    match result {
        Ok(value) => json!({"ok":true,"value":value}),
        Err(error) => json!({"ok":false,"error":error.to_string()}),
    }
}

fn adoption_readback(
    service: &StudioService,
    seed: &Seed,
    adopted: &Value,
) -> FixtureResult<Value> {
    require(
        adopted["schema"] == ADOPTION_SCHEMA,
        "Unknown adoption observation schema",
    )?;
    let outcome: ApplyOutcome = serde_json::from_value(adopted["outcome"].clone())?;
    let asset_id: Uuid = serde_json::from_value(adopted["asset_id"].clone())?;
    let observed = &adopted["observation"];
    require(
        observed["source"]["project_id"] == json!(seed.project.id)
            && observed["source"]["generation"] == json!(seed.project.generation)
            && observed["source"]["revision"] == seed.project.revision
            && observed["source"]["caller_request_id"] == seed.request_id
            && observed["source"]["source_asset_id"] == json!(seed.source_asset_id)
            && observed["source"]["source_sha256"] == seed.source_sha256
            && observed["source"]["profile"] == profile_id(seed.profile)
            && observed["native_execution_retried"] == false
            && observed["human_review_or_alignment"] == false
            && observed["resampling_fidelity_assessed"] == false,
        "Adoption observation does not identify this original source",
    )?;
    admit_seed(service, seed, false)?;
    require(
        outcome.project == service.project(seed.project.id)?
            && outcome.project.id == seed.project.id
            && outcome.project.generation == seed.project.generation
            && outcome.previous_revision == seed.project.revision
            && outcome.project.revision
                == seed
                    .project
                    .revision
                    .checked_add(1)
                    .ok_or("Source revision overflow")?
            && outcome.request_id == format!("{}:audio-derive-import", seed.request_id)
            && !outcome.replayed
            && outcome.project.audio == seed.project.audio
            && outcome.project.assets.len() == seed.project.assets.len() + 1
            && seed
                .project
                .assets
                .iter()
                .all(|old| outcome.project.assets.iter().any(|asset| asset == old)),
        "Reopened canonical import changed its scope, original Assets or authored audio",
    )?;
    let added = outcome
        .project
        .assets
        .iter()
        .find(|asset| asset.id == asset_id)
        .ok_or("Adopted Asset is absent after SQLite reopen")?;
    let expected_sha = observed["artifact"]["sha256"]
        .as_str()
        .ok_or("Adopted descriptor has no SHA")?;
    let expected_bytes = observed["artifact"]["bytes"]
        .as_u64()
        .ok_or("Adopted descriptor has no byte count")?;
    require(
        expected_bytes <= 40 * 1024 * 1024
            && added.id != seed.source_asset_id
            && added.media_type == "audio/wav"
            && added.content_sha256.as_deref() == Some(expected_sha),
        "Adopted Asset does not select the native artifact descriptor",
    )?;
    let target = service
        .store()
        .lock()
        .read_blob(expected_sha, expected_bytes)?;
    require(
        target.len() as u64 == expected_bytes && sha(&target) == expected_sha,
        "Adopted target CAS bytes changed after SQLite reopen",
    )?;
    let native_request = format!("{}:audio-derive-native", seed.request_id);
    let lineage_request = format!("{}:audio-derive-lineage", seed.request_id);
    let native = service
        .latest_production_receipt(seed.project.id, &native_request)?
        .ok_or("Original native receipt is absent after reopen")?;
    let lineage = service
        .latest_production_receipt(seed.project.id, &lineage_request)?
        .ok_or("Original lineage receipt is absent after reopen")?;
    require(
        json!(native.id) == observed["native_receipt_id"]
            && json!(lineage.id) == observed["lineage_receipt_id"]
            && native.command == "driver.mlt-video.audio.derive"
            && native.stage == "completed"
            && lineage.command == "motionwright.audio-derivative.observe"
            && lineage.stage == "observed"
            && native.generation == seed.project.generation
            && native.revision == seed.project.revision
            && lineage.generation == seed.project.generation
            && lineage.revision == seed.project.revision
            && sha(&serde_json::to_vec(&native)?) == observed["native_receipt_sha256"],
        "Reopened receipt identity changed after its original adoption",
    )?;
    for key in [
        "source",
        "source_lineage_sha256",
        "target",
        "artifact",
        "recipe_sha256",
        "runtime_sha256",
        "original_source",
        "original_output",
        "process_originals",
        "native_receipt_id",
        "native_receipt_sha256",
        "import_request_id",
        "import_intent",
        "native_execution_retried",
        "human_review_or_alignment",
        "resampling_fidelity_assessed",
    ] {
        require(
            observed.get(key).is_some() && lineage.payload.get(key) == observed.get(key),
            "Retained adoption differs from its original stored lineage",
        )?;
    }
    let import_intent: FileImportIntent =
        serde_json::from_value(observed["import_intent"].clone())?;
    let recovered = service
        .recover_file_import(
            seed.project.id,
            &RevisionStamp::from(&outcome.project),
            &outcome.request_id,
            &import_intent,
        )?
        .ok_or("Canonical import receipt is absent after reopen")?;
    let mut historical = outcome.clone();
    historical.replayed = true;
    require(
        recovered == historical,
        "Read-only import recovery differs from the completed adoption",
    )?;
    require(
        (adopted["source_lease_release_confirmed"] == true
            && adopted["source_lease_release_error"].is_null())
            || (adopted["source_lease_release_confirmed"] == false
                && adopted["source_lease_release_error"].is_object()),
        "Adoption cleanup observation is inconsistent",
    )?;
    Ok(json!({
        "project_id":outcome.project.id,"generation":outcome.project.generation,"revision":outcome.project.revision,
        "source_asset_id":seed.source_asset_id,"source_sha256":seed.source_sha256,
        "adopted_asset_id":asset_id,"adopted_sha256":expected_sha,"adopted_bytes":expected_bytes,
        "native_receipt_id":native.id,"lineage_receipt_id":lineage.id,"import_request_id":outcome.request_id,
        "original_audio_unchanged":true,"original_cas_preserved":true,"target_cas_verified":true,
        "source_lease_release_confirmed":adopted["source_lease_release_confirmed"],
        "source_lease_release_error":adopted["source_lease_release_error"],
        "native_execution_retried":false,"formal_acceptance":false
    }))
}

// Preserve a returned error and any confirmed canonical import independently
// of derivative qualification. This JSON is private evidence, never an Adopted
// constructor or authority to repeat an effect.
fn original_error_result(
    original: &semwright_native_sdk::Error,
    confirmed_import: Option<&ApplyOutcome>,
) -> Value {
    json!({
        "kind":"original-error",
        "error":original,
        "confirmed_import":confirmed_import,
        "derivative_adoption_accepted":false,
        "redispatch_permitted":false
    })
}

// Canonical receipt recovery only. The caller separately supplies the closed
// fixture/lineage scope; matching this historical result does not qualify a
// derivative or assert that a native render ran.
fn recover_confirmed_import(
    service: &StudioService,
    source: &Project,
    request_id: &str,
    intent: &FileImportIntent,
    outcome: &ApplyOutcome,
) -> FixtureResult<Value> {
    require(
        matches!(intent, FileImportIntent::Asset { .. })
            && outcome.request_id == request_id
            && outcome.project.id == source.id
            && outcome.project.generation == source.generation
            && outcome.project.resource_key() == source.resource_key()
            && outcome.previous_revision == source.revision,
        "Confirmed import does not belong to the original Asset request",
    )?;
    let recovered = service
        .recover_file_import(
            source.id,
            &RevisionStamp::from(&outcome.project),
            request_id,
            intent,
        )?
        .ok_or("Confirmed canonical import receipt is absent after reopen")?;
    let mut historical = outcome.clone();
    historical.replayed = true;
    require(
        recovered == historical,
        "Read-only recovery differs from the original confirmed import",
    )?;
    Ok(json!({
        "project_id":outcome.project.id,
        "generation":outcome.project.generation,
        "revision":outcome.project.revision,
        "import_request_id":outcome.request_id,
        "confirmed_import_sha256":sha(&serde_json::to_vec(outcome)?),
        "canonical_import_recovery_read_only":true,
        "derivative_adoption_accepted":false,
        "native_dispatch_performed":false,
        "formal_acceptance":false
    }))
}

fn confirmed_import_readback(
    service: &StudioService,
    seed: &Seed,
    outcome: &ApplyOutcome,
) -> FixtureResult<Value> {
    admit_seed(service, seed, false)?;
    let import_request = format!("{}:audio-derive-import", seed.request_id);
    let lineage = service
        .latest_production_receipt(
            seed.project.id,
            &format!("{}:audio-derive-lineage", seed.request_id),
        )?
        .ok_or("Original derivative lineage is absent after confirmed import")?;
    require(
        lineage.command == "motionwright.audio-derivative.observe"
            && lineage.stage == "observed"
            && lineage.generation == seed.project.generation
            && lineage.revision == seed.project.revision
            && lineage.payload["import_request_id"] == import_request
            && lineage.payload["source"]["project_id"] == json!(seed.project.id)
            && lineage.payload["source"]["generation"] == json!(seed.project.generation)
            && lineage.payload["source"]["revision"] == seed.project.revision
            && lineage.payload["source"]["caller_request_id"] == seed.request_id
            && lineage.payload["source"]["source_asset_id"] == json!(seed.source_asset_id)
            && lineage.payload["source"]["source_sha256"] == seed.source_sha256
            && lineage.payload["source"]["profile"] == profile_id(seed.profile),
        "Confirmed import lineage no longer identifies this original source",
    )?;
    let intent: FileImportIntent =
        serde_json::from_value(lineage.payload["import_intent"].clone())?;
    recover_confirmed_import(service, &seed.project, &import_request, &intent, outcome)
}

async fn derive(
    database: &Path,
    connection_path: &Path,
    owner_media_root: &Path,
    owner_output_root: &Path,
    seed_path: &Path,
    target: &Path,
) -> FixtureResult<()> {
    require(
        cfg!(target_os = "linux") && env::var("GITHUB_ACTIONS").as_deref() == Ok("true"),
        "Real audio derivation requires the disposable Linux GH owner",
    )?;
    fresh_output(target)?;
    let seed_bytes = read_original(seed_path, JSON_LIMIT)?;
    let seed: Seed = decode(&seed_bytes)?;
    let service = StudioService::open(database)?;
    admit_seed(&service, &seed, true)?;
    let connection = ProductionConnection::load(connection_path)?;
    require(
        owner_media_root == seed.media_root
            && owner_output_root == connection.output_root
            && connection.resource == seed.resource,
        "Owner arguments must use the exact seeded CAS and configured output/resource",
    )?;
    let coordinator = ProductionCoordinator::new(service.clone(), connection)?;
    let original = coordinator
        .derive_audio_asset_with_owner_roots(
            seed.project.id,
            &RevisionStamp::from(&seed.project),
            &seed.request_id,
            AudioDerivativeIntent {
                source_asset_id: seed.source_asset_id,
                profile: seed.profile,
            },
            owner_media_root,
            owner_output_root,
        )
        .await;
    // Retain the original result before any post-operation observation can fail.
    // No branch below performs a second derive or import.
    let native_result = match &original {
        Ok(adopted) => json!({"kind":"adopted","value":adopted}),
        Err(error) => {
            let mut retained = original_error_result(error.original(), error.confirmed_import());
            retained["original_pair_observations"] = error.original_pair_observations();
            retained
        }
    };
    let receipts = observation(service.production_receipts(seed.project.id, 64));
    let receipt_custody = coordinator.private_receipt_custody_snapshot();
    drop(coordinator);
    drop(service);
    let reopen = StudioService::open(database);
    let reopened = match (&original, reopen) {
        (Ok(_), Ok(fresh)) => {
            observation(adoption_readback(&fresh, &seed, &native_result["value"]))
        }
        (Err(error), Ok(fresh)) => match error.confirmed_import() {
            Some(outcome) => observation(confirmed_import_readback(&fresh, &seed, outcome)),
            None => observation(fresh.project(seed.project.id)),
        },
        (_, Err(error)) => json!({"ok":false,"error":error.to_string()}),
    };
    let caller_status = match &original {
        Err(error) if error.confirmed_import().is_some() => {
            "confirmed-import-qualification-failure"
        }
        Err(_) => "original-operation-error",
        Ok(adopted)
            if adopted.source_lease_release_confirmed()
                && reopened["ok"] == true
                && receipts["ok"] == true =>
        {
            "completed"
        }
        Ok(_) => "postcompletion-observation-failure",
    };
    let record = json!({
        "schema":RESULT_SCHEMA,"caller_status":caller_status,"request_id":seed.request_id,"profile":seed.profile,
        "seed_sha256":sha(&seed_bytes),
        "source_project_id":seed.project.id,"source_generation":seed.project.generation,"source_revision":seed.project.revision,
        "owner_media_root":owner_media_root,"owner_output_root":owner_output_root,
        "original_result":native_result,"original_receipts":receipts,
        "receipt_inventory_limit":64,"receipt_inventory_complete_claimed":false,
        "private_receipt_custody":receipt_custody,"sqlite_reopen":reopened,
        "entrypoint_invocations_in_this_caller":1,"native_execution_retried":false,
        "runtime_pins_verified_by_this_fixture":false,"resampling_fidelity_accepted":false,"formal_acceptance":false
    });
    let custody = new_json(target, &record);
    match original {
        Err(primary) => {
            if custody.is_err() {
                let _ = writeln!(
                    std::io::stderr().lock(),
                    "audio fixture secondary: original result custody was not confirmed; inspect the owner's original receipts"
                );
            }
            Err(primary.into())
        }
        Ok(adopted) => {
            if let Err(error) = custody {
                // This diagnostic is only an attempted fallback, not credited
                // evidence persistence. The already-confirmed import stays put.
                let _ = writeln!(
                    std::io::stderr().lock(),
                    "{}",
                    json!({
                        "status":"postcompletion-observation-failure",
                        "adopted_asset_id":adopted.asset_id(),
                        "import_request_id":adopted.import_outcome().request_id,
                        "source_lease_release_confirmed":adopted.source_lease_release_confirmed(),
                        "source_lease_release_error":adopted.source_lease_release_error(),
                        "result_file_custody_confirmed":false,"redispatch_permitted":false
                    })
                );
                return Err(error);
            }
            require(
                record["original_receipts"]["ok"] == true,
                "Adoption completed and was retained; receipt inventory needs inspection, never redispatch",
            )?;
            require(
                adopted.source_lease_release_confirmed(),
                "Adoption completed and was retained; CAS lease cleanup needs owner inspection, never redispatch",
            )?;
            require(
                record["sqlite_reopen"]["ok"] == true,
                "Adoption completed and was retained; its read-only reopen observation needs inspection, never redispatch",
            )
        }
    }
}

fn readback(
    database: &Path,
    seed_path: &Path,
    original_result: &Path,
    target: &Path,
) -> FixtureResult<()> {
    fresh_output(target)?;
    let seed_bytes = read_original(seed_path, JSON_LIMIT)?;
    let seed: Seed = decode(&seed_bytes)?;
    let original_bytes = read_original(original_result, JSON_LIMIT)?;
    let result: Value = decode(&original_bytes)?;
    require(
        result["schema"] == RESULT_SCHEMA
            && result["request_id"] == seed.request_id
            && result["profile"] == profile_id(seed.profile)
            && result["seed_sha256"] == sha(&seed_bytes)
            && result["native_execution_retried"] == false,
        "Original result does not identify this closed source request",
    )?;
    let service = StudioService::open(database)?;
    let (result_kind, observed) = match result["original_result"]["kind"].as_str() {
        Some("adopted") => (
            "adopted",
            adoption_readback(&service, &seed, &result["original_result"]["value"])?,
        ),
        Some("original-error")
            if result["caller_status"] == "confirmed-import-qualification-failure"
                && result["original_result"]["error"]["outcome_known"] == true
                && result["original_result"]["derivative_adoption_accepted"] == false
                && result["original_result"]["redispatch_permitted"] == false =>
        {
            let outcome: ApplyOutcome =
                serde_json::from_value(result["original_result"]["confirmed_import"].clone())?;
            (
                "confirmed-import-qualification-failure",
                confirmed_import_readback(&service, &seed, &outcome)?,
            )
        }
        _ => {
            return Err(
                "Original result has no closed adopted or confirmed-import readback".into(),
            );
        }
    };
    new_json(
        target,
        &json!({
            "schema":"motionwright.audio-derivative-owner-readback/2",
            "original_result_sha256":sha(&original_bytes),"seed_sha256":sha(&seed_bytes),
            "result_kind":result_kind,
            "observed":observed,"native_dispatch_performed":false,"formal_acceptance":false
        }),
    )
}

fn usage() -> ! {
    eprintln!(
        "usage: native-audio-derivative-e2e seed PROFILE FRESH_DATABASE NEW_SOURCE_WAV NEW_SEED_JSON |          derive EXISTING_DATABASE OWNER_CONNECTION OWNER_MEDIA_ROOT OWNER_OUTPUT_ROOT SEED_JSON NEW_RESULT_JSON |          readback EXISTING_DATABASE SEED_JSON ORIGINAL_RESULT_JSON NEW_READBACK_JSON"
    );
    std::process::exit(2);
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> FixtureResult<()> {
    let args: Vec<_> = env::args_os().skip(1).collect();
    match args.as_slice() {
        [command, profile, database, source, target] if command == "seed" => {
            let profile = parse_profile(profile.to_str().ok_or("Profile is not UTF-8")?)?;
            let saved = seed(
                profile,
                Path::new(database),
                Path::new(source),
                Path::new(target),
            )?;
            println!(
                "{}",
                serde_json::to_string(&json!({
                    "schema":SEED_SCHEMA,"resource":saved.resource,"media_root":saved.media_root,
                    "request_id":saved.request_id,"source_sha256":saved.source_sha256,"native_dispatch_performed":false
                }))?
            );
        }
        [command, database, connection, media, output, saved, target] if command == "derive" => {
            derive(
                Path::new(database),
                Path::new(connection),
                Path::new(media),
                Path::new(output),
                Path::new(saved),
                Path::new(target),
            )
            .await?;
        }
        [command, database, saved, result, target] if command == "readback" => {
            readback(
                Path::new(database),
                Path::new(saved),
                Path::new(result),
                Path::new(target),
            )?;
        }
        _ => usage(),
    }
    Ok(())
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    fn owned_paths() -> (tempfile::TempDir, PathBuf, PathBuf, PathBuf) {
        use std::os::unix::fs::PermissionsExt;
        let directory = tempfile::tempdir().unwrap();
        let parent = directory.path().canonicalize().unwrap();
        let state = parent.join("state");
        fs::create_dir(&state).unwrap();
        fs::set_permissions(&state, fs::Permissions::from_mode(0o700)).unwrap();
        (
            directory,
            state.join("studio.sqlite3"),
            parent.join("original.wav"),
            parent.join("seed.json"),
        )
    }

    #[test]
    fn both_closed_audio_sources_reopen_the_exact_store_cas_without_native_dispatch() {
        for profile in [
            AudioDerivativeProfile::Stereo48ToMono16,
            AudioDerivativeProfile::Mono16ToDualMono48,
        ] {
            let (_directory, database, wave, json_path) = owned_paths();
            let saved = seed(profile, &database, &wave, &json_path).unwrap();
            let read: Seed = decode(&read_original(&json_path, JSON_LIMIT).unwrap()).unwrap();
            let service = StudioService::open(&database).unwrap();
            admit_seed(&service, &read, true).unwrap();
            assert_eq!(read.project, saved.project);
            assert_eq!(read.profile, profile);
            assert_eq!(read.media_root, service.store().lock().blob_root());
            assert_eq!(
                read_original(&wave, 1024 * 1024).unwrap(),
                source_wave(profile)
            );
            assert_eq!(
                service
                    .production_receipt_watermark(saved.project.id)
                    .unwrap()
                    .count,
                0
            );
            assert_eq!(
                service.project(saved.project.id).unwrap().audio,
                saved.project.audio
            );
        }
    }

    #[test]
    fn audio_seed_preflight_rejects_existing_or_noncanonical_outputs_before_database_creation() {
        let (directory, database, source, target) = owned_paths();
        fs::write(&target, b"retained existing bytes").unwrap();
        assert!(
            seed(
                AudioDerivativeProfile::Stereo48ToMono16,
                &database,
                &source,
                &target
            )
            .is_err()
        );
        assert!(!database.exists());
        assert!(!source.exists());
        assert_eq!(fs::read(&target).unwrap(), b"retained existing bytes");
        let parent = directory.path().canonicalize().unwrap();
        let canonical_target = parent.join("new-seed.json");
        let noncanonical_source = parent.join("state").join("..").join("noncanonical.wav");
        assert!(
            seed(
                AudioDerivativeProfile::Stereo48ToMono16,
                &database,
                &noncanonical_source,
                &canonical_target
            )
            .is_err()
        );
        assert!(!database.exists());
        assert!(!canonical_target.exists());
        assert!(!parent.join("noncanonical.wav").exists());
    }

    #[test]
    fn audio_seed_rejects_changed_saved_scope_without_native_or_cas_mutation() {
        let (_directory, database, source, target) = owned_paths();
        let mut saved = seed(
            AudioDerivativeProfile::Mono16ToDualMono48,
            &database,
            &source,
            &target,
        )
        .unwrap();
        let service = StudioService::open(&database).unwrap();
        let original = service.project(saved.project.id).unwrap();
        let digest = saved.source_sha256.clone();
        saved.source_sha256 = "0".repeat(64);
        assert!(admit_seed(&service, &saved, true).is_err());
        saved.source_sha256 = digest;
        saved.request_id.push_str("-changed");
        assert!(admit_seed(&service, &saved, true).is_err());
        assert_eq!(service.project(original.id).unwrap(), original);
        assert_eq!(
            service
                .production_receipt_watermark(original.id)
                .unwrap()
                .count,
            0
        );
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&saved.source_sha256, saved.source_bytes)
                .unwrap(),
            source_wave(saved.profile)
        );
    }

    #[test]
    fn audio_owner_confirmed_import_error_is_serialized_without_adoption() {
        let (_directory, database, source, saved_path) = owned_paths();
        let seed = seed(
            AudioDerivativeProfile::Mono16ToDualMono48,
            &database,
            &source,
            &saved_path,
        )
        .unwrap();
        let service = StudioService::open(&database).unwrap();
        let request = format!("{}:audio-derive-import", seed.request_id);
        let intent = FileImportIntent::Asset {
            name: "source-only-confirmed-import.wav".into(),
            media_type: "audio/wav".into(),
        };
        // A single real target import, using fixture bytes solely to exercise
        // canonical custody. No native derivation or fake native lineage exists.
        let imported = service
            .import_file_verified(
                seed.project.id,
                &RevisionStamp::from(&seed.project),
                &request,
                &source,
                &BlobDescriptor {
                    sha256: seed.source_sha256.clone(),
                    size_bytes: seed.source_bytes,
                },
                intent.clone(),
            )
            .unwrap();
        let current = service.project(seed.project.id).unwrap();
        let history =
            serde_json::to_value(service.history(seed.project.id, 0, 256).unwrap()).unwrap();
        let recovered = service
            .recover_file_import(
                seed.project.id,
                &RevisionStamp::from(&imported.project),
                &request,
                &intent,
            )
            .unwrap()
            .unwrap();
        assert!(recovered.replayed);
        let original = semwright_native_sdk::Error::new(
            semwright_native_sdk::ErrorCode::BackendFailed,
            "source-only qualification rejected a confirmed historical import",
        );
        let record = original_error_result(&original, Some(&recovered));
        let retained: Value = decode(&serde_json::to_vec(&record).unwrap()).unwrap();
        assert_eq!(retained["kind"], "original-error");
        assert_eq!(retained["error"], serde_json::to_value(&original).unwrap());
        assert_eq!(
            retained["confirmed_import"],
            serde_json::to_value(&recovered).unwrap()
        );
        assert!(retained.get("value").is_none());
        assert_eq!(retained["derivative_adoption_accepted"], false);
        assert_eq!(retained["redispatch_permitted"], false);
        let read = recover_confirmed_import(&service, &seed.project, &request, &intent, &recovered)
            .unwrap();
        assert_eq!(read["canonical_import_recovery_read_only"], true);
        assert_eq!(read["derivative_adoption_accepted"], false);
        assert_eq!(read["native_dispatch_performed"], false);
        assert_eq!(read["formal_acceptance"], false);
        // Canonical recovery alone must not manufacture the native lineage
        // needed by the separate diagnostic result readback.
        assert!(confirmed_import_readback(&service, &seed, &recovered).is_err());
        assert_eq!(service.project(seed.project.id).unwrap(), current);
        assert_eq!(
            serde_json::to_value(service.history(seed.project.id, 0, 256).unwrap()).unwrap(),
            history
        );
        assert_eq!(
            service
                .production_receipt_watermark(seed.project.id)
                .unwrap()
                .count,
            0
        );
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&seed.source_sha256, seed.source_bytes)
                .unwrap(),
            source_wave(seed.profile)
        );
    }
}
