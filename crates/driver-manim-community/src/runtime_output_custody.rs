//! Private, bounded custody of an already-completed Host runtime-tool failure.
//! This module starts no process and grants no native or filesystem authority.

use semwright_driver_sdk::ToolExecutionOutput;
use semwright_types::NativeDiagnostic;
#[cfg(target_os = "linux")]
use semwright_types::{NativeFailurePhase, NativeFailureReason};
#[cfg(target_os = "linux")]
use sha2::{Digest, Sha256};
use std::path::Path;

const MAX_STREAM_BYTES: usize = 256 * 1024;
#[cfg(target_os = "linux")]
const MAX_RECEIPT_BYTES: usize = 4096;
#[cfg(target_os = "linux")]
const MAX_DIAGNOSTIC_TAIL_BYTES: usize = 8192;
type CustodyResult<T> = std::result::Result<T, &'static str>;

fn within_bounds(output: &ToolExecutionOutput) -> bool {
    output.stdout.len() <= MAX_STREAM_BYTES && output.stderr.len() <= MAX_STREAM_BYTES
}

/// Existing bounded diagnostic DATA only. It never carries plaintext or a path.
#[cfg(target_os = "linux")]
pub(super) fn diagnostic(output: &ToolExecutionOutput) -> Option<NativeDiagnostic> {
    if !within_bounds(output) {
        return None;
    }
    let tail = &output.stderr[output
        .stderr
        .len()
        .saturating_sub(MAX_DIAGNOSTIC_TAIL_BYTES)..];
    let diagnostic = NativeDiagnostic {
        phase: NativeFailurePhase::NativeExchange,
        reason: NativeFailureReason::ChildExit,
        exit_code: (0..=255)
            .contains(&output.exit_code)
            .then_some(output.exit_code),
        signal: None,
        stderr_tail_sha256: hex::encode(Sha256::digest(tail)),
        stderr_tail_bytes: tail.len() as u16,
        stderr_tail_truncated: output.stderr.len() > tail.len(),
    };
    diagnostic.is_valid().then_some(diagnostic)
}

#[cfg(not(target_os = "linux"))]
pub(super) fn diagnostic(_output: &ToolExecutionOutput) -> Option<NativeDiagnostic> {
    // SDK5c2 does not supply an observed stderr stream on these platforms.
    None
}

fn directory_name(job_ref: &str, source_file: &str, plan_sha256: &str) -> CustodyResult<String> {
    let token = source_file
        .strip_prefix("mw_")
        .and_then(|name| name.strip_suffix(".py"))
        .ok_or("runtime_output_identity")?;
    if token.len() != 32
        || !token
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        || job_ref != format!("manim-{token}")
        || plan_sha256.len() != 64
        || !plan_sha256
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("runtime_output_identity");
    }
    Ok(format!("mw_{token}.runtime-custody"))
}

pub(super) fn retain(
    work_root: &Path,
    job_ref: &str,
    source_file: &str,
    plan_sha256: &str,
    output: &ToolExecutionOutput,
) -> CustodyResult<()> {
    if !within_bounds(output) {
        return Err("runtime_output_bounds");
    }
    let name = directory_name(job_ref, source_file, plan_sha256)?;
    #[cfg(target_os = "linux")]
    {
        linux::retain(work_root, &name, job_ref, source_file, plan_sha256, output)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (work_root, name);
        Err("runtime_output_custody_unsupported")
    }
}

#[cfg(target_os = "linux")]
mod linux {
    use super::*;
    use serde_json::json;
    use std::{
        fs::{self, DirBuilder, File, Metadata, OpenOptions},
        io::{Read, Seek, SeekFrom, Write},
        os::{
            fd::AsRawFd,
            unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
        },
        path::PathBuf,
    };

    const FILE_NAMES: [&str; 3] = ["stdout.bin", "stderr.bin", "receipt.json"];

    #[derive(Clone, Copy, PartialEq, Eq)]
    struct Identity {
        device: u64,
        inode: u64,
        mode: u32,
        uid: u32,
        gid: u32,
    }

    fn identity(metadata: &Metadata) -> Identity {
        Identity {
            device: metadata.dev(),
            inode: metadata.ino(),
            mode: metadata.mode(),
            uid: metadata.uid(),
            gid: metadata.gid(),
        }
    }

    struct Directory {
        path: PathBuf,
        file: File,
        identity: Identity,
    }

    struct OriginalFile {
        name: &'static str,
        file: File,
        identity: Identity,
    }

    impl Directory {
        fn open(path: &Path, anchored: &Path) -> CustodyResult<Self> {
            let before = fs::symlink_metadata(path).map_err(|_| "runtime_output_private_root")?;
            if !path.is_absolute()
                || !before.is_dir()
                || before.file_type().is_symlink()
                || (before.mode() & 0o7777) != 0o700
                || fs::canonicalize(path).map_err(|_| "runtime_output_private_root")? != path
            {
                return Err("runtime_output_private_root");
            }
            let file = OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
                .open(anchored)
                .map_err(|_| "runtime_output_private_root")?;
            let opened = file.metadata().map_err(|_| "runtime_output_private_root")?;
            if !opened.is_dir() || identity(&opened) != identity(&before) {
                return Err("runtime_output_directory_changed");
            }
            let directory = Self {
                path: path.to_owned(),
                file,
                identity: identity(&before),
            };
            directory.verify()?;
            Ok(directory)
        }

        fn verify(&self) -> CustodyResult<()> {
            let path =
                fs::symlink_metadata(&self.path).map_err(|_| "runtime_output_directory_changed")?;
            let opened = self
                .file
                .metadata()
                .map_err(|_| "runtime_output_directory_changed")?;
            if !path.is_dir()
                || path.file_type().is_symlink()
                || identity(&path) != self.identity
                || identity(&opened) != self.identity
                || fs::canonicalize(&self.path).map_err(|_| "runtime_output_directory_changed")?
                    != self.path
            {
                return Err("runtime_output_directory_changed");
            }
            Ok(())
        }

        fn anchored(&self, name: &str) -> PathBuf {
            // The original directory descriptor remains held throughout capture.
            PathBuf::from(format!("/proc/self/fd/{}/{}", self.file.as_raw_fd(), name))
        }

        fn create_child(&self, name: &str) -> CustodyResult<Self> {
            self.verify()?;
            DirBuilder::new()
                .mode(0o700)
                .create(self.anchored(name))
                .map_err(|_| "runtime_output_reservation")?;
            self.verify()?;
            let child = Self::open(&self.path.join(name), &self.anchored(name))?;
            if child.identity.uid != self.identity.uid {
                return Err("runtime_output_private_root");
            }
            self.verify()?;
            Ok(child)
        }

        fn create(&self, name: &'static str) -> CustodyResult<OriginalFile> {
            self.verify()?;
            if !FILE_NAMES.contains(&name) {
                return Err("runtime_output_identity");
            }
            let file = OpenOptions::new()
                .read(true)
                .write(true)
                .create_new(true)
                .mode(0o600)
                .custom_flags(libc::O_NOFOLLOW)
                .open(self.anchored(name))
                .map_err(|_| "runtime_output_reservation")?;
            let opened = file.metadata().map_err(|_| "runtime_output_file_changed")?;
            let original = OriginalFile {
                name,
                file,
                identity: identity(&opened),
            };
            self.verify_member(&original)?;
            Ok(original)
        }

        fn verify_member(&self, original: &OriginalFile) -> CustodyResult<()> {
            self.verify()?;
            let path = fs::symlink_metadata(self.anchored(original.name))
                .map_err(|_| "runtime_output_file_changed")?;
            let opened = original
                .file
                .metadata()
                .map_err(|_| "runtime_output_file_changed")?;
            if !path.is_file()
                || path.file_type().is_symlink()
                || (path.mode() & 0o7777) != 0o600
                || path.uid() != self.identity.uid
                || path.nlink() != 1
                || opened.nlink() != 1
                || identity(&path) != original.identity
                || identity(&opened) != original.identity
                || path.len() != opened.len()
            {
                return Err("runtime_output_file_changed");
            }
            Ok(())
        }

        fn write(
            &self,
            original: &mut OriginalFile,
            bytes: &[u8],
            maximum: usize,
        ) -> CustodyResult<()> {
            if bytes.len() > maximum {
                return Err("runtime_output_bounds");
            }
            self.verify_member(original)?;
            if original
                .file
                .metadata()
                .map_err(|_| "runtime_output_file_changed")?
                .len()
                != 0
            {
                return Err("runtime_output_file_changed");
            }
            original
                .file
                .write_all(bytes)
                .map_err(|_| "runtime_output_write")?;
            original
                .file
                .sync_all()
                .map_err(|_| "runtime_output_sync")?;
            self.verify_member(original)?;
            let before = original
                .file
                .metadata()
                .map_err(|_| "runtime_output_readback")?;
            if before.len() != bytes.len() as u64 {
                return Err("runtime_output_readback");
            }
            original
                .file
                .seek(SeekFrom::Start(0))
                .map_err(|_| "runtime_output_readback")?;
            let mut readback = Vec::with_capacity(bytes.len());
            (&mut original.file)
                .take(bytes.len() as u64 + 1)
                .read_to_end(&mut readback)
                .map_err(|_| "runtime_output_readback")?;
            let after = original
                .file
                .metadata()
                .map_err(|_| "runtime_output_readback")?;
            self.verify_member(original)?;
            if readback != bytes
                || before.len() != after.len()
                || before.mtime() != after.mtime()
                || before.mtime_nsec() != after.mtime_nsec()
                || before.ctime() != after.ctime()
                || before.ctime_nsec() != after.ctime_nsec()
            {
                return Err("runtime_output_readback");
            }
            Ok(())
        }
    }

    pub(super) fn retain(
        work_root: &Path,
        name: &str,
        job_ref: &str,
        source_file: &str,
        plan_sha256: &str,
        output: &ToolExecutionOutput,
    ) -> CustodyResult<()> {
        let work = Directory::open(work_root, work_root)?;
        let directory = work.create_child(name)?;
        let mut stdout = directory.create("stdout.bin")?;
        directory.write(&mut stdout, &output.stdout, MAX_STREAM_BYTES)?;
        work.verify()?;
        let mut stderr = directory.create("stderr.bin")?;
        directory.write(&mut stderr, &output.stderr, MAX_STREAM_BYTES)?;
        work.verify()?;
        let mut receipt = serde_json::to_vec(&json!({
            "schema": "motionwright.manim.runtime-output/1",
            "runtime_tool": "manim-runner",
            "configured_manim_version": super::super::MANIM_VERSION,
            "job_ref": job_ref,
            "source_file": source_file,
            "plan_sha256": plan_sha256,
            "process_exit_code": output.exit_code,
            "stdout": {
                "file": "stdout.bin",
                "bytes": output.stdout.len(),
                "sha256": hex::encode(Sha256::digest(&output.stdout)),
            },
            "stderr": {
                "file": "stderr.bin",
                "bytes": output.stderr.len(),
                "sha256": hex::encode(Sha256::digest(&output.stderr)),
            },
        }))
        .map_err(|_| "runtime_output_receipt")?;
        receipt.push(b'\n');
        let mut original = directory.create("receipt.json")?;
        directory.write(&mut original, &receipt, MAX_RECEIPT_BYTES)?;
        directory
            .file
            .sync_all()
            .map_err(|_| "runtime_output_sync")?;
        work.file.sync_all().map_err(|_| "runtime_output_sync")?;
        directory.verify()?;
        work.verify()?;
        Ok(())
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::os::unix::fs::{PermissionsExt, symlink};

        const JOB: &str = "manim-0123456789abcdef0123456789abcdef";
        const SOURCE: &str = "mw_0123456789abcdef0123456789abcdef.py";
        const NAME: &str = "mw_0123456789abcdef0123456789abcdef.runtime-custody";

        fn private_root() -> tempfile::TempDir {
            let root = tempfile::tempdir().unwrap();
            fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
            root
        }

        fn original_output() -> ToolExecutionOutput {
            ToolExecutionOutput {
                exit_code: 2,
                stdout: b"private stdout\0\xff".to_vec(),
                stderr: b"private stderr\0\xfe".to_vec(),
            }
        }

        #[test]
        fn runtime_output_files_are_private_exclusive_and_byte_exact() {
            let root = private_root();
            let original = original_output();
            super::super::retain(root.path(), JOB, SOURCE, &"a".repeat(64), &original).unwrap();
            let directory = root.path().join(NAME);
            assert_eq!(fs::metadata(&directory).unwrap().mode() & 0o7777, 0o700);
            assert_eq!(
                fs::read(directory.join("stdout.bin")).unwrap(),
                original.stdout
            );
            assert_eq!(
                fs::read(directory.join("stderr.bin")).unwrap(),
                original.stderr
            );
            for name in FILE_NAMES {
                let metadata = fs::symlink_metadata(directory.join(name)).unwrap();
                assert!(metadata.is_file());
                assert_eq!(metadata.mode() & 0o7777, 0o600);
                assert_eq!(metadata.nlink(), 1);
            }
            let raw = fs::read(directory.join("receipt.json")).unwrap();
            assert!(raw.len() <= MAX_RECEIPT_BYTES);
            let receipt: serde_json::Value = serde_json::from_slice(&raw).unwrap();
            assert_eq!(receipt["job_ref"], JOB);
            assert_eq!(receipt["source_file"], SOURCE);
            assert_eq!(receipt["process_exit_code"], 2);
            assert_eq!(
                receipt["stderr"]["sha256"],
                hex::encode(Sha256::digest(&original.stderr))
            );
        }

        #[test]
        fn runtime_output_channel_limits_are_exact_and_checked_before_writes() {
            let root = private_root();
            let output = ToolExecutionOutput {
                exit_code: 2,
                stdout: vec![0; MAX_STREAM_BYTES],
                stderr: vec![255; MAX_STREAM_BYTES],
            };
            super::super::retain(root.path(), JOB, SOURCE, &"a".repeat(64), &output).unwrap();
            assert_eq!(
                fs::read(root.path().join(NAME).join("stderr.bin")).unwrap(),
                output.stderr
            );
            for stderr in [false, true] {
                let root = private_root();
                let mut output = original_output();
                if stderr {
                    output.stderr = vec![0; MAX_STREAM_BYTES + 1];
                } else {
                    output.stdout = vec![0; MAX_STREAM_BYTES + 1];
                }
                assert_eq!(
                    super::super::retain(root.path(), JOB, SOURCE, &"a".repeat(64), &output),
                    Err("runtime_output_bounds")
                );
                assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
            }
        }

        #[test]
        fn runtime_output_existing_directory_is_not_reused_or_overwritten() {
            let root = private_root();
            let first = original_output();
            super::super::retain(root.path(), JOB, SOURCE, &"a".repeat(64), &first).unwrap();
            let before =
                FILE_NAMES.map(|name| fs::read(root.path().join(NAME).join(name)).unwrap());
            let mut changed = original_output();
            changed.stderr = b"replacement".to_vec();
            assert_eq!(
                super::super::retain(root.path(), JOB, SOURCE, &"a".repeat(64), &changed),
                Err("runtime_output_reservation")
            );
            for (name, bytes) in FILE_NAMES.into_iter().zip(before) {
                assert_eq!(fs::read(root.path().join(NAME).join(name)).unwrap(), bytes);
            }
        }

        #[test]
        fn runtime_output_rejects_symlink_and_insecure_root_without_writes() {
            let root = private_root();
            fs::set_permissions(root.path(), fs::Permissions::from_mode(0o755)).unwrap();
            assert_eq!(
                super::super::retain(
                    root.path(),
                    JOB,
                    SOURCE,
                    &"a".repeat(64),
                    &original_output()
                ),
                Err("runtime_output_private_root")
            );
            assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
            fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
            let outside = private_root();
            let alias = root.path().join(NAME);
            symlink(outside.path(), &alias).unwrap();
            assert_eq!(
                super::super::retain(
                    root.path(),
                    JOB,
                    SOURCE,
                    &"a".repeat(64),
                    &original_output()
                ),
                Err("runtime_output_reservation")
            );
            assert_eq!(fs::read_dir(outside.path()).unwrap().count(), 0);
            let aliases = private_root();
            let linked_root = aliases.path().join("work");
            symlink(outside.path(), &linked_root).unwrap();
            assert_eq!(
                super::super::retain(
                    &linked_root,
                    JOB,
                    SOURCE,
                    &"a".repeat(64),
                    &original_output()
                ),
                Err("runtime_output_private_root")
            );
        }

        #[test]
        fn runtime_output_detects_hardlink_and_directory_replacement() {
            let root = private_root();
            let work = Directory::open(root.path(), root.path()).unwrap();
            let directory = work.create_child(NAME).unwrap();
            let mut file = directory.create("stderr.bin").unwrap();
            fs::hard_link(
                directory.path.join("stderr.bin"),
                root.path().join("hardlink.bin"),
            )
            .unwrap();
            assert_eq!(
                directory.write(&mut file, b"must not be written", MAX_STREAM_BYTES),
                Err("runtime_output_file_changed")
            );
            assert!(
                fs::read(root.path().join("hardlink.bin"))
                    .unwrap()
                    .is_empty()
            );
            fs::rename(&directory.path, root.path().join("original-directory")).unwrap();
            DirBuilder::new()
                .mode(0o700)
                .create(&directory.path)
                .unwrap();
            assert_eq!(directory.verify(), Err("runtime_output_directory_changed"));
        }

        #[test]
        fn runtime_output_rejects_invalid_identity_before_creating_a_directory() {
            let root = private_root();
            for (job, source, plan) in [
                ("manim-other", SOURCE, "a".repeat(64)),
                (JOB, "../source.py", "a".repeat(64)),
                (JOB, SOURCE, "A".repeat(64)),
            ] {
                assert_eq!(
                    super::super::retain(root.path(), job, source, &plan, &original_output()),
                    Err("runtime_output_identity")
                );
                assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
            }
        }
    }
}

#[cfg(all(test, target_os = "linux"))]
mod diagnostic_tests {
    use super::*;

    #[test]
    fn runtime_failure_diagnostic_exposes_only_a_bounded_tail_digest() {
        let output = ToolExecutionOutput {
            exit_code: 2,
            stdout: b"private stdout sentinel".to_vec(),
            stderr: vec![255; MAX_DIAGNOSTIC_TAIL_BYTES + 1],
        };
        let diagnostic = diagnostic(&output).unwrap();
        assert!(diagnostic.is_valid());
        assert!(matches!(
            diagnostic.phase,
            NativeFailurePhase::NativeExchange
        ));
        assert!(matches!(diagnostic.reason, NativeFailureReason::ChildExit));
        assert_eq!(diagnostic.exit_code, Some(2));
        assert_eq!(diagnostic.stderr_tail_bytes, 8192);
        assert!(diagnostic.stderr_tail_truncated);
        assert_eq!(
            diagnostic.stderr_tail_sha256,
            hex::encode(Sha256::digest(&output.stderr[1..]))
        );
        let public = serde_json::to_value(diagnostic).unwrap();
        assert!(public.get("stdout").is_none());
        assert!(public.get("stderr").is_none());
        assert!(public.get("path").is_none());
        assert!(!public.to_string().contains("private stdout sentinel"));
    }
}

#[cfg(all(test, not(target_os = "linux")))]
mod unsupported_tests {
    use super::*;

    #[test]
    fn unsupported_custody_does_not_claim_an_observed_stderr_stream() {
        let output = ToolExecutionOutput {
            exit_code: 2,
            stdout: Vec::new(),
            stderr: Vec::new(),
        };
        assert!(diagnostic(&output).is_none());
        assert_eq!(
            retain(
                Path::new("must-not-be-opened"),
                "manim-0123456789abcdef0123456789abcdef",
                "mw_0123456789abcdef0123456789abcdef.py",
                &"a".repeat(64),
                &output,
            ),
            Err("runtime_output_custody_unsupported")
        );
    }
}
