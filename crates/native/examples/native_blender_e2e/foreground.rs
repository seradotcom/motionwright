//! Private phase channel for the existing product-cycle CLI.
//!
//! This exchanges only TTY coordination records with the original parent.
//! It never reads /dev/tty, answers a Broker challenge, or grants Native consent.

use motionwright_native::production::{BlenderOriginalPhase, BlenderProductCycleHook};
use semwright_native_sdk::{Error, ErrorCode, Result};

pub(crate) const FD_ENV: &str = "MOTIONWRIGHT_BLENDER_PHASE_FD";

fn admit_command(command: &str, channel_present: bool) -> Result<()> {
    if channel_present && command != "product-cycle" {
        return Err(Error::invalid(
            "Blender phase channel is restricted to product-cycle",
        ));
    }
    Ok(())
}

pub(crate) fn check_command(command: &str) -> Result<()> {
    admit_command(command, std::env::var_os(FD_ENV).is_some())
}

pub(crate) fn from_environment(attempt_sha256: &str) -> Result<Option<ForegroundChannel>> {
    match std::env::var_os(FD_ENV) {
        None => Ok(None),
        Some(value) => ForegroundChannel::adopt(&value, attempt_sha256).map(Some),
    }
}

#[cfg(any(target_os = "linux", test))]
const MAX_MESSAGE: usize = 256;

#[cfg(any(target_os = "linux", test))]
fn descriptor(value: &std::ffi::OsStr) -> Result<i32> {
    let value = value
        .to_str()
        .ok_or_else(|| Error::invalid("Blender phase FD must be decimal ASCII"))?;
    if value.is_empty() || value.len() > 10 || !value.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(Error::invalid("Blender phase FD must be decimal ASCII"));
    }
    let fd: i32 = value
        .parse()
        .map_err(|_| Error::invalid("Blender phase FD is outside its descriptor range"))?;
    if fd <= 2 || fd.to_string() != value {
        return Err(Error::invalid(
            "Blender phase FD must be canonical and greater than two",
        ));
    }
    Ok(fd)
}

#[cfg(any(target_os = "linux", test))]
fn request_line(
    digest: &str,
    sequence: u8,
    phase: BlenderOriginalPhase,
    entering: bool,
) -> Result<Vec<u8>> {
    use BlenderOriginalPhase::{CreateOriginal, EditOriginal, VerifyEdited};
    const ORDER: [(BlenderOriginalPhase, bool); 6] = [
        (CreateOriginal, true),
        (CreateOriginal, false),
        (EditOriginal, true),
        (EditOriginal, false),
        (VerifyEdited, true),
        (VerifyEdited, false),
    ];
    if digest.len() != 64
        || !digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        || ORDER.get(usize::from(sequence)) != Some(&(phase, entering))
    {
        return Err(Error::invalid(
            "Blender phase digest or transition is outside the closed sequence",
        ));
    }
    let phase = match phase {
        CreateOriginal => "create_original",
        EditOriginal => "edit_original",
        VerifyEdited => "verify_edited",
    };
    let direction = if entering { "enter" } else { "leave" };
    let message = format!("MWBLENDER-TTY/1 {digest} {sequence} {direction} {phase}\n").into_bytes();
    if message.len() > MAX_MESSAGE {
        return Err(Error::invalid(
            "Blender phase request exceeds its fixed byte budget",
        ));
    }
    Ok(message)
}

#[cfg(any(target_os = "linux", test))]
fn verify_ack(digest: &str, sequence: u8, bytes: &[u8]) -> Result<()> {
    let expected = format!("MWBLENDER-TTY/1 {digest} {sequence} ack\n");
    if bytes.len() > MAX_MESSAGE || bytes != expected.as_bytes() {
        return Err(Error::new(
            ErrorCode::PluginProtocolError,
            "Blender phase acknowledgement differs from the one requested transition",
        ));
    }
    Ok(())
}

#[cfg(target_os = "linux")]
mod platform {
    use super::*;
    use std::{
        io::{Read, Write},
        mem::{MaybeUninit, size_of},
        os::fd::{AsRawFd, FromRawFd, OwnedFd},
        os::unix::net::UnixStream,
        time::{Duration, Instant},
    };

    const IO_LIMIT: Duration = Duration::from_secs(3);

    pub(crate) struct ForegroundChannel {
        stream: UnixStream,
        attempt_sha256: String,
        parent_pid: libc::pid_t,
        next_sequence: u8,
        poisoned: bool,
    }

    fn system_failure(message: &'static str) -> Error {
        Error::new(ErrorCode::Unavailable, message)
    }

    fn io_failure(error: std::io::Error, message: &'static str) -> Error {
        Error::new(
            if matches!(
                error.kind(),
                std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock
            ) {
                ErrorCode::Timeout
            } else {
                ErrorCode::Unavailable
            },
            message,
        )
    }

    fn anonymous_endpoint(fd: i32, peer: bool) -> Result<()> {
        let mut address: libc::sockaddr_un = unsafe { std::mem::zeroed() };
        let mut length = size_of::<libc::sockaddr_un>() as libc::socklen_t;
        let result = unsafe {
            if peer {
                libc::getpeername(
                    fd,
                    (&mut address as *mut libc::sockaddr_un).cast(),
                    &mut length,
                )
            } else {
                libc::getsockname(
                    fd,
                    (&mut address as *mut libc::sockaddr_un).cast(),
                    &mut length,
                )
            }
        };
        if result != 0
            || address.sun_family != libc::AF_UNIX as libc::sa_family_t
            || length as usize != size_of::<libc::sa_family_t>()
        {
            return Err(Error::invalid(
                "Blender phase channel must be an anonymous connected Unix socketpair",
            ));
        }
        Ok(())
    }

    fn validate_socket(fd: i32, parent_pid: libc::pid_t) -> Result<()> {
        if parent_pid <= 1 {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Blender phase parent is unavailable",
            ));
        }
        let mut metadata = MaybeUninit::<libc::stat>::uninit();
        if unsafe { libc::fstat(fd, metadata.as_mut_ptr()) } != 0 {
            return Err(system_failure(
                "Blender phase descriptor cannot be inspected",
            ));
        }
        let metadata = unsafe { metadata.assume_init() };
        if metadata.st_mode & libc::S_IFMT != libc::S_IFSOCK {
            return Err(Error::invalid("Blender phase descriptor is not a socket"));
        }
        let mut kind: libc::c_int = 0;
        let mut kind_length = size_of::<libc::c_int>() as libc::socklen_t;
        if unsafe {
            libc::getsockopt(
                fd,
                libc::SOL_SOCKET,
                libc::SO_TYPE,
                (&mut kind as *mut libc::c_int).cast(),
                &mut kind_length,
            )
        } != 0
            || kind_length as usize != size_of::<libc::c_int>()
            || kind != libc::SOCK_STREAM
        {
            return Err(Error::invalid("Blender phase channel must use SOCK_STREAM"));
        }
        anonymous_endpoint(fd, false)?;
        anonymous_endpoint(fd, true)?;
        let mut peer = MaybeUninit::<libc::ucred>::uninit();
        let mut peer_length = size_of::<libc::ucred>() as libc::socklen_t;
        if unsafe {
            libc::getsockopt(
                fd,
                libc::SOL_SOCKET,
                libc::SO_PEERCRED,
                peer.as_mut_ptr().cast(),
                &mut peer_length,
            )
        } != 0
            || peer_length as usize != size_of::<libc::ucred>()
        {
            return Err(system_failure(
                "Blender phase parent credentials are unavailable",
            ));
        }
        let peer = unsafe { peer.assume_init() };
        if peer.pid != parent_pid
            || peer.uid != unsafe { libc::geteuid() }
            || peer.gid != unsafe { libc::getegid() }
        {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Blender phase peer is not the current parent with the current uid and gid",
            ));
        }
        Ok(())
    }

    fn adopt_descriptor(fd: i32, parent_pid: libc::pid_t) -> Result<UnixStream> {
        let flags = unsafe { libc::fcntl(fd, libc::F_GETFD) };
        if fd <= 2 || flags < 0 {
            return Err(Error::invalid(
                "Blender phase descriptor is not an open inherited descriptor",
            ));
        }
        // Ownership transfers exactly once; every later rejection closes it.
        let owned = unsafe { OwnedFd::from_raw_fd(fd) };
        if unsafe { libc::fcntl(owned.as_raw_fd(), libc::F_SETFD, flags | libc::FD_CLOEXEC) } < 0 {
            return Err(system_failure(
                "Blender phase descriptor could not retain close-on-exec",
            ));
        }
        let retained_flags = unsafe { libc::fcntl(owned.as_raw_fd(), libc::F_GETFD) };
        if retained_flags < 0 || retained_flags & libc::FD_CLOEXEC == 0 {
            return Err(system_failure(
                "Blender phase descriptor could not verify close-on-exec",
            ));
        }
        validate_socket(owned.as_raw_fd(), parent_pid)?;
        let stream = UnixStream::from(owned);
        stream
            .set_nonblocking(false)
            .map_err(|error| io_failure(error, "Blender phase socket mode could not be bounded"))?;
        Ok(stream)
    }

    fn remaining(deadline: Instant) -> Result<Duration> {
        deadline
            .checked_duration_since(Instant::now())
            .filter(|left| !left.is_zero())
            .ok_or_else(|| Error::new(ErrorCode::Timeout, "Blender phase channel deadline expired"))
    }

    fn write_request(stream: &mut UnixStream, bytes: &[u8]) -> Result<()> {
        if bytes.is_empty() || bytes.len() > MAX_MESSAGE {
            return Err(Error::invalid("Blender phase request byte budget"));
        }
        let deadline = Instant::now() + IO_LIMIT;
        let mut written = 0;
        while written < bytes.len() {
            stream
                .set_write_timeout(Some(remaining(deadline)?))
                .map_err(|error| io_failure(error, "Blender phase write deadline failed"))?;
            match stream.write(&bytes[written..]) {
                Ok(0) => return Err(system_failure("Blender phase request writer closed")),
                Ok(count) => written += count,
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(error) => return Err(io_failure(error, "Blender phase request write failed")),
            }
        }
        Ok(())
    }

    fn read_ack(stream: &mut UnixStream, digest: &str, sequence: u8) -> Result<()> {
        let deadline = Instant::now() + IO_LIMIT;
        let mut bytes = Vec::with_capacity(MAX_MESSAGE);
        while bytes.len() < MAX_MESSAGE {
            stream
                .set_read_timeout(Some(remaining(deadline)?))
                .map_err(|error| io_failure(error, "Blender phase read deadline failed"))?;
            let mut one = [0u8; 1];
            match stream.read(&mut one) {
                Ok(0) => return Err(system_failure("Blender phase acknowledgement reached EOF")),
                Ok(_) => {
                    bytes.push(one[0]);
                    if one[0] == b'\n' {
                        return verify_ack(digest, sequence, &bytes);
                    }
                }
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(error) => {
                    return Err(io_failure(
                        error,
                        "Blender phase acknowledgement read failed",
                    ));
                }
            }
        }
        Err(Error::new(
            ErrorCode::PluginProtocolError,
            "Blender phase acknowledgement exceeds its byte budget",
        ))
    }

    fn reject_unsolicited(stream: &UnixStream, final_ack: bool) -> Result<()> {
        let mut one = [0u8; 1];
        let count = unsafe {
            libc::recv(
                stream.as_raw_fd(),
                one.as_mut_ptr().cast(),
                one.len(),
                libc::MSG_PEEK | libc::MSG_DONTWAIT,
            )
        };
        if count > 0 {
            return Err(Error::new(
                ErrorCode::PluginProtocolError,
                "Blender phase channel contains unsolicited bytes",
            ));
        }
        if count == 0 {
            return if final_ack {
                Ok(())
            } else {
                Err(system_failure(
                    "Blender phase channel closed before its final acknowledgement",
                ))
            };
        }
        let error = std::io::Error::last_os_error();
        if error.kind() != std::io::ErrorKind::WouldBlock {
            return Err(io_failure(
                error,
                "Blender phase channel status is unavailable",
            ));
        }
        Ok(())
    }

    impl ForegroundChannel {
        pub(super) fn adopt(value: &std::ffi::OsStr, digest: &str) -> Result<Self> {
            let fd = descriptor(value)?;
            let parent_pid = unsafe { libc::getppid() };
            let stream = adopt_descriptor(fd, parent_pid)?;
            request_line(digest, 0, BlenderOriginalPhase::CreateOriginal, true)?;
            Ok(Self {
                stream,
                attempt_sha256: digest.to_owned(),
                parent_pid,
                next_sequence: 0,
                poisoned: false,
            })
        }

        fn exchange(&mut self, phase: BlenderOriginalPhase, entering: bool) -> Result<()> {
            if self.poisoned {
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "Blender phase channel cannot retry a failed exchange",
                ));
            }
            self.poisoned = true;
            let bytes = request_line(&self.attempt_sha256, self.next_sequence, phase, entering)?;
            if unsafe { libc::getppid() } != self.parent_pid {
                return Err(Error::new(
                    ErrorCode::PermissionDenied,
                    "Blender phase parent changed",
                ));
            }
            validate_socket(self.stream.as_raw_fd(), self.parent_pid)?;
            reject_unsolicited(&self.stream, false)?;
            // Partial syscalls continue only their remaining bytes. No message
            // or Native operation is resent after any terminal I/O failure.
            write_request(&mut self.stream, &bytes)?;
            read_ack(&mut self.stream, &self.attempt_sha256, self.next_sequence)?;
            reject_unsolicited(&self.stream, self.next_sequence == 5)?;
            self.next_sequence += 1;
            self.poisoned = false;
            Ok(())
        }
    }

    impl BlenderProductCycleHook for ForegroundChannel {
        fn enter(&mut self, phase: BlenderOriginalPhase) -> Result<()> {
            self.exchange(phase, true)
        }
        fn leave(&mut self, phase: BlenderOriginalPhase) -> Result<()> {
            self.exchange(phase, false)
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::os::fd::IntoRawFd;

        #[test]
        fn original_anonymous_stream_identity_and_cloexec_are_checked_without_native() {
            let (client, _peer) = UnixStream::pair().unwrap();
            let fd = client.into_raw_fd();
            assert_eq!(unsafe { libc::fcntl(fd, libc::F_SETFD, 0) }, 0);
            let owned = adopt_descriptor(fd, unsafe { libc::getpid() }).unwrap();
            assert_ne!(
                unsafe { libc::fcntl(owned.as_raw_fd(), libc::F_GETFD) } & libc::FD_CLOEXEC,
                0
            );
            assert!(validate_socket(owned.as_raw_fd(), unsafe { libc::getpid() } + 1).is_err());
        }

        #[test]
        fn original_non_socket_and_non_stream_descriptors_are_rejected() {
            let file = tempfile::tempfile().unwrap();
            assert!(adopt_descriptor(file.into_raw_fd(), unsafe { libc::getpid() }).is_err());
            let (client, _peer) = std::os::unix::net::UnixDatagram::pair().unwrap();
            assert!(adopt_descriptor(client.into_raw_fd(), unsafe { libc::getpid() }).is_err());
        }

        #[test]
        fn original_bounded_ack_rejects_eof_overflow_and_unsolicited_data() {
            let digest = "a".repeat(64);
            let (mut client, mut peer) = UnixStream::pair().unwrap();
            peer.write_all(format!("MWBLENDER-TTY/1 {digest} 0 ack\n").as_bytes())
                .unwrap();
            read_ack(&mut client, &digest, 0).unwrap();
            peer.write_all(b"x").unwrap();
            assert!(reject_unsolicited(&client, false).is_err());

            let (mut client, mut peer) = UnixStream::pair().unwrap();
            peer.write_all(&[b'x'; MAX_MESSAGE]).unwrap();
            assert!(read_ack(&mut client, &digest, 0).is_err());
            drop(peer);
            let (mut client, peer) = UnixStream::pair().unwrap();
            drop(peer);
            assert!(read_ack(&mut client, &digest, 0).is_err());
        }

        #[test]
        fn original_one_request_writer_preserves_exact_protocol_bytes() {
            let digest = "a".repeat(64);
            let expected =
                request_line(&digest, 0, BlenderOriginalPhase::CreateOriginal, true).unwrap();
            let (mut client, mut peer) = UnixStream::pair().unwrap();
            write_request(&mut client, &expected).unwrap();
            let mut actual = vec![0; expected.len()];
            peer.read_exact(&mut actual).unwrap();
            assert_eq!(actual, expected);
            assert!(remaining(Instant::now() - Duration::from_secs(1)).is_err());
        }
    }
}

#[cfg(not(target_os = "linux"))]
mod platform {
    use super::*;

    pub(crate) struct ForegroundChannel;

    impl ForegroundChannel {
        pub(super) fn adopt(_value: &std::ffi::OsStr, _digest: &str) -> Result<Self> {
            Err(Error::new(
                ErrorCode::Unsupported,
                "Blender foreground phase channel requires Linux",
            ))
        }
    }

    impl BlenderProductCycleHook for ForegroundChannel {
        fn enter(&mut self, _phase: BlenderOriginalPhase) -> Result<()> {
            Err(Error::new(
                ErrorCode::Unsupported,
                "Blender foreground phase channel requires Linux",
            ))
        }
        fn leave(&mut self, _phase: BlenderOriginalPhase) -> Result<()> {
            Err(Error::new(
                ErrorCode::Unsupported,
                "Blender foreground phase channel requires Linux",
            ))
        }
    }
}

pub(crate) use platform::ForegroundChannel;

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;

    #[test]
    fn original_fd_environment_is_exact_and_restricted_to_product_cycle() {
        for command in [
            "seed",
            "original",
            "seed-product",
            "recipe-product",
            "realize",
        ] {
            assert!(admit_command(command, true).is_err());
            assert!(admit_command(command, false).is_ok());
        }
        assert!(admit_command("product-cycle", true).is_ok());
        assert_eq!(descriptor(OsStr::new("3")).unwrap(), 3);
        for value in [
            "",
            "0",
            "1",
            "2",
            "-3",
            "+3",
            "03",
            " 3",
            "3 ",
            "3\n",
            "abc",
            "2147483648",
        ] {
            assert!(descriptor(OsStr::new(value)).is_err(), "{value:?}");
        }
    }

    #[test]
    fn original_phase_messages_and_acknowledgements_have_one_closed_order() {
        use BlenderOriginalPhase::{CreateOriginal, EditOriginal, VerifyEdited};
        let digest = "a".repeat(64);
        let phases = [
            CreateOriginal,
            CreateOriginal,
            EditOriginal,
            EditOriginal,
            VerifyEdited,
            VerifyEdited,
        ];
        for (sequence, phase) in phases.into_iter().enumerate() {
            let sequence = sequence as u8;
            let request = request_line(&digest, sequence, phase, sequence % 2 == 0).unwrap();
            assert!(request.len() <= MAX_MESSAGE && request.ends_with(b"\n"));
            assert!(request_line(&digest, sequence, phase, sequence % 2 != 0).is_err());
            let ack = format!("MWBLENDER-TTY/1 {digest} {sequence} ack\n");
            verify_ack(&digest, sequence, ack.as_bytes()).unwrap();
            assert!(verify_ack(&digest, sequence, format!("{ack}extra").as_bytes()).is_err());
            assert!(
                verify_ack(
                    &digest,
                    sequence,
                    format!("MWBLENDER-TTY/1 {digest} {sequence} ack create_original\n").as_bytes()
                )
                .is_err()
            );
        }
        assert!(request_line(&digest, 6, CreateOriginal, true).is_err());
        assert!(request_line(&digest, 0, VerifyEdited, true).is_err());
        assert!(request_line(&"A".repeat(64), 0, CreateOriginal, true).is_err());
        assert!(
            verify_ack(
                &digest,
                0,
                format!("MWBLENDER-TTY/1 {digest} 1 ack\n").as_bytes()
            )
            .is_err()
        );
    }
}
