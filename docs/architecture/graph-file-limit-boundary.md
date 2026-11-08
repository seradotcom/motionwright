# Graph executable staging and Store file limits

| Requirement | Enforcement |
| --- | --- |
| Graph Store/CAS files remain bounded at 4 MiB | Store transport hard and soft RLIMIT_FSIZE before opening Store |
| SDK provider staging uses authenticated executables | Controller allowance derived from the verified producer index |
| Requests cannot precede resource enforcement | Kernel observation of the actual Store child before RPC admission |

The SDK stages complete provider and runtime executables before its restricted socket becomes ready. Applying the 4 MiB Store limit to the whole controller tree prevents staging a larger authenticated executable. A POSIX copy of the captured 67,032,280-byte provider terminated with SIGXFSZ at 4,194,304 bytes. This demonstrates a sufficient startup blocker; it does not reconstruct stderr absent from the original failed run.

The case controller receives an exact allowance equal to the largest verified staged executable or 4 MiB, whichever is larger. The five input roles are native_provider, manim_runner, python3, ffmpeg and semwright_sandbox. Each reference retains its path, digest and size and is bound to the verified producer-index digest. Values above 256 MiB and arbitrary larger allowances are rejected. Command receipts distinguish requested controller limits from required Store limits; they do not claim that a failed process enforced them.

Both Store writer transports receive a closed environment requiring exactly 4,194,304 bytes. The shared Rust example sets both RLIMIT_FSIZE values and reads them back before opening any Store. Native requires the setting; ordinary sessions without it retain their existing behavior. Unsupported platforms, malformed settings or failed enforcement reject the selected Graph session. The two unsafe libc calls operate only on initialized local rlimit structures, have synchronous pointer lifetimes, and check their return values; the kernel resource API is necessary to establish the actual process restriction.

On the selected Linux execution path, Studio observes both limits through the actual Store writer child PID's /proc limits before returning from startup. RPC admission also requires that observation, including for restarted sessions. This does not introduce another launcher or substitute PID. The sandbox driver's existing 4 MiB policy, Store/CAS size predicates, stream quotas, read-only reopening and final closure/upload gates remain in force.

These checks establish the resource boundary. They do not grant Graph acceptance: the original Native case and independent reopening of the same physical Store/CAS remain necessary.
