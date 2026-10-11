# Native Motion Canvas render-phase cost evidence (MW05-E01-02)

This revision instruments the existing Motionwright/Semwright native
conformance workflow. The canonical application Film is rendered twice:
first through Motionwright StudioService + Native SDK ProductionCoordinator,
then through an independent direct Semwright Broker/Driver Host job with
the **same** 60-frame Film. No new renderer, command, grant or automatic
retry is introduced.

## Actual measured phases

The source-bound receipt native-render-stage-cost.json will report
monotonic wall-clock microsecond measurements from a real disposable
GitHub Actions native run:

- Motionwright-mediated render until its completed native job receipt;
- independent mediated manifest, exact frame hashes and native opacity
  ROI inspection;
- direct Broker composition.plan, composition.apply, render.start,
  render.status terminal observation, render.result and composition.verify;
- independent FFmpeg decoded-RGBA pixel equivalence for every frame
  in both actual native artifact roots.

Stage observation of a render includes queue/wait/poll time, not
CPU-only time. The report must match the exact source Git SHA, pinned
Semwright SHA, Film digest and measured 60-frame decoded parity, with
two separate acknowledged jobs.

## Interpretation: no fabricated performance

The second native render runs after the first on the same runner. It may
reuse warmed browser/provider state and filesystem caches. Therefore
this is NOT a controlled cold-vs-warm experiment. A faster direct render
does not establish renderer cache savings or prove Motionwright mediation
is slower. The new receipt includes no inferred percentage speedup,
saved frames, monetary savings or competitor comparison.

PNG file hashes can legitimately differ for identical decoded pixels.
The existing parity gate checks all 60 decoded RGBA frame hashes without
tolerance and the new timing receipt requires that exact pixel proof.

## Fail-closed evidence boundary

A missing phase, negative duration, reversed monotonic clock, changed
Film/source SHA, missing distinct native job, unverified frames or
incorrect phase totals is rejected. The original provider's uncertainty
policy is unchanged: no timeout causes redispatch and no fake success
is produced from cached output. All identities are exact-source and
no owner session tickets, socket addresses, private paths or grants are
serialized in the stage report.

These measurements do not establish human review, audible mix quality,
performance superiority or full renderer support. Independent repeated
cold/warm trials and more complex films are still required. The original
MW05-E01-02 requirement remains PARTIAL and the 60 private acceptance
cases stay NOT_RUN.
