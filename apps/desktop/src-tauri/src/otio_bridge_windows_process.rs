//! Bounded Windows supervision for an already admitted OTIO runtime.
use std::ffi::{OsStr, c_void};
use std::fs::File;
use std::io;
use std::mem::{size_of, zeroed};
use std::os::windows::ffi::OsStrExt;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::path::Path;
use std::ptr::{null, null_mut};
use std::thread;
use std::time::{Duration, Instant};

use windows_sys::Win32::Foundation::{
    DUPLICATE_SAME_ACCESS, DuplicateHandle, HANDLE, WAIT_OBJECT_0, WAIT_TIMEOUT,
};
use windows_sys::Win32::System::JobObjects::{
    CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectBasicAccountingInformation,
    JobObjectExtendedLimitInformation, QueryInformationJobObject, SetInformationJobObject,
    TerminateJobObject,
};
use windows_sys::Win32::System::Threading::{
    CREATE_NO_WINDOW, CREATE_UNICODE_ENVIRONMENT, CreateProcessW, DeleteProcThreadAttributeList,
    EXTENDED_STARTUPINFO_PRESENT, GetCurrentProcess, GetExitCodeProcess,
    InitializeProcThreadAttributeList, LPPROC_THREAD_ATTRIBUTE_LIST,
    PROC_THREAD_ATTRIBUTE_HANDLE_LIST, PROC_THREAD_ATTRIBUTE_JOB_LIST, PROCESS_INFORMATION,
    STARTF_USESTDHANDLES, STARTUPINFOEXW, UpdateProcThreadAttribute, WaitForSingleObject,
};

const POLL: Duration = Duration::from_millis(20);
const CLEANUP: Duration = Duration::from_secs(5);

/// Failure class for a single, non-replayed inspection.
#[derive(Debug)]
pub(super) enum BridgeProcessError {
    Start(io::Error),
    Exit(u32),
    TimedOut,
    Uncertain,
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}

fn wide(value: &OsStr) -> io::Result<Vec<u16>> {
    let units: Vec<u16> = value.encode_wide().collect();
    if units.contains(&0) {
        return Err(invalid("Windows bridge input contains an interior NUL"));
    }
    Ok(units)
}

fn append_argument(command: &mut Vec<u16>, argument: &[u16]) {
    const QUOTE: u16 = b'"' as u16;
    const SLASH: u16 = b'\\' as u16;
    command.push(QUOTE);
    let mut slashes = 0;
    for &unit in argument {
        if unit == SLASH {
            slashes += 1;
            continue;
        }
        let escaped = if unit == QUOTE {
            slashes * 2 + 1
        } else {
            slashes
        };
        command.extend(std::iter::repeat_n(SLASH, escaped));
        command.push(unit);
        slashes = 0;
    }
    command.extend(std::iter::repeat_n(SLASH, slashes * 2));
    command.push(QUOTE);
}

fn command_line(executable: &[u16], args: &[&OsStr]) -> io::Result<Vec<u16>> {
    if executable.contains(&(b'"' as u16)) {
        return Err(invalid("Windows bridge executable path contains a quote"));
    }
    // The CRT parses argv[0] separately. An absolute file path cannot end in a separator.
    let mut command = vec![b'"' as u16];
    command.extend_from_slice(executable);
    command.push(b'"' as u16);
    for arg in args {
        command.push(b' ' as u16);
        append_argument(&mut command, &wide(arg)?);
        if command.len() >= 32_767 {
            return Err(invalid("Windows bridge command line is too long"));
        }
    }
    if command.len() >= 32_767 {
        return Err(invalid("Windows bridge command line is too long"));
    }
    command.push(0);
    Ok(command)
}

fn environment(decoder: &Path, temp_dir: &Path, system_root: &Path) -> io::Result<Vec<u16>> {
    // Sorted case-insensitively as required by CreateProcessW. No host variables
    // or PATH entries are inherited. The caller supplies a trusted SystemRoot.
    let mut block = Vec::new();
    for (key, path) in [
        ("MOTIONWRIGHT_OTIO_FFPROBE=", decoder),
        ("SystemRoot=", system_root),
        ("TEMP=", temp_dir),
        ("TMP=", temp_dir),
    ] {
        block.extend(key.encode_utf16());
        block.extend(wide(path.as_os_str())?);
        block.push(0);
    }
    block.push(0);
    Ok(block)
}

fn duplicate_inheritable(file: &File) -> io::Result<OwnedHandle> {
    let mut copy = null_mut();
    // SAFETY: Both process arguments are this process, the file is borrowed for
    // the call, and DuplicateHandle initializes copy on success.
    unsafe {
        let current = GetCurrentProcess();
        if DuplicateHandle(
            current,
            file.as_raw_handle(),
            current,
            &mut copy,
            0,
            1,
            DUPLICATE_SAME_ACCESS,
        ) == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(OwnedHandle::from_raw_handle(copy))
    }
}

struct Attributes {
    memory: Vec<usize>,
}

impl Attributes {
    fn new() -> io::Result<Self> {
        let mut bytes = 0;
        // SAFETY: The first call queries buffer size using the documented null
        // list. The second receives aligned storage that outlives deletion.
        unsafe {
            InitializeProcThreadAttributeList(null_mut(), 2, 0, &mut bytes);
            if bytes == 0 {
                return Err(io::Error::last_os_error());
            }
            let mut memory = vec![0; bytes.div_ceil(size_of::<usize>())];
            if InitializeProcThreadAttributeList(memory.as_mut_ptr().cast(), 2, 0, &mut bytes) == 0
            {
                return Err(io::Error::last_os_error());
            }
            Ok(Self { memory })
        }
    }

    fn raw(&mut self) -> LPPROC_THREAD_ATTRIBUTE_LIST {
        self.memory.as_mut_ptr().cast()
    }

    fn set(&mut self, key: u32, handles: &mut [HANDLE]) -> io::Result<()> {
        // SAFETY: The caller retains both handle arrays until the list is
        // destroyed. They contain only valid handles for the designated key.
        if unsafe {
            UpdateProcThreadAttribute(
                self.raw(),
                0,
                key as usize,
                handles.as_mut_ptr().cast(),
                std::mem::size_of_val(handles),
                null_mut(),
                null_mut(),
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
}

impl Drop for Attributes {
    fn drop(&mut self) {
        // SAFETY: The constructor initialized this list and owns its storage.
        unsafe { DeleteProcThreadAttributeList(self.raw()) };
    }
}

fn create_job() -> io::Result<OwnedHandle> {
    // SAFETY: Null inputs create an unnamed, noninheritable job. The typed
    // limit record stays alive through SetInformationJobObject.
    unsafe {
        let raw = CreateJobObjectW(null(), null());
        if raw.is_null() {
            return Err(io::Error::last_os_error());
        }
        let job = OwnedHandle::from_raw_handle(raw);
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(
            raw,
            JobObjectExtendedLimitInformation,
            (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        ) == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(job)
    }
}

fn active_processes(job: &OwnedHandle) -> io::Result<u32> {
    // SAFETY: The owned job stays open and the accounting buffer has the
    // documented size/type for this information class.
    unsafe {
        let mut info: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = zeroed();
        if QueryInformationJobObject(
            job.as_raw_handle(),
            JobObjectBasicAccountingInformation,
            (&mut info as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
            size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
            null_mut(),
        ) == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(info.ActiveProcesses)
    }
}

fn terminate_and_verify(job: &OwnedHandle) -> bool {
    // SAFETY: The job is private to this invocation and remains open while
    // querying all member exits. Closing it is an additional backstop.
    unsafe { TerminateJobObject(job.as_raw_handle(), 1) };
    let started = Instant::now();
    loop {
        match active_processes(job) {
            Ok(0) => return true,
            Ok(_) if started.elapsed() < CLEANUP => thread::sleep(POLL),
            _ => return false,
        }
    }
}

fn launch(
    executable: &Path,
    args: &[&OsStr],
    decoder: &Path,
    cwd: &Path,
    temp_dir: &Path,
    system_root: &Path,
    stderr: &File,
) -> io::Result<(OwnedHandle, OwnedHandle)> {
    if [executable, decoder, cwd, temp_dir, system_root]
        .iter()
        .any(|path| !path.is_absolute())
    {
        return Err(invalid(
            "Windows bridge executable and directories must be absolute",
        ));
    }
    let mut application = wide(executable.as_os_str())?;
    let mut command = command_line(&application, args)?;
    application.push(0);
    let mut directory = wide(cwd.as_os_str())?;
    directory.push(0);
    let mut env = environment(decoder, temp_dir, system_root)?;

    let job = create_job()?;
    let sink = File::options().read(true).write(true).open(r"\\.\NUL")?;
    let null_handle = duplicate_inheritable(&sink)?;
    let stderr_handle = duplicate_inheritable(stderr)?;
    let mut inherited = [null_handle.as_raw_handle(), stderr_handle.as_raw_handle()];
    let mut jobs = [job.as_raw_handle()];
    let mut attributes = Attributes::new()?;
    attributes.set(PROC_THREAD_ATTRIBUTE_HANDLE_LIST, &mut inherited)?;
    attributes.set(PROC_THREAD_ATTRIBUTE_JOB_LIST, &mut jobs)?;

    // SAFETY: All pointed-to data remains alive through the call. CreateProcessW
    // assigns membership before execution and inherits just the listed handles.
    // The parent retains its private, noninheritable job handle.
    unsafe {
        let mut startup: STARTUPINFOEXW = zeroed();
        startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = inherited[0];
        startup.StartupInfo.hStdOutput = inherited[0];
        startup.StartupInfo.hStdError = inherited[1];
        startup.lpAttributeList = attributes.raw();
        let mut process: PROCESS_INFORMATION = zeroed();
        if CreateProcessW(
            application.as_ptr(),
            command.as_mut_ptr(),
            null(),
            null(),
            1,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
            env.as_mut_ptr().cast::<c_void>(),
            directory.as_ptr(),
            &startup.StartupInfo,
            &mut process,
        ) == 0
        {
            return Err(io::Error::last_os_error());
        }
        drop(OwnedHandle::from_raw_handle(process.hThread));
        Ok((job, OwnedHandle::from_raw_handle(process.hProcess)))
    }
}

pub(super) struct BridgeDirectories<'a> {
    pub(super) cwd: &'a Path,
    pub(super) temp: &'a Path,
    pub(super) system_root: &'a Path,
}

/// Run the already admitted absolute executable. A clean result requires both
/// a zero root exit code and a quiescent job. The caller supplies a protected
/// runtime directory and an independent private scratch directory.
pub(super) fn run(
    executable: &Path,
    args: &[&OsStr],
    decoder: &Path,
    directories: BridgeDirectories<'_>,
    stderr: &File,
    limit: Duration,
) -> Result<(), BridgeProcessError> {
    use BridgeProcessError::{Exit, Start, TimedOut, Uncertain};
    let started = Instant::now();
    let (job, process) = launch(
        executable,
        args,
        decoder,
        directories.cwd,
        directories.temp,
        directories.system_root,
        stderr,
    )
    .map_err(Start)?;
    let remaining = limit.saturating_sub(started.elapsed());
    let timeout_ms = remaining.as_millis().min(u128::from(u32::MAX - 1)) as u32;
    // SAFETY: This process handle is retained and timeout_ms is never INFINITE.
    match unsafe { WaitForSingleObject(process.as_raw_handle(), timeout_ms) } {
        WAIT_OBJECT_0 => {
            let mut code = 0;
            // SAFETY: The process is signaled and its exit-code buffer is live.
            if unsafe { GetExitCodeProcess(process.as_raw_handle(), &mut code) } == 0 {
                terminate_and_verify(&job);
                return Err(Uncertain);
            }
            match active_processes(&job) {
                Ok(0) if code == 0 => Ok(()),
                Ok(0) => Err(Exit(code)),
                Ok(_) => {
                    if terminate_and_verify(&job) && code != 0 {
                        Err(Exit(code))
                    } else {
                        Err(Uncertain)
                    }
                }
                Err(_) => {
                    terminate_and_verify(&job);
                    Err(Uncertain)
                }
            }
        }
        WAIT_TIMEOUT if terminate_and_verify(&job) => Err(TimedOut),
        WAIT_TIMEOUT => Err(Uncertain),
        _ => {
            terminate_and_verify(&job);
            Err(Uncertain)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn windows_arguments_preserve_empty_quotes_and_trailing_backslashes() {
        let executable = wide(OsStr::new(r"E:\OTIO Runtime\python.exe")).unwrap();
        let arguments = [
            OsStr::new(""),
            OsStr::new(r#"a\"b"#),
            OsStr::new(r"with space\\"),
            OsStr::new("café 💡"),
        ];
        let command = command_line(&executable, &arguments).unwrap();
        assert_eq!(
            String::from_utf16(&command[..command.len() - 1]).unwrap(),
            r#""E:\OTIO Runtime\python.exe" "" "a\\\"b" "with space\\\\" "café 💡""#
        );
        assert_eq!(command.last(), Some(&0));
        assert!(command_line(&executable, &[OsStr::new("a\0b")]).is_err());
    }

    #[test]
    fn new_job_is_quiescent_and_has_only_the_kill_on_close_limit() {
        let job = create_job().unwrap();
        assert_eq!(active_processes(&job).unwrap(), 0);
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
        // SAFETY: Match the information class to its output buffer.
        assert_ne!(
            unsafe {
                QueryInformationJobObject(
                    job.as_raw_handle(),
                    JobObjectExtendedLimitInformation,
                    (&mut limits as *mut JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                    null_mut(),
                )
            },
            0
        );
        assert_eq!(
            limits.BasicLimitInformation.LimitFlags,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        );
    }

    #[test]
    fn closing_job_kills_a_live_child_and_timeout_reports_proven_cleanup() {
        // A native system process works even when the test build itself needs
        // toolchain DLLs from PATH. The supervisor inherits no PATH.
        let system_root = std::env::var_os("SystemRoot").unwrap();
        let system_root = Path::new(&system_root);
        let executable = system_root.join(r"System32\WindowsPowerShell\v1.0\powershell.exe");
        let cwd = executable.parent().unwrap();
        let scratch = tempfile::tempdir().unwrap();
        let stderr = File::create_new(scratch.path().join("job-stderr.log")).unwrap();
        let args = [
            OsStr::new("-NoProfile"),
            OsStr::new("-NonInteractive"),
            OsStr::new("-Command"),
            OsStr::new("[System.Threading.Thread]::Sleep(30000)"),
        ];
        let (job, child) = launch(
            &executable,
            &args,
            &executable,
            cwd,
            scratch.path(),
            system_root,
            &stderr,
        )
        .unwrap();
        assert_eq!(active_processes(&job).unwrap(), 1);
        assert_eq!(
            unsafe { WaitForSingleObject(child.as_raw_handle(), 0) },
            WAIT_TIMEOUT
        );
        drop(job);
        assert_eq!(
            unsafe { WaitForSingleObject(child.as_raw_handle(), 5_000) },
            WAIT_OBJECT_0
        );

        let timed = run(
            &executable,
            &args,
            &executable,
            BridgeDirectories {
                cwd,
                temp: scratch.path(),
                system_root,
            },
            &stderr,
            Duration::from_secs(2),
        );
        assert!(
            matches!(timed, Err(BridgeProcessError::TimedOut)),
            "{timed:?}"
        );
    }
}
