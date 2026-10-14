# Motionwright v0.1: bounded provider process ancestry

This is a CI-only reliability and privacy hardening for issue #49. It changes no renderer, provider lifecycle, Semwright Native SDK pin, effect grant, or native job mutation.

## What was missing

`/proc/<pid>/task/<tid>/children` is a point-in-time observation. A child PID can exit and be reused between reading the parent's `children` list and reading the child's `/proc/<pid>/status`. A child PID set alone discarded the identity of the reporting parent, so a recycled, unrelated process could be counted as a Driver Host descendant. Missing child status and thread child lists could also look like complete samples.

## New conservative contract

For each reported child PID the sampler retains the expected parent PID; it admits and traverses the child only when the parsed kernel `PPid` matches. Missing/mismatched child status, unreadable child lists, and conflicting parent reports mark the sample `truncated=true`. Incomplete samples cannot be used as authoritative proof of provider exit. A missing `State` maps to `?`, not an empty valid state. Tests cover unrelated recycled PIDs, missing children, vanished thread files, conflicting parents, and a valid multithreaded descendant chain.

This check reduces false ancestry; it is **not** a race-free process identity attestation. The Linux PID and parent can both be reused over time, and this sampler does not read `/proc/<pid>/stat` start ticks or use pidfd identity. It still cannot identify which upstream provider lease invalidation path caused the intermittent generation loss.

## Release boundary

The native render's outcome stays `UNKNOWN` unless the same original job obtains canonical terminal evidence. Neither a green CI run nor this passive process sample authorizes render retry, `project.close`, installed-owner acceptance, or release of v0.1. Open blockers #49, #89, #91, #126 remain independent.
